"""Runtime configuration, loaded from environment / .env via pydantic-settings.

Design notes
- One Settings object, validated at startup. A bad or missing value fails *before*
  any tool runs (fail-closed, ADR 0005).
- Secrets are ``SecretStr`` so they never appear in ``repr()``, logs, or traces.
- Field API names are config, not code, because they are placeholders until the API
  contract in the work repo is read (TODO(dustin)).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TriageMode(StrEnum):
    MOCK = "mock"
    LIVE = "live"


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- mode / llm -----------------------------------------------------------
    triage_mode: TriageMode = TriageMode.MOCK
    llm_provider: LLMProvider = LLMProvider.ANTHROPIC  # TODO(dustin): confirm with team lead
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # ---- partner scoping (ADR 0005) -------------------------------------------
    partner_code: str = Field(default="CNET", min_length=1)
    partner_name: str = "Coach Net"

    # ---- agent limits (constraint 5) ------------------------------------------
    step_cap: int = Field(default=8, ge=1, le=50)
    tool_timeout_seconds: float = Field(default=15.0, gt=0)
    run_budget_seconds: float = Field(default=60.0, gt=0)
    trace_dir: str = "traces"

    # ---- runbook / retrieval (ADR 0004) ---------------------------------------
    runbook_dir: str = "docs/runbook"
    index_dir: str = "data/index"
    # hashing = deterministic local bag-of-words embedder (free, lexical, used in mock).
    # TODO(dustin): pick a live embeddings provider (Anthropic has no embeddings API;
    # options: keep hashing for a ~20-entry runbook, Voyage, or OpenAI per TrxNow policy).
    embeddings_provider: Literal["hashing", "openai", "voyage"] = "hashing"

    # ---- mock-mode knobs (ignored in live mode) ------------------------------
    # healthy | down | deploying | restart_loop | status_error
    mock_app_state: str = "healthy"

    # ---- salesforce (read-only integration user) ------------------------------
    sf_instance_url: str | None = None
    sf_client_id: str | None = None
    sf_client_secret: SecretStr | None = None
    sf_username: str | None = None
    sf_password: SecretStr | None = None  # TODO(dustin): prefer JWT bearer flow
    sf_api_version: str = "v62.0"
    # Field API names. TODO(dustin): confirm against API contract / XMLs in work repo.
    sf_case_partner_field: str = "Partner_Code__c"
    sf_case_external_id_field: str = "HubSpot_Ticket_Id__c"
    sf_case_sync_status_field: str = "Sync_Status__c"
    sf_case_last_sync_field: str = "Last_Sync_At__c"
    sf_case_correlation_field: str = "Mule_Correlation_Id__c"
    sf_case_redispatch_field: str = "Redispatch__c"

    # ---- anypoint (view-only connected app) -----------------------------------
    anypoint_client_id: str | None = None
    anypoint_client_secret: SecretStr | None = None
    anypoint_org_id: str | None = None
    anypoint_env_id: str | None = None
    anypoint_private_space_id: str | None = None
    anypoint_base_url: str = "https://anypoint.mulesoft.com"
    mule_app_name: str = Field(default="coachnet-saas-api", min_length=1)  # TODO(dustin)

    @field_validator("partner_code", "mule_app_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank (fail-closed scoping, ADR 0005)")
        return v.strip()

    @property
    def is_mock(self) -> bool:
        return self.triage_mode is TriageMode.MOCK

    def require_live_credentials(self) -> None:
        """Raise if live mode is selected but required secrets are missing.

        Called once at startup in live mode. Mock mode needs nothing.
        """
        if self.is_mock:
            return
        missing: list[str] = []
        if self.llm_provider is LLMProvider.ANTHROPIC and not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if self.llm_provider is LLMProvider.OPENAI and not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        # Integration credentials are checked per tool in Phases 2-4 so that a partially
        # configured live env can still run the tools that are ready.
        if missing:
            raise RuntimeError(f"TRIAGE_MODE=live but missing: {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
