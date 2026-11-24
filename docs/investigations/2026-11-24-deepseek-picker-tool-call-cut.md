# DeepSeek cuts the picker's tool-call arguments short (2026-11-23/24)

Status: **worked around, cause not fully known.** `pick_v3` plus code salvage (commit `294198a`) lowered the rate and stopped it from failing scenarios. A field rename (`wish_met` → `wish_result`) removed it in a replay experiment and shipped as `pick_v4` (commit `896d17c`). Since then no picker answer has been cut: 0 of 41 picker calls in development run 5, and none in evaluation round 11 (`docs/planning-evaluation-history.md`). The salvage code stays as a safety net.

This document records how the problem was found, every occurrence in the evaluation runs, the runtime context needed to reproduce it, what was tried, and how to repeat the analysis. The tool that does the counting and the replays is `backend/minemydinner/evaluation/tool_call_cut.py`.

## 1. Summary

- **What happens.** The picker model (plan 3b-2 one, `agent/picker.py`) answers through a forced tool call `PickerAnswer`. Sometimes DeepSeek returns that tool call with arguments that stop right after the key `"wish_met"`:
  `{"choices": [], "slots": [{"index": 0, "recipe_id": 779}], "wish_met"`
  The HTTP status is 200, `finish_reason` is `"tool_calls"`, and the completion is billed in full (about 105-125 tokens) although the arguments hold only about 25 tokens. Nothing reports an error; LangChain fails to parse the arguments and puts the call in `invalid_tool_calls`.
- **Where.** Only the picker. In the 99 local run folders that hold a results.jsonl (2026-11-24), no other model call (route, parse, planner, reader, the old reviser) ever came back cut, although the planner's own answer has a `wish_met` field too (it appeared in 34 planner answers in the four runs below, never cut).
- **How often.** 17 of 172 picker calls in development runs 1-4 (about 10%), up to 21% in one run; 3 of 45 (7%) after the `pick_v3` schema change; 0 of 41 after the `pick_v4` field rename (development run 5), and none in round 11.
- **Effect.** A cut answer is a missing answer. Before the fixes it cost a retry and sometimes the whole change (a dish removed with no replacement, an add not made, a reply saying nothing was found while the model had chosen a dish).
- **What helps.** Making the field non-nullable did not stop it (run 4 and the rename experiment still cut after `"wish_met"` when the field was a string enum). Renaming the field did: 5 cut of 21 answers with `wish_met`, 0 of 19 with `wish_result`, same inputs, same prompt otherwise.

## 2. Setting

| Item | Value |
|---|---|
| Model | `deepseek-flash` (catalog id, `backend/config/models.yaml`), provider `deepseek`, no fallback used in any occurrence (`fallback_used: false`) |
| Model options | `extra_body: {thinking: {type: disabled}}` (thinking mode is off; it rejects forced tool choice) |
| Task | `planning_eval` (evaluation runs; production uses `planning` with the same model) |
| Tool choice | forced (LangChain `create_agent` with `ToolStrategy` sends `tool_choice` any → `"required"`) |
| Tools offered to the picker | `search_recipes` (at most 2 calls per attempt) and the answer tool `PickerAnswer` |
| Output limit | none set (`max_tokens` is not sent) |
| Libraries | langchain 1.4.2, langchain-core 1.7.0, langchain-deepseek 1.1.1, langchain-openai 1.4.1, langgraph 1.2.11, openai 2.49.0, httpx 0.28.1 |
| Library used in the runs | `backend/eval_data/planning/library_revise.yaml`, test database, `--with-embeddings` |

The answer schema in runs 1-3 (`PICK_TOOLS_VERSION` `726ed9ae7fed`, `pick_v1` / `pick_v2`):

```text
PickerAnswer {
  choices: [ChoiceAnswer {index: int, ids: [int]}]
  slots:   [SlotAnswer {index: int, recipe_id: int | null, reason: str = ""}]
  wish_met: bool | null = null
  note: str = ""
}
```

In run 4 (`PICK_TOOLS_VERSION` `eeab2df90e05`, `pick_v3`) no field is nullable:

```text
PickerAnswer {
  choices: [ChoiceAnswer {index: int, ids: [int]}]
  slots:   [SlotAnswer {index: int, recipe_id: int (0 = nothing fits), reason: str = ""}]
  note: str = ""
  wish_met: "yes" | "no" | "no_wish"
}
```

In every cut answer the model wrote `wish_met` right after `slots`, whatever the schema's field order (run 4 lists `note` before `wish_met`; the model still put `wish_met` first).

## 3. How it was found

1. **Development run 1** (`20261123T112836Z`, commit `e770385`, `pick_v1`): three picker replies were empty — no tool call, no content (`meal_avoid_shrimp_en` twice, `new_guest_brings_the_card` once). In `meal_avoid_shrimp_en` both attempts were empty, so the shrimp dish, which broke the one-meal avoidance, was removed with no replacement and the plan shrank. At the time the run record kept only `content` and `tool_calls`, so the cause was not visible. The fix after run 1 (`af1a940`) asked again at once after an empty reply (within a turn budget of 6 picker calls) and started recording `invalid_tool_calls`. The billed tokens of those empty replies (113-124 output tokens in their `llm_calls` rows) match the cut answers seen later, so they were almost certainly the same problem.
2. **Development run 2** (`20261123T125329Z`, commit `af1a940`): with the new record, the "empty" replies showed up as `invalid_tool_calls` whose arguments stop after `"wish_met"` (4 calls). The re-asks recovered them; no scenario failed because of it.
3. **Development run 3** (`20261123T135719Z`, commit `e807d2c`, `pick_v2`): 10 of 47 picker calls were cut. In `message_after_shopping_button` all four attempts (two invokes plus two re-asks) were cut, the turn's budget ran out, the add stayed empty, and the reply said no vegetable dish was found although the picker had chosen one every time. Checking which model answered and whether any error code was returned: the four `llm_calls` rows were `success: true`, no fallback, 104-117 output tokens each; the error was LangChain's local `OUTPUT_PARSING_FAILURE` (`JSONDecodeError: Expecting ':' delimiter: line 1 column 70 (char 69)`).
4. **Raw HTTP replay** (a paid replay, 4 calls): the first picker input of `message_after_shopping_button` was sent straight to the DeepSeek HTTP API with the same messages, tools, `tool_choice: "required"` and thinking disabled. One of the three `PickerAnswer` replies came back cut in DeepSeek's own JSON (section 5). So the cut is on DeepSeek's side, not in LangChain's parsing or the stream assembly.
5. **Fix after run 3** (`294198a`, `pick_v3`): no nullable field; `wish_met` a three-value string after `note`; `recipe_id` 0 for nothing fits; code salvages a cut answer whose `slots` closed. A replay of run 3's cut inputs with the new schema (15 calls) returned 12 answers, none cut.
6. **Development run 4** (`20261123T153538Z`, commit `294198a`, `pick_v3`): 3 of 45 picker calls still cut, still right after `"wish_met"` (now a string enum). One was salvaged; two were set aside (a wish slot with `wish_met` unknown) and asked again. All three scenarios passed.
7. **Field-name experiment** (a paid replay, 60 calls, about $0.01): the 10 distinct inputs that were cut in runs 2-4, each 3 times, with `pick_v3` as is and with the field renamed to `wish_result` (the prompt's mentions renamed too) — section 6.

## 4. Every occurrence

Rates per run (from `python -m minemydinner.evaluation.tool_call_cut scan`):

| Run (folder under `backend/eval_data/planning/runs/`) | Commit | Picker prompt / schema | Picker calls | Cut | Empty | Rate |
|---|---|---|---|---|---|---|
| `20261123T112836Z-revise-shortlist-vectors` (run 1) | `e770385` | `pick_v1` / `726ed9ae7fed` | 37 | 0 | 3 | 8% (empty, cause not recorded) |
| `20261123T125329Z-revise-shortlist-vectors` (run 2) | `af1a940` | `pick_v1` / `726ed9ae7fed` | 43 | 4 | 0 | 9% |
| `20261123T135719Z-revise-shortlist-vectors` (run 3) | `e807d2c` | `pick_v2` / `726ed9ae7fed` | 47 | 10 | 0 | 21% |
| `20261123T153538Z-revise-shortlist-vectors` (run 4) | `294198a` | `pick_v3` / `eeab2df90e05` | 45 | 3 | 0 | 7% |

The cut arguments, exactly as recorded (all `PickerAnswer`, all `served_model` `deepseek-flash`, no fallback):

| Run | Scenario | Arguments received | Scenario result |
|---|---|---|---|
| 2 | `replace_keeps_hand_added` | `{"choices": [], "slots": [{"index": 0, "recipe_id": 767}], "wish_met"` | as expected (re-ask) |
| 2 | `add_a_soup` (×3) | `… "recipe_id": 748}], "wish_met"` (twice), `… "recipe_id": 746}], "wish_met"` | as expected |
| 3 | `replace_staple_with_noodles` | `… "recipe_id": 755}], "wish_met"` | as expected |
| 3 | `meal_avoid_beef` (×2) | `… "recipe_id": 777}], "wish_met"`, `… "recipe_id": 767}], "wish_met"` | as expected |
| 3 | `meal_avoid_covers_hand_added` (×2) | `{"choices": [], "slots": [{"index": 0, "recipe_id": 777}, {"index": 1, "recipe_id": 768}], "wish_met"` and the same with 769 | as expected |
| 3 | `must_use_potato` | `{"choices": [{"index": 0, "ids": [741]}], "slots": [{"index": 0, "recipe_id": 759}], "wish_met"` | as expected |
| 3 | `message_after_shopping_button` (×4) | `… "recipe_id": 775}], "wish_met"`, `… 754 …` (twice), `… 779 …` | **miss** (budget spent; add left empty) |
| 4 | `replace_staple_with_noodles` | `… "recipe_id": 755}], "wish_met"` | as expected (salvaged) |
| 4 | `general_heavier_keeps_hand_added` | `… "recipe_id": 743}, {"index": 1, "recipe_id": 739}], "wish_met"` | as expected (set aside, asked again) |
| 4 | `too_many_eggs` | `… "recipe_id": 779}, {"index": 1, "recipe_id": 741}], "wish_met"` | as expected (set aside, asked again) |

Run 1's three empty replies are in `meal_avoid_shrimp_en` (two, scenario missed: the plan shrank to 2 dishes) and `new_guest_brings_the_card` (one).

Arguments are 69, 95 or 101 characters long — whatever `choices` and `slots` hold — and always end at the same key. Every occurrence is in the development set `scenarios_revise.yaml`; none of these runs included a held-out set.

## 5. Raw evidence: one cut answer from the HTTP API

Request (the first picker call of `message_after_shopping_button`, run 3), sent with httpx to `https://api.deepseek.com/chat/completions` on 2026-11-24:

```json
{
  "model": "deepseek-flash",
  "messages": [{"role": "system", "content": "<pick_v2>"}, {"role": "user", "content": "<below>"}],
  "tools": ["search_recipes (schema from agent/planner.py PICK_TOOLS)", "PickerAnswer (schema of section 2, runs 1-3)"],
  "tool_choice": "required",
  "thinking": {"type": "disabled"}
}
```

User message:

```text
Shortlist: candidates from the user's library for each dish type this meal needs, best first, as JSON keyed by dish type.
{"vegetable dish":{"complete":false,"candidates":[{"id":754,"title":"鸡蛋饼","time_min":10,"time_source":"estimated","servings":1,"missing":["鸡蛋"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":753,"title":"白煮蛋","time_min":12,"time_source":"stated","servings":1,"missing":["鸡蛋"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":779,"title":"清炒蘑菇","time_min":10,"time_source":"stated","servings":2,"missing":["蘑菇","蒜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":775,"title":"松仁玉米","time_min":15,"time_source":"stated","servings":2,"missing":["玉米","松子"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":760,"title":"青椒土豆片","time_min":15,"time_source":"estimated","servings":2,"missing":["土豆","青椒"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]}]}}

Current plan:
- 777 辣椒炒肉 (meat dish; 猪肉, 辣椒)
- 771 清炒菠菜 (vegetable dish; 菠菜)

Diner and meal rules:
- none recorded

Meal conditions: shopping allowed; time limit: none; must use in at least one dish: nothing

The user's wish: none

Slots (new dishes to choose):
- Slot 0: a vegetable dish, added

Reply locale: zh
```

Four replies: one cut `PickerAnswer`, one `search_recipes` call, two complete `PickerAnswer`. The cut one, in full except the identifiers of this one call (the response id, its creation time and the tool-call id are left out):

```json
{
  "object": "chat.completion",
  "model": "deepseek-flash",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "",
      "tool_calls": [{
        "index": 0,
        "id": "call_00_…",
        "type": "function",
        "function": {
          "name": "PickerAnswer",
          "arguments": "{\"choices\": [], \"slots\": [{\"index\": 0, \"recipe_id\": 779}], \"wish_met\""
        }
      }]
    },
    "logprobs": null,
    "finish_reason": "tool_calls"
  }],
  "usage": {"prompt_tokens": 3100, "completion_tokens": 110, "total_tokens": 3210,
            "prompt_tokens_details": {"cached_tokens": 1920},
            "prompt_cache_hit_tokens": 1920, "prompt_cache_miss_tokens": 1180},
  "system_fingerprint": "bc19ba97cbe016479903222bb1578db6"
}
```

A complete one from the same request, for comparison:

```json
"arguments": "{\"choices\": [], \"slots\": [{\"index\": 0, \"recipe_id\": 775}], \"wish_met\": null, \"note\": \"松仁玉米口味清甜，与辣椒炒肉、清炒菠菜搭配起来荤素均衡，也补充了一味玉米和坚果的嚼感。\"}",
"finish_reason": "tool_calls",
"usage": {"prompt_tokens": 3100, "completion_tokens": 127, ...},
"system_fingerprint": "bc19ba97cbe016479903222bb1578db6"
```

What this rules out:
- **A token limit**: no `max_tokens` is sent, and `finish_reason` would be `length`.
- **Thinking**: disabled; no reasoning content in the reply.
- **LangChain or stream assembly**: DeepSeek's own non-streaming JSON already holds the cut arguments.
- **A provider error**: HTTP 200 and a normal finish reason; the completion tokens (110) say the model produced more than the arguments show.
- **A second tool call carrying the rest**: there is one tool call and empty content.

## 6. What was tried

| Attempt | Calls | `PickerAnswer` replies | Cut | Notes |
|---|---|---|---|---|
| Raw replay, run 3 input, `pick_v2` schema | 4 | 3 | 1 | section 5 |
| Replay of run 3's cut inputs (5 distinct), `pick_v3` schema | 15 | 12 | 0 | after commit `294198a` |
| Development run 4, `pick_v3` schema | 45 picker calls | — | 3 | still after `"wish_met"` |
| Field-name experiment, runs 2-4 cut inputs (10 distinct, ×3), `pick_v3` as is | 30 | 21 | **5** | |
| Same inputs, field renamed `wish_result` (prompt mentions renamed too) | 30 | 19 | **0** | |

Reading the results:
- **The nullable type was not the cause.** With `wish_met` a required three-value string, answers were still cut right after that key (run 4; 5 of 21 in the experiment).
- **The key name looks like the trigger.** Renaming it removed the cut on the same inputs. The sample is small (5/21 against 0/19; a one-sided Fisher exact test gives p ≈ 0.03), and the two `pick_v3` replays disagree (0/12 one day, 5/21 the next on a superset of the inputs), so the rate varies from call to call. Treat it as strong but not final evidence.
- **Why this key and why only the picker** is not known. The planner's `PlanProposal` also has a `wish_met` field and never came back cut, possibly because there it comes after a plain list of ids rather than an array of objects.
- **Salvage works as a safety net.** In run 4 one cut answer was used as salvaged; two were set aside because a wish slot was filled and `wish_met` was unknown, and asked again; all three scenarios passed.

## 7. Current handling

In the code since `294198a`:
- An empty reply or a cut answer is asked again at once (the re-ask says the answer was cut short), within the turn's budget (6 picker calls, so a change turn stays at 7 model calls or fewer).
- A cut answer whose `choices` and `slots` closed is salvaged: it goes through every check like a full answer; a closed, valid `wish_met` and the note are kept; with `wish_met` unknown, an answer that filled a wish-only slot is set aside.
- When no usable answer comes, the reply says the dish could not be added or changed this time, never that the library has none.

Since `896d17c`: `pick_v4` is `pick_v3` with the field renamed `wish_result`. Code converts it back to the internal `wish_met` at the boundary (`PickerAnswer.picks()`), so records and replies did not change. The salvage and the re-ask stay. Development run 5 (0 of 41 cut) and round 11 (none cut) are the first results with it; the rate is watched in later runs with `tool_call_cut scan`.

## 8. How to repeat this analysis

The run records with model text (`results.jsonl`) are git-ignored and exist only on the machine that ran the evaluation; keep a private copy of the four run folders above if this needs repeating later. `summary.json`, `tools.json` and the prompt copies of each run are committed.

In the commands below, `$py` is the project's Python interpreter (`python.defaultInterpreterPath` in `.vscode/settings.json`), for example `$py = "C:/path/to/python.exe"`.

Count cut answers in run records (free):

```powershell
cd backend
& $py -m minemydinner.evaluation.tool_call_cut scan --runs eval_data/planning/runs/20261123T125329Z-revise-shortlist-vectors eval_data/planning/runs/20261123T135719Z-revise-shortlist-vectors eval_data/planning/runs/20261123T153538Z-revise-shortlist-vectors
```

Replay the cut inputs against DeepSeek with today's picker prompt and schema, optionally with a field renamed (paid; each call costs about $0.0002; get approval for the spend first):

```powershell
cd backend
& $py -m minemydinner.evaluation.tool_call_cut replay --runs <run folders> --repeat 3 --out <scratch dir> --spend
& $py -m minemydinner.evaluation.tool_call_cut replay --runs <run folders> --repeat 3 --out <scratch dir> --rename wish_result=wish_met --spend
```

Since `pick_v4` the schema already uses `wish_result`, so the rename example compares against the old name; renaming a field the schema does not have stops with an error. Each raw HTTP response is saved under `--out`; the summary prints the calls, the `PickerAnswer` replies, the cut ones and the search calls. The replay needs `DEEPSEEK_API_KEY` in the environment (it is read through the settings and never printed); it calls no database.

To compare a candidate fix, run `replay` with and without the change on the same inputs and the same `--repeat`, and compare the cut counts.

## 9. Open questions

- Why DeepSeek stops after this key; whether other keys or other positions (after an array of objects) do the same. A report to DeepSeek with the raw request and reply of section 5 could answer it.
- Whether the production task (`planning`, same model) sees the same rate; production calls record `invalid_tool_calls` in the planning run since `af1a940`, so a query of real planning runs would show it.
