"""Black-box checks for reduced three-source household recovery."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path

import pytest


APP_PATH = Path(os.environ.get("TASK_APP", "/app/linkage_ledger.py"))
DEFAULT_DATA_DIR = Path(os.environ.get("TASK_DATA_DIR", "/app/data"))
SOURCE_NAMES = ("a", "b", "c")


def default_payload() -> dict:
    return json.loads(
        (DEFAULT_DATA_DIR / "current_run.json").read_text(encoding="utf-8")
    )


def write_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def run_case(
    input_path: Path,
    result_path: Path,
    timeout: int = 8,
) -> subprocess.CompletedProcess:
    kwargs = {}
    if os.geteuid() == 0:
        input_path.chmod(0o644)
        for path in (input_path.parent, result_path.parent):
            path.mkdir(parents=True, exist_ok=True)
            current = path
            while current != Path("/tmp") and current != Path("/"):
                current.chmod(0o777 if current == path else 0o755)
                current = current.parent
        kwargs = {"user": 65534, "group": 65534}
    return subprocess.run(
        [sys.executable, str(APP_PATH), str(input_path), str(result_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
        **kwargs,
    )


def run_payload(
    tmp_path: Path,
    name: str,
    payload: dict,
) -> tuple[subprocess.CompletedProcess, dict, Path]:
    root = tmp_path / name
    input_path = root / "run.json"
    result_path = root / "result.json"
    write_payload(input_path, payload)
    completed = run_case(input_path, result_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    return completed, result, result_path


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in text
        if not character.isspace() and character != "-"
    )


def record_no(
    payload: dict,
    source_name: str,
    surname: str,
    age: int,
    given: str,
) -> str:
    return next(
        record["source_no"]
        for record in payload[f"source_{source_name}"]["records"]
        if record["surname"] == surname
        and record["age"] == age
        and record["given"] == given
    )


def household_output(result: dict, address: str) -> dict:
    target = normalize_text(address)
    return next(
        household
        for household in result["households"]
        if household["address"] == target
    )


def person_support(payload: dict, first_name: str, second_name: str) -> Decimal:
    fields = payload["model"]["fields"]
    total = Decimal(0)
    for field_name in ("surname", "given", "sex"):
        parameter = fields[field_name]
        m_value = Decimal(str(parameter["m"]))
        u_value = Decimal(str(parameter["u"]))
        if first_name == second_name:
            total += (m_value / u_value).ln()
        else:
            total += (
                (Decimal(1) - m_value) / (Decimal(1) - u_value)
            ).ln()
    return total - Decimal(str(payload["model"]["record_cost"]))


def cap_payload() -> dict:
    payload = default_payload()
    payload["run_id"] = "public-cap"
    for source_name in SOURCE_NAMES:
        households = []
        records = []
        for index in range(8):
            tag = f"{index:02d}"
            address = f"Cap Row {index}"
            households.append(
                {
                    "address": address,
                    "ages": [20 + index, 40 + index],
                    "household_no": f"{source_name.upper()}-CAP-{tag}",
                    "size": 2,
                }
            )
            for member_index, age in enumerate((20 + index, 40 + index), 1):
                records.append(
                    {
                        "address": address,
                        "age": age,
                        "given": f"Given{index}{member_index}",
                        "sex": "F" if member_index == 1 else "M",
                        "source_no": (
                            f"{source_name.upper()}-R-{tag}-{member_index}"
                        ),
                        "surname": f"Family{tag}",
                    }
                )
        payload[f"source_{source_name}"] = {
            "households": households,
            "records": records,
        }
    return payload


def full_match_payload() -> dict:
    payload = default_payload()
    payload["run_id"] = "fully-matched"
    hazel = next(
        household
        for household in payload["source_b"]["households"]
        if household["household_no"].endswith("HAZEL")
    )
    hazel["address"] = "12 Elm Terrace"
    hazel["ages"] = [33]
    hazel["size"] = 1
    jo = next(
        record
        for record in payload["source_b"]["records"]
        if record["given"] == "Jo"
    )
    jo["address"] = "12 Elm Terrace"
    jo["age"] = 33
    jo["surname"] = "Ford"
    return payload


def ambiguous_payload() -> dict:
    payload = default_payload()
    payload["run_id"] = "ambiguous-households"
    pine_a = next(
        household
        for household in payload["source_a"]["households"]
        if household["household_no"].endswith("PINE")
    )
    pine_a["address"] = "12 Elm Terrace"
    pine_a["ages"] = [33]
    pine_a["size"] = 1
    pine_b = next(
        household
        for household in payload["source_b"]["households"]
        if household["household_no"].endswith("PINE")
    )
    pine_b["address"] = "12 Elm Terrace"
    pine_b["ages"] = [33]
    pine_b["size"] = 1
    return payload


def no_overlap_payload() -> dict:
    payload = default_payload()
    payload["run_id"] = "no-overlap"
    for household in payload["source_b"]["households"]:
        household["address"] = "Zeta Place"
    return payload


def age_mismatch_payload() -> dict:
    payload = default_payload()
    payload["run_id"] = "age-mismatch"
    maple_b = next(
        household
        for household in payload["source_b"]["households"]
        if household["household_no"].endswith("MAPLE")
    )
    maple_b["ages"] = [8, 35, 38]
    nia = next(
        record
        for record in payload["source_b"]["records"]
        if record["surname"] == "Abara" and record["age"] == 37
    )
    nia["age"] = 38
    return payload


def age_unresolved_payload() -> dict:
    payload = default_payload()
    payload["run_id"] = "unresolved-age"
    nia = next(
        record
        for record in payload["source_a"]["records"]
        if record["surname"] == "Abara"
    )
    nia["age"] = 33
    return payload


def address_unresolved_payload() -> dict:
    payload = default_payload()
    payload["run_id"] = "unresolved-address"
    nia = next(
        record
        for record in payload["source_a"]["records"]
        if record["surname"] == "Abara"
    )
    nia["address"] = "Quartz Quay"
    return payload


def canonical_tie_payload() -> dict:
    payload = default_payload()
    payload["run_id"] = "canonical-twin-tie"
    imani = next(
        record
        for record in payload["source_a"]["records"]
        if record["surname"] == "Mensah" and record["age"] == 12
    )
    duplicate = copy.deepcopy(imani)
    duplicate["source_no"] = "WIN-00-TWIN"
    payload["source_a"]["records"].append(duplicate)
    return payload


def chapman_values(n_a: int, n_b: int, matched: int) -> tuple[Decimal, Decimal]:
    estimate = Decimal((n_a + 1) * (n_b + 1)) / Decimal(matched + 1) - 1
    variance = (
        Decimal((n_a + 1) * (n_b + 1) * (n_a - matched) * (n_b - matched))
        / (Decimal(matched + 1) ** 2 * Decimal(matched + 2))
    )
    return estimate, variance


def test_existing_entry_is_python_runnable(tmp_path):
    """题面：「入口是 `/app/linkage_ledger.py`」。"""
    case_id = "ENTRY-01-runtime"
    input_path = DEFAULT_DATA_DIR / "current_run.json"
    result_path = tmp_path / "entry" / "result.json"
    result_path.parent.mkdir(parents=True)
    completed = run_case(input_path, result_path)
    assert completed.returncode == 0, case_id
    assert result_path.is_file(), case_id


def test_default_recovers_eight_households(tmp_path):
    """题面：「季度复核前，区级服务评估组收到窗口接案、电话回访和住户回访三批匿名材料。」。"""
    case_id = "BASE-01-default-recovery"
    completed, result, result_path = run_payload(tmp_path, "default", default_payload())
    assert completed.returncode == 0, case_id
    assert result_path.is_file(), case_id
    assert len(result["households"]) == 9, case_id
    assert sum(
        household["source_households"]["a"] is not None
        for household in result["households"]
    ) == 8, case_id


def test_default_member_links_use_three_sources(tmp_path):
    """题面：「全来源分配使地址支持总和最大。」。"""
    case_id = "MEMBER-01-three-source-link"
    payload = default_payload()
    completed, result, _ = run_payload(tmp_path, "member-links", payload)
    maple = household_output(result, "17 Maple Walk")
    nia = next(member for member in maple["members"] if member["age"] == 37)
    assert completed.returncode == 0, case_id
    assert nia["source_records"] == {
        "a": record_no(payload, "a", "Abara", 37, "Nia"),
        "b": record_no(payload, "b", "Abara", 37, "Nia"),
        "c": record_no(payload, "c", "Abara", 37, "Nia"),
    }, case_id


def test_default_unobserved_counts_are_exposed(tmp_path):
    """题面：「来源未观测数是该来源户摘要人数减记录数。」。"""
    case_id = "UNOBS-01-source-unobserved"
    completed, result, _ = run_payload(tmp_path, "unobserved", default_payload())
    population = result["population"]
    assert completed.returncode == 0, case_id
    assert population["unobserved_a"] == 3, case_id
    assert population["unobserved_b"] == 4, case_id
    assert population["unobserved_c"] == 2, case_id


def test_household_and_record_order_does_not_change_result(tmp_path):
    """题面：「户按规范地址排序，年龄数组和三侧户号作为次级键。」。"""
    case_id = "ORDER-01-permutation"
    member_case_id = "ORDER-02-member-order"
    payload = default_payload()
    shuffled = copy.deepcopy(payload)
    for source_name in SOURCE_NAMES:
        shuffled[f"source_{source_name}"]["households"].reverse()
        shuffled[f"source_{source_name}"]["records"].reverse()
    first = run_payload(tmp_path, "first-order", payload)[1]
    second = run_payload(tmp_path, "second-order", shuffled)[1]
    assert first == second, case_id
    assert first["households"] == second["households"], member_case_id


def test_same_age_twin_slots_use_global_support(tmp_path):
    """题面：「同户同岁的多个槽先使跨来源人员支持总和最大；」。"""
    case_id = "TWIN-01-global-support"
    payload = default_payload()
    completed, result, _ = run_payload(tmp_path, "twins", payload)
    rowan = household_output(result, "4 Rowan Court")
    imani_slot = next(
        member
        for member in rowan["members"]
        if member["source_records"]["a"]
        == record_no(payload, "a", "Mensah", 12, "Imani")
    )
    assert completed.returncode == 0, case_id
    assert imani_slot["source_records"]["b"] == record_no(
        payload, "b", "Mensah", 12, "Imani"
    ), case_id
    assert imani_slot["source_records"]["c"] == record_no(
        payload, "c", "Mensah", 12, "Imani"
    ), case_id


def test_equal_support_twin_tie_uses_canonical_record_order(tmp_path):
    """题面：「序列字典序最小者。」。"""
    case_id = "TWIN-02-canonical-tie"
    tuple_case_id = "TWIN-03-tuple-tie"
    minimum_case_id = "TWIN-04-minimum-sequence"
    payload = canonical_tie_payload()
    completed, result, _ = run_payload(tmp_path, "canonical-tie", payload)
    rowan = household_output(result, "4 Rowan Court")
    pairs = [
        (
            member["source_records"]["a"],
            member["source_records"]["b"],
            member["source_records"]["c"],
        )
        for member in rowan["members"]
        if member["age"] == 12
    ]
    assert completed.returncode == 0, case_id
    assert pairs == [
        (
            "WIN-00-TWIN",
            record_no(payload, "b", "Mensah", 12, "Imani"),
            record_no(payload, "c", "Mensah", 12, "Imani"),
        ),
        (
            record_no(payload, "a", "Mensah", 12, "Imani"),
            record_no(payload, "b", "Mensah", 12, "Zuri"),
            record_no(payload, "c", "Mensah", 12, "Zuri"),
        ),
    ], case_id
    assert pairs[0][0] == "WIN-00-TWIN", tuple_case_id
    assert pairs == sorted(pairs), minimum_case_id


def test_member_support_sums_pairwise_log_likelihoods(tmp_path):
    """题面：「该槽已观测记录两两人员支持之和。」。"""
    case_id = "SUPPORT-01-pairwise"
    llr_case_id = "FS-01-llr"
    payload = default_payload()
    completed, result, _ = run_payload(tmp_path, "support", payload)
    maple = household_output(result, "17 Maple Walk")
    nia = next(member for member in maple["members"] if member["age"] == 37)
    expected = person_support(payload, "Abara", "Abara") * 3
    assert completed.returncode == 0, case_id
    assert nia["support"] == pytest.approx(float(expected), abs=5e-7), case_id
    assert expected > 0, llr_case_id


def test_ambiguous_household_assignment_is_refused(tmp_path):
    """题面：「户唯一性差距是逐个禁用已选户对后重新求最优总和所得差值的最小值，」。"""
    case_id = "HOUSE-01-ambiguity"
    payload = ambiguous_payload()
    input_path = tmp_path / "ambiguous" / "run.json"
    result_path = tmp_path / "ambiguous" / "result.json"
    write_payload(input_path, payload)
    result = run_case(input_path, result_path, timeout=8)
    assert result.returncode != 0, case_id
    assert not result_path.exists(), case_id


def test_unresolved_age_slots_are_refused(tmp_path):
    """题面：「无法注入年龄槽」。"""
    case_id = "SLOT-01-age-refusal"
    input_path = tmp_path / "unresolved" / "run.json"
    result_path = tmp_path / "unresolved" / "result.json"
    write_payload(input_path, age_unresolved_payload())
    result = run_case(input_path, result_path)
    assert result.returncode != 0, case_id
    assert not result_path.exists(), case_id


def test_no_aligned_ab_household_is_refused(tmp_path):
    """题面：「`matched` 为 0 时拒绝。」。"""
    case_id = "HOUSE-02-no-overlap"
    input_path = tmp_path / "no-overlap" / "run.json"
    result_path = tmp_path / "no-overlap" / "result.json"
    write_payload(input_path, no_overlap_payload())
    result = run_case(input_path, result_path)
    assert result.returncode != 0, case_id
    assert not result_path.exists(), case_id


def test_household_uniqueness_gap_is_positive(tmp_path):
    """题面：「户唯一性差距是逐个禁用已选户对后重新求最优总和所得差值的最小值，」。"""
    case_id = "HOUSE-03-gap"
    completed, result, _ = run_payload(tmp_path, "gap", default_payload())
    assert completed.returncode == 0, case_id
    assert result["diagnostics"]["uniqueness_gap"] == pytest.approx(
        1.057, abs=5e-7
    ), case_id


def test_source_households_are_recovered_from_a_anchor(tmp_path):
    """题面：「以 `source_a` 为锚点」。"""
    case_id = "HOUSE-04-anchor"
    completed, result, _ = run_payload(tmp_path, "anchor", default_payload())
    maple = household_output(result, "17 Maple Walk")
    assert completed.returncode == 0, case_id
    assert maple["source_households"] == {
        "a": "WIN-17-MAPLE",
        "b": "REV-31-MAPLE",
        "c": "HOM-53-MAPLE",
    }, case_id


def test_household_ages_must_match_exactly(tmp_path):
    """题面：「两户只有年龄数组逐位相等」。"""
    case_id = "HOUSE-05-age-profile"
    completed, result, _ = run_payload(tmp_path, "age-profile", age_mismatch_payload())
    maple = household_output(result, "17 Maple Walk")
    assert completed.returncode == 0, case_id
    assert maple["source_households"]["b"] is None, case_id


def test_unmatched_household_members_enter_chapman_counts(tmp_path):
    """题面：「A 无 B 对应户时该户成员计为 A 侧未匹配」。"""
    case_id = "HOUSE-06-unmatched"
    completed, result, _ = run_payload(tmp_path, "unmatched", default_payload())
    population = result["population"]
    assert completed.returncode == 0, case_id
    assert population["matched"] == 15, case_id
    assert population["unmatched_a"] == 1, case_id
    assert population["unmatched_b"] == 1, case_id


def test_partial_households_keep_single_source_members(tmp_path):
    """题面：「没有候选的户保留单来源状态。」。"""
    case_id = "HOUSE-07-partial"
    completed, result, _ = run_payload(tmp_path, "partial", default_payload())
    elm = household_output(result, "12 Elm Terrace")
    assert completed.returncode == 0, case_id
    assert elm["source_households"]["b"] is None, case_id
    assert elm["source_households"]["c"] is None, case_id


def test_address_normalization_ignores_case_space_and_hyphen(tmp_path):
    """题面：「姓名和地址先做 Unicode NFKC、大小写折叠，再去掉空白与连字符。」。"""
    case_id = "MATCH-01-normalization"
    payload = default_payload()
    maple_b = next(
        household
        for household in payload["source_b"]["households"]
        if household["household_no"].endswith("MAPLE")
    )
    maple_c = next(
        household
        for household in payload["source_c"]["households"]
        if household["household_no"].endswith("MAPLE")
    )
    maple_b["address"] = "  17-MAPLE walk "
    maple_c["address"] = "17 maple-walk"
    completed, result, _ = run_payload(tmp_path, "normalization", payload)
    maple = household_output(result, "17 Maple Walk")
    assert completed.returncode == 0, case_id
    assert maple["source_households"] == {
        "a": "WIN-17-MAPLE",
        "b": "REV-31-MAPLE",
        "c": "HOM-53-MAPLE",
    }, case_id


def test_household_candidate_requires_positive_adjusted_support(tmp_path):
    """题面：「地址支持减去 `household_cost` 后大于 0」。"""
    case_id = "MATCH-02-positive"
    payload = default_payload()
    payload["model"]["household_cost"] = 10
    input_path = tmp_path / "positive-support" / "run.json"
    result_path = tmp_path / "positive-support" / "result.json"
    write_payload(input_path, payload)
    result = run_case(input_path, result_path)
    assert result.returncode != 0, case_id
    assert not result_path.exists(), case_id


def test_record_slot_requires_matching_age(tmp_path):
    """题面：「记录进入成员槽必须年龄相等」。"""
    case_id = "SLOT-02-age-match"
    input_path = tmp_path / "slot-age" / "run.json"
    result_path = tmp_path / "slot-age" / "result.json"
    write_payload(input_path, age_unresolved_payload())
    result = run_case(input_path, result_path)
    assert result.returncode != 0, case_id
    assert not result_path.exists(), case_id


def test_record_slot_requires_positive_address_support(tmp_path):
    """题面：「记录地址对该户的调整支持大于 0」。"""
    case_id = "SLOT-03-address-support"
    input_path = tmp_path / "slot-address" / "run.json"
    result_path = tmp_path / "slot-address" / "result.json"
    write_payload(input_path, address_unresolved_payload())
    result = run_case(input_path, result_path)
    assert result.returncode != 0, case_id
    assert not result_path.exists(), case_id


def test_record_assignment_uses_global_address_support(tmp_path):
    """题面：「全来源分配使地址支持总和最大。」。"""
    case_id = "SLOT-04-global-assignment"
    payload = default_payload()
    completed, result, _ = run_payload(tmp_path, "slot-global", payload)
    aspen = household_output(result, "22 Aspen Road")
    lina_slot = next(
        member
        for member in aspen["members"]
        if member["source_records"]["a"] == record_no(payload, "a", "Silva", 27, "Lina")
    )
    assert completed.returncode == 0, case_id
    assert lina_slot["source_records"]["b"] == record_no(
        payload, "b", "Silva", 27, "Lina"
    ), case_id
    assert lina_slot["source_records"]["c"] == record_no(
        payload, "c", "Silva", 27, "Lina"
    ), case_id


def test_each_source_record_enters_one_slot(tmp_path):
    """题面：「每条记录进入恰好」。"""
    case_id = "SLOT-05-one-slot"
    payload = default_payload()
    completed, result, _ = run_payload(tmp_path, "one-slot", payload)
    observed = Counter(
        record_no_value
        for household in result["households"]
        for member in household["members"]
        for record_no_value in member["source_records"].values()
        if record_no_value is not None
    )
    expected = Counter(
        record["source_no"]
        for source_name in SOURCE_NAMES
        for record in payload[f"source_{source_name}"]["records"]
    )
    assert completed.returncode == 0, case_id
    assert observed == expected, case_id


def test_unobserved_member_slots_remain_in_roster(tmp_path):
    """题面：「一个槽三侧都没有记录时是」。"""
    case_id = "SLOT-06-empty-slot"
    completed, result, _ = run_payload(tmp_path, "empty-slot", default_payload())
    maple = household_output(result, "17 Maple Walk")
    willow = household_output(result, "6 Willow Yard")
    maple_empty = next(member for member in maple["members"] if member["age"] == 8)
    willow_empty = next(member for member in willow["members"] if member["age"] == 60)
    assert completed.returncode == 0, case_id
    assert set(maple_empty["source_records"].values()) == {None}, case_id
    assert set(willow_empty["source_records"].values()) == {None}, case_id


def test_population_counts_use_household_roster_sizes(tmp_path):
    """题面：「分别是 A、B 户摘要人数总和」。"""
    case_id = "CHAP-01-roster"
    completed, result, _ = run_payload(tmp_path, "population-roster", default_payload())
    population = result["population"]
    assert completed.returncode == 0, case_id
    assert population["n_a"] == 16, case_id
    assert population["n_b"] == 16, case_id
    assert population["unobserved_members"] == 2, case_id


def test_chapman_estimate_uses_matched_members(tmp_path):
    """题面：「否则 `chapman_estimate = ((n_a + 1) * (n_b + 1) / (matched + 1)) - 1`」。"""
    case_id = "CHAP-02-estimate"
    completed, result, _ = run_payload(tmp_path, "chapman-estimate", default_payload())
    population = result["population"]
    assert completed.returncode == 0, case_id
    assert population["matched"] == 15, case_id
    assert population["chapman_estimate"] == pytest.approx(17.063, abs=5e-10), case_id


def test_chapman_variance_uses_unrounded_counts(tmp_path):
    """题面：「`variance = ((n_a + 1) * (n_b + 1) * (n_a - matched) * (n_b - matched)) /」。"""
    case_id = "CHAP-03-variance"
    completed, result, _ = run_payload(tmp_path, "chapman-variance", default_payload())
    population = result["population"]
    _, variance = chapman_values(16, 16, 15)
    expected = variance.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    assert completed.returncode == 0, case_id
    assert population["variance"] == pytest.approx(float(expected), abs=5e-10), case_id


def test_chapman_interval_lower_floors_and_respects_maximum(tmp_path):
    """题面：「下界向下取整到 3 位且不低于 `max(n_a, n_b)`」。"""
    case_id = "CHAP-04-lower"
    first = run_payload(tmp_path, "lower-default", default_payload())[1]
    second = run_payload(tmp_path, "lower-full", full_match_payload())[1]
    estimate, variance = chapman_values(16, 16, 15)
    half_width = Decimal("1.96") * variance.sqrt()
    expected = max(
        Decimal(16),
        (estimate - half_width).quantize(Decimal("0.001"), rounding=ROUND_FLOOR),
    )
    assert first["population"]["interval_lower"] == pytest.approx(
        float(expected), abs=5e-10
    ), case_id
    assert second["population"]["interval_lower"] == pytest.approx(16.0), case_id


def test_chapman_interval_upper_ceils(tmp_path):
    """题面：「上界向上取整到 3 位。」。"""
    case_id = "CHAP-05-upper"
    completed, result, _ = run_payload(tmp_path, "chapman-upper", default_payload())
    estimate, variance = chapman_values(16, 16, 15)
    half_width = Decimal("1.96") * variance.sqrt()
    expected = (estimate + half_width).quantize(
        Decimal("0.001"),
        rounding=ROUND_CEILING,
    )
    assert completed.returncode == 0, case_id
    assert result["population"]["interval_upper"] == pytest.approx(
        float(expected), abs=5e-10
    ), case_id


def test_chapman_estimate_rounds_half_up(tmp_path):
    """题面：「估计值按 half-up 保留 3 位小数」。"""
    case_id = "CHAP-06-half-up"
    completed, result, _ = run_payload(tmp_path, "chapman-half-up", default_payload())
    assert completed.returncode == 0, case_id
    assert result["population"]["chapman_estimate"] == pytest.approx(
        17.063, abs=5e-10
    ), case_id


def test_eight_households_are_accepted_at_cap(tmp_path):
    """题面：「每份来源最多 8 户、16 条记录」。"""
    case_id = "SIZE-01-household-cap"
    completed, result, _ = run_payload(tmp_path, "household-cap", cap_payload())
    assert completed.returncode == 0, case_id
    assert len(result["households"]) == 8, case_id


def test_nine_households_are_refused(tmp_path):
    """题面：「超过任一项上限时返回非零状态。」。"""
    case_id = "SIZE-02-household-reject"
    payload = cap_payload()
    payload["source_a"]["households"].append(
        {
            "address": "Overflow House",
            "ages": [99],
            "household_no": "A-OVERFLOW",
            "size": 1,
        }
    )
    input_path = tmp_path / "nine-households" / "run.json"
    result_path = tmp_path / "nine-households" / "result.json"
    write_payload(input_path, payload)
    result = run_case(input_path, result_path)
    assert result.returncode != 0, case_id
    assert not result_path.exists(), case_id


def test_sixteen_records_are_accepted_at_cap(tmp_path):
    """题面：「每份来源最多 8 户、16 条记录」。"""
    case_id = "SIZE-03-record-cap"
    completed, result, _ = run_payload(tmp_path, "record-cap", cap_payload())
    observed_a = sum(
        member["source_records"]["a"] is not None
        for household in result["households"]
        for member in household["members"]
    )
    assert completed.returncode == 0, case_id
    assert len(result["households"]) == 8, case_id
    assert observed_a == 16, case_id


def test_seventeen_records_are_refused(tmp_path):
    """题面：「超过任一项上限时返回非零状态。」。"""
    case_id = "SIZE-04-record-reject"
    payload = cap_payload()
    extra = copy.deepcopy(payload["source_a"]["records"][0])
    extra["source_no"] = "A-OVERFLOW-RECORD"
    payload["source_a"]["records"].append(extra)
    input_path = tmp_path / "seventeen-records" / "run.json"
    result_path = tmp_path / "seventeen-records" / "result.json"
    write_payload(input_path, payload)
    result = run_case(input_path, result_path)
    assert result.returncode != 0, case_id
    assert not result_path.exists(), case_id


def test_public_cap_completes_within_limit(tmp_path):
    """题面：「单次从读取到写完结果在 5 秒内完成。」。"""
    case_id = "PERF-01-public-cap"
    input_path = tmp_path / "performance" / "run.json"
    result_path = tmp_path / "performance" / "result.json"
    write_payload(input_path, cap_payload())
    started = time.monotonic()
    result = run_case(input_path, result_path, timeout=8)
    elapsed = time.monotonic() - started
    assert result.returncode == 0, case_id
    assert len(json.loads(result_path.read_text(encoding="utf-8"))["households"]) == 8, case_id
    assert elapsed < 5, case_id


def test_default_data_source_is_present_and_described():
    """题面：「镜像内 `environment/app/data/current_run.json` 是一份示例材料。」。"""
    case_id = "DATA-01-sample"
    source_text = (DEFAULT_DATA_DIR / "DATA_SOURCE.txt").read_text(encoding="utf-8")
    assert (DEFAULT_DATA_DIR / "current_run.json").is_file(), case_id
    assert "匿名样例" in source_text, case_id
