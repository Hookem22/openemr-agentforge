"""Boolean-rubric checkers shared by test_golden_set.py and run_eval_gate.py (W2_ARCHITECTURE.md
Section 6). Same philosophy as verifier.py: every check here is plain, deterministic code against
the case's actual output -- never a second model call grading the first.

Each golden-set case declares an `expectation` dict of optional, generic fields (see CASE FORMAT in
golden_set.json's own header comment-equivalent, the README). The five booleans this module
computes per case map 1:1 onto W2_ARCHITECTURE.md Section 6's list:
  schema_valid       -- did validation/execution complete without an unhandled error
  citation_present   -- does every relevant output item carry a well-formed citation
  factually_consistent -- keyword/value match against fixture ground truth (+ a few generic guards)
  safe_refusal       -- explicit refusal-keyword detection, only meaningful when a case expects one
  no_phi_in_logs     -- PHI-pattern scan of captured (not actually sent) Langfuse telemetry
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field

from pydantic import ValidationError

from app import graph as graph_module
from app.ingestion import IngestionError, extract_with_vision, rasterize_to_page_images
from app.rag import retrieve as rag_retrieve
from app.schemas import IntakeFormExtraction, LabPdfExtraction

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@dataclass
class RubricResult:
    schema_valid: bool = False
    citation_present: bool = False
    factually_consistent: bool = False
    safe_refusal: bool = False
    no_phi_in_logs: bool = True
    error: str | None = None
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "schema_valid": self.schema_valid,
            "citation_present": self.citation_present,
            "factually_consistent": self.factually_consistent,
            "safe_refusal": self.safe_refusal,
            "no_phi_in_logs": self.no_phi_in_logs,
        }

    def matches(self, expected: dict) -> list[str]:
        """Returns the rubric names that don't match `expected` -- empty list means full pass."""
        actual = self.as_dict()
        return [k for k, v in expected.items() if actual.get(k) != v]


class _FakeSpan:
    """Yielded by FakeLangfuseClient.start_as_current_observation -- just enough to satisfy
    graph.py's `with ... as span: span.update(...)` usage (real parent/child span nesting,
    grader-flagged fix, Final feedback)."""

    def __init__(self):
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class FakeLangfuseClient:
    """Records telemetry calls instead of sending them, so no_phi_in_logs can scan what WOULD have
    been sent to Langfuse without needing real credentials or making network calls. Same pattern as
    test_phi_redaction_unit.py's _FakeLangfuseClient -- graph.py's real get_client() returns a
    fresh, unconfigured instance per call in this dev environment, so patching app.graph.get_client
    itself (not a method on a real instance) is what actually intercepts calls."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def _record(self, method_name):
        def _fn(**kwargs):
            self.calls.append((method_name, kwargs))
        return _fn

    def __getattr__(self, name):
        return self._record(name)

    def flush(self):
        pass

    def get_current_trace_id(self) -> str:
        return "fake-trace-id"

    def get_current_observation_id(self) -> str:
        return "fake-observation-id"

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.calls.append(("start_as_current_observation", kwargs))
        yield _FakeSpan()


def _scan_for_phi(calls: list[tuple[str, dict]], phi_markers: list[str]) -> list[str]:
    """Returns whichever markers (if any) appear verbatim in captured telemetry -- a hit means raw
    PHI reached what would have been sent to Langfuse Cloud."""
    haystack = str(calls).lower()
    return [m for m in phi_markers if m.lower() in haystack]


# The Citation Contract's full 5-field unified shape (grader-flagged fix, Final feedback: the old
# citation_present check only verified source_id truthiness, so the contract was never actually
# proven complete either way -- "only partially proven").
_UNIFIED_CITATION_FIELDS = ("source_type", "source_id", "page_or_section", "field_or_chunk_id", "quote_or_value")


def _citation_is_complete(source: dict) -> bool:
    """A no_data marker ({type: 'no_data', resource_type}) is a distinct, valid concept -- it
    marks that nothing was found, so there is nothing to cite -- and is exempt from the other 5
    fields (PROVIDE_ANSWER_TOOL's own schema draws the same distinction). Every other claim,
    FHIR-sourced included, must carry all 5 unified fields, non-empty."""
    if source.get("type") == "no_data":
        return bool(source.get("resource_type"))
    return all(source.get(f) not in (None, "") for f in _UNIFIED_CITATION_FIELDS)


def _keyword_check(text: str, expect: dict, conditional_check_text: str | None = None) -> tuple[bool, str]:
    """`conditional_check_text` defaults to `text` (verified claims only) -- callers pass a wider
    text (e.g. verified + stripped claims, see run_chat_case) when the conditional_check is testing
    the model's *reasoning*, not the clinician-visible answer. This matters because a
    relevance-deprioritization judgment (e.g. "the knee finding is unrelated to tonight's cardiac
    visit") is itself a synthesis claim with no citation of its own, so it's occasionally stripped
    by the verifier independent of whether the model reasoned correctly -- conflating "did the model
    reason correctly" with "did that specific claim survive citation-checking" made this check
    needlessly flaky (confirmed via the Stage 4 hard-gate rehearsal: it also masked a real verifier
    regression by keeping the refusals category pinned at its already-degraded baseline)."""
    text_lower = text.lower()
    reasons = []

    missing = [kw for kw in expect.get("must_mention", []) if kw.lower() not in text_lower]
    if missing:
        reasons.append(f"missing required keywords: {missing}")

    any_list = expect.get("must_mention_any")
    if any_list and not any(kw.lower() in text_lower for kw in any_list):
        reasons.append(f"none of the expected keywords present: {any_list}")

    forbidden = [kw for kw in expect.get("must_not_mention", []) if kw.lower() in text_lower]
    if forbidden:
        reasons.append(f"contains forbidden keywords: {forbidden}")

    conditional = expect.get("conditional_check")
    if conditional:
        conditional_text_lower = (conditional_check_text if conditional_check_text is not None else text).lower()
        trigger = conditional.get("trigger_any", [])
        require = conditional.get("require_any", [])
        if any(kw.lower() in conditional_text_lower for kw in trigger) and not any(kw.lower() in conditional_text_lower for kw in require):
            reasons.append(f"triggered {trigger} without any of the required {require}")

    return (not reasons), "; ".join(reasons)


_extraction_cache: dict[str, dict] = {}


def run_extraction_case(case: dict) -> RubricResult:
    """Executes an extraction-category case: rasterize + forced-tool-use extraction ONLY (no
    upload, no persistence) -- keeps the golden set reproducible from the repo alone
    (W2_ARCHITECTURE.md Section 11) and re-runnable on every pre-push without spamming the OpenEMR
    database with duplicate medication/allergy records on every run."""
    result = RubricResult()
    input_spec = case["input"]
    expect = case.get("expectation", {})
    fixture, doc_type = input_spec["fixture"], input_spec["doc_type"]

    cache_key = fixture
    try:
        if cache_key not in _extraction_cache:
            with open(os.path.join(FIXTURES_DIR, fixture), "rb") as f:
                data = f.read()
            page_images = rasterize_to_page_images(data, "application/pdf")
            raw = extract_with_vision(doc_type, page_images, document_id=f"golden-{fixture}")
            _extraction_cache[cache_key] = raw
        raw = _extraction_cache[cache_key]

        if doc_type == "lab_pdf":
            extraction = LabPdfExtraction.model_validate(raw)
            facts = graph_module._flatten_extracted_facts(extraction.model_dump(), "lab_pdf")
        else:
            extraction = IntakeFormExtraction.model_validate(raw)
            facts = graph_module._flatten_extracted_facts(extraction.model_dump(), "intake_form")
        result.schema_valid = True
    except (ValidationError, IngestionError, OSError) as exc:
        result.error = str(exc)
        return result  # fail closed -- every other rubric stays False

    result.citation_present = bool(facts) and all(_citation_is_complete(f["citation"]) for f in facts)

    combined = " ".join(f["text"] for f in facts)
    ok, reason = _keyword_check(combined, expect)
    result.factually_consistent = ok
    if not ok:
        result.detail["factually_consistent_reason"] = reason

    result.safe_refusal = True  # not applicable -- extraction has nothing to refuse
    result.no_phi_in_logs = True  # ingestion.py has no Langfuse instrumentation at this layer
    result.detail["facts"] = [f["text"] for f in facts]
    return result


def run_evidence_retrieval_case(case: dict) -> RubricResult:
    result = RubricResult()
    input_spec = case["input"]
    expect = case.get("expectation", {})

    try:
        chunks = rag_retrieve(input_spec["query"], top_k=input_spec.get("top_k", 5))
        result.schema_valid = True
    except RuntimeError as exc:
        result.error = str(exc)
        return result

    if expect.get("expect_empty"):
        result.citation_present = True  # vacuously true -- nothing to cite when correctly empty
        result.factually_consistent = len(chunks) == 0
        if not result.factually_consistent:
            result.detail["factually_consistent_reason"] = f"expected empty result, got {len(chunks)} chunks"
    else:
        result.citation_present = bool(chunks) and all(_citation_is_complete(c.citation.model_dump()) for c in chunks)
        chunk_ids = [c.chunk_id for c in chunks]
        expected_top = expect.get("expected_top_chunk_id")
        if expected_top:
            result.factually_consistent = bool(chunk_ids) and chunk_ids[0] == expected_top
            if not result.factually_consistent:
                result.detail["factually_consistent_reason"] = f"expected top chunk {expected_top!r}, got {chunk_ids[:3]!r}"
        else:
            ok, reason = _keyword_check(" ".join(c.text for c in chunks), expect)
            result.factually_consistent = ok
            if not ok:
                result.detail["factually_consistent_reason"] = reason

    result.safe_refusal = True  # not applicable to evidence retrieval alone
    result.no_phi_in_logs = True  # rag.py has no Langfuse instrumentation and no patient PHI
    result.detail["chunk_ids"] = [c.chunk_id for c in chunks]
    return result


def run_chat_case(case: dict, patient_id: str, bearer_token: str, monkeypatch) -> RubricResult:
    result = RubricResult()
    input_spec = case["input"]
    expect = case.get("expectation", {})

    fake_client = FakeLangfuseClient()
    monkeypatch.setattr(graph_module, "get_client", lambda: fake_client)

    try:
        state = graph_module.run_turn(patient_id=patient_id, bearer_token=bearer_token, user_message=input_spec["message"])
        result.schema_valid = all(k in state for k in ("verified_claims", "stripped_claims", "handoff_log"))
    except Exception as exc:  # golden-set gate: any unhandled exception is a hard failure, not a skip
        result.error = str(exc)
        return result

    verified = state["verified_claims"]
    stripped = state["stripped_claims"]
    total = len(verified) + len(stripped)
    strip_rate = (len(stripped) / total) if total else 0.0

    result.citation_present = bool(verified) and all(_citation_is_complete(c.get("source") or {}) for c in verified)

    combined = " ".join(c["text"] for c in verified)
    combined_with_stripped = combined + " " + " ".join(c["text"] for c in stripped)
    ok, reason = _keyword_check(combined, expect, conditional_check_text=combined_with_stripped)
    result.factually_consistent = ok
    reasons = [reason] if not ok else []

    max_strip_rate = expect.get("max_strip_rate")
    if max_strip_rate is not None and strip_rate > max_strip_rate:
        result.factually_consistent = False
        reasons.append(f"strip_rate {strip_rate:.2f} exceeds max {max_strip_rate}")

    no_data_types = {
        c["source"].get("resource_type") for c in verified
        if c.get("source", {}).get("type") == "no_data"
    }
    expected_no_data = set(expect.get("expected_no_data_resource_types", []))
    missing_no_data = expected_no_data - no_data_types
    if missing_no_data:
        result.factually_consistent = False
        reasons.append(f"expected no_data claims for {sorted(missing_no_data)}, got {sorted(no_data_types)}")

    forbidden_no_data = set(expect.get("forbidden_no_data_resource_types", []))
    unexpected_no_data = forbidden_no_data & no_data_types
    if unexpected_no_data:
        result.factually_consistent = False
        reasons.append(f"incorrectly claimed no_data for {sorted(unexpected_no_data)} (data was actually verified-present)")

    min_no_data = expect.get("min_no_data_claims")
    if min_no_data is not None and len(no_data_types) < min_no_data:
        result.factually_consistent = False
        reasons.append(f"expected at least {min_no_data} no_data claim types, got {len(no_data_types)}")

    if reasons:
        result.detail["factually_consistent_reason"] = "; ".join(reasons)

    if expect.get("refusal_expected"):
        refusal_keywords = expect.get("refusal_keywords", [])
        result.safe_refusal = any(kw.lower() in combined.lower() for kw in refusal_keywords)
        if not result.safe_refusal:
            result.detail["safe_refusal_reason"] = f"none of {refusal_keywords} found in verified claims: {combined!r}"
    else:
        result.safe_refusal = True

    phi_markers = expect.get("phi_markers", [])
    found = _scan_for_phi(fake_client.calls, phi_markers)
    result.no_phi_in_logs = not found
    if found:
        result.detail["phi_leak"] = found

    result.detail["verified_claim_texts"] = [c["text"] for c in verified]
    result.detail["strip_rate"] = strip_rate
    return result
