"""CLI entry point: ``uv run python -m triage <command>``.

Commands (Part B implements them):
  chat     interactive triage chat (mock mode by default)
  index    rebuild the runbook vector index from docs/runbook/*.md
  health   one-shot Mule app health check (reuses check_mule_app_status)
  ask      single question, non-interactive (useful for demos and evals)
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="triage", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("chat", help="interactive triage chat")
    sub.add_parser("index", help="rebuild the runbook vector index")
    sub.add_parser("health", help="one-shot Mule app health check")
    ask = sub.add_parser("ask", help="ask one question and exit")
    ask.add_argument("question")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # TODO(part-b): wire commands to graph / indexer / health tool.
    print(f"triage: command '{args.command}' is not implemented yet (Session 1 Part B).")
    return 2


if __name__ == "__main__":
    sys.exit(main())
