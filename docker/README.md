# terrarium-dev: the Terrarium dev container

## What it is

A pre-built linux/amd64 dev and test box: o2, the patched Arco server, the
Python venv and a snapshot of mm-terrarium `main`, driven by the
`terrarium-dev` launcher. It runs the test suites, `./terrarium.sh` and
`./smoke-test.sh` without the ten native setup steps. It is not a venue
image (no start on boot, no PA audio, amd64 only), and nothing here runs on
an ESP32: ESP32s are the o2lite clients, and the Terrarium needs a full
Linux or macOS kernel. Spec:
`docs/superpowers/specs/2026-09-29-terrarium-dev-container-design.md`.

**Image status:** the image is not yet published to GHCR (that is the Phase
2 pipeline). Until Phase 2 publishes `ghcr.io/musical-mycology/terrarium-dev`,
build it locally with `docker/build.sh` and pass `--tag local` (or run
`terrarium-dev use local` once; `terrarium-dev update` then tries to pull
`:local` and fails, so switch back with `terrarium-dev use main` before
updating). The pull-based commands below are the
intended path once it is published.

## Host setup (Docker Engine in WSL2)

Use Docker Engine inside the Ubuntu distro, **not Docker Desktop**. With
`--network host`, "host" means the WSL distro, which mirrored networking puts
on the LAN; Docker Desktop's host network is its own VM.

1. **Enable systemd in WSL** (`/etc/wsl.conf`: `[boot]` then `systemd=true`,
   then `wsl --shutdown` from Windows) so the daemons below start on their own.
2. **Install Docker Engine** from Docker's official apt repository, following
   <https://docs.docker.com/engine/install/ubuntu/>, then add yourself to the
   `docker` group and restart the WSL shell:

   **RUN ON: WSL UBUNTU**

   ```bash
   sudo usermod -aG docker "$USER"
   ```
3. **Install avahi-daemon** on the host. The container uses the host's daemon
   through bind mounts (never a second one: two daemons on host networking
   contend for UDP 5353). With no daemon Arco never advertises and the
   readiness probe dies 60 s later with "Arco did not report ready".

   **RUN ON: WSL UBUNTU**

   ```bash
   sudo apt install avahi-daemon && sudo systemctl enable --now avahi-daemon
   ```
4. **Networking for real devices.** WSL2
   defaults to NAT, so Linux sits on its own subnet: LAN devices cannot
   discover or reach Arco (ESP32 firmware connects to the internal IP in
   the O2 mDNS TXT record). Simulated devices are unaffected; the launcher
   warns when `wslinfo --networking-mode` reports `nat`. To try
   real devices, follow Microsoft's WSL networking docs
   (<https://learn.microsoft.com/windows/wsl/networking>):
   - **Windows 11 (22H2 or later):** `networkingMode=mirrored` under
     `[wsl2]` in `%UserProfile%\.wslconfig`, then `wsl --shutdown`. That
     page lists multicast support and direct LAN access to WSL for this
     mode. Allow inbound traffic for the WSL VM in the Hyper-V firewall
     (admin PowerShell, verbatim from that page):

     **RUN ON: WINDOWS (admin PowerShell)**

     ```powershell
     Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
     ```

     **Confirmed 2026-09-29 on a teammate's Windows 11 box:** mirrored mode
     with avahi-daemon active, and an ESP32 dev shroom discovers Arco over
     mDNS and connects. Avahi coexists with Windows' own mDNS responder on
     UDP 5353.
   - **Windows 10:** mirrored mode is unavailable. `networkingMode=bridged`
     with a Hyper-V external switch (Pro only) is deprecated and not on that
     page; unverified. Use simulated devices only, or a native host.

## Install the launcher

**RUN ON: WSL UBUNTU**

```bash
docker run --rm ghcr.io/musical-mycology/terrarium-dev:main launcher > terrarium-dev && chmod +x terrarium-dev
sudo mv terrarium-dev /usr/local/bin/
```

From a clone of mm-terrarium, `docker/terrarium-dev` works directly (it needs
no other file from the repo). Before Phase 2 publishes the image, the pull
form fails: build with `docker/build.sh` and use the clone's launcher with
`--tag local`.

## Everyday use

**RUN ON: WSL UBUNTU**

```bash
terrarium-dev run --room TEST --seconds 45   # ./terrarium.sh in the container
terrarium-dev test                            # pytest, then node --test
```

| Command | Runs inside the container |
|---|---|
| `terrarium-dev run [args]` (default) | `./terrarium.sh [args]` |
| `terrarium-dev smoke [args]` | `./smoke-test.sh [args]` |
| `terrarium-dev test` | `pytest`, then `node --test` |
| `terrarium-dev shell` | an interactive bash in the container |
| `terrarium-dev update` | pull the selected tag; prune venv volumes stamped with other image ids |
| `terrarium-dev clean` | `./terrarium.sh --clean` (only clears run records of stopped stacks; it refuses while a stack is running) |
| `terrarium-dev use <tag>` | save a default tag (`main`, `sha-<short>`, `v<date>`, `local`) to `~/.config/terrarium-dev/tag` |
| `terrarium-dev selfcheck` | hermetic image check (see *Self-check*) |

Flags go before the command:

- `--tag <tag>`: `main`, `sha-<short>`, `v<date>` or `local`. Default: the
  saved tag, else `main`. `terrarium-dev use v<date>` pins a known-good cut
  handed out by the team instead of following `main`.
- `--checkout <path>`: the mm-terrarium checkout to mount. Default: the
  current directory if it is one (has `terrarium.sh` and `control/`), else
  none, which selects snapshot mode.
- `--headless`: no audio mounts and no PulseAudio check.

**Checkout mode versus snapshot mode.**

- *Checkout mode:* your checkout is mounted at `/work` and edits take effect
  on the next run; runs and logs land in your checkout's `runs/`. A named
  volume `terrarium-venv-<hash of checkout path>` shadows `/work/.venv`, so
  the host's own `.venv` (a macOS or WSL venv) is never used inside the
  container. If the checkout has no `.venv`, the launcher creates an empty
  one so Docker does not create a root-owned mountpoint there. The volume is stamped with the image id and
  recreated when the image changes. Before `run`, `smoke`, `test`, `shell`
  and `clean`, the container entrypoint hashes the checkout's
  `requirements.txt` and `requirements-dev.txt` and compares it with the
  hash stamped in the venv volume; on a mismatch it prints "requirements
  changed; installing into the venv volume", runs `pip install -r
  requirements-dev.txt` into the volume and restamps. A branch that adds a
  Python dependency therefore needs no image rebuild. Arco and luxaeterna
  come from the image, so a checkout that needs newer ones needs a newer
  image (`terrarium-dev update`, or bump the pins and build with `--tag
  local`).
- *Snapshot mode:* no checkout; the copy of `main` baked into the image runs
  from `/opt/mm/terrarium`, and `~/terrarium-runs` is mounted over its
  `runs/` so logs survive `--rm`.

The launcher refuses, naming the fix, when: Docker is not reachable;
`/run/avahi-daemon/socket` or the system D-Bus socket is missing; there is no PulseAudio socket and no
`--headless`; another terrarium-dev stack is running; or a checkout's venv
volume is still in use by a container (stop or remove it and rerun). A
failed `docker pull` also stops with a message naming the tag to check.

**Old venv volumes.** Volumes from moved or deleted checkouts are never
pruned automatically. List them with `docker volume ls --filter
label=mm.terrarium-dev.venv=1` and remove one with `docker volume rm <name>`.

## Troubleshooting

- Import errors from `luxaeterna` or `pyarco` after pulling a branch mean the
  image is older than the checkout (Arco and luxaeterna come from the image,
  not the venv). Run `terrarium-dev update`, or bump the pins and build with
  `--tag local`.

## Networking

The container runs with `--network host`, so it only reaches real devices if
the host itself is on the LAN.

| Host | Real devices | Simulated devices |
|---|---|---|
| Native Linux | yes | yes |
| WSL2, Windows 11, mirrored mode | yes | yes |
| WSL2, NAT mode | no (the launcher warns) | yes |
| macOS, Docker Desktop | no (its network is its own VM; the launcher warns) | `run`, `smoke` and `shell` refuse (no host Avahi socket); only `test`, `clean` and `selfcheck` work |

On macOS use a native setup (`docs/MM_TERRARIUM.md`, *Running it*) to run the
stack; the container is useful there only for the test suites.

One stack per host: with host networking, two stacks collide on ports 8080,
8788 and 8772, so the launcher refuses to start a
second one. `terrarium-dev test`, `clean` and `selfcheck` do not use host
networking and are not affected.

## Audio

On by default. On WSL2 the launcher mounts WSLg (`/mnt/wslg`) and points
`PULSE_SERVER` at its PulseAudio socket; on native Linux it mounts
`$XDG_RUNTIME_DIR/pulse/native`. The image routes ALSA's default device to
pulse (`/etc/asound.conf`) with a `sysdefault` fallback: without it Arco
segfaults at startup when no PulseAudio server is reachable (`--headless`,
`selfcheck`). The fallback applies only when the pulse connection fails. If no PulseAudio socket exists the launcher
refuses rather than silently muting: fix the audio setup, or pass
`--headless` before the command (`terrarium-dev --headless run ...`) to run
silent (CI always does). The ALSA-to-pulse path is
still a Phase 1 acceptance item: if the Room drone is silent without
`--headless`, report it.

## Building and pins

`docker/build.sh` builds `ghcr.io/musical-mycology/terrarium-dev:local` for
linux/amd64 from the current checkout (that checkout is the snapshot), using
`docker/Dockerfile`. `docker/build.sh --tag <tag>` names another tag and
`--dry-run` prints the `docker buildx build` command only. Phase 2's CI will
call the same script.

`docker/pins.env` holds every source-built dependency (base image, arco, o2,
luxaeterna): `BASE_IMAGE` (Ubuntu 26.04 pinned by digest), and the repo and
full SHA for Arco, o2 and luxaeterna. Python requirements are ranges and apt
packages float, so two builds of the same pins can differ in those. To
bump one, change that one line in a PR; use full SHAs. Test a bump with
`docker/build.sh` and `terrarium-dev --tag local selfcheck`.

The Arco Linux patch (`docs/upstream/arco-linux-build.patch`) is applied only
inside the image build and never committed to the arco mirror. If a bump of
`ARCO_SHA` breaks it, the build fails at the patch step with one of two
messages:

- "no longer applies to ARCO_SHA: rebase the patch onto the new pin": Arco
  changed the touched code; regenerate the patch against the new SHA.
- "already in ARCO_SHA: the fixes landed upstream": the reverse apply
  succeeded, so Roger Dannenberg has taken the fixes. Delete the patch step
  from the Dockerfile and `docs/upstream/arco-linux-build.patch`.

The o2 patch (`docs/upstream/o2-csget-bad-id.patch`, a one-line fix for an
Arco abort on a stale o2lite cs/get) is handled the same way against
`O2_SHA`, with the same two failure messages naming `O2_SHA`.

## Self-check

**RUN ON: WSL UBUNTU**

```bash
terrarium-dev selfcheck            # local build: terrarium-dev --tag local selfcheck
```

Hermetic: no mounts, Docker's default bridge network, and the image's own
`dbus-daemon` and `avahi-daemon` (there is no host daemon to contend with on
UDP 5353 in its own network namespace), so it needs no host setup beyond a
reachable Docker. It runs both test suites and `./smoke-test.sh --ci` on the
baked snapshot and prints `SELFCHECK_OK` only if all three pass. That proves
the image's toolchain, Arco build, venv and mDNS path work end to end. It
does not prove audio output, host Avahi mounts or real-device networking.
