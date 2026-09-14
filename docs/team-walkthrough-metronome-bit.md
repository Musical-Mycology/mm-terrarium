# Team walkthrough: standing up a Metronome Bit round

Audience: anyone on the team who wants to run a Terrarium locally and play a
round of the Metronome Bit end to end. Uses the **mm-tuneshroom solo web
app**, so no physical Tuneshroom hardware is required -- and, because of its
new standalone ("solo") mode, it's fine to launch it *before* the Terrarium
even exists.

**Run everything below on your own dev machine.** You need two repos
checked out side by side:
- `mm-terrarium`, with a sibling `arco` checkout at the same level as the
  main clone (a git worktree resolves to the same place), and a `.venv`. In
  a fresh worktree, symlink the shared one:
  `ln -s /path/to/mm-terrarium/.venv .venv`. There is no bare `python` on
  the dev boxes -- always use `.venv/bin/python`.
- `mm-tuneshroom`, with the Flutter SDK on `PATH` (`flutter --version` to
  check).

Three terminal tabs: one for the Terrarium server, one for `tool/sim`, plus
the browser tab `tool/sim` opens for you.

---

## Step 1 -- Set up a Tuneshroom (the solo web app)

From the `mm-tuneshroom` root:

```
tool/sim build
tool/sim serve --devices 1
```

`build` runs `flutter build web -t lib/sim_main.dart` (the one-time-per-code-
change step; skip it on a repeat run if you haven't changed anything).
`serve` starts a local static server on `127.0.0.1:8780` and opens
`http://127.0.0.1:8780/?dev=ie1` in your browser automatically -- watch for
the `BROWSE_URL:` line it prints.

You'll see the Tuneshroom **live already**, with no Terrarium running: lit
up in its idle aurora, and tap/double-tap/shake wired to local functions
(`play_aurora`/`win`/`fireworks_player`). That's the point of solo mode
(`docs/superpowers/specs/2026-09-13-standalone-solo-mode-design.md` in that
repo) -- a Tuneshroom is supposed to be alive on its own, with or without a
Room. It'll also try once to reach a hub at `127.0.0.1:8080`, fail (nothing's
there yet), and settle back into solo with a "lost" status chip. That's
expected -- ignore it for now.

## Step 2 -- Base Terrarium, no Room

In `mm-terrarium`:

```
./terrarium.sh
```

This wraps `harness.run_stack --no-bit --serve --devices 0
--console-port 8772`: no Bit loaded, no Testshrooms spawned, boots straight
to `NO_ROOM`.

Console: **http://127.0.0.1:8772/**

## Step 3 -- Load the Terrarium Bit (MetronomeBit)

In the Console, **Load** -> **MetronomeBit**, Room **DEMO** (the only
`room_types` entry in `bits/metronome/bit.toml`).

Because you booted with no Room, this first Load brings up Arco + the DEMO
Room from `NO_ROOM` (about 15 s) on port 8080, then loads the Bit into
`SETUP`. MetronomeBit's `[start]` table sets `when = "admin"`, so nothing
times out and nothing auto-starts on device count -- the round only begins
when something with start authority says go, which is Step 4. While `SETUP`
holds, the Room shows the lobby's aurora and breathing pad instead of
sitting static and silent.

## Step 4 -- Connect protocol: bring the Tuneshroom in and start

**Reload the Tuneshroom browser tab from Step 1.** This matters: the solo
app only attempts its hub connection once, at boot (`SimController.start()`
opens the link exactly once and does not auto-retry on failure -- see
`lib/sim/sim_controller.dart`). Since Arco wasn't up yet the first time, you
have to trigger a fresh attempt now that it is.

What happens, wire-level, after the reload (this is the current
`docs/diagrams/player-flow.seq` sequence -- see *Diagrams* below):

1. **hello.** The link connects to Arco this time; Control registers the
   device and pushes the Room's declared instrument config back
   (`/ie1/room`).
2. **Invite.** The lobby is `WAITING` and this device hasn't joined, so
   Control starts sending an invite: a white/black LED flash repeating
   every 5 s. The page's status chip moves from "connecting" to "in lobby".
3. **Handshake.** Double-tap the Tuneshroom in the browser (or physically,
   on real hardware) while it's showing the invite flash. One tap with
   `count >= 2`, or two taps within 1.5 s, both count.
4. **Join.** Control grants the join exactly as an explicit `/game/join`
   would -- the served role blob replaces the bundled solo blob (geometry,
   ambient, thresholds, functions all switch over).
5. **Ceremony.** Two green flashes on the device, a bell up the A-major
   scale on the Room's own fixture voice, and a chime sent to the device.
6. **Start.** The Bit is still in `SETUP` (`min_scored` is 1, but nothing
   auto-starts on count). Go to the Console and click **Run**. That calls
   `GameServer.request_start` as the Console's always-admin source (no key
   needed), flips `SETUP` -> `RUNNING`, and the round begins.

   A real player instead scans the Join card's Start QR/URL or an NFC tag,
   which needs the Bit's key (`metro-dev` for MetronomeBit). The Console's
   Run button skips that because it's already a trusted admin source.

From here the round plays like any other Bit: taps in, timed LED cues out,
and a `/release` once the closing fade ends -- at which point the app's
`_clearRole` reapplies the bundled solo blob rather than going dark, so it's
immediately alive again for the next round.

### Headless alternative (no Flutter/browser)

If you just need a scriptable device with no UI, `mm-terrarium`'s own
`harness/o2_shroom.py --handshake` does the same hello/invite/double-tap/
join/ceremony sequence entirely on the CLI, printing each step
(`handshake: invite seen, double-tap sent at ...`, role-granted, etc.). It
has no solo mode, though -- it's a plain o2lite client, so it must be
launched **after** Arco is already up (i.e. after Step 3, not before Step
2), unlike the Flutter app above:

```
.venv/bin/python -m harness.o2_shroom --handshake
```

---

## Good to know

- **One Arco per process.** A running `terrarium.sh` process can only ever
  connect to one Arco. To switch to a different Room, restart with
  `./terrarium.sh --room NAME` rather than trying to Unload a live Room --
  the Console refuses an Unload with connected clients.
- **ABORT keeps the Room up.** With live clients connected, ABORT ends only
  the Bit; the Room and Arco stay up, and the next Load reuses them.
- **`--console-port`** can open the Room panel (per-fixture light/audio
  state) and the Triggers panel (fire any of the Bit's declared cues by
  hand) if you want to poke at internals mid-round.

## Diagrams

`docs/diagrams/player-flow.seq` (rendered into `docs/MM_TERRARIUM.md` under
*Lobby, join handshake, and admin start*) now shows this lobby handshake --
invite, double-tap, ceremony, admin start -- instead of the older immediate
`/game/join` flow it still showed before this pass. Regenerate after any
further protocol change with `.venv/bin/python -m tools.render_diagrams`;
`tests/test_diagrams.py` fails the suite if a diagram source and its
committed output ever drift apart.

The Bit lifecycle (`docs/diagrams/lifecycle.d2`) and Room state machine
(`docs/diagrams/terrarium-state.d2`) diagrams were checked against the
current lobby code too -- the lobby's WAITING/FULL states live inside
`SETUP` and don't add a new top-level state to either diagram, so those two
were already current and needed no change.
