"""Promote a saved report into the Golden Bucket (the add-golden-bucket
change). Run by an analyst, never by the agent: a trio only enters the
bucket through this explicit approval.

Shows the report's recorded question, SQL and content, applies any edits
given on the command line, re-checks them (read-only SQL, no email-shaped
text), asks for confirmation, then embeds the question and stores the trio.
Re-approving a report stores a new version and retires the previous one.

Usage:
    uv run python scripts/approve_trio.py --list
    uv run python scripts/approve_trio.py --report-id 42
    uv run python scripts/approve_trio.py --report-id 42 \\
        --question "Revenue by category last month?" \\
        --sql-file edited.sql --report-file edited.md

An --sql-file holding several statements separates them with a line
containing only `---`.
"""

import argparse
import getpass
import sys

from retail_agent.config import ConfigError, load_config
from retail_agent.errors import AgentError
from retail_agent.golden_bucket import (
    GeminiEmbedder,
    GoldenBucketStore,
    TrioRejectedError,
    approve_report,
    check_trio,
)
from retail_agent.startup import StartupError, build_reports_store


def _read_sql_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    statements, current = [], []
    for line in text.splitlines():
        if line.strip() == "---":
            statements.append("\n".join(current).strip())
            current = []
        else:
            current.append(line)
    statements.append("\n".join(current).strip())
    return [s for s in statements if s]


def _read_text_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve a saved report into the Golden Bucket.")
    parser.add_argument("--list", action="store_true", help="List recent saved reports and exit.")
    parser.add_argument("--report-id", type=int, help="Id of the saved report to approve.")
    parser.add_argument("--question", help="Replace the recorded question.")
    parser.add_argument("--sql-file", help="Replace the recorded SQL with the statements in this file.")
    parser.add_argument("--report-file", help="Replace the report content with this file's text.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args()
    if not args.list and args.report_id is None:
        parser.error("give --list or --report-id")

    try:
        config = load_config()
        reports_store = build_reports_store(config)
    except (ConfigError, StartupError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.list:
        for r in reports_store.list_reports_for_review():
            marker = "" if r["has_source_sql"] else "  (no source SQL)"
            print(f"[{r['id']}] {r['title']} — {r['owner']}, {r['created_at']}{marker}")
        return 0

    saved = reports_store.get_report_for_review(args.report_id)
    if saved is None:
        print(f"No saved report with id {args.report_id}.", file=sys.stderr)
        return 1

    question = args.question if args.question is not None else saved["source_question"]
    sql = _read_sql_file(args.sql_file) if args.sql_file else saved["source_sql"]
    report = _read_text_file(args.report_file) if args.report_file else saved["content"]

    print(f"Question:\n  {question}\n\nSQL:")
    for statement in sql or ["(none recorded)"]:
        print(f"  {statement}")
    print(f"\nReport:\n{report}\n")

    # Fail before asking for confirmation; approve_report re-checks anyway.
    try:
        if not sql:
            raise TrioRejectedError("this report has no source SQL; supply it with --sql-file.")
        if not question:
            raise TrioRejectedError("this report has no source question; supply it with --question.")
        check_trio(question=question, sql=sql, report=report)
    except TrioRejectedError as exc:
        print(f"Rejected: {exc}", file=sys.stderr)
        return 1

    if not args.yes and input("Approve this trio into the Golden Bucket? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Not approved; nothing stored.")
        return 0

    try:
        golden_store = GoldenBucketStore(config.reports_database_url, table=config.golden_table)
        embedder = GeminiEmbedder(api_key=config.gemini_api_key, model=config.golden_embedding_model)
        trio = approve_report(
            reports_store=reports_store,
            golden_store=golden_store,
            embedder=embedder,
            embedding_model=config.golden_embedding_model,
            report_id=args.report_id,
            approved_by=getpass.getuser(),
            question=question,
            sql=sql,
            report=report,
        )
    except TrioRejectedError as exc:
        print(f"Rejected: {exc}", file=sys.stderr)
        return 1
    except AgentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Stored trio {trio['id']} (version {trio['version']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
