"""Text extraction utilities ported from src/modules/utils.ts."""

import re
from datetime import datetime
from typing import Optional

# Regex patterns (from utils.ts L10-14)
DOI_REGEX = re.compile(r"10\.\d{4,9}/[-\._;\(\)\/:A-z0-9><]+[^\.\]]")
ARXIV_REGEX = re.compile(r"arXiv[\.:](\d+\.\d+)")
URL_REGEX = re.compile(r"https?://[^\s]+")


def is_chinese(text: str) -> bool:
    """Check if text is predominantly Chinese (from utils.ts L315-318)."""
    text = re.sub(r"\s+", "", text)
    chinese_chars = len(re.findall(r"[一-龥]", text))
    return chinese_chars / max(len(text), 1) > 0.5


def is_doi(text: str) -> bool:
    """Check if text is a pure DOI (from utils.ts L320-328)."""
    if not text:
        return False
    match = DOI_REGEX.search(text)
    if match and match.group() == text:
        return not re.search(r"(cnki|issn)", text, re.IGNORECASE)
    return False


def get_identifiers(text: str) -> dict[str, str]:
    """Extract DOI and arXiv identifiers from text (from utils.ts L20-43)."""
    identifiers: dict[str, str] = {}
    # DOI
    cleaned = re.sub(r"\s+", "", text)
    doi_match = DOI_REGEX.search(cleaned)
    if doi_match:
        identifiers["DOI"] = doi_match.group()
    # arXiv
    arxiv_match = ARXIV_REGEX.search(cleaned)
    if arxiv_match:
        identifiers["arXiv"] = arxiv_match.group(1)
    return identifiers


def extract_url(text: str) -> Optional[str]:
    """Extract URL from text (from utils.ts L45-49)."""
    match = URL_REGEX.search(text)
    if match:
        return match.group()
    return None


def identifiers_to_url(identifiers: dict[str, str]) -> Optional[str]:
    """Convert identifiers to URL (from utils.ts L159-168)."""
    if "DOI" in identifiers:
        return f"https://doi.org/{identifiers['DOI']}"
    if "arXiv" in identifiers:
        return f"https://arxiv.org/abs/{identifiers['arXiv']}"
    return None


def parse_ref_text(text: str) -> dict:
    """
    Parse a reference text string into structured info.
    Ported from utils.ts L52-107 (parseRefText).
    """
    try:
        text = re.sub(r"^\[\d+?\]", "", text)
        text = re.sub(r"\s+", " ", text)

        # Match title in Chinese quotes “...”
        title: str = ""
        title_match: str = ""
        quoted = re.search(r"“(.+)”", text)
        if quoted:
            title_match = quoted.group(0)
            title = quoted.group(1)
            if title.endswith(","):
                title = title[:-1]
        else:
            # Split by period, find longest segments
            parts = text.split(". ")
            if len(parts) < 2:
                parts = text.split(".")

            scored: list[tuple[float, str]] = []
            for s in parts:
                count = 0
                for pat in [r"[A-Z]\.", r"[,.\-\(\)\:]", r"\d"]:
                    matches = re.findall(pat, s)
                    count += len(matches)
                scored.append((count / max(len(s), 1), s))

            # Filter segments with enough spaces (>=3 words)
            scored = [(sc, s) for sc, s in scored if len(re.findall(r"\s+", s)) >= 3]
            if not scored:
                return {"title": text}
            scored.sort(key=lambda x: x[0])
            title = scored[0][1].strip()
            # Find the match string in original text
            if title in text:
                title_match = title
            else:
                return {"title": text}

            if re.search(r"\[[A-Z]\]$", title):
                title = re.sub(r"\[[A-Z]\]$", "", title)

        title = title.strip()
        split_by_title = text.split(title_match)
        author_info = split_by_title[0].strip()

        # Parse publication venue from the part after title
        venue_part = split_by_title[1] if len(split_by_title) > 1 else ""
        venue_match = re.search(r"[^.\s].+[^\.]", venue_part)
        publication_venue = ""
        if venue_match:
            publication_venue = re.split(r"[,\d]", venue_match.group())[0].strip()

        # Handle "et al."
        if "et al." in author_info:
            author_info = author_info.split("et al.")[0] + "et al."

        # Extract year
        current_year = datetime.now().year
        year = None
        year_matches = re.findall(r"[^\d]\d{4}[^\d-]", text)
        for ym in year_matches:
            y = re.search(r"\d+", ym)
            if y and int(y.group()) <= current_year + 1:
                year = y.group()
                break

        # Clean author_info: remove year in various formats
        if year:
            for pattern in [f"({year})", f"{year}.", year]:
                author_info = author_info.replace(pattern, "").strip()
            # Remove leftover empty parens and trailing dots
            author_info = re.sub(r"\(\s*\)", "", author_info).strip()
            author_info = re.sub(r"\s+\.$", "", author_info).strip()

        return {
            "year": year,
            "title": title,
            "authors": [author_info] if author_info else [],
            "publication_venue": publication_venue,
        }
    except Exception:
        return {"title": text}


def ref_text_to_info(text: str) -> dict:
    """
    Convert reference text to ItemBaseInfo.
    Ported from utils.ts L170-178 (refText2Info).
    """
    identifiers = get_identifiers(text)
    info = {
        "identifiers": identifiers,
        "url": extract_url(text) or identifiers_to_url(identifiers),
        "authors": [],
        "type": "preprint" if "arXiv" in identifiers else "journalArticle",
    }
    parsed = parse_ref_text(text)
    info.update(parsed)
    return info
