"""Route tests for the dashboard.

Two things beyond "does it return 200": that each page renders the
substance it exists to show (the incremental figure, a veto's reason code,
the hash-chain verdict), and that nothing on any page depends on
JavaScript — filters are links, the kill switch is validated on the
server, and the flow diagram is complete in the markup.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from dashboard import live
from dashboard.main import app
from db.enums import CaseState, ExperimentArm, PolicyVerdict
from db.hashchain import append_case_event
from db.models import Action, Base, Case, Customer, Diagnosis, ExperimentAssignment, Outcome
from db.session import get_db
from policy import killswitch

NOON = dt.datetime(2026, 1, 5, 12, 0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A client backed by its own in-memory database, with the kill switch
    pointed at a temp path so a test can never halt the real repo.

    Not the shared db_session fixture: TestClient runs sync endpoints on a
    worker thread, and SQLite refuses a connection created on another one.
    StaticPool plus check_same_thread=False keeps one connection shared
    across both threads, which is what an in-memory database needs anyway
    — a second connection would see an empty schema.
    """
    monkeypatch.setattr(killswitch, "KILL_SWITCH_PATH", tmp_path / "KILL_SWITCH")
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    _seed(session)

    # /run holds its session in module state; a leftover one would leak
    # between tests.
    live.clear_session()
    monkeypatch.setattr(live, "DEFAULT_BATCH_SIZE", 12)

    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        live.clear_session()
        session.close()
        engine.dispose()


def _seed(db) -> None:
    """A miniature batch: enough treated and holdout cases for a real
    report, one recovery, one veto, one promise-to-pay."""
    for index in range(24):
        customer = Customer(
            external_ref=f"cust_{index:03d}", name=f"Customer {index}",
            dnd=(index == 3), whatsapp_opt_in=(index % 2 == 0),
        )
        db.add(customer)
        db.flush()

        arm = ExperimentArm.HOLDOUT.value if index % 5 == 0 else ExperimentArm.TREATMENT.value
        recovered = index % 3 == 0
        case = Case(
            customer_id=customer.id, lane="subscription",
            state=CaseState.RECOVERED.value if recovered else CaseState.IN_TREATMENT.value,
            exposure_amount=500_000 + index * 1_000, detected_at=NOON, ladder_rung=1,
            true_root_cause="balance_timing",
        )
        db.add(case)
        db.flush()

        db.add(Diagnosis(
            case_id=case.id, cause="balance_timing", confidence=1.0, method="rule",
            evidence={"failure_code": "INSUFFICIENT_FUNDS", "retry_viable": "yes", "rule_description": "Balance timing."},
        ))
        db.add(ExperimentAssignment(case_id=case.id, arm=arm, stratum="balance_timing:mid", seed=index))
        append_case_event(db, case.id, "case_detected", {"signal_id": index, "source": "payment_webhook"})
        append_case_event(db, case.id, "case_diagnosed", {"cause": "balance_timing", "method": "rule", "confidence": 1.0})

        if arm == ExperimentArm.TREATMENT.value:
            append_case_event(db, case.id, "action_proposed",
                              {"ladder_rung": 0, "action_type": "sms_pre_debit_notice", "channel": "sms", "cause": "balance_timing"})
            vetoed = index == 3
            db.add(Action(
                case_id=case.id, ladder_rung=0, proposed_action="sms_pre_debit_notice", channel="sms",
                policy_verdict=PolicyVerdict.VETOED.value if vetoed else PolicyVerdict.ALLOWED.value,
                veto_reason_code="CUSTOMER_DND" if vetoed else None,
                executed_action=None if vetoed else "sms_pre_debit_notice",
                cost=0 if vetoed else 20, delivered=None if vetoed else True,
                idempotency_key=f"case:{case.id}:rung:0",
            ))
            db.flush()
            if vetoed:
                append_case_event(db, case.id, "action_vetoed", {
                    "action_type": "sms_pre_debit_notice", "channel": "sms", "reason_code": "CUSTOMER_DND",
                    "checks_evaluated": ["kill_switch", "circuit_breaker", "action_allowlist", "quiet_hours",
                                         "frequency_caps", "consent_and_eligibility"],
                })
            else:
                append_case_event(db, case.id, "action_executed", {
                    "action_type": "sms_pre_debit_notice", "channel": "sms",
                    "external_id": f"ext_{case.id}", "delivery_success": True,
                })

        if recovered:
            db.add(Outcome(case_id=case.id, outcome_type="payment", amount=case.exposure_amount,
                           occurred_at=NOON + dt.timedelta(days=5)))
            append_case_event(db, case.id, "case_recovered", {"arm": arm, "recovery_day": 5, "prior_state": "in_treatment"})
    db.commit()


# --- every page --------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/run", "/cases", "/cases/1", "/vetoes", "/playbooks"])
def test_every_page_renders(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "<html" in response.text


@pytest.mark.parametrize("path", ["/", "/run", "/cases", "/cases/1", "/vetoes", "/playbooks"])
def test_the_synthetic_marker_is_on_every_page(client, path):
    """A judge should never be more than a glance from knowing this data
    is synthetic and that its ground truth is known."""
    assert "SYNTHETIC · GROUND TRUTH KNOWN" in client.get(path).text


@pytest.mark.parametrize("path", ["/", "/run", "/cases", "/cases/1", "/vetoes", "/playbooks"])
def test_the_status_bar_carries_the_policy_version_everywhere(client, path):
    assert "POLICY v" in client.get(path).text


def test_the_rail_marks_the_current_page(client):
    assert 'aria-current="page"' in client.get("/vetoes").text


# --- scoreboard --------------------------------------------------------


def test_the_scoreboard_leads_with_the_incremental_figure_and_its_interval(client):
    body = client.get("/").text
    assert "Incremental recovered" in body
    assert "95% CI" in body
    assert "True simulated effect" in body


def test_the_scoreboard_shows_the_ground_truth_verdict(client):
    body = client.get("/").text
    assert "verdict-stamp" in body
    assert ("PASS" in body) or ("FAIL" in body)


def test_the_flow_diagram_is_complete_in_the_markup(client):
    """The reveal is an enhancement, not a dependency: with scripting off,
    the SVG must already contain every ribbon and node."""
    body = client.get("/").text
    assert body.count("flow-ribbon flow-ribbon--") == 7
    assert "flow-callout" in body
    assert "is-animated" not in body  # only JavaScript adds that


def test_harm_metrics_appear_beside_the_money(client):
    body = client.get("/").text
    assert "Opt-out rate" in body
    assert "Complaint rate" in body
    assert "structural, not measured" in body


def test_money_uses_indian_digit_grouping(client):
    """Total exposure here is over a lakh, which is exactly where Indian
    grouping diverges from Western: ₹1,23,000, never ₹123,000."""
    body = client.get("/").text
    import re

    lakh_figures = re.findall(r"₹\d{1,2},\d{2},\d{3}", body)
    assert lakh_figures, "no lakh-scale rupee figure found to check grouping against"
    assert not re.search(r"₹\d{3},\d{3}", body)


# --- case queue and replay ---------------------------------------------


def test_the_case_queue_filters_server_side_by_query_param(client):
    all_cases = client.get("/cases").text
    holdout_only = client.get("/cases?arm=holdout").text
    assert all_cases.count('href="/cases/') > holdout_only.count('href="/cases/')


def test_an_impossible_filter_gives_directions_not_a_blank_table(client):
    body = client.get("/cases?state=written_off").text
    assert "No case matches" in body
    assert "Clear all filters" in body


def test_a_missing_case_returns_404_with_a_route_out(client):
    response = client.get("/cases/999999")
    assert response.status_code == 404
    assert "Browse the case queue" in response.text


def test_the_replay_renders_the_ledger_as_the_timeline(client):
    body = client.get("/cases/1").text
    assert "Case opened" in body
    assert "case_diagnosed" in body  # the hash-chain listing
    assert "Hash chain" in body
    assert "VERIFIED" in body


def test_a_vetoed_action_renders_as_a_gate_with_its_reason_code(client):
    """Case 4 is the DND customer. The veto must be legible without a
    legend: a blocked-by-policy gate, the uppercase code, and the rules
    that were evaluated before it fired."""
    body = client.get("/cases/4").text
    assert "blocked at the policy gate" in body
    assert "CUSTOMER_DND" in body
    assert "Blocked by policy" in body
    assert "Rules evaluated" in body
    assert "node--veto" in body


def test_an_allowed_action_renders_as_passing_through(client):
    body = client.get("/cases/2").text
    assert "Passed the gate" in body
    assert "node--veto" not in body


def test_action_provenance_is_never_ambiguous(client):
    assert "rule · playbook rung" in client.get("/cases/2").text


# --- veto log ----------------------------------------------------------


def test_the_veto_log_lists_blocked_actions_with_their_codes(client):
    body = client.get("/vetoes").text
    assert "CUSTOMER_DND" in body
    assert "Blocked actions" in body


def test_the_veto_log_shows_every_bound_including_ones_that_never_fired(client):
    """A log of only the codes that fired invites the question of whether
    the other bounds exist at all. The page answers it."""
    body = client.get("/vetoes").text
    assert "Every bound in the gate" in body
    for label in ("Quiet hours", "Cost ceiling", "Expected-value floor", "Content rules"):
        assert label in body


def test_the_veto_log_filters_by_reason_code(client):
    filtered = client.get("/vetoes?code=CUSTOMER_DND")
    assert filtered.status_code == 200
    assert 'aria-pressed="true"' in filtered.text


def test_a_code_that_never_fired_explains_itself(client):
    body = client.get("/vetoes?code=OUTSIDE_QUIET_HOURS").text
    assert "never fired here" in body or "never met their conditions" in body
    assert "Clear the filter" in body


# --- kill switch --------------------------------------------------------


def test_halting_requires_the_typed_word(client):
    """The button is disabled by script until HALT is typed, but this is
    the check that actually matters."""
    response = client.post("/halt", data={"confirm": "yes please"}, follow_redirects=False)
    assert response.status_code == 303
    assert killswitch.is_engaged() is False


def test_halting_with_the_word_engages_the_switch_and_bands_every_page(client):
    client.post("/halt", data={"confirm": "HALT"}, follow_redirects=False)
    assert killswitch.is_engaged() is True

    body = client.get("/").text
    assert "Outbound halted" in body
    assert "KILL_SWITCH_ENGAGED" in body
    assert "HALTED" in body  # the rail indicator agrees with the band

    client.post("/resume", follow_redirects=False)
    assert killswitch.is_engaged() is False


def test_healthz_reports_the_same_halt_state_as_the_pages(client):
    assert client.get("/healthz").json()["outbound_halted"] is False
    client.post("/halt", data={"confirm": "HALT"}, follow_redirects=False)
    assert client.get("/healthz").json()["outbound_halted"] is True
    client.post("/resume", follow_redirects=False)


# --- live run page ------------------------------------------------------


def test_a_batch_with_no_live_run_says_so_rather_than_claiming_nothing_started(client):
    """`make seed` runs all 21 days in its own process, so data can exist
    with no session to step. The page has to explain that rather than
    look broken."""
    body = client.get("/run").text
    assert "NO LIVE RUN" in body
    assert "not being stepped here" in body
    assert "New batch" in body
    assert "Advance one day" not in body


def test_an_empty_database_offers_to_generate_a_batch(client):
    client.post("/run/reset", follow_redirects=False)
    body = client.get("/run").text
    assert "Generate a batch" in body
    assert "RUN NOT STARTED" in body


def test_generating_a_batch_replaces_the_data_and_starts_at_day_zero(client):
    client.post("/run/new", follow_redirects=False)
    body = client.get("/run").text

    assert "DAY 0 / 21" in body
    assert "Advance one day" in body
    session = live.current_session()
    assert session is not None and session.tick_number == 0


def test_advancing_moves_the_run_forward_a_day_at_a_time(client):
    client.post("/run/new", follow_redirects=False)
    client.post("/run/tick", data={"days": 1}, follow_redirects=False)
    assert live.current_session().tick_number == 1

    client.post("/run/tick", data={"days": 5}, follow_redirects=False)
    assert live.current_session().tick_number == 6
    assert "DAY 6 / 21" in client.get("/run").text


def test_advancing_without_a_run_redirects_instead_of_erroring(client):
    response = client.post("/run/tick", data={"days": 1}, follow_redirects=False)
    assert response.status_code == 303
    assert live.current_session() is None


def test_clearing_empties_the_batch_and_the_session(client):
    client.post("/run/new", follow_redirects=False)
    client.post("/run/reset", follow_redirects=False)

    assert live.current_session() is None
    assert "Generate a batch" in client.get("/run").text


def test_the_run_page_needs_no_javascript_to_advance(client):
    """Every control is a form post, so the page works with scripting off
    — which also means it cannot break on camera."""
    client.post("/run/new", follow_redirects=False)
    body = client.get("/run").text
    assert 'action="/run/tick"' in body
    assert "onclick" not in body


def test_the_charts_appear_once_a_day_has_run(client):
    client.post("/run/new", follow_redirects=False)
    assert "Nothing has run yet" in client.get("/run").text

    client.post("/run/tick", data={"days": 2}, follow_redirects=False)
    body = client.get("/run").text
    assert "Cases recovered" in body
    assert "Actions attempted" in body


# --- no-JavaScript ------------------------------------------------------


def test_filters_and_pagination_are_plain_links(client):
    """Server-side filtering means every view is a linkable URL. If these
    ever became buttons wired to script, the pages would stop working
    with JavaScript disabled."""
    body = client.get("/vetoes").text
    assert 'href="/vetoes?code=' in body
    assert "onclick" not in body


# --- run-page safety ----------------------------------------------------


def test_ticking_a_run_whose_batch_vanished_closes_it_instead_of_writing_into_nothing(client):
    """The session is module state and the batch is in the database; they
    can get out of step (another tab, a clear). Advancing then would tick
    the day counter forward against no cases at all."""
    client.post("/run/new", follow_redirects=False)
    assert live.current_session() is not None

    live.reset_database(next(iter(app.dependency_overrides.values()))())
    response = client.post("/run/tick", data={"days": 1}, follow_redirects=False)

    assert response.status_code == 303
    assert "error=no-batch" in response.headers["location"]
    assert live.current_session() is None


def test_the_run_page_explains_a_failed_advance(client):
    body = client.get("/run?error=no-batch").text
    assert "no longer exists" in body


def test_clearing_takes_two_deliberate_clicks(client):
    """A one-click wipe next to 'Advance one day' is a hazard when someone
    is clicking through the run. The disclosure is a plain <details>, so
    it still works with scripting off."""
    client.post("/run/new", follow_redirects=False)
    body = client.get("/run").text
    assert "<details class=\"confirm\">" in body
    assert "Clear everything" in body


def test_run_mutations_are_serialized(client):
    """Uvicorn runs sync endpoints on a threadpool, so two overlapping
    posts would otherwise advance one shared session concurrently."""
    import threading

    client.post("/run/new", follow_redirects=False)
    start = threading.Barrier(4)

    def advance():
        start.wait()
        client.post("/run/tick", data={"days": 1}, follow_redirects=False)

    threads = [threading.Thread(target=advance) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    session = live.current_session()
    assert session.tick_number == 4
    assert len(session.history) == 4
    assert [r.tick_number for r in session.history] == [0, 1, 2, 3]
