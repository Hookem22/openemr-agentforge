"""Pure unit tests for the /ready endpoint's degraded-vs-down logic (W2_ARCHITECTURE.md Section 9).
No live API/network calls -- each dependency check function is monkeypatched directly."""
from __future__ import annotations

from app import main as main_module


def _bust_ready_cache():
    main_module._ready_cache["result"] = None


def test_all_dependencies_ok_reports_overall_ok(monkeypatch):
    monkeypatch.setattr(main_module, "_check_core", lambda: ("ok", None))
    monkeypatch.setattr(main_module, "_check_document_storage", lambda: ("ok", None))
    monkeypatch.setattr(main_module, "_check_vector_index", lambda: ("ok", None))
    monkeypatch.setattr(main_module, "_check_voyage_reachability", lambda: ("ok", None))
    _bust_ready_cache()

    result = main_module.ready()

    assert result["status"] == "ok"


def test_voyage_unavailable_reports_degraded_not_down():
    """Core invariant guarded: the core FHIR chat flow doesn't depend on Voyage, so its outage must
    degrade the service, not report it as fully down."""
    import unittest.mock as mock

    with mock.patch.object(main_module, "_check_core", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_document_storage", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_vector_index", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_voyage_reachability", return_value=("degraded", "simulated outage")):
        _bust_ready_cache()
        result = main_module.ready()

    assert result["status"] == "degraded"
    assert result["checks"]["voyage_api"]["status"] == "degraded"


def test_document_storage_unavailable_reports_degraded():
    import unittest.mock as mock

    with mock.patch.object(main_module, "_check_core", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_document_storage", return_value=("degraded", "simulated outage")), \
         mock.patch.object(main_module, "_check_vector_index", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_voyage_reachability", return_value=("ok", None)):
        _bust_ready_cache()
        result = main_module.ready()

    assert result["status"] == "degraded"


def test_core_fhir_chat_down_reports_overall_down():
    """Core invariant guarded: unlike the three optional Week 2 dependencies, a broken core FHIR
    chat flow (Week 1) is a genuine outage, not a degradation."""
    import unittest.mock as mock

    with mock.patch.object(main_module, "_check_core", return_value=("down", "simulated FHIR outage")), \
         mock.patch.object(main_module, "_check_document_storage", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_vector_index", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_voyage_reachability", return_value=("ok", None)):
        _bust_ready_cache()
        result = main_module.ready()

    assert result["status"] == "down"


def test_ready_result_is_cached_within_ttl():
    """Boundary: /ready must not spend a real Voyage API call (or hammer OpenEMR) on every single
    poll -- repeated calls within the TTL window must reuse the cached result."""
    import unittest.mock as mock

    voyage_check = mock.Mock(return_value=("ok", None))
    with mock.patch.object(main_module, "_check_core", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_document_storage", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_vector_index", return_value=("ok", None)), \
         mock.patch.object(main_module, "_check_voyage_reachability", voyage_check):
        _bust_ready_cache()
        main_module.ready()
        main_module.ready()

    assert voyage_check.call_count == 1
