"""Wire message schemas for the Terrarium Console -- the JSON-serializable
contract between the browser panel and ConsoleAgent. Pure dict builders with
no engine imports, mirroring uplink/protocol.py. Command parsing and the
events shared with the uplink are re-used from uplink.protocol so there is a
single source of truth.
"""

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
                   registration, devices, bit_status) -> dict:
    return {
        "event": "snapshot",
        "state": state,
        "installed_bits": installed_bits,
        "loaded_bit": loaded_bit,
        "roles": roles,
        "registration": registration,
        "devices": devices,
        "bit_status": bit_status,
    }


def devices_changed_event(devices) -> dict:
    return {"event": "devices_changed", "devices": devices}


def bit_status_event(status) -> dict:
    return {"event": "bit_status", "status": status}


def log_event(level: str, message: str) -> dict:
    return {"event": "log", "level": level, "message": message}
