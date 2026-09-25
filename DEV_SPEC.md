# Developer Specification (DEV_SPEC)

> 项目：**Elder Companion Agent**（面向独居老人的 AI 陪伴 Agent + 子女看护端）
> 版本：v0.1（设计定稿，待开发）
> 周期：2 天 MVP；演示地点：美国；模型供应商：OpenAI
> 团队：**A（功能 / 主程）**——后端、对话、语音、症状日志、子女端数据；**B（视觉 / 前端）**——跌倒检测、UI 打磨、演示素材

## 目录

1. 项目概述
2. 核心特点
3. 技术选型与技术分析
4. 测试方案
5. 系统架构与模块设计
6. 项目排期（开发阶段）
7. 可扩展性与未来展望
8. 附录：设计决策记录（ADR）

---

## 1. 项目概述

独居老人最缺的是「有人说话」，子女最担心的是「身体出状况没人知道」。本项目用一个 AI Agent 同时解决两端：

| 端 | 用户 | 形态 | 核心价值 |
|---|---|---|---|
| **老人端** `/elder` | 老人 | 手机/平板浏览器，一个大按钮语音对话 | 随时有人陪聊、被关心；零学习成本 |
| **子女端** `/family` | 子女 | 浏览器 Dashboard | 自动整理的症状日志、危险信号告警、跌倒检测、每日摘要 |

老人不需要「填健康表」——Agent 在日常闲聊中听到「这两天早上起来头有点晕」，就自动抽取为结构化症状记录，同步给子女；听到「胸口闷」立即告警。子女端另接家中摄像头（演示用视频文件），检测跌倒并推送告警与截图。

### 设计理念 (Design Philosophy)

1. **陪伴优先，记录无感 (Companionship First, Invisible Logging)**
   老人端只做对话，从不弹表单、不追问「请描述症状严重程度 1–10」。症状抽取在后台异步完成，不影响对话延迟与体验。
2. **LLM 负责理解，代码负责决策 (LLM Understands, Code Decides)**
   LLM 只输出「这句话里提到了什么症状」（结构化 JSON）；症状归一化、去重合并、是否告警、告警等级全部由确定性代码 + `config/symptoms.yaml` 规则完成——可测试、可解释、改规则不用改 prompt。
3. **安全边界明确 (Care, Not Diagnosis)**
   Agent 只关心、追问、建议告诉家人或就医；不诊断、不推荐药物。危险信号走代码规则兜底，不依赖 LLM「自觉」。
4. **视觉能力协议化 (Vision as an MCP Server)**
   跌倒检测是独立的 **fall-mcp** 服务：查询与控制能力（事件、截图、状态、视频分析）以 MCP 工具暴露，任何 MCP 客户端（本项目子女端问答 Agent、Claude Desktop、Cursor）都能直接使用；实时告警走 push（`POST /api/alerts`），因为 MCP 是客户端拉取模型。A、B 并行开发，检测进程挂了不影响对话。
5. **演示可控 (Demo-Deterministic)**
   所有外部依赖（OpenAI、摄像头）都有 mock / 预录素材替代路径；演示关键路径（症状日志、危险告警、跌倒告警）必须 100% 可复现。

### 非目标 (Non-Goals, v1)

- 不做医疗诊断、用药建议、与医院/HIS 系统对接
- 不做注册登录与多家庭（写死 1 位老人 + 1 位子女，URL 直达）
- 不做原生 App、不做推送通知（子女端页面内实时告警即可）
- 不做 OpenAI Realtime 语音对语音（放入第 7 章）
- 不做方言识别优化、不做本地 ASR/TTS
- 跌倒检测不追求真实环境鲁棒性（演示以预录视频为主）

---

## 2. 核心特点

### 2.1 老人端能力

- **语音对话**：按住说话 → 转写 → 回复 → 自动朗读；中英文自动跟随。
- **有温度的人设**：称呼昵称、句子短、一次只问一个问题、记得老人的基本情况（画像）和最近对话。
- **主动关怀**：对话开场/空闲时主动问候吃饭、睡眠、吃药、心情。
- **健康话题安全处理**：表达关心 + 追问细节（部位、多久、多严重）+ 建议告诉家人/就医。

### 2.2 症状日志（亮点）

- 每条老人消息后台异步抽取症状 → 结构化 `symptom_log`。
- 症状归一化到枚举（`dizziness`、`chest_pain`…），同一症状 24h 内合并计数，形成时间线。
- 保留原话 `raw_quote`，子女可点开看老人原话，避免 LLM 「脑补」。
- **危险信号规则**：胸痛、呼吸困难、单侧无力/说话不清、意识混乱、跌倒、自伤念头 → 高优先级告警实时推送。

### 2.3 子女端能力

- 今日摘要（2–3 句自然语言：精神状态、提到的不适、需要关注的事）
- 症状时间线（按天分组、严重度色块、出现次数、原话）
- 告警中心（症状类 / 跌倒类，跌倒附截图），SSE 实时弹出
- 跌倒检测面板（实时检测画面 MJPEG + 最近事件）
- 对话记录（只读）

### 2.4 跌倒检测

- YOLO11-pose 关键点 + 状态机规则判定（下落速度、躯干角度、倒地持续时间）。
- 输入：摄像头或视频文件；输出：截图 + 告警事件。
- 可选：截图交给 GPT 视觉模型二次确认，降低误报。
- **以 MCP Server 对外（fall-mcp）**：`get_fall_events`、`get_event_snapshot`、`get_monitor_status`、`start_monitoring` / `stop_monitoring`、`analyze_video` 六个工具；Streamable HTTP（主）+ stdio（Claude Desktop）。
- 子女端「问问 AI」（可选）：“妈妈今天有没有摔倒？最近哪里不舒服？”——Agent 通过 MCP 调跌倒工具 + 本地症状工具综合回答。

### 2.5 演示核心指标（待填，由阶段 G 产出）

| 指标 | 目标 | 实测 |
|---|---|---|
| 语音一轮端到端延迟 P50 | < 4s | – |
| 症状抽取评测集准确率（症状名 + 是否危险） | ≥ 90% | – |
| 危险信号召回（评测集） | 100% | – |
| 演示视频跌倒检出 / 误报 | 全部检出 / 0 误报 | – |

---

## 3. 技术选型与技术分析

### 3.1 总体技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 语言与包管理 | Python 3.12 + uv | 与 rift 项目一致；跌倒检测依赖 Python 生态 |
| Web 后端 | FastAPI + Uvicorn | 同时托管 API、页面、SSE |
| 前端 | Jinja2 + 原生 JS + CSS | 两个页面，不引入构建工具，2 天内最省事 |
| 实时推送 | SSE（`/api/alerts/stream`） | 单向推送足够；比 WebSocket 简单，浏览器原生重连 |
| 存储 | SQLite + SQLAlchemy 2.x | 单机演示足够 |
| LLM | OpenAI Chat Completions（`CHAT_MODEL`） | 对话、摘要 |
| 结构化抽取 | OpenAI Structured Outputs（`response_format=json_schema`, strict） | 症状抽取保证 JSON 合法 |
| ASR | OpenAI `gpt-4o-transcribe`（备选 `whisper-1`） | 口音/老人语速鲁棒性好于浏览器 Web Speech |
| TTS | OpenAI `gpt-4o-mini-tts` | 支持 `instructions` 控制语速/语气（慢、温和） |
| 姿态估计 | Ultralytics YOLO11n-pose | CPU 可跑实时；开箱预训练权重 |
| 视频 | OpenCV | 读取摄像头/视频、画骨架、编码 MJPEG |
| MCP | 官方 `mcp` Python SDK（FastMCP） | fall-mcp：Streamable HTTP（主）+ stdio（桌面客户端）；与 MJPEG 同一 Starlette 应用 |
| 质量 | pytest、ruff | 离线单测全部使用 mock LLM |

> 模型名一律写在 `.env`，开工前在 OpenAI 控制台确认可用型号并更新默认值。

### 3.2 技术分析：语音交互链路

**方案对比**

| 方案 | 延迟 | 开发成本 | 症状日志所需文本 | 结论 |
|---|---|---|---|---|
| ① 浏览器 Web Speech API（ASR）+ `speechSynthesis`（TTS） | 低 | 最低 | 有 | ❌ 识别质量不稳定、音色机械，Safari/Chrome 行为不一致 |
| ② **录音上传 → OpenAI 转写 → Chat → TTS（串行管线）** | 中（3–4s） | 低 | 有 | ✅ **MVP 采用** |
| ③ OpenAI Realtime API（语音对语音，WebRTC） | 最低（<1s） | 高（会话管理、打断、工具调用、转写旁路） | 需额外开启转写 | ⏳ 第 7 章加分项 |

**延迟预算（方案 ②）**

| 环节 | 预估 | 优化手段 |
|---|---|---|
| 录音上传（webm/opus，~5s 语音 ≈ 50KB） | 0.1–0.3s | opus 压缩 |
| 转写 | 0.5–1.2s | 用 `gpt-4o-transcribe`；传 `language` 提示可选 |
| Chat 回复（~60 token） | 0.8–1.5s | 限制 `max_tokens`；prompt 要求短句；非推理型 mini 模型 |
| TTS | 0.6–1.2s | mini-tts；**先返回文字、再异步取音频** |
| 症状抽取 | 0（异步，不在关键路径） | FastAPI `BackgroundTasks` |
| **合计** | **≈ 2.5–4s** | 前端「在听 / 在想」动画掩盖等待 |

**关键实现点**

- 前端 `MediaRecorder`（`audio/webm;codecs=opus`；Safari 回落 `audio/mp4`），按住录音、松开上传。
- 麦克风权限要求安全上下文：演示用 `http://localhost` 或 HTTPS（手机访问需 HTTPS，见 3.9 风险）。
- 浏览器自动播放限制：首次交互（按按钮）后播放音频不受限，满足条件。
- `/api/chat` 返回 `{user_text, reply_text, audio_url}`；音频文件存 `data/audio/`，以静态路径返回。

### 3.3 技术分析：对话编排

- **不使用 LangGraph / Agent 框架**：老人端没有多步任务流（不同于 rift 的预约状态机），只是「人设 + 上下文 + 单次生成」，引入编排框架是负收益。
- **上下文构成**：`system(人设 prompt + 老人画像 + 当前时间 + 近期症状摘要)` + 最近 10 轮消息 + 本轮输入。
  - 注入「近期症状」让 Agent 能自然回访：「王阿姨，昨天您说膝盖疼，今天好点了吗？」——这是陪伴感的关键体验点。
- **主动问候**：老人端打开时调 `POST /api/chat/greet`，按时段（早/午/晚）+ 未回访症状生成开场白。
- **安全护栏（双层）**：
  1. prompt 层：禁止诊断、禁止药名与剂量建议；涉及危险症状时安抚 + 建议立即联系家人/拨打 911。
  2. 代码层：症状抽取命中危险信号 → 告警子女（不依赖 LLM 回复是否得当）。

### 3.4 技术分析：症状抽取与日志

#### 3.4.1 症状枚举（`config/symptoms.yaml`，唯一数据源）

| canonical | 显示名 | 危险信号 |
|---|---|---|
| `chest_pain` | Chest pain / 胸痛胸闷 | ✅ high |
| `shortness_of_breath` | Shortness of breath / 呼吸困难 | ✅ high |
| `numbness_weakness` | One-sided numbness or weakness / 单侧麻木无力 | ✅ high |
| `slurred_speech` | Slurred speech / 说话不清 | ✅ high |
| `confusion` | Confusion / 意识混乱 | ✅ high |
| `fall` | Fall / 摔倒 | ✅ high |
| `self_harm` | Self-harm thoughts / 自伤念头 | ✅ high |
| `dizziness` `headache` `fatigue` `insomnia` `cough` `fever` `nausea` `stomach_pain` `joint_pain` `back_pain` `low_mood` `loneliness` `memory_issue` `appetite_loss` | … | ❌（`severity=severe` 时 → medium 告警） |
| `other` | 自由文本 `label` | ❌ |

#### 3.4.2 抽取器输出协议（Structured Outputs, strict）

输入：抽取 prompt（含枚举清单）+ 最近 3 轮对话（用于指代消解：「还是那样」「又疼了」）+ 本轮老人发言。

```json
{
  "symptoms": [
    {
      "canonical": "dizziness",
      "label": "morning dizziness",
      "body_part": "head",
      "severity": "mild | moderate | severe | unknown",
      "duration": "2 days",
      "onset": "when getting up in the morning",
      "status": "new | ongoing | improved | resolved",
      "raw_quote": "I've been a bit dizzy in the mornings"
    }
  ]
}
```

- 没提到症状 → `symptoms: []`（占多数，要求模型不「脑补」）。
- 否定与他人：「我没头疼」「我老伴咳嗽」→ 不记录（prompt + 评测集覆盖）。
- `raw_quote` 必须是原话子串，代码校验不通过则丢弃该条（防幻觉）。
- `status=improved/resolved` 用于子女端显示「已好转」。

#### 3.4.3 合并与告警规则（代码）

```
for s in extraction.symptoms:
    validate(raw_quote ⊂ user_text) else drop
    existing = find(elder, canonical, last_seen within 24h)   # other 按 label 比较
    if existing: count += 1; last_seen = now; severity = max(...); status = s.status
    else:        insert new row
    level = rules.alert_level(canonical, severity)           # 读 symptoms.yaml
    if level and not recently_alerted(canonical, 2h):         # 告警去抖
        alerts.create(type=symptom, level, content, ref=symptom_id)
```

### 3.5 技术分析：跌倒检测

**方案对比**

| 方案 | 效果 | 成本 | 结论 |
|---|---|---|---|
| 单帧目标检测（Roboflow/HF 上的 YOLO fall 模型） | 易把「躺床/躺沙发」判为跌倒 | 低 | ❌ |
| **YOLO11-pose 关键点 + 时序规则状态机** | 可解释、可调参，区分「躺下」与「摔倒」靠下落速度 | 低 | ✅ **MVP 采用** |
| 关键点 + ST-GCN / PoseC3D（NTU `falling down` 类） | 更鲁棒 | 中（权重、依赖 mmaction2） | ⏳ 第 7 章 |
| 多模态大模型逐帧判断 | 灵活 | 慢、贵 | 仅做**报警后二次确认**（可选） |

**判定状态机**（每个跟踪到的人一个实例，参数写在 `settings.yaml`）

```
STANDING ──(A: bbox 宽高比 > 1.2  或  躯干与竖直夹角 > 60°)──▶ 候选
        并且 (B: 髋部中点在 ≤ 0.6s 内下降 > 0.35 × 身高)      ──▶ FALLING
FALLING ──(持续 ≥ 3s 保持近水平 且 髋部位移 < 阈值)──▶ DOWN ⇒ 触发告警（截图）
FALLING ──(3s 内恢复直立)──▶ STANDING（不报）
DOWN    ──(恢复直立)──▶ STANDING；同一 track 30s 内不重复报
```

- 缓慢躺下不满足 B（下落速度）→ 不报，这是区分「躺下」与「摔倒」的核心。
- 关键点置信度 < 0.3 的帧跳过；人体框用 `model.track(persist=True)` 获得 track_id。
- 性能：YOLO11n-pose 在笔记本 CPU 约 15–25 FPS（640 输入），演示足够；抽帧处理（每 2 帧）作为兜底。
- 隐私：视频帧只在本地处理，不上传；仅开启二次确认时上传单张截图。

### 3.6 技术分析：fall-mcp 服务设计

**为什么做成 MCP，以及边界在哪**

| 需求 | MCP 适合？ | 做法 |
|---|---|---|
| 实时告警（摔倒发生 → 子女端立刻弹窗） | ❌ MCP 是客户端发起的请求/响应，没有可靠的服务端主动推送给浏览器的通道 | 保留 push：`Reporter` → `POST /api/alerts` → SSE |
| 查询历史事件、取截图、看监控状态 | ✅ | MCP tools |
| 启停监控、切换视频源 | ✅ | MCP tools |
| 离线分析一段视频（子女上传录像） | ✅ | MCP tool `analyze_video` |
| 被外部 Agent 复用（Claude Desktop 里问“今天有没有摔倒”） | ✅ | stdio / Streamable HTTP |

**工具清单**

| Tool | 参数 | 返回 | 说明 |
|---|---|---|---|
| `get_fall_events` | `since?: ISO时间`, `limit=20` | `[{event_id, ts, track_id, confidence, verified, snapshot_id}]` | 读事件存储 |
| `get_event_snapshot` | `event_id` | `ImageContent`（JPEG）+ 文本描述 | 让多模态客户端直接“看”截图 |
| `get_monitor_status` | – | `{running, source, fps, persons, states: {track_id: STANDING/FALLING/DOWN}, uptime_s}` | 健康检查 + 当前画面状态 |
| `start_monitoring` | `source: "0" \| 视频路径`, `loop=true` | `{running, source}` | 启动检测线程（已运行则切换源） |
| `stop_monitoring` | – | `{running: false}` | 停止检测线程 |
| `analyze_video` | `path` | `{events: [...], duration_s, frames}` | 离线整段分析，不触发实时告警，结果写入事件存储（`source=offline`） |

- **Resource**（可选）：`fall://live/snapshot` —— 当前帧截图。
- **事件存储**：`data/fall_events.jsonl`（追加写）+ 内存索引；截图 `data/snapshots/{event_id}.jpg`。fall-mcp 是事件的唯一数据源，主服务 `alert` 表只保存告警副本（含 `ref_id=event_id`）。
- **进程形态**：一个 Starlette 应用挂载三样东西——`/mcp`（FastMCP Streamable HTTP）、`/stream`（MJPEG）、`/healthz`；检测循环在后台线程，由 `start/stop_monitoring` 控制，与 MCP 请求通过线程安全的 `MonitorController` 交互。
- **stdio 模式**：`python -m fall_detector.server --stdio` 供 Claude Desktop 配置；stdio 模式下不启 MJPEG。
- **错误映射**：视频不存在 / 摄像头打不开 / 未在运行 → MCP tool error（`isError=true` + 可读信息），不抛裸异常。

**子女端问答 Agent（可选，E5）**

```
family.js 输入问题 → POST /api/family/ask
  → OpenAI Chat（tools = fall-mcp 工具列表(经 MCP client 转换) + 本地工具 get_symptoms / get_today_summary）
  → tool_calls → MCP client (Streamable HTTP :8001/mcp) / 本地函数
  → 最终回答（引用事件时间与截图链接）
```

### 3.7 模型层设计

- `LLMClient` 薄封装 OpenAI SDK：`chat()`、`extract_json(schema)`、`transcribe()`、`tts()`、`vision_check()`。
- `MockLLMClient` 实现同一接口，按 fixture 返回，用于单测与无网演示兜底（`LLM_PROVIDER=mock`）。
- 超时与降级：
  - 转写失败 → 前端提示「没听清，再说一次好吗？」（并提供文字输入框兜底）
  - Chat 失败 → 固定安抚话术
  - TTS 失败 → 只显示文字
  - 抽取失败 → 记录日志，不影响对话
- 密钥只从环境变量读取，`.env` 在 `.gitignore`。

### 3.8 数据模型（SQLite）

```sql
elder(id, name, nickname, language, profile_text, created_at)
message(id, elder_id, role[user|assistant], text, audio_path, created_at)
symptom_log(id, elder_id, canonical, label, body_part, severity, duration, onset,
            status, raw_quote, message_id, count, first_seen, last_seen)
alert(id, elder_id, type[symptom|fall], level[high|medium], title, content,
      snapshot_path, ref_id, created_at, is_read)
```

### 3.9 关键风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 手机访问 `http://局域网IP` 无法录音（非安全上下文） | 老人端演示失败 | 演示用笔记本 `localhost`；或 `ngrok`/Cloudflare Tunnel 提供 HTTPS |
| 会场网络差，OpenAI 超时 | 全链路卡住 | 手机热点备份；`LLM_PROVIDER=mock` 兜底回放 |
| LLM 抽取漏掉危险信号 | 核心卖点失效 | 评测集覆盖 + prompt 示例；演示话术提前跑通 |
| LLM 回复越界（诊断、药物） | 伦理与合规风险 | prompt 护栏 + 页面免责声明；评测集加越界用例 |
| 跌倒检测误报/漏报 | 演示翻车 | 预录视频 + 调好的参数；现场实时摄像头只作展示 |
| 两人接口对不上 | 联调耗时 | 唯一契约 `POST /api/alerts` 在 D1 上午冻结 |

---

## 4. 测试方案

### 4.1 设计理念

每个任务都有明确的验收命令。规则类代码（症状合并、告警规则、跌倒状态机）零 LLM 依赖，用表驱动单测锁死；LLM 相关能力用小型评测集手动跑；演示路径用剧本逐条彩排。

### 4.2 三层评测

| 层 | 对象 | 数据 | 评分 | 运行时机 |
|---|---|---|---|---|
| **L1 组件** | 症状合并、告警规则与去抖、`raw_quote` 校验、跌倒状态机、事件存储、fall-mcp 工具（进程内 client）、API 契约 | 表驱动用例；跌倒用合成关键点序列 | 精确断言 | 每次提交（`pytest -m unit`） |
| **L2 抽取** | 症状抽取器 | `eval/extraction_cases.yaml` ≈ 30 条（中英混合，含否定、他人、无症状、危险信号） | 症状 canonical 集合精确匹配；危险信号召回必须 100% | 改 prompt 后（需 API Key） |
| **L3 演示剧本** | 端到端流程 | 第 6 章 G2 演示脚本 | 人工逐条核对子女端结果 | D2 下午彩排 |

### 4.3 L2 用例示例

```yaml
- say: "I've been a bit dizzy in the mornings for two days"
  expect: [dizziness]
- say: "我今天挺好的，就是没什么胃口"
  expect: [appetite_loss]
- say: "My husband has a bad cough, I'm fine though"
  expect: []
- say: "胸口有点闷，喘不太上气"
  expect: [chest_pain, shortness_of_breath]
  expect_alert: high
- say: "I didn't have a headache today"
  expect: []
```

### 4.4 测试目录

```
tests/
  unit/            # L1，离线（mock LLM）
  integration/     # FastAPI TestClient + 内存 SQLite
  fixtures/        # mock LLM 回放、合成关键点序列
eval/
  extraction_cases.yaml
  run_extraction_eval.py
```

pytest markers：`unit`、`integration`、`llm`（需要真实 API）。

---

## 5. 系统架构与模块设计

### 5.1 整体架构

```
┌─────────────────────────── 演示笔记本 (localhost) ───────────────────────────┐
│                                                                              │
│  ┌────────────────── web (FastAPI :8000) ──────────────────┐                 │
│  │ /elder  老人端（大按钮录音、自动播放）                    │                 │
│  │ /family 子女端（摘要 / 症状 / 告警 / 跌倒面板 / 对话）     │                 │
│  │                                                         │                 │
│  │  chat.service ─── transcribe / chat / tts ─────────────────▶ OpenAI API   │
│  │       │                                                 │                 │
│  │       └─(BackgroundTask)─▶ symptoms.extractor ─────────────▶ OpenAI API   │
│  │                             └▶ merge → rules → alerts   │                 │
│  │  alerts.bus ── SSE ─▶ /family                           │                 │
│  │  SQLite: data/app.db                                    │                 │
│  └───────────────────────────▲─────────────────────────────┘                 │
│      push: POST /api/alerts  │        │ pull: MCP (Streamable HTTP)          │
│                              │        ▼ (E5 问答 Agent，可选)                  │
│  ┌─────────── fall-mcp (独立进程 :8001, Starlette) ─────────┐                 │
│  │ /mcp     FastMCP tools: get_fall_events / get_event_snapshot /            │
│  │          get_monitor_status / start|stop_monitoring / analyze_video       │
│  │ /stream  MJPEG（子女端 <img> 嵌入）   /healthz            │                 │
│  │ MonitorController ── 后台线程:                           │                 │
│  │   OpenCV 摄像头 / demo.mp4 → YOLO11n-pose.track          │                 │
│  │   → FallStateMachine → EventStore(jsonl + snapshots)     │                 │
│  │   → (可选) vision_check ────────────────────────────────────▶ OpenAI API   │
│  │   → Reporter ── push ─┘                                  │                 │
│  └──────────────────────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────────────────┘
  Claude Desktop / Cursor ── stdio / Streamable HTTP ──▶ fall-mcp（同一套工具）
```

### 5.2 目录结构

```
AI-for-elder/
├── pyproject.toml
├── .env.example                  # OPENAI_API_KEY, CHAT_MODEL, ASR_MODEL, TTS_MODEL, LLM_PROVIDER ...
├── .gitignore                    # .env, data/, *.pt
├── README.md
├── DEV_SPEC.md
├── config/
│   ├── settings.yaml             # 模型、上下文轮数、告警去抖、跌倒参数
│   ├── symptoms.yaml             # 唯一数据源：症状枚举、显示名、危险等级
│   └── prompts/
│       ├── companion.txt         # 老人端人设
│       ├── greet.txt
│       ├── extract_symptoms.txt
│       ├── daily_summary.txt
│       └── fall_verify.txt
├── src/
│   ├── elder_companion/          # 负责人 A
│   │   ├── settings.py
│   │   ├── db.py                 # engine / session
│   │   ├── models.py             # Elder / Message / SymptomLog / Alert
│   │   ├── seed.py               # 演示老人画像 + 可选历史数据
│   │   ├── llm/{client.py, mock.py}
│   │   ├── chat/{service.py, context.py}
│   │   ├── symptoms/{schema.py, extractor.py, merge.py, rules.py}
│   │   ├── alerts/{service.py, bus.py}
│   │   ├── summary.py
│   │   └── web/
│   │       ├── app.py
│   │       ├── routes/{pages.py, chat.py, family.py, alerts.py}
│   │       ├── templates/{elder.html, family.html}
│   │       └── static/{elder.js, family.js, style.css}
│   └── fall_detector/            # 负责人 B（fall-mcp）
│       ├── pose.py               # YOLO11-pose 封装，输出每帧 tracks + keypoints
│       ├── rules.py              # 特征计算 + FallStateMachine（纯函数，可单测）
│       ├── events.py             # EventStore：jsonl 追加 + 内存索引 + 截图路径
│       ├── monitor.py            # MonitorController：检测线程启停、切源、状态快照、最新帧
│       ├── reporter.py           # POST /api/alerts + 可选视觉复核
│       ├── mcp_tools.py          # FastMCP 工具定义（6 个 tool + 1 个 resource）
│       ├── stream.py             # MJPEG 生成器
│       ├── server.py             # Starlette 组装：/mcp、/stream、/healthz；--stdio 模式
│       └── __main__.py           # python -m fall_detector --source demo/videos/fall_01.mp4（纯 CLI 调试）
├── docs/mcp_desktop.md           # fall-mcp 接入 Claude Desktop
├── demo/videos/                  # 预录跌倒 / 躺下 / 正常走动视频
├── data/                         # app.db, audio/, snapshots/（git 忽略）
├── eval/{extraction_cases.yaml, run_extraction_eval.py}
└── tests/{unit, integration, fixtures}/
```

### 5.3 模块依赖规则

```
web ──▶ chat, symptoms, alerts, summary ──▶ llm, models
symptoms.rules / symptoms.merge ──▶ 纯 Python（不依赖 llm / 网络）
fall_detector ──▶ 不 import elder_companion；只通过 HTTP 与主服务交互
fall_detector.rules ──▶ 纯 Python（输入关键点序列，输出事件），可离线单测
fall_detector.mcp_tools ──▶ 只调用 MonitorController / EventStore，不直接碰 YOLO
elder_companion.family_agent（E5）──▶ 只通过 MCP client 访问跌倒数据，不读 fall_events.jsonl
```

### 5.4 数据流

**老人说一句话**：
`elder.js 录音` → `POST /api/chat (multipart audio)` → `transcribe` → 存 `message(user)` → `context.build`（画像 + 近期症状 + 最近 10 轮）→ `chat` → 存 `message(assistant)` → 返回 `{user_text, reply_text}` → 前端显示文字 → `GET /api/tts/{message_id}` 取音频播放；同时 `BackgroundTask: extractor → merge → rules → alerts.create → bus.publish`。

**子女端实时告警**：
`family.js EventSource('/api/alerts/stream')` ← `alerts.bus`（进程内 `asyncio.Queue` 广播）← `alerts.create`（症状规则 / 跌倒上报）。

**跌倒告警**：
`fall_detector` 帧循环 → `pose.track` → `FallStateMachine.update` → `DOWN` 事件 → 截图保存到共享 `data/snapshots/` → （可选 `vision_check`）→ `POST /api/alerts` → SSE → 子女端弹窗 + 截图。

**今日摘要**：
`GET /api/summary/today` → 当日 messages + symptom_logs → `daily_summary` prompt → 缓存 10 分钟。

### 5.5 API 契约

| Method | Path | 请求 / 响应 | 负责 |
|---|---|---|---|
| GET | `/elder`、`/family` | 页面 | A |
| POST | `/api/chat` | multipart `audio` 或 JSON `{text}` → `{message_id, user_text, reply_text}` | A |
| POST | `/api/chat/greet` | → `{message_id, reply_text}` | A |
| GET | `/api/tts/{message_id}` | → `audio/mpeg` | A |
| GET | `/api/messages?limit=` | 对话记录 | A |
| GET | `/api/symptoms?days=7` | 症状日志（按天分组） | A |
| GET | `/api/summary/today` | `{summary, generated_at}` | A |
| GET | `/api/alerts` | 告警列表 | A |
| POST | `/api/alerts` | **跌倒服务调用（唯一跨进程契约，D1 上午冻结）** | A |
| POST | `/api/alerts/{id}/read` | 标记已读 | A |
| GET | `/api/alerts/stream` | SSE，`event: alert`，`data: Alert JSON` | A |
| POST | `/api/family/ask`（可选 E5） | `{question}` → `{answer, tool_calls}` | A |
| GET | `:8001/stream` | MJPEG 检测画面 | B |
| MCP | `:8001/mcp` | fall-mcp 工具（见 3.6）；stdio：`python -m fall_detector.server --stdio` | B |

```json
// POST /api/alerts
{"type": "fall", "level": "high", "title": "Fall detected",
 "content": "Person down for 3s in living room", "snapshot_path": "snapshots/20261001_101530.jpg"}
```

### 5.6 配置驱动

```yaml
# config/settings.yaml（节选）
llm:
  provider: ${LLM_PROVIDER:-openai}          # openai | mock
  chat_model: ${CHAT_MODEL:-gpt-4.1-mini}
  extract_model: ${EXTRACT_MODEL:-gpt-4.1-mini}
  asr_model: ${ASR_MODEL:-gpt-4o-transcribe}
  tts_model: ${TTS_MODEL:-gpt-4o-mini-tts}
  tts_voice: ${TTS_VOICE:-coral}
  tts_instructions: "Speak slowly, warmly and clearly, like a caring family member."
  timeout_s: 15
chat:
  history_turns: 10
  max_reply_tokens: 150
symptoms:
  merge_window_hours: 24
  alert_debounce_hours: 2
fall:
  aspect_ratio_threshold: 1.2
  torso_angle_deg: 60
  drop_ratio: 0.35
  drop_window_s: 0.6
  down_confirm_s: 3
  cooldown_s: 30
  vision_verify: false
  report_url: http://localhost:8000/api/alerts
  mcp:
    host: 127.0.0.1
    port: 8001
    events_path: data/fall_events.jsonl
    snapshots_dir: data/snapshots
    autostart_source: demo/videos/fall_01.mp4   # 启动即开始监控；null 表示等 start_monitoring
```

---

## 6. 项目排期（开发阶段）

> 原则：每个任务 ≈ 0.5–1.5h 可验收；完成后在标题与进度表标 ✅。任务格式：负责人 / 目标 / 修改文件 / 实现类与函数 / 验收标准 / 测试方法。

### 阶段总览（大阶段 → 目的）

| 阶段 | 负责 | 目的 | 时间 | 里程碑 |
|---|---|---|---|---|
| A 工程骨架 | A | 可启动、可配置、DB 就绪、契约冻结 | D1 上午 | 契约冻结 |
| B 对话核心 | A | 文本对话 + 人设 + 上下文 | D1 上午 | |
| C 语音链路 | A | 老人端语音对话 | D1 下午 | |
| D 症状日志 | A | 抽取 → 合并 → 告警 | D1 下午–晚 | **M1：说一句话 → 子女端出现症状** |
| E 子女端 | A（B 样式） | 摘要、时间线、告警 SSE、面板；可选 MCP 问答 | D2 上午 | |
| F 跌倒检测（fall-mcp） | B | 视频 → 关键点 → 状态机 → 事件/告警 → 画面 → MCP 工具 | D1 全天 – D2 上午 | **M2：播放视频 → 子女端弹跌倒告警**；**M3：MCP 工具可被 Claude Desktop 调用** |
| G 收尾 | A + B | UI 打磨、评测、演示脚本、彩排 | D2 下午 | **交付** |

### 时间线（两人并行）

| | A（功能） | B（视觉 / 前端） |
|---|---|---|
| **D1 上午** | A1–A3、B1–B3 | F1–F2（跑通 pose、准备演示视频） |
| **D1 下午** | C1–C3、D1–D2 | F3–F4（状态机 + 单测） |
| **D1 晚** | D3–D4 → **M1** | F5（事件存储 + 上报）联调契约 |
| **D2 上午** | E1–E4；E5（可选，F7 完成后） | F6（MJPEG）→ **M2**；F7–F8（MCP）→ **M3**；F9（可选） |
| **D2 下午** | G3、G4；协助 G1 | G1（样式）、G2、G4 |

### 📊 进度跟踪表 (Progress Tracking)

| 阶段 | 任务 | 状态 |
|---|---|---|
| A | A1 A2 A3 | ✅⬜⬜ |
| B | B1 B2 B3 | ⬜⬜⬜ |
| C | C1 C2 C3 | ⬜⬜⬜ |
| D | D1 D2 D3 D4 | ⬜⬜⬜⬜ |
| E | E1 E2 E3 E4 E5* | ⬜⬜⬜⬜⬜ |
| F | F1 F2 F3 F4 F5 F6 F7 F8 F9* | ⬜⬜⬜⬜⬜⬜⬜⬜⬜ |
| G | G1 G2 G3 G4 | ⬜⬜⬜⬜ |

### 📈 总体进度

`1 / 31`（* 为可选任务，不计入交付门槛）

---

## 阶段 A：工程骨架（目标：可启动、可配置、契约冻结）

### A1：uv 项目与目录骨架 ✅
- **负责人**：A
- **目标**：按 5.2 创建目录与 `pyproject.toml`（fastapi、uvicorn、sqlalchemy、openai、pydantic、pyyaml、python-multipart、jinja2、sse-starlette、mcp（E5 客户端与 fall-mcp 服务端共用）；可选组 `vision`: ultralytics、opencv-python）。
- **修改文件**：`pyproject.toml`、`.gitignore`、`.env.example`、`README.md`、`src/**/__init__.py`。
- **验收标准**：`uv sync` 成功；`uv run python -c "import elder_companion, fall_detector"` 通过。
- **测试方法**：`uv run pytest -q`（空测试集通过）。

### A2：Settings 加载
- **负责人**：A
- **目标**：读取 `config/settings.yaml`，支持 `${ENV:-default}` 展开；`.env` 自动加载；缺 `OPENAI_API_KEY` 且 `provider=openai` 时启动报错。
- **修改文件**：`src/elder_companion/settings.py`、`config/settings.yaml`、`tests/unit/test_settings.py`。
- **实现类/函数**：`Settings`（Pydantic）、`load_settings(path) -> Settings`。
- **验收标准**：环境变量覆盖默认值生效；缺 key 报出变量名。
- **测试方法**：`uv run pytest -q tests/unit/test_settings.py`。

### A3：数据模型、种子与 API 契约冻结
- **负责人**：A（与 B 确认契约）
- **目标**：建立 4 张表；种子写入 1 位演示老人（姓名、昵称、语言、画像：年龄、独居、高血压、爱好）；FastAPI 空壳启动，`POST /api/alerts` 先实现（让 B 能尽早联调）。
- **修改文件**：`db.py`、`models.py`、`seed.py`、`web/app.py`、`web/routes/alerts.py`、`alerts/service.py`。
- **实现类/函数**：`Elder`、`Message`、`SymptomLog`、`Alert`；`init_db()`、`seed_demo()`；`AlertIn` / `AlertOut` schema；`create_alert()`。
- **验收标准**：`uv run uvicorn elder_companion.web.app:app` 启动；`curl -X POST /api/alerts` 返回 201 并入库。
- **测试方法**：`uv run pytest -q tests/integration/test_alerts_api.py`。

---

## 阶段 B：对话核心（目标：文本对话有温度）

### B1：LLMClient 与 Mock
- **负责人**：A
- **目标**：封装 `chat(messages)`、`extract_json(messages, schema)`、`transcribe(file)`、`tts(text) -> bytes`、`vision_check(image, prompt)`；Mock 实现同接口（按关键词匹配 fixture 返回）；统一超时与异常类型 `LLMError`。
- **修改文件**：`llm/client.py`、`llm/mock.py`、`tests/unit/test_llm_mock.py`、`tests/fixtures/mock_llm.yaml`。
- **实现类/函数**：`BaseLLMClient`、`OpenAIClient`、`MockLLMClient`、`get_llm(settings)`。
- **验收标准**：`LLM_PROVIDER=mock` 下全部接口可离线返回。
- **测试方法**：`uv run pytest -q tests/unit/test_llm_mock.py`；手动 `uv run python -m elder_companion.llm.client --ping`（真实 key）。

### B2：人设 Prompt 与上下文构建
- **负责人**：A
- **目标**：编写 `companion.txt`（温和、短句、一次一问、跟随语言、禁止诊断与药物、危险情况建议联系家人/911）；`build_context()` 拼接画像 + 当前时间 + 近 48h 未解决症状 + 最近 10 轮。
- **修改文件**：`config/prompts/companion.txt`、`config/prompts/greet.txt`、`chat/context.py`、`tests/unit/test_context.py`。
- **实现类/函数**：`build_context(elder, history, recent_symptoms, now) -> list[dict]`。
- **验收标准**：单测断言上下文包含画像、症状回访信息、轮数截断正确。
- **测试方法**：`uv run pytest -q tests/unit/test_context.py`。

### B3：文本对话接口
- **负责人**：A
- **目标**：`POST /api/chat`（JSON `{text}`）与 `POST /api/chat/greet`；消息落库；LLM 失败返回固定安抚话术。
- **修改文件**：`chat/service.py`、`web/routes/chat.py`、`tests/integration/test_chat_api.py`。
- **实现类/函数**：`ChatService.reply(elder_id, text) -> ChatResult`、`ChatService.greet(elder_id)`。
- **验收标准**：curl 发送文本得到中文/英文对应回复；消息表有两条记录。
- **测试方法**：`uv run pytest -q tests/integration/test_chat_api.py`（mock）；手动真实 API 聊 5 轮检查人设。

---

## 阶段 C：语音链路（目标：老人端能说能听）

### C1：语音转写
- **负责人**：A
- **目标**：`/api/chat` 支持 multipart `audio`（webm / mp4 / wav），调用转写后走 B3 流程；空转写或过短返回 `{"need_retry": true}`。
- **修改文件**：`web/routes/chat.py`、`chat/service.py`、`tests/integration/test_chat_audio.py`。
- **实现类/函数**：`ChatService.reply_audio(elder_id, upload) -> ChatResult`。
- **验收标准**：上传 `tests/fixtures/hello.webm` 返回正确 `user_text`（真实 API）；mock 下流程通过。
- **测试方法**：`uv run pytest -q tests/integration/test_chat_audio.py`。

### C2：TTS
- **负责人**：A
- **目标**：`GET /api/tts/{message_id}` 生成并缓存 mp3（`data/audio/{id}.mp3`），带 `tts_instructions`（慢速、温和）；失败返回 204，前端只显示文字。
- **修改文件**：`web/routes/chat.py`、`chat/service.py`。
- **实现类/函数**：`ChatService.synthesize(message_id) -> Path`。
- **验收标准**：浏览器直接打开该 URL 可播放；二次请求命中缓存。
- **测试方法**：手动；`tests/integration/test_tts.py`（mock 返回静音 mp3）。

### C3：老人端页面
- **负责人**：A（B 在 G1 打磨样式）
- **目标**：`/elder` 页面——大圆按钮（按住说话 / 点击切换两种模式）、状态提示（在听 / 在想 / 在说）、最近 3 条对话气泡、字号 ≥ 24px、高对比度；进入页面点击「开始聊天」触发 greet 并解锁音频播放；隐藏的文字输入框兜底。
- **修改文件**：`web/templates/elder.html`、`web/static/elder.js`、`web/static/style.css`、`web/routes/pages.py`。
- **实现类/函数**：`startRecording()`、`stopAndSend()`、`playReply(messageId)`、`setStatus(state)`。
- **验收标准**：Chrome + Safari（笔记本）`localhost` 下完成一轮语音对话；端到端 < 5s。
- **测试方法**：手动；记录 5 轮延迟。

---

## 阶段 D：症状日志（目标：M1 —— 说一句话，子女端出现症状）

### D1：症状枚举与 Schema
- **负责人**：A
- **目标**：`config/symptoms.yaml`（canonical、中英显示名、alert 等级）；Pydantic `SymptomItem` / `SymptomExtraction`，导出 JSON Schema 供 Structured Outputs 使用，`canonical` 枚举从 yaml 生成。
- **修改文件**：`config/symptoms.yaml`、`symptoms/schema.py`、`tests/unit/test_symptom_schema.py`。
- **验收标准**：枚举外的 canonical 校验失败；导出的 schema 满足 strict 模式要求（所有字段 required、`additionalProperties: false`）。
- **测试方法**：`uv run pytest -q tests/unit/test_symptom_schema.py`。

### D2：抽取器
- **负责人**：A
- **目标**：`extract_symptoms.txt`（枚举清单、否定/他人不记录、`raw_quote` 必须原话、无症状返回空、5 个 few-shot）；`SymptomExtractor.extract(user_text, recent_turns)`；`raw_quote` 子串校验。
- **修改文件**：`config/prompts/extract_symptoms.txt`、`symptoms/extractor.py`、`eval/extraction_cases.yaml`、`eval/run_extraction_eval.py`。
- **实现类/函数**：`SymptomExtractor`、`verify_quote(item, text) -> bool`。
- **验收标准**：L2 评测集准确率 ≥ 90%，危险信号召回 100%。
- **测试方法**：`uv run python eval/run_extraction_eval.py`（需 key，输出逐条对错与汇总）。

### D3：合并与告警规则
- **负责人**：A
- **目标**：实现 3.4.3 的合并、告警等级、2h 去抖；纯函数优先，DB 操作集中在 service。
- **修改文件**：`symptoms/merge.py`、`symptoms/rules.py`、`alerts/service.py`、`tests/unit/test_symptom_merge.py`、`tests/unit/test_alert_rules.py`。
- **实现类/函数**：`merge_symptom(existing, item, now) -> MergeResult`、`alert_level(canonical, severity) -> Level | None`、`should_alert(last_alert_at, now)`。
- **验收标准**：表驱动用例覆盖：新增、24h 内合并、超窗新增、severity 取最大、`other` 按 label 合并、危险信号 high、severe 普通症状 medium、去抖生效。
- **测试方法**：`uv run pytest -q tests/unit/test_symptom_merge.py tests/unit/test_alert_rules.py`。

### D4：挂接对话链路与告警广播
- **负责人**：A
- **目标**：`/api/chat` 完成后 `BackgroundTasks` 执行抽取 → 合并 → 告警；`alerts.bus` 进程内广播（`asyncio.Queue` 订阅者列表），`GET /api/alerts/stream` SSE 输出。
- **修改文件**：`chat/service.py`、`web/routes/chat.py`、`alerts/bus.py`、`web/routes/alerts.py`、`tests/integration/test_symptom_pipeline.py`。
- **实现类/函数**：`process_message_symptoms(message_id)`、`AlertBus.publish()`、`AlertBus.subscribe()`。
- **验收标准**：**M1**——语音说「这两天早上起来头有点晕」→ `symptom_log` 出现 `dizziness`；说「胸口闷，喘不上气」→ SSE 客户端（`curl -N`）收到 high 告警；对话接口延迟不受影响。
- **测试方法**：`uv run pytest -q tests/integration/test_symptom_pipeline.py`（mock）；手动真实链路。

---

## 阶段 E：子女端（目标：一屏看清老人状态）

### E1：子女端数据接口
- **负责人**：A
- **目标**：`/api/symptoms?days=7`（按天分组、含 count、status、raw_quote）、`/api/messages`、`/api/alerts`、`/api/alerts/{id}/read`。
- **修改文件**：`web/routes/family.py`、`web/routes/alerts.py`、`tests/integration/test_family_api.py`。
- **验收标准**：种子数据下返回结构符合前端需要（字段在测试中断言）。
- **测试方法**：`uv run pytest -q tests/integration/test_family_api.py`。

### E2：今日摘要
- **负责人**：A
- **目标**：`daily_summary.txt`（2–3 句：精神状态、提到的不适、建议关注，不诊断）；10 分钟缓存；无对话时返回「今天还没有聊天」。
- **修改文件**：`summary.py`、`config/prompts/daily_summary.txt`、`web/routes/family.py`。
- **实现类/函数**：`DailySummary.get(elder_id, date)`。
- **验收标准**：聊 5 轮后摘要能准确提到症状，且不含诊断用语。
- **测试方法**：手动；mock 单测覆盖缓存逻辑。

### E3：种子历史数据
- **负责人**：A
- **目标**：`seed.py --with-history` 生成过去 6 天的对话与症状（膝盖疼反复出现、失眠好转等），让时间线演示饱满。
- **修改文件**：`seed.py`。
- **验收标准**：子女端时间线展示 7 天数据且故事连贯。
- **测试方法**：`uv run python -m elder_companion.seed --reset --with-history`。

### E4：子女端页面
- **负责人**：A（功能）/ B（样式）
- **目标**：`/family` 页面——顶部今日摘要卡；左侧症状时间线（按天、严重度色块、次数、点开看原话）；右侧告警列表（跌倒带截图缩略图）；跌倒检测面板 `<img src=":8001/stream">`；`EventSource` 收到告警时顶部红色横幅 + 提示音；对话记录折叠区。
- **修改文件**：`web/templates/family.html`、`web/static/family.js`、`web/static/style.css`。
- **实现类/函数**：`loadSummary()`、`loadSymptoms()`、`loadAlerts()`、`connectAlertStream()`、`showAlertBanner(alert)`。
- **验收标准**：老人端说出危险症状 → 子女端 ≤ 10s 内弹横幅（抽取耗时在内）；页面在 1280px 与 390px 宽度下均可用。
- **测试方法**：手动（双窗口并排演练）。

### E5（可选）：子女端「问问 AI」
- **负责人**：A（依赖 F7）
- **目标**：`POST /api/family/ask`——启动时通过 MCP client（Streamable HTTP）连 fall-mcp 并 `list_tools()`，转换为 OpenAI function tools；与本地工具 `get_symptoms(days)`、`get_today_summary()` 合并；最多 4 轮 tool call 循环后给出回答；fall-mcp 不可用时自动去掉跌倒工具并在回答中说明。子女端加一个输入框。
- **修改文件**：`src/elder_companion/family_agent.py`、`web/routes/family.py`、`web/templates/family.html`、`web/static/family.js`、`tests/integration/test_family_agent.py`。
- **实现类/函数**：`FamilyAgent.ask(question) -> AskResult`、`mcp_tools_to_openai(tools)`、`LocalTools`。
- **验收标准**：问「妈妈今天有没有摔倒？最近哪里不舒服？」→ 回答同时引用跌倒事件时间与症状日志；`tool_calls` 中可见 `get_fall_events` 与 `get_symptoms`。
- **测试方法**：`uv run pytest -q tests/integration/test_family_agent.py`（mock LLM 固定 tool_calls + 进程内 fall-mcp）；手动真实链路。

---

## 阶段 F：跌倒检测 fall-mcp（目标：M2 —— 播放视频，子女端弹跌倒告警；M3 —— 工具以 MCP 对外）

### F1：环境与 Pose 跑通
- **负责人**：B
- **目标**：安装 `vision` 依赖组；`pose.py` 封装 `YOLO("yolo11n-pose.pt").track(frame, persist=True)`，输出 `list[PersonPose(track_id, bbox, keypoints[17,3])]`；可视化脚本画骨架。
- **修改文件**：`src/fall_detector/pose.py`、`src/fall_detector/__main__.py`。
- **验收标准**：`python -m fall_detector --source demo/videos/walk.mp4 --show` 窗口显示骨架与 track_id，FPS ≥ 12。
- **测试方法**：手动；终端打印 FPS。

### F2：演示视频素材
- **负责人**：B
- **目标**：录制/收集 ≥ 3 段视频：正常走动、缓慢躺到沙发（不应报）、跌倒（应报）；固定机位（模拟客厅摄像头高度）。可用 Le2i / UR Fall 公开数据集片段补充（注意许可）。
- **修改文件**：`demo/videos/*.mp4`、`demo/videos/README.md`（来源与预期结果）。
- **验收标准**：每段视频标注预期结果。
- **测试方法**：人工检查。

### F3：特征计算
- **负责人**：B
- **目标**：由关键点计算：bbox 宽高比、躯干角（肩中点–髋中点与竖直方向夹角）、髋中点高度（归一化到身高）、下落速度；低置信度关键点处理。
- **修改文件**：`src/fall_detector/rules.py`、`tests/unit/test_fall_features.py`。
- **实现类/函数**：`compute_features(pose, ts) -> Features`。
- **验收标准**：合成的「直立」「水平」关键点得到预期角度与宽高比。
- **测试方法**：`uv run pytest -q tests/unit/test_fall_features.py`。

### F4：跌倒状态机
- **负责人**：B
- **目标**：实现 3.5 状态机（参数来自 settings），每个 track 独立实例，冷却期不重复报。
- **修改文件**：`src/fall_detector/rules.py`、`tests/unit/test_fall_state_machine.py`、`tests/fixtures/pose_sequences/*.json`。
- **实现类/函数**：`FallStateMachine.update(features) -> FallEvent | None`。
- **验收标准**：合成序列：快速倒地并保持 3s → 报 1 次；缓慢躺下 → 不报；倒地 1s 后起身 → 不报；持续倒地 60s → 冷却期内只报 1 次。三段演示视频结果与标注一致。
- **测试方法**：`uv run pytest -q tests/unit/test_fall_state_machine.py`；`python -m fall_detector --source demo/videos/fall_01.mp4 --dry-run`。

### F5：事件存储、截图与上报
- **负责人**：B
- **目标**：`EventStore` 追加写 `data/fall_events.jsonl`（`event_id, ts, track_id, confidence, source[live|offline], verified, snapshot_id`）并维护内存索引；事件触发时保存带骨架标注的截图；`live` 事件调用 `POST /api/alerts`（`ref_id=event_id`），主服务不可达时本地日志 + 重试 3 次，不阻塞检测线程。
- **修改文件**：`src/fall_detector/events.py`、`src/fall_detector/reporter.py`、`tests/unit/test_event_store.py`、`tests/unit/test_reporter.py`。
- **实现类/函数**：`EventStore.add(event, frame) -> FallEventRecord`、`EventStore.query(since, limit)`、`EventStore.snapshot_path(event_id)`、`Reporter.report(record)`。
- **验收标准**：重启进程后历史事件可查询；主服务运行时播放跌倒视频 → `alert` 表出现 fall 记录，子女端 SSE 收到。
- **测试方法**：`uv run pytest -q tests/unit/test_event_store.py tests/unit/test_reporter.py`（`httpx.MockTransport`）；联调。

### F6：MonitorController 与服务组装（MJPEG）
- **负责人**：B
- **目标**：`MonitorController` 管理检测后台线程（`start(source, loop)` / `stop()` / `status()` / `latest_frame()`，线程安全，切源时先停后启）；`server.py` 组装 Starlette：`/stream`（带骨架与 STANDING / FALLING / DOWN 状态文字的 MJPEG，视频播完自动循环）、`/healthz`；启动时按 `autostart_source` 自动开始监控。
- **修改文件**：`src/fall_detector/monitor.py`、`src/fall_detector/stream.py`、`src/fall_detector/server.py`、`tests/unit/test_monitor.py`。
- **实现类/函数**：`MonitorController`、`MonitorStatus`、`mjpeg_generator(controller)`、`create_app(settings) -> Starlette`。
- **验收标准**：**M2**——`uv run python -m fall_detector.server` 启动后子女端面板实时显示检测画面；跌倒视频播放时子女端弹出跌倒告警 + 截图。
- **测试方法**：`uv run pytest -q tests/unit/test_monitor.py`（用 fake pose 源）；手动联调。

### F7：fall-mcp 工具
- **负责人**：B
- **目标**：用 FastMCP 实现 3.6 的 6 个工具 + `fall://live/snapshot` resource，挂载到 `server.py` 的 `/mcp`（Streamable HTTP）；`--stdio` 模式只起 MCP（不起 MJPEG）；参数用类型注解生成 schema，错误映射为 `isError=true` 的可读信息；`get_event_snapshot` 返回 `ImageContent`。
- **修改文件**：`src/fall_detector/mcp_tools.py`、`src/fall_detector/server.py`、`tests/integration/test_fall_mcp.py`。
- **实现类/函数**：`build_mcp(controller, store) -> FastMCP`；工具函数 `get_fall_events`、`get_event_snapshot`、`get_monitor_status`、`start_monitoring`、`stop_monitoring`、`analyze_video`。
- **验收标准**：MCP Inspector（`npx @modelcontextprotocol/inspector`）连 `http://127.0.0.1:8001/mcp` 可列出并调用全部工具；`analyze_video(demo/videos/fall_01.mp4)` 返回 ≥ 1 个事件，`analyze_video(demo/videos/lie_down.mp4)` 返回 0 个；不存在的路径返回工具错误而非崩溃。
- **测试方法**：`uv run pytest -q tests/integration/test_fall_mcp.py`（MCP SDK 进程内 client + fake pose 源）；Inspector 手动验证。

### F8：Claude Desktop 联调
- **负责人**：B
- **目标**：写 `docs/mcp_desktop.md`（`claude_desktop_config.json` 的 stdio 配置示例，含 `uv run --directory` 绝对路径）；在 Claude Desktop 中问「今天有没有检测到摔倒？给我看截图」完成调用。
- **修改文件**：`docs/mcp_desktop.md`。
- **验收标准**：Claude Desktop 中能列出 fall-mcp 工具，并返回事件与截图。
- **测试方法**：手动；录屏作为演示加分素材。

### F9（可选）：视觉模型二次确认
- **负责人**：B
- **目标**：`vision_verify: true` 时，事件截图交给 GPT 视觉模型（`fall_verify.txt`：「图中是否有人摔倒在地？只回答 JSON」），结果写入事件 `verified` 字段；否定则告警降级为 medium 或不推送。
- **修改文件**：`src/fall_detector/reporter.py`、`config/prompts/fall_verify.txt`。
- **验收标准**：跌倒截图确认通过；躺沙发截图被否决。
- **测试方法**：手动对 3 张截图验证。

---

## 阶段 G：收尾（目标：演示稳定、可讲述）

### G1：UI 打磨与适配
- **负责人**：B（A 配合）
- **目标**：老人端：大字、高对比、按钮动效、状态插画；子女端：卡片布局、严重度配色、告警横幅；免责声明（「本产品不提供医疗诊断」）。
- **修改文件**：`web/static/style.css`、两个模板。
- **验收标准**：iPad / 手机尺寸（390px）下无横向滚动；老人端一眼能看出「按这里说话」。
- **测试方法**：浏览器设备模拟 + 真机。

### G2：演示脚本与素材
- **负责人**：B（A 审核）
- **目标**：写 `demo/SCRIPT.md`（下方草案细化到每句台词、预期画面、兜底方案），准备备用录屏视频（网络全挂时播放）。
- **修改文件**：`demo/SCRIPT.md`、`demo/backup_recording.mp4`。
- **验收标准**：完整演示 ≤ 5 分钟，每一步都有兜底。
- **测试方法**：彩排。

### G3：一键启动与 README
- **负责人**：A
- **目标**：`scripts/dev_up.ps1`：初始化 DB + 种子 → 启动 web → 启动 fall_detector；README 写清环境、`.env`、启动、演示账号/URL。
- **修改文件**：`scripts/dev_up.ps1`、`README.md`。
- **验收标准**：新克隆的仓库按 README 10 分钟内跑起来。
- **测试方法**：在队友电脑上从零执行一遍。

### G4：全链路验收与彩排
- **负责人**：A + B
- **目标**：跑完 L1 + L2；按演示脚本彩排 ≥ 3 遍，记录 2.5 指标；准备 mock 兜底（`LLM_PROVIDER=mock` 下演示剧本可完整回放）。
- **修改文件**：`DEV_SPEC.md`（进度与指标收口）。
- **验收标准**：`uv run pytest -q` 全绿；2.5 表填写；进度表全部 ✅。
- **测试方法**：`uv run pytest -q && uv run python eval/run_extraction_eval.py`。

### 演示脚本（草案）

| # | 操作 | 预期 |
|---|---|---|
| 1 | 打开老人端，点「开始聊天」 | AI 按时段问候，并回访昨天的膝盖疼（种子数据） |
| 2 | 老人：“Much better today, but I didn't sleep well last night.” | AI 关心睡眠；子女端新增 `insomnia` |
| 3 | 老人：“这两天早上起来头有点晕” | AI 追问细节；子女端 `dizziness` 出现在时间线 |
| 4 | 老人：“My chest feels tight and I can't catch my breath” | AI 安抚并建议联系家人/911；子女端弹出 **high** 告警 |
| 5 | 切到子女端，播放跌倒视频 | 检测画面显示 FALLING → DOWN；弹出跌倒告警 + 截图 |
| 6 | 子女端刷新今日摘要 | 摘要覆盖睡眠、头晕、胸闷告警 |
| 7 | 子女端「问问 AI」：“Did Mom fall today? Anything else I should know?”（E5） | 回答引用跌倒时间 + 症状；展示 tool_calls |
| 8 | 切到 Claude Desktop 问同样问题（F8） | 通过 fall-mcp 返回事件与截图——展示「能力可复用」 |

### 交付里程碑

| 里程碑 | 完成阶段 | 时间 | 可演示内容 |
|---|---|---|---|
| 契约冻结 | A | D1 上午 | `POST /api/alerts` 可用，B 可联调 |
| M1 | A–D | D1 晚 | 语音对话 + 症状日志 + 危险告警（SSE） |
| M2 | E、F1–F6 | D2 上午 | 子女端完整 + 跌倒告警 |
| M3 | F7–F8（+E5） | D2 中午 | fall-mcp：Inspector / Claude Desktop 调用；（可选）子女端问答 Agent |
| 交付 | G | D2 傍晚 | 打磨后的 UI + 演示脚本 + 兜底方案 |

---

## 7. 可扩展性与未来展望

- **Realtime 语音**：OpenAI Realtime API（WebRTC）实现 <1s 语音对语音与打断；转写旁路继续喂症状抽取。
- **更多视觉 MCP 工具**：活动量统计（今天走动了多久）、久坐/长时间未出现提醒、夜间起夜次数——在 fall-mcp 基础上扩展为 home-vision-mcp。
- **更鲁棒的跌倒识别**：ST-GCN / PoseC3D（NTU RGB+D `falling down`）替换规则；自家场景数据微调；多摄像头。
- **非视觉传感**：毫米波雷达（卫生间/卧室隐私场景）、智能手表跌倒与心率。
- **用药提醒与依从性**：定时提醒 + 对话中确认「吃了没」，记录依从率给子女。
- **认知与情绪趋势**：长期追踪孤独感、情绪低落、重复提问（认知衰退早期信号），周报推送。
- **长期记忆**：老人的家人、往事、喜好写入记忆库（向量检索），陪伴更有「熟人感」。
- **通知通道**：短信 / 邮件 / App 推送；紧急情况一键呼叫子女。
- **多家庭与权限**：账号体系、多子女共同看护、医生只读视图。
- **合规**：HIPAA 相关数据加密与审计（进入美国市场必需）。

---

## 8. 附录：设计决策记录（ADR）

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| 1 | 语音采用「录音 → 转写 → Chat → TTS」串行管线 | 浏览器 Web Speech；Realtime API | 质量与开发成本平衡；症状日志天然需要文本；Realtime 留作加分项 |
| 2 | 全部模型走 OpenAI，模型名配置化 | 多供应商 / 本地模型 | 演示在美国无网络限制；一套 SDK 覆盖 LLM/ASR/TTS/Vision |
| 3 | 不用 LangGraph / Agent 框架 | LangGraph（rift 同款） | 老人端无多步任务流；2 天工期内框架是负收益 |
| 4 | 症状抽取异步、不在对话关键路径 | 同步抽取 / 一次调用同时回复+抽取 | 对话延迟优先；抽取失败不影响陪伴 |
| 5 | LLM 只抽取，归一化/合并/告警由代码 + `symptoms.yaml` 决定 | LLM 直接判断是否告警 | 可测试、可解释；危险信号召回不依赖 LLM 判断 |
| 6 | Structured Outputs strict + `raw_quote` 原话校验 | 自由 JSON / 正则解析 | 保证格式合法；抑制幻觉症状 |
| 7 | 跌倒检测 = YOLO11-pose + 时序规则状态机；视觉 LLM 仅做可选复核 | 单帧 fall 检测模型；ST-GCN | 开箱可用、可解释、可调参；下落速度可区分躺下与摔倒 |
| 8 | 跌倒检测独立进程，做成 **fall-mcp**：查询/控制走 MCP 工具，实时告警仍走 push（`POST /api/alerts`） | 纯 HTTP 服务；纯 MCP（告警也靠客户端轮询） | MCP 是客户端拉取模型，不适合实时告警；查询能力协议化后可被子女端 Agent、Claude Desktop 复用；两人并行、YOLO 依赖隔离 |
| 13 | fall-mcp 同一 Starlette 进程挂 `/mcp` + `/stream` + `/healthz`；stdio 模式供桌面客户端 | MJPEG 与 MCP 分两个进程 | 共享同一个 MonitorController 与最新帧，避免跨进程同步 |
| 14 | fall-mcp 是跌倒事件唯一数据源；主服务 alert 表只存告警副本（`ref_id`） | 主服务存全部事件 | 离线分析事件不产生告警，但仍可查询；职责清晰 |
| 9 | 实时推送用 SSE | WebSocket | 单向推送足够，实现与重连更简单 |
| 10 | 前端 Jinja2 + 原生 JS，不做构建 | React / Vite | 两个页面、2 天工期；重点在 AI 能力 |
| 11 | 写死 1 位老人 + 1 位子女，不做登录 | 账号体系 | 控制范围；演示聚焦核心价值 |
| 12 | 演示以预录视频 + 种子历史数据为主，mock 兜底 | 全部现场实时 | 演示可复现，避免网络/光线导致翻车 |
