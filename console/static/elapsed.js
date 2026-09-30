// Elapsed-per-stage display for blocking Terrarium operations (a Room load
// blocks the server tick for seconds and goes silent between stages). The
// timing lives here, pure and injectable, so busy.js and rooms.js share it:
// the count is ticked client-side and keeps moving while the server says
// nothing.

// "spawning arco" at 0 s, "spawning arco · 7 s" after.
export function formatStage(text, seconds) {
  return seconds >= 1 ? `${text} · ${Math.floor(seconds)} s` : text;
}

// start(text) begins (or restarts) timing a stage and renders it; stop()
// clears the interval. render(str) is the caller's paint. now/setInterval/
// clearInterval are injectable for tests.
export function createStageTimer({
  render,
  now = () => Date.now(),
  setInterval: setIv = (fn, ms) => globalThis.setInterval(fn, ms),
  clearInterval: clearIv = (h) => globalThis.clearInterval(h),
  tickMs = 1000,
}) {
  let handle = null;
  let text = null;
  let t0 = 0;

  const paint = () => render(formatStage(text, (now() - t0) / 1000));

  function stop() {
    if (handle !== null) clearIv(handle);
    handle = null;
    text = null;
  }

  function start(stageText) {
    stop();
    text = stageText;
    t0 = now();
    paint();
    handle = setIv(paint, tickMs);
    // Node (tests): never keep the process alive for a display ticker.
    if (handle && typeof handle.unref === "function") handle.unref();
  }

  return { start, stop, active: () => handle !== null };
}
