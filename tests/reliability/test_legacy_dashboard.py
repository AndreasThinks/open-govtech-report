import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from huggingface_hub.errors import EntryNotFoundError
import huggingface_hub


def test_loader_handles_legacy_revision_without_manifest(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[2] / 'web/app.py'
    node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'get_db_path')
    node.decorator_list = []
    fake_db = tmp_path / 'published.db'
    calls=[]
    def download(**kw):
        calls.append(kw)
        if kw['filename']=='data/manifest.json':
            raise EntryNotFoundError('legacy revision has no manifest')
        return str(fake_db)
    monkeypatch.setattr(huggingface_hub,'HfApi',lambda: SimpleNamespace(dataset_info=lambda _: SimpleNamespace(sha='exact-legacy-sha')))
    monkeypatch.setattr(huggingface_hub,'hf_hub_download',download)
    fake_os=SimpleNamespace(path=SimpleNamespace(join=lambda *args: '/absent',dirname=lambda _: '/absent',exists=lambda _: False))
    ns={'os':fake_os,'__file__':str(source),'logger':Mock(),'st':Mock()}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
    assert ns['get_db_path']()==str(fake_db)
    assert all(c['revision']=='exact-legacy-sha' for c in calls)
    ns['st'].warning.assert_called_once()
    ns['st'].stop.assert_not_called()
