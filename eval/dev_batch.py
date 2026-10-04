"""Host-only dev manifest, bounded process fan-out and admin score collection."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

import httpx
from eval.sampler import random_specs


def prepare(root, base_url, token_file):
    headers={'X-Admin-Token':token_file.read_text().strip()}
    with httpx.Client(base_url=base_url,headers=headers,timeout=60,trust_env=False) as client:
        response=client.get('/admin/worlds'); response.raise_for_status()
        groups={}
        for seed,world in sorted(response.json().items(),key=lambda item:int(item[0])):
            seed=int(seed)
            if not 1000<=seed<=1999: continue
            response=client.get('/admin/truth/'+world); response.raise_for_status()
            family=response.json()['family']
            if len(groups.get(family,[]))<2: groups.setdefault(family,[]).append({'seed':seed,'world':world})
            if len(groups)==4 and all(len(value)==2 for value in groups.values()): break
    assert len(groups)==4 and all(len(value)==2 for value in groups.values())
    # Family labels remain in this host-only audit, never in scientific context.
    (root/'selection-admin.json').write_text(json.dumps(groups,indent=2)+'\n')
    worlds=sorted([item for group in groups.values() for item in group],key=lambda item:item['seed'])
    for item in worlds:
        schedule=[item.model_dump(mode='json') for item in random_specs(item['seed'], 12)]
        path=root/f"schedule-{item['seed']}.json"
        path.write_text(json.dumps(schedule,indent=2)+'\n')
        item['schedule']=str(path.resolve())
    (root/'manifest.json').write_text(json.dumps(worlds,indent=2)+'\n')
    return worlds


async def batch(options):
    root=Path(options.output).resolve(); root.mkdir(parents=True,exist_ok=True)
    manifest=root/'manifest.json'
    worlds=json.loads(manifest.read_text()) if manifest.exists() else prepare(root,options.base_url,Path(options.token_file))
    semaphore=asyncio.Semaphore(options.jobs)
    async def run(item,condition):
        async with semaphore:
            directory=root/f"{condition}-{item['seed']}"
            if directory.exists() and not options.resume: raise FileExistsError(f'Refusing overwrite: {directory}')
            command=[sys.executable,'-m','lab.run','--world',item['world'],'--condition',condition,
                     '--seed',str(item['seed']),'--output',str(directory),'--auto-approve',
                     '--min-experiments',str(options.min_experiments),'--model',options.model,
                     '--token-cap',str(options.token_cap)]
            if condition=='random': command += ['--schedule',item['schedule']]
            if directory.exists(): command += ['--resume']
            environment=dict(os.environ,SOLZERO_WORLD_URL=options.base_url)
            environment.pop('SOLZERO_ADMIN_TOKEN',None)
            started=time.monotonic()
            with (root/f"{condition}-{item['seed']}.log").open('w') as log:
                process=await asyncio.create_subprocess_exec(*command,stdout=log,stderr=asyncio.subprocess.STDOUT,env=environment,start_new_session=True)
                try:
                    await asyncio.wait_for(process.wait(),options.timeout)
                except TimeoutError:
                    os.killpg(process.pid,signal.SIGINT)
                    try: await asyncio.wait_for(process.wait(),20)
                    except TimeoutError:
                        os.killpg(process.pid,signal.SIGKILL); await process.wait()
            summary_path=directory/'summary.json'
            result=json.loads(summary_path.read_text()) if summary_path.exists() else {'status':'failed_before_summary'}
            result.update(seed=item['seed'],condition=condition,exit_code=process.returncode,
                          host_wall_seconds=time.monotonic()-started)
            if 'session_id' in result:
                with httpx.Client(base_url=options.base_url,headers={'X-Admin-Token':Path(options.token_file).read_text().strip()},timeout=120,trust_env=False) as client:
                    response=client.get('/admin/score/'+result['session_id']); response.raise_for_status()
                    score=response.json()
                    (root/f"score-{condition}-{item['seed']}.json").write_text(json.dumps(score,indent=2)+'\n')
                    shots=(score.get('commit') or {}).get('shots',[])
                    result['hits']=sum(shot['hit'] for shot in shots);result['shots']=len(shots)
                    result['targets']=[{key:shot[key] for key in ('target_id','hit','miss_m')} for shot in shots]
            print(json.dumps(result),flush=True)
            return result
    results=await asyncio.gather(*(run(item,condition) for condition in options.conditions for item in worlds))
    (root/('report-'+'-'.join(options.conditions)+'.json')).write_text(json.dumps(results,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--token-file',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--conditions',nargs='+',choices=['lab','random','single'],default=['lab','random','single'])
    parser.add_argument('--jobs',type=int,default=16)
    parser.add_argument('--timeout',type=float,default=1800)
    parser.add_argument('--min-experiments',type=int,default=12)
    parser.add_argument('--model',default='sonnet')
    parser.add_argument('--token-cap',type=int,default=2000000)
    parser.add_argument('--resume',action='store_true')
    options=parser.parse_args()
    if not 1<=options.jobs<=16:parser.error('Use one to sixteen isolated session processes')
    asyncio.run(batch(options))

if __name__=='__main__': main()
