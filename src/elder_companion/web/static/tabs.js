/* Bottom tab bar.
 *
 * Swaps panels in place. Nothing is destroyed, so the alert stream, the camera and the
 * loaded history keep running while another tab is showing; switching back is instant and
 * the MJPEG connection is not torn down and re-established every time.
 *
 * The chosen tab is remembered per browser, so reopening the installed app lands where the
 * reader left off rather than always on Today.
 */
(function () {
  const bar = document.querySelector(".tabbar");
  if (!bar) return;

  const tabs = [...bar.querySelectorAll("[data-panel]")];
  const KEY = "sunny.tab";

  function show(name, remember = true) {
    let matched = false;
    for (const tab of tabs) {
      const on = tab.dataset.panel === name;
      matched = matched || on;
      tab.setAttribute("aria-selected", String(on));
      const panel = document.getElementById(`panel-${tab.dataset.panel}`);
      if (panel) panel.hidden = !on;
    }
    if (!matched) return show("today", remember);
    // Each tab is its own screen: start it at the top, the way a native push does.
    window.scrollTo({ top: 0, behavior: "instant" });
    if (remember) {
      try { localStorage.setItem(KEY, name); } catch { /* private mode */ }
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => show(tab.dataset.panel));
  }

  /** An urgent alert should take the reader to it, not leave a badge on another screen. */
  window.showTab = show;

  let start = "today";
  try { start = localStorage.getItem(KEY) || "today"; } catch { /* private mode */ }
  show(start, false);
})();
