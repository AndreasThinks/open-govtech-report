import importlib.util
from pathlib import Path


def load_monitor():
    path=Path(__file__).resolve().parents[2]/'scripts/watch_publication.py'
    spec=importlib.util.spec_from_file_location('watch_publication',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_monitor_is_silent_when_healthy(tmp_path):
    assert load_monitor().change_report(tmp_path/'state.json',[])==''


def test_monitor_deduplicates_incident_and_reports_recovery(tmp_path):
    module=load_monitor();state=tmp_path/'state.json'
    assert 'needs attention' in module.change_report(state,['Scheduled refresh is disabled.'])
    assert module.change_report(state,['Scheduled refresh is disabled.'])==''
    assert 'recovered' in module.change_report(state,[])
    assert module.change_report(state,[])==''


def test_monitor_reports_disabled_schedule_and_failed_verification(monkeypatch,tmp_path):
    from types import SimpleNamespace
    module=load_monitor()
    responses=iter([
        SimpleNamespace(returncode=1,stdout=''),
        SimpleNamespace(returncode=0,stdout='{"state":"disabled_inactivity"}'),
        SimpleNamespace(returncode=0,stdout='[{"status":"completed","conclusion":"failure","url":"https://github.com/example/run"}]'),
    ])
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw: next(responses))
    issues=module.collect_issues(tmp_path)
    assert len(issues)==3
    assert any('disabled_inactivity' in s for s in issues)
