"""python -m harness.led_smoke — drive TestBit through the in-process stack and
watch it on the Web LED simulator.

Requires luxaeterna[websim] installed editable (see requirements-dev.txt):
    python -m pip install -e "/Users/chris/projects/luxaeterna[websim]"
"""

from __future__ import annotations

import time

from bits.test_bit import TestBit
from control.engine import GameServer
from control.state import State
from harness.device_bridge import DeviceBridge
from luxaeterna.backends.websim import WebSimBackend
from luxaeterna.output import OutputLoop
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.universe import Universe

HOST, PORT = "127.0.0.1", 8770


def main() -> None:
    gs = GameServer({"test_bit": TestBit})
    cap = shroom_capability()
    bridge = DeviceBridge(capability=cap)
    gs.on_release = bridge.on_release

    gs.load_bit("test_bit")
    res = gs.join("sim-dev", "TEST_PLAYER_NODE")
    session = bridge.on_grant(res)

    uni = Universe()
    backend = WebSimBackend(capability=cap, host=HOST, port=PORT)
    loop = OutputLoop(uni, backend, on_frame=session.render_into, always_send=True)
    loop.start()
    print(f"Watch the Shroom at http://{HOST}:{PORT}/  (Ctrl-C to stop)")

    gs.run()
    try:
        while session.state != "running":
            time.sleep(0.02)
        cc = 0
        while gs.state == State.RUNNING:
            session.feed_midi(0xB0, 74, cc)          # cc:74 -> hue
            session.feed_midi(0x90, 60, 100)         # new voice at current hue
            cc = (cc + 8) % 128
            gs.tick(0.15)                            # advances TestBit toward complete
            time.sleep(0.15)
        time.sleep(1.2)                              # let the closing fade + idle play
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()


if __name__ == "__main__":
    main()
