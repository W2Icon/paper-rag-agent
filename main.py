#!/usr/bin/env python3
"""
CLI entry point for PDF Reference Extractor.

Extracts reference lists and locates in-text citation positions.
Use --full-paper to extract metadata, structured sections, and paragraphs.

Usage:
    python main.py paper.pdf
    python main.py paper.pdf -o refs.json
    python main.py paper.pdf --from-page 10 --verbose
    python main.py paper.pdf --full-paper -v
"""

import argparse
import sys
from pathlib import Path

from extractor import PDFReferenceExtractor
from paper_extractor import PaperExtractor


def main():
    parser = argparse.ArgumentParser(description="Extract references from academic PDFs.")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("-o", "--output", default=None, help="Output JSON file (default: output/<pdf_name>.json)")
    parser.add_argument("--from-page", type=int, default=0, help="Start scanning from this page (1-indexed)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output")
    parser.add_argument("--full-paper", action="store_true", dest="full_paper",
                        help="Full paper extraction: metadata, sections, paragraphs, references, citations")

    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"Error: file not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    if args.full_paper:
        extractor = PaperExtractor()
        result = extractor.extract(pdf_path, from_page=args.from_page, verbose=args.verbose)
        if args.verbose:
            print(f"\nDone: {len(result.sections)} sections, {len(result.references)} references "
                  f"from {result.total_pages} pages", file=sys.stderr)
    else:
        extractor = PDFReferenceExtractor()
        result = extractor.extract(pdf_path, from_page=args.from_page, verbose=args.verbose)
        if args.verbose:
            print(f"\nDone: {len(result.references)} references from {result.total_pages} pages", file=sys.stderr)

    json_str = result.to_json(pretty=not args.compact)

    if args.output:
        output_path = Path(args.output)
    else:
        from config import get_config
        output_path = get_config().data_dir / "output" / f"{pdf_path.stem}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json_str, encoding="utf-8")
    if args.verbose:
        print(f"Saved to: {output_path}", file=sys.stderr)
    else:
        print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()
