import os

from dotenv import load_dotenv

# override=True: same rationale as agent/app/config.py -- a pre-set empty shell var shouldn't beat
# a real value in .env.
load_dotenv(override=True)


class Settings:
    """Env-driven config, same pattern as agent/app/config.py. Model is per-agent-role, not one
    setting -- see ARCHITECTURE.md's model-tiering decision (Red Team/Orchestrator/Documentation =
    Haiku, Judge = Sonnet)."""

    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    redteam_model: str = os.environ.get("REDTEAM_MODEL", "claude-haiku-4-5-20251001")
    redteam_escalation_model: str = os.environ.get("REDTEAM_ESCALATION_MODEL", "claude-sonnet-4-6")
    judge_model: str = os.environ.get("JUDGE_MODEL", "claude-sonnet-4-6")

    # Target: the deployed Clinical Co-Pilot / OpenEMR instance under test.
    target_base_url: str = os.environ.get("TARGET_BASE_URL", "http://localhost:8080")
    target_id: str = os.environ.get("TARGET_ID", "clinical-copilot-openemr")
    # No automated version probe exists yet (the target has no version/build-SHA endpoint) -- set
    # by hand per run until one does. Recorded on every ObservedResponse/exploit_record row so a
    # regression can tell whether the target actually changed, per ARCHITECTURE.md decision #4.
    target_version: str = os.environ.get("TARGET_VERSION", "unknown")

    # Dedicated, disposable OpenEMR login for the Red Team Agent to authenticate as -- deliberately
    # NOT the admin/pass credential used for manual testing: the cross-patient IDOR hypothesis in
    # THREAT_MODEL.md is only meaningful against an ordinary clinical-role user, since an
    # administrator may legitimately have platform-wide access already. See THREAT_MODEL.md
    # Section 2/6.
    redteam_openemr_user: str = os.environ.get("REDTEAM_OPENEMR_USER", "")
    redteam_openemr_pass: str = os.environ.get("REDTEAM_OPENEMR_PASS", "")
    redteam_openemr_site: str = os.environ.get("REDTEAM_OPENEMR_SITE", "default")

    # The exact scope string interface/modules/copilot/config.php's registered OAuth2 client
    # requests (COPILOT_SCOPE) -- submitted verbatim as individual scope[...] consent-form fields
    # in openemr_adapter.py's OAuth2 flow, rather than trying to reverse-engineer the consent
    # page's per-resource checkbox-to-scope JavaScript (confirmed live: submitting only the
    # top-level pre-checked boxes granted a token that got 401'd on every FHIR resource read --
    # the granular per-resource scopes below are what the FHIR API actually checks).
    target_oauth_scopes: list[str] = os.environ.get(
        "TARGET_OAUTH_SCOPES",
        "openid offline_access api:oemr api:fhir user/Patient.read user/Encounter.read "
        "user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read "
        "user/Observation.read user/DocumentReference.read user/document.crs "
        "user/medication.cruds user/allergy.cruds user/procedure_result_from_document.c "
        "user/document_lookup.rs",
    ).split()

    # Postgres Exploit DB.
    database_url: str = os.environ.get("DATABASE_URL", "")

    langfuse_public_key: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.environ.get("LANGFUSE_SECRET_KEY", "")
    langfuse_host: str = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")


settings = Settings()
