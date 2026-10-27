// The LED inspector's numbers. It shows the four wire channels as sent, not
// the W-folded screen colour that decodeRgbw gives. Pure: no DOM, no wire.

// Where an LED sits: its fixture, index, Room zones and model zone.
export function ledAddress(layout, index) {
  const p = (layout.pixels || []).find((q) => q.index === index);
  return {
    fixture: layout.fixture,
    index,
    roomZones: p ? [...(p.room_zones || [])] : [],
    modelZone: p ? (p.model_zone ?? null) : null,
  };
}

export function rgbwAt(channels, index) {
  return {
    r: channels[index * 4] || 0,
    g: channels[index * 4 + 1] || 0,
    b: channels[index * 4 + 2] || 0,
    w: channels[index * 4 + 3] || 0,
  };
}

export function gestureLabel(g) {
  const base = `${g.verb} ${g.dev}`;
  return g.refused ? `${base} (refused: ${g.refused})` : base;
}
