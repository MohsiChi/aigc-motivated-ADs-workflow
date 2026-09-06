# 开发日志（Dev Log）

> 规则：每个工作会话结束前追加一条。记录"做了什么、为什么、证据、下一步"。
> 这份日志是完整的工程过程记录：项目如何被 AI 辅助一步步工程化。

---

## Session 1 — 2026-09-04｜项目规划与框架

**目标**：完成整体规划（PRD/架构/调研）+ 可运行的代码框架 + 测试。

### 做了什么

1. **环境盘点**：通读项目 README 与前期学习笔记；确认 Day 1（Tool Calling）已完成。
2. **发现问题**：`day1_tool_calling.py` 中 `get_date()` 只有函数头没有函数体（语法错误），已补全修复。
3. **业界调研**：检索成熟开源方案（MoneyPrinterTurbo / ShortGPT / ArcReel / AI_Video_studio / Pixelle-Video / LangGraph），产出 `docs/03-业界方案调研.md`。
4. **工程文档**：产出 `docs/01-PRD.md`（范围/用户故事/验收标准）、`docs/02-架构设计.md`（分层架构、双层状态机、数据模型、Agent 契约、6 条设计决策、v2 路线）。
5. **代码框架**：按架构文档实现全套骨架（详见提交记录）：配置、双层状态机、SQLite 任务存储、工作流引擎（含质检重试循环）、4 个 Agent、Tool 注册表、Mock/即梦 Provider、FastAPI 应用、pytest 测试。
6. **仓库初始化**：`git init` + baseline 提交（6ef6e4a），`.env` 确认被 gitignore 排除。
7. **环境事件**：发现本机 Python 3.12 已被卸载（仅剩 MinGW 的 Python 2.7.9，不可用；本机其他旧虚拟环境也因此失效）。已安装/将安装 Python 环境（见证据节）。

### 关键决策

- 沿用 README 已定的范围决策（不引入 Redis/向量库/分布式），并把"为什么"写进架构文档决策 D1–D6。
- 默认 `MOCK_LLM=1`：零成本、确定可复现，真实模式需显式开启。
- 质检不通过的镜头：有限重试 → 降级采用（ACCEPTED_FALLBACK）+ 告警，不阻塞整任务。

### 证据

- **pytest**：`16 passed, 1 warning in 2.56s`（状态机 7 / 流水线 4 / API 5），API 套件复跑一轮仍 5 passed（无偶发）。
- **端到端演示（真实 HTTP 服务，MOCK 模式）**：
  - `POST /api/v1/tasks {"topic": "给这双鞋子做个广告", "product_info": "轻便跑鞋，透气网面，缓震中底", "scene_count": 3}` → `202`，`task_id=cdbe5eea4d81`
  - 约 300ms 后 `GET /api/v1/tasks/cdbe5eea4d81` → `SUCCEEDED`；artifacts 五件套齐全（storyboard/prompts/generation/review_report/scene_states），3 镜全部 `ACCEPTED`，steps 4 条、events 3 条。
  - 素材 sidecar 文件真实落盘：`outputs/cdbe5eea4d81/scene_0{1,2,3}_{keyframe,clip}.json`
- **过程中发现并修复的 bug（留痕）**：
  1. `day1_tool_calling.py` 的 `get_date()` 空函数体语法错误（补全）。
  2. 竞态：任务状态先于 `scene_states` 产物写库，轮询方可能读到"已 SUCCEEDED 但产物未齐"。修复契约：**状态最后写**（`app/core/workflow.py`）。

### 环境记录

- 本机原 Python 3.12 已被卸载（本机旧虚拟环境因此失效），仅剩 MinGW Python 2.7.9。
- 新装 **Python 3.13.15**（官方安装包，per-user，PrependPath；python.org 直连超时，改用 npmmirror CDN 下载成功）。
- 项目虚拟环境：`aigc-agent-workflow\.venv`；依赖安装：清华源索引异常（fastapi 查无版本），改用阿里云源成功。
- 测试/运行命令（PowerShell）：
  - `.venv\Scripts\python.exe -m pytest`
  - `.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

### 下一步

- [x] 安装 Python 环境并跑绿测试（Python 3.13.15 + .venv + pytest 16/16）
- [x] 端到端 HTTP 演示（见证据节）
- [ ] `MOCK_LLM=0` 真实 DeepSeek 冒烟（花一分钱验证真实链路）
- [ ] v2-1：JimengProvider 接火山引擎即梦（需 API Key 决策）
- [ ] v2-2：Redis 队列 / RAG / 剪辑 Agent（按 PRD 路线）

### 待用户决策（不阻塞）

1. ~~真实模式冒烟是否现在做~~ → 已在 Session 2 完成（见下）。
2. 即梦/火山引擎 API Key：用户决定**暂不申请（太贵）**，改走零成本路线（Session 2 已落地）。

---

## Session 2 — 2026-09-04（同日）｜真实链路冒烟 + 零成本出真视频

**目标**：① 真实 DeepSeek 全链路冒烟；② 回答"不花钱能否跑通真视频"——落地零成本成片路线。

### 做了什么

1. **密钥更新**：用户提供了新的 DeepSeek Key（与 .env 旧值不同），已替换。⚠️ 该 Key 曾出现在聊天明文里，建议日后轮换。
2. **真实模式冒烟（第一次）**：全流程真实调用成功，且质检门禁**真抓到 LLM 违规**——镜头 3 的 video_prompt 含广告法极限词"最"，系统按规则返工 2 次 → 降级采用，任务仍 SUCCEEDED。暴露两个改进点并当场修复：
   - 返工原因没有喂回 LLM（导致重复犯错）→ `_llm_tool_loop` 现在把 `review_feedback` 写进返工提示词；
   - 已 ACCEPTED 镜头被复检报告重复流转，产生告警 → `_apply_verdicts` 幂等化。
3. **新增 Compose Agent（剪辑，v1 提前落地）**：PIL 渲染占位关键帧 + 字幕叠加 + ffmpeg zoompan 运镜 + concat 成片。即使全 Mock 也产出**真实可播放 MP4**。ffmpeg 用 `imageio-ffmpeg` 自带二进制，零系统安装。
4. **新增 PollinationsProvider**：免费、无 Key 的真实 AI 图片生成（连通性实测 OK，256px 测试图 3.8s 返回）；其 `generate_video_clip` 用"真实关键帧 + 本地 ffmpeg"产出真实镜头 MP4。
5. **真实链路又抓到一个生产级问题并修复**：DeepSeek 偶尔不按契约输出（把分镜字段原样当提示词返回）→ PromptAgent 增加确定性"兜底合成"修复层（不赌下一轮 LLM）。
6. **最终形态端到端演示成功**：`MOCK_LLM=0` + `GEN_PROVIDER=pollinations`，任务 `283e1b1bf1fd` 五步全 SUCCEEDED、3 镜全 ACCEPTED，产出 `outputs/283e1b1bf1fd/final.mp4`（约 15s，真实 AI 图 + 字幕 + 运镜）。

### 证据

- pytest：`16 passed`（含真实 ffmpeg 编码路径）。
- 第一次真实冒烟（保温杯）：`98d7181120d0`，R6 极限词"最"被质检拦截 → 返工 2 次 → 降级采用。
- 最终演示：`283e1b1bf1fd`，steps 全部 attempt=1 SUCCEEDED，scene_states 全 ACCEPTED，final.mp4 191.8KB；3 张关键帧为 Pollinations 真实生成（33.9~76.3KB PNG）。

### 关键决策

- 即梦太贵 → 零成本三件套：**真实 LLM（DeepSeek，分级别成本）+ 免费图（Pollinations，可选）+ 本地合成（ffmpeg，必选）**。想换真视频模型时只需新增 Provider，上层不动。
- 剪辑 Agent 从 v2 提前到 v1：演示目标是"做出视频"，成片是核心证据。

### 下一步

- [ ] v2-1：JimengProvider（若日后申请到火山引擎 Key）
- [ ] v2-2：Redis 队列 / RAG / Docker（按 PRD 路线）
- [ ] GitHub 推送（用户明确要求：全部完成后再推；仓库已自包含于 aigc-agent-workflow 目录）


---

## Session 3 — 2026-09-04（同日）｜转场升级 + 规范化输出 + 生产插座答疑

**起因**：用户看成片后连问：镜头像 PPT 硬切？生产环境视频会动吗？图片生成是否留了多模态插座？三个档位各是什么？输出存在哪里、能否规范化？

### 做了什么

1. **剪辑升级：淡入淡出转场**。`concat_videos_with_transitions` 用 ffmpeg `xfade` 滤镜链（输入先归一化 720x1280@25），消除"PPT 硬切"；失败自动回退直连拼接，不阻塞出片。
2. **规范化输出目录**（用户明确要求）。新增 `app/core/output_layout.py`（唯一布局定义）+ `app/core/exporter.py`（任务完成后落盘人可读产物）：

   ```
   outputs/<task_id>/
   ├── 00_task.json          任务元信息
   ├── 01_script/            storyboard.json + 分镜脚本.md
   ├── 02_prompts/           prompts.json + 提示词.md
   ├── 03_keyframes/         关键帧（真实图片或 PIL 占位图）
   ├── 04_clips/             镜头片段（真实视频或本地合成）
   ├── 05_review/            review_report.json
   └── 06_final/             final.mp4
   ```

   Provider 生成时直接写入 03/04；导出器补齐 00/01/02/05；`artifacts.export_dir` 记录 bundle 路径；新增测试断言规范文件齐全。
3. **生产插座说明强化**：`jimeng_provider.py` 占位 docstring 明确候选后端（即梦/Seedance、GPT Image、Gemini），接口不变、GEN_PROVIDER 切换即可接入。
4. **真实模式重跑**：任务 `9d5ed59e23a2`（真实 LLM + pollinations 真实图 + 转场合成 + 规范导出）。

### 对用户问题的结论

- "视频会动吗"：会。v1 免费路线 = 静态关键帧+推近+转场；真实动态来自图生视频模型（即梦/Seedance 档），接上 Provider 后 ComposeAgent 自动切换纯拼接模式，上层零改动。
- "三个档位"：mock=离线测试（PIL 画占位图，不是真内容）；pollinations=真实文生图（按提示词生成，免费但质量一般）；jimeng=生产质量（待 Key）。前两者是测试/低成本档，生产用第三档。

### 证据

- pytest：`16 passed`（含规范化 bundle 断言）。

### Session 3 补记（真实环境的三堂课）

1. **重跑真实模式失败 -> 完备性兜底**：任务 `9d5ed59e23a2` 中 LLM 自由 Tool Calling 漏做了镜头 1。修复：工具循环结束后逐镜检查完备性，缺口自动触发"补做轮"（`generation_agent.py`）。这是继质检返工之后第二个"LLM 不可靠、工程兜底"的实例。
2. **Pollinations 全程 500 宕机**：补记时该免费服务连续 5 分钟以上所有请求 500（任务 `d2d5ca8117e0` 因此失败）。三档回退设计的必要性被真实验证。另暴露 v2 议题：中途被杀的任务停留在 RUNNING，需要 stale-task 恢复机制。
3. **Mock 档重跑成功**：任务 `d9d57bb5f0ff`（换商品"运动水壶"，验证泛化），真实 LLM + 本地生成，18 秒 SUCCEEDED，xfade 转场 + 规范目录产物齐全（00~06 共 28 个文件）。
4. **新增 `scripts/recompose_showcase.py`**：用既有任务的关键帧重剪成片，零生成成本；已产出 `演示物料\成片-运动鞋广告-v2-转场版.mp4`（真实 AI 关键帧 + 转场版）。
5. pytest 复验：`16 passed`。


---

## Session 4 — 2026-09-04（同日）｜双语提示词 + 免费档限速纪律 + README 规范化

**起因**：用户指出 v1 视频里没有鞋子画面；要求规范 README；要求记录剪辑限制。

### 做了什么

1. **取证定位**：`283e1b1bf1fd` 发给 pollinations 的提示词全是中文，而 Flux 系模型英文训练、对中文不敏感——"画面没有鞋子"的根因（不是流程问题，是提示词语言问题）。
2. **双语提示词契约**：`ScenePrompt` 新增 `image_prompt_en`；Prompt Agent 同时产出英文版；Generation Agent 调图生工具时**确定性切换**英文版（不依赖 LLM 自选语言）。
3. **免费档限速纪律（真实事故：429 限流 + 重试风暴）**：PollinationsProvider 重写为"礼貌客户端"——全局最小请求间隔 4s + 429/5xx 指数退避（8s/16s）+ 3 次后干净失败。要点：免费档的 429 是常态，客户端必须自带限速与退避，否则重试会把限流打成风暴（这正是 SLA 治理与成本优化的纪律）。
4. **README 全面规范化**（用户要求）：安装/"注册"步骤、日常使用、工作原理、"哪些是真的 / 哪些是测试档"对照表、三档说明、FAQ、已知限制。
5. **用户要求记录的已知限制**：剪辑为基础版——**无 BGM、无真人配音（旁白仅字幕）、无节奏变化**，转场仅淡入淡出；v2 议题：BGM、TTS 配音、节奏剪辑、更多转场。

### 证据

- pytest：`16 passed`（双语契约与礼貌客户端改动后复验）。
- 诊断记录：256px 英文短提示 3.9s 成功；720x1280 立即 429（限流实锤）。

### Session 4 补记（证据收尾）

- **探针实验**：英文短提示（256px）3.9s 成功；720x1280 长提示触发 429；human 主题英文提示成功（71KB）——确认 pollinations 免费档 = 限流 + 间歇性 500，与提示词内容弱相关、与服务状态强相关。
- **流水线实跑 `2ae34e00c9e7`**：双语契约生效——镜头 3 成功生成真实跑鞋关键帧（70.2KB）；镜头 1/2 遭遇服务持续 500，礼貌客户端 3 次退避后干净失败、完备性补做轮如设计运转。该任务的旁白由真实 DeepSeek 产出（"还在忍受跑步时闷热黏腻的双脚吗？..."）。
- **v3 成片**：用该任务真实旁白 + 3 张英文提示词关键帧（1 张流水线产出 + 2 张同法补生成）+ 字幕 + 转场合成，位于 演示物料\成片-运动鞋广告-v3-英文提示词版.mp4；来源构成已在演示物料 README 诚实披露。
- **LLM 可靠性第三例**：DeepSeek 返回非法 JSON（char 1928 缺分隔符）→ `chat_json_robust` 带错误信息自动重试一次。三例兜底（契约修复/漏镜补做/JSON 修复）均为真实事故驱动。
- pytest 终验：`16 passed`。


---

## Session 5 — 2026-09-04（同日）｜外部代码审查整改

**起因**：外部审查（逐文件审 + 磁盘产物实测）给出 4 红 5 中 1 低 + 3 个 GitHub 阻塞项。逐条验证后全部确认属实，按单修复并补回归测试。

### 🔴 修复

1. **00_task.json 状态时序**：产物导出移到状态落定 SUCCEEDED 之后；历史产物已重导出修复（d9d57bb5f0ff → SUCCEEDED）。
2. **pollinations 读阶段超时逃逸**：`_polite_get` 增加 OSError 分支（socket.timeout/ConnectionReset/RemoteDisconnected 全部纳入退避重试）。
3. **"回退"表述与实现不符**：实现 Provider 构造期回退（jimeng 未实现不再启动崩溃，自动回退 mock）；运行期生成失败仍如实报错留痕——.env.example 与 README 的"可回退"表述已改为准确版本。
4. **ffmpeg 无 timeout**：渲染 300s / 拼接 600s 护栏，超时干净失败；concat 临时清单文件用后即删（顺手修泄漏）。

### 🟠 修复

5. 镜头状态机跨任务串数据：`run_task` 开头重置 `_scene_machines`。
6. Worker 兜底裸写：新增 `_fail_task_safe`（走状态机守卫 + `fail_running_steps` 关闭停留 RUNNING 的步骤）。
7. duration 契约矛盾：Scene 收紧为 [2,15] 与 R4 对齐；ScriptAgent 对 LLM 输出钳制，避免白烧返工。
8. R6 单字"最"误伤：改正则排除 最后/最新/最近/最多/最少/最终/最初，仍拦截 最好/最优/顶级 等。
9. ps1 脚本补 `exit $LASTEXITCODE`。

### GitHub 阻塞项

- day1_tool_calling.py 为学习期草稿，按用户决定移出仓库（仅本地保留）；代码/文档中的相关引用已改写为直接描述机制。
- 全部文档去岗位化：职位/HR/面试官/私有文件名字样清零（grep 验证无残留）；用户自写的 README 改写保留并微调标点。
- 新增 MIT LICENSE。

### 证据

- 新增 7 项回归测试（质检规则误伤/真伤、R4、00_task.json 终态、跨任务污染、兜底失败路径、礼貌客户端重试/耗尽）。
- pytest 终验：`23 passed`。

---

## Session 6 — 2026-09-05/06｜三轮外部评审查证 → P0 交付门禁落地

**起因**：引入外部只读评审作为对抗性输入，要求逐条验证而非照单接受。前一阶段约束为"不修改，只回应"；验证完成、缺陷定性清楚后才授权修改。分支：`fix/p0-delivery-gate`。

### 查证方法

三个只读诊断脚本（项目真实组件 + 单点 Agent 桩 + 临时目录/临时 SQLite，不写仓库），加一次对仓库自带 `data/tasks.db` 的只读盘点。原则：不接受"读代码觉得"，只接受运行时证据；并把"合成实验证明可达"与"历史数据已真实发生"严格分开标注。

### 查证结论

**推翻 4 处机制性误判**：

1. "返工像自动抽奖" → 实为**确定性空转**：`prompt` 步骤执行 1 次而 `generation`/`review` 各 3 次，三轮裁决签名 `unique=1`。返工没有改变 Review 的任何输入。
2. 评审虚构的返工实现 → 实际是把结构化的逐镜逐条原因注入 tool-calling 任务单；该机制对 R3 有效，对提示词级规则无效（图生工具的 prompt 被代码确定性覆盖，Review 只读 `prompts` 产物）。
3. "1 个 commit 无法证明系统逐步演化" → 本文件即演化证据（逐 session 的事故→修复→证据链）；评审未读 `docs/`，其批评的 Redis/规则质检/Mock 三件事在架构文档 D1/D4/D3+D7 已有取舍说明。
4. "README 最容易让人误解" → 测试档对照表与已知限制已明确披露 mock 不按提示词生成、生产级真动需接图生视频模型；问题是披露位置排在标题之后，不是隐瞒。

**确认成立**：过度 Agent 化（ReviewAgent / ComposeAgent 全程零 LLM 调用）、无评估体系、Candidate Ranking 优于 retry-until-good、生产级成熟度低。

### 定位到的 P0 缺陷：Verdict 与 Delivery Eligibility 脱钩

实测链路：R6 命中旁白 → 三轮返工裁决逐字相同 → `ACCEPTED_FALLBACK` → ComposeAgent 只读 `storyboard`、不读 `scene_states` → 违规旁白烧成字幕进入 `final.mp4`（**用同一句违规旁白重渲染的帧与流水线产出的帧逐字节相同**）→ 任务对外报 `SUCCEEDED`。

仓库历史数据中，同一空转模式**已真实发生两次**：`98d7181120d0`（保温杯）与 `d9d57bb5f0ff`（运动水壶），均为 R6、均 `retried steps: generation [1,2,3]`、均降级采用 + 任务 SUCCEEDED。（这两次命中的是 `video_prompt`，违规词进入生成提示词但未直接上画面；"烧进成片"走的是 `narration` 路径，属合成实验证明的可达路径。两者暴露面不同，不可混为一谈。）

同时定位到 4 个配套缺口：`export_task_bundle` 只在 SUCCEEDED 路径调用（FAILED 任务无任何磁盘产物）；`scene_states` 从未写入交付包；`_clamp_durations` 已实现"确定性修复优先"却只覆盖 R4；两条合成分支产出不同交付物（mock 档烧字幕、真实图档不烧）。

### 做了什么（Plan A，全程 TDD：先红后绿）

1. **A1 审计包不分成败**：抽出 `WorkflowEngine._export_bundle`，成功与失败双路径都导出；`00_task.json` 增加 `scene_states`。
2. **A2 诊断固化为回归测试**：新建 `tests/test_p0_defects.py`，以 `xfail(strict=True)` 记录两个缺陷；`tests/conftest.py` 抽出共享 `build_engine` / `create_task`，`test_workflow.py` 改为委托（消除重复装配）。
3. **A3 结构化裁决**：`models.Finding(rule_id, field, message, owner, severity)`；`review_agent` 增加 `_RULE_META` 归属表与 `make_finding()`，`_execute` 改为一次命中同时产出人读 `reasons` 与机器读 `findings`（同源同序同数量，message 逐字未变以保住既有断言）。
4. **A4 交付门禁**：新建 `app/core/repair_policy.py`（`REPAIR_ACTIONS` + `has_repair`，返工路由的唯一真相来源）；`SceneStatus.BLOCKED` 终态（仅可由 REJECTED 进入）；`_review_retry_loop` 头部插入门禁——BLOCKER 且无修复通道则置 BLOCKED、不返工、抛 `StepFailure`，随后仍导出审计包。
5. **A5 文档同批**：PRD（FR-4 改写、AC 新增第 5 条并顺延、"git 提交历史清晰"改为如实描述）、架构文档（状态图加 BLOCKED、Agent 契约表加 findings、新增 ADR D10~D12、演进路线补 6~9 项）、README（流程图 Review 行、人工经验表、双层状态机与质检返工两条 bullet、已知限制补 3 条）。

### 关键决策

- **owner 与 severity 两轴正交，缺一不可**：只有 owner 则合规文案照样出货，只有 severity 则返工继续空转。
- **门禁做在 workflow 层，不做在 Compose**：Compose 因此无需理解 severity，改动面更小、职责更干净。
- **拒绝三项外部建议**（理由写入 D10）：`scope` 第三轴（当前无 task 级规则，且 scope 是 owner 的函数）、`ApprovedSceneManifest` 新 artifact（`scene_states` 已是交付资格载体）、状态四拆（scene 级 FAILED 与 task 级冗余，+1 个 BLOCKED 足够）。
- **导出时机必须先修**：否则门禁落地后会得到一批"被正确拦截但查无实据"的任务。
- **发布顺序**：未修复状态的自审描述不单独进公开仓库，与修复同批一次写完（即本条目）。

### 事故与修复（本轮自身，如实记录）

1. conftest 抽 helper 后 `test_scene_state_not_leaked_across_tasks` 报 `NameError`（该测试内联使用了被删掉的 `utc_now` / `TaskRecord`）→ 改用共享 `create_task` helper。计划书风险表已预判此类，处置一致。
2. 计划书预测 `32 passed`，实际 **33 passed**：算术漏了"xfail 转 passed 也要计入 passed"。已更正计划书。
3. 诊断脚本首跑失败（桩函数返回 dict，而调用方还要 `.model_dump()`）→ 修正后重跑；失败那次不计入证据。
4. 自我更正一处口述数字：曾说"6 次注定无效的付费生成"，DB 盘点后为 **4 次无效 generation 重跑**（2 任务 × 2 次返工）；且"付费"仅对真实模式下的 LLM tool-calling 调用成立，素材生成档位未落库故无法归因。
5. 编辑工具本轮对"含长中文行的替换"持续故障（短补丁可成、长补丁必挂），文档同步改用带断言的精确替换脚本：每处断言"恰好命中一次"，不符即中止不写入；按 bytes 读写保留各文件原有换行风格（README 为 CRLF，docs 为 LF），改动前已备份原文件。

### 证据

- **pytest 逐阶段**：A0 基线 `23 passed` → A1 `25 passed` → A2 `25 passed, 2 xfailed` → A3 `29 passed, 2 xfailed` → A4 **`33 passed, 1 xfailed`**。
- **门禁单测**（`test_blocker_compliance_failure_blocks_delivery`，由 xfail 转 passed）：`status=FAILED`、`error` 含"合规门禁"、`scene_states["2"]=BLOCKED`、`generation` 只有 `attempt=1`、`06_final/final.mp4` 不存在、`00_task.json` 与 `05_review/review_report.json` 存在。
- **端到端 HTTP 复验**（MOCK 档，零成本，端口 8011）：任务 `d0a1c7a422af` → `SUCCEEDED`、三镜全 `ACCEPTED`、0 warning、steps 为 script(1)→prompt(1)→generation(1)→review(1)→compose(1)、成片存在 ⇒ **门禁不误伤正常任务**；同一任务的 `00_task.json` 实测已含 `scene_states` 字段。
- 仍保留的 1 条 xfail：提示词级失败（R2）返工不会重跑 prompt 步骤——由 Plan B 修复后摘除。
- 三个诊断脚本的原始 stdout 与 DB 盘点结果保存在项目外归档，不进仓库。

### 下一步

- [ ] **Plan B**：owner routing（按 `Finding.owner` 重跑对应步骤）+ `_sanitize_narration`（与 `_clamp_durations` 对称，须留痕原文/改后/触发规则）+ `PromptAgent`/`ScriptAgent` 局部返工 → 摘掉最后 1 条 xfail。
- [ ] **Plan C**：`GET /api/v1/metrics`（成功率/返工/降级率/拦截率/P50-P95/规则分布/路由分布）+ usage instrumentation（`chat()` 当前丢弃 `usage`）+ `gen_provider`/`mock_llm` 落库 + 移除 `_move_scenes` 吞异常 + `_scene_machines` 移入 per-task 上下文 + stale RUNNING 恢复。
- [ ] **Plan D**：Provider 无关后处理（Provider 不再借用 `render_scene_video`）+ 新增 ffmpeg 视频字幕能力（前置缺口：`overlay_subtitle` 只支持静图）+ R2/R6 规则校准。
- [ ] commit 与 push 由用户执行。

---

## Session 7 — 2026-09-06｜F15 修复：消除状态机与质检报告的静默不一致

**起因**：Session 6 收尾时把 `_move_scenes` 吞 `IllegalTransitionError`（F12）列为待修项。用户要求"先确认再万无一失地修改"，确认过程中发现 F12 只是表象，底下藏着一条更深的路径（编号 F15）。

### 确认过程（为什么不能直接改）

F12 的原计划是"把 warning 换成 raise"。逐行核对后发现这样做会把一个本可完成的任务变成崩溃，因为**非法流转当前是可达的**：

1. `rejected` 原先由**质检报告**推导（`[v.scene_id for v in report.verdicts if not v.passed]`），不是由状态机推导。
2. `_apply_verdicts` 遇到「已 ACCEPTED 但本轮报告判不通过」时，因 ACCEPTED 是终态、`can_transition(REJECTED)` 为 False 而**静默 continue**，状态保持 ACCEPTED。
3. 但该镜头仍在 `rejected` 里 → `_move_scenes(rejected, GENERATING)` 触发 `ACCEPTED→GENERATING` 非法流转 → 被吞成 warning → 循环继续 → **该镜头被无谓重新生成一遍**。
4. 收尾再触发一次非法流转再被吞一次。终局：状态机说 ACCEPTED、报告说 failed、行为是重生成过，三者矛盾，对外只有 SUCCEEDED + 两条 warning。

**可达性分析**：R1/R2/R5/R6 的输入（`prompts` / `storyboard`）在返工轮之间不变，不会翻转；R4 只对返工镜头生效而那些本就是 REJECTED；**唯一可达路径是 R3**——它检查磁盘文件是否存在，已 ACCEPTED 镜头的关键帧/片段在两轮之间消失即翻转。概率低但非零且无防护。

### 做了什么（三步，顺序不可颠倒）

1. `_apply_verdicts`：区分「真幂等」（`state == target`，保持静默 continue）与「终态 vs 新裁决矛盾」（记 warning + `append_event` 留痕，状态不回退）。
2. 新增 `_rejected_scenes()`：`rejected` 改由状态机推导（只取 `state is REJECTED`），`reasons` 仍从报告取但按 `rejected` 过滤。这样四个 `_move_scenes` 调用点**按构造合法**。
3. `_move_scenes`：去掉 try/except，非法流转先 `log.error` 再抛 `IllegalTransitionError`。

**顺序理由**：合法性必须先成为结构性保证（第 2 步），守卫才能真正生效（第 3 步）。颠倒顺序等于把静默错乱换成崩溃。

**语义决策**：矛盾时取「记警告并继续」而非「判任务 FAILED」——终态是设计意图，镜头内容未变、首轮 ACCEPTED 判断依然有效，因外部删文件就整单失败对调用方过脆。记为 ADR D13。

### 证据

- **红灯验证（关键）**：把修复前的 `workflow.py` 换回后运行新测试，测试确实失败（`AssertionError: ['镜头 2 重试 2 次后仍未通过质检，降级采用']`——没有不一致留痕），且 `logs/app.log` 记录了 **2 次被吞的非法流转**：`ACCEPTED -> GENERATING`、`ACCEPTED -> IN_REVIEW`。这是 F15 可达性的运行时实证，不是推演。
- **绿灯验证**：换回修复版后新测试通过。
- **全量**：`pytest` 由 `33 passed, 1 xfailed` → **`34 passed, 1 xfailed`**。
- **不误伤**：真实 HTTP 端到端复验（MOCK 档、零成本、端口 8012），任务 `2cfb26963c50` → SUCCEEDED、三镜全 ACCEPTED、0 warning、无返工、成片存在、`00_task.json` 终态与 `scene_states` 正确。
- 修复后的代码在绿灯轮与全量套件中**零新增**非法流转命中。

### 顺手修的

- `.gitignore` 补 `.idea/`：用户以 IDE 打开项目后出现未跟踪的 `.idea/`，而 `git add -A` 会把它提交进公开仓库。

### 下一步

- [ ] 仍保留的 1 条 xfail：MAJOR 级返工未按 owner 路由（提示词级失败仍确定性空转）——后续计划的第一项。
- [ ] `_scene_machines` 移入 per-task 执行上下文（多 Worker 前置）。
- [ ] stale RUNNING 任务恢复。
- [ ] commit 与 push 由用户执行。

