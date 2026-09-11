# Lobby, join handshake, and admin start

**Date:** 2026-09-11
**Status:** Approved design, pre-implementation
**Driver:** A round today starts on a timer or the instant enough players
join, the Room is static and silent while it waits, a device has to send an
explicit join with a node it learned from a poster, and two devices joining
together make no sound at all. This spec replaces the timed wait with an
admin-started lobby that every Bit gets by default: an aurora and a
breathing warm pad while it waits, a lobby-initiated handshake that turns
any instrument on the venue network into a player with a double tap, a
three-step join ceremony per instrument, a silent green pulse when the Bit
is full, and one start authority reached from a QR code, an NFC tag, the
device wire, or the Console.

## 1. What exists today (for the record)

- `control/start_condition.py` evaluates a Bit's `[start]` table
  (`immediate`, `players`, `operator`) inside the harness SETUP hold
  (`harness/terrarium_boot.py`'s `_wait_in_setup`). `players` starts the
  instant `min_scored` is reached. The hold yields with `"state-changed"`
  when anything else moves the engine out of SETUP, so a Console Run
  already hands off cleanly.
- `GameServer.tick` runs generators and `Bit.fires(at)` only in RUNNING.
  Nothing animates the Room in SETUP: each fixture's light session renders
  the Bit's ROOM declaration at its static values, and the fixture drone
  starts on RUNNING (`devicelink/agent.py`'s `on_state_change`).
- A role's `welcome` pair (light half rendered device-side on grant, audio
  half played on a transient Arco voice by `control/audio.py`) is the only
  join ceremony. The audio half exists so a welcome cue never disturbs a
  sustained drone; the same voice path serves the bell below.
- `SolidCue` overrides are applied Control-side at the two frame-send seams,
  so a device needs no new wire message to flash a solid colour. `PlayCue`
  is untimed on the wire by design; Control may hold one and send it later.
- A device hellos (its dev id is the app's GemID for the MycoQuest app) and
  lands in `DevicePool`; it plays nothing until it sends `/game/join
  <node>`. The engine refuses every verb from an unregistered device.
- `harness/www_server.py` serves `www/` on port 8788 to the venue LAN.
  `control/join_info.py` builds the Join card's guest URL and QR.
- The Console and the uplink both carry a `run` command that calls
  `gs.run()` directly with no rule applied.

## 2. Lifecycle and the lobby state

SETUP stays the engine state. Inside SETUP a new pure module,
`control/lobby.py`, owns the lobby:

- **Lobby state** is `WAITING` or `FULL`. `FULL` means every scored role
  whose capacity is finite is at capacity. A Bit whose scored roles are all
  uncapped never reaches `FULL` and waits. The state is recomputed from
  `RegistrationState.counts()` on every registration change and resets on
  every SETUP entry.
- **Join count and note scale.** The lobby counts granted scored joins in
  this SETUP. The bell for the Nth join is the Nth note of the A major
  scale starting at A4: MIDI 69, 71, 73, 74, 76, 78, 80, then back to 69 on
  the eighth join, wrapping every seven.
- **Start condition `admin`.** `[start] when = "admin"` joins `immediate`,
  `players`, and `operator`. With it the harness hold waits with no
  deadline unless `timeout_seconds` is set, in which case `on_timeout`
  applies as today. The only thing that moves SETUP to RUNNING is a start
  request. `start_decision` returns `None` for `admin` unless the timeout
  fires; it never starts on its own.
- **One start authority.** `GameServer.request_start(key, source_dev,
  source) -> str | None` is the single function every entry point calls.
  It never raises; a refusal is a reason string. In order:
  1. Not in SETUP: refused, `"not in SETUP"`. No room reaction.
  2. The loaded Bit's start condition is not `admin`: the Console and
     uplink (admin sources, no key) still start as they always have; a
     keyed source is refused, `"Bit does not take an admin start"`.
  3. Key mismatch for a keyed source: refused, `"bad key"`. Logged on the
     Console with the source; **no room reaction at all**, so a stranger
     scanning an old poster cannot make the fixtures react.
  4. Admin source (`gs.is_admin(source_dev)`, section 3): start
     unconditionally, even with zero scored players.
  5. Otherwise the Bit's rule: scored count below a positive `min_scored`
     is refused, `"minimum not met"`; absent or zero `min_scored` allows a
     start with no scored players.
  6. Accept: `gs.run()`. The harness hold sees the state change and yields
     through its existing path.
- **Room feedback**, emitted by the lobby as fixture cues (section 4):
  accept gives one green flash on every fixture; `"minimum not met"` gives
  two red flashes; any other refusal after a valid key gives three red
  flashes. A `"bad key"` refusal gives nothing.
- **Observation.** Every attempt fires a `on_start_requested(record)`
  engine observer event carrying source, source_dev, outcome, and reason.
  The Console logs it; the uplink may forward it later.

## 3. Start entry points and source identity

All three adapters are a few lines each and call `request_start`.

| Entry | Wire | Source label | Key | Admin? |
|---|---|---|---|---|
| Web | `GET /start?key=<key>[&dev=<GemID>]` on the LAN static server (port 8788) | `web:<dev>` or `web:anonymous` | required | if `dev` is listed |
| Device | `/game/start "ss" dev key` | `device:<dev>` | required | if `dev` is listed |
| Console | existing `run` command | `console` | none | always (`source_dev = "terrarium"`) |
| Uplink | existing `run` command | `uplink` | none | always (`source_dev = "terrarium"`) |

- **Web.** `harness/www_server.py` gains one route. QR codes and NFC tags
  carry that URL, so a phone's browser is the adapter. The handler runs on
  the server's own thread and must not touch the engine: it enqueues
  `(key, dev)` on a bounded queue and answers `202 Accepted` at once. The
  device-link agent drains the queue on its tick and calls `request_start`.
  The MycoQuest app appends `dev=<GemID>` when it opens the URL; a poster
  scan is anonymous. A request whose peer address is the box's own
  loopback address is the Terrarium itself: the handler substitutes
  `source_dev = "terrarium"` and the source label `web:terrarium`,
  matching the loopback trust the Console already has and giving the CI
  start-after-grant path (section 7) the override. Only `GET` is served;
  the response body is plain text and never echoes the key.
- **Device.** The agent recognises the `start` verb before the engine's
  verb dispatch and routes it to `request_start` with the hello'd dev, so
  no Bit needs a `start` handler and the engine's "unregistered device"
  refusal does not apply (an admin instrument is not necessarily a
  player). The encoder lives in `devicelink/protocol.py` with the other
  wire rows.
- **Console and uplink.** Their existing `run` handlers call
  `request_start(None, "terrarium", "console"|"uplink")` instead of
  `gs.run()`. A refusal becomes the usual `error` event.
- **The Terrarium is a built-in admin device.** `control/lobby.py`
  declares `TERRARIUM_ADMIN = "terrarium"`, a reserved dev id that is
  always in the effective admin set and can never be removed by config.
  It is the identity the Console, the uplink, and a loopback web hit carry,
  so the Terrarium's own operator surfaces go through the same rule as an
  admin instrument rather than around it. `TERRARIUM_ADMIN` is refused as
  a hello'd dev id at the transport, so no device can impersonate the box.
- **Admin identity.** `terrarium.toml` gains `[admin] devices = [...]`, a
  list of dev ids (GemIDs) that count as admin instruments in addition to
  the built-in one. The Terrarium cannot verify who holds a dev id; the
  device wire and the static server carry no auth, the same trusted-LAN
  model the Console already runs on, and the spec records that plainly.
  An absent list means the Terrarium itself is the only admin.
- **`GameServer.is_admin(dev) -> bool`** exposes the effective set (the
  built-in identity plus the configured list) to Bits and to the Console,
  so a Bit can gate admin-only behaviour on the same answer the start rule
  uses and the Console can label admin devices in its device list.
- **`/game/start` and the web URL are the same thing.** A tap on an admin
  instrument, an NFC tap, and a QR scan all end in the one function; there
  is exactly one piece of start logic to maintain.

## 4. Lobby light and sound

Everything here lives in `devicelink/agent.py`, which already owns each
fixture's light session, the audio grants, the per-tick breath, and the
timed cue queues. It runs only while the engine is in SETUP and the Bit's
`[lobby] enabled` is true.

- **Waiting look.** On SETUP entry the agent swaps every fixture's light
  session to a lobby manifest: luxaeterna's `aurora` on the fixture's
  whole strip with a hue lane on cc:74 and a level lane on cc:11. A lobby
  feeder, mirroring `_feed_breath`, drives cc:74 as a slow hue drift and
  cc:11 as the breath envelope into every fixture each tick. Fixture
  sessions share one clock, so the drift reads as one motion across the
  room. On RUNNING the agent swaps back to the Bit's ROOM declarations
  through the existing `_setup_room()` seam.
- **Waiting sound.** The same cc:11 value is fed to every audio-capable
  fixture voice, program set to the warm pad (`control/audio.py`'s
  `WELCOME_INSTRUMENTS` program 89), and the fixture drone starts on SETUP
  entry rather than RUNNING. Light and sound read one number. On `FULL`,
  on start, and on UNLOADING the lobby drone stops; the Bit's own
  `on_run_start` and program changes proceed unchanged.
- **Join ceremony.** For each granted scored join the lobby emits, from
  one computed presentation time `at`:
  1. two green `SolidCue` overrides on the device: on at `at`, off at
     `at + 0.2`, on at `at + 0.4`, off at `at + 0.6`;
  2. the bell on a transient Room voice at `at + 0.8`: a bell program
     (`WELCOME_INSTRUMENTS` gains a `"bell"` entry) at the lobby's next
     scale note, note off after 1.0 s;
  3. the device's chime `PlayCue`, held in the agent's timed queue and
     sent at `at + 1.8`, with `params` carrying the MIDI key so the
     device synthesises the same pitch. `harness/sim_audio.py` gains a
     keyed chime; the wire stays untimed.
  Ceremonies are queued: at most one plays at a time and the next starts
  `ceremony_gap_s` (1.0 s) after the previous one's last step, so the
  scale is always heard in order even when two joins land together.
- **Full.** When the lobby reports `FULL`, the drift stops, cc:74 is
  pinned to green (hue 0.33), the breath keeps pulsing cc:11 into the
  light only, and every fixture drone stops. Silent green pulse. A release
  that drops the lobby back to `WAITING` resumes the drift and the drone.
- **Start and refusal flashes.** Green once on accept; two red for
  minimum not met; three red for any other keyed refusal. Each flash is a
  `SolidCue` on every fixture: 0.25 s on, 0.25 s off.
- **Opt-out.** `[lobby] enabled = false` gives today's behaviour: static
  ROOM declaration in SETUP, drone on RUNNING, no ceremony, no handshake.
  `request_start` still works.

## 5. The lobby handshake

The handshake is the default path for a device that hellos and sends no
join. It ends by calling `GameServer.join(dev, node)`, the same call an
explicit join makes, so the ceremony, the note scale, and `Bit.on_join`
are unchanged.

- **Invite.** While the lobby is `WAITING`, a device that is in
  `DevicePool`, holds no role, is not a bound fixture, and is not in the
  closing-fade set is invited: two white `SolidCue` flashes (0.2 s on,
  0.2 s off, twice). The invite re-flashes every `invite_interval_s`
  (5 s) while the device stays un-joined. Fixtures are never invited.
  Nothing is invited while `FULL`; a device that hellos into a full lobby
  waits dark until a slot frees.
- **Accept.** A double tap from an invited device joins it to the Bit's
  `launch.default_join_role`, resolved to that role's node through
  `BitConfig.node_for`, the node the Join card already prints. Double tap
  is either one `/game/tap` with `count >= 2`, or two `/game/tap`
  messages from the same device whose gesture stamps are within
  `double_tap_window_s` (1.5 s). The join then runs the section 4
  ceremony and the device drops into the aurora waiting state.
- **Taps before a role.** The agent intercepts `tap` from invited,
  un-joined devices before engine verb dispatch. Every other verb from an
  un-joined device is refused as today. A refused handshake join (for
  example the slot filled between invite and tap) sends the device the
  normal deny event and leaves it invited; the next invite flash tells the
  player to try again once a slot frees.
- **Explicit join still works.** A guest URL carrying a node, or a
  Testshroom's own join, skips the handshake and goes straight to the
  ceremony. Jam roles stay explicit-join only, in and out of the lobby.
- **State.** Per-device handshake state (invited-at, last-invite-at,
  first-tap stamp) lives in `control/lobby.py`, pure, and is cleared on
  release, on join, and on SETUP exit.

## 6. Manifest and config schema

**Bit manifest `[start]`** (`control/bit_config.py`):

```toml
[start]
when = "admin"          # new value alongside immediate | players | operator
key = "metro-dev"       # required when when = "admin"; profile/CLI override
min_scored = 2          # unchanged meaning; absent or 0 allows an empty start
timeout_seconds = 600   # optional for admin; on_timeout applies as today
on_timeout = "abort"
```

`key` is refused as a located `ManifestError` when `when = "admin"` and it
is missing or empty, and ignored with the existing unknown-key warning
otherwise. It rides `merge_overrides` like every other field.

**Bit manifest `[lobby]`** (new, optional; defaults in `control/lobby.py`):

```toml
[lobby]
enabled = true
invite_interval_s = 5.0
ceremony_gap_s = 1.0
double_tap_window_s = 1.5
```

**Terrarium config `[admin]`** (`control/terrarium_config.py`):

```toml
[admin]
devices = ["gem-0001", "gem-0002"]
```

A non-string entry is a located `TerrariumConfigError`, and listing the
reserved `"terrarium"` id is a located error too (it is always present).
The list is threaded into `GameServer` at construction by
`harness/terrarium_boot.py`, the same way the carried-instrument catalog
is; `GameServer.is_admin` unions it with `TERRARIUM_ADMIN`.

**MetronomeBit** (`bits/metronome/bit.toml`): `when = "admin"`, `key =
"metro-dev"`, `min_scored = 2` unchanged, `timeout_seconds` removed.
`profiles/dev-metronome.toml` keeps 80 BPM. TestBit is unchanged and gets
the lobby with its existing `immediate` condition, so its short SETUP
window still self-starts.

## 7. Harness, Console, and CI

- **Join card** (`console/static/join.js`, `control/join_info.py`) gains a
  Start row: the start URL, its QR, the key in plain text for the
  operator, and the `/game/start "ss" dev key` wire row. `build_join_info`
  takes the loaded Bit's start condition; a Bit without an admin start
  shows no Start row. `terrarium_boot` prints `START_URL: <url>`
  (`markers.START_URL`, collected by `run_stack`, never waited on).
- **Console events.** The event log gets one line per start attempt
  (source, outcome, reason), per invite, per handshake accept, and per
  lobby state transition. `state_changed` is unchanged; the lobby state
  rides a new `lobby_changed` event and a `lobby` key on the snapshot.
- **`run_stack --ci`** gains `--start-after-grant`: once every spawned
  device has reported `DEVICE_ROLE_GRANTED`, it performs an HTTP GET on
  the collected `START_URL` with the key read from the resolved manifest,
  the same adapter a phone uses; because it runs on the box the hit is
  loopback and carries the Terrarium's own admin identity. `./smoke-test.sh --ci --profile
  profiles/dev-metronome.toml` passes the flag.
- **Testshroom** (`harness/o2_shroom.py`) gains `--handshake`: hello, no
  join, render invite frames, send a count-2 tap on the first invite. It
  also renders `/leds` frames before a role is granted (today it does;
  this pins it) and synthesises the chime at the key carried in the play
  cue's params. CI runs one device via handshake and one via explicit
  join.
- **Serve mode** needs no change beyond the hold honouring `when =
  "admin"`.

## 8. Testing

Offline suite, no Arco, all doubles as strict as the library (boundary
rule 5):

- `tests/test_lobby.py`: the start rule as a table over key, admin,
  minimum, and state; `FULL` detection over capped, uncapped, and mixed
  roles; the note scale and its wrap; double-tap detection with count-2
  and paired taps inside and outside the window; invite scheduling and
  re-flash; ceremony queueing and spacing.
- `tests/test_lobby_frames.py`: the real-luxaeterna frame rig from
  `tests/test_metronome_bit_live_frames.py` asserting pixels: white
  double flash on invite, green double flash on accept, aurora drift
  moving across two fixtures, green pulse with no drone note on `FULL`,
  red flash counts on refusal, and the swap back to the Bit's declaration
  on RUNNING.
- `tests/test_audio.py` additions: the bell program and key per join on
  the recording fake voice; drone on at SETUP and off at `FULL`.
- `tests/test_www_server.py` additions: `/start` against a real local
  socket, queueing, `202`, no key in the body.
- `tests/test_o2_transport.py` / agent tests: the `start` verb routes to
  `request_start` from an un-joined device; `tap` from an invited device
  is intercepted, from anyone else refused.
- Config tests: `[start] key` required for `admin`, `[lobby]` defaults,
  `[admin] devices` parsing and located errors (including the reserved
  id), `start_decision` for `admin` with and without a timeout.
- Admin identity tests: `is_admin("terrarium")` is true with no config;
  the Console and uplink `run` start with zero players on an admin Bit;
  a loopback `/start` hit is treated as the Terrarium and a LAN-address
  hit is not; a hello with dev `"terrarium"` is refused.
- Console tests: `lobby_changed`, the Start row, the start log lines.

**Live gate on MYCOLOGICAL**, to be recorded in this section when run:
`./terrarium.sh --room DEMO`, load MetronomeBit; one Testshroom via
`--handshake` and one via explicit join, bell heard at A4 then B4;
a phone scanning the start QR starts the round; a wrong key gives no
room reaction; a start with one player gives two red flashes.

## 9. Cross-repo follow-ups (recorded, not built here)

- **mm-tuneshroom**: send `/game/start "ss" dev key` from an admin
  control (and `dev=<GemID>` on the web start URL); render `/leds` frames
  and play the chime at the carried key before a role is granted; a
  browser guest over o2ws has no sample player today.
- **mm-renquest / MycoQuest admin site**: write a device's GemID into the
  venue's `[admin] devices` list (or its future mm-fairyring cache) so a
  venue can register an admin instrument without editing `terrarium.toml`
  by hand.
- **Real Tuneshroom hardware**: the sample chime at the carried note.

## 10. Deviations recorded during execution

- **Start rule ordering (section 2).** The key is checked before the SETUP
  check: a valid key outside SETUP gives three red flashes (the "any other
  reason" case); a bad key stays silent; an unkeyed Console/uplink start
  outside SETUP is refused with no room reaction. With no Bit loaded every
  start is refused silently because there is no key to check against.
- **`min_scored` default.** 1 for `players` (unchanged, still refuses 0), 0
  for `admin` (absent means an empty start is allowed).
- **Accept flash.** The green accept flash is emitted by the agent, not the
  LobbyRuntime, because RUNNING tears the runtime down before the
  `on_start_requested` record arrives.
- **Testshroom handshake.** The invite is detected as a solid white frame
  (every byte >= 200) and answered with one count-2 tap, re-armed at most
  every 2 s.
