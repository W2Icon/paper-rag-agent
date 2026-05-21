# PDF Reference Extractor — AI Agent Usage Guide

## Overview

Extracts the **reference list** and **in-text citation locations** from academic PDF papers. Full-paper mode additionally extracts **metadata** (title, authors, affiliations, abstract, keywords, DOI) and **structured sections with paragraphs**.

**Capabilities:**
- Locate the reference/bibliography section (detects "References", "Bibliography", "参考文献")
- Split multi-column reference lists into individual entries
- Parse each entry: title, authors, year, DOI, arXiv ID, URL
- Locate in-text citation positions: page, x/y coordinates, and the containing sentence
- Support 6 citation formats: `[1]`, `(1)`, `［1］`, `¹²`, `(Author, Year)`, `Author (Year)`
- Handle compound citations: `[5,6]`, `[3-5]`, `[16,18,21]`
- Full-paper mode: extract metadata and structured sections with paragraphs
- Handle both English and Chinese papers

**Limitations:**
- Requires the PDF to have selectable text (not scanned images)
- Best results on papers with 3+ pages
- Metadata extraction accuracy ~75-85% (heuristic-based)
- Section detection accuracy ~80% (font/height-based)

---

## Python API

### Installation

```bash
pip install pymupdf
```

### Mode 1: Reference Extraction (original)

```python
from extractor import PDFReferenceExtractor

extractor = PDFReferenceExtractor()
result = extractor.extract("paper.pdf")

for ref in result.references:
    print(f"[{ref.ref_number}] {ref.title}")
    for c in ref.citations:
        print(f"  Cited on page {c.page}: ...{c.sentence}")
```

### Mode 2: Full Paper Extraction

```python
from paper_extractor import PaperExtractor

extractor = PaperExtractor()
result = extractor.extract("paper.pdf")

print(f"Title: {result.meta.title}")
print(f"Authors: {result.meta.authors}")
print(f"DOI: {result.meta.doi}")

for section in result.sections:
    print(f"\n[{section.level}] {section.heading}")
    for p in section.paragraphs[:2]:
        print(f"  {p[:120]}...")
```

### Input

| API | Parameter | Type | Required | Description |
|-----|-----------|------|----------|-------------|
| Both | `source` | `str \| Path \| bytes` | Yes | PDF file path or raw bytes |
| Both | `from_page` | `int` | No | 1-indexed page to start reference scanning from. `0` = auto |
| Both | `verbose` | `bool` | No | Print progress to stderr |

---

### Output — `PDFReferenceExtractor` → `ExtractResult`

```python
ExtractResult(
    references: list[Reference],
    total_pages: int,
    source: str,           # always "pdf"
)
```

### Output — `PaperExtractor` → `PaperExtractResult`

```python
PaperExtractResult(
    meta: PaperMeta,
    sections: list[Section],
    references: list[Reference],
    total_pages: int,
)
```

### Output — `PaperMeta`

| Field | Type | Description |
|-------|------|-------------|
| `title` | `str` | Paper title |
| `authors` | `list[str]` | Author names |
| `affiliations` | `list[str]` | Institution affiliations |
| `abstract` | `str` | Abstract text |
| `keywords` | `list[str]` | Keywords |
| `doi` | `str \| None` | DOI identifier |

### Output — `Section`

| Field | Type | Description |
|-------|------|-------------|
| `heading` | `str` | Section heading text |
| `level` | `int` | Heading level (1, 2, or 3) |
| `paragraphs` | `list[str]` | Paragraph text |
| `page_start` | `int` | First page of section |
| `page_end` | `int` | Last page of section |

### Output — `Reference`

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `text` | `str` | Cleaned reference text | `"Zhou P, Wang M. Carbon dioxide..."` |
| `raw_text` | `str` | Original text before prefix stripping | `"[1] Zhou P, Wang M..."` |
| `title` | `str` | Parsed paper title | `"Carbon dioxide emissions allocation: A review"` |
| `authors` | `list[str]` | Author strings | `["Zhou P, Wang M."]` |
| `year` | `str \| None` | Publication year | `"2016"` |
| `identifiers` | `dict[str,str]` | DOI / arXiv IDs | `{"DOI": "10.1234/abcd.5678"}` |
| `url` | `str \| None` | URL from PDF hyperlink or text | `"https://doi.org/..."` |
| `type` | `str` | `"journalArticle"` or `"preprint"` | `"journalArticle"` |
| `position` | `dict` | Reference entry position in PDF | `{"x": 56.6, "y": 188.1}` |
| `ref_number` | `int \| None` | Reference number in bibliography | `1` |
| `citations` | `list[Citation]` | In-text citation occurrences | (see below) |

### Output — `Citation` (within Reference.citations)

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `page` | `int` | Page number (1-indexed) | `2` |
| `x` | `float` | X coordinate in page | `275.2` |
| `y` | `float` | Y coordinate in page | `295.5` |
| `text` | `str` | Matched citation text | `"[1]"` |
| `sentence` | `str` | The full sentence containing this citation | `"Electric vehicles... transportation in the future [1]."` |

---

## CLI Usage

```bash
# Reference-only: extract and save to output/<pdf_name>.json
python main.py paper.pdf

# Full paper: metadata + sections + references + citations
python main.py paper.pdf --full-paper -v

# Custom output path
python main.py paper.pdf -o results.json

# Scan references from a specific page
python main.py thesis.pdf --from-page 120 -v

# Compact JSON
python main.py paper.pdf --compact
```

### CLI Output Schema (--full-paper)

```json
{
  "meta": {
    "title": "Physically rational data augmentation for energy consumption estimation of electric vehicles",
    "authors": ["Yifan Ma", "Wei Sun", "Zhoulun Zhao"],
    "affiliations": ["State Key Laboratory of Automotive Simulation and Control, Jilin University"],
    "abstract": "With the surge of electric vehicles, accurate estimation of their energy consumption...",
    "keywords": ["Energy consumption", "Data augmentation", "Electric vehicle"],
    "doi": "10.1016/j.apenergy.2024.123871"
  },
  "sections": [
    {
      "heading": "1. Introduction",
      "level": 1,
      "paragraphs": ["Electric vehicles, offering zero tailpipe emissions..."],
      "page_start": 1,
      "page_end": 2
    },
    {
      "heading": "3. Results and discussion",
      "level": 1,
      "paragraphs": ["3.1. Augmented datasets The proposed approach..."],
      "page_start": 5,
      "page_end": 8
    }
  ],
  "references": [
    {
      "text": "Zhou P, Wang M. Carbon dioxide emissions allocation: A review. Ecol Econ 2016;125:47-59.",
      "raw_text": "[1] Zhou P, Wang M. Carbon dioxide emissions allocation: A review...",
      "title": "Carbon dioxide emissions allocation: A review",
      "authors": ["Zhou P, Wang M."],
      "year": "2016",
      "identifiers": {},
      "url": "http://refhub.elsevier.com/S0306-2619(24)01254-6/sb1",
      "type": "journalArticle",
      "position": {"x": 311.6, "y": 673.7},
      "ref_number": 1,
      "citations": [
        {
          "page": 2,
          "x": 275.2,
          "y": 295.5,
          "text": "[1]",
          "sentence": "Electric vehicles, offering zero tailpipe emissions, are regarded as a promising solution for sustainable transportation in the future [1]."
        }
      ]
    }
  ],
  "total_pages": 12
}
```

---

## Supported Citation Formats

| Format | Example | Method |
|--------|---------|--------|
| Bracketed numbers | `[1]` `[2,3]` `[4-6]` `[16,18,21]` | Regex + page.search_for() |
| Parenthesized numbers | `(1)` `(2,3)` | Regex + page.search_for() |
| Fullwidth brackets | `［1］` `［2,3］` | Regex + page.search_for() |
| Unicode superscript | `¹²` `³⁻⁵` | Unicode digit mapping |
| Author-year (parenthetical) | `(Zhou and Wang, 2016)` `(Zhang et al., 2020)` | Dynamic pattern from reference metadata |
| Author-year (narrative) | `Zhou et al. (2016) found...` | Dynamic pattern from reference metadata |

---

## How It Works

### Reference Extraction Pipeline

1. **Read PDF pages** via PyMuPDF, extracting text spans with `(x, y, width, height, text)`.
2. **Merge same-line spans** — items with the same Y coordinate are joined into a `PDFLine`.
3. **Remove headers/footers** — duplicate text at same position across pages is stripped.
4. **Detect columns** — X-position clustering identifies multi-column layouts.
5. **Find reference section** — scans pages back-to-front for "References"/"Bibliography"/"参考文献" header.
6. **Split into entries** — 8 regex patterns detect reference-number prefixes; continuation lines merged by indentation.
7. **Parse each entry** — extract DOI/arXiv via regex, split title/authors/year heuristically.

### Citation Location

1. **Numbered citations**: Scan body pages' `page.get_text()` for bracket/paren citation patterns, then use `page.search_for()` to get exact PDF bounding box coordinates.
2. **Author-year citations**: Build search patterns from each reference's first-author-lastname + year, scan body text for matches.
3. **Sentence extraction**: From the match position, search backward/forward for `. ! ?` sentence boundaries to isolate the containing sentence.

### Section Parsing

1. Call `get_ref_lines(full_text=True)` to get all text parts (already cleaned of headers/footers).
2. For each part, check if the first few lines match heading patterns: numbering (`1.`, `2.1.`, `3.3.1`), keyword (`Introduction`, `Methods`, etc.), or font-size change.
3. Remaining lines are grouped into paragraphs by vertical spacing and indentation.
4. Heading level is inferred from numbering depth and font size.

### Metadata Extraction

1. **Title**: largest-font text block in upper 8-35% of page 1.
2. **Authors**: text block directly below title with person-name font size.
3. **Affiliations**: text below authors matching institution keywords.
4. **Abstract**: text after the second "A B S T R A C T" marker (right-column actual abstract).
5. **Keywords**: text after "Keywords:" label.
6. **DOI**: regex on full page 1 text.

---

## Project Files

| File | Purpose |
|------|---------|
| `main.py` | CLI entry point |
| `extractor.py` | `PDFReferenceExtractor` — reference-only API |
| `paper_extractor.py` | `PaperExtractor` — full-paper-extraction API |
| `reference_parser.py` | Core parsing: reference section detection, entry merging |
| `citation_finder.py` | In-text citation scanning and sentence extraction |
| `section_parser.py` | Section heading detection and paragraph grouping |
| `metadata_extractor.py` | Title, authors, affiliations, abstract, keywords, DOI |
| `pdf_reader.py` | PDF → `PDFLine` text extraction layer |
| `utils.py` | DOI/arXiv regex, reference text parsing |
| `ref_types.py` | Dataclasses: `PDFLine`, `PDFItem`, `ItemInfo` |

## Dependencies

- `pymupdf` (PyMuPDF) — MIT/AGPL licensed, the only runtime dependency

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| 0 references | PDF has <3 pages or no reference section header | Use `from_page` to point to the reference section manually |
| Missing references | Unusual numbering format | Extend `REF_REGEX` in `reference_parser.py` |
| Garbled Chinese text | PDF uses non-standard font encoding | Try a different PDF reader or OCR preprocessing |
| References merged incorrectly | Indentation detection failed | Check `merge_same_ref` indent logic |
| Wrong title extracted | Journal masthead font is larger than paper title | Title heuristic uses top-8% filter; adjust if journal header is unusually large |
| Missing section headings | All text is same font size | Section detection falls back to numbering patterns; unnumbered headings may be missed if font size doesn't differ |
| Equation number as citation | `(1)` `(2)` in equations match citation regex | Known limitation — no equation-aware filter |
