import pytest

from minemydinner.agent.records import PlanningRecorder, load_run
from tests.planningfakes import rollback_session_factory


@pytest.fixture
def recorder(session) -> PlanningRecorder:
    return PlanningRecorder(rollback_session_factory(session))


def test_recorder_appends_events_in_order(session, recorder):
    run = recorder.start("t", {}, [], "plan_v1", "abc", False)
    recorder.add_event(run, {"kind": "tool_call"})
    recorder.add_event(run, {"kind": "validation"})
    assert [e["kind"] for e in load_run(session, run).events] == ["tool_call", "validation"]


def test_start_defaults_and_finish(session, recorder):
    run = recorder.start("t2", {"headcount": 2}, ["servings"], "plan_v1", "abc", True)
    row = load_run(session, run)
    assert row.events == [] and row.retries == 0 and row.has_temporary_guests is True
    assert row.first_passed is None and row.final_plan is None
    recorder.finish(run, 1, False, True, {"dishes": []})
    session.expire_all()
    row = load_run(session, run)
    assert (row.retries, row.first_passed, row.final_passed) == (1, False, True)
    assert row.final_plan == {"dishes": []}
