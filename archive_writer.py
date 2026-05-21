"""
Knowledge-base archive writer.

Renders the SQLite-backed library into a folder of human-readable markdown
files. The DB remains the canonical source of truth — markdown is a derived
view, regenerable on demand:

    <workspace>/archive/                       # path comes from AppConfig.archive_dir
    ├── INDEX.md                               # all papers + cross-paper edges
    ├── _graph.json                            # machine-readable network data
    └── <slug>.md                              # one file per paper

A paper's markdown bundles everything we know about it:
    - extracted metadata (title / authors / DOI)
    - LLM analysis (summary, methodology, key findings)
    - viewpoints (lit_review_entries — what this paper says about each cited work)
    - cross-paper relationships (shared_citations.relationship)
    - top references sorted by LLM relevance

Designed to open cleanly in Obsidian (frontmatter + [[wiki links]]) but also
readable as plain markdown in any editor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from db_connection import DatabaseConnection
from db.lit_review_repo import LitReviewRepo
from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo
from db.shared_citations_repo import SharedCitationsRepo
from db.tag_repo import TagRepo
from models import LitReviewEntryRecord, PaperRecord, ReferenceRecord


_INDEX_FILE = "INDEX.md"
_GRAPH_FILE = "_graph.json"


# ── Slug helpers ──────────────────────────────────────────────────


def slugify(text: str, max_len: int = 80) -> str:
    """Stable filesystem-safe slug. ASCII-only to avoid OS quirks across
    macOS / Linux / NAS; CJK characters get dropped (we have paper_id + year
    in the filename as the unique identifier anyway)."""
    text = re.sub(r"[^A-Za-z0-9\s\-]", "", text or "")
    text = re.sub(r"\s+", "-", text.strip()).lower()
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "untitled"


def paper_filename(paper: PaperRecord) -> str:
    """Naming: `{paper_id:03d}-{slug-of-title}.md`. paper_id-prefixed so
    sort order matches ingest order; slug for human readability."""
    return f"{paper.id:03d}-{slugify(paper.title)}.md"


# ── Per-paper markdown ────────────────────────────────────────────


@dataclass
class _PaperBundle:
    """Snapshot of everything we render for one paper. Pre-fetched so the
    write step has no SQL noise."""
    paper: PaperRecord
    references: list[ReferenceRecord]
    viewpoints: list[LitReviewEntryRecord]
    refs_by_id: dict[int, ReferenceRecord]
    related_pairs: list[dict]  # {other_id, other_title, shared_count, relationship, explanation, similarity}
    tags: list[str]


def _fetch_bundle(db: DatabaseConnection, paper: PaperRecord) -> _PaperBundle:
    ref_repo = ReferenceRepo(db)
    lr_repo = LitReviewRepo(db)
    sc_repo = SharedCitationsRepo(db)
    tag_repo = TagRepo(db)

    refs = ref_repo.get_references_for_paper(paper.id)
    # Pull llm_relevance_score / llm_relationship — not on the ReferenceRecord
    # dataclass, so fetch raw rows.
    score_rows = db.conn.execute(
        "SELECT id, llm_relevance_score, llm_relationship "
        "FROM references_ WHERE paper_id = ?",
        (paper.id,),
    ).fetchall()
    scores = {r["id"]: (r["llm_relevance_score"], r["llm_relationship"]) for r in score_rows}
    for r in refs:
        s = scores.get(r.id)
        if s:
            r.llm_relevance_score = s[0]
            r.llm_relationship = s[1]
        else:
            r.llm_relevance_score = None
            r.llm_relationship = None

    viewpoints = lr_repo.get_by_paper(paper.id)
    refs_by_id = {r.id: r for r in refs}

    pair_rows = sc_repo.list_pairs_for_paper(paper.id, min_shared=1)
    paper_repo = PaperRepo(db)
    related: list[dict] = []
    for r in pair_rows:
        other = paper_repo.get_by_id(r["other_id"])
        related.append({
            "other_id": r["other_id"],
            "other_title": other.title if other else "(deleted)",
            "shared_count": r["shared_count"],
            "relationship": r["relationship"],
            "similarity_score": r["similarity_score"],
        })

    tag_names = [t.name for t in tag_repo.get_tags_for_paper(paper.id)]

    return _PaperBundle(
        paper=paper,
        references=refs,
        viewpoints=viewpoints,
        refs_by_id=refs_by_id,
        related_pairs=related,
        tags=tag_names,
    )


def render_paper_markdown(db: DatabaseConnection, paper_id: int) -> str:
    """Render one paper's archive markdown. Returns the full file content."""
    paper = PaperRepo(db).get_by_id(paper_id)
    if paper is None:
        raise ValueError(f"paper {paper_id} not found")
    bundle = _fetch_bundle(db, paper)
    return _render(bundle)


def _render(b: _PaperBundle) -> str:
    p = b.paper
    lines: list[str] = []

    # Frontmatter (Obsidian / static-site friendly)
    lines.append("---")
    lines.append(f"paper_id: {p.id}")
    lines.append(f"title: {_yaml_str(p.title)}")
    if p.authors:
        lines.append("authors:")
        for a in p.authors:
            lines.append(f"  - {_yaml_str(a)}")
    if p.doi:
        lines.append(f"doi: {_yaml_str(p.doi)}")
    if p.ingested_at:
        lines.append(f"ingested_at: {p.ingested_at}")
    if b.tags:
        lines.append("tags:")
        for t in b.tags:
            lines.append(f"  - {_yaml_str(t)}")
    lines.append("---")
    lines.append("")

    # Title + meta block
    lines.append(f"# {p.title or '(untitled)'}")
    lines.append("")
    if p.authors:
        lines.append("**Authors**: " + ", ".join(p.authors))
    if p.affiliations:
        lines.append("**Affiliations**: " + "; ".join(p.affiliations))
    if p.doi:
        lines.append(f"**DOI**: [{p.doi}](https://doi.org/{p.doi})")
    if p.pdf_path:
        lines.append(f"**PDF**: `{p.pdf_path}`")
    lines.append("")

    # Abstract
    if p.abstract:
        lines.append("## Abstract")
        lines.append("")
        lines.append(p.abstract.strip())
        lines.append("")

    # LLM analysis
    if p.llm_summary or p.llm_methodology or p.llm_key_findings:
        lines.append("## LLM Analysis")
        lines.append("")
        if p.llm_summary:
            lines.append("**Summary**")
            lines.append("")
            lines.append(p.llm_summary.strip())
            lines.append("")
        if p.llm_research_field:
            lines.append(f"**Research Field**: {p.llm_research_field}")
            lines.append("")
        if p.llm_methodology:
            lines.append("**Methodology**")
            lines.append("")
            lines.append(p.llm_methodology.strip())
            lines.append("")
        if p.llm_key_findings:
            lines.append("**Key Findings**")
            lines.append("")
            for f in p.llm_key_findings:
                lines.append(f"- {str(f).strip()}")
            lines.append("")

    # Viewpoints on cited work (论点)
    if b.viewpoints:
        lines.append(f"## Viewpoints on Cited Work ({len(b.viewpoints)})")
        lines.append("")
        lines.append("> What this paper explicitly says about each reference it cites.")
        lines.append("")
        # Group by category for readability
        by_cat: dict[str, list[LitReviewEntryRecord]] = {}
        for v in b.viewpoints:
            cat = v.category or "uncategorized"
            by_cat.setdefault(cat, []).append(v)
        for cat in sorted(by_cat.keys()):
            lines.append(f"### {cat}")
            lines.append("")
            for v in by_cat[cat]:
                ref = b.refs_by_id.get(v.cited_ref_id) if v.cited_ref_id else None
                ref_label = _ref_label(ref) if ref else f"(ref #{v.cited_ref_id})"
                lines.append(f"- **{ref_label}**")
                lines.append(f"  > {v.viewpoint.strip()}")
            lines.append("")

    # Cross-paper relationships (文献中枢)
    if b.related_pairs:
        lines.append("## Cross-paper Relationships")
        lines.append("")
        lines.append("> Other papers in the library that share references with this one.")
        lines.append("")
        for r in sorted(b.related_pairs, key=lambda x: -x["shared_count"]):
            rel = r["relationship"] or "(detection-only, no LLM yet)"
            sim = f", abstract sim {r['similarity_score']:.3f}" if r["similarity_score"] is not None else ""
            lines.append(
                f"- ↔ Paper [{r['other_id']}] "
                f"**{r['other_title'][:80]}** — shared refs: {r['shared_count']}{sim}"
            )
            lines.append(f"  - relationship: `{rel}`")
        lines.append("")

    # References (top 20 by relevance)
    if b.references:
        scored = [r for r in b.references if r.llm_relevance_score is not None]
        scored.sort(key=lambda r: -(r.llm_relevance_score or 0))
        shown = scored[:20] if scored else b.references[:20]
        title = "References (top by LLM relevance)" if scored else f"References ({len(b.references)} total)"
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| # | Title | Year | Score | Role |")
        lines.append("|---|---|---|---|---|")
        for r in shown:
            score = f"{r.llm_relevance_score:.2f}" if r.llm_relevance_score is not None else "—"
            role = r.llm_relationship or "—"
            lines.append(
                f"| {r.ref_number or '?'} | {_truncate(r.title or r.text, 60)} | "
                f"{r.year or '—'} | {score} | {role} |"
            )
        if len(b.references) > len(shown):
            lines.append("")
            lines.append(f"*({len(b.references) - len(shown)} more references not shown)*")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── INDEX.md + _graph.json ────────────────────────────────────────


def render_index(db: DatabaseConnection) -> str:
    """Generate a top-level INDEX.md that lists all papers + the network of
    cross-paper relationships. This is the entry point a human browses to
    find what's in the library."""
    paper_repo = PaperRepo(db)
    sc_repo = SharedCitationsRepo(db)
    papers = sorted(paper_repo.list_papers(limit=10000), key=lambda p: p.id)

    lines: list[str] = []
    lines.append("# Paper Library Index")
    lines.append("")
    lines.append(f"Generated from {len(papers)} paper(s) in `papers.db`.")
    lines.append("")

    # Per-paper card
    lines.append("## Papers")
    lines.append("")
    for p in papers:
        fname = paper_filename(p)
        authors = ", ".join(p.authors[:3]) + (" et al." if len(p.authors) > 3 else "")
        lines.append(f"### [{p.id}] [{p.title or '(untitled)'}](./{fname})")
        meta = []
        if authors:
            meta.append(authors)
        if p.doi:
            meta.append(f"DOI: `{p.doi}`")
        if meta:
            lines.append(", ".join(meta))
        if p.llm_summary:
            lines.append("")
            lines.append(f"> {_truncate(p.llm_summary, 220)}")
        lines.append("")

    # Cross-paper network
    pairs = _list_all_pairs(db, sc_repo)
    if pairs:
        lines.append("## Cross-paper Network")
        lines.append("")
        lines.append("| Pair | Shared refs | Relationship | Abstract sim |")
        lines.append("|---|---|---|---|")
        for p in pairs[:50]:
            rel = p["relationship"] or "(detection-only)"
            sim = f"{p['similarity_score']:.3f}" if p.get("similarity_score") is not None else "—"
            lines.append(
                f"| [{p['a_id']}] ↔ [{p['b_id']}] | {p['shared_count']} | `{rel}` | {sim} |"
            )
        lines.append("")
        if len(pairs) > 50:
            lines.append(f"*({len(pairs) - 50} more pairs not shown)*")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_graph_json(db: DatabaseConnection) -> str:
    """Machine-readable mirror of the cross-paper edges. Lets future
    visualizers (Mermaid, D3, etc.) reuse the same data without re-parsing md."""
    paper_repo = PaperRepo(db)
    sc_repo = SharedCitationsRepo(db)
    papers = sorted(paper_repo.list_papers(limit=10000), key=lambda p: p.id)
    pairs = _list_all_pairs(db, sc_repo)

    data = {
        "nodes": [
            {
                "id": p.id,
                "title": p.title,
                "year": (p.ingested_at or "")[:4],
                "doi": p.doi,
                "filename": paper_filename(p),
            }
            for p in papers
        ],
        "edges": [
            {
                "source": p["a_id"],
                "target": p["b_id"],
                "shared_count": p["shared_count"],
                "relationship": p["relationship"],
                "similarity_score": p["similarity_score"],
            }
            for p in pairs
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _list_all_pairs(db: DatabaseConnection, sc_repo: SharedCitationsRepo) -> list[dict]:
    """Aggregate paper-pair edges across the whole library."""
    rows = db.conn.execute(
        """
        SELECT paper_a_id, paper_b_id, COUNT(*) AS shared_count,
               relationship, AVG(similarity_score) AS similarity_score
        FROM shared_citations
        GROUP BY paper_a_id, paper_b_id, relationship
        ORDER BY shared_count DESC
        """
    ).fetchall()
    return [
        {
            "a_id": r["paper_a_id"],
            "b_id": r["paper_b_id"],
            "shared_count": r["shared_count"],
            "relationship": r["relationship"],
            "similarity_score": r["similarity_score"],
        }
        for r in rows
    ]


# ── Disk writer ───────────────────────────────────────────────────


def write_paper(db: DatabaseConnection, paper_id: int, archive_dir: Path) -> Path:
    """Render + write one paper's markdown file. Returns the path."""
    paper = PaperRepo(db).get_by_id(paper_id)
    if paper is None:
        raise ValueError(f"paper {paper_id} not found")
    archive_dir.mkdir(parents=True, exist_ok=True)
    content = render_paper_markdown(db, paper_id)
    path = archive_dir / paper_filename(paper)
    path.write_text(content, encoding="utf-8")
    return path


def write_index(db: DatabaseConnection, archive_dir: Path) -> tuple[Path, Path]:
    """Render + write INDEX.md and _graph.json. Returns both paths."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    index_path = archive_dir / _INDEX_FILE
    graph_path = archive_dir / _GRAPH_FILE
    index_path.write_text(render_index(db), encoding="utf-8")
    graph_path.write_text(render_graph_json(db), encoding="utf-8")
    return index_path, graph_path


def write_all(db: DatabaseConnection, archive_dir: Path) -> dict:
    """Regenerate the entire archive folder from current DB state."""
    paper_repo = PaperRepo(db)
    archive_dir.mkdir(parents=True, exist_ok=True)
    papers = paper_repo.list_papers(limit=10000)
    written: list[Path] = []
    for p in papers:
        written.append(write_paper(db, p.id, archive_dir))
    index, graph = write_index(db, archive_dir)
    return {
        "papers": len(written),
        "paper_paths": [str(p) for p in written],
        "index": str(index),
        "graph": str(graph),
    }


# ── Internal helpers ──────────────────────────────────────────────


def _yaml_str(s: str) -> str:
    """Quote-safe YAML scalar. Always wrap in double quotes and escape."""
    if not s:
        return '""'
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _truncate(s: Optional[str], n: int) -> str:
    if not s:
        return ""
    s = s.strip().replace("\n", " ")
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _ref_label(ref: ReferenceRecord) -> str:
    bits = []
    if ref.title:
        bits.append(_truncate(ref.title, 80))
    if ref.authors:
        bits.append(f"({ref.authors[0]}{'+' if len(ref.authors) > 1 else ''}, {ref.year or '?'})")
    elif ref.year:
        bits.append(f"({ref.year})")
    return " ".join(bits) or f"ref#{ref.ref_number or '?'}"
