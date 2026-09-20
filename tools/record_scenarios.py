"""Re-record every device contract kit scenario and overwrite the committed
contract_kit/recordings/<name>.json files (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md sections 4.3 and 5.4).

    .venv/bin/python -m tools.record_scenarios

Running this twice in a row leaves the working tree clean: the recorder
reads no clock, no commit and no random number, so a scenario that has not
changed re-records byte for byte. When a diff DOES appear, it is Control's
real behavior having changed, and every device repo replaying the export
will see the same change; review it before committing.
"""
from __future__ import annotations

import json
from pathlib import Path

from contract_kit.scenarios import ALL_SCENARIOS

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "contract_kit" / "recordings"


def main(argv: list[str] | None = None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for scenario_fn in ALL_SCENARIOS:
        data = scenario_fn()
        out = OUT_DIR / f"{data['name']}.json"
        out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"wrote {out.relative_to(REPO_ROOT)} "
              f"({len(data['steps'])} steps)")


if __name__ == "__main__":
    main()
