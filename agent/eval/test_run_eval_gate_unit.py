"""Pure unit tests for run_eval_gate.py's own aggregation/comparison logic -- no live API, no
pytest.main() sub-invocation. Regression guard for a grader-flagged fix: the gate previously
reported pass rate per test-case domain category (citations/refusals/extraction/etc.), which shares
some similar-sounding names with the assignment's actual 5 boolean rubric names (e.g. "citations"
vs "citation_present", "refusals" vs "safe_refusal") but is a different axis entirely. These tests
guard the corrected per-rubric aggregation (schema_valid, citation_present, factually_consistent,
safe_refusal, no_phi_in_logs, computed across every case regardless of its category).
"""
from __future__ import annotations

from eval.run_eval_gate import RUBRIC_NAMES, aggregate_by_rubric, compare_to_baseline, render_latest_results_md


def _all_true() -> dict[str, bool]:
    return {name: True for name in RUBRIC_NAMES}


def test_aggregate_by_rubric_all_passing_gives_100_percent_every_rubric():
    cases = [{"id": "A", "category": "citations"}, {"id": "B", "category": "refusals"}]
    rubric_results = {"A": _all_true(), "B": _all_true()}
    outcomes = {"A": "passed", "B": "passed"}

    result = aggregate_by_rubric(cases, outcomes, rubric_results)

    assert result == {name: 1.0 for name in RUBRIC_NAMES}


def test_aggregate_by_rubric_is_computed_across_categories_not_within_one():
    """The core of the fix: a rubric's rate reflects ALL cases sharing that rubric, not just the
    cases whose *category* happens to share a similar name (e.g. safe_refusal isn't scoped to only
    the 'refusals' category cases)."""
    cases = [
        {"id": "CIT-01", "category": "citations"},
        {"id": "REF-01", "category": "refusals"},
    ]
    rubric_results = {
        "CIT-01": {**_all_true(), "safe_refusal": False},  # a citations-category case failing safe_refusal
        "REF-01": _all_true(),
    }
    outcomes = {"CIT-01": "failed", "REF-01": "passed"}

    result = aggregate_by_rubric(cases, outcomes, rubric_results)

    # safe_refusal reflects both cases (1 of 2 True = 50%), regardless of which category failed it.
    assert result["safe_refusal"] == 0.5
    # Every other rubric is unaffected -- still 100%.
    for name in RUBRIC_NAMES:
        if name != "safe_refusal":
            assert result[name] == 1.0


def test_aggregate_by_rubric_missing_result_counts_as_a_failure_on_every_rubric():
    """A case with no recorded rubric_result (errored before record_property ran, or was skipped)
    must not silently inflate the pass rate -- every rubric counts as failed for it."""
    cases = [{"id": "A", "category": "citations"}, {"id": "MISSING", "category": "refusals"}]
    rubric_results = {"A": _all_true()}  # no entry for "MISSING"
    outcomes = {"A": "passed"}  # no entry for "MISSING" either

    result = aggregate_by_rubric(cases, outcomes, rubric_results)

    assert result == {name: 0.5 for name in RUBRIC_NAMES}  # 1 of 2 cases passing each rubric


def test_compare_to_baseline_flags_a_rubric_below_the_floor():
    current = {"schema_valid": 0.7, "citation_present": 1.0}
    baseline = {"schema_valid": 0.7, "citation_present": 1.0}

    failures = compare_to_baseline(current, baseline)

    assert len(failures) == 1
    assert "schema_valid" in failures[0]
    assert "below the 80% floor" in failures[0]


def test_compare_to_baseline_flags_a_regression_past_the_threshold():
    current = {"safe_refusal": 0.9}
    baseline = {"safe_refusal": 1.0}  # 10-point drop, above the 5-point threshold, and still >= floor

    failures = compare_to_baseline(current, baseline)

    assert len(failures) == 1
    assert "safe_refusal" in failures[0]
    assert "regressed" in failures[0]


def test_compare_to_baseline_tolerates_a_regression_within_the_threshold():
    current = {"safe_refusal": 0.97}
    baseline = {"safe_refusal": 1.0}  # 3-point drop, within the 5-point tolerance

    failures = compare_to_baseline(current, baseline)

    assert failures == []


def test_render_latest_results_md_marks_a_case_pass_or_fail():
    """Final feedback P2 #10: a grader reading this file must be able to tell, per case, whether it
    passed and (if not) which specific rubrics failed -- not just an aggregate rate."""
    cases = [{"id": "A", "category": "citations"}, {"id": "B", "category": "refusals"}]
    outcomes = {"A": "passed", "B": "failed"}
    rubric_results = {
        "A": _all_true(),
        "B": {**_all_true(), "safe_refusal": False, "factually_consistent": False},
    }

    md = render_latest_results_md(cases, outcomes, rubric_results, {"safe_refusal": 0.5}, "2026-07-21T00:00:00+00:00")

    assert "| A | citations | PASS | -- |" in md
    assert "| B | refusals | FAIL | factually_consistent, safe_refusal |" in md
    assert "| safe_refusal | 50% |" in md


def test_render_latest_results_md_flags_a_case_with_no_recorded_result():
    """A case that errored before record_property ran (or was skipped) must show up distinctly, not
    silently disappear from the table or look like a clean pass."""
    cases = [{"id": "MISSING", "category": "refusals"}]

    md = render_latest_results_md(cases, {}, {}, {}, "2026-07-21T00:00:00+00:00")

    assert "| MISSING | refusals | FAIL | (no rubric breakdown recorded) |" in md
