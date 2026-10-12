from pac.evaluation.domain.north_star import north_stars
from pac.evaluation.domain.prediction import Prediction
from pac.evaluation.domain.run import RunConfig
from pac.guardrail.domain.classification import Classification, Usage
from pac.guardrail.domain.decision import Decision, Verdict


def _config(run_id: str, split: str, repeats: int = 1, **over: object) -> RunConfig:
    return RunConfig.model_validate(
        {
            "run_id": run_id,
            "created_at": run_id,
            "classifier": "m",
            "backend": "b",
            "prompt_version": "p",
            "policy_version": "v0",
            "policy_sha256": "x",
            "dataset_sha256": "y",
            "split": split,
            "n_cases": 2,
            "repeats": repeats,
            "with_history": False,
            **over,
        }
    )


def _pred(case_id: str, gold: Verdict, said: Verdict, repeat: int = 0) -> Prediction:
    result = Classification(
        decision=Decision(
            rationale="r",
            verdict=said,
            rule_ids=("R1",) if said == "violation" else (),
            confidence=0.9,
        ),
        model="m",
        prompt_version="p",
        policy_version="v0",
        latency_ms=1.0,
        model_latency_ms=1.0,
        usage=Usage(),
        cached=True,
    )
    return Prediction(
        case_id=case_id,
        repeat=repeat,
        split="test",
        kind="user_input",
        gold=gold,
        gold_rules=(),
        label_confidence="high",
        tags=(),
        origin="targeted",
        result=result,
    )


def test_pools_held_out_sets_and_keeps_the_spread_over_runs() -> None:
    test = [
        _pred("a", "allow", "allow", 0),
        _pred("b", "violation", "allow", 0),
        _pred("a", "allow", "allow", 1),
        _pred("b", "violation", "violation", 1),
    ]
    agents = [_pred("c", "allow", "allow"), _pred("d", "allow", "violation")]
    runs = [
        (_config("1", "test", repeats=2), test),
        (_config("2", "agents"), agents),
        (_config("3", "dev"), [_pred("e", "allow", "allow")]),
        (_config("4", "test", with_history=True), test),
    ]
    (star,) = north_stars(runs, exclude=set())
    assert (star.correct, star.total) == (2, 4)
    assert (star.low, star.high) == (0.5, 0.75)
    assert star.sets == ("test", "agents")
    assert star.missing == ("stress", "agents-pressure")


def test_excluded_events_leave_every_prompt_and_the_repeated_run_wins() -> None:
    once = [_pred("a", "allow", "violation"), _pred("t", "allow", "allow")]
    thrice = [_pred("a", "allow", "allow", r) for r in range(3)] + [
        _pred("t", "allow", "violation", r) for r in range(3)
    ]
    runs = [(_config("9", "stress"), once), (_config("1", "stress", repeats=3), thrice)]
    (star,) = north_stars(runs, exclude={"t"})
    assert star.run_ids == ("1",)
    assert (star.correct, star.total) == (1, 1)


def test_only_runs_on_todays_files_count_when_given() -> None:
    runs = [(_config("1", "test", dataset_sha256="old"), [_pred("a", "allow", "allow")])]
    assert north_stars(runs, set(), current={"test": "new"}) == []
    assert len(north_stars(runs, set(), current={"test": "old"})) == 1


def test_a_run_that_scored_only_part_of_a_set_does_not_count() -> None:
    runs = [(_config("1", "test"), [_pred("a", "allow", "allow")])]
    assert north_stars(runs, set(), expected={"test": 2}) == []
    assert len(north_stars(runs, set(), expected={"test": 1})) == 1
