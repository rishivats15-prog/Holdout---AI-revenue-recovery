"""FastAPI app and routes.

Every page is server-rendered. Filtering and sorting are query params, not
client JavaScript, so each view is a plain linkable URL that renders with
scripting disabled. The only JavaScript on any page is the count-up, the
flow-diagram reveal, and the kill switch's typed confirmation — and the
kill switch is validated on the server regardless.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from dashboard import chrome, live, queries, viz
from dashboard.replay import build_replay
from db.hashchain import verify_case_chain
from db.models import Case
from db.session import get_db, init_db
from decide.ladder import load_playbook
from diagnosis.rules import load_rule_table
from eval.money import format_paise, format_rate
from eval.report import generate_batch_report, load_veto_reason_counts
from policy import killswitch, reasons
from policy.gate import CHECKS, POLICY_VERSION

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Revenue Recovery", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Money is stored in paise everywhere and formatted exactly once — here,
# through the same function the CLI batch report uses, so a rupee figure
# can never read differently on the dashboard than in the report.
templates.env.filters["paise"] = format_paise
templates.env.filters["paise_exact"] = lambda value: format_paise(value, paise_precision=True)
templates.env.filters["rate"] = format_rate
templates.env.filters["clock"] = lambda value: value.strftime("%d %b · %H:%M") if value else "—"
templates.env.filters["short_hash"] = lambda value: (value or "")[:12]


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "outbound_halted": killswitch.is_engaged(), "policy_version": POLICY_VERSION}


# ------------------------------------------------------------ scoreboard


@app.get("/", response_class=HTMLResponse)
def scoreboard(request: Request, db: Session = Depends(get_db)):
    report = generate_batch_report(db)
    context = chrome.base_context(
        db, active_href="/", page_title="Scoreboard", status_items=chrome.scoreboard_status(report)
    )
    context.update(
        request=request,
        report=report,
        flow=viz.build_flow_diagram(report),
        harm_bars=viz.build_harm_bars(report),
        interval=viz.build_interval_plot(report),
    )
    return templates.TemplateResponse(request=request, name="scoreboard.html", context=context)


# ----------------------------------------------------------- case queue


@app.get("/cases", response_class=HTMLResponse)
def case_queue(
    request: Request,
    state: str | None = None,
    cause: str | None = None,
    arm: str | None = None,
    sort: str = "exposure",
    offset: int = 0,
    db: Session = Depends(get_db),
):
    page = queries.case_queue(db, state=state, cause=cause, arm=arm, sort=sort, offset=max(offset, 0))
    applied = next((value for value in (state, cause, arm) if value), None)
    context = chrome.base_context(
        db, active_href="/cases", page_title="Cases",
        status_items=chrome.queue_status(page.shown, page.total, applied),
    )
    context.update(
        request=request,
        page=page,
        states=queries.distinct_states(db),
        causes=queries.distinct_causes(db),
        filters={"state": state, "cause": cause, "arm": arm, "sort": sort},
        sorts=[(key, label) for key, (_, label) in queries.CASE_SORTS.items()],
    )
    return templates.TemplateResponse(request=request, name="cases.html", context=context)


# ---------------------------------------------------------- case replay


@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_replay(request: Request, case_id: int, db: Session = Depends(get_db)):
    case = db.get(Case, case_id)
    if case is None:
        context = chrome.base_context(
            db, active_href="/cases", page_title="Case not found",
            status_items=[chrome.StatusItem(f"CASE / {case_id:04d}"), chrome.StatusItem("NOT FOUND")],
        )
        context.update(request=request, case_id=case_id)
        return templates.TemplateResponse(request=request, name="case_missing.html", context=context, status_code=404)

    replay = build_replay(db, case)
    rung_label = f"{case.ladder_rung}/{replay.ladder_length}" if replay.ladder_length else str(case.ladder_rung)
    context = chrome.base_context(
        db, active_href="/cases", page_title=f"Case {case.id}",
        status_items=chrome.case_status(case, rung_label, replay.playbook_version),
    )
    context.update(
        request=request,
        replay=replay,
        case=case,
        chain=verify_case_chain(db, case.id),
        rung_label=rung_label,
    )
    return templates.TemplateResponse(request=request, name="case_replay.html", context=context)


# -------------------------------------------------------------- vetoes


@app.get("/vetoes", response_class=HTMLResponse)
def veto_log(request: Request, code: str | None = None, offset: int = 0, db: Session = Depends(get_db)):
    counts = load_veto_reason_counts(db)
    page = queries.veto_log(db, reason_code=code, offset=max(offset, 0))
    context = chrome.base_context(
        db, active_href="/vetoes", page_title="Veto log",
        status_items=chrome.veto_status(sum(count for _, count in counts), len(counts), code),
    )
    context.update(
        request=request,
        page=page,
        counts=counts,
        active_code=code,
        distribution=viz.build_veto_distribution(counts),
        bounds=_bound_coverage(counts),
    )
    return templates.TemplateResponse(request=request, name="vetoes.html", context=context)


def _bound_coverage(counts: tuple[tuple[str, int], ...]) -> list[dict]:
    """Every bound in the gate with the number of vetoes it produced in
    this batch — zeros included. A veto log that only lists the codes that
    fired invites the question of whether the rest are wired at all; this
    answers it on the page rather than in the test suite."""
    by_code = dict(counts)
    return [
        {
            "check": name,
            "label": reasons.CHECK_LABELS.get(name, name),
            "bound": reasons.CHECK_BOUNDS.get(name, ""),
            "codes": reasons.CHECK_REASONS.get(name, ()),
            "fired": sum(by_code.get(code, 0) for code in reasons.CHECK_REASONS.get(name, ())),
        }
        for name, _ in CHECKS
    ]


# ------------------------------------------------------------ playbooks


@app.get("/playbooks", response_class=HTMLResponse)
def playbooks(request: Request, lane: str = "subscription", db: Session = Depends(get_db)):
    playbook = load_playbook(lane)
    rules = load_rule_table(lane)
    context = chrome.base_context(
        db, active_href="/playbooks", page_title="Playbooks",
        status_items=[
            chrome.StatusItem(f"LANE {lane.upper()}"),
            chrome.StatusItem(f"PLAYBOOK v{playbook.version}"),
            chrome.StatusItem(f"LADDERS {len(playbook.ladders)}"),
            chrome.StatusItem(f"POLICY v{POLICY_VERSION}"),
        ],
    )
    context.update(
        request=request,
        playbook=playbook,
        rules=rules,
        checks=[name for name, _ in CHECKS],
    )
    return templates.TemplateResponse(request=request, name="playbooks.html", context=context)


# ------------------------------------------------------------- live run

RUN_ERRORS = {
    "no-run": "There is no run to advance. Generate a batch to start one.",
    "no-batch": "The run was tracking a batch that no longer exists, so it was closed. Generate a new one.",
}


@app.get("/run", response_class=HTMLResponse)
def run_page(request: Request, error: str | None = None, db: Session = Depends(get_db)):
    snapshot = live.snapshot(db)
    session = snapshot.session
    context = chrome.base_context(
        db, active_href="/run", page_title="Live run",
        status_items=chrome.run_status(session, snapshot.total_cases),
    )
    context.update(
        request=request,
        snapshot=snapshot,
        session=session,
        progression=viz.build_run_progression(
            session.history, session.total_ticks, total_cases=snapshot.total_cases
        ) if session else None,
        recent=list(reversed(session.history[-8:])) if session else [],
        error=RUN_ERRORS.get(error or ""),
    )
    return templates.TemplateResponse(request=request, name="run.html", context=context)


@app.post("/run/new")
def run_new_batch(db: Session = Depends(get_db)):
    """Wipes and regenerates. Not destructive in any meaningful sense —
    the generator is seeded, so the same batch comes back — but it does
    discard whatever run was in progress."""
    with live.run_lock:
        live.start_new_batch(db)
    return RedirectResponse(url="/run", status_code=303)


@app.post("/run/tick")
def run_advance(days: int = Form(default=1), db: Session = Depends(get_db)):
    with live.run_lock:
        session = live.current_session()
        if session is None:
            return RedirectResponse(url="/run?error=no-run", status_code=303)
        # The batch can be gone even though a session is tracked — someone
        # regenerated in another tab, or the database was cleared. Ticking
        # into nothing corrupts the run's day count for no benefit.
        if db.query(Case).count() == 0:
            live.clear_session()
            return RedirectResponse(url="/run?error=no-batch", status_code=303)
        session.advance_days(db, max(1, min(days, session.total_ticks)))
    return RedirectResponse(url="/run", status_code=303)


@app.post("/run/reset")
def run_reset(db: Session = Depends(get_db)):
    with live.run_lock:
        live.reset_database(db)
        live.clear_session()
    return RedirectResponse(url="/run", status_code=303)


# ----------------------------------------------------------- kill switch


@app.post("/halt")
def halt(confirm: str = Form(default="")):
    """Requires the literal word HALT. The dashboard disables the button
    until it's typed, but the check that matters is this one — a post
    without it changes nothing, scripting or no scripting."""
    if confirm.strip().upper() != "HALT":
        return RedirectResponse(url="/?halt=unconfirmed", status_code=303)
    killswitch.engage(actor="dashboard")
    return RedirectResponse(url="/", status_code=303)


@app.post("/resume")
def resume():
    killswitch.disengage()
    return RedirectResponse(url="/", status_code=303)
