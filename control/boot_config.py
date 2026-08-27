"""Boot-time configuration for control.boot's load sequence. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.bit_config import BitConfig


@dataclass
class BootConfig:
    room_name: str
    bit_name: str
    # The resolved BitConfig (manifest + any launch-time overrides) for
    # bit_name, threaded through to GameServer.load_bit() so a Bit's
    # __init__ sees its own manifest defaults (e.g. TestBit's
    # extras["run_duration_seconds"]). None keeps every existing caller
    # that never set this (e.g. tests constructing BootConfig directly)
    # on load_bit's own config=None default -- an unconfigured Bit
    # instantiation, exactly as before this field existed.
    bit_config: BitConfig | None = None
    arco_soundfont: str | None = None
    # None = no array backend configured; "simulator" = Terrarium spawns
    # one (Spec 2's job); any other string = a real ArtNet/WLED host.
    array_backend: str | None = None
    arco_ready_timeout: float = 15.0
    room_setup_timeout: float = 30.0
    # O2 ensemble name Control and pyarco share with the Arco server.
    o2_ensemble: str = "arco"
    # How far ahead of a gesture a cue is scheduled, in seconds. ONE
    # installation-wide constant, never per-cue: a per-cue horizon would let
    # two cues from one gesture land on different frames and would make the
    # clamp counter meaningless. It must clear the 44 Hz frame quantization
    # (22.7 ms) plus Arco's block and buffer latency plus network time.
    # MEASURED 2026-08-14, and 60 ms is kept because the measurement says it
    # is right -- this is no longer a placeholder. Live o2lite run against a
    # real Arco, 2418 frames, using --horizon 0 so nothing could be held back
    # and the figure is genuine one-way delivery:
    #
    #     p50 4.5 ms | p95 9.3 ms | p99 11.8 ms | p99.9 38.6 ms | worst 80.2
    #
    # The horizon has to cover the whole gesture-to-display chain, not just
    # that transport hop: Control's 44 Hz render tick (22.7 ms of
    # quantization) + delivery (11.8 ms at p99) + the device's own tick
    # (~5 ms) ~= 40 ms. 60 ms covers it with ~20 ms of headroom for the
    # jitter tail, and p99 rather than worst-case is deliberate -- this is
    # fixed added latency on EVERY cue, so sizing it to the worst frame ever
    # seen taxes every gesture in the room for one hiccup.
    #
    # DO NOT "fix" this by reading a clamp count. The earlier belief that
    # 60 ms was far too small came from a 93%-clamped run and a ~67 ms
    # end-to-end estimate; both were artifacts. O2 honors the timestamp and
    # delivers the frame AT `when`, so a device-side queue that re-checks the
    # deadline on arrival always finds it a few ms past due, at ANY horizon.
    # Measured: 93.3% clamped at a 150 ms horizon and 95.6% at 300 ms, with
    # lateness pinned near +3 ms both times. The old "~67 ms" was just
    # 60 ms of horizon plus ~6 ms of that overhead. See
    # docs/MM_TERRARIUM.md's "Not yet built / deferred".
    #
    # Every figure above is a DEV-BOX figure. No venue-box measurement
    # exists, and none of these numbers carry from a dev box to the venue box.
    cue_horizon: float = 0.060

    # Control-side reap threshold (seconds of silence before a device is
    # removed from DevicePool and, if it held one, its role slot freed).
    # Default is three missed heartbeats at the harness clients' own
    # default --heartbeat-interval (5.0s) -- the same generous-multiple
    # shape _MAX_CLOSING_FRAMES already uses relative to a session's fade
    # time. See docs/superpowers/specs/
    # 2026-08-25-device-liveness-detection-design.md.
    stale_timeout: float = 15.0

    @property
    def array_backend_configured(self) -> bool:
        return self.array_backend is not None
