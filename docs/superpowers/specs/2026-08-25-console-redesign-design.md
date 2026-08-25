# Terrarium Console redesign — design

**Date:** 2026-08-25
**Status:** Approved design; mockup review pending before implementation.
**Inputs:** `docs/console-frontend-audit.md` (what the console does today and
what is wrong with it), `docs/mm-design-system-handoff.md` (the brand tokens),
and the Stitch look-and-feel export at
`/Users/chris/Documents/Musical Mycology/Terrarium Design/stitch_frontend_ui_redesign`
(`DESIGN.md` + `screen.png` + `code.html`).
**Depends on:** Console operator rounds
(`2026-08-21-console-operator-rounds-design.md`, implemented) — serve mode and
the lazy full registry are what make a Load picker meaningful.

## 1. What this is

A ground-up rebuild of `console/static/` — the Terrarium Console's whole
front end — into the Stitch visual language, mapping **every existing
feature** onto it. The Stitch export is a look-and-feel reference only: its
panels (environment sensors, sequencer, schedulers, sessions, power) do not
exist in this system and are not built. Every region of the new layout maps
to data already on the console wire, plus the two command families that were
implemented server-side but never given UI (`arm_room`/`release_room`,
`load_bit` overrides).

The server side is untouched except for one additive change (section 8).

## 2. Decisions (from brainstorming, in order made)

1. **Single screen, no views.** One dashboard in a three-region shell
   (sidebar / main / right rail). No state hides behind a tab mid-show.
2. **Scope = existing UI features + the no-UI wire commands.** Room binding
   (`arm_room`/`release_room`) and Load-time overrides get UI. Wire-protocol
   changes are out of scope with one sanctioned exception (section 8).
3. **Desktop + tablet.** Three columns ≥1200px; defined collapse for
   portrait tablet; phone works but is not a design target.
4. **No build step.** Hand-written CSS implementing the Stitch `DESIGN.md`
   tokens, vanilla ES modules, self-hosted fonts. The allowlisted
   single-port server model is unchanged (plus `.woff2` in the allowlist).
5. **Sidebar is the Loaded-Bit card, not an inventory.** The full Bit list
   appears only in a Load picker overlay.
6. **Phase is the source of truth; buttons are overrides.** Bits may
   auto-advance (start conditions, self-completion). Run/Abort/Load are
   never hidden or disabled by state — they attempt the transition and an
   engine refusal surfaces inline. This matches the rounds slice, where
   holds "still yield to the operator pressing Run."
7. **Deliverables: this spec + a static HTML mockup** (real panels,
   realistic fake data, both breakpoints) reviewed before implementation.

## 3. Design language (adopted from the Stitch DESIGN.md)

The Stitch `DESIGN.md` is adopted as written; this section records only the
bindings and deviations.

- **Tokens.** Surfaces `#220e05`/`#2b160c`/`#301a10`/`#3c2419`/`#482f23`
  (tonal layering, darkest at the back), ink `#ffdbcc`, primary gold
  `#f2c35b`/`#d4a843`, plus the MM brand set (`mm-*`). Status palette:
  **sage `#7a9e6e`** = running/connected/ok, **rose `#d96680`** =
  error/abort/disconnect, **terracotta `#c07850`** = warning/admin-manual/
  unbound. No neutral greys anywhere.
- **Type.** Londrina Solid for headings, button labels, eyebrows (Title
  Case or uppercase; never body copy). Atkinson Hyperlegible for UI copy.
  **JetBrains Mono** for cue scripts, the log, dev ids, controller values,
  timestamps. All three self-hosted as woff2 in `console/static/fonts/`
  (no CDN; venues may be offline).
- **Shape.** 18px radius on cards ("organic"), pills for buttons and status
  chips, 4px on subtitle-bar-style labels, 8px on inputs.
- **Depth.** Tonal layering, hairline borders (gold or tan-edge at low
  opacity), warm-tinted shadows only on overlays. No inner shadows.
- **Icons.** Lucide, 2px stroke, inlined as SVG (no icon font, no CDN).
  This resolves the design system's flagged icon substitution.
- **Motion.** 0.2s `cubic-bezier(0.4,0,0.2,1)` on state-carrying changes;
  none on the LED canvas; `prefers-reduced-motion` respected. The brand's
  slow decorative motion does not appear below the top bar.
- **Deviation from the mock:** no settings/bell/avatar icons (nothing
  behind them), no "New Session"/"Power Off" (no such concepts), zone dots
  replaced by the real full-width LED strip.

## 4. Layout

```
+------------------------------------------------------------------+
| TOP BAR   Terrarium Console ✦   [CONNECTED]   [DEMO · 1/1 bound] |
+----------------+--------------------------------+----------------+
| SIDEBAR        | MAIN                           | RIGHT RAIL     |
|                |                                |                |
| Loaded-Bit     | Surface (Room) card            | Registration   |
|  card          |   per fixture: LED canvas,     | Devices        |
|  art / name    |   zone bar, binding chip       | Roles &        |
|  phase chip    |   instrument cards             |  manifests (▾) |
|  details       |                                | Event log      |
|  Run / Abort   | Bit status card (when loaded)  |                |
|  Load →picker  |                                |                |
|                | Triggers card                  |                |
+----------------+--------------------------------+----------------+
```

Widths ≥1200px: sidebar 280px fixed, rail 320px fixed, main fluid.

### 4.1 Top bar

- Wordmark "Terrarium Console" in Londrina with the ✦ ornament — the one
  place brand identity lives; nothing decorative below it.
- **Connection chip**: sage `CONNECTED` / rose `DISCONNECTED — retrying (n)`
  from the websocket, with attempt count. Replaces today's `#conn` span.
- **Room chip**: room type + `n/n fixtures bound`; terracotta whenever any
  fixture is unbound; absent when no Room is configured.

### 4.2 Sidebar — the Loaded-Bit card

State when nothing is loaded: an empty-state card ("No Bit loaded") with the
Load button. When loaded:

- **Cover art slot**: square, 18px radius. Ships rendering a default brand
  mushroom mark; reserves an optional `bit.toml [console]` art/icon field
  for later (declaring that field is NOT part of this slice — only the slot).
- **Identity**: `display_name` (Londrina), `name` + `vversion` (mono),
  kind chip (`R_GAME` / `TOOL`).
- **Phase chip** (the card's centerpiece), driven by `state_changed`:

  | Engine state | Chip | Color |
  |---|---|---|
  | IDLE | (empty-state card) | — |
  | LOADING / LOADED | `LOADED` | gold |
  | SETUP | `WAITING ROOM — registration open` | sage |
  | RUNNING | `RUNNING` | sage |
  | COMPLETING / UNLOADING | `WRAPPING UP` | gold |

  In SETUP the chip's sub-line renders the manifest start condition from
  `bits_listed` ("starts: 2 players, 120s timeout" / "starts: operator" /
  "starts: immediately").
- **Details**: supported rooms (active one highlighted), scored/jam role
  summary ("2 scored · jam open", from the loaded role table), description,
  `[console] notes`.
- **Buttons** — all overrides, never state-hidden:
  - **Run** (gold, primary): sends `run`.
  - **Abort** (rose, outline): two-tap confirm — becomes "Confirm abort?"
    for 4s, no modal — then sends `abort`.
  - **Load** (outline): opens the **Load picker** overlay.

### 4.3 Load picker (overlay)

Top layer surface, warm shadow, dismissable (Esc / outside click / ✕).
One card per `bits_listed` entry: art slot, display name, name+version
(mono), kind chip, supported rooms, start condition, **scored/jam summary**
(section 8), description, notes. Hidden bits render dimmed but loadable.
Error rows (rose, path + message) list after the cards. Each card has:

- **Load** button → sends `load_bit {name}`.
- **"Overrides ▸"** expander → a small form keyed off the manifest's
  override tables (at minimum: free-form key/value rows grouped per table,
  e.g. `rhythm.bpm = 80`, plus `start.*`), sent as the `overrides` dict.
  A `ManifestError` refusal renders on the card in rose with the server's
  located message. No client-side schema validation — the server is the
  validator and its errors are already good.

### 4.4 Main — Surface card

Header: "Surface" + `DEMO · 864 px · GRB`, and a **frames chip**: sage
`LIVE` while `room_frame`s arrived in the last 2s, dim `NO FRAMES`
otherwise. "No Room configured" empty state when `room` is null.

Per fixture, in declaration order:

- **LED strip as one `<canvas>`** — full-width, borderless, height ~28px.
  Paints from `room_frame` (GRB decode). Replaces the per-pixel divs
  (864 DOM nodes → 1 canvas per fixture).
- **Zone bar**: proportional spans, `name (start..end)`, exactly today's
  data.
- **Binding chip + controls**: bound → dev id chip in sage, **Release**
  behind the same two-tap confirm, sends `release_room {room_type,
  fixture}`. Unbound → terracotta `NOT BOUND` chip + **Arm** button
  opening an inline row (window seconds, default 30) that sends
  `arm_room {room_type, fixture, window_seconds}`; while the armed window
  is open the chip shows gold `ARMED`. (Armed-state display is
  best-effort from the arm action + subsequent `room_changed`; the wire
  does not push window expiry.)

Below the fixtures, **instrument cards**: one list discriminated by
LIGHT/AUDIO badge (light: instrument, target zone, params; audio: program,
drone, extra keys copied through), each lane one row —
`cc:74 → hue` with the live controller value in gold mono, updating from
`room_changed`. "No instruments declared (no Bit loaded)" empty state.

### 4.5 Main — Bit status card

Rendered only when `bit_status` is non-empty: a compact JetBrains Mono
key/value board (MetronomeBit: turn, cycle, elapsed…). Values render via a
small typed formatter: scalars plain, lists joined, nested objects
pretty-printed one level — never `[object Object]`.

### 4.6 Main — Triggers card

"No triggers declared" empty state. Per declared trigger, one row-card:

- Name (Londrina), target chip (`DEVICE`/`ROOM`), description.
- Condition line: `description · (source[: verb])`, dim.
- **Script collapsed by default**: summary `N steps · X.Xs` (total = max
  offset); "Show script" expands the mono step list (today's
  `+0.36s  @target  cc:70 = 125` / play-cue lines).
- DEVICE targets: device picker (from live device list, selection
  preserved across re-renders) + **Fire** (gold pill). ROOM targets: Fire
  only. Sends `fire_trigger {name[, dev]}`.
- Last-fired line: `fired_by → devs (n cues)`; `ADMIN MANUAL` tag in
  terracotta bold; a brief sage flash on the row when a `trigger_fired`
  arrives; `never fired` dim otherwise. (Fire history is still
  broadcast-only — a late-joining browser shows `never fired`; fixing that
  is a wire change and out of scope.)

### 4.7 Right rail

Top to bottom:

1. **Registration** — one row per non-Room role: name, class chip
   (`UNIQUE`/`SHARED`/`JAM`), scored marker, occupancy meter `n/cap`
   (∞ unbounded) filling sage. First because it is the SETUP-phase card.
2. **Devices** — per pool device: dev id (mono), name, role chip (dim `—`
   for none, including the Room-bound device, preserving the Room filter).
3. **Roles & manifests** — collapsed by default; expands to per-role
   instrument cards (same component as 4.4's) plus the welcome
   declaration. Never raw JSON.
4. **Event log** — JetBrains Mono; client-side `[HH:MM:SS]` stamps at
   receipt; level tags INFO (dim) / WARN (terracotta) / ERR (rose, tinted
   row); auto-scroll with pause-on-hover; capped at 500 entries
   (drop-oldest). Carries state transitions, refusals, `bit_completed`
   results, server `log` events.

Dropped as redundant (not lost): the standalone "Loaded Bit / State" header
line (now sidebar + top bar), and the separate registration-counts vs
roles-table split (now rail cards 1 and 3).

## 5. Behavior

- **Override feedback.** Every command control: pressed/pending state on
  click; on an `error` event naming its command, the originating control
  flashes rose and shows the reason inline for ~6s (and it logs). Matching
  error→control uses the command name plus, for `load_bit`/`fire_trigger`,
  the last-clicked control of that command (single-operator assumption,
  same as the trust model).
- **Confirms.** Abort and Release only: two-tap inline confirm, 4s window.
  Nothing else confirms; Fire is a show action and must be instant.
- **Reconnect.** On socket close: rose top-bar chip, whole page dims 20%
  (unambiguous stale-data signal), 1s retry with attempt count. On
  reconnect, `snapshot` + `bits_listed` repopulate everything; the only
  client-held state that survives is `lastFired` and UI state (collapse,
  picker selections).
- **Motion.** 0.2s tint transitions on state-carrying changes; LED canvas
  repaints with no transition; `prefers-reduced-motion` disables all.

## 6. Rendering rules (carried forward — the defect-fix list)

All nine load-bearing rules from the audit remain binding:

1. High-frequency events never rebuild lists (signature-gated: Bits,
   Triggers, fixture blocks).
2. A frame paints; it never rebuilds (canvas repaint only).
3. Per-fixture rebuild granularity; untouched fixtures survive.
4. Declaration order is physical order; rebuilt fixtures re-insert in
   place.
5. Pending frames keyed per dev (server-side; unchanged).
6. GRB decode, not RGB.
7. No shared globals between panel scripts — now enforced by **ES
   modules** (`type="module"`), replacing the IIFE pattern.
   `tests/test_console_script_isolation.py` is reworked to assert the
   module structure instead.
8. The Room's node id, role name, and registration counts stay hidden;
   instruments/surface/controllers stay visible (server filters
   unchanged).
9. An unknown dev in a frame event is a no-op.

## 7. Files

```
console/static/
  index.html          shell: three regions + top bar, module script tags
  terrarium.css       the whole design system: tokens (:root --t-*), type,
                      components (chips, cards, buttons, meters), layout,
                      breakpoints
  wire.js             websocket connect/reconnect, event dispatch, send();
                      the only socket-touching module
  bit.js              sidebar card + Load picker overlay
  surface.js          Surface card: fixtures, canvases, binding, instruments
  triggers.js         Triggers card
  rail.js             Registration, Devices, Roles, Event log
  shell.js            top bar chips, dim-on-disconnect, entry point wiring
  fonts/              LondrinaSolid-{Regular,Black}.woff2,
                      AtkinsonHyperlegible-{Regular,Bold}.woff2,
                      JetBrainsMono-Regular.woff2
```

`console/server.py`: `_CONTENT_TYPES` gains `".woff2": "font/woff2"`.
The five current static files are deleted. `ConsoleAgent`,
`console/protocol.py`, and all engine code are otherwise untouched except
section 8.

Fonts: Londrina Solid and Atkinson Hyperlegible convert from the design
system's TTFs; JetBrains Mono woff2 vendored from its upstream release
(OFL). If conversion tooling is a hassle at implementation time, shipping
the TTFs with `".ttf": "font/ttf"` in the allowlist is an acceptable
fallback — size over elegance is fine on a LAN.

## 8. The one wire addition: role summary in `bits_listed`

The Load picker shows scored/jam capacities before any Bit is loaded.
`bits_listed` today carries only manifest data; the role table exists only
on an instance. Additive fix, no schema break:

- `BitRegistry.list_view()` gains a best-effort `roles` key per row:
  `{"scored": [...], "jam_open": bool, "capacity_total": int|None}` —
  computed by instantiating the Bit class with its resolved config and
  reading `role_table`, inside a `try/except` (a Bit whose constructor or
  role_table raises yields `roles: null`, never a discovery failure).
  Instantiation happens at `list_view` time (console connect), not
  discovery — preserving "discovery never imports Bit code" is impossible
  here, so the compromise is: the lazy map already imports on load;
  `list_view` imports on first console connect. `--list-bits` CLI output
  is unchanged.
- Non-Room roles only (the Room filter applies here too — a ROOM-class
  role must not leak through the picker).
- One new test: `bits_listed` carries the summary for TestBit
  (1 scored shared + 1 jam) and `roles: null` for a constructor-raising
  fake, and never a ROOM role.

## 9. Non-goals

- Any other wire/protocol change: fire history in `snapshot`, device
  last-seen display, armed-window expiry push — all deferred.
- Authentication, or any trust-model change. Trusted LAN, `127.0.0.1`
  default, exactly as today.
- Phone-width tuning (<760px renders single-column, untuned).
- Cover-art manifest field and art pipeline (slot only).
- The luxaeterna `WebSimBackend` canvases (different repo's surface).
- Client-side override schema validation (server validates).

## 10. Responsive

- **≥1200px**: three columns (280 / fluid / 320).
- **760–1200px**: sidebar collapses to a top strip (art, name, phase chip,
  Run/Abort/Load); rail stacks below main; picker overlay unchanged.
- **<760px**: single column, functional, out of design scope.

## 11. Verification

- **Static tests** (`tests/test_console_static.py`): file set matches the
  server allowlist (incl. fonts); `index.html` references every module;
  no CDN URLs anywhere in `static/` (offline venue guarantee).
- **Isolation test** rework: every `.js` in static/ is an ES module and
  the only entry point is the one `index.html` loads.
- **Protocol/agent tests**: section 8's `list_view` roles tests; all
  existing agent tests pass unchanged (no event schema changed).
- **Interactive UAT** (`--console-port`, run_stack): load via picker with
  overrides (MetronomeBit bpm), watch SETUP→RUNNING phases, fire triggers,
  arm/release a fixture, abort with confirm, kill the socket and watch the
  dim/reconnect cycle, tablet-width check.
- **Mockup gate first**: a self-contained static mockup (both breakpoints,
  MetronomeBit-RUNNING fake data) is reviewed and approved before any of
  the above is built.

## 12. Sequencing

1. Mockup (design artifact, not committed to `console/static/`) → review.
2. On approval: implementation plan via writing-plans; the natural task
   split is (a) tokens/CSS + shell + wire.js, (b) sidebar + picker +
   section 8 backend, (c) surface card, (d) triggers card, (e) rail,
   (f) static/isolation test rework + UAT.
