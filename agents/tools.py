"""
Agent toolbelt — thin Python wrappers around the existing repos/retrievers,
exposed in OpenAI function-calling schema.

Each tool returns a JSON-friendly dict and accepts only json-friendly args.
The db connection and the embedding-capable LLM provider are injected via
ToolContext to avoid re-creating them on every call.

Tools deliberately return COMPACT data — full bodies risk blowing the
agent's context window. Specialists ask for more detail when needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from db_connection import DatabaseConnection
from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo
from db.lit_review_repo import LitReviewRepo
from db.shared_citations_repo import SharedCitationsRepo
from db.fts_index import ensure_fts_index
from embedder import PaperEmbedder, bytes_to_vec
from intent_classifier import RetrievalProfile
from llm_interface import LLMProvider
from retrieval import Retriever


# ── Context (injected into every tool) ────────────────────────────


@dataclass
class ToolContext:
    db: DatabaseConnection
    llm: LLMProvider
    embedder: PaperEmbedder
    retriever: Optional[Retriever] = None
    # Per-specialist intent profile. None ⇒ no section weighting (default
    # abstract+FTS recall). When set, search_papers passes section_weights
    # to hybrid_search so the section recall path fires.
    profile: Optional[RetrievalProfile] = None

    def get_retriever(self) -> Retriever:
        if self.retriever is None:
            ensure_fts_index(self.db)
            self.retriever = Retriever(self.db)
        return self.retriever

    def with_profile(self, profile: Optional[RetrievalProfile]) -> "ToolContext":
        """Shallow-clone the context with a different profile. Retriever and
        other heavy state are SHARED — only `profile` is overridden. Used by
        specialists to inject their per-task intent without mutating a
        shared parent context (thread-safe under parallel dispatch)."""
        return replace(self, profile=profile)


# ── Tool schemas (OpenAI function-calling format) ─────────────────


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": (
                "Search the paper library by natural-language query. Returns ranked papers with "
                "title and abstract snippet. Use mode='hybrid' (default) for best recall, "
                "'vector' for semantic-only (better cross-lingual), 'fts' for exact term match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["hybrid", "vector", "fts"], "default": "hybrid"},
                    "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 30},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper",
            "description": (
                "Fetch a paper's metadata + LLM analysis (summary, research_field, methodology, "
                "key_findings). Use after search_papers to read details. Returns null if not found."
            ),
            "parameters": {
                "type": "object",
                "properties": {"paper_id": {"type": "integer"}},
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_sections",
            "description": (
                "List sections of a paper with truncated body. Useful when LLM summary is too "
                "shallow and you need actual text. Returns [{heading, level, snippet, page_start}]."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "integer"},
                    "max_chars_per_section": {"type": "integer", "default": 400, "minimum": 80, "maximum": 1500},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_lit_review_entries",
            "description": (
                "Get the LLM-extracted viewpoints this paper expresses about its OWN cited works. "
                "Each entry = (cited_ref_id, viewpoint, category). Useful for understanding how "
                "the paper positions itself vs prior art."
            ),
            "parameters": {
                "type": "object",
                "properties": {"paper_id": {"type": "integer"}},
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_references",
            "description": (
                "Get the paper's references sorted by LLM-rated relevance. Each ref has "
                "title, authors, year, doi, llm_relevance_score, llm_relationship "
                "(foundational/comparison/methodology/...). Top N only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "integer"},
                    "min_relevance": {"type": "number", "default": 0.0},
                    "top_n": {"type": "integer", "default": 15, "minimum": 1, "maximum": 60},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_related_papers",
            "description": (
                "Find OTHER papers in the library that share references with this paper. "
                "Returns [{paper_id, title, shared_count, relationship, similarity_score}]. "
                "Relationship is the LLM-determined paper-to-paper label."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "integer"},
                    "min_shared": {"type": "integer", "default": 1, "minimum": 1},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_papers_graph",
            "description": (
                "Knowledge-graph traversal over keywords / research-fields / papers. "
                "PREFER THIS over search_papers when the question names specific concepts, "
                "methods, or research areas; asks about citing/cited relationships; or "
                "asks about research gaps. Returns ranked papers with the matched "
                "graph anchors. With include_external=true, also surfaces highly-cited "
                "but not-yet-imported papers (useful for gap analysis)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 30},
                    "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 2,
                              "description": "1 = direct keyword/field hits only; "
                                              "2 = also expand via co-occurring keywords"},
                    "include_external": {"type": "boolean", "default": False,
                                         "description": "Also return paper_external nodes "
                                                         "most-cited by matching local papers"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_within_paper",
            "description": (
                "Semantic search over a SPECIFIC paper's sections. Returns top-K most relevant "
                "section snippets to the query. Use when you need passages, not whole sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 8},
                },
                "required": ["paper_id", "query"],
            },
        },
    },
]


# ── Implementations ───────────────────────────────────────────────


def _truncate(s: Optional[str], n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


def search_papers(ctx: ToolContext, query: str, mode: str = "hybrid",
                  top_k: int = 8) -> list[dict]:
    top_k = max(1, min(int(top_k), 30))
    use_vector = mode in ("hybrid", "vector")
    use_fts = mode in ("hybrid", "fts")
    qvec = ctx.embedder.embed_query(query) if use_vector else None
    # If the specialist installed a profile, route through section + viewpoint paths.
    section_weights = None
    use_viewpoints = False
    if ctx.profile is not None:
        if not ctx.profile.is_default:
            section_weights = ctx.profile.section_weights
        use_viewpoints = ctx.profile.use_viewpoints
    hits = ctx.get_retriever().hybrid_search(
        query, qvec, per_path_k=30, top_k=top_k,
        use_vector=use_vector, use_fts=use_fts,
        section_weights=section_weights,
        use_viewpoints=use_viewpoints,
    )
    return [
        {
            "paper_id": h.paper_id,
            "title": h.title,
            "score": round(h.score, 4),
            "abstract_snippet": _truncate(h.abstract, 240),
            "source_scores": {k: round(v, 4) for k, v in h.source_scores.items()
                              if isinstance(v, (int, float))},
        }
        for h in hits
    ]


def get_paper(ctx: ToolContext, paper_id: int) -> Optional[dict]:
    p = PaperRepo(ctx.db).get_by_id(int(paper_id))
    if not p:
        return None
    return {
        "paper_id": p.id,
        "title": p.title,
        "authors": p.authors,
        "abstract": p.abstract,
        "keywords": p.keywords,
        "doi": p.doi,
        "llm_summary": p.llm_summary,
        "llm_research_field": p.llm_research_field,
        "llm_methodology": p.llm_methodology,
        "llm_key_findings": p.llm_key_findings or [],
        "llm_analyzed_at": p.llm_analyzed_at,
    }


def get_paper_sections(ctx: ToolContext, paper_id: int,
                       max_chars_per_section: int = 400) -> list[dict]:
    secs = PaperRepo(ctx.db).get_sections(int(paper_id))
    out = []
    for s in secs:
        body = " ".join(s.paragraphs) if s.paragraphs else s.body_text
        out.append({
            "section_id": s.id,
            "heading": s.heading,
            "level": s.level,
            "page_start": s.page_start,
            "page_end": s.page_end,
            "snippet": _truncate(body, int(max_chars_per_section)),
        })
    return out


def get_lit_review_entries(ctx: ToolContext, paper_id: int) -> list[dict]:
    entries = LitReviewRepo(ctx.db).get_by_paper(int(paper_id))
    return [
        {
            "cited_ref_id": e.cited_ref_id,
            "category": e.category,
            "viewpoint": e.viewpoint,
        }
        for e in entries
    ]


def get_paper_references(ctx: ToolContext, paper_id: int,
                         min_relevance: float = 0.0, top_n: int = 15) -> list[dict]:
    refs = ReferenceRepo(ctx.db).get_references_for_paper(int(paper_id))
    # Re-fetch llm fields directly (not on dataclass currently)
    rows = ctx.db.conn.execute(
        "SELECT id, title, year, authors, identifiers, "
        "llm_relevance_score, llm_relationship "
        "FROM references_ WHERE paper_id = ?",
        (int(paper_id),),
    ).fetchall()
    items = []
    for r in rows:
        score = r["llm_relevance_score"]
        if score is None or score < float(min_relevance):
            continue
        ids = json.loads(r["identifiers"] or "{}")
        items.append({
            "ref_id": r["id"],
            "title": r["title"],
            "authors": json.loads(r["authors"] or "[]"),
            "year": r["year"],
            "doi": ids.get("DOI"),
            "llm_relevance_score": score,
            "llm_relationship": r["llm_relationship"],
        })
    items.sort(key=lambda x: -(x["llm_relevance_score"] or 0.0))
    return items[: int(top_n)]


def find_related_papers(ctx: ToolContext, paper_id: int,
                        min_shared: int = 1) -> list[dict]:
    paper_repo = PaperRepo(ctx.db)
    rows = SharedCitationsRepo(ctx.db).list_pairs_for_paper(
        int(paper_id), min_shared=int(min_shared)
    )
    out = []
    for r in rows:
        other = paper_repo.get_by_id(r["other_id"])
        out.append({
            "paper_id": r["other_id"],
            "title": other.title if other else "?",
            "shared_count": r["shared_count"],
            "relationship": r["relationship"],
            "similarity_score": (round(r["similarity_score"], 3)
                                  if r["similarity_score"] is not None else None),
        })
    return out


def search_within_paper(ctx: ToolContext, paper_id: int, query: str,
                        top_k: int = 3) -> list[dict]:
    """Section-level vector search restricted to one paper."""
    import math
    rows = ctx.db.conn.execute(
        "SELECT id, heading, paragraphs, section_embedding "
        "FROM sections WHERE paper_id = ? AND section_embedding IS NOT NULL",
        (int(paper_id),),
    ).fetchall()
    if not rows:
        return []
    qvec = ctx.embedder.embed_query(query)
    qn = math.sqrt(sum(x * x for x in qvec)) + 1e-12

    scored = []
    for r in rows:
        vec = bytes_to_vec(r["section_embedding"])
        if not vec:
            continue
        dot = sum(a * b for a, b in zip(qvec, vec))
        rn = math.sqrt(sum(x * x for x in vec)) + 1e-12
        cos = dot / (qn * rn)
        scored.append((cos, r))
    scored.sort(key=lambda x: -x[0])

    out = []
    for cos, r in scored[: int(top_k)]:
        paragraphs = json.loads(r["paragraphs"] or "[]")
        out.append({
            "section_id": r["id"],
            "heading": r["heading"],
            "score": round(cos, 4),
            "snippet": _truncate(" ".join(paragraphs), 400),
        })
    return out


def search_papers_graph(ctx: ToolContext, query: str, top_k: int = 8,
                         depth: int = 2, include_external: bool = False) -> dict:
    """Graph-only retrieval. Returns matched anchors + ranked local papers,
    and optionally a list of highly-cited external papers (for gap signals).
    """
    from graph_retrieval import GraphRetriever
    top_k = max(1, min(int(top_k), 30))
    depth = 1 if int(depth) <= 1 else 2
    gr = GraphRetriever(ctx.db)

    exp = gr.explain(query)
    anchors_out = [
        {"node_type": a["node_type"], "name": a["display_name"],
         "relevance": round(a["relevance"], 2)}
        for a in exp.get("anchors", [])
    ]

    hits = gr.search(query, k=top_k, depth=depth)
    paper_repo = PaperRepo(ctx.db)
    papers_out = []
    for pid, score in hits:
        p = paper_repo.get_by_id(pid)
        if p is None:
            continue
        papers_out.append({
            "paper_id": pid,
            "title": p.title,
            "score": round(score, 4),
            "abstract_snippet": _truncate(p.abstract, 240),
            "research_field": p.llm_research_field,
        })

    out: dict[str, Any] = {"anchors": anchors_out, "papers": papers_out}

    if include_external:
        ext = gr.search_external_recommendations(query, k=top_k)
        out["external_recommendations"] = [
            {"title": e["title"], "doi": e["doi"], "cited_by_count": e["cite_count"]}
            for e in ext
        ]
    return out


# ── Dispatch table ────────────────────────────────────────────────


TOOL_DISPATCH: dict[str, Callable[..., Any]] = {
    "search_papers": search_papers,
    "search_papers_graph": search_papers_graph,
    "get_paper": get_paper,
    "get_paper_sections": get_paper_sections,
    "get_lit_review_entries": get_lit_review_entries,
    "get_paper_references": get_paper_references,
    "find_related_papers": find_related_papers,
    "search_within_paper": search_within_paper,
}


def execute_tool(ctx: ToolContext, name: str, args: dict) -> Any:
    """Call a registered tool with arguments from the LLM. Returns the raw
    Python result (caller is responsible for serialization)."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return fn(ctx, **args)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} raised {type(e).__name__}: {e}"}
