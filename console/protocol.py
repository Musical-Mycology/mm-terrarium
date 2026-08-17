"""Wire message schemas for the Terrarium Console -- the JSON-serializable
contract between the browser panel and ConsoleAgent. Pure dict builders with
no engine imports, mirroring uplink/protocol.py. Command parsing and the
events shared with the uplink are re-used from uplink.protocol so there is a
single source of truth.
"""

from dataclasses import dataclass

from uplink.protocol import (  # re-exported: single source of truth
    AbortCommand,
    LoadBitCommand,
    RunCommand,
    bit_completed_event,
    error_event,
    parse_command,
    registration_changed_event,
    state_changed_event,
)

__all__ = [
    "AbortCommand", "LoadBitCommand", "RunCommand", "parse_command",
    "bit_completed_event", "error_event", "registration_changed_event",
    "state_changed_event", "role_view", "device_view", "snapshot_event",
    "devices_changed_event", "bit_status_event", "log_event",
    "ArmRoomCommand", "ReleaseRoomCommand", "parse_admin_command",
    "room_changed_event",
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


def device_view(info, role_name) -> dict:
    return {"dev": info.dev, "name": info.name, "role": role_name}


def snapshot_event(*, state, installed_bits, loaded_bit, roles,
                   registration, devices, bit_status, room=None) -> dict:
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
    }


def room_changed_event(room) -> dict:
    """The Room panel's read model. `room` is control.room_view.room_view()'s
    output, or None when no Room is configured."""
    return {"event": "room_changed", "room": room}


def devices_changed_event(devices) -> dict:
    return {"event": "devices_changed", "devices": devices}


def bit_status_event(status) -> dict:
    return {"event": "bit_status", "status": status}


def log_event(level: str, message: str) -> dict:
    return {"event": "log", "level": level, "message": message}


@dataclass
class ArmRoomCommand:
    room_type: str
    window_seconds: float = 30.0


@dataclass
class ReleaseRoomCommand:
    room_type: str


def parse_admin_command(msg: dict):
    """Console-only admin commands -- never sent by the uplink's remote
    broker. Kept separate from uplink.protocol.parse_command: Room
    registration is a local, trusted-operator action (design spec section
    7), not something a remote fairyring peer should ever request."""
    command = msg.get("command")
    if command == "arm_room":
        room_type = msg.get("room_type")
        if not isinstance(room_type, str):
            raise ValueError("arm_room requires a string 'room_type'")
        window = msg.get("window_seconds", 30.0)
        return ArmRoomCommand(room_type=room_type, window_seconds=float(window))
    if command == "release_room":
        room_type = msg.get("room_type")
        if not isinstance(room_type, str):
            raise ValueError("release_room requires a string 'room_type'")
        return ReleaseRoomCommand(room_type=room_type)
    raise ValueError(f"unrecognized admin command: {command!r}")
