#!/usr/bin/env python3
"""Rasterise a digital PDF into an image-only PDF, simulating a scan.

Used to build a labelled mini-corpus for threshold calibration when no real
scanned PDFs are available.
"""

import sys
from pathlib import Path

import fitz


def rasterise(src: Path, dst: Path, dpi: int = 150) -> None:
    src_doc = fitz.open(str(src))
    dst_doc = fitz.open()
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for i in range(len(src_doc)):
        page = src_doc[i]
        pix = page.get_pixmap(matrix=mat, alpha=False)
        new_page = dst_doc.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, pixmap=pix)
    dst_doc.save(str(dst), garbage=4, deflate=True)
    dst_doc.close()
    src_doc.close()


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: make_scanned_fixture.py <src.pdf> <dst.pdf>", file=sys.stderr)
        sys.exit(2)
    rasterise(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"Rasterised → {sys.argv[2]}")


if __name__ == "__main__":
    main()
