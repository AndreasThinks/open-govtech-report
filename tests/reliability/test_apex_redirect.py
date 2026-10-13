from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.elements=[]
    def handle_starttag(self,tag,attrs):
        self.elements.append((tag,dict(attrs)))


def test_apex_redirect_has_https_fallback_and_canonical():
    page=Page();page.feed((ROOT/'redirect/index.html').read_text())
    target='https://www.govtech-report.cloud/'
    assert any(t=='meta' and a.get('http-equiv')=='refresh' and a.get('content')=='0;url='+target for t,a in page.elements)
    assert any(t=='link' and a.get('rel')=='canonical' and a.get('href')==target for t,a in page.elements)
    assert any(t=='a' and a.get('href')==target for t,a in page.elements)
    assert not any(t in {'iframe','script'} and 'src' in a for t,a in page.elements)


def test_apex_redirect_pages_workflow_uses_only_redirect_directory():
    source=(ROOT/'.github/workflows/deploy-redirect.yml').read_text()
    assert 'path: redirect' in source
    assert 'actions/deploy-pages@v4' in source
    assert 'pages: write' in source
    assert 'id-token: write' in source
    assert 'cancel-in-progress: false' in source
