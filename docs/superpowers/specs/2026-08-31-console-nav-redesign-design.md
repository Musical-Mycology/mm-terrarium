# Terrarium Console nav redesign — sidebar nav, no rail, switchable center views

Date: 2026-08-31. Brainstormed with Chris; supersedes the 2026-08-25 layout's
right rail. Front-end only: no wire-protocol, backend, or `console/agent.py`
change. All six ES modules keep their module boundaries; the standing
front-end-wide rule holds throughout: **no high-frequency wire event may
rebuild a DOM subtree whose declaration has not changed** (armed
`wire.confirmTap` buttons die with their element), and the new view switcher
must **hide/show** the three center views, never tear them down.

## Layout

```
topbar (unchanged)
┌ sidebar ──────────────┐ ┌ content (single column now) ──────────┐
│ Loaded Bit            │ │ one of three views, others hidden:     │
│  [icon] Title (1 row) │ │  #viewLive: roomCard + bitStatusCard   │
│  TestBit · v1.0.0     │ │            (functionsMount lives      │
│  [Bit Details] pill   │ │             inside roomCard already)  │
│  Run/Abort/Load,phase │ │  #viewRoom: roomsPanel (full width)   │
│ Registration          │ │  #viewLog:  logCard (full width)      │
│  role rows + meters   │ └────────────────────────────────────────┘
│  ▸ Instruments pull   │
│ nav: Live             │
│      Room: TEST       │
│      Event Log        │
└───────────────────────┘
```

The right rail (`.rail`, `#devicesCard`, `#rolesCard`) is deleted. The
`roomsPanel` strip no longer renders across the top of the default view.

## Decisions (from brainstorm)

1. **Bit identity row**: icon + display-name `h3` on ONE row (font reduced
   from 21px to 17px so it fits); the `name · vX.Y.Z` mono line drops 3px
   (12.5px → 9.5px).
2. **Bit Details pill**: a pill button under the version line opening an
   overlay popup (via `#overlayMount`, same pattern as the Load picker) with
   the Rooms / Roles / About / Notes `dl` (moved out of the always-visible
   panel) **plus** the Roles & Manifests refcards that used to live in the
   rail's `#rolesCard`. Extensible: the popup body is one column, sections
   appended in order.
3. **Registration moves to the sidebar** and gains an **Instruments** pull
   (`<details>`): every connected instrument (the old Devices card rows),
   each tagged **Scored** (holds a scored role), **Jam** (role class `jam`),
   **Fixture** (its dev id is bound as a Room fixture, learned from
   `snapshot.room`/`room_changed`'s `fixtures[].dev`), else the role class
   capitalized (e.g. `Shared`), or **Unregistered** (no role, not a
   fixture). Terminology: the UI says "Instruments", never "Devices".
   Hub-level discovery of o2lite clients that never sent `/game/hello` is
   out of scope (Control is an o2lite client with no service directory);
   noted as a possible upstream ask.
4. **Center views + nav**: three sidebar nav buttons — **Live** (default),
   **Room: [NAME]** (label shows the active room name, or `Room: none`),
   **Event Log**. Clicking toggles `hidden` on `#viewLive`/`#viewRoom`/
   `#viewLog`; nothing is unmounted. `rooms.js` and `rail.js`'s log keep
   rendering into their mounts even while hidden.
5. **Default view** with a room loaded and a Bit running: Live (room
   surface + Bit status). Room selection/load/unload gets the full center
   width in the Room view — progress stages and refusal reasons get space a
   sidebar dropdown could not give them.

## Module changes

- `index.html` — new skeleton per the layout above.
- `terrarium.css` — `.content` single column; `.rail` rules retired or
  repurposed for the sidebar cards; `.viewnav` button styles; identity-row
  and version-line font sizes; `.pill` for Bit Details.
- `shell.js` — owns the view switcher and the `Room: [NAME]` nav label
  (from `snapshot.rooms`/`room_loaded`/`room_unloaded` it already sees via
  `wire.on`). Exports `showView(name)` for tests.
- `bit.js` — identity-row compaction; Bit Details pill + popup; the
  roles-refcard builder moves here from `rail.js` (it already imports
  nothing rail-specific beyond `buildInstrumentCard` from `surface.js`).
- `rail.js` — keeps Registration (now sidebar-mounted), the Instruments
  pull (absorbing the Devices renderer with tags), and the Event log +
  `logLine` export. Devices/roles cards as separate cards are gone.
- `rooms.js` — unchanged logic; its mount just lives inside `#viewRoom`.
- Tests: `tests/js/*.test.js` updated to the new mounts; `full_stack.test.js`
  exercises the view switcher; `tests/test_console_js.py` runs them all
  under pytest unchanged.
