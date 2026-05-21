"""Tests for RRF fusion and cosine similarity in retrieval.py.

These are pure-Python algorithms (no DB / LLM) so we can test them directly.
"""

from __future__ import annotations

import math

import pytest

from retrieval import RRF_K, Retriever, SearchHit, _cosine_matrix


# ── Cosine similarity ───────────────────────────────────────────────


def test_cosine_identical_vectors_returns_one() -> None:
    q = [1.0, 0.0, 0.0]
    sims = _cosine_matrix(q, [[1.0, 0.0, 0.0]])
    assert sims[0] == pytest.approx(1.0, abs=1e-6)


def test_cosine_orthogonal_returns_zero() -> None:
    sims = _cosine_matrix([1.0, 0.0], [[0.0, 1.0]])
    assert sims[0] == pytest.approx(0.0, abs=1e-6)


def test_cosine_handles_empty_doc_vector() -> None:
    sims = _cosine_matrix([1.0, 0.0], [[]])
    # Empty doc → sentinel -1.0 (so it ranks last).
    assert sims[0] == -1.0


def test_cosine_is_scale_invariant() -> None:
    q = [1.0, 1.0]
    a = [2.0, 2.0]   # same direction, larger magnitude
    b = [0.1, 0.1]   # same direction, smaller magnitude
    sims = _cosine_matrix(q, [a, b])
    assert sims[0] == pytest.approx(1.0, abs=1e-6)
    assert sims[1] == pytest.approx(1.0, abs=1e-6)


def test_cosine_ranking_order() -> None:
    q = [1.0, 0.0]
    docs = [
        [0.0, 1.0],    # orthogonal
        [1.0, 0.0],    # identical
        [0.7, 0.3],    # close
    ]
    sims = _cosine_matrix(q, docs)
    ranked = sorted(range(len(sims)), key=lambda i: -sims[i])
    assert ranked[0] == 1  # identical wins
    assert ranked[-1] == 0  # orthogonal loses


# ── Reciprocal Rank Fusion ──────────────────────────────────────────


def test_rrf_single_path_preserves_order() -> None:
    hits = {"vector": [(101, 0.9), (102, 0.7), (103, 0.5)]}
    fused = Retriever.rrf_fuse(hits, top_k=10)
    assert [h.paper_id for h in fused] == [101, 102, 103]


def test_rrf_two_paths_fuses_correctly() -> None:
    # Paper 1: rank 1 in both paths → highest fused score.
    # Paper 2: rank 2 in both.
    # Paper 3: only in vector path, rank 3.
    # Paper 4: only in fts path, rank 3.
    hits = {
        "vector": [(1, 0.9), (2, 0.7), (3, 0.5)],
        "fts": [(1, 5.0), (2, 4.0), (4, 3.0)],
    }
    fused = Retriever.rrf_fuse(hits, top_k=10)
    ids = [h.paper_id for h in fused]
    assert ids[0] == 1   # appears top-1 in both paths
    assert ids[1] == 2   # second in both paths
    # 3 and 4 each appear only once at rank 3 → tied score, order arbitrary
    assert set(ids[2:4]) == {3, 4}


def test_rrf_records_per_path_provenance() -> None:
    hits = {
        "vector": [(1, 0.9), (2, 0.5)],
        "fts":    [(2, 4.0), (1, 2.0)],
    }
    fused = Retriever.rrf_fuse(hits, top_k=10)
    by_id = {h.paper_id: h for h in fused}
    assert by_id[1].source_scores == {"vector": 0.9, "fts": 2.0}
    assert by_id[1].rank_in_source == {"vector": 1, "fts": 2}
    assert by_id[2].source_scores == {"vector": 0.5, "fts": 4.0}
    assert by_id[2].rank_in_source == {"vector": 2, "fts": 1}


def test_rrf_score_formula() -> None:
    """RRF score = sum over paths of 1 / (k + rank). Verify the math."""
    hits = {"a": [(1, 0.0), (2, 0.0)]}
    fused = Retriever.rrf_fuse(hits, top_k=10)
    by_id = {h.paper_id: h for h in fused}
    assert by_id[1].score == pytest.approx(1.0 / (RRF_K + 1))
    assert by_id[2].score == pytest.approx(1.0 / (RRF_K + 2))


def test_rrf_empty_input_returns_empty() -> None:
    assert Retriever.rrf_fuse({}, top_k=10) == []
    assert Retriever.rrf_fuse({"a": []}, top_k=10) == []


def test_rrf_top_k_truncates() -> None:
    hits = {"a": [(i, 1.0) for i in range(20)]}
    fused = Retriever.rrf_fuse(hits, top_k=5)
    assert len(fused) == 5


def test_searchhit_explain_lists_per_path() -> None:
    h = SearchHit(paper_id=1, score=0.1)
    h.source_scores = {"vector": 0.9, "fts": 5.0}
    h.rank_in_source = {"vector": 1, "fts": 3}
    exp = h.explain()
    assert "vector#1" in exp
    assert "fts#3" in exp
