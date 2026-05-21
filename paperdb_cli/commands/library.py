"""Library-management commands: list / show / search / delete / export / tag /
init-db / stats. Read-mostly; the heavy lifting lives in the repo layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from db_connection import DatabaseConnection
from schema import init_schema
from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo
from db.tag_repo import TagRepo
from db.lit_review_repo import LitReviewRepo

from paperdb_cli.serializers import (
    cit_to_dict, paper_to_dict, ref_to_dict, section_to_dict,
)


def cmd_list(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    tag_repo = TagRepo(db)

    if args.tag:
        paper_ids = tag_repo.get_papers_by_tag(args.tag)
        papers = [paper_repo.get_by_id(pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]
    else:
        papers = paper_repo.list_papers(limit=args.limit, offset=args.offset)

    if args.as_json:
        data = [paper_to_dict(p) for p in papers]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if not papers:
        print("No papers found.")
        return

    for p in papers:
        tags = tag_repo.get_tags_for_paper(p.id)
        tag_str = f" [{', '.join(t.name for t in tags)}]" if tags else ""
        year = ""
        if p.doi:
            year = f" DOI:{p.doi}"
        authors = ", ".join(p.authors[:3])
        if len(p.authors) > 3:
            authors += " et al."
        print(f"  [{p.id:>4}] {p.title[:70]}")
        print(f"         {authors}{year}{tag_str}")


def cmd_show(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    ref_repo = ReferenceRepo(db)
    tag_repo = TagRepo(db)

    paper = paper_repo.get_by_id(args.paper_id)
    if not paper:
        print(f"Paper not found: {args.paper_id}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        data = paper_to_dict(paper)
        if args.sections:
            secs = paper_repo.get_sections(paper.id)
            data["sections"] = [section_to_dict(s) for s in secs]
        if args.references:
            refs = ref_repo.get_references_for_paper(paper.id)
            data["references"] = [ref_to_dict(r) for r in refs]
        if args.citations:
            cits = paper_repo.get_citation_locations(paper.id)
            data["citation_locations"] = [cit_to_dict(c) for c in cits]
        if args.llm:
            data["llm"] = {
                "analyzed_at": paper.llm_analyzed_at,
                "summary": paper.llm_summary,
                "research_field": paper.llm_research_field,
                "methodology": paper.llm_methodology,
                "key_findings": paper.llm_key_findings or [],
            }
            lr_repo = LitReviewRepo(db)
            entries = lr_repo.get_by_paper(paper.id)
            data["lit_review_entries"] = [
                {
                    "cited_ref_id": e.cited_ref_id,
                    "viewpoint": e.viewpoint,
                    "category": e.category,
                    "context": e.context,
                }
                for e in entries
            ]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    tags = tag_repo.get_tags_for_paper(paper.id)
    print(f"ID:           {paper.id}")
    print(f"Title:        {paper.title}")
    print(f"Authors:      {', '.join(paper.authors)}")
    print(f"DOI:          {paper.doi or 'N/A'}")
    print(f"Pages:        {paper.total_pages}")
    print(f"Abstract:     {paper.abstract[:200]}{'...' if len(paper.abstract) > 200 else ''}")
    print(f"Keywords:     {', '.join(paper.keywords)}")
    if tags:
        print(f"Tags:         {', '.join(t.name for t in tags)}")
    print(f"PDF:          {paper.pdf_path}")
    print(f"Ingested:     {paper.ingested_at}")

    if args.sections:
        secs = paper_repo.get_sections(paper.id)
        print(f"\n--- Sections ({len(secs)}) ---")
        for s in secs:
            indent = "  " * s.level
            print(f"  {indent}[L{s.level}] {s.heading} (pp.{s.page_start}-{s.page_end}, {len(s.paragraphs)} para)")

    if args.references:
        refs = ref_repo.get_references_for_paper(paper.id)
        print(f"\n--- References ({len(refs)}) ---")
        for r in refs:
            num = f"[{r.ref_number}]" if r.ref_number else "[-]"
            doi = f" DOI:{r.identifiers.get('DOI', '')}" if r.identifiers.get("DOI") else ""
            print(f"  {num} {r.title[:70]}{doi}")

    if args.citations:
        cits = paper_repo.get_citation_locations(paper.id)
        print(f"\n--- Citation Locations ({len(cits)}) ---")
        for c in cits:
            print(f"  p.{c.page} ({c.x:.0f},{c.y:.0f}) {c.text}: {c.sentence[:80]}...")

    if args.llm:
        print("\n--- LLM Analysis ---")
        if not paper.llm_analyzed_at:
            print(f"  (not analyzed yet — run `paperdb analyze {paper.id}`)")
        else:
            print(f"  Analyzed at:   {paper.llm_analyzed_at}")
            print(f"  Research field: {paper.llm_research_field or '-'}")
            print(f"  Summary:       {paper.llm_summary or '-'}")
            print(f"  Methodology:   {paper.llm_methodology or '-'}")
            findings = paper.llm_key_findings or []
            if findings:
                print(f"  Key findings ({len(findings)}):")
                for f in findings:
                    print(f"    • {f}")

        lr_repo = LitReviewRepo(db)
        entries = lr_repo.get_by_paper(paper.id)
        print(f"\n--- Lit Review Entries ({len(entries)}) ---")
        for e in entries:
            ref = next((r for r in ref_repo.get_references_for_paper(paper.id) if r.id == e.cited_ref_id), None)
            ref_title = ref.title[:55] if ref and ref.title else f"ref#{e.cited_ref_id}"
            print(f"  [{e.category}] → {ref_title}")
            print(f"     {e.viewpoint}")


def cmd_search(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)

    papers = paper_repo.search_papers(args.query, limit=args.limit, field=args.field)

    if args.as_json:
        print(json.dumps([paper_to_dict(p) for p in papers], ensure_ascii=False, indent=2))
        return

    if not papers:
        print(f'No results for "{args.query}"')
        return

    print(f'Found {len(papers)} result(s) for "{args.query}":')
    for p in papers:
        authors = ", ".join(p.authors[:2])
        if len(p.authors) > 2:
            authors += " et al."
        print(f"  [{p.id:>4}] {p.title[:70]}")
        print(f"         {authors}")


def cmd_delete(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    paper = paper_repo.get_by_id(args.paper_id)
    if not paper:
        print(f"Paper not found: {args.paper_id}", file=sys.stderr)
        sys.exit(1)

    if not args.force:
        answer = input(f"Delete [{paper.id}] {paper.title[:60]}? [y/N] ")
        if answer.lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    paper_repo.delete_paper(args.paper_id)
    print(f"Deleted paper {args.paper_id}.")
    # Knowledge graph: cascade has already dropped the paper_local node;
    # this call sweeps now-orphaned keyword / research_field / paper_external nodes.
    try:
        from knowledge_graph import KGBuilder
        res = KGBuilder(db).remove_paper(args.paper_id)
        orph = res.get("orphans_removed") or {}
        if any(orph.values()):
            print(f"  KG: swept orphans {orph}")
    except Exception as e:
        print(f"  WARN: kg cleanup failed: {e}", file=sys.stderr)


def cmd_stats(args, db: DatabaseConnection) -> None:
    from db.shared_citations_repo import SharedCitationsRepo
    paper_repo = PaperRepo(db)
    stats = paper_repo.get_stats()
    emb_stats = paper_repo.get_embedding_stats()
    stats.update({f"emb_{k}": v for k, v in emb_stats.items()})
    sc_repo = SharedCitationsRepo(db)
    stats["shared_citation_rows"] = sc_repo.count()
    stats["shared_citation_pairs"] = sc_repo.count_pairs()

    from knowledge_graph import KGBuilder
    kg_stats = KGBuilder(db).stats()
    stats["kg_nodes"] = kg_stats["nodes"]
    stats["kg_edges"] = kg_stats["edges"]

    if args.as_json:
        print(json.dumps(stats, indent=2))
        return

    print("Database Statistics:")
    print(f"  Papers:             {stats['papers']}")
    print(f"  Sections:           {stats['sections']}")
    print(f"  References:         {stats['references']}")
    print(f"  Citation Locations: {stats['citation_locations']}")
    print(f"  Tags:               {stats['tags']}")
    print(f"  Embeddings:")
    print(f"    Title       : {emb_stats['papers_with_title_emb']}/{stats['papers']}")
    print(f"    Abstract    : {emb_stats['papers_with_abstract_emb']}/{stats['papers']}")
    print(f"    Fulltext    : {emb_stats['papers_with_fulltext_emb']}/{stats['papers']}")
    print(f"    Sections    : {emb_stats['sections_with_emb']}/{stats['sections']}")
    print(f"    Chunks      : {emb_stats['section_chunks_with_emb']}/{emb_stats['section_chunks_total']}")
    print(f"    References  : {emb_stats['references_with_emb']}/{stats['references']}")
    print(f"  Shared citations: {stats['shared_citation_rows']} rows across "
          f"{stats['shared_citation_pairs']} pair(s)")
    kg_nodes = stats.get("kg_nodes") or {}
    kg_edges = stats.get("kg_edges") or {}
    if sum(kg_nodes.values()) or sum(kg_edges.values()):
        print(f"  Knowledge graph:")
        print(f"    Paper nodes  : local={kg_nodes.get('paper_local', 0)}, "
              f"external={kg_nodes.get('paper_external', 0)}")
        print(f"    Keyword nodes: {kg_nodes.get('keyword', 0)}")
        print(f"    Field nodes  : {kg_nodes.get('research_field', 0)}")
        print(f"    Edges        : "
              f"has_keyword={kg_edges.get('has_keyword', 0)}, "
              f"in_field={kg_edges.get('in_field', 0)}, "
              f"cites={kg_edges.get('cites', 0)}, "
              f"cross_cites={kg_edges.get('cross_cites', 0)}")
    else:
        print(f"  Knowledge graph: empty  (run `kg build` to populate)")


def cmd_export(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    ref_repo = ReferenceRepo(db)
    tag_repo = TagRepo(db)

    if args.paper_id:
        paper = paper_repo.get_by_id(args.paper_id)
        if not paper:
            print(f"Paper not found: {args.paper_id}", file=sys.stderr)
            sys.exit(1)
        papers = [paper]
    elif args.tag:
        paper_ids = tag_repo.get_papers_by_tag(args.tag)
        papers = [paper_repo.get_by_id(pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]
    else:
        papers = paper_repo.list_papers(limit=10000)

    data = []
    for p in papers:
        d = paper_to_dict(p)
        d["sections"] = [section_to_dict(s) for s in paper_repo.get_sections(p.id)]
        d["references"] = [ref_to_dict(r) for r in ref_repo.get_references_for_paper(p.id)]
        d["tags"] = [t.name for t in tag_repo.get_tags_for_paper(p.id)]
        data.append(d)

    json_str = json.dumps(data, ensure_ascii=False, indent=None if args.compact else 2)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Exported {len(data)} paper(s) to {args.output}")
    else:
        print(json_str)


def cmd_tag(args, db: DatabaseConnection) -> None:
    tag_repo = TagRepo(db)

    if args.tag_action == "add":
        tag_repo.tag_paper(args.paper_id, args.tag_name)
        print(f"Tagged paper {args.paper_id} with '{args.tag_name}'")
    elif args.tag_action == "remove":
        tag_repo.untag_paper(args.paper_id, args.tag_name)
        print(f"Removed tag '{args.tag_name}' from paper {args.paper_id}")
    elif args.tag_action == "list":
        tags = tag_repo.list_tags()
        if not tags:
            print("No tags.")
            return
        for t in tags:
            paper_ids = tag_repo.get_papers_by_tag(t.name)
            print(f"  {t.name} ({len(paper_ids)} papers)")
    else:
        print("Usage: paperdb tag {add|remove|list}", file=sys.stderr)


def cmd_init_db(args, db: DatabaseConnection) -> None:
    init_schema(db)
    print(f"Database initialized at {db.db_path}")
