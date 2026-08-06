# Student Hardware Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For human workers (the two students):** this plan is written for you. Tasks
> tagged **[SW]** are software and follow a strict test-first cycle. Tasks tagged
> **[HW]** are physical builds and follow a verification protocol instead: there
> is no failing test to write, so the equivalent discipline is *measure before
> you connect, and record the measurement*. Tasks tagged **[PROC]** are
> procurement and belong to Chris.

**Goal:** Build two complete, interchangeable Terrarium hardware sets (venue box,
LED array, two Tuneshrooms each) proven end to end on the existing `devicelink`
wire, finishing four weeks before the Nov 13 hardware deadline.

**Architecture:** Two independent lanes joined by three gates. Lane A builds the
venue side (Pi 5 box, PCM5122 audio, multi-universe Art-Net, the LED arrays).
Lane B builds the instrument side (Radxa bring-up, I2S full duplex, sensors,
Tuneshroom assembly). All device traffic uses `devicelink` JSON-over-websocket;
o2lite is attempted only in a bounded window after hardware is already complete.

**Tech Stack:** Python 3.12+, pytest, `luxaeterna` (Art-Net to WLED renderer),
`mm-terrarium` (`control/`, `devicelink/`, `harness/`), Raspberry Pi OS on Pi 5,
Debian/Armbian on Radxa Zero 3W, WLED on ESP32, ALSA, device-tree overlays.

**Spec:** [`docs/superpowers/specs/2026-08-06-student-hardware-track-design.md`](../specs/2026-08-06-student-hardware-track-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **Two repos are in play.** Every file path below is prefixed with its repo:
  `luxaeterna/…` or `mm-terrarium/…`. They are separate checkouts and separate
  commits.
- **Test command, luxaeterna:** `python -m pytest tests -v`
- **Test command, mm-terrarium:** `python -m pip install -r requirements-dev.txt && python -m pytest tests -v`
- **Bare metal only.** No VMs, no WSL2, no NAT'd hosts. O2 discovery is UDP
  multicast and Art-Net is UDP broadcast; a virtual NIC on its own subnet
  receives neither.
- **The bench runs on its own AP and flat subnet**, never campus wireless.
  Campus client isolation and multicast filtering will present as a hardware
  bug for a week before anyone suspects the network.
- **Array electrical limits are absolute.** 864 SK6812 RGBW pixels at full white
  draw **21.6 A**. The Mean Well LRS-150-12 supplies **12.5 A**. The software
  budget is **10.0 A (80% of supply)**. The array is never driven white without
  the limiter of Task A8 in the path.
- **Trim every MT3608 to 5.0 V on a bench supply, verified with a meter, before
  it is connected to any board.** An untrimmed MT3608 destroys a Radxa.
- **Any timing figure must be measured on the target hardware.** The Pi 5 relays
  every hop through the same process doing room synthesis while feeding a 44 Hz
  render loop. Figures measured anywhere else do not transfer.
- **Colour order on the Tuneshroom wire is GRB (3 channels), on the array it is
  RGBW (4 channels).** Do not assume either.
- **Commit after every green test.** Small commits, present-tense messages.

---

# Phase 0 — Procurement [PROC]

**Owner: Chris. Window: 2026-08-06 to 2026-08-21. Must complete before students
arrive.**

## Task 0.1: ETC bench tooling inventory

**Files:**
- Create: `mm-terrarium/docs/hardware/bench-inventory.md`

**Interfaces:**
- Produces: the confirmed gap list that Task 0.2's Wave 1 order must close.

- [ ] **Step 1: Contact ETC and confirm what exists on the bench**

Confirm presence and working condition of each item. Record model numbers.

| Item | Needed for | Present? |
|---|---|---|
| Temperature-controlled soldering station | ~30 terminations per Tuneshroom | |
| Hot air rework station | SK6812 strip repair | |
| Bench DC power supply, 0-30 V, current-limited | **MT3608 trimming.** Non-negotiable. | |
| Digital multimeter | MT3608 trim verification, continuity | |
| Oscilloscope (≥20 MHz) | I2S clock debug, SK6812 800 kHz data line | |
| Wire strippers, crimpers, ferrule kit | harnesses | |
| ESD mat and strap | Radxa and Pi handling | |
| 3D printer access | enclosure iteration | |
| Silicone casting supplies and vacuum degasser | Tuneshroom bodies | |

- [ ] **Step 2: Write the inventory file recording what is present, what is missing, and model numbers for what is present**

- [ ] **Step 3: Add every missing item to the Wave 1 order in Task 0.2**

The current-limited bench supply is the one item that blocks work if absent. If
ETC does not have one, it is the highest-priority line in Wave 1.

- [ ] **Step 4: Commit**

```bash
git add docs/hardware/bench-inventory.md
git commit -m "docs(hardware): ETC bench tooling inventory and gap list"
```

## Task 0.2: Wave 1 order (bench-critical)

**Files:**
- Create: `mm-terrarium/docs/hardware/bom-wave1.md`

**Interfaces:**
- Produces: parts physically present at the ETC bench on 2026-08-24.

- [ ] **Step 1: Write the order list**

| Part | Qty | Note |
|---|---|---|
| Radxa Zero 3W 2 GB | 5 | 4 builds + 1 test mule. Most volatile availability line. |
| Raspberry Pi 5 8 GB | 2 | |
| Official Pi 5 27 W USB-C PSU | 2 | |
| microSD / NVMe storage | 4 | |
| **PCM5122 I2S DAC HAT** | 3 | |
| **Alternative DAC candidate (different chipset)** | 1 | See Step 2. |
| WLED ESP32 controller (GLEDOPTO class) | 3 | 2 for the full array, 1 bench |
| 5 V SK6812 RGBW strip, short (~1 m) | 1 | first-light rig |
| Wireless AP + 5-port switch | 1 ea | **dedicated bench subnet** |
| LIS3DH breakout | 6 | |
| INMP441 breakout | 6 | |
| MAX98357A breakout | 6 | |
| 50 mm 4Ω 3 W full-range driver | 6 | |
| 5 V SK6812 Mini RGBW, 12-px cap+stem sets | 6 | |
| 18650 3500 mAh cell | 8 | |
| 18650 holder | 8 | |
| MT3608 boost module | 8 | |
| TP4056 charge module | 8 | |
| Silicone hookup wire, JST connectors, heatshrink, ferrules, solder, flux | lot | |
| Anything flagged missing in Task 0.1 | | |

Approximate total: **$1,000.**

- [ ] **Step 2: Order two different DAC chipsets, not two of the same**

The Pi 5 has **no analog audio output at all**. If the PCM5122 does not work on
Pi 5, there is no audio path and no fallback. Ordering the alternative now costs
$25; discovering the problem in September costs two weeks. This is the single
highest-leverage line in the order.

- [ ] **Step 3: Place the order with the ETC bench as the ship-to address**

- [ ] **Step 4: Record order numbers and expected arrival dates in the BOM file**

- [ ] **Step 5: Commit**

```bash
git add docs/hardware/bom-wave1.md
git commit -m "docs(hardware): Wave 1 bench-critical BOM and order record"
```

## Task 0.3: Wave 2 order (bulky, long-lead)

**Files:**
- Create: `mm-terrarium/docs/hardware/bom-wave2.md`

- [ ] **Step 1: Write the order list**

| Part | Qty | Note |
|---|---|---|
| **12 V SK6812 RGBW 144/m, PER-LED addressable** | 8 m | 6 m array + 2 m spare. See Step 2. |
| Muzata Spotless LED channel + diffuser | 6 m | |
| Mean Well LRS-150-12 (12.5 A) | 1 | full array |
| 12 V 5 A PSU | 1 | bench array |
| 12 V SK6812 RGBW 144/m, per-LED | 2 m | bench twin array |
| Fiber optic end-glow bundle, 100-200 strand @ 0.75 mm | 3 | |
| RGBW fiber engine | 3 | |
| Powered studio monitors | 1 pr | line-out for bench and dry runs |
| Inline fuse holders + fuses | 6 | injection points |
| DC distribution blocks | 2 | |
| 14-16 AWG silicone wire, red/black | 20 m ea | power injection |

Approximate total: **$800 to $1,300.**

- [ ] **Step 2: Verify the LED strip is the PER-LED variant before ordering**

12 V SK6812 ships in two variants. The **grouped** variant puts one IC per three
LEDs, which turns a 6 m 144/m run into **288 controllable pixels instead of 864**
and silently guts the canvas.

Confirm the phrase "addressable per LED" or equivalent appears in the **listing
body**, not just the title. Cross-check against `MM_HARDWARE_DESIGN.md` §9.2,
which gives the exact distinction and notes that **WS2814 can never do per-LED at
12 V** and must not be substituted.

- [ ] **Step 3: Place the order, ship to ETC**

- [ ] **Step 4: Record order numbers, expected arrival, and the confirmed strip variant in the BOM file**

- [ ] **Step 5: Commit**

```bash
git add docs/hardware/bom-wave2.md
git commit -m "docs(hardware): Wave 2 long-lead BOM and order record"
```

---

# Phase A — Lane A, Venue

## Task A1 [SW]: Give `artnet.py` its first tests, and fix the length-field defect

**Files:**
- Create: `luxaeterna/tests/backends/test_artnet.py`
- Modify: `luxaeterna/luxaeterna/backends/artnet.py:64-74`

**Interfaces:**
- Consumes: `ArtNet(host, port)`, `.open()`, `.close()`, `.send(frame, universe_id)`, `.is_open` (existing).
- Produces: `ArtNet._build_packet` with a **truthful length field**. Task A5 relies on `send()` accepting frames of exactly `DMX_CHANNELS` bytes and encoding `universe_id` little-endian.

**Context:** `luxaeterna/backends/artnet.py` has zero tests and has never been
instantiated against hardware. `_build_packet` hardcodes the Art-Net length field
to `DMX_CHANNELS` (512) regardless of the frame handed to it, so a short frame
produces a packet whose header lies about its payload.

- [ ] **Step 1: Write the failing test file**

```python
"""Art-Net III backend: packet construction, socket lifecycle, error paths."""

from __future__ import annotations

import socket
import struct

import pytest

from luxaeterna.backends.artnet import ArtNet
from luxaeterna.constants import (
    ARTNET_HEADER,
    ARTNET_OPCODE_DMX,
    ARTNET_PORT,
    ARTNET_PROTOCOL_VERSION,
    DMX_CHANNELS,
)
from luxaeterna.exceptions import BackendError


class FakeSocket:
    """Stands in for a UDP socket; records everything the backend does to it."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.opts: list[tuple[int, int, int]] = []
        self.blocking: bool | None = None
        self.closed = False
        self.raise_on_send: OSError | None = None

    def setsockopt(self, level, opt, value):
        self.opts.append((level, opt, value))

    def setblocking(self, flag):
        self.blocking = flag

    def sendto(self, data, addr):
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append((bytes(data), addr))

    def close(self):
        self.closed = True


@pytest.fixture
def sockets(monkeypatch):
    """Replace socket.socket with a factory recording every FakeSocket made."""
    made: list[FakeSocket] = []

    def factory(family, type_):
        assert family == socket.AF_INET
        assert type_ == socket.SOCK_DGRAM
        s = FakeSocket()
        made.append(s)
        return s

    monkeypatch.setattr(socket, "socket", factory)
    return made


def full_frame(value: int = 0) -> bytearray:
    return bytearray([value]) * DMX_CHANNELS


# --- lifecycle ---

def test_send_before_open_raises_backend_error(sockets):
    a = ArtNet()
    with pytest.raises(BackendError, match="not open"):
        a.send(full_frame())


def test_open_sets_broadcast_and_nonblocking(sockets):
    a = ArtNet()
    a.open()
    assert a.is_open is True
    s = sockets[0]
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST, 1) in s.opts
    assert s.blocking is False


def test_open_is_idempotent(sockets):
    a = ArtNet()
    a.open()
    a.open()
    assert len(sockets) == 1


def test_close_is_idempotent_and_clears_is_open(sockets):
    a = ArtNet()
    a.open()
    a.close()
    a.close()
    assert a.is_open is False
    assert sockets[0].closed is True


def test_context_manager_opens_and_closes(sockets):
    with ArtNet() as a:
        assert a.is_open is True
    assert a.is_open is False


# --- packet structure ---

def test_packet_starts_with_header_opcode_and_version(sockets):
    a = ArtNet()
    a.open()
    a.send(full_frame())
    packet, _ = sockets[0].sent[0]
    assert packet[0:8] == ARTNET_HEADER
    assert struct.unpack("<H", packet[8:10])[0] == ARTNET_OPCODE_DMX
    assert struct.unpack(">H", packet[10:12])[0] == ARTNET_PROTOCOL_VERSION


def test_universe_is_little_endian_at_offset_14(sockets):
    a = ArtNet()
    a.open()
    a.send(full_frame(), universe_id=6)
    packet, _ = sockets[0].sent[0]
    assert struct.unpack("<H", packet[14:16])[0] == 6


def test_length_field_is_big_endian_and_matches_frame_length(sockets):
    a = ArtNet()
    a.open()
    a.send(full_frame())
    packet, _ = sockets[0].sent[0]
    assert struct.unpack(">H", packet[16:18])[0] == DMX_CHANNELS
    assert len(packet) == 18 + DMX_CHANNELS


def test_short_frame_length_field_tells_the_truth(sockets):
    """The defect this task fixes: the header used to claim 512 regardless."""
    a = ArtNet()
    a.open()
    a.send(bytearray(64))
    packet, _ = sockets[0].sent[0]
    assert struct.unpack(">H", packet[16:18])[0] == 64
    assert len(packet) == 18 + 64


def test_odd_length_frame_is_rejected(sockets):
    """Art-Net requires an even data length in 2..512."""
    a = ArtNet()
    a.open()
    with pytest.raises(BackendError, match="even"):
        a.send(bytearray(63))


def test_oversized_frame_is_rejected(sockets):
    a = ArtNet()
    a.open()
    with pytest.raises(BackendError, match="length"):
        a.send(bytearray(DMX_CHANNELS + 2))


def test_empty_frame_is_rejected(sockets):
    a = ArtNet()
    a.open()
    with pytest.raises(BackendError, match="length"):
        a.send(bytearray(0))


# --- sequence ---

def test_sequence_increments_per_send(sockets):
    a = ArtNet()
    a.open()
    a.send(full_frame())
    a.send(full_frame())
    first, _ = sockets[0].sent[0]
    second, _ = sockets[0].sent[1]
    assert first[12] == 1
    assert second[12] == 2


def test_sequence_wraps_at_256(sockets):
    a = ArtNet()
    a.open()
    for _ in range(255):
        a.send(full_frame())
    a.send(full_frame())
    last, _ = sockets[0].sent[-1]
    assert last[12] == 0


def test_physical_byte_is_zero(sockets):
    a = ArtNet()
    a.open()
    a.send(full_frame())
    packet, _ = sockets[0].sent[0]
    assert packet[13] == 0


# --- addressing and safety ---

def test_defaults_to_broadcast_on_artnet_port(sockets):
    a = ArtNet()
    a.open()
    a.send(full_frame())
    _, addr = sockets[0].sent[0]
    assert addr == ("255.255.255.255", ARTNET_PORT)


def test_unicast_host_is_honoured(sockets):
    a = ArtNet(host="10.0.0.42")
    a.open()
    a.send(full_frame())
    _, addr = sockets[0].sent[0]
    assert addr == ("10.0.0.42", ARTNET_PORT)


def test_send_does_not_mutate_the_caller_frame(sockets):
    a = ArtNet()
    a.open()
    frame = full_frame(7)
    a.send(frame)
    assert frame == bytearray([7]) * DMX_CHANNELS


def test_os_error_becomes_backend_error(sockets):
    a = ArtNet()
    a.open()
    sockets[0].raise_on_send = OSError("network unreachable")
    with pytest.raises(BackendError, match="send failed"):
        a.send(full_frame())
```

- [ ] **Step 2: Run the tests to see which fail**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/backends/test_artnet.py -v`

Expected: most pass, and these four **FAIL**:
`test_short_frame_length_field_tells_the_truth`,
`test_odd_length_frame_is_rejected`,
`test_oversized_frame_is_rejected`,
`test_empty_frame_is_rejected`.

That is the defect. Do not proceed until you have seen these four fail.

- [ ] **Step 3: Fix `_build_packet` and add validation to `send`**

Replace lines 49-74 of `luxaeterna/luxaeterna/backends/artnet.py`:

```python
    def send(self, frame: bytearray, universe_id: int = 0) -> None:
        if self._sock is None:
            raise BackendError("Art-Net socket not open")

        length = len(frame)
        if length < 2 or length > DMX_CHANNELS:
            raise BackendError(
                f"Art-Net frame length {length} outside 2-{DMX_CHANNELS}")
        if length % 2 != 0:
            raise BackendError(
                f"Art-Net frame length {length} must be even")

        self._sequence = (self._sequence + 1) % 256
        packet = self._build_packet(frame, universe_id)
        try:
            self._sock.sendto(packet, (self.host, self.port))
        except OSError as exc:
            raise BackendError(f"Art-Net send failed: {exc}") from exc

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def _build_packet(self, frame: bytearray, universe: int) -> bytes:
        """Construct an ArtDmx packet (opcode 0x5000).

        The length field reports the *actual* payload length. Art-Net III
        permits any even length in 2..512; reporting a fixed 512 for a short
        frame makes the header lie about the payload, which some nodes accept
        and others drop silently.
        """
        return (
            ARTNET_HEADER
            + struct.pack("<H", ARTNET_OPCODE_DMX)
            + struct.pack(">H", ARTNET_PROTOCOL_VERSION)
            + bytes([self._sequence, 0])          # sequence, physical
            + struct.pack("<H", universe)          # universe (low byte first)
            + struct.pack(">H", len(frame))       # length (high byte first)
            + bytes(frame)
        )
```

- [ ] **Step 4: Run the full luxaeterna suite**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests -v`
Expected: all PASS, including the four that previously failed.

- [ ] **Step 5: Commit**

```bash
cd /Users/chris/projects/luxaeterna
git add tests/backends/test_artnet.py luxaeterna/backends/artnet.py
git commit -m "test(artnet): first test suite; fix length field to report actual payload length"
```

## Task A2 [HW]: First light — real LEDs from Lux Aeterna

**Files:**
- Create: `mm-terrarium/docs/hardware/first-light.md`

**Interfaces:**
- Consumes: `ArtNet` from Task A1.
- Produces: a known-good WLED controller address recorded in `first-light.md`, used by every later Lane A task.

**Verification protocol (there is no failing test here; the LEDs are the assertion).**

- [ ] **Step 1: Stand up the bench network before touching any LED**

Configure the dedicated AP with a flat subnet (suggested `10.44.0.0/24`, chosen
so the third octet reads as the frame rate and is memorable). No client
isolation. No guest mode. Record the SSID, subnet, and DHCP range.

Verify broadcast reaches the subnet:

```bash
ping -b 10.44.0.255
```

If the AP drops broadcast, nothing downstream in Lane A will ever work. Fix it
here.

- [ ] **Step 2: Flash WLED to the ESP32 and attach the 1 m 5 V SK6812 strip**

Set in WLED: LED type SK6812 RGBW, correct count, **Art-Net / E1.31 enabled,
universe 0, start channel 1, DMX mode "Multi RGBW"**. Give the controller a DHCP
reservation and record the IP.

- [ ] **Step 3: Confirm the strip lights from the WLED web UI before involving any Python**

If it does not light here, the fault is wiring or WLED config, not luxaeterna.
Do not proceed past this step with a dark strip.

- [ ] **Step 4: Drive it from Lux Aeterna**

```bash
cd /Users/chris/projects/luxaeterna
python - <<'PY'
import time
from luxaeterna.backends.artnet import ArtNet
from luxaeterna.universe import Universe
from luxaeterna.output import OutputLoop

WLED_IP = "10.44.0.50"   # replace with the reserved address from Step 2

u = Universe(universe_id=0)
loop = OutputLoop(u, ArtNet(host=WLED_IP), always_send=True)
loop.start()
try:
    for i in range(0, 60):          # 15 pixels x RGBW
        u.set(i, 0)
    while True:
        for ch, name in ((0, "red"), (1, "green"), (2, "blue"), (3, "white")):
            u.fill(0, 0, 60)
            for px in range(15):
                u.set(px * 4 + ch, 120)
            print(name, "fps", round(loop.fps, 1))
            time.sleep(1.5)
except KeyboardInterrupt:
    pass
finally:
    loop.stop()
PY
```

- [ ] **Step 5: Record the result**

Write `first-light.md` capturing: AP SSID and subnet, WLED controller IP and
reservation, WLED LED-type and DMX-mode settings, the observed FPS from the
script, and a photo. Note explicitly whether the white channel lit separately
from R, G, and B, since that confirms the strip is genuinely RGBW and not RGB.

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/mm-terrarium
git add docs/hardware/first-light.md
git commit -m "docs(hardware): first light record, bench network and WLED config"
```

## Task A3 [HW]: Venue box #1 and the PCM5122 audio path

**Files:**
- Create: `mm-terrarium/docs/hardware/venue-box-runbook.md`

**Interfaces:**
- Produces: a booting Pi 5 with a **verified analog line-out**, and the runbook Task A6 repeats verbatim to build box #2.

**This task answers the highest-risk procurement question in the plan.** The Pi 5
has no analog audio output. If the PCM5122 does not work, there is no audio path
at all, and the alternative DAC from Task 0.2 Step 2 gets tried immediately.

- [ ] **Step 1: Assemble and boot**

Pi 5 8 GB, storage, official 27 W PSU, PCM5122 DAC HAT seated on the 40-pin
header. Install 64-bit Raspberry Pi OS. Record the OS image version in the
runbook.

- [ ] **Step 2: Enable the DAC overlay**

Add to `/boot/firmware/config.txt`:

```
dtparam=audio=off
dtoverlay=hifiberry-dac
```

Reboot.

- [ ] **Step 3: Confirm ALSA sees the card**

```bash
aplay -l
```

Expected: a card whose name contains `snd_rpi_hifiberry_dac` or similar. If no
card appears, try `dtoverlay=hifiberry-dacplus`, then `allo-boss-dac-pcm512x-audio`.
If none of the three enumerate, **stop and switch to the alternative DAC**. Record
every overlay attempted and its result.

- [ ] **Step 4: Prove audio actually comes out**

```bash
speaker-test -D hw:0,0 -c 2 -t sine -f 440 -l 3
```

Listen on the powered monitors. Then confirm a real file plays:

```bash
aplay -D hw:0,0 /usr/share/sounds/alsa/Front_Center.wav
```

- [ ] **Step 5: Measure output level and check for defects**

With the sine test running, confirm at the monitors: no hum, no clipping, both
channels present. Record the observed behaviour. A DAC that enumerates but
outputs silence or hum is a failure, not a pass.

- [ ] **Step 6: Write the runbook**

`venue-box-runbook.md` must be sufficient for someone who did not build this box
to build the next one: OS image version and download URL, exact `config.txt`
lines, the overlay that worked and the ones that did not, the `aplay -l` output,
and the verification commands from Steps 4 and 5.

- [ ] **Step 7: Commit**

```bash
git add docs/hardware/venue-box-runbook.md
git commit -m "docs(hardware): venue box runbook, PCM5122 audio path verified"
```

## Task A4 [SW]: `PixelSpan` — map a pixel strip across DMX universes

**Files:**
- Create: `luxaeterna/luxaeterna/pixelspan.py`
- Create: `luxaeterna/tests/test_pixelspan.py`

**Interfaces:**
- Produces, consumed by Task A5:
  - `PixelSpan(pixel_count: int, channels_per_pixel: int = 4, start_universe: int = 0)`
  - `.channel_count -> int`
  - `.pixels_per_universe -> int`
  - `.universe_count -> int`
  - `.universe_ids -> list[int]`
  - `.locate(pixel_index: int) -> tuple[int, int]` returning `(universe_id, channel_offset)`
  - `.slice_for(universe_id: int) -> tuple[int, int]` returning `(first_pixel, pixel_count)` in that universe
  - Raises `ChannelError` (from `luxaeterna.exceptions`) on out-of-range input.

**Context:** the Terrarium array is 864 pixels x 4 channels = 3456 channels, which
is 6.75 universes, so 7 universes must be addressed. `ArtNet.send()` sends one
universe per call and `Universe` holds exactly 512 channels, so nothing today
spans that boundary.

**Design note to preserve:** at 4 channels per pixel, 512 / 4 = 128 pixels per
universe **exactly**, so an RGBW pixel never straddles a universe boundary. This
is a property worth a test, because it is what keeps the mapping arithmetic
trivial. Three-channel (RGB) strips do straddle, and supporting them is
deliberately out of scope: `channels_per_pixel` must divide 512 evenly or
construction raises.

- [ ] **Step 1: Write the failing test file**

```python
"""PixelSpan: mapping a logical pixel strip across consecutive DMX universes."""

from __future__ import annotations

import pytest

from luxaeterna.exceptions import ChannelError
from luxaeterna.pixelspan import PixelSpan

TERRARIUM = 864          # 6 m at 144 px/m
SHROOM = 12


def test_terrarium_array_needs_seven_universes():
    span = PixelSpan(TERRARIUM)
    assert span.channel_count == 3456
    assert span.pixels_per_universe == 128
    assert span.universe_count == 7
    assert span.universe_ids == [0, 1, 2, 3, 4, 5, 6]


def test_a_small_span_fits_in_one_universe():
    span = PixelSpan(SHROOM)
    assert span.channel_count == 48
    assert span.universe_count == 1
    assert span.universe_ids == [0]


def test_exact_multiple_does_not_allocate_a_spare_universe():
    span = PixelSpan(256)          # exactly 2 universes
    assert span.universe_count == 2


def test_start_universe_offsets_the_ids():
    span = PixelSpan(TERRARIUM, start_universe=10)
    assert span.universe_ids == [10, 11, 12, 13, 14, 15, 16]


# --- locate ---

def test_locate_first_pixel():
    assert PixelSpan(TERRARIUM).locate(0) == (0, 0)


def test_locate_last_pixel_of_first_universe():
    assert PixelSpan(TERRARIUM).locate(127) == (0, 508)


def test_locate_first_pixel_of_second_universe():
    assert PixelSpan(TERRARIUM).locate(128) == (1, 0)


def test_locate_last_pixel_of_the_array():
    # 863 // 128 == 6, remainder 95, 95 * 4 == 380
    assert PixelSpan(TERRARIUM).locate(863) == (6, 380)


def test_locate_honours_start_universe():
    assert PixelSpan(TERRARIUM, start_universe=10).locate(128) == (11, 0)


def test_locate_rejects_negative_pixel():
    with pytest.raises(ChannelError):
        PixelSpan(TERRARIUM).locate(-1)


def test_locate_rejects_pixel_past_the_end():
    with pytest.raises(ChannelError):
        PixelSpan(TERRARIUM).locate(864)


# --- slice_for ---

def test_slice_for_a_full_universe():
    assert PixelSpan(TERRARIUM).slice_for(0) == (0, 128)


def test_slice_for_the_partial_last_universe():
    # 864 - 6 * 128 == 96 pixels in the final universe
    assert PixelSpan(TERRARIUM).slice_for(6) == (768, 96)


def test_slice_for_honours_start_universe():
    assert PixelSpan(TERRARIUM, start_universe=10).slice_for(16) == (768, 96)


def test_slice_for_rejects_a_universe_outside_the_span():
    with pytest.raises(ChannelError):
        PixelSpan(TERRARIUM).slice_for(7)


# --- construction guards ---

def test_channels_per_pixel_must_divide_a_universe_evenly():
    """RGB (3 ch) straddles universe boundaries; out of scope by decision."""
    with pytest.raises(ChannelError, match="divide"):
        PixelSpan(TERRARIUM, channels_per_pixel=3)


def test_pixel_count_must_be_positive():
    with pytest.raises(ChannelError):
        PixelSpan(0)


def test_no_pixel_ever_straddles_a_universe_boundary():
    """The property that keeps the arithmetic trivial. Guard it."""
    span = PixelSpan(TERRARIUM)
    for px in range(TERRARIUM):
        _, offset = span.locate(px)
        assert offset + span.channels_per_pixel <= 512
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/test_pixelspan.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'luxaeterna.pixelspan'`

- [ ] **Step 3: Write the implementation**

```python
"""Lux Aeterna — map a logical pixel strip across consecutive DMX universes.

A DMX universe is 512 channels. An RGBW pixel is 4 channels, and 512 / 4 = 128
exactly, so an RGBW pixel never straddles a universe boundary. That property is
what keeps this arithmetic trivial, and it is why ``channels_per_pixel`` must
divide 512 evenly. Three-channel RGB strips do straddle; supporting them is out
of scope by decision.
"""

from __future__ import annotations

from .constants import DMX_CHANNELS
from .exceptions import ChannelError


class PixelSpan:
    """A pixel strip laid out across one or more consecutive universes.

    Parameters
    ----------
    pixel_count : int
        Number of logical pixels in the strip.
    channels_per_pixel : int
        Channels each pixel occupies. Must divide 512 evenly (1, 2, 4, 8, …).
    start_universe : int
        DMX universe id of the first universe in the span.
    """

    __slots__ = ("pixel_count", "channels_per_pixel", "start_universe")

    def __init__(self, pixel_count: int, channels_per_pixel: int = 4,
                 start_universe: int = 0) -> None:
        if pixel_count <= 0:
            raise ChannelError(f"pixel_count must be positive, got {pixel_count}")
        if channels_per_pixel <= 0:
            raise ChannelError(
                f"channels_per_pixel must be positive, got {channels_per_pixel}")
        if DMX_CHANNELS % channels_per_pixel != 0:
            raise ChannelError(
                f"channels_per_pixel {channels_per_pixel} must divide "
                f"{DMX_CHANNELS} evenly; pixels may not straddle universes")
        if start_universe < 0:
            raise ChannelError(f"start_universe must be >= 0, got {start_universe}")

        self.pixel_count = pixel_count
        self.channels_per_pixel = channels_per_pixel
        self.start_universe = start_universe

    @property
    def channel_count(self) -> int:
        return self.pixel_count * self.channels_per_pixel

    @property
    def pixels_per_universe(self) -> int:
        return DMX_CHANNELS // self.channels_per_pixel

    @property
    def universe_count(self) -> int:
        per = self.pixels_per_universe
        return (self.pixel_count + per - 1) // per

    @property
    def universe_ids(self) -> list[int]:
        return [self.start_universe + i for i in range(self.universe_count)]

    def locate(self, pixel_index: int) -> tuple[int, int]:
        """Return ``(universe_id, channel_offset)`` for *pixel_index*."""
        if not (0 <= pixel_index < self.pixel_count):
            raise ChannelError(
                f"Pixel {pixel_index} out of range 0-{self.pixel_count - 1}")
        per = self.pixels_per_universe
        universe_offset, within = divmod(pixel_index, per)
        return (self.start_universe + universe_offset,
                within * self.channels_per_pixel)

    def slice_for(self, universe_id: int) -> tuple[int, int]:
        """Return ``(first_pixel, pixel_count)`` carried by *universe_id*."""
        offset = universe_id - self.start_universe
        if not (0 <= offset < self.universe_count):
            raise ChannelError(
                f"Universe {universe_id} outside span "
                f"{self.start_universe}-{self.start_universe + self.universe_count - 1}")
        per = self.pixels_per_universe
        first = offset * per
        return first, min(per, self.pixel_count - first)

    def __repr__(self) -> str:
        return (f"PixelSpan(pixel_count={self.pixel_count}, "
                f"channels_per_pixel={self.channels_per_pixel}, "
                f"start_universe={self.start_universe})")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/test_pixelspan.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite for regressions**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/luxaeterna
git add luxaeterna/pixelspan.py tests/test_pixelspan.py
git commit -m "feat(pixelspan): map a pixel strip across consecutive DMX universes"
```

## Task A5 [SW]: `UniverseSet` and `MultiUniverseOutputLoop`

**Files:**
- Create: `luxaeterna/luxaeterna/universeset.py`
- Create: `luxaeterna/tests/test_universeset.py`

**Interfaces:**
- Consumes: `PixelSpan` (Task A4), `Universe`, `OutputLoop`, `DMXBackend`.
- Produces, consumed by Tasks A8 and A10:
  - `UniverseSet(span: PixelSpan)` with `.universes -> list[Universe]`,
    `.set_pixels(values: bytes | bytearray) -> None`,
    `.fill_pixel(pixel_index: int, values: bytes) -> None`,
    `.frames() -> list[tuple[int, bytearray]]`, `.reset() -> None`
  - `MultiUniverseOutputLoop(universe_set, backend, frame_rate=44.0, on_frame=None, on_error=None, always_send=True)`
    with `.start()`, `.stop(timeout=2.0)`, `.running`, `.fps`, `._loop_once() -> int`

**Context:** `OutputLoop` drives exactly one `Universe` on its own thread. Seven
universes must not become seven threads racing each other; one loop sends all
seven per tick so the array stays frame-coherent.

`_loop_once()` returns the **number of universes sent**, mirroring `OutputLoop._loop_once`'s
"factored out so tests can drive one deterministic tick" pattern.

- [ ] **Step 1: Write the failing test file**

```python
"""UniverseSet: a PixelSpan's universes written and sent as one coherent frame."""

from __future__ import annotations

import pytest

from luxaeterna.backends.base import DMXBackend
from luxaeterna.constants import DMX_CHANNELS
from luxaeterna.exceptions import ChannelError
from luxaeterna.pixelspan import PixelSpan
from luxaeterna.universeset import MultiUniverseOutputLoop, UniverseSet

TERRARIUM = 864


class RecordingBackend(DMXBackend):
    def __init__(self) -> None:
        self.sent: list[tuple[int, bytes]] = []
        self._open = False
        self.fail_on_universe: int | None = None

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def send(self, frame, universe_id: int = 0) -> None:
        if self.fail_on_universe == universe_id:
            raise RuntimeError(f"universe {universe_id} exploded")
        self.sent.append((universe_id, bytes(frame)))

    @property
    def is_open(self) -> bool:
        return self._open


# --- UniverseSet ---

def test_allocates_one_universe_per_span_universe():
    us = UniverseSet(PixelSpan(TERRARIUM))
    assert len(us.universes) == 7
    assert [u.universe_id for u in us.universes] == [0, 1, 2, 3, 4, 5, 6]


def test_honours_start_universe():
    us = UniverseSet(PixelSpan(TERRARIUM, start_universe=10))
    assert [u.universe_id for u in us.universes] == [10, 11, 12, 13, 14, 15, 16]


def test_set_pixels_writes_across_the_universe_boundary():
    us = UniverseSet(PixelSpan(TERRARIUM))
    values = bytearray(3456)
    values[508:516] = bytes([1, 2, 3, 4, 5, 6, 7, 8])   # pixel 127 then 128
    us.set_pixels(values)
    assert us.universes[0].get(508) == 1
    assert us.universes[0].get(511) == 4
    assert us.universes[1].get(0) == 5
    assert us.universes[1].get(3) == 8


def test_set_pixels_zero_fills_the_tail_of_the_last_universe():
    us = UniverseSet(PixelSpan(TERRARIUM))
    us.set_pixels(bytearray([255]) * 3456)
    last = us.universes[6]
    assert last.get(383) == 255          # final real channel
    assert last.get(384) == 0            # padding begins
    assert last.get(511) == 0


def test_set_pixels_rejects_a_wrong_length_buffer():
    us = UniverseSet(PixelSpan(TERRARIUM))
    with pytest.raises(ChannelError, match="3456"):
        us.set_pixels(bytearray(3455))


def test_fill_pixel_writes_one_pixel_in_the_right_universe():
    us = UniverseSet(PixelSpan(TERRARIUM))
    us.fill_pixel(863, bytes([9, 8, 7, 6]))
    assert us.universes[6].get(380) == 9
    assert us.universes[6].get(383) == 6


def test_fill_pixel_rejects_wrong_channel_count():
    us = UniverseSet(PixelSpan(TERRARIUM))
    with pytest.raises(ChannelError):
        us.fill_pixel(0, bytes([1, 2, 3]))


def test_frames_returns_one_full_dmx_frame_per_universe():
    us = UniverseSet(PixelSpan(TERRARIUM))
    frames = us.frames()
    assert len(frames) == 7
    assert [uid for uid, _ in frames] == [0, 1, 2, 3, 4, 5, 6]
    assert all(len(f) == DMX_CHANNELS for _, f in frames)


def test_reset_zeroes_every_universe():
    us = UniverseSet(PixelSpan(TERRARIUM))
    us.set_pixels(bytearray([200]) * 3456)
    us.reset()
    assert all(u.get(0) == 0 for u in us.universes)


# --- MultiUniverseOutputLoop ---

def test_one_tick_sends_every_universe_exactly_once():
    us = UniverseSet(PixelSpan(TERRARIUM))
    backend = RecordingBackend()
    loop = MultiUniverseOutputLoop(us, backend)
    backend.open()
    assert loop._loop_once() == 7
    assert [uid for uid, _ in backend.sent] == [0, 1, 2, 3, 4, 5, 6]


def test_on_frame_hook_runs_before_the_send():
    us = UniverseSet(PixelSpan(TERRARIUM))
    backend = RecordingBackend()
    seen: list[int] = []

    def paint(universe_set):
        seen.append(len(backend.sent))
        universe_set.fill_pixel(0, bytes([1, 2, 3, 4]))

    loop = MultiUniverseOutputLoop(us, backend, on_frame=paint)
    backend.open()
    loop._loop_once()
    assert seen == [0]                       # hook ran before anything was sent
    assert backend.sent[0][1][0] == 1        # and its paint reached the wire


def test_a_failing_universe_does_not_stop_the_others():
    us = UniverseSet(PixelSpan(TERRARIUM))
    backend = RecordingBackend()
    backend.fail_on_universe = 3
    errors: list[Exception] = []
    loop = MultiUniverseOutputLoop(us, backend, on_error=errors.append)
    backend.open()
    assert loop._loop_once() == 6
    assert [uid for uid, _ in backend.sent] == [0, 1, 2, 4, 5, 6]
    assert len(errors) == 1


def test_a_failing_on_frame_hook_does_not_stop_the_send():
    us = UniverseSet(PixelSpan(TERRARIUM))
    backend = RecordingBackend()
    errors: list[Exception] = []

    def boom(_):
        raise RuntimeError("paint failed")

    loop = MultiUniverseOutputLoop(us, backend, on_frame=boom, on_error=errors.append)
    backend.open()
    assert loop._loop_once() == 7
    assert len(errors) == 1


def test_start_opens_the_backend_and_stop_closes_it():
    us = UniverseSet(PixelSpan(TERRARIUM))
    backend = RecordingBackend()
    loop = MultiUniverseOutputLoop(us, backend)
    loop.start()
    assert backend.is_open is True
    assert loop.running is True
    loop.stop()
    assert backend.is_open is False
    assert loop.running is False


def test_start_is_idempotent():
    us = UniverseSet(PixelSpan(TERRARIUM))
    loop = MultiUniverseOutputLoop(us, RecordingBackend())
    loop.start()
    loop.start()
    loop.stop()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/test_universeset.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'luxaeterna.universeset'`

- [ ] **Step 3: Write the implementation**

```python
"""Lux Aeterna — a PixelSpan's universes, written and sent as one coherent frame.

``OutputLoop`` drives exactly one ``Universe`` on its own thread. An 864-pixel
array needs seven, and seven independent threads would tear the array: universe 0
of frame N could reach the wire alongside universe 6 of frame N-1.
``MultiUniverseOutputLoop`` sends all of them from one thread per tick instead.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .backends.base import DMXBackend
from .constants import DMX_CHANNELS, DMX_REFRESH_HZ
from .exceptions import ChannelError
from .logutil import ThrottledLog
from .pixelspan import PixelSpan
from .universe import Universe

log = logging.getLogger(__name__)


class UniverseSet:
    """The universes backing one :class:`PixelSpan`."""

    __slots__ = ("span", "universes")

    def __init__(self, span: PixelSpan) -> None:
        self.span = span
        self.universes = [Universe(universe_id=uid) for uid in span.universe_ids]

    def _universe_at(self, universe_id: int) -> Universe:
        return self.universes[universe_id - self.span.start_universe]

    def set_pixels(self, values: bytes | bytearray) -> None:
        """Write the whole strip. *values* must be exactly ``channel_count`` long."""
        expected = self.span.channel_count
        if len(values) != expected:
            raise ChannelError(
                f"expected {expected} channels, got {len(values)}")
        per_universe = DMX_CHANNELS
        for index, universe in enumerate(self.universes):
            start = index * per_universe
            chunk = values[start:start + per_universe]
            universe.set_range(0, chunk)
            if len(chunk) < per_universe:
                universe.fill(0, len(chunk), per_universe - len(chunk))

    def fill_pixel(self, pixel_index: int, values: bytes) -> None:
        """Write a single pixel's channels."""
        if len(values) != self.span.channels_per_pixel:
            raise ChannelError(
                f"expected {self.span.channels_per_pixel} channels, got {len(values)}")
        universe_id, offset = self.span.locate(pixel_index)
        self._universe_at(universe_id).set_range(offset, values)

    def frames(self) -> list[tuple[int, bytearray]]:
        """Snapshot every universe as ``(universe_id, frame)``."""
        return [(u.universe_id, u.get_frame()) for u in self.universes]

    def reset(self) -> None:
        for universe in self.universes:
            universe.reset()

    def __repr__(self) -> str:
        return f"UniverseSet({self.span!r}, universes={len(self.universes)})"


class MultiUniverseOutputLoop:
    """Send every universe in a :class:`UniverseSet` once per tick, from one thread.

    Parameters mirror :class:`~luxaeterna.output.OutputLoop`, with two
    differences: ``on_frame`` receives the :class:`UniverseSet` rather than a
    single ``Universe``, and ``always_send`` defaults to ``True`` because a
    partially-dirty array must not send a partial frame.
    """

    def __init__(
        self,
        universe_set: UniverseSet,
        backend: DMXBackend,
        frame_rate: float = DMX_REFRESH_HZ,
        on_error: Callable[[Exception], None] | None = None,
        always_send: bool = True,
        on_frame: Callable[[UniverseSet], None] | None = None,
    ) -> None:
        self.universe_set = universe_set
        self.backend = backend
        self.frame_interval = 1.0 / frame_rate
        self.on_error = on_error
        self.always_send = always_send
        self.on_frame = on_frame

        self._throttle = ThrottledLog(log)
        self._running = False
        self._thread: threading.Thread | None = None
        self._fps: float = 0.0

    def start(self) -> None:
        if self._running:
            return
        self.backend.open()
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="luxaeterna-output-multi", daemon=True)
        self._thread.start()
        log.info("Multi-universe output started for %d universes",
                 len(self.universe_set.universes))

    def stop(self, timeout: float = 2.0) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        self.backend.close()
        log.info("Multi-universe output stopped")

    @property
    def running(self) -> bool:
        return self._running

    @property
    def fps(self) -> float:
        """Approximate full-array frames per second actually achieved."""
        return self._fps

    def _report(self, key: str, message: str, exc: Exception) -> None:
        if self.on_error:
            self.on_error(exc)
        else:
            self._throttle.log(key, logging.ERROR, "%s: %s", message, exc)

    def _loop_once(self) -> int:
        """Run one tick. Returns the number of universes actually sent."""
        if self.on_frame is not None:
            try:
                self.on_frame(self.universe_set)
            except Exception as exc:
                self._report("on_frame", "on_frame hook error", exc)

        sent = 0
        for universe_id, frame in self.universe_set.frames():
            if not self.always_send and not self.universe_set._universe_at(
                    universe_id).dirty:
                continue
            try:
                self.backend.send(frame, universe_id)
                sent += 1
            except Exception as exc:
                self._report(f"send:{universe_id}",
                             f"output error on universe {universe_id}", exc)
        return sent

    def _loop(self) -> None:
        interval = self.frame_interval
        frames = 0
        fps_clock = time.monotonic()

        while self._running:
            loop_start = time.monotonic()

            if self._loop_once():
                frames += 1

            now = time.monotonic()
            elapsed_fps = now - fps_clock
            if elapsed_fps >= 1.0:
                self._fps = frames / elapsed_fps
                frames = 0
                fps_clock = now

            sleep_time = interval - (now - loop_start)
            if sleep_time > 0:
                time.sleep(sleep_time)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/test_universeset.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/luxaeterna
git add luxaeterna/universeset.py tests/test_universeset.py
git commit -m "feat(universeset): frame-coherent multi-universe output for spanned pixel arrays"
```

## Task A6 [HW]: Venue box #2 from the runbook

**Files:**
- Modify: `mm-terrarium/docs/hardware/venue-box-runbook.md`

**Interfaces:**
- Consumes: the runbook from Task A3.
- Produces: a second, identical venue box, and a runbook proven to work in
  someone else's hands.

**This task's real deliverable is the runbook, not the box.** The build is the
test of the document.

- [ ] **Step 1: Swap builders**

Whoever did **not** write the runbook in Task A3 builds this box, following the
runbook only. No verbal help. Every question that has to be asked out loud is a
runbook defect.

- [ ] **Step 2: Build and verify using only the runbook's own commands**

Run the Task A3 Step 3 to Step 5 verifications on the new box. Record the
`aplay -l` output for box #2 alongside box #1's.

- [ ] **Step 3: Record every point where the runbook was insufficient**

- [ ] **Step 4: Amend the runbook to close each gap**

- [ ] **Step 5: Label both boxes physically**

`TERRARIUM-SHOW` and `TERRARIUM-BENCH`. These labels are load-bearing from Task
C2 onward, when the show set is sealed.

- [ ] **Step 6: Commit**

```bash
git add docs/hardware/venue-box-runbook.md
git commit -m "docs(hardware): runbook corrections from the box #2 build"
```

## Task A7 [HW]: Bench array (2 m) end to end from a venue box

**Files:**
- Create: `mm-terrarium/harness/array_smoke.py`
- Create: `mm-terrarium/tests/test_array_smoke.py`

**Interfaces:**
- Consumes: `PixelSpan`, `UniverseSet`, `MultiUniverseOutputLoop` (Tasks A4, A5); `ArtNet` (Task A1).
- Produces: `build(pixel_count, wled_host, start_universe=0) -> tuple[UniverseSet, MultiUniverseOutputLoop]`, the entry point Task A9 reuses for the 6 m array.

**Context:** this is the first time the multi-universe code meets real hardware.
A 2 m run at 144/m is 288 pixels, which is exactly **3 universes** with no
partial tail, so a mapping bug shows up as a dark third of the strip rather than
as something subtle.

- [ ] **Step 1: Write the failing test**

```python
"""array_smoke: builds a spanned array driver without touching the network."""

from __future__ import annotations

import pytest

from harness.array_smoke import build


def test_build_sizes_the_span_for_a_two_metre_bench_array():
    universe_set, loop = build(288, "10.44.0.50")
    assert universe_set.span.pixel_count == 288
    assert universe_set.span.universe_count == 3
    assert len(universe_set.universes) == 3
    assert loop.running is False


def test_build_sizes_the_span_for_the_full_six_metre_array():
    universe_set, _ = build(864, "10.44.0.50")
    assert universe_set.span.universe_count == 7


def test_build_honours_start_universe():
    universe_set, _ = build(288, "10.44.0.50", start_universe=4)
    assert universe_set.span.universe_ids == [4, 5, 6]


def test_build_targets_the_requested_wled_host():
    _, loop = build(288, "10.44.0.77")
    assert loop.backend.host == "10.44.0.77"


def test_build_runs_at_the_dmx_refresh_rate():
    _, loop = build(288, "10.44.0.50")
    assert loop.frame_interval == pytest.approx(1.0 / 44.0)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd mm-terrarium && python -m pytest tests/test_array_smoke.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'harness.array_smoke'`

- [ ] **Step 3: Write the implementation**

```python
"""Drive a spanned SK6812 RGBW array over Art-Net to a WLED controller.

Usage:
    python -m harness.array_smoke --host 10.44.0.50 --pixels 288 --seconds 20

This is the venue-side sibling of ``led_smoke.py``: same renderer, real strip
instead of a browser canvas, and a pixel count large enough to span universes.
"""

from __future__ import annotations

import argparse
import math
import time

from luxaeterna.backends.artnet import ArtNet
from luxaeterna.pixelspan import PixelSpan
from luxaeterna.universeset import MultiUniverseOutputLoop, UniverseSet

CHANNELS_PER_PIXEL = 4          # SK6812 RGBW


def build(pixel_count: int, wled_host: str, start_universe: int = 0
          ) -> tuple[UniverseSet, MultiUniverseOutputLoop]:
    """Construct the universe set and its output loop. Does not start the loop."""
    span = PixelSpan(pixel_count,
                     channels_per_pixel=CHANNELS_PER_PIXEL,
                     start_universe=start_universe)
    universe_set = UniverseSet(span)
    loop = MultiUniverseOutputLoop(universe_set, ArtNet(host=wled_host))
    return universe_set, loop


def _travelling_wave(universe_set: UniverseSet, phase: float) -> None:
    """A slow green wave. Green only, so a wrong colour order is obvious."""
    count = universe_set.span.pixel_count
    for px in range(count):
        level = int(90 * (0.5 + 0.5 * math.sin(phase + px * 0.05)))
        universe_set.fill_pixel(px, bytes([0, level, 0, 0]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="WLED controller IP")
    parser.add_argument("--pixels", type=int, default=288)
    parser.add_argument("--start-universe", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()

    universe_set, loop = build(args.pixels, args.host, args.start_universe)
    start = time.monotonic()
    loop.on_frame = lambda us: _travelling_wave(us, (time.monotonic() - start) * 2.0)
    loop.start()
    try:
        while time.monotonic() - start < args.seconds:
            time.sleep(1.0)
            print(f"fps {loop.fps:.1f}")
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd mm-terrarium && python -m pytest tests/test_array_smoke.py -v`
Expected: all PASS.

- [ ] **Step 5: Wire the bench array and configure WLED for 3 universes**

Cut 2 m of 12 V per-LED SK6812, mount in Muzata channel, power from the 12 V 5 A
supply. In WLED: 288 LEDs, type SK6812 RGBW, **DMX start universe 0, mode Multi
RGBW**, and confirm the controller reports 3 universes.

- [ ] **Step 6: Run against the real strip from a venue box**

```bash
python -m harness.array_smoke --host 10.44.0.50 --pixels 288 --seconds 30
```

Pass criteria, all four:
1. All 288 pixels animate. A dark run of exactly 128 pixels means a universe
   mapping or WLED universe-count error.
2. The wave is **green**. Any other colour means the WLED colour order is wrong.
3. Printed FPS is at or near 44.
4. No dropouts, tearing, or flicker across 30 s.

- [ ] **Step 7: Commit**

```bash
git add harness/array_smoke.py tests/test_array_smoke.py
git commit -m "feat(harness): spanned Art-Net array driver, verified on the 2 m bench array"
```

## Task A8 [SW]: Power limiter — the array brightness cap

**Files:**
- Create: `luxaeterna/luxaeterna/power.py`
- Create: `luxaeterna/tests/test_power.py`

**Interfaces:**
- Produces, consumed by Task A9:
  - `PowerBudget(max_amps: float, amps_per_pixel_full: float = 0.025, channels_per_pixel: int = 4)`
  - `PowerLimiter(budget: PowerBudget, hard_ceiling: int = 117)`
  - `.estimate_amps(channels: bytes | bytearray) -> float`
  - `.scale_for(channels: bytes | bytearray) -> float`
  - `.apply(channels: bytes | bytearray) -> bytearray`

**Context, and why this is a safety task rather than a preference:**

| Quantity | Value | Source |
|---|---|---|
| SK6812 RGBW at 12 V, full white | 0.3 W/LED = **0.025 A/LED** | `MM_HARDWARE_DESIGN.md` §6.3 (128 LEDs = 38 W) |
| Array at full white | 864 x 0.025 = **21.6 A** | |
| Mean Well LRS-150-12 rating | **12.5 A** | §11.7 |
| Software budget (80% of supply) | **10.0 A** | this plan |

The array can demand **1.7x** what the supply can deliver. Two defences, both
required, because a bug in the adaptive one must not be able to exceed the
supply:

1. **Hard ceiling.** No channel ever exceeds `hard_ceiling` (117). All 3456
   channels at 117 draws 3456 x (117/255) x 0.00625 = **9.9 A**. Unconditional.
2. **Adaptive scale.** Estimate the frame's actual draw and scale only when it
   exceeds budget, so ordinary content keeps its full range and only near-white
   frames get pulled down.

- [ ] **Step 1: Write the failing test file**

```python
"""PowerLimiter: keep an LED array inside its supply's current budget."""

from __future__ import annotations

import pytest

from luxaeterna.power import PowerBudget, PowerLimiter

TERRARIUM_PIXELS = 864
TERRARIUM_CHANNELS = TERRARIUM_PIXELS * 4


def white(n_channels: int = TERRARIUM_CHANNELS) -> bytearray:
    return bytearray([255]) * n_channels


def black(n_channels: int = TERRARIUM_CHANNELS) -> bytearray:
    return bytearray(n_channels)


def budget(max_amps: float = 10.0) -> PowerBudget:
    return PowerBudget(max_amps=max_amps)


# --- estimation ---

def test_black_frame_draws_nothing():
    assert PowerLimiter(budget()).estimate_amps(black()) == pytest.approx(0.0)


def test_full_white_array_matches_the_documented_21_6_amps():
    amps = PowerLimiter(budget()).estimate_amps(white())
    assert amps == pytest.approx(21.6, abs=0.05)


def test_half_scale_white_draws_half():
    frame = bytearray([128]) * TERRARIUM_CHANNELS
    amps = PowerLimiter(budget()).estimate_amps(frame)
    assert amps == pytest.approx(21.6 * 128 / 255, abs=0.05)


def test_one_pixel_full_white_draws_one_pixel_worth():
    frame = black()
    frame[0:4] = bytes([255, 255, 255, 255])
    assert PowerLimiter(budget()).estimate_amps(frame) == pytest.approx(0.025, abs=1e-4)


# --- scale_for ---

def test_frame_under_budget_is_not_scaled():
    frame = black()
    frame[0:4] = bytes([255, 255, 255, 255])
    assert PowerLimiter(budget()).scale_for(frame) == 1.0


def test_full_white_is_scaled_to_the_budget():
    limiter = PowerLimiter(budget(10.0))
    assert limiter.scale_for(white()) == pytest.approx(10.0 / 21.6, abs=0.001)


def test_black_frame_scale_is_one_not_a_division_by_zero():
    assert PowerLimiter(budget()).scale_for(black()) == 1.0


# --- apply ---

def test_apply_never_exceeds_the_hard_ceiling():
    out = PowerLimiter(budget()).apply(white())
    assert max(out) <= 117


def test_apply_keeps_the_result_inside_budget():
    limiter = PowerLimiter(budget(10.0))
    out = limiter.apply(white())
    assert limiter.estimate_amps(out) <= 10.0


def test_hard_ceiling_alone_holds_even_with_a_huge_budget():
    """Defence in depth: a wrong budget must not let the array exceed the supply."""
    limiter = PowerLimiter(PowerBudget(max_amps=10_000.0))
    out = limiter.apply(white())
    assert max(out) <= 117
    assert limiter.estimate_amps(out) < 10.5


def test_apply_leaves_a_dim_frame_untouched():
    frame = black()
    frame[0:4] = bytes([10, 20, 30, 40])
    out = PowerLimiter(budget()).apply(frame)
    assert out[0:4] == bytearray([10, 20, 30, 40])


def test_apply_does_not_mutate_the_input():
    frame = white()
    PowerLimiter(budget()).apply(frame)
    assert frame == bytearray([255]) * TERRARIUM_CHANNELS


def test_apply_returns_the_same_length():
    assert len(PowerLimiter(budget()).apply(white())) == TERRARIUM_CHANNELS


def test_apply_preserves_relative_colour_within_a_pixel():
    """Values must stay below the hard ceiling, or the ceiling flattens them
    into each other before the adaptive scale ever runs."""
    frame = black()
    frame[0:4] = bytes([100, 80, 60, 0])
    out = PowerLimiter(PowerBudget(max_amps=0.003)).apply(frame)
    assert out[0] > out[1] > out[2] > out[3]


def test_the_hard_ceiling_flattens_channels_above_it():
    """Documents the ceiling's real cost: 255 and 128 both clamp to 117, so
    near-white content loses hue detail. Accepted; the supply rating wins."""
    frame = black()
    frame[0:4] = bytes([255, 128, 64, 0])
    out = PowerLimiter(PowerBudget(max_amps=10.0)).apply(frame)
    assert out[0] == out[1] == 117


# --- budget guards ---

def test_negative_budget_is_rejected():
    with pytest.raises(ValueError):
        PowerBudget(max_amps=-1.0)


def test_hard_ceiling_out_of_range_is_rejected():
    with pytest.raises(ValueError):
        PowerLimiter(budget(), hard_ceiling=256)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/test_power.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'luxaeterna.power'`

- [ ] **Step 3: Write the implementation**

```python
"""Lux Aeterna — keep an LED array inside its power supply's current budget.

The Terrarium's 864 SK6812 RGBW pixels draw 21.6 A at full white against a
12.5 A supply, so the array can demand 1.7x what the supply delivers. Two
independent defences are applied, in this order:

1. A **hard ceiling** on every channel value. Unconditional, and sized so that
   even an all-channels-at-ceiling frame stays under the supply rating. A bug in
   the adaptive stage cannot defeat it.
2. An **adaptive scale** that estimates the frame's actual draw and reduces it
   only when it exceeds budget, so ordinary content keeps its full range.

Default ``amps_per_pixel_full`` (0.025 A) is SK6812 RGBW at 12 V, from
MM_HARDWARE_DESIGN.md §6.3 (128 LEDs measured at 38 W).
"""

from __future__ import annotations

from dataclasses import dataclass

# All four channels at 255 on one pixel == amps_per_pixel_full.
_FULL_CHANNEL = 255


@dataclass(frozen=True)
class PowerBudget:
    """The current envelope an array must stay inside."""

    max_amps: float
    amps_per_pixel_full: float = 0.025
    channels_per_pixel: int = 4

    def __post_init__(self) -> None:
        if self.max_amps <= 0:
            raise ValueError(f"max_amps must be positive, got {self.max_amps}")
        if self.amps_per_pixel_full <= 0:
            raise ValueError(
                f"amps_per_pixel_full must be positive, got {self.amps_per_pixel_full}")
        if self.channels_per_pixel <= 0:
            raise ValueError(
                f"channels_per_pixel must be positive, got {self.channels_per_pixel}")

    @property
    def amps_per_channel_full(self) -> float:
        """Current drawn by one channel at full value."""
        return self.amps_per_pixel_full / self.channels_per_pixel


class PowerLimiter:
    """Apply a hard ceiling and an adaptive scale to a flat channel buffer."""

    __slots__ = ("budget", "hard_ceiling")

    def __init__(self, budget: PowerBudget, hard_ceiling: int = 117) -> None:
        if not (0 < hard_ceiling <= _FULL_CHANNEL):
            raise ValueError(
                f"hard_ceiling must be in 1-{_FULL_CHANNEL}, got {hard_ceiling}")
        self.budget = budget
        self.hard_ceiling = hard_ceiling

    def estimate_amps(self, channels: bytes | bytearray) -> float:
        """Estimated current for *channels*, a flat buffer of 0-255 values."""
        per_channel = self.budget.amps_per_channel_full / _FULL_CHANNEL
        return sum(channels) * per_channel

    def scale_for(self, channels: bytes | bytearray) -> float:
        """Multiplier that brings *channels* inside budget. 1.0 if already inside."""
        amps = self.estimate_amps(channels)
        if amps <= self.budget.max_amps or amps == 0.0:
            return 1.0
        return self.budget.max_amps / amps

    def apply(self, channels: bytes | bytearray) -> bytearray:
        """Return a new buffer, ceiling-clamped then scaled into budget."""
        clamped = bytearray(min(v, self.hard_ceiling) for v in channels)
        scale = self.scale_for(clamped)
        if scale >= 1.0:
            return clamped
        return bytearray(int(v * scale) for v in clamped)

    def __repr__(self) -> str:
        return (f"PowerLimiter(max_amps={self.budget.max_amps}, "
                f"hard_ceiling={self.hard_ceiling})")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/test_power.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/luxaeterna
git add luxaeterna/power.py tests/test_power.py
git commit -m "feat(power): current-budget limiter with unconditional hard ceiling"
```

## Task A9 [HW]: 6 m array build, power distribution, and its documentation

**Files:**
- Create: `mm-terrarium/docs/hardware/array-power.md`
- Modify: `mm-terrarium/harness/array_smoke.py`
- Modify: `mm-terrarium/tests/test_array_smoke.py`

**Interfaces:**
- Consumes: `build()` (Task A7), `PowerBudget` and `PowerLimiter` (Task A8).
- Produces: `build(..., max_amps: float | None = None)` inserting a limiter into
  the `on_frame` path when `max_amps` is given.

**Context:** power injection, fusing, and wire gauge for this array are
documented nowhere in the MM doc set. This task **produces** that documentation.
Do the arithmetic before cutting any wire.

- [ ] **Step 1: Add the limiter to `array_smoke.build`, test first**

Append to `mm-terrarium/tests/test_array_smoke.py`:

```python
def test_build_without_max_amps_installs_no_limiter():
    universe_set, loop = build(864, "10.44.0.50")
    assert loop.limiter is None


def test_build_with_max_amps_installs_a_limiter():
    _, loop = build(864, "10.44.0.50", max_amps=10.0)
    assert loop.limiter is not None
    assert loop.limiter.budget.max_amps == 10.0


def test_limiter_clamps_a_full_white_array_frame():
    _, loop = build(864, "10.44.0.50", max_amps=10.0)
    out = loop.limiter.apply(bytearray([255]) * 3456)
    assert max(out) <= 117
    assert loop.limiter.estimate_amps(out) <= 10.0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mm-terrarium && python -m pytest tests/test_array_smoke.py -v`
Expected: FAIL, `AttributeError: 'MultiUniverseOutputLoop' object has no attribute 'limiter'`

- [ ] **Step 3: Implement**

In `mm-terrarium/harness/array_smoke.py`, add the import and replace `build`:

```python
from luxaeterna.power import PowerBudget, PowerLimiter

TERRARIUM_MAX_AMPS = 10.0       # 80% of the LRS-150-12's 12.5 A


def build(pixel_count: int, wled_host: str, start_universe: int = 0,
          max_amps: float | None = None
          ) -> tuple[UniverseSet, MultiUniverseOutputLoop]:
    """Construct the universe set and its output loop. Does not start the loop.

    Passing *max_amps* installs a :class:`PowerLimiter` on the loop. The
    Terrarium array MUST be built with ``max_amps=TERRARIUM_MAX_AMPS``; it draws
    21.6 A at full white against a 12.5 A supply.
    """
    span = PixelSpan(pixel_count,
                     channels_per_pixel=CHANNELS_PER_PIXEL,
                     start_universe=start_universe)
    universe_set = UniverseSet(span)
    loop = MultiUniverseOutputLoop(universe_set, ArtNet(host=wled_host))
    loop.limiter = (
        PowerLimiter(PowerBudget(max_amps=max_amps)) if max_amps else None)
    return universe_set, loop
```

Then apply the limiter in `_travelling_wave`'s caller by wrapping the paint hook:

```python
def _limited(paint, loop):
    """Wrap a paint hook so every frame passes through the loop's limiter."""
    def hook(universe_set):
        paint(universe_set)
        if loop.limiter is not None:
            for universe in universe_set.universes:
                universe.set_range(0, loop.limiter.apply(universe.get_frame()))
    return hook
```

and in `main()`, replace the `loop.on_frame = ...` line with:

```python
    loop.on_frame = _limited(
        lambda us: _travelling_wave(us, (time.monotonic() - start) * 2.0), loop)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mm-terrarium && python -m pytest tests/test_array_smoke.py -v`
Expected: all PASS.

- [ ] **Step 5: Do the power arithmetic and write `array-power.md` BEFORE cutting wire**

Compute and record:

- Total current at the 10.0 A software budget, and at the 21.6 A unlimited worst
  case (the number the wiring must physically survive if software fails).
- **Injection points.** A 12 V run of 864 px at 144/m over 6 m cannot be fed from
  one end. Compute voltage drop per metre at the design current and place
  injection points so the far end stays within 5% of 12 V. Expect three or four.
- **Wire gauge** for the injection runs, sized for the **21.6 A worst case**, not
  the 10.0 A budget. Software limits are not a wiring safety argument.
- **Fusing.** One inline fuse per injection branch, rated for its branch, sized
  below the wire's ampacity.
- A wiring diagram showing the supply, distribution block, every injection point
  with its fuse, and the WLED controller's data connection.

- [ ] **Step 6: Build the array**

Mount 6 m of strip in Muzata channel. Land injection points and fuses per the
document written in Step 5. Land the data line from the WLED controller.

- [ ] **Step 7: Power up on a current-limited bench supply first**

Set the bench supply to 12 V with a **2 A** limit. Power the array with all
pixels off, then bring up a small number of pixels. Confirm current tracks the
prediction from Step 5 within about 15%. Only after that matches, move to the
LRS-150-12.

If measured current diverges from prediction by more than 15%, stop. Either the
strip is not the part you think it is or the wiring is wrong.

- [ ] **Step 8: Run the full array with the limiter engaged**

```bash
python -m harness.array_smoke --host 10.44.0.50 --pixels 864 --seconds 60
```

Pass criteria:
1. All 864 pixels animate. A dark 128-pixel run means a universe error.
2. Measured supply current stays at or below 10 A with a clamp meter.
3. FPS at or near 44.
4. No visible dimming toward the far end (injection is adequate).
5. No connector or wire is warm to the touch after 60 s.

- [ ] **Step 9: Record measured values back into `array-power.md`**

Predicted vs measured current at several brightness levels, far-end voltage
under load, and observed FPS.

- [ ] **Step 10: Commit**

```bash
git add docs/hardware/array-power.md harness/array_smoke.py tests/test_array_smoke.py
git commit -m "feat(array): 6m array power distribution, limiter in the render path, measurements recorded"
```

## Task A10 [SW]: Measure the 44 Hz render loop on the Pi 5

**Files:**
- Create: `mm-terrarium/harness/render_bench.py`
- Create: `mm-terrarium/tests/test_render_bench.py`
- Create: `mm-terrarium/docs/hardware/timing-measurements.md`

**Interfaces:**
- Consumes: `build()` (Task A9).
- Produces: `measure(loop, seconds, sample_hz=1.0) -> FrameStats` where
  `FrameStats` is a frozen dataclass with fields `frames: int`, `seconds: float`,
  `mean_fps: float`, `min_fps: float`, `p95_frame_ms: float`, `worst_frame_ms: float`.

**Context:** `MM_TERRARIUM.md` § *Host platform* is explicit that any timing
figure must be measured on the venue box, and that the M1a-era "under 50 ms"
number does not carry over because Control was not in the path. A single mean
FPS reading is not enough: a loop that averages 44 Hz while stalling for 200 ms
once a second looks fine and is not.

- [ ] **Step 1: Write the failing test**

```python
"""render_bench: frame-timing statistics for the multi-universe output loop."""

from __future__ import annotations

import pytest

from harness.render_bench import FrameStats, summarise


def test_a_perfectly_regular_loop_reports_its_nominal_rate():
    intervals = [1.0 / 44.0] * 44
    stats = summarise(intervals)
    assert stats.frames == 44
    assert stats.mean_fps == pytest.approx(44.0, abs=0.01)
    assert stats.worst_frame_ms == pytest.approx(1000.0 / 44.0, abs=0.01)


def test_a_single_stall_is_visible_in_the_worst_frame_not_the_mean():
    intervals = [1.0 / 44.0] * 43 + [0.2]
    stats = summarise(intervals)
    assert stats.mean_fps > 20.0                 # mean still looks acceptable
    assert stats.worst_frame_ms == pytest.approx(200.0, abs=0.1)


def test_p95_ignores_a_single_outlier_but_catches_a_sustained_one():
    good = [1.0 / 44.0] * 95 + [0.1] * 5
    stats = summarise(good)
    assert stats.p95_frame_ms >= 100.0


def test_min_fps_reflects_the_worst_frame():
    intervals = [1.0 / 44.0] * 43 + [0.5]
    stats = summarise(intervals)
    assert stats.min_fps == pytest.approx(2.0, abs=0.01)


def test_empty_sample_is_rejected():
    with pytest.raises(ValueError):
        summarise([])


def test_stats_are_immutable():
    stats = summarise([1.0 / 44.0] * 10)
    with pytest.raises(Exception):
        stats.frames = 99
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mm-terrarium && python -m pytest tests/test_render_bench.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'harness.render_bench'`

- [ ] **Step 3: Write the implementation**

```python
"""Frame-timing statistics for the Terrarium's multi-universe render loop.

Mean FPS alone hides stalls: a loop that averages 44 Hz while pausing 200 ms
once a second reads as healthy and is not. This reports the worst frame and the
95th percentile alongside the mean, so a stall cannot hide behind an average.

Usage (ON THE VENUE BOX, never a laptop):
    python -m harness.render_bench --host 10.44.0.50 --pixels 864 --seconds 120
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Sequence

from harness.array_smoke import TERRARIUM_MAX_AMPS, build


@dataclass(frozen=True)
class FrameStats:
    frames: int
    seconds: float
    mean_fps: float
    min_fps: float
    p95_frame_ms: float
    worst_frame_ms: float


def summarise(intervals: Sequence[float]) -> FrameStats:
    """Reduce a sequence of per-frame intervals (seconds) to statistics."""
    if not intervals:
        raise ValueError("need at least one frame interval")
    total = sum(intervals)
    ordered = sorted(intervals)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    worst = ordered[-1]
    return FrameStats(
        frames=len(intervals),
        seconds=total,
        mean_fps=len(intervals) / total,
        min_fps=1.0 / worst,
        p95_frame_ms=ordered[p95_index] * 1000.0,
        worst_frame_ms=worst * 1000.0,
    )


def measure(loop, seconds: float) -> FrameStats:
    """Drive *loop* synchronously for *seconds*, timing every tick."""
    intervals: list[float] = []
    loop.backend.open()
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            tick = time.monotonic()
            loop._loop_once()
            elapsed = time.monotonic() - tick
            sleep = loop.frame_interval - elapsed
            if sleep > 0:
                time.sleep(sleep)
            intervals.append(time.monotonic() - tick)
    finally:
        loop.backend.close()
    return summarise(intervals)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--pixels", type=int, default=864)
    parser.add_argument("--seconds", type=float, default=120.0)
    args = parser.parse_args()

    _, loop = build(args.pixels, args.host, max_amps=TERRARIUM_MAX_AMPS)
    stats = measure(loop, args.seconds)
    print(f"frames      {stats.frames}")
    print(f"duration    {stats.seconds:.1f} s")
    print(f"mean fps    {stats.mean_fps:.2f}")
    print(f"min fps     {stats.min_fps:.2f}")
    print(f"p95 frame   {stats.p95_frame_ms:.2f} ms")
    print(f"worst frame {stats.worst_frame_ms:.2f} ms")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mm-terrarium && python -m pytest tests/test_render_bench.py -v`
Expected: all PASS.

- [ ] **Step 5: Run it on the venue box against the real 864-pixel array**

**On `TERRARIUM-SHOW`, not a laptop.**

```bash
python -m harness.render_bench --host 10.44.0.50 --pixels 864 --seconds 120
```

Pass criteria:
- `mean fps` at or above 43.0
- `p95 frame` at or below 25 ms (44 Hz is 22.7 ms)
- `worst frame` at or below 50 ms

- [ ] **Step 6: Repeat under load**

Re-run with the Control console and a `devicelink` server also running, since
that is the real condition: one process relaying device traffic while feeding
the render loop. Record both numbers.

- [ ] **Step 7: Write `timing-measurements.md`**

Record: hardware (Pi 5 model, storage, OS version), pixel count, the exact
command, both idle and loaded results, and the date. This file is the answer to
"is it 44 Hz on the venue box", and it is the only acceptable form of that
answer.

- [ ] **Step 8: Commit**

```bash
git add harness/render_bench.py tests/test_render_bench.py docs/hardware/timing-measurements.md
git commit -m "feat(harness): venue-box frame-timing bench; record measured 44 Hz results"
```

---

# Phase B — Lane B, Instrument

## Task B1 [HW]: Radxa Zero 3W bring-up

**Files:**
- Create: `mm-terrarium/docs/hardware/radxa-runbook.md`

**Interfaces:**
- Produces: a Radxa on the bench subnet with Python 3 and SSH, and the runbook Task B8 repeats per unit.

- [ ] **Step 1: Flash the OS**

Official Radxa Debian or Armbian for Zero 3W. Record the exact image name, URL,
and SHA. Boot, expand the filesystem, set the hostname to `shroom-mule`.

- [ ] **Step 2: Join the bench subnet and confirm reachability**

Connect to the bench AP with a DHCP reservation. Verify from a venue box:

```bash
ping -c 3 shroom-mule.local && ssh shroom-mule 'uname -a && python3 --version'
```

- [ ] **Step 3: Verify the 40-pin header is alive**

```bash
gpioinfo | head -40
ls /dev/i2c-* /dev/snd/
```

Record which I2C buses and sound devices enumerate on a stock image, before any
overlay work. This is the baseline Task B2 changes.

- [ ] **Step 4: Write the runbook with everything above**

- [ ] **Step 5: Commit**

```bash
git add docs/hardware/radxa-runbook.md
git commit -m "docs(hardware): Radxa Zero 3W bring-up runbook and stock-image baseline"
```

## Task B2 [HW]: I2S full-duplex device-tree overlay

**Files:**
- Modify: `mm-terrarium/docs/hardware/radxa-runbook.md`

**Interfaces:**
- Produces: simultaneous I2S capture and playback on one Radxa, which Task B3 exercises with real parts.

**This is the hardest single task in the plan and the main reason Gate 1 exists.**
The INMP441 and MAX98357A share BCLK (pin 12) and LRCK (pin 35), with the mic on
DI (pin 38) and the amp on DO (pin 40). That is a full-duplex I2S configuration,
and the stock overlays generally provide simplex.

- [ ] **Step 1: Wire the mic and amp to the header before touching software**

Per `MM_HARDWARE_DESIGN.md` §4.2: INMP441 BCLK 12, LRCK 35, DI 38, L/R select to
GND. MAX98357A BCLK 12, LRCK 35, DO 40, VIN to 5 V. Verify continuity with a
meter before power.

- [ ] **Step 2: Survey the available overlays and record what exists**

```bash
ls /boot/dtbo/ 2>/dev/null || ls /boot/overlay-user/ 2>/dev/null
```

Record every I2S-related overlay name. Try each in isolation and record the
resulting `arecord -l` and `aplay -l` output. **Record the failures too**, since
the failures are what justify writing a custom overlay if it comes to that.

- [ ] **Step 3: Confirm capture and playback each work alone**

```bash
arecord -l
arecord -D hw:0,0 -f S32_LE -r 48000 -c 2 -d 5 /tmp/mic.wav
aplay -l
aplay -D hw:0,0 /tmp/mic.wav
```

- [ ] **Step 4: Confirm they work SIMULTANEOUSLY**

This is the actual gate criterion. Run in two shells at once:

```bash
arecord -D hw:0,0 -f S32_LE -r 48000 -c 2 -d 30 /tmp/dup.wav
```
```bash
aplay -D hw:0,0 /usr/share/sounds/alsa/Front_Center.wav
```

Both must run without `device busy`, and the recording must contain real audio.

- [ ] **Step 5: If no stock overlay gives full duplex, write one**

Compile a custom overlay declaring the I2S controller in full-duplex mode with
both codecs on the same bus. Record the `.dts` source in the runbook verbatim.
Budget two days here; if it exceeds four, escalate to Chris rather than pushing
into Gate 1.

- [ ] **Step 6: Record the working configuration in the runbook**

Exact overlay name or `.dts` source, the config file lines, `arecord -l` and
`aplay -l` output, and the simultaneous-operation verification from Step 4.

- [ ] **Step 7: Commit**

```bash
git add docs/hardware/radxa-runbook.md
git commit -m "docs(hardware): I2S full-duplex overlay for INMP441 + MAX98357A on Radxa Zero 3W"
```

## Task B3 [HW]: Mic capture and speaker playback with real parts

**Files:**
- Modify: `mm-terrarium/docs/hardware/radxa-runbook.md`

- [ ] **Step 1: Connect the 50 mm driver to the MAX98357A**

- [ ] **Step 2: Verify playback quality**

```bash
speaker-test -D hw:0,0 -c 2 -t sine -f 440 -l 3
```

Listen for: audible tone, no crackle, no clipping at moderate level. Record the
level at which clipping begins.

- [ ] **Step 3: Verify mic capture quality**

```bash
arecord -D hw:0,0 -f S32_LE -r 48000 -c 1 -d 10 /tmp/speech.wav
aplay /tmp/speech.wav
```

Speak at normal volume 30 cm away. Playback must be intelligible.

- [ ] **Step 4: Check for feedback with both running**

Mic and speaker share a small enclosure later, so run both simultaneously at
working volume and record at what level feedback starts. This number drives
enclosure damping decisions in Task C1.

- [ ] **Step 5: Record all results in the runbook**

- [ ] **Step 6: Commit**

```bash
git add docs/hardware/radxa-runbook.md
git commit -m "docs(hardware): INMP441 and MAX98357A verified with real parts; feedback threshold recorded"
```

## Task B4 [HW]: LIS3DH over I2C with the hardware tap interrupt

**Files:**
- Create: `mm-terrarium/harness/lis3dh_probe.py`
- Modify: `mm-terrarium/docs/hardware/radxa-runbook.md`

**Interfaces:**
- Produces: `read_tilt() -> tuple[float, float, float]` and a tap-interrupt
  callback path, consumed by Task B6's client.

**Context:** the LIS3DH was chosen over the ADXL345 specifically for its
**hardware tap interrupt**, which offloads hit detection to the chip. Polling
the accelerometer in Python and calling it a tap detector throws away the reason
the part was selected.

- [ ] **Step 1: Confirm the chip answers on I2C**

```bash
i2cdetect -y 1
```

Expected: a device at `0x18` or `0x19`. If nothing appears, check SDA on pin 3,
SCL on pin 5, VCC on pin 1 (3.3 V), and that the breakout's pull-ups are present.

- [ ] **Step 2: Write the probe script**

```python
"""LIS3DH probe: tilt readout plus hardware tap interrupt.

The LIS3DH was chosen over the ADXL345 for its on-chip tap detection. Configure
the interrupt and watch the line; do not poll acceleration and call it a tap.

Usage:  python3 -m harness.lis3dh_probe
"""

from __future__ import annotations

import time

import board
import busio
import adafruit_lis3dh

TAP_THRESHOLD = 40          # tune on the bench; lower is more sensitive


def open_sensor():
    i2c = busio.I2C(board.SCL, board.SDA)
    sensor = adafruit_lis3dh.LIS3DH_I2C(i2c)
    sensor.range = adafruit_lis3dh.RANGE_4_G
    sensor.data_rate = adafruit_lis3dh.DATARATE_400_HZ
    sensor.set_tap(1, TAP_THRESHOLD)
    return sensor


def read_tilt(sensor) -> tuple[float, float, float]:
    """Return acceleration in m/s^2 as (x, y, z)."""
    return sensor.acceleration


def main() -> None:
    sensor = open_sensor()
    print("tilt + tap probe running; tap the sensor")
    taps = 0
    while True:
        if sensor.tapped:
            taps += 1
            print(f"TAP {taps}")
        x, y, z = read_tilt(sensor)
        print(f"  x={x:+6.2f} y={y:+6.2f} z={z:+6.2f}", end="\r")
        time.sleep(0.02)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Install the driver and run it**

```bash
pip3 install adafruit-circuitpython-lis3dh
python3 -m harness.lis3dh_probe
```

- [ ] **Step 4: Verify tilt and tap independently**

Tilt: rotating the board must move the axis values smoothly and predictably.
Tap: a sharp tap on the enclosure must print `TAP`, and gentle tilting must
**not**. Tune `TAP_THRESHOLD` until both hold, and record the final value.

- [ ] **Step 5: Record the working threshold and I2C address in the runbook**

- [ ] **Step 6: Commit**

```bash
git add harness/lis3dh_probe.py docs/hardware/radxa-runbook.md
git commit -m "feat(harness): LIS3DH tilt and hardware tap probe, threshold tuned on bench"
```

## Task B5 [HW]: 12-pixel SK6812 cap and stem array

**Files:**
- Create: `mm-terrarium/harness/shroom_leds.py`
- Modify: `mm-terrarium/docs/hardware/radxa-runbook.md`

**Interfaces:**
- Produces: `ShroomLEDs.show(channels: bytes) -> None` accepting **36 bytes
  (12 pixels x GRB)**, matching `devicelink.protocol.leds_event`. Consumed by
  Task B6.

**Context:** 8 pixels in the cap ring, 4 in the stem, on GPIO2_C7 (pin 7),
800 kHz one-wire. The Radxa's 3.3 V logic is in spec for 5 V SK6812 data.

**The channel-count trap:** the parts are SK6812 **RGBW** (4 channels), but the
`devicelink` wire ships **36 ints = 12 px x GRB** (3 channels), and
`shroom_capability` declares `color_order: "GRB"`. Task B7 resolves that
mismatch. This task drives the strip natively and records what the hardware
actually wants.

- [ ] **Step 1: Wire the chain and verify before power**

Data to pin 7, 5 V from the trimmed MT3608, common ground. Confirm with a meter
that the boost output reads **5.0 V** before connecting.

- [ ] **Step 2: Write the driver**

```python
"""12-pixel SK6812 cap-and-stem array on the Radxa's GPIO2_C7 (pin 7).

The devicelink wire ships 36 ints (12 px x GRB, 3 channels), but SK6812 Mini
RGBW parts want 4 channels per pixel. ``show()`` takes the 3-channel wire form
and expands it; the white channel is driven to 0 until Task B7 settles the
protocol question.
"""

from __future__ import annotations

import board
import neopixel_spi as neopixel

PIXEL_COUNT = 12
RING = slice(0, 8)
STEM = slice(8, 12)


class ShroomLEDs:
    def __init__(self, pixel_count: int = PIXEL_COUNT) -> None:
        self.pixel_count = pixel_count
        self._pixels = neopixel.NeoPixel_SPI(
            board.SPI(), pixel_count, pixel_order=neopixel.GRBW, auto_write=False)

    def show(self, channels: bytes) -> None:
        """Display 36 bytes of GRB data (12 px x 3), expanding to GRBW."""
        expected = self.pixel_count * 3
        if len(channels) != expected:
            raise ValueError(f"expected {expected} channels, got {len(channels)}")
        for px in range(self.pixel_count):
            g, r, b = channels[px * 3:px * 3 + 3]
            self._pixels[px] = (g, r, b, 0)
        self._pixels.show()

    def clear(self) -> None:
        self.show(bytes(self.pixel_count * 3))


def main() -> None:
    import time
    leds = ShroomLEDs()
    try:
        while True:
            for name, colour in (("green", (120, 0, 0)),
                                 ("red", (0, 120, 0)),
                                 ("blue", (0, 0, 120))):
                print(name)
                leds.show(bytes(colour * PIXEL_COUNT))
                time.sleep(1.0)
    except KeyboardInterrupt:
        leds.clear()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Install the driver and run it**

```bash
pip3 install adafruit-circuitpython-neopixel-spi
python3 -m harness.shroom_leds
```

- [ ] **Step 4: Verify colour order empirically**

The script names each colour as it displays it. If "green" shows red, the pixel
order is wrong. Try `neopixel.GRBW`, `neopixel.RGBW`, then `neopixel.GRB`, and
**record which one the parts actually want**. Do not assume the datasheet.

- [ ] **Step 5: Verify ring and stem addressing**

Light `RING` only, then `STEM` only. Confirm 8 pixels then 4 pixels, and that
they match `shroom_capability`'s declared zones (`ring` start 0 count 8, `stem`
start 8 count 4).

- [ ] **Step 6: Record the pixel order, zone mapping, and any level derating in the runbook**

- [ ] **Step 7: Commit**

```bash
git add harness/shroom_leds.py docs/hardware/radxa-runbook.md
git commit -m "feat(harness): 12-pixel SK6812 cap and stem driver, pixel order verified empirically"
```

## Task B6 [SW]: `devicelink` client on the Radxa

**Files:**
- Create: `mm-terrarium/harness/shroom_client.py`
- Create: `mm-terrarium/tests/test_shroom_client.py`

**Interfaces:**
- Consumes: `devicelink.protocol.Envelope`, `encode`, `decode`;
  `ShroomLEDs.show` (B5); `read_tilt` (B4).
- Produces:
  - `ShroomClient(dev: str, node: str, leds=None, on_role=None)`
  - `.hello() -> dict`, `.join() -> dict`, `.tilt(value: float) -> dict`
  - `.handle(msg: dict) -> str` returning the handled address, or `""` if dropped
  - `.config -> dict | None`, `.released -> bool`

**Context, the exact wire** (`devicelink/protocol.py`, `devicelink/agent.py`):

| Direction | Address | Typespec | Args |
|---|---|---|---|
| up | `/game/hello` | `s` | `[dev]` |
| up | `/game/join` | `ss` | `[dev, node]` |
| up | `/game/<verb>` | `sf` | `[dev, value]` |
| down | `/<dev>/role` | `b` | `[config]` |
| down | `/<dev>/deny` | `ss` | `[reason, hint]` |
| down | `/<dev>/leds` | `b` | `[[36 ints]]` |
| down | `/<dev>/release` | `""` | `[]` |
| down | `/<dev>/error` | `ss` | `[context, message]` |

`TestBit` provides a `tilt` handler mapping tilt onto `cc:74`, so `tilt` is the
verb to exercise first.

- [ ] **Step 1: Write the failing test file**

```python
"""ShroomClient: the Radxa's devicelink participation, tested without a socket."""

from __future__ import annotations

import pytest

from devicelink import protocol
from harness.shroom_client import ShroomClient

DEV = "ie1"
NODE = "node-a"


class FakeLEDs:
    def __init__(self) -> None:
        self.shown: list[bytes] = []
        self.cleared = 0

    def show(self, channels: bytes) -> None:
        self.shown.append(bytes(channels))

    def clear(self) -> None:
        self.cleared += 1


def client(**kw) -> ShroomClient:
    return ShroomClient(DEV, NODE, leds=FakeLEDs(), **kw)


# --- outbound ---

def test_hello_matches_the_wire():
    msg = client().hello()
    env = protocol.decode(msg)
    assert env.address == "/game/hello"
    assert env.typespec == "s"
    assert env.args == [DEV]


def test_join_carries_dev_and_node():
    env = protocol.decode(client().join())
    assert env.address == "/game/join"
    assert env.typespec == "ss"
    assert env.args == [DEV, NODE]


def test_tilt_carries_dev_and_a_float():
    env = protocol.decode(client().tilt(0.75))
    assert env.address == "/game/tilt"
    assert env.typespec == "sf"
    assert env.args[0] == DEV
    assert env.args[1] == pytest.approx(0.75)


def test_every_outbound_message_survives_a_decode_round_trip():
    c = client()
    for msg in (c.hello(), c.join(), c.tilt(0.1)):
        assert protocol.decode(msg).address.startswith("/game/")


# --- inbound ---

def test_role_stores_the_config_blob_verbatim():
    c = client()
    blob = {"bit_name": "test_bit", "role": "player", "light_manifest": {"v": 2}}
    assert c.handle(protocol.role_event(DEV, blob)) == f"/{DEV}/role"
    assert c.config == blob


def test_role_fires_the_on_role_callback():
    seen = []
    c = client(on_role=seen.append)
    blob = {"role": "player"}
    c.handle(protocol.role_event(DEV, blob))
    assert seen == [blob]


def test_leds_are_forwarded_to_the_strip():
    c = client()
    channels = list(range(36))
    c.handle(protocol.leds_event(DEV, channels))
    assert c.leds.shown[-1] == bytes(range(36))


def test_leds_with_a_wrong_channel_count_are_dropped_not_raised():
    c = client()
    assert c.handle(protocol.leds_event(DEV, list(range(12)))) == ""
    assert c.leds.shown == []


def test_release_clears_the_strip_and_sets_released():
    c = client()
    assert c.handle(protocol.release_event(DEV)) == f"/{DEV}/release"
    assert c.released is True
    assert c.leds.cleared == 1


def test_deny_is_recorded_and_leaves_config_unset():
    c = client()
    assert c.handle(protocol.deny_event(DEV, "full", "try node-b")) == f"/{DEV}/deny"
    assert c.config is None
    assert c.last_deny == ("full", "try node-b")


def test_error_is_recorded():
    c = client()
    c.handle(protocol.error_event(DEV, "join", "missing node"))
    assert c.last_error == ("join", "missing node")


# --- robustness: a malformed frame is dropped, never raised ---

def test_a_message_for_another_device_is_ignored():
    c = client()
    assert c.handle(protocol.leds_event("ie2", list(range(36)))) == ""
    assert c.leds.shown == []


def test_a_malformed_envelope_is_dropped():
    c = client()
    assert c.handle({"address": "/ie1/leds", "typespec": "b", "args": []}) == ""


def test_a_non_dict_message_is_dropped():
    c = client()
    assert c.handle("not a message") == ""


def test_an_unknown_address_is_dropped():
    c = client()
    assert c.handle(protocol.encode(protocol.Envelope(0.0, "/ie1/wat", "", []))) == ""


def test_a_rejoin_clears_the_released_flag():
    c = client()
    c.handle(protocol.release_event(DEV))
    c.join()
    assert c.released is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mm-terrarium && python -m pytest tests/test_shroom_client.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'harness.shroom_client'`

- [ ] **Step 3: Write the implementation**

```python
"""The Radxa Tuneshroom's devicelink participation.

Socket-free by design: ``handle()`` takes a decoded JSON message and returns the
address it handled, or ``""`` if it dropped the frame. That keeps the whole
protocol surface testable on a laptop, and it matches the engine's rule that a
malformed frame is 'drop this frame', never an error.

The transport half lives in ``main()`` and is deliberately thin, because it is
the part that gets replaced when o2lite lands.
"""

from __future__ import annotations

import logging
from typing import Callable

from devicelink import protocol

logger = logging.getLogger(__name__)

LED_CHANNELS = 36           # 12 pixels x GRB, per protocol.leds_event


class ShroomClient:
    """Tracks one device's devicelink session and drives its LEDs."""

    def __init__(self, dev: str, node: str, leds=None,
                 on_role: Callable[[dict], None] | None = None) -> None:
        self.dev = dev
        self.node = node
        self.leds = leds
        self.on_role = on_role
        self.config: dict | None = None
        self.released = False
        self.last_deny: tuple[str, str] | None = None
        self.last_error: tuple[str, str] | None = None

    # --- outbound ---

    def _up(self, verb: str, typespec: str, args: list) -> dict:
        return protocol.encode(
            protocol.Envelope(timestamp=0.0, address=f"/game/{verb}",
                              typespec=typespec, args=args))

    def hello(self) -> dict:
        return self._up("hello", "s", [self.dev])

    def join(self) -> dict:
        self.released = False
        return self._up("join", "ss", [self.dev, self.node])

    def tilt(self, value: float) -> dict:
        return self._up("tilt", "sf", [self.dev, float(value)])

    # --- inbound ---

    def handle(self, msg) -> str:
        """Process one inbound message. Returns its address, or "" if dropped."""
        try:
            env = protocol.decode(msg)
        except (ValueError, AttributeError, TypeError):
            logger.debug("dropping malformed envelope")
            return ""

        prefix = f"/{self.dev}/"
        if not env.address.startswith(prefix):
            return ""
        kind = env.address[len(prefix):]

        if kind == "role":
            return self._on_role(env)
        if kind == "leds":
            return self._on_leds(env)
        if kind == "release":
            return self._on_release(env)
        if kind == "deny":
            self.last_deny = (env.args[0], env.args[1])
            return env.address
        if kind == "error":
            self.last_error = (env.args[0], env.args[1])
            return env.address
        return ""

    def _on_role(self, env) -> str:
        if not env.args or not isinstance(env.args[0], dict):
            return ""
        self.config = env.args[0]
        if self.on_role is not None:
            self.on_role(self.config)
        return env.address

    def _on_leds(self, env) -> str:
        if not env.args or not isinstance(env.args[0], list):
            return ""
        channels = env.args[0]
        if len(channels) != LED_CHANNELS:
            logger.debug("dropping /leds with %d channels", len(channels))
            return ""
        if self.leds is not None:
            self.leds.show(bytes(v & 0xFF for v in channels))
        return env.address

    def _on_release(self, env) -> str:
        self.released = True
        if self.leds is not None:
            self.leds.clear()
        return env.address


def main() -> None:
    """Connect to a DeviceLinkServer and run the sensor-up / LED-down loop."""
    import argparse
    import asyncio
    import json

    import websockets

    from harness.lis3dh_probe import open_sensor, read_tilt
    from harness.shroom_leds import ShroomLEDs

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="ws://host:port")
    parser.add_argument("--dev", default="ie1")
    parser.add_argument("--node", default="node-a")
    args = parser.parse_args()

    client = ShroomClient(args.dev, args.node, leds=ShroomLEDs())
    sensor = open_sensor()

    async def run() -> None:
        async with websockets.connect(args.server) as ws:
            await ws.send(json.dumps(client.hello()))
            await ws.send(json.dumps(client.join()))

            async def pump_down() -> None:
                async for raw in ws:
                    client.handle(json.loads(raw))

            async def pump_up() -> None:
                while True:
                    x, _, _ = read_tilt(sensor)
                    await ws.send(json.dumps(client.tilt(x / 9.81)))
                    await asyncio.sleep(0.05)      # 20 Hz sensor rate

            await asyncio.gather(pump_down(), pump_up())

    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mm-terrarium && python -m pytest tests/test_shroom_client.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole mm-terrarium suite for regressions**

Run: `cd mm-terrarium && python -m pytest tests -v`
Expected: all PASS (205+ tests).

- [ ] **Step 6: Run against a real DeviceLink server**

On a venue box:
```bash
python -m harness.devicelink_smoke --hold
```
On the Radxa:
```bash
pip3 install websockets
python3 -m harness.shroom_client --server ws://10.44.0.10:8081 --dev ie1 --node node-a
```

Pass criteria:
1. The server logs a join and grants a role.
2. `/ie1/leds` frames arrive and the cap-and-stem array animates.
3. Tilting the Radxa changes the LED colour, proving the round trip
   sensor to `/game/tilt` to `cc:74` to Lux Aeterna to `/ie1/leds` to strip.
4. Ending the Bit fades the LEDs and then releases, in that order. Release
   arrives **after** the fade, never at the moment the Bit ends.

- [ ] **Step 7: Commit**

```bash
git add harness/shroom_client.py tests/test_shroom_client.py
git commit -m "feat(harness): Radxa devicelink client with socket-free protocol tests"
```

## Task B7 [SW]: Resolve the GRB-vs-RGBW channel mismatch

**Files:**
- Modify: `mm-terrarium/devicelink/protocol.py:79-81`
- Modify: `mm-terrarium/tests/test_devicelink_protocol.py`
- Create: `mm-terrarium/docs/superpowers/specs/2026-XX-XX-shroom-rgbw-wire-decision.md` (dated the day you write it)

**Interfaces:**
- Consumes: `protocol.leds_event`, `ShroomLEDs.show` (B5), `ShroomClient._on_leds` (B6).
- Produces: a decision, and whichever of the two changes it implies.

**Context:** the hardware is SK6812 Mini **RGBW**, four channels, with a
dedicated white die chosen for clean diffusion. The wire ships **three**:
`leds_event` docstring says "a flat sequence of 36 ints (12 pixels x GRB)" and
`shroom_capability` declares `color_order: "GRB"`. So the white die is currently
unreachable over `devicelink`, and Task B5's driver hardcodes `w=0`.

**This is a real decision, not a bug to silently fix.** Two options:

- **(recommended) Widen the wire to 48 ints (12 px x GRBW).** The part was chosen
  *for* its white die, and dropping it wastes both the component choice and the
  diffusion quality that justified it. The cost is a coordinated change across
  `protocol.py`, `shroom_capability`, the luxaeterna WebSim backend, and the Dart
  counterpart in `mm-tuneshroom/lib/link/envelope.dart`.
- **Keep 3 channels and synthesise white on the device.** Cheaper, no
  cross-repo change, but it makes the device a renderer rather than an LED sink,
  which contradicts `MM_HARDWARE_DESIGN.md` §4.4's "the device is an LED sink,
  not a renderer."

**What would change the recommendation:** if the browser simulator cannot
represent a separate white channel faithfully, parity between 2H and 2S breaks,
and the 3-channel path becomes the honest choice.

- [ ] **Step 1: Escalate to Chris with the two options before writing any code**

This crosses into the simulator, which is Chris-owned. Do not decide unilaterally.

- [ ] **Step 2: Write the decision into a dated spec file under `docs/superpowers/specs/`**

Record: the mismatch, both options, the decision, and who made it.

- [ ] **Step 3: Write the failing test for the chosen option**

If widening the wire, add to `mm-terrarium/tests/test_devicelink_protocol.py`:

```python
def test_leds_event_carries_48_channels_for_12_rgbw_pixels():
    msg = protocol.leds_event("ie1", list(range(48)))
    env = protocol.decode(msg)
    assert env.address == "/ie1/leds"
    assert env.typespec == "b"
    assert env.args[0] == list(range(48))


def test_leds_event_rejects_a_three_channel_frame():
    with pytest.raises(ValueError, match="48"):
        protocol.leds_event("ie1", list(range(36)))
```

- [ ] **Step 4: Run to verify failure**

Run: `cd mm-terrarium && python -m pytest tests/test_devicelink_protocol.py -v`
Expected: FAIL.

- [ ] **Step 5: Implement the chosen option across every affected site**

If widening: `protocol.leds_event` (validation and docstring),
`luxaeterna`'s `shroom_capability` colour order, `ShroomLEDs.show`'s expected
length, `ShroomClient.LED_CHANNELS`, the WebSim backend's pixel slice, and
`mm-tuneshroom/lib/link/envelope.dart`. The protocol docstring says explicitly
that the Dart counterpart changes together with it.

- [ ] **Step 6: Run both suites**

Run: `cd mm-terrarium && python -m pytest tests -v`
Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests -v`
Expected: all PASS.

- [ ] **Step 7: Verify on hardware that the white die now lights independently**

- [ ] **Step 8: Commit**

```bash
git add devicelink/protocol.py tests/test_devicelink_protocol.py docs/superpowers/specs/
git commit -m "feat(devicelink): carry the SK6812 white channel on the /leds wire"
```

## Task B8 [HW]: Tuneshroom first article

**Files:**
- Create: `mm-terrarium/docs/hardware/tuneshroom-runbook.md`

**Interfaces:**
- Consumes: everything from B1 through B7.
- Produces: one complete Tuneshroom, and the runbook Task B9 repeats three times.

**Roughly 30 terminations.** Build slowly; this article defines the next three.

- [ ] **Step 1: TRIM THE MT3608 BEFORE ANYTHING ELSE**

Connect the MT3608 input to the bench supply at 3.7 V, **with no load and
nothing else connected**. Adjust the trim pot until the output measures
**5.00 V ± 0.05 V** on a meter. Record the measured value.

An untrimmed MT3608 can output well over 20 V and will destroy the Radxa. This
step is a hard stop: do not connect the boost output to any board until the
meter reads 5.0 V. It is the single most expensive mistake available in this
plan.

- [ ] **Step 2: Build the power chain and verify it standalone**

18650 in holder, TP4056 across the cell, MT3608 from the cell to a 5 V rail.
Verify under a dummy load (a 10 Ω 5 W resistor draws 500 mA) that the rail holds
5.0 V. Verify TP4056 charging works and terminates.

- [ ] **Step 3: Land the subsystems one at a time, testing after each**

In this order, running the verification from its own task after each:
1. Radxa on the 5 V rail. Boot check (B1).
2. LIS3DH on I2C. Run `lis3dh_probe` (B4).
3. SK6812 chain on pin 7. Run `shroom_leds` (B5).
4. INMP441 and MAX98357A on I2S. Run the B2 Step 4 duplex check and B3.
5. Speaker driver.

Never land two subsystems between tests. A fault found after one termination is
a five-minute fix; found after ten it is an afternoon.

- [ ] **Step 4: Run the full client on the assembled unit**

```bash
python3 -m harness.shroom_client --server ws://10.44.0.10:8081 --dev ie1 --node node-a
```

All four Task B6 Step 6 pass criteria must hold on the assembled hardware.

- [ ] **Step 5: Measure runtime on a full cell**

Run the client continuously with LEDs and audio active until the unit powers
down. Record the time. `MM_HARDWARE_DESIGN.md` §9.3 predicts **~3.3 h**. Record
what you actually get; a large divergence means the power budget is wrong.

- [ ] **Step 6: Write the runbook**

Build order, the MT3608 trim procedure as a numbered stop-point, every
termination with its pin, the per-subsystem verification commands, the measured
runtime, and photographs of the harness.

- [ ] **Step 7: Commit**

```bash
git add docs/hardware/tuneshroom-runbook.md
git commit -m "docs(hardware): Tuneshroom first-article build runbook and measured runtime"
```

## Task B9 [HW]: Tuneshrooms #2, #3, #4

**Files:**
- Modify: `mm-terrarium/docs/hardware/tuneshroom-runbook.md`

- [ ] **Step 1: Swap builders for unit #2**

Whoever did not build the first article builds #2 from the runbook alone. Same
rule as Task A6: every question asked out loud is a runbook defect.

- [ ] **Step 2: Build #2, amending the runbook wherever it falls short**

- [ ] **Step 3: Build #3 and #4 from the amended runbook**

Record build time per unit. If unit #4 takes materially longer than #3, the
runbook still has gaps.

- [ ] **Step 4: Verify all four units independently**

Each unit joins as a distinct `dev` (`ie1` through `ie4`), receives LED frames,
and reports tilt. Then run **two units simultaneously** against one server,
which is the show configuration and is not proven by four separate single-unit
tests.

- [ ] **Step 5: Label and assign**

`SHROOM-SHOW-1`, `SHROOM-SHOW-2`, `SHROOM-BENCH-1`, `SHROOM-BENCH-2`.

- [ ] **Step 6: Commit**

```bash
git add docs/hardware/tuneshroom-runbook.md
git commit -m "docs(hardware): runbook corrections from units 2-4; four units verified"
```

## Task B10 [SW]: Local sample playback and the tap-latency measurement

**Files:**
- Create: `mm-terrarium/harness/local_sample.py`
- Create: `mm-terrarium/tests/test_local_sample.py`
- Modify: `mm-terrarium/docs/hardware/timing-measurements.md`

**Interfaces:**
- Consumes: `open_sensor` (B4), `ShroomClient` (B6).
- Produces: `SamplePlayer(sample_paths: dict[str, str])` with
  `.preload() -> None`, `.play(name: str) -> float` returning the dispatch
  latency in seconds, and `.last_latency_ms -> float`.

**Context:** `MM_HARDWARE_DESIGN.md` §4.4 is explicit that local sample playback
exists to preserve **tap to local sound under 20 ms** now that synthesis moved to
the Terrarium. Arco owns the room mix; the device owns the player's own ear. A
network round trip cannot meet 20 ms, which is the whole reason this component
exists.

**The measurement is the deliverable, not the playback.** "It feels responsive"
is not a result.

- [ ] **Step 1: Write the failing test**

```python
"""SamplePlayer: preloaded local playback with dispatch-latency accounting."""

from __future__ import annotations

import pytest

from harness.local_sample import SamplePlayer


class FakeSink:
    def __init__(self) -> None:
        self.played: list[str] = []

    def write(self, name: str, data: bytes) -> None:
        self.played.append(name)


def player(**kw) -> SamplePlayer:
    return SamplePlayer({"tap": "/tmp/tap.wav"}, sink=FakeSink(), **kw)


def test_preload_reads_every_sample_into_memory():
    p = player(loader=lambda path: b"\x00" * 128)
    p.preload()
    assert p.is_preloaded is True


def test_play_before_preload_raises():
    p = player(loader=lambda path: b"\x00" * 128)
    with pytest.raises(RuntimeError, match="preload"):
        p.play("tap")


def test_play_dispatches_to_the_sink():
    p = player(loader=lambda path: b"\x00" * 128)
    p.preload()
    p.play("tap")
    assert p.sink.played == ["tap"]


def test_play_returns_a_latency_and_records_it():
    p = player(loader=lambda path: b"\x00" * 128)
    p.preload()
    latency = p.play("tap")
    assert latency >= 0.0
    assert p.last_latency_ms == pytest.approx(latency * 1000.0)


def test_unknown_sample_name_raises():
    p = player(loader=lambda path: b"\x00" * 128)
    p.preload()
    with pytest.raises(KeyError):
        p.play("nope")


def test_preload_is_idempotent():
    calls = []

    def loader(path):
        calls.append(path)
        return b"\x00" * 128

    p = player(loader=loader)
    p.preload()
    p.preload()
    assert len(calls) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mm-terrarium && python -m pytest tests/test_local_sample.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'harness.local_sample'`

- [ ] **Step 3: Write the implementation**

```python
"""Local sample playback on the Tuneshroom.

Synthesis lives on the Terrarium, but 'tap -> local sound under 20 ms' cannot
survive a network round trip, so the immediate hit sounds from the device's own
speaker. Arco still owns the room mix and everything the hit implies beyond the
player's own ear.

Samples are read into memory at preload, never at play: a file read on the tap
path is the difference between 3 ms and 30 ms.
"""

from __future__ import annotations

import time


class SamplePlayer:
    """Preloaded PCM samples dispatched to an audio sink with latency accounting."""

    def __init__(self, sample_paths: dict[str, str], sink=None, loader=None) -> None:
        self.sample_paths = dict(sample_paths)
        self.sink = sink
        self._loader = loader or self._default_loader
        self._data: dict[str, bytes] = {}
        self.last_latency_ms: float = 0.0

    @staticmethod
    def _default_loader(path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    @property
    def is_preloaded(self) -> bool:
        return len(self._data) == len(self.sample_paths)

    def preload(self) -> None:
        """Read every sample into memory. Idempotent."""
        if self.is_preloaded:
            return
        for name, path in self.sample_paths.items():
            self._data[name] = self._loader(path)

    def play(self, name: str) -> float:
        """Dispatch *name* to the sink. Returns dispatch latency in seconds."""
        if not self.is_preloaded:
            raise RuntimeError("call preload() before play()")
        if name not in self._data:
            raise KeyError(name)
        started = time.perf_counter()
        if self.sink is not None:
            self.sink.write(name, self._data[name])
        latency = time.perf_counter() - started
        self.last_latency_ms = latency * 1000.0
        return latency
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mm-terrarium && python -m pytest tests/test_local_sample.py -v`
Expected: all PASS.

- [ ] **Step 5: Wire it to the tap interrupt on real hardware**

Add an ALSA sink and drive `play("tap")` from the LIS3DH tap interrupt in
`shroom_client.main()`. Preload at startup, never on the tap path.

- [ ] **Step 6: Measure end-to-end tap latency with a microphone, not a stopwatch**

Software timestamps measure dispatch, not sound. Record the physical tap and the
resulting sound on one audio track (a phone recorder next to the unit is
adequate), open the recording in any waveform editor, and measure the gap
between the tap transient and the sample onset. Take **20 taps** and record the
median and the worst.

Pass criterion: **median under 20 ms**. Record the worst case regardless.

- [ ] **Step 7: Append the results to `timing-measurements.md`**

Method, 20 samples, median, worst, and the audio buffer size in use. If the
median exceeds 20 ms, the ALSA period size is the first thing to reduce, and
record what you changed.

- [ ] **Step 8: Commit**

```bash
git add harness/local_sample.py tests/test_local_sample.py docs/hardware/timing-measurements.md
git commit -m "feat(harness): preloaded local sample playback; measured tap-to-sound latency"
```

---

# Phase C — Integration and gates

## Task C1 [HW]: Enclosure casting (runs in parallel from W3)

**Files:**
- Create: `mm-terrarium/docs/hardware/enclosure-notes.md`

**Interfaces:**
- Consumes: the feedback threshold from Task B3 Step 4; the zone mapping from B5 Step 5.
- Produces: 4 silicone Tuneshroom bodies.

**This starts in W3 and runs alongside everything else.** The body is
simultaneously light diffuser and acoustic resonance chamber, so it needs
iterations against both, which is why it cannot be a single pass in November.

- [ ] **Step 1: Print the first mould from the ~6 inch mushroom form**

- [ ] **Step 2: Cast body #1 and evaluate it optically**

Mount a lit 12-pixel array inside. Assess: are the 8 ring pixels individually
distinguishable, or has the silicone blurred them into one glow? Does the
4-pixel stem read separately from the ring? Photograph and record.

- [ ] **Step 3: Evaluate body #1 acoustically**

Mount the 50 mm driver and the INMP441 as they will sit in the final unit. Play
the Task B3 sine test and confirm the body reinforces rather than muffles. Re-run
the B3 Step 4 feedback check **inside the body**, since proximity in a small
cavity is exactly what causes feedback, and compare against the open-bench
threshold.

- [ ] **Step 4: Iterate wall thickness and pigment loading**

Thinner walls and lighter pigment improve pixel separation; thicker walls
improve acoustics. Record at least three variants with both measurements so the
tradeoff is documented rather than remembered.

- [ ] **Step 5: Cast the four production bodies from the chosen variant**

- [ ] **Step 6: Write `enclosure-notes.md`**

Mould source, silicone product and shore hardness, pigment loading, wall
thickness, cure schedule, and the optical and acoustic results per variant.

- [ ] **Step 7: Commit**

```bash
git add docs/hardware/enclosure-notes.md
git commit -m "docs(hardware): enclosure casting iterations with optical and acoustic results"
```

## Task C2 [GATE]: Gate 2 acceptance demonstration

**Files:**
- Create: `mm-terrarium/docs/hardware/gate2-acceptance.md`

**Target date: Fri 2026-10-16.**

- [ ] **Step 1: Assemble the complete show set**

`TERRARIUM-SHOW` + 6 m array + `SHROOM-SHOW-1` + `SHROOM-SHOW-2`, on the bench
subnet, everything in its enclosure.

- [ ] **Step 2: Run all five spec criteria in one sitting, recording each**

1. 864 px across 7 universes at sustained 44 Hz, **measured on the Pi 5**
   (`harness.render_bench`, Task A10).
2. Audio out the PCM5122 line-out (Task A3 Steps 4-5).
3. Both Tuneshrooms joined over `devicelink`, publishing tilt, rendering frames.
4. Tap to local sound **median under 20 ms, measured** (Task B10 Step 6).
5. A phone browser Tuneshroom substituted for `SHROOM-SHOW-2` mid-session with
   no venue-box reconfiguration.

- [ ] **Step 3: Record the outcome, pass or fail, with the actual numbers**

Do not round in your favour and do not record "approximately 44". Write what the
tool printed.

- [ ] **Step 4: If any criterion fails, cancel the o2lite window**

The Phase C o2lite window (Task C3) is contingent on Gate 2 passing. On failure
it is cancelled outright and its three weeks go to finishing hardware. This is a
standing decision from the spec, not a judgement call to make in the moment.

- [ ] **Step 5: On pass, seal the show set**

Physically separate it. From this point only `TERRARIUM-BENCH`, `SHROOM-BENCH-1`,
and `SHROOM-BENCH-2` are touched.

- [ ] **Step 6: Commit**

```bash
git add docs/hardware/gate2-acceptance.md
git commit -m "docs(hardware): Gate 2 acceptance results, show set sealed"
```

## Task C3 [SW]: o2lite integration window

**Files:**
- Create: `mm-terrarium/docs/hardware/o2lite-window-log.md`

**Window: 2026-10-19 to 2026-11-06. Bench twin only. Aborts at Lock regardless of state.**

**Precondition:** Gate 2 passed. If it did not, this task does not run.

**Standing fallback:** if the o2lite path is not demonstrably better than
`devicelink` on the bench twin by Nov 6, the show runs on `devicelink`. The
`devicelink` envelopes mirror o2ws field-for-field, so nothing built so far is
wasted either way.

- [ ] **Step 1: Confirm with Chris that an Arco server exists to talk to**

If `arcoserver/` still does not exist on Nov 6 minus three weeks, this window
has nothing to integrate against. Say so immediately rather than burning the
window on a dependency that has not landed.

- [ ] **Step 2: Stand up the Arco server on `TERRARIUM-BENCH` only**

Never on `TERRARIUM-SHOW`. The show set is sealed.

- [ ] **Step 3: Verify O2 discovery works on the bench subnet**

O2 discovery is UDP multicast. This is the first real test of the bench network
under the protocol it was provisioned for, so a failure here is a network
finding, not necessarily an O2 finding. Check both.

- [ ] **Step 4: Swap `ShroomClient`'s transport half for o2lite**

Only `main()` changes. `handle()`, `hello()`, `join()`, and `tilt()` are
transport-agnostic by construction and their tests must keep passing unchanged.
If they need to change, the abstraction was wrong and that is worth recording.

- [ ] **Step 5: Run the full mm-terrarium suite**

Run: `cd mm-terrarium && python -m pytest tests -v`
Expected: all PASS. Any protocol-level test that now fails is a real regression.

- [ ] **Step 6: Compare against `devicelink` on the same hardware**

Measure round-trip sensor-to-LED latency on both paths, on the bench twin, and
record both. A hop-count argument is not a measurement.

- [ ] **Step 7: Log the outcome and make the call by Fri Nov 6**

Record what worked, what did not, and the decision with its reasoning. If the
answer is "not ready", that is a successful outcome for this window: it was
bounded on purpose.

- [ ] **Step 8: Commit**

```bash
git add docs/hardware/o2lite-window-log.md
git commit -m "docs(hardware): o2lite integration window log and go/no-go decision"
```

## Task C4: Handoff documentation

**Files:**
- Create: `mm-terrarium/docs/hardware/README.md`
- Modify: `mm-terrarium/docs/MM_TERRARIUM.md`

**Target: W13, week of 2026-11-16, after Dry Run 2.**

- [ ] **Step 1: Write the hardware docs index**

`docs/hardware/README.md` linking every file this plan produced, with one line
each: bench inventory, both BOMs, first light, venue box runbook, Radxa runbook,
Tuneshroom runbook, array power, enclosure notes, timing measurements, Gate 2
acceptance, o2lite window log.

- [ ] **Step 2: Write the show-day runbook**

Cold start from powered-off to running Bit, in order, with the expected output of
each step. Assume the reader is tired and it is loud. Include the swap procedure
for substituting a bench unit for a failed show unit, and the phone-simulator
substitution from Gate 2 criterion 5.

- [ ] **Step 3: Update `docs/MM_TERRARIUM.md`**

The deep-dive currently says the venue target is bare-metal Linux on a Pi 5 and
that the real-time outputs "do not exist yet". After this plan they do. Update
the *Status* block and *Not yet built / deferred*, and add the measured 44 Hz and
tap-latency figures with a pointer to `timing-measurements.md`, replacing the
note that no measured figures exist.

- [ ] **Step 4: Run the deep-dive sync skill**

Follow `mm-deepdive-sync` so the deep-dive change lands on `main` properly rather
than sitting on a branch.

- [ ] **Step 5: Commit**

```bash
git add docs/hardware/README.md docs/MM_TERRARIUM.md
git commit -m "docs: hardware index, show-day runbook, deep-dive updated with measured figures"
```

---

## Self-review notes

**Spec coverage.** Every §3.1 done-criterion maps to a task: criterion 1 to A10,
2 to A3, 3 to B6 and B9, 4 to B10, 5 to C2 Step 2.5. Every §3.2 under-counted
item maps: `artnet.py` tests to A1, multi-universe to A4 and A5, array power
documentation to A9. Every §7 risk has a task step: campus multicast to A2
Step 1, PCM5122 to 0.2 Step 2 and A3 Step 3, strip variant to 0.3 Step 2,
overcurrent to A8 and A9 Step 7, MT3608 to B8 Step 1, Radxa stock to 0.2,
unblock bandwidth to the two-lane structure, Arco never landing to C3 Step 1.
All three §6 gates are represented: Gate 1 by B1 through B6 with the Sep 25 date,
Gate 2 by C2, Gate 3 by C3 Step 7. Both §10 open items that are actionable
appear: ETC tooling as Task 0.1, the Gantt-vs-plan reconciliation is a Chris
decision left in the spec.

**Cross-task type consistency checked:** `PixelSpan.locate` returns
`(universe_id, channel_offset)` and is consumed that way in `UniverseSet.fill_pixel`.
`PowerLimiter.apply` returns `bytearray` and is consumed as such in A9's
`_limited`. `ShroomClient.handle` returns `str` in every branch. `ShroomLEDs.show`
takes 36 bytes in B5 and B6 and becomes 48 only if Task B7 decides so, which is
why B7 lists every call site.
