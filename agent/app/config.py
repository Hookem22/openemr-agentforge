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

    # TEMPORARY: stands in for the real per-session token the auth-bridge endpoint will mint later.
    # See agent-implementation.md decision #1 (session-bridge, deferred until the agent itself works).
    dev_bearer_token: str = os.environ.get("DEV_BEARER_TOKEN", "")


settings = Settings()
