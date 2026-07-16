"""Document ingestion: upload -> extract -> validate -> persist (W2_ARCHITECTURE.md Section 2).

`attach_and_extract` is the intake-extractor worker's core tool. Raw Claude-vision output never
bypasses `schemas.py` -- every extracted fact is validated before it's persisted or shown to the
clinician (the "vision extraction without invention" hard problem: the schema is the safety net,
not the model's own stated confidence). Every persisted fact links back to the uploaded source
document (Section 2.1/2.2's dedup + document_id linkage).
"""
from __future__ import annotations

import base64
import hashlib
import uuid

import fitz  # pymupdf
import httpx
from anthropic import Anthropic
from langfuse import get_client, observe
from pydantic import ValidationError

from .config import settings
from .retry import retry_connect_only_http, retry_idempotent_http
from .schemas import DocType, IntakeFormExtraction, LabPdfExtraction

# Categories seeded by docs/seed-w2-document-categories.sql. Deliberately single-word (no space)
# -- see that file's comment for the pre-existing OpenEMR category-lookup bug this sidesteps.
CATEGORY_BY_DOC_TYPE: dict[DocType, str] = {
    "lab_pdf": "LabPDF",
    "intake_form": "IntakeForm",
}

IMAGE_MIMETYPES = {"image/png", "image/jpeg", "image/jpg"}


class IngestionError(RuntimeError):
    """Raised for a hard failure in the ingestion pipeline (upload rejected, extraction call
    failed, response failed schema validation entirely). Distinct from a low-confidence or
    partially-empty extraction, which is not an error -- it's returned as-is for the clinician to see."""


def _headers(bearer_token: str, correlation_id: str) -> dict:
    # X-Correlation-Id lets a grader reconstruct a full Week 2 request -- upload -> extraction ->
    # OpenEMR write -- across the Langfuse/OpenEMR boundary (Engineering Requirements: "correlation
    # ID ... propagate into ... FHIR writes"). Read server-side in
    # ProcedureRestController::postResultsFromDocument, the one write path this project owns.
    return {"Authorization": f"Bearer {bearer_token}", "X-Correlation-Id": correlation_id}


def _file_hash(data: bytes) -> str:
    # Matches the sha3-512 digest OpenEMR itself computes on every document (documents.hash),
    # though OpenEMR never checks it before insert -- this client-side dedup check is what
    # actually prevents re-uploading identical bytes (W2_ARCHITECTURE.md Section 2, step (b)).
    return hashlib.sha3_512(data).hexdigest()


@retry_idempotent_http
def _lookup_document(bearer_token: str, patient_id: str, category: str, filename: str, correlation_id: str) -> dict | None:
    """Resolves a document's id/hash by (patient, category, filename) via the new
    document_lookup endpoint (a direct query), not DocumentService::getAllAtPath() --
    see docs/seed-w2-document-categories.sql and Gauntlet/Week 2/STATUS.md for why:
    getAllAtPath() hit an environment-specific issue discovered while building this. Retried on
    transient errors (retry.py) -- a GET, always safe."""
    resp = httpx.get(
        f"{settings.oemr_api_base_url}/patient/{patient_id}/document_lookup",
        params={"path": category, "filename": filename},
        headers=_headers(bearer_token, correlation_id),
        timeout=15.0,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json() or None


@retry_connect_only_http
def _upload_document(bearer_token: str, patient_id: str, category: str, filename: str, data: bytes, mimetype: str, correlation_id: str) -> None:
    # Connect-only retry, not the full transient set (retry.py): OpenEMR's document-upload endpoint
    # has no server-side dedup (only the pre-upload hash lookup in upload_and_resolve_document,
    # which runs *before* this call, not inside it), so retrying a ReadTimeout here risks a genuine
    # duplicate document row if the first attempt actually succeeded and only the response was lost.
    resp = httpx.post(
        f"{settings.oemr_api_base_url}/patient/{patient_id}/document",
        params={"path": category},
        headers=_headers(bearer_token, correlation_id),
        files={"document": (filename, data, mimetype)},
        timeout=30.0,
    )
    resp.raise_for_status()
    if resp.json() is not True:
        raise IngestionError(f"OpenEMR rejected the document upload (category={category!r}, patient={patient_id!r})")


@observe(name="document_ingestion", capture_input=False, capture_output=False)
def upload_and_resolve_document(
    bearer_token: str, patient_id: str, doc_type: DocType, filename: str, data: bytes, mimetype: str, correlation_id: str
) -> tuple[int, bool]:
    """Uploads `data` under the category matching `doc_type`, unless a document with identical
    bytes is already there under the same filename (dedup by hash), and returns
    (documents.id, was_deduped).

    W2_ARCHITECTURE.md Section 9's `document_ingestion` span. Redacted: `data` (raw file bytes) and
    `bearer_token` are excluded from auto-capture (capture_input/output=False) -- only doc_type,
    dedup outcome, and byte count (not PHI) are sent manually below. `correlation_id` is also safe
    to log directly -- it's an opaque request identifier, never PHI."""
    category = CATEGORY_BY_DOC_TYPE[doc_type]
    file_hash = _file_hash(data)

    existing = _lookup_document(bearer_token, patient_id, category, filename, correlation_id)
    if existing is not None and existing.get("hash") == file_hash:
        get_client().update_current_span(
            input={"doc_type": doc_type, "byte_count": len(data), "correlation_id": correlation_id},
            output={"was_deduped": True},
        )
        return int(existing["id"]), True

    try:
        _upload_document(bearer_token, patient_id, category, filename, data, mimetype, correlation_id)

        # insertAtPath() (see W2_ARCHITECTURE.md Section 2) returns only a bare success boolean, not
        # the new row's id -- resolve it with a follow-up lookup call.
        match = _lookup_document(bearer_token, patient_id, category, filename, correlation_id)
        if match is None:
            raise IngestionError("document uploaded successfully but could not be resolved afterward")
    except (IngestionError, httpx.HTTPError) as exc:
        get_client().update_current_span(
            input={"doc_type": doc_type, "byte_count": len(data), "correlation_id": correlation_id},
            output={"error": str(exc)},
            level="ERROR",
        )
        raise

    get_client().update_current_span(
        input={"doc_type": doc_type, "byte_count": len(data), "correlation_id": correlation_id},
        output={"was_deduped": False},
    )
    return int(match["id"]), False


def rasterize_to_page_images(data: bytes, mimetype: str) -> list[bytes]:
    """Returns one PNG per page for Claude vision input. A plain image upload (a phone photo of an
    intake form, not a PDF) is treated as a single page."""
    if mimetype in IMAGE_MIMETYPES:
        return [data]

    pages = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            pages.append(pix.tobytes("png"))
    return pages


BBOX_SCHEMA = {
    "type": "object",
    "description": "Normalized (0.0-1.0) location of this field on its page image.",
    "properties": {
        "page": {"type": "integer"},
        "x0": {"type": "number"},
        "y0": {"type": "number"},
        "x1": {"type": "number"},
        "y1": {"type": "number"},
    },
    "required": ["page", "x0", "y0", "x1", "y1"],
}

CITATION_SCHEMA = {
    "type": "object",
    "properties": {
        "source_type": {"type": "string", "enum": ["document"]},
        "source_id": {"type": "string", "description": "The document id passed in your instructions."},
        "page_or_section": {"type": "string", "description": "Page number this was read from."},
        "field_or_chunk_id": {"type": "string", "description": "Which field this citation supports, e.g. 'test_name' or 'value'."},
        "quote_or_value": {"type": "string", "description": "The literal text you read off the page for this field."},
    },
    "required": ["source_type", "source_id"],
}


def _extracted_field_schema(value_properties: dict, required_value_fields: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            **value_properties,
            "confidence": {"type": "number", "description": "Your confidence this field was read correctly, 0.0-1.0."},
            "bbox": BBOX_SCHEMA,
            "citation": CITATION_SCHEMA,
        },
        "required": [*required_value_fields, "confidence", "citation"],
    }


EXTRACT_LAB_PDF_TOOL = {
    "name": "extract_lab_pdf",
    "description": (
        "Submit every lab test result you can read from this document. If a value is illegible or "
        "you are not confident, still include it with a low confidence score rather than omitting "
        "it or guessing a plausible-looking value -- never invent a result that is not visibly "
        "printed on the page."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": _extracted_field_schema(
                    {
                        "test_name": {"type": "string"},
                        "value": {"type": "string"},
                        "unit": {"type": "string"},
                        "reference_range": {"type": "string"},
                        "collection_date": {"type": "string", "description": "ISO 8601 if readable, e.g. 2026-07-13"},
                        "abnormal_flag": {"type": "boolean"},
                    },
                    ["test_name", "value"],
                ),
            }
        },
        "required": ["results"],
    },
}

EXTRACT_INTAKE_FORM_TOOL = {
    "name": "extract_intake_form",
    "description": (
        "Submit every intake field you can read from this form. Omit a section entirely (e.g. "
        "leave `family_history` as an empty list) if the form has nothing legible there -- never "
        "invent a plausible-looking entry to fill a section."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "demographics": _extracted_field_schema(
                {"name": {"type": "string"}, "date_of_birth": {"type": "string"}, "sex": {"type": "string"}}, []
            ),
            "chief_concern": _extracted_field_schema({"text": {"type": "string"}}, ["text"]),
            "current_medications": {
                "type": "array",
                "items": _extracted_field_schema(
                    {"name": {"type": "string"}, "dose": {"type": "string"}, "frequency": {"type": "string"}}, ["name"]
                ),
            },
            "allergies": {
                "type": "array",
                "items": _extracted_field_schema({"allergen": {"type": "string"}, "reaction": {"type": "string"}}, ["allergen"]),
            },
            "family_history": {
                "type": "array",
                "items": _extracted_field_schema({"relation": {"type": "string"}, "condition": {"type": "string"}}, ["relation", "condition"]),
            },
        },
        "required": ["current_medications", "allergies", "family_history"],
    },
}

EXTRACTION_TOOL_BY_DOC_TYPE = {
    "lab_pdf": EXTRACT_LAB_PDF_TOOL,
    "intake_form": EXTRACT_INTAKE_FORM_TOOL,
}


def _anthropic_client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    # No explicit retry config: the SDK already defaults to max_retries=2, retrying connection
    # errors and 408/409/429/5xx (see app/retry.py's module docstring for the verified details) --
    # this is deliberate reliance on that default, not a missing retry.
    return Anthropic(api_key=settings.anthropic_api_key)


def _collect_confidences(raw_extraction: dict, doc_type: DocType) -> list[float]:
    """Walks the raw (pre-validation) extraction dict and pulls every field's `confidence` value,
    for the extraction-confidence-per-document metric (W2_ARCHITECTURE.md Section 9 /
    Engineering Requirements). Defensive, not schema-trusting: a malformed or missing confidence
    from Claude should degrade this telemetry, not crash it -- schema validation in
    `attach_and_extract` is the real safety net for malformed extractions, not this."""
    values: list[float] = []

    def _add(entry: object) -> None:
        if isinstance(entry, dict):
            confidence = entry.get("confidence")
            if isinstance(confidence, (int, float)):
                values.append(float(confidence))

    if doc_type == "lab_pdf":
        for result in raw_extraction.get("results") or []:
            _add(result)
    else:
        _add(raw_extraction.get("demographics"))
        _add(raw_extraction.get("chief_concern"))
        for key in ("current_medications", "allergies", "family_history"):
            for entry in raw_extraction.get(key) or []:
                _add(entry)
    return values


@observe(as_type="generation", name="extraction", capture_input=False, capture_output=False)
def extract_with_vision(doc_type: DocType, page_images: list[bytes], document_id: int) -> dict:
    """Forces Claude to call the doc-type-matched extraction tool over the page images, and
    returns its raw (not-yet-validated) tool input. Validation happens in `attach_and_extract` --
    this function's output must never be persisted or shown to a clinician directly.

    W2_ARCHITECTURE.md Section 9's `extraction` span. Redacted: `page_images` (raw document scans)
    and the returned extraction dict are real PHI -- capture_input/output=False excludes both from
    auto-capture; only doc_type, page count, and token usage (never PHI) are sent manually below."""
    tool = EXTRACTION_TOOL_BY_DOC_TYPE[doc_type]
    # Explicit annotation: without it, mypy infers dict[str, str] from the first (text-only) entry
    # and then rejects the image entry's nested "source" dict below.
    content: list[dict[str, object]] = [
        {"type": "text", "text": f"source document id: {document_id}. Extract every field you can read."}
    ]
    for image_bytes in page_images:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": _b64(image_bytes)},
            }
        )

    client = _anthropic_client()
    # Tool schemas and message content are built as plain dicts (matching the tool-schema literals
    # above), not the SDK's exact nested TypedDicts -- mypy can't verify the overload match, but the
    # shapes are exercised extensively by test_ingestion_integration.py and live testing.
    response = client.messages.create(  # type: ignore[call-overload]
        model=settings.anthropic_model,
        max_tokens=4096,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": content}],
    )
    get_client().update_current_generation(
        model=settings.anthropic_model,
        input={"doc_type": doc_type, "page_count": len(page_images)},
        usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
    )
    for block in response.model_dump()["content"]:
        if block.get("type") == "tool_use":
            raw_extraction = block["input"]
            # Confidence values themselves are never PHI (a float, not a clinical value) -- safe to
            # log directly, unlike the extraction content around them.
            confidences = _collect_confidences(raw_extraction, doc_type)
            get_client().update_current_generation(
                output={
                    "stop_reason": response.stop_reason,
                    "extracted": True,
                    "field_count": len(confidences),
                    "mean_confidence": (sum(confidences) / len(confidences)) if confidences else None,
                    "min_confidence": min(confidences) if confidences else None,
                }
            )
            return raw_extraction
    get_client().update_current_generation(output={"stop_reason": response.stop_reason, "extracted": False}, level="ERROR")
    raise IngestionError("Claude did not call the forced extraction tool")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@retry_idempotent_http
def persist_lab_results(
    bearer_token: str, patient_id: str, document_id: int, extraction: LabPdfExtraction, correlation_id: str
) -> dict:
    """Calls the new procedure_result_from_document endpoint (ProcedureService::
    insertResultsFromDocument, W2_ARCHITECTURE.md Section 2.1) -- the one genuinely new OpenEMR
    write path. Every result's document_id links back to this document; a repeat call with the
    same document_id + results is a no-op (server-side dedup on procedure_order.external_id).
    `correlation_id` is sent as X-Correlation-Id and logged server-side in
    ProcedureRestController::postResultsFromDocument -- this is the one write path we own, so it's
    the one place a full agent-to-OpenEMR trace can actually be confirmed end to end.

    Retried on the full transient-error set (retry.py), not just connect failures: unlike
    _upload_document, a duplicate call here is a harmless server-side no-op (external_id dedup), so
    a ReadTimeout-then-retry can't create a duplicate write."""
    if not extraction.results:
        return {"skipped": True, "procedure_order_id": None, "result_ids": []}

    payload = {
        "document_id": document_id,
        "results": [
            {
                "test_name": r.test_name,
                "value": r.value,
                "unit": r.unit,
                "reference_range": r.reference_range,
                "collection_date": r.collection_date,
                "abnormal_flag": r.abnormal_flag,
            }
            for r in extraction.results
        ],
    }
    resp = httpx.post(
        f"{settings.oemr_api_base_url}/patient/{patient_id}/procedure_result_from_document",
        headers={**_headers(bearer_token, correlation_id), "Content-Type": "application/json"},
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


@retry_connect_only_http
def _post_json(url: str, headers: dict, json_body: dict, timeout: float) -> httpx.Response:
    # Connect-only retry, not the full transient set (retry.py): the medication/allergy endpoints
    # this feeds have no server-side dedup, so a ReadTimeout-then-retry risks a genuine duplicate
    # entry if the first attempt actually succeeded and only the response was lost.
    resp = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
    resp.raise_for_status()
    return resp


def _post_json_tolerant(url: str, headers: dict, json_body: dict, timeout: float) -> dict:
    """POSTs and parses the JSON response, but never raises on an individual item's failure
    (network error, non-2xx, or an empty/non-JSON body) -- one bad item (e.g. a transient error
    from the target dev server) must not silently discard every other item in the same request,
    nor crash the whole ingestion call. Returns {"ok": True, "response": ...} or
    {"ok": False, "error": ...}."""
    try:
        resp = _post_json(url, headers, json_body, timeout)
        return {"ok": True, "response": resp.json()}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:  # response body was not valid JSON
        return {"ok": False, "error": f"non-JSON response: {exc}"}


def persist_intake_facts(
    bearer_token: str, patient_id: str, patient_uuid: str, extraction: IntakeFormExtraction, correlation_id: str
) -> dict:
    """Reuses OpenEMR's existing medication/allergy endpoints (W2_ARCHITECTURE.md Section 2.2) --
    no new OpenEMR write path needed for these. Demographics, chief concern, and family history are
    NOT persisted to a native table (documented MVP limitation, Section 2.2/12) -- they're still
    validated, cited, and returned to the caller, just not written back into the chart. Each item
    is persisted independently (see _post_json_tolerant) so one failure doesn't discard the rest.

    Note the two endpoints key patients differently -- a real OpenEMR API inconsistency, not a
    typo: POST .../medication takes the native int pid (ListRestController uses it directly in
    SQL), POST .../allergy takes the FHIR patient uuid (AllergyIntoleranceRestController validates
    it as a UUID and 400s on a plain int) -- hence this function takes both identifiers.

    `correlation_id` is sent as X-Correlation-Id on both calls for consistency with every other
    Week 2 write, though these two stock (unmodified) OpenEMR controllers don't parse/log it
    server-side -- only the new procedure_result_from_document endpoint does (see
    persist_lab_results); documented as a scoped limitation, not silently assumed to be covered."""
    persisted: dict = {"medications": [], "allergies": []}
    headers = {**_headers(bearer_token, correlation_id), "Content-Type": "application/json"}

    for med in extraction.current_medications:
        result = _post_json_tolerant(
            f"{settings.oemr_api_base_url}/patient/{patient_id}/medication",
            headers,
            {
                "title": med.name,
                "dosage": med.dose or "",
                "frequency": med.frequency or "",
                "active": True,
                # ListService::insert() (src/Services/ListService.php) reads these keys without a
                # default -- omitting them triggers PHP "Undefined array key" warnings that print
                # as raw HTML ahead of the JSON body, corrupting it (same class of bug as the
                # already-documented FhirAllergyIntoleranceService PHP-warning issue).
                "begdate": "",
                "enddate": "",
                "diagnosis": "",
            },
            15.0,
        )
        persisted["medications"].append(result)

    for allergy in extraction.allergies:
        result = _post_json_tolerant(
            f"{settings.oemr_api_base_url}/patient/{patient_uuid}/allergy",
            headers,
            {"title": allergy.allergen, "reaction": allergy.reaction or "", "begdate": "", "enddate": "", "diagnosis": ""},
            15.0,
        )
        persisted["allergies"].append(result)

    return persisted


def attach_and_extract(
    patient_id: str,
    data: bytes,
    filename: str,
    doc_type: DocType,
    bearer_token: str,
    mimetype: str = "application/pdf",
    patient_uuid: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """The intake-extractor worker's core tool. Uploads the file, extracts structured facts via
    forced Claude tool-use, validates against schemas.py, persists what has a native home, and
    returns the validated extraction plus a persistence summary. Never lets raw VLM output bypass
    Pydantic validation (W2_ARCHITECTURE.md Section 2, step (e)).

    `patient_id` is the native int pid (what the document/procedure/medication endpoints take).
    `patient_uuid` (only required for intake_form) is the FHIR patient uuid the allergy endpoint
    takes instead -- see persist_intake_facts for why these differ.

    `correlation_id`: threaded into every OpenEMR write below as X-Correlation-Id (Engineering
    Requirements: correlation ID must propagate into ingestion flows and FHIR writes). Callers that
    already have one (graph.py's intake_extractor_node, main.py's /ingest route) should pass their
    own; defaults to a fresh id here only so fixture/test callers that don't care about tracing
    (attach_and_extract_from_path, the golden-set runner) don't need to invent one."""
    correlation_id = correlation_id or uuid.uuid4().hex
    document_id, was_deduped = upload_and_resolve_document(
        bearer_token, patient_id, doc_type, filename, data, mimetype, correlation_id
    )

    page_images = rasterize_to_page_images(data, mimetype)
    raw_extraction = extract_with_vision(doc_type, page_images, document_id)

    try:
        if doc_type == "lab_pdf":
            extraction: LabPdfExtraction | IntakeFormExtraction = LabPdfExtraction.model_validate(raw_extraction)
        else:
            extraction = IntakeFormExtraction.model_validate(raw_extraction)
    except ValidationError as exc:
        raise IngestionError(f"extraction failed schema validation: {exc}") from exc

    if isinstance(extraction, LabPdfExtraction):
        persistence = persist_lab_results(bearer_token, patient_id, document_id, extraction, correlation_id)
    else:
        if not patient_uuid:
            raise IngestionError("patient_uuid is required for intake_form ingestion (needed for the allergy endpoint)")
        persistence = persist_intake_facts(bearer_token, patient_id, patient_uuid, extraction, correlation_id)

    return {
        "document_id": document_id,
        "was_deduped": was_deduped,
        "extraction": extraction.model_dump(),
        "persistence": persistence,
        "correlation_id": correlation_id,
    }


def attach_and_extract_from_path(
    patient_id: str, file_path: str, doc_type: DocType, bearer_token: str, mimetype: str = "application/pdf",
    correlation_id: str | None = None,
) -> dict:
    """Convenience wrapper for scripts/fixtures/tests that have a file on disk rather than raw
    upload bytes (e.g. the CLI, or the golden-set runner in Stage 4)."""
    with open(file_path, "rb") as f:
        data = f.read()
    filename = file_path.rsplit("/", 1)[-1]
    return attach_and_extract(patient_id, data, filename, doc_type, bearer_token, mimetype, correlation_id=correlation_id)
