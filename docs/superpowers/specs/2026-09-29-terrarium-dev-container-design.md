# Terrarium dev container: a pre-built, pull-and-run dev and test box

**Date:** 2026-09-29
**Repos:** mm-terrarium
**Status:** design approved (Phase 1); Phase 2 (CI/CD) to be specced separately

## Problem

Standing up a Terrarium on a Linux or WSL2 host takes ten manual steps
(`docs/MM_TERRARIUM.md`, *Linux / WSL host setup*): a long apt list, three
sibling checkouts cloned by hand, an o2 build, a machine-local
`libraries.txt`, a local patch to Arco source that must never be committed,
a cmake configure that has to run twice, a `server` symlink, and a venv with
an editable luxaeterna. Every engineer repeats it, each step has a silent
failure mode, and teammates (Victor first) keep losing time to it.

A container removes the build pain. It does **not** by itself remove the
networking constraint: real devices need mDNS multicast and direct UDP to
Arco's ephemeral O2 ports, so the container must share a network stack that
is already on the LAN (native Linux, or WSL2 in mirrored mode). Simulated
devices work everywhere.

Out of scope, stated so nobody expects it: running Terrarium on an ESP32.
ESP32s are the o2lite clients (Tuneshrooms, dev shrooms, WLED controllers);
the Terrarium needs a full Linux or macOS kernel. Also out of scope: a venue
appliance image (audio to a PA, start on boot, arm64 as a first-class
target). The Dec 4 show machine stays a Mac.

## Goal

A teammate with Docker Engine in a WSL2 Ubuntu distro (or on native Linux)
can pull one public image and, with no builds and no sibling clones:

1. run the test suites (`pytest` and `node --test`);
2. run `./terrarium.sh` and `./smoke-test.sh` against either their own
   mounted mm-terrarium checkout or a snapshot of `main` baked into the image;
3. hear Arco's audio by default, or run silent with `--headless`;
4. connect real ESP32 devices when the host is on the LAN (WSL2 mirrored
   mode or native Linux).

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Purpose | Dev and test box, not a venue appliance | Removes today's pain; fits CI; no audio passthrough or boot service needed |
| Code delivery | Toolchain baked, checkout mounted, `main` snapshot as fallback | People edit code; a rebuild per change would be worse than today; the fallback keeps "just pull and run" working |
| Windows runtime | Docker Engine inside the WSL2 distro, not Docker Desktop | With `--network host`, "host" is the WSL distro, which mirrored mode puts on the LAN (confirmed with a dev shroom 2026-09-29). Docker Desktop's host network is its own VM |
| Architecture | linux/amd64 only | WSL boxes are x86_64; arm64 is a later one-flag addition in Phase 2 |
| Audio | On by default; `--headless` opts out | Sound design is half the product; CI always passes `--headless` |
| Avahi | Use the host's daemon via bind mounts, never a second one in the container | Two daemons on host networking would contend for UDP 5353 |
| Entry point | A bash launcher, `terrarium-dev`, plus `docker/build.sh` | Run flags are conditional (mount or not, audio or not, WSL mode checks); a script handles that where Compose would not |
| Hosting (Phase 2) | GitHub Actions builds, GHCR hosts a **public** package | Every dependency is public (`Musical-Mycology/arco`, `luxaeterna`, `mm-terrarium`, `rbdannenberg/o2`); pull needs no login; no Jenkins executor contention; ECR would force an AWS identity on every puller |

## Design

### 1. Image layout (`docker/Dockerfile`)

Three stages:

1. **`base`**: `ubuntu:26.04` pinned by digest (the release the WSL setup was
   verified on). Runtime apt libraries from setup step 1 (their non-`-dev`
   forms), `fluid-soundfont-gm`, `libasound2-plugins` (the ALSA pulse
   plugin), `python3`, `python3-venv`, `nodejs`, `git`. `TERM` is set to
   `xterm-256color` (Arco's curses fails silently without it; see *Host
   platform (gotcha)*).
2. **`build`**: `base` plus the `-dev` packages and `cmake`. Then:
   - clone `rbdannenberg/o2` at its pin and build `Release/libo2_static.a`
     with the flags in setup step 3;
   - clone `Musical-Mycology/arco` at its pin, apply
     `docs/upstream/arco-linux-build.patch`, copy
     `docs/upstream/arco-libraries-ubuntu.txt` to
     `apps/common/libraries.txt`;
   - in `apps/pytest`, configure twice (setup step 6 explains why), build,
     and symlink `server` to `pytestserver`.
   The patch is applied only inside the image build and is never committed
   to the arco mirror.
3. **`runtime`**: `base` plus:

```
/opt/mm/arco                     arco tree: server binary, pyarco, o2litepy
/opt/mm/terrarium                snapshot of mm-terrarium at the build commit
/opt/mm/terrarium/.venv          requirements-dev.txt + luxaeterna[websim] at its pin
/etc/asound.conf                 default PCM routed to pulse
/usr/local/bin/terrarium-dev     the launcher
```

`MM_ARCO_PATH` and `ARCO_ROOT` both point at `/opt/mm/arco`, so
`harness/arco_paths.py` and the shell wrappers resolve Arco with no sibling
checkout.

**`launcher` subcommand.** `docker run --rm <image> launcher` prints the
launcher script to stdout, so someone with no repo clone installs it with:

```bash
docker run --rm ghcr.io/musical-mycology/terrarium-dev:main launcher > terrarium-dev && chmod +x terrarium-dev
```

### 2. Pins (`docker/pins.env`)

One file holds every external version:

- `BASE_IMAGE` (`ubuntu:26.04@sha256:...`)
- `ARCO_SHA` (initially `c8092e2`, the commit the Linux patch was made
  against)
- `O2_SHA`
- `LUXAETERNA_SHA`

`O2_SHA` and `LUXAETERNA_SHA` are set during implementation to the commits
the image self-check (section 6) passes on. Bumping any pin is a one-line PR.

If the patch fails to apply after an `ARCO_SHA` bump, the build fails at that
step. `docker/build.sh` first tries a reverse apply; if that succeeds (the
fixes have landed upstream with Roger Dannenberg) it fails with a message
saying to delete the patch step, not a raw `git apply` error.

### 3. Image tags

- `:main`: latest build of `main`
- `:sha-<short>`: every build, reproducible
- `:v<YYYY-MM-DD>`: a known-good cut handed to teammates
- `:local`: built on the developer's own machine by `docker/build.sh`, never
  pushed

### 4. The launcher (`docker/terrarium-dev`)

A single bash script, installed from the image or run from the repo.

| Command | Runs inside the container |
|---|---|
| `terrarium-dev run [args]` (default) | `./terrarium.sh [args]` |
| `terrarium-dev smoke [args]` | `./smoke-test.sh [args]` |
| `terrarium-dev test` | `.venv/bin/python -m pytest tests` then `node --test tests/js/*.test.js` |
| `terrarium-dev shell` | an interactive bash, fully set up |
| `terrarium-dev update` | pull the selected tag; prune venv volumes stamped with other image digests |
| `terrarium-dev clean` | `./terrarium.sh --clean` |
| `terrarium-dev use <tag>` | save a default tag to `~/.config/terrarium-dev/tag` |
| `terrarium-dev launcher` | (image entrypoint only) print the launcher |

Flags, accepted before the command:

- `--tag <tag>`: `main`, `sha-<short>`, `v<date>` or `local`. Default: the
  saved tag, else `main`.
- `--checkout <path>`: default is the current directory if it is an
  mm-terrarium checkout (has `terrarium.sh` and `control/`), else none, which
  selects snapshot mode.
- `--headless`: no audio mounts, no PulseAudio check.

**The `docker run` it builds:**

- `--rm --init --network host`. `--init` forwards Ctrl-C and SIGTERM, which
  `harness/signals.py` already turns into a clean shutdown.
- `--user $(id -u):$(id -g)`, so files under `runs/` belong to the user.
- Label `mm.terrarium-dev=1` (for the one-stack check).
- **Checkout mode:** the checkout at `/work` (working dir), and a named
  volume `terrarium-venv-<hash of checkout path>` over `/work/.venv`.
- **Snapshot mode:** working dir `/opt/mm/terrarium`, and
  `~/terrarium-runs` mounted over `/opt/mm/terrarium/runs` so logs survive.
- The host's `/run/dbus/system_bus_socket` and `/run/avahi-daemon/`.
- **Audio (unless `--headless`):** on WSLg, `/mnt/wslg` with
  `PULSE_SERVER=unix:/mnt/wslg/PulseServer`; on native Linux,
  `$XDG_RUNTIME_DIR/pulse/native` mounted and `PULSE_SERVER` pointed at it.

**The `.venv` problem and the venv volume.** `terrarium.sh` and
`smoke-test.sh` hard-code `.venv/bin/python` relative to the checkout. A
mounted checkout carries the host's own `.venv` (a WSL or macOS venv, or the
documented symlink), which is wrong inside the container. The named volume
over `/work/.venv` shadows it without touching the host: Docker seeds an
empty named volume from the image's content at that path, so the image must
also hold a copy of the venv at `/work/.venv` (a build-time copy of
`/opt/mm/terrarium/.venv`). The volume is labelled with the image digest; the
launcher recreates it when the digest differs.

**Requirements drift.** Before running, the launcher hashes the mounted
checkout's `requirements.txt` and `requirements-dev.txt`. If the hash differs
from the one stored in the venv volume, it runs `pip install -r
requirements-dev.txt` into the volume and stores the new hash. A branch that
adds a Python dependency never needs an image rebuild. Arco and luxaeterna
drift are not handled this way; they need a newer image.

**Preflight checks, in order (each refusal names its fix):**

1. Docker not reachable: refuse, pointing to the Docker Engine in WSL setup
   note.
2. `/run/avahi-daemon/socket` missing on the host: refuse, with the same fix
   `harness/host_preflight.py` gives (systemd on, avahi-daemon running).
3. No PulseAudio socket and no `--headless`: refuse, suggesting `--headless`.
4. `wslinfo --networking-mode` reports `nat`: warn only (simulated devices
   only; mirrored mode for real devices).
5. A container labelled `mm.terrarium-dev=1` already running: refuse. With
   host networking two stacks would collide on 8080, 8788 and 8772.
6. Host is macOS: warn that the image is amd64 only and that Docker Desktop's
   network is its own VM; a native setup is the better choice.

### 5. Local builds (`docker/build.sh`)

Reads `docker/pins.env`, runs the patch reverse-apply check, and builds
`ghcr.io/musical-mycology/terrarium-dev:local` from the current checkout (the
snapshot is whatever is checked out). Phase 2's workflow calls the same
script, so local and CI builds cannot diverge.

### 6. Failure modes

- **Arco needs a pty with `TERM` set and a non-zero size.** `pty_popen`
  creates its own pty, so the container needs no controlling terminal; the
  image sets `TERM`. The pty drain thread already handles WSL's ~40 KB of
  ALSA startup noise.
- **Stale run records.** Pids in `runs/*/procs.jsonl` are container pids.
  `--rm` ends every process when the container exits, and `sweep_stale`
  matches on spawn time, never pid alone, so a reused pid in a later
  container is never killed.
- **Image older than the checkout.** Python deps self-heal (section 4). For
  Arco or luxaeterna API drift the launcher's failure hint says to run
  `terrarium-dev update`, or bump pins and use `--tag local`.
- **PulseAudio chain does not reach Arco.** PortAudio's ALSA backend through
  the pulse plugin is the expected path but is unverified with Arco; it is an
  acceptance item. If it fails, the fallback is building PortAudio's
  PulseAudio host API into the image.
- **D-Bus mount for Avahi.** The Avahi client library talks to the daemon
  over the system D-Bus; the socket mount is the expected path but is an
  acceptance item.

### 7. Testing

- **Launcher unit tests** in `tests/test_terrarium_dev.py`: run the script
  with a fake `docker` (and fake `wslinfo`) first on `PATH` that records its
  argv. Assert the mounts and flags for checkout mode, snapshot mode,
  `--headless`, `--tag` and saved tags, and each preflight refusal and
  warning. No new test framework; runs in the existing offline suite.
- **Image self-check:** `terrarium-dev test` then `terrarium-dev smoke --ci
  --headless`. This is also Phase 2's gate before any push.
- **Phase 1 acceptance**, by hand on a Windows 11 WSL2 box in mirrored mode:
  1. `terrarium-dev test` passes.
  2. `terrarium-dev run --room TEST --seconds 45` exits 0 with "room loaded:
     TEST".
  3. The Room drone is audible without `--headless`.
  4. A real ESP32 dev shroom discovers Arco over mDNS and joins.
  5. On a machine with no repo clone: the `launcher` install, then
     `terrarium-dev run --room TEST --seconds 45` in snapshot mode, works.

### 8. Docs

- `docs/MM_TERRARIUM.md`: a new *Running in the container* subsection ahead
  of *Linux / WSL host setup*. The native steps stay, as the reference for
  what the image does.
- `README.md`: a short quickstart (install Docker Engine in WSL, the
  `launcher` one-liner, `terrarium-dev run --room TEST`).
- `docker/README.md`: the Docker Engine in WSL2 setup note (systemd,
  avahi-daemon, mirrored mode and the Hyper-V firewall rule, all as in setup
  steps 1 and 9), pins, and how to bump them.

## Phase 2 constraints (fixed here, specced later)

- GitHub Actions on standard GitHub-hosted runners only (never paid larger
  runners), building with `docker/build.sh`.
- Push to GHCR as a **public** package named `terrarium-dev` under the
  `musical-mycology` org, using the workflow's `GITHUB_TOKEN`.
- Tag scheme as in section 3; `:v<date>` tags cut deliberately.
- The image self-check (section 7) gates every push.
- Stay on the free tier: public repo plus public package. The Phase 2 spec
  links GitHub's current Actions and Packages billing pages and adds pruning
  of old untagged versions.
- Record the exception in `MM_ARCHITECTURE.md`'s CI/CD split (GitHub Actions,
  not mm-jenkins, for this image), alongside static sites.

## Open questions

None blocking. The two acceptance items (PulseAudio chain, D-Bus Avahi
mount) are verified during implementation, with fallbacks named above.
