"""Wire message schemas for the Terrarium Console -- the JSON-serializable
contract between the browser panel and ConsoleAgent. Pure dict builders with
no engine imports, mirroring uplink/protocol.py. Command parsing and the
events shared with the uplink are re-used from uplink.protocol so there is a
single source of truth.
"""

from dataclasses import dataclass

from uplink.protocol import (  # re-exported: single source of truth
    AbortCommand,
    ListBitsCommand,
    LoadBitCommand,
    RunCommand,
    bit_completed_event,
    bits_listed_event,
    error_event,
    parse_command,
    registration_changed_event,
    state_changed_event,
)

__all__ = [
    "AbortCommand", "ListBitsCommand", "LoadBitCommand", "RunCommand",
    "parse_command", "bit_completed_event", "bits_listed_event",
    "error_event", "registration_changed_event",
    "state_changed_event", "role_view", "device_view", "snapshot_event",
    "devices_changed_event", "bit_status_event", "log_event",
    "ArmRoomCommand", "ReleaseRoomCommand", "parse_admin_command",
    "room_changed_event", "room_frame_event",
    "triggers_changed_event", "trigger_fired_event", "FireTriggerCommand",
]


def role_view(role) -> dict:
    return {
        "role": role.name,
        "class": role.role_class.name,
        "capacity": role.capacity,
        "scored": role.scored,
        "ugen_manifest": role.ugen_manifest,
        "light_manifest": role.light_manifest,
        "welcome": role.welcome,
    }


def device_view(info, role_name, url=None) -> dict:
    return {"dev": info.dev, "name": info.name, "role": role_name, "url": url}


def snapshot_event(*, state, installed_bits, loaded_bit, roles,
                   registration, devices, bit_status, room=None,
                   triggers=None) -> dict:
    return {
        "event": "snapshot",
        "state": state,
        "installed_bits": installed_bits,
        "loaded_bit": loaded_bit,
        "roles": roles,
        "registration": registration,
        "devices": devices,
        "bit_status": bit_status,
        "room": room,
        "triggers": triggers or [],
    }


def room_changed_event(room) -> dict:
    """The Room panel's read model. `room` is control.room_view.room_view()'s
    output, or None when no Room is configured."""
    return {"event": "room_changed", "room": room}


def room_frame_event(dev: str, channels) -> dict:
    """One rendered Room frame, for display only. Decimated and droppable:
    see console/agent.py's ROOM_FRAME_INTERVAL. An int list rather than base64
    for consistency with devicelink/protocol.py's leds_event."""
    return {"event": "room_frame", "dev": dev, "channels": list(channels)}


def triggers_changed_event(triggers) -> dict:
    """Every trigger the loaded Bit declares, as control.trigger_view's
    triggers_view() builds them. A trigger table is static per Bit, so in
    practice this fires on load and unload."""
    return {"event": "triggers_changed", "triggers": triggers}


def trigger_fired_event(fired) -> dict:
    """One fire, as control.trigger_view's trigger_fired_view() builds it."""
    return {"event": "trigger_fired", "fired": fired}


def devices_changed_event(devices) -> dict:
    return {"event": "devices_changed", "devices": devices}


def bit_status_event(status) -> dict:
    return {"event": "bit_status", "status": status}


def log_event(level: str, message: str) -> dict:
    return {"event": "log", "level": level, "message": message}


@dataclass
class ArmRoomCommand:
    room_type: str
    fixture: str
    window_seconds: float = 30.0


@dataclass
class ReleaseRoomCommand:
    room_type: str
    fixture: str | None = None


@dataclass
class FireTriggerCommand:
    name: str
    dev: str | None = None


def parse_admin_command(msg: dict):
    """Console-only admin commands -- never sent by the uplink's remote
    broker. Kept separate from uplink.protocol.parse_command: Room
    registration is a local, trusted-operator action (design spec section
    7), not something a remote fairyring peer should ever request. Firing a
    declared trigger is the same kind of action for the same reason, so it
    lives here too rather than in the shared parser."""
    command = msg.get("command")
    if command == "arm_room":
        room_type = msg.get("room_type")
        if not isinstance(room_type, str):
            raise ValueError("arm_room requires a string 'room_type'")
        fixture = msg.get("fixture")
        if not isinstance(fixture, str) or not fixture:
            raise ValueError("arm_room requires a non-empty string 'fixture'")
        window = msg.get("window_seconds", 30.0)
        return ArmRoomCommand(room_type=room_type, fixture=fixture,
                              window_seconds=float(window))
    if command == "release_room":
        room_type = msg.get("room_type")
        if not isinstance(room_type, str):
            raise ValueError("release_room requires a string 'room_type'")
        fixture = msg.get("fixture")
        if fixture is not None and not isinstance(fixture, str):
            raise ValueError("release_room 'fixture' must be a string when given")
        return ReleaseRoomCommand(room_type=room_type, fixture=fixture)
    if command == "fire_trigger":
        name = msg.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("fire_trigger requires a non-empty string 'name'")
        dev = msg.get("dev")
        if dev is not None and not isinstance(dev, str):
            raise ValueError("fire_trigger 'dev' must be a string when given")
        return FireTriggerCommand(name=name, dev=dev or None)
    raise ValueError(f"unrecognized admin command: {command!r}")
