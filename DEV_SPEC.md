# Developer Specification (DEV_SPEC)

> Project: **Elder Companion Agent** (an AI companion agent for older adults living alone, plus a family care dashboard)
> Version: v0.1 (design finalized, development in progress)
> Timeline: 2-day MVP; demo location: United States; model provider: OpenAI
> Team: **A (features / lead dev)** — backend, chat, voice, symptom log, family dashboard data; **B (vision / frontend)** — fall detection, UI polish, demo materials

## Table of Contents

1. Project Overview
2. Key Features
3. Technology Choices & Technical Analysis
4. Testing Strategy
5. System Architecture & Module Design
6. Project Schedule (Development Stages)
7. Extensibility & Future Work
8. Appendix: Architecture Decision Records (ADR)

---

## 1. Project Overview

What older adults living alone lack most is *someone to talk to*; what their adult children worry about most is *a health problem nobody notices*. This project addresses both sides with a single AI agent:

| Side | User | Form factor | Core value |
|---|---|---|---|
| **Elder app** `/elder` | Older adult | Phone/tablet browser; one big button for voice chat | Someone to talk to and who cares, anytime; zero learning curve |
| **Family dashboard** `/family` | Adult children | Browser dashboard | Auto-organized symptom log, red-flag alerts, fall detection, daily summary |

The elder never has to "fill out a health form". When the agent hears something like "I've been a bit dizzy in the mornings for the past couple of days" during casual chat, it automatically extracts a structured symptom record and syncs it to the family; if it hears "my chest feels tight", it alerts them immediately. The family side is also connected to a home camera (a video file for the demo) to detect falls and push alerts with snapshots.

### Design Philosophy

1. **Companionship First, Invisible Logging**
   The elder app only talks. It never pops up forms or asks "please rate your symptom severity from 1 to 10". Symptom extraction runs asynchronously in the background and never affects chat latency or experience.
2. **LLM Understands, Code Decides**
   The LLM only outputs "which symptoms were mentioned in this utterance" (structured JSON). Normalization, de-duplication/merging, whether to alert, and alert level are all handled by deterministic code plus rules in `config/symptoms.yaml` — testable, explainable, and rules can change without touching prompts.
3. **Care, Not Diagnosis**
   The agent only shows concern, asks follow-up questions, and suggests telling family or seeing a doctor; it never diagnoses or recommends medication. Red-flag detection is backstopped by code rules rather than relying on the LLM's judgment.
4. **Vision as an MCP Server**
   Fall detection is a standalone **fall-mcp** service. Query and control capabilities (events, snapshots, status, video analysis) are exposed as MCP tools usable by any MCP client (this project's family Q&A agent, Claude Desktop, Cursor). Real-time alerts are pushed (`POST /api/alerts`) because MCP is a client-pull model. A and B develop in parallel, and a crashed detector never affects chat.
5. **Demo-Deterministic**
   Every external dependency (OpenAI, camera) has a mock or pre-recorded fallback; the critical demo paths (symptom log, red-flag alert, fall alert) must be 100% reproducible.

### Non-Goals (v1)

- No medical diagnosis, medication advice, or integration with hospital/EHR systems
- No sign-up/login or multi-family support (one hard-coded elder + one family member, direct URLs)
- No native app and no push notifications (real-time alerts inside the dashboard page are enough)
- No OpenAI Realtime speech-to-speech (moved to Chapter 7)
- No dialect-recognition tuning, no local ASR/TTS
- Fall detection does not aim for real-world robustness (the demo relies mainly on pre-recorded video)

---

## 2. Key Features

### 2.1 Elder App

- **Voice chat**: hold to talk → transcription → reply → read aloud automatically; follows English or Chinese automatically.
- **A warm persona**: addresses the elder by nickname, uses short sentences, asks one question at a time, remembers the elder's background (profile) and recent conversations.
- **Proactive care**: at conversation start or when idle, proactively asks about meals, sleep, medication, and mood.
- **Safe handling of health topics**: express concern + ask for details (where, how long, how bad) + suggest telling family / seeing a doctor.

### 2.2 Symptom Log (Highlight)

- Every elder message is processed asynchronously in the background to extract symptoms → structured `symptom_log`.
- Symptoms are normalized to an enum (`dizziness`, `chest_pain`, …); repeats of the same symptom within 24h are merged and counted, forming a timeline.
- The original words are kept as `raw_quote`, so family can see exactly what the elder said, preventing the LLM from "filling in the blanks".
- **Red-flag rules**: chest pain, shortness of breath, one-sided weakness/slurred speech, confusion, falls, self-harm thoughts → high-priority alerts pushed in real time.

### 2.3 Family Dashboard

- Today's summary (2–3 natural-language sentences: mood, discomforts mentioned, things to watch)
- Symptom timeline (grouped by day, severity color chips, occurrence counts, original quotes)
- Alert center (symptom / fall types; falls include a snapshot), real-time pop-ups via SSE
- Fall-detection panel (live detection view via MJPEG + recent events)
- Conversation history (read-only)

### 2.4 Fall Detection

- YOLO11-pose keypoints + state-machine rules (fall speed, torso angle, time spent on the ground).
- Input: camera or video file; output: snapshot + alert event.
- Optional: send the snapshot to a GPT vision model for a second opinion to reduce false positives.
- **Exposed as an MCP server (fall-mcp)**: six tools — `get_fall_events`, `get_event_snapshot`, `get_monitor_status`, `start_monitoring` / `stop_monitoring`, `analyze_video`; Streamable HTTP (primary) + stdio (Claude Desktop).
- "Ask AI" on the family dashboard (optional): "Did Mom fall today? Has anything been bothering her lately?" — the agent calls the fall tools over MCP plus local symptom tools and answers with both.

### 2.5 Key Demo Metrics (to be filled in during Stage G)

| Metric | Target | Measured |
|---|---|---|
| Voice round-trip end-to-end latency P50 | < 4s | – |
| Symptom-extraction eval accuracy (symptom name + red-flag) | ≥ 90% | – |
| Red-flag recall (eval set) | 100% | – |
| Demo-video fall detections / false positives | all detected / 0 false positives | – |

---

## 3. Technology Choices & Technical Analysis

### 3.1 Overall Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language & packaging | Python 3.12 + uv | Consistent with the rift project; fall detection depends on the Python ecosystem |
| Web backend | FastAPI + Uvicorn | Serves the API, pages, and SSE |
| Frontend | Jinja2 + vanilla JS + CSS | Two pages, no build tooling — the least effort for a 2-day build |
| Real-time push | SSE (`/api/alerts/stream`) | One-way push is enough; simpler than WebSocket, with native browser reconnect |
| Storage | SQLite + SQLAlchemy 2.x | Sufficient for a single-machine demo |
| LLM | OpenAI Chat Completions (`CHAT_MODEL`) | Chat, summaries |
| Structured extraction | OpenAI Structured Outputs (`response_format=json_schema`, strict) | Guarantees valid JSON for symptom extraction |
| ASR | OpenAI `gpt-4o-transcribe` (fallback `whisper-1`) | More robust to accents and older speakers' pace than browser Web Speech |
| TTS | OpenAI `gpt-4o-mini-tts` | Supports `instructions` to control pace/tone (slow, warm) |
| Pose estimation | Ultralytics YOLO11n-pose | Real-time on CPU; pretrained weights out of the box |
| Video | OpenCV | Read camera/video, draw skeletons, encode MJPEG |
| MCP | Official `mcp` Python SDK (FastMCP) | fall-mcp: Streamable HTTP (primary) + stdio (desktop clients); same Starlette app as the MJPEG stream |
| Quality | pytest, ruff | All offline unit tests use the mock LLM |

> All model names live in `.env`. Before starting, confirm available models in the OpenAI console and update the defaults.

### 3.2 Technical Analysis: Voice Interaction Pipeline

**Option comparison**

| Option | Latency | Dev cost | Text available for the symptom log | Verdict |
|---|---|---|---|---|
| ① Browser Web Speech API (ASR) + `speechSynthesis` (TTS) | Low | Lowest | Yes | ❌ Unreliable recognition, robotic voice, inconsistent between Safari and Chrome |
| ② **Upload recording → OpenAI transcription → Chat → TTS (sequential pipeline)** | Medium (3–4s) | Low | Yes | ✅ **Chosen for the MVP** |
| ③ OpenAI Realtime API (speech-to-speech, WebRTC) | Lowest (<1s) | High (session management, barge-in, tool calls, side-channel transcription) | Requires enabling transcription separately | ⏳ Stretch goal in Chapter 7 |

**Latency budget (option ②)**

| Step | Estimate | Optimization |
|---|---|---|
| Upload recording (webm/opus, ~5s of speech ≈ 50KB) | 0.1–0.3s | Opus compression |
| Transcription | 0.5–1.2s | Use `gpt-4o-transcribe`; optionally pass a `language` hint |
| Chat reply (~60 tokens) | 0.8–1.5s | Cap `max_tokens`; prompt asks for short sentences; non-reasoning mini model |
| TTS | 0.6–1.2s | mini-tts; **return text first, fetch audio asynchronously** |
| Symptom extraction | 0 (async, off the critical path) | FastAPI `BackgroundTasks` |
| **Total** | **≈ 2.5–4s** | "Listening / thinking" animations on the frontend mask the wait |

**Key implementation points**

- Frontend `MediaRecorder` (`audio/webm;codecs=opus`; Safari falls back to `audio/mp4`): hold to record, release to upload.
- Microphone access requires a secure context: use `http://localhost` or HTTPS for the demo (phones need HTTPS; see risks in 3.9).
- Browser autoplay restrictions: audio playback after the first user interaction (pressing the button) is allowed, which our flow satisfies.
- `POST /api/chat/audio` returns `{message_id, user_text, reply_text, fallback, need_retry}`; the frontend then fetches `GET /api/tts/{message_id}` (mp3 cached in `data/audio/`, served `no-cache` + ETag so a DB reset never replays stale audio).
- **Silence**: the ASR model invents sentences for silent clips, so the page measures mic level while recording and never uploads a clip whose peak RMS stays below a speech threshold.
- **Script**: without a hint, Mandarin comes back in Traditional characters; `llm.asr_prompt` (a description nobody would say aloud) steers it to Simplified, and transcripts that merely echo that prompt are discarded.
- **One question per reply** is enforced in code (`chat/postprocess.py`): the prompt rule alone was ignored about half the time in Chinese.

### 3.3 Technical Analysis: Conversation Orchestration

- **No LangGraph / agent framework**: the elder app has no multi-step task flow (unlike rift's booking state machine) — it is just "persona + context + single generation". An orchestration framework would be a net negative.
- **Context composition**: `system(persona prompt + elder profile + current time + recent symptom summary)` + last 10 turns + current input.
  - Injecting recent symptoms lets the agent follow up naturally: "Maggie, yesterday you said your knee hurt — is it any better today?" This is key to the feeling of companionship.
- **Proactive greeting**: opening the elder app calls `POST /api/chat/greet`, which generates an opener based on time of day (morning/afternoon/evening) + symptoms not yet followed up on.
- **Safety guardrails (two layers)**:
  1. Prompt layer: no diagnosis, no drug names or dosage advice; for red-flag symptoms, reassure + suggest contacting family immediately / calling 911.
  2. Code layer: when symptom extraction hits a red flag → alert the family (independent of whether the LLM's reply was appropriate).

### 3.4 Technical Analysis: Symptom Extraction & Logging

#### 3.4.1 Symptom Enum (`config/symptoms.yaml`, single source of truth)

| canonical | Display name | Red flag |
|---|---|---|
| `chest_pain` | Chest pain / 胸痛胸闷 | ✅ high |
| `shortness_of_breath` | Shortness of breath / 呼吸困难 | ✅ high |
| `numbness_weakness` | One-sided numbness or weakness / 单侧麻木无力 | ✅ high |
| `slurred_speech` | Slurred speech / 说话不清 | ✅ high |
| `confusion` | Confusion / 意识混乱 | ✅ high |
| `fall` | Fall / 摔倒 | ✅ high |
| `self_harm` | Self-harm thoughts / 自伤念头 | ✅ high |
| `dizziness` `headache` `fatigue` `insomnia` `cough` `fever` `nausea` `stomach_pain` `joint_pain` `back_pain` `low_mood` `loneliness` `memory_issue` `appetite_loss` | … | ❌ (→ medium alert when `severity=severe`) |
| `other` | Free-text `label` | ❌ |

Display names are bilingual (English / Chinese) because the elder may speak either language.

#### 3.4.2 Extractor Output Contract (Structured Outputs, strict)

Input: extraction prompt (including the enum list) + last 3 turns (for coreference such as "still the same", "it hurts again") + the elder's current utterance.

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

- No symptom mentioned → `symptoms: []` (the majority case; the model must not "fill in the blanks").
- Negations and other people: "I didn't have a headache", "my husband has a cough" → not recorded (covered by the prompt + eval set).
- `raw_quote` must be a substring of the original utterance; entries that fail this code check are dropped (anti-hallucination).
- `status=improved/resolved` lets the dashboard show "improving".

#### 3.4.3 Merge & Alert Rules (code)

```
for s in extraction.symptoms:
    validate(raw_quote ⊂ user_text) else drop
    existing = find(elder, canonical, last_seen within 24h)   # `other` compares by label
    if existing: count += 1; last_seen = now; severity = max(...); status = s.status
    else:        insert new row
    level = rules.alert_level(canonical, severity)           # reads symptoms.yaml
    if level and not recently_alerted(canonical, 2h):         # alert debounce
        alerts.create(type=symptom, level, content, ref=symptom_id)
```

### 3.5 Technical Analysis: Fall Detection

**Option comparison**

| Option | Quality | Cost | Verdict |
|---|---|---|---|
| Single-frame object detection (YOLO "fall" models from Roboflow/HF) | Easily mistakes "lying in bed / on the sofa" for a fall | Low | ❌ |
| **YOLO11-pose keypoints + temporal rule state machine** | Explainable and tunable; fall speed separates "lying down" from "falling" | Low | ✅ **Chosen for the MVP** |
| Keypoints + ST-GCN / PoseC3D (NTU `falling down` class) | More robust | Medium (weights, mmaction2 dependency) | ⏳ Chapter 7 |
| Multimodal LLM judging every frame | Flexible | Slow, expensive | Only as an **optional second check after an alert** |

**Detection state machine** (one instance per tracked person; parameters in `settings.yaml`)

```
STANDING ──(A: bbox aspect ratio > 1.2  or  torso angle from vertical > 60°)──▶ candidate
        and (B: hip midpoint drops > 0.35 × body height within ≤ 0.6s)    ──▶ FALLING
FALLING ──(stays near-horizontal ≥ 3s and hip displacement < threshold)──▶ DOWN ⇒ fire alert (snapshot)
FALLING ──(back upright within 3s)──▶ STANDING (no alert)
DOWN    ──(back upright)──▶ STANDING; no repeat alert for the same track within 30s
```

- Slowly lying down fails condition B (fall speed) → no alert. This is the core of distinguishing "lying down" from "falling".
- Frames with keypoint confidence < 0.3 are skipped; person boxes get a track_id from `model.track(persist=True)`.
- Performance: YOLO11n-pose runs at ~15–25 FPS on a laptop CPU (640 input), enough for the demo; processing every 2nd frame is the fallback.
- Privacy: video frames are processed locally and never uploaded; only a single snapshot is uploaded, and only when the second check is enabled.

### 3.6 Technical Analysis: fall-mcp Service Design

**Why MCP, and where the boundary is**

| Need | Good fit for MCP? | Approach |
|---|---|---|
| Real-time alert (fall happens → dashboard pops up immediately) | ❌ MCP is client-initiated request/response, with no reliable server-to-browser push channel | Keep push: `Reporter` → `POST /api/alerts` → SSE |
| Query past events, fetch snapshots, check monitor status | ✅ | MCP tools |
| Start/stop monitoring, switch video source | ✅ | MCP tools |
| Offline analysis of a video (a recording uploaded by family) | ✅ | MCP tool `analyze_video` |
| Reuse by external agents (asking "any falls today?" in Claude Desktop) | ✅ | stdio / Streamable HTTP |

**Tool list**

| Tool | Params | Returns | Notes |
|---|---|---|---|
| `get_fall_events` | `since?: ISO time`, `limit=20` | `[{event_id, ts, track_id, confidence, verified, snapshot_id}]` | Reads the event store |
| `get_event_snapshot` | `event_id` | `ImageContent` (JPEG) + text description | Lets multimodal clients "see" the snapshot directly |
| `get_monitor_status` | – | `{running, source, fps, persons, states: {track_id: STANDING/FALLING/DOWN}, uptime_s}` | Health check + current scene state |
| `start_monitoring` | `source: "0" \| video path`, `loop=true` | `{running, source}` | Starts the detection thread (switches source if already running) |
| `stop_monitoring` | – | `{running: false}` | Stops the detection thread |
| `analyze_video` | `path` | `{events: [...], duration_s, frames}` | Offline whole-video analysis; does not fire real-time alerts; results written to the event store (`source=offline`) |

- **Resource** (optional): `fall://live/snapshot` — snapshot of the current frame.
- **Event store**: `data/fall_events.jsonl` (append-only) + in-memory index; snapshots at `data/snapshots/{event_id}.jpg`. fall-mcp is the single source of truth for events; the main service's `alert` table only keeps alert copies (with `ref_id=event_id`).
- **Process layout**: one Starlette app mounts three things — `/mcp` (FastMCP Streamable HTTP), `/stream` (MJPEG), `/healthz`. The detection loop runs on a background thread controlled by `start/stop_monitoring`, and interacts with MCP requests through a thread-safe `MonitorController`.
- **stdio mode**: `python -m fall_detector.server --stdio` for Claude Desktop configuration; MJPEG is not started in stdio mode.
- **Error mapping**: missing video / camera won't open / not running → MCP tool error (`isError=true` + readable message), never a bare exception.

**Family Q&A agent (optional, E5)**

```
family.js question → POST /api/family/ask
  → OpenAI Chat (tools = fall-mcp tool list (converted via MCP client) + local tools get_symptoms / get_today_summary)
  → tool_calls → MCP client (Streamable HTTP :8001/mcp) / local functions
  → final answer (citing event times and snapshot links)
```

### 3.7 Model Layer Design

- `LLMClient` is a thin wrapper around the OpenAI SDK: `chat()`, `extract_json(schema)`, `transcribe()`, `tts()`. There is no `vision_check()` here: per 5.3, `fall_detector` must not import `elder_companion`, so F9 calls the OpenAI SDK directly.
- `MockLLMClient` implements the same interface and replays `config/mock_llm.yaml` (which covers the demo-script lines); used for unit tests and as an offline demo fallback (`LLM_PROVIDER=mock`).
- Timeouts and degradation:
  - Transcription fails → frontend prompt "Sorry, I didn't catch that — could you say it again?" (with a text input as fallback)
  - Chat fails → fixed reassuring reply
  - TTS fails → show text only
  - Extraction fails → log it; chat is unaffected
- Secrets are read only from environment variables; `.env` is in `.gitignore`.

### 3.8 Data Model (SQLite)

```sql
elder(id, name, nickname, language, profile_text, created_at)
message(id, elder_id, role[user|assistant], text, audio_path, created_at)
symptom_log(id, elder_id, canonical, label, body_part, severity, duration, onset,
            status, raw_quote, message_id, count, first_seen, last_seen)
alert(id, elder_id, type[symptom|fall], level[high|medium], title, content,
      snapshot_path, ref_id, created_at, is_read)
```

### 3.9 Key Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Phone accessing `http://<LAN IP>` can't record (not a secure context) | Elder-app demo fails | Demo on the laptop via `localhost`; or use `ngrok` / Cloudflare Tunnel for HTTPS |
| Poor venue network, OpenAI timeouts | Whole pipeline stalls | Phone hotspot backup; `LLM_PROVIDER=mock` replay fallback |
| LLM extraction misses a red flag | Core selling point fails | Eval-set coverage + prompt examples; rehearse demo lines in advance |
| LLM reply oversteps (diagnosis, medication) | Ethical/compliance risk | Prompt guardrails + on-page disclaimer; add overstep cases to the eval set |
| Fall-detection false positives/negatives | Demo failure | Pre-recorded videos + tuned parameters; live camera for show only |
| A and B's interfaces don't match | Integration time sink | The single contract `POST /api/alerts` is frozen on D1 morning |

---

## 4. Testing Strategy

### 4.1 Philosophy

Every task has an explicit acceptance command. Rule code (symptom merging, alert rules, fall state machine) has zero LLM dependency and is locked down with table-driven unit tests; LLM capabilities are run manually against a small eval set; the demo path is rehearsed script step by script step.

### 4.2 Three Evaluation Layers

| Layer | Target | Data | Scoring | When |
|---|---|---|---|---|
| **L1 Components** | Symptom merging, alert rules & debounce, `raw_quote` check, fall state machine, event store, fall-mcp tools (in-process client), API contract | Table-driven cases; synthetic keypoint sequences for falls | Exact assertions | Every commit (`pytest -m unit`) |
| **L2 Extraction** | Symptom extractor | `eval/extraction_cases.yaml` ≈ 30 cases (mixed English/Chinese; negations, other people, no symptom, red flags) | Exact match on the set of canonical symptoms; red-flag recall must be 100% | After prompt changes (needs API key) |
| **L3 Demo script** | End-to-end flow | Chapter 6 G2 demo script | Manual check of dashboard results, step by step | D2 afternoon rehearsal |

### 4.3 L2 Case Examples

```yaml
- say: "I've been a bit dizzy in the mornings for two days"
  expect: [dizziness]
- say: "我今天挺好的，就是没什么胃口"   # "I'm fine today, just not much appetite"
  expect: [appetite_loss]
- say: "My husband has a bad cough, I'm fine though"
  expect: []
- say: "胸口有点闷，喘不太上气"         # "My chest feels tight, I can't quite catch my breath"
  expect: [chest_pain, shortness_of_breath]
  expect_alert: high
- say: "I didn't have a headache today"
  expect: []
```

### 4.4 Test Layout

```
tests/
  unit/            # L1, offline (mock LLM)
  integration/     # FastAPI TestClient + temporary SQLite
  fixtures/        # mock LLM replays, synthetic keypoint sequences
eval/
  extraction_cases.yaml
  run_extraction_eval.py
```

pytest markers: `unit`, `integration`, `llm` (needs the real API).

---

## 5. System Architecture & Module Design

### 5.1 Overall Architecture

```
┌──────────────────────────── Demo laptop (localhost) ─────────────────────────┐
│                                                                              │
│  ┌────────────────── web (FastAPI :8000) ──────────────────┐                 │
│  │ /elder  elder app (big record button, autoplay)         │                 │
│  │ /family dashboard (summary/symptoms/alerts/fall/chat)   │                 │
│  │                                                         │                 │
│  │  chat.service ─── transcribe / chat / tts ─────────────────▶ OpenAI API   │
│  │       │                                                 │                 │
│  │       └─(BackgroundTask)─▶ symptoms.extractor ─────────────▶ OpenAI API   │
│  │                             └▶ merge → rules → alerts   │                 │
│  │  alerts.bus ── SSE ─▶ /family                           │                 │
│  │  SQLite: data/app.db                                    │                 │
│  └───────────────────────────▲─────────────────────────────┘                 │
│      push: POST /api/alerts  │        │ pull: MCP (Streamable HTTP)          │
│                              │        ▼ (E5 Q&A agent, optional)             │
│  ┌─────────── fall-mcp (separate process :8001, Starlette) ─┐                │
│  │ /mcp     FastMCP tools: get_fall_events / get_event_snapshot /            │
│  │          get_monitor_status / start|stop_monitoring / analyze_video       │
│  │ /stream  MJPEG (embedded via <img> in dashboard)  /healthz│               │
│  │ MonitorController ── background thread:                  │                │
│  │   OpenCV camera / demo.mp4 → YOLO11n-pose.track          │                │
│  │   → FallStateMachine → EventStore(jsonl + snapshots)     │                │
│  │   → (optional) vision_check ─────────────────────────────────▶ OpenAI API │
│  │   → Reporter ── push ─┘                                  │                │
│  └──────────────────────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────────────────┘
  Claude Desktop / Cursor ── stdio / Streamable HTTP ──▶ fall-mcp (same tools)
```

### 5.2 Directory Layout

```
AI-for-elder/
├── pyproject.toml
├── .env.example                  # OPENAI_API_KEY, CHAT_MODEL, ASR_MODEL, TTS_MODEL, LLM_PROVIDER ...
├── .gitignore                    # .env, data/, *.pt
├── README.md
├── DEV_SPEC.md
├── config/
│   ├── settings.yaml             # models, context turns, alert debounce, fall parameters
│   ├── mock_llm.yaml             # canned replies for LLM_PROVIDER=mock (tests + offline demo)
│   ├── symptoms.yaml             # single source of truth: symptom enum, display names, alert levels
│   └── prompts/
│       ├── companion.txt         # elder-app persona
│       ├── greet.txt
│       ├── extract_symptoms.txt
│       ├── daily_summary.txt
│       └── fall_verify.txt
├── src/
│   ├── elder_companion/          # owner: A
│   │   ├── settings.py
│   │   ├── db.py                 # engine / session
│   │   ├── models.py             # Elder / Message / SymptomLog / Alert
│   │   ├── seed.py               # demo elder profile + optional history
│   │   ├── elders.py             # get_elder / ElderNotFound
│   │   ├── prompts.py            # load/render config/prompts/*.txt (string.Template)
│   │   ├── llm/{client.py, mock.py, __main__.py}
│   │   ├── chat/{service.py, context.py}
│   │   ├── symptoms/{schema.py, extractor.py, merge.py, rules.py}
│   │   ├── alerts/{service.py, bus.py}
│   │   ├── summary.py
│   │   └── web/
│   │       ├── app.py
│   │       ├── routes/{pages.py, chat.py, family.py, alerts.py}
│   │       ├── templates/{elder.html, family.html}
│   │       └── static/{elder.js, family.js, style.css}
│   └── fall_detector/            # owner: B (fall-mcp)
│       ├── pose.py               # YOLO11-pose wrapper; per-frame tracks + keypoints
│       ├── rules.py              # feature computation + FallStateMachine (pure functions, unit-testable)
│       ├── events.py             # EventStore: jsonl append + in-memory index + snapshot paths
│       ├── monitor.py            # MonitorController: thread start/stop, source switching, status, latest frame
│       ├── reporter.py           # POST /api/alerts + optional vision check
│       ├── mcp_tools.py          # FastMCP tool definitions (6 tools + 1 resource)
│       ├── stream.py             # MJPEG generator
│       ├── server.py             # Starlette assembly: /mcp, /stream, /healthz; --stdio mode
│       └── __main__.py           # python -m fall_detector --source demo/videos/fall_01.mp4 (CLI debugging)
├── docs/mcp_desktop.md           # connecting fall-mcp to Claude Desktop
├── demo/videos/                  # pre-recorded fall / lying-down / walking videos
├── data/                         # app.db, audio/, snapshots/ (git-ignored)
├── eval/{extraction_cases.yaml, run_extraction_eval.py}
└── tests/{unit, integration, fixtures}/
```

### 5.3 Module Dependency Rules

```
web ──▶ chat, symptoms, alerts, summary ──▶ llm, models
symptoms.rules / symptoms.merge ──▶ pure Python (no llm / network)
fall_detector ──▶ never imports elder_companion; talks to the main service only over HTTP
fall_detector.rules ──▶ pure Python (keypoint sequences in, events out), unit-testable offline
fall_detector.mcp_tools ──▶ only calls MonitorController / EventStore, never touches YOLO directly
elder_companion.family_agent (E5) ──▶ accesses fall data only through the MCP client, never reads fall_events.jsonl
```

### 5.4 Data Flows

**The elder says something**:
`elder.js recording` → `POST /api/chat/audio (multipart)` → `transcribe` → save `message(user)` → `context.build` (profile + recent symptoms + last 10 turns) → `chat` → save `message(assistant)` → return `{user_text, reply_text}` → frontend shows text → `GET /api/tts/{message_id}` fetches audio and plays it; meanwhile `BackgroundTask: extractor → merge → rules → alerts.create → bus.publish`.

**Real-time alerts on the dashboard**:
`family.js EventSource('/api/alerts/stream')` ← `alerts.bus` (in-process `asyncio.Queue` broadcast) ← `alerts.create` (symptom rules / fall reports).

**Fall alert**:
`fall_detector` frame loop → `pose.track` → `FallStateMachine.update` → `DOWN` event → snapshot saved to the shared `data/snapshots/` → (optional `vision_check`) → `POST /api/alerts` → SSE → dashboard pop-up + snapshot.

**Today's summary**:
`GET /api/summary/today` → today's messages + symptom_logs → `daily_summary` prompt → cached for 10 minutes.

### 5.5 API Contract

| Method | Path | Request / Response | Owner |
|---|---|---|---|
| GET | `/elder`, `/family` | Pages | A |
| POST | `/api/chat` | JSON `{text}` → `{message_id, user_text, reply_text, fallback, need_retry}` | A |
| POST | `/api/chat/audio` | multipart `audio` (+ optional `elder_id`) → same shape; `need_retry: true` = "please say it again", nothing saved | A |
| POST | `/api/chat/greet` | optional `{elder_id}` → `{message_id, user_text: "", reply_text, fallback}` | A |
| GET | `/api/tts/{message_id}` | → `audio/mpeg` (ETag/304), 204 if TTS unavailable (page falls back to browser speech) | A |
| GET | `/api/messages?limit=` | Conversation history | A |
| GET | `/api/symptoms?days=7` | Symptom log (grouped by day) | A |
| GET | `/api/summary/today` | `{summary, generated_at}` | A |
| GET | `/api/alerts` | Alert list | A |
| POST | `/api/alerts` | **Called by the fall service (the only cross-process contract, frozen on D1 morning)** | A |
| POST | `/api/alerts/{id}/read` | Mark as read | A |
| GET | `/api/alerts/stream` | SSE, `event: alert`, `data: Alert JSON` | A |
| POST | `/api/family/ask` (optional E5) | `{question}` → `{answer, tool_calls}` | A |
| GET | `:8001/stream` | MJPEG detection view | B |
| MCP | `:8001/mcp` | fall-mcp tools (see 3.6); stdio: `python -m fall_detector.server --stdio` | B |

```json
// POST /api/alerts
{"type": "fall", "level": "high", "title": "Fall detected",
 "content": "Person down for 3s in living room", "snapshot_path": "snapshots/20261001_101530.jpg"}
```

### 5.6 Configuration-Driven

```yaml
# config/settings.yaml (excerpt)
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
  companion_name: ${COMPANION_NAME:-Sunny}
  timezone: ${ELDER_TIMEZONE:-America/Los_Angeles}   # elder's local time
  history_turns: 10
  max_reply_tokens: 150
  follow_up_hours: 48
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
    autostart_source: demo/videos/fall_01.mp4   # start monitoring on launch; null = wait for start_monitoring
```

---

## 6. Project Schedule (Development Stages)

> Principles: each task is ≈ 0.5–1.5h and independently verifiable; when done, mark ✅ in its heading and in the progress table. Task format: Owner / Goal / Files / Classes & functions / Acceptance criteria / How to test.

### Stage Overview (stage → purpose)

| Stage | Owner | Purpose | When | Milestone |
|---|---|---|---|---|
| A Project skeleton | A | Runnable, configurable, DB ready, contract frozen | D1 morning | Contract frozen |
| B Chat core | A | Text chat + persona + context | D1 morning | |
| C Voice pipeline | A | Voice chat in the elder app | D1 afternoon | |
| D Symptom log | A | Extract → merge → alert | D1 afternoon–evening | **M1: say one sentence → symptom shows up on the dashboard** |
| E Family dashboard | A (B: styling) | Summary, timeline, SSE alerts, panels; optional MCP Q&A | D2 morning | |
| F Fall detection (fall-mcp) | B | Video → keypoints → state machine → events/alerts → live view → MCP tools | D1 all day – D2 morning | **M2: play video → fall alert on the dashboard**; **M3: MCP tools callable from Claude Desktop** |
| G Wrap-up | A + B | UI polish, evals, demo script, rehearsal | D2 afternoon | **Delivery** |

### Timeline (two people in parallel)

| | A (features) | B (vision / frontend) |
|---|---|---|
| **D1 morning** | A1–A3, B1–B3 | F1–F2 (get pose running, prepare demo videos) |
| **D1 afternoon** | C1–C3, D1–D2 | F3–F4 (state machine + unit tests) |
| **D1 evening** | D3–D4 → **M1** | F5 (event store + reporting), integrate against the contract |
| **D2 morning** | E1–E4; E5 (optional, after F7) | F6 (MJPEG) → **M2**; F7–F8 (MCP) → **M3**; F9 (optional) |
| **D2 afternoon** | G3, G4; help with G1 | G1 (styling), G2, G4 |

### 📊 Progress Tracking

| Stage | Tasks | Status |
|---|---|---|
| A | A1 A2 A3 | ✅✅✅ |
| B | B1 B2 B3 | ✅✅✅ |
| C | C1 C2 C3 | ✅✅✅ |
| D | D1 D2 D3 D4 | ⬜⬜⬜⬜ |
| E | E1 E2 E3 E4 E5* | ⬜⬜⬜⬜⬜ |
| F | F1 F2 F3 F4 F5 F6 F7 F8 F9* | ⬜⬜⬜⬜⬜⬜⬜⬜⬜ |
| G | G1 G2 G3 G4 | ⬜⬜⬜⬜ |

### 📈 Overall Progress

`9 / 31` (* = optional task, not required for delivery)

---

## Stage A: Project Skeleton (goal: runnable, configurable, contract frozen)

### A1: uv project and directory skeleton ✅
- **Owner**: A
- **Goal**: create the directories from 5.2 and `pyproject.toml` (fastapi, uvicorn, sqlalchemy, openai, pydantic, pyyaml, python-multipart, jinja2, sse-starlette, mcp (shared by the E5 client and the fall-mcp server); optional `vision` extra: ultralytics, opencv-python).
- **Files**: `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`, `src/**/__init__.py`.
- **Acceptance**: `uv sync` succeeds; `uv run python -c "import elder_companion, fall_detector"` passes.
- **How to test**: `uv run pytest -q` (empty suite passes).

### A2: Settings loading ✅
- **Owner**: A
- **Goal**: read `config/settings.yaml` with `${ENV:-default}` expansion; auto-load `.env`; fail at startup if `OPENAI_API_KEY` is missing and `provider=openai`.
- **Files**: `src/elder_companion/settings.py`, `config/settings.yaml`, `tests/unit/test_settings.py`.
- **Classes/functions**: `Settings` (Pydantic), `load_settings(path) -> Settings`.
- **Acceptance**: environment variables override defaults; a missing key error names the variable.
- **How to test**: `uv run pytest -q tests/unit/test_settings.py`.

### A3: Data model, seed data, and API contract freeze ✅
- **Owner**: A (confirm the contract with B)
- **Goal**: create the 4 tables; seed one demo elder (name, nickname, language, profile: age, lives alone, high blood pressure, hobbies); bring up an empty FastAPI shell and implement `POST /api/alerts` first (so B can integrate early).
- **Files**: `db.py`, `models.py`, `seed.py`, `web/app.py`, `web/deps.py`, `web/routes/alerts.py`, `alerts/schemas.py`, `alerts/service.py`.
- **Classes/functions**: `Elder`, `Message`, `SymptomLog`, `Alert`; `init_db()`, `seed_demo()`; `AlertIn` / `AlertOut` schemas; `create_alert()`.
- **Acceptance**: `uv run elder-web` (or `uv run uvicorn --factory elder_companion.web.app:create_app`) starts; `curl -X POST /api/alerts` returns 201 and the row is stored.
- **How to test**: `uv run pytest -q tests/integration/test_alerts_api.py`.

---

## Stage B: Chat Core (goal: warm text chat)

### B1: LLMClient and mock ✅
- **Owner**: A
- **Goal**: wrap `chat(messages)`, `extract_json(messages, schema)`, `transcribe(file)`, `tts(text) -> bytes`; a mock with the same interface (returns fixtures matched by keyword); unified timeouts and a common `LLMError` exception type. (`vision_check` dropped — see 3.7.)
- **Files**: `llm/client.py`, `llm/mock.py`, `llm/__main__.py`, `config/mock_llm.yaml`, `tests/unit/test_llm_mock.py`.
- **Classes/functions**: `BaseLLMClient`, `OpenAIClient`, `MockLLMClient`, `get_llm(settings)`.
- **Acceptance**: with `LLM_PROVIDER=mock`, every method returns offline.
- **How to test**: `uv run pytest -q tests/unit/test_llm_mock.py` (OpenAIClient is exercised against a fake HTTP transport); manually `uv run python -m elder_companion.llm --ping` (real key).

### B2: Persona prompt and context building ✅
- **Owner**: A
- **Goal**: write `companion.txt` (warm, short sentences, one question at a time, follows the elder's language, no diagnosis or medication advice, suggests contacting family / 911 for dangerous situations); `build_context()` combines profile + current time + unresolved symptoms from the last 48h + last 10 turns.
- **Files**: `config/prompts/companion.txt`, `config/prompts/greet.txt`, `prompts.py`, `chat/context.py`, `settings.py` (`chat.companion_name`, `chat.timezone`, `chat.follow_up_hours`), `tests/unit/test_context.py`.
- **Classes/functions**: `build_context(elder, history, recent_symptoms, now) -> list[dict]`, `build_greet_context(...)`, `render_prompt(name, **values)`.
- **Acceptance**: unit tests assert the context contains the profile and symptom follow-up info, and that turn truncation is correct.
- **How to test**: `uv run pytest -q tests/unit/test_context.py`.

### B3: Text chat endpoint ✅
- **Owner**: A
- **Goal**: `POST /api/chat` (JSON `{text}`) and `POST /api/chat/greet`; messages persisted; LLM failure returns a fixed reassuring reply.
- **Files**: `chat/service.py`, `web/routes/chat.py`, `web/deps.py`, `web/app.py`, `elders.py`, `tests/integration/test_chat_api.py`.
- **Classes/functions**: `ChatService.reply(elder_id, text) -> ChatResult`, `ChatService.greet(elder_id)`. Responses also carry `fallback: bool`.
- **Acceptance**: curl with text gets a reply in the matching language (Chinese/English); the message table has two rows.
- **How to test**: `uv run pytest -q tests/integration/test_chat_api.py` (mock); manually chat 5 turns against the real API to check the persona.

---

## Stage C: Voice Pipeline (goal: the elder app can listen and speak)

### C1: Speech transcription ✅
- **Owner**: A
- **Goal**: `POST /api/chat/audio` (a separate endpoint rather than content-type sniffing on `/api/chat`) accepts multipart `audio` (webm / mp4 / wav), transcribes it, then follows the B3 flow; empty or too-short transcriptions and ASR failures return `{"need_retry": true}` without saving anything.
- **Files**: `web/routes/chat.py`, `chat/service.py`, `llm/client.py` (`asr_prompt`, echo guard), `tests/integration/test_chat_audio.py`.
- **Classes/functions**: `ChatService.reply_audio(elder_id, upload) -> ChatResult`.
- **Acceptance**: a real clip returns the correct `user_text` (verified with real English and Mandarin clips; Mandarin comes back in Simplified characters); the flow passes under mock.
- **How to test**: `uv run pytest -q tests/integration/test_chat_audio.py`.

### C2: TTS ✅
- **Owner**: A
- **Goal**: `GET /api/tts/{message_id}` generates and caches an mp3 (`data/audio/{id}.mp3`) using `tts_instructions` (slow, warm); on failure return 204 and the frontend shows text only.
- **Files**: `web/routes/chat.py`, `chat/service.py`.
- **Classes/functions**: `ChatService.synthesize(message_id) -> Path`.
- **Acceptance**: opening the URL directly in a browser plays audio; a second request hits the server-side cache; `If-None-Match` returns 304.
- **How to test**: manual; `tests/integration/test_tts.py` (mock returns a silent mp3).

### C3: Elder app page ✅
- **Owner**: A (B polishes styling in G1)
- **Goal**: `/elder` page — a big round button (hold-to-talk / tap-to-toggle modes), status indicator (listening / thinking / speaking), the last 3 chat bubbles, font size ≥ 24px, high contrast; tapping "Start chatting" on entry triggers greet and unlocks audio playback; a hidden text input as fallback.
- **Files**: `web/templates/elder.html`, `web/static/elder.js`, `web/static/style.css`, `web/routes/pages.py`.
- **Classes/functions**: `startRecording()`, `stopAndSend()`, `playReply(messageId)`, `setStatus(state)`.
- **Acceptance**: one full voice round-trip on Chrome + Safari (laptop) via `localhost`; end-to-end < 5s.
- **How to test**: manual; record latency over 5 rounds (`window.__timings` in the console).
- **Measured (real API, 5 voice turns)**: reply text median 1.67s, text + audio median 3.39s (one long Chinese reply took 6.8s, dominated by TTS). Full browser recording path (MediaRecorder → level meter → upload → ASR → reply → TTS) verified in Chromium with a virtual mic; still to verify on a real microphone and on Safari.

---

## Stage D: Symptom Log (goal: M1 — say one sentence, the symptom appears on the dashboard)

### D1: Symptom enum and schema
- **Owner**: A
- **Goal**: `config/symptoms.yaml` (canonical names, English/Chinese display names, alert levels); Pydantic `SymptomItem` / `SymptomExtraction`, exporting a JSON Schema for Structured Outputs, with the `canonical` enum generated from the yaml.
- **Files**: `config/symptoms.yaml`, `symptoms/schema.py`, `tests/unit/test_symptom_schema.py`.
- **Acceptance**: canonical values outside the enum fail validation; the exported schema meets strict-mode requirements (all fields required, `additionalProperties: false`).
- **How to test**: `uv run pytest -q tests/unit/test_symptom_schema.py`.

### D2: Extractor
- **Owner**: A
- **Goal**: `extract_symptoms.txt` (enum list, don't record negations/other people, `raw_quote` must be verbatim, return empty when there are no symptoms, 5 few-shot examples); `SymptomExtractor.extract(user_text, recent_turns)`; `raw_quote` substring check.
- **Files**: `config/prompts/extract_symptoms.txt`, `symptoms/extractor.py`, `eval/extraction_cases.yaml`, `eval/run_extraction_eval.py`.
- **Classes/functions**: `SymptomExtractor`, `verify_quote(item, text) -> bool`.
- **Acceptance**: L2 eval accuracy ≥ 90%, red-flag recall 100%.
- **How to test**: `uv run python eval/run_extraction_eval.py` (needs a key; prints per-case results and a summary).

### D3: Merge and alert rules
- **Owner**: A
- **Goal**: implement the merging, alert levels, and 2h debounce from 3.4.3; prefer pure functions and keep DB operations in the service.
- **Files**: `symptoms/merge.py`, `symptoms/rules.py`, `alerts/service.py`, `tests/unit/test_symptom_merge.py`, `tests/unit/test_alert_rules.py`.
- **Classes/functions**: `merge_symptom(existing, item, now) -> MergeResult`, `alert_level(canonical, severity) -> Level | None`, `should_alert(last_alert_at, now)`.
- **Acceptance**: table-driven cases cover: new insert, merge within 24h, new row outside the window, severity takes the max, `other` merges by label, red flags → high, severe ordinary symptoms → medium, debounce works.
- **How to test**: `uv run pytest -q tests/unit/test_symptom_merge.py tests/unit/test_alert_rules.py`.

### D4: Wire into the chat flow and broadcast alerts
- **Owner**: A
- **Goal**: after `/api/chat` completes, `BackgroundTasks` runs extract → merge → alert; `alerts.bus` broadcasts in-process (a list of `asyncio.Queue` subscribers); `GET /api/alerts/stream` emits SSE.
- **Files**: `chat/service.py`, `web/routes/chat.py`, `alerts/bus.py`, `web/routes/alerts.py`, `tests/integration/test_symptom_pipeline.py`.
- **Classes/functions**: `process_message_symptoms(message_id)`, `AlertBus.publish()`, `AlertBus.subscribe()`.
- **Acceptance**: **M1** — saying "这两天早上起来头有点晕" ("I've been a bit dizzy in the mornings these past two days") → `dizziness` appears in `symptom_log`; saying "胸口闷，喘不上气" ("my chest feels tight, I can't catch my breath") → an SSE client (`curl -N`) receives a high alert; chat latency is unaffected.
- **How to test**: `uv run pytest -q tests/integration/test_symptom_pipeline.py` (mock); manual end-to-end with the real API.

---

## Stage E: Family Dashboard (goal: see the elder's status on one screen)

### E1: Dashboard data endpoints
- **Owner**: A
- **Goal**: `/api/symptoms?days=7` (grouped by day, with count, status, raw_quote), `/api/messages`, `/api/alerts`, `/api/alerts/{id}/read`.
- **Files**: `web/routes/family.py`, `web/routes/alerts.py`, `tests/integration/test_family_api.py`.
- **Acceptance**: with seed data, responses have the shape the frontend needs (fields asserted in tests).
- **How to test**: `uv run pytest -q tests/integration/test_family_api.py`.

### E2: Today's summary
- **Owner**: A
- **Goal**: `daily_summary.txt` (2–3 sentences: mood, discomforts mentioned, things to watch; no diagnosis); 10-minute cache; returns "No conversations yet today" when there is no chat.
- **Files**: `summary.py`, `config/prompts/daily_summary.txt`, `web/routes/family.py`.
- **Classes/functions**: `DailySummary.get(elder_id, date)`.
- **Acceptance**: after 5 turns of chat the summary accurately mentions the symptoms and contains no diagnostic language.
- **How to test**: manual; mock unit tests cover the caching logic.

### E3: Seed history data
- **Owner**: A
- **Goal**: `seed.py --with-history` generates conversations and symptoms for the past 6 days (recurring knee pain, improving insomnia, etc.) so the timeline looks full in the demo.
- **Files**: `seed.py`.
- **Acceptance**: the dashboard timeline shows 7 days of data with a coherent story.
- **How to test**: `uv run python -m elder_companion.seed --reset --with-history`.

### E4: Dashboard page
- **Owner**: A (features) / B (styling)
- **Goal**: `/family` page — today's summary card at the top; symptom timeline on the left (by day, severity color chips, counts, click to see the original quote); alert list on the right (falls with snapshot thumbnails); fall-detection panel `<img src=":8001/stream">`; a red banner + sound at the top when `EventSource` receives an alert; collapsible conversation history.
- **Files**: `web/templates/family.html`, `web/static/family.js`, `web/static/style.css`.
- **Classes/functions**: `loadSummary()`, `loadSymptoms()`, `loadAlerts()`, `connectAlertStream()`, `showAlertBanner(alert)`.
- **Acceptance**: elder mentions a red-flag symptom → the dashboard shows the banner within ≤ 10s (including extraction time); the page works at both 1280px and 390px widths.
- **How to test**: manual (two windows side by side).

### E5 (optional): "Ask AI" on the dashboard
- **Owner**: A (depends on F7)
- **Goal**: `POST /api/family/ask` — on startup, connect to fall-mcp via the MCP client (Streamable HTTP), `list_tools()`, and convert them to OpenAI function tools; merge with local tools `get_symptoms(days)` and `get_today_summary()`; answer after at most 4 rounds of tool calls; if fall-mcp is unavailable, drop the fall tools automatically and say so in the answer. Add an input box to the dashboard.
- **Files**: `src/elder_companion/family_agent.py`, `web/routes/family.py`, `web/templates/family.html`, `web/static/family.js`, `tests/integration/test_family_agent.py`.
- **Classes/functions**: `FamilyAgent.ask(question) -> AskResult`, `mcp_tools_to_openai(tools)`, `LocalTools`.
- **Acceptance**: asking "Did Mom fall today? Has anything been bothering her lately?" → the answer cites both the fall event time and the symptom log; `tool_calls` shows `get_fall_events` and `get_symptoms`.
- **How to test**: `uv run pytest -q tests/integration/test_family_agent.py` (mock LLM with fixed tool_calls + in-process fall-mcp); manual end-to-end.

---

## Stage F: Fall Detection fall-mcp (goal: M2 — play a video, the dashboard shows a fall alert; M3 — tools exposed over MCP)

### F1: Environment setup and pose running
- **Owner**: B
- **Goal**: install the `vision` extra; `pose.py` wraps `YOLO("yolo11n-pose.pt").track(frame, persist=True)` and outputs `list[PersonPose(track_id, bbox, keypoints[17,3])]`; a visualization script draws skeletons.
- **Files**: `src/fall_detector/pose.py`, `src/fall_detector/__main__.py`.
- **Acceptance**: `python -m fall_detector --source demo/videos/walk.mp4 --show` displays skeletons and track_ids in a window at FPS ≥ 12.
- **How to test**: manual; FPS printed to the terminal.

### F2: Demo video footage
- **Owner**: B
- **Goal**: record/collect ≥ 3 clips: normal walking, slowly lying down on a sofa (should not alert), a fall (should alert); fixed camera position (simulating a living-room camera height). Clips from the Le2i / UR Fall public datasets may supplement these (mind the licenses).
- **Files**: `demo/videos/*.mp4`, `demo/videos/README.md` (sources and expected results).
- **Acceptance**: every clip is labeled with its expected result.
- **How to test**: manual review.

### F3: Feature computation
- **Owner**: B
- **Goal**: compute from keypoints: bbox aspect ratio, torso angle (shoulder midpoint–hip midpoint vs. vertical), hip-midpoint height (normalized to body height), fall speed; handle low-confidence keypoints.
- **Files**: `src/fall_detector/rules.py`, `tests/unit/test_fall_features.py`.
- **Classes/functions**: `compute_features(pose, ts) -> Features`.
- **Acceptance**: synthetic "upright" and "horizontal" keypoints produce the expected angles and aspect ratios.
- **How to test**: `uv run pytest -q tests/unit/test_fall_features.py`.

### F4: Fall state machine
- **Owner**: B
- **Goal**: implement the state machine from 3.5 (parameters from settings), one instance per track, no repeat alerts during cooldown.
- **Files**: `src/fall_detector/rules.py`, `tests/unit/test_fall_state_machine.py`, `tests/fixtures/pose_sequences/*.json`.
- **Classes/functions**: `FallStateMachine.update(features) -> FallEvent | None`.
- **Acceptance**: synthetic sequences: fast fall then staying down 3s → exactly 1 alert; slowly lying down → none; down for 1s then getting up → none; staying down for 60s → only 1 alert within the cooldown. Results on the three demo videos match their labels.
- **How to test**: `uv run pytest -q tests/unit/test_fall_state_machine.py`; `python -m fall_detector --source demo/videos/fall_01.mp4 --dry-run`.

### F5: Event store, snapshots, and reporting
- **Owner**: B
- **Goal**: `EventStore` appends to `data/fall_events.jsonl` (`event_id, ts, track_id, confidence, source[live|offline], verified, snapshot_id`) and maintains an in-memory index; on an event, save a snapshot with the skeleton overlay; `live` events call `POST /api/alerts` (`ref_id=event_id`); if the main service is unreachable, log locally and retry 3 times without blocking the detection thread.
- **Files**: `src/fall_detector/events.py`, `src/fall_detector/reporter.py`, `tests/unit/test_event_store.py`, `tests/unit/test_reporter.py`.
- **Classes/functions**: `EventStore.add(event, frame) -> FallEventRecord`, `EventStore.query(since, limit)`, `EventStore.snapshot_path(event_id)`, `Reporter.report(record)`.
- **Acceptance**: past events are still queryable after a process restart; with the main service running, playing the fall video → a fall row appears in the `alert` table and the dashboard receives it over SSE.
- **How to test**: `uv run pytest -q tests/unit/test_event_store.py tests/unit/test_reporter.py` (`httpx.MockTransport`); integration run.

### F6: MonitorController and service assembly (MJPEG)
- **Owner**: B
- **Goal**: `MonitorController` manages the background detection thread (`start(source, loop)` / `stop()` / `status()` / `latest_frame()`, thread-safe, stop-then-start when switching sources); `server.py` assembles the Starlette app: `/stream` (MJPEG with skeletons and STANDING / FALLING / DOWN status text; video files loop when finished) and `/healthz`; monitoring starts automatically from `autostart_source` on launch.
- **Files**: `src/fall_detector/monitor.py`, `src/fall_detector/stream.py`, `src/fall_detector/server.py`, `tests/unit/test_monitor.py`.
- **Classes/functions**: `MonitorController`, `MonitorStatus`, `mjpeg_generator(controller)`, `create_app(settings) -> Starlette`.
- **Acceptance**: **M2** — after `uv run python -m fall_detector.server` starts, the dashboard panel shows the live detection view; when the fall video plays, the dashboard pops up a fall alert + snapshot.
- **How to test**: `uv run pytest -q tests/unit/test_monitor.py` (with a fake pose source); manual integration.

### F7: fall-mcp tools
- **Owner**: B
- **Goal**: implement the 6 tools from 3.6 + the `fall://live/snapshot` resource with FastMCP, mounted at `/mcp` in `server.py` (Streamable HTTP); `--stdio` mode runs MCP only (no MJPEG); parameter schemas generated from type annotations; errors mapped to readable `isError=true` results; `get_event_snapshot` returns `ImageContent`.
- **Files**: `src/fall_detector/mcp_tools.py`, `src/fall_detector/server.py`, `tests/integration/test_fall_mcp.py`.
- **Classes/functions**: `build_mcp(controller, store) -> FastMCP`; tool functions `get_fall_events`, `get_event_snapshot`, `get_monitor_status`, `start_monitoring`, `stop_monitoring`, `analyze_video`.
- **Acceptance**: MCP Inspector (`npx @modelcontextprotocol/inspector`) connected to `http://127.0.0.1:8001/mcp` can list and call every tool; `analyze_video(demo/videos/fall_01.mp4)` returns ≥ 1 event and `analyze_video(demo/videos/lie_down.mp4)` returns 0; a nonexistent path returns a tool error instead of crashing.
- **How to test**: `uv run pytest -q tests/integration/test_fall_mcp.py` (MCP SDK in-process client + fake pose source); manual verification in the Inspector.

### F8: Claude Desktop integration
- **Owner**: B
- **Goal**: write `docs/mcp_desktop.md` (a stdio config example for `claude_desktop_config.json`, including the absolute path for `uv run --directory`); in Claude Desktop, ask "Were any falls detected today? Show me the snapshot" and complete the tool calls.
- **Files**: `docs/mcp_desktop.md`.
- **Acceptance**: Claude Desktop lists the fall-mcp tools and returns events and snapshots.
- **How to test**: manual; record the screen as bonus demo material.

### F9 (optional): Second check with a vision model
- **Owner**: B
- **Goal**: when `vision_verify: true`, send the event snapshot to a GPT vision model (`fall_verify.txt`: "Is there a person who has fallen on the ground in this image? Reply with JSON only"); write the result to the event's `verified` field; if rejected, downgrade the alert to medium or don't push it.
- **Files**: `src/fall_detector/reporter.py`, `config/prompts/fall_verify.txt`.
- **Acceptance**: fall snapshots are confirmed; the lying-on-sofa snapshot is rejected.
- **How to test**: manually verify against 3 snapshots.

---

## Stage G: Wrap-up (goal: a stable, well-told demo)

### G1: UI polish and responsiveness
- **Owner**: B (with A's help)
- **Goal**: elder app: large text, high contrast, button animations, status illustrations; dashboard: card layout, severity colors, alert banner; a disclaimer ("This product does not provide medical diagnosis").
- **Files**: `web/static/style.css`, both templates.
- **Acceptance**: no horizontal scrolling at iPad / phone sizes (390px); in the elder app it's obvious at a glance where to "press here to talk".
- **How to test**: browser device emulation + real devices.

### G2: Demo script and materials
- **Owner**: B (reviewed by A)
- **Goal**: write `demo/SCRIPT.md` (expanding the draft below down to every line, expected screen, and fallback), and prepare a backup screen recording (to play if the network completely fails).
- **Files**: `demo/SCRIPT.md`, `demo/backup_recording.mp4`.
- **Acceptance**: the full demo is ≤ 5 minutes and every step has a fallback.
- **How to test**: rehearsal.

### G3: One-command startup and README
- **Owner**: A
- **Goal**: `scripts/dev_up.ps1`: init DB + seed → start web → start fall_detector; the README clearly covers environment, `.env`, startup, and demo URLs.
- **Files**: `scripts/dev_up.ps1`, `README.md`.
- **Acceptance**: a fresh clone runs within 10 minutes by following the README.
- **How to test**: run it from scratch on the teammate's machine.

### G4: End-to-end acceptance and rehearsal
- **Owner**: A + B
- **Goal**: run all of L1 + L2; rehearse the demo script ≥ 3 times and record the 2.5 metrics; prepare the mock fallback (the full demo script replays under `LLM_PROVIDER=mock`).
- **Files**: `DEV_SPEC.md` (final progress and metrics).
- **Acceptance**: `uv run pytest -q` is all green; the 2.5 table is filled in; every task in the progress table is ✅.
- **How to test**: `uv run pytest -q && uv run python eval/run_extraction_eval.py`.

### Demo Script (draft)

| # | Action | Expected |
|---|---|---|
| 1 | Open the elder app and tap "Start chatting" | AI greets by time of day and follows up on yesterday's knee pain (seed data) |
| 2 | Elder: "Much better today, but I didn't sleep well last night." | AI asks about sleep; `insomnia` is added on the dashboard |
| 3 | Elder: "这两天早上起来头有点晕" ("I've been a bit dizzy in the mornings these past two days") | AI asks for details; `dizziness` appears on the dashboard timeline |
| 4 | Elder: "My chest feels tight and I can't catch my breath" | AI reassures and suggests contacting family / 911; a **high** alert pops up on the dashboard |
| 5 | Switch to the dashboard and play the fall video | Detection view shows FALLING → DOWN; fall alert + snapshot pops up |
| 6 | Refresh today's summary on the dashboard | Summary covers sleep, dizziness, and the chest-tightness alert |
| 7 | Dashboard "Ask AI": "Did Mom fall today? Anything else I should know?" (E5) | Answer cites the fall time + symptoms; tool_calls are shown |
| 8 | Switch to Claude Desktop and ask the same question (F8) | Returns events and snapshots via fall-mcp — showing the capability is reusable |

### Delivery Milestones

| Milestone | Stages completed | When | What can be demoed |
|---|---|---|---|
| Contract frozen | A | D1 morning | `POST /api/alerts` available; B can integrate |
| M1 | A–D | D1 evening | Voice chat + symptom log + red-flag alerts (SSE) |
| M2 | E, F1–F6 | D2 morning | Complete dashboard + fall alerts |
| M3 | F7–F8 (+E5) | D2 midday | fall-mcp called from Inspector / Claude Desktop; (optional) dashboard Q&A agent |
| Delivery | G | D2 evening | Polished UI + demo script + fallbacks |

---

## 7. Extensibility & Future Work

- **Realtime voice**: OpenAI Realtime API (WebRTC) for <1s speech-to-speech with barge-in; side-channel transcription keeps feeding symptom extraction.
- **More vision MCP tools**: activity stats (how long she walked around today), alerts for prolonged sitting / not being seen for a long time, number of nighttime bathroom trips — growing fall-mcp into a home-vision-mcp.
- **More robust fall recognition**: replace rules with ST-GCN / PoseC3D (NTU RGB+D `falling down`); fine-tune on in-home footage; multiple cameras.
- **Non-visual sensing**: mmWave radar (for private spaces like bathrooms/bedrooms), smartwatch fall detection and heart rate.
- **Medication reminders and adherence**: scheduled reminders + confirming "did you take it?" in conversation, reporting adherence to the family.
- **Cognitive and mood trends**: long-term tracking of loneliness, low mood, and repeated questions (an early sign of cognitive decline), with weekly reports.
- **Long-term memory**: store the elder's family, life stories, and preferences in a memory store (vector retrieval) for a more "familiar" companion.
- **Notification channels**: SMS / email / app push; one-tap call to family in emergencies.
- **Multi-family and permissions**: accounts, multiple caregivers per elder, a read-only view for doctors.
- **Compliance**: HIPAA-related data encryption and auditing (required to enter the US market).

---

## 8. Appendix: Architecture Decision Records (ADR)

| # | Decision | Alternatives | Rationale |
|---|---|---|---|
| 1 | Voice uses a sequential "record → transcribe → chat → TTS" pipeline | Browser Web Speech; Realtime API | Balances quality and dev cost; the symptom log needs text anyway; Realtime kept as a stretch goal |
| 2 | All models go through OpenAI, with configurable model names | Multiple providers / local models | Demo is in the US with no network restrictions; one SDK covers LLM/ASR/TTS/Vision |
| 3 | No LangGraph / agent framework | LangGraph (as in rift) | The elder app has no multi-step task flow; a framework is a net negative in a 2-day build |
| 4 | Symptom extraction is async and off the chat critical path | Synchronous extraction / one call for both reply and extraction | Chat latency comes first; extraction failures don't affect companionship |
| 5 | The LLM only extracts; normalization/merging/alerting are decided by code + `symptoms.yaml` | Let the LLM decide whether to alert | Testable and explainable; red-flag recall doesn't depend on LLM judgment |
| 6 | Strict Structured Outputs + verbatim `raw_quote` check | Free-form JSON / regex parsing | Guarantees valid format; suppresses hallucinated symptoms |
| 7 | Fall detection = YOLO11-pose + temporal rule state machine; vision LLM only as an optional second check | Single-frame fall detection model; ST-GCN | Works out of the box, explainable, tunable; fall speed distinguishes lying down from falling |
| 8 | Fall detection is a separate process built as **fall-mcp**: queries/control via MCP tools, real-time alerts still pushed (`POST /api/alerts`) | Plain HTTP service; pure MCP (clients poll for alerts too) | MCP is client-pull and unsuited to real-time alerts; exposing queries as a protocol lets the dashboard agent and Claude Desktop reuse them; enables parallel work and isolates YOLO dependencies |
| 9 | SSE for real-time push | WebSocket | One-way push is enough; simpler to implement and reconnect |
| 10 | Frontend is Jinja2 + vanilla JS, no build step | React / Vite | Two pages, a 2-day build; the focus is the AI capabilities |
| 11 | One hard-coded elder + one family member, no login | Account system | Controls scope; the demo focuses on core value |
| 12 | Demo relies mainly on pre-recorded video + seeded history, with a mock fallback | Everything live on site | Reproducible demo; avoids failures from network or lighting |
| 13 | fall-mcp serves `/mcp` + `/stream` + `/healthz` from one Starlette process; stdio mode for desktop clients | MJPEG and MCP in two processes | Shares one MonitorController and latest frame; avoids cross-process sync |
| 14 | fall-mcp is the single source of truth for fall events; the main service's alert table only stores alert copies (`ref_id`) | Main service stores all events | Offline-analysis events create no alerts but remain queryable; clear separation of responsibilities |
