"""`paperdb classify-sections` — rule-based backfill for sections.section_type."""

from __future__ import annotations

from db_connection import DatabaseConnection


def cmd_classify_sections(args, db: DatabaseConnection) -> None:
    """Rule-based backfill for sections.section_type. No LLM involved."""
    from section_classifier import classify_section_type, SECTION_TYPES

    # Default mode is --missing if no scope flag given
    if not args.paper_id and not args.all_papers and not args.missing:
        args.missing = True

    where_clauses = []
    params: list = []
    if args.paper_id is not None:
        where_clauses.append("paper_id = ?")
        params.append(args.paper_id)
    if args.missing and not args.force:
        where_clauses.append("section_type IS NULL")
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    rows = db.conn.execute(
        f"SELECT id, heading FROM sections{where_sql} ORDER BY paper_id, sort_order",
        params,
    ).fetchall()

    if not rows:
        print("No sections to classify.")
        return

    counts: dict[str, int] = {t: 0 for t in SECTION_TYPES}
    other_headings: list[str] = []
    updates: list[tuple[str, int]] = []
    for r in rows:
        st = classify_section_type(r["heading"] or "")
        counts[st] += 1
        if st == "other":
            other_headings.append(r["heading"] or "")
        updates.append((st, r["id"]))

    total = len(updates)
    matched = total - counts["other"]
    coverage_pct = (matched / total * 100) if total else 0.0

    print(f"Scanned {total} section(s). Coverage: {matched}/{total} = {coverage_pct:.1f}%")
    print("Breakdown:")
    for t in SECTION_TYPES:
        n = counts[t]
        if n:
            print(f"  {t:<16} {n}")

    if args.show_other and other_headings:
        print(f"\nHeadings classified as 'other' ({len(other_headings)}):")
        for h in other_headings[:50]:
            print(f"  {h!r}")
        if len(other_headings) > 50:
            print(f"  ... and {len(other_headings) - 50} more")

    if args.dry_run:
        print("\n[dry-run] no changes written")
        return

    with db.transaction() as cur:
        cur.executemany(
            "UPDATE sections SET section_type = ? WHERE id = ?",
            updates,
        )
    print(f"\nWrote section_type for {total} section(s).")
