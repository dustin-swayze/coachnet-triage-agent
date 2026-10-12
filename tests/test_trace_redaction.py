"""Traces are the artifact most likely to be pasted into Slack, so secrets must never reach disk."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from helpers import run
from pydantic import BaseModel, SecretStr

from triage.config import Settings
from triage.tracing import MAX_STRING, REDACTED, Tracer, redact

# ---------------------------------------------------------------------------
# redact()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["password", "api_key", "apiKey", "API-KEY", "Authorization", "access_token", "client_secret"],
)
def test_secret_looking_keys_are_masked(key: str):
    """Whatever the value looks like, a secret-looking key is masked."""
    assert redact({key: "plain-looking-value"}) == {key: REDACTED}


def test_empty_secret_values_are_left_alone():
    """None / '' under a secret key carry no secret, so they stay visible (useful for debugging)."""
    assert redact({"password": None, "api_key": ""}) == {"password": None, "api_key": ""}


def test_secretstr_is_never_rendered():
    """SecretStr is masked even under an innocent key and never leaks via repr."""
    out = redact({"note": SecretStr("hunter2-super-secret")})
    assert out == {"note": REDACTED}
    assert "hunter2" not in json.dumps(out)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("calling with sk-ant-abc123456789 now", "sk-ant-abc123456789"),
        ("Authorization: Bearer abcdefghijkl123 sent", "abcdefghijkl123"),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMTIzNDU2 end",
            "eyJhbGciOiJIUzI1NiJ9",
        ),
        ("retry with access_token=xyz123 failed", "xyz123"),
        ('body {"client_secret": "s3cr3tvalue"}', "s3cr3tvalue"),
        ("00D5g000001AbCd!AQEAQBkLm3n4o5p6q7r8s9t0uVwXyZ", "AQEAQBkLm3n4o5p6q7r8s9t0uVwXyZ"),
    ],
)
def test_secrets_inside_free_text_are_scrubbed(text: str, secret: str):
    """Secret-looking substrings are removed from ordinary strings, keeping surrounding text."""
    out = redact({"message": text})["message"]
    assert secret not in out
    assert REDACTED in out


def test_free_text_scrub_keeps_the_rest_of_the_sentence():
    """Scrubbing is surgical: non-secret words around the secret survive."""
    out = redact("key sk-ant-abc123456789 used for case 00412345")
    assert out.startswith("key ")
    assert out.endswith("used for case 00412345")


@pytest.mark.parametrize("key", ["phone", "email", "Contact_Phone", "customer_name", "vin"])
def test_pii_keys_are_masked(key: str):
    """PII fields (phone, email, ...) are masked regardless of case."""
    assert redact({key: "555-0100"}) == {key: REDACTED}


def test_long_strings_are_truncated_with_a_marker():
    """Strings over 500 chars are cut and the marker says how much was dropped."""
    out = redact("x" * 1200)
    assert out == "x" * MAX_STRING + "...[+700 chars]"


def test_custom_max_string_is_honoured():
    """The prompt builder raises the limit for payload excerpts."""
    assert redact("y" * 50, max_string=10) == "y" * 10 + "...[+40 chars]"


def test_nested_structures_are_redacted_recursively():
    """Secrets are found inside dicts, lists, and tuples at any depth."""
    data = {
        "outer": [
            {"headers": {"Authorization": "Bearer abcdefghijkl123"}},
            ("ok", {"password": "pw"}),
        ],
        "n": 3,
        "flag": True,
        "nothing": None,
    }
    out = redact(data)
    assert out["outer"][0]["headers"]["Authorization"] == REDACTED
    assert out["outer"][1][1]["password"] == REDACTED
    assert out["outer"][1][0] == "ok"
    assert (out["n"], out["flag"], out["nothing"]) == (3, True, None)
    json.dumps(out)  # JSON-safe


def test_pydantic_models_are_dumped_then_redacted():
    """Models (tool requests/results) are accepted; their secret fields are masked."""

    class Creds(BaseModel):
        user: str
        client_secret: SecretStr
        email: str

    out = redact(Creds(user="svc", client_secret=SecretStr("abc"), email="a@b.c"))
    assert out == {"user": "svc", "client_secret": REDACTED, "email": REDACTED}


def test_redact_does_not_mutate_its_input():
    """redact returns a copy; the caller's data (which the graph still uses) is untouched."""
    data = {"password": "pw", "msg": "sk-ant-abc123456789", "nested": [{"email": "a@b.c"}]}
    snapshot = copy.deepcopy(data)
    redact(data)
    assert data == snapshot


def test_token_usage_counts_survive_redaction():
    """Usage metadata is not secret; masking it would blind cost tracking in live traces."""
    usage = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
    assert redact({"usage": usage}) == {"usage": usage}


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------


def test_tracer_writes_redacted_json_lines(tmp_path):
    """Each event is one valid JSON line, redacted before it reaches disk, mirrored in memory."""
    tracer = Tracer(tmp_path, "abc123")
    rec = tracer.event(
        "tool_call", tool="t", inputs={"api_key": "k", "q": "Bearer abcdefghijkl123"}
    )

    lines = tracer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == rec == tracer.events[0]
    assert rec["inputs"] == {"api_key": REDACTED, "q": REDACTED}
    assert rec["run_id"] == "abc123"


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------


def test_secret_in_user_question_never_reaches_the_trace_file(settings: Settings):
    """A key pasted into the question is scrubbed from every line of the on-disk trace."""
    result = run("my key is sk-ant-SECRETVALUE123456 case 00412345", settings)

    raw = result.tracer.path.read_text(encoding="utf-8")
    assert "SECRETVALUE" not in raw
    assert "SECRETVALUE" not in json.dumps(result.tracer.events, default=str)
    # ...and the run still did its job.
    assert result.state["case"] is not None


def test_trace_file_structure(settings: Settings):
    """Every line is JSON with ts/run_id/event; one run_start, one run_end; tool_calls complete."""
    result = run("Case 00412345 looks like a duplicate", settings)

    lines = result.tracer.path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]  # every line is valid JSON
    assert len(records) == len(result.tracer.events)
    run_id = result.state["run_id"]
    for rec in records:
        assert {"ts", "run_id", "event"} <= rec.keys()
        assert rec["run_id"] == run_id

    kinds = [r["event"] for r in records]
    assert kinds.count("run_start") == 1
    assert kinds.count("run_end") == 1
    assert kinds[0] == "run_start"
    assert kinds[-1] == "run_end"

    tool_calls = [r for r in records if r["event"] == "tool_call"]
    assert len(tool_calls) == result.state["steps_used"] >= 3
    for rec in tool_calls:
        assert {"tool", "inputs", "latency_ms", "error"} <= rec.keys()
        assert isinstance(rec["latency_ms"], int)


def test_run_end_event_summarises_the_outcome(settings: Settings):
    """run_end carries steps, incomplete flag, and matched runbook ids for later analysis."""
    result = run("Case 00412345 looks like a duplicate", settings)
    end = result.tracer.events[-1]
    assert end["event"] == "run_end"
    assert end["steps_used"] == result.state["steps_used"]
    assert end["incomplete"] is False
    assert end["matched_runbook_ids"] == ["cnet-001"]


def test_trace_file_lives_in_the_configured_trace_dir(settings: Settings):
    """Traces go to settings.trace_dir/<run_id>.jsonl, so tests never write into the repo."""
    result = run("Is the Coach Net app up?", settings)
    assert result.tracer.path.parent == Path(settings.trace_dir)
    assert result.tracer.path.name == f"{result.state['run_id']}.jsonl"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_repr_does_not_leak_api_keys(monkeypatch: pytest.MonkeyPatch):
    """ANTHROPIC_API_KEY / SF secrets from the environment never appear in repr, str, or dumps."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ENVSECRET999999")
    monkeypatch.setenv("SF_CLIENT_SECRET", "sf-ENVSECRET888888")
    s = Settings(_env_file=None)

    assert s.anthropic_api_key is not None  # it was loaded...
    for rendered in (repr(s), str(s), json.dumps(s.model_dump(mode="json"))):
        assert "ENVSECRET" not in rendered  # ...but never rendered


def test_settings_explicit_secret_is_masked():
    """Same when the key is passed directly."""
    s = Settings(_env_file=None, anthropic_api_key="sk-ant-DIRECTSECRET777777")
    assert "DIRECTSECRET" not in repr(s)
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-DIRECTSECRET777777"
