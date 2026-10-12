"""CLI entry point: ``uv run python -m triage <command>``.

Commands:
  chat     interactive triage chat (mock mode by default)
  ask      one question, non-interactive (demos, scripting, evals)
  index    rebuild the runbook vector index from docs/runbook/*.md
  health   one-shot Mule app health check (same tool and predicate as the agent)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from triage.config import Settings, get_settings

BANNER = """\
Partner Sync Triage Agent  [{mode} mode | partner {partner} | model {model}]
Ask about a case, e.g. "Case 00412345 looks like a duplicate, what happened?"
Commands: :app <healthy|down|deploying|restart_loop|status_error> (mock only), :quit
"""


def _ensure_index(settings: Settings) -> None:
    """In mock mode, build the runbook index on first use (free, ~1s). In live mode the
    index is an explicit step so embedding costs are never a surprise."""
    if Path(settings.index_dir).is_dir():
        return
    if not settings.is_mock:
        sys.exit("Runbook index not found. Run: uv run python -m triage index")
    from triage.runbook.indexer import build_index  # noqa: PLC0415

    n = build_index(settings)
    print(f"(runbook index not found; built it from {n} entries with free local embeddings)\n")


def _run(question: str, settings: Settings) -> int:
    from triage.graph.build import run_triage  # noqa: PLC0415
    from triage.llm import get_chat_model  # noqa: PLC0415
    from triage.tools import get_tools  # noqa: PLC0415

    result = run_triage(question, settings, tools=get_tools(settings), llm=get_chat_model(settings))
    print(result.response)
    print(f"\n(trace: {result.tracer.path.as_posix()}  |  {result.elapsed_s:.2f}s)")
    return 0


def cmd_chat(settings: Settings) -> int:
    _ensure_index(settings)
    model = "scripted-mock" if settings.is_mock else f"{settings.llm_provider}:{settings.llm_model}"
    print(BANNER.format(mode=settings.triage_mode, partner=settings.partner_code, model=model))
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question in (":quit", ":q", "exit", "quit"):
            return 0
        if question.startswith(":app"):
            if not settings.is_mock:
                print("(:app only works in mock mode)")
                continue
            parts = question.split()
            if len(parts) == 2:
                settings = settings.model_copy(update={"mock_app_state": parts[1]})
                print(f"(mock app state -> {parts[1]})")
            else:
                print(f"(mock app state is {settings.mock_app_state})")
            continue
        print()
        _run(question, settings)
        print()


def cmd_index(settings: Settings) -> int:
    from triage.runbook.indexer import build_index  # noqa: PLC0415

    n = build_index(settings)
    print(
        f"Indexed {n} runbook entries from {settings.runbook_dir} into {settings.index_dir} "
        f"(embeddings: {settings.embeddings_provider})"
    )
    return 0


def cmd_health(settings: Settings) -> int:
    from triage.schemas.tools import AppStatusRequest  # noqa: PLC0415
    from triage.tools import ToolError, get_tools  # noqa: PLC0415

    try:
        status = get_tools(settings).check_mule_app_status(AppStatusRequest())
    except ToolError as exc:
        print(f"UNKNOWN: status check failed: {type(exc).__name__}: {exc}")
        return 3
    print(("HEALTHY: " if status.is_healthy else "UNHEALTHY: ") + status.summary)
    return 0 if status.is_healthy else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="triage", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("chat", help="interactive triage chat")
    sub.add_parser("index", help="rebuild the runbook vector index")
    sub.add_parser("health", help="one-shot Mule app health check")
    ask = sub.add_parser("ask", help="ask one question and exit")
    ask.add_argument("question")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    settings = get_settings()
    if args.command == "chat":
        return cmd_chat(settings)
    if args.command == "ask":
        _ensure_index(settings)
        return _run(args.question, settings)
    if args.command == "index":
        return cmd_index(settings)
    if args.command == "health":
        return cmd_health(settings)
    return 2


if __name__ == "__main__":
    sys.exit(main())
