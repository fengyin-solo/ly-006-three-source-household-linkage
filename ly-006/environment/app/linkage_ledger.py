#!/usr/bin/env python3
"""Quarterly anonymous registration linkage entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def normalized(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(character.casefold() for character in value if character.isalnum())


def link_exact(payload: dict) -> tuple[list[dict], int]:
    source_a = payload["source_a"]["records"]
    source_b = payload["source_b"]["records"]
    used: set[str] = set()
    links = []
    for row_a in source_a:
        for row_b in source_b:
            if row_b["source_no"] in used:
                continue
            same_name = (
                normalized(row_a["surname"]) == normalized(row_b["surname"])
                and normalized(row_a["given"]) == normalized(row_b["given"])
            )
            if not same_name or row_a["age"] != row_b["age"]:
                continue
            used.add(row_b["source_no"])
            links.append(
                {
                    "age": row_a["age"],
                    "support": 1.0,
                    "source_records": {
                        "a": row_a["source_no"],
                        "b": row_b["source_no"],
                        "c": None,
                    },
                }
            )
            break
    return links, len(links)


def build_result(payload: dict) -> dict:
    links, matched = link_exact(payload)
    count_a = len(payload["source_a"]["records"])
    count_b = len(payload["source_b"]["records"])
    estimate = ((count_a + 1) * (count_b + 1) / (matched + 1)) - 1
    return {
        "run_id": payload["run_id"],
        "households": [
            {
                "household_key": f"H{index:04d}",
                "address": str(row["age"]),
                "ages": [row["age"]],
                "source_households": {"a": None, "b": None, "c": None},
                "members": [row],
                "observed": 1,
                "unobserved": 0,
            }
            for index, row in enumerate(links, 1)
        ],
        "population": {
            "n_a": count_a,
            "n_b": count_b,
            "matched": matched,
            "unmatched_a": count_a - matched,
            "unmatched_b": count_b - matched,
            "unobserved_a": 0,
            "unobserved_b": 0,
            "unobserved_c": 0,
            "unobserved_members": 0,
            "chapman_estimate": round(estimate, 3),
            "variance": 0.0,
            "interval_lower": max(count_a, count_b),
            "interval_upper": round(estimate, 3),
        },
    }


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("REFUSED BAD_INPUT\n")
        return 2
    input_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        result = build_result(payload)
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, ZeroDivisionError):
        sys.stderr.write("REFUSED BAD_INPUT\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
