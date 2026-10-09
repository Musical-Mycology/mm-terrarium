# The light lexicon: what an instrument's light means

**Audience:** anyone who makes an MM instrument or Room fixture light up:
firmware (mm-devshroom), the Flutter app and simulator (mm-tuneshroom), Bit
authors, and Control itself (`devicelink/lobby_runtime.py`, `control/lobby.py`).
**Status:** agreed at the 2026-10-05 weekly kickoff (Chris, Sophia, Victor);
written 2026-10-05. This doc is **normative**: it is the target. Each signal's
*Today* column says whether the code already does it; where it does not, the
gap is listed in [section 6](#6-gaps-between-this-lexicon-and-the-code).
**Owner:** mm-terrarium owns this file, as it owns the device contract
([`device-contract-guide.md`](device-contract-guide.md)). A new status signal
is added here first, then built.

## 1. Why: the furnace rule

A furnace has one indicator LED and no screen, yet it tells a repair tech
exactly why the house is cold: a short vocabulary of flashes, each with one
meaning. MM instruments work the same way. An instrument sealed in a case, or
a Room fixture up on a Tower, must be diagnosable from its light alone,
without opening it or attaching a laptop. That only works if every signal
means one thing everywhere: if a Bit flashes green twice for a high score,
nobody can tell "you scored" from "you joined a different round".

## 2. The grammar

- **Colour is the topic.** White is neutral and about connection. Green is
  success and readiness. Red is failure. All colours drifting (the aurora)
  is free play, with no Terrarium in charge.
- **A pulse is a state, a flash is an event.** A slow pulse loops for as long
  as the device is in that state. A flash is a one-shot that says something
  just happened, then the state's own light resumes.
- **The count is the code.** Two flashes is the plain form (success, failure,
  invite). Three red flashes is a distinct code ("refused: not allowed
  now"). New codes take a new count or colour, never an existing one.
- **Flashes fill the whole strip** at full level, in the strip's own channel
  order (`SolidCue` semantics, W stays 0). Timing, from `control/lobby.py`:
  0.2 s on and 0.2 s off on a player device (`DEVICE_FLASH_ON_S`,
  `DEVICE_FLASH_GAP_S`), 0.25 s on and 0.25 s off on a Room fixture
  (`FIXTURE_FLASH_ON_S`, `FIXTURE_FLASH_GAP_S`).

## 3. Reserved patterns: the rule for Bits and instrument functions

Every signal in sections 4 and 5 is **reserved**. A Bit, an instrument
function, or firmware game code must never render one to mean anything else.
The line is the *pattern*, not the colour:

- **Reserved:** two or three full-strip solid flashes of white, green or red;
  a steady slow green or white pulse with nothing else moving; the
  `sys:*` signatures in section 4.
- **Free for game light:** any hue, including green and red, as part of a
  Bit's own animation; single flashes of any colour; multicolour flourishes.

What the in-repo Bits do today, checked against that line (all allowed):
MetronomeBit shifts a player's hue to red on a miss and back to green on
recovery (`fail_player`, `metro_recovery`: a hue lane, not a flash);
MinigameBit blinks single 0.5 s white flashes; Rev1Bit's hold and swing are
single `SolidCue` flashes (white, red, blue); the built-in `flash`
diagnostic is one steady 5 s white (section 4, Identify).

## 4. The signals on a player instrument

In order of a device's life, boot to round's end. *Renders* is who draws
it: **firmware** (the device, alone, before Control is talking to it) or
**Control** (frames on `/<dev>/leds`; see *Where the light is rendered* in
[`MM_TERRARIUM.md`](MM_TERRARIUM.md)).

| Signal | Pattern | Means | Renders | Today |
|---|---|---|---|---|
| **Solo** | the aurora: all colours drifting, gestures play local functions | No Wi-Fi, or no Terrarium found. The device is a toy on its own. An armed beat-capable device also lands here once its link has been lost past 15 s. | firmware / app | Ships in mm-tuneshroom's solo mode (`[solo]` in `instruments/tuneshroom.toml`, exported to its `assets/solo/tuneshroom.json`). Rev 1 firmware: not yet. |
| **Looking** | slow white pulse | On Wi-Fi; no Terrarium is talking to it yet (discovering, or hello'd to a Terrarium with no round open). Also the lost-link cause: an armed beat-capable device after 3 s with nothing from Control. | firmware | Not yet. Rev 1 firmware animates while joining Wi-Fi, then goes dark. |
| **Invite** | white flash x2, repeating every 5 s | A round is open and this device may join: double-tap to accept. | Control | Ships (`LobbyRuntime.consider_invite`; needs a Room loaded with the lobby enabled; the `/handshake` itself goes out regardless). Between invites the device shows the slow white pulse. |
| **Success** | green flash x2 | What you just tried worked. On the device: the handshake validated. | Control | Ships for validation (the join ceremony: green x2, then a Room bell at +0.8 s and a device chime at +1.8 s). |
| **Ready** | slow green pulse | Validated: a scored slot is reserved; waiting for the round to start. | Control | Ships (`LobbyRuntime` status pulse, from the end of the ceremony's green flashes until `/role`; lobby-gated like the invite). |
| **Failure** | red flash x2 | What you just tried failed. On the device: the accept was denied (`/<dev>/deny`). The device stays hello'd and gets a jam role at start. | Control | Ships (`LobbyRuntime.on_deny`; lobby-gated, so a deny with no Room, or in RUNNING, shows nothing). |
| **Role granted** | `sys:loaded`: one green flash and two soft green pulses over 1.5 s (or the role's own `welcome`) | You have a role; the round is yours. | Control (Lux Aeterna) | Ships (luxaeterna `synth/status.py`). |
| **Playing** | the Bit's own light | Anything the Bit wants, inside section 3's rule. | Control | Ships. |
| **Round over** | `sys:closing`: a 0.6 s fade to dark | `/<dev>/release`: the role ended. The last frame holds (contract rule 6) and the device is back in the pool. | Control (Lux Aeterna) | Ships. |
| **Fault** | `sys:error`: one slow red swell (rise, hold, fall over 1.6 s) | The role's light manifest failed to resolve on this device. A bug, not a player error. | Control (Lux Aeterna) | Ships. |
| **Identify** | steady white for 5 s, chime first if the device has samples | An operator fired the `flash` built-in from the Console to find this device. | Control | Ships (`control/builtins.py`). |

Lux Aeterna also defines `sys:idle`, `sys:disconnected` (red double-blink,
then a red breath), `sys:identify` and `sys:selftest` (an R, G, B channel
sweep). Control does not drive them today; their patterns are reserved all
the same, so they keep their meaning if it ever does.

**Who renders what, at the boundary.** Firmware owns the light only until
Control starts talking to it: Solo and Looking are the device's own, and
every later signal arrives as frames. The device stays a pixel sink from
then on; it never draws Invite, Ready or Failure itself. The handover point
is the first `/<dev>/leds` frame, and a lost link keeps the last frame lit
(device contract guide, rule 9 and section 8). An armed beat-capable device that hears
nothing from Control for 3 s drops back to Looking, and to Solo at 15 s (spec
`2026-10-08-bidirectional-heartbeat-design.md` section 6.2).

## 5. The signals on a Room fixture

A Room fixture (the Tower, a dev strip, the venue array) is not carried by a
player, so it has no Solo or Looking state. With no Bit it plays its
instrument's ambient light. During SETUP it shows the lobby, and it carries
the start feedback for the whole Room.

| Signal | Pattern | Means | Today |
|---|---|---|---|
| **Lobby open** | aurora, hue drifting round the wheel every 20 s, breathing with the drone | SETUP: registration is open. | Ships (`lobby_light_manifest`). |
| **Lobby full** | aurora held at green, still breathing; drone silent | Every scored slot is taken; waiting for start. The Room's form of Ready. | Ships (`LobbyRuntime.set_state(FULL)`). |
| **Start accepted** | green flash x2 | A start request was accepted; the round begins. | Ships (`FEEDBACK_ACCEPT`, and the agent's own flash path after RUNNING). |
| **Start below minimum** | red flash x2 | A non-admin start with too few scored players. | Ships (`FEEDBACK_MINIMUM`). |
| **Start refused** | red flash x3 | A keyed start (a device's `/game/start`, `GET /start?key=`) with the right key but outside SETUP, or an accepted start that lost the race to another. | Ships (`FEEDBACK_REFUSED`). |
| **No reaction** | nothing | A wrong key, a keyed start to a Bit that takes no admin start, or an operator (Console, uplink) start outside SETUP. A stranger with an old poster gets no room reaction, on purpose. | Ships (`FEEDBACK_NONE`, `control/start_condition.py`). |

The start flashes go to every bound Room fixture (`_flash_fixtures`), not to
the device that asked.

## 6. Gaps between this lexicon and the code

G1 to G3 shipped on 2026-10-05 (spec
`docs/superpowers/specs/2026-10-05-device-status-light-design.md`); G4 was
decided on 2026-10-08 and its firmware half is still open. A gap is a
Control or firmware change, not a doc change.

- **G1. Done:** the white invite pulse and the green Ready pulse live in
  `LobbyRuntime`'s status pulse plus `DeviceLinkAgent._bases`.
- **G2. Done:** `/<dev>/deny` flashes red x2 via `LobbyRuntime.on_deny`.
- **G3. Done:** Start accepted is green x2 (`FEEDBACK_ACCEPT` and
  `_flash_fixtures_now`).
- **G4. Decided 2026-10-08; firmware open.** Looking after 3 s of silence,
  Solo at 15 s, for beat-capable devices (spec
  `docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md`).
  Rev 1 firmware still has to render both (mm-devshroom).

The remaining G4 work is firmware (mm-devshroom).

## 7. Adding a signal

1. Check sections 2 and 3: does an existing signal already mean this? Use it.
2. Pick a pattern no row above uses (a new count or a new colour).
3. Add the row here, with *Today* set to "Not yet", in the same PR as, or
   before, the code that renders it.
4. If Control renders it to devices, the recorded frames change: re-record
   the contract scenarios and re-export (device contract guide, section 1).
