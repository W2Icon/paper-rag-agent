"""
Metadata extractor — extract title, authors, affiliations, abstract, keywords, DOI
from page 1 of an academic PDF using PyMuPDF's native block structure.
"""

import re
from dataclasses import dataclass
from typing import Optional

import fitz

from utils import DOI_REGEX

_INSTITUTION_KW = re.compile(
    r"(?:University|Institute|College|School|Department|Laboratory|"
    r"Center|Centre|Academy|Corporation|Corp|Ltd|Inc|LLC|GmbH|"
    r"SA|S\.A\.|BV|B\.V\.|AG|S\.L\.|SARL|Co\.|Ltd\.|PLC|"
    r"大学|学院|研究院|研究所|实验室|中心|科学院|公司|有限公司)",
    re.IGNORECASE,
)


@dataclass
class PaperMeta:
    title: str
    authors: list[str]
    affiliations: list[str]
    abstract: str
    keywords: list[str]
    doi: Optional[str]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "authors": self.authors,
            "affiliations": self.affiliations,
            "abstract": self.abstract,
            "keywords": self.keywords,
            "doi": self.doi,
        }


def _extract_text_blocks(page: fitz.Page) -> list[dict]:
    """Extract text blocks from page with aggregated font info."""
    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        spans = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                spans.append(span)
        if not spans:
            continue
        bbox = block["bbox"]
        text = " ".join(s["text"] for s in spans).strip()
        max_size = max(s["size"] for s in spans)
        blocks.append({
            "text": text,
            "max_size": max_size,
            "y0": bbox[1],
            "y1": bbox[3],
            "x0": bbox[0],
            "x1": bbox[2],
            "word_count": len(text.split()),
        })
    return blocks


# Journal/publisher prefixes that PyMuPDF sometimes merges into the title block.
# Anchored at the start. The DOI-URL pattern is greedy across whitespace so it
# eats both bare DOIs ("10.xxxx/yyy") and full URLs ("https://doi.org/...").
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"Article|Letter|Research(?:\s+Article)?|Review|Editorial|Perspective|"
    r"Brief\s+Communication|Comment|News\s+(?:and|&)\s+Views|"
    r"OPEN\s+ACCESS|Original\s+(?:Research|Article)|Communication"
    r")"
    r"(?:\s+(?:https?://\S+|doi\.org/\S+|10\.\d{4,9}/\S+))?"
    r"\s+",
    re.IGNORECASE,
)


def _strip_title_prefix(text: str) -> str:
    """Remove journal masthead artifacts that bled into the same PyMuPDF block
    as the title (e.g. 'Article https://doi.org/... Deep learning predicts ...')."""
    cleaned = _TITLE_PREFIX_RE.sub("", text).strip()
    # Also strip a bare leading "Article" with no DOI but with significant
    # remaining text (e.g. "Article Speed Change Pattern Optimization for ...")
    if cleaned == text:
        cleaned = re.sub(r"^(?:Article|Letter|Review)\s+(?=[A-Z])", "", text).strip()
    return cleaned or text


def extract_title(blocks: list[dict], page_height: float) -> tuple[str, float]:
    """Extract title and return (title, title_y1_bottom).

    Returns the post-strip/post-merge title text along with the bottom-y of
    the merged sibling block range. The y1 is needed by downstream authors
    extraction; returning it here avoids a fragile re-lookup by text equality
    (which would fail after we strip masthead prefixes or merge siblings).

    Handles three real-world wrinkles:
      1. Multi-line titles split into separate same-font blocks by PyMuPDF →
         after picking the top candidate, merge ALL same-font blocks in the
         title's vertical neighborhood (within ~3 line heights).
      2. Journal masthead text ('Article', 'Letter', DOI URL) accidentally
         merged INTO the title block by PyMuPDF → strip with regex prefix.
      3. Title that's just barely inside the body of the page (y_rel slightly
         under 0.07) → fall through to the existing y_rel > 0.07 filter.
    """
    # Pool A: primary-candidate pool. Require enough words to rule out
    # journal banners ("Applied Energy", "Nature Communications").
    primary_pool = []
    for b in blocks:
        wc = b["word_count"]
        y_rel = b["y0"] / page_height
        if y_rel < 0.07 or y_rel > 0.35:
            continue
        if wc < 4:
            continue
        primary_pool.append(b)

    if not primary_pool:
        # Relax: any block with >=5 words in upper 50%
        for b in blocks:
            if b["word_count"] >= 5 and b["y0"] / page_height < 0.5:
                primary_pool.append(b)

    if not primary_pool:
        return "", page_height * 0.25

    # Pick the block with the largest font (ties → most words).
    primary_pool.sort(key=lambda b: (b["max_size"], b["word_count"]), reverse=True)
    primary = primary_pool[0]
    primary_size = primary["max_size"]

    # Pool B: sibling-merge pool. Includes short fragments (e.g. "for X",
    # "Traffic and Road Information") that share the title font. Same y-band
    # filter, but no word-count floor.
    siblings = []
    for b in blocks:
        wc = b["word_count"]
        y_rel = b["y0"] / page_height
        if y_rel < 0.07 or y_rel > 0.40:  # slight extension for tail fragments
            continue
        if wc < 1:
            continue
        if abs(b["max_size"] - primary_size) < 0.5:
            siblings.append(b)

    if len(siblings) == 1:
        return _strip_title_prefix(primary["text"]), primary["y1"]

    # Sort by reading order (top-to-bottom), then concatenate the contiguous
    # vertical strip that includes the primary block.
    siblings.sort(key=lambda b: b["y0"])
    # Identify the contiguous run that contains primary. Allow gaps up to
    # ~1.6 × the font size (i.e. one blank line max) between consecutive blocks.
    max_gap = primary_size * 1.6
    primary_idx = siblings.index(primary)

    start = primary_idx
    while start > 0 and siblings[start]["y0"] - siblings[start - 1]["y1"] < max_gap:
        start -= 1
    end = primary_idx
    while end < len(siblings) - 1 and siblings[end + 1]["y0"] - siblings[end]["y1"] < max_gap:
        end += 1

    run = siblings[start:end + 1]
    parts = [b["text"].strip() for b in run if b["text"].strip()]
    merged = " ".join(parts)
    y1 = max(b["y1"] for b in run)
    return _strip_title_prefix(merged), y1


# Labels that PyMuPDF sometimes glues onto the front of the author block
# ("Graphical abstract Authors Xinmei Yuan, ...", "Authors: Foo, Bar")
_AUTHORS_LABEL_RE = re.compile(
    r"^\s*(?:"
    r"Graphical\s+abstract\s+(?:Authors?\s*[:：]?\s*)?|"
    r"Authors?\s*[:：]\s*|"
    r"By\s*[:：]\s*"
    r")",
    re.IGNORECASE,
)

# Trailing affiliation / footnote markers attached to a name. Applied
# iteratively so multi-marker tails get stripped one chunk at a time.
# Examples handled:
#   "DONG XIE 1"            → "DONG XIE"
#   "Irfan Ullah 1,2"       → "Irfan Ullah"
#   "Yiyuan Fang   1, *"    → "Yiyuan Fang"
#   "John Doe †"            → "John Doe"
#   "Yifan Ma a"            → "Yifan Ma"  (letter-style superscript)
# Requires a leading \s so we never eat into a single-letter trailing piece
# of a real name (e.g. "Su" → "S" would be wrong).
_AFFIL_MARKER_RE = re.compile(
    r"\s+(?:[0-9,]+|[a-z]|[\*\†\‡\§\¶]+)\s*$"
)

# Block text patterns that should NEVER be merged into the author line.
# When the merge loop encounters one of these, it stops merging.
_NON_AUTHOR_BLOCK_RE = re.compile(
    r"^\s*(?:Received|Accepted|Published|Available\s+online|"
    r"Correspondence|Check\s+for\s+updates|DOI|Editor|"
    r"https?://|\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)

# Tokens that mean "not a real name" — IEEE / ACM-style titles that often
# follow a comma after the actual name.
_NON_NAME_TOKENS = {
    "member", "student member", "senior member", "fellow", "ieee",
    "life member", "life fellow", "associate member", "graduate student member",
    "and",  # "...and Smith" lingering token from "by X and Y" patterns
}

# First-token blacklist — if the parsed author begins with any of these, the
# extractor latched onto the wrong block (usually the abstract or a header).
_BLACKLIST_FIRST_TOKEN = {
    "abstract", "abstract:", "keywords", "keywords:",
    "index", "received", "accepted", "published",
    "introduction", "article", "letter",
}


def _split_authors(merged: str) -> list[str]:
    """Split the author-line text into raw name candidates.

    Three-pass split:
      1. Pipe `|` if present (Paper 5 style "A 1,2 | B 3 | ...") — pipes take
         precedence because the inline "1,2" affiliation marker would foul a
         comma split.
      2. Comma otherwise.
      3. Within each piece, also split on `" and "` / `" & "` so e.g.
         "Foo 2 and Bar Baz" yields ["Foo 2", "Bar Baz"] (Paper 6 case).
    """
    if "|" in merged:
        pieces = [p.strip() for p in merged.split("|")]
    else:
        pieces = [p.strip() for p in merged.split(",")]
    final: list[str] = []
    for p in pieces:
        for sub in re.split(r"\s+(?:and|AND|&)\s+", p):
            sub = sub.strip()
            if sub:
                final.append(sub)
    return final


def _clean_name(name: str) -> str:
    """Strip trailing affiliation markers and whitespace fluff from one name.

    Iterative: a name like "John Doe 1, 2 *" has three marker chunks (" 1",
    " 2", " *") that need to be peeled off one at a time.
    """
    name = name.strip()
    # Remove ALL-CAPS "AND" connector at the start ("AND JINTAO DENG 2" → "JINTAO DENG 2")
    name = re.sub(r"^(?:and|AND|&)\s+", "", name)
    # Iteratively strip trailing affiliation markers
    for _ in range(5):  # bounded — names don't have 5+ trailing markers
        new_name = _AFFIL_MARKER_RE.sub("", name)
        if new_name == name:
            break
        name = new_name
    # Collapse internal multi-space (PyMuPDF leaves "Yiyuan Fang   1" with 3 spaces)
    name = re.sub(r"\s{2,}", " ", name)
    return name.strip()


def _is_valid_name(name: str) -> bool:
    """Reject obvious non-names: empty, pure digits, IEEE-style title tokens,
    or blacklisted first tokens like 'Abstract'."""
    if len(name) < 2:
        return False
    if re.match(r"^[\d\s\*\.,]+$", name):
        return False
    if name.lower() in _NON_NAME_TOKENS:
        return False
    first = name.split()[0].lower().rstrip(":")
    if first in _BLACKLIST_FIRST_TOKEN:
        return False
    return True


def extract_authors(blocks: list[dict], title_y1: float) -> list[str]:
    """Extract authors: blocks directly below title with person-name patterns.

    Pipeline:
      1. Pick blocks in the y-window directly below the title.
      2. Merge adjacent-y blocks.
      3. Strip leading label prefixes ("Authors:", "Graphical abstract Authors ...").
      4. Split on `|` or `,` (whichever the document actually uses).
      5. For each candidate: strip trailing affiliation markers, filter
         IEEE-title noise and blacklisted first tokens.
      6. If the first surviving candidate's first token is blacklisted
         (e.g. "Abstract:"), bail out with [] — we latched onto the wrong block.
    """
    author_blocks = [b for b in blocks
                     if b["y0"] > title_y1 + 2
                     and b["y0"] < title_y1 + 60
                     and b["max_size"] >= 8]

    if not author_blocks:
        return []

    author_blocks.sort(key=lambda b: b["y0"])

    # Merge blocks that are close in y. Stop merging when we hit a block whose
    # text clearly belongs to a different section (date headers, DOI lines,
    # "Correspondence" labels) — those bleed into the merge otherwise.
    merged_text = ""
    last_y1 = author_blocks[0]["y0"]
    for b in author_blocks:
        if b["y0"] - last_y1 > 20:
            break
        if _NON_AUTHOR_BLOCK_RE.match(b["text"]):
            break
        merged_text += " " + b["text"]
        last_y1 = max(last_y1, b["y1"])

    merged_text = merged_text.strip()

    # Strip leading label prefix ("Graphical abstract Authors", "Authors:", "By:")
    merged_text = _AUTHORS_LABEL_RE.sub("", merged_text)

    # Bail early if we clearly latched onto the wrong block (Abstract / Keywords)
    first_token = merged_text.split()[0].lower().rstrip(":") if merged_text else ""
    if first_token in _BLACKLIST_FIRST_TOKEN:
        return []

    raw = _split_authors(merged_text)
    authors: list[str] = []
    for name in raw:
        name = _clean_name(name)
        if _is_valid_name(name):
            authors.append(name)
    return authors


def extract_affiliations(blocks: list[dict], authors_y1: float) -> list[str]:
    """Extract affiliations below authors, containing institution keywords."""
    aff_blocks = [b for b in blocks
                  if b["y0"] > authors_y1 + 2
                  and b["y0"] < authors_y1 + 120
                  and _INSTITUTION_KW.search(b["text"])]

    affiliations = []
    for b in sorted(aff_blocks, key=lambda b: b["y0"]):
        text = b["text"]
        # Split by superscript markers: "a  Text1 b  Text2 c  Text3"
        parts = re.split(r"\s{2,}(?=[a-z]\s{2,})", text)
        if len(parts) > 1:
            for part in parts:
                part = re.sub(r"^[a-z0-9,\*\s]+", "", part).strip()
                if len(part) > 10:
                    affiliations.append(part)
        else:
            text = re.sub(r"^[a-z0-9,\*\s]+", "", text).strip()
            if len(text) > 10:
                affiliations.append(text)
    return affiliations


def extract_abstract(page: fitz.Page, blocks: list[dict]) -> str:
    """Extract abstract: find 'Abstract' label (often spaced out), collect text until next heading."""
    text = page.get_text()

    # "A B S T R A C T" may appear twice on page 1 (left column article info + right column actual abstract).
    # The actual abstract text follows the SECOND occurrence.
    pattern = r'A\s?B\s?S\s?T\s?R\s?A\s?C\s?T\s*\n+'
    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    if not matches:
        return ""

    # Use the LAST occurrence (the actual abstract, not the article-meta block)
    raw_match = matches[-1]
    abs_start = raw_match.end()

    # Find end: next section heading, OR a journal/footer line that appears
    # directly under the abstract without a blank-line gap. Each new pattern
    # below anchors on a single \n so it fires even when no paragraph break
    # separates abstract from what follows — a layout used by Elsevier
    # (©-license line on the next line) and IEEE (INDEX TERMS, I. INTRODUCTION
    # both immediately under the abstract paragraph).
    end_match = re.search(
        r'\n\s*\n(?:A\s?R\s?T\s?I\s?C\s?L\s?E|G\s?R\s?A\s?P\s?H\s?I\s?C\s?A\s?L|'
        r'(?:1|2|3|I|II|III)\.?\s+(?:Introduction|Method|Related|Background|System|Experiment|'
        r'[A-Z][a-z]+)|'
        r'\n(?:[A-Z]\s){2,})'
        r'|\n\s*[✩✦✪∗\*†‡§¶]'                  # footnote dingbat at line start
        r'|\n\s*E-?mail\s+address(?:es)?\s*[:：]'
        r'|\n\s*https?://(?:dx\.)?doi\.org/'
        r'|\n\s*Received\s+\d'                   # "Received 15 December 2023"
        r'|\n\s*©\s*\d{4}'                       # "© 2017 The Authors..." — Elsevier/IEEE copyright
        r'|\n\s*INDEX\s+TERMS\b'                 # IEEE keyword block
        r'|\n\s*KEYWORDS\s*[:：]'                # ACM/Springer KEYWORDS label
        # Numbered section heading without requiring a blank line above.
        # Roman numerals up to X cover IEEE papers ("I. INTRODUCTION", "VII. CONCLUSION").
        r'|\n\s*(?:I|II|III|IV|V|VI|VII|VIII|IX|X)\.\s+[A-Z]'
        # Arabic-numbered intro/method-class headings ("1. Introduction").
        r'|\n\s*\d+\.\s+(?:Introduction|Method|Methods|Background|Related\s+Work|'
        r'Preliminaries|Overview|Literature\s+Review|Problem\s+Statement|Motivation)\b',
        text[abs_start:], re.IGNORECASE
    )
    abs_end = abs_start + end_match.start() if end_match else min(abs_start + 5000, len(text))
    abs_text = text[abs_start:abs_end].strip()

    # Remove stray fragments from multi-column extraction
    abs_text = re.sub(r'\n[A-Z][a-z]+(?:\s+[a-z]+){0,3}\s*\n(?=[a-z])', '\n', abs_text)
    # Remove "Dataset link:" fragments
    abs_text = re.sub(r'\n(?:Dataset link:.*?)(?=\n)', '', abs_text)
    abs_text = re.sub(r'\n[Bb]/.*?(?=\n)', '', abs_text)
    # Normalize: single newlines become spaces, double newlines become paragraph breaks
    abs_text = re.sub(r'\n{3,}', '\n\n', abs_text)
    abs_text = re.sub(r'\n([^\n])', r' \1', abs_text)
    abs_text = re.sub(r' {2,}', ' ', abs_text)

    return abs_text.strip()


def extract_keywords(page: fitz.Page) -> list[str]:
    """Extract keywords after 'Keywords:' / 'Index Terms:' / '关键词' label."""
    text = page.get_text()
    m = re.search(r'\n(?:Keywords?|Index Terms)[\s:]*\n?(.*?)(?:\n(?:[A-Z]\s){2,}|\n\s*\n|\n(?:Introduction|1\.\s))',
                  text, re.DOTALL | re.IGNORECASE)
    if not m:
        return []

    kw_text = m.group(1).strip()
    keywords = []
    for line in kw_text.split("\n"):
        line = line.strip().rstrip(";,")
        if not line or len(line) < 3:
            continue
        for part in re.split(r"[;,]", line):
            part = part.strip().rstrip(".")
            if part and len(part) > 1:
                keywords.append(part)
    return keywords


def extract_doi(text: str) -> Optional[str]:
    """Extract DOI from page text."""
    m = DOI_REGEX.search(text)
    if m:
        return m.group().rstrip(".")
    return None


def extract_metadata(doc: fitz.Document) -> PaperMeta:
    """
    Extract all metadata from the first page of a PDF.
    """
    page = doc[0]
    page_height = page.rect.height
    page_text = page.get_text()
    blocks = _extract_text_blocks(page)

    title, title_y1 = extract_title(blocks, page_height)

    authors = extract_authors(blocks, title_y1)
    authors_y1 = title_y1 + 20
    if authors:
        # Find the author block by looking for blocks below title with person-name font size
        for b in blocks:
            if b["y0"] > title_y1 and b["max_size"] >= 9 and b["max_size"] <= 12:
                # This is likely the author block
                authors_y1 = max(authors_y1, b["y1"])

    affiliations = extract_affiliations(blocks, authors_y1)
    abstract = extract_abstract(page, blocks)
    keywords = extract_keywords(page)
    doi = extract_doi(page_text)

    return PaperMeta(
        title=title,
        authors=authors,
        affiliations=affiliations,
        abstract=abstract,
        keywords=keywords,
        doi=doi,
    )
