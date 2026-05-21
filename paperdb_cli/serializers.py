"""Plain-dict conversions used by `paperdb list / show / export / search`.

Kept tiny on purpose — these are the wire formats for `--json` output, so
adding fields here is an API change for any downstream tooling consuming
the JSON.
"""

from __future__ import annotations


def paper_to_dict(p) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "authors": p.authors,
        "affiliations": p.affiliations,
        "abstract": p.abstract,
        "keywords": p.keywords,
        "doi": p.doi,
        "total_pages": p.total_pages,
        "pdf_path": p.pdf_path,
        "ingested_at": p.ingested_at,
        "publication_year": getattr(p, "publication_year", None),
    }


def section_to_dict(s) -> dict:
    return {
        "heading": s.heading,
        "level": s.level,
        "paragraphs": s.paragraphs,
        "page_start": s.page_start,
        "page_end": s.page_end,
    }


def ref_to_dict(r) -> dict:
    return {
        "ref_number": r.ref_number,
        "title": r.title,
        "authors": r.authors,
        "year": r.year,
        "identifiers": r.identifiers,
        "url": r.url,
        "type": r.type,
    }


def cit_to_dict(c) -> dict:
    return {
        "page": c.page,
        "x": c.x,
        "y": c.y,
        "text": c.text,
        "sentence": c.sentence,
    }
