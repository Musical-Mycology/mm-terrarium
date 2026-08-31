"""CaptureBit: records labelled sensor telemetry from a real phone so
gesture definitions come from measured data rather than guessed thresholds.
See docs/superpowers/specs/2026-08-07-sensor-telemetry-capture-design.md.

This is a TOOL Bit, not a production game Bit: it declares no light and no
audio, and it does not close the repo's "no production Bit exists" gap.

Deliberately thin. Wire parsing lives in devicelink/protocol.py and every
byte of persistence lives in capture/store.py, so what is left here is the
declaration plus dispatch.
"""

from pathlib import Path

from capture.store import CaptureError, CaptureStore, new_session_id
from control.bit import Bit
from control.roles import Role, RoleClass, RoleTable
from devicelink.protocol import decode_capture_command, decode_telemetry_batch

CAPTURE_NODE = "CAPTURE_NODE"

# How long a device may go silent before its open capture is closed as
# truncated. A phone that walks out of WiFi range mid-window must not leave
# a capture open for the rest of the session.
IDLE_TIMEOUT_S = 10.0

# Default trace root when no store is supplied, matching harness/capture_smoke.py.
CAPTURE_DIR = "./captures"


class CaptureBit(Bit):
    version = "0.1"

    def __init__(self, store: CaptureStore | None = None, config=None,
                 idle_timeout_s: float = IDLE_TIMEOUT_S,
                 provenance: dict | None = None):
        super().__init__(config)
        if store is None:
            store = CaptureStore(root=Path(CAPTURE_DIR),
                                 session_id=new_session_id(),
                                 bit={"name": "capture", "version": self.version},
                                 provenance=provenance)
        self._store = store
        self._idle_timeout_s = idle_timeout_s

    @property
    def role_table(self) -> RoleTable:
        recorder = Role(name="recorder", role_class=RoleClass.SHARED,
                        capacity=None, scored=False)
        return RoleTable(roles={"recorder": recorder},
                         node_map={CAPTURE_NODE: ["recorder"]})

    def update(self, dt: float) -> bool:
        """Never self-completes: a capture session ends when the operator
        ends it from the console. The tick is still used, to expire captures
        whose device has gone quiet."""
        self._store.expire(self._idle_timeout_s)
        return False

    def on_unload(self) -> None:
        # This engine has no per-device "leave" event -- GameServer.on_release
        # fires only for every registered device at once, when the whole Bit
        # unloads. So "close never arrives" (idle timeout, via update()) and
        # "device released mid-capture" (spec section 8) are the SAME code
        # path here: on_unload -> truncate_all. There is nothing else to wire.
        self._store.truncate_all("bit unloaded")

    def status(self) -> dict:
        """Rendered by the Terrarium Console with no console changes, which
        is what makes the console a live capture dashboard."""
        return {"session": self._store.session_id,
                "captures": self._store.counts(),
                "open": self._store.open_ids(),
                "failures": self._store.failures,
                "bytes": self._store.bytes_written}

    def verb_handlers(self) -> dict:
        return {"capture": self._on_capture, "telemetry": self._on_telemetry}

    # Both handlers return [] on success (there are no light cues to emit) or
    # a refusal string, which control/engine.py surfaces to the device as
    # /<dev>/error. Neither ever raises: boundary rule 2. `at` is unused: a
    # capture is a recording, not a rendered consequence, so there is nothing
    # here to schedule.
    def _on_capture(self, dev: str, args: list, at: float):
        try:
            cmd = decode_capture_command(args)
        except ValueError as exc:
            return f"bad capture command: {exc}"
        try:
            if cmd.action == "open":
                self._store.open_capture(dev, cmd)
            elif cmd.action == "close":
                self._store.close_capture(dev, cmd.meta)
            else:
                self._store.abandon(dev, cmd.meta.get("reason", ""))
        except CaptureError as exc:
            return str(exc)
        return []

    def _on_telemetry(self, dev: str, args: list, at: float):
        try:
            batch = decode_telemetry_batch(args)
        except ValueError as exc:
            return f"bad telemetry batch: {exc}"
        try:
            self._store.append(dev, batch)
        except CaptureError as exc:
            return str(exc)
        return []
