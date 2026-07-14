import os

from dotenv import load_dotenv

# override=True: some shells (including this dev environment's) pre-set ANTHROPIC_API_KEY="" in
# the environment, and load_dotenv() otherwise leaves existing env vars alone -- which would make
# the real key in .env silently lose to the empty shell value.
load_dotenv(override=True)


class Settings:
    """Env-driven config. Model is intentionally swappable without a code change
    (see agent-implementation.md decision #4: start with Sonnet, keep it easy to try others)."""

    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    fhir_base_url: str = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/apis/default/fhir")
    # Week 2: OpenEMR's standard (non-FHIR) REST API -- used for document upload and the new
    # procedure_result_from_document write path, neither of which exists in the FHIR API (see
    # W2_ARCHITECTURE.md Section 2).
    oemr_api_base_url: str = os.environ.get("OEMR_API_BASE_URL", "http://localhost:8080/apis/default/api")

    # TEMPORARY: stands in for the real per-session token the auth-bridge endpoint will mint later.
    # See agent-implementation.md decision #1 (session-bridge, deferred until the agent itself works).
    dev_bearer_token: str = os.environ.get("DEV_BEARER_TOKEN", "")

    # Salt used to hash the FHIR patient UUID before it's sent to Langfuse Cloud as a session_id
    # grouping key (see PHI_AUDIT.md). Not a security control -- just keeps the raw patient
    # identifier out of the third-party trace payload. If unset, falls back to a fixed dev-only
    # string (documented in .env.example); set a real random value in production.
    langfuse_session_salt: str = os.environ.get("LANGFUSE_SESSION_SALT", "dev-only-unset-salt")

    # Week 2 hybrid RAG (W2_ARCHITECTURE.md Section 4): one vendor for both embeddings and rerank
    # instead of adding Cohere as a second vendor (see W2_ARCHITECTURE.md Section 12).
    voyage_api_key: str = os.environ.get("VOYAGE_API_KEY", "")
    voyage_embed_model: str = os.environ.get("VOYAGE_EMBED_MODEL", "voyage-3-lite")
    voyage_rerank_model: str = os.environ.get("VOYAGE_RERANK_MODEL", "rerank-2")


settings = Settings()
