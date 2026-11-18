import importlib
from pathlib import Path
from unittest.mock import Mock, call

import pytest


@pytest.mark.parametrize('observed,expected', [([998], (2, 2)), ([914, 1000], (86, 0))])
def test_resumed_source_clock(monkeypatch, observed, expected):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / 'e2e'))
    drill = importlib.import_module('recovery_drill')
    run = drill.Drill(None, None)
    monkeypatch.setattr(drill.time, 'time', lambda: 1000)
    measured = iter(observed)
    run.run = Mock(side_effect=lambda node, cmd: str(next(measured)) if cmd == 'date +%s' else '')
    assert run.align_source_clock() == expected
    updates = [c for c in run.run.call_args_list if c.args[1].startswith('sudo date')]
    assert updates == ([] if len(observed) == 1 else [call('central', 'sudo date -u --set=@1000 >/dev/null')])
