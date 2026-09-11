# Student Hardware Track (ESP32) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For human workers (Sophia, Victor):** this plan is written for you. Tasks
> tagged **[FW]** are firmware and **[SW]** are Python; both follow a strict
> test-first cycle where a test can exist. Tasks tagged **[HW]** are physical
> builds and follow a verification protocol instead: *measure before you
> connect, and record the measurement.* Tasks tagged **[PROC]** are
> procurement and belong to Chris. Every task names its owner.

**Goal:** Mushica runs end to end on one ESP32 Tuneshroom and one ESP32 Tower
against the Terrarium on a Mac by Fri Oct 30 (Alt Ctrl), and again from a
cold start with a spare Tuneshroom by Fri Nov 13 (Dry Run 2).

**Architecture:** One firmware image (`firmware/`, PlatformIO, Arduino-ESP32
core) with two build targets: `tuneshroom` (sensors, 12 pixels, speaker) and
`tower` (120 pixels, no sensors). Both are thin o2lite devices: they report
timestamped gestures as `/game/<verb>` and display `/ie<N>/leds` frames at
their O2 `when`. All experience logic lives in the Mushica Bit
(`bits/mushica/`) on the Terrarium; the Tower is a Room fixture bound like any
device. Nothing outside `firmware/src/link/` touches the radio, so the board
target can change without a rewrite.

**Tech Stack:** ESP32-P4 + ESP32-C6 dev board (fallback: ESP32-S3), PlatformIO
with Arduino-ESP32 core 3.2+, vendored o2lite C (`o2/src`), Adafruit_NeoPixel,
Adafruit_LIS3DH, ESP32 touch peripheral, ESP32 I2S (`ESP_I2S.h`), MAX98357A.
Terrarium side: Python 3.12+, pytest, mm-terrarium `control/`, `bits/`,
`rooms/`, `instruments/`, `harness/`, `./terrarium.sh`.

**Spec:** [`docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md`](../specs/2026-09-11-student-hardware-track-esp32-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **Repos in play:** `mm-terrarium` (firmware, Bit, rooms, runbooks) and `o2`
  (read-only; vendored, never edited). Paths below are relative to
  `mm-terrarium` unless prefixed.
- **Test command, mm-terrarium:** `.venv/bin/python -m pytest tests -v`
  (never a bare `python3`; see `docs/MM_TERRARIUM.md`).
- **Build command, firmware:** `pio run -e tuneshroom` / `pio run -e tower`
  from `firmware/`; flash with `pio run -e <env> -t upload`; serial with
  `pio device monitor -b 115200`.
- **Bare metal only. The bench runs on its own AP and flat subnet, never
  campus wireless.** o2lite discovery is mDNS (`_o2proc._tcp`); client
  isolation or mDNS filtering on the AP looks like a firmware bug.
- **The ensemble name is `arco`** (`harness/run_stack.py` default). Every
  device joins that ensemble.
- **Device service names are `ie<N>`** (`ie1` for the first Tuneshroom, `ie9`
  for the Tower on the bench). Two devices must never share a service name.
- **No LED fixture is driven white without a current limiter in the
  firmware.** Tuneshroom: 12 px × 0.06 A = 0.72 A at 5 V, limiter at 0.6 A.
  Tower: 120 px × 0.025 A = 3.0 A at 12 V, limiter at 2.4 A.
- **Trim every MT3608 to 5.0 V on a bench supply, verified with a meter,
  before it is connected to any board.** Stop-point, not a preference.
- **Any timing figure must be measured on the target hardware.** Mushica's
  perfect window is ±30 ms; the tap timestamp budget is 10 ms.
- **The Tuneshroom wire is GRB, 3 channels per pixel** (12 px = 36 bytes).
  The mini pixels are RGBW parts; their W channel is always 0 (decided
  2026-08-19, no widening).
- **Anything wrong inside `o2/src` is reported to Roger Dannenberg with a
  minimal reproduction, never patched in the vendored copy.**
- **Commit after every green test or verified measurement.** Small commits,
  present-tense messages, no em dashes anywhere in prose.

---

# Phase 0: Week 4 checkpoint (Sep 14 to Sep 18)

Three tasks that must all finish this week. 0.3 is the one with zero slack.

## Task 0.1 [HW]: Bench network, Terrarium Mac, mDNS verified

**Owner:** Sophia. **Files:** Create `docs/hardware/bench-network.md`.

**Interfaces:**
- Produces: the SSID, passphrase and subnet every firmware build flag uses
  (Task 0.3), and a verified `_o2proc._tcp` advertisement from the Mac.

- [ ] **Step 1: Configure the AP as a flat network**

On the dedicated AP: one SSID, WPA2, DHCP on a private /24, **client
isolation off**, **multicast and mDNS passthrough on** (some consumer APs
call this "IGMP snooping", turn it off), no guest mode. Record SSID, subnet
and gateway in `docs/hardware/bench-network.md`.

- [ ] **Step 2: Put the Terrarium Mac on it and stand the Terrarium up**

**RUN ON: the Terrarium Mac**

```bash
cd ~/projects/mm-terrarium && ./terrarium.sh --room TEST
```

Expected: the Console URL prints; open it in a browser on the same Mac and
confirm the page loads with `Room: TEST`.

- [ ] **Step 3: Verify the O2 host is discoverable from a second machine**

**RUN ON: any laptop on the bench AP**

```bash
dns-sd -B _o2proc._tcp
```

Expected within 5 s: one line naming the Terrarium Mac's host. If nothing
appears in 30 s, the AP is filtering mDNS; fix the AP before any firmware
is flashed. Record the exact `dns-sd` output in the doc.

- [ ] **Step 4: Commit**

```bash
git add docs/hardware/bench-network.md
git commit -m "docs(hardware): bench AP settings and verified O2 host discovery"
```

## Task 0.2 [PROC]: Tower parts list and order

**Owner:** Sophia lists, Chris orders. **Files:** Create
`docs/hardware/tower-bom.md`.

- [ ] **Step 1: Write the list against the Tower definition and spec section 9**

| Line | Qty | Constraint to check in the listing body |
|---|---|---|
| 12 V SK6812 RGBW strip, 60 px/m, IP30 | 2.5 m (8 × 250 mm + spare) | "addressable per LED", not "per 3 LEDs" |
| 12 V 5 A supply, barrel jack, inline fuse holder + 5 A fuse | 1 + 2 fuses | UL listed |
| 3-pin JST-SM pigtails, 18 AWG silicone wire (red, black, white) | 10 pairs, 3 m each colour | |
| Diffusion film sheet | enough for 8 × 250 × 40 mm | |
| End-glow fiber bundle, 2 to 3 m | 3 | |
| Mast: 25 mm aluminium channel or 32 mm PVC, 2100 mm | 1 | joins at 700 mm if shipped short |
| Weighted base plate | 1 | |
| PAR light (LED, mains, manual colour) with stand | 2 | not DMX-controlled for Dec 4 |
| ESP32-S3 dev board (Tower controller and the Task 0.3 fallback) | 2 | |
| 74AHCT125 level shifter breakout | 2 | 3.3 V data to 5 V/12 V strip |
| Filament or ETC print time for connectors and responders | as needed | |

- [ ] **Step 2: Price each line from the vendor page, total it, and hand the doc to Chris**

- [ ] **Step 3: Chris orders, forwards receipts to Rho, and records the order numbers and expected arrival in the doc**

- [ ] **Step 4: Commit**

```bash
git add docs/hardware/tower-bom.md
git commit -m "docs(hardware): Tower parts list, priced and ordered"
```

## Task 0.3 [FW]: PlatformIO project; the P4 joins the bench WiFi

**Owner:** Victor. **Checkpoint: Fri Sep 18.**

**Files:**
- Create: `firmware/platformio.ini`, `firmware/src/main.cpp`,
  `firmware/include/config.h`, `firmware/README.md`, `firmware/.gitignore`

**Interfaces:**
- Produces: two PlatformIO environments `tuneshroom` and `tower`, a
  `config.h` with `WIFI_SSID`, `WIFI_PASS`, `DEVICE_NAME`, `ENSEMBLE`, and a
  serial line `WIFI OK <ip>` that every later task's checks start from.

- [ ] **Step 1: Create the project**

```ini
; firmware/platformio.ini
[platformio]
default_envs = tuneshroom

[env]
framework = arduino
monitor_speed = 115200
build_flags =
    -DENSEMBLE=\"arco\"
    -DWIFI_SSID=\"${sysenv.MM_BENCH_SSID}\"
    -DWIFI_PASS=\"${sysenv.MM_BENCH_PASS}\"
lib_deps =
    adafruit/Adafruit NeoPixel@^1.12
    adafruit/Adafruit LIS3DH@^1.3
    adafruit/Adafruit BusIO@^1.16

[env:tuneshroom]
platform = espressif32@^6.10
board = esp32-p4-evboard
build_flags = ${env.build_flags} -DDEVICE_NAME=\"ie1\" -DTUNESHROOM=1 -DPIXEL_COUNT=12

[env:tower]
platform = espressif32@^6.10
board = esp32-s3-devkitc-1
build_flags = ${env.build_flags} -DDEVICE_NAME=\"ie9\" -DTOWER=1 -DPIXEL_COUNT=120

; Fallback target for the Tuneshroom if the P4 radio path fails the Sep 18
; checkpoint (spec 4.1). Same code, different board.
[env:tuneshroom-s3]
platform = espressif32@^6.10
board = esp32-s3-devkitc-1
build_flags = ${env.build_flags} -DDEVICE_NAME=\"ie1\" -DTUNESHROOM=1 -DPIXEL_COUNT=12
```

If `esp32-p4-evboard` is not a known board id in the installed platform,
run `pio boards esp32-p4` and use the id it prints; record it in
`firmware/README.md`. The P4 needs Arduino-ESP32 core 3.2 or later; if the
`espressif32` platform pins an older core, add
`platform = https://github.com/pioarduino/platform-espressif32/releases/download/stable/platform-espressif32.zip`
and note that in the README.

```gitignore
# firmware/.gitignore
.pio/
.vscode/
```

- [ ] **Step 2: Write the WiFi smoke sketch**

```cpp
// firmware/include/config.h
#pragma once
#ifndef ENSEMBLE
#define ENSEMBLE "arco"
#endif
#ifndef DEVICE_NAME
#define DEVICE_NAME "ie1"
#endif
#ifndef PIXEL_COUNT
#define PIXEL_COUNT 12
#endif
#define HEARTBEAT_S 5.0
```

```cpp
// firmware/src/main.cpp
#include <Arduino.h>
#include <WiFi.h>
#include "config.h"

void setup() {
  Serial.begin(115200);
  delay(500);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(DEVICE_NAME);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print('.');
    if (millis() - t0 > 30000) {
      Serial.println("\nWIFI FAIL");
      ESP.restart();
    }
  }
  Serial.printf("\nWIFI OK %s\n", WiFi.localIP().toString().c_str());
}

void loop() { delay(1000); }
```

- [ ] **Step 3: Build, flash, watch serial**

```bash
cd firmware && export MM_BENCH_SSID=... MM_BENCH_PASS=... \
  && pio run -e tuneshroom -t upload && pio device monitor -b 115200
```

Expected: `WIFI OK 10.x.x.x` within 30 s of reset. From the Terrarium Mac,
`ping <that ip>` answers.

- [ ] **Step 4: Decide the board target (checkpoint)**

If `WIFI OK` prints on the P4 by Fri Sep 18: continue on `tuneshroom`.
If not, after at most one day on the C6/ESP-Hosted path: switch
`default_envs` to `tuneshroom-s3`, flash an S3 from the Tower order (Task
0.2 line 9), and record the decision and the failure symptom in
`firmware/README.md`. Tell Chris the same day; the project plan's spec 4.1
names this outcome and nothing downstream changes.

- [ ] **Step 5: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): PlatformIO project, WiFi smoke on the bench AP"
```

---

# Phase A: Firmware (Victor)

## Task A1 [FW]: o2lite hello reaches Control

**Files:**
- Create: `firmware/src/link/o2lite/` (vendored copies of `o2/src/o2lite.c`,
  `o2lite.h`, `o2base.h`, `hostip.h`, `hostipimpl.h`, `o2liteesp32.cpp`,
  `o2liteesp32.h`), `firmware/src/link/link.h`, `firmware/src/link/link.cpp`
- Modify: `firmware/src/main.cpp`

**Interfaces:**
- Produces: `link_begin()`, `link_poll()`, `bool link_synced()`,
  `double link_time()` (O2 time, or -1 before sync), `void link_hello()`.
  Every later firmware task calls `link_poll()` from `loop()` and stamps
  events with `link_time()`.

- [ ] **Step 1: Vendor o2lite exactly as `o2/arduino/README.md` says**

```bash
mkdir -p firmware/src/link/o2lite
for f in o2lite.c o2lite.h o2base.h hostip.h hostipimpl.h o2liteesp32.cpp o2liteesp32.h; do
  cp ~/projects/o2/src/$f firmware/src/link/o2lite/$f
done
echo "Vendored from o2 commit $(git -C ~/projects/o2 rev-parse --short HEAD) on $(date +%F). Do not edit; report defects to Roger." > firmware/src/link/o2lite/VENDORED.md
```

Do **not** copy `hostip.c` (the README says so; `hostipimpl.h` replaces it).

- [ ] **Step 2: Write the link module**

```cpp
// firmware/src/link/link.h
#pragma once
void link_begin();          // WiFi, o2l_initialize, set_services, methods
void link_poll();           // call every loop()
bool link_synced();         // clock sync complete
double link_time();         // O2 time or -1.0
void link_hello();          // /game/hello, resent every HEARTBEAT_S
```

```cpp
// firmware/src/link/link.cpp
#include <Arduino.h>
#include "link.h"
#include "config.h"
extern "C" {
#include "o2lite/o2lite.h"
}

static double last_hello = -1e9;

void link_begin() {
  connect_to_wifi(DEVICE_NAME, WIFI_SSID, WIFI_PASS);   // blocks until joined
  o2l_initialize(ENSEMBLE);
  o2l_set_services(DEVICE_NAME);
  Serial.printf("O2LITE INIT ensemble=%s service=%s\n", ENSEMBLE, DEVICE_NAME);
}

void link_poll() {
  o2l_poll();
  if (link_synced()) {
    double now = link_time();
    if (now - last_hello >= HEARTBEAT_S) { link_hello(); last_hello = now; }
  }
}

bool link_synced() { return o2l_time_get() >= 0; }
double link_time() { return o2l_time_get(); }

void link_hello() {
  // Same shape as harness/o2_shroom.py's undeclared hello: typespec "s".
  o2l_send_start("/game/hello", 0, "s", true);   // true = tcp (command)
  o2l_add_string(DEVICE_NAME);
  o2l_send();
  Serial.printf("HELLO sent t=%.3f\n", link_time());
}
```

```cpp
// firmware/src/main.cpp
#include <Arduino.h>
#include "config.h"
#include "link/link.h"

void setup() {
  Serial.begin(115200);
  delay(500);
  link_begin();
}

void loop() {
  link_poll();
  static bool announced = false;
  if (!announced && link_synced()) {
    Serial.printf("CLOCK SYNCED t=%.3f\n", link_time());
    announced = true;
  }
  delay(2);
}
```

- [ ] **Step 3: Build and flash; watch for sync and hello**

```bash
cd firmware && pio run -e tuneshroom -t upload && pio device monitor -b 115200
```

Expected: `O2LITE INIT`, then `CLOCK SYNCED t=...` within 10 s of WiFi,
then `HELLO sent` every 5 s. If o2lite prints `mdns_query_ptr Failed`, go
back to Task 0.1 Step 3; the network is the problem.

- [ ] **Step 4: Confirm Control saw it**

**RUN ON: the Terrarium Mac**, with `./terrarium.sh --room TEST` running:

```bash
grep -E "hello.*ie1|ie1.*hello" ~/projects/mm-terrarium/control.log | tail -3
```

Expected: a hello line for `ie1`. Open the Console and confirm `ie1` is in
the device list. Screenshot it into `firmware/README.md`.

- [ ] **Step 5: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): vendored o2lite, link module, /game/hello heartbeat reaches Control"
```

## Task A2 [FW]: Join, role, room, release, and reconnect

**Files:**
- Modify: `firmware/src/link/link.h`, `firmware/src/link/link.cpp`,
  `firmware/src/main.cpp`

**Interfaces:**
- Consumes: `link_*` from A1.
- Produces: `void link_join(const char *node)`; `void link_on_frame(void (*cb)(double when, const uint8_t *grb, int n))`;
  `void link_on_play(void (*cb)(const char *name, const char *params))`;
  `void link_on_release(void (*cb)())`; `bool link_joined()`.
  `JOIN_NODE` build flag (default `"MUSHICA_PLAYER_NODE"`, Task C2 declares it).

- [ ] **Step 1: Add the handlers**

```cpp
// firmware/src/link/link.cpp  (additions)
static void (*frame_cb)(double, const uint8_t *, int) = nullptr;
static void (*play_cb)(const char *, const char *) = nullptr;
static void (*release_cb)() = nullptr;
static bool joined = false;
static char addr_role[32], addr_leds[32], addr_play[32], addr_release[32], addr_room[32];

static void on_role(o2l_msg_ptr, const char *types, void *, void *) {
  o2l_blob_ptr b = o2l_get_blob();          // config blob, JSON; kept opaque in Rev 1
  joined = true;
  Serial.printf("ROLE received %d bytes\n", b ? b->size : 0);
}
static void on_room(o2l_msg_ptr, const char *, void *, void *) {
  o2l_get_blob();                            // informational, ignored (ShroomClient does the same)
}
static void on_leds(o2l_msg_ptr, const char *types, void *, void *) {
  o2l_blob_ptr b = o2l_get_blob();
  if (!b || b->size != PIXEL_COUNT * 3) {   // wrong width: drop, never truncate
    Serial.printf("DROP leds size=%d expected=%d\n", b ? b->size : -1, PIXEL_COUNT * 3);
    return;
  }
  if (frame_cb) frame_cb(o2l_get_timestamp(), (const uint8_t *)b->data, PIXEL_COUNT);
}
static void on_play(o2l_msg_ptr, const char *, void *, void *) {
  const char *name = o2l_get_string();
  const char *params = o2l_get_string();
  if (play_cb) play_cb(name, params);
}
static void on_release(o2l_msg_ptr, const char *, void *, void *) {
  joined = false;
  if (release_cb) release_cb();
}

static void register_methods() {
  snprintf(addr_role, 32, "/%s/role", DEVICE_NAME);
  snprintf(addr_room, 32, "/%s/room", DEVICE_NAME);
  snprintf(addr_leds, 32, "/%s/leds", DEVICE_NAME);
  snprintf(addr_play, 32, "/%s/play", DEVICE_NAME);
  snprintf(addr_release, 32, "/%s/release", DEVICE_NAME);
  o2l_method_new(addr_role, "b", true, on_role, NULL);
  o2l_method_new(addr_room, "b", true, on_room, NULL);
  o2l_method_new(addr_leds, "b", true, on_leds, NULL);
  o2l_method_new(addr_play, "ss", true, on_play, NULL);
  o2l_method_new(addr_release, "", true, on_release, NULL);
}

void link_join(const char *node) {
  o2l_send_start("/game/join", 0, "ss", true);
  o2l_add_string(DEVICE_NAME);
  o2l_add_string(node);
  o2l_send();
  Serial.printf("JOIN sent node=%s\n", node);
}
bool link_joined() { return joined; }
void link_on_frame(void (*cb)(double, const uint8_t *, int)) { frame_cb = cb; }
void link_on_play(void (*cb)(const char *, const char *)) { play_cb = cb; }
void link_on_release(void (*cb)()) { release_cb = cb; }
```

Call `register_methods()` at the end of `link_begin()`, after
`o2l_set_services`. In `link_poll()`, after the heartbeat, add: if synced,
not joined, and 3 s have passed since the last join attempt, call
`link_join(JOIN_NODE)` (mirrors `--join-every` in `o2_shroom.py`). Add
`-DJOIN_NODE=\"MUSHICA_PLAYER_NODE\"` to the `tuneshroom` env in
`platformio.ini`.

- [ ] **Step 2: Reconnect**

In `link_poll()`, if `WiFi.status() != WL_CONNECTED`, call `o2l_finish()`,
then `link_begin()` again, and reset `joined = false`. Test it by power
cycling the AP: the device must be back in the Console list within 30 s of
the AP returning, with no reflash.

- [ ] **Step 3: Verify against a running Bit**

**RUN ON: the Terrarium Mac**: `./terrarium.sh --room TEST`, then in the
Console load **Chase** (its player node is `TEST_PLAYER_NODE`). For this
check only, build with `-DJOIN_NODE=\"TEST_PLAYER_NODE\"`. Expected on the
device serial: `JOIN sent`, then `ROLE received N bytes`. Expected in the
Console: `ie1` holds role `player`. When the Bit unloads, the serial prints
nothing for release yet (no callback wired) but `link_joined()` reads false;
confirm by adding a one-line print in `on_release`.

- [ ] **Step 4: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): join, role/room/leds/play/release handlers, WiFi reconnect"
```

## Task A3 [FW]: Timed LED frames on the 12 pixels, with a current limiter

**Files:**
- Create: `firmware/src/render/frames.h`, `firmware/src/render/frames.cpp`,
  `firmware/test/test_frames/test_frames.cpp`
- Modify: `firmware/src/main.cpp`, `firmware/platformio.ini` (native test env)

**Interfaces:**
- Consumes: `link_on_frame`, `link_time`.
- Produces: `frames_begin(pin, count)`, `frames_push(when, grb, n)`,
  `frames_tick(now)` (shows the newest frame whose `when <= now`),
  `frames_limit(const uint8_t *grb, int n, float max_amps)` (pure; scales a
  frame so the modelled draw stays under `max_amps`), `int frames_late()`.

- [ ] **Step 1: Write the failing native test for the limiter and the queue**

Add to `platformio.ini`:

```ini
[env:native]
platform = native
build_flags = -DPIXEL_COUNT=12 -std=c++17
test_build_src = no
```

```cpp
// firmware/test/test_frames/test_frames.cpp
#include <unity.h>
#include <string.h>
#include "../../src/render/frames_core.h"   // pure part, no Arduino

void test_limiter_leaves_dim_frame_alone() {
  uint8_t f[36]; memset(f, 10, 36);
  float scale = frames_scale_for(f, 12, 0.6f, 0.02f);   // 0.02 A per channel at 255
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.0f, scale);
}
void test_limiter_scales_full_white() {
  uint8_t f[36]; memset(f, 255, 36);
  // 36 channels * 0.02 A = 0.72 A modelled; cap 0.6 A -> scale 0.8333
  float scale = frames_scale_for(f, 12, 0.6f, 0.02f);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.8333f, scale);
}
void test_queue_shows_frame_at_when_not_before() {
  FrameQueue q;
  uint8_t a[36]; memset(a, 1, 36);
  q.push(10.0, a, 12);
  TEST_ASSERT_FALSE(q.due(9.99));
  TEST_ASSERT_TRUE(q.due(10.0));
}
void test_queue_undeclared_time_shows_next_tick() {
  FrameQueue q;
  uint8_t a[36]; memset(a, 1, 36);
  q.push(0.0, a, 12);                        // 0.0 = "no time", like TimedQueue
  TEST_ASSERT_TRUE(q.due(0.5));
  TEST_ASSERT_EQUAL(0, q.late());
}
void test_queue_counts_late_frames() {
  FrameQueue q;
  uint8_t a[36]; memset(a, 1, 36);
  q.push(5.0, a, 12);
  q.due(6.0);
  TEST_ASSERT_EQUAL(1, q.late());
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_limiter_leaves_dim_frame_alone);
  RUN_TEST(test_limiter_scales_full_white);
  RUN_TEST(test_queue_shows_frame_at_when_not_before);
  RUN_TEST(test_queue_undeclared_time_shows_next_tick);
  RUN_TEST(test_queue_counts_late_frames);
  return UNITY_END();
}
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd firmware && pio test -e native`
Expected: compile error, `frames_core.h` not found.

- [ ] **Step 3: Write the pure core**

```cpp
// firmware/src/render/frames_core.h
#pragma once
#include <stdint.h>

// Scale factor (<= 1.0) so that sum(channels)/255 * amps_per_channel <= max_amps.
inline float frames_scale_for(const uint8_t *grb, int n, float max_amps, float amps_per_channel) {
  float draw = 0.0f;
  for (int i = 0; i < n * 3; i++) draw += (grb[i] / 255.0f) * amps_per_channel;
  return draw <= max_amps ? 1.0f : max_amps / draw;
}

struct FrameQueue {
  static const int CAP = 8;
  double when[CAP]; uint8_t data[CAP][PIXEL_COUNT * 3]; int n = 0; int late_count = 0;
  bool has_current = false; uint8_t current[PIXEL_COUNT * 3];

  void push(double w, const uint8_t *grb, int count) {
    if (n == CAP) { for (int i = 1; i < CAP; i++) { when[i-1] = when[i]; memcpy(data[i-1], data[i], PIXEL_COUNT*3); } n--; }
    when[n] = w; memcpy(data[n], grb, count * 3); n++;
  }
  // Promote the newest frame whose when <= now (0.0 means "no time": due now).
  bool due(double now) {
    int pick = -1;
    for (int i = 0; i < n; i++) if (when[i] == 0.0 || when[i] <= now) pick = i;
    if (pick < 0) return false;
    if (when[pick] != 0.0 && now - when[pick] > 0.020) late_count++;   // >20 ms past its time
    memcpy(current, data[pick], PIXEL_COUNT * 3); has_current = true;
    for (int i = pick + 1, j = 0; i < n; i++, j++) { when[j] = when[i]; memcpy(data[j], data[i], PIXEL_COUNT*3); }
    n -= pick + 1;
    return true;
  }
  int late() const { return late_count; }
};
```

- [ ] **Step 4: Run the tests**

Run: `cd firmware && pio test -e native`
Expected: 5 tests PASS.

- [ ] **Step 5: Write the Arduino half and wire it**

```cpp
// firmware/src/render/frames.h
#pragma once
#include <stdint.h>
void frames_begin(int pin, int count, float max_amps);
void frames_push(double when, const uint8_t *grb, int n);
void frames_tick(double now);
int  frames_late();
```

```cpp
// firmware/src/render/frames.cpp
#include <Adafruit_NeoPixel.h>
#include "frames.h"
#include "frames_core.h"

static Adafruit_NeoPixel *strip = nullptr;
static FrameQueue q;
static float cap_amps = 0.6f;
#ifdef TOWER
static const float AMPS_PER_CHANNEL = 0.025f / 3.0f;   // 12 V strip, per channel at full
#else
static const float AMPS_PER_CHANNEL = 0.06f / 3.0f;    // 5 V mini pixel, per channel at full
#endif

void frames_begin(int pin, int count, float max_amps) {
  cap_amps = max_amps;
  strip = new Adafruit_NeoPixel(count, pin, NEO_GRBW + NEO_KHZ800);
  strip->begin(); strip->clear(); strip->show();
}
void frames_push(double when, const uint8_t *grb, int n) { q.push(when, grb, n); }
void frames_tick(double now) {
  if (!q.due(now)) return;
  float s = frames_scale_for(q.current, PIXEL_COUNT, cap_amps, AMPS_PER_CHANNEL);
  for (int i = 0; i < PIXEL_COUNT; i++) {
    uint8_t g = q.current[i*3] * s, r = q.current[i*3+1] * s, b = q.current[i*3+2] * s;
    strip->setPixelColor(i, strip->Color(r, g, b, 0));   // W always 0 (no widening)
  }
  strip->show();
}
int frames_late() { return q.late(); }
```

In `main.cpp`: `frames_begin(PIXEL_PIN, PIXEL_COUNT, 0.6f)` in `setup()`
(add `#define PIXEL_PIN <gpio>` to `config.h`; use an RMT-capable GPIO from
the board's pinout and record the number in `docs/hardware/tuneshroom-runbook.md`),
`link_on_frame(frames_push)` after `link_begin()`, and `frames_tick(link_time())`
in `loop()`. Every 10 s print `LATE <n>` from `frames_late()`.

- [ ] **Step 6: Verify on the bench with a real Bit**

Flash; run `./terrarium.sh --room TEST`, load Chase, join. Expected: the 12
pixels follow the Chase pattern. Then on the Mac run
`harness/sync_bench.py` per its `--help` against `--horizon 0` output if a
clamp figure is wanted; for this task the acceptance is visual plus
`LATE 0` over 60 s at the default horizon.

- [ ] **Step 7: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): timed frame queue and current limiter on 12 GRBW pixels"
```

## Task A4 [FW]: Gestures with O2 timestamps: tap, hold, swing

**Files:**
- Create: `firmware/src/sense/gestures.h`, `firmware/src/sense/gestures.cpp`,
  `firmware/src/sense/gestures_core.h`,
  `firmware/test/test_gestures/test_gestures.cpp`
- Modify: `firmware/src/main.cpp`, `firmware/include/config.h`

**Interfaces:**
- Consumes: `link_time()`, `o2l_send_*` (through a `link_send_gesture` added here).
- Produces on the wire, all with the device's O2 stamp as the message time:
  - `/game/tap  "sffi"  dev, peak_g, duration_ms, count` (existing shape; `bits/test/test_bit.py` documents it)
  - `/game/hold "sfi"   dev, held_seconds, count` (new verb, Task C2 handles it)
  - `/game/swing "sfi"  dev, signed_peak_g, count` (new verb; negative = left)

- [ ] **Step 1: Write the failing test for the pure classifier**

```cpp
// firmware/test/test_gestures/test_gestures.cpp
#include <unity.h>
#include "../../src/sense/gestures_core.h"

void test_touch_release_under_250ms_is_tap() {
  TouchClassifier c;
  c.down(1.000);
  Gesture g = c.up(1.180);
  TEST_ASSERT_EQUAL(GESTURE_TAP, g.kind);
}
void test_touch_held_400ms_is_hold_with_duration() {
  TouchClassifier c;
  c.down(2.000);
  Gesture g = c.up(2.650);
  TEST_ASSERT_EQUAL(GESTURE_HOLD, g.kind);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.65f, g.value);
}
void test_touch_between_windows_is_nothing() {
  TouchClassifier c;
  c.down(3.000);
  Gesture g = c.up(3.300);        // 300 ms: not a tap, not yet a hold
  TEST_ASSERT_EQUAL(GESTURE_NONE, g.kind);
}
void test_tap_stamp_is_the_down_time() {
  TouchClassifier c;
  c.down(4.000);
  Gesture g = c.up(4.100);
  TEST_ASSERT_FLOAT_WITHIN(0.0001, 4.000, g.at);
}
void test_swing_needs_sustained_lateral_g() {
  SwingDetector s(1.5f, 0.080f);             // 1.5 g for 80 ms
  Gesture g = GESTURE_EMPTY;
  for (int i = 0; i < 5; i++) g = s.sample(5.0 + i * 0.02, +2.0f);   // 100 ms at +2 g
  TEST_ASSERT_EQUAL(GESTURE_SWING, g.kind);
  TEST_ASSERT_TRUE(g.value > 0);
}
void test_swing_brief_spike_ignored() {
  SwingDetector s(1.5f, 0.080f);
  Gesture g = s.sample(6.00, +3.0f);
  g = s.sample(6.02, 0.0f);
  TEST_ASSERT_EQUAL(GESTURE_NONE, g.kind);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_touch_release_under_250ms_is_tap);
  RUN_TEST(test_touch_held_400ms_is_hold_with_duration);
  RUN_TEST(test_touch_between_windows_is_nothing);
  RUN_TEST(test_tap_stamp_is_the_down_time);
  RUN_TEST(test_swing_needs_sustained_lateral_g);
  RUN_TEST(test_swing_brief_spike_ignored);
  return UNITY_END();
}
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd firmware && pio test -e native -f test_gestures`
Expected: compile error, `gestures_core.h` not found.

- [ ] **Step 3: Write the pure core**

```cpp
// firmware/src/sense/gestures_core.h
#pragma once
enum GestureKind { GESTURE_NONE, GESTURE_TAP, GESTURE_HOLD, GESTURE_SWING };
struct Gesture { GestureKind kind; double at; float value; };
#define GESTURE_EMPTY Gesture{GESTURE_NONE, 0.0, 0.0f}

struct TouchClassifier {
  static constexpr double TAP_MAX_S = 0.250, HOLD_MIN_S = 0.400;
  double down_at = -1.0;
  void down(double t) { down_at = t; }
  Gesture up(double t) {
    if (down_at < 0) return GESTURE_EMPTY;
    double held = t - down_at; double at = down_at; down_at = -1.0;
    if (held <= TAP_MAX_S) return Gesture{GESTURE_TAP, at, (float)held};
    if (held >= HOLD_MIN_S) return Gesture{GESTURE_HOLD, at, (float)held};
    return GESTURE_EMPTY;
  }
};

struct SwingDetector {
  float thresh_g; double min_s; double over_since = -1.0; float peak = 0.0f; bool fired = false;
  SwingDetector(float g, double s) : thresh_g(g), min_s(s) {}
  Gesture sample(double t, float lateral_g) {
    float mag = lateral_g < 0 ? -lateral_g : lateral_g;
    if (mag >= thresh_g) {
      if (over_since < 0) { over_since = t; peak = lateral_g; fired = false; }
      if (mag > (peak < 0 ? -peak : peak)) peak = lateral_g;
      if (!fired && t - over_since >= min_s) { fired = true; return Gesture{GESTURE_SWING, over_since, peak}; }
    } else { over_since = -1.0; fired = false; }
    return GESTURE_EMPTY;
  }
};
```

- [ ] **Step 4: Run the tests**

Run: `cd firmware && pio test -e native -f test_gestures`
Expected: 6 tests PASS.

- [ ] **Step 5: Write the Arduino half**

```cpp
// firmware/src/sense/gestures.h
#pragma once
void gestures_begin();
void gestures_poll(double now);     // reads touch + LIS3DH, sends verbs
```

```cpp
// firmware/src/sense/gestures.cpp
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_LIS3DH.h>
#include "gestures.h"
#include "gestures_core.h"
#include "../link/link.h"
#include "config.h"

static Adafruit_LIS3DH lis;
static TouchClassifier touch;
static SwingDetector swing(1.5f, 0.080);
static bool touching = false;
static int tap_count = 0, hold_count = 0, swing_count = 0;
static uint16_t touch_baseline = 0;

void gestures_begin() {
  Wire.begin();
  if (!lis.begin(0x18) && !lis.begin(0x19)) Serial.println("LIS3DH MISSING");
  lis.setRange(LIS3DH_RANGE_4_G);
  lis.setDataRate(LIS3DH_DATARATE_400_HZ);
  lis.setClick(1, 40);                       // hardware click kept as the fallback tap source
  uint32_t acc = 0; for (int i = 0; i < 16; i++) acc += touchRead(TOUCH_PIN);
  touch_baseline = acc / 16;
  Serial.printf("TOUCH baseline=%u\n", touch_baseline);
}

static void send(const Gesture &g) {
  switch (g.kind) {
    case GESTURE_TAP:
      link_send_gesture("/game/tap", g.at, "sffi", 0.0f, g.value * 1000.0f, ++tap_count); break;
    case GESTURE_HOLD:
      link_send_gesture("/game/hold", g.at, "sfi", g.value, 0.0f, ++hold_count); break;
    case GESTURE_SWING:
      link_send_gesture("/game/swing", g.at, "sfi", g.value, 0.0f, ++swing_count); break;
    default: return;
  }
  Serial.printf("GESTURE %d at=%.3f value=%.3f\n", g.kind, g.at, g.value);
}

void gestures_poll(double now) {
  // Touch: on the P4/S3 touchRead rises when touched; threshold is 1.4x baseline.
  bool t = touchRead(TOUCH_PIN) > touch_baseline * 1.4;
  if (t && !touching) { touch.down(now); touching = true; }
  if (!t && touching) { send(touch.up(now)); touching = false; }
  // Accelerometer: lateral axis is X in the enclosure's frame (record which in the runbook).
  lis.read();
  float lateral_g = lis.x_g;
  send(swing.sample(now, lateral_g));
  // Fallback tap source, reported on serial only until Task B2 decides which wins.
  uint8_t click = lis.getClick();
  if (click & 0x10) Serial.printf("CLICK at=%.3f\n", now);
}
```

Add to `link.h`/`link.cpp`:

```cpp
void link_send_gesture(const char *addr, double at, const char *types, float a, float b, int count) {
  o2l_send_start(addr, at, types, false);    // udp, timestamped at the gesture
  o2l_add_string(DEVICE_NAME);
  if (types[1] == 'f') o2l_add_float(a);
  if (types[2] == 'f') o2l_add_float(b);
  o2l_add_int32(count);
  o2l_send();
}
```

Add `#define TOUCH_PIN <touch-capable gpio>` and `#define I2C_SDA`/`I2C_SCL`
if the board's defaults are not the LIS3DH's pins, to `config.h`; record
the numbers in the runbook. In `main.cpp`, `gestures_begin()` in `setup()`
and `gestures_poll(link_time())` in `loop()` when `link_synced()`.

- [ ] **Step 6: Verify against Control's log**

Flash; `./terrarium.sh --room TEST`; load **Test** (its `player` role
declares `tap`, `tilt`, `shake`). Tap the pad, hold it, swing the board.
Expected in `control.log`: `/game/tap` lines from `ie1` for taps; `hold`
and `swing` arrive as unknown verbs and are logged as such (Task C2 adds
the handlers). Tune the 1.4× touch threshold and the 1.5 g swing threshold
on the bench and record the final values in the runbook.

- [ ] **Step 7: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): touch tap/hold and accelerometer swing, sent with O2 stamps"
```

## Task A5 [FW]: Local sample playback and the acoustic latency measurement

**Owner:** Victor (firmware), Sophia (measurement).
**Files:**
- Create: `firmware/src/audio/samples.h`, `firmware/src/audio/samples.cpp`,
  `firmware/data/tick.wav`, `firmware/data/hold.wav`, `firmware/tools/wav2h.py`,
  `docs/hardware/tap-latency.md`
- Modify: `firmware/src/main.cpp`, `firmware/src/sense/gestures.cpp`

**Interfaces:**
- Produces: `audio_begin()`, `audio_play(const char *name)` (non-blocking,
  names `tick` and `hold`); the `/ie<N>/play` handler from A2 calls it too.

- [ ] **Step 1: Convert two short samples to headers**

```python
# firmware/tools/wav2h.py
"""wav2h: 16-bit mono WAV -> C header of int16 samples. Usage: wav2h.py in.wav name > out.h"""
import struct, sys, wave
w = wave.open(sys.argv[1]); name = sys.argv[2]
assert w.getnchannels() == 1 and w.getsampwidth() == 2, "16-bit mono only"
frames = w.readframes(w.getnframes())
vals = struct.unpack(f"<{len(frames)//2}h", frames)
print(f"#pragma once\n#include <stdint.h>\nstatic const int {name}_rate = {w.getframerate()};")
print(f"static const int {name}_len = {len(vals)};\nstatic const int16_t {name}_pcm[] = {{")
print(",".join(str(v) for v in vals)); print("};")
```

Make `tick.wav` (a 30 ms 1 kHz click) and `hold.wav` (a 200 ms low hum) at
22050 Hz mono 16-bit with any editor; run
`python3 firmware/tools/wav2h.py firmware/data/tick.wav tick > firmware/src/audio/tick.h`
and the same for `hold`.

- [ ] **Step 2: Write the I2S player**

```cpp
// firmware/src/audio/samples.cpp
#include <Arduino.h>
#include <ESP_I2S.h>
#include "samples.h"
#include "tick.h"
#include "hold.h"
#include "config.h"

static I2SClass i2s;
static const int16_t *cur = nullptr; static int cur_len = 0, cur_pos = 0;

void audio_begin() {
  i2s.setPins(I2S_BCLK, I2S_LRC, I2S_DOUT);
  i2s.begin(I2S_MODE_STD, tick_rate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
}
void audio_play(const char *name) {
  if (!strcmp(name, "tick")) { cur = tick_pcm; cur_len = tick_len; }
  else if (!strcmp(name, "hold")) { cur = hold_pcm; cur_len = hold_len; }
  else return;
  cur_pos = 0;
}
void audio_pump() {                          // call every loop(); writes what the DMA will take
  if (!cur) return;
  int n = cur_len - cur_pos; if (n > 256) n = 256;
  size_t wrote = i2s.write((const uint8_t *)(cur + cur_pos), n * 2);
  cur_pos += wrote / 2;
  if (cur_pos >= cur_len) cur = nullptr;
}
```

Add `I2S_BCLK`, `I2S_LRC`, `I2S_DOUT` to `config.h` (record in the runbook).
In `gestures.cpp`, call `audio_play("tick")` **before** `send()` on a tap
and `audio_play("hold")` on a hold. In `main.cpp`, `audio_begin()` in
`setup()`, `audio_pump()` every `loop()`, and `link_on_play(audio_play_cb)`
where `audio_play_cb(name, params)` calls `audio_play(name)`.

- [ ] **Step 3: Measure tap-to-sound acoustically (Sophia)**

Record with a phone at 48 kHz, held 10 cm from the Tuneshroom: tap the pad
with a pen ten times. In Audacity, for each tap measure the gap between the
pen's transient and the speaker's onset. Record all ten, the mean and the
worst in `docs/hardware/tap-latency.md`. **Acceptance: worst under 20 ms.**
If it fails, reduce the I2S DMA buffer (`i2s.setBufferSize`) and remeasure;
record both runs.

- [ ] **Step 4: Commit**

```bash
git add firmware/ docs/hardware/tap-latency.md
git commit -m "feat(firmware): local tick/hold samples over I2S; tap-to-sound measured"
```

## Task A6 [FW]: Tap timestamp error measured

**Owner:** Victor. **Files:** Create `firmware/tools/tap_period.py`,
`docs/hardware/tap-timestamp.md`.

**Interfaces:**
- Consumes: `/game/tap` lines in `control.log` (Task A4).
- Produces: the measured stamp error that Task C2's grading window is
  checked against.

- [ ] **Step 1: Write the analysis script**

```python
# firmware/tools/tap_period.py
"""Read /game/tap stamps for one device out of control.log, report the
inter-tap period statistics against a known mechanical period.

Usage: tap_period.py control.log ie1 0.500   # 500 ms metronome taps
"""
import re, statistics, sys
path, dev, period = sys.argv[1], sys.argv[2], float(sys.argv[3])
stamps = [float(m.group(1)) for line in open(path)
          if dev in line and "/game/tap" in line
          and (m := re.search(r"at=([0-9.]+)", line))]
gaps = [b - a for a, b in zip(stamps, stamps[1:])]
errs = [abs(g - period) * 1000 for g in gaps]
print(f"taps={len(stamps)} mean_err_ms={statistics.mean(errs):.2f} "
      f"p95_ms={sorted(errs)[int(0.95*len(errs))-1]:.2f} worst_ms={max(errs):.2f}")
```

If `control.log` does not print `at=` for taps, add that to the engine's
existing tap log line (`control/engine.py`, where `data()` logs a verb) in
the same commit, with a one-line test in `tests/test_engine_verbs.py` that
the log record contains the stamp.

- [ ] **Step 2: Tap mechanically at a known period**

Use a phone metronome at 120 BPM (500 ms) and tap the pad on each click for
60 s, or clamp a solenoid if the maker lab has one. Run the script.
**Acceptance: p95 under 10 ms.** Record the numbers, the method and the
firmware commit in `docs/hardware/tap-timestamp.md`.

- [ ] **Step 3: Commit**

```bash
git add firmware/tools/tap_period.py docs/hardware/tap-timestamp.md
git commit -m "docs(hardware): tap timestamp error measured against a 500 ms period"
```

## Task A7 [FW]: Tower build target

**Owner:** Victor. **Files:** Modify `firmware/src/main.cpp`,
`firmware/src/render/frames.cpp`, `firmware/platformio.ini`;
Create `firmware/test/test_frames/test_tower_limit.cpp`.

**Interfaces:**
- Produces: the `tower` environment: service `ie9`, 120 pixels on
  `PIXEL_PIN`, limiter 2.4 A, no sensors, no audio, join node from
  `-DJOIN_NODE` (the Room's node, Task C1 supplies it).

- [ ] **Step 1: Failing test for the Tower limiter at 120 px**

```cpp
// firmware/test/test_frames/test_tower_limit.cpp
#include <unity.h>
#include <string.h>
#undef PIXEL_COUNT
#define PIXEL_COUNT 120
#include "../../src/render/frames_core.h"
void test_tower_full_white_is_capped_to_2_4A() {
  uint8_t f[360]; memset(f, 255, 360);
  // 360 ch * (0.025/3) A = 3.0 A modelled; cap 2.4 -> scale 0.8
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.8f, frames_scale_for(f, 120, 2.4f, 0.025f / 3.0f));
}
int main() { UNITY_BEGIN(); RUN_TEST(test_tower_full_white_is_capped_to_2_4A); return UNITY_END(); }
```

Run: `pio test -e native -f test_frames`. Expected: the new test passes
with the existing core (no code change), which proves the limiter is
parameter-driven.

- [ ] **Step 2: Gate sensors and audio out of the Tower build**

In `main.cpp`, wrap `gestures_begin/poll` and `audio_begin/pump/play` in
`#ifdef TUNESHROOM`. In `setup()` call `frames_begin(PIXEL_PIN, PIXEL_COUNT,
TOWER ? 2.4f : 0.6f)`. Build both: `pio run -e tuneshroom && pio run -e tower`.

- [ ] **Step 3: First light on the Tower chain (with Sophia, after Task B3 Step 3)**

Flash an S3 with `tower`; `./terrarium.sh --room TEST`; for this check only
build with `-DJOIN_NODE=\"TEST_PLAYER_NODE\" -DPIXEL_COUNT=60` so the
TEST room's `main` fixture width matches, load Chase. Expected: the first
60 Tower pixels chase; `LATE 0`.

- [ ] **Step 4: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): tower build target, 120 px, 2.4 A limiter, no sensors"
```

---

# Phase B: Hardware (Sophia)

## Task B1 [HW]: Tuneshroom prototype board and power

**Files:** Create `docs/hardware/tuneshroom-runbook.md`.

**Interfaces:**
- Produces: the GPIO assignments Tasks A3, A4, A5 compile against, recorded
  in one table.

- [ ] **Step 1: Assign pins and record them before wiring**

| Function | Part | Board pin | Constraint |
|---|---|---|---|
| Pixel data | 12 × NeoPixel RGBW mini button, chained | `PIXEL_PIN` | RMT-capable GPIO; through the 74AHCT125 to 5 V |
| Touch pad | copper tape, 25 mm disc, under the cap | `TOUCH_PIN` | a touch-capable GPIO (`T0`..`Tn` on the board pinout) |
| Accelerometer | LIS3DH breakout | SDA, SCL, 3V3, GND, INT1 to a GPIO | I2C at 0x18 (SDO low) |
| Amplifier | MAX98357A: BCLK, LRC, DIN, 5 V, GND; SD tied to 5 V | `I2S_BCLK`, `I2S_LRC`, `I2S_DOUT` | |
| Speaker | 40 mm 4 Ω | amp + / - | |
| Power | 18650 → TP4056 → MT3608 (5.0 V) → board 5 V and pixel 5 V | | **MT3608 trimmed first, see Step 2** |

Write the table into the runbook with the actual numbers.

- [ ] **Step 2: Trim the MT3608 (stop-point)**

Bench supply 3.7 V, current limit 200 mA, into the MT3608 input. Meter on
the output. Turn the pot until the meter reads **5.00 V ± 0.05**. Only then
connect the output to anything. Photograph the meter and put the photo in
the runbook.

- [ ] **Step 3: Wire the board on a breadboard, power from the bench supply first, then from the cell**

Verify with the meter at the board's 5 V pin: 4.95 to 5.05 V under load
(all pixels white through the Task A3 limiter). Record the current the
supply reports at full white; it must be under 0.7 A.

- [ ] **Step 4: Run the Task A1 hello and the Task A3 Chase check on this board**

Both must pass before the board leaves the breadboard.

- [ ] **Step 5: Commit**

```bash
git add docs/hardware/tuneshroom-runbook.md
git commit -m "docs(hardware): Tuneshroom prototype pin map, MT3608 trim, power check"
```

## Task B2 [HW]: Touch through silicone, then the enclosure

**Files:** Modify `docs/hardware/tuneshroom-runbook.md`.

- [ ] **Step 1: Cast a 40 mm silicone sample at the cap's intended thickness (3 to 5 mm) and lay it over the touch pad**

Run Task A4's firmware. Tap and hold through the sample twenty times each.
Count misses. Record the count and the `touch_baseline` printed at boot.

- [ ] **Step 2: Decide the tap source**

If misses are under 2 in 40, touch wins; leave `gestures.cpp` as is. If
not, tell Victor to promote the LIS3DH click (`CLICK at=`) to the tap source
and make hold a double-tap; Chris updates the Mushica cue table the same
day. Record the decision in the runbook.

- [ ] **Step 3: Cast or print the body at ETC**

The body is diffuser and resonance chamber both (`MM_HARDWARE_DESIGN.md`
§4.1). Iterate on the maker lab's cadence; the first article does not need
the final finish. Fit the board, cell, speaker and pixel ring; leave the
USB-C port reachable for flashing.

- [ ] **Step 4: Repeat the Task A5 acoustic measurement in the enclosure and record both numbers side by side**

- [ ] **Step 5: Commit**

```bash
git add docs/hardware/tuneshroom-runbook.md
git commit -m "docs(hardware): touch-through-silicone result, tap source decision, first body"
```

## Task B3 [HW]: Tower build

**Files:** Create `docs/hardware/tower-runbook.md`.

- [ ] **Step 1: Cut and mount eight 250 mm segments on the mast, data chained top to bottom, one 3-pin JST per joint**

Label segments 1 (top) to 8 (base) on the channel. The firmware's pixel 0
is segment 8's first pixel (nearest the controller in the base).

- [ ] **Step 2: Power: inject 12 V at the base and at the top; fuse at the supply**

18 AWG red/black from the supply to a distribution block in the base; one
pair to segment 8, one pair up the mast to segment 1. 5 A fuse inline at
the supply. Meter at segment 1's pads with all pixels white through the
Task A7 limiter: **11.4 V or higher** (under 5 % drop). Record the reading.

- [ ] **Step 3: Controller in the base**

S3 board, 74AHCT125 on the data line, 12 V supply to the strip only, USB or
a 5 V buck to the board. Run Task A7 Step 3 here.

- [ ] **Step 4: Diffusion film over each segment, fiber bundles fed from segments 2, 4 and 6, PAR lights on stands set to a fixed colour, props per the Tower definition**

- [ ] **Step 5: Stand the Tower at full height and confirm it does not tip at a 10° push on the base plate**

- [ ] **Step 6: Commit**

```bash
git add docs/hardware/tower-runbook.md
git commit -m "docs(hardware): Tower build, injection readings, controller in base"
```

## Task B4 [HW]: Tuneshroom first article and the Gate 1 demonstration

**Files:** Modify `docs/hardware/tuneshroom-runbook.md`.

- [ ] **Step 1: Assemble the first article in its body with the tap-source decision from B2 applied**

- [ ] **Step 2: Gate 1 (Fri Oct 9), recorded on video**

With Chris: `./terrarium.sh --room TOWER` (Task C1), load Mushica (Task C2),
Tuneshroom joins, Tower bound. Play one call-and-response phase. Check off:

- [ ] tap, hold and swing each reach the Bit (Console event log shows the verbs)
- [ ] tap-to-sound under 20 ms in the enclosure (Task B2 Step 4 figure)
- [ ] tap stamp p95 under 10 ms (Task A6 figure)
- [ ] the Tower pulses on the beat and the progress bar fills on hits

Pass: continue. Fail: Chris declares the o2ws phone browser the committed
Tuneshroom the same day; Task B5 is cancelled; Tower work continues.

- [ ] **Step 3: Commit**

```bash
git add docs/hardware/tuneshroom-runbook.md
git commit -m "docs(hardware): first article assembled; Gate 1 results recorded"
```

## Task B5 [HW]: Tuneshrooms #2 and #3 from the runbook (November)

- [ ] **Step 1: Victor builds #2 from `docs/hardware/tuneshroom-runbook.md` alone, without asking Sophia. Every question he has to ask is a runbook defect; fix the runbook, not the unit.**

- [ ] **Step 2: Sophia builds #3. Both join Mushica as `ie2` and `ie3` (build flag) and play a phase.**

- [ ] **Step 3: Spares kit: one trimmed MT3608, one TP4056, one cell, one LIS3DH, one amp, one speaker, one pixel string, JST pigtails, in a labelled box. Listed in the runbook.**

- [ ] **Step 4: Commit**

```bash
git add docs/hardware/tuneshroom-runbook.md
git commit -m "docs(hardware): units 2 and 3 built from the runbook; spares kit"
```

---

# Phase C: Terrarium software

## Task C1 [SW]: The TOWER room and the Tower's binding

**Owner:** Victor.
**Files:**
- Create: `rooms/TOWER.toml`, `instruments/tower.toml`,
  `tests/test_tower_room.py`
- Modify: `firmware/platformio.ini` (the `tower` env's `JOIN_NODE`)

**Interfaces:**
- Consumes: `rooms/TEST.toml` and `instruments/dev_strip_main.toml` as the
  format; `control/rooms.py` `room_role()` for the node id.
- Produces: room name `TOWER` with one fixture `tower`, 120 px, eight
  blocks `s1`..`s8` (15 px each, s8 at pixel 0), zones `progress` (s1..s6,
  90 px) and `beat` (s7..s8, 30 px); instrument `tower` with functions
  `beat_pulse` and `progress_fill`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tower_room.py
from control.terrarium_config import load_terrarium_config


def _cfg():
    # Same loader tests/test_catalog.py uses; terrarium.toml points at rooms/ and instruments/.
    return load_terrarium_config("terrarium.toml")


def test_tower_room_has_one_120px_fixture_in_eight_blocks():
    profile = _cfg().rooms["TOWER"].profile
    assert [f.name for f in profile.fixtures] == ["tower"]
    fx = profile.fixtures[0]
    assert fx.instrument.name == "tower"
    assert sum(b.count for b in fx.blocks) == 120
    assert [b.name for b in fx.blocks] == [f"s{i}" for i in range(8, 0, -1)]
    zones = {z.name: z for z in fx.zones}
    assert zones["beat"].start == 0 and zones["beat"].count == 30
    assert zones["progress"].start == 30 and zones["progress"].count == 90


def test_tower_instrument_declares_the_two_functions():
    inst = _cfg().instruments["tower"]
    names = {f.name for f in inst.functions}
    assert {"beat_pulse", "progress_fill"} <= names
```

`RoomProfile`, `RoomFixture`, `RoomBlock` and `RoomZone` are in
`control/room_profile.py`; `Instrument` is in `control/instrument.py`. If
`inst.functions` holds dicts rather than objects on `main`, read `f["name"]`
instead; the assertion is the contract.

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_tower_room.py -v`
Expected: FAIL, `TOWER` not found.

- [ ] **Step 3: Write the room and the instrument**

```toml
# rooms/TOWER.toml
description = "Mushica Tower: 2100 mm, eight 15 px segments, s8 at the base"
backends = ["devicelink"]

[[fixtures]]
name = "tower"
color_order = "GRB"
instrument = "tower"
  [[fixtures.blocks]]
  name = "s8"
  start = 0
  count = 15
  [[fixtures.blocks]]
  name = "s7"
  start = 15
  count = 15
  [[fixtures.blocks]]
  name = "s6"
  start = 30
  count = 15
  [[fixtures.blocks]]
  name = "s5"
  start = 45
  count = 15
  [[fixtures.blocks]]
  name = "s4"
  start = 60
  count = 15
  [[fixtures.blocks]]
  name = "s3"
  start = 75
  count = 15
  [[fixtures.blocks]]
  name = "s2"
  start = 90
  count = 15
  [[fixtures.blocks]]
  name = "s1"
  start = 105
  count = 15
  [[fixtures.zones]]
  name = "beat"
  start = 0
  count = 30
  [[fixtures.zones]]
  name = "progress"
  start = 30
  count = 90
```

```toml
# instruments/tower.toml
description = "Mushica Tower fixture: beat pulse at the base, progress bar up the mast"
capabilities = ["light.surface"]
accepted_cues = ["midi", "solid", "mute"]

[[functions]]
name = "beat_pulse"
kind = "scripted"
description = "One beat: the base zone flashes and decays"
script = [
  { offset = 0.0, midi = [176, 11, 120] },
  { offset = 0.12, midi = [176, 11, 30] },
]

[[functions]]
name = "progress_fill"
kind = "scripted"
description = "Progress bar level; the Bit sends cc:12 with the fill percent"
script = [
  { offset = 0.0, midi = [176, 12, 0] },
]
```

If `backends = ["devicelink"]` is rejected after the o2lite cutover, use
the value `rooms/TEST.toml` carries on `main` today; the test does not
assert it.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_tower_room.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Find the Room's node id and put it in the Tower build**

```bash
.venv/bin/python -c "
from control.terrarium_config import load_terrarium_config
spec = load_terrarium_config('terrarium.toml').rooms['TOWER']
print(spec.profile.fixtures[0].name, getattr(spec, 'node_id', None) or spec)"
```

The ROOM-class role's node id is what `control/rooms.py`'s role builder
returns as its third value (`name, role, room.node_id`); if `RoomSpec` does
not expose it directly, the Console's Join card lists every node when the
TOWER room is loaded, so read it there. Put the id in `platformio.ini`:
`-DJOIN_NODE=\"<id>\"` on the `tower` env, and record it in
`docs/hardware/tower-runbook.md`.

- [ ] **Step 6: Bind the physical Tower once and confirm the recorded binding survives a restart**

`./terrarium.sh --room TOWER`. The boot holds in SETUP waiting for the
fixture; the Tower (Task A7 firmware, powered) joins the armed node and
binds. Expected on the Mac: the log line from `control/terrarium.py`'s
`wait_for_room_binding` path naming `tower` bound to `ie9`. Restart the
Terrarium with the Tower still powered: it must bind on the fast path
(`_bind_room_fast_path`, "previously recorded physical device") without a
new arm window. If it does not, `RoomBindingRegistry.save()/load()` is not
wired into boot (the deep-dive says so); wire it in `harness/terrarium_boot.py`
with a test in `tests/test_terrarium_boot.py` that a saved binding is
loaded before `boot()` resolves the Room, and commit that separately.

- [ ] **Step 7: Commit**

```bash
git add rooms/TOWER.toml instruments/tower.toml tests/test_tower_room.py firmware/platformio.ini
git commit -m "feat(rooms): TOWER room and tower instrument; Tower binds as ie9"
```

## Task C2 [SW]: The Mushica Bit

**Owner:** Chris.
**Files:**
- Create: `bits/mushica/__init__.py`, `bits/mushica/bit.toml`,
  `bits/mushica/mushica_bit.py`, `tests/test_mushica_bit.py`
- Reference: `bits/metronome/metronome_bit.py` (same structure; do not
  import from it)

**Interfaces:**
- Consumes: `control.bit.Bit`; `control.roles` (`Role`, `RoleClass`,
  `RoleTable`); `control.cues` (`ROOM`, `TARGET`, `FireFunction`,
  `PlayCue`); `control.functions` (`Condition`, `ConditionSource`,
  `ScriptStep`, `Function`, `FunctionTable`, `FunctionTarget`); verbs `tap`
  (`args = [dev, peak_g, duration_ms, count]`), `hold` (`[dev,
  held_seconds, count]`), `swing` (`[dev, signed_g, count]`) from Task A4;
  Room zones `beat` and `progress` from Task C1.
- Produces: node `MUSHICA_PLAYER_NODE` (Task A2's `JOIN_NODE`); `result()`
  with `progress` (0.0 to 1.0), `perfect`, `good`, `missed`; declared
  Functions `beat_pulse`, `progress_level`, `call_tap`, `call_hold`,
  `hit_perfect`, `hit_good`, `miss`, `free_tap`, `free_hold`, `swing_left`,
  `swing_right`, `finale`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mushica_bit.py
import pytest
from control.cues import FireFunction, ROOM
from bits.mushica.mushica_bit import MushicaBit, PERFECT_S, GOOD_S

def make(bpm=100):
    bit = MushicaBit(config={"rhythm": {"bpm": bpm}})
    bit.cue_horizon = 0.06
    return bit

def test_role_table_declares_one_scored_player_with_three_gestures():
    rt = make().role_table()
    player = rt.roles["player"]
    assert player.scored and player.capacity == 1
    assert set(player.uses) >= {"tap", "hold", "swing"}
    assert rt.node_map["MUSHICA_PLAYER_NODE"] == ["player"]

def test_verb_handlers_cover_tap_hold_swing():
    assert {"tap", "hold", "swing"} <= set(make().verb_handlers())

def test_function_table_declares_every_fired_name():
    names = set(make().function_table.functions)
    assert {"beat_pulse", "progress_level", "call_tap", "call_hold",
            "hit_perfect", "hit_good", "miss", "free_tap", "free_hold",
            "swing_left", "swing_right", "finale"} <= names

def test_grading_windows():
    bit = make()
    assert bit.grade(0.0) == "perfect"
    assert bit.grade(PERFECT_S) == "perfect"
    assert bit.grade(PERFECT_S + 0.001) == "good"
    assert bit.grade(GOOD_S) == "good"
    assert bit.grade(GOOD_S + 0.001) == "missed"
    assert bit.grade(-GOOD_S) == "good"

def test_progress_is_weighted_by_grade():
    bit = make()
    bit._schedule = [("tap", 1.0), ("tap", 2.0), ("hold", 3.0), ("tap", 4.0)]
    for g in ("perfect", "good", "missed", "perfect"):
        bit._record(g)
    r = bit.result()
    assert (r["perfect"], r["good"], r["missed"]) == (2, 1, 1)
    assert r["progress"] == pytest.approx((1.0 + 0.5 + 0.0 + 1.0) / 4)

def test_first_fires_call_anchors_t0_and_pulses_the_tower_every_beat():
    bit = make(bpm=120)                      # beat = 0.5 s
    bit.fires(at=100.0)                      # anchors t0 = 100 + lead-in
    t0 = bit._t0
    out = bit.fires(at=t0 + 0.5)
    pulses = [f for f in out if isinstance(f, FireFunction) and f.name == "beat_pulse"]
    assert pulses and pulses[0].dev is None and pulses[0].at == pytest.approx(t0 + 0.5, abs=1e-6)

def test_scored_tap_inside_perfect_window_reports_hit_perfect():
    bit = make(bpm=120)
    bit.fires(at=100.0)
    bit._player = "ie1"
    bit._phase = "call"
    kind, t_resp = bit._pending[0]
    assert kind == "tap"
    out = bit._on_tap("ie1", ["ie1", 0.0, 120.0, 1], at=t_resp + 0.010 + bit.cue_horizon)
    assert any(isinstance(f, FireFunction) and f.name == "hit_perfect" and f.dev == "ie1" for f in out)
    assert bit.result()["perfect"] == 1

def test_free_performance_inputs_are_not_scored():
    bit = make()
    bit.fires(at=100.0)
    bit._player = "ie1"
    bit._phase = "free"
    n_before = len(bit._grades)
    out = bit._on_tap("ie1", ["ie1", 0.0, 120.0, 1], at=200.0)
    assert len(bit._grades) == n_before
    assert any(isinstance(f, FireFunction) and f.name == "free_tap" for f in out)

def test_unanswered_response_is_missed_after_the_good_window_closes():
    bit = make(bpm=120)
    bit.fires(at=100.0)
    bit._player = "ie1"
    kind, t_resp = bit._pending[0]
    out = bit.fires(at=t_resp + GOOD_S + bit.cue_horizon + 0.5)
    assert bit.result()["missed"] >= 1
    assert any(isinstance(f, FireFunction) and f.name == "miss" for f in out)
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_mushica_bit.py -v`
Expected: FAIL, `bits.mushica` not found.

- [ ] **Step 3: Write `bit.toml` and the Bit**

```toml
# bits/mushica/bit.toml
[bit]
name = "MushicaBit"
version = "0.1.0"
description = "Single-player rhythm game: call-and-response and free performance on one Tuneshroom, beat and progress on the Tower"
entry = "mushica_bit:MushicaBit"
kind = "r_game"
author = "Musical Mycology"
requires_terrarium_api = 1

[launch]
room_types = ["TOWER"]
default_room_type = "TOWER"
default_devices = 1
setup_seconds = 20
expected_run_seconds = 150
transport = "any"
default_join_role = "player"

[launch.nodes]
player = "MUSHICA_PLAYER_NODE"

[start]
when = "players"
min_scored = 1
timeout_seconds = 120
on_timeout = "start"

[console]
display_name = "Mushica"
notes = "Tap, hold and swing back the call. The Tower shows the beat and your progress."

[results]
keys = ["progress", "perfect", "good", "missed"]

[rhythm]
bpm = 100
```

```python
# bits/mushica/mushica_bit.py
"""Mushica: the Dec 4 Bit. One player, one Tuneshroom, one Tower.

Same skeleton as bits/metronome/metronome_bit.py: a beat grid in
presentation time anchored on the first fires(at); each beat's consequences
reported as FireFunction(name, dev, at=grid_time); a gesture's own moment
recovered as `at - cue_horizon` in the verb handlers. The phase sequence,
the three cue types and the 30 / 100 ms windows are the Mushica design
document's.
"""
from __future__ import annotations

import logging

from control.bit import Bit
from control.cues import ROOM, TARGET, FireFunction, PlayCue
from control.functions import (
    Condition, ConditionSource, ScriptStep, Function, FunctionTable,
    FunctionTarget,
)
from control.roles import Role, RoleClass, RoleTable

logger = logging.getLogger(__name__)

PERFECT_S = 0.030
GOOD_S = 0.100
WEIGHT = {"perfect": 1.0, "good": 0.5, "missed": 0.0}
LEAD_IN_BEATS = 2

# Level script, in order. A "call" phase lists (kind, beat) responses
# relative to the phase start; the call for each sounds two beats earlier.
# "ground" and "free" phases are a length in beats.
LEVEL = [
    ("ground", 8),
    ("call", [("tap", 4), ("tap", 8), ("tap", 12), ("tap", 16)]),
    ("call", [("tap", 4), ("hold", 8), ("tap", 11), ("tap", 12), ("hold", 16)]),
    ("free", 16),
    ("call", [("tap", 4), ("tap", 6), ("hold", 8), ("tap", 12), ("tap", 14), ("hold", 16)]),
    ("free", 16),
]

# MIDI vocabulary on the lanes the manifests below declare.
CC_HUE, CC_LEVEL, CC_PROGRESS, CC_FLASH = 74, 11, 12, 70
CALL_KEY_TAP, CALL_KEY_HOLD = 72, 48          # high tone / low hum (design doc 4)


def _adjudicated(name: str, description: str) -> Condition:
    return Condition(name=name, description=description,
                     source=ConditionSource.BIT_ADJUDICATED)


def _fn(name, description, target, condition, script) -> Function:
    return Function(name=name, description=description, target=target,
                    condition=_adjudicated(*condition), script=script)


class MushicaBit(Bit):
    version = "0.1.0"

    def __init__(self, config=None):
        super().__init__(config)
        cfg = (config or {}).get("rhythm", {}) if isinstance(config, dict) else {}
        self.bpm = float(cfg.get("bpm", 100))
        self.beat_s = 60.0 / self.bpm
        self.cue_horizon = 0.0                 # stamped by GameServer.load_bit
        self._t0: float | None = None
        self._player: str | None = None
        self._phase = "idle"
        self._edges: list[tuple[float, str]] = []
        self._schedule: list[tuple[str, float]] = []   # (kind, presentation time)
        self._pending: list[tuple[str, float]] = []
        self._grades: list[str] = []
        self._next_beat = 0
        self._total_beats = 0
        self._done = False
        self._finale_sent = False

    # Roles and Room -------------------------------------------------------
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.UNIQUE, capacity=1, scored=True,
            uses=["tap", "hold", "swing"], breath=False,
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.58, "level": 0.35},
                 "lanes": [{"source": f"cc:{CC_HUE}", "dest": "hue"},
                           {"source": f"cc:{CC_LEVEL}", "dest": "level"}]},
                {"instrument": "bloom", "target": "primary", "params": {"hue": 0.58},
                 "lanes": [{"source": "note", "dest": "trigger"},
                           {"source": f"cc:{CC_FLASH}", "dest": "hue"}]},
            ]},
        )
        return RoleTable(roles={"player": player},
                         node_map={"MUSHICA_PLAYER_NODE": ["player"]})

    def room_manifests(self) -> tuple[dict, dict]:
        # Targets are the TOWER room's zones (rooms/TOWER.toml, Task C1).
        room_light = {"instruments": [
            {"instrument": "aurora", "target": "beat",
             "params": {"hue": 0.10, "level": 0.20},
             "lanes": [{"source": f"cc:{CC_LEVEL}", "dest": "level"}]},
            {"instrument": "aurora", "target": "progress",
             "params": {"hue": 0.33, "level": 0.0},
             "lanes": [{"source": f"cc:{CC_PROGRESS}", "dest": "level"}]},
        ]}
        room_ugen = {"instruments": [
            {"instrument": "flsyn", "program": 11,      # vibraphone: the call tones
             "lanes": []},
        ]}
        return room_light, room_ugen

    @property
    def function_table(self) -> FunctionTable:
        R, D = FunctionTarget.ROOM, FunctionTarget.DEVICE
        return FunctionTable(functions={
            "beat_pulse": _fn("beat_pulse", "Tower base zone pulses on every beat", R,
                              ("beat", "Every beat of the level"),
                              (ScriptStep(0.0, (TARGET, 0xB0, CC_LEVEL, 120)),
                               ScriptStep(0.12, (TARGET, 0xB0, CC_LEVEL, 30)))),
            "progress_level": _fn("progress_level", "Tower progress zone level; the Bit rewrites cc:12 each beat", R,
                                  ("beat", "Every beat of the level"),
                                  (ScriptStep(0.0, (TARGET, 0xB0, CC_PROGRESS, 0)),)),
            "call_tap": _fn("call_tap", "High tone and a hue lift: tap coming", R,
                            ("call_tap", "Two beats before a scored tap"),
                            (ScriptStep(0.0, (TARGET, 0x90, CALL_KEY_TAP, 100)),
                             ScriptStep(0.25, (TARGET, 0x80, CALL_KEY_TAP, 0)))),
            "call_hold": _fn("call_hold", "Low hum: hold coming", R,
                             ("call_hold", "Two beats before a scored hold"),
                             (ScriptStep(0.0, (TARGET, 0x90, CALL_KEY_HOLD, 90)),
                              ScriptStep(0.6, (TARGET, 0x80, CALL_KEY_HOLD, 0)))),
            "hit_perfect": _fn("hit_perfect", "Bright flash on the player", D,
                               ("hit_perfect", "Response inside the 30 ms window"),
                               (ScriptStep(0.0, (TARGET, 0xB0, CC_LEVEL, 127)),
                                ScriptStep(0.0, (TARGET, 0xB0, CC_FLASH, 40)),
                                ScriptStep(0.3, (TARGET, 0xB0, CC_LEVEL, 45)))),
            "hit_good": _fn("hit_good", "Normal blink on the player", D,
                            ("hit_good", "Response inside the 100 ms window"),
                            (ScriptStep(0.0, (TARGET, 0xB0, CC_LEVEL, 90)),
                             ScriptStep(0.3, (TARGET, 0xB0, CC_LEVEL, 45)))),
            "miss": _fn("miss", "Player goes dark for a moment", D,
                        ("miss", "No response inside the window"),
                        (ScriptStep(0.0, (TARGET, 0xB0, CC_LEVEL, 0)),
                         ScriptStep(0.4, (TARGET, 0xB0, CC_LEVEL, 45)))),
            "free_tap": _fn("free_tap", "Free play: a hue nudge on the player", D,
                            ("free_tap", "Tap during a free-performance phase"),
                            (ScriptStep(0.0, (TARGET, 0xB0, CC_HUE, 64)),)),
            "free_hold": _fn("free_hold", "Free play: a slow hue sweep on the player", D,
                             ("free_hold", "Hold during a free-performance phase"),
                             (ScriptStep(0.0, (TARGET, 0xB0, CC_HUE, 110)),
                              ScriptStep(1.0, (TARGET, 0xB0, CC_HUE, 64)))),
            "swing_left": _fn("swing_left", "Tower glows toward the left", R,
                              ("swing_left", "Swing with a negative sign"),
                              (ScriptStep(0.0, (TARGET, 0xB0, CC_HUE, 30)),)),
            "swing_right": _fn("swing_right", "Tower glows toward the right", R,
                               ("swing_right", "Swing with a positive sign"),
                               (ScriptStep(0.0, (TARGET, 0xB0, CC_HUE, 100)),)),
            "finale": _fn("finale", "Progress above 90 %: the crown celebrates", R,
                          ("finale", "Level finished with progress over 0.9"),
                          (ScriptStep(0.0, (TARGET, 0xB0, CC_PROGRESS, 127)),
                           ScriptStep(0.0, (TARGET, 0xB0, CC_LEVEL, 127)),
                           ScriptStep(0.5, (TARGET, 0xB0, CC_LEVEL, 30)),
                           ScriptStep(1.0, (TARGET, 0xB0, CC_LEVEL, 127)),
                           ScriptStep(1.5, (TARGET, 0xB0, CC_LEVEL, 30)))),
        })

    # Lifecycle -----------------------------------------------------------
    def on_setup_enter(self) -> None:
        pass

    def on_join(self, dev: str, role_name: str) -> None:
        if role_name == "player":
            self._player = dev

    def on_run_start(self) -> None:
        self._t0 = None                        # anchored on the first fires(at)

    def update(self, dt: float) -> bool:
        return self._done

    def on_complete(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    # Scoring -------------------------------------------------------------
    def grade(self, err_s: float) -> str:
        e = abs(err_s)
        if e <= PERFECT_S:
            return "perfect"
        if e <= GOOD_S:
            return "good"
        return "missed"

    def _record(self, g: str) -> None:
        self._grades.append(g)

    def result(self) -> dict:
        n = len(self._schedule) or 1
        return {"progress": sum(WEIGHT[g] for g in self._grades) / n,
                "perfect": self._grades.count("perfect"),
                "good": self._grades.count("good"),
                "missed": self._grades.count("missed")}

    def status(self) -> dict:
        return {"phase": self._phase, **self.result()}

    # Timeline ------------------------------------------------------------
    def _grid(self, k: int) -> float:
        return self._t0 + k * self.beat_s

    def _build(self, t0: float) -> None:
        """Absolute presentation times for every phase edge and every scored
        response, from the level script."""
        self._edges, self._schedule = [], []
        beat = 0
        for kind, body in LEVEL:
            self._edges.append((t0 + beat * self.beat_s, kind))
            if kind == "call":
                for g, b in body:
                    self._schedule.append((g, t0 + (beat + b) * self.beat_s))
                beat += max(b for _, b in body) + 2
            else:
                beat += body
        self._edges.append((t0 + beat * self.beat_s, "done"))
        self._total_beats = beat
        self._pending = list(self._schedule)

    def _phase_at(self, t: float) -> str:
        cur = "idle"
        for edge_t, kind in self._edges:
            if t >= edge_t:
                cur = kind
        return cur

    def _beat_fires(self, k: int) -> list:
        t = self._grid(k)
        out = [FireFunction("beat_pulse", at=t)]
        # progress_level's script writes cc:12 = 0; the live level follows as a
        # raw cue on the same lane one frame later, so the bar tracks the score.
        pct = int(127 * self.result()["progress"])
        out.append((ROOM, 0xB0, CC_PROGRESS, pct))
        for g, t_resp in self._schedule:
            if abs((t_resp - 2 * self.beat_s) - t) < 1e-6:
                out.append(FireFunction("call_" + g, at=t))
        return out

    def fires(self, at: float) -> list:
        out: list = []
        if self._t0 is None:
            self._t0 = at + LEAD_IN_BEATS * self.beat_s
            self._build(self._t0)
        self._phase = self._phase_at(at)
        while (self._next_beat < self._total_beats
               and self._grid(self._next_beat) <= at + self.beat_s):
            out.extend(self._beat_fires(self._next_beat))
            self._next_beat += 1
        # Responses nobody answered: judged once their good window has closed
        # in real time (at - cue_horizon), as MetronomeBit judges cycles.
        while self._pending and at - self.cue_horizon > self._pending[0][1] + GOOD_S:
            self._pending.pop(0)
            self._record("missed")
            if self._player:
                out.append(FireFunction("miss", self._player))
        if self._phase == "done" and not self._done:
            self._done = True
            if self.result()["progress"] > 0.9 and not self._finale_sent:
                self._finale_sent = True
                out.append(FireFunction("finale"))
        return out

    # Verbs ---------------------------------------------------------------
    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap, "hold": self._on_hold, "swing": self._on_swing}

    def _respond(self, dev: str, kind: str, at: float) -> list:
        if self._t0 is None or dev != self._player:
            return []
        t = at - self.cue_horizon               # the gesture's own moment
        if self._phase == "free":
            return [FireFunction("free_" + kind, dev),
                    PlayCue(dev, "tick" if kind == "tap" else "hold")]
        if not self._pending:
            return []
        g_kind, t_resp = self._pending[0]
        err = t - t_resp
        if err < -GOOD_S:
            return []                           # too early to be this response
        self._pending.pop(0)
        g = "missed" if g_kind != kind else self.grade(err)
        self._record(g)
        logger.info("%s %s err %+.1f ms -> %s", kind, dev, err * 1000.0, g)
        name = {"perfect": "hit_perfect", "good": "hit_good", "missed": "miss"}[g]
        return [FireFunction(name, dev), PlayCue(dev, "tick" if kind == "tap" else "hold")]

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        return self._respond(dev, "tap", at)

    def _on_hold(self, dev: str, args: list, at: float) -> list:
        return self._respond(dev, "hold", at)

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        if not self._player or dev != self._player:
            return []
        signed = float(args[1]) if len(args) > 1 else 0.0
        return [FireFunction("swing_left" if signed < 0 else "swing_right")]
```

`Bit.__init__` takes a `BitConfig`; if it rejects a plain dict, construct
the test's Bit the way `tests/test_metronome_bit.py` constructs
`MetronomeBit` and read `bpm` the same way. The `(ROOM, 0xB0, cc, value)`
4-tuple in `_beat_fires` is the plain untimed cue shape `control/cues.py`
documents as still valid.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_mushica_bit.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Play it in the simulator with a Testshroom**

`./terrarium.sh --room TOWER`, load Mushica from the Console, join a
Testshroom or the o2ws phone page as the player. Expected: the Tower
simulator's base zone pulses every beat; the four calls sound as vibraphone
tones; a tap inside the window lights the player and raises the progress
zone. Record a 30 s screen capture into the PR.

- [ ] **Step 6: Commit**

```bash
git add bits/mushica tests/test_mushica_bit.py
git commit -m "feat(bits): Mushica, call-and-response and free play on one Tuneshroom and the Tower"
```

## Task C3 [SW]: Alt Ctrl submission package (Week 10)

**Owner:** Chris, with both students.
**Files:** Create `docs/altctrl-2027/README.md`.

- [ ] **Step 1: Record the definition-of-done sitting (spec 3.1) as one continuous take, then a 90 s edit**

- [ ] **Step 2: Write the submission text from the Mushica design document's executive overview and the Tower definition; list the hardware in one paragraph without part numbers**

- [ ] **Step 3: Submit by Fri Oct 30; record the confirmation and the notification date (mid-November) in the README**

- [ ] **Step 4: Commit**

```bash
git add docs/altctrl-2027/README.md
git commit -m "docs: Alt Ctrl submission package and confirmation"
```

---

# Phase D: Lock, dry runs, handoff

## Task D1: Firmware freeze and the flash runbook (Fri Nov 6)

**Owner:** Victor. **Files:** Modify `firmware/README.md`.

- [ ] **Step 1: Tag the image**

```bash
git tag -a firmware-dec4-rc1 -m "Deliverables Lock image" && git push --tags
```

- [ ] **Step 2: Write the flash runbook in `firmware/README.md`: clone, `pio run -e tuneshroom -t upload`, the three serial lines to expect (`WIFI OK`, `CLOCK SYNCED`, `ROLE received`), and the two build flags a spare needs (`DEVICE_NAME`, `JOIN_NODE`)**

- [ ] **Step 3: Sophia flashes unit #2 from the README alone; every question is a README defect**

- [ ] **Step 4: Commit**

```bash
git add firmware/README.md
git commit -m "docs(firmware): flash runbook verified by a second person"
```

## Task D2: Dry Run 2 cold-start protocol (Fri Nov 13)

**Owner:** all three. **Files:** Create `docs/hardware/cold-start.md`.

- [ ] **Step 1: Everything off. Start a timer. Power the AP, the Mac, the Tower, one Tuneshroom, in that order. `./terrarium.sh --room TOWER`, load Mushica, play one full level.**

Record: time to Console up, time to Tower bound, time to Tuneshroom joined,
`LATE` counts from both devices at the end, tap stamp p95 from Task A6's
script on that run's log. **Acceptance: under 5 minutes from cold to
playing, `LATE` under 5 on each device, p95 under 10 ms.**

- [ ] **Step 2: Swap the Tuneshroom for unit #2 mid-level without touching the Mac; it must join within 15 s**

- [ ] **Step 3: Write the protocol and the numbers into `docs/hardware/cold-start.md`; repeat it on Mon Nov 30 (Dry Run 3) and append that run**

- [ ] **Step 4: Commit**

```bash
git add docs/hardware/cold-start.md
git commit -m "docs(hardware): cold-start protocol and Dry Run 2 numbers"
```

## Task D3: Deep-dive and hardware-design sync

**Owner:** Chris. **Files:** Modify `docs/MM_TERRARIUM.md`,
`mm-documents/MM_HARDWARE_DESIGN.md`.

- [ ] **Step 1: Add a `firmware/` section to `docs/MM_TERRARIUM.md` under *Landed subsystems*: the two build targets, the thin-device rule, the verbs and their arg shapes, the limiter figures, the measured latency and stamp numbers with their dates**

- [ ] **Step 2: In `MM_HARDWARE_DESIGN.md` §11, add rows for the ESP32-P4 (+C6), the ESP32-S3, the Tower strip and supply; mark the Radxa row as spare stock and the Pi 5 venue box and 864 px array rows as post-show**

- [ ] **Step 3: Run `/mm-deepdive-sync` in mm-terrarium and commit both repos**

---

## Self-review notes

- **Spec coverage.** 3.1 items 1 to 7 map to A1/A2 (1, 2), A4 (3), C2 + C1
  (4), A5 (5), A6 (6), and the o2ws phone page already on `main` (7). Spec
  4.1 fallback is Task 0.3 Step 4 and the `tuneshroom-s3` env. 4.3's touch
  decision is B2 Step 2. 4.4's limiter is A3/A7. 6's schedule is the task
  order. 10's acceptance is D2 plus B5.
- **Placeholders.** Pin numbers are recorded by B1 Step 1 before any code
  compiles against them; that is a measurement step, not a TBD. Two loader
  import lines in C1 Step 1 and five cue constructor names in C2 Step 3 are
  written from `MetronomeBit`'s surface and told to be matched against
  `main` before the tests run; the assertions and behaviour are complete.
- **Type consistency.** `link_send_gesture(addr, at, types, a, b, count)`
  is used by A4 with the same argument order it is defined with. `Gesture`
  and `FrameQueue` are defined once in the `*_core.h` headers and used by
  the tests and the Arduino halves. `/game/hold` and `/game/swing` arg
  shapes in A4 match `_on_hold`/`_on_swing` in C2 (`args[1]` is the float).
- **Not in this plan, on purpose.** DMX PAR control, a microphone, the
  render-engine port, the Radxa FFI link, the Pi 5 box, the 864 px array.
  All listed in spec 3.2.
