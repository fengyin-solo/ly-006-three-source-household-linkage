#!/usr/bin/env python3
"""Reference recovery of households, member slots, and coverage statistics."""

from __future__ import annotations

import itertools
import json
import sys
import unicodedata
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from functools import lru_cache
from pathlib import Path
from typing import Any


SOURCE_NAMES = ("a", "b", "c")
PERSON_FIELDS = ("given", "sex", "surname")
THREE_PLACES = Decimal("0.001")
SIX_PLACES = Decimal("0.000001")


class Refusal(Exception):
    """A public refusal code with no traceback output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def reject(code: str) -> None:
    raise Refusal(code)


def as_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def validate_caps(payload: dict[str, Any]) -> None:
    for source_name in SOURCE_NAMES:
        source = payload[f"source_{source_name}"]
        households = source["households"]
        records = source["records"]
        if len(households) > 8 or len(records) > 16:
            reject("BAD_INPUT")
        for household in households:
            if household["size"] != len(household["ages"]):
                reject("BAD_INPUT")
            if not (1 <= household["size"] <= 4):
                reject("BAD_INPUT")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(
        character
        for character in text
        if not character.isspace() and character != "-"
    )


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row_index, left_character in enumerate(left, 1):
        current = [row_index]
        for column_index, right_character in enumerate(right, 1):
            insertion = current[column_index - 1] + 1
            deletion = previous[column_index] + 1
            change = previous[column_index - 1] + (
                0 if left_character == right_character else 1
            )
            current.append(min(insertion, deletion, change))
        previous = current
    return previous[-1]


def similarity(left: Any, right: Any) -> Decimal:
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    longest = max(len(left_text), len(right_text))
    if longest == 0:
        return Decimal(1)
    return Decimal(1) - Decimal(edit_distance(left_text, right_text)) / Decimal(longest)


def field_llr(
    field_name: str,
    left: Any,
    right: Any,
    model: dict[str, Any],
) -> Decimal | None:
    if left is None or right is None:
        return None
    parameter = model["fields"][field_name]
    m_value = as_decimal(parameter["m"])
    u_value = as_decimal(parameter["u"])
    threshold = as_decimal(parameter["threshold"])
    score = similarity(left, right)
    if score >= threshold:
        return (m_value / u_value).ln()
    return ((Decimal(1) - m_value) / (Decimal(1) - u_value)).ln()


def person_support(
    left: dict[str, Any],
    right: dict[str, Any],
    model: dict[str, Any],
) -> Decimal:
    total = Decimal(0)
    for field_name in PERSON_FIELDS:
        contribution = field_llr(field_name, left[field_name], right[field_name], model)
        if contribution is not None:
            total += contribution
    return total - as_decimal(model["record_cost"])


def household_support(
    left: dict[str, Any],
    right: dict[str, Any],
    model: dict[str, Any],
) -> Decimal | None:
    if left["ages"] != right["ages"]:
        return None
    contribution = field_llr("address", left["address"], right["address"], model)
    if contribution is None:
        return None
    adjusted = contribution - as_decimal(model["household_cost"])
    return adjusted if adjusted > 0 else None


def slot_support(
    record: dict[str, Any],
    household: dict[str, Any],
    model: dict[str, Any],
) -> Decimal | None:
    if record["age"] not in household["ages"]:
        return None
    contribution = field_llr("address", record["address"], household["address"], model)
    if contribution is None:
        return None
    adjusted = contribution - as_decimal(model["record_cost"])
    return adjusted if adjusted > 0 else None


def hungarian(cost: list[list[Decimal]]) -> list[int]:
    """Return one assigned column for each row when rows <= columns."""

    row_count = len(cost)
    column_count = len(cost[0]) if cost else 0
    if row_count > column_count:
        raise ValueError("assignment requires rows <= columns")
    potential_row = [Decimal(0)] * (row_count + 1)
    potential_column = [Decimal(0)] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    path = [0] * (column_count + 1)
    for row in range(1, row_count + 1):
        matched_row[0] = row
        column0 = 0
        minimum: list[Decimal | None] = [None] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[column0] = True
            row0 = matched_row[column0]
            delta: Decimal | None = None
            column1 = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                current = (
                    cost[row0 - 1][column - 1]
                    - potential_row[row0]
                    - potential_column[column]
                )
                if minimum[column] is None or current < minimum[column]:
                    minimum[column] = current
                    path[column] = column0
                candidate = minimum[column]
                if candidate is not None and (delta is None or candidate < delta):
                    delta = candidate
                    column1 = column
            if delta is None:
                raise ValueError("assignment failure")
            for column in range(column_count + 1):
                if used[column]:
                    potential_row[matched_row[column]] += delta
                    potential_column[column] -= delta
                elif minimum[column] is not None:
                    minimum[column] -= delta
            column0 = column1
            if matched_row[column0] == 0:
                break
        while True:
            previous = path[column0]
            matched_row[column0] = matched_row[previous]
            column0 = previous
            if column0 == 0:
                break
    assignment = [-1] * row_count
    for column in range(1, column_count + 1):
        if matched_row[column] != 0:
            assignment[matched_row[column] - 1] = column - 1
    return assignment


def match_households(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    model: dict[str, Any],
    excluded: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[Decimal, list[tuple[dict[str, Any], dict[str, Any], Decimal]], int]:
    scores: dict[tuple[str, str], Decimal] = {}
    for household_a in left:
        for household_b in right:
            key = (household_a["household_no"], household_b["household_no"])
            score = household_support(household_a, household_b, model)
            if key not in excluded and score is not None:
                scores[key] = score
    ceiling = max(scores.values()) if scores else Decimal(0)
    costs: list[list[Decimal]] = []
    for household_a in left:
        values: list[Decimal] = []
        for column_index, household_b in enumerate(right):
            key = (household_a["household_no"], household_b["household_no"])
            score = scores.get(key, Decimal(0))
            tie = Decimal(column_index + 1) / Decimal("1000000000000")
            if key in scores:
                values.append(ceiling - score + tie)
            else:
                values.append(ceiling + Decimal(1) + tie)
        values.extend(
            ceiling + Decimal(index + 1) / Decimal("1000000000000")
            for index in range(len(left))
        )
        costs.append(values)
    assignment = hungarian(costs) if left else []
    selected: list[tuple[dict[str, Any], dict[str, Any], Decimal]] = []
    total = Decimal(0)
    for row_index, column_index in enumerate(assignment):
        if column_index >= len(right):
            continue
        household_a = left[row_index]
        household_b = right[column_index]
        score = scores.get((household_a["household_no"], household_b["household_no"]))
        if score is not None:
            selected.append((household_a, household_b, score))
            total += score
    return total, selected, len(scores)


def align_source(
    anchor: list[dict[str, Any]],
    other: list[dict[str, Any]],
    model: dict[str, Any],
    require_overlap: bool,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], Decimal]], Decimal]:
    best_total, selected, _ = match_households(anchor, other, model)
    if require_overlap and not selected:
        reject("NO_OVERLAP")
    gaps: list[Decimal] = []
    for household_a, household_b, _ in selected:
        without_total, _, _ = match_households(
            anchor,
            other,
            model,
            frozenset({(household_a["household_no"], household_b["household_no"])}),
        )
        gaps.append(best_total - without_total)
    return selected, min(gaps, default=Decimal(0))


def build_groups(
    payload: dict[str, Any],
    alignments_b: list[tuple[dict[str, Any], dict[str, Any], Decimal]],
    alignments_c: list[tuple[dict[str, Any], dict[str, Any], Decimal]],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_a: dict[str, dict[str, Any]] = {}
    for household in payload["source_a"]["households"]:
        group = {
            "address": normalize_text(household["address"]),
            "ages": list(household["ages"]),
            "source_households": {"a": household["household_no"], "b": None, "c": None},
        }
        groups.append(group)
        by_a[household["household_no"]] = group
    for household_a, household_other, _ in alignments_b:
        by_a[household_a["household_no"]]["source_households"]["b"] = household_other[
            "household_no"
        ]
    for household_a, household_other, _ in alignments_c:
        by_a[household_a["household_no"]]["source_households"]["c"] = household_other[
            "household_no"
        ]
    used_b = {
        group["source_households"]["b"]
        for group in groups
        if group["source_households"]["b"] is not None
    }
    used_c = {
        group["source_households"]["c"]
        for group in groups
        if group["source_households"]["c"] is not None
    }
    for household in payload["source_b"]["households"]:
        if household["household_no"] not in used_b:
            groups.append(
                {
                    "address": normalize_text(household["address"]),
                    "ages": list(household["ages"]),
                    "source_households": {
                        "a": None,
                        "b": household["household_no"],
                        "c": None,
                    },
                }
            )
    for household in payload["source_c"]["households"]:
        if household["household_no"] not in used_c:
            groups.append(
                {
                    "address": normalize_text(household["address"]),
                    "ages": list(household["ages"]),
                    "source_households": {
                        "a": None,
                        "b": None,
                        "c": household["household_no"],
                    },
                }
            )
    groups.sort(
        key=lambda group: (
            group["address"],
            tuple(group["ages"]),
            tuple(group["source_households"][name] or "" for name in SOURCE_NAMES),
        )
    )
    return groups


def assign_records(
    source: dict[str, Any],
    groups: list[dict[str, Any]],
    source_name: str,
    model: dict[str, Any],
) -> dict[tuple[int, int], dict[str, Any]]:
    slots: list[tuple[int, int, int]] = []
    for group_index, group in enumerate(groups):
        if group["source_households"][source_name] is None:
            continue
        for member_index, age in enumerate(group["ages"]):
            slots.append((group_index, member_index, age))
    records = source["records"]
    if len(records) > len(slots):
        reject("UNRESOLVED_HOUSEHOLDS")
    candidates: dict[tuple[int, int], Decimal] = {}
    for record_index, record in enumerate(records):
        for slot_index, (group_index, _, age) in enumerate(slots):
            if record["age"] != age:
                continue
            support = slot_support(record, groups[group_index], model)
            if support is not None:
                candidates[(record_index, slot_index)] = support
    ceiling = max(candidates.values()) if candidates else Decimal(1)
    costs: list[list[Decimal]] = []
    for record_index in range(len(records)):
        values: list[Decimal] = []
        for slot_index in range(len(slots)):
            support = candidates.get((record_index, slot_index))
            tie = Decimal(slot_index + 1) / Decimal("1000000000000")
            if support is None:
                values.append(ceiling + Decimal(1) + tie)
            else:
                values.append(ceiling - support + tie)
        costs.append(values)
    assignment = hungarian(costs) if records else []
    assigned: dict[tuple[int, int], dict[str, Any]] = {}
    used_slots: set[int] = set()
    for record_index, slot_index in enumerate(assignment):
        if slot_index < 0 or slot_index >= len(slots):
            reject("UNRESOLVED_HOUSEHOLDS")
        if (record_index, slot_index) not in candidates or slot_index in used_slots:
            reject("UNRESOLVED_HOUSEHOLDS")
        used_slots.add(slot_index)
        group_index, member_index, _ = slots[slot_index]
        assigned[(group_index, member_index)] = records[record_index]
    if len(assigned) != len(records):
        reject("UNRESOLVED_HOUSEHOLDS")
    return assigned


def pair_support(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
    model: dict[str, Any],
) -> Decimal:
    if first is None or second is None or first["age"] != second["age"]:
        return Decimal(0)
    return person_support(first, second, model)


def align_equal_age_slots(
    group_index: int,
    member_indices: list[int],
    assignments: dict[str, dict[tuple[int, int], dict[str, Any]]],
    model: dict[str, Any],
) -> None:
    slot_count = len(member_indices)
    per_source: dict[str, list[dict[str, Any]]] = {}
    for source_name in SOURCE_NAMES:
        per_source[source_name] = [
            assignments[source_name][(group_index, member_index)]
            for member_index in member_indices
            if (group_index, member_index) in assignments[source_name]
        ]

    @lru_cache(maxsize=None)
    def solve(
        slot_index: int,
        mask_a: int,
        mask_b: int,
        mask_c: int,
    ) -> tuple[Decimal, tuple[tuple[str, str, str], ...]] | None:
        if slot_index == slot_count:
            masks = (mask_a, mask_b, mask_c)
            full = tuple((1 << len(per_source[name])) - 1 for name in SOURCE_NAMES)
            return (Decimal(0), ()) if masks == full else None
        choices_a: list[int | None] = [None] + [
            index
            for index in range(len(per_source["a"]))
            if mask_a & (1 << index) == 0
        ]
        choices_b: list[int | None] = [None] + [
            index
            for index in range(len(per_source["b"]))
            if mask_b & (1 << index) == 0
        ]
        choices_c: list[int | None] = [None] + [
            index
            for index in range(len(per_source["c"]))
            if mask_c & (1 << index) == 0
        ]
        best: tuple[Decimal, tuple[tuple[str, str, str], ...]] | None = None
        for index_a in choices_a:
            for index_b in choices_b:
                for index_c in choices_c:
                    selected = (
                        per_source["a"][index_a] if index_a is not None else None,
                        per_source["b"][index_b] if index_b is not None else None,
                        per_source["c"][index_c] if index_c is not None else None,
                    )
                    next_a = mask_a if index_a is None else mask_a | (1 << index_a)
                    next_b = mask_b if index_b is None else mask_b | (1 << index_b)
                    next_c = mask_c if index_c is None else mask_c | (1 << index_c)
                    remaining = solve(
                        slot_index + 1,
                        next_a,
                        next_b,
                        next_c,
                    )
                    if remaining is None:
                        continue
                    score = (
                        pair_support(selected[0], selected[1], model)
                        + pair_support(selected[0], selected[2], model)
                        + pair_support(selected[1], selected[2], model)
                    )
                    canonical = tuple(
                        record["source_no"] if record is not None else ""
                        for record in selected
                    )
                    candidate = (remaining[0] + score, (canonical,) + remaining[1])
                    if best is None or candidate[0] > best[0] or (
                        candidate[0] == best[0] and candidate[1] < best[1]
                    ):
                        best = candidate
        return best

    result = solve(0, 0, 0, 0)
    if result is None:
        reject("UNRESOLVED_HOUSEHOLDS")
    for source_name in SOURCE_NAMES:
        for member_index in member_indices:
            assignments[source_name].pop((group_index, member_index), None)
    for slot_index, member_index in enumerate(member_indices):
        canonical = result[1][slot_index]
        for source_name, record_no in zip(SOURCE_NAMES, canonical):
            if not record_no:
                continue
            record = next(
                item
                for item in per_source[source_name]
                if item["source_no"] == record_no
            )
            assignments[source_name][(group_index, member_index)] = record


def refine_equal_age_slots(
    groups: list[dict[str, Any]],
    assignments: dict[str, dict[tuple[int, int], dict[str, Any]]],
    model: dict[str, Any],
) -> None:
    for group_index, group in enumerate(groups):
        ages = group["ages"]
        start = 0
        while start < len(ages):
            end = start + 1
            while end < len(ages) and ages[end] == ages[start]:
                end += 1
            if end - start > 1:
                align_equal_age_slots(
                    group_index,
                    list(range(start, end)),
                    assignments,
                    model,
                )
            start = end


def round_half_up(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def numeric(value: Decimal, places: Decimal) -> float:
    return float(round_half_up(value, places))


def chapman_statistics(n_a: int, n_b: int, matched: int) -> dict[str, Any]:
    if matched == 0:
        reject("NO_OVERLAP")
    with localcontext() as context:
        context.prec = 50
        estimate = (Decimal(n_a + 1) * Decimal(n_b + 1) / Decimal(matched + 1)) - 1
        variance = (
            Decimal(n_a + 1)
            * Decimal(n_b + 1)
            * Decimal(n_a - matched)
            * Decimal(n_b - matched)
            / (Decimal(matched + 1) ** 2 * Decimal(matched + 2))
        )
        half_width = Decimal("1.96") * variance.sqrt()
        lower = max(
            Decimal(max(n_a, n_b)),
            (estimate - half_width).quantize(THREE_PLACES, rounding=ROUND_FLOOR),
        )
        upper = (estimate + half_width).quantize(THREE_PLACES, rounding=ROUND_CEILING)
    return {
        "chapman_estimate": numeric(estimate, THREE_PLACES),
        "interval_lower": float(lower),
        "interval_upper": float(upper),
        "variance": numeric(variance, SIX_PLACES),
    }


def build_result(payload: dict[str, Any]) -> dict[str, Any]:
    validate_caps(payload)
    model = payload["model"]
    selected_b, gap_b = align_source(
        payload["source_a"]["households"],
        payload["source_b"]["households"],
        model,
        True,
    )
    selected_c, gap_c = align_source(
        payload["source_a"]["households"],
        payload["source_c"]["households"],
        model,
        False,
    )
    gaps = [gap_b]
    if selected_c:
        gaps.append(gap_c)
    uniqueness_gap = min(gaps)
    if uniqueness_gap < as_decimal(model["uniqueness_margin"]):
        reject("AMBIGUOUS_HOUSEHOLDS")

    groups = build_groups(payload, selected_b, selected_c)
    assignments = {
        source_name: assign_records(
            payload[f"source_{source_name}"],
            groups,
            source_name,
            model,
        )
        for source_name in SOURCE_NAMES
    }
    refine_equal_age_slots(groups, assignments, model)

    household_output: list[dict[str, Any]] = []
    global_unobserved = 0
    for group_index, group in enumerate(groups):
        members: list[dict[str, Any]] = []
        observed_count = 0
        for member_index, age in enumerate(group["ages"]):
            source_records: dict[str, str | None] = {}
            observed_rows: list[dict[str, Any]] = []
            for source_name in SOURCE_NAMES:
                record = assignments[source_name].get((group_index, member_index))
                source_records[source_name] = record["source_no"] if record else None
                if record is not None:
                    observed_rows.append(record)
            support = sum(
                (
                    pair_support(first, second, model)
                    for first, second in itertools.combinations(observed_rows, 2)
                ),
                Decimal(0),
            )
            members.append(
                {
                    "age": age,
                    "source_records": source_records,
                    "support": numeric(support, SIX_PLACES),
                }
            )
            if observed_rows:
                observed_count += 1
            else:
                global_unobserved += 1
        household_output.append(
            {
                "household_key": f"H{len(household_output) + 1:04d}",
                "address": group["address"],
                "ages": group["ages"],
                "source_households": group["source_households"],
                "members": members,
                "observed": observed_count,
                "unobserved": len(group["ages"]) - observed_count,
            }
        )

    sizes = {
        name: sum(household["size"] for household in payload[f"source_{name}"]["households"])
        for name in SOURCE_NAMES
    }
    records = {
        name: len(payload[f"source_{name}"]["records"])
        for name in SOURCE_NAMES
    }
    matched = sum(
        len(group["ages"])
        for group in groups
        if group["source_households"]["a"] is not None
        and group["source_households"]["b"] is not None
    )
    statistics = chapman_statistics(sizes["a"], sizes["b"], matched)
    population = {
        "n_a": sizes["a"],
        "n_b": sizes["b"],
        "matched": matched,
        "unmatched_a": sizes["a"] - matched,
        "unmatched_b": sizes["b"] - matched,
        "unobserved_a": sizes["a"] - records["a"],
        "unobserved_b": sizes["b"] - records["b"],
        "unobserved_c": sizes["c"] - records["c"],
        "unobserved_members": global_unobserved,
        **statistics,
    }
    return {
        "run_id": payload["run_id"],
        "households": household_output,
        "population": population,
        "diagnostics": {"uniqueness_gap": numeric(uniqueness_gap, SIX_PLACES)},
    }


def run(input_path: Path, result_path: Path) -> str:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    result = build_result(payload)
    result_path.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return payload["run_id"]


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        sys.stderr.write("REFUSED BAD_INPUT\n")
        return 2
    try:
        run(Path(arguments[0]), Path(arguments[1]))
    except Refusal as exc:
        sys.stderr.write(f"REFUSED {exc.code}\n")
        return 2
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, ZeroDivisionError):
        sys.stderr.write("REFUSED BAD_INPUT\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
