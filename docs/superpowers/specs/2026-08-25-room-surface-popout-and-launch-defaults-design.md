# Room-surface pop-out and launch defaults

Date: 2026-08-25. Status: approved design, pre-implementation.

## Problem

The Room's per-fixture surface canvases are consolidated into the Terrarium
Console's admin view now, so a smoke-test launch no longer needs a separate
auto-opened browser tab per Room surface. Today `run_stack --open` opens a
tab for every `BROWSE_URL:` line any child prints: the Console, each
simulated player device (IE), and each Room fixture canvas. The Room tabs
are clutter. But the fixture canvases must stay one click away: the operator
should open them on demand, per fixture, from the Room card in the Console.

Two gaps block that:

1. The Console has no way to learn a fixture canvas's URL. Each canvas is an
   HTTP server owned by its own simulator subprocess on an ephemeral port,
   and the URL only ever surfaces as a stdout marker line that `run_stack`
   tails. Nothing feeds it into `ConsoleAgent`.
2. The markers cannot distinguish a Room-surface tab from a player-device
   tab. In o2lite mode both are emitted by `harness/o2_shroom.py` with the
   identical `BROWSE_URL: Watch the Shroom at ...` line.

## Decision (approach A of three considered)

The simulator process that owns a canvas URL reports it over the devicelink
wire it already has. Rejected alternatives: Control capturing simulator
stdout (breaks the deliberate inherited-stdout contract `run_stack`'s tee
and logs depend on), and `run_stack` pushing harvested URLs into the Console
(only works under `run_stack`; a bare `terrarium_boot --console-port` run,
the documented venue path, would get a button that never appears).

## 1. Wire message

New upstream engine-level message in `devicelink/protocol.py` (the single
source of truth for the wire shape):

    /game/canvas  "ss"  [dev, url]

Not a `/game/<verb>` gameplay message: `DeviceLinkAgent` handles it in its
own inbound dispatch, like `hello`, and it is never routed to
`Bit.verb_handlers()`. Which simulator serves which canvas is transport
furniture, not gameplay.

Senders: `harness/room_simulator.py` and `harness/o2_shroom.py` send it
once, immediately after `hello`, carrying the URL their `WebSimBackend`
already prints. Both send unconditionally (no `--no-join` branching here;
a player device's canvas URL is equally useful to the Console, same
reasoning as the existing `label` plumbing). Devices that never send it
(real hardware, phones, the Flutter sim) simply have no URL on record, and
nothing downstream requires one.

Scheme allowlist at the decode boundary: only `http://` and `https://`
URLs are accepted; anything else (`javascript:`, `data:`, a relative path)
is refused at decode and logged, never stored. This lives in
`devicelink/protocol.py` alongside the existing label sanitization, so a
hostile device cannot plant a script link in the operator's admin panel.

`DeviceLinkAgent` stores `dev -> url` alongside its existing per-device
state and clears it when the device is dropped. No persistence: an
ephemeral port is stale the moment its process dies.

## 2. Console plumbing

`ConsoleAgent` already builds the Room read model via
`control.room_view.room_view()` and broadcasts it as `room_changed`.

URL map access: constructor injection of a read callback (for example
`canvas_urls: Callable[[], dict]`), the same pattern `on_room_frame` used
in the other direction. Zero `control/engine.py` changes; `ConsoleAgent`
stays constructible under `tests/` with a plain lambda. When a canvas
message arrives, `DeviceLinkAgent` pokes the existing observer path so the
Console re-broadcasts: `room_changed` when the dev is a bound fixture,
`devices_changed` otherwise. No new event type; an old browser ignores the
extra field, keeping the wire additive.

View join: `fixtures_view()` in `control/room_view.py` gains a `url` field
per fixture entry, resolved as `gs.room.bound[fixture.name] -> dev -> url
map lookup`, `null` when the fixture is unbound or its device never
reported a canvas. The URL map is passed in as a plain dict argument, so
`control/` gains no new imports (the existing purity test continues to pin
this). Device-list entries get the same optional `url` field, which covers
player-device canvases later without another wire revision.

Front-end trust handling: the URL is only ever set as `href` on an
`<a target="_blank" rel="noopener">`, never via `innerHTML`.

## 3. Marker split and run_stack default

New constant in `harness/markers.py`: `ROOM_URL = "ROOM_URL:"`, sibling to
`BROWSE_URL`. Meaning: a URL worth knowing, not worth an automatic tab.

Emitters:

- `harness/room_simulator.py` (websocket-mode Room fixture): `ROOM_URL`.
- `harness/o2_shroom.py`: `ROOM_URL` when `--no-join` is set (it is the
  Room fixture then), `BROWSE_URL` otherwise (a real simulated player
  device). This is the one justified `--no-join` branch: the two roles
  genuinely differ in whether an operator wants an auto-tab.
- `harness/terrarium_boot.py` (Console URL): unchanged, `BROWSE_URL`.

`run_stack`: the collector matches both prefixes. `BROWSE_URL` keeps
today's behavior (collected, echoed, auto-opened under `--open`).
`ROOM_URL` is collected and echoed with a distinct label, for example
`room surface (open from the Console): <url>`, and never auto-opened, so a
copy-paste path survives a Console outage. Net effect of
`./smoke-test.sh --open --devices 2`: Console tab plus two shroom tabs
open; Room surface tabs do not, and their buttons wait on the Room card.

No `--open-room-surfaces` restore flag (YAGNI: the buttons cover the click
path, the echoed URLs cover the paste path).

## 4. Per-fixture pop-out button

`console/static/surface.js` renders the Room card's per-fixture rows; the
button lands there. A fixture row whose view entry carries a non-null
`url` renders a small pop-out control: a real
`<a href target="_blank" rel="noopener">` styled as a button, not a
`window.open` click handler, so middle-click, cmd-click and
open-in-new-window behave natively and no popup blocker is involved. A
fixture with `url: null` renders no button (absence, not a disabled stub;
there is nothing the operator could do from the panel to enable it).

Two constraints inherited from this front-end's own history:

- Signature-gated rebuilds: `room_changed` fires on every controller
  change, roughly four times per painted frame. The URL joins the
  existing per-card signature inputs so the row rebuilds only when the
  URL actually changes (once, when the canvas message first arrives).
- Behavioral tests, not greps: the button gets DOM-stub coverage in the
  existing `tests/js/` harness (Node `vm`, wrapped by the Python runner,
  skipping where node is absent).

Styling follows `console/static/terrarium.css`'s existing dark-brand
vocabulary; an icon-sized affordance on the row, no new visual language.

## 5. Testing

Python suite (offline, no O2; the load-bearing offline property holds):

- `devicelink/protocol.py`: canvas encode/decode round-trip; scheme
  allowlist refusals.
- `DeviceLinkAgent`: stores `dev -> url`; drop/release clears it; a canvas
  message for a never-hello'd dev is ignored; observer poke fires
  `room_changed` for a bound fixture and `devices_changed` otherwise.
- `control/room_view.py`: bound-fixture URL join; unbound and unreported
  both yield null; import purity unchanged.
- `ConsoleAgent`: snapshot and `room_changed` carry the field, tested with
  a fake `canvas_urls` callback and no devicelink import.
- Harness: both emitters send canvas-after-hello; `tests/test_markers.py`
  re-pins emitters per section 3; `run_stack` collector opens `BROWSE_URL`
  only and echoes both kinds with distinct labels.

JS behavioral tests: renders when `url` present, absent when null;
`href`/`rel`/`target` correct; no row rebuild on an unrelated controller
change.

One live smoke at the end: `./smoke-test.sh --open --devices 2` confirms
three tabs auto-open (Console plus two shrooms), Room tabs do not, and the
buttons appear on both fixture rows and open the right canvases.

## Edge cases

- A simulator restarting mid-run re-hellos and re-sends canvas, so the URL
  refreshes.
- Flutter sim, phones, and hardware never send canvas: no button, correct.
- Old browser against new server ignores the extra field; new browser
  against old server sees no `url` key and draws no button. Additive in
  both directions.
