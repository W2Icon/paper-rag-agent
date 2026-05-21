#!/usr/bin/env python3
"""
Calibrate the auto-backend chars/page threshold.

For each PDF in a directory, measure several signals of "selectable-text density"
using PyMuPDF (the same library `_resolve_backend` uses):
  - mean chars/page    (current signal, threshold = 200)
  - median chars/page  (robust to title-page outlier)
  - p25  chars/page    (25th percentile)
  - % pages with < 50 chars  (direct "needs OCR" proxy)
  - % pages with < 100 chars

Optionally takes a ground-truth labels file (CSV: filename,is_scanned) so we
can compute the optimal threshold that maximises classification accuracy.
Without labels, just reports the distribution + a recommended threshold based
on the gap between the lower mode (scanned) and upper mode (digital).
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

import fitz


def measure_pdf(path: Path, sample_pages: int = 0) -> dict:
    """Compute chars/page metrics for one PDF.

    sample_pages=0  → use all pages (most accurate)
    sample_pages=N  → use first N pages (mirrors the production `_resolve_backend`
                      which only samples 3 pages for speed)
    """
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    try:
        n_pages = len(doc)
        if sample_pages > 0:
            n_to_read = min(sample_pages, n_pages)
        else:
            n_to_read = n_pages

        per_page_chars: list[int] = []
        for i in range(n_to_read):
            per_page_chars.append(len(doc[i].get_text("text")))
    finally:
        doc.close()

    if not per_page_chars:
        return {"error": "empty pdf"}

    mean = statistics.mean(per_page_chars)
    median = statistics.median(per_page_chars)
    p25 = _percentile(per_page_chars, 25)
    p10 = _percentile(per_page_chars, 10)
    pct_lt_50 = sum(1 for c in per_page_chars if c < 50) / len(per_page_chars) * 100
    pct_lt_100 = sum(1 for c in per_page_chars if c < 100) / len(per_page_chars) * 100

    return {
        "n_pages_total": n_pages,
        "n_pages_sampled": n_to_read,
        "mean": mean,
        "median": median,
        "p25": p25,
        "p10": p10,
        "pct_lt_50": pct_lt_50,
        "pct_lt_100": pct_lt_100,
        "min": min(per_page_chars),
        "max": max(per_page_chars),
        "raw": per_page_chars,
    }


def _percentile(data: list[int], pct: float) -> float:
    """Linear-interpolation percentile (numpy-free)."""
    s = sorted(data)
    if not s:
        return 0.0
    k = (len(s) - 1) * pct / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return s[f] * (c - k) + s[c] * (k - f)


def load_labels(path: Path) -> dict[str, bool]:
    """Load a CSV with `filename,is_scanned` rows. is_scanned ∈ {0,1,true,false}."""
    labels: dict[str, bool] = {}
    with path.open() as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row or len(row) < 2:
                continue
            if i == 0 and row[0].lower() in ("filename", "file", "name"):
                continue
            name = row[0].strip()
            val = row[1].strip().lower()
            labels[name] = val in ("1", "true", "yes", "y", "scanned")
    return labels


def find_optimal_threshold(
    samples: list[tuple[str, float, bool]],
    metric_name: str,
) -> tuple[float, float]:
    """Sweep threshold values, find one with best accuracy.

    Routing rule: metric < threshold → "predict scanned" → use MinerU.
    Returns (best_threshold, best_accuracy).
    """
    if not samples:
        return 0.0, 0.0
    values = sorted({v for _, v, _ in samples})
    # Try each midpoint between adjacent values + edges
    candidates = [0.0]
    for i in range(len(values) - 1):
        candidates.append((values[i] + values[i + 1]) / 2)
    candidates.append(values[-1] + 1)

    best_thresh = candidates[0]
    best_acc = -1.0
    for t in candidates:
        correct = 0
        for _, v, is_scanned in samples:
            predicted_scanned = v < t
            if predicted_scanned == is_scanned:
                correct += 1
        acc = correct / len(samples)
        if acc > best_acc:
            best_acc = acc
            best_thresh = t
    return best_thresh, best_acc


def ascii_bar(value: float, max_value: float, width: int = 30) -> str:
    if max_value <= 0:
        return ""
    n = int(width * value / max_value)
    return "█" * n + "·" * (width - n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="Directory of PDFs to scan")
    ap.add_argument("--labels", help="Optional CSV of ground-truth labels (filename,is_scanned)")
    ap.add_argument("--sample-pages", type=int, default=0,
                    help="Only read first N pages (mimics production sniff). "
                         "0 = read all (default, more accurate calibration).")
    ap.add_argument("--prod-sample", action="store_true",
                    help="Shortcut for --sample-pages=3 (what the production code does).")
    args = ap.parse_args()

    if args.prod_sample and not args.sample_pages:
        args.sample_pages = 3

    root = Path(args.path).expanduser().resolve()
    pdfs = sorted(p for p in root.glob("*.pdf") if not p.name.startswith("."))
    if not pdfs:
        print(f"No PDFs found in {root}", file=sys.stderr)
        sys.exit(1)

    labels: dict[str, bool] = {}
    if args.labels:
        labels = load_labels(Path(args.labels))
        print(f"Loaded {len(labels)} labels from {args.labels}\n")

    print(f"Scanning {len(pdfs)} PDFs in {root}")
    print(f"Sample mode: {'first ' + str(args.sample_pages) + ' pages' if args.sample_pages else 'all pages'}")
    print()

    results: list[tuple[Path, dict]] = []
    for pdf in pdfs:
        r = measure_pdf(pdf, sample_pages=args.sample_pages)
        results.append((pdf, r))

    # ── Per-file table ──
    print("─" * 110)
    hdr = (f"{'file':<60} {'pages':>6} {'mean':>7} {'med':>7} "
           f"{'p25':>7} {'p10':>7} {'%<50':>6} {'%<100':>6} {'label':>8}")
    print(hdr)
    print("─" * 110)
    for pdf, r in results:
        if "error" in r:
            print(f"{pdf.name[:58]:<60} ERROR: {r['error']}")
            continue
        lbl = labels.get(pdf.name, None)
        lbl_str = ("scanned" if lbl else "digital") if lbl is not None else "?"
        print(
            f"{pdf.name[:58]:<60} {r['n_pages_total']:>6} "
            f"{r['mean']:>7.0f} {r['median']:>7.0f} {r['p25']:>7.0f} {r['p10']:>7.0f} "
            f"{r['pct_lt_50']:>5.0f}% {r['pct_lt_100']:>5.0f}% {lbl_str:>8}"
        )
    print("─" * 110)

    # ── Aggregate distribution ──
    valid = [r for _, r in results if "error" not in r]
    if not valid:
        print("No valid PDFs.", file=sys.stderr)
        sys.exit(1)

    print("\nAggregate chars/page (across all pages of all PDFs):")
    all_pages = [c for r in valid for c in r["raw"]]
    overall_mean = statistics.mean(all_pages)
    overall_median = statistics.median(all_pages)
    print(f"  total pages: {len(all_pages)}")
    print(f"  mean:        {overall_mean:.0f}")
    print(f"  median:      {overall_median:.0f}")
    print(f"  p10:         {_percentile(all_pages, 10):.0f}")
    print(f"  p25:         {_percentile(all_pages, 25):.0f}")
    print(f"  p75:         {_percentile(all_pages, 75):.0f}")
    print(f"  p90:         {_percentile(all_pages, 90):.0f}")
    print(f"  min/max:     {min(all_pages)} / {max(all_pages)}")
    print(f"  pages < 50 chars:  {sum(1 for c in all_pages if c < 50)} "
          f"({sum(1 for c in all_pages if c < 50) / len(all_pages) * 100:.1f}%)")
    print(f"  pages < 200 chars: {sum(1 for c in all_pages if c < 200)} "
          f"({sum(1 for c in all_pages if c < 200) / len(all_pages) * 100:.1f}%)")

    # ── Per-file mean distribution + histogram ──
    per_file_means = sorted((r["mean"], pdf.name) for pdf, r in results if "error" not in r)
    print("\nPer-file MEAN chars/page (sorted ascending — discover the gap):")
    max_mean = max(m for m, _ in per_file_means)
    for m, name in per_file_means:
        print(f"  {m:>6.0f}  {ascii_bar(m, max_mean)}  {name[:55]}")

    per_file_pct_lt50 = sorted((r["pct_lt_50"], pdf.name) for pdf, r in results if "error" not in r)
    print("\nPer-file %pages-with-<50-chars (sorted descending — scanned PDFs first):")
    per_file_pct_lt50 = sorted(per_file_pct_lt50, reverse=True)
    for p, name in per_file_pct_lt50:
        print(f"  {p:>5.0f}%  {ascii_bar(p, 100)}  {name[:55]}")

    # ── Threshold sweep (only meaningful with labels) ──
    if labels:
        print("\n" + "═" * 110)
        print("OPTIMAL THRESHOLD SWEEP (requires labels)")
        print("═" * 110)

        labeled = []
        for pdf, r in results:
            if "error" in r:
                continue
            if pdf.name not in labels:
                continue
            labeled.append((pdf.name, r, labels[pdf.name]))

        for metric in ("mean", "median", "p25", "p10", "pct_lt_50", "pct_lt_100"):
            # For "%<N" metrics: higher = more scanned, so flip predicate.
            if metric.startswith("pct_lt"):
                samples = [(name, 100 - r[metric], is_s) for name, r, is_s in labeled]
                desc = f"{metric} (note: predicate flipped: <thr → digital)"
            else:
                samples = [(name, r[metric], is_s) for name, r, is_s in labeled]
                desc = metric
            t, acc = find_optimal_threshold(samples, metric)
            print(f"  {desc:<35}  best_threshold={t:>8.1f}  accuracy={acc * 100:.1f}%")
    else:
        print("\n" + "═" * 110)
        print("HEURISTIC RECOMMENDATION (no labels provided)")
        print("═" * 110)
        means = [m for m, _ in per_file_means]
        # Detect a bimodal gap by looking at the largest jump between sorted means.
        gaps = [(means[i + 1] - means[i], means[i], means[i + 1]) for i in range(len(means) - 1)]
        if gaps:
            gaps.sort(reverse=True)
            biggest_gap, lo, hi = gaps[0]
            midpoint = (lo + hi) / 2
            print(f"  Largest gap in per-file mean chars/page:")
            print(f"    {lo:.0f}  →  {hi:.0f}   (Δ={biggest_gap:.0f})")
            print(f"    midpoint suggestion: {midpoint:.0f}")
            print(f"  Current production threshold: 200")
            if biggest_gap > 500:
                print(f"  ✓ Clear bimodal gap → suggest threshold = {midpoint:.0f}")
            else:
                print(f"  ⚠ No clear gap (all PDFs cluster together).")
                print(f"    Likely all the same type (all digital, or all scanned).")
                print(f"    For digital-only corpora, recommend threshold ≤ {min(means) * 0.5:.0f}")
                print(f"    (half the smallest observed mean — leaves safety margin).")


if __name__ == "__main__":
    main()
