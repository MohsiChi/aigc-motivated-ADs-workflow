# PRD：AIGC Multi-Agent 视频生产 Workflow

| 文档 | 内容 |
|---|---|
| 版本 | v1.0（MVP 范围） |
| 日期 | 2026-09-04 |
| 状态 | 已定稿，开发中 |
| 关联 | `02-架构设计.md`、`03-业界方案调研.md`、`dev-log.md` |

---

## 1. 背景与目标

### 1.1 背景

本项目源于真实的电商 AIGC 内容生产需求：把"用 AI 生成视频"从人肉单次作业，变成**状态机驱动、可调度、多 Agent 协作、可重试可观测**的生产系统。

项目作者已有真实的人工 AIGC 视频生产经验（蛋壳黑陶纪录短片：选题→文案→关键帧→图生视频→逐段质检返工→剪辑成片）。本项目把这条人工流水线**重新工程化**为 Multi-Agent Workflow——不虚构经历，而是把做过的事系统化。

### 1.2 一句话目标

> 输入一句话商品主题（如"给这双鞋子做个广告"），系统自动跑完「编剧→提示词→素材生成→质检」Multi-Agent 流水线，产出一份带分镜脚本、逐镜生成提示词、素材清单与质检报告的短视频广告任务结果。

### 1.3 项目要证明的三件事

1. **能用 AI 跑通项目**：可运行的代码 + 工程文档 + 清晰的 git 历史，全程由 AI 辅助开发并留痕。
2. **核心工程关键词逐条落地**：任务状态机、任务调度、Multi-Agent、Sub-Agent 组件化、Tool Calling、Workflow 编排、Prompt Engineering。
3. **工程素养**：零成本可演示（Mock 模式）、有测试、有日志留痕、密钥不进代码。

## 2. 用户故事

- **US-1**：作为"商家运营"，我 `POST /api/v1/tasks` 提交主题与商品信息，系统**立即**返回 `task_id`（HTTP 202），不阻塞等待生成。
- **US-2**：我轮询 `GET /api/v1/tasks/{task_id}`，能看到任务当前所在步骤（script/prompt/generation/review）、状态与已产出的中间物料。
- **US-3**：任务完成后，我能拿到完整产出：分镜脚本、逐镜生成提示词、素材清单（含文件路径）、质检报告、镜头级状态。
- **US-4**：某镜头质检不通过时，系统自动有限次重试（携带质检原因返工），重试过程全程留痕；重试耗尽不崩溃、有明确的降级策略与告警记录。

## 3. 范围

### 3.1 v1 范围内（必须做）

| 模块 | 说明 | 对应能力点 |
|---|---|---|
| 任务 API | FastAPI：提交即返回、轮询查询 | 任务调度系统（接口形态） |
| 任务状态机 | PENDING→RUNNING→SUCCEEDED/FAILED，非法流转拒绝 | 任务状态机流转 |
| 镜头级状态机 | 每个镜头独立状态 + 重试 | 状态机、失败处理 |
| Workflow 引擎 | 进程内队列 + Worker，顺序编排四步流水线 | 任务调度、Workflow 编排 |
| 5 个 Sub-Agent | script / prompt / generation / review / compose，统一接口、可插拔 | Multi-Agent、组件化 |
| Tool Calling | 生成类工具以 JSON Schema 注册，LLM 决策 + 程序执行 | Tool Calling |
| 质检与重试 | 规则化质量门禁 + 有限次返工 | 质量门禁、重试逻辑 |
| 剪辑合成 | Compose Agent：关键帧 + 字幕 + 运镜 -> 真实可播放 MP4（ffmpeg，零成本） | 视频业务链路落地 |
| 持久化 | SQLite（stdlib）：任务/步骤/事件三表留痕 | 可观测、审计 |
| 测试 | pytest 覆盖状态机、流水线、API | 工程质量 |

### 3.2 v1 明确不做（写出来防止范围蔓延）

- ❌ Redis / 消息队列 / Celery：接口与队列抽象已预留，v2 再换（理由见 `02-架构设计.md` 决策 D1）
- ❌ 真实即梦/火山视频生成 API：v1 用 Mock Provider，适配器接口已留好
- ~~真实剪辑合成~~ → **已在 Session 2 落地（Compose Agent，ffmpeg 零成本成片）**
- ❌ 向量数据库 RAG：v2 议题
- ❌ Docker / 部署 / CI：v2 议题

## 4. 功能需求

- **FR-1 任务提交**：`POST /api/v1/tasks`，入参 `{topic, product_info?, scene_count?}`；校验后落库（PENDING），入队，返回 `task_id`。
- **FR-2 任务查询**：`GET /api/v1/tasks/{task_id}` 返回状态、当前步骤、错误、告警、全部 artifacts、步骤历史、镜头级状态；不存在返回 404。
- **FR-3 流水线**：script → prompt → generation → review 顺序执行；任一步失败则任务 FAILED 并记录错误；步骤级输入输出全部落库。
- **FR-4 质检重试**：质检不通过的镜头自动返工（最多 `MAX_SCENE_RETRIES` 次，默认 2）；返工携带质检原因；重试耗尽后镜头标记 `ACCEPTED_FALLBACK`（降级采用）并产生告警，任务仍可完成。
- **FR-5 留痕**：任务生命周期事件（创建/开始/重试/完成/失败）写入 `task_events` 表；运行日志写文件。
- **FR-6 Mock 模式**：`MOCK_LLM=1`（默认）时全流程零外部调用、输出确定可复现；`MOCK_LLM=0` 走真实 DeepSeek。
- **FR-7 成片输出**：无论何种 Provider，任务完成后必须产出真实可播放的 `final.mp4`（字幕 + 运镜 + 拼接）；Provider 已产出真实镜头 MP4 时，剪辑自动切换为纯拼接模式。
- **FR-8 规范化产物目录**：每个任务一个目录，固定布局 00 元信息 / 01 脚本 / 02 提示词 / 03 关键帧 / 04 片段 / 05 质检 / 06 成片；脚本与提示词同时提供 JSON（机器读）与 MD（人读）两种形态。

## 5. 非功能需求

- **NFR-1 零成本可运行**：默认配置下不需要任何付费 API 即可端到端演示（零成本演示的兜底）。
- **NFR-2 可测试**：状态机、流水线（含重试路径）、API 均有自动化测试。
- **NFR-3 密钥安全**：真实 API Key 只存在于本地 `.env`（已 gitignore），代码一律 `os.getenv` 读取。
- **NFR-4 幂等与容错**：同一任务不会被重复执行；Worker 内未捕获异常不会杀死调度线程。

## 6. 验收标准（AC）

1. `POST /api/v1/tasks` 返回 202 + `task_id`；随后 `GET` 可查到任务。
2. 状态机只允许合法流转，非法流转抛出 `IllegalTransitionError`（有测试证明）。
3. Mock 模式下全流程跑完：任务 SUCCEEDED，artifacts 含 `storyboard / prompts / generation / review_report / scene_states` 五件套，素材文件真实落盘。
4. 构造"质检不通过"场景：能观察到重试记录（步骤 attempt>1、事件表留痕），重试耗尽后出现 `ACCEPTED_FALLBACK` 与告警，任务仍 SUCCEEDED。
5. `pytest` 全绿。
6. git 提交历史清晰；`docs/` 下 PRD/架构/调研/开发日志齐备。
7. 全真实模式（真实 LLM + 免费真实图片源）端到端产出可播放成片（已验证：任务 `283e1b1bf1fd`）。

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| DeepSeek API 不稳定/欠费 | 真实模式跑不通 | Mock 模式兜底；LLM 层已抽象可换模型 |
| 本机 Python 环境缺失 | 无法运行 | 环境安装写入 `dev-log.md`，requirements 固定 |
| 范围膨胀（想加 RAG/Docker） | 交付延期 | 3.2 节白名单制，新增一律进 v2 清单 |
| 技术选型被追问 | 架构文档决策 D1 写清了演进路径，能讲清为什么 |
