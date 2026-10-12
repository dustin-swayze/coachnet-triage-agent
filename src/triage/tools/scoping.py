"""Shared partner-scoping helpers (ADR 0005), used by both mock and live tools.

The idea in one breath: the query filter is the control (it keeps other partners'
rows from ever leaving Salesforce); the post-check is the detector (it catches a
filter that silently matched the wrong field). Both fail closed with ``ScopeError``.
"""

from __future__ import annotations

import re

from triage.config import Settings
from triage.schemas.tools import CaseRecord
from triage.tools.base import ScopeError

# Salesforce API names: letter first, then letters/digits/underscores, optional __c.
# Validating them matters because field names are interpolated into SOQL text
# (values are bound parameters; names cannot be).
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(__c)?$")

CASE_NUMBER_FIELD = "CaseNumber"  # standard field, not configurable


def require_partner_code(settings: Settings) -> str:
    """Return the configured partner code or raise ``ScopeError`` if it is blank.

    Settings already rejects blanks at startup; this is the second line of defence
    for objects built by hand (tests, notebooks)."""
    code = (settings.partner_code or "").strip()
    if not code:
        raise ScopeError("partner_code is blank; refusing to query without a partner scope")
    return code


def assert_case_in_scope(record: CaseRecord, settings: Settings) -> CaseRecord:
    """Post-query detector: the record's partner must equal the configured partner.

    The filter should already have guaranteed this; if it did not (wrong field name,
    permission quirk), we would rather raise than hand another partner's case to the LLM."""
    expected = require_partner_code(settings)
    if record.partner_code != expected:
        raise ScopeError(
            f"case {record.case_number} failed the post-query partner check; refusing to return it"
        )
    return record


def _checked_field(name: str, setting: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ScopeError(f"{setting} is blank; cannot build a partner-scoped query")
    if not _FIELD_NAME.fullmatch(name):
        raise ScopeError(f"{setting}={name!r} is not a valid Salesforce field API name")
    return name


def soql_case_filter(
    settings: Settings,
    *,
    case_number: str | None = None,
    external_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Build the parameterised WHERE clause for a partner-scoped Case lookup.

    Returns ``(where, params)``, e.g.
    ``("CaseNumber = :case_number AND Partner_Code__c = :partner_code", {...})``.
    The partner predicate is always present: if it cannot be built, raise instead of
    returning a looser filter (the filter is the control, ADR 0005)."""
    partner_field = _checked_field(settings.sf_case_partner_field, "SF_CASE_PARTNER_FIELD")
    partner_code = require_partner_code(settings)

    if bool(case_number) == bool(external_id):
        raise ScopeError("provide exactly one of case_number or external_id")

    params: dict[str, str]
    if case_number:
        lookup = f"{CASE_NUMBER_FIELD} = :case_number"
        params = {"case_number": case_number}
    else:
        ext_field = _checked_field(settings.sf_case_external_id_field, "SF_CASE_EXTERNAL_ID_FIELD")
        lookup = f"{ext_field} = :external_id"
        params = {"external_id": external_id or ""}

    params["partner_code"] = partner_code
    return f"{lookup} AND {partner_field} = :partner_code", params
