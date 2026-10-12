"""Chat model factory (constraint 7, ADR 0003).

mock -> ``ScriptedChatModel``: a real LangChain ``BaseChatModel`` whose replies are
        chosen by the *graph node* that is calling it (read from the run's tags).
live -> ``init_chat_model(model, model_provider=...)``.

Why ``init_chat_model``: one config string swaps provider and model without touching
call sites, and LangChain normalises tool calling and structured output across
providers. Why a hand-rolled fake instead of ``GenericFakeChatModel``: that model
replays replies in call order, which desyncs the moment the graph skips a branch.
Keying on node name keeps scenario scripts readable and branch-proof.

What the mock responders are: small deterministic heuristics that read the same
prompt a real model reads (the ``<evidence>`` JSON and tool results) and produce
plausible tool calls / structured output. They prove the plumbing. They are not, and
are not presented as, model reasoning. Phase 5 evals measure the real model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from itertools import count
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from triage.config import Settings

Responder = Callable[[list[BaseMessage], dict[str, Any]], AIMessage]
Script = Responder | AIMessage | Sequence[AIMessage]

_ids = count(1)


def _tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_{next(_ids)}", "type": "tool_call"}


def ai_tool_call(name: str, args: dict[str, Any]) -> AIMessage:
    """Convenience for scenario scripts and tests."""
    return AIMessage(content="", tool_calls=[_tool_call(name, args)])


class ScriptedChatModel(BaseChatModel):
    """Fake chat model keyed by graph node name.

    ``responders`` maps node name -> callable(messages, kwargs) | AIMessage | list of
    AIMessages (consumed in order for that node, last one repeats). The node name is
    the first run tag that matches a key; nodes pass ``config={"tags": [node]}``.
    """

    responders: dict[str, Any] = Field(default_factory=dict)
    model_name: str = "scripted-mock"
    _calls: list[tuple[str, int]] = PrivateAttr(default_factory=list)
    _cursor: dict[str, int] = PrivateAttr(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "scripted-mock"

    @property
    def calls(self) -> list[tuple[str, int]]:
        """(node, message_count) per call, for tests."""
        return list(self._calls)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:  # noqa: D102
        return self.bind(tools=list(tools), **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        tags = list(run_manager.tags) if run_manager is not None else []
        node = next((t for t in tags if t in self.responders), None)
        if node is None:
            raise ValueError(f"ScriptedChatModel: no scripted reply for tags {tags}")
        self._calls.append((node, len(messages)))
        script = self.responders[node]
        if callable(script):
            msg = script(messages, kwargs)
        elif isinstance(script, AIMessage):
            msg = script
        else:
            i = self._cursor.get(node, 0)
            msg = script[min(i, len(script) - 1)]
            self._cursor[node] = i + 1
        # Fresh tool-call ids each time so repeated scripted messages stay valid.
        if msg.tool_calls:
            msg = AIMessage(
                content=msg.content,
                tool_calls=[_tool_call(tc["name"], tc["args"]) for tc in msg.tool_calls],
            )
        return ChatResult(generations=[ChatGeneration(message=msg)])


# ---------------------------------------------------------------------------
# Default mock responders (heuristics standing in for the model; see module doc)
# ---------------------------------------------------------------------------
CASE_RE = re.compile(r"\b(\d{8})\b")
TICKET_RE = re.compile(r"\b(hs-\d{3,})\b", re.IGNORECASE)  # TODO(dustin): real HubSpot id format
CORR_RE = re.compile(r"\bcorr(?:elation)?(?:[ _-]?id)?\s*[=:]\s*([A-Za-z0-9-]{4,})", re.IGNORECASE)
HEALTH_WORDS = re.compile(
    r"\b(up|down|running|health|healthy|status of the (app|integration)|outage|alive|restart\w*)\b",
    re.IGNORECASE,
)


def regex_parse(text: str) -> dict[str, Any]:
    """Deterministic extraction. Used by the mock parse responder and as the live
    fallback if the model's structured output fails validation."""
    case = CASE_RE.search(text)
    ticket = TICKET_RE.search(text)
    corr = CORR_RE.search(text)
    has_ref = bool(case or ticket or corr)
    if has_ref:
        intent = "CASE_TRIAGE"
    elif HEALTH_WORDS.search(text):
        intent = "HEALTH_ONLY"
    else:
        intent = "UNCLEAR"
    return {
        "intent": intent,
        "case_number": case.group(1) if case else None,
        "external_id": ticket.group(1).lower() if ticket else None,
        "correlation_id": corr.group(1) if corr else None,
        "symptom": text.strip(),
    }


def _mock_parse(messages: list[BaseMessage], kwargs: dict[str, Any]) -> AIMessage:
    from triage.graph.prompts import extract_user_request  # noqa: PLC0415

    return ai_tool_call("ParsedRequest", regex_parse(extract_user_request(messages)))


def build_runbook_query(ev: dict[str, Any]) -> str:
    """Turn the evidence JSON into a retrieval query: symptom + error lines + case flags."""
    parts: list[str] = [ev.get("symptom") or ""]
    case = ev.get("case") or {}
    if case:
        parts.append(f"sync status {case.get('sync_status')}")
        if case.get("duplicate_of_case_number"):
            parts.append(
                f"duplicate of {case['duplicate_of_case_number']}, same HubSpot ticket created "
                f"twice, redispatch {case.get('redispatch')}"
            )
    elif ev.get("case_lookup_error"):
        parts.append("case not found in Salesforce")
    entries = (ev.get("logs") or {}).get("entries") or []
    bad = [e for e in entries if e.get("level") in ("ERROR", "WARN")]
    for e in bad[:6]:
        parts.append(f"{e.get('error_type') or ''} {e.get('message', '')}")
    if entries and not bad:
        parts.append("no errors in logs, responded 200 to HubSpot")
    return " . ".join(p for p in parts if p).strip()[:1900]


def _mock_diagnose(messages: list[BaseMessage], kwargs: dict[str, Any]) -> AIMessage:
    from triage.graph.prompts import extract_evidence  # noqa: PLC0415

    ev = extract_evidence(messages)
    tool_results = [m for m in messages if isinstance(m, ToolMessage)]
    case = ev.get("case") or {}
    entries = (ev.get("logs") or {}).get("entries") or []
    bad = [e for e in entries if e.get("level") in ("ERROR", "WARN")]
    sources = [
        s for s, present in (("get_sf_case", case), ("search_mule_logs", entries)) if present
    ]

    # "Nothing wrong" check before searching: clean logs, synced, not a duplicate.
    if (
        case
        and entries
        and not bad
        and case.get("sync_status") == "SYNCED"
        and not case.get("duplicate_of_case_number")
    ):
        return ai_tool_call(
            "Diagnosis",
            {
                "summary": f"No sync failure found for case {case.get('case_number')}: the "
                "Mule transaction completed and the case shows as synced.",
                "likely_cause": "No failure detected. The report may refer to a different "
                "case, or to downstream handling after sync.",
                "matched_runbook_ids": [],
                "suggested_fix": "Confirm the case/ticket number with the reporter and what "
                "exactly they expected to see.",
                "confidence": 0.6,
                "evidence_refs": sources,
                "needs_human": False,
            },
        )

    if not tool_results:
        return ai_tool_call("search_runbook", {"query": build_runbook_query(ev), "k": 3})

    try:
        result = json.loads(tool_results[-1].text)
        hits = result.get("hits") or []
    except (json.JSONDecodeError, AttributeError):
        hits = []
    if not hits or hits[0]["score"] < 0.15:
        return ai_tool_call(
            "Diagnosis",
            {
                "summary": "No known issue matched the evidence.",
                "likely_cause": "Unknown; the runbook has no entry that fits.",
                "matched_runbook_ids": [],
                "suggested_fix": "Escalate to the integration owner with the trace file; "
                "consider adding a runbook entry once the cause is known.",
                "confidence": 0.2,
                "evidence_refs": sources,
                "needs_human": True,
            },
        )
    top = hits[0]
    # Most telling line: first typed ERROR, else first ERROR, else first typed WARN.
    errors_ = [e for e in bad if e.get("level") == "ERROR"]
    ranked = (
        [e for e in errors_ if e.get("error_type")]
        + errors_
        + [e for e in bad if e.get("error_type")]
        + bad
    )
    key_entry = ranked[0] if ranked else None
    key_line = ""
    if key_entry:
        msg, etype = key_entry.get("message", ""), key_entry.get("error_type") or ""
        key_line = msg if etype in msg else f"{etype} {msg}".strip()
    summary = f"{top['title']}." + (f" Key log line: {key_line[:200]}" if key_line else "")
    if not sources:
        summary += " (No case or log evidence was found; this is a weak match.)"
    confidence = round(min(0.95, 0.45 + 0.5 * float(top["score"])), 2)
    if not sources:
        confidence = min(confidence, 0.35)
    return ai_tool_call(
        "Diagnosis",
        {
            "summary": summary,
            "likely_cause": top["likely_cause"],
            "matched_runbook_ids": [top["id"]],
            "suggested_fix": top["fix"],
            "confidence": confidence,
            "evidence_refs": sources + ["search_runbook"],
            "needs_human": "WRITE" in top["fix"],
        },
    )


def default_mock_responders() -> dict[str, Script]:
    return {"parse_request": _mock_parse, "diagnose": _mock_diagnose}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def get_chat_model(settings: Settings, responders: dict[str, Script] | None = None) -> Any:
    """Return a LangChain chat model for the configured mode.

    ``responders`` only applies in mock mode; tests pass scenario-specific scripts.
    """
    if settings.is_mock:
        return ScriptedChatModel(responders=responders or default_mock_responders())

    settings.require_live_credentials()
    from langchain.chat_models import init_chat_model  # noqa: PLC0415

    key = (
        settings.anthropic_api_key
        if settings.llm_provider.value == "anthropic"
        else settings.openai_api_key
    )
    return init_chat_model(
        settings.llm_model,
        model_provider=settings.llm_provider.value,
        api_key=key.get_secret_value() if key else None,
        temperature=0,
        timeout=settings.tool_timeout_seconds * 2,
        max_retries=1,
    )


def model_label(llm: Any) -> str:
    """Human-readable model name for traces."""
    for attr in ("model_name", "model"):
        value = getattr(llm, attr, None)
        if isinstance(value, str):
            return value
    return type(llm).__name__
