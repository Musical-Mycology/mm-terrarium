"""Wire message schemas for the Terrarium uplink -- the JSON-serializable
contract between UplinkAgent and a future fairyring broker. See design spec
section 4.
"""

from dataclasses import dataclass


# --- Down: fairyring -> Terrarium, one dataclass per command ---------------

@dataclass
class LoadBitCommand:
    name: str
    overrides: dict | None = None


@dataclass
class RunCommand:
    pass


@dataclass
class AbortCommand:
    pass


@dataclass
class ListBitsCommand:
    pass


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
        return LoadBitCommand(name=name, overrides=overrides)
    if command == "run":
        return RunCommand()
    if command == "abort":
        return AbortCommand()
    if command == "list_bits":
        return ListBitsCommand()
    raise ValueError(f"unrecognized command: {command!r}")


# --- Up: Terrarium -> fairyring, plain dict builders ------------------------
# (terminal messages -- only ever produced here, never parsed back on this
# side, so a builder function is enough; no dataclass round-trip needed.)

def state_changed_event(state_name: str) -> dict:
    return {"event": "state_changed", "state": state_name}


def registration_changed_event(counts: list[tuple[str, int, int | None]]) -> dict:
    return {
        "event": "registration_changed",
        "roles": [
            {"role": name, "count": count, "capacity": capacity}
            for name, count, capacity in counts
        ],
    }


def bit_completed_event(result: dict, bit_name: str = "",
                        bit_version: str = "") -> dict:
    return {
        "event": "bit_completed",
        "result": result,
        "bit": {"name": bit_name, "version": bit_version},
    }


def bits_listed_event(bits: list[dict], errors: list[dict]) -> dict:
    return {"event": "bits_listed", "bits": bits, "errors": errors}


def error_event(command: str, message: str) -> dict:
    return {"event": "error", "command": command, "message": message}
