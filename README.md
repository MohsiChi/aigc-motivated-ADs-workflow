# AIGC Agent Workflow

> 一条 Multi-Agent 短视频广告生产流水线：输入一句话主题，自动产出成片
> 任务状态机 · 任务调度 · 5 个 Sub-Agent · Tool Calling · 质检返工 · 规范化导出

## 项目背景

本项目是对一段 AIGC 制作流程的反思性创作。2026 年 7 月，团队以人工方式完成《黑如漆·薄如纸 —— AIGC 唤醒龙山蛋壳黑陶古老文化血脉》[完整视频](https://www.bilibili.com/video/BV1Brtg6AEgS/)）立项参考历史文物及文献；文案以人工创作予以分镜与TeX排版；镜头素材用即梦图生视频生成，首尾帧衔接转场；素材逐段经"生成、验收、返工"质检后进入剪映完成剪辑。

当时的流程能够跑通，但每一步都靠人工,无意义时间损耗也浪费于是。如果放任人对于细节，强迫症式的完美渴求而进行之反复打磨, 如对关键帧、片段的效果不满意进行反复"抽奖"或是对文案的过度诗意化打磨, 不仅不一定"抽奖"出真的更优秀的片段，并且过度诗意化打磨文案或造成信息欠准确。相较蛋壳黑陶高柄杯的科普类短片, 商品AI广告对剪辑质量的要求较低, 其第一性在于内容完整与平台推流, 所以可以尝试以多agent实现快速成片, 以细节把控的些许下降为代价、形成自动化流水线。但这种代价完全可接受, 对于商品广告而言, 完整性、时效性与推流方为第一性之所要。
本项目把这条流程重新实现为多智能体工作流：输入一句话主题，五个 Agent 依次完成分镜文案、提示词、素材生成、质检与剪辑；任务提交后立即返回编号，后台执行期间状态、产物与重试记录落库。制作内容从纪录短片移植商品广告，流水线本身不做内容假设，保持通用。

```
POST /api/v1/tasks {"topic": "给这双鞋子做个广告"}
        ↓ 202 立即返回 task_id
[Script Agent]  编剧：主题 → 分镜脚本（钩子/卖点/转化）
        ↓
[Prompt Agent]  提示词：分镜 → 逐镜图生/视频提示词（中英双语）
        ↓
[Generation Agent]  生成：Tool Calling 驱动，先关键帧后图生视频
        ↓
[Review Agent]  质检：6 条规则门禁（含广告法极限词）+ 分级裁决：MAJOR 返工 / BLOCKER 拦截不予交付
        ↓
[Compose Agent]  剪辑：字幕叠加 + 推近运镜 + 淡入淡出转场 → final.mp4
```

## 人工经验提炼而来的工作流workflow.
| 《蛋壳黑陶》人工做法 | 沉淀为本项目里的设计 |
|---|---|
| 即梦"首尾帧"模式串联镜头转场，保证镜头衔接不跳变 | Tool Calling 契约：先出关键帧、图生视频工具必须接收上一镜关键帧路径（先关键帧后成片） |
| "生成 → 验收 → 返工"逐镜质量门禁，废弃素材全程留痕可追溯 | 镜头级状态机 + Review Agent 规则质检返工闭环（BLOCKER 级合规失败不予交付）；steps/events 三表全程留痕 |
| 旁白成文后按字数/语速逐段对轨，保证声画同步 | 分镜契约内嵌 narration 旁白与 duration_s 秒级规格；旁白由 Compose 自动叠加为字幕（**仅测试档**，见"已知限制"） |
| 剪映人工剪辑（节奏/转场/混音）| Compose Agent 自动化合成 + 规范化产物目录（00~06，JSON/MD 双形态，Exporter 统一落盘） |

## 测试档说明

| 环节 | 现状 | 说明 |
|---|---|---|
| 编剧 / 提示词（LLM） | （如DeepSeek，可换任意 OpenAI 兼容模型） | `MOCK_LLM=0` 时真实调用；`=1` 时用确定性假数据（测试用） |
| 关键帧图片 | 三档可选（见下） | 测试档=占位图；免费档=测试文生图；生产档=预留位置 |
| 视频片段 | 免费/测试档 = 本地 ffmpeg 合成（画面是"动的运镜"，内容不产生新动作）；**生产级"真动"需接图生视频模型** | `tools/providers/jimeng_provider.py` 是预留插座 |
| 剪辑合成 | （ffmpeg：字幕 + 运镜 + 转场） | 基础版：无 BGM、无真人配音、无节奏变化；字幕仅在测试档合成路径生效（见"已知限制"） |
| 状态机 / 调度 / 重试 / 持久化 / 留痕 |  | 双层状态机、SQLite 三表、质检返工循环 |

三档素材生成（环境变量 `GEN_PROVIDER`）：

| 档位 | 设置 | 是什么 | 用途 |
|---|---|---|---|
| 测试档（默认） | `GEN_PROVIDER=mock` | PIL 画的占位卡片，**不按提示词生成内容**，零成本离线 | 验证流水线机制、跑测试、对外演示骨架 |
| 免费真实档 | `GEN_PROVIDER=pollinations` | 免费无 Key 的真实文生图（Flux），按提示词出图 | 低成本出"真图"；境外服务偶发不稳定；失败时任务报错留痕，手动切回测试档即可 |
| 生产档（占位） | `GEN_PROVIDER=jimeng` | 即梦/Seedance、GPT Image、Gemini 等真实多模态生成 | 实现两个方法 + 填 Key 即可接入，上层零改动 |

## 安装 / 配置步骤

前置：Python 3.10+

```powershell
# 1. 克隆仓库
git clone https://github.com/MohsiChi/aigc-motivated-ADs-workflow aigc-agent-workflow
cd aigc-agent-workflow

# 2. 建虚拟环境 + 装依赖
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 3. 配置环境变量（"注册"你的 Key）
copy .env.example .env
# 用记事本打开 .env：
#   - 只跑测试档：什么都不用填
#   - 要真实 LLM：填 DEEPSEEK_API_KEY=你的key，并把 MOCK_LLM 改成 0

# 4. 跑测试（全测试档，零成本）
.\.venv\Scripts\python -m pytest

# 5. 启动服务
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 日常使用

**提交任务**（另开一个终端）：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/tasks" `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes('{"topic": "给这双鞋子做个广告", "product_info": "轻便跑鞋，透气网面", "scene_count": 3}'))
# → 返回 { task_id, status=PENDING, created_at }
```

**查询进度/结果**：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/tasks/<task_id>" | ConvertTo-Json -Depth 8
```

**产物在哪里**：每个任务一个规范化目录 `outputs/<task_id>/`：

```
00_task.json        任务元信息
01_script/          分镜脚本（storyboard.json + 分镜脚本.md）
02_prompts/         逐镜生成提示词（prompts.json + 提示词.md，含英文版）
03_keyframes/       每镜关键帧
04_clips/           每镜素材片段
05_review/          质检报告
06_final/           成片 final.mp4
```

**模式切换**：`$env:MOCK_LLM="0"; $env:GEN_PROVIDER="pollinations"` 后再启动服务即为"真实 LLM + 免费真图"；全默认则零成本。

## 系统是怎么工作的

- **提交即返回**：`POST /tasks` 落库（PENDING）入队后立即返回 `task_id`，HTTP 请求不阻塞——长耗时生成任务的标准做法。
- **后台调度**：Worker 线程从队列取任务，按流水线顺序执行（v1 进程内队列；队列是注入的边界，v2 可换 Redis 而不动引擎）。
- **双层状态机**：任务级 `PENDING→RUNNING→SUCCEEDED/FAILED`；镜头级 `PENDING→GENERATING→IN_REVIEW→ACCEPTED/REJECTED→(返工)`，另有 `BLOCKED` 终态承载"合规门禁未通过、不予交付"。非法流转直接抛异常。
- **Artifact 驱动**：Agent 之间不互相"聊天"，只传递上一步的结构化产物——不背全量对话历史，上下文不膨胀。
- **质检返工闭环（分级）**：裁决带 owner / severity 双轴。MAJOR/MINOR 不通过 → 携原因返工（最多 `MAX_SCENE_RETRIES` 次）→ 耗尽则降级采用并告警，不阻塞整片；**BLOCKER（合规）不通过且无修复通道 → 镜头置 `BLOCKED`、不返工（重跑注定得到逐字相同的裁决）、不出成片、任务 FAILED 并导出审计包**。
- **对 LLM 输出的工程兜底**：LLM 输出是不可靠依赖——JSON 契约兜底修复、返工原因喂回、漏镜自动补做，都是真实事故换来的防线。
- **双轨留痕**：SQLite 存任务/步骤/事件（API 可查、可审计）；磁盘存规范化产物目录（人可读、可交付）。

## 项目结构

```
app/
├── main.py            # FastAPI 入口（应用工厂 + Worker 生命周期）
├── config.py          # 配置（环境变量/.env，含 MOCK 开关）
├── api/               # API 层：路由 + 请求/响应模型
├── core/              # 调度层：state_machine / task_store / workflow / models / exporter / output_layout
├── agents/            # 执行层：script / prompt / generation / review / compose 五个 Sub-Agent
├── tools/             # 能力层：Tool 注册表 + 生成 Provider（mock/pollinations/jimeng 占位）+ PIL/ffmpeg 渲染
└── llm/               # 能力层：DeepSeek 客户端 + JSON 解析
tests/                 # pytest：状态机 / 流水线（含返工路径）/ API
docs/                  # PRD / 架构设计 / 业界调研 / 开发日志（dev-log.md）
scripts/               # run.ps1 / test.ps1 / smoke_real_llm.py / recompose_showcase.py
```

## 文档索引（想深入了解）

1. [01-PRD.md](docs/01-PRD.md)：范围、用户故事、验收标准
2. [02-架构设计.md](docs/02-架构设计.md)：分层架构、双层状态机、Agent 契约、9 条设计决策、v2 路线
3. [03-业界方案调研.md](docs/03-业界方案调研.md)：MoneyPrinterTurbo / ShortGPT / LangGraph 等借鉴
4. [dev-log.md](docs/dev-log.md)：开发日志——每次会话做了什么、踩了什么坑、证据在哪

## FAQ

**为什么测试档的成片是"卡片"？** 测试档的目的是零成本验证流水线机制（状态机/调度/质检/剪辑都在真实工作），不是出内容。要看真实画面用免费档或生产档。

**pollinations 挂了怎么办？** 换回 `GEN_PROVIDER=mock`；或用 `scripts/recompose_showcase.py` 拿历史任务的真实关键帧重剪成片，零生成成本。

**API Key 安全吗？** 只存在于本地 `.env`（已 gitignore，从不提交）；仓库提供 `.env.example` 模板。代码一律 `os.getenv` 读取。

**想接即梦/GPT Image/Gemini？** 实现 `app/tools/providers/jimeng_provider.py` 里的两个方法（提交异步任务→轮询→下载产物），`.env` 设 `GEN_PROVIDER=jimeng`，其余零改动。

## 已知限制（v2 路线，详见架构文档）

- 剪辑为基础版：**无 BGM、无真人配音、无节奏变化**；转场仅淡入淡出；旁白以字幕呈现，且**字幕只在测试档生效**（见下条）。
- 免费档图片质量依赖英文提示词（中文提示词对 Flux 系模型不敏感，本项目已自动切换英文版）。
- 中途被杀的任务停留在 RUNNING，缺 stale-task 恢复；并发/队列/成本计量均为 v2 议题。
- **旁白字幕只在测试档生效**：真实图档的 Provider 直接返回镜头 MP4，Compose 走"直接拼接"分支，旁白不会烧成字幕；补齐需要新增 ffmpeg 视频字幕（drawtext）能力，属 v2。
- **MAJOR 级返工尚未按 owner 路由**：提示词级规则（R1/R2/R5/R6-提示词）失败时仍重跑 generation，裁决不会改变。该缺陷已用 `xfail(strict=True)` 测试显式标注，修复见架构文档演进路线第 6 项。
- **无指标端点与成本计量**：成功率/返工/延迟的原料已在 steps 表但未聚合暴露；LLM token usage 未落库，因此当前无法给出真实单条成本（不做估算伪装）。
