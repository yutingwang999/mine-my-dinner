"""Find and replay the picker answers DeepSeek cuts short
(docs/investigations/2026-11-24-deepseek-picker-tool-call-cut.md).

DeepSeek sometimes returns a `PickerAnswer` tool call whose arguments stop right after one
key, with HTTP 200 and `finish_reason: "tool_calls"`. This module counts those answers in
local run records and replays the inputs that were cut, so the effect of a schema change can
be measured. `scan` reads files only; `replay` calls DeepSeek and refuses to unless `--spend`
is given. The API key comes from the settings and is never printed.

    python -m minemydinner.evaluation.tool_call_cut scan --runs <run folder>...
    python -m minemydinner.evaluation.tool_call_cut replay --runs <run folder>... \\
        --repeat 3 --out <dir> [--rename wish_result=wish_met] --spend
"""

import argparse
import copy
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ANSWER_TOOL = "PickerAnswer"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
REPLAY_TASK = "planning_eval"


def replay_model(config=None) -> tuple[str, str]:
    """The API URL and model id the replay uses: the primary model of the `planning_eval`
    task in the model config. Only DeepSeek's endpoint is known here."""
    from minemydinner.llm.config import load_llm_config

    config = config or load_llm_config()
    model = config.models[config.tasks[REPLAY_TASK].primary]
    if model.provider != "deepseek":
        raise SystemExit(
            f"The replay calls DeepSeek, but task {REPLAY_TASK} runs on {model.provider} "
            f"({model.model}); set a DeepSeek model as its primary first."
        )
    return DEEPSEEK_URL, model.model


@dataclass
class PickCall:
    """One picker call of a run record."""

    run: str
    scenario: str
    human: str
    cut_args: list[str]
    empty: bool


def pick_calls(run_folder: Path) -> Iterator[PickCall]:
    """Every picker call in a run folder's results.jsonl (git-ignored, so local only)."""
    for line in (run_folder / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        for run in record.get("planning_runs") or []:
            for event in run.get("events", []):
                if event.get("kind") != "model_call" or event.get("step") != "pick":
                    continue
                response = event.get("response") or {}
                cut = [
                    call.get("args") or ""
                    for call in response.get("invalid_tool_calls") or []
                    if call.get("name") == ANSWER_TOOL
                ]
                empty = not cut and not response.get("tool_calls") and not (
                    response.get("content") or ""
                ).strip()
                human = next(
                    (m["content"] for m in event.get("messages", []) if m.get("type") == "human"),
                    "",
                )
                yield PickCall(run_folder.name, record.get("name", ""), human, cut, empty)


def is_cut(arguments: str) -> bool:
    """True when tool-call arguments are not valid JSON (the cut answers are not)."""
    try:
        json.loads(arguments)
    except ValueError:
        return True
    return False


def scan(run_folders: list[Path]) -> list[str]:
    """Per run: picker calls, cut answers, empty replies, and each cut answer's arguments."""
    lines = []
    for folder in run_folders:
        calls = list(pick_calls(folder))
        cut = [c for c in calls if c.cut_args]
        empty = [c for c in calls if c.empty]
        lines.append(f"{folder.name}: picker calls {len(calls)}, cut {len(cut)}, empty {len(empty)}")
        for call in cut:
            for args in call.cut_args:
                lines.append(f"  cut   {call.scenario}: {args}")
        for call in empty:
            lines.append(f"  empty {call.scenario}")
    return lines


def renamed(tool: dict, system: str, old: str, new: str) -> tuple[dict, str]:
    """The answer tool and system prompt with one field renamed (for a comparison run)."""
    tool = copy.deepcopy(tool)
    params = tool["function"]["parameters"]
    if old not in params["properties"]:
        raise SystemExit(f"{old} is not a field of {ANSWER_TOOL}")
    params["properties"] = {(new if k == old else k): v for k, v in params["properties"].items()}
    params["required"] = [(new if k == old else k) for k in params.get("required", [])]
    return tool, re.sub(rf"\b{re.escape(old)}\b", new, system)


def replay(run_folders: list[Path], repeat: int, out: Path, rename: str | None) -> list[str]:
    """Send every distinct input that was cut in these runs to DeepSeek `repeat` times, with
    today's picker prompt and answer schema (optionally one field renamed), and save each raw
    HTTP response under `out`. Returns a summary."""
    import httpx
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from minemydinner.agent.picker import PickerAnswer
    from minemydinner.agent.planner import PICK_TOOLS
    from minemydinner.agent.request import load_prompt
    from minemydinner.settings import Settings

    inputs: dict[str, str] = {}
    for folder in run_folders:
        for call in pick_calls(folder):
            if call.cut_args or call.empty:
                inputs.setdefault(call.human, call.scenario)
    system = load_prompt("pick")
    answer_tool = convert_to_openai_tool(PickerAnswer)
    label = "current"
    if rename:
        old, new = rename.split("=", 1)
        answer_tool, system = renamed(answer_tool, system, old, new)
        label = f"{old}->{new}"
    tools = [convert_to_openai_tool(t) for t in PICK_TOOLS] + [answer_tool]
    api_url, model_id = replay_model()
    key = Settings().deepseek_api_key.get_secret_value()
    out.mkdir(parents=True, exist_ok=True)
    total = answers = cut = searches = 0
    lines = [f"{len(inputs)} distinct inputs, {repeat} times each, schema: {label}"]
    for n, (human, scenario) in enumerate(inputs.items()):
        body = {
            "model": model_id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": human}],
            "tools": tools,
            "tool_choice": "required",
            "thinking": {"type": "disabled"},
        }
        for i in range(repeat):
            response = httpx.post(
                api_url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60
            )
            (out / f"{n:02d}_{i}.json").write_text(response.text, encoding="utf-8")
            total += 1
            message = response.json()["choices"][0]["message"]
            for call in message.get("tool_calls") or []:
                if call["function"]["name"] != ANSWER_TOOL:
                    searches += 1
                    continue
                answers += 1
                if is_cut(call["function"]["arguments"]):
                    cut += 1
                    lines.append(f"  cut {scenario}: {call['function']['arguments']}")
    lines.append(f"calls {total}; answers {answers}; cut {cut}; searches {searches}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    scan_p = sub.add_parser("scan", help="count cut picker answers in run records (no API call)")
    scan_p.add_argument("--runs", nargs="+", type=Path, required=True)
    replay_p = sub.add_parser("replay", help="replay the cut inputs against DeepSeek (paid)")
    replay_p.add_argument("--runs", nargs="+", type=Path, required=True)
    replay_p.add_argument("--repeat", type=int, default=3)
    replay_p.add_argument("--out", type=Path, required=True)
    replay_p.add_argument("--rename", help="rename one answer field, e.g. wish_result=wish_met")
    replay_p.add_argument("--spend", action="store_true", help="confirm the paid API calls")
    args = parser.parse_args(argv)
    if args.command == "scan":
        lines = scan(args.runs)
    else:
        if not args.spend:
            print("replay calls the DeepSeek API and costs money: add --spend to run it")
            return 2
        lines = replay(args.runs, args.repeat, args.out, args.rename)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
