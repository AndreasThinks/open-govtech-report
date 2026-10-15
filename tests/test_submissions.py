"""Tests for community submissions of GitHub and other-forge accounts."""

import urllib.error

from govtech_scraper.submissions import (
    Submission,
    check_online,
    country_label,
    load_submissions,
    parse,
    validate,
)

BASE = {"country": "NL", "account_type": "org", "description": "A real body",
        "evidence": ["https://www.gov.nl/about"]}


def test_github_file_without_host_is_still_valid():
    data = {"org": "alphagov", **BASE, "country": "GB"}
    assert validate(data, "gb-alphagov.yaml") == []
    sub = parse(data)
    assert (sub.host, sub.owner, sub.country, sub.is_github) == ("github.com", "alphagov",
                                                                "U.K. Central", True)


def test_forge_file_needs_host_owner_and_platform():
    data = {"host": "gitlab.com", "owner": "commonground", **BASE}
    assert "Missing required field: 'platform'" in validate(data)
    data["platform"] = "gitlab"
    assert validate(data, "nl-commonground.yaml") == []
    sub = parse(data)
    assert (sub.host, sub.platform, sub.owner, sub.country) == (
        "gitlab.com", "gitlab", "commonground", "Netherlands")


def test_forge_field_checks():
    data = {"host": "Not a host", "owner": "bad owner!", "platform": "bitbucket",
            "same_as_github": "no spaces please", **BASE}
    errors = " ".join(validate(data))
    for field in ("'host'", "'platform'", "'owner'", "'same_as_github'"):
        assert field in errors


def test_nested_gitlab_group_and_labels():
    data = {"host": "gitlab.com", "owner": "gov/sub-team", "platform": "gitlab", **BASE,
            "country": "U.K. Councils"}
    assert validate(data) == []
    assert parse(data).country == "U.K. Councils"


def test_country_codes_map_to_registry_labels():
    assert country_label("de") == "Germany"
    assert country_label("US") == "U.S. Federal"
    assert country_label("Greece") == "Greece"
    assert country_label("ZZ") is None
    assert any("country" in e for e in validate({"org": "x", **BASE, "country": "ZZ"}))


def test_evidence_and_filename_rules():
    assert any("evidence" in e for e in validate({"org": "x", **BASE, "evidence": ["http://x"]}))
    assert any("Filename" in e for e in validate({"org": "x", **BASE}, "NL Bad.yaml"))


def test_load_skips_invalid_files(tmp_path):
    (tmp_path / "ok.yaml").write_text(
        "org: alphagov\ncountry: GB\naccount_type: org\ndescription: GDS\n"
        "evidence: [https://www.gov.uk/government/organisations/government-digital-service]\n")
    (tmp_path / "bad.yaml").write_text("org: [\n")
    seen = []
    subs = load_submissions(tmp_path, on_invalid=lambda p, e: seen.append(p.name))
    assert [s.owner for s in subs] == ["alphagov"] and seen == ["bad.yaml"]


def test_check_online_uses_the_right_api():
    urls = []

    def ok(url):
        urls.append(url)
        return {}

    check_online(Submission("codeberg.org", "betagouv", "France", platform="forgejo"), ok)
    check_online(Submission("gitlab.com", "gov/sub", "Netherlands", platform="gitlab"), ok)
    assert urls == ["https://codeberg.org/api/v1/version",
                    "https://codeberg.org/api/v1/users/betagouv",
                    "https://gitlab.com/api/v4/groups/gov%2Fsub?with_projects=false"]


def test_check_online_reports_missing_account():
    def missing(url):
        raise urllib.error.HTTPError(url, 404, "nope", {}, None)

    errors, warnings = check_online(
        Submission("gitlab.com", "nobody", "Netherlands", platform="gitlab"), missing)
    assert errors and "not found" in errors[0] and not warnings


def test_check_online_rate_limit_is_only_a_warning():
    def limited(url):
        raise urllib.error.HTTPError(url, 403, "rate limited", {}, None)

    errors, warnings = check_online(Submission("github.com", "alphagov", "U.K. Central"), limited)
    assert errors == [] and "403" in warnings[0]
