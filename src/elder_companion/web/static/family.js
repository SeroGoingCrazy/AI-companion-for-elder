/* Family dashboard: today's summary, alerts (live over SSE), symptom timeline, fall view,
 * conversation history, care list, and sibling sharing (who is handling what).
 *
 * GET /api/alerts/stream sends `alert` (new alert: banner + sound) and `activity` (the elder
 * said something: reload the timeline and history). All text is inserted with textContent.
 */
(() => {
  "use strict";

  const cfg = window.APP_CONFIG || {};
  const $ = (id) => document.getElementById(id);
  const tz = cfg.timezone || undefined;

  const STATUS_TEXT = { new: "new", ongoing: "ongoing", improved: "improving", resolved: "resolved" };
  const HISTORY_PAGE = 30;
  const ACTIVITY_DEBOUNCE_MS = 800;
  const FALL_RETRY_MS = 15000;
  const FALL_STATUS_MS = 2000; // the demo playlist changes clip on its own
  const MEMBER_KEY = "family.memberId";

  // ---------- helpers ----------

  const LOCALE = "en-US"; // the page is English; don't mix in the browser's date language

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
    if (day === yesterday) return `Yesterday ${fmtTime(iso)}`;
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
    btn.textContent = "Updating…";
    try {
      const s = await api(`/api/summary/today${refresh ? "?refresh=true" : ""}`);
      $("summary-text").textContent = s.summary;
      $("summary-text").classList.toggle("muted", s.empty);
      $("summary-meta").textContent = s.empty
        ? ""
        : `Updated ${fmtTime(s.generated_at)}${s.fallback ? " · AI summary unavailable, showing a plain list" : ""}`;
      setSummaryStale(false);
    } catch (e) {
      console.error(e);
      $("summary-meta").textContent = "Couldn't load the summary. Try Refresh.";
    } finally {
      btn.disabled = false;
      btn.textContent = "Refresh";
    }
  }

  function setSummaryStale(stale) {
    summaryStale = stale;
    $("summary-refresh").classList.toggle("btn-attention", stale);
    const meta = $("summary-meta");
    if (stale && !meta.textContent.includes("New activity")) {
      meta.textContent = [meta.textContent, "New activity since this summary"].filter(Boolean).join(" · ");
    }
  }

  // ---------- alerts ----------

  const alerts = new Map(); // id -> alert
  let bannerAlert = null;

  function alertItem(a) {
    const isFall = a.type === "fall";
    const snapshot = a.snapshot_path
      ? el("a", { class: "snapshot", href: `/media/${a.snapshot_path}`, target: "_blank", rel: "noopener" },
          el("img", { src: `/media/${a.snapshot_path}`, alt: "Snapshot at the time of the fall", loading: "lazy" }))
      : null;
    const read = a.is_read
      ? null
      : el("button", { class: "btn btn-quiet btn-small", type: "button", text: "Mark read", onclick: () => markRead(a.id) });
    return el("li", { class: `alert-item level-${a.level}${a.is_read ? " is-read" : ""}`, "data-id": a.id },
      el("div", { class: "alert-icon", "aria-hidden": "true", text: isFall ? "🧍" : "🩺" }),
      el("div", { class: "alert-main" },
        el("p", { class: "alert-title" },
          el("span", { class: `level-badge level-${a.level}`, text: a.level === "high" ? "Urgent" : "Watch" }),
          " ", a.title),
        a.content ? el("p", { class: "alert-content", text: a.content }) : null,
        el("p", { class: "meta", text: `${isFall ? "Fall detection" : "From chat"} · ${fmtWhen(a.created_at)}` })),
      snapshot, read, claimWidget("alert", a.id));
  }

  function renderAlerts() {
    const list = [...alerts.values()].sort((a, b) => b.id - a.id);
    $("alert-list").replaceChildren(...list.map(alertItem));
    $("alerts-empty").hidden = list.length > 0;
    const unread = list.filter((a) => !a.is_read).length;
    $("unread-count").hidden = unread === 0;
    $("unread-count").textContent = `${unread} new`;
    document.title = `${unread ? `(${unread}) ` : ""}${cfg.nickname}'s day`;
  }

  async function loadAlerts() {
    try {
      const list = await api("/api/alerts?limit=30");
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
    $("banner-time").textContent = `${a.type === "fall" ? "Fall detection" : "From chat"} · ${fmtWhen(a.created_at)}`;
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
    const details = [
      s.body_part && `Where: ${s.body_part}`,
      s.duration && `How long: ${s.duration}`,
      s.onset && `When: ${s.onset}`,
      `Severity: ${s.severity === "unknown" ? "not stated" : s.severity}`,
      `Last mentioned ${fmtTime(s.last_seen)}`,
    ].filter(Boolean);
    const name = s.canonical === "other" ? s.label : s.display_en;
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
      const days = data.days.map((d) =>
        el("li", { class: `day${d.symptoms.length ? "" : " is-empty"}` },
          el("h3", { class: "day-label", text: d.label }),
          d.symptoms.length
            ? el("ul", { class: "symptom-list" }, ...d.symptoms.map(symptomRow))
            : el("p", { class: "empty", text: "Nothing mentioned" })));
      $("timeline").replaceChildren(...days);
    } catch (e) {
      console.error(e);
    }
  }

  // ---------- conversation history ----------

  let oldestId = null;

  function historyItem(m) {
    const who = m.role === "user" ? cfg.nickname : "Companion";
    return el("li", { class: `history-msg ${m.role}${m.private ? " is-private" : ""}` },
      el("p", { class: "meta", text: `${who} · ${fmtWhen(m.created_at)}` }),
      m.private
        ? el("p", {
          class: "history-text private-note",
          text: m.awaiting_consent
            ? `Not shared: ${cfg.nickname} hasn't said yet whether to share this`
            : `Kept private at ${cfg.nickname}'s request`,
        })
        : el("p", { class: "history-text", text: m.text }));
  }

  async function loadHistory() {
    try {
      const msgs = await api(`/api/messages?limit=${HISTORY_PAGE}`);
      $("history-list").replaceChildren(...msgs.map(historyItem));
      oldestId = msgs.length ? msgs[0].id : null;
      $("history-older").hidden = msgs.length < HISTORY_PAGE;
      const todayKey = dayFmt.format(new Date());
      const today = msgs.filter((m) => m.role === "user" && dayFmt.format(new Date(m.created_at)) === todayKey).length;
      $("history-meta").textContent = `${today} message${today === 1 ? "" : "s"} from ${cfg.nickname} today`;
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
    const url = `${location.protocol}//${location.hostname}:${cfg.fallStreamPort}/stream`;
    img.onload = () => {
      img.hidden = false;
      $("fall-offline").hidden = true;
      $("fall-status").textContent = "Live";
      refreshFallStatus();
    };
    img.onerror = () => {
      img.hidden = true;
      $("fall-offline").hidden = false;
      $("fall-status").textContent = "Offline";
      setTimeout(() => { img.src = `${url}?t=${Date.now()}`; }, FALL_RETRY_MS);
    };
    img.src = url;
  }

  // Camera / demo video / pause: switches fall-mcp's source through /api/fall (same origin)
  const fallButtons = [...document.querySelectorAll("[data-fall-mode]")];

  function showFallStatus(s) {
    const mode = !s || !s.running ? "stop" : s.live ? "camera" : "demo";
    fallButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.fallMode === mode)));
    if (!$("fall-stream").hidden) {
      $("fall-status").textContent = mode === "camera" ? "Live · camera"
        : mode === "demo" ? `Demo${s.clip ? ` · ${s.clip}` : ""}` : "Paused";
    }
  }

  function showFallError(msg) {
    $("fall-error").textContent = msg || "";
    $("fall-error").hidden = !msg;
  }

  async function refreshFallStatus() {
    try {
      const r = await fetch("/api/fall/status");
      showFallStatus(r.ok ? await r.json() : null);
    } catch (e) {
      showFallStatus(null);
    }
  }

  async function setFallSource(mode) {
    fallButtons.forEach((b) => { b.disabled = true; });
    showFallError("");
    try {
      const r = await fetch("/api/fall/source", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) showFallError(body.error || "Couldn't switch the source.");
      else showFallStatus(body);
    } catch (e) {
      showFallError("Fall detection is offline.");
    } finally {
      fallButtons.forEach((b) => { b.disabled = false; });
    }
  }

  fallButtons.forEach((b) => b.addEventListener("click", () => setFallSource(b.dataset.fallMode)));

  // ---------- family members (siblings share this page; ?member= picks who is acting) ----------

  const members = cfg.members || [];
  let memberId = null;

  function readStoredMember() {
    try { return Number(localStorage.getItem(MEMBER_KEY)) || null; } catch { return null; }
  }

  function setMember(id, { remember = true } = {}) {
    memberId = members.some((m) => m.id === id) ? id : (members[0] && members[0].id);
    $("member-select").value = String(memberId);
    if (remember) {
      try { localStorage.setItem(MEMBER_KEY, String(memberId)); } catch { /* private mode */ }
      const url = new URL(location.href);
      url.searchParams.set("member", String(memberId));
      history.replaceState(null, "", url);
    }
    renderAlerts();
    renderCareList();
  }

  function initMembers() {
    const select = $("member-select");
    select.replaceChildren(...members.map((m) => el("option", { value: m.id, text: `${m.name} (${m.relation})` })));
    select.closest(".member-picker").hidden = members.length === 0;
    select.addEventListener("change", () => setMember(Number(select.value)));
    setMember(cfg.memberId || readStoredMember(), { remember: Boolean(cfg.memberId) });
  }

  const memberName = (id) => (members.find((m) => m.id === id) || {}).name || "Someone";

  // ---------- claims ("Ben: I'll call her doctor") ----------

  const claims = new Map(); // "alert:12" -> newest claim for that target

  async function loadClaims() {
    try {
      const list = await api("/api/claims");
      claims.clear();
      for (const c of list) {
        const key = `${c.target_type}:${c.target_id}`;
        if (!claims.has(key)) claims.set(key, c); // newest first
      }
      renderAlerts();
      renderCareList();
    } catch (e) {
      console.error(e);
    }
  }

  async function saveClaim(targetType, targetId, note) {
    try {
      const c = await api("/api/claims", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_type: targetType, target_id: targetId, member_id: memberId, note }),
      });
      claims.set(`${targetType}:${targetId}`, c);
    } catch (e) {
      console.error(e);
    }
    renderAlerts();
    renderCareList();
  }

  async function finishClaim(c) {
    try {
      claims.set(`${c.target_type}:${c.target_id}`, await api(`/api/claims/${c.id}/done`, { method: "POST" }));
    } catch (e) {
      console.error(e);
    }
    renderAlerts();
    renderCareList();
  }

  function claimForm(targetType, targetId, box) {
    const input = el("input", { type: "text", class: "claim-input", maxlength: "500",
      placeholder: "e.g. I'll call her doctor", "aria-label": "What you'll do (optional)" });
    const form = el("form", { class: "claim-form" }, input,
      el("button", { type: "submit", class: "btn btn-small btn-attention", text: "Save" }),
      el("button", { type: "button", class: "btn btn-small", text: "Cancel",
        onclick: () => box.replaceWith(claimWidget(targetType, targetId)) }));
    form.addEventListener("submit", (ev) => { ev.preventDefault(); saveClaim(targetType, targetId, input.value.trim()); });
    box.replaceChildren(form);
    input.focus();
  }

  function claimWidget(targetType, targetId) {
    const c = claims.get(`${targetType}:${targetId}`);
    const box = el("div", { class: "claim" });
    if (c && c.done_at) {
      box.append(el("span", { class: "claim-badge is-done", text: `✓ Handled by ${c.member_name}` }));
    } else if (c) {
      const who = c.member_id === memberId ? "You are" : `${c.member_name} is`;
      box.append(
        el("span", { class: "claim-badge", text: `${who} handling this${c.note ? `: “${c.note}”` : ""}` }),
        el("button", { type: "button", class: "btn btn-small", text: "Mark done", onclick: () => finishClaim(c) }));
    } else if (memberId != null) {
      box.append(el("button", { type: "button", class: "btn btn-small", text: "I'll handle this",
        onclick: () => claimForm(targetType, targetId, box) }));
    }
    return box;
  }

  // ---------- care list ----------

  let careItems = [];

  function renderCareList() {
    const list = $("care-list");
    if (!list) return;
    list.replaceChildren(...careItems.map((i) =>
      el("li", { class: "care-item" },
        el("p", { class: "care-title" },
          el("span", { class: `care-kind care-${i.kind}`, text: i.kind === "person" ? "Person" : "Topic" }),
          " ", i.subject,
          el("span", { class: "meta", text: ` · ${i.mention_count} mentions, last ${fmtWhen(i.last_seen)}` })),
        i.raw_quote ? el("blockquote", { class: "quote", text: i.raw_quote }) : null,
        claimWidget("memory_item", i.id))));
    $("care-empty").hidden = careItems.length > 0;
  }

  async function loadCareList() {
    try {
      careItems = await api("/api/care-list");
      renderCareList();
    } catch (e) {
      console.error(e);
    }
  }

  // ---------- reminders (set by the family, raised in her next chat) ----------

  const DOSE_MARK = { confirmed: "\u2713", declined: "\u2013", no_response: "?" };
  const DOSE_LABEL = {
    confirmed: "said she did it",
    declined: "said not yet",
    no_response: "never answered",
  };
  const dayFmtShort = new Intl.DateTimeFormat(LOCALE, { timeZone: tz, weekday: "short" });

  function whenLabel(r) {
    if (r.schedule_time) return `Every day at ${r.schedule_time}`;
    return `Once on ${dateFmt.format(new Date(`${r.schedule_date}T12:00:00`))}`;
  }

  function doseDot(d) {
    const status = d.status || "none";
    // Date-only string: parse at midday so the label can't slip a day in another zone.
    const day = dayFmtShort.format(new Date(`${d.date}T12:00:00`));
    const what = d.status ? DOSE_LABEL[d.status] : "not asked";
    return el("li", {
      class: `dose dose-${status}`,
      title: `${day}: ${what}`,
      text: DOSE_MARK[d.status] || "\u00b7",
    }, el("span", { class: "visually-hidden", text: `${day}: ${what}` }));
  }

  function reminderItem(r) {
    const off = !r.active;
    return el("li", { class: `reminder-item${off ? " is-off" : ""}`, "data-id": r.id },
      el("div", {},
        el("p", { class: "reminder-text", text: r.text }),
        el("p", { class: "reminder-when-label",
          text: `${whenLabel(r)}${r.from_member_name ? ` · set by ${r.from_member_name}` : ""}${off ? " · off" : ""}` })),
      off ? null : el("button", {
        class: "btn btn-quiet btn-small", type: "button",
        "aria-label": `Turn off the reminder: ${r.text}`,
        text: "Turn off", onclick: () => turnOffReminder(r.id),
      }),
      el("ul", { class: "adherence", "aria-label": "Last 7 days" }, ...r.history.map(doseDot)),
      el("span", { class: "adherence-summary",
        text: r.raised_days ? `${r.confirmed_days}/${r.raised_days} days` : "not asked yet" }));
  }

  function renderReminders(list) {
    const ul = $("reminder-list");
    if (!ul) return;
    ul.replaceChildren(...list.map(reminderItem));
    $("reminders-empty").hidden = list.length > 0;
  }

  async function loadReminders() {
    try {
      renderReminders(await api("/api/reminders"));
    } catch (e) {
      console.error(e);
    }
  }

  async function turnOffReminder(id) {
    try {
      await api(`/api/reminders/${id}`, { method: "DELETE" });
      await loadReminders();
    } catch (e) {
      console.error(e);
    }
  }

  function initReminderForm() {
    const form = $("reminder-form");
    if (!form) return;
    const time = $("reminder-time");
    const dateInput = $("reminder-date");
    const error = $("reminder-error");
    const kind = () => form.querySelector('input[name="reminder-kind"]:checked').value;

    const syncKind = () => {
      const daily = kind() === "daily";
      time.disabled = !daily;
      dateInput.disabled = daily;
      if (!daily && !dateInput.value) dateInput.value = dayFmt.format(new Date());
    };
    for (const radio of form.querySelectorAll('input[name="reminder-kind"]')) {
      radio.addEventListener("change", syncKind);
    }
    syncKind();

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const text = $("reminder-text").value.trim();
      if (!text) return;
      const body = { text };
      if (kind() === "daily") body.schedule_time = time.value;
      else body.schedule_date = dateInput.value;
      if (memberId != null) body.from_member_id = memberId;
      error.hidden = true;
      const submit = form.querySelector('button[type="submit"]');
      submit.disabled = true;
      try {
        await api("/api/reminders", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        $("reminder-text").value = "";
        await loadReminders();
      } catch (e) {
        console.error(e);
        error.textContent = "Couldn't save that reminder. Please check the time and try again.";
        error.hidden = false;
      } finally {
        submit.disabled = false;
      }
    });
  }

  // ---------- live updates ----------

  let activityTimer = null;

  function connectAlertStream() {
    const live = $("live");
    const setLive = (state, text) => { live.dataset.state = state; $("live-text").textContent = text; };
    const source = new EventSource("/api/alerts/stream");
    let dropped = false;

    source.addEventListener("open", () => {
      setLive("live", "Live");
      if (dropped) { loadAlerts(); loadSymptoms(); loadHistory(); loadCareList(); loadClaims(); loadReminders(); } // catch up
      dropped = false;
    });
    source.addEventListener("error", () => {
      dropped = true;
      setLive("down", "Reconnecting…"); // EventSource retries by itself
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
      // After each turn: new symptoms / memory, and a privacy request may hide earlier items.
      activityTimer = setTimeout(() => {
        loadSymptoms(); loadHistory(); loadCareList(); loadAlerts(); loadReminders();
      }, ACTIVITY_DEBOUNCE_MS);
    });
  }

  // ---------- start ----------

  $("summary-refresh").addEventListener("click", () => loadSummary(true));
  $("banner-read").addEventListener("click", () => bannerAlert && markRead(bannerAlert.id));
  $("banner-close").addEventListener("click", hideBanner);
  $("sound-btn").addEventListener("click", () => setSound(!soundOn));
  $("history-older").addEventListener("click", loadOlderHistory);

  initMembers();
  initReminderForm();
  loadSummary();
  loadAlerts();
  loadClaims();
  loadCareList();
  loadReminders();
  loadSymptoms();
  loadHistory();
  connectFallStream();
  refreshFallStatus();
  setInterval(() => {
    if (!document.hidden && !$("fall-stream").hidden) refreshFallStatus();
  }, FALL_STATUS_MS);
  connectAlertStream();

  window.__dashboard = { loadSummary, loadSymptoms, loadAlerts, loadReminders, showAlertBanner, get summaryStale() { return summaryStale; } };
})();
