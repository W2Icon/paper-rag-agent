from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from paper_extractor import PaperExtractor
from converters import convert_extract_result, compute_sha256
from db_connection import DatabaseConnection
from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo


@dataclass
class IngestResult:
    pdf_path: str
    paper_id: Optional[int] = None
    status: str = "pending"
    error_message: Optional[str] = None
    title: str = ""
    sections_count: int = 0
    references_count: int = 0
    citations_count: int = 0
    embeddings_count: int = 0
    # One-stop pipeline outcomes
    analyzed: bool = False
    viewpoints_count: int = 0
    cross_pairs_detected: int = 0
    cross_pairs_related: int = 0
    archive_path: Optional[str] = None
    kg_updated: bool = False
    kg_promoted: bool = False
    # Warnings from optional steps that failed; ingest itself still succeeded
    pipeline_warnings: list[str] = field(default_factory=list)


@dataclass
class BatchResult:
    results: list[IngestResult] = field(default_factory=list)
    total: int = 0
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            f"Total: {self.total}  |  "
            f"Succeeded: {self.succeeded}  |  "
            f"Skipped: {self.skipped}  |  "
            f"Failed: {self.failed}  |  "
            f"Time: {self.elapsed_seconds:.1f}s",
        ]
        for r in self.results:
            if r.status == "error":
                lines.append(f"  ERROR: {r.pdf_path} — {r.error_message}")
        return "\n".join(lines)


class BatchProcessor:

    def __init__(
        self,
        db: DatabaseConnection,
        verbose: bool = False,
        from_page: int = 0,
        with_embeddings: bool = False,
        with_analysis: bool = False,
        with_cross_analyze: bool = False,
        with_archive: bool = False,
        with_kg_update: bool = True,
        parse_backend: str = "pymupdf",
    ):
        self._db = db
        self._paper_repo = PaperRepo(db)
        self._ref_repo = ReferenceRepo(db)
        self._parse_backend = parse_backend
        self._extractor = PaperExtractor(backend=parse_backend)
        self._verbose = verbose
        self._from_page = from_page
        self._with_embeddings = with_embeddings
        self._with_analysis = with_analysis
        self._with_cross_analyze = with_cross_analyze
        self._with_archive = with_archive
        self._with_kg_update = with_kg_update
        self._embedder = None      # lazy: only instantiate if embedding requested
        self._analyzer = None      # lazy: only instantiate if analysis requested
        self._llm_provider = None  # shared between analyzer + embedder + cross_analyzer
        self._llm_config = None

    def _get_llm(self):
        """Shared LLM provider for embedder + analyzer + cross-analyzer."""
        if self._llm_provider is None:
            from llm_interface import LLMConfig, get_llm_provider
            cfg = LLMConfig.from_env()
            if cfg.provider in ("none", ""):
                raise RuntimeError(
                    "LLM features require LLM_PROVIDER and LLM_API_KEY to be set."
                )
            self._llm_provider = get_llm_provider(cfg)
            self._llm_config = cfg
        return self._llm_provider

    def _get_embedder(self):
        if self._embedder is None:
            from embedder import PaperEmbedder
            self._embedder = PaperEmbedder(self._get_llm())
        return self._embedder

    def _get_analyzer(self):
        if self._analyzer is None:
            from llm_analyzer import PaperAnalyzer
            self._analyzer = PaperAnalyzer(self._get_llm(), self._llm_config)
        return self._analyzer

    def ingest_pdf(self, pdf_path: Path) -> IngestResult:
        result = IngestResult(pdf_path=str(pdf_path))
        try:
            sha256 = compute_sha256(pdf_path)
            existing = self._paper_repo.get_by_sha256(sha256)
            if existing:
                result.status = "skipped"
                result.paper_id = existing.id
                result.title = existing.title
                return result

            if self._verbose:
                print(f"  Extracting: {pdf_path.name}", file=sys.stderr)

            extract_result = self._extractor.extract(
                pdf_path, from_page=self._from_page, verbose=False,
            )

            paper, sections, ref_groups = convert_extract_result(extract_result, pdf_path)

            paper_id = self._paper_repo.insert_full_paper(
                paper, sections, ref_groups, self._ref_repo,
            )

            total_citations = sum(len(cits) for _, cits in ref_groups)
            result.paper_id = paper_id
            result.status = "success"
            result.title = paper.title
            result.sections_count = len(sections)
            result.references_count = len(ref_groups)
            result.citations_count = total_citations

            # ── Post-ingest pipeline (each step is independent, soft-failing) ──
            self._run_pipeline(paper_id, result)

        except Exception as e:
            result.status = "error"
            result.error_message = str(e)

        return result

    def embed_paper_by_id(self, paper_id: int, *, force: bool = False,
                          include_sections: bool = True,
                          include_refs: bool = True) -> int:
        """Compute and persist embeddings for a single paper. Returns total
        vectors written. Skips silently when already embedded unless force=True."""
        paper = self._paper_repo.get_by_id(paper_id)
        if not paper:
            raise ValueError(f"paper {paper_id} not found")

        # Check existing state: if already populated and not forced, skip
        if not force:
            row = self._db.conn.execute(
                "SELECT title_embedding IS NOT NULL AS has_t, "
                "abstract_embedding IS NOT NULL AS has_a "
                "FROM papers WHERE id = ?",
                (paper_id,),
            ).fetchone()
            if row and row["has_t"] and row["has_a"]:
                return 0

        from embedder import vec_to_bytes
        embedder = self._get_embedder()
        sections = self._paper_repo.get_sections(paper_id)
        references = self._ref_repo.get_references_for_paper(paper_id)

        result = embedder.embed_paper(
            paper, sections, references,
            include_sections=include_sections,
            include_refs=include_refs,
        )

        self._paper_repo.update_paper_embeddings(
            paper_id,
            title=vec_to_bytes(result.title) if result.title else None,
            abstract=vec_to_bytes(result.abstract) if result.abstract else None,
            fulltext=vec_to_bytes(result.fulltext) if result.fulltext else None,
        )
        if result.sections:
            self._paper_repo.update_section_embeddings(
                [(sid, vec_to_bytes(v)) for sid, v in result.sections]
            )
        if result.chunks:
            # Stamp paper_id onto each chunk so the repo can persist it
            # without a separate join.
            for ch in result.chunks:
                ch.paper_id = paper_id
            self._paper_repo.replace_section_chunks(result.chunks)
        if result.references:
            self._ref_repo.update_ref_embeddings(
                [(rid, vec_to_bytes(v)) for rid, v in result.references]
            )
        return result.total_vectors

    # ── One-stop post-ingest pipeline ────────────────────────────

    def _run_pipeline(self, paper_id: int, result: IngestResult) -> None:
        """Run optional post-ingest steps. Each step is independent and
        soft-failing — if one step throws, we record a warning and continue
        with the rest. The ingest itself is already committed by the time we
        get here, so partial pipeline failure is acceptable.
        """
        if self._with_embeddings:
            try:
                result.embeddings_count = self.embed_paper_by_id(paper_id)
            except Exception as e:
                result.pipeline_warnings.append(f"embed failed: {e}")
                if self._verbose:
                    print(f"  WARN: embed failed for paper {paper_id}: {e}",
                          file=sys.stderr)

        if self._with_analysis:
            try:
                vp_count = self.analyze_paper_by_id(paper_id)
                result.analyzed = True
                result.viewpoints_count = vp_count
            except Exception as e:
                result.pipeline_warnings.append(f"analyze failed: {e}")
                if self._verbose:
                    print(f"  WARN: analyze failed for paper {paper_id}: {e}",
                          file=sys.stderr)

        # Embed the LLM-generated content right after analysis succeeds.
        if self._with_analysis and self._with_embeddings and result.analyzed:
            try:
                self.embed_llm_content_by_id(paper_id)
            except Exception as e:
                result.pipeline_warnings.append(f"embed_llm_content failed: {e}")

        if self._with_cross_analyze and result.analyzed:
            try:
                d, r = self.cross_analyze_paper(paper_id)
                result.cross_pairs_detected = d
                result.cross_pairs_related = r
            except Exception as e:
                result.pipeline_warnings.append(f"cross-analyze failed: {e}")
                if self._verbose:
                    print(f"  WARN: cross-analyze failed for paper {paper_id}: {e}",
                          file=sys.stderr)

        # Knowledge graph sync — runs after analysis so llm_research_field
        # is populated. Idempotent: safe to re-run for an existing paper.
        if self._with_kg_update:
            try:
                from knowledge_graph import KGBuilder
                kg_res = KGBuilder(self._db).add_paper(paper_id)
                result.kg_updated = True
                result.kg_promoted = bool(kg_res.get("promoted_external"))
            except Exception as e:
                result.pipeline_warnings.append(f"kg update failed: {e}")
                if self._verbose:
                    print(f"  WARN: kg update failed for paper {paper_id}: {e}",
                          file=sys.stderr)

        if self._with_archive:
            try:
                from config import get_config
                from archive_writer import write_paper, write_index
                cfg = get_config()
                path = write_paper(self._db, paper_id, cfg.archive_dir)
                write_index(self._db, cfg.archive_dir)
                result.archive_path = str(path)
            except Exception as e:
                result.pipeline_warnings.append(f"archive failed: {e}")
                if self._verbose:
                    print(f"  WARN: archive failed for paper {paper_id}: {e}",
                          file=sys.stderr)

    def analyze_paper_by_id(self, paper_id: int, *, force: bool = False) -> int:
        """Run the 3-stage LLM analyze (summary, ref scoring, viewpoints) on
        one paper. Returns the number of lit_review_entries written. Skips
        silently when llm_analyzed_at is set unless force=True.
        """
        from db.lit_review_repo import LitReviewRepo
        from llm_analyzer import PaperAnalyzer  # noqa: F401 — for type clarity

        paper = self._paper_repo.get_by_id(paper_id)
        if paper is None:
            raise ValueError(f"paper {paper_id} not found")
        if not force and paper.llm_analyzed_at:
            return 0

        analyzer = self._get_analyzer()
        lr_repo = LitReviewRepo(self._db)

        sections = self._paper_repo.get_sections(paper_id)
        references = self._ref_repo.get_references_for_paper(paper_id)
        citations = self._paper_repo.get_citation_locations(paper_id)

        # Stage 1 — paper-level summary / methodology / findings
        analysis = analyzer.analyze_paper(paper, sections)
        self._paper_repo.update_llm_fields(
            paper_id,
            summary=analysis.summary,
            research_field=analysis.research_field,
            methodology=analysis.methodology,
            key_findings=analysis.key_findings,
        )
        # Refresh in-memory paper so downstream stages see fresh fields
        paper.llm_summary = analysis.summary
        paper.llm_research_field = analysis.research_field
        paper.llm_methodology = analysis.methodology
        paper.llm_key_findings = analysis.key_findings

        # Stage 2 — per-reference relevance + role
        if references:
            scores = analyzer.score_references(paper, references)
            updates = [(s.ref_id, s.relevance_score, s.relationship) for s in scores]
            if updates:
                self._ref_repo.bulk_update_llm_fields(updates)

        # Stage 3 — lit-review viewpoints (论点)
        n_vp = 0
        if references and citations:
            if force:
                lr_repo.delete_by_paper(paper_id)
            entries = analyzer.extract_lit_review(paper_id, sections, references, citations)
            if entries:
                lr_repo.insert_many(entries)
                n_vp = len(entries)
        return n_vp

    def embed_llm_content_by_id(self, paper_id: int) -> int:
        """Embed papers.llm_summary + all lit_review_entries.viewpoint for one
        paper. Assumes analyze_paper_by_id has already run."""
        from embedder import vec_to_bytes
        from db.lit_review_repo import LitReviewRepo

        paper = self._paper_repo.get_by_id(paper_id)
        if paper is None or not paper.llm_summary:
            return 0

        lr_repo = LitReviewRepo(self._db)
        viewpoints = lr_repo.get_by_paper(paper_id)

        embedder = self._get_embedder()
        result = embedder.embed_llm_content(paper, viewpoints)

        if result.summary:
            self._paper_repo.update_summary_embedding(paper_id, vec_to_bytes(result.summary))
        if result.viewpoints:
            lr_repo.update_viewpoint_embeddings(
                [(eid, vec_to_bytes(v)) for eid, v in result.viewpoints]
            )
        return result.total_vectors

    def cross_analyze_paper(self, paper_id: int) -> tuple[int, int]:
        """Detect shared refs between this paper and the rest of the library,
        then LLM-relate any new pairs. Returns (detected, related)."""
        from cross_analysis import CitationCrossAnalyzer
        analyzer = CitationCrossAnalyzer(self._db)
        detected = analyzer.detect_all_shared_citations(paper_id_filter=paper_id)
        # Relate pairs with ≥1 shared ref (looser threshold for one-stop flow;
        # the LLM call is cheap and a small library benefits from full edges).
        ok, _fail = analyzer.relate_all_pairs(
            self._get_llm(), min_shared=1, limit=20, tier="simple",
        )
        return detected, ok

    def ingest_directory(
        self,
        dir_path: Path,
        recursive: bool = False,
        pattern: str = "*.pdf",
    ) -> BatchResult:
        pdfs = self._collect_pdfs(dir_path, recursive, pattern)
        batch = BatchResult(total=len(pdfs))
        start = time.time()

        for i, pdf in enumerate(pdfs, 1):
            self._show_progress(i, len(pdfs), pdf.name)
            r = self.ingest_pdf(pdf)
            batch.results.append(r)
            if r.status == "success":
                batch.succeeded += 1
            elif r.status == "skipped":
                batch.skipped += 1
            else:
                batch.failed += 1

        batch.elapsed_seconds = time.time() - start
        if self._verbose:
            print("", file=sys.stderr)
        return batch

    def _show_progress(self, current: int, total: int, name: str) -> None:
        if not self._verbose:
            return
        pct = current / total * 100 if total else 0
        bar_len = 30
        filled = int(bar_len * current / total) if total else 0
        bar = "#" * filled + "-" * (bar_len - filled)
        print(
            f"\r  [{bar}] {current}/{total} ({pct:.0f}%) {name[:40]}",
            end="", flush=True, file=sys.stderr,
        )

    def _collect_pdfs(self, dir_path: Path, recursive: bool, pattern: str) -> list[Path]:
        if recursive:
            pdfs = sorted(dir_path.rglob(pattern))
        else:
            pdfs = sorted(dir_path.glob(pattern))
        return pdfs
