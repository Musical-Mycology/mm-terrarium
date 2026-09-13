"""Wire message schemas for the Terrarium uplink -- the JSON-serializable
contract between UplinkAgent and a future fairyring broker. See design spec
section 4.
"""

from dataclasses import dataclass, field

from control.lobby import TERRARIUM_ADMIN
from control.roles import RoleClass


@dataclass(frozen=True)
class UplinkIdentity:
    """What the box presents in the first frame of every connection (spec
    2026-09-13 section 6.2; shape from mm-fairyring issue #1)."""
    tenant_slug: str
    terrarium_name: str
    secret: str = field(repr=False)


def identity_frame(identity: UplinkIdentity) -> dict:
    return {"event": "identity", "tenant_slug": identity.tenant_slug,
            "terrarium_name": identity.terrarium_name,
            "secret": identity.secret}


# --- Down: fairyring -> Terrarium, one dataclass per command ---------------

@dataclass
class LoadBitCommand:
    name: str
    overrides: dict | None = None
    # Which Room to load the Bit into. None keeps the active Room (and is
    # refused with "no room loaded" when there is none). A different name
    # than the active Room makes the Console agent unload and reload the
    # Room first (spec 2026-09-10 section 4).
    room: str | None = None


@dataclass
class RunCommand:
    pass


@dataclass
class AbortCommand:
    pass


@dataclass
class RestartCommand:
    pass


@dataclass
class ListBitsCommand:
    pass


@dataclass
class LoadRoomCommand:
    name: str


@dataclass
class UnloadRoomCommand:
    force: bool = False


def parse_command(msg: dict):
    """Parse an inbound down-message dict into a command object.

    Raises ValueError for an unrecognized or malformed command.
    """
    command = msg.get("command")
    if command == "load_bit":
        name = msg.get("name")
        if not isinstance(name, str):
            raise ValueError("load_bit requires a string 'name'")
        overrides = msg.get("overrides")
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("load_bit 'overrides' must be a dict when given")
        room = msg.get("room")
        if room is not None and not isinstance(room, str):
            raise ValueError("load_bit 'room' must be a string when given")
        return LoadBitCommand(name=name, overrides=overrides, room=room)
    if command == "run":
        return RunCommand()
    if command == "abort":
        return AbortCommand()
    if command == "restart":
        return RestartCommand()
    if command == "list_bits":
        return ListBitsCommand()
    if command == "load_room":
        name = msg.get("name")
        if not isinstance(name, str):
            raise ValueError("load_room requires a string 'name'")
        return LoadRoomCommand(name=name)
    if command == "unload_room":
        force = msg.get("force", False)
        if not isinstance(force, bool):
            raise ValueError("unload_room 'force' must be a bool when given")
        return UnloadRoomCommand(force=force)
    raise ValueError(f"unrecognized command: {command!r}")


# --- Up: Terrarium -> fairyring, plain dict builders ------------------------
# (terminal messages -- only ever produced here, never parsed back on this
# side, so a builder function is enough; no dataclass round-trip needed.)

def state_changed_event(state_name: str, loaded_bit: str | None = None, *,
                        terrarium_state: str | None = None,
                        lan_ip: str | None = None) -> dict:
    event = {"event": "state_changed", "state": state_name,
             "loaded_bit": loaded_bit, "terrarium_state": terrarium_state}
    if lan_ip is not None:
        event["lan_ip"] = lan_ip
    return event


def registration_changed_event(counts: list[tuple[str, int, int | None]]) -> dict:
    return {
        "event": "registration_changed",
        "roles": [
            {"role": name, "count": count, "capacity": capacity}
            for name, count, capacity in counts
        ],
    }


def players_view(granted) -> list[dict]:
    """bit_completed.players (spec 2026-09-13 section 5.2): JAM is "jam",
    every other player-bearing class is "scored". ROOM never reaches here.
    The reserved TERRARIUM_ADMIN ("terrarium") id is refused on the device
    wire (devicelink/agent.py); this is a second guard against it ever
    appearing in players (spec 5.3, MycoQuest invariant 15)."""
    return [{"dev": dev, "role": role,
             "class": "jam" if role_class is RoleClass.JAM else "scored"}
            for dev, role, role_class in granted
            if dev != TERRARIUM_ADMIN]


def bit_completed_event(result: dict, bit_name: str = "",
                        bit_version: str = "", *, room_name=None,
                        terrarium_config_version=None, players=()) -> dict:
    event = {
        "event": "bit_completed",
        "result": result,
        "bit": {"name": bit_name, "version": bit_version},
        "players": list(players),
    }
    if room_name is not None:
        event["room_name"] = room_name
    if terrarium_config_version is not None:
        event["terrarium_config_version"] = terrarium_config_version
    return event


def bits_listed_event(bits: list[dict], errors: list[dict]) -> dict:
    return {"event": "bits_listed", "bits": bits, "errors": errors}


def error_event(command: str, message: str) -> dict:
    return {"event": "error", "command": command, "message": message}


def room_loaded_event(name: str) -> dict:
    return {"event": "room_loaded", "name": name}


def room_unloaded_event(name: str) -> dict:
    return {"event": "room_unloaded", "name": name}


def room_load_failed_event(name: str, reason: str) -> dict:
    return {"event": "room_load_failed", "name": name, "reason": reason}


def room_load_progress_event(stage: str) -> dict:
    return {"event": "room_load_progress", "stage": stage}
