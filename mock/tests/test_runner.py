import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from mock.server import create_app
from lab.client import WorldClient
from lab.session import LabSession
from lab.run import Runner, resume_session
from lab.policies import role_policy
from lab import tool_functions as functions
from schemas import parse_spec


@pytest.fixture
def session(tmp_path):
    server=TestClient(create_app())
    def transport(request):
        response=server.request(request.method,request.url.path,content=request.content,headers={'content-type':'application/json'})
        return httpx.Response(response.status_code,json=response.json())
    client=WorldClient('http://fixture',transport=httpx.MockTransport(transport))
    value=LabSession(client,'mock-dev',runs_root=tmp_path)
    functions.configure(value,seed=1000)
    return value


def options():
    return SimpleNamespace(schedule=None,no_tracing=True,resume=False,auto_approve=True,
                           min_experiments=0,model='sonnet',analyst_model=None,token_cap=2000000,cycles=12)


def test_checkpoint_resumes_budget_and_pending_table_without_new_session(session,tmp_path):
    runner=Runner(session,tmp_path,'/tmp',options())
    spec=parse_spec({'type':'drop','sample_id':'ref_100','height_m':1})
    session.preregister(spec,[],spec)
    runner.save()
    saved=json.loads((tmp_path/'checkpoint.json').read_text())
    restored=resume_session(session.client,saved,tmp_path,True)
    assert restored.info.session_id==session.info.session_id
    restored.execute(spec)
    assert restored.budget_left==11
    runner.session=restored; runner.save()
    restored_again=resume_session(session.client,json.loads((tmp_path/'checkpoint.json').read_text()),tmp_path,True)
    assert len(restored_again.results)==1 and restored_again.budget_left==11
    with pytest.raises(ValueError,match='pre-registration'): restored_again.execute(spec)


def test_ambiguous_checkpoint_never_replays(session,tmp_path):
    runner=Runner(session,tmp_path,'/tmp',options())
    runner.inflight={'name':'drop'};runner.save()
    with pytest.raises(RuntimeError,match='Ambiguous'):
        resume_session(session.client,json.loads((tmp_path/'checkpoint.json').read_text()),tmp_path,True)


def test_auto_approval_preserves_envelope_and_minimum(session):
    commit={'law_id':'test','claim':'insufficient_evidence','claims_non_ordinary':False,
            'shots':[{'target_id':target.target_id,'speed_mps':3,'elevation_deg':45} for target in session.info.targets]}
    event={'type':'tool_call','target':'commit_mission','data':{'arguments':{'commit':commit}}}
    assert role_policy('pi')(event)['result']=='ASK'
    assert role_policy('pi',True)(event)['result']=='ALLOW'
    assert role_policy('pi',True,12)(event)['result']=='DENY'
    commit['shots'][0]['speed_mps']=100
    assert role_policy('pi',True)(event)['result']=='DENY'
    for target in ('/admin/score','world.read','calibration.read','eval.read','sim.read','read_file','shell'):
        assert role_policy('single',True)({'type':'tool_call','target':target})['result']=='DENY'


def test_coverage_reports_actual_settings_and_failures(session):
    spec=parse_spec({'type':'drop','sample_id':'ref_100','height_m':1})
    session.preregister(spec,[],spec);session.execute(spec)
    report=functions.coverage()
    assert report['experiments']==1 and report['budget_left']==11
    assert report['by_sample']['ref_100'][0]['height_m']==1
    assert report['by_sample']['ref_020']==[]


def test_only_connect_failures_retry(monkeypatch):
    monkeypatch.setattr('lab.client.time.sleep',lambda _:None)
    from schemas import SessionRequest
    for failure, expected in ((httpx.ConnectError,3),(httpx.ReadTimeout,1)):
        calls=[]
        def fail(request):
            calls.append(request)
            raise failure('test')
        client=WorldClient('http://fixture',transport=httpx.MockTransport(fail))
        with pytest.raises(failure):client.start(SessionRequest(world_id='mock-dev',condition='lab'))
        assert len(calls)==expected


def test_mlflow_span_persists_inputs_and_outputs(tmp_path):
    mlflow=pytest.importorskip('mlflow')
    mlflow.set_tracking_uri(f'sqlite:///{tmp_path}/trace.db')
    experiment=mlflow.set_experiment('verification')
    runner=Runner.__new__(Runner);runner.trace_enabled=True
    with runner.span('verification',{'sample':'public'}) as span: span.set_outputs({'ok':True})
    mlflow.flush_trace_async_logging()
    traces=mlflow.search_traces(experiment_ids=[experiment.experiment_id],return_type='list')
    assert len(traces)==1
    assert traces[0].data.spans[0].inputs=={'sample':'public'}
    assert traces[0].data.spans[0].outputs=={'ok':True}


def test_repeated_model_measurement_returns_memo_without_second_http_run(session,tmp_path,monkeypatch):
    import asyncio
    spec={'type':'drop','sample_id':'ref_100','height_m':1}
    functions.record_candidates([spec,{**spec,'height_m':.5}],spec)
    functions.preregister(spec,[],spec)
    class Executor:
        def __init__(self,**kwargs):pass
        async def close(self):pass
        async def run_turn(self,*args):
            first=await self._tool_executor('drop',{'sample_id':'ref_100','height_m':1})
            duplicate=await self._tool_executor('drop',{'sample_id':'ref_100','height_m':1})
            assert duplicate==first
            yield SimpleNamespace(usage=None)
    monkeypatch.setattr('lab.run.ClaudeSDKExecutor',Executor)
    runner=Runner(session,tmp_path,'/tmp',options())
    asyncio.run(runner.turn('operator','Execute once',1))
    assert len(session.results)==1 and session.budget_left==11
    assert sum(entry['kind']=='result' for entry in runner.entries())==1


def test_fsynced_result_recovers_crash_before_checkpoint_without_reexecution(session,tmp_path):
    spec=parse_spec({'type':'drop','sample_id':'ref_100','height_m':1})
    session.preregister(spec,[],spec)
    runner=Runner(session,tmp_path,'/tmp',options())
    runner.inflight={'name':'drop','key':'acknowledged'};runner.save()
    saved=json.loads((tmp_path/'checkpoint.json').read_text())
    result=session.execute(spec)  # ledger is durable, checkpoint still has the intent
    restored=resume_session(session.client,saved,tmp_path,True)
    assert restored.results==[result] and restored.budget_left==11
    assert restored.pending is None and saved['inflight'] is None
    with pytest.raises(ValueError):restored.execute(spec)


def test_single_condition_ledger_preserves_actual_actor(session,tmp_path):
    single=LabSession(session.client,'mock-dev','single',runs_root=tmp_path/'single')
    spec=parse_spec({'type':'drop','sample_id':'ref_100','height_m':1})
    single.preregister(spec,[],spec);single.execute(spec)
    records=[json.loads(line) for line in single.ledger.path.read_text().splitlines()]
    assert {item['agent'] for item in records}=={'single'}


def test_adopted_session_never_starts_another_and_writes_flat_ledger(session, tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError('must not start another session')
    monkeypatch.setattr(session.client, 'start', forbidden)
    adopted = LabSession(session.client, '', info=session.info, runs_root=tmp_path / 'adopted',
                         flat_ledger=True, approval='auto')
    assert adopted.info == session.info
    assert adopted.ledger.path == tmp_path / 'adopted' / 'ledger.jsonl'
    assert adopted.ledger.path.exists()


def test_result_ledger_failure_blocks_further_mutations(session, monkeypatch):
    spec = parse_spec({'type':'drop','sample_id':'ref_100','height_m':1})
    session.preregister(spec, [], None)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(session.ledger, 'append', fail)
    with pytest.raises(OSError):
        session.execute(spec)
    assert session.budget_left == 11 and session.uncertain
    with pytest.raises(RuntimeError, match='unknown outcome'):
        session.execute(spec)


def test_eval_cli_zero_wall_cap_writes_summary_without_http(session, tmp_path):
    import subprocess
    import sys
    info = tmp_path / 'info.json'
    info.write_text(session.info.model_dump_json())
    output = tmp_path / 'adapter'
    result = subprocess.run([sys.executable, '-m', 'lab.run', '--world-url', 'http://127.0.0.1:1',
                             '--session-info', str(info), '--out', str(output), '--no-tracing',
                             '--max-wall-s', '0', '--approval', 'auto'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['status'] == 'cap_reached'
    assert summary['n_experiments'] == summary['tokens_used'] == 0
    assert summary['session_id'] == session.info.session_id
    assert (output / 'ledger.jsonl').exists()
