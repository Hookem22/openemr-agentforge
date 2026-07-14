"""Pure unit tests for the RAG corpus/fusion logic (app/rag.py) that don't need a Voyage API call:
corpus parsing, tokenization, and reciprocal rank fusion. The dense-embedding and rerank calls
themselves are covered separately by an integration test gated on a real VOYAGE_API_KEY (Section 6's
two-tier testing strategy: fast/offline always runs, real-API is the pre-push gate).
"""
from __future__ import annotations

from app.rag import (
    _Chunk,
    _chunk_to_evidence,
    _corpus_hash,
    _parse_frontmatter,
    _slugify,
    _tokenize,
    load_corpus,
    reciprocal_rank_fusion,
)


def test_load_corpus_finds_all_seeded_guideline_documents():
    """Boundary: the corpus should load every *.md file under agent/data/guidelines with at least
    one chunk each -- a silent parsing failure on one file would quietly shrink the evidence base."""
    chunks = load_corpus()
    assert len(chunks) > 0
    source_slugs = {c.source_slug for c in chunks}
    # 7 guideline documents were sourced for Stage 2, covering all 4 seeded patients' conditions.
    assert len(source_slugs) == 7


def test_every_chunk_has_required_metadata():
    """Failure mode guarded: a chunk missing source_org/source_title/section would produce an
    incomplete citation downstream -- verified here before it ever reaches the verifier."""
    for chunk in load_corpus():
        assert chunk.chunk_id
        assert chunk.text
        assert chunk.source_slug
        assert chunk.source_title
        assert chunk.source_org != ""
        assert chunk.section


def test_parse_frontmatter_extracts_expected_keys():
    content = """---
source: Test Org
title: Test Guideline
url: https://example.com
---

## Section One

Some body text.
"""
    meta, body = _parse_frontmatter(content)
    assert meta == {"source": "Test Org", "title": "Test Guideline", "url": "https://example.com"}
    assert body.startswith("## Section One")


def test_parse_frontmatter_handles_missing_frontmatter():
    """Boundary: a file with no frontmatter delimiter must not crash -- treated as having no
    metadata rather than raising."""
    meta, body = _parse_frontmatter("## Just a section\n\nBody text.")
    assert meta == {}
    assert "Just a section" in body


def test_slugify_produces_url_safe_chunk_ids():
    assert _slugify("Blood Pressure Screening!") == "blood-pressure-screening"
    assert _slugify("A/B & C") == "a-b-c"


def test_corpus_hash_changes_when_content_changes():
    """This hash is what gates the embedding cache -- if it didn't change on real content edits,
    a corpus update would silently keep serving stale embeddings."""
    chunks_a = [_Chunk("id1", "text one", "slug", "Title", "Org", "Section")]
    chunks_b = [_Chunk("id1", "text ONE CHANGED", "slug", "Title", "Org", "Section")]
    assert _corpus_hash(chunks_a) != _corpus_hash(chunks_b)
    assert _corpus_hash(chunks_a) == _corpus_hash(chunks_a)


def test_tokenize_lowercases_and_splits_on_non_alphanumeric():
    assert _tokenize("Sulfa-Allergy Conflict!") == ["sulfa", "allergy", "conflict"]


def test_reciprocal_rank_fusion_favors_items_ranked_highly_in_both_lists():
    # index 2 is #1 in both rankings -> should come out on top of the fused list.
    bm25_ranking = [2, 0, 1]
    dense_ranking = [2, 1, 0]
    fused = reciprocal_rank_fusion([bm25_ranking, dense_ranking])
    assert fused[0] == 2


def test_reciprocal_rank_fusion_includes_items_present_in_only_one_list():
    """Boundary: an item found by only one retrieval method (e.g. an exact keyword match BM25
    catches but dense search misses) must still surface in the fused ranking, not get dropped."""
    fused = reciprocal_rank_fusion([[0, 1], [2]])
    assert set(fused) == {0, 1, 2}


def test_chunk_to_evidence_builds_a_guideline_shaped_citation():
    chunk = _Chunk(
        chunk_id="doc#section",
        text="Some clinical guidance text.",
        source_slug="doc",
        source_title="Test Guideline",
        source_org="Test Org",
        section="Section",
    )
    evidence = _chunk_to_evidence(chunk)
    assert evidence.citation.source_type == "guideline"
    assert evidence.citation.source_id == "doc"
    assert evidence.citation.field_or_chunk_id == "doc#section"
    assert evidence.citation.quote_or_value.startswith("Some clinical guidance")
