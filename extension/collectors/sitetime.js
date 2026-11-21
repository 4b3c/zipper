/* Time on this site, while it is the tab in front of them.
 *
 * Counts only while the tab is visible and the window has focus; the
 * background adds the browser's idle check. It sends the hostname and a
 * number of seconds -- never the URL, the title or anything on the page.
 * Which sites are watched is the manifest's `matches` list for this file.
 */
(() => {
  const api = globalThis.browser ?? globalThis.chrome;
  const STEP = 15;
  let last = Date.now();

  function tick() {
    const now = Date.now();
    // Real elapsed time, capped: a laptop waking from sleep fires a late
    // timer, and those minutes were not spent here.
    const secs = Math.min(STEP * 2, Math.round((now - last) / 1000));
    last = now;
    if (document.visibilityState !== 'visible' || !document.hasFocus()) return;
    if (secs <= 0) return;
    try {
      api.runtime.sendMessage({ type: 'zipper:tick', domain: location.hostname, secs })
        .catch(() => {});
    } catch { /* extension reloaded under the page; the next load fixes it */ }
  }

  setInterval(tick, STEP * 1000);
  // Coming back to the tab restarts the clock, so the time it was hidden or
  // unfocused is not claimed by the first tick after.
  document.addEventListener('visibilitychange', () => { last = Date.now(); });
  window.addEventListener('focus', () => { last = Date.now(); });
})();
