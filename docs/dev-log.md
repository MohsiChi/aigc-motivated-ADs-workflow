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
