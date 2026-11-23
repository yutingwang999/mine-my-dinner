import json

from minemydinner.evaluation.tool_call_cut import is_cut, main, pick_calls, renamed, scan

CUT_ARGS = '{"choices": [], "slots": [{"index": 0, "recipe_id": 779}], "wish_met"'


def _event(response: dict, human: str = "Slots: ...") -> dict:
    return {
        "kind": "model_call",
        "step": "pick",
        "messages": [{"type": "system", "content": "prompt"}, {"type": "human", "content": human}],
        "response": response,
    }


def _run_folder(tmp_path, events: list[dict]):
    folder = tmp_path / "20261123T000000Z-revise"
    folder.mkdir()
    record = {"name": "add_a_soup", "planning_runs": [{"events": events}]}
    (folder / "results.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    return folder


def test_scan_counts_cut_answers_and_empty_replies(tmp_path):
    folder = _run_folder(
        tmp_path,
        [
            _event({"tool_calls": [], "invalid_tool_calls": [{"name": "PickerAnswer", "args": CUT_ARGS}]}),
            _event({"content": "", "tool_calls": []}),
            _event({"tool_calls": [{"name": "PickerAnswer", "args": {}}]}),
            {"kind": "model_call", "step": "planner", "messages": [], "response": {}},
        ],
    )
    calls = list(pick_calls(folder))
    assert [bool(c.cut_args) for c in calls] == [True, False, False]
    assert [c.empty for c in calls] == [False, True, False]
    lines = scan([folder])
    assert lines[0] == f"{folder.name}: picker calls 3, cut 1, empty 1"
    assert f"  cut   add_a_soup: {CUT_ARGS}" in lines


def test_is_cut_is_true_only_for_invalid_json():
    assert is_cut(CUT_ARGS)
    assert not is_cut('{"slots": [], "wish_met": null}')


def test_renamed_renames_the_field_and_the_prompt_word():
    tool = {
        "function": {
            "parameters": {
                "properties": {"slots": {}, "wish_met": {"type": "string"}},
                "required": ["slots", "wish_met"],
            }
        }
    }
    new_tool, system = renamed(tool, "Set wish_met; never wish_meter.", "wish_met", "wish_result")
    params = new_tool["function"]["parameters"]
    assert list(params["properties"]) == ["slots", "wish_result"]
    assert params["required"] == ["slots", "wish_result"]
    assert system == "Set wish_result; never wish_meter."
    assert "wish_met" in tool["function"]["parameters"]["properties"]


def test_renamed_stops_when_the_field_is_not_in_the_schema():
    tool = {"function": {"parameters": {"properties": {"wish_result": {}}, "required": []}}}
    try:
        renamed(tool, "prompt", "wish_met", "wish_result")
    except SystemExit as stop:
        assert "wish_met is not a field of PickerAnswer" in str(stop)
    else:
        raise AssertionError("renaming a missing field must stop")


def test_replay_refuses_without_spend(tmp_path, capsys):
    assert main(["replay", "--runs", str(tmp_path), "--out", str(tmp_path / "out")]) == 2
    assert "--spend" in capsys.readouterr().out
