"""B3 verifier correctness, isolation, bounded repair and host invariants."""
import json
import time
from pathlib import Path

import pytest

from bonus import swarm_verified as verified
from lab8.db import connect, init_store, load_json, load_jsonl
from lab8.durability import InjectedCrash
from lab8.metrics import trial_pass
from lab8.schemas import Budget
from lab8.shapes.swarm_reference import run_claim as baseline
from lab8.tools import compute_compensation, lookup_order
from lab8.trace import Trace
from tests.conftest import CLAIMS, WORLD, BY_ID

BONUS = Path(__file__).resolve().parents[1]/'bonus'
ADVERSARIAL = load_jsonl(BONUS/'cases.jsonl')
ADV_WORLD = load_json(BONUS/'world.json')


def check(store, claim, seed):
    start = time.time()
    result = verified.run_claim(claim['claim_id'], seed=seed, store_path=str(store))
    conn = connect(store)
    try:
        passed, reason = trial_pass(result.as_dict(), claim['expected'], conn, started_at=start)
    finally:
        conn.close()
    assert passed, reason
    return result


@pytest.mark.parametrize('claim', CLAIMS, ids=lambda c:c['claim_id'])
@pytest.mark.parametrize('seed', [11,22,33,44,55])
def test_verified_public_contract(tmp_path, monkeypatch, claim, seed):
    monkeypatch.chdir(tmp_path)
    store=tmp_path/'public.sqlite'
    init_store(store,WORLD,CLAIMS).close()
    check(store,claim,seed)


@pytest.mark.parametrize('claim', ADVERSARIAL, ids=lambda c:c['claim_id'])
@pytest.mark.parametrize('seed', [11,22,33,44,55])
def test_verified_adversarial_contract(tmp_path,monkeypatch,claim,seed):
    monkeypatch.chdir(tmp_path)
    store=tmp_path/'adversarial.sqlite'
    init_store(store,ADV_WORLD,ADVERSARIAL).close()
    check(store,claim,seed)


def test_verifier_fixes_reference_failure_before_zero_quote_return(public_db,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    wrong=baseline('C-002',seed=55,store_path=str(public_db))
    assert wrong.status=='resolved'
    # Fresh trial: do not let a baseline's incorrect committed effect contaminate bonus.
    init_store(public_db,WORLD,CLAIMS).close()
    result=check(public_db,BY_ID['C-002'],55)
    events=json.loads(Path(result.trace_path).read_text(encoding='utf-8'))['events']
    overrides=[e for e in events if e['kind']=='verifier_override']
    assert len(overrides)==1
    assert overrides[0]['payload']=={'changed_fields':['delay_days'],'repair_round':1}
    assert result.model_calls==wrong.model_calls  # retain peer/parse/draft calls
    assert result.tool_calls==wrong.tool_calls+2  # fresh read and one corrective quote


@pytest.mark.parametrize('field,value',[
    ('delay_days',0), ('status','cancelled'), ('damage_verified',True),
    ('shipping_fee_vnd',99999), ('paid_vnd',1), ('order_id','O-UNTRUSTED'),
])
def test_verifier_checks_every_quote_input_once(public_db,tmp_path,monkeypatch,field,value):
    monkeypatch.chdir(tmp_path)
    conn=connect(public_db)
    try:
        candidate=dict(lookup_order(conn,'O-107','P-100')['order'])
        candidate[field]=value
        candidate_quote=compute_compensation(conn,candidate)
        budget,trace=Budget(),Trace('R-unit-verify')
        fixed,reason=verified.verify_quote(conn,budget,trace,candidate_order=candidate,
            quote_payload=candidate_quote,order_id='O-107',principal_id='P-100')
        assert reason is None and fixed['amount_vnd']==35000
        assert budget.tool_calls==2
        assert [e['kind'] for e in trace.events]==['verifier_check','verifier_override']
        assert trace.events[-1]['payload']=={'changed_fields':[field],'repair_round':1}
        assert conn.execute("SELECT COUNT(*) FROM refund_ledger WHERE claim_id='C-001'").fetchone()[0]==0
    finally:
        conn.close()


def test_matching_candidate_preserves_baseline_quote_without_repair(public_db,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline_result=baseline('C-001',seed=11,store_path=str(public_db))
    init_store(public_db,WORLD,CLAIMS).close()
    result=check(public_db,BY_ID['C-001'],11)
    events=json.loads(Path(result.trace_path).read_text(encoding='utf-8'))['events']
    assert not any(e['kind']=='verifier_override' for e in events)
    assert result.proposal_amount==baseline_result.proposal_amount
    assert result.model_calls==baseline_result.model_calls
    assert result.tool_calls==baseline_result.tool_calls+1


def test_verifier_fresh_scope_failure_never_returns_quote(public_db,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn=connect(public_db)
    try:
        budget,trace=Budget(),Trace('R-unit-scope')
        quote,reason=verified.verify_quote(conn,budget,trace,candidate_order={},
            quote_payload={'amount_vnd':99999},order_id='O-107',principal_id='P-200')
        assert quote is None and reason=='outside_principal_scope'
        assert budget.tool_calls==1
        assert trace.events[-1]['kind']=='verifier_denied'
    finally:
        conn.close()


@pytest.mark.parametrize('point',['before_effect','after_effect_before_checkpoint','after_checkpoint'])
@pytest.mark.parametrize('claim_id',['C-001','C-003','C-014'])
def test_verified_crash_resume_and_retry_are_once(public_db,tmp_path,monkeypatch,point,claim_id):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(InjectedCrash):
        verified.run_claim(claim_id,seed=55,store_path=str(public_db),crash_at=point)
    check(public_db,BY_ID[claim_id],11)
    result=check(public_db,BY_ID[claim_id],55)
    assert len(result.refund_effects)==result.committed_audit_count==1


@pytest.mark.parametrize('approval_id',['A-003',None,'A-DOES-NOT-EXIST'])
def test_verifier_does_not_bypass_host_approval(public_db,tmp_path,monkeypatch,approval_id):
    monkeypatch.chdir(tmp_path)
    conn=connect(public_db)
    try:
        conn.execute("UPDATE claims SET approval_id=? WHERE claim_id='C-001'",(approval_id,))
        conn.commit()
    finally:
        conn.close()
    result=verified.run_claim('C-001',seed=55,store_path=str(public_db))
    assert result.status=='denied' and result.proposal_amount==35000
    assert not result.refund_effects and result.committed_audit_count==0
    conn=connect(public_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE claim_id='C-001' AND event_type='policy_denied'").fetchone()[0]==1
    finally:
        conn.close()
