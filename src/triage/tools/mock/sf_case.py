"""Mock ``get_sf_case``: a tiny in-memory imitation of a partner-scoped SOQL query.

It reads the same config field names the live tool will, so a misconfigured field
behaves like real SOQL (INVALID_FIELD) instead of silently succeeding.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from triage.config import Settings
from triage.schemas.tools import CaseRecord, GetCaseRequest, SyncStatusValue
from triage.tools.base import NotFoundError, ScopeError, UpstreamError
from triage.tools.mock._fixtures import load_fixture
from triage.tools.scoping import assert_case_in_scope, soql_case_filter

# Fixture case numbers that trigger simulated infrastructure behaviour.
UPSTREAM_ERROR_CASE = "00466666"
SLOW_CASE = "00477777"


def _sync_status(raw: Any) -> SyncStatusValue:
    try:
        return SyncStatusValue(raw)
    except ValueError:
        return SyncStatusValue.UNKNOWN


class MockGetSfCase:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._records: list[dict[str, Any]] = load_fixture("cases.json")

    def __call__(self, request: GetCaseRequest) -> CaseRecord:
        s = self._settings
        # 1. Build the filter first; if it cannot be built we never "query" (fail closed).
        _where, params = soql_case_filter(
            s, case_number=request.case_number, external_id=request.external_id
        )

        # 2. Real SOQL rejects unknown fields (INVALID_FIELD); we surface that as ScopeError
        #    because without the partner field we cannot prove scoping.
        self._require_field(s.sf_case_partner_field, "cannot confirm partner scoping")
        if request.external_id:
            self._require_field(s.sf_case_external_id_field, "cannot search by external id")

        # 3. The "WHERE": other partners' and null-partner rows are filtered out, and the
        #    caller cannot tell they exist.
        partner = params["partner_code"]
        in_scope = [r for r in self._records if r.get(s.sf_case_partner_field) == partner]
        if request.case_number:
            matches = [r for r in in_scope if r["CaseNumber"] == params["case_number"]]
        else:
            ext_field = s.sf_case_external_id_field
            matches = [r for r in in_scope if r.get(ext_field) == params["external_id"]]
        if not matches:
            target = request.case_number or request.external_id
            raise NotFoundError(f"no {params['partner_code']} case found for {target}")

        # 4. Simulated upstream behaviour.
        chosen = max(matches, key=lambda r: r["CreatedDate"])  # newest wins on external-id hits
        if chosen["CaseNumber"] == UPSTREAM_ERROR_CASE:
            raise UpstreamError(
                "Salesforce returned 503 Service Unavailable",
                status_code=503,
                body_excerpt="SERVER_UNAVAILABLE: simulated outage",
                retryable=True,
            )
        if chosen["CaseNumber"] == SLOW_CASE:
            # The graph, not the tool, enforces timeouts; we just take too long.
            time.sleep(s.tool_timeout_seconds + 0.5)

        record = self._to_record(chosen, self._duplicate_of(chosen, in_scope))
        # 5. Post-query detector (ADR 0005).
        return assert_case_in_scope(record, s)

    # ------------------------------------------------------------------
    def _require_field(self, name: str, why: str) -> None:
        if not any(name in r for r in self._records):
            raise ScopeError(f"{why}: field {name} not found on Case")

    def _duplicate_of(self, chosen: dict[str, Any], in_scope: list[dict[str, Any]]) -> str | None:
        """CaseNumber of the oldest in-scope Case sharing the external id, if it is older."""
        ext_field = self._settings.sf_case_external_id_field
        ext = chosen.get(ext_field)
        if not ext:
            return None
        siblings = [r for r in in_scope if r.get(ext_field) == ext]
        oldest = min(siblings, key=lambda r: r["CreatedDate"])
        if oldest["CreatedDate"] < chosen["CreatedDate"]:
            return str(oldest["CaseNumber"])
        return None

    def _to_record(self, raw: dict[str, Any], duplicate_of: str | None) -> CaseRecord:
        s = self._settings
        last_sync: datetime | None = raw.get(s.sf_case_last_sync_field)
        return CaseRecord(
            case_id=raw["Id"],
            case_number=raw["CaseNumber"],
            partner_code=raw.get(s.sf_case_partner_field) or "",
            subject=raw["Subject"],
            status=raw["Status"],
            created_at=raw["CreatedDate"],
            last_modified_at=raw["LastModifiedDate"],
            external_id=raw.get(s.sf_case_external_id_field),
            sync_status=_sync_status(raw.get(s.sf_case_sync_status_field)),
            last_sync_at=last_sync,
            correlation_id=raw.get(s.sf_case_correlation_field),
            redispatch=raw.get(s.sf_case_redispatch_field),
            duplicate_of_case_number=duplicate_of,
            source="mock",
        )
