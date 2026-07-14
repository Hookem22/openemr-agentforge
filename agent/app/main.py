import base64
import time
from typing import Literal

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from langfuse import get_client
from pydantic import BaseModel, field_validator

from .config import settings
from .graph import run_turn
from .ingestion import IngestionError, attach_and_extract
from .rag import _voyage_client, load_corpus

app = FastAPI(title="Clinical Co-Pilot Agent")


class PendingDocumentInput(BaseModel):
    """A document attached to this chat turn for the intake-extractor worker to process inline
    (W2_ARCHITECTURE.md Section 3), as opposed to the standalone /ingest upload flow -- use this
    when the extraction needs to inform the *same* turn's answer (e.g. "summarize this lab and
    compare to guideline targets" in one message)."""

    data_base64: str
    filename: str
    doc_type: Literal["lab_pdf", "intake_form"]
    mimetype: str = "application/pdf"


class ChatRequest(BaseModel):
    patient_id: str  # FHIR patient uuid (Week 1 convention)
    patient_pid: str | None = None  # OpenEMR-native int pid, required only when pending_document is set
    message: str
    conversation_history: list[dict] = []
    pending_document: PendingDocumentInput | None = None

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        # System-boundary validation: a blank message passed straight through would hit the
        # Anthropic API's "messages must have non-empty content" rule and raise mid-turn (a real
        # crash caught by eval/test_boundary_conditions.py). Reject cleanly here instead.
        if not v.strip():
            raise ValueError("message must not be empty")
        return v


class ChatResponse(BaseModel):
    verified_claims: list[dict]
    stripped_claims: list[dict]
    tool_failures: list[dict]
    strip_rate: float
    conversation_history: list[dict]
    handoff_log: list[dict]
    correlation_id: str


def _resolve_bearer_token(authorization: str | None) -> str:
    # TEMPORARY: until the auth-bridge endpoint exists (agent-implementation.md decision #1), the
    # bearer token comes from either an explicit Authorization header (preferred, so this already
    # works the same way once the bridge exists) or the dev-only fallback token from settings.
    bearer_token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization.split(" ", 1)[1]
    bearer_token = bearer_token or settings.dev_bearer_token
    if not bearer_token:
        raise HTTPException(status_code=401, detail="No bearer token provided (Authorization header or DEV_BEARER_TOKEN)")
    return bearer_token


@app.get("/health")
def health():
    return {"status": "ok"}


_READY_CACHE_TTL_SECONDS = 60.0  # avoid spending real Voyage tokens / hammering OpenEMR on every poll
_ready_cache: dict = {"timestamp": 0.0, "result": None}


def _check_core() -> tuple[str, str | None]:
    """The FHIR chat flow's own hard dependencies (Week 1) -- if either is broken, the service is
    genuinely down, not merely degraded."""
    if not settings.anthropic_api_key:
        return "down", "ANTHROPIC_API_KEY is not set"
    try:
        # 8s, not 3s: the real /metadata response is a large FHIR CapabilityStatement -- measured
        # ~4.3s against the deployed Railway instance, so 3s was a false-positive "down" waiting to
        # happen on any normal network variance, not an actual outage signal.
        resp = httpx.get(f"{settings.fhir_base_url}/metadata", timeout=8.0)
        if resp.status_code >= 500:
            return "down", f"OpenEMR FHIR metadata returned {resp.status_code}"
        return "ok", None
    except httpx.HTTPError as exc:
        return "down", f"OpenEMR FHIR endpoint unreachable: {exc}"


def _check_document_storage() -> tuple[str, str | None]:
    """W2_ARCHITECTURE.md Section 9 -- OpenEMR's standard (non-FHIR) API, used by document
    upload/procedure-result/medication/allergy write paths (Stage 1). A distinct check from
    `_check_core`'s FHIR reachability since this is a different base URL/API surface."""
    try:
        resp = httpx.get(f"{settings.oemr_api_base_url}/patient", timeout=8.0)
        if resp.status_code in (200, 401, 403):  # reachable and answering -- auth failure is not a reachability failure
            return "ok", None
        return "degraded", f"OpenEMR standard API returned {resp.status_code}"
    except httpx.HTTPError as exc:
        return "degraded", f"OpenEMR standard API unreachable: {exc}"


def _check_vector_index() -> tuple[str, str | None]:
    """Local guideline corpus loaded/present -- deliberately does not require a live Voyage call
    (that's `_check_voyage_reachability`'s job) since this check is about the corpus files
    themselves, not the embedding step."""
    try:
        chunks = load_corpus()
        if not chunks:
            return "degraded", "guideline corpus loaded but contains zero chunks"
        return "ok", None
    except Exception as exc:  # noqa: BLE001 -- any parse failure here means "index not usable"
        return "degraded", f"guideline corpus failed to load: {exc}"


def _check_voyage_reachability() -> tuple[str, str | None]:
    """A real reachability probe (not just an api-key-is-set check -- a revoked/invalid key still
    "looks configured"), cached via `_ready_cache`'s TTL so /ready polling doesn't spend real Voyage
    tokens on every health check."""
    if not settings.voyage_api_key:
        return "down", "VOYAGE_API_KEY is not set"
    try:
        _voyage_client().embed(["ready-check"], model=settings.voyage_embed_model, input_type="query")
        return "ok", None
    except Exception as exc:  # noqa: BLE001 -- any Voyage SDK error here means "not reachable right now"
        return "degraded", f"Voyage API unreachable: {exc}"


@app.get("/ready")
def ready():
    """W2_ARCHITECTURE.md Section 9: degrades gracefully rather than reporting a binary down --
    the core FHIR chat flow (Week 1) can keep working even if document storage, the vector index,
    or Voyage are unavailable (only ingestion/evidence-retrieval would be affected), so those three
    are reported as "degraded" dependencies, not folded into an all-or-nothing health check."""
    now = time.time()
    cached = _ready_cache["result"]
    if cached and (now - _ready_cache["timestamp"]) < _READY_CACHE_TTL_SECONDS:
        return cached

    core_status, core_detail = _check_core()
    checks = {
        "core_fhir_chat": {"status": core_status, "detail": core_detail},
        "document_storage": dict(zip(("status", "detail"), _check_document_storage())),
        "vector_index": dict(zip(("status", "detail"), _check_vector_index())),
        "voyage_api": dict(zip(("status", "detail"), _check_voyage_reachability())),
    }

    if core_status == "down":
        overall = "down"
    elif any(c["status"] != "ok" for c in checks.values()):
        overall = "degraded"
    else:
        overall = "ok"

    result = {"status": overall, "checks": checks}
    _ready_cache["timestamp"] = now
    _ready_cache["result"] = result
    return result


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)):
    bearer_token = _resolve_bearer_token(authorization)

    pending_document = None
    if req.pending_document:
        pending_document = {
            "data": base64.b64decode(req.pending_document.data_base64),
            "filename": req.pending_document.filename,
            "doc_type": req.pending_document.doc_type,
            "mimetype": req.pending_document.mimetype,
        }

    result = run_turn(
        patient_id=req.patient_id,
        bearer_token=bearer_token,
        user_message=req.message,
        prior_messages=req.conversation_history,
        patient_pid=req.patient_pid,
        pending_document=pending_document,
    )
    # Flush now rather than waiting for the SDK's background batch interval -- this is a
    # request/response call, not a long-running worker, so we want the trace visible immediately
    # (and don't want it lost if the dev server reloads between requests).
    get_client().flush()

    total = len(result["verified_claims"]) + len(result["stripped_claims"])
    strip_rate = (len(result["stripped_claims"]) / total) if total else 0.0

    return ChatResponse(
        verified_claims=result["verified_claims"],
        stripped_claims=result["stripped_claims"],
        tool_failures=result["tool_failures"],
        strip_rate=strip_rate,
        conversation_history=result["messages"],
        handoff_log=result["handoff_log"],
        correlation_id=result["correlation_id"],
    )


@app.post("/ingest")
async def ingest(
    patient_id: str = Form(...),
    doc_type: Literal["lab_pdf", "intake_form"] = Form(...),
    file: UploadFile = File(...),
    patient_uuid: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
):
    """The intake-extractor worker's HTTP entry point (W2_ARCHITECTURE.md Section 2). Called by
    interface/modules/copilot/upload.php, same auth-bridge pattern as /chat.

    `patient_uuid` is only required for doc_type=intake_form -- see ingestion.persist_intake_facts
    for why the allergy endpoint needs the FHIR uuid while everything else here uses the int pid."""
    bearer_token = _resolve_bearer_token(authorization)
    data = await file.read()

    try:
        result = attach_and_extract(
            patient_id=patient_id,
            data=data,
            filename=file.filename or "upload",
            doc_type=doc_type,
            bearer_token=bearer_token,
            mimetype=file.content_type or "application/octet-stream",
            patient_uuid=patient_uuid,
        )
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    get_client().flush()
    return result
