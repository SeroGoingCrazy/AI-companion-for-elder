/* The language switch. One button, one cookie, one reload.
 *
 * Server-rendered text is already in the right language; reloading is what applies the
 * choice to it. The cookie is what the server reads on the next request, and it is set
 * here rather than by an endpoint so the switch costs no round trip of its own. */
(function () {
  const btn = document.getElementById("lang-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const next = btn.dataset.next;
    // A year, site-wide, so the elder never has to choose twice.
    document.cookie = `lang=${encodeURIComponent(next)}; path=/; max-age=31536000; samesite=lax`;
    const url = new URL(window.location.href);
    url.searchParams.delete("lang");  // the cookie is the record now
    window.location.replace(url.toString());
  });
})();

/* Fill {placeholders} in a string from the table. Kept deliberately dumb: the tables are
 * ours, not user input, and anything unmatched is left visible so a missing value shows
 * up in testing instead of rendering as an empty gap. */
window.fmt = function (template, values) {
  return String(template || "").replace(/\{(\w+)\}/g, (whole, key) =>
    values && key in values ? values[key] : whole,
  );
};
