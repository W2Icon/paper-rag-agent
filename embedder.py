"""
PaperEmbedder — Phase 3 vector generation.

Embeds title / abstract / sections / references via the configured LLM provider
(default: Qwen text-embedding-v4 through DashScope International) and writes
float32 bytes into the pre-allocated BLOB columns.

Design decisions:
  - **fulltext_embedding** is computed as the mean-pool of section embeddings
    rather than a separate API call. Most papers exceed the 8192-token cap on
    a single embedding request, and mean-pool is a strong baseline.
  - References are embedded as `"<title> [<authors>, <year>]"` so author/year
    contribute to similarity (matters for shared-citation dedup).
  - Sections are embedded by joining their paragraphs and truncating at the
    provider's per-request char budget.
  - Inputs are sliced into batches by the provider itself (Qwen cap = 10).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from llm_interface import LLMProvider
from models import (
    PaperRecord, SectionRecord, ReferenceRecord, LitReviewEntryRecord,
)


# Rough char budget per text — Qwen v4 accepts 8192 tokens; ~3.5 char/token (EN)
# or ~2 char/token (CN), so 16000 chars is a safe upper bound for either.
MAX_CHARS_PER_INPUT = 16000

# ── Two-tier chunking (Phase 3+) ──────────────────────────────────
#
# Sections longer than CHUNK_THRESHOLD_CHARS get split into chunks of
# ~CHUNK_TARGET_CHARS at paragraph boundaries. The section's own embedding
# is then the mean-pool of its chunk embeddings (mirrors the existing
# fulltext = mean(sections) design).
#
# Short sections (≤ threshold) produce one chunk = the whole section, so
# behavior is identical to the pre-chunking pipeline.
CHUNK_THRESHOLD_CHARS = 8000   # below this → single chunk (no split)
CHUNK_TARGET_CHARS = 4000      # try to land each chunk near this size
CHUNK_MAX_CHARS = 8000         # hard ceiling per chunk before truncation


# ── Serialization helpers ─────────────────────────────────────────


def vec_to_bytes(vec: list[float]) -> bytes:
    """Pack a float vector as raw little-endian float32 bytes."""
    return struct.pack(f"<{len(vec)}f", *vec)


def bytes_to_vec(blob: Optional[bytes]) -> list[float]:
    """Unpack float32 bytes into a Python list. Returns [] for None/empty."""
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def mean_pool(vectors: list[list[float]]) -> list[float]:
    """Element-wise mean of a list of vectors. Empty list → []."""
    if not vectors:
        return []
    n = len(vectors)
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        if len(v) != dim:
            continue
        for i, x in enumerate(v):
            out[i] += x
    return [x / n for x in out]


# ── Chunking ──────────────────────────────────────────────────────


@dataclass
class SectionChunk:
    """One chunk of a section's body, ready to embed and persist."""
    chunk_idx: int
    text: str           # the input string for the embedder
    char_start: int     # offset within the section's joined paragraph text
    char_end: int


def chunk_section(section: SectionRecord, *,
                  threshold: int = CHUNK_THRESHOLD_CHARS,
                  target: int = CHUNK_TARGET_CHARS,
                  max_chars: int = CHUNK_MAX_CHARS) -> list[SectionChunk]:
    """Split a section's body into chunks at paragraph boundaries.

    Short sections (joined body ≤ `threshold` chars) return a single chunk
    containing the whole section — identical embedding behavior to the
    pre-chunking code path.

    Long sections are greedy-packed paragraph-by-paragraph into chunks of
    target ~`target` chars (hard cap `max_chars`). No overlap (the section
    mean-pool captures cross-chunk context already).
    """
    paragraphs = section.paragraphs or (
        [section.body_text] if section.body_text else []
    )
    body = " ".join(paragraphs).strip()

    if not body:
        return [SectionChunk(chunk_idx=0, text=" ", char_start=0, char_end=0)]

    heading = section.heading or ""

    def wrap(body_text: str, start: int, end: int, idx: int) -> SectionChunk:
        prefixed = f"{heading}\n{body_text}".strip() if heading else body_text
        return SectionChunk(
            chunk_idx=idx,
            text=prefixed[:MAX_CHARS_PER_INPUT] or " ",
            char_start=start,
            char_end=end,
        )

    # Short path: one chunk for the whole section
    if len(body) <= threshold:
        return [wrap(body, 0, len(body), 0)]

    # Long path: greedy paragraph packing
    chunks: list[SectionChunk] = []
    # Track running char offset of each paragraph within `body`
    para_offsets: list[tuple[int, int]] = []
    cursor = 0
    for p in paragraphs:
        start = body.find(p, cursor) if p else cursor
        if start < 0:
            start = cursor
        end = start + len(p)
        para_offsets.append((start, end))
        cursor = end

    i = 0
    n = len(paragraphs)
    chunk_idx = 0
    while i < n:
        cur_paras: list[str] = []
        cur_len = 0
        j = i
        while j < n:
            p = paragraphs[j]
            if cur_paras and cur_len + len(p) + 1 > max_chars:
                break
            cur_paras.append(p)
            cur_len += len(p) + 1
            j += 1
            if cur_len >= target:
                break

        if not cur_paras:
            # Single paragraph longer than max_chars — emit truncated and
            # advance one paragraph (avoid infinite loop).
            cur_paras = [paragraphs[i][:max_chars]]
            j = i + 1

        start = para_offsets[i][0]
        end = para_offsets[j - 1][1]
        chunks.append(wrap(" ".join(cur_paras), start, end, chunk_idx))
        chunk_idx += 1
        i = j

    return chunks


# ── Embedder ──────────────────────────────────────────────────────


@dataclass
class EmbeddedChunk:
    """Result row for one section chunk (persisted to section_chunks table)."""
    section_id: int
    chunk_idx: int
    text: str
    char_start: int
    char_end: int
    vec: list[float]
    paper_id: int = 0  # stamped by BatchProcessor before persistence


@dataclass
class PaperEmbeddingResult:
    title: list[float]
    abstract: list[float]
    fulltext: list[float]
    sections: list[tuple[int, list[float]]]   # (section_id, vec) — mean-pool of chunks
    references: list[tuple[int, list[float]]]  # (ref_id, vec)
    chunks: list[EmbeddedChunk] = field(default_factory=list)

    @property
    def total_vectors(self) -> int:
        n = sum(1 for v in (self.title, self.abstract, self.fulltext) if v)
        return n + len(self.sections) + len(self.references) + len(self.chunks)


class PaperEmbedder:

    def __init__(self, provider: LLMProvider):
        self._llm = provider

    # ── Per-component embedding ──────────────────────────────────

    def embed_paper(
        self,
        paper: PaperRecord,
        sections: list[SectionRecord],
        references: list[ReferenceRecord],
        *,
        include_sections: bool = True,
        include_refs: bool = True,
    ) -> PaperEmbeddingResult:
        # 1. Title + abstract: always cheap, batch them together
        head_texts = [
            (paper.title or paper.pdf_path)[:MAX_CHARS_PER_INPUT],
            (paper.abstract or paper.title or " ")[:MAX_CHARS_PER_INPUT],
        ]
        head_vecs = self._llm.embed(head_texts)
        title_vec, abstract_vec = head_vecs[0], head_vecs[1]

        # 2. Sections: two-tier chunking. Each section produces 1+ chunks,
        # chunks get embedded, and the section's own embedding is the
        # mean-pool of its chunk embeddings. Short sections collapse to a
        # single chunk so behavior is identical to the pre-chunking path.
        section_vecs: list[tuple[int, list[float]]] = []
        embedded_chunks: list[EmbeddedChunk] = []
        if include_sections and sections:
            # Build a flat list of (section, chunk) pairs so we can batch all
            # chunk texts into one (auto-batched) embed call.
            chunk_pairs: list[tuple[SectionRecord, SectionChunk]] = []
            for s in sections:
                for ch in chunk_section(s):
                    chunk_pairs.append((s, ch))

            if chunk_pairs:
                chunk_texts = [ch.text for _, ch in chunk_pairs]
                chunk_vecs = self._llm.embed(chunk_texts)

                # Group chunks back per-section to mean-pool
                per_section: dict[int, list[list[float]]] = {}
                for (s, ch), v in zip(chunk_pairs, chunk_vecs):
                    if s.id is None:
                        continue
                    per_section.setdefault(s.id, []).append(v)
                    embedded_chunks.append(EmbeddedChunk(
                        section_id=s.id,
                        chunk_idx=ch.chunk_idx,
                        text=ch.text,
                        char_start=ch.char_start,
                        char_end=ch.char_end,
                        vec=v,
                    ))

                for sid, vecs in per_section.items():
                    section_vecs.append((sid, mean_pool(vecs)))

        # 3. References: embed each as "title [authors, year]"
        ref_vecs: list[tuple[int, list[float]]] = []
        if include_refs and references:
            ref_texts = [_ref_text(r) for r in references]
            vecs = self._llm.embed(ref_texts)
            ref_vecs = list(zip([r.id for r in references], vecs))

        # 4. Fulltext: mean-pool of section embeddings; fallback to abstract
        if section_vecs:
            fulltext_vec = mean_pool([v for _, v in section_vecs])
        else:
            fulltext_vec = list(abstract_vec)

        return PaperEmbeddingResult(
            title=title_vec,
            abstract=abstract_vec,
            fulltext=fulltext_vec,
            sections=section_vecs,
            references=ref_vecs,
            chunks=embedded_chunks,
        )

    # ── Query-side embedding (used at retrieval time) ────────────

    def embed_query(self, text: str) -> list[float]:
        text = (text or " ")[:MAX_CHARS_PER_INPUT]
        return self._llm.embed([text])[0]

    # ── LLM-content embedding (after `paperdb analyze`) ──────────

    def embed_llm_content(
        self,
        paper: PaperRecord,
        viewpoints: list[LitReviewEntryRecord],
    ) -> "LLMContentEmbeddingResult":
        """Embed LLM-generated artifacts so the retriever can find papers by
        "what they argue", not only "what their abstract says".

        Two products:
          1. `summary_embedding` — one vector per paper, built from a concat
             of llm_summary + llm_methodology + key_findings. Captures the
             paper's core argument in distilled form (richer than the raw
             abstract, which is often just a teaser).
          2. `viewpoint_embeddings` — one vector per `lit_review_entries` row.
             Lets queries like "what do papers say about Smith 2020?" hit
             the explicit viewpoint text, not just incidental word overlap.

        Returns vectors for caller to persist via PaperRepo / LitReviewRepo.
        """
        summary_text = _summary_text(paper)
        view_texts = [v.embedding_text or " " for v in viewpoints]

        # Batch summary + viewpoints into one embed call; provider auto-chunks
        if summary_text:
            inputs = [summary_text[:MAX_CHARS_PER_INPUT]] + [t[:MAX_CHARS_PER_INPUT] for t in view_texts]
            vecs = self._llm.embed(inputs)
            summary_vec = vecs[0]
            view_vecs = vecs[1:]
        else:
            summary_vec = []
            view_vecs = self._llm.embed([t[:MAX_CHARS_PER_INPUT] for t in view_texts]) if view_texts else []

        viewpoint_pairs: list[tuple[int, list[float]]] = []
        for v, vec in zip(viewpoints, view_vecs):
            if v.id is None:
                continue
            viewpoint_pairs.append((v.id, vec))

        return LLMContentEmbeddingResult(
            summary=summary_vec,
            viewpoints=viewpoint_pairs,
        )


@dataclass
class LLMContentEmbeddingResult:
    summary: list[float]
    viewpoints: list[tuple[int, list[float]]]  # (lit_review_entry_id, vec)

    @property
    def total_vectors(self) -> int:
        return (1 if self.summary else 0) + len(self.viewpoints)


def _summary_text(paper: PaperRecord) -> str:
    """Compose the text we embed for a paper's `summary_embedding`. Includes
    summary + research field + methodology + key findings — the LLM-distilled
    core argument, more focused than the raw abstract."""
    parts: list[str] = []
    if paper.llm_summary:
        parts.append(paper.llm_summary.strip())
    if paper.llm_research_field:
        parts.append(f"Field: {paper.llm_research_field.strip()}")
    if paper.llm_methodology:
        parts.append(f"Methodology: {paper.llm_methodology.strip()}")
    if paper.llm_key_findings:
        findings = "; ".join(str(f) for f in paper.llm_key_findings)
        parts.append(f"Key findings: {findings}")
    return "\n".join(parts).strip()


# ── Text builders ─────────────────────────────────────────────────


def _section_text(section: SectionRecord) -> str:
    body = " ".join(section.paragraphs) if section.paragraphs else section.body_text
    composed = f"{section.heading}\n{body}".strip()
    return composed[:MAX_CHARS_PER_INPUT] or " "


def _ref_text(ref: ReferenceRecord) -> str:
    parts: list[str] = []
    if ref.title:
        parts.append(ref.title)
    elif ref.text:
        parts.append(ref.text)
    if ref.authors:
        parts.append(f"[{', '.join(ref.authors[:3])}{', et al.' if len(ref.authors) > 3 else ''}]")
    if ref.year:
        parts.append(f"({ref.year})")
    composed = " ".join(parts).strip()
    return composed[:MAX_CHARS_PER_INPUT] or " "
