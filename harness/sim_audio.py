"""Generated tones for the simulated Tuneshroom's /<dev>/play path.

The real device plays preloaded samples off disk (harness/local_sample.py,
MM_HARDWARE_DESIGN.md section 4.4). The simulator has no sample library and
should not grow an asset directory just to click, so the samples are tiny
sine-blip WAVs generated in memory at preload and played through macOS
`afplay`. Where afplay is missing (or raises), the sink degrades to a
printed `play: <name>` line: the operator still sees the cue land, which is
the sim's actual job.

Latency is deliberately NOT the point here: `afplay` spawns a subprocess
per play, which is fine for a browser-canvas simulator and useless for the
sub-20 ms hardware path. Do not lift this sink onto a device.
"""

from __future__ import annotations

import io
import math
import struct
import subprocess
import tempfile
import wave

from harness.local_sample import SamplePlayer

_RATE = 22050

# name -> list of (frequency_hz, seconds) segments, matching the two sample
# names TestBit declares (bits/test/test_bit.py: click on tap, chime on
# double-tap).
SIM_TONES: dict[str, list[tuple[float, float]]] = {
    "click": [(2000.0, 0.03)],
    "chime": [(1318.5, 0.09), (1760.0, 0.12)],
}


def tone_wav(segments: list[tuple[float, float]], rate: int = _RATE) -> bytes:
    """A mono 16-bit WAV of consecutive sine blips, as bytes.

    Each segment gets a linear fade-out over its own length so the blip
    ends at zero amplitude and never clicks (the bad kind of click)."""
    frames = bytearray()
    for freq, seconds in segments:
        n = max(1, int(rate * seconds))
        for i in range(n):
            fade = 1.0 - (i / n)
            value = math.sin(2.0 * math.pi * freq * i / rate) * fade
            frames += struct.pack("<h", int(value * 32000))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return out.getvalue()


def _afplay(path: str) -> None:
    # Fire-and-forget: -q 1 keeps afplay quiet on stdout, and nothing waits
    # on the process -- a play must never stall the tick loop.
    subprocess.Popen(["afplay", "-q", "1", path],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class AfplaySink:
    """SamplePlayer sink: WAV bytes -> temp file once -> afplay per play.

    The temp file is written on first play of each name and reused after,
    so the play path is one subprocess spawn, not a write plus a spawn. A
    raising runner (no afplay on this host, say) degrades to a printed
    line rather than an exception: see the module docstring.
    """

    def __init__(self, runner=None) -> None:
        self._runner = runner or _afplay
        self._paths: dict[str, str] = {}

    def write(self, name: str, data: bytes) -> None:
        path = self._paths.get(name)
        if path is None:
            handle = tempfile.NamedTemporaryFile(
                prefix=f"mm-sim-{name}-", suffix=".wav", delete=False)
            with handle:
                handle.write(data)
            path = handle.name
            self._paths[name] = path
        try:
            self._runner(path)
        except Exception:
            print(f"play: {name}", flush=True)


def build_sim_player(runner=None) -> SamplePlayer:
    """A preloaded SamplePlayer over the generated tone set."""
    player = SamplePlayer(
        sample_paths={name: f"<generated:{name}>" for name in SIM_TONES},
        sink=AfplaySink(runner=runner),
        loader=lambda ref: tone_wav(
            SIM_TONES[ref.split(":", 1)[1].rstrip(">")]),
    )
    player.preload()
    return player
