"""Hybrid retrieval over the small guideline corpus (W2_ARCHITECTURE.md Section 4): sparse (BM25)
+ dense (Voyage embeddings) search, fused via reciprocal rank fusion, then reranked with Voyage
rerank. Returns citation-shaped evidence chunks the verifier can check claims against.

Corpus lives in agent/data/guidelines/*.md -- each file is one guideline document, front-matter
(source org, title, url) followed by `## `-delimited sections; each section is one retrievable
chunk. Embeddings are cached to agent/data/guidelines_index_cache.json (gitignored, rebuilt
automatically whenever the corpus content changes) so a normal run doesn't re-embed on every boot.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass

import voyageai
from rank_bm25 import BM25Okapi

from .config import settings
from .schemas import Citation, GuidelineChunk

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDELINES_DIR = os.path.join(_AGENT_ROOT, "data", "guidelines")
INDEX_CACHE_PATH = os.path.join(_AGENT_ROOT, "data", "guidelines_index_cache.json")


@dataclass
class _Chunk:
    chunk_id: str
    text: str
    source_slug: str
    source_title: str
    source_org: str
    section: str


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    _, fm_text, body = parts
    meta = {}
    for line in fm_text.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body.strip()


def load_corpus(guidelines_dir: str = GUIDELINES_DIR) -> list[_Chunk]:
    """Parses every *.md file in `guidelines_dir` into `## `-delimited chunks. Each chunk carries
    enough source metadata (org, title, section) to build a full citation without re-reading the
    file at query time."""
    chunks: list[_Chunk] = []
    if not os.path.isdir(guidelines_dir):
        return chunks
    for filename in sorted(os.listdir(guidelines_dir)):
        if not filename.endswith(".md"):
            continue
        with open(os.path.join(guidelines_dir, filename), "r", encoding="utf-8") as f:
            content = f.read()
        meta, body = _parse_frontmatter(content)
        slug = filename[:-3]
        for section_block in re.split(r"^## ", body, flags=re.MULTILINE):
            section_block = section_block.strip()
            if not section_block:
                continue
            lines = section_block.splitlines()
            section_title = lines[0].strip()
            section_text = "\n".join(lines[1:]).strip()
            if not section_text:
                continue
            chunks.append(
                _Chunk(
                    chunk_id=f"{slug}#{_slugify(section_title)}",
                    text=section_text,
                    source_slug=slug,
                    source_title=meta.get("title", slug),
                    source_org=meta.get("source", "unknown"),
                    section=section_title,
                )
            )
    return chunks


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _corpus_hash(chunks: list[_Chunk]) -> str:
    joined = "\x00".join(f"{c.chunk_id}\x01{c.text}" for c in chunks)
    return hashlib.sha256(joined.encode()).hexdigest()


def _voyage_client() -> voyageai.Client:
    if not settings.voyage_api_key:
        raise RuntimeError("VOYAGE_API_KEY is not set")
    return voyageai.Client(api_key=settings.voyage_api_key)


def _load_or_build_embeddings(chunks: list[_Chunk]) -> list[list[float]]:
    content_hash = _corpus_hash(chunks)
    if os.path.exists(INDEX_CACHE_PATH):
        try:
            with open(INDEX_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("content_hash") == content_hash:
                return cache["embeddings"]
        except (json.JSONDecodeError, KeyError):
            pass  # corrupt/stale cache -- fall through and rebuild

    raw_embeddings = _voyage_client().embed(
        [c.text for c in chunks], model=settings.voyage_embed_model, input_type="document"
    ).embeddings
    # Voyage's SDK types this as list[list[float]] | list[list[int]] -- coerce explicitly so the
    # cosine-similarity math downstream always gets real floats, not just to satisfy mypy.
    embeddings: list[list[float]] = [[float(x) for x in emb] for emb in raw_embeddings]
    os.makedirs(os.path.dirname(INDEX_CACHE_PATH), exist_ok=True)
    with open(INDEX_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"content_hash": content_hash, "embeddings": embeddings}, f)
    return embeddings


# Empirically set from live Voyage rerank scores against this corpus: genuinely relevant
# clinical queries scored 0.5-0.8, an off-topic control query ("chocolate chip cookie recipe")
# scored 0.2-0.4 -- 0.4 sits in the gap between those two clusters, not a tuned/arbitrary value.
MIN_RELEVANCE_SCORE = 0.4

_INDEX: dict | None = None


def _get_index() -> dict:
    """Lazily builds the in-process index once per server lifetime (small corpus, cheap to hold
    in memory) -- call `reset_index()` in tests that need a fresh build."""
    global _INDEX
    if _INDEX is None:
        chunks = load_corpus()
        if not chunks:
            raise RuntimeError(f"no guideline chunks found in {GUIDELINES_DIR}")
        embeddings = _load_or_build_embeddings(chunks)
        bm25 = BM25Okapi([_tokenize(c.text) for c in chunks])
        _INDEX = {"chunks": chunks, "embeddings": embeddings, "bm25": bm25}
    return _INDEX


def reset_index() -> None:
    global _INDEX
    _INDEX = None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60) -> list[int]:
    """Combines multiple rank-ordered lists of item indices into one fused ranking: score =
    sum of 1/(k + rank) across lists. Standard, parameter-light way to combine sparse + dense
    retrieval without calibrating each method's raw score scale against the other."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda i: scores[i], reverse=True)


def _chunk_to_evidence(chunk: _Chunk) -> GuidelineChunk:
    citation = Citation(
        source_type="guideline",
        source_id=chunk.source_slug,
        page_or_section=chunk.section,
        field_or_chunk_id=chunk.chunk_id,
        quote_or_value=chunk.text[:280],
    )
    return GuidelineChunk(
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        source_title=chunk.source_title,
        source_org=chunk.source_org,
        section=chunk.section,
        citation=citation,
    )


def retrieve(query: str, top_k: int = 5, candidate_pool: int = 10) -> list[GuidelineChunk]:
    """Hybrid retrieval: BM25 + Voyage dense search over the guideline corpus, fused via
    reciprocal rank fusion, reranked with Voyage rerank (W2_ARCHITECTURE.md Section 4). Returns the
    top `top_k` chunks as citation-shaped GuidelineChunk objects, or [] if the corpus has nothing
    relevant enough to survive reranking above a minimal bar -- callers must treat empty results as
    a real "no guideline evidence found" case (Section 10 failure-mode table), not an error."""
    index = _get_index()
    chunks: list[_Chunk] = index["chunks"]
    bm25: BM25Okapi = index["bm25"]
    embeddings: list[list[float]] = index["embeddings"]

    bm25_scores = bm25.get_scores(_tokenize(query))
    bm25_ranking = sorted(range(len(chunks)), key=lambda i: bm25_scores[i], reverse=True)[:candidate_pool]

    client = _voyage_client()
    raw_query_embedding = client.embed([query], model=settings.voyage_embed_model, input_type="query").embeddings[0]
    query_embedding: list[float] = [float(x) for x in raw_query_embedding]
    dense_scores = [_cosine_similarity(query_embedding, emb) for emb in embeddings]
    dense_ranking = sorted(range(len(chunks)), key=lambda i: dense_scores[i], reverse=True)[:candidate_pool]

    fused = reciprocal_rank_fusion([bm25_ranking, dense_ranking])[:candidate_pool]
    if not fused:
        return []

    candidate_texts = [chunks[i].text for i in fused]
    rerank_result = client.rerank(query, candidate_texts, model=settings.voyage_rerank_model, top_k=top_k)

    return [
        _chunk_to_evidence(chunks[fused[r.index]])
        for r in rerank_result.results
        if r.relevance_score >= MIN_RELEVANCE_SCORE
    ]
