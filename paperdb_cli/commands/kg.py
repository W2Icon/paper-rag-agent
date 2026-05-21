"""`paperdb kg ...` — knowledge graph build / stats / neighbors / search / promote."""

from __future__ import annotations

import json
import sys
from typing import Optional

from db_connection import DatabaseConnection
from db.paper_repo import PaperRepo


def cmd_kg(args, db: DatabaseConnection) -> None:
    from knowledge_graph import KGBuilder
    kg = KGBuilder(db)
    action = args.kg_action or "stats"

    if action == "build":
        if args.paper_id:
            res = kg.add_paper(args.paper_id)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return
        if args.verbose and not args.rebuild:
            print("Syncing all papers into the knowledge graph (incremental)...")
        res = kg.build_all(rebuild=args.rebuild)
        print(f"Processed {res['papers']} paper(s).")
        orph = res["orphans_removed"]
        if any(orph.values()):
            print(f"Swept orphans: {orph}")
        print()
        _print_kg_stats(kg.stats())
        return

    if action == "stats":
        _print_kg_stats(kg.stats())
        return

    if action == "promote":
        res = kg.promote_externals()
        print(f"Promoted {res['promoted']} paper_external node(s) to paper_local.")
        return

    if action == "neighbors":
        if args.paper_id:
            _show_paper_neighbors(db, args.paper_id, top=args.top)
        else:
            _show_term_neighbors(db, args.term, depth=args.depth, top=args.top)
        return

    if action == "search":
        from graph_retrieval import GraphRetriever
        gr = GraphRetriever(db)
        if args.explain:
            exp = gr.explain(args.query)
            print(f"normalized query: {exp['query_normalized']!r}")
            if not exp["anchors"]:
                print("no anchors found — graph won't return anything")
                return
            print(f"anchors ({len(exp['anchors'])}):")
            for a in exp["anchors"]:
                print(f"  [{a['node_type']}] {a['display_name']:<40} "
                      f"relevance={a['relevance']:.2f} "
                      f"papers={len(a['paper_ids'])}")
            print()
        hits = gr.search(args.query, k=args.top, depth=args.depth)
        if not hits:
            print("(no matches)")
        else:
            paper_repo = PaperRepo(db)
            print(f"Graph hits (top {len(hits)}):")
            for pid, score in hits:
                p = paper_repo.get_by_id(pid)
                title = (p.title if p else "?")[:80]
                print(f"  [{pid}] score={score:.3f}  {title}")
        if args.include_external:
            recs = gr.search_external_recommendations(args.query, k=args.top)
            print()
            if not recs:
                print("(no external recommendations)")
            else:
                print(f"External recommendations (top {len(recs)} most-cited by anchor matches):")
                for r in recs:
                    doi = f"  doi:{r['doi']}" if r["doi"] else ""
                    print(f"  cited {r['cite_count']}x  {r['title'][:70]}{doi}")
        return

    print("Use: kg {build|stats|neighbors|promote|search}", file=sys.stderr)
    sys.exit(2)


def _print_kg_stats(stats: dict) -> None:
    nodes = stats.get("nodes", {})
    edges = stats.get("edges", {})
    total_nodes = sum(nodes.values())
    total_edges = sum(edges.values())
    print("Knowledge Graph:")
    print(f"  Nodes ({total_nodes} total):")
    # Stable order; paper_local / paper_external first so the local-vs-external
    # split is visible at a glance.
    order = ["paper_local", "paper_external", "keyword", "research_field", "author"]
    for k in order:
        if k in nodes:
            print(f"    {k:<16} {nodes[k]}")
    for k, v in nodes.items():
        if k not in order:
            print(f"    {k:<16} {v}")
    print(f"  Edges ({total_edges} total):")
    for k, v in sorted(edges.items()):
        print(f"    {k:<16} {v}")


def _show_term_neighbors(db: DatabaseConnection, term: str, *, depth: int, top: int) -> None:
    from knowledge_graph import _normalize_keyword
    norm = _normalize_keyword(term)
    if not norm:
        print(f"Empty term.", file=sys.stderr)
        sys.exit(2)
    rows = db.conn.execute(
        "SELECT id, node_type, display_name FROM kg_nodes "
        "WHERE node_type IN ('keyword', 'research_field') AND name = ?",
        (norm,),
    ).fetchall()
    if not rows:
        print(f"No node found for '{term}'.")
        return
    for r in rows:
        _print_term_node_neighbors(db, r["id"], r["node_type"], r["display_name"],
                                    depth=depth, top=top)


def _print_term_node_neighbors(db: DatabaseConnection, node_id: int, node_type: str,
                                display: str, *, depth: int, top: int) -> None:
    edge_type = "has_keyword" if node_type == "keyword" else "in_field"

    paper_rows = db.conn.execute(f"""
        SELECT n.paper_id, n.display_name FROM kg_edges e
        JOIN kg_nodes n ON n.id = e.src_id
        WHERE e.dst_id = ? AND e.edge_type = '{edge_type}'
        ORDER BY n.paper_id
        LIMIT ?
    """, (node_id, top)).fetchall()
    total_papers = db.conn.execute(
        f"SELECT COUNT(*) AS n FROM kg_edges WHERE dst_id=? AND edge_type='{edge_type}'",
        (node_id,),
    ).fetchone()["n"]

    print(f"{node_type}: {display}  ({total_papers} paper(s))")
    print(f"  papers (top {min(top, total_papers)}):")
    for r in paper_rows:
        print(f"    [{r['paper_id']}] {r['display_name'][:80]}")

    if depth >= 1 and node_type == "keyword":
        co_rows = db.conn.execute("""
            SELECT n.display_name AS kw, COUNT(*) AS cnt
            FROM kg_edges e1
            JOIN kg_edges e2 ON e1.src_id = e2.src_id
            JOIN kg_nodes n ON n.id = e2.dst_id
            WHERE e1.dst_id = ? AND e1.edge_type='has_keyword'
              AND e2.edge_type='has_keyword' AND e2.dst_id != ?
            GROUP BY e2.dst_id
            ORDER BY cnt DESC, n.display_name
            LIMIT ?
        """, (node_id, node_id, top)).fetchall()
        if co_rows:
            print(f"  co-occurring keywords (top {len(co_rows)}):")
            for r in co_rows:
                print(f"    {r['kw']:<30} ({r['cnt']} co-occurrence(s))")

    if depth >= 1 and node_type == "research_field":
        kw_rows = db.conn.execute("""
            SELECT n.display_name AS kw, COUNT(*) AS cnt
            FROM kg_edges e1
            JOIN kg_edges e2 ON e1.src_id = e2.src_id
            JOIN kg_nodes n ON n.id = e2.dst_id
            WHERE e1.dst_id = ? AND e1.edge_type='in_field'
              AND e2.edge_type='has_keyword'
            GROUP BY e2.dst_id
            ORDER BY cnt DESC, n.display_name
            LIMIT ?
        """, (node_id, top)).fetchall()
        if kw_rows:
            print(f"  keywords in this field (top {len(kw_rows)}):")
            for r in kw_rows:
                print(f"    {r['kw']:<30} ({r['cnt']} paper(s))")


def _show_paper_neighbors(db: DatabaseConnection, paper_id: int, *, top: int) -> None:
    local = db.conn.execute(
        "SELECT id, display_name FROM kg_nodes "
        "WHERE node_type='paper_local' AND paper_id = ?",
        (paper_id,),
    ).fetchone()
    if not local:
        print(f"No paper_local node for paper {paper_id}. Run `kg build --paper-id {paper_id}`.")
        return
    print(f"paper_local [{paper_id}]: {local['display_name']}")

    def fetch_dst(edge_type: str, dst_type: Optional[str] = None) -> list:
        sql = ("SELECT n.display_name AS name, n.node_type AS t, n.paper_id AS pid "
               "FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst_id "
               "WHERE e.src_id=? AND e.edge_type=?")
        params = [local["id"], edge_type]
        if dst_type:
            sql += " AND n.node_type=?"
            params.append(dst_type)
        sql += f" ORDER BY n.display_name LIMIT ?"
        params.append(top)
        return db.conn.execute(sql, params).fetchall()

    kws = fetch_dst("has_keyword")
    if kws:
        print(f"  keywords: {', '.join(k['name'] for k in kws)}")
    fields = fetch_dst("in_field")
    if fields:
        print(f"  research_field: {', '.join(k['name'] for k in fields)}")

    crosses = fetch_dst("cross_cites")
    if crosses:
        print(f"  cross_cites → ({len(crosses)} local paper(s)):")
        for r in crosses:
            print(f"    [{r['pid']}] {r['name'][:80]}")
    cites = fetch_dst("cites")
    if cites:
        print(f"  cites → ({len(cites)} external paper(s)):")
        for r in cites:
            print(f"    (ext) {r['name'][:80]}")

    incoming = db.conn.execute(
        "SELECT n.paper_id AS pid, n.display_name AS name, e.edge_type "
        "FROM kg_edges e JOIN kg_nodes n ON n.id = e.src_id "
        "WHERE e.dst_id = ? AND e.edge_type='cross_cites' "
        "ORDER BY n.paper_id LIMIT ?",
        (local["id"], top),
    ).fetchall()
    if incoming:
        print(f"  cited by ({len(incoming)} local paper(s)):")
        for r in incoming:
            print(f"    [{r['pid']}] {r['name'][:80]}")
