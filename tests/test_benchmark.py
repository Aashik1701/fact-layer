"""Runs the A9/A10 deterministic parser benchmark (fact_layer/benchmark.py)
as individual pytest cases, so a regression shows up as a named failing
test rather than only a lower aggregate number."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.benchmark import BENCHMARK_CASES, run_benchmark


def test_benchmark_has_expected_category_coverage():
    categories = {c.category for c in BENCHMARK_CASES}
    expected = {"numeric_parsing", "unit_context", "period_context", "table_structure",
                "ambiguity", "diagnostics", "layout", "evidence"}
    assert expected.issubset(categories)
    assert len(BENCHMARK_CASES) >= 25


@pytest.mark.parametrize("case", BENCHMARK_CASES, ids=[c.id for c in BENCHMARK_CASES])
def test_benchmark_case(case):
    assert case.check(), f"[{case.category}] {case.description}"


def test_benchmark_report_aggregates_correctly():
    report = run_benchmark(BENCHMARK_CASES)
    assert report.total == len(BENCHMARK_CASES)
    by_cat = report.by_category()
    assert sum(total for _, total in by_cat.values()) == report.total
    assert sum(passed for passed, _ in by_cat.values()) == report.passed
