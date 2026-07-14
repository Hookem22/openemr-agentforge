"""The PR-blocking eval gate (W2_ARCHITECTURE.md Section 6) -- runs BOTH testing tiers, in order:

1. Tier 1 (fast, deterministic, free): every `test_*_unit.py` and `test_*_integration.py` file.
   Fails the gate immediately on any failure here, before spending time/money on Tier 2.
2. Tier 2 (the golden set, real APIs): the 50-case golden set, aggregated to a per-category pass
   rate and compared against the checked-in `baseline_results.json`. Fails on a >5-percentage-point
   regression or a drop below the 80% floor.

**Why both tiers matter, found by actually rehearsing the hard-gate check** (temporarily disabling
verify_claims's citation check entirely -- the most dangerous class of regression this whole
project exists to prevent): running the golden set alone caught it in one trial (2 cases failed)
but *missed it entirely* in another (0 cases failed, gate reported PASSED) -- because the golden
set only notices a broken verifier when the model *also* happens to hallucinate something wrong in
that specific run, which is inherently probabilistic. `test_verifier_unit.py`, by contrast, hands
the verifier a claim with a citation *known* not to match anything fetched and asserts it gets
stripped -- that's deterministic regardless of what any LLM does, and caught the same regression
100% of the time, instantly, for free. The golden set is the right tool for *behavioral/quality*
regressions (extraction accuracy, retrieval relevance); the unit/integration tier is the right tool
for *structural/logic* regressions (a verifier that stopped verifying). Running only one tier left
exactly the class of regression this project cares most about only probabilistically caught.

Usage:
    python eval/run_eval_gate.py                 # run the gate, compare to the checked-in baseline
    python eval/run_eval_gate.py --update-baseline  # (re)write baseline_results.json from this run
    python eval/run_eval_gate.py --skip-tier1    # golden set only (debugging the gate itself)
    python eval/run_eval_gate.py --push-to-langfuse  # also push category pass rates as Langfuse scores

--push-to-langfuse (W2_ARCHITECTURE.md Section 9's "eval regression" alert): the pre-push hook only
catches a regression at push time. Running this flag on a schedule (e.g. a nightly cron, not wired
up here since this repo has no CI runner assumed -- see the eval gate's own two-tier-strategy
rationale) pushes each category's pass rate as a NUMERIC Langfuse score
(`eval_gate_pass_rate_{category}`), the same mechanism `verify_node`'s `strip_rate` score already
uses -- so a Langfuse alert on that score dropping >5 points catches a *live* regression between
scheduled runs, not just the ones a developer happens to push through.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections import defaultdict

import pytest

GOLDEN_SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_set.json")
BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline_results.json")
REGRESSION_THRESHOLD = 0.05  # a category's pass rate may not drop more than 5 percentage points
FLOOR = 0.8  # every category must independently clear an 80% floor, regardless of baseline


class _ResultCollector:
    """A pytest plugin that records pass/fail per test id via hook calls -- avoids parsing
    stdout/junit output, which would silently break if pytest's text format ever changed."""

    def __init__(self):
        self.outcomes: dict[str, str] = {}

    def pytest_runtest_logreport(self, report):
        if report.when != "call":
            return
        test_id = report.nodeid.split("[", 1)[-1].rstrip("]")
        self.outcomes[test_id] = "passed" if report.passed else "failed"


def _load_cases() -> list[dict]:
    with open(GOLDEN_SET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["cases"]


def run_tier1_suite() -> bool:
    """Runs every deterministic, no-live-API test file. Returns True iff all pass."""
    eval_dir = os.path.dirname(os.path.abspath(__file__))
    targets = sorted(
        os.path.join(eval_dir, f)
        for f in os.listdir(eval_dir)
        if (f.startswith("test_") and (f.endswith("_unit.py") or f.endswith("_integration.py")))
    )
    exit_code = pytest.main(["-q", *targets])
    return exit_code == pytest.ExitCode.OK


def push_scores_to_langfuse(current: dict[str, float]) -> None:
    """Pushes each category's pass rate as a NUMERIC score attached to one fresh, dedicated trace
    for this gate run -- not tied to any patient/chat trace, so no PHI is anywhere near it (only a
    category name and a 0-1 rate). Requires LANGFUSE_PUBLIC_KEY/SECRET_KEY to be set; no-ops with a
    warning otherwise (matches every other Langfuse call site's graceful-degradation behavior)."""
    from langfuse import get_client

    client = get_client()
    trace_id = client.create_trace_id(seed=f"eval-gate-{uuid.uuid4().hex}")
    for category, rate in current.items():
        client.create_score(trace_id=trace_id, name=f"eval_gate_pass_rate_{category}", value=rate, data_type="NUMERIC")
    client.flush()
    print(f"Pushed {len(current)} category scores to Langfuse (trace {trace_id}).")


def run_golden_set() -> dict[str, float]:
    """Runs the full golden set once and returns {category: pass_rate}."""
    collector = _ResultCollector()
    test_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_golden_set.py")
    exit_code = pytest.main(["-q", test_target], plugins=[collector])
    if exit_code not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
        print(f"eval gate: pytest exited abnormally ({exit_code}) -- likely a fixture/setup error, not case failures", file=sys.stderr)
        sys.exit(2)

    cases = _load_cases()
    by_category: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        outcome = collector.outcomes.get(case["id"])
        if outcome is None:
            print(f"eval gate: no result recorded for case {case['id']!r} -- treating as failed", file=sys.stderr)
            outcome = "failed"
        by_category[case["category"]].append(outcome)

    return {
        category: outcomes.count("passed") / len(outcomes)
        for category, outcomes in by_category.items()
    }


def compare_to_baseline(current: dict[str, float], baseline: dict[str, float]) -> list[str]:
    """Returns a list of human-readable failure reasons -- empty means the gate passes."""
    failures = []
    for category, rate in sorted(current.items()):
        if rate < FLOOR:
            failures.append(f"{category}: pass rate {rate:.0%} is below the {FLOOR:.0%} floor")
            continue
        baseline_rate = baseline.get(category)
        if baseline_rate is not None and (baseline_rate - rate) > REGRESSION_THRESHOLD:
            failures.append(
                f"{category}: pass rate regressed from {baseline_rate:.0%} (baseline) to {rate:.0%} "
                f"(more than the {REGRESSION_THRESHOLD:.0%} allowed drop)"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-baseline", action="store_true", help="write this run's results as the new baseline instead of gating against it")
    parser.add_argument("--skip-tier1", action="store_true", help="skip the unit/integration suite (debugging the gate itself, not for normal use)")
    parser.add_argument("--push-to-langfuse", action="store_true", help="also push this run's category pass rates as Langfuse scores (for the Section 9 eval-regression alert)")
    args = parser.parse_args()

    if not args.skip_tier1:
        print("Tier 1: running the deterministic unit/integration suite (no live API)...")
        if not run_tier1_suite():
            print("\nEVAL GATE FAILED: Tier 1 (unit/integration tests) has failures -- see output above.", file=sys.stderr)
            print("Fix these before the golden set is even worth running; they're free and instant.", file=sys.stderr)
            return 1
        print("Tier 1 passed.\n")

    print("Tier 2: running the 50-case golden set against the real Anthropic + Voyage APIs (and local OpenEMR for chat cases)...")
    current = run_golden_set()

    print("\nPer-category pass rate:")
    for category, rate in sorted(current.items()):
        print(f"  {category:20s} {rate:.0%}")

    if args.push_to_langfuse:
        push_scores_to_langfuse(current)

    if args.update_baseline:
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"\nWrote new baseline to {BASELINE_PATH}")
        return 0

    if not os.path.exists(BASELINE_PATH):
        print(f"\nNo baseline found at {BASELINE_PATH} -- run with --update-baseline first.", file=sys.stderr)
        return 2

    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    failures = compare_to_baseline(current, baseline)
    if failures:
        print("\nEVAL GATE FAILED:")
        for reason in failures:
            print(f"  - {reason}")
        return 1

    print("\nEVAL GATE PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
