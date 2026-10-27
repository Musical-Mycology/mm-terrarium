// A sliding window of one fixture's LED values and gestures, on Control's
// clock. The server sends only changes, so each value holds until the next
// frame (a step trace). Pure: no DOM, no wire.
export class FixtureTrace {
  constructor(pixelCount, windowS = 10) {
    this.pixelCount = pixelCount;
    this.windowS = windowS;
    this.frames = []; // [{t, channels}] oldest first
    this._gestures = [];
  }

  pushFrame(t, channels) {
    this.frames.push({ t, channels: Array.from(channels) });
    // Keep the newest frame at or before the window start, so the first
    // sample can be carried in; drop everything older than that.
    const start = t - this.windowS;
    while (this.frames.length > 1 && this.frames[1].t <= start) this.frames.shift();
  }

  pushGesture({ t, at, dev, verb, refused }) {
    this._gestures.push({ t, at, dev, verb, refused: refused ?? null });
    const start = t - this.windowS;
    this._gestures = this._gestures.filter((g) => g.t >= start);
  }

  // Step-held samples for one pixel inside [now - windowS, now], oldest first.
  // The value in force at the window start is carried in at the start time.
  samples(index, now) {
    if (!(index >= 0 && index < this.pixelCount)) return [];
    const start = now - this.windowS;
    const out = [];
    let carried = null;
    for (const f of this.frames) {
      if (f.t > now) break;
      const s = {
        t: f.t,
        r: f.channels[index * 4] || 0,
        g: f.channels[index * 4 + 1] || 0,
        b: f.channels[index * 4 + 2] || 0,
        w: f.channels[index * 4 + 3] || 0,
      };
      if (f.t <= start) carried = { ...s, t: start };
      else out.push(s);
    }
    if (carried) out.unshift(carried);
    return out;
  }

  gestures(now) {
    const start = now - this.windowS;
    return this._gestures.filter((g) => g.t >= start && g.t <= now);
  }
}
