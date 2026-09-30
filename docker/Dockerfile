# syntax=docker/dockerfile:1
# terrarium-dev: a pre-built Terrarium dev and test box (docker/README.md).
# Build with docker/build.sh, which passes every ARG from docker/pins.env.
# Spec: docs/superpowers/specs/2026-09-29-terrarium-dev-container-design.md
ARG BASE_IMAGE

# ---- base: everything a run needs. The -dev packages from the verified
# Ubuntu 26.04 setup list (docs/MM_TERRARIUM.md, Linux / WSL host setup,
# step 1) are installed as-is: their runtime library names carry t64 and
# version suffixes, and the -dev list is the one known to resolve.
# build-essential + python3-dev: netifaces (o2litepy) builds from source.
FROM ${BASE_IMAGE} AS base
ENV DEBIAN_FRONTEND=noninteractive \
    TERM=xterm-256color \
    LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
      portaudio19-dev libavahi-client-dev libsndfile1-dev libfluidsynth-dev \
      libportmidi-dev fluid-soundfont-gm libncurses-dev libogg-dev \
      libvorbis-dev libflac-dev libopus-dev libglib2.0-dev \
      libasound2-plugins dbus avahi-daemon \
      python3 python3-venv python3-dev build-essential \
      nodejs git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---- build: o2 and the patched Arco server at their pins.
FROM base AS build
RUN apt-get update && apt-get install -y --no-install-recommends cmake \
    && rm -rf /var/lib/apt/lists/*
ARG O2_REPO
ARG O2_SHA
ARG ARCO_REPO
ARG ARCO_SHA
WORKDIR /build
RUN git init -q o2 && git -C o2 fetch -q --depth 1 "$O2_REPO" "$O2_SHA" \
    && git -C o2 checkout -q FETCH_HEAD \
    && cmake -S o2 -B o2/Release -DCMAKE_BUILD_TYPE=Release -DTESTS_BUILD=OFF \
         -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    && cmake --build o2/Release -j"$(nproc)"
RUN git init -q arco && git -C arco fetch -q --depth 1 "$ARCO_REPO" "$ARCO_SHA" \
    && git -C arco checkout -q FETCH_HEAD
COPY docs/upstream/arco-linux-build.patch docs/upstream/arco-libraries-ubuntu.txt /build/upstream/
# The Linux fixes are pending upstream with Roger Dannenberg: applied here
# only, never committed to the arco mirror.
RUN if git -C arco apply --check /build/upstream/arco-linux-build.patch; then \
      git -C arco apply /build/upstream/arco-linux-build.patch; \
    elif git -C arco apply --reverse --check /build/upstream/arco-linux-build.patch; then \
      echo "arco-linux-build.patch is already in ARCO_SHA: the fixes landed upstream. Delete the patch step here and docs/upstream/arco-linux-build.patch." >&2; exit 1; \
    else \
      echo "arco-linux-build.patch no longer applies to ARCO_SHA: rebase the patch onto the new pin." >&2; exit 1; \
    fi \
    && cp /build/upstream/arco-libraries-ubuntu.txt arco/apps/common/libraries.txt
# The SECOND configure is required: arcoserver.cmakeinclude tests USE_MIDI
# before option(USE_MIDI) defines it (docs/MM_TERRARIUM.md, setup step 6).
WORKDIR /build/arco/apps/pytest
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    && cmake -S . -B build \
    && cmake --build build -j"$(nproc)" \
    && ln -s pytestserver server \
    && test -x server

# ---- runtime: base + Arco + the venv + the snapshot + the scripts.
FROM base AS runtime
COPY --from=build /build/arco /opt/mm/arco
ENV MM_ARCO_PATH=/opt/mm/arco \
    ARCO_ROOT=/opt/mm/arco
ARG LUXAETERNA_REPO
ARG LUXAETERNA_SHA
# One venv, at /work/.venv: a mounted checkout's venv volume is seeded from
# it, and the snapshot reaches it through a symlink.
COPY requirements.txt requirements-dev.txt /opt/mm/terrarium/
RUN python3 -m venv /work/.venv \
    && /work/.venv/bin/python -m pip install --no-cache-dir -r /opt/mm/terrarium/requirements-dev.txt \
    && /work/.venv/bin/python -m pip install --no-cache-dir \
         "luxaeterna[websim] @ git+${LUXAETERNA_REPO}@${LUXAETERNA_SHA}" \
    && chmod -R a+rwX /work
COPY . /opt/mm/terrarium/
COPY docker/terrarium-dev /usr/local/bin/terrarium-dev
COPY docker/entrypoint.sh docker/selfcheck.sh /usr/local/lib/terrarium-dev/
COPY docker/asound.conf /etc/asound.conf
WORKDIR /opt/mm/terrarium
# The launcher runs as the host uid (--user), so the venv (chmod'd in its own
# layer above, to avoid duplicating it) and the snapshot are world-writable.
RUN ln -s /work/.venv /opt/mm/terrarium/.venv \
    && mkdir -p /opt/mm/terrarium/runs \
    && /usr/local/lib/terrarium-dev/entrypoint.sh stamp-venv \
    && chmod a+rw /work/.venv/.td-req-hash \
    && chmod -R a+rwX /opt/mm/terrarium
ENTRYPOINT ["/usr/local/lib/terrarium-dev/entrypoint.sh"]
CMD ["help"]
