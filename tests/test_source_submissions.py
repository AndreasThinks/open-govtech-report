"""Regression contracts for the reviewed source-expansion data batch."""
from pathlib import Path

from govtech_scraper.accounts import _load_community_submissions
from govtech_scraper.forges import load_forge_hosts
from govtech_scraper.submissions import load_submissions


def test_reviewed_missing_accounts_use_existing_submission_loader():
    rows = _load_community_submissions(set())
    names = {x.username.lower() for x in rows}
    assert {'datagouv', 'gendarmerienationale', 'aphp', 'dcc-bs', 'swisstopo',
            'comune-di-montesilvano', 'nova-scotia-digital-service'} <= names
    assert not {'ckan', 'toferc', 'gbetorre'} & names


def test_expansion_submission_batch_has_no_invalid_entries():
    errors = []
    root = Path(__file__).resolve().parents[1] / 'submissions' / 'pending'
    rows = load_submissions(root, on_invalid=lambda path, errs: errors.append((str(path), errs)))
    assert rows, 'Expansion must add real submission records'
    assert not errors
    assert len({(x.host.lower(), x.owner.lower()) for x in rows}) == len(rows)


def test_reachable_forges_enabled_blocked_and_mirrors_not_enabled():
    hosts = {x.host: x for x in load_forge_hosts()}
    expected = {'code.peren.gouv.fr', 'gitlab.cerema.fr', 'gitlab.has-sante.fr',
                'gitlab-forge.din.developpement-durable.gouv.fr', 'gitlab.osti.gov',
                'gitlab.inria.fr'}
    assert expected <= hosts.keys()
    assert all(not hosts[h].include_personal for h in expected)
    assert not {'koodivaramu.eesti.ee', 'openforge.gov.in', 'git.opencode.swiss',
                'forge.grandlyon.com', 'softwarepublico.gov.br'} & hosts.keys()


def test_submission_validation_has_history_for_base_diff():
    import yaml
    workflow = Path(__file__).resolve().parents[1] / '.github/workflows/validate-submission.yml'
    steps = yaml.safe_load(workflow.read_text())['jobs']['validate']['steps']
    checkout = next(s for s in steps if s.get('uses', '').startswith('actions/checkout@'))
    assert checkout.get('with', {}).get('fetch-depth') == 0


def test_scoped_forge_refresh_preserves_unsubmitted_accounts(tmp_db):
    from govtech_scraper.forges import ForgeHost, _save_host
    from govtech_scraper.models import Repository
    for owner in ['drees_code', 'dreesXcode', 'unsubmitted', 'drees_code/sub']:
        tmp_db.save_repositories([Repository(owner='gitlab.com/' + owner, name='old',
            html_url='https://gitlab.com/' + owner + '/old', country='France')])
    _save_host(tmp_db, ForgeHost(host='gitlab.com', country='France', owners=['drees_code']),
               [], complete=True)
    current = dict(tmp_db.conn.execute('SELECT owner,current FROM repositories'))
    assert current['gitlab.com/unsubmitted'] == 1
    assert current['gitlab.com/dreesXcode'] == 1
    assert current['gitlab.com/drees_code'] == 0
    assert current['gitlab.com/drees_code/sub'] == 0


async def test_missing_submitted_group_preserves_previous_repositories(tmp_db):
    from govtech_scraper.forges import ForgeHost, ForgeNotFound, scrape_forges
    from govtech_scraper.models import Repository
    tmp_db.save_repositories([Repository(owner='gitlab.com/old', name='tool',
        html_url='https://gitlab.com/old/tool', country='France')])
    async def get(url, params):
        raise ForgeNotFound(url)
    result = await scrape_forges(tmp_db,
        [ForgeHost(host='gitlab.com', country='France', owners=['old'])], get=get, details=False)
    assert result[0].missing_owners == ['old']
    assert tmp_db.conn.execute('SELECT current FROM repositories').fetchone()[0] == 1
