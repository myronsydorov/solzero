"""Engineering-only sixteen-process HTTP isolation check; no scientific models."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from schemas import Commit, parse_spec
from lab.client import WorldClient
from lab.session import LabSession


def worker(origin, output):
    with WorldClient(origin) as client:
        session=LabSession(client,'mock-dev','random',runs_root=output,approve_commit=lambda _:True)
        for _ in range(12):
            result=session.execute(parse_spec({'type':'drop','sample_id':'ref_100','height_m':1}))
        assert result.budget_left==0
        response=httpx.post(origin+'/experiment',json={'session_id':session.info.session_id,'spec':result.spec.model_dump(mode='json')},trust_env=False)
        assert response.status_code==409
        session.commit(Commit(law_id='engineering-fixture',claim='insufficient_evidence',claims_non_ordinary=False,
                              shots=[{'target_id':target.target_id,'speed_mps':3,'elevation_deg':45} for target in session.info.targets]))
        return {'session_id':session.info.session_id,'experiments':len(session.results),'budget_left':session.budget_left,'thirteenth_status':response.status_code,'ledger':str(session.ledger.path)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true');parser.add_argument('--origin');parser.add_argument('--output',required=True)
    options=parser.parse_args()
    if options.worker:
        print(json.dumps(worker(options.origin,options.output)));return
    output=Path(options.output).resolve();output.mkdir(parents=True,exist_ok=False)
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    with (output/'server.log').open('w') as stream:
        server=subprocess.Popen([sys.executable,'-m','mock.server','--seed','1000','--port',str(port)],stdout=stream,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    if httpx.get(origin+'/openapi.json',trust_env=False).is_success:break
                except httpx.TransportError:pass
                time.sleep(.1)
            def launch(index):
                result=subprocess.run([sys.executable,'-m','eval.verify_parallel','--worker','--origin',origin,'--output',str(output/str(index))],capture_output=True,text=True)
                (output/f'worker-{index}.log').write_text(result.stdout+result.stderr)
                assert result.returncode==0,result.stderr
                return json.loads(result.stdout)
            started=time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:results=list(pool.map(launch,range(16)))
            assert len({result['session_id'] for result in results})==16
            summary={'engineering_fixture':True,'concurrent_processes':16,'experiments':sum(result['experiments'] for result in results),'wall_seconds':time.monotonic()-started,'sessions':results}
            (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
            print(json.dumps({key:value for key,value in summary.items() if key!='sessions'}))
        finally:server.terminate();server.wait(timeout=10)

if __name__=='__main__':main()
