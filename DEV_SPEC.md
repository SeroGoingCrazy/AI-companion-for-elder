# Developer Specification (DEV_SPEC)

> Project: **Elder Companion Agent** (an AI companion agent for older adults living alone, plus a family care dashboard)
> Version: v0.2 (adds companion memory, family loop, parent-controlled privacy, reports)
> Timeline: 2-day core MVP + 1 day for Stage H; demo location: United States; model provider: OpenAI
> Build note: Stage H is complete, including family reminders (H5), the daily digest / weekly report (H7), family redaction (H11) and the voice switch (H12).
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
| **Elder app** `/elder` | Older adult | Phone/tablet browser; one big button for voice chat | Someone to talk to who cares and remembers, anytime; zero learning curve; the parent decides what stays private |
| **Family dashboard** `/family` | Adult children (siblings share one view) | Browser dashboard | Auto-organized symptom log, red-flag alerts, fall detection, daily summary; reminders for the parent; care list, weekly report, doctor one-pager |

The elder never has to "fill out a health form". When the agent hears something like "I've been a bit dizzy in the mornings for the past couple of days" during casual chat, it automatically extracts a structured symptom record and syncs it to the family; if it hears "my chest feels tight", it alerts them immediately. The family side is also connected to a home camera (a video file for the demo) to detect falls and push alerts with snapshots.

Beyond health, the agent remembers the ordinary things a friend would: on Monday the parent says "I'm going to repot my orchid this week", and on Wednesday the agent opens with "How did the orchid repotting go?" Children can set reminders (e.g., a daily pill), and the agent brings them up naturally in the next session. The parent stays in control: "keep this between us" is honored. The agent still remembers what was said, but it never passes it on to the family (the one safety exception is disclosed up front; see 2.7).

> **Terminology**: a **session** is one visit to the elder app, starting with the greeting (`POST /api/chat/greet`). Product copy may call it a "call", but v1 has no telephony.

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
6. **Remembered, Not Monitored; the Parent Owns the Conversation**
   The agent brings ordinary life details back the way a friend would, so the parent feels remembered, not checked on. Privacy is something the parent exercises in their own voice: "keep this between us" hides that part from every family view, while the AI itself still remembers it. The only exception is a high-level red flag (chest pain, fall, self-harm, …), which always alerts the family. This rule is fixed in code and told to the parent up front, never hidden.

### Non-Goals (v1)

- No medical diagnosis, medication advice, or integration with hospital/EHR systems
- No sign-up/login or multi-elder support (one hard-coded elder; several family members picked by URL parameter, no authentication)
- No telephony: no outbound calls or parent-initiated callback (web app only; see Chapter 7)
- No family ↔ parent messaging (Chapter 7)
- No scam detection (Chapter 7)
- No native app and no push notifications (real-time alerts inside the dashboard page are enough)
- No OpenAI Realtime speech-to-speech (moved to Chapter 7)
- No dialect-recognition tuning, no local ASR/TTS
- Fall detection does not aim for real-world robustness (the demo relies mainly on pre-recorded video)

---

## 2. Key Features

### 2.1 Elder App

- **Voice chat**: hold to talk → transcription → reply → read aloud automatically; follows English or Chinese automatically.
- **A warm persona**: addresses the elder by nickname, uses short sentences, asks one question at a time, remembers the elder's background (profile), recent conversations, and small life details (2.5).
- **Proactive care**: at conversation start or when idle, proactively asks about meals, sleep, medication, and mood. The greeting also carries the session agenda: due reminders and follow-ups (2.6).
- **Privacy on request**: "keep this between us" is acknowledged and honored (2.7).
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
- Conversation history (read-only; private segments redacted, see 2.7)
- Family loop: set reminders for the parent and see adherence (2.6); care list (2.5); weekly report and doctor one-pager (2.8)

### 2.4 Fall Detection

- YOLO11-pose keypoints + state-machine rules (fall speed, torso angle, time spent on the ground).
- Input: camera or video file; output: snapshot + alert event.
- Optional: send the snapshot to a GPT vision model for a second opinion to reduce false positives.
- **Exposed as an MCP server (fall-mcp)**: six tools — `get_fall_events`, `get_event_snapshot`, `get_monitor_status`, `start_monitoring` / `stop_monitoring`, `analyze_video`; Streamable HTTP (primary) + stdio (Claude Desktop).
- "Ask AI" on the family dashboard (optional): "Did Mom fall today? Has anything been bothering her lately?" — the agent calls the fall tools over MCP plus local symptom tools and answers with both.

### 2.5 Companion Memory ("Did you repot the orchid?")

- Alongside symptom extraction, every elder message also goes through a background **companion memory extractor**. It picks up small, non-medical things: plans and events worth asking about later, people and topics the parent keeps mentioning, and life stories. Health complaints are left to the symptom extractor.
- **Conversational follow-up**: "I'm going to repot my orchid this week" becomes a `follow_up` item with a due date. At the first session on or after that date, the greeting asks: "How did the orchid repotting go?"
- **Care list** (family view): people and topics mentioned ≥ 2 times (a neighbor, a grandchild's exam), with counts and the last mention, so children know what matters to the parent right now.
- **Family memoir**: stories the parent tells are saved as a keepsake page, with the verbatim excerpt next to a lightly polished retelling.

### 2.6 Family Loop

- **Medication / follow-up reminders**: a family member sets a reminder, either daily at a time or once on a date (e.g., "blood pressure pill, mornings", "ask if she booked the eye-doctor follow-up"). The agent brings it up naturally in the first session after it is due, and records the parent's answer as confirmed / declined / no response. The dashboard shows 7-day adherence. The agent only uses the family's wording and never comments on dose or medication choice.
- **Sibling sharing**: several family members share one dashboard (switched with `?member=`), and everyone sees the same summaries. Any member can claim an alert or care-list item ("Ben: I'll call her doctor"), so it's visible who's handling what.
- **Agenda budget**: each greeting carries at most 3 items, picked in this order: due reminders, then follow-ups. Items that don't fit carry over to the next session, so the opener never feels like a checklist.

### 2.7 Parent-Controlled Privacy ("This stays between us")

- At any time the parent can say "keep this between us" or "don't tell my daughter about this". The agent acknowledges it and honors it.
- The current utterance, plus up to 3 preceding related user messages in the same session, is marked `private`. Private content is hidden from **every** family-facing view: today's summary, weekly report, care list, memoir, symptom timeline, conversation history, and Ask AI. That day's summary only says: "Your parent asked to keep part of today's conversation private."
- **The AI still remembers**: private items stay in the agent's own context, so later it can ask the parent "How is your friend doing?" They are tagged never-relay and never reach the family.
- **Safety exception**: a high-level red flag (as defined in `symptoms.yaml`) always alerts and appears in the timeline with the exact quote, even inside a private segment.
- **Disclosure**: the agent explains this rule in the first session and repeats a short version whenever the parent asks for privacy: "I'll keep this between us. Just so you know, if it's ever about your safety, like a fall or chest pain, I'll always let Amy know."

### 2.8 Reports

- **Weekly report**: one page covering the mood trend (a daily 1–5 score), the most-discussed topics, a symptom summary (what, how often, getting better or worse), and reminder adherence. It is built only from non-private data.
- **One-pager for the doctor**: a printable symptom log for the last N days. For each symptom it shows first/last seen, count, max severity, and status, with the parent's exact words and timestamps; it also lists red-flag alerts and reminder adherence. It is rendered from structured data by a template, with no LLM involved, so nothing can be invented.

### 2.9 Key Demo Metrics (to be filled in during Stage G)

| Metric | Target | Measured |
|---|---|---|
| Voice round-trip end-to-end latency P50 | < 4s | – |
| Symptom-extraction eval accuracy (symptom name + red-flag) | ≥ 90% | – |
| Red-flag recall (eval set) | 100% | – |
| Demo-video fall detections / false positives | all detected / 0 false positives | demo clips: 2/2 main falls, 0/4 FP; all 17 URFD clips: 4/5 falls, 0/12 FP |
| Memory-extraction eval accuracy (kind + subject) | ≥ 90% | – (eval set ready, not yet run against the API) |
| Privacy-request recall (eval set) | 100% | – (same) |
| Private text leaking into any family endpoint (leak test) | 0 | 0 across all 11 family GET routes/pages (offline leak test) |
| Red flags inside private segments still alerted | 100% | 100% in tests (fall in English and Chinese private segments) |

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
- Microphone access requires a secure context: use `http://localhost` or HTTPS for the demo (phones need HTTPS; see risks in 3.13).
- Browser autoplay restrictions: audio playback after the first user interaction (pressing the button) is allowed, which our flow satisfies.
- `POST /api/chat/audio` returns `{message_id, user_text, reply_text, fallback, need_retry}`; the frontend then fetches `GET /api/tts/{message_id}` (mp3 cached in `data/audio/`, served `no-cache` + ETag so a DB reset never replays stale audio).
- **Silence**: the ASR model invents sentences for silent clips, so the page measures mic level while recording and never uploads a clip whose peak RMS stays below a speech threshold.
- **Script**: without a hint, Mandarin comes back in Traditional characters; `llm.asr_prompt` (a description nobody would say aloud) steers it to Simplified, and transcripts that merely echo that prompt are discarded.
- **One question per reply** is enforced in code (`chat/postprocess.py`): the prompt rule alone was ignored about half the time in Chinese.

### 3.3 Technical Analysis: Conversation Orchestration

- **No LangGraph / agent framework**: the elder app has no multi-step task flow (unlike rift's booking state machine) — it is just "persona + context + single generation". An orchestration framework would be a net negative.
- **Context composition**: `system(persona prompt + elder profile + current time + recent symptom summary + companion memory)` + last 10 turns + current input.
  - Injecting recent symptoms lets the agent follow up naturally: "Maggie, yesterday you said your knee hurt — is it any better today?" This is key to the feeling of companionship.
  - Companion memory = open follow-ups, recent care-list people/topics, and private items. Private items are tagged `(private: never suggest telling family)`, so the agent can bring them up with the parent but never offers to pass them on.
- **Proactive greeting**: opening the elder app calls `POST /api/chat/greet`, which starts a session. The opener is generated from time of day (morning/afternoon/evening) + the session agenda (≤ 3 items; see 3.8) + symptoms not yet followed up on. On the very first session it also delivers the privacy disclosure (3.9).
- **Safety guardrails (three layers)**:
  1. Prompt layer: no diagnosis, no drug names or dosage advice; for red-flag symptoms, reassure + suggest contacting family immediately / calling 911.
  2. Code layer: when symptom extraction hits a red flag → alert the family (independent of whether the LLM's reply was appropriate, and independent of privacy marks).
  3. Reminder layer: reminders are brought up as "Amy wanted me to check …". The agent never speaks as a family member and never adds medication content to a reminder.

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
STANDING ──(A: bbox aspect ratio > 1.2  or  torso angle from vertical > 60° with aspect > 0.8)──▶ candidate
        and (B: hip midpoint drops > 0.25 × standing height within ≤ 0.6s)                  ──▶ FALLING
STANDING ──(A without B: horizontal, slow)──▶ LYING (never alerts; LYING ──B──▶ FALLING)
FALLING ──(stays near-horizontal ≥ 3s and hip displacement < threshold)──▶ DOWN ⇒ fire alert (snapshot)
FALLING ──(back upright ≥ 0.5s within 3s)──▶ STANDING (no alert)
DOWN    ──(back upright)──▶ STANDING; no repeat alert for the same track within 30s
```

- **Tuned on real clips (Stage F)**: `drop_ratio` 0.35 → 0.25 (a person close to the camera has a bbox cut off by the frame edge, inflating "standing height"); LYING → FALLING added (falls that pitch forward read horizontal before the hips drop); a new track id adopts the history of a person who vanished at the same spot within 1.5s (ByteTrack often re-ids a falling body); untracked one-off boxes are ignored.

- Slowly lying down fails condition B (fall speed) → no alert. This is the core of distinguishing "lying down" from "falling".
- Frames with keypoint confidence < 0.3 are skipped; person boxes get a track_id from `model.track(persist=True)`.
- Performance: measured ~50 FPS offline / 32 FPS live (with annotation + JPEG) for YOLO11n-pose on a laptop CPU at 640 input; Apple Silicon can use `FALL_DEVICE=mps`.
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

### 3.7 Technical Analysis: Companion Memory Extraction

**Two directions, one privacy layer**

```
   parent → family (inbound)                         family → parent (outbound)
 elder utterance                                   reminder (set by family)
   │                                                           │
   ├─▶ symptom extractor (3.4, unchanged)                      ▼
   │                                               agenda.select ──▶ greeting / context
   └─▶ memory extractor (new)                                  ▲
         ├ items: follow_up  "repot my orchid"  ───────────────┘  (follow-ups)
         ├ items: person / topic  ──▶ care list
         ├ items: story           ──▶ memoir
         ├ reminder_acks          ──▶ reminder_log (adherence)
         └ privacy_request        ──▶ message.private

 every family-facing read ──▶ privacy.py ──▶ summary / weekly / doctor / care list / memoir / history / Ask AI
```

- **Separate call, separate prompt** (`extract_memory.txt`, ADR 17). It also runs in the same `BackgroundTask` after the reply, so it adds no chat latency. A memory-prompt change can never regress red-flag recall.
- **Input**: last 3 turns (with message ids) + current utterance + the reminders delivered in this session (`reminder_id` + text), so the model can link "yes, I took it" to the right reminder.

**Output contract (Structured Outputs, strict)**

```json
{
  "items": [
    {
      "kind": "follow_up | person | topic | story",
      "subject": "orchid",
      "text": "planning to repot her orchid this week",
      "due_in_days": 3,
      "raw_quote": "I'm going to repot my orchid this week"
    }
  ],
  "reminder_acks": [{"reminder_id": 12, "status": "confirmed | declined"}],
  "privacy_request": {"requested": true, "covers_message_ids": [41, 42]}
}
```

**Rules (code)**

- `raw_quote` must be a substring of the utterance (same check as 3.4.2); failures are dropped.
- Nothing worth remembering → all lists empty (the majority case). Health complaints are never memory items.
- Items merge by `(kind, normalized subject)`: `mention_count += 1`, `last_seen = now`. An item is `private` only if **all** its mentions are private.
- `follow_up`: `due_date = today + (due_in_days ?? follow_up_default_delay_days)`; status `open` → `asked` (once a greeting carries it) → `expired` after `follow_up_expire_days` if never asked.
- Care list = `person | topic` items with `mention_count ≥ care_list_min_mentions`, visible only (non-private).
- `privacy_request.covers_message_ids` is clamped to the current message + at most `max_span_user_messages` preceding user messages of the same session. If it is empty, it defaults to the current message + the previous user message.

### 3.8 Technical Analysis: Agenda (reminders & follow-ups)

- **Sources**: active `reminder` rows due now and not yet mentioned today; `memory_item(kind=follow_up, status=open, due_date ≤ today)`.
- **Selection**: `select_agenda(...)` is a pure function. Priority is reminders > follow-ups, oldest first within a kind. It returns ≤ `max_items_per_greet` items. Unselected items carry over.
- **Delivery at the greeting only**: selected items go into `greet.txt` as a numbered list, and after the greeting is saved they are marked: reminder → `reminder_log(status=mentioned)`, follow-up → `asked`. Reminders created mid-session wait for the next greeting. This keeps delivery status deterministic, and the agent never interrupts a conversation with a new item.
- **Answers**: extractor `reminder_acks` → `reminder_log` confirmed/declined; a reminder still `mentioned` at end of day → `no_response`.
- **Reminder style** (prompt few-shots): "Amy wanted me to check — have you had your blood pressure pill this morning?" No dose, no medical opinion, never first person as the family member.

### 3.9 Technical Analysis: Parent-Controlled Privacy

**What the family sees for content inside a private segment**

| Content | Family sees |
|---|---|
| High-level red-flag symptom | Alert + timeline entry with the exact quote (unchanged) |
| Other symptoms | Nothing (occurrence hidden; the row is hidden if it has no visible occurrence) |
| Memory items (follow-ups, care list, stories) | Nothing; the agent still uses them in its own context |
| Messages in conversation history | "(private)" placeholder |
| Summary / weekly report | Built from non-private messages only, plus "Your parent asked to keep part of today's conversation private." |
| Ask AI (E5) | Local tools read through the same filter |

**Implementation**

- **Mark on write, filter on read (ADR 18)**: nothing is deleted. `message.private = 1`, and symptom occurrences and memory items take their visibility from their source messages. Every family-facing read goes through `privacy.py` (`visible_messages`, `visible_symptoms`, `visible_memory`). Routes, reports, and E5 tools never query those tables directly (5.3).
- **Symptom occurrences**: `symptom_mention` links each occurrence (with its `raw_quote`) to its message. A `symptom_log` row is visible if its level is high, or if it has ≥ 1 non-private occurrence. Displayed counts and quotes use visible occurrences only; high rows show all.
- **Marking can be retroactive**: the request often comes *after* the content ("… anyway, keep that between us"). So `mark_private()` also invalidates today's summary cache and recomputes today's digest, and a summary generated seconds earlier can't leak the content.
- **Safety bypass (ADR 19)**: the red-flag alert path (3.4.3) never looks at privacy marks, so a high alert fires no matter the order of extraction and marking.
- **Disclosure**: `elder.privacy_disclosed_at` is null until the first greeting includes the disclosure. `companion.txt` repeats the short disclosure every time the parent asks for privacy.
- Out of scope in v1: the parent lifting privacy later ("you can tell her now").

### 3.10 Technical Analysis: Reports

- **Daily digest**: `daily_summary.txt` now returns structured output `{summary, mood_score 1–5, topics[]}` computed from non-private messages. The result is stored in `daily_digest`: today's row is refreshed on demand (10-minute cache, replacing E2's cache), and past days are frozen. The E2 "today's summary" reads from it.
- **Weekly report** (`/family/report/weekly`): aggregates 7 digests (mood line, topic counts), symptom stats (from `visible_symptoms`), and reminder adherence. A 2–3 sentence overview comes from `weekly_report.txt`. Printable HTML.
- **Doctor one-pager** (`/family/doctor?days=30`): a deterministic Jinja template with no LLM (ADR 21), print CSS, ≤ 2 pages. Disclaimer: "Prepared from conversations with an AI companion; not a clinical record."
- **Memoir** (`/family/memoir`): visible `story` items in chronological order, each showing the verbatim excerpt next to an optional retelling (`memoir.txt`, cached per story). The retelling must stay faithful, which is why the quote is always shown beside it.

### 3.11 Model Layer Design

- `LLMClient` is a thin wrapper around the OpenAI SDK: `chat()`, `extract_json(schema)`, `transcribe()`, `tts()`. There is no `vision_check()` here: per 5.3, `fall_detector` must not import `elder_companion`, so F9 calls the OpenAI SDK directly.
- `MockLLMClient` implements the same interface and replays `config/mock_llm.yaml` (which covers the demo-script lines); used for unit tests and as an offline demo fallback (`LLM_PROVIDER=mock`).
- Timeouts and degradation:
  - Transcription fails → frontend prompt "Sorry, I didn't catch that — could you say it again?" (with a text input as fallback)
  - Chat fails → fixed reassuring reply
  - TTS fails → show text only
  - Extraction fails → log it; chat is unaffected
- Secrets are read only from environment variables; `.env` is in `.gitignore`.

### 3.12 Data Model (SQLite)

```sql
elder(id, name, nickname, language, profile_text, privacy_disclosed_at, created_at)
family_member(id, elder_id, name, relation, created_at)
chat_session(id, elder_id, started_at)                     -- created by /api/chat/greet
message(id, elder_id, session_id, role[user|assistant], text, audio_path,
        private, created_at)
symptom_log(id, elder_id, canonical, label, body_part, severity, duration, onset,
            status, raw_quote, message_id, count, first_seen, last_seen)
symptom_mention(id, symptom_log_id, message_id, raw_quote, severity, created_at)  -- one per occurrence
alert(id, elder_id, type[symptom|fall], level[high|medium], title, content,
      snapshot_path, ref_id, message_id, created_at, is_read)   -- message_id: source of a symptom alert

-- Stage H
memory_item(id, elder_id, kind[follow_up|person|topic|story], subject, text, raw_quote,
            message_id, private, mention_count, due_date, status[open|asked|expired],
            retelling, first_seen, last_seen)                    -- retelling: cached memoir text
reminder(id, elder_id, from_member_id, text,
         schedule_time, schedule_date, active, created_at)     -- daily HH:MM or one-off date
reminder_log(id, reminder_id, date, status[mentioned|confirmed|declined|no_response],
             message_id, created_at, updated_at)               -- unique (reminder_id, date)
daily_digest(id, elder_id, date, summary, mood_score, topics_json, has_private,
             fingerprint, generated_at)      -- unique (elder_id, date); fingerprint: rebuild when the day changes
claim(id, elder_id, target_type[alert|memory_item|symptom_log], target_id,
      member_id, note, created_at, done_at)
```

### 3.13 Key Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Phone accessing `http://<LAN IP>` can't record (not a secure context) | Elder-app demo fails | Demo on the laptop via `localhost`; or use `ngrok` / Cloudflare Tunnel for HTTPS |
| Poor venue network, OpenAI timeouts | Whole pipeline stalls | Phone hotspot backup; `LLM_PROVIDER=mock` replay fallback |
| LLM extraction misses a red flag | Core selling point fails | Eval-set coverage + prompt examples; rehearse demo lines in advance |
| LLM reply oversteps (diagnosis, medication) | Ethical/compliance risk | Prompt guardrails + on-page disclaimer; add overstep cases to the eval set |
| Fall-detection false positives/negatives | Demo failure | Pre-recorded videos + tuned parameters; live camera for show only |
| A and B's interfaces don't match | Integration time sink | The single contract `POST /api/alerts` is frozen on D1 morning |
| Private content leaks into a family view | Breaks the core trust promise | Single `privacy.py` read layer; a leak test scans every family endpoint for private text; summary cache invalidated on marking |
| Parent expects privacy for a red flag | Feels betrayed | Up-front and in-the-moment disclosure; the bypass is a fixed code rule, not an LLM decision |
| Agenda turns chats into a checklist | Companion feel is lost | ≤ 3 items per greeting; follow-ups phrased as curiosity, not questions from a form |
| Memory extractor over-captures trivia | Noisy care list and follow-ups | Mention threshold for the care list; "nothing worth remembering" eval cases |
| Reminder wording drifts into medication advice | Compliance risk | Reminder-layer prompt rule + overstep cases in the eval set |

---

## 4. Testing Strategy

### 4.1 Philosophy

Every task has an explicit acceptance command. Rule code (symptom merging, alert rules, fall state machine) has zero LLM dependency and is locked down with table-driven unit tests; LLM capabilities are run manually against a small eval set; the demo path is rehearsed script step by script step.

### 4.2 Three Evaluation Layers

| Layer | Target | Data | Scoring | When |
|---|---|---|---|---|
| **L1 Components** | Symptom merging, alert rules & debounce, `raw_quote` check, fall state machine, event store, fall-mcp tools (in-process client), API contract; memory merging, follow-up lifecycle, care-list aggregation, agenda selection & budget, privacy filter, doctor one-pager rendering; **privacy leak test** (every family endpoint scanned for private text) | Table-driven cases; synthetic keypoint sequences for falls; seeded private segments | Exact assertions | Every commit (`pytest -m unit`, leak test in `integration`) |
| **L2 Extraction** | Symptom extractor | `eval/extraction_cases.yaml` ≈ 30 cases (mixed English/Chinese; negations, other people, no symptom, red flags) | Exact match on the set of canonical symptoms; red-flag recall must be 100% | After prompt changes (needs API key) |
| **L2 Memory** | Memory extractor | `eval/memory_cases.yaml` ≈ 25 cases (plans, recurring people, stories, nothing-to-remember, health complaints that must *not* become items, privacy requests, reminder acks) | Exact match on the set of `(kind, subject)`; privacy-request recall must be 100% | After prompt changes (needs API key) |
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

```yaml
# eval/memory_cases.yaml
- say: "I'm going to repot my orchid this week"
  expect_items: [[follow_up, orchid]]
- say: "Linda from next door brought soup again"        # Linda already mentioned once in context
  expect_items: [[person, Linda]]
- say: "My knee is hurting again"
  expect_items: []                                      # health → symptom extractor only
- say: "My friend got bad news from her doctor. Please keep this between us."
  expect_privacy: true
- say: "别告诉我女儿，我昨天在厨房摔了一跤"              # "Don't tell my daughter, I fell in the kitchen yesterday"
  expect_privacy: true                                  # integration test: fall alert still fires (high)
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
  memory_cases.yaml
  run_memory_eval.py
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
│  │       ├─(BackgroundTask)─▶ symptoms.extractor ─────────────▶ OpenAI API   │
│  │       │                     └▶ merge → rules → alerts   │                 │
│  │       └─(BackgroundTask)─▶ memory.extractor ───────────────▶ OpenAI API   │
│  │                             └▶ items / privacy / acks   │                 │
│  │  agenda ─▶ greet/context (family → parent)              │                 │
│  │  privacy.py ─▶ all family-facing reads                  │                 │
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
│       ├── daily_summary.txt     # structured: summary, mood_score, topics
│       ├── extract_memory.txt
│       ├── weekly_report.txt
│       ├── memoir.txt
│       └── fall_verify.txt
├── src/
│   ├── elder_companion/          # owner: A
│   │   ├── settings.py
│   │   ├── db.py                 # engine / session
│   │   ├── models.py             # Elder / Message / SymptomLog / Alert + Stage H tables (3.12)
│   │   ├── seed.py               # demo elder profile, family members, optional history
│   │   ├── elders.py             # get_elder / ElderNotFound
│   │   ├── prompts.py            # load/render config/prompts/*.txt (string.Template)
│   │   ├── llm/{client.py, mock.py, __main__.py}
│   │   ├── chat/{service.py, context.py}
│   │   ├── symptoms/{schema.py, extractor.py, merge.py, rules.py, service.py}
│   │   ├── memory/{schema.py, extractor.py, store.py, care_list.py}
│   │   ├── agenda/{select.py, service.py}
│   │   ├── family_loop/{reminders.py, claims.py}
│   │   ├── privacy.py            # mark_private + visible_* — the only family-facing read path
│   │   ├── alerts/{service.py, bus.py}
│   │   ├── summary.py            # today's summary (reads reports.digest)
│   │   ├── reports/{digest.py, weekly.py, doctor.py, memoir.py}
│   │   └── web/
│   │       ├── app.py
│   │       ├── routes/{pages.py, chat.py, family.py, alerts.py, reports.py}
│   │       ├── templates/{elder.html, family.html, report_weekly.html, doctor.html, memoir.html}
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
├── eval/{extraction_cases.yaml, run_extraction_eval.py, memory_cases.yaml, run_memory_eval.py}
└── tests/{unit, integration, fixtures}/
```

### 5.3 Module Dependency Rules

```
web ──▶ chat, symptoms, memory, agenda, family_loop, alerts, summary, reports ──▶ llm, models
symptoms.rules / symptoms.merge ──▶ pure Python (no llm / network)
memory merge / care_list / agenda.select / privacy filters ──▶ pure Python (no llm / network)
family-facing reads (routes/family, routes/reports, reports.*, summary, family_agent local tools)
    ──▶ privacy.visible_* only; never query message / symptom_* / memory_item directly
alerts (red-flag path) ──▶ never reads privacy marks (safety bypass, ADR 19)
reports.doctor ──▶ never imports llm (ADR 21)
fall_detector ──▶ never imports elder_companion; talks to the main service only over HTTP
fall_detector.rules ──▶ pure Python (keypoint sequences in, events out), unit-testable offline
fall_detector.mcp_tools ──▶ only calls MonitorController / EventStore, never touches YOLO directly
elder_companion.family_agent (E5) ──▶ accesses fall data only through the MCP client, never reads fall_events.jsonl
```

### 5.4 Data Flows

**The elder says something**:
`elder.js recording` → `POST /api/chat/audio (multipart)` → `transcribe` → save `message(user)` → `context.build` (profile + recent symptoms + companion memory + last 10 turns) → `chat` → save `message(assistant)` → return `{user_text, reply_text}` → frontend shows text → `GET /api/tts/{message_id}` fetches audio and plays it. Meanwhile, in a `BackgroundTask`:
1. `symptom extractor → merge (+ symptom_mention) → rules → alerts.create → bus.publish`
2. `memory extractor → memory_item merge; privacy_request → privacy.mark_private (→ invalidate summary cache, recompute today's digest); reminder_acks → reminder_log`

**Session start (greeting)**:
`elder.js "Start chatting"` → `POST /api/chat/greet` → new `chat_session` → `agenda.select` (due reminders → due follow-ups, ≤ 3) → `greet` prompt (+ privacy disclosure if `privacy_disclosed_at` is null) → save `message(assistant)` → mark agenda items (reminder → mentioned, follow-up → asked).

**Reminder**:
`family.js` → `POST /api/reminders` → `reminder` → next greeting after it is due brings it up → `reminder_log` (mentioned → confirmed / declined / no_response) → dashboard adherence.

**Real-time alerts on the dashboard**:
`family.js EventSource('/api/alerts/stream')` ← `alerts.bus` (in-process `asyncio.Queue` broadcast) ← `alerts.create` (symptom rules / fall reports).

**Fall alert**:
`fall_detector` frame loop → `pose.track` → `FallStateMachine.update` → `DOWN` event → snapshot saved to the shared `data/snapshots/` → (optional `vision_check`) → `POST /api/alerts` → SSE → dashboard pop-up + snapshot.

**Today's summary**:
`GET /api/summary/today` → `privacy.visible_messages` + `visible_symptoms` for today → `daily_summary` prompt (structured) → `daily_digest` row, cached for 10 minutes (invalidated by `mark_private`).

### 5.5 API Contract

| Method | Path | Request / Response | Owner |
|---|---|---|---|
| GET | `/elder`, `/family` | Pages | A |
| POST | `/api/chat` | JSON `{text}` → `{message_id, user_text, reply_text, fallback, need_retry}` | A |
| POST | `/api/chat/audio` | multipart `audio` (+ optional `elder_id`) → same shape; `need_retry: true` = "please say it again", nothing saved | A |
| POST | `/api/chat/greet` | optional `{elder_id}` → `{message_id, user_text: "", reply_text, fallback}` | A |
| GET | `/api/tts/{message_id}` | → `audio/mpeg` (ETag/304), 204 if TTS unavailable (page falls back to browser speech) | A |
| GET | `/api/messages?limit=` | Conversation history (private messages → `{"private": true}` placeholder) | A |
| GET | `/api/symptoms?days=7` | Symptom log (grouped by day; visible occurrences only) | A |
| GET | `/api/family/members` | `[{id, name, relation}]` | A |
| POST | `/api/reminders` | `{text, from_member_id?, schedule_time?, schedule_date?}` → reminder with adherence (exactly one schedule) | A |
| GET | `/api/reminders` | Reminders with 7-day adherence, active first | A |
| DELETE | `/api/reminders/{id}` | Deactivate a reminder (its history is kept) | A |
| GET | `/api/care-list` | Visible `person \| topic` items, ≥ threshold mentions | A |
| GET | `/api/claims` | Claims, newest first, with `member_name` and `done_at` | A |
| POST | `/api/claims` | `{target_type, target_id, member_id, note}` → claim | A |
| POST | `/api/claims/{id}/done` | Mark handled | A |
| GET | `/family/report/weekly?week=` | Weekly report page (printable); `week=0` is the last 7 days | A (B: styling) |
| GET | `/family/doctor?days=30` | Doctor one-pager page (printable, no LLM) | A (B: styling) |
| GET | `/family/memoir` | Memoir page | A (B: styling) |
| GET | `/api/summary/today` | `{summary, generated_at, fallback, empty, has_private}` | A |
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
  tts_voices:                                # the elder app's voice switch (H12)
    female: ${TTS_VOICE_FEMALE:-coral}
    male: ${TTS_VOICE_MALE:-ash}
  tts_instructions: "Speak slowly, warmly and clearly, like a caring family member."
  timeout_s: 15
chat:
  companion_name: ${COMPANION_NAME:-Hallo}
  timezone: ${ELDER_TIMEZONE:-America/Los_Angeles}   # elder's local time
  history_turns: 10
  max_reply_tokens: 150
  follow_up_hours: 48
symptoms:
  merge_window_hours: 24
  alert_debounce_hours: 2
memory:
  care_list_min_mentions: 2
  follow_up_default_delay_days: 1
  follow_up_expire_days: 7
agenda:
  max_items_per_greet: 3
privacy:
  max_span_user_messages: 3        # preceding user messages a privacy request can cover
  bypass_levels: [high]            # alert levels that ignore privacy marks — do not change without an ADR
fall:
  aspect_ratio_threshold: 1.2
  torso_angle_deg: 60
  drop_ratio: 0.25
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
| H Companion memory & family loop | A (B: UI, report pages) | Remember life details, family reminders, parent-controlled privacy, reports, sibling sharing | D2 afternoon – D3 morning | **M4: orchid follow-up + reminders + privacy with red-flag bypass** |
| G Wrap-up | A + B | UI polish, evals, demo script, rehearsal | D3 afternoon | **Delivery** |

### Timeline (two people in parallel)

| | A (features) | B (vision / frontend) |
|---|---|---|
| **D1 morning** | A1–A3, B1–B3 | F1–F2 (get pose running, prepare demo videos) |
| **D1 afternoon** | C1–C3, D1–D2 | F3–F4 (state machine + unit tests) |
| **D1 evening** | D3–D4 → **M1** | F5 (event store + reporting), integrate against the contract |
| **D2 morning** | E1–E4; E5 (optional, after F7) | F6 (MJPEG) → **M2**; F7–F8 (MCP) → **M3**; F9 (optional) |
| **D2 afternoon** | H1–H3 | G1 (styling); H5 UI |
| **D3 morning** | H4 → **M4**; H7, H8 | H6, H9 UI; H10 page |
| **D3 afternoon** | G3, G4; H10 backend if time allows | G2, G4 |

> Stage H priorities: **P0** (H1–H5) must ship; **P1** (H6–H8) next; **P2** (H9–H10) is cut first if behind.

### 📊 Progress Tracking

| Stage | Tasks | Status |
|---|---|---|
| A | A1 A2 A3 | ✅✅✅ |
| B | B1 B2 B3 | ✅✅✅ |
| C | C1 C2 C3 | ✅✅✅ |
| D | D1 D2 D3 D4 | ✅✅✅✅ |
| E | E1 E2 E3 E4 E5* | ✅✅✅✅⬜ |
| F | F1 F2 F3 F4 F5 F6 F7 F8 F9* | ✅✅✅✅✅✅✅✅⬜ |
| H | H1 H2 H3 H4 H5 H6 H7 H8 H9 H10 H11 H12 H13 H14 | ✅✅✅✅✅✅✅✅✅✅✅✅✅✅ |
| G | G1 G2 G3 G4 | ✅⬜⬜⬜ |

### 📈 Overall Progress

`40 / 45` (* = optional task, not required for delivery)

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

### D1: Symptom enum and schema ✅
- **Owner**: A
- **Goal**: `config/symptoms.yaml` (canonical names, English/Chinese display names, alert levels); Pydantic `SymptomItem` / `SymptomExtraction`, exporting a JSON Schema for Structured Outputs, with the `canonical` enum generated from the yaml.
- **Files**: `config/symptoms.yaml`, `symptoms/schema.py`, `tests/unit/test_symptom_schema.py`.
- **Acceptance**: canonical values outside the enum fail validation; the exported schema meets strict-mode requirements (all fields required, `additionalProperties: false`).
- **How to test**: `uv run pytest -q tests/unit/test_symptom_schema.py`.

### D2: Extractor ✅
- **Owner**: A
- **Goal**: `extract_symptoms.txt` (enum list, don't record negations/other people, `raw_quote` must be verbatim, return empty when there are no symptoms, 5 few-shot examples); `SymptomExtractor.extract(user_text, recent_turns)`; `raw_quote` substring check.
- **Files**: `config/prompts/extract_symptoms.txt`, `symptoms/extractor.py`, `eval/extraction_cases.yaml`, `eval/run_extraction_eval.py`.
- **Classes/functions**: `SymptomExtractor`, `verify_quote(item, text) -> bool`.
- **Acceptance**: L2 eval accuracy ≥ 90%, red-flag recall 100%.
- **How to test**: `uv run python eval/run_extraction_eval.py` (needs a key; prints per-case results and a summary).
- **Measured (gpt-4.1-mini, 30 cases, 3 runs)**: 30/30 every run, red-flag recall 10/10, no false red flags. The recent turns go in the user message right before the utterance: at the end of the system prompt, follow-ups like "Much better today" (about the knee) were missed and accuracy sat at 90%. Cases may list `optional` canonicals for genuinely ambiguous wording (e.g. "leg hurts" after a fall).

### D3: Merge and alert rules ✅
- **Owner**: A
- **Goal**: implement the merging, alert levels, and 2h debounce from 3.4.3; prefer pure functions and keep DB operations in the service.
- **Files**: `symptoms/merge.py`, `symptoms/rules.py`, `alerts/service.py`, `tests/unit/test_symptom_merge.py`, `tests/unit/test_alert_rules.py`.
- **Classes/functions**: `merge_symptom(existing, item, now) -> MergeResult`, `alert_level(canonical, severity, status) -> Level | None`, `should_alert(last_alert_at, now)`; `alerts.service.last_symptom_alert_at()` (symptom alerts keep the `symptom_log` id in `ref_id`; debounce is per canonical).
- **Decisions**: a repeat mention within the window turns `new` into `ongoing`; red flags alert even when `resolved` ("I fell but I'm fine" still goes to family); severe ordinary symptoms stop alerting once `resolved`.
- **Acceptance**: table-driven cases cover: new insert, merge within 24h, new row outside the window, severity takes the max, `other` merges by label, red flags → high, severe ordinary symptoms → medium, debounce works.
- **How to test**: `uv run pytest -q tests/unit/test_symptom_merge.py tests/unit/test_alert_rules.py`.

### D4: Wire into the chat flow and broadcast alerts ✅
- **Owner**: A
- **Goal**: after `/api/chat` completes, `BackgroundTasks` runs extract → merge → alert; `alerts.bus` broadcasts in-process (a list of `asyncio.Queue` subscribers); `GET /api/alerts/stream` emits SSE.
- **Files**: `chat/service.py`, `web/routes/chat.py`, `alerts/bus.py`, `web/routes/alerts.py`, `tests/integration/test_symptom_pipeline.py`.
- **Classes/functions**: `process_message_symptoms(message_id)`, `AlertBus.publish()`, `AlertBus.subscribe()`; `SymptomService.process_message()` (`symptoms/service.py`).
- **Measured (real API)**: chat replies in 1.0–1.3s with extraction running afterwards; "这两天早上起来头有点晕" logs `dizziness` (mild, 2 days); "My chest feels tight and I can't catch my breath" pushes two high alerts (Chest pain, Shortness of breath) to `curl -N /api/alerts/stream`. Each red-flag symptom is its own alert, so one sentence can raise two.
- **Acceptance**: **M1** — saying "这两天早上起来头有点晕" ("I've been a bit dizzy in the mornings these past two days") → `dizziness` appears in `symptom_log`; saying "胸口闷，喘不上气" ("my chest feels tight, I can't catch my breath") → an SSE client (`curl -N`) receives a high alert; chat latency is unaffected.
- **How to test**: `uv run pytest -q tests/integration/test_symptom_pipeline.py` (mock); manual end-to-end with the real API.

---

## Stage E: Family Dashboard (goal: see the elder's status on one screen)

### E1: Dashboard data endpoints ✅
- **Owner**: A
- **Goal**: `/api/symptoms?days=7` (grouped by day, with count, status, raw_quote), `/api/messages`, `/api/alerts`, `/api/alerts/{id}/read`.
- **Files**: `web/routes/family.py`, `web/routes/alerts.py`, `tests/integration/test_family_api.py`.
- **Acceptance**: with seed data, responses have the shape the frontend needs (fields asserted in tests).
- **How to test**: `uv run pytest -q tests/integration/test_family_api.py`.
- **Notes**: days are the elder's calendar days (`chat.timezone`), newest first, empty days included; each row sits on the day it was last mentioned. All API timestamps now carry a UTC offset (`UtcDateTime`): naive values made browsers read them as local time.

### E2: Today's summary ✅
- **Owner**: A
- **Goal**: `daily_summary.txt` (2–3 sentences: mood, discomforts mentioned, things to watch; no diagnosis); 10-minute cache; returns "No conversations yet today" when there is no chat.
- **Files**: `summary.py`, `config/prompts/daily_summary.txt`, `web/routes/family.py`.
- **Classes/functions**: `DailySummary.get(elder_id, date)`.
- **Acceptance**: after 5 turns of chat the summary accurately mentions the symptoms and contains no diagnostic language.
- **How to test**: manual; mock unit tests cover the caching logic (`tests/unit/test_summary.py`).
- **Notes**: `GET /api/summary/today?refresh=true` → `{summary, generated_at, fallback, empty}`. The cache is keyed on today's data (last message, symptom rows, alerts) as well as the 10-minute TTL, so a refresh right after a new symptom is never stale. LLM failure → a plain rule-based summary (`fallback: true`, not cached). Real API: 5 demo turns summarized in 1.1s, mentioning the chest alert first, the dizziness, poor sleep and the improved knee, with no diagnosis.

### E3: Seed history data ✅
- **Owner**: A
- **Goal**: `seed.py --with-history` generates conversations and symptoms for the past 6 days (recurring knee pain, improving insomnia, etc.) so the timeline looks full in the demo.
- **Files**: `seed.py`.
- **Acceptance**: the dashboard timeline shows 7 days of data with a coherent story.
- **How to test**: `uv run python -m elder_companion.seed --reset --with-history`.
- **Story**: knee pain on cold mornings (severe on day -4 → a read medium alert), poor sleep improving mid-week, one lonely evening, and the knee aching again yesterday so today's greeting follows up on it. Today is left empty for the live demo. Skipped if the elder already has messages.

### E4: Dashboard page ✅
- **Owner**: A (features) / B (styling)
- **Goal**: `/family` page — today's summary card at the top; symptom timeline on the left (by day, severity color chips, counts, click to see the original quote); alert list on the right (falls with snapshot thumbnails); fall-detection panel `<img src=":8001/stream">`; a red banner + sound at the top when `EventSource` receives an alert; collapsible conversation history.
- **Files**: `web/templates/family.html`, `web/static/family.js`, `web/static/style.css`.
- **Classes/functions**: `loadSummary()`, `loadSymptoms()`, `loadAlerts()`, `connectAlertStream()`, `showAlertBanner(alert)`.
- **Acceptance**: elder mentions a red-flag symptom → the dashboard shows the banner within ≤ 10s (including extraction time); the page works at both 1280px and 390px widths.
- **How to test**: manual (two windows side by side); `tests/integration/test_pages.py` covers render/config/snapshots.
- **Notes**: the SSE stream also carries `event: activity` after every elder turn, so a mild symptom such as dizziness shows up without an alert or polling. Fall snapshots are served at `/media/snapshots/…` from `paths.data_dir`. Browsers only allow the chime after a click, so it has a "Sound on" toggle; on load the newest unread urgent alert is shown in the banner. Measured: the banner appeared < 1s after the chest line (mock extraction), with the timeline, history and the "new activity" hint on the summary updating live.

### E5 (optional): "Ask AI" on the dashboard
- **Owner**: A (depends on F7)
- **Goal**: `POST /api/family/ask` — on startup, connect to fall-mcp via the MCP client (Streamable HTTP), `list_tools()`, and convert them to OpenAI function tools; merge with local tools `get_symptoms(days)` and `get_today_summary()`; answer after at most 4 rounds of tool calls; if fall-mcp is unavailable, drop the fall tools automatically and say so in the answer. Add an input box to the dashboard.
- **Files**: `src/elder_companion/family_agent.py`, `web/routes/family.py`, `web/templates/family.html`, `web/static/family.js`, `tests/integration/test_family_agent.py`.
- **Classes/functions**: `FamilyAgent.ask(question) -> AskResult`, `mcp_tools_to_openai(tools)`, `LocalTools`.
- **Acceptance**: asking "Did Mom fall today? Has anything been bothering her lately?" → the answer cites both the fall event time and the symptom log; `tool_calls` shows `get_fall_events` and `get_symptoms`.
- **How to test**: `uv run pytest -q tests/integration/test_family_agent.py` (mock LLM with fixed tool_calls + in-process fall-mcp); manual end-to-end.

---

## Stage F: Fall Detection fall-mcp (goal: M2 — play a video, the dashboard shows a fall alert; M3 — tools exposed over MCP)

### F1: Environment setup and pose running ✅
- **Owner**: B
- **Goal**: install the `vision` extra; `pose.py` wraps `YOLO("yolo11n-pose.pt").track(frame, persist=True)` and outputs `list[PersonPose(track_id, bbox, keypoints[17,3])]`; a visualization script draws skeletons.
- **Files**: `src/fall_detector/pose.py`, `src/fall_detector/__main__.py`.
- **Acceptance**: `python -m fall_detector --source demo/videos/walk.mp4 --show` displays skeletons and track_ids in a window at FPS ≥ 12.
- **How to test**: manual; FPS printed to the terminal.

### F2: Demo video footage ✅
- **Owner**: B
- **Goal**: record/collect ≥ 3 clips: normal walking, slowly lying down on a sofa (should not alert), a fall (should alert); fixed camera position (simulating a living-room camera height). Clips from the Le2i / UR Fall public datasets may supplement these (mind the licenses).
- **Files**: `demo/videos/*.mp4`, `demo/videos/README.md` (sources and expected results).
- **Acceptance**: every clip is labeled with its expected result.
- **How to test**: manual review.

### F3: Feature computation ✅
- **Owner**: B
- **Goal**: compute from keypoints: bbox aspect ratio, torso angle (shoulder midpoint–hip midpoint vs. vertical), hip-midpoint height (normalized to body height), fall speed; handle low-confidence keypoints.
- **Files**: `src/fall_detector/rules.py`, `tests/unit/test_fall_features.py`.
- **Classes/functions**: `compute_features(pose, ts) -> Features`.
- **Acceptance**: synthetic "upright" and "horizontal" keypoints produce the expected angles and aspect ratios.
- **How to test**: `uv run pytest -q tests/unit/test_fall_features.py`.

### F4: Fall state machine ✅
- **Owner**: B
- **Goal**: implement the state machine from 3.5 (parameters from settings), one instance per track, no repeat alerts during cooldown.
- **Files**: `src/fall_detector/rules.py`, `tests/unit/test_fall_state_machine.py`, `tests/fixtures/pose_sequences/*.json`.
- **Classes/functions**: `FallStateMachine.update(features) -> FallEvent | None`.
- **Acceptance**: synthetic sequences: fast fall then staying down 3s → exactly 1 alert; slowly lying down → none; down for 1s then getting up → none; staying down for 60s → only 1 alert within the cooldown. Results on the three demo videos match their labels.
- **How to test**: `uv run pytest -q tests/unit/test_fall_state_machine.py`; `python -m fall_detector --source demo/videos/fall_01.mp4 --dry-run`.

### F5: Event store, snapshots, and reporting ✅
- **Owner**: B
- **Goal**: `EventStore` appends to `data/fall_events.jsonl` (`event_id, ts, track_id, confidence, source[live|offline], verified, snapshot_id`) and maintains an in-memory index; on an event, save a snapshot with the skeleton overlay; `live` events call `POST /api/alerts` (`ref_id=event_id`); if the main service is unreachable, log locally and retry 3 times without blocking the detection thread.
- **Files**: `src/fall_detector/events.py`, `src/fall_detector/reporter.py`, `tests/unit/test_event_store.py`, `tests/unit/test_reporter.py`.
- **Classes/functions**: `EventStore.add(event, frame) -> FallEventRecord`, `EventStore.query(since, limit)`, `EventStore.snapshot_path(event_id)`, `Reporter.report(record)`.
- **Acceptance**: past events are still queryable after a process restart; with the main service running, playing the fall video → a fall row appears in the `alert` table and the dashboard receives it over SSE.
- **How to test**: `uv run pytest -q tests/unit/test_event_store.py tests/unit/test_reporter.py` (`httpx.MockTransport`); integration run.

### F6: MonitorController and service assembly (MJPEG) ✅
- **Owner**: B
- **Goal**: `MonitorController` manages the background detection thread (`start(source, loop)` / `stop()` / `status()` / `latest_frame()`, thread-safe, stop-then-start when switching sources); `server.py` assembles the Starlette app: `/stream` (MJPEG with skeletons and STANDING / FALLING / DOWN status text; video files loop when finished) and `/healthz`; monitoring starts automatically from `autostart_source` on launch.
- **Files**: `src/fall_detector/monitor.py`, `src/fall_detector/stream.py`, `src/fall_detector/server.py`, `tests/unit/test_monitor.py`.
- **Classes/functions**: `MonitorController`, `MonitorStatus`, `mjpeg_generator(controller)`, `create_app(settings) -> Starlette`.
- **Acceptance**: **M2** — after `uv run python -m fall_detector.server` starts, the dashboard panel shows the live detection view; when the fall video plays, the dashboard pops up a fall alert + snapshot.
- **How to test**: `uv run pytest -q tests/unit/test_monitor.py` (with a fake pose source); manual integration.

### F7: fall-mcp tools ✅
- **Owner**: B
- **Goal**: implement the 6 tools from 3.6 + the `fall://live/snapshot` resource with FastMCP, mounted at `/mcp` in `server.py` (Streamable HTTP); `--stdio` mode runs MCP only (no MJPEG); parameter schemas generated from type annotations; errors mapped to readable `isError=true` results; `get_event_snapshot` returns `ImageContent`.
- **Files**: `src/fall_detector/mcp_tools.py`, `src/fall_detector/server.py`, `tests/integration/test_fall_mcp.py`.
- **Classes/functions**: `build_mcp(controller, store) -> FastMCP`; tool functions `get_fall_events`, `get_event_snapshot`, `get_monitor_status`, `start_monitoring`, `stop_monitoring`, `analyze_video`.
- **Acceptance**: MCP Inspector (`npx @modelcontextprotocol/inspector`) connected to `http://127.0.0.1:8001/mcp` can list and call every tool; `analyze_video(demo/videos/fall_01.mp4)` returns ≥ 1 event and `analyze_video(demo/videos/lie_down.mp4)` returns 0; a nonexistent path returns a tool error instead of crashing.
- **How to test**: `uv run pytest -q tests/integration/test_fall_mcp.py` (MCP SDK in-process client + fake pose source); manual verification in the Inspector.

### F8: Claude Desktop integration ✅
- **Owner**: B
- **Goal**: write `docs/mcp_desktop.md` (a stdio config example for `claude_desktop_config.json`, including the absolute path for `uv run --directory`); in Claude Desktop, ask "Were any falls detected today? Show me the snapshot" and complete the tool calls.
- **Files**: `docs/mcp_desktop.md`.
- **Acceptance**: Claude Desktop lists the fall-mcp tools and returns events and snapshots.
- **How to test**: manual; record the screen as bonus demo material.
- **Status**: stdio verified with the MCP SDK client (6 tools, events + snapshot image from the shared store); asking inside Claude Desktop itself is still a manual check for rehearsal.

### F9 (optional): Second check with a vision model
- **Owner**: B
- **Goal**: when `vision_verify: true`, send the event snapshot to a GPT vision model (`fall_verify.txt`: "Is there a person who has fallen on the ground in this image? Reply with JSON only"); write the result to the event's `verified` field; if rejected, downgrade the alert to medium or don't push it.
- **Files**: `src/fall_detector/reporter.py`, `config/prompts/fall_verify.txt`.
- **Acceptance**: fall snapshots are confirmed; the lying-on-sofa snapshot is rejected.
- **How to test**: manually verify against 3 snapshots.

---

## Stage H: Companion Memory & Family Loop (goal: M4 — remembered, not monitored)

> Design: 2.5–2.8, 3.7–3.10. Priority tag after each title: P0 must ship, P1 next, P2 cut first.

### H1 (P0): Data model for memory, family loop, and privacy ✅
- **Owner**: A
- **Goal**: add the Stage H tables from 3.12 (`family_member`, `chat_session`, `symptom_mention`, `memory_item`, `reminder`, `reminder_log`, `daily_digest`, `claim`) and the columns `message.private`, `message.session_id`, `elder.privacy_disclosed_at`. D3's merge now also writes one `symptom_mention` per occurrence. Seed two family members (Amy, Ben). With `--with-history`, also seed an orchid follow-up due today, a neighbor mentioned 3 times, two stories, and one private segment.
- **Files**: `models.py`, `seed.py`, `symptoms/merge.py`, `tests/unit/test_models_stage_h.py`.
- **Acceptance**: `seed --reset --with-history` runs cleanly; every symptom occurrence has a `symptom_mention` row; existing D/E tests still pass.
- **How to test**: `uv run pytest -q tests/unit/test_models_stage_h.py tests/unit/test_symptom_merge.py`.
- **Notes**: `reminder`, `reminder_log` and `daily_digest` are not created (H5 / H7 deferred). `init_db` adds missing columns to an existing sqlite file (`ALTER TABLE ADD COLUMN`), so a v0.1 `data/app.db` keeps working. The demo daughter is now Amy (was Emily in v0.1 seed text); seeded history also has one chat session per day and counts the privacy rule as already disclosed. Neighbor is Rosa (not Linda), so the demo's private "Linda" never collides with the care list.

### H2 (P0): Companion memory extractor ✅
- **Owner**: A
- **Goal**: `extract_memory.txt` (kinds, "health complaints are not memory", verbatim `raw_quote`, privacy-request detection in English and Chinese, reminder acks, family replies, few-shots). Strict schema and merge rules from 3.7. Runs in the same `BackgroundTask` as symptom extraction, and a failure in one never affects the other.
- **Files**: `config/prompts/extract_memory.txt`, `memory/schema.py`, `memory/extractor.py`, `memory/store.py`, `eval/memory_cases.yaml`, `eval/run_memory_eval.py`, `tests/unit/test_memory_merge.py`.
- **Classes/functions**: `MemoryExtraction`, `MemoryExtractor.extract(user_text, recent_turns, session_agenda)`, `merge_memory_item(existing, item, now) -> MergeResult`, `process_message_memory(message_id)`.
- **Acceptance**: "I'm going to repot my orchid this week" → `follow_up(subject=orchid)` with a due date ≤ 7 days; health complaints yield no items; L2 memory eval ≥ 90% with privacy-request recall 100%.
- **How to test**: `uv run pytest -q tests/unit/test_memory_merge.py tests/integration/test_memory_pipeline.py`; `uv run python eval/run_memory_eval.py`.
- **Notes**: no `reminder_acks` (H5 deferred). Runs after the symptom pipeline in one background task (`pipeline.process_elder_message`); the dashboard's `activity` event is published after both. Stories merge only on identical words (each story is its own keepsake). A private mention never merges into a visible item (it gets a private twin), so private words never overwrite visible ones; a follow-up already asked reopens only for an explicit new date. A privacy request also hides the companion's reply to each covered message. The L2 memory eval (25 cases) has not been run against the API yet (no key on the build machine).

### H3 (P0): Agenda and follow-ups in the conversation ✅
- **Owner**: A
- **Goal**: `select_agenda` (3.8, pure function), greeting delivery and marking, and `build_context` extended with companion memory (private items tagged never-relay). Update `greet.txt` / `companion.txt` with reminder-style few-shots and the rule "no medication content, never speak as the family member". Reminder acks → `reminder_log`; end-of-day `no_response`.
- **Files**: `agenda/select.py`, `agenda/service.py`, `chat/context.py`, `chat/service.py`, `config/prompts/greet.txt`, `config/prompts/companion.txt`, `tests/unit/test_agenda_select.py`, `tests/integration/test_agenda_flow.py`.
- **Classes/functions**: `AgendaItem`, `select_agenda(reminders, follow_ups, now, budget) -> list[AgendaItem]`, `AgendaService.mark_carried(items, greet_message_id)`.
- **Acceptance**: the seeded orchid follow-up → the greeting asks about the orchid, and it is not asked again next session. With 3 due reminders + 1 follow-up pending, the greeting carries 3 items in priority order and the rest carry over. "Yes, I took it after breakfast" → `reminder_log.status = confirmed`.
- **How to test**: `uv run pytest -q tests/unit/test_agenda_select.py tests/integration/test_agenda_flow.py` (mock LLM).
- **Notes**: the agenda holds follow-ups only (reminders deferred); `AgendaItem.kind` leaves room for them ahead of follow-ups. `select_agenda(follow_ups, today, budget)`; `AgendaService.mark_carried(items)` runs only when the model's greeting (not the canned fallback) went out. Because replies ask one question, item 1 is the greeting's question and the rest stay in the companion's memory context for later in the chat.

### H4 (P0): Parent-controlled privacy ✅
- **Owner**: A
- **Goal**: implement 3.9: span clamping, `mark_private` with cache invalidation, the `visible_*` read layer, and retrofitting every family-facing read to use it (E1 endpoints, E2 summary, E5 local tools, history redaction). Add the private-day note to the summary, the first-session disclosure in the greeting, and the in-the-moment disclosure in `companion.txt`.
- **Files**: `privacy.py`, `memory/extractor.py`, `web/routes/family.py`, `summary.py`, `family_agent.py`, `config/prompts/companion.txt`, `config/prompts/greet.txt`, `tests/unit/test_privacy_filter.py`, `tests/integration/test_privacy_leak.py`.
- **Classes/functions**: `mark_private(message_ids)`, `visible_messages(...)`, `visible_symptoms(...)`, `visible_memory(...)`.
- **Acceptance**:
  - "My friend Linda got bad news from her doctor. Please keep this between us." → the leak test finds "Linda" in **no** family endpoint or page response; today's summary carries the private note; next session the agent can still ask the parent about Linda.
  - "Keep this between us, but I fell in the kitchen yesterday" → the high `fall` alert still fires with the quote.
  - The first-ever greeting contains the disclosure.
- **How to test**: `uv run pytest -q tests/unit/test_privacy_filter.py tests/integration/test_privacy_leak.py`.
- **Notes**: E5 is not built, so there is no `family_agent.py` to retrofit. Summary invalidation needs no hook: the cache key includes today's private message ids. Non-urgent symptom alerts raised by a private message are hidden via `alert.message_id`; one pushed live over SSE before a later "keep that between us" can still flash on an open dashboard until its next reload. A visible care-list item whose latest mention is marked private later loses that quote (text falls back to the subject).

### H5 (P0): Reminders on the dashboard ✅
- **Owner**: A (endpoints) / B (UI)
- **Goal**: `/api/reminders` endpoints (5.5). Dashboard panel: add a reminder (text + daily time or date); list with 7-day adherence dots; deactivate a reminder. The acting member defaults to the first family member (the switcher comes in H9).
- **Files**: `family_loop/reminders.py`, `web/routes/family.py`, `web/templates/family.html`, `web/static/family.js`, `tests/integration/test_reminders_api.py`.
- **Acceptance**: Amy adds "blood pressure pill, 8:00" → the next elder greeting after 8:00 checks on it → the parent says "yes, took it" → after a refresh the dashboard shows today as confirmed.
- **How to test**: `uv run pytest -q tests/integration/test_reminders_api.py`; manual (two windows).
- **Notes**: delivery is at the greeting only (3.8), so a reminder added mid-session waits for the
  next one. Acks ride on the memory extractor's `reminder_acks` (3.7), which costs no extra call and
  is ignored unless that reminder was actually raised today. Unanswered earlier days are swept to
  `no_response` when the next greeting is built. The adherence row shows status only, never a quote,
  so a private answer cannot leak through it. The `alert` table is untouched: a missed dose is not
  an alert, it is a dot on the card.

### H6 (P1): Doctor one-pager ✅
- **Owner**: A (query) / B (template, print CSS)
- **Goal**: `/family/doctor?days=30` per 3.10: per-symptom table + exact quotes with timestamps from visible `symptom_mention`s, red-flag alerts, reminder adherence, disclaimer; a "Print / Save as PDF" button.
- **Files**: `reports/doctor.py`, `web/routes/reports.py`, `web/templates/doctor.html`, `web/static/style.css`, `tests/unit/test_doctor_report.py`.
- **Acceptance**: prints to ≤ 2 Letter pages; every quote matches a stored `raw_quote`; private non-red-flag symptoms are absent; it makes no LLM call (the test uses a client that raises).
- **How to test**: `uv run pytest -q tests/unit/test_doctor_report.py`; manual print preview.
- **Notes**: no reminder-adherence section (the weekly report covers adherence). Log rows of the same symptom are combined into one line; the 4 most recent quotes are shown with a count of earlier ones, which keeps the page short.

### H7 (P1): Daily digest and weekly report ✅
- **Owner**: A (B: styling)
- **Goal**: `daily_summary.txt` → structured `{summary, mood_score, topics}` stored in `daily_digest` (3.10), with E2 reading from it. Weekly report page: mood line, top topics, symptom counts and trend, reminder adherence, and a 2–3 sentence overview.
- **Files**: `reports/digest.py`, `reports/weekly.py`, `summary.py`, `config/prompts/daily_summary.txt`, `config/prompts/weekly_report.txt`, `web/templates/report_weekly.html`, `tests/unit/test_weekly_aggregate.py`.
- **Acceptance**: with seeded history the report shows 7 mood points, the top 3 topics, symptom trends, and adherence; days with private segments carry the private note and no private content.
- **How to test**: `uv run pytest -q tests/unit/test_weekly_aggregate.py` (mock digests),
  `tests/integration/test_weekly_report.py`; manual view and print preview.
- **Notes**: the digest row is the cache, keyed by a fingerprint of the day's messages, symptoms,
  alerts and privacy marks, so E2's separate TTL cache is gone (it could serve a stale summary for
  minutes right after she spoke). A privacy request drops the digests for the days it touches
  (`digest.invalidate`), because the request normally arrives after the day was already summarized.
  The weekly page reuses stored digests for past days and only builds today, so it is one model
  call for the overview. Every number on the page is computed in code from `visible_symptoms` and
  `reminder_log`; the model only writes the overview, and with the model down the page still shows
  the full mood line, tables and adherence. `seed_history` writes six digests so the demo opens
  with a real mood line instead of seven live calls.

### H8 (P1): Care list ✅
- **Owner**: A
- **Goal**: `/api/care-list` + a dashboard card: visible `person | topic` items with ≥ `care_list_min_mentions`, count, last mention, and latest quote.
- **Files**: `memory/care_list.py`, `web/routes/family.py`, `web/templates/family.html`, `web/static/family.js`, `tests/unit/test_care_list.py`.
- **Acceptance**: the seeded neighbor (3 mentions) appears; single mentions and private items don't.
- **How to test**: `uv run pytest -q tests/unit/test_care_list.py`.

### H9 (P2): Sibling sharing and claims ✅
- **Owner**: A (endpoints) / B (UI)
- **Goal**: a member switcher (`/family?member=<id>`, remembered in `localStorage`); `/api/claims` endpoints; a "I'll handle this" button with a note on alerts and care-list items; "Ben is handling this" badges; mark done.
- **Files**: `family_loop/claims.py`, `web/routes/family.py`, `web/templates/family.html`, `web/static/family.js`, `tests/integration/test_claims_api.py`.
- **Acceptance**: Ben claims the chest-tightness alert with "I'll call her doctor" → Amy's view shows the badge; after marking it done the badge shows as handled.
- **How to test**: `uv run pytest -q tests/integration/test_claims_api.py`; manual with two browser windows.
- **Notes**: `?member=` accepts an id or a name (`?member=ben`). Also `GET /api/claims`. Verified in the browser: Ben's claim shows as "Ben is handling this: “I'll call her doctor”" in Amy's view.

### H10 (P2): Family memoir ✅
- **Owner**: A (backend) / B (page)
- **Goal**: `/family/memoir` per 3.10: visible stories in chronological order, the verbatim excerpt next to a cached retelling (`memoir.txt`, which must not add facts).
- **Files**: `reports/memoir.py`, `config/prompts/memoir.txt`, `web/routes/reports.py`, `web/templates/memoir.html`.
- **Acceptance**: the seeded stories appear with their excerpts; private stories are absent; a second load makes no LLM calls (cache).
- **How to test**: `uv run pytest -q tests/unit/test_memoir.py`; manual.
- **Notes**: the retelling is cached on `memory_item.retelling`; if the model is down the page shows her words and retries on the next load.

### H11 (P1): Personal details scrubbed from the family's view of the conversation ✅
- **Owner**: A
- **Goal**: on top of H4's parent-controlled marks, the family never reads personal details in the conversation: phone numbers, emails, addresses, ID / card / account numbers, passwords and PINs, bank balances and bank names, and the surnames of people outside the family. The companion's own context keeps the original words (ADR 22).
- **Design**: two passes in `redaction.py`. `redact_rules` (regex, deterministic, no network) always runs, also over the LLM's output. `Redactor` makes one `extract_json` call per chat turn (user message + reply; greetings too) with `redact_family.txt` and stores the result in the new nullable `message.family_text`; it runs in the background pipeline after memory, before the `activity` event, so the dashboard reloads an already-redacted turn. On LLM failure the rule-based text is stored. Readers use `family_text(msg)`: the stored text, or rules only for rows not yet processed (seed data, older messages).
- **Files**: `redaction.py`, `config/prompts/redact_family.txt`, `models.py`, `privacy.py`, `summary.py`, `pipeline.py`, `web/app.py`, `web/deps.py`, `web/routes/chat.py`, `config/mock_llm.yaml`, `tests/unit/test_redaction.py`, `tests/integration/test_family_redaction.py`.
- **Acceptance**:
  - "My bank PIN is 4821 and Amy's number is 555-123-4567" → `/api/messages` shows `[password]` and `[phone number]`, keeps the rest of the sentence, and the stored `message.text` is unchanged.
  - Today's summary prompt never contains those details.
  - Ordinary talk (symptoms, times, blood pressure "140/90", dates, first names) is left unchanged.
- **How to test**: `uv run pytest -q tests/unit/test_redaction.py tests/integration/test_family_redaction.py`.
- **Notes**: checked against the real model: "Rosa Diaz at 12 Oak Lane … Chase savings has about 80,000 dollars, PIN 4821" → "Rosa at [address] … [financial detail] savings has about [financial detail], PIN [password]". Costs one extra (background) LLM call per turn. Not yet applied: symptom `raw_quote`s on the timeline / doctor one-pager and memoir excerpts.

### H12 (P2): Woman's / man's voice switch ✅
- **Owner**: A
- **Goal**: a two-button switch in the elder app header ("Woman's voice" / "Man's voice"), remembered per device in `localStorage`; switching replays the latest reply in the new voice.
- **Design**: `llm.tts_voices` maps a key to an OpenAI voice (`female: coral`, `male: ash`, overridable via `TTS_VOICE_FEMALE` / `TTS_VOICE_MALE`). `GET /api/tts/{id}?voice=male` (unknown key → 422, no param → `tts_voice`); audio is cached per voice as `{id}.{voice}.mp3`. The browser-voice fallback picks a voice whose name suggests the chosen gender when one is installed.
- **Files**: `settings.py`, `config/settings.yaml`, `llm/client.py`, `llm/mock.py`, `chat/service.py`, `web/routes/chat.py`, `web/routes/pages.py`, `web/templates/elder.html`, `web/static/elder.js`, `web/static/style.css`, `tests/integration/test_tts.py`, `tests/integration/test_pages.py`.
- **Acceptance**: the switch renders on `/elder`; `?voice=male` and `?voice=female` each generate once and are then cached; `?voice=robot` → 422.
- **How to test**: `uv run pytest -q tests/integration/test_tts.py tests/integration/test_pages.py`; manual: switch voices in the elder app.
- **Notes**: verified in the browser with the real model (male mp3 returned). Browsers that loaded the elder app before this change need a hard refresh (static files are not versioned).

### H13 (P0): No medical advice ✅
- **Owner**: A
- **Goal**: the companion never gives medical advice, even when asked: no causes, diagnoses or "it's nothing serious", no medicines, doses or supplements, no treatments or home remedies. It listens, and points her to her doctor. Emergencies still get "call 911 now".
- **Design**: two layers. `companion.txt` ("Health: you never give medical advice", with a "What can I take?" example) and `daily_summary.txt`. Then `guard_medical_advice` in `chat/postprocess.py`, a deterministic check run on every reply and greeting. It covers medicine names, doses, "take / stop / double your pills", ice / heat / compresses and similar remedies, and diagnosis or reassurance phrases, in English and Chinese. A reply that trips it is replaced with a safe line in the same language. Replies that mention 911 / 急救 are never replaced, and neither are reminder questions like "Did you take your pills?".
- **Files**: `config/prompts/companion.txt`, `config/prompts/daily_summary.txt`, `chat/postprocess.py`, `chat/service.py`, `tests/unit/test_postprocess.py`, `tests/unit/test_context.py`.
- **Acceptance**: "Try taking some ibuprofen", "put some ice on it", "200 mg", "sounds like arthritis", "建议你吃点止痛药", "热敷一下" are all replaced; ordinary caring replies, reminders and the 911 reply pass unchanged.
- **How to test**: `uv run pytest -q tests/unit/test_postprocess.py`.
- **Notes**: with the real model, "What should I take for it? Is ibuprofen okay?" gets "I can't give medical advice, but your doctor is the best person to ask" from the prompt alone.

### H14 (P0): Ask before sharing ✅
- **Owner**: A
- **Goal**: when she mentions a symptom or a personal matter (money, family quarrels, a friend's troubles, secrets), the companion asks whether it may tell her family. Until she says yes, the family sees none of it. Each subject is asked about once and her answer is remembered. Red flags never wait (ADR 19).
- **Design** (ADR 23): `share_consent` holds one row per subject: `symptom:<canonical>` for non-red-flag symptoms, `topic:<subject>` for personal matters. Its decision is `pending | share | private`. `message.consent_keys` records which subjects a held message waits on. `consent.py` runs in the pipeline after symptoms and before memory, and makes one extraction call (`extract_consent.txt`) that returns personal topics and her answer to a pending question. Her answer is applied first; an empty answer applies to the question just asked. The message and the companion's reply are then held (`private = true`) when any of their subjects is not shared. A "yes" releases held messages whose subjects are now all shared, along with the memory items first seen in them. Symptom mentions, alerts, the summary and reports follow `message.private` through `privacy.py`, so nothing else changed there. Non-red-flag alerts are pushed over SSE only after the consent step and only if visible; red-flag alerts are pushed immediately. "Keep this between us" (H4) still marks messages private for good: `mark_private` clears their consent keys. The companion sees her choices in the "Her sharing choices" section of `companion.txt`. The first-chat disclosure now says it will always ask before telling the family anything personal. `privacy.ask_before_sharing` (`ASK_BEFORE_SHARING`) switches the whole feature off.
- **Family view**: a held message shows "Not shared: Maggie hasn't said yet whether to share this" (`awaiting_consent`) or "Kept private at Maggie's request". The private-day note now reads "Part of today's conversation is not shared: … chose to keep it private or has not said yet whether to share it."
- **Files**: `consent.py`, `config/prompts/extract_consent.txt`, `config/prompts/companion.txt`, `models.py`, `privacy.py`, `pipeline.py`, `symptoms/service.py`, `chat/context.py`, `chat/service.py`, `settings.py`, `config/settings.yaml`, `config/mock_llm.yaml`, `web/app.py`, `web/deps.py`, `web/routes/family.py`, `web/static/family.js`, `tests/integration/test_share_consent.py`, `tests/integration/test_demo_script.py`.
- **Acceptance**:
  - "My knee hurts again." → nothing on the dashboard, and `share_consent` has `symptom:joint_pain = pending` → "Yes, you can tell her." → the knee pain and both messages appear.
  - Once she has agreed, a later knee mention shows right away, and the companion's context says not to ask again.
  - "No, don't tell them." → the subject stays hidden for good, including later mentions and the "no" itself.
  - A money worry is held until she says yes.
  - Chest pain → high alerts are pushed immediately and no consent row is created.
  - A severe (medium) knee alert is not pushed until she says yes.
- **How to test**: `uv run pytest -q tests/integration/test_share_consent.py tests/integration/test_demo_script.py`.
- **Notes**: checked with the real model on a throwaway DB (EN + ZH): dizziness, knee pain, poor sleep and "son asking for money" were each held, then released on "yes" or kept private on "no". The model usually asks the sharing question the first time a subject comes up, but sometimes asks a detail question first and asks about sharing on the next turn; the subject stays hidden in the meantime. The extra extraction call runs in the background.

---

## Stage G: Wrap-up (goal: a stable, well-told demo)

### G1: UI polish and responsiveness ✅
- **Owner**: B (with A's help)
- **Goal**: elder app: large text, high contrast, button animations, status illustrations; dashboard: card layout, severity colors, alert banner; a disclaimer ("This product does not provide medical diagnosis").
- **Files**: `web/static/style.css`, both templates.
- **Acceptance**: no horizontal scrolling at iPad / phone sizes (390px); in the elder app it's obvious at a glance where to "press here to talk".
- **How to test**: browser device emulation + real devices.

### G2: Demo script and materials
- **Owner**: B (reviewed by A)
- **Goal**: write `demo/SCRIPT.md` (expanding the draft below down to every line, expected screen, and fallback), and prepare a backup screen recording (to play if the network completely fails).
- **Files**: `demo/SCRIPT.md`, `demo/backup_recording.mp4`.
- **Acceptance**: the full demo is ≤ 7 minutes and every step has a fallback.
- **How to test**: rehearsal.

### G3: One-command startup and README
- **Owner**: A
- **Goal**: `scripts/dev_up.ps1`: init DB + seed → start web → start fall_detector; the README clearly covers environment, `.env`, startup, and demo URLs.
- **Files**: `scripts/dev_up.ps1`, `README.md`.
- **Acceptance**: a fresh clone runs within 10 minutes by following the README.
- **How to test**: run it from scratch on the teammate's machine.

### G4: End-to-end acceptance and rehearsal
- **Owner**: A + B
- **Goal**: run all of L1 + L2 (both evals); rehearse the demo script ≥ 3 times and record the 2.9 metrics; prepare the mock fallback (the full demo script replays under `LLM_PROVIDER=mock`).
- **Files**: `DEV_SPEC.md` (final progress and metrics).
- **Acceptance**: `uv run pytest -q` is all green (including the privacy leak test); the 2.9 table is filled in; every P0/P1 task in the progress table is ✅.
- **How to test**: `uv run pytest -q && uv run python eval/run_extraction_eval.py && uv run python eval/run_memory_eval.py`.

### Demo Script (draft)

| # | Action | Expected |
|---|---|---|
| 0 | On the dashboard, show Amy's reminder "blood pressure pill with breakfast" and its 7-day dots | One day missed, the rest taken; today not asked yet |
| 1 | Open the elder app and tap "Start chatting" | AI greets by time of day and raises Amy's reminder in her words ("Amy asked me to check ..."), ahead of the orchid follow-up |
| 1b | Elder: "Yes, I took it with breakfast." | AI is warm and moves on; the dashboard's dot for today turns green (confirmed) without anyone typing anything |
| 2 | Elder: "My knee's much better, but I didn't sleep well last night." | AI asks whether to let Amy know; the dashboard shows "Not shared: Maggie hasn't said yet …" and no `insomnia` |
| 2b | Elder: "Yes, you can tell her." | `insomnia` and the conversation appear on the dashboard (H14) |
| 3 | Elder: "这两天早上起来头有点晕" ("I've been a bit dizzy in the mornings these past two days"), then "可以，告诉她吧" | AI asks whether to tell Amy; after "yes" `dizziness` appears on the dashboard timeline |
| 4 | Elder: "My friend Linda got bad news from her doctor. Keep this between us, okay?" | AI agrees and gives the short safety disclosure; nothing about Linda anywhere on the dashboard |
| 5 | Elder: "My chest feels tight and I can't catch my breath" | AI reassures and suggests contacting family / 911; a **high** alert pops up on the dashboard |
| 6 | Switch to the dashboard and play the fall video | Detection view shows FALLING → DOWN; fall alert + snapshot pops up |
| 7 | Refresh today's summary on the dashboard | Summary covers sleep, dizziness, and the chest-tightness alert, plus "Part of today's conversation is not shared" |
| 8 | Switch to Ben (`?member=ben`), claim the chest alert: "I'll call her doctor"; open the doctor one-pager and Maggie's stories | Amy's view shows "Ben is handling this"; one-pager lists symptoms with exact words; memoir shows her teaching story |
| 8b | Open the weekly report | Mood line over 7 days with the mid-week dip, top topics led by the knee, one grouped row per symptom with its direction, and the pill's adherence dots; prints to one page |
| 9 | Dashboard "Ask AI": "Did Mom fall today? Anything else I should know?" (E5) | Answer cites the fall time + symptoms (no Linda); tool_calls are shown |
| 10 | Switch to Claude Desktop and ask the same question (F8) | Returns events and snapshots via fall-mcp — showing the capability is reusable |

### Delivery Milestones

| Milestone | Stages completed | When | What can be demoed |
|---|---|---|---|
| Contract frozen | A | D1 morning | `POST /api/alerts` available; B can integrate |
| M1 | A–D | D1 evening | Voice chat + symptom log + red-flag alerts (SSE) |
| M2 | E, F1–F6 | D2 morning | Complete dashboard + fall alerts |
| M3 | F7–F8 (+E5) | D2 midday | fall-mcp called from Inspector / Claude Desktop; (optional) dashboard Q&A agent |
| M4 | H1–H4 (H5 deferred) | D3 midday | Orchid follow-up, privacy with red-flag bypass |
| Delivery | G (+ H6, H8–H10; H7 deferred) | D3 evening | Polished UI + doctor one-pager + memoir + demo script + fallbacks |

---

## 7. Extensibility & Future Work

- **Realtime voice**: OpenAI Realtime API (WebRTC) for <1s speech-to-speech with barge-in; side-channel transcription keeps feeding symptom extraction.
- **More vision MCP tools**: activity stats (how long she walked around today), alerts for prolonged sitting / not being seen for a long time, number of nighttime bathroom trips — growing fall-mcp into a home-vision-mcp.
- **More robust fall recognition**: replace rules with ST-GCN / PoseC3D (NTU RGB+D `falling down`); fine-tune on in-home footage; multiple cameras.
- **Non-visual sensing**: mmWave radar (for private spaces like bathrooms/bedrooms), smartwatch fall detection and heart rate.
- **Telephony**: daily outbound calls and a parent-initiated callback number (e.g., Twilio) on the same chat / extraction / agenda pipeline, for parents who won't open an app.
- **Scam alert**: flag mentions of a stranger's call, a money transfer, or a prize — common phone scams against elders — as a new alert type.
- **Family ↔ parent messages**: a family member leaves a message, the agent passes it on in its own voice (no voice cloning), and the parent's answer is carried back.
- **Family reminders and reports** (deferred from Stage H): reminders with adherence (H5) and the daily digest / weekly report (H7), as designed in 2.6, 2.8, 3.8 and 3.10.
- **Reminder escalation**: notify the family when a reminder is declined or unanswered several days in a row.
- **Cognitive and mood trends**: long-term tracking of loneliness, low mood, and repeated questions (an early sign of cognitive decline), building on the weekly report.
- **Deeper long-term memory**: v1 keeps structured memory items (2.5); next is vector retrieval over all past conversations and preferences.
- **Privacy controls for the parent**: lift a privacy mark later ("you can tell her now"), review what's private.
- **Notification channels**: SMS / email / app push; one-tap call to family in emergencies.
- **Multi-family and permissions**: real accounts, per-member permissions, a read-only view for doctors.
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
| 11 | One hard-coded elder; several family members picked by URL parameter, no login (updated in v0.2 for sibling sharing) | Account system | Controls scope; the demo focuses on core value |
| 12 | Demo relies mainly on pre-recorded video + seeded history, with a mock fallback | Everything live on site | Reproducible demo; avoids failures from network or lighting |
| 13 | fall-mcp serves `/mcp` + `/stream` + `/healthz` from one Starlette process; stdio mode for desktop clients | MJPEG and MCP in two processes | Shares one MonitorController and latest frame; avoids cross-process sync |
| 14 | fall-mcp is the single source of truth for fall events; the main service's alert table only stores alert copies (`ref_id`) | Main service stores all events | Offline-analysis events create no alerts but remain queryable; clear separation of responsibilities |
| 15 | Demo footage = UR Fall Detection Dataset clips (RGB half, 2x, last frame held 4s), rebuilt by `scripts/fetch_demo_media.py`, not committed | Self-recorded clips; Le2i (sign-up required) | Public, CC BY-NC-SA 4.0, fixed indoor camera like the target setup; clips stay out of git but anyone can rebuild them |
| 16 | fall-mcp uses mcp 2.x `MCPServer` (FastMCP renamed) | Pin `mcp<2` | The installed SDK is 2.x; in-process `Client(server)` makes tool tests cheap |
| 17 | Companion memory uses its own extractor call and prompt, separate from symptom extraction | One combined extraction call | Memory-prompt changes can't regress red-flag recall; failures are independent; both are async, so the extra call costs no latency |
| 18 | Privacy = mark on write, filter on read through one module (`privacy.py`) | Delete private content; let the LLM decide what to omit from summaries | The AI keeps remembering; retroactive marking works; one module to test for leaks |
| 19 | High-level red flags always bypass privacy; the rule is disclosed up front and in the moment | Honor every privacy request; make all symptoms bypass | Keeps the safety floor deterministic while the privacy promise stays meaningful for everything else |
| 20 | Reminders and follow-ups share one agenda, carried only by the greeting with a budget of 3 | Inject everything into every turn | Deterministic delivery status; never interrupts; avoids the checklist feel |
| 21 | Doctor one-pager is rendered from structured data without an LLM | LLM-written summary | Doctors need exact words and dates; nothing can be invented |
| 22 | Family view of the conversation = redacted copy (`message.family_text`, LLM pass + regex rules always), original kept for the companion | Redact before saving; regex only; show the family summaries only | The AI keeps her words; rules give a deterministic floor even when the model is down; the family still sees what she actually talked about |
| 23 | Ask before sharing: symptoms and personal matters are held from the family (`message.private`) until she says yes; the choice is remembered per subject in `share_consent`; red flags never wait | Show everything unless she says "keep this between us"; ask about every mention | "Remembered, not monitored": the family only sees what she chose to share; asking once per subject avoids nagging; reusing `message.private` keeps one read path (ADR 18) |
