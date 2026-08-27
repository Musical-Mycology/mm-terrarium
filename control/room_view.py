"""The Room read model the Terrarium Console renders.

Pure dict builders with no engine imports, mirroring console/protocol.py so
this is testable with no GameServer, no renderer and no socket.

Scope is deliberate and load-bearing. The 2026-08-10 room-concept spec's
section 7 said the Room is never surfaced; the 2026-08-17 room-panel spec
narrows that rather than deleting it. What this module may expose is the
Room's instruments, its surface and its live controller values. What it must
never expose is the Room's Registration Node id, its registration counts, or
its role name -- those stay behind the untouched filters in console/agent.py
and control/rooms.py's non_room_counts(). Two tests in tests/test_room_view.py
assert the node id and role name are absent from the serialized blob.
"""

from __future__ import annotations


def _light_instruments(manifest: dict) -> list[dict]:
    out = []
    for decl in manifest.get("instruments", []):
        entry = {"kind": "light",
                 "instrument": decl.get("instrument"),
                 "target": decl.get("target", "primary"),
                 "params": decl.get("params", {}),
                 "lanes": decl.get("lanes", [])}
        out.append(entry)
    return out


def _audio_instruments(manifest: dict) -> list[dict]:
    """Audio declarations carry keys light ones do not (program, drone), and
    ugen_manifest is v0 and provisional, so anything beyond the shared shape
    is copied through rather than enumerated. A Bit that grows a new audio key
    shows it on the panel without this module changing."""
    out = []
    for decl in manifest.get("instruments", []):
        entry = {"kind": "audio",
                 "instrument": decl.get("instrument"),
                 "lanes": decl.get("lanes", [])}
        for key, value in decl.items():
            if key not in ("instrument", "lanes"):
                entry[key] = value
        out.append(entry)
    return out


def capability_view(profile) -> dict:
    """The Room's surface, as the Console draws it.

    `primary` is absent by construction: control/room_profile.py does not
    declare it and harness/room_surface.py appends it only for the renderer.
    It spans the whole surface, so drawing it would cover every real zone.
    """
    return {
        "surface_id": profile.surface_id,
        "pixel_count": profile.pixel_count,
        "color_order": profile.color_order,
        "zones": [{"name": z.name, "start": z.start, "count": z.count}
                  for z in profile.zones],
    }


def _instrument_view(instrument) -> dict:
    return {
        "name": instrument.name,
        "capabilities": sorted(instrument.capabilities),
        "functions": list(instrument.functions),
        "accepted_cues": list(instrument.accepted_cues),
    }


def fixtures_view(profile, room, canvas_urls=None) -> list[dict]:
    """One entry per fixture: its own pixel count, its zones (already
    namespaced <fixture>.<zone> by RoomProfile.zones), its channel offset
    into the concatenated frame, and which dev is bound (None if not yet).
    The dev id is shown, matching the precedent this module already set for
    the whole Room (the old single bound_dev field): it is not the
    Registration Node id, the role name, or a registration count, so it is
    not covered by the hiding rule in this module's docstring.

    `canvas_urls` is a plain dev -> url dict (DeviceLinkAgent.canvas_urls()'s
    shape, passed in rather than imported so this module stays engine-free).
    A fixture with no bound dev, or a bound dev with no reported canvas yet,
    gets `"url": None`.
    """
    urls = canvas_urls or {}
    out = []
    for name, start, count in profile.fixture_slices():
        fixture = next(f for f in profile.fixtures if f.name == name)
        dev = room.bound.get(name)
        out.append({
            "name": name,
            "pixel_count": fixture.pixel_count,
            "channel_start": start,
            "channel_count": count,
            "zones": [{"name": f"{name}.{z.name}", "start": z.start, "count": z.count}
                      for z in fixture.zones],
            "dev": dev,
            "url": urls.get(dev) if dev else None,
            "instrument": _instrument_view(fixture.instrument),
        })
    return out


def room_view(room, profile, role, controllers: dict, canvas_urls=None) -> dict | None:
    """Build the Console's whole Room panel payload.

    Returns None when no Room is configured, which the panel renders as
    "No Room configured". `role` is None when no Bit is loaded, which yields
    the surface with an empty instrument list.

    Light and audio instruments are returned as ONE list discriminated by
    `kind`, not two -- see the module docstring. `fixtures` replaced the
    single `bound_dev` field once the Room became N fixtures: an old browser
    tab reading `room.bound_dev` degrades gracefully to `undefined` rather
    than breaking, and the privacy filters this module's docstring describes
    (node id, role name, registration counts) are unaffected either way.
    """
    if room is None or profile is None:
        return None
    instruments: list[dict] = []
    if role is not None:
        instruments = (_light_instruments(role.light_manifest or {})
                       + _audio_instruments(role.ugen_manifest or {}))
    # Each manifest entry names an instrument *declaration* (e.g. "rainbow"),
    # not a physical Instrument object -- the room-level Instrument that
    # backs the fixture(s) it plays on isn't threaded through the manifest.
    # Rather than guess per-entry, every entry gets the same
    # `instrument_name`: the room's first fixture's Instrument name. This is
    # a deliberate ambiguity-breaking simplification (a multi-fixture Room
    # with per-fixture Instruments would need real per-entry attribution;
    # nothing in this codebase needs that yet).
    instrument_name = (profile.fixtures[0].instrument.name
                        if profile.fixtures else None)
    for entry in instruments:
        entry["instrument_name"] = instrument_name
    return {
        "room_type": room.name,
        "fixtures": fixtures_view(profile, room, canvas_urls),
        "capability": capability_view(profile),
        "instruments": instruments,
        "controllers": dict(controllers),
    }
