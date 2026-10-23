"""Report stable test outcomes for local evidence collection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


CASES_BY_TEST = json.loads(
    Path(__file__).with_name("case_ids.json").read_text(encoding="utf-8")
)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item._case_outcome = report.outcome


def pytest_sessionfinish(session, exitstatus) -> None:
    for item in session.items:
        outcome = getattr(item, "_case_outcome", "not_run").upper()
        base = item.name.split("[", 1)[0]
        parameter = item.name.split("[", 1)[1][:-1] if "[" in item.name else ""
        declared = CASES_BY_TEST.get(base, [base])
        case_ids = [parameter] if parameter in declared else declared
        for case_id in case_ids:
            print(f"OBSERVED_CASE {case_id} {outcome}")
