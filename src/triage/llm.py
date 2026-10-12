"""Chat model and embeddings factory (constraint 7, ADR 0003).

mock  -> ScriptedChatModel (replies keyed by node name from config tags) + FakeEmbeddings
live  -> langchain.chat_models.init_chat_model(f"{provider}:{model}") + provider embeddings

Why ``init_chat_model``: one string swaps provider and model without touching call
sites; LangChain normalises tool-calling and structured output across providers.
"""

from __future__ import annotations

from typing import Any

from triage.config import Settings


def get_chat_model(settings: Settings, scenario: dict[str, Any] | None = None) -> Any:
    """Return a LangChain BaseChatModel.

    ``scenario`` is only used in mock mode: a mapping of node name -> scripted reply
    (text, tool calls, or a structured-output dict). Part B defines the format and
    ships one scenario file per seed runbook entry under tests/scenarios/.
    """
    raise NotImplementedError("Session 1 Part B")


def get_embeddings(settings: Settings) -> Any:
    """Return a LangChain Embeddings implementation. Deterministic fake in mock mode."""
    raise NotImplementedError("Session 1 Part B")
