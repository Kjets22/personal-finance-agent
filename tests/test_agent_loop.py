import json

from finance_agent.agent import MAX_STEPS, build_prompt, parse_action
from finance_agent.llm import GarbageClient
from finance_agent.pipeline import prepare_state, run
from tests import fakes

REQUIRED_KEYS = {"totals", "by_category", "flagged", "transactions", "summary"}


def tools_called(report):
    return [s["tool"] for s in report["agent_trace"]]


def test_happy_path(data_dir, out_path):
    report = run(data_dir, out_path, fakes.perfect_agent())
    assert report["status"] == "ok"
    assert tools_called(report) == ["list_uncategorized", "categorize_batch", "list_uncategorized",
                                    "categorize_batch", "compute_totals", "write_report"]
    assert all(s["ok"] for s in report["agent_trace"])
    assert report["warnings"] == []
    assert json.loads(out_path.read_text()) == report


def test_garbage_model_still_writes_degraded_report(data_dir, out_path):
    report = run(data_dir, out_path, GarbageClient())
    assert out_path.exists()
    assert REQUIRED_KEYS <= set(report)
    assert report["status"] == "degraded"
    assert len(report["agent_trace"]) == 3
    assert not any(s["ok"] for s in report["agent_trace"])
    assert "model_reply" in report["agent_trace"][0]
    assert sum(t["category"] == "Uncategorized" for t in report["transactions"]) == 16
    assert any("fallback" in w for w in report["warnings"])
    assert report["llm"]["summary_source"] == "template"


def test_fenced_chatty_model_works(data_dir, out_path):
    assert run(data_dir, out_path, fakes.fenced_chatty_agent())["status"] == "ok"


def test_wrong_categories_become_uncategorized_but_run_finishes(data_dir, out_path):
    report = run(data_dir, out_path, fakes.wrong_category_agent())
    assert report["status"] == "degraded"  # agent finished, but rows fell back
    assert tools_called(report)[-1] == "write_report"
    assert report["by_category"]["Uncategorized"] > 0


def test_hallucinated_ids_are_recovered_by_retry(data_dir, out_path):
    report = run(data_dir, out_path, fakes.hallucinating_id_agent())
    assert report["status"] == "ok"
    assert any("t999" in w for w in report["warnings"])


def test_premature_write_report_is_an_observation(data_dir, out_path):
    report = run(data_dir, out_path, fakes.impatient_agent())
    first = report["agent_trace"][0]
    assert first["tool"] == "write_report" and not first["ok"]
    assert "still need a category" in first["observation"]["error"]
    assert report["status"] == "ok"


def test_stall_is_detected(data_dir, out_path):
    report = run(data_dir, out_path, fakes.looping_agent())
    assert tools_called(report) == ["compute_totals"] * 3
    assert report["status"] == "degraded"
    assert any("repeated" in w for w in report["warnings"])


def test_unknown_tool_is_an_observation(data_dir, out_path):
    report = run(data_dir, out_path, fakes.unknown_tool_agent())
    assert "unknown tool" in report["agent_trace"][0]["observation"]["error"]
    assert report["status"] == "degraded"


def test_model_down_still_writes_report(data_dir, out_path):
    report = run(data_dir, out_path, fakes.offline_model_agent())
    assert report["status"] == "degraded"
    assert "model call failed" in report["agent_trace"][0]["observation"]["error"]


def test_step_limit(data_dir, out_path):
    """Alternating valid calls never trip the failure or stall guards; the step limit must."""
    counter = {"n": 0}

    def controller(prompt):
        counter["n"] += 1
        return fakes.action("list_uncategorized", limit=1 + counter["n"] % 2)

    from finance_agent.llm import ScriptedClient
    report = run(data_dir, out_path, ScriptedClient(fakes.routed(controller)))
    assert len(report["agent_trace"]) == MAX_STEPS
    assert any("step limit" in w for w in report["warnings"])


def test_bad_args_are_observations(data_dir, out_path):
    replies = iter([
        fakes.action("list_uncategorized", limit="8"),
        fakes.action("categorize_batch", ids=["nope"]),
        fakes.action("categorize_batch", ids=[f"t{i:03d}" for i in range(1, 10)]),
    ])
    from finance_agent.llm import ScriptedClient
    report = run(data_dir, out_path, ScriptedClient(fakes.routed(lambda p: next(replies))))
    errors = [s["observation"]["error"] for s in report["agent_trace"]]
    assert "limit must be" in errors[0]
    assert "unknown ids" in errors[1]
    assert "1-8" in errors[2]


def test_parse_action():
    assert parse_action('```json\n{"tool": "compute_totals"}\n```') == ("compute_totals", {})
    for bad in ['{"args": {}}', '{"tool": "x", "args": [1]}', "hello"]:
        try:
            parse_action(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_prompt_is_deterministic(data_dir, out_path):
    a = build_prompt(prepare_state(data_dir, out_path, fakes.perfect_agent()), [])
    b = build_prompt(prepare_state(data_dir, out_path, fakes.perfect_agent()), [])
    assert a == b
