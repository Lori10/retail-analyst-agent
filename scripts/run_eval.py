"""Run the golden-set QA eval harness against the real agent.

This makes real, billed calls: every question in the golden set runs through
the actual compiled LangGraph — live Gemini for both the agent and the judge,
live BigQuery for every `run_query`/`get_schema` call — plus the same
Postgres the CLI needs for the Saved Reports/Conversation stores. It is
deliberately a manual script, never run by `uv run pytest` (see
docs/implementation-notes.md's Quality Assurance section for why).

Usage:
    uv run python scripts/run_eval.py \\
        [--golden-set eval/golden_set.json] \\
        [--out eval_results.json] \\
        [--fail-under 0.8]

Exits non-zero if the pass rate is below --fail-under, so this can be wired
into a CI regression gate later without further code changes.
"""

import argparse
import json
import sys

from retail_agent.config import ConfigError, load_config
from retail_agent.eval import run_eval


def _report_to_dict(report) -> dict:
    """Serialize an `EvalReport` into a plain JSON-safe dict.

    Args:
        report: The `EvalReport` from `eval.run_eval`.

    Returns:
        A dict with `pass_rate` and one entry per case under `cases`.
    """
    return {
        "pass_rate": report.pass_rate,
        "cases": [
            {
                "id": cr.case.id,
                "category": cr.case.category,
                "question": cr.case.question,
                "passed": cr.passed,
                "run_error": cr.run_error,
                "answer": cr.result.answer_text if cr.result else None,
                "tools_called": cr.result.tools_called if cr.result else [],
                "sql_queries": cr.result.sql_queries if cr.result else [],
                "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in cr.checks],
                "judge": {
                    "answers_question": cr.verdict.answers_question,
                    "plausible": cr.verdict.plausible,
                    "no_pii": cr.verdict.no_pii,
                    "reasoning": cr.verdict.reasoning,
                    "parse_error": cr.verdict.parse_error,
                },
            }
            for cr in report.case_reports
        ],
    }


def _print_summary(report) -> None:
    """Print a one-line-per-case pass/fail table plus the overall pass rate.

    Args:
        report: The `EvalReport` from `eval.run_eval`.
    """
    for cr in report.case_reports:
        status = "PASS" if cr.passed else "FAIL"
        print(f"[{status}] {cr.case.id} ({cr.case.category})")
        if cr.run_error:
            print(f"         run error: {cr.run_error}")
        elif not cr.passed:
            for check in cr.checks:
                if not check.passed:
                    print(f"         check failed: {check.name} — {check.detail}")
            if not cr.verdict.passed:
                print(f"         judge: {cr.verdict.reasoning}")
    print(f"\n{sum(1 for cr in report.case_reports if cr.passed)}/{len(report.case_reports)} passed "
          f"({report.pass_rate:.0%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden-set", default="eval/golden_set.json", help="Path to the golden set JSON file.")
    parser.add_argument("--out", default="eval_results.json", help="Path to write the full JSON report to.")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        help="Exit non-zero if the pass rate is below this (0.0-1.0). Default 0.0 (never fails the run).",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    report = run_eval(config, args.golden_set)
    _print_summary(report)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(_report_to_dict(report), f, indent=2)
    print(f"\nFull report written to {args.out}")

    if report.pass_rate < args.fail_under:
        print(f"\nPass rate {report.pass_rate:.0%} is below --fail-under {args.fail_under:.0%}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
