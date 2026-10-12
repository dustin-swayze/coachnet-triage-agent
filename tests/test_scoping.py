"""Fail-closed partner scoping (ADR 0005): unprovable scope gives an error, never data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from helpers import make_llm, run
from pydantic import ValidationError

from triage.config import Settings
from triage.runbook.tool import RunbookSearch
from triage.schemas.tools import (
    CaseRecord,
    GetCaseRequest,
    RunbookSearchRequest,
    SearchLogsRequest,
)
from triage.tools.base import NotFoundError, ScopeError
from triage.tools.mock import MockGetSfCase
from triage.tools.scoping import assert_case_in_scope, soql_case_filter

# ---------------------------------------------------------------------------
# scoping helpers
# ---------------------------------------------------------------------------


def _blank_partner(settings: Settings) -> Settings:
    # Settings validation forbids a blank code, so build the bad object by hand.
    return settings.model_copy(update={"partner_code": ""})


def test_soql_filter_refuses_blank_partner_code(settings: Settings):
    """No partner code -> no query at all (never a looser, unscoped filter)."""
    with pytest.raises(ScopeError):
        soql_case_filter(_blank_partner(settings), case_number="00412345")


def test_soql_filter_refuses_whitespace_partner_code(settings: Settings):
    """Whitespace-only is blank too."""
    s = settings.model_copy(update={"partner_code": "   "})
    with pytest.raises(ScopeError):
        soql_case_filter(s, case_number="00412345")


@pytest.mark.parametrize("bad", ["x; DROP", "Partner Code__c", "1abc__c", "a'b", "a-b"])
def test_soql_filter_refuses_invalid_field_names(settings: Settings, bad: str):
    """Field names are interpolated into SOQL text, so anything not an API name is refused."""
    s = settings.model_copy(update={"sf_case_partner_field": bad})
    with pytest.raises(ScopeError):
        soql_case_filter(s, case_number="00412345")


def test_soql_filter_refuses_blank_field_name(settings: Settings):
    """A blank partner field name cannot produce a partner predicate."""
    s = settings.model_copy(update={"sf_case_partner_field": ""})
    with pytest.raises(ScopeError):
        soql_case_filter(s, case_number="00412345")


def test_soql_filter_refuses_invalid_external_id_field(settings: Settings):
    """The external-id field name is validated like the partner field."""
    s = settings.model_copy(update={"sf_case_external_id_field": "x; DROP"})
    with pytest.raises(ScopeError):
        soql_case_filter(s, external_id="hs-55501")


def test_soql_filter_requires_exactly_one_lookup_key(settings: Settings):
    """Zero or two lookup keys are refused rather than guessed at."""
    with pytest.raises(ScopeError):
        soql_case_filter(settings)
    with pytest.raises(ScopeError):
        soql_case_filter(settings, case_number="00412345", external_id="hs-1")


def test_soql_filter_always_contains_partner_clause(settings: Settings):
    """The happy path returns a bound partner predicate for both lookup kinds."""
    where, params = soql_case_filter(settings, case_number="00412345")
    assert where == "CaseNumber = :case_number AND Partner_Code__c = :partner_code"
    assert params == {"case_number": "00412345", "partner_code": "CNET"}

    where, params = soql_case_filter(settings, external_id="hs-55501")
    assert where == "HubSpot_Ticket_Id__c = :external_id AND Partner_Code__c = :partner_code"
    assert params == {"external_id": "hs-55501", "partner_code": "CNET"}


def _case(partner: str) -> CaseRecord:
    now = datetime.now(UTC)
    return CaseRecord(
        case_id="5008d00000AbC999AAA",
        case_number="00400001",
        partner_code=partner,
        subject="x",
        status="New",
        created_at=now,
        last_modified_at=now,
        source="mock",
    )


def test_post_query_check_rejects_other_partners_case(settings: Settings):
    """Detector: a record from another partner is refused even if the filter let it through."""
    with pytest.raises(ScopeError):
        assert_case_in_scope(_case("ACME"), settings)


def test_post_query_check_rejects_empty_partner_on_record(settings: Settings):
    """A record with no partner code cannot be proven in scope."""
    with pytest.raises(ScopeError):
        assert_case_in_scope(_case(""), settings)


def test_post_query_check_accepts_own_partner(settings: Settings):
    """Control: the check passes the record through unchanged for the configured partner."""
    rec = _case("CNET")
    assert assert_case_in_scope(rec, settings) is rec


def test_blank_partner_code_rejected_at_startup():
    """Settings refuses a blank partner code before any tool can run."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, partner_code="   ")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, partner_code="")


# ---------------------------------------------------------------------------
# mock get_sf_case
# ---------------------------------------------------------------------------


def test_other_partner_case_looks_like_not_found(settings: Settings):
    """Another partner's case is indistinguishable from a missing one (no existence leak)."""
    with pytest.raises(NotFoundError) as exc:
        MockGetSfCase(settings)(GetCaseRequest(case_number="00499999"))
    assert "ACME" not in str(exc.value)
    assert "acme" not in str(exc.value).lower()


def test_other_partner_case_by_external_id_looks_like_not_found(settings: Settings):
    """Same invariant for lookup by HubSpot ticket id."""
    with pytest.raises(NotFoundError) as exc:
        MockGetSfCase(settings)(GetCaseRequest(external_id="hs-70001"))
    assert "ACME" not in str(exc.value)


def test_null_partner_case_is_not_found(settings: Settings):
    """A case with no partner code is excluded by the filter (null != CNET)."""
    with pytest.raises(NotFoundError):
        MockGetSfCase(settings)(GetCaseRequest(case_number="00488888"))


def test_misconfigured_partner_field_fails_closed(settings: Settings):
    """If the partner field does not exist on Case, scope is unprovable -> ScopeError."""
    s = settings.model_copy(update={"sf_case_partner_field": "Wrong__c"})
    with pytest.raises(ScopeError):
        MockGetSfCase(s)(GetCaseRequest(case_number="00412345"))


def test_misconfigured_external_id_field_fails_closed(settings: Settings):
    """Searching by external id with a nonexistent field also fails closed."""
    s = settings.model_copy(update={"sf_case_external_id_field": "Wrong__c"})
    with pytest.raises(ScopeError):
        MockGetSfCase(s)(GetCaseRequest(external_id="hs-55501"))


def test_cnet_case_invisible_to_another_partner(settings: Settings):
    """Scoping follows config: with partner ACME, the CNET case is not found."""
    s = settings.model_copy(update={"partner_code": "ACME"})
    with pytest.raises(NotFoundError):
        MockGetSfCase(s)(GetCaseRequest(case_number="00412345"))


def test_own_partner_case_is_returned(settings: Settings):
    """Control for the negatives above: the CNET case is found for CNET."""
    rec = MockGetSfCase(settings)(GetCaseRequest(case_number="00412345"))
    assert rec.partner_code == "CNET"
    assert rec.case_number == "00412345"


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------


def test_other_partner_case_end_to_end_reveals_nothing(settings: Settings):
    """Asking about another partner's case gives a found-nothing answer and no leak."""
    llm = make_llm()
    result = run("Look at case 00499999", settings, llm=llm)

    assert "found nothing" in result.response
    assert "ACME" not in result.response
    assert "ACME" not in result.tracer.path.read_text(encoding="utf-8")
    assert result.state["diagnosis"] is None
    assert result.state["case"] is None
    assert "diagnose" not in [node for node, _ in llm.calls]
    assert "hs-70001" not in result.response  # the other partner's ticket id


def test_null_partner_case_end_to_end_reveals_nothing(settings: Settings):
    """Same for a case with no partner code."""
    result = run("Look at case 00488888", settings)
    assert "found nothing" in result.response
    assert "hs-88888" not in result.response
    assert result.state["diagnosis"] is None


def test_misconfigured_scope_end_to_end_returns_error_not_data(settings: Settings):
    """A scope failure surfaces as ScopeError evidence; no case data reaches the answer."""
    s = settings.model_copy(update={"sf_case_partner_field": "Wrong__c"})
    result = run("Look at case 00412345", s)
    assert [e.error_type for e in result.state["errors"] if e.tool == "get_sf_case"] == [
        "ScopeError"
    ]
    assert result.state["case"] is None


# ---------------------------------------------------------------------------
# logs / runbook scoping
# ---------------------------------------------------------------------------


def test_log_search_requires_an_identifier():
    """No bare window dumps: a log request without any identifier is invalid by construction."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        SearchLogsRequest(start=now - timedelta(hours=1), end=now)


def test_log_search_rejects_inverted_window():
    """End must be after start."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        SearchLogsRequest(case_number="00412345", start=now, end=now - timedelta(hours=1))


def test_runbook_search_is_scoped_to_partner_or_all(settings: Settings):
    """A different partner only sees 'all'-partner entries, never CNET-specific ones."""
    acme = settings.model_copy(update={"partner_code": "ACME"})
    result = RunbookSearch(acme)(RunbookSearchRequest(query="duplicate case redispatch", k=10))
    assert result.hits, "partner=all entries should still be visible"
    assert {h.partner for h in result.hits} == {"all"}
    assert {h.id for h in result.hits} == {"cnet-005", "cnet-006", "cnet-007"}


def test_runbook_search_for_cnet_includes_partner_specific_entries(settings: Settings):
    """Control: CNET sees its own entries as well as 'all'."""
    result = RunbookSearch(settings)(RunbookSearchRequest(query="duplicate case redispatch", k=10))
    assert {h.partner for h in result.hits} == {"CNET", "all"}
