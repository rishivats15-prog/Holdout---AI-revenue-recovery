"""policy/killswitch.py — one flag, read identically by the CLI, the
dashboard, and the policy gate.

The property that matters most is the last test: the gate and the UI must
never disagree about whether outbound action is halted.
"""

from __future__ import annotations

import json

from policy import checks, killswitch, reasons


def test_a_missing_file_means_running(tmp_path):
    assert killswitch.is_engaged(tmp_path / "KILL_SWITCH") is False
    assert killswitch.read_status(tmp_path / "KILL_SWITCH").engaged is False


def test_engage_writes_the_flag_with_actor_and_time(tmp_path):
    path = tmp_path / "KILL_SWITCH"
    status = killswitch.engage(actor="cli:tester", path=path)

    assert path.exists()
    assert status.engaged is True
    assert status.actor == "cli:tester"
    assert json.loads(path.read_text())["actor"] == "cli:tester"


def test_a_bare_touch_is_a_fully_valid_engaged_switch(tmp_path):
    """The RUNBOOK's one-command instruction is `touch KILL_SWITCH`. The
    flag is the file's existence; its contents are optional metadata."""
    path = tmp_path / "KILL_SWITCH"
    path.write_text("")

    status = killswitch.read_status(path)

    assert status.engaged is True
    assert status.actor is None
    assert status.engaged_at_display == "unknown"


def test_a_hand_edited_flag_still_reads_as_engaged(tmp_path):
    path = tmp_path / "KILL_SWITCH"
    path.write_text("someone typed a note in here")
    assert killswitch.read_status(path).engaged is True


def test_disengage_removes_the_flag_and_reports_whether_it_did(tmp_path):
    path = tmp_path / "KILL_SWITCH"
    killswitch.engage(actor="cli:tester", path=path)

    assert killswitch.disengage(path) is True
    assert not path.exists()
    assert killswitch.disengage(path) is False  # resuming twice is not an error


def test_the_policy_gate_reads_the_same_flag_the_cli_sets(tmp_path, monkeypatch):
    """The one that must never break: engage through the CLI's module and
    the gate's first check has to veto, because both resolve the same
    path through the same predicate."""
    path = tmp_path / "KILL_SWITCH"
    monkeypatch.setattr(checks, "KILL_SWITCH_PATH", path)

    assert checks.check_kill_switch(None) is None

    killswitch.engage(actor="cli:tester", path=path)
    assert checks.check_kill_switch(None) == reasons.KILL_SWITCH_ENGAGED
    assert killswitch.read_status(path).engaged is True

    killswitch.disengage(path)
    assert checks.check_kill_switch(None) is None
    assert killswitch.read_status(path).engaged is False
