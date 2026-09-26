/* Elder app: hold-to-talk (or tap-to-start / tap-to-stop) voice chat with the companion.
 *
 * Flow per turn: record -> POST /api/chat/audio -> show reply text -> GET /api/tts/{id} -> play.
 * If TTS is unavailable (204) or fails, the browser's own speech synthesis reads the reply.
 */
(() => {
  "use strict";

  const cfg = window.APP_CONFIG || {};
  const $ = (id) => document.getElementById(id);
  const els = {
    start: $("start-screen"),
    startBtn: $("start-btn"),
    chat: $("chat-screen"),
    conversation: $("conversation"),
    status: $("status"),
    mic: $("mic-btn"),
    typeInstead: document.querySelector(".type-instead"),
    textForm: $("text-form"),
    textInput: $("text-input"),
  };

  const TEXT = {
    idle: "Hold the button and talk to me",
    listening: "I'm listening…",
    listeningTap: "I'm listening… tap the button again when you're done",
    thinking: "Let me think…",
    speaking: "Speaking… tap the button to talk",
    tooShort: "That was very short. Hold the button down while you talk.",
    silence: "I didn't hear anything. Hold the button and talk to me.",
    retry: { en: "Sorry, I didn't quite catch that. Could you say it again?", zh: "不好意思，我没听清楚，能再说一遍吗？" },
    micDenied: "Please allow the microphone so I can hear you. You can also type below.",
    noMic: "This browser can't record audio. You can type below instead.",
    error: "Sorry, something went wrong. Please try again.",
  };
  const TAP_MS = 350;          // shorter press = tap mode (tap again to stop)
  const MIN_RECORD_MS = 500;   // ignore accidental blips
  const MAX_RECORD_MS = 60000; // auto-stop long recordings
  const MAX_BUBBLES = 3;
  // Peak RMS below this means nobody spoke. Sending silence makes the speech model invent
  // sentences, so such clips are never uploaded. Speech is typically 0.05-0.3.
  const SPEECH_LEVEL = 0.012;
  const CJK = /[一-鿿]/;

  let state = "idle";
  let lastLang = "en";
  let stream = null;
  let recorder = null;
  let chunks = [];
  let recStart = 0;
  let maxTimer = null;
  let pressing = false;
  let pressStart = 0;
  let tapMode = false;
  let stopPlayback = null; // ends the current playback early (barge-in)
  const player = new Audio();
  let audioCtx = null;
  let meter = null; // { source, timer } while recording
  let peakLevel = 0;

  // Timings for latency checks: window.__timings in the console.
  window.__timings = [];
  function logTiming(label, t0) {
    const ms = Math.round(performance.now() - t0);
    window.__timings.push({ label, ms });
    console.info(`[timing] ${label}: ${ms} ms`);
  }

  const langOf = (text) => (CJK.test(text) ? "zh" : "en");

  function setStatus(next, text) {
    state = next;
    document.body.dataset.state = next;
    els.status.textContent = text || TEXT[next] || "";
    els.mic.setAttribute("aria-pressed", String(next === "listening"));
    els.mic.setAttribute("aria-label", next === "listening" ? "Stop and send" : "Hold to talk");
    els.mic.disabled = next === "thinking";
  }

  function addBubble(role, text) {
    const div = document.createElement("div");
    div.className = `bubble ${role}`;
    div.textContent = text;
    if (langOf(text) === "zh") div.lang = "zh";
    els.conversation.append(div);
    while (els.conversation.children.length > MAX_BUBBLES) {
      els.conversation.firstElementChild.remove();
    }
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
    return res.json();
  }

  function fail(err) {
    console.error(err);
    setStatus("idle", TEXT.error);
  }

  // ---------- playback ----------

  // A tiny silent WAV, played inside the first tap so iOS Safari lets us play audio later.
  function silentWavUrl() {
    const samples = 800;
    const buf = new ArrayBuffer(44 + samples * 2);
    const v = new DataView(buf);
    const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    str(0, "RIFF"); v.setUint32(4, 36 + samples * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 8000, true); v.setUint32(28, 16000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, samples * 2, true);
    return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
  }

  function unlockAudio() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx && !audioCtx) audioCtx = new Ctx();
    player.src = silentWavUrl();
    player.play().catch(() => {});
    if ("speechSynthesis" in window) window.speechSynthesis.speak(new SpeechSynthesisUtterance(""));
  }

  function stopSpeaking() {
    if (stopPlayback) stopPlayback();
  }

  function playBlob(blob) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true;
        player.onended = player.onerror = null;
        stopPlayback = null;
        URL.revokeObjectURL(url);
        resolve();
      };
      stopPlayback = () => { player.pause(); done(); };
      player.onended = done;
      player.onerror = done;
      player.src = url;
      player.play().catch(done);
    });
  }

  function speakWithBrowser(text) {
    return new Promise((resolve) => {
      if (!("speechSynthesis" in window)) return resolve();
      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true;
        stopPlayback = null;
        resolve();
      };
      const u = new SpeechSynthesisUtterance(text);
      u.lang = langOf(text) === "zh" ? "zh-CN" : "en-US";
      u.rate = 0.9;
      u.onend = done;
      u.onerror = done;
      stopPlayback = () => { window.speechSynthesis.cancel(); done(); };
      window.speechSynthesis.speak(u);
    });
  }

  async function playReply(messageId, text) {
    setStatus("speaking");
    const t0 = performance.now();
    try {
      const res = await fetch(`/api/tts/${messageId}`, { cache: "no-cache" });
      if (state !== "speaking") return; // she started talking again meanwhile
      if (res.status === 200) {
        const blob = await res.blob();
        logTiming("tts audio", t0);
        if (state !== "speaking") return;
        await playBlob(blob);
      } else {
        await speakWithBrowser(text);
      }
    } catch (err) {
      console.warn("TTS failed, using browser voice", err);
      if (state === "speaking") await speakWithBrowser(text);
    }
    if (state === "speaking") setStatus("idle");
  }

  async function showReply(data) {
    addBubble("assistant", data.reply_text);
    if (data.message_id) await playReply(data.message_id, data.reply_text);
    else setStatus("idle");
  }

  // ---------- recording ----------

  function pickMimeType() {
    for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return "";
  }

  async function ensureStream() {
    if (stream && stream.active) return stream;
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    return stream;
  }

  // ---------- input level (silence detection) ----------

  function startLevelMeter() {
    peakLevel = 0;
    if (!audioCtx) {
      peakLevel = 1; // can't measure: never block sending
      return;
    }
    try {
      if (audioCtx.state === "suspended") audioCtx.resume();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      const buf = new Float32Array(analyser.fftSize);
      const timer = setInterval(() => {
        analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const x of buf) sum += x * x;
        peakLevel = Math.max(peakLevel, Math.sqrt(sum / buf.length));
      }, 50);
      meter = { source, timer };
    } catch (err) {
      console.warn("level meter unavailable", err);
      peakLevel = 1;
    }
  }

  function stopLevelMeter() {
    if (!meter) return;
    clearInterval(meter.timer);
    meter.source.disconnect();
    meter = null;
  }

  function openTyping() {
    els.typeInstead.open = true;
    els.textInput.focus();
  }

  async function startRecording() {
    stopSpeaking();
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      pressing = false;
      setStatus("idle", TEXT.noMic);
      openTyping();
      return;
    }
    try {
      await ensureStream();
    } catch (err) {
      console.warn(err);
      pressing = false;
      setStatus("idle", TEXT.micDenied);
      return;
    }
    const mimeType = pickMimeType();
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunks = [];
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.start();
    startLevelMeter();
    recStart = performance.now();
    maxTimer = setTimeout(stopAndSend, MAX_RECORD_MS);
    // Released before the mic was ready (e.g. during the permission prompt): switch to tap mode.
    tapMode = tapMode || !pressing;
    setStatus("listening", tapMode ? TEXT.listeningTap : TEXT.listening);
  }

  function stopAndSend() {
    if (!recorder || recorder.state !== "recording") return;
    clearTimeout(maxTimer);
    stopLevelMeter();
    const rec = recorder;
    const duration = performance.now() - recStart;
    const level = peakLevel;
    recorder = null;
    tapMode = false;
    rec.onstop = () => {
      if (duration < MIN_RECORD_MS) {
        setStatus("idle", TEXT.tooShort);
        return;
      }
      console.info(`[level] peak RMS ${level.toFixed(3)}`);
      if (level < SPEECH_LEVEL) {
        setStatus("idle", TEXT.silence);
        return;
      }
      sendAudio(new Blob(chunks, { type: rec.mimeType || "audio/webm" }));
    };
    rec.stop();
  }

  async function sendAudio(blob) {
    setStatus("thinking");
    const t0 = performance.now();
    const form = new FormData();
    form.append("audio", blob, "speech");
    if (cfg.elderId) form.append("elder_id", String(cfg.elderId));
    try {
      const res = await fetch("/api/chat/audio", { method: "POST", body: form });
      if (!res.ok) throw new Error(`/api/chat/audio -> HTTP ${res.status}`);
      const data = await res.json();
      logTiming("voice turn (upload + transcribe + reply)", t0);
      if (data.need_retry) {
        const msg = TEXT.retry[lastLang];
        setStatus("idle", msg);
        speakWithBrowser(msg);
        return;
      }
      lastLang = langOf(data.user_text);
      addBubble("user", data.user_text);
      await showReply(data);
    } catch (err) {
      fail(err);
    }
  }

  // ---------- input handlers ----------

  function onPress(e) {
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    if (state === "thinking") return;
    if (recorder && recorder.state === "recording") { // second tap in tap mode
      stopAndSend();
      return;
    }
    try {
      els.mic.setPointerCapture(e.pointerId); // keep receiving pointerup if the finger slides off
    } catch {
      /* not critical */
    }
    pressing = true;
    pressStart = performance.now();
    tapMode = false;
    startRecording();
  }

  function onRelease() {
    if (!pressing) return;
    pressing = false;
    if (!recorder) return; // mic still starting; startRecording switches to tap mode
    if (performance.now() - pressStart < TAP_MS) {
      tapMode = true;
      setStatus("listening", TEXT.listeningTap);
      return;
    }
    stopAndSend();
  }

  els.mic.addEventListener("pointerdown", onPress);
  els.mic.addEventListener("pointerup", onRelease);
  els.mic.addEventListener("pointercancel", onRelease);
  els.mic.addEventListener("contextmenu", (e) => e.preventDefault()); // long-press menu on phones
  els.mic.addEventListener("click", (e) => {
    if (e.detail !== 0) return; // pointer clicks are handled above; this is Enter/Space
    if (recorder && recorder.state === "recording") stopAndSend();
    else if (state !== "thinking") {
      tapMode = true;
      pressing = false;
      startRecording();
    }
  });

  els.textForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = els.textInput.value.trim();
    if (!text || state === "thinking") return;
    stopSpeaking();
    els.textInput.value = "";
    lastLang = langOf(text);
    addBubble("user", text);
    setStatus("thinking");
    const t0 = performance.now();
    try {
      const data = await postJSON("/api/chat", cfg.elderId ? { text, elder_id: cfg.elderId } : { text });
      logTiming("text turn", t0);
      await showReply(data);
    } catch (err) {
      fail(err);
    }
  });

  els.startBtn.addEventListener("click", async () => {
    unlockAudio();
    els.start.hidden = true;
    els.chat.hidden = false;
    setStatus("thinking");
    // Ask for the mic now so the permission prompt doesn't interrupt her first sentence.
    if (navigator.mediaDevices && window.MediaRecorder) ensureStream().catch(() => {});
    const t0 = performance.now();
    try {
      const data = await postJSON("/api/chat/greet", cfg.elderId ? { elder_id: cfg.elderId } : {});
      logTiming("greeting", t0);
      await showReply(data);
    } catch (err) {
      fail(err);
    }
  });
})();
