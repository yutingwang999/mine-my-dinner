import json
from decimal import Decimal

import pytest

from minemydinner.evaluation import run as run_module
from minemydinner.evaluation.export import export_logs
from minemydinner.evaluation.run import run_evaluation
from minemydinner.models import ExtractionLog, LlmCall
from tests.test_eval_metrics import recipe

FIELDS = ("lang", "input_text", "model_output", "user_corrected", "model_name", "prompt_version")


def log(corrected, lang="zh") -> ExtractionLog:
    return ExtractionLog(
        input_text="虚构的菜谱文字",
        model_output=recipe(["葱"], lang),
        user_corrected=corrected,
        model_name="gemini",
        prompt_version="extract_v1",
    )


@pytest.fixture
def logs_with_and_without_correction(session):
    session.add_all([log(recipe(["葱"], "zh") | {"cookware_ids": []}), log({})])
    session.flush()


@pytest.fixture
def llm_calls_rows(session):
    def call(task, success, cost, latency, model="gemini-test"):
        return LlmCall(
            task=task,
            provider="google_genai",
            model=model,
            input_tokens=100,
            output_tokens=50,
            cost_estimate=Decimal(cost),
            latency_ms=latency,
            success=success,
        )

    session.add_all(
        [
            call("extraction", True, "0.002", 1000),
            call("extraction", True, "0.004", 3000),
            call("extraction", False, "0.001", 500),
            call("image_understanding", True, "0.006", 2000),
            call("planning", True, "9", 9000),
        ]
    )
    session.flush()


def fictional_jsonl(tmp_path):
    path = tmp_path / "extraction.jsonl"
    rows = [
        {"lang": "zh", "input_text": "虚构", "model_output": recipe(["葱"], "zh"),
         "user_corrected": recipe(["葱"], "zh"), "model_name": "gemini", "prompt_version": "extract_v1"},
        {"lang": "en", "input_text": "fictional", "model_output": recipe([], "en"),
         "user_corrected": recipe(["egg"], "en"), "model_name": "gemini", "prompt_version": "extract_v1"},
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    return path


def run_export(session, out):
    count = export_logs(session, out)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert count == len(rows)
    return rows


def test_export_writes_only_rows_with_corrections(session, tmp_path, logs_with_and_without_correction):
    rows = run_export(session, tmp_path / "eval" / "out.jsonl")
    assert [row["lang"] for row in rows] == ["zh"]
    assert set(rows[0]) == set(FIELDS)
    assert rows[0]["input_text"] == "虚构的菜谱文字"


def test_run_prints_metrics_cost_and_latency(tmp_path, session, llm_calls_rows, capsys):
    assert run_evaluation(fictional_jsonl(tmp_path), session) == 0
    out = capsys.readouterr().out
    for key in ["ingredient_f1", "fabrication_rate", "zh", "en", "cost_per_item", "latency_ms"]:
        assert key in out
    assert "success_rate" in out


def test_run_reports_costs_from_successful_extraction_calls_only(tmp_path, session, llm_calls_rows):
    from minemydinner.evaluation.run import call_stats

    [stats] = call_stats(session)
    assert stats.model == "gemini-test"
    assert stats.calls == 4 and stats.success_rate == pytest.approx(0.75)
    assert stats.cost_per_item == pytest.approx(0.004)
    assert stats.latency_ms == pytest.approx(2000)


def test_empty_input_exits_with_clear_message(tmp_path, session, capsys):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    assert run_evaluation(empty, session) == 2
    assert "no rows" in capsys.readouterr().err


def test_missing_file_exits_with_clear_message(tmp_path, session, capsys):
    assert run_evaluation(tmp_path / "absent.jsonl", session) == 2
    assert "absent.jsonl" in capsys.readouterr().err


def test_broken_line_names_its_line_number(tmp_path, session, capsys):
    path = tmp_path / "broken.jsonl"
    first = fictional_jsonl(tmp_path).read_text(encoding="utf-8").splitlines()[0]
    path.write_text(f"{first}\nnot json\n", encoding="utf-8")
    assert run_evaluation(path, session) == 2
    assert "line 2" in capsys.readouterr().err


def test_main_parses_input_argument(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(run_module, "_run_with_database", lambda path: seen.setdefault("path", path) and 0)
    run_module.main(["--input", str(tmp_path / "x.jsonl")])
    assert seen["path"] == tmp_path / "x.jsonl"
