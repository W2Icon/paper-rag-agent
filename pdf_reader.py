"""PDF text extraction layer using PyMuPDF, equivalent to pdf.ts readPdfPage + annotations."""

from typing import Optional

import fitz  # PyMuPDF

from ref_types import PDFItem, PDFLine
from utils import URL_REGEX


def _flatten_spans_to_items(page: fitz.Page) -> list[PDFItem]:
    """
    Extract text spans from a PyMuPDF page and convert to PDFItem format.
    Equivalent to pdfPage.getTextContent() in PDF.js.
    """
    text_dict = page.get_text("dict")
    items: list[PDFItem] = []

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # skip non-text blocks (images)
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                bbox = span["bbox"]  # [x0, y0, x1, y1], origin top-left
                x, y = bbox[0], bbox[1]
                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]
                items.append(
                    PDFItem(
                        str=text,
                        transform=[1.0, 0.0, 0.0, 1.0, x, y],
                        width=width,
                        height=height,
                        font_name=span.get("font", ""),
                    )
                )

    return items


def _extract_links(page: fitz.Page) -> list[dict]:
    """
    Extract link annotations from a PyMuPDF page.
    Equivalent to pdfPage.getAnnotations() filtered for URL links.
    Returns list of dicts with 'rect' and 'url'.
    """
    annotations: list[dict] = []
    for link in page.get_links():
        uri = link.get("uri", "")
        if uri:
            rect = link["from"]  # Rect in PyMuPDF format
            annotations.append({"rect": list(rect), "url": uri})
    return annotations


def _is_intersect(a: dict, b: dict) -> bool:
    """Check if two boxes intersect (from pdf.ts L232-243)."""
    return not (
        b["right"] < a["left"]
        or b["left"] > a["right"]
        or b["bottom"] > a["top"]
        or b["top"] < a["bottom"]
    )


def _update_items_annotations(items: list[PDFItem], annotations: list[dict]) -> None:
    """
    Attach URL to items that intersect with link annotations.
    Ported from pdf.ts L248-265 (updateItemsAnnotions).
    """
    for annotation in annotations:
        rect = annotation["rect"]
        anno_box = {
            "left": rect[0],
            "bottom": rect[1],
            "right": rect[2],
            "top": rect[3],
        }
        for item in items:
            x, y = item.transform[4], item.transform[5]
            item_box = {
                "left": x,
                "bottom": y,
                "right": x + item.width,
                "top": y + item.height,
            }
            if _is_intersect(anno_box, item_box):
                item.url = annotation.get("url")


def _merge_same_line(items: list[PDFItem]) -> list[PDFLine]:
    """
    Merge PDFItems with the same Y coordinate into PDFLines.
    Ported from pdf.ts L78-134 (mergeSameLine).
    """
    if not items:
        return []

    def to_line(item: PDFItem) -> PDFLine:
        x = round(item.transform[4], 1)
        y = round(item.transform[5], 1)
        line = PDFLine(
            x=x,
            y=y,
            text=item.str or "",
            height=item.height,
            width=item.width,
            url=item.url,
            _height=[item.height],
        )
        if line.width < 0:
            line.x += line.width
            line.width = -line.width
        return line

    lines = [to_line(items[0])]

    for j in range(1, len(items)):
        line = to_line(items[j])
        last_line = lines[-1]

        # Check if same line (same Y or within height tolerance, for super/subscript)
        if (
            line.y == last_line.y
            or (line.y >= last_line.y and line.y < last_line.y + last_line.height)
            or (
                line.y + line.height > last_line.y
                and line.y + line.height <= last_line.y + last_line.height
            )
        ):
            last_line.text += " " + line.text
            last_line.width += line.width
            last_line.url = last_line.url or line.url
            last_line._height.append(line.height)
        else:
            # Compute mode height for completed line
            hh = last_line._height
            freq: dict[str, int] = {}
            for h in hh:
                key = str(h)
                freq[key] = freq.get(key, 0) + 1
            last_line.height = float(
                sorted(freq.keys(), key=lambda k: freq[k], reverse=True)[0]
            )
            lines.append(line)

    # Process last line's height
    if lines:
        hh = lines[-1]._height
        freq: dict[str, int] = {}
        for h in hh:
            key = str(h)
            freq[key] = freq.get(key, 0) + 1
        lines[-1].height = float(
            sorted(freq.keys(), key=lambda k: freq[k], reverse=True)[0]
        )

    return lines


def read_pdf_page(page: fitz.Page) -> list[PDFLine]:
    """
    Read a single PDF page and return merged text lines.
    Equivalent to pdf.ts readPdfPage() (L272-284).
    """
    items = _flatten_spans_to_items(page)
    if not items:
        return []

    annotations = _extract_links(page)
    _update_items_annotations(items, annotations)

    lines = _merge_same_line(items)
    return lines
