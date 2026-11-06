import pytest

from minemydinner.evaluation.metrics import EvalItem, score_item, summarize


def line(name, amount=None, unit=None, amount_type="missing"):
    return {
        "raw": name,
        "name": name,
        "amount": amount,
        "unit": unit,
        "amount_type": amount_type,
        "role": "main",
        "optional": False,
    }


def recipe(lines, lang="zh"):
    return {
        "lang": lang,
        "title": "测试菜",
        "dish_type": "other",
        "time_min": None,
        "time_source": None,
        "servings": None,
        "tags": [],
        "steps_summary": None,
        "ingredients": [entry if isinstance(entry, dict) else line(entry) for entry in lines],
        "cookware": [],
    }


def item(gold, predicted=(), lang="zh", predicted_raw=None) -> EvalItem:
    return EvalItem(
        lang=lang,
        gold=recipe(gold, lang),
        predicted=predicted_raw if predicted_raw is not None else recipe(predicted, lang),
    )


def test_f1_counts_missing_and_extra_lines():
    report = summarize([item(gold=["葱", "蛋"], predicted=["葱", "盐"])])
    assert report.overall.ingredient_f1 == pytest.approx(0.5)


def test_names_match_ignoring_case_and_outer_spaces():
    report = summarize([item(gold=["Egg"], predicted=[" egg "], lang="en")])
    assert report.overall.ingredient_f1 == 1.0


def test_repeated_names_match_once_each():
    counts = score_item(recipe(["盐"]), recipe(["盐", "盐"]))
    assert (counts.true_positives, counts.false_positives, counts.false_negatives) == (1, 1, 0)


def test_f1_is_micro_averaged_over_lines():
    report = summarize(
        [
            item(gold=["葱", "蛋", "盐", "糖"], predicted=["葱", "蛋", "盐", "糖"]),
            item(gold=["醋"], predicted=[]),
        ]
    )
    # 4 true positives, 0 false positives, 1 false negative.
    assert report.overall.ingredient_f1 == pytest.approx(2 * 1.0 * 0.8 / 1.8)


def test_amount_accuracy_uses_only_precise_gold():
    report = summarize(
        [
            item(
                gold=[line("蛋", 2, "piece", "count"), line("盐", None, None, "vague")],
                predicted=[line("蛋", 2, "piece", "count"), line("盐", None, None, "vague")],
            )
        ]
    )
    assert report.overall.amount_accuracy == 1.0 and report.overall.amount_counted == 1


def test_amount_accuracy_needs_equal_amount_and_unit():
    report = summarize(
        [
            item(
                gold=[line("蛋", 2, "piece", "count"), line("面粉", 200, "g", "exact")],
                predicted=[line("蛋", 2, "stalk", "count"), line("面粉", 250, "g", "exact")],
            )
        ]
    )
    assert report.overall.amount_accuracy == 0.0 and report.overall.amount_counted == 2


def test_amount_type_accuracy_covers_all_matched_lines():
    report = summarize(
        [
            item(
                gold=[line("盐", None, None, "vague"), line("葱")],
                predicted=[line("盐", None, None, "missing"), line("葱")],
            )
        ]
    )
    assert report.overall.amount_type_accuracy == pytest.approx(0.5)


def test_fabrication_rate_uses_raw_model_output():
    report = summarize(
        [item(gold=[line("葱", None, None, "vague")], predicted=[line("葱", 5, "g", "exact")])]
    )
    assert report.overall.fabrication_rate == 1.0


def test_fabrication_rate_ignores_lines_whose_gold_has_an_amount():
    report = summarize(
        [
            item(
                gold=[line("蛋", 2, "piece", "count"), line("葱", None, None, "vague")],
                predicted=[line("蛋", 3, "piece", "count"), line("葱", None, None, "vague")],
            )
        ]
    )
    assert report.overall.fabrication_rate == 0.0


def test_results_are_split_by_language():
    report = summarize(
        [item(lang="zh", gold=["葱"], predicted=["葱"]), item(lang="en", gold=["egg"], predicted=[])]
    )
    assert report.by_language["zh"].ingredient_f1 == 1.0
    assert report.by_language["en"].ingredient_f1 == 0.0
    assert report.overall.items == 2


def test_invalid_output_counts_against_json_rate_and_recall():
    report = summarize([item(gold=["葱"], predicted_raw={"bad": 1})])
    assert report.overall.valid_json_rate == 0.0 and report.overall.ingredient_f1 == 0.0


def test_valid_json_rate_is_per_row():
    report = summarize([item(gold=["葱"], predicted=["葱"]), item(gold=["葱"], predicted_raw={})])
    assert report.overall.valid_json_rate == pytest.approx(0.5)


def test_no_precise_samples_reports_none_not_zero():
    report = summarize(
        [item(gold=[line("盐", None, None, "vague")], predicted=[line("盐", None, None, "vague")])]
    )
    assert report.overall.amount_accuracy is None


def test_no_lines_at_all_reports_none():
    report = summarize([item(gold=[], predicted=[])])
    assert report.overall.ingredient_f1 is None and report.overall.fabrication_rate is None
