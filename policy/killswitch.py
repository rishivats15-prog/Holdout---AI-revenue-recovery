"""The kill switch — one flag, read by everything that can send.

There is exactly one source of truth: the presence of the `KILL_SWITCH`
file in the repo root. The CLI (`make halt` / `touch KILL_SWITCH`), the
dashboard's halt button, and the policy gate's first check all read this
same path through this same module, so the rail indicator and the gate can
never disagree about whether outbound action is halted.

The file's *contents* are optional metadata — who engaged it and when, so
the dashboard band can say so. A bare `touch KILL_SWITCH` with no contents
is still a fully valid engaged switch, which is what makes the one-command
instruction on page one of the RUNBOOK true.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KILL_SWITCH_PATH = REPO_ROOT / "KILL_SWITCH"


@dataclass(frozen=True)
class KillSwitchStatus:
    engaged: bool
    engaged_at: dt.datetime | None = None
    actor: str | None = None

    @property
    def engaged_at_display(self) -> str:
        return self.engaged_at.strftime("%Y-%m-%d %H:%M") if self.engaged_at else "unknown"


def is_engaged(path: Path | None = None) -> bool:
    """The single predicate. policy/checks.py's kill-switch check calls
    this, so the gate and the dashboard answer the same question the same
    way."""
    return (path or KILL_SWITCH_PATH).exists()


def read_status(path: Path | None = None) -> KillSwitchStatus:
    path = path or KILL_SWITCH_PATH
    if not path.exists():
        return KillSwitchStatus(engaged=False)

    try:
        raw = json.loads(path.read_text())
        engaged_at = dt.datetime.fromisoformat(raw["engaged_at"])
        return KillSwitchStatus(engaged=True, engaged_at=engaged_at, actor=raw.get("actor"))
    except (ValueError, KeyError, OSError):
        # A bare `touch KILL_SWITCH`, or a file someone hand-edited. Still
        # engaged — the flag is the file's existence, never its contents.
        return KillSwitchStatus(engaged=True)


def engage(actor: str, path: Path | None = None) -> KillSwitchStatus:
    path = path or KILL_SWITCH_PATH
    engaged_at = dt.datetime.now().replace(microsecond=0)
    path.write_text(json.dumps({"engaged_at": engaged_at.isoformat(), "actor": actor}, indent=2))
    return KillSwitchStatus(engaged=True, engaged_at=engaged_at, actor=actor)


def disengage(path: Path | None = None) -> bool:
    """Returns whether anything was actually engaged. Resuming an already
    -resumed system is not an error."""
    path = path or KILL_SWITCH_PATH
    if not path.exists():
        return False
    path.unlink()
    return True
