# paper-rag-agent

[English](README.md) · **简体中文**

> 本地运行的学术论文知识库，自带 LLM 分析、多路召回检索，以及原生支持 Claude Code 的多智能体助手。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-D97757)](https://claude.com/claude-code)

`paper-rag-agent`（包名：`paperdb`）能把 PDF 论文吸纳进本地库 — 抽取结构化内容（章节、引用、参考文献、表格、公式、图）、跑 LLM 分析、生成向量索引，并通过多智能体编排回答你对整个文献库的问题。一切都在本地、用你自己的 API key 跑，没有 SaaS，没有遥测。

---

## 为什么用它

- **版式感知的 PDF 解析** — PyMuPDF 快速路径 + 可选 [MinerU](https://github.com/opendatalab/MinerU) 后端，扫描版 PDF / 公式 → LaTeX / 表格 → HTML/Markdown 全覆盖
- **三层检索** — 向量召回 + BM25 + RRF 融合 + LLM 重排
- **多智能体问答** — 编排器并行派发"文献综述/事实问答/对比/缺口分析"四个 specialist
- **跨论文分析** — 自动发现共享引用，LLM 标注论文对关系
- **知识图谱** — 全库范围的实体/主题图，agent 可查询
- **单一目录工作区** — `cd ~/paperdb` 一切都在：数据库 + PDF + Markdown 归档 + 配置
- **Claude Code 原生** — 自带 slash 命令（`/paper-ingest`、`/paper-search`、`/paper-agent`）和 subagent，开箱即用

---

## 快速开始

```bash
# 1. 安装
pip install -e ".[api,ui]"

# 2. 创建工作区（默认 ~/paperdb/）— 会落地一份 config.toml 模板
paperdb init

# 3. 填 API key
$EDITOR ~/paperdb/config.toml          # 填上 llm.api_key 和 embedding.api_key

# 4. 吸纳论文
paperdb ingest path/to/paper.pdf

# 5. 启动 Web UI（API 在 :8765，Streamlit 在 :8501）
paperdb start
```

完事。打开 <http://localhost:8501> 就能开始对你的文献库提问。

---

## 配置

### 一个文件，一个地点

所有 API key 和参数都在**一个文件**：`<workspace>/config.toml`。

| 工作区 | config 文件位置 |
|---|---|
| 默认（`~/paperdb/`） | `~/paperdb/config.toml` |
| 自定义（`paperdb init ~/my-papers`） | `~/my-papers/config.toml` |
| Docker / CI | `$PAPERDB_WORKSPACE/config.toml` |

随时查看 paperdb 正在读哪个 config：

```bash
paperdb workspace show
```

### 必填的 key

两个 provider，两个 key（完整带注释的模板见 [`config.toml.example`](./config.toml.example)）：

| Key | 在哪申请 | 用途 |
|---|---|---|
| `llm.api_key` | [platform.deepseek.com](https://platform.deepseek.com/api_keys)（或 OpenAI / 任意 OpenAI 兼容 endpoint） | 论文摘要、观点抽取、重排、agent 推理 |
| `embedding.api_key` | [dashscope.aliyuncs.com](https://dashscope.console.aliyun.com/apiKey) | Qwen text-embedding-v4 向量检索 |

### 环境变量（Docker / CI 替代方案）

如果你喜欢用环境变量（Docker、K8s、CI 场景），把 `.env.example` 复制成 `.env`，再用你顺手的工具加载（`direnv`、`docker-compose env_file`、systemd `EnvironmentFile`）。**真实环境变量优先级最高，永远覆盖 `config.toml`**。

---

## 工作区结构

```
~/paperdb/
├── config.toml             ← 你的 API key + 参数（被 .gitignore 屏蔽）
├── papers.db               ← SQLite 数据库（唯一权威数据源）
├── paperdb.workspace       ← 标记文件（让 cd 时祖先目录能自动找到）
├── pdfs/                   ← 你吸纳过的原始 PDF
├── archive/                ← 派生的 per-paper markdown（Obsidian 兼容）
└── cache/                  ← MinerU 临时输出，可重生成
```

**为什么这样设计**：整个文献库就一个文件夹。`tar` 备份、`rsync` 迁移、新机器一句 `export PAPERDB_WORKSPACE=...` 即可指向。没有数据散落在 OS 各种"应用支持目录"里。

### 从旧版本升级

如果你用过旧版 paperdb（数据散在 `~/Library/Application Support/paperdb/`、`~/.config/paperdb/`、`~/knowledge-base/archive/`），一句话搬过来：

```bash
paperdb workspace migrate
# → 显示移动计划 → 问你确认 → 把老目录改名成 *.backup-YYYY-MM-DD
```

你现有的 config.toml（含 API key）和 `papers.db` 都会保留，不会删任何东西，只是改名。

---

## CLI 速查

```bash
# 工作区
paperdb init [path]                 # 创建新工作区
paperdb workspace show              # 我现在读的是哪个工作区？
paperdb workspace migrate           # 合并旧 OS 目录的数据

# 吸纳论文
paperdb ingest paper.pdf            # 抽取 + 向量 + LLM 分析 + 归档（一条龙）
paperdb ingest ./papers/ -r         # 递归吸纳目录
paperdb ingest paper.pdf --backend mineru   # 强制 MinerU（扫描版/复杂版式）
paperdb ingest paper.pdf --backend auto     # 自动嗅探扫描版 vs 数字版
paperdb ingest paper.pdf --quick    # 只抽取，不跑 LLM

# 查看
paperdb list [--tag energy]
paperdb show 42 --sections --references --citations --llm
paperdb stats

# 检索
paperdb search "EV charging" --field title
paperdb search-rag "EV charging" --rerank --explain

# 多智能体问答
paperdb agent "我的库里关于锂电池退化的论文都讲了什么？"
paperdb agent "对比 paper 12 和 17 的方法学" --task compare

# 跨论文分析
paperdb cross-analyze all          # 检测共享引用 + LLM 标注关系

# Web UI
paperdb start                       # API + Streamlit 一起启
paperdb start --host 0.0.0.0        # 局域网可访问
```

---

## 可选：MinerU 后端处理扫描版

默认 PyMuPDF 后端解析速度 ~1 秒/篇，但只能处理 born-digital PDF。如果要处理扫描版、公式、表格，装可选的 [MinerU](https://github.com/opendatalab/MinerU)：

```bash
pip install -e ".[mineru]"
# 然后自动路由就生效了：
paperdb ingest scanned-paper.pdf --backend auto
```

MinerU 本地跑 DocLayout-YOLO + UniMERNet + RapidTable 几个模型（首次约 2 GB 模型下载）。完整管线见 [`docs/architecture.md`](./docs/architecture.md)。

---

## Claude Code 集成

如果你用 [Claude Code](https://claude.com/claude-code)，这个 repo 自带原生 slash 命令和 subagent（在 `.claude/` 下）：

| Slash 命令 | 用途 |
|---|---|
| `/paper-ingest <pdf>` | 一键吸纳管线 |
| `/paper-search <query>` | 混合检索，可选重排 |
| `/paper-agent <question>` | 多智能体回答，带引用 |
| `/paper-stats` | 文献库健康快照 |

| Subagent | 适用场景 |
|---|---|
| `paper-lit-reviewer` | 跨论文主题综述 |
| `paper-qa` | 聚焦事实/数值问题 |
| `paper-comparator` | 多论文对齐表 |
| `paper-gap-analyst` | 未解问题 / 矛盾 |
| `paper-librarian` | 吸纳 / 分析 / 维护 |

`cd` 进 repo（或任何有 workspace 标记的目录）— Claude Code 自动加载它们。

---

## 架构

```
                     CLI / REST API / Streamlit UI
                                │
                       ┌────────┴─────────┐
                       │  BatchProcessor   │
                       └────────┬─────────┘
                                │
       ┌────────────────────────┼────────────────────────┐
       ▼                        ▼                        ▼
  PaperExtractor          PaperEmbedder            PaperAnalyzer
  (pymupdf | mineru)      (Qwen v4, 1024D)         (DeepSeek 双层)
       │                        │                        │
       └────────────────────────┴────────────────────────┘
                                │
                       ┌────────┴─────────┐
                       │  SQLite (WAL)     │
                       │  - papers          │
                       │  - sections + chunks
                       │  - references_     │
                       │  - citation_locations
                       │  - section_tables / formulas / images
                       │  - shared_citations  (跨论文)
                       │  - kg_nodes / kg_edges (图)
                       │  - FTS5 索引
                       └────────┬─────────┘
                                │
                       ┌────────┴─────────┐
                       │  多智能体编排    │
                       │  (DeepSeek pro    │
                       │   + thinking)    │
                       └──────────────────┘
```

详见 [`docs/architecture.md`](./docs/architecture.md)。

---

## 状态 & 路线图

✅ 已实现：SQLite 存储、LLM 分析、向量检索（Qwen v4）、三层 RAG、跨论文关系、多智能体助手、REST API + Streamlit UI、MinerU 可选后端、统一工作区

🚧 路线图：Docker 容器、NAS 部署指南（Synology / QNAP）、webhook 触发吸纳、Obsidian 插件

---

## 贡献

欢迎 PR。开发流程见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

---

## License

[MIT](./LICENSE)

---

## 致谢

- [DeepSeek](https://www.deepseek.com/) — 主力 LLM provider，支持 `thinking` 模式
- [Qwen](https://qwenlm.github.io/) — 阿里 DashScope 提供的 `text-embedding-v4`
- [MinerU](https://github.com/opendatalab/MinerU) — 版式感知 PDF 解析
- [PyMuPDF](https://pymupdf.readthedocs.io/) — 高速 PDF 文本抽取
- [Claude Code](https://claude.com/claude-code) — Anthropic 出品的 CLI agent，本项目主要由它写就
