"""Report every planned dev condition, retaining interrupted and unrun cells."""
import argparse
import json
from pathlib import Path


def report(root):
    manifest=json.loads((root/'manifest.json').read_text())
    selection=json.loads((root/'selection-admin.json').read_text())
    families={item['seed']:family for family,items in selection.items() for item in items}
    rows=[]
    for item in manifest:
        for condition in ('lab','random','single'):
            directory=root/f"{condition}-{item['seed']}"
            path=directory/'summary.json'
            result=json.loads(path.read_text()) if path.exists() else {'status':'not_run_provider_limit','cycles':0}
            usage=result.get('usage',{})
            row={'seed':item['seed'],'family':families[item['seed']],'world':item['world'],'condition':condition,**result}
            row['tokens_including_cache']=sum(usage.get(key,0) for key in ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens')) if usage else None
            row['usage_complete']=result.get('usage_complete',False)
            score_path=root/f"score-{condition}-{item['seed']}.json"
            if score_path.exists():
                score=json.loads(score_path.read_text());shots=(score.get('commit') or {}).get('shots',[])
                row['hits']=sum(shot['hit'] for shot in shots) if shots else None
                row['shots']=len(shots)
            rows.append(row)
    complete=all(item['status']=='committed' and item['cycles']==12 for item in rows)
    summary={'scientific_freeze_ready':complete,'planned_sessions':len(rows),'completed_sessions':sum(item['status']=='committed' for item in rows),'sessions':rows}
    (root/'all-conditions-report.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines=['# Dev comparison status','',f"Completed {summary['completed_sessions']} of {len(rows)} planned sessions. Scientific freeze ready: {complete}.",'',
           '| Seed | Family | Condition | Status | Experiments | Seconds | Reported tokens incl. cache |',
           '| --- | --- | --- | --- | ---: | ---: | ---: |']
    for row in rows:
        seconds=f"{row['wall_seconds']:.1f}" if 'wall_seconds' in row else '—'
        lines.append(f"| {row['seed']} | {row['family']} | {row['condition']} | {row['status']} | {row['cycles']} | {seconds} | {row['tokens_including_cache'] or '—'} |")
    lines += ['', 'Token counts on interrupted turns may be partial. Unrun cells are not failures or zero-hit outcomes. No comparative scientific conclusion is supported by this incomplete matrix.']
    (root/'all-conditions-report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({key:value for key,value in summary.items() if key!='sessions'}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    report(parser.parse_args().directory)
