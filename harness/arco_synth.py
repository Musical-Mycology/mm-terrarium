"""ArcoSynthPool: the pyarco-backed SynthPool behind control/audio.py.

Dev/test-only, and a DELIBERATE holding position. Boundary rule 1 puts audio
decisions in Control, and the pure half of this already lives there; only the
pyarco-touching half sits in harness/, matching how luxaeterna is currently
carried (requirements-dev.txt, importorskip in tests). It moves into control/
once pyarco's source-of-truth is settled, which is bootstrap open question #1
and Roger Dannenberg's call. See
docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md section 5.

pyarco imports happen INSIDE start(), never at module level, so importing this
module costs nothing when Arco is absent and the offline suite stays green.

Run a client with:
    PYTHONPATH=/Users/chris/projects/arco <interpreter> ...
"""

from __future__ import annotations

# Must be a real General MIDI set: program numbers only mean what bits/test_bit.py
# and control/audio.py's WELCOME_INSTRUMENTS assume (e.g. program 89 = Warm Pad,
# program 9 = Glockenspiel) if the soundfont follows the GM map. VintageDreamsWaves
# is a 314 KB synth-waveform collection that does not; its program 89 is "Techno
# Bells", which decays, so the sustained drone died within seconds during live
# testing. FluidR3_GM is the standard GM set (also what Roger Dannenberg's own
# arco/apps/pytest/miditest.py expects). Do not change the program numbers below
# to "fix" this; they were correct all along, only the soundfont was wrong.
DEFAULT_SOUNDFONT = "/Users/chris/projects/fluidsynth/sf2/FluidR3_GM.sf2"


class ArcoVoice:
    """One MIDI channel on the shared Flsyn ugen.

    The channel is REAL but INTERNAL: no method here takes one, so callers use
    the same surface Roger's written notes describe (allocate N voices sharing
    one Flsyn) while the implementation does what his shipped MidiSender does.
    If the channel-parameter design wins instead, the change stops at this file.
    """

    def __init__(self, flsyn, channel: int) -> None:
        self._flsyn = flsyn
        self.channel = channel

    def note_on(self, key: int, vel: int) -> None:
        self._flsyn.noteon(self.channel, key, vel)

    def note_off(self, key: int) -> None:
        self._flsyn.noteoff(self.channel, key)

    def control_change(self, num: int, val: int) -> None:
        self._flsyn.control_change(self.channel, num, val)

    def program_change(self, prog: int) -> None:
        self._flsyn.program_change(self.channel, prog)

    def all_off(self) -> None:
        self._flsyn.alloff(self.channel)


class ArcoSynthPool:
    """One Flsyn ugen, up to max_channels voices sharing it."""

    def __init__(self, soundfont: str = DEFAULT_SOUNDFONT,
                 ensemble: str = "arco", max_channels: int = 16) -> None:
        self._soundfont = soundfont
        self._ensemble = ensemble
        self._free = list(range(max_channels))
        self._flsyn = None
        self._sched = None
        self._arco = None

    def start(self) -> None:
        """Connect to the Arco server and build the shared Flsyn.

        arco.initialize() BLOCKS until connected and reset, then poll() is all
        that is needed: we deliberately do not call sched.run(), because the
        driver loop owns the loop.
        """
        from pyarco import sched                       # noqa: PLC0415 (lazy by design)
        from pyarco.arco_engine import arco            # noqa: PLC0415
        from pyarco.ugens.flsyn import Flsyn           # noqa: PLC0415

        arco.initialize(ensemble=self._ensemble)       # raises TimeoutError if no server
        self._sched = sched
        self._arco = arco
        self._flsyn = Flsyn(self._soundfont)
        self._flsyn.play()

    def acquire(self) -> ArcoVoice:
        if self._flsyn is None:
            raise RuntimeError("ArcoSynthPool.start() must run before acquire()")
        if not self._free:
            raise RuntimeError(
                "no free MIDI channels: one Flsyn carries 16 voices")
        return ArcoVoice(self._flsyn, self._free.pop(0))

    def release(self, voice: ArcoVoice) -> None:
        if voice.channel not in self._free:
            self._free.append(voice.channel)
            self._free.sort()

    def poll(self) -> None:
        if self._sched is not None:
            self._sched.poll()                 # pumps o2lite via its poll functions

    def shutdown(self) -> None:
        """Silence every channel, then drop the Flsyn so pyarco's destructor
        frees the Arco ugen id. Boundary rule 1: Control owns the id space,
        which means freeing it at unload (see arco/doc/pyarco.md, "Ugen IDs")."""
        if self._flsyn is not None:
            for chan in range(16):
                self._flsyn.alloff(chan)
            self._flsyn = None
        if self._arco is not None:
            self._arco.finish()
            self._arco = None
        self._sched = None
