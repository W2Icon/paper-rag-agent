"""
Query → retrieval-mode router.

Picks one of {vector, fts, graph, all} as the primary recall mode for a
given natural-language query. Two layers:

  1. LLM router  (simple tier, JSON output)  — used when an LLM provider
     is available. Reads only the query (no DB context) so it stays cheap.
  2. Rule router — fallback when LLM is unavailable or fails. Heuristics
     over query phrasing PLUS a check against the actual KG anchors
     (if the query produces no anchors, graph is downgraded).

Modes:
  vector  semantic / abstract similarity  — "papers about <topic>", summaries
  fts     keyword / lexical match         — author names, exact identifiers
  graph   entity / relation traversal     — concepts, methods, fields, cited works
  all     union of vector + fts + graph   — ambiguous or multi-signal queries
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from db_connection import DatabaseConnection


VALID_MODES = ("vector", "fts", "graph", "all")


@dataclass
class RouteDecision:
    mode: str               # one of VALID_MODES
    reason: str             # short human-readable explanation
    source: str             # "llm" | "rule" | "rule_fallback"


# ── Rule-based router ────────────────────────────────────────────


_GRAPH_HINTS = (
    " in ", "field of", "research field", "methodology", "approach",
    "method for", "techniques for", "technique of", "related to",
    "compare", "comparison", "cite", "cited by", "use the ",
    "based on", " vs ", "versus", "between ", "shared ",
    "papers that", "papers using", "papers applying", "papers in ",
    "research on ", "method called",
)

_VECTOR_HINTS = (
    "summary of", "summarize", "summarise", "explain", "what is",
    "tell me about", "overview", "background", "introduction to",
    "describe ", "discussion of", "概述", "总结", "介绍",
)

_FTS_HINTS = (
    # Tend to be paired with very specific tokens / IDs
    "author:", "doi:", "title:", "named ", "called ",
)


class RuleRouter:
    """Lightweight router — no LLM, no DB read except an optional anchor probe."""

    def __init__(self, db: Optional[DatabaseConnection] = None):
        self._db = db

    def route(self, query: str) -> RouteDecision:
        if not query or not query.strip():
            return RouteDecision("all", "empty query, run everything", "rule")

        q = " " + query.lower() + " "
        graph_signal = sum(1 for h in _GRAPH_HINTS if h in q)
        vector_signal = sum(1 for h in _VECTOR_HINTS if h in q)
        fts_signal = sum(1 for h in _FTS_HINTS if h in q)

        # Anchor probe: even if phrasing screams "graph", graph is useless
        # when no anchors match the KG. Conversely, finding KG anchors is
        # itself a strong vote for graph.
        has_anchors = False
        if self._db is not None:
            try:
                from graph_retrieval import GraphRetriever
                anchors = GraphRetriever(self._db)._find_anchors(query)
                has_anchors = bool(anchors)
                if has_anchors:
                    graph_signal += 1
            except Exception:
                pass

        if fts_signal >= 1 and fts_signal >= graph_signal and fts_signal >= vector_signal:
            return RouteDecision("fts", "rule: identifier-style cue", "rule")
        if graph_signal >= 2 and graph_signal > vector_signal:
            why = "rule: entity/relation cues" + (" + KG anchor match" if has_anchors else "")
            return RouteDecision("graph", why, "rule")
        if vector_signal >= 1 and vector_signal > graph_signal:
            return RouteDecision("vector", "rule: explanatory/summary cue", "rule")
        if graph_signal >= 1 and has_anchors and vector_signal == 0:
            return RouteDecision("graph", "rule: KG anchors + light graph cue", "rule")
        return RouteDecision("all", "rule: no decisive signal, run all paths", "rule")


# ── LLM router ───────────────────────────────────────────────────


_ROUTER_PROMPT = """You decide which retrieval path to use for a question against an academic-paper library.

Modes:
- vector : semantic similarity over paper abstracts. Best for "papers about <topic>", overviews, summaries, "explain X".
- fts    : lexical / keyword search. Best when the query contains specific identifiers (author names, DOIs, exact phrasing).
- graph  : entity-and-relation traversal across a paper / keyword / research-field knowledge graph. Best when the query names specific concepts, methods, research areas, OR asks about relationships between papers (citing, comparing, sharing references).
- all    : union of vector + fts + graph. Use when the query is ambiguous or mixes signals.

Reply with a single JSON object:
{"mode": "vector|fts|graph|all", "reason": "<short justification, max 15 words>"}

Examples:
- "summarize transformer-based NMT approaches" -> {"mode": "vector", "reason": "summary/overview question"}
- "papers using MILP for EV charging station placement" -> {"mode": "graph", "reason": "entity+method+domain anchors"}
- "Smith 2020 Nature paper on graphene" -> {"mode": "fts", "reason": "specific author + identifier"}
- "what's been done on attention mechanisms and how do these compare" -> {"mode": "all", "reason": "mixed exploratory + comparison"}

Question: """


class LLMRouter:
    """LLM-backed router. Uses the simple tier of `LLMProvider` and returns
    a strict JSON decision."""

    def __init__(self, llm, db: Optional[DatabaseConnection] = None):
        """`llm` is an LLMProvider; `db` is optional and only used for
        rule-based fallback when the LLM call fails."""
        self._llm = llm
        self._fallback = RuleRouter(db)

    def route(self, query: str) -> RouteDecision:
        if not query or not query.strip():
            return RouteDecision("all", "empty query", "rule")
        try:
            obj = self._llm.complete_json(
                _ROUTER_PROMPT + query,
                system="You are a fast retrieval-mode classifier. Output strict JSON only.",
                tier="simple",
            )
            if isinstance(obj, str):
                obj = json.loads(obj)
            mode = (obj.get("mode") or "").strip()
            reason = (obj.get("reason") or "").strip()
            if mode not in VALID_MODES:
                raise ValueError(f"LLM returned invalid mode: {mode!r}")
            return RouteDecision(mode, reason or "llm-routed", "llm")
        except Exception as e:
            decision = self._fallback.route(query)
            return RouteDecision(
                decision.mode,
                f"llm failed ({type(e).__name__}); {decision.reason}",
                "rule_fallback",
            )


# ── Top-level factory ────────────────────────────────────────────


def get_router(db: DatabaseConnection, llm=None) -> "LLMRouter | RuleRouter":
    """Returns an LLM router when an `llm` provider is supplied, otherwise
    a rule-based one. Both expose the same `.route(query) -> RouteDecision`."""
    if llm is not None:
        return LLMRouter(llm, db=db)
    return RuleRouter(db)
