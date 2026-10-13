from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pipeline_frozen_serialized_and_tested():
    workflow = (ROOT / '.github/workflows/scrape-and-publish.yml').read_text()
    assert 'cancel-in-progress: false' in workflow
    assert 'uv sync --frozen' in workflow
    assert 'pip install' not in workflow
    assert 'except RuntimeError' not in workflow
    assert workflow.index('pytest') < workflow.index('scripts/publish_hf.py')
    assert 'scripts/prepare_publication.py' in workflow
    assert 'publication/' in workflow


def test_dashboard_deploy_is_gated():
    workflow = (ROOT / '.github/workflows/deploy-dashboard.yml').read_text()
    assert 'uv run --frozen pytest' in workflow
    assert 'pip install' not in workflow
    assert 'queries.py' in workflow


def test_required_dependencies_are_locked():
    import tomllib
    from packaging.requirements import Requirement
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    lock = tomllib.loads((ROOT / 'uv.lock').read_text())
    root = next(p for p in lock['package'] if p['name'] == project['project']['name'])
    locked = {p['name'] for p in root['dependencies']}
    required = {Requirement(p).name.lower().replace('_', '-') for p in project['project']['dependencies']}
    assert required <= locked, f'Regenerate uv.lock; missing runtime dependencies: {required-locked}'
