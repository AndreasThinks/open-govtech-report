"""No remote calls or paid work: disk gates run before opening state."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from govtech_scraper import cloud_worker as module


def test_low_space_rejects_before_opening_master(tmp_path, monkeypatch):
    (tmp_path / 'master.db').write_bytes(b'x' * 100)
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=79 * 1024**2))
    worker = object.__new__(module.Worker)
    def forbidden():
        pytest.fail('opened mutable state before disk preflight')
    worker.state = SimpleNamespace(directory=tmp_path, master=tmp_path/'master.db', lock=nullcontext, open_normal=forbidden)
    worker.clock = lambda: 0
    with pytest.raises(RuntimeError, match='Insufficient free disk'):
        asyncio.run(worker.run(mode='classify'))


def test_budget_scales_with_database_and_wal(tmp_path, monkeypatch):
    (tmp_path/'master.db').write_bytes(b'x'*100)
    (tmp_path/'master.db-wal').write_bytes(b'x'*50)
    required = 4 * 150 + 1024**3
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=required-1))
    with pytest.raises(RuntimeError, match='Insufficient free disk'):
        module.check_disk_headroom(tmp_path)
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=required))
    assert module.check_disk_headroom(tmp_path)['required_bytes'] == required


def test_missing_master_is_not_created(tmp_path, monkeypatch):
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=10*1024**3))
    with pytest.raises(FileNotFoundError):
        module.check_disk_headroom(tmp_path)
    assert not (tmp_path/'master.db').exists()
