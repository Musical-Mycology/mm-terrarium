# mm-devshroom issue draft: handshake protocol, contract v3

Draft for Victor. Not filed. Paste the title and body below into an mm-devshroom issue.

**Title:** Firmware still sends /game/join; mm-terrarium contract v3 retires it (use /game/handshake)

## Body

### Context

mm-terrarium moved the instrument-to-Terrarium contract from v2 to v3. The change
is in the branch `claude/instrument-handshake-protocol-833d7c`; spec:
`docs/superpowers/specs/2026-10-01-instrument-handshake-protocol-design.md` in
mm-terrarium.

What changed on the wire:

- `/game/join` is retired. Terrarium answers it with
  `/<dev>/error ["join", "retired in contract v3: use /game/handshake"]`.
- New down message `/<dev>/handshake s round_id` (invite), new up message
  `/game/handshake sss dev round_id node` (the device's accept), new down message
  `/<dev>/validated ss round_id role`.
- `role`, `deny`, `release`, `room`, `error`, `handshake` and `validated` travel
  over TCP; `leds` and `play` over UDP.
- Terrarium assigns a jam role to a device that hellos but never handshakes.

Current effect on mm-devshroom `origin/main` (cce3dec): the firmware sends
`/game/join`, gets the "retired" `/error`, and never completes a handshake. It
still hellos, so Terrarium gives it a jam role at RUNNING. It cannot be a scored
player and it ignores the invite.

### Firmware checklist (spec section 8)

1. One identity: derive the dev id from the MAC (e.g.
   `ts-<last 6 hex>`, at most 31 chars), and use it for both
   `o2l_set_services` and every hello/handshake arg (replaces the separate
   `DEVICE_ID` and `O2_SERVICE_NAME` constants).
2. Hello (`ssss`, instrument `tuneshroom_rev1`) on link-up and every 5 s.
3. On `/<dev>/handshake`: store the round id, show a prompt (the white
   flash arrives as frames anyway). On a double tap while a round id is
   held: send `/game/handshake [dev, round_id, node]` (node from NFC if
   touched, else empty).
4. On `/<dev>/validated`: clear the prompt. On `/<dev>/deny`: clear it,
   log reason and hint.
5. On `/<dev>/role`: parse the blob; gestures are sent only after a role
   is **received** (not after a send).
6. On `/<dev>/release`: keep the last frame, drop the role, stop gestures.
7. On link loss: drop role and round id; start over on link-up.
8. Never send `/game/join`.

### Scenarios to replay

`docs/device-contract-guide.md` (v3) in mm-terrarium maps each item to the
recorded scenarios that fail without it (section 5.3), and section 7 lists the
must-fail cases a replay runner needs. Scenarios per item:

| Item | Scenarios |
|---|---|
| 1 | `limits`, `link.service_is_dev_id` (contract.json) |
| 2 | `boot_hello_heartbeat`, every scenario |
| 3 | `handshake_validate_then_role`, `handshake_stale_round`, `room_node_handshake_binds` |
| 4 | `handshake_over_cap_deny`, `deny_stays_hellod` |
| 5 | `gestures_after_role`, `late_hello_gets_jam`, `jam_solo_fallback` |
| 6 | `release_keeps_display` |
| 7 | `link_loss_rejoin`, `link_loss_keeps_display` |
| 8 | `join_retired_error` (shows the answer a v2 device gets) |

Also read guide section 4 (the session interface a device exposes), 5.1 (the v3
wire) and 5.2 (device rules and what the recordings check).

### Re-exporting the contract

Run from an mm-terrarium checkout, as a module, through the project venv (the
script form fails with `No module named 'control'`):

```bash
cd /Users/chris/projects/mm-terrarium && .venv/bin/python -m tools.export_contract /Users/chris/projects/mm-devshroom/test/contract
```

The tool does not delete stale files. Delete
`test/contract/scenarios/explicit_join_role.json` and
`test/contract/scenarios/lobby_tap_join.json` by hand (v2 scenarios). Commit the
folder in its own commit naming `_provenance.commit`; never hand-edit it. Guard
checks to port are in guide section 2, and `contract_version` must equal 3.
