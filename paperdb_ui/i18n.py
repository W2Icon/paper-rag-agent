"""Lightweight i18n for the paperdb Streamlit UI.

  - language stored in st.session_state.lang ("en" | "zh")
  - t(key, **kwargs) looks up TRANSLATIONS[lang][key]; falls back to EN, then key
  - language_toggle() renders the top-right EN / 中文 switcher (called from theme.apply_theme)

Only UI chrome is translated — LLM output, paper metadata, and SSE event payloads
are kept verbatim.
"""

from __future__ import annotations

from typing import Any

import streamlit as st


_DEFAULT_LANG = "en"
_SUPPORTED = ("en", "zh")


def get_lang() -> str:
    return st.session_state.get("lang", _DEFAULT_LANG)


def set_lang(code: str) -> None:
    if code in _SUPPORTED:
        st.session_state["lang"] = code


def t(key: str, **kwargs: Any) -> str:
    """Translate `key` for the current language. kwargs feed str.format()."""
    lang = get_lang()
    s = (
        TRANSLATIONS.get(lang, {}).get(key)
        or TRANSLATIONS["en"].get(key)
        or key
    )
    if kwargs:
        try:
            s = s.format(**kwargs)
        except Exception:
            pass
    return s


def language_toggle() -> None:
    """Top-right EN / 中文 switcher. Rendered once per page via apply_theme()."""
    # Push to the right with a wide spacer column.
    spacer, en_col, zh_col = st.columns([14, 1, 1])
    current = get_lang()
    with en_col:
        if st.button(
            "EN", key="__lang_en", use_container_width=True,
            type="primary" if current == "en" else "secondary",
        ):
            if current != "en":
                set_lang("en")
                st.rerun()
    with zh_col:
        if st.button(
            "中文", key="__lang_zh", use_container_width=True,
            type="primary" if current == "zh" else "secondary",
        ):
            if current != "zh":
                set_lang("zh")
                st.rerun()


# ── Translation tables ───────────────────────────────────────────


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # ── Landing (main.py) ──
        "landing.eyebrow": "Academic research workspace",
        "landing.dek": (
            "Ingest a PDF, get a structured archive. Browse papers, search by "
            "intent, and chat with a multi-agent assistant grounded in your own "
            "library."
        ),
        "landing.backend_unreachable_title": "Backend unreachable.",
        "landing.backend_unreachable_hint": (
            "Start the API in another terminal: <code>paperdb-api</code>"
        ),
        "landing.library_overview": "Library overview",
        "landing.metric.papers": "Papers",
        "landing.metric.sections": "Sections",
        "landing.metric.references": "References",
        "landing.metric.cross_paper_edges": "Cross-paper edges",
        "landing.metric.viewpoints": "Viewpoints",
        "landing.metric.embedded_suffix": "{n} embedded",
        "landing.where_to_go": "Where to go",
        "landing.page.library": "Library",
        "landing.page.library.desc": "Browse every ingested paper. Filter by tag, drill into any row.",
        "landing.page.paper_detail": "Paper Detail",
        "landing.page.paper_detail.desc": "Single-paper view: metadata, LLM analysis, sections, viewpoints, cited references.",
        "landing.page.search": "Search",
        "landing.page.search.desc": "Three-path RAG search with intent weighting (summary / methodology / gap / …).",
        "landing.page.agent_chat": "Agent Chat",
        "landing.page.agent_chat.desc": "Natural-language Q&A. The multi-agent system plans, searches, and cites your library.",
        "landing.page.ingest": "Ingest",
        "landing.page.ingest.desc": "Upload a new PDF. Auto-runs the full pipeline: extract → embed → analyze → archive.",
        "landing.page.settings": "Settings",
        "landing.page.settings.desc": "Backend URL, data paths, and current config.",
        "landing.architecture": "Architecture",
        "landing.architecture.body": (
            "<code>paperdb-api</code> (FastAPI, port 8765) serves the data and "
            "multi-agent logic. This Streamlit app talks to it over HTTP. "
            "Everything lives in one workspace folder (default <code>~/paperdb/</code>): "
            "the SQLite database is the source of truth, and a markdown archive "
            "under <code>&lt;workspace&gt;/archive/</code> mirrors it in human-readable form."
        ),
        "landing.stats_failed": "Failed to fetch stats: {err}",

        # ── Library page ──
        "library.title": "Library",
        "library.eyebrow": "Browse",
        "library.dek": "Every paper currently in your local store. Filter, jump in, drill down.",
        "library.filters": "Filters",
        "library.limit": "Limit",
        "library.tag_filter": "Tag filter",
        "library.only_analyzed": "Only LLM-analyzed",
        "library.only_embedded": "Only embedded",
        "library.count.one": "1 paper",
        "library.count.many": "{n} papers",
        "library.empty": "No papers yet. Go to **Ingest** to add one, or relax filters.",
        "library.load_failed": "Failed to load papers: {err}",
        "library.open": "Open →",
        "library.pill.analyzed": "analyzed",
        "library.pill.not_analyzed": "not analyzed",
        "library.pill.embedded": "embedded",
        "library.pill.no_embed": "no embed",

        # ── Paper Detail page ──
        "paper.title": "Paper detail",
        "paper.eyebrow": "Single paper",
        "paper.dek": "Metadata, LLM analysis, sections, and cited references for one paper.",
        "paper.paper_id": "Paper ID",
        "paper.load_failed": "Failed to load paper {id}: {err}",
        "paper.authors": "Authors",
        "paper.doi": "DOI",
        "paper.pages": "Pages",
        "paper.ingested": "Ingested",
        "paper.tags": "Tags",
        "paper.abstract": "Abstract",
        "paper.llm_analysis": "LLM analysis",
        "paper.not_analyzed": "Not analyzed yet. Run `POST /papers/{{id}}/analyze` or via the agent.",
        "paper.research_field": "Research field",
        "paper.summary": "Summary",
        "paper.methodology": "Methodology",
        "paper.key_findings": "Key findings",
        "paper.sections": "Sections",
        "paper.no_sections": "No sections recorded.",
        "paper.sections_load_failed": "Failed to load sections: {err}",
        "paper.references": "References",
        "paper.no_references": "No references recorded.",
        "paper.references_load_failed": "Failed to load references: {err}",
        "paper.relevance": "relevance",

        # ── Search page ──
        "search.title": "Search",
        "search.eyebrow": "Retrieval",
        "search.dek": (
            "Multi-path RAG over the library — vector + lexical + section-aware "
            "+ viewpoint, fused via RRF. Optional LLM rerank for the final ordering."
        ),
        "search.options": "Options",
        "search.mode": "Mode",
        "search.rerank_toggle": "LLM rerank top results",
        "search.top_k": "Top K",
        "search.filters": "Filters",
        "search.year_from": "Year from (ingested)",
        "search.year_to": "Year to (ingested)",
        "search.tag": "Tag",
        "search.query": "Query",
        "search.query.placeholder": "e.g. EV energy consumption estimation / 电动汽车续航",
        "search.button": "Search",
        "search.running": "Running search…",
        "search.failed": "Search failed: {err}",
        "search.done.summary": "Done · {n} hit(s) · {ms}ms total — {detail}",
        "search.found": "Found {n} hit(s) — mode={mode}, rerank={rerank}",
        "search.no_results": "No results. Try mode='vector' (cross-lingual) or relax filters.",
        "search.stage.embed": "Embed query (Qwen text-embedding-v4)",
        "search.stage.retrieve": "Retrieve · {paths}",
        "search.stage.fuse": "RRF fusion (k=60)",
        "search.stage.rerank": "LLM rerank top candidates",
        "search.open": "Open",

        # ── Agent Chat page ──
        "agent.title": "Agent chat",
        "agent.eyebrow": "Ask",
        "agent.dek": (
            "Natural-language research questions, grounded in your library. "
            "Orchestrator plans → specialists (LitReview · QA · Compare · Gap) "
            "run in parallel → Synth composes the final report with [paper N] citations."
        ),
        "agent.task": "Task",
        "agent.task.help": (
            "`auto` lets the orchestrator pick. Other modes skip the "
            "orchestrator and invoke one specialist directly."
        ),
        "agent.no_synth": "Skip Synth (show raw JSON)",
        "agent.no_synth.help": "Debug mode — show specialist outputs without the synth report.",
        "agent.clear": "Clear chat",
        "agent.question.placeholder": (
            "e.g.  Compare the EV-energy-prediction papers in my library and "
            "identify the most important unsolved problem."
        ),
        "agent.ask": "Ask",
        "agent.running_caption": (
            "⏳ Running in background — you can switch pages and come back; "
            "progress is preserved for this browser session."
        ),
        "agent.status.planning": "Planning research strategy… · {s:.1f}s",
        "agent.status.plan_ready": "Plan ready · dispatching specialists… · {s:.1f}s",
        "agent.status.specialists": "Specialists running · {done}/{total} done · {s:.1f}s",
        "agent.status.composing": "Composing final answer… · {s:.1f}s",
        "agent.status.done": "✓ Done in {s:.1f}s · {n_specs} specialist(s) · {n_calls} tool calls · via {via}",
        "agent.status.error": "Agent error: {msg}",
        "agent.section.plan": "Plan",
        "agent.section.specialists": "Specialists",
        "agent.section.final": "Final answer",
        "agent.synth_instruction": "Synth instruction —",
        "agent.spec.running": "⏳ {name} — running… ({n} tool calls so far)",
        "agent.spec.done": "{mark} {name} — {n_calls} tool calls · {iters} iter",
        "agent.spec.sub_query": "sub-query: {q}",
        "agent.synth.streaming": "⏳ Synth — streaming…",
        "agent.synth.complete": "✓ Synth — complete",
        "agent.synth.thinking": "_(thinking…)_",
        "agent.synth.empty": "No synthesis produced.",
        "agent.error": "Agent error: {msg}",
        "agent.stream_failed": "Streaming failed: {err}",

        # ── Ingest page ──
        "ingest.title": "Ingest",
        "ingest.eyebrow": "Add papers",
        "ingest.dek": "Drop a PDF. The pipeline extracts structure, embeds vectors, and (optionally) runs LLM analysis.",
        "ingest.dropzone": "Drop PDF(s) here",
        "ingest.auto_embed": "Compute embeddings after ingest",
        "ingest.auto_analyze": "Run LLM analyze after ingest",
        "ingest.auto_analyze.help": "Adds 30-120s per paper. Can also be done later.",
        "ingest.button": "Ingest {n} file(s)",
        "ingest.processing": "Processing {name} ({i}/{total})…",
        "ingest.analyzing": "Analyzing paper {id}…",
        "ingest.analyze_failed": "  · analyze failed: {err}",
        "ingest.skipped": "~ [{id}] {title} (duplicate, skipped)",
        "ingest.failed": "✗ {name} — {err}",
        "ingest.done_summary": "Done. success={ok}  skipped={skip}  failed={fail}",
        "ingest.go_to_library": "Go to Library →",

        # ── Settings page ──
        "settings.title": "Settings",
        "settings.eyebrow": "Configuration",
        "settings.dek": "Backend URL, environment variables, and runtime metadata. Read-only.",
        "settings.frontend": "Frontend",
        "settings.frontend_hint": "Change with: `paperdb-ui --api-url http://<host>:<port>` or env var.",
        "settings.backend": "Backend (live)",
        "settings.backend_failed": "Could not query backend: {err}",
        "settings.llm_env": "LLM environment",
        "settings.llm_env.col_var": "Variable",
        "settings.llm_env.col_val": "Value",
        "settings.llm_env.unset": "(unset)",
        "settings.llm_env.set": "(set)",
        "settings.llm_env.caption": (
            "These are read at backend startup. To change them, edit your shell "
            "environment (or a TOML config at ~/.config/paperdb/config.toml) "
            "and restart `paperdb-api`."
        ),
    },

    "zh": {
        # ── 首页 ──
        "landing.eyebrow": "学术研究工作台",
        "landing.dek": (
            "导入 PDF 自动生成结构化档案。浏览文献、按意图检索、"
            "通过多 Agent 助手对你本地的库进行问答。"
        ),
        "landing.backend_unreachable_title": "后端不可达。",
        "landing.backend_unreachable_hint": (
            "请在另一个终端启动 API： <code>paperdb-api</code>"
        ),
        "landing.library_overview": "文献库概览",
        "landing.metric.papers": "论文",
        "landing.metric.sections": "章节",
        "landing.metric.references": "参考文献",
        "landing.metric.cross_paper_edges": "跨论文关系",
        "landing.metric.viewpoints": "观点",
        "landing.metric.embedded_suffix": "已嵌入 {n}",
        "landing.where_to_go": "去哪里",
        "landing.page.library": "文献库",
        "landing.page.library.desc": "浏览所有已导入的论文。可按标签过滤、点入详情。",
        "landing.page.paper_detail": "论文详情",
        "landing.page.paper_detail.desc": "单篇论文视图：元数据、LLM 分析、章节、观点、引用文献。",
        "landing.page.search": "检索",
        "landing.page.search.desc": "三路 RAG 检索，按意图加权（综述 / 方法 / 缺口 / …）。",
        "landing.page.agent_chat": "Agent 对话",
        "landing.page.agent_chat.desc": "自然语言问答。多 Agent 系统规划、检索并引用你的文献。",
        "landing.page.ingest": "导入",
        "landing.page.ingest.desc": "上传 PDF，自动执行完整流水线：提取 → 嵌入 → 分析 → 归档。",
        "landing.page.settings": "设置",
        "landing.page.settings.desc": "后端 URL、数据路径与当前配置。",
        "landing.architecture": "架构",
        "landing.architecture.body": (
            "<code>paperdb-api</code>（FastAPI，端口 8765）提供数据与多 Agent 逻辑。"
            "本 Streamlit 应用通过 HTTP 与之通信。"
            "所有数据集中在一个 workspace 目录（默认 <code>~/paperdb/</code>）：SQLite 文件是权威存储，"
            "<code>&lt;workspace&gt;/archive/</code> 下还有一份 Markdown 归档可供人读。"
        ),
        "landing.stats_failed": "拉取统计失败：{err}",

        # ── 文献库 ──
        "library.title": "文献库",
        "library.eyebrow": "浏览",
        "library.dek": "本地存储中的所有论文。可过滤、跳转、深入查看。",
        "library.filters": "过滤",
        "library.limit": "数量上限",
        "library.tag_filter": "标签过滤",
        "library.only_analyzed": "仅显示已 LLM 分析",
        "library.only_embedded": "仅显示已嵌入",
        "library.count.one": "1 篇",
        "library.count.many": "{n} 篇",
        "library.empty": "暂无论文。请到 **导入** 页添加,或放宽过滤条件。",
        "library.load_failed": "加载论文失败：{err}",
        "library.open": "打开 →",
        "library.pill.analyzed": "已分析",
        "library.pill.not_analyzed": "未分析",
        "library.pill.embedded": "已嵌入",
        "library.pill.no_embed": "未嵌入",

        # ── 论文详情 ──
        "paper.title": "论文详情",
        "paper.eyebrow": "单篇论文",
        "paper.dek": "单篇论文的元数据、LLM 分析、章节与引用。",
        "paper.paper_id": "论文 ID",
        "paper.load_failed": "加载论文 {id} 失败：{err}",
        "paper.authors": "作者",
        "paper.doi": "DOI",
        "paper.pages": "页数",
        "paper.ingested": "导入时间",
        "paper.tags": "标签",
        "paper.abstract": "摘要",
        "paper.llm_analysis": "LLM 分析",
        "paper.not_analyzed": "尚未分析。可执行 `POST /papers/{{id}}/analyze` 或通过 Agent 触发。",
        "paper.research_field": "研究领域",
        "paper.summary": "概要",
        "paper.methodology": "方法",
        "paper.key_findings": "关键发现",
        "paper.sections": "章节",
        "paper.no_sections": "未记录章节。",
        "paper.sections_load_failed": "加载章节失败：{err}",
        "paper.references": "参考文献",
        "paper.no_references": "未记录参考文献。",
        "paper.references_load_failed": "加载参考文献失败：{err}",
        "paper.relevance": "相关度",

        # ── 检索 ──
        "search.title": "检索",
        "search.eyebrow": "Retrieval",
        "search.dek": (
            "对本地文献库的多路 RAG 检索 —— 向量 + 词法 + 章节感知 + 观点，"
            "通过 RRF 融合。可选 LLM 重排得到最终顺序。"
        ),
        "search.options": "选项",
        "search.mode": "模式",
        "search.rerank_toggle": "LLM 重排 Top 结果",
        "search.top_k": "Top K",
        "search.filters": "过滤",
        "search.year_from": "起始年份（导入时）",
        "search.year_to": "终止年份（导入时）",
        "search.tag": "标签",
        "search.query": "查询",
        "search.query.placeholder": "如：EV energy consumption estimation / 电动汽车续航",
        "search.button": "检索",
        "search.running": "正在检索…",
        "search.failed": "检索失败：{err}",
        "search.done.summary": "完成 · {n} 条命中 · 总耗时 {ms}ms — {detail}",
        "search.found": "命中 {n} 条 — 模式={mode}, 重排={rerank}",
        "search.no_results": "无结果。可尝试 mode='vector'（跨语言）或放宽过滤。",
        "search.stage.embed": "嵌入查询（Qwen text-embedding-v4）",
        "search.stage.retrieve": "召回 · {paths}",
        "search.stage.fuse": "RRF 融合（k=60）",
        "search.stage.rerank": "LLM 对候选重排",
        "search.open": "打开",

        # ── Agent 对话 ──
        "agent.title": "Agent 对话",
        "agent.eyebrow": "提问",
        "agent.dek": (
            "基于你本地文献库的自然语言问答。"
            "编排器规划 → 专家 (LitReview · QA · Compare · Gap) 并行运行 → "
            "Synth 整合输出带 [paper N] 引用的报告。"
        ),
        "agent.task": "任务",
        "agent.task.help": (
            "`auto` 由编排器自动选择。其他模式跳过编排器、"
            "直接调用单个专家。"
        ),
        "agent.no_synth": "跳过 Synth（显示原始 JSON）",
        "agent.no_synth.help": "调试模式 —— 显示专家输出，不做整合。",
        "agent.clear": "清空对话",
        "agent.question.placeholder": (
            "例：对比我库中关于电动车续航预测的论文，找出最重要的待解决问题。"
        ),
        "agent.ask": "提问",
        "agent.running_caption": (
            "⏳ 后台运行中 —— 你可以切到其他页面再回来；"
            "进度在当前浏览器会话内会保留。"
        ),
        "agent.status.planning": "正在规划研究策略… · {s:.1f}s",
        "agent.status.plan_ready": "规划完成 · 正在分派专家… · {s:.1f}s",
        "agent.status.specialists": "专家执行中 · 完成 {done}/{total} · {s:.1f}s",
        "agent.status.composing": "正在生成最终答案… · {s:.1f}s",
        "agent.status.done": "✓ 完成，用时 {s:.1f}s · 专家 {n_specs} 个 · 工具调用 {n_calls} 次 · via {via}",
        "agent.status.error": "Agent 错误：{msg}",
        "agent.section.plan": "规划",
        "agent.section.specialists": "专家",
        "agent.section.final": "最终答案",
        "agent.synth_instruction": "Synth 指令 —",
        "agent.spec.running": "⏳ {name} — 运行中…（已调用 {n} 次工具）",
        "agent.spec.done": "{mark} {name} — 工具调用 {n_calls} 次 · 迭代 {iters} 次",
        "agent.spec.sub_query": "子查询：{q}",
        "agent.synth.streaming": "⏳ Synth — 流式输出中…",
        "agent.synth.complete": "✓ Synth — 完成",
        "agent.synth.thinking": "_(思考中…)_",
        "agent.synth.empty": "未生成整合答案。",
        "agent.error": "Agent 错误：{msg}",
        "agent.stream_failed": "流式传输失败：{err}",

        # ── 导入 ──
        "ingest.title": "导入",
        "ingest.eyebrow": "添加论文",
        "ingest.dek": "上传 PDF。流水线会自动提取结构、计算向量嵌入,并可选地执行 LLM 分析。",
        "ingest.dropzone": "拖入 PDF 文件",
        "ingest.auto_embed": "导入后计算嵌入",
        "ingest.auto_analyze": "导入后执行 LLM 分析",
        "ingest.auto_analyze.help": "每篇额外 30-120 秒。也可稍后单独执行。",
        "ingest.button": "导入 {n} 个文件",
        "ingest.processing": "处理中 {name} ({i}/{total})…",
        "ingest.analyzing": "分析论文 {id} 中…",
        "ingest.analyze_failed": "  · 分析失败：{err}",
        "ingest.skipped": "~ [{id}] {title}（重复，已跳过）",
        "ingest.failed": "✗ {name} — {err}",
        "ingest.done_summary": "完成。成功={ok}  跳过={skip}  失败={fail}",
        "ingest.go_to_library": "前往文献库 →",

        # ── 设置 ──
        "settings.title": "设置",
        "settings.eyebrow": "配置",
        "settings.dek": "后端 URL、环境变量与运行时元数据。只读。",
        "settings.frontend": "前端",
        "settings.frontend_hint": "可用： `paperdb-ui --api-url http://<host>:<port>` 或环境变量修改。",
        "settings.backend": "后端（实时）",
        "settings.backend_failed": "无法连接后端：{err}",
        "settings.llm_env": "LLM 环境变量",
        "settings.llm_env.col_var": "变量",
        "settings.llm_env.col_val": "取值",
        "settings.llm_env.unset": "（未设置）",
        "settings.llm_env.set": "（已设置）",
        "settings.llm_env.caption": (
            "这些值在后端启动时读取。如需修改，请编辑 shell 环境变量"
            "（或 ~/.config/paperdb/config.toml 配置文件），然后重启 `paperdb-api`。"
        ),
    },
}
