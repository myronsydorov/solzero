"""Host-only exact-observation replay for a mock started without admin access."""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from mock.server import create_app

root = Path('runs/session-mock-1000')
entries = [json.loads(line) for line in (root/'s_mock_0001/ledger.jsonl').read_text().splitlines()]
client = TestClient(create_app(seed=1000, admin_token='replay-only'))
sid = client.post('/session',json={'world_id':'mock-dev','condition':'random'}).json()['session_id']
matched = 0
for entry in entries:
    if entry['kind'] != 'result':
        continue
    original = entry['payload']
    result = client.post('/experiment',json={'session_id':sid,'spec':original['spec']}).json()
    assert result['observables'] == original['observables'] and result['budget_left']==original['budget_left']
    matched += 1
commit=json.loads((root/'pending-commit.json').read_text())['commit']
response=client.post('/commit',json={'session_id':sid,**commit})
response.raise_for_status()
score=client.get(f'/admin/score/{sid}',headers={'Authorization':'Bearer replay-only'}).json()
report={'source':'deterministic replay; original admin endpoint disabled','matched_observations':matched,'score':score}
(root/'admin-score-replay.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'matched_observations':matched,'mission':score['mission']},indent=2))
