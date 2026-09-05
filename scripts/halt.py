"""The kill switch, from the command line.

    python -m scripts.halt          # halt all outbound action
    python -m scripts.halt --resume # resume
    python -m scripts.halt --status # report without changing anything

This and the dashboard's halt button set the same flag through the same
module (policy/killswitch.py), which is also what the policy gate's first
check reads. The rail indicator and the gate can therefore never disagree
about whether outbound action is halted.

`touch KILL_SWITCH` in the repo root does exactly the same thing — the
flag is the file's existence, not its contents. This script only adds who
and when, for the dashboard band.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from policy import killswitch


def main() -> int:
    parser = argparse.ArgumentParser(description="Halt or resume all outbound action.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--resume", action="store_true", help="lift the halt")
    group.add_argument("--status", action="store_true", help="report the current state and exit")
    args = parser.parse_args()

    if args.status:
        status = killswitch.read_status()
        if status.engaged:
            actor = status.actor or "unknown"
            print(f"HALTED — engaged {status.engaged_at_display} by {actor}")
            print(f"Flag file: {killswitch.KILL_SWITCH_PATH}")
            return 1
        print("RUNNING — outbound action is allowed")
        return 0

    if args.resume:
        if killswitch.disengage():
            print("Outbound action resumed. Cases pick up on the next tick; nothing was rolled back.")
        else:
            print("Already running — no kill switch was engaged.")
        return 0

    status = killswitch.engage(actor=f"cli:{getpass.getuser()}")
    print(
        f"HALTED at {status.engaged_at_display}.\n"
        f"Every proposed action is now vetoed with KILL_SWITCH_ENGAGED, in every lane.\n"
        f"Nothing is deleted or rolled back. Resume with: python -m scripts.halt --resume",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
