"""Boot-time configuration for control.boot's load sequence. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.rooms import RoomType


@dataclass
class BootConfig:
    room_type: RoomType
    bit_name: str
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
    # STILL A PLACEHOLDER, and known too small: the live 2026-08-13 o2lite
    # run clamped 762 of 820 frames, so scheduling was bypassed on 93% of
    # them, and single-frame arithmetic put end-to-end delivery through Arco
    # at ~67 ms against this 60 ms. One frame is not a distribution, which is
    # why this has not simply been raised to 67 ms.
    #
    # The tooling to replace it properly landed 2026-08-14 and is not yet
    # run: TimedQueue now records the lateness behind every clamp, and
    # harness/sync_bench.py reduces it to mean/p95/p99/worst. Measure with a
    # deliberately oversized --horizon so nothing clamps and the sample is
    # not censored, then take p99 (see the design spec for why p99 and not
    # worst-case: this is fixed added latency on EVERY cue). A live run is
    # currently blocked on an upstream O2 clock-sync problem -- see
    # docs/MM_TERRARIUM.md's "Not yet built / deferred".
    #
    # Whatever number comes out is a DEV-BOX figure. No venue-box measurement
    # exists, and none of these numbers carry from a dev box to the venue box.
    cue_horizon: float = 0.060

    @property
    def array_backend_configured(self) -> bool:
        return self.array_backend is not None
