#!/usr/bin/env python3
"""Run with: uv run --with playwright python tests/site_browser.py (local mocks only)."""
import copy
import functools
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from playwright.sync_api import sync_playwright, expect

SITE = Path(__file__).resolve().parents[1] / "src/govtech_scraper/site"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Desktop(unittest.TestCase):
    width = 1280

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(SITE)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": self.width, "height": 900})
        self.page = self.context.new_page()
        self.page.evaluate((SITE / "sample.js").read_text())
        # JSON round-trip matches real API serialization (NaN/Infinity become null).
        self.fixtures = self.page.evaluate("""() => JSON.parse(JSON.stringify({overview: OGI_SAMPLE.overview, themes: OGI_SAMPLE.themes,
            theme: OGI_SAMPLE.theme('00'), countries: OGI_SAMPLE.countries,
            repo: OGI_SAMPLE.repo('i-dot-ai', 'consult'), context: OGI_SAMPLE.context('i-dot-ai', 'consult')}))""")
        self.fixtures["repo"]["stars"] = 321
        self.fixtures["repo"]["forks"] = 17
        self.mode = "classified"
        self.context_error = False
        self.missing = False
        self.page.route("**/v1/**", self.api)

    def tearDown(self):
        self.context.close()

    def api(self, route):
        path = route.request.url.split("/v1/", 1)[1].split("?", 1)[0]
        if self.mode == "offline":
            route.abort()
            return
        status = 200
        if path == "overview":
            body = copy.deepcopy(self.fixtures["overview"])
            if self.mode == "unclassified":
                body["classification"]["model"] = None
        elif path == "themes":
            body = self.fixtures["themes"] if self.mode == "classified" else {"model": None, "themes": []}
        elif path.startswith("themes/"):
            body = self.fixtures["theme"]
        elif path == "countries":
            body = self.fixtures["countries"]
        elif path == "repos":
            body = {"total": 1, "items": [self.fixtures["repo"]]}
        elif path.endswith("/context"):
            status = 503 if self.context_error else 404 if self.missing else 200
            body = self.fixtures["context"]
        else:
            status = 404 if self.missing else 200
            body = self.fixtures["repo"]
        route.fulfill(status=status, json=body)

    def go(self, route):
        self.page.goto(self.base + "/#" + route)
        expect(self.page.locator("#app h1")).to_be_visible()

    def layout(self):
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))

    def test_offline_sample(self):
        self.mode = "offline"
        self.go("/repo/i-dot-ai/consult")
        expect(self.page.locator("#banner")).to_contain_text("every figure on this site is an example")
        expect(self.page.locator("#app h1")).to_have_text("i-dot-ai/consult")
        self.layout()

    def test_live_missing_repo(self):
        self.missing = True
        self.go("/repo/i-dot-ai/consult")
        expect(self.page.locator("#app h1")).to_have_text("Not found")
        expect(self.page.locator("#app")).not_to_contain_text("example-gov")
        expect(self.page.locator("#banner")).to_be_hidden()
        self.layout()

    def test_context_error_and_navigation_recovery(self):
        self.context_error = True
        self.go("/repo/i-dot-ai/consult")
        expect(self.page.locator("#app")).to_contain_text("History unavailable")
        expect(self.page.locator("#app")).to_contain_text("Government forks unavailable")
        expect(self.page.locator("#app")).to_contain_text("Similar repositories unavailable")
        expect(self.page.locator("#app")).not_to_contain_text("example-gov")
        stat = self.page.locator(".stat").filter(has_text="government forks")
        expect(stat.locator("b")).to_have_text("Unavailable")
        self.layout()
        self.context_error = False
        self.page.evaluate("location.hash = '#/repos'")
        expect(self.page.locator("#app h1")).to_have_text("Repositories")
        self.page.locator('#app a[href="#/repo/i-dot-ai/consult"]').first.click()
        expect(self.page.locator("#app h1")).to_have_text("i-dot-ai/consult")
        expect(self.page.locator("#app")).not_to_contain_text("History unavailable")
        expect(self.page.locator("#hist polyline").first).to_be_visible()

    def test_unclassified_bookmarked_filters(self):
        self.mode = "unclassified"
        for key, value in [("domain", "00"), ("tag", "ai-and-machine-learning"), ("kind", "tool")]:
            with self.subTest(filter=key):
                self.go(f"/repos?{key}={value}")
                expect(self.page.locator("#app")).to_contain_text("Classification filters are unavailable")
                expect(self.page.locator("#app table")).to_have_count(0)
                expect(self.page.locator('select[name="domain"]')).to_be_disabled()
                expect(self.page.locator('select[name="tag"]')).to_be_disabled()
                self.layout()
                self.page.locator('#ff button[type="submit"]').click()
                expect(self.page.locator("#app")).to_contain_text("321")
                expect(self.page.locator("#app")).not_to_contain_text("Classification filters are unavailable")

    def test_submit_forge_account_and_server(self):
        self.missing = True  # not in the index yet
        self.page.route("https://codeberg.org/**", lambda r: r.fulfill(status=200, json={"login": "betagouv"}))
        self.go("/submit")
        self.page.fill("#suburl", "https://codeberg.org/betagouv")
        expect(self.page.locator("#subdetect")).to_contain_text("Found")
        expect(self.page.locator("#subdetect")).to_contain_text("Forgejo or Gitea")
        expect(self.page.locator("#subtwin")).to_be_visible()
        self.page.fill('input[name="country"]', "fr")
        self.page.fill('input[name="description"]', "beta.gouv.fr, the French state incubator")
        self.page.fill('input[name="same_as_github"]', "betagouv")
        self.page.fill('textarea[name="evidence"]', "https://beta.gouv.fr")
        self.page.locator('#sub button[type="submit"]').click()
        yaml = self.page.locator("#subyaml").input_value()
        for line in ["host: codeberg.org", "platform: forgejo", 'owner: "betagouv"',
                     "same_as_github: betagouv", 'country: "FR"', "  - https://beta.gouv.fr"]:
            self.assertIn(line, yaml)
        href = self.page.locator("#subgo").get_attribute("href")
        self.assertIn("filename=fr-codeberg-betagouv.yaml", href)
        self.layout()
        # A curated government server is already read in full.
        self.page.fill("#suburl", "https://code.overheid.nl/MinBZK")
        expect(self.page.locator("#subdetect")).to_contain_text("already reads in full")
        # An unknown server with no group is a server suggestion, which opens an issue.
        self.page.fill("#suburl", "https://git.example.gov")
        expect(self.page.locator("#subplat")).to_be_visible()
        expect(self.page.locator("#subdetect")).to_contain_text("whole server")
        self.page.locator('#sub button[type="submit"]').click()
        self.assertIn("/issues/new?", self.page.locator("#subgo").get_attribute("href"))

    def test_forge_repo_page(self):
        repo = self.fixtures["repo"]
        repo.update(full_name="gitlab.com/commonground/sub/api",
                    html_url="https://gitlab.com/commonground/sub/api")
        self.mode = "unclassified"
        self.go("/repo/gitlab.com/commonground/sub/api")
        expect(self.page.locator("#app h1")).to_have_text("gitlab.com/commonground/sub/api")
        expect(self.page.locator("#app a.btn").first).to_have_text("View on gitlab.com")
        expect(self.page.locator('#app a[href="#/account/gitlab.com/commonground/sub"]').first).to_be_visible()
        self.layout()

    def test_sample_theme_snippets_labelled_and_links_safe(self):
        self.mode = "unclassified"
        self.go("/theme/00")
        expect(self.page.locator("#app")).to_contain_text("Example repositories")
        self.missing = True
        self.page.locator('#app a[href^="#/repo/"]').first.click()
        expect(self.page.locator("#app h1")).to_have_text("Not found")

    def test_classified_details(self):
        self.go("/repo/i-dot-ai/consult")
        expect(self.page.locator("#banner")).to_be_hidden()
        expect(self.page.locator(".stat").filter(has_text="stars").locator("b")).to_have_text("321")
        expect(self.page.locator(".prob")).to_be_visible()
        expect(self.page.locator("#hist polyline").first).to_be_visible()
        self.layout()

    def test_evidence_uncertainty_is_explicit(self):
        cls = self.fixtures['repo']['classification']
        cls.update(confidence_mode='evidence', tags=[],
                   abstentions={'tags': ['Insufficient source support']})
        self.go('/repo/i-dot-ai/consult')
        expect(self.page.locator('#app')).to_contain_text('Insufficient source support')
        expect(self.page.locator('#app')).to_contain_text('not verified negatives')
        expect(self.page.locator('.prob')).to_have_count(0)
        self.layout()
        self.go('/method')
        expect(self.page.locator('#app')).to_contain_text('at least one domain, kind or tag')
        expect(self.page.locator('#app')).not_to_contain_text('Classification reads metadata only')

    def test_about_funnel_and_repository_walkthrough(self):
        self.fixtures["overview"]["sources"] = [
            {"key": "github", "repos": 18000, "original": 15000},
            {"key": "servers", "repos": 3450, "original": 2300, "hosts": 7},
            {"key": "submitted", "repos": 30, "original": 20}]
        self.go("/about")
        expect(self.page.locator(".funnel svg")).to_be_visible()
        expect(self.page.locator(".funnel")).to_contain_text("Government servers")
        expect(self.page.locator("#journey")).to_contain_text("Follow one repository")
        expect(self.page.locator("#journey .checks li")).to_have_count(4)
        self.layout()

    def test_subscribe_to_theme_and_search(self):
        self.go("/theme/00")
        sub = self.page.locator(".phead details.follow")
        sub.locator("summary").click()
        expect(sub.locator("input")).to_have_value(self.base + "/feeds/theme/00.xml")
        self.layout()
        self.go("/repos?country=U.K.%20Central&tag=payments&sort=stars&maintained=true")
        sub = self.page.locator(".pager details.follow")
        sub.locator("summary").click()
        expect(sub.locator("input")).to_have_value(self.base + "/feeds/repos.xml?country=U.K.+Central&tag=payments")
        self.layout()

    def test_healthy_empty_context(self):
        self.fixtures["context"] = {"history": [], "reused_by": [], "similar": []}
        self.go("/repo/i-dot-ai/consult")
        expect(self.page.locator("#app")).to_contain_text("No government forks yet.")
        expect(self.page.locator("#app")).not_to_contain_text("unavailable")


class Mobile(Desktop):
    width = 390


if __name__ == "__main__":
    unittest.main(verbosity=2)
