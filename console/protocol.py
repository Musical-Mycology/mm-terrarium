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
    LoadRoomCommand,
    RunCommand,
    UnloadRoomCommand,
    bit_completed_event,
    bits_listed_event,
    error_event,
    parse_command,
    registration_changed_event,
    room_load_failed_event,
    room_load_progress_event,
    room_loaded_event,
    room_unloaded_event,
    state_changed_event,
)

__all__ = [
    "AbortCommand", "ListBitsCommand", "LoadBitCommand", "RunCommand",
    "LoadRoomCommand", "UnloadRoomCommand",
    "parse_command", "bit_completed_event", "bits_listed_event",
    "error_event", "registration_changed_event",
    "room_loaded_event", "room_unloaded_event", "room_load_failed_event",
    "room_load_progress_event",
    "state_changed_event", "role_view", "device_view", "snapshot_event",
    "devices_changed_event", "bit_status_event", "log_event",
    "ArmRoomCommand", "ReleaseRoomCommand", "parse_admin_command",
    "room_changed_event", "room_frame_event",
    "functions_changed_event", "function_fired_event", "FireFunctionCommand",
    "ListDesignsCommand", "GetDesignCommand", "SaveDesignCommand",
    "PublishDesignCommand", "CloneDesignCommand",
    "design_row", "designs_listed_event", "designs_changed_event",
    "design_event",
]


def role_view(role, requirement=None) -> dict:
    """`requirement` is the loaded Bit's InstrumentRequirement for
    `role.requires` (GameServer.slot_requirement), or None when the role has
    no `requires` slot or no Bit is loaded. Surfaced as `requires` so the
    rail can show an operator why a join was refused: {"slot", "capabilities"}
    with capabilities sorted for a stable wire shape, or None."""
    requires = None
    if role.requires is not None:
        requires = {"slot": role.requires,
                    "capabilities": (sorted(requirement.capabilities)
                                      if requirement is not None else [])}
    return {
        "role": role.name,
        "class": role.role_class.name,
        "capacity": role.capacity,
        "scored": role.scored,
        "ugen_manifest": role.ugen_manifest,
        "light_manifest": role.light_manifest,
        "welcome": role.welcome,
        "requires": requires,
    }


def device_view(info, role_name, url=None, muted=False) -> dict:
    return {"dev": info.dev, "name": info.name, "role": role_name, "url": url,
            "muted": muted}


def snapshot_event(*, state, installed_bits, loaded_bit, roles,
                   registration, devices, bit_status, room=None,
                   functions=None, terrarium_state=None, rooms=None,
                   instrument_functions=None, surface_instruments=None,
                   builtins=None, designs=None) -> dict:
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
        "functions": functions or [],
        "terrarium_state": terrarium_state,
        "rooms": rooms or [],
        "instrument_functions": instrument_functions or {},
        "surface_instruments": surface_instruments or {},
        "builtins": builtins or {},
        "designs": designs or [],
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


def functions_changed_event(functions, instruments=None, surfaces=None,
                            builtins=None) -> dict:
    """Every function the loaded Bit declares, as control.function_view's
    functions_view() builds them, plus the present instruments' SCRIPTED
    functions, the dev/room -> instrument-name map, and each instrument's
    built-in names. A function table is static per Bit, so `functions` in
    practice only changes on load/unload; the other three can also change
    on a Room load/unload."""
    return {"event": "functions_changed", "functions": functions,
            "instrument_functions": instruments or {},
            "surface_instruments": surfaces or {},
            "builtins": builtins or {}}


def function_fired_event(fired) -> dict:
    """One fire, as control.function_view's function_fired_view() builds it."""
    return {"event": "function_fired", "fired": fired}


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
class FireFunctionCommand:
    name: str
    dev: str | None = None


@dataclass
class ListDesignsCommand:
    pass


@dataclass
class GetDesignCommand:
    state: str
    name: str


@dataclass
class SaveDesignCommand:
    name: str
    text: str


@dataclass
class PublishDesignCommand:
    name: str


@dataclass
class CloneDesignCommand:
    source_state: str
    source_name: str
    new_name: str


def design_row(entry) -> dict:
    return {"name": entry.name, "state": entry.state, "error": entry.error}


def designs_listed_event(designs: list) -> dict:
    return {"event": "designs_listed", "designs": designs}


def designs_changed_event(designs: list) -> dict:
    return {"event": "designs_changed", "designs": designs}


def design_event(name: str, state: str, text: str, errors: list) -> dict:
    return {"event": "design", "name": name, "state": state,
            "text": text, "errors": errors}


def parse_admin_command(msg: dict):
    """Console-only admin commands -- never sent by the uplink's remote
    broker. Kept separate from uplink.protocol.parse_command: Room
    registration is a local, trusted-operator action (design spec section
    7), not something a remote fairyring peer should ever request. Firing a
    declared function is the same kind of action for the same reason, so it
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
    if command == "fire_function":
        name = msg.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("fire_function requires a non-empty string 'name'")
        dev = msg.get("dev")
        if dev is not None and not isinstance(dev, str):
            raise ValueError("fire_function 'dev' must be a string when given")
        return FireFunctionCommand(name=name, dev=dev or None)
    if command == "list_designs":
        return ListDesignsCommand()
    if command == "get_design":
        state = msg.get("state")
        if state not in ("published", "draft"):
            raise ValueError("get_design requires 'state' of 'published' or 'draft'")
        name = msg.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("get_design requires a non-empty string 'name'")
        return GetDesignCommand(state=state, name=name)
    if command == "save_design":
        name = msg.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("save_design requires a non-empty string 'name'")
        text = msg.get("text")
        if not isinstance(text, str):
            raise ValueError("save_design requires a string 'text'")
        return SaveDesignCommand(name=name, text=text)
    if command == "publish_design":
        name = msg.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("publish_design requires a non-empty string 'name'")
        return PublishDesignCommand(name=name)
    if command == "clone_design":
        source_state = msg.get("source_state")
        if source_state not in ("published", "draft"):
            raise ValueError(
                "clone_design requires 'source_state' of 'published' or 'draft'")
        source_name = msg.get("source_name")
        if not isinstance(source_name, str) or not source_name:
            raise ValueError(
                "clone_design requires a non-empty string 'source_name'")
        new_name = msg.get("new_name")
        if not isinstance(new_name, str) or not new_name:
            raise ValueError(
                "clone_design requires a non-empty string 'new_name'")
        return CloneDesignCommand(source_state=source_state,
                                  source_name=source_name, new_name=new_name)
    raise ValueError(f"unrecognized admin command: {command!r}")
