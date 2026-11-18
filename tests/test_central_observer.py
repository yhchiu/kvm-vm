import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location('observer', Path(__file__).resolve().parents[1] /
                                              'tools/monitoring/watch-central.py')
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


def test_outage_and_recovery_survive_webhook_failure_and_restart(tmp_path):
    health, webhook, state = [tmp_path / n for n in ('health', 'webhook', 'state.json')]
    health.write_text('https://central.test/health')
    webhook.write_text('https://receiver.test/notify')
    opener = Mock()
    def ok(body):
        response = io.BytesIO(body)
        response.status = 200
        return response
    opener.open.side_effect = [OSError(), OSError(), OSError(), ok(b'OK'), ok(b''), ok(b'')]
    assert observer.check_once(opener,health,webhook,state,2,0) == 1
    assert not state.exists()
    assert observer.check_once(opener,health,webhook,state,2,1) == 2
    pending = json.loads(state.read_text())['pending'][0]
    assert pending['status'] == 'firing'
    assert observer.check_once(opener,health,webhook,state,2,0) == 0
    delivered = [json.loads(call.args[0].data) for call in opener.open.call_args_list[-2:]]
    assert delivered[0]['id'] == pending['id']
    assert [r['status'] for r in delivered] == ['firing','resolved']
    assert json.loads(state.read_text()) == {'healthy':True,'pending':[]}
