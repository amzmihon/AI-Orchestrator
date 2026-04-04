"""
Application configuration — loaded from environment variables.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, BaseModel


class LLMConfig(BaseModel):
    name: str = Field(..., description="Unique name for this LLM (e.g. 'bitnet', 'qwen', 'gpt4')")
    base_url: str = Field(..., description="Base URL for the LLM endpoint")
    api_key: str = Field(default="", description="API key for this LLM (if needed)")
    model: str = Field(..., description="Model name as required by the LLM API")
    role: str = Field(default="standby", description="Role: primary, secondary, standby, etc.")
    capabilities: list[str] = Field(default_factory=list, description="Tags: ['fast', 'complex', 'no_pii', 'internal', ...]")
    temperature: float = 0.1
    max_tokens: int = 8192
    request_timeout: int = 120
    cost_per_1k_input_tokens: float = 0.0
    cost_per_1k_output_tokens: float = 0.0
    privacy_level: str = "standard"
    max_concurrent_requests: int = 10
    priority: int = 0
    tags: list[str] = Field(default_factory=list)
    cooldown_seconds: int = 60
    max_fail_count: int = 3
    provider_type: str = "openai"
    enabled: bool = True


class Settings(BaseSettings):
    """Central configuration for the AI Orchestrator."""

    # ── App ──────────────────────────────────────────────
    app_name: str = "ATL-AI Orchestrator"
    app_version: str = "1.0.0"
    debug: bool = False

    # ── Authentication Mode ──────────────────────────────
    # "jwt"    → Decode JWT locally using the Main App's public key (fastest, no network call)
    # "http"   → Call the Main App's /verify-user endpoint (original method)
    # "hybrid" → Try JWT first, fall back to HTTP if JWT fails
    auth_mode: str = Field(
        default="hybrid",
        description="Auth strategy: jwt | http | hybrid",
    )

    # ── JWT Settings (for Silent Authentication) ─────────
    jwt_secret_or_public_key: str = Field(
        default="",
        description="Main App's JWT secret (HS256) or public key (RS256). "
        "Leave empty to auto-detect from Main App's JWKS endpoint.",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm: HS256, RS256, etc.",
    )
    jwt_audience: str = Field(
        default="",
        description="Expected 'aud' claim in JWT (optional).",
    )
    jwt_issuer: str = Field(
        default="",
        description="Expected 'iss' claim in JWT (optional).",
    )

    # ── Main App Auth (HTTP fallback) ────────────────────
    main_app_base_url: str = Field(
        default="http://localhost:7000",
        description="Base URL of the main office application",
    )
    main_app_verify_endpoint: str = Field(
        default="/verify-user",
        description="Endpoint on the main app to verify user tokens",
    )
    token_cache_ttl_seconds: int = Field(
        default=300,
        description="How long to cache verified tokens (seconds)",
    )

    # ── Staging Database ─────────────────────────────────
    staging_db_host: str = "localhost"
    staging_db_port: int = 5433
    staging_db_name: str = "staging"
    staging_db_user: str = "ai_reader"
    staging_db_password: str = "changeme"
    staging_db_url_override: str = Field(
        default="",
        description="Full DB URL override (e.g. sqlite+aiosqlite:///path/to/db.sqlite3). "
        "When set, staging_db_host/port/name/user/password are ignored.",
    )

    @property
    def staging_db_url(self) -> str:
        if self.staging_db_url_override:
            return self.staging_db_url_override
        return (
            f"postgresql+asyncpg://{self.staging_db_user}:{self.staging_db_password}"
            f"@{self.staging_db_host}:{self.staging_db_port}/{self.staging_db_name}"
        )

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.staging_db_url


    # ── Multi-LLM Support ───────────────────────────────
    llms: list[LLMConfig] = Field(
        default_factory=list,
        description="List of LLM configurations (endpoint, model, role, capabilities, etc.)",
    )

    # Backward compatibility (single LLM fields)
    llm_base_url: str = Field(
        default="http://localhost:7002",
        description="Base URL of the LiteLLM proxy (legacy)",
    )
    llm_api_key: str = Field(
        default="sk-change-me",
        description="Default API key for the LiteLLM proxy (legacy)",
    )
    llm_model: str = Field(
        default="qwen-sql",
        description="Model name as registered in LiteLLM config (legacy)",
    )
    llm_fast_model: str = Field(
        default="qwen-sql-fast",
        description="Lightweight model for simple queries (legacy)",
    )
    llm_temperature: float = 0.1
    llm_max_tokens: int = 8192
    llm_request_timeout: int = 120

    # ── Qwen 3.5 Context Window ──────────────────────────
    # Qwen 3.5 hybrid attention supports 200K–1M tokens.
    # Set this to match num_ctx in litellm_config.yaml.
    llm_context_window: int = Field(
        default=131072,
        description="Max context window in tokens (Qwen 3.5 supports up to 1M)",
    )
    llm_schema_budget_tokens: int = Field(
        default=32000,
        description="Max tokens allocated for schema injection into prompts",
    )
    llm_history_budget_tokens: int = Field(
        default=16000,
        description="Max tokens allocated for conversation history in prompts",
    )

    # ── Guardrails ───────────────────────────────────────
    pii_detection_enabled: bool = Field(
        default=True,
        description="Enable PII detection and redaction on user input",
    )
    output_validation_enabled: bool = Field(
        default=True,
        description="Enable structured output validation against schema",
    )
    max_sql_retries: int = Field(
        default=2,
        description="Max SQL regeneration attempts on execution failure",
    )

    # ── Episodic Memory ──────────────────────────────────
    episodic_memory_enabled: bool = Field(
        default=True,
        description="Enable episodic memory for reasoning chain recording",
    )
    episodic_memory_max_episodes: int = Field(
        default=50,
        description="Max reasoning episodes to retain per user",
    )

    # ── Per-Department LLM API Keys ──────────────────────
    # JSON mapping: {"HR": "sk-hr-key", "Sales": "sk-sales-key", ...}
    # If a department key exists, it's used instead of llm_api_key.
    llm_department_keys: str = Field(
        default="{}",
        description='JSON dict of department→API key, e.g. {"HR":"sk-hr-xxx","Sales":"sk-sales-xxx"}',
    )

    # ── Ollama ───────────────────────────────────────────
    ollama_url: str = Field(
        default="http://localhost:11434",
        description="Base URL of the Ollama engine",
    )

    # ── Rate Limiting ────────────────────────────────────
    rate_limit_per_minute: int = 10

    # ── Admin Authentication ─────────────────────────────
    admin_jwt_secret: str = Field(
        default="orchestrator-admin-secret-change-me",
        description="Secret key for orchestrator admin JWT tokens (separate from ERP JWT).",
    )
    admin_jwt_ttl: int = Field(
        default=28800,
        description="Admin JWT token TTL in seconds (default: 8 hours).",
    )
    admin_default_username: str = Field(
        default="admin",
        description="Default admin username seeded on first startup.",
    )
    admin_default_password: str = Field(
        default="admin123",
        description="Default admin password seeded on first startup.",
    )
    erp_admin_roles: str = Field(
        default="admin,owner,gm",
        description="Comma-separated ERP roles that receive orchestrator admin access.",
    )

    # ── Paths ────────────────────────────────────────────
    schema_map_path: str = "schema_map.json"
    role_config_path: str = "rbac/role_config.json"

    model_config = {
        "env_file": ".env",
        "env_prefix": "ATL_",
        "extra": "ignore",
    }


# Singleton
settings = Settings()

# ── Config Migration & Legacy Bridge (Phase 1.5) ─────────────
import pathlib
import json

def check_and_migrate_legacy_config():
    if not settings.llms:
        connections_path = pathlib.Path(__file__).parent / "connections.json"
        if connections_path.exists():
            try:
                with open(connections_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for llm_data in data.get("llm_providers", []):
                        if llm_data.get("is_active"):
                            settings.llms.append(LLMConfig(
                                name=llm_data.get("id", "legacy-llm"),
                                base_url=llm_data.get("base_url", settings.llm_base_url),
                                api_key=llm_data.get("api_key", settings.llm_api_key),
                                model=llm_data.get("model", settings.llm_model),
                                role="primary",
                                temperature=llm_data.get("temperature", settings.llm_temperature),
                                max_tokens=llm_data.get("max_tokens", settings.llm_max_tokens),
                                provider_type=llm_data.get("provider", "openai"),
                                enabled=True
                            ))
                            break
            except Exception:
                pass
                
        # Fallback to pure env vars if still empty
        if not settings.llms:
            settings.llms.append(LLMConfig(
                name="legacy-primary",
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                role="primary",
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                provider_type="openai",
                enabled=True
            ))

check_and_migrate_legacy_config()
