"""Loads and validates a lane's playbook YAML — the ordered treatment
ladder per root cause. Pure data access: nothing here branches on a cause
by name. Adding a lane means writing playbooks/<lane>.yaml; this module
doesn't change.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from diagnosis.rules import load_rule_table

PLAYBOOKS_DIR = Path(__file__).resolve().parent.parent / "playbooks"


@dataclass(frozen=True)
class Rung:
    rung: int
    action_type: str
    channel: str | None
    terminal_state: str | None
    cooldown_minutes: int
    cost_paise: int
    copy_template: str | None
    waiver_offer_paise: int


@dataclass(frozen=True)
class Playbook:
    lane: str
    name: str
    version: str
    source_path: str
    raw_content: dict
    ladders: dict[str, tuple[Rung, ...]]
    promise_to_pay_grace_days: int


@lru_cache(maxsize=None)
def load_playbook(lane: str) -> Playbook:
    path = PLAYBOOKS_DIR / f"{lane}.yaml"
    raw = yaml.safe_load(path.read_text())

    ladders: dict[str, tuple[Rung, ...]] = {}
    for cause, rung_specs in raw["ladders"].items():
        ladders[cause] = _parse_ladder(path, cause, rung_specs)

    diagnosable_causes = load_rule_table(lane).causes
    laddered_causes = set(ladders)
    if diagnosable_causes != laddered_causes:
        missing = diagnosable_causes - laddered_causes
        orphaned = laddered_causes - diagnosable_causes
        raise ValueError(
            f"{path}: playbook causes must exactly match diagnosis causes for lane "
            f"{lane!r} — missing ladders: {missing or 'none'}, orphaned ladders: {orphaned or 'none'}"
        )

    return Playbook(
        lane=raw["lane"],
        name=raw["name"],
        version=str(raw["version"]),
        source_path=str(path),
        raw_content=raw,
        ladders=ladders,
        promise_to_pay_grace_days=raw["promise_to_pay"]["grace_days"],
    )


def _parse_ladder(path: Path, cause: str, rung_specs: list[dict]) -> tuple[Rung, ...]:
    rungs = []
    for expected_index, spec in enumerate(rung_specs):
        if spec["rung"] != expected_index:
            raise ValueError(
                f"{path}: cause {cause!r} rungs must be sequential from 0, "
                f"found {spec['rung']} at position {expected_index}"
            )
        has_channel = spec.get("channel") is not None
        has_terminal_state = spec.get("terminal_state") is not None
        if has_channel == has_terminal_state:
            raise ValueError(
                f"{path}: cause {cause!r} rung {spec['rung']} must set exactly one of "
                "channel or terminal_state"
            )
        rungs.append(
            Rung(
                rung=spec["rung"],
                action_type=spec["action_type"],
                channel=spec.get("channel"),
                terminal_state=spec.get("terminal_state"),
                cooldown_minutes=spec["cooldown_minutes"],
                cost_paise=spec["cost_paise"],
                copy_template=spec.get("copy_template"),
                waiver_offer_paise=spec.get("waiver_offer_paise", 0),
            )
        )
    return tuple(rungs)


def next_rung(lane: str, cause: str, ladder_rung: int) -> Rung | None:
    """The rung at `ladder_rung` for this cause, or None if the ladder is
    exhausted — the caller's cue to write the case off or hand it to a
    human rather than propose anything further."""
    ladder = load_playbook(lane).ladders[cause]
    if ladder_rung >= len(ladder):
        return None
    return ladder[ladder_rung]
