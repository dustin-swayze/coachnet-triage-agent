"""Optional LangSmith tracing (ADR 0006).

Invariants protected:
- Off by default, and "off" wins even if the shell has LANGSMITH_TRACING=true.
- On requires an API key (fails at startup, not mid-run).
- When on, one triage run is one trace: graph root, nodes, tools, and model calls all
  nest under it, including tools executed in the timeout worker pool.
- Outbound payloads go through the same redaction as the JSONL traces.
- The JSONL audit trail records whether LangSmith was in use.

No test touches the network: the LangSmith client is replaced with a recorder.
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest
from helpers import run
from langsmith import Client
from langsmith.run_helpers import get_current_run_tree
from pydantic import SecretStr, ValidationError

import triage.observability as obs
from triage.config import Settings


class RecordingClient(Client):
    """A real langsmith Client whose network methods are replaced by a list."""

    instances: list[RecordingClient] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.recorded: list[dict] = []
        super().__init__(api_key="test", api_url="http://127.0.0.1:9", auto_batch_tracing=False)
        RecordingClient.instances.append(self)

    def create_run(self, name, inputs, run_type, **kwargs):  # noqa: D102
        self.recorded.append(
            {
                "name": name,
                "run_type": run_type,
                "id": str(kwargs.get("id")),
                "parent": str(kwargs.get("parent_run_id")),
                "trace": str(kwargs.get("trace_id")),
                "inputs": inputs,
            }
        )

    def update_run(self, run_id, **kwargs):  # noqa: D102
        pass

    def flush(self):  # noqa: D102
        pass


@pytest.fixture
def recording_client(monkeypatch):
    RecordingClient.instances = []
    monkeypatch.setattr(obs, "Client", RecordingClient)
    return RecordingClient


def _ls_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"langsmith_enabled": True, "langsmith_api_key": SecretStr("lsv2_test_key_not_real")}
    )


def test_off_by_default(settings: Settings):
    """Shipping default: nothing leaves the machine."""
    assert settings.langsmith_enabled is False


def test_enabling_without_key_fails_at_startup():
    """A half-configured LangSmith is a config error, caught before any run."""
    with pytest.raises(ValidationError, match="LANGSMITH_API_KEY"):
        Settings(_env_file=None, langsmith_enabled=True)


def test_flag_off_wins_over_stray_library_env(settings, tools, monkeypatch, recording_client):
    """LANGSMITH_TRACING=true in the shell must not start uploads when our flag is off."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_stray_key")
    seen: list[bool] = []

    def spy(request, _inner=tools.get_sf_case):
        seen.append(get_current_run_tree() is not None)
        return _inner(request)

    result = run(
        "Case 00412345 looks like a duplicate",
        settings,
        tools=dataclasses.replace(tools, get_sf_case=spy),
    )

    assert result.state["diagnosis"] is not None
    assert seen == [False], "a run tree existed inside a tool call with LangSmith off"
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert recording_client.instances == []


def test_flag_on_traces_one_tree_with_nested_tools(settings, tools, recording_client):
    """Graph root, tool calls (run in the worker pool), and model calls share one trace."""
    result = run("Case 00412345 looks like a duplicate", _ls_settings(settings), tools=tools)

    assert result.state["diagnosis"] is not None
    (client,) = recording_client.instances
    runs = client.recorded
    assert len({r["trace"] for r in runs}) == 1, "run split into several traces"
    by_name = {r["name"]: r for r in runs}
    root = by_name["triage"]
    assert root["parent"] == "None"
    tool_runs = [r for r in runs if r["run_type"] == "tool"]
    assert {r["name"] for r in tool_runs} >= {
        "check_mule_app_status",
        "get_sf_case",
        "search_mule_logs",
        "search_runbook",
    }
    ids = {r["id"] for r in runs}
    assert all(r["parent"] in ids for r in tool_runs), "a tool run lost its parent"
    assert any(r["run_type"] == "llm" for r in runs), "model calls were not traced"


def test_flag_on_client_masks_outbound_payloads(settings, tools, recording_client):
    """The client is built with our redaction for both inputs and outputs."""
    run("Case 00412345 looks like a duplicate", _ls_settings(settings), tools=tools)
    (client,) = recording_client.instances
    assert client.init_kwargs["hide_inputs"] is obs.mask
    assert client.init_kwargs["hide_outputs"] is obs.mask
    assert client.init_kwargs["api_key"] == "lsv2_test_key_not_real"


def test_mask_redacts_secrets_and_keeps_payload_context():
    """Secrets are scrubbed; long payload excerpts survive up to the outbound limit."""
    data = {
        "question": "key sk-ant-SECRETVALUE123456 case 00412345",
        "authorization": "Bearer abcdefghijklmnop",
        "payload": "x" * 3000,
    }
    masked = obs.mask(data)
    assert "SECRETVALUE" not in json.dumps(masked)
    assert masked["authorization"] == "***"
    assert len(masked["payload"]) == 3000
    assert data["authorization"].startswith("Bearer"), "mask must not mutate its input"


def test_jsonl_records_langsmith_usage(settings, tools, recording_client):
    """The local audit trail says whether data also went to LangSmith."""
    off = run("Case 00412345", settings, tools=tools)
    on = run("Case 00412345", _ls_settings(settings), tools=tools)
    start_off = next(e for e in off.tracer.events if e["event"] == "run_start")
    start_on = next(e for e in on.tracer.events if e["event"] == "run_start")
    assert start_off["langsmith_project"] is None
    assert start_on["langsmith_project"] == "partner-triage"
