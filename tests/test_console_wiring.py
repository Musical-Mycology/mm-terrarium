from bits.test_bit import TestBit
from console.agent import ConsoleAgent
from control.engine import GameServer
from uplink.link import UplinkAgent
from uplink.transport import FakeTransport


class _RecordingServer:
    def __init__(self):
        self.broadcasts = []
    def drain_new_clients(self): return []
    def drain_inbound(self): return []
    def send(self, client, msg): pass
    def broadcast(self, msg): self.broadcasts.append(msg)


def test_console_and_uplink_observe_the_same_engine_simultaneously():
    gs = GameServer({"TestBit": TestBit})
    transport = FakeTransport()
    transport.connect()
    uplink = UplinkAgent(gs, transport)
    console_server = _RecordingServer()
    ConsoleAgent(gs, console_server)

    gs.load_bit("TestBit")   # one transition sequence, two observers

    assert any(m.get("event") == "state_changed" for m in transport.sent)
    assert any(m.get("event") == "state_changed" for m in console_server.broadcasts)
