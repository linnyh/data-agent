<div align="center">

# 数枢 DataPivot

**对话式数据分析 Agent** —— 上传数据，开口提问，自动分析

[![Version](https://img.shields.io/badge/version-0.2.0-22d3ee?style=flat-square)](https://github.com/linnyh/data-agent)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://github.com/linnyh/data-agent)
[![FastAPI](https://img.shields.io/badge/fastapi-0.141%2B-009688?style=flat-square&logo=fastapi)](https://github.com/linnyh/data-agent)
[![LangGraph](https://img.shields.io/badge/langgraph-1.2%2B-ff6b00?style=flat-square)](https://github.com/linnyh/data-agent)
[![React](https://img.shields.io/badge/react-18-61dafb?style=flat-square&logo=react)](https://github.com/linnyh/data-agent)
[![Vite](https://img.shields.io/badge/vite-6-646cff?style=flat-square&logo=vite)](https://github.com/linnyh/data-agent)
[![CI](https://github.com/linnyh/data-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/linnyh/data-agent/actions/workflows/ci.yml)
[![Stars](https://img.shields.io/github/stars/linnyh/data-agent?style=social)](https://github.com/linnyh/data-agent)

</div>

基于 **LangGraph** 的对话式数据分析服务：用户上传数据（csv/json/sqlite/文档/视频简报）
→ 与 Agent 多轮对话澄清需求 → Agent 全自动执行分析（结构化 SQL + 文档抽取 + 视频多模态
三条链）→ 叙述解读 + 结构化表格 + 可视化图表 → 追问迭代。

## ✨ 特性

- 🖥 **macOS 桌面 App**：DataPivot.app 双击即用（Tauri 壳 + Python sidecar，whisper/ffmpeg 内置离线），原生标题栏、系统亮暗跟随、侧边栏布局
- ⚙️ **App 内配置**：设置页编辑模型 API/视频模型/LangSmith 配置，保存立即生效
- 💡 **快捷指令**：上传文件后按列结构自动生成分析建议（点击填入输入框），「换一批」耗尽规则池后由模型增强
- 🤖 **对话式分析**：自然语言提问，Agent 自动澄清歧义，全自动执行分析
- 📊 **多模态数据源**：结构化数据（csv/json/sqlite）+ 文档（md/pdf）+ 视频简报，统一注册进 DuckDB 只读查询
- 🧭 **先规划后求解**：求解前由规划 Agent 带只读探查实查口径/量纲/去重假设，产出解题规划供 solver 参考
- ⚖️ **去重口径判定**：投票判定最终结果是否应按目标输出列去重，建议并入解题规划注入 solver
- 🎯 **输出形态预测**：抽取强对齐 SQL 案例与字段约束，预测输出列/行数上限/任务类型（摇摆原则宁多勿漏），与规划、去重建议并发产出
- 🔧 **ReAct 求解**：只读 SQL 探查 + 沙箱代码执行，失败自动重试，兜底直跑
- 📈 **自动图表**：解读结果时自动附图（bar/line/pie/scatter），霓虹主题、导出 PNG
- 💬 **多轮记忆**：会话历史注入，支持追问迭代与口径重算
- 🏠 **本地单用户**：无登录流程，打开即用（所有会话归属本地用户）
- ⚡ **节点级进度**：SSE 实时推送执行阶段；可选开启执行轨迹（`DATA_AGENT_DEBUG_TRACE=1`）——实时展示每个节点的输入/输出与 solve 内部工具调用，结果气泡可折叠审计，历史回放可恢复
- 🎨 **明暗主题**：macOS 语义色（深色/浅色），跟随系统，localStorage 持久化

## 🖥 界面预览

<p align="center">
  <img src="img/example-dark.png" width="45%" alt="深色主题界面" />
  <img src="img/example-light.png" width="45%" alt="浅色主题界面" />
</p>

## 🚀 快速开始

### macOS 桌面 App（双击即用）

```bash
./desktop/scripts/build-sidecar.sh   # 1. PyInstaller 打包 Python 服务（含 whisper/ffmpeg/前端）
./desktop/scripts/build-app.sh       # 2. Tauri 构建 DataPivot.app（ad-hoc 签名）
./desktop/scripts/make-dmg.sh        # 3. 可选：生成 dmg
```

- 双击 `DataPivot.app` 即用：自动拉起本地服务（端口 8765 起自动避让），窗口加载同源前端
- 首次启动生成 `~/Library/Application Support/DataPivot/`（数据目录 + `.env` 配置模板）
- 模型配置在 App 内设置页（右上角齿轮）填写，保存立即生效
- 验收脚本：`./desktop/scripts/e2e-m1.sh`（完整跑通一次上传分析）

### 生产模式（单端口：前端 + API 同源）

```bash
uv sync
cd web && npm install && npm run build && cd ..

# 配置写在 .env（已 gitignore）：
#   模型：MODEL_API_URL / MODEL_API_KEY / MODEL_NAME（必填）
#   服务：DATA_AGENT_PORT（默认 8000）/ DATA_AGENT_DATA_DIR（默认 ./data）
python -m data_agent.infrastructure.main    # 浏览器打开 http://localhost:8000
```

### 开发模式（前端热更新）

```bash
python -m data_agent.infrastructure.main    # 终端 1：后端 8000
cd web && npm run dev                       # 终端 2：http://localhost:5173（proxy 到 8000）
```

### 终端对话 CLI（无界面）

```bash
uv run python scripts/chat_cli.py --files 数据.csv    # 或进入后 /upload <文件>
```

### Docker 部署（单容器，前端 + API 同源）

```bash
docker build -t datapivot .

docker run -d --name datapivot -p 8000:8000 \
  -v datapivot-data:/app/data \
  -e MODEL_API_URL=https://api.deepseek.com \
  -e MODEL_API_KEY=sk-xxx \
  -e MODEL_NAME=deepseek-v4-flash \
  datapivot
```

- 数据目录 `/app/data`(会话文件/checkpoint/用户库)通过 volume 持久化
- 镜像内已含前端构建产物与视频资产依赖(ffmpeg/whisper),无需额外安装

## 🏗 架构

```
src/data_agent/
├── domain/            # 纯接口与领域模型（零引擎依赖；SOLID 依赖倒置的根）
│   ├── models.py      # Session / Dataset / AnalysisGoal / Result
│   ├── datasource.py  # IDataSourceRegistry / IDataSourceDescriber
│   ├── solver.py      # ISolver / ISolverSandbox / IScaffoldGenerator
│   ├── pipeline.py    # IDocExtractor / IVideoPreprocessor / IVideoResultJudge
│   ├── judges.py      # IRelevanceJudge（doc/表相关性判定）
│   └── llm.py         # ILLM（模型能力窄抽象）
├── application/       # LangGraph 图 = 用例编排
│   ├── state.py       # PipelineState（checkpoint 持久化状态）
│   ├── graph.py       # clarify → 管线(视频→doc→表→规划→求解) → narrate
│   ├── clarify.py     # 歧义检测 + interrupt/resume
│   └── narrate.py     # 结果叙述解读 + 图表规格
├── adapters/          # 依赖倒置的实现
│   ├── duckdb/        # DuckDB 数据源注册/描述（与内置参考实现逐字节对齐）
│   ├── judges/        # 相关性判定（多轮投票 + 召回优先兜底）
│   ├── pipeline/      # 视频/doc/规划/去重判定/输出形态 算法资产 Adapter（模型工厂注入）
│   ├── models.py      # OpenAI 兼容 endpoint（think/nothink 两实例）
│   ├── sandbox.py     # 子进程执行 + 超时 + 内存限制
│   ├── scaffold.py    # solver.py 脚手架生成
│   ├── explore.py     # 只读 SQL 探查工具
│   ├── solver_files.py# hash 定位文件编辑
│   └── solver_agent.py# create_react_agent + attempt 循环
├── assets/            # 内置算法资产（文档结构化引擎、视频预处理组件、评分器等）
└── infrastructure/    # 技术设施
    ├── api/app.py     # FastAPI：sessions/upload/chat(SSE)/result/settings/suggestions
    ├── db.py          # 用户/会话持久化（SQLite）
    ├── storage.py     # 会话目录 + 上传分类落盘 + TTL 清理
    ├── checkpoint.py  # AsyncSqliteSaver（thread_id=会话 ID）
    ├── suggestions.py # 快捷指令生成（规则模板 + LLM 增强）
    ├── container.py   # 依赖注入根
    └── main.py        # 服务入口

web/                  # React 前端（Vite + TS + Tailwind + ECharts）
desktop/              # macOS 桌面壳（Tauri）与 PyInstaller 打包（sidecar 生命周期/端口探测/SIGTERM 清理）
scripts/              # 终端对话 CLI
tests/                # 单测 + 冒烟 + 标注集（benchmark/）
```

依赖方向单向：`application → domain ← adapters`；`infrastructure` 组装一切。

## 🔌 API

| 端点 | 说明 |
|------|------|
| `POST /sessions` | 创建会话（本地单用户模式，无认证） |
| `GET /sessions` | 列出会话（含首问标题） |
| `POST /sessions/{id}/upload` | multipart 上传数据文件（csv/json/sqlite/md/pdf/mp4），按扩展名分类落盘 |
| `GET /sessions/{id}/files` | 列出会话已上传文件（路径相对会话目录） |
| `DELETE /sessions/{id}/files?path=` | 按路径删除已上传文件 |
| `POST /sessions/{id}/chat` | `{question, resume?}` → SSE 流：`clarification`（Agent 提问）/ `progress`（执行阶段）/ `result`（叙述+表格+图表）/ `error`；收到 clarification 后带 `resume` 重调续跑 |
| `GET /sessions/{id}/result` | 最近一次分析结果（从 checkpoint 状态读取） |
| `GET /sessions/{id}/history` | 会话历史：每轮问答记录（含图表规格与执行轨迹；ADR-0005，checkpoint 为事实源） |
| `GET /sessions/{id}/suggestions?offset=` | 快捷指令：按文件列结构生成候选分析目标；offset 超规则池后由模型增强 |
| `GET /settings` / `PUT /settings` | 模型配置读写（.env 白名单键），保存立即生效并落盘 |

本地单用户模式：所有会话归属固定本地用户（首次请求自动创建），无登录流程；会话不存在返回 404。

## 🔭 可观测性（LangSmith）

观察 LangGraph 执行轨迹：节点边界、LLM 每次调用、ReAct 工具调用、token 与延迟。

```bash
# .env 追加（langsmith.com 注册获取 key；数据会上传到 LangSmith 服务器）
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com   # 默认值，可省略；自托管时改写
LANGSMITH_API_KEY=lsv2_xxx
LANGSMITH_PROJECT=data-agent
```

覆盖范围：solver ReAct 循环与对话层（clarify / narrate）自动追踪；
规划 / 去重判定 / 输出形态等 pydantic_ai 资产不在自动追踪内（官方追踪方案为 pydantic_logfire）。

## 🧪 测试

```bash
uv run pytest tests/ -q          # 全量（91 测试）
```

覆盖：领域模型 / DuckDB 与内置参考实现逐字节对齐 / 投票聚合语义 / 求解沙箱三态 /
attempt 循环 / 管线图端到端（fake 注入）/ 对话流（interrupt-resume）/ 规划·去重·输出形态建议注入 /
API 集成与隔离 / 文件列表与删除 / 图表规格下发 / 评分机制。

## 📚 文档

- 术语与领域模型：`CONTEXT.md`
- 架构决策记录：`docs/adr/`（0001 框架选择 / 0002 架构边界 / 0003 认证 / 0004 交互边界 / 0005 历史事实源）
- **设计文档（方案与设计思路）**：`docs/DESIGN.md`
- Agent skills 配置（issue 追踪 / triage 标签 / 域文档约定）：`CLAUDE.md`、`docs/agents/`

## 📊 回归基线

- 离线回归：待离线评测数据与模型 endpoint 就绪后执行分数对标；
- 自建标注集在 `tests/benchmark/`（task_basic / task_filter），评分器实现
  DABench 列签名匹配规范（`src/data_agent/assets/run_compare_to_gt.py`，机制经单测守护）。
