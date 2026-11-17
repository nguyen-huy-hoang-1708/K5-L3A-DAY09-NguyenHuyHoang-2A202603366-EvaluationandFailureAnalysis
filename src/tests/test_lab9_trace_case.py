"""Regression test from docs/trace_case.md: seeded_regression C-014@11 (FM-05 pre_deducted).

The agent subtracts the prior 30.000đ ship refund itself (event 5 `proposal` 120000), then the host
subtracts it again: ledger 90.000đ, label 120.000đ, while the reply says 120.000đ.
Run: python -m pytest -q -m student -o addopts= -k "lab9 and trace_case"
"""
import pytest

pytestmark = pytest.mark.student


def _ctx(case):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=case["label"],
                            output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})


@pytest.fixture(scope="module")
def cases():
    from lab9.trace_case import investigate

    return {shape: investigate(shape, "C-014", 11) for shape in ("seeded_regression", "fixed")}


def test_first_wrong_step_is_the_pre_deducted_proposal(cases):
    events = cases["seeded_regression"]["trial"]["events"]
    kinds = [e["kind"] for e in events]
    first = kinds.index("proposal")
    assert kinds[:first] == ["handoff", "investigator", "quote", "refund_history"]
    assert events[2]["payload"]["amount_vnd"] == 150000          # event 3: quote is still right
    assert events[first]["payload"]["amount_vnd"] == 120000       # event 5: 150000 - 30000 by the agent


def test_ledger_double_deducted_and_reply_hides_it(cases):
    from lab9.graders import RefundAmountCorrect, ReplyMatchesLedger

    bad = cases["seeded_regression"]
    assert bad["trial"]["snapshot"]["refund_vnd"] == 90000
    assert RefundAmountCorrect().evaluate(_ctx(bad)).value is False
    assert ReplyMatchesLedger().evaluate(_ctx(bad)).value is False   # reply says 120.000đ


def test_fixed_passes_the_same_trial(cases):
    from lab9.graders import RefundAmountCorrect, ReplyMatchesLedger

    good = cases["fixed"]
    assert "proposal" not in [e["kind"] for e in good["trial"]["events"]]
    assert good["trial"]["snapshot"]["refund_vnd"] == 120000
    assert RefundAmountCorrect().evaluate(_ctx(good)).value is True
    assert ReplyMatchesLedger().evaluate(_ctx(good)).value is True
    assert good["trial"]["lab8_pass"] is True
