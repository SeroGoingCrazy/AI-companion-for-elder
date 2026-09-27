/* Family dashboard: today's summary, alerts (live over SSE), symptom timeline, fall view,
 * conversation history.
 *
 * GET /api/alerts/stream sends `alert` (new alert: banner + sound) and `activity` (the elder
 * said something: reload the timeline and history). All text is inserted with textContent.
 */
(() => {
  "use strict";

  const cfg = window.APP_CONFIG || {};
  const $ = (id) => document.getElementById(id);
  const tz = cfg.timezone || undefined;

  // Server-rendered strings for the chosen language (config/i18n/<lang>.yaml).
  const S = cfg.t || {};
  const SEV = cfg.severity || {};
  const STATUS_TEXT = cfg.status || { new: "new", ongoing: "ongoing", improved: "improving", resolved: "resolved" };
  const HISTORY_PAGE = 30;
  const ACTIVITY_DEBOUNCE_MS = 800;
  const FALL_RETRY_MS = 15000;

  // ---------- helpers ----------

  // Dates follow the interface language, not the browser's, so a switch to Chinese
  // does not leave "Sat, Sep 26" sitting next to Chinese text.
  const LOCALE = cfg.lang === "zh" ? "zh-CN" : "en-US";

  const timeFmt = new Intl.DateTimeFormat(LOCALE, { timeZone: tz, hour: "numeric", minute: "2-digit" });
  const dayFmt = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" });
  const dateFmt = new Intl.DateTimeFormat(LOCALE, { timeZone: tz, weekday: "short", month: "short", day: "numeric" });

  const fmtTime = (iso) => timeFmt.format(new Date(iso));
  function fmtWhen(iso) {
    // "3:05 PM" today, "Yesterday 3:05 PM", else "Sat, Sep 26 3:05 PM" (elder's time zone)
    const d = new Date(iso);
    const day = dayFmt.format(d);
    const today = dayFmt.format(new Date());
    const yesterday = dayFmt.format(new Date(Date.now() - 864e5));
    if (day === today) return fmtTime(iso);
    if (day === yesterday) return `${S.yesterday} ${fmtTime(iso)}`;
    return `${dateFmt.format(d)} ${fmtTime(iso)}`;
  }

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children) if (c != null) node.append(c);
    return node;
  }

  async function api(path, options) {
    const r = await fetch(path, options);
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }

  function severityClass(s) {
    if (s.red_flag) return "sev-red";
    return `sev-${s.severity}`;
  }

  // ---------- summary ----------

  let summaryStale = false;

  async function loadSummary(refresh = false) {
    const btn = $("summary-refresh");
    btn.disabled = true;
    btn.textContent = S.updating;
    try {
      const s = await api(`/api/summary/today${refresh ? "?refresh=true" : ""}`);
      $("summary-text").textContent = s.summary;
      $("summary-text").classList.toggle("muted", s.empty);
      $("summary-meta").textContent = s.empty
        ? ""
        : `${window.fmt(S.updated, { time: fmtTime(s.generated_at) })}${s.fallback ? ` · ${S.fallback_note}` : ""}`;
      setSummaryStale(false);
    } catch (e) {
      console.error(e);
      $("summary-meta").textContent = S.summary_failed;
    } finally {
      btn.disabled = false;
      btn.textContent = S.refresh;
    }
  }

  function setSummaryStale(stale) {
    summaryStale = stale;
    $("summary-refresh").classList.toggle("btn-attention", stale);
    const meta = $("summary-meta");
    if (stale && !meta.textContent.includes(S.new_activity)) {
      meta.textContent = [meta.textContent, S.new_activity].filter(Boolean).join(" · ");
    }
  }

  // ---------- alerts ----------

  const alerts = new Map(); // id -> alert
  let bannerAlert = null;

  function alertItem(a) {
    const isFall = a.type === "fall";
    const snapshot = a.snapshot_path
      ? el("a", { class: "snapshot", href: `/media/${a.snapshot_path}`, target: "_blank", rel: "noopener" },
          el("img", { src: `/media/${a.snapshot_path}`, alt: S.snapshot_alt, loading: "lazy" }))
      : null;
    const read = a.is_read
      ? null
      : el("button", { class: "btn btn-quiet btn-small", type: "button", text: S.mark_read, onclick: () => markRead(a.id) });
    return el("li", { class: `alert-item level-${a.level}${a.is_read ? " is-read" : ""}`, "data-id": a.id },
      el("div", { class: "alert-icon", "aria-hidden": "true", text: isFall ? "🧍" : "🩺" }),
      el("div", { class: "alert-main" },
        el("p", { class: "alert-title" },
          el("span", { class: `level-badge level-${a.level}`, text: a.level === "high" ? S.urgent : S.watch }),
          " ", a.title),
        a.content ? el("p", { class: "alert-content", text: a.content }) : null,
        el("p", { class: "meta", text: `${isFall ? S.from_fall : S.from_chat} · ${fmtWhen(a.created_at)}` })),
      snapshot, read);
  }

  function renderAlerts() {
    const list = [...alerts.values()].sort((a, b) => b.id - a.id);
    $("alert-list").replaceChildren(...list.map(alertItem));
    $("alerts-empty").hidden = list.length > 0;
    const unread = list.filter((a) => !a.is_read).length;
    $("unread-count").hidden = unread === 0;
    $("unread-count").textContent = window.fmt(S.unread, { n: unread });
    const badge = $("tab-badge");
    if (badge) {
      badge.hidden = unread === 0;
      badge.textContent = unread > 9 ? "9+" : String(unread);
    }
    document.title = `${unread ? `(${unread}) ` : ""}${cfg.nickname}'s day`;
  }

  async function loadAlerts() {
    try {
      const list = await api(`/api/alerts?limit=30&lang=${encodeURIComponent(cfg.lang || "en")}`);
      alerts.clear();
      for (const a of list) alerts.set(a.id, a);
      renderAlerts();
      // Opening the dashboard after something happened: show the newest unread urgent alert.
      const urgent = list.find((a) => !a.is_read && a.level === "high");
      if (urgent && !bannerAlert) showAlertBanner(urgent, { sound: false });
    } catch (e) {
      console.error(e);
    }
  }

  async function markRead(id) {
    try {
      const a = await api(`/api/alerts/${id}/read`, { method: "POST" });
      alerts.set(a.id, a);
      renderAlerts();
      if (bannerAlert && bannerAlert.id === id) {
        hideBanner();
        // e.g. "chest pain" and "shortness of breath" arrive together: show the next one
        const next = [...alerts.values()].sort((x, y) => y.id - x.id).find((x) => !x.is_read && x.level === "high");
        if (next) showAlertBanner(next, { sound: false });
      }
    } catch (e) {
      console.error(e);
    }
  }

  function showAlertBanner(a, { sound = true } = {}) {
    bannerAlert = a;
    const banner = $("alert-banner");
    banner.dataset.level = a.level;
    $("banner-title").textContent = a.title;
    $("banner-content").textContent = a.content || (a.type === "fall" ? "A fall was detected." : "");
    $("banner-time").textContent = `${a.type === "fall" ? S.from_fall : S.from_chat} · ${fmtWhen(a.created_at)}`;
    banner.hidden = false;
    if (sound) beep(a.level);
  }

  function hideBanner() {
    $("alert-banner").hidden = true;
    bannerAlert = null;
  }

  // ---------- sound (browsers only allow audio after a user gesture) ----------

  let audioCtx = null;
  let soundOn = false;

  function setSound(on) {
    soundOn = on;
    if (on && !audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) audioCtx = new Ctx();
    }
    if (on && audioCtx && audioCtx.state === "suspended") audioCtx.resume();
    const btn = $("sound-btn");
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "🔔 Sound on" : "🔕 Sound off";
  }

  function beep(level) {
    if (!soundOn || !audioCtx) return;
    const tones = level === "high" ? [880, 660, 880, 660] : [660, 520];
    let t = audioCtx.currentTime;
    for (const freq of tones) {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t);
      osc.stop(t + 0.25);
      t += 0.28;
    }
  }

  // ---------- symptom timeline ----------

  function symptomRow(s) {
    // The API carries both names for every canonical (SymptomOut.display_en / display_zh),
    // so the interface language picks one; "other" has no canonical name and keeps the
    // model's own wording, which is already in whatever language she spoke.
    const name = s.canonical === "other" ? s.label : (cfg.lang === "zh" ? s.display_zh : s.display_en);
    const sev = SEV[s.severity] || s.severity;
    const details = [
      s.body_part && window.fmt(S.detail_where, { v: s.body_part }),
      s.duration && window.fmt(S.detail_duration, { v: s.duration }),
      s.onset && window.fmt(S.detail_onset, { v: s.onset }),
      window.fmt(S.detail_severity, { v: sev }),
      window.fmt(S.detail_last_seen, { time: fmtTime(s.last_seen) }),
    ].filter(Boolean);
    return el("li", { class: "symptom" },
      el("details", {},
        el("summary", {},
          el("span", { class: `chip ${severityClass(s)}` },
            name, s.count > 1 ? el("span", { class: "chip-count", text: ` ×${s.count}` }) : null),
          el("span", { class: `sym-status sym-status-${s.status}`, text: STATUS_TEXT[s.status] || s.status })),
        el("div", { class: "symptom-detail" },
          el("blockquote", { class: "quote", lang: /[一-鿿]/.test(s.raw_quote) ? "zh" : "en", text: `“${s.raw_quote}”` }),
          el("p", { class: "meta", text: details.join(" · ") }))));
  }


  async function loadSymptoms() {
    try {
      const data = await api("/api/symptoms?days=7");
      $("timeline").replaceChildren(...timelineRows(data.days));
    } catch (e) {
      console.error(e);
    }
  }

  /** The API labels days in English; on a phone the label is re-derived so it matches the
   *  interface language and the elder's time zone. */
  function dayLabel(iso) {
    const key = (d) => dayFmt.format(d);
    const d = new Date(`${iso}T12:00:00Z`);
    if (key(d) === key(new Date())) return S.today;
    if (key(d) === key(new Date(Date.now() - 864e5))) return S.yesterday;
    return dateFmt.format(d);
  }

  /** Quiet days collapse into one row.
   *
   *  Seven rows each saying "nothing mentioned" filled more than half the card on a phone,
   *  and the answer the reader wants — was anything mentioned — was buried in the middle of
   *  it. Collapsing keeps every day accounted for: the row still names the range it covers,
   *  so nothing silently disappears from the record. */
  function timelineRows(days) {
    const rows = [];
    for (let i = 0; i < days.length; ) {
      const d = days[i];
      if (d.symptoms.length) {
        rows.push(el("li", { class: "day" },
          el("h3", { class: "day-label", text: dayLabel(d.date) }),
          el("ul", { class: "symptom-list" }, ...d.symptoms.map(symptomRow))));
        i += 1;
        continue;
      }
      let j = i;
      while (j < days.length && !days[j].symptoms.length) j += 1;
      const run = days.slice(i, j);
      const text = run.length === 1
        ? window.fmt(S.quiet_one, { day: dayLabel(run[0].date) })
        : window.fmt(S.quiet_days, {
            from: dayLabel(run[run.length - 1].date),
            to: dayLabel(run[0].date),
          });
      rows.push(el("li", { class: "day is-empty" },
        el("p", { class: "quiet-run", text }),
        run.length > 2
          ? el("p", { class: "meta", text: window.fmt(S.days_quiet, { n: run.length }) })
          : null));
      i = j;
    }
    return rows;
  }


  // ---------- conversation history ----------

  let oldestId = null;

  function historyItem(m) {
    const who = m.role === "user" ? cfg.nickname : S.companion_role;
    return el("li", { class: `history-msg ${m.role}` },
      el("p", { class: "meta", text: `${who} · ${fmtWhen(m.created_at)}` }),
      el("p", { class: "history-text", text: m.text }));
  }

  async function loadHistory() {
    try {
      const msgs = await api(`/api/messages?limit=${HISTORY_PAGE}`);
      $("history-list").replaceChildren(...msgs.map(historyItem));
      oldestId = msgs.length ? msgs[0].id : null;
      $("history-older").hidden = msgs.length < HISTORY_PAGE;
      const todayKey = dayFmt.format(new Date());
      const today = msgs.filter((m) => m.role === "user" && dayFmt.format(new Date(m.created_at)) === todayKey).length;
      $("history-meta").textContent = window.fmt(S.messages_today, { n: today, nickname: cfg.nickname });
    } catch (e) {
      console.error(e);
    }
  }

  async function loadOlderHistory() {
    if (oldestId == null) return;
    try {
      const msgs = await api(`/api/messages?limit=${HISTORY_PAGE}&before_id=${oldestId}`);
      $("history-list").prepend(...msgs.map(historyItem));
      if (msgs.length) oldestId = msgs[0].id;
      $("history-older").hidden = msgs.length < HISTORY_PAGE;
    } catch (e) {
      console.error(e);
    }
  }

  // ---------- fall-detection view (fall-mcp MJPEG, may not be running) ----------

  function connectFallStream() {
    const img = $("fall-stream");
    // Same origin, proxied by the app (web/routes/fall_proxy.py): a phone cannot reach
    // fall-mcp's port directly, and over HTTPS an http://host:8001 image is mixed content.
    const url = "/fall/stream";
    img.onload = () => {
      img.hidden = false;
      $("fall-offline").hidden = true;
      $("fall-status").textContent = S.live;
    };
    img.onerror = () => {
      img.hidden = true;
      $("fall-offline").hidden = false;
      $("fall-status").textContent = S.offline;
      setTimeout(() => { img.src = `${url}?t=${Date.now()}`; }, FALL_RETRY_MS);
    };
    img.src = url;
  }

  // ---------- live updates ----------

  let activityTimer = null;

  function connectAlertStream() {
    const live = $("live");
    const setLive = (state, text) => { live.dataset.state = state; $("live-text").textContent = text; };
    // The page language can come from ?lang=, which the cookie does not know about,
    // so it is passed explicitly rather than left to the server to guess.
    const source = new EventSource(`/api/alerts/stream?lang=${encodeURIComponent(cfg.lang || "en")}`);
    let dropped = false;

    source.addEventListener("open", () => {
      setLive("live", S.live);
      if (dropped) { loadAlerts(); loadSymptoms(); loadHistory(); } // catch up on missed events
      dropped = false;
    });
    source.addEventListener("error", () => {
      dropped = true;
      setLive("down", S.reconnecting); // EventSource retries by itself
    });
    source.addEventListener("alert", (ev) => {
      const a = JSON.parse(ev.data);
      alerts.set(a.id, a);
      renderAlerts();
      showAlertBanner(a);
      if (a.type === "symptom") setSummaryStale(true);
    });
    source.addEventListener("activity", () => {
      setSummaryStale(true);
      clearTimeout(activityTimer);
      activityTimer = setTimeout(() => { loadSymptoms(); loadHistory(); }, ACTIVITY_DEBOUNCE_MS);
    });
  }

  // ---------- start ----------

  $("summary-refresh").addEventListener("click", () => loadSummary(true));
  $("banner-read").addEventListener("click", () => bannerAlert && markRead(bannerAlert.id));
  $("banner-close").addEventListener("click", hideBanner);
  $("sound-btn").addEventListener("click", () => setSound(!soundOn));
  $("history-older").addEventListener("click", loadOlderHistory);

  loadSummary();
  loadAlerts();
  loadSymptoms();
  loadHistory();
  connectFallStream();
  connectAlertStream();

  window.__dashboard = { loadSummary, loadSymptoms, loadAlerts, showAlertBanner, get summaryStale() { return summaryStale; } };
})();
