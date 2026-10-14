// The one pixel wire (contract v5): R, G, B, W per pixel. A screen has no
// white die, so W folds into r, g and b, clipped at 255, exactly as an
// RGB-only device shows it (spec 2026-10-14 rgbw-device-wire section 3.2).
// A trailing partial pixel is dropped.
export function decodeRgbw(channels) {
  const n = Math.floor(channels.length / 4);
  const out = [];
  for (let i = 0; i < n; i++) {
    const w = channels[i * 4 + 3] || 0;
    out.push([
      Math.min(255, (channels[i * 4] || 0) + w),
      Math.min(255, (channels[i * 4 + 1] || 0) + w),
      Math.min(255, (channels[i * 4 + 2] || 0) + w),
    ]);
  }
  return out;
}
