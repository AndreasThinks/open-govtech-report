"""Community submissions of government accounts, on GitHub or another forge.

One YAML file per account lives in submissions/pending/ (merged files are accepted).
A file with only `org:` is a GitHub account, as before. A file with `host:` and
`owner:` is an account on a GitLab or Forgejo/Gitea server, such as gitlab.com or
codeberg.org; `platform:` says which API to read.

Countries are stored as government.github.com registry labels so submitted accounts
group with the registry's. Two-letter ISO codes are accepted and mapped.
"""

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SUBMISSIONS_DIR = REPO_ROOT / "submissions" / "pending"

GITHUB = "github.com"
PLATFORMS = ("gitlab", "forgejo")
ACCOUNT_TYPES = ("org", "user")

# Registry labels for ISO 3166-1 alpha-2 codes. GB and US map to the national label;
# use the label itself ("U.K. Councils", "U.S. City") for other levels.
COUNTRY_CODES = {
    "AR": "Argentina", "AT": "Austria", "AU": "Australia", "BE": "Belgium", "BG": "Bulgaria",
    "BO": "Bolivia", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland", "CL": "Chile",
    "CN": "China", "CO": "Colombia", "CY": "Cyprus", "CZ": "Czech Republic", "DE": "Germany",
    "DK": "Denmark", "EC": "Ecuador", "EE": "Estonia", "EG": "Egypt", "ES": "Spain",
    "ET": "Ethiopia", "EU": "European Union", "FI": "Finland", "FR": "France",
    "GB": "U.K. Central", "GR": "Greece", "GT": "Guatemala", "HK": "Hong Kong", "HR": "Croatia",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IM": "Isle of Man",
    "IN": "India", "IS": "Iceland", "IT": "Italy", "JE": "Jersey", "JP": "Japan",
    "KH": "Cambodia", "KR": "Republic of Korea", "LT": "Lithuania", "LU": "Luxemburg",
    "LV": "Latvia", "MT": "Malta", "MU": "Mauritius", "MX": "Mexico", "MY": "Malaysia",
    "NL": "Netherlands", "NO": "Norway", "NP": "Nepal", "NZ": "New Zealand", "PA": "Panama",
    "PE": "Peru", "PF": "French Polynesia", "PH": "Philippines", "PL": "Poland",
    "PT": "Portugal", "PY": "Paraguay", "RO": "Romania", "RU": "Russia", "SA": "Saudi Arabia",
    "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia", "SK": "Slovakia", "TH": "Thailand",
    "TW": "Taiwan", "UA": "Ukraine", "US": "U.S. Federal", "VE": "Venezuela", "XK": "Kosovo",
    "ZA": "South Africa",
}

GITHUB_LOGIN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,37}[a-zA-Z0-9])?$")
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
# A GitLab group path (may nest) or a Forgejo owner: letters, digits, . _ - and /.
OWNER_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.\-]*(/[A-Za-z0-9_.][A-Za-z0-9_.\-]*)*$")
FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.yaml$")
PLACEHOLDER_DESCRIPTIONS = {"", "Short description of the organisation", "One plain sentence"}


@dataclass
class Submission:
    host: str  # "github.com" for GitHub accounts
    owner: str
    country: str  # registry label
    account_type: str = "org"
    platform: str = "github"
    same_as_github: str | None = None
    file: str = ""

    @property
    def is_github(self) -> bool:
        return self.host == GITHUB


def country_label(value: object) -> str | None:
    """Registry label for an ISO code or a label; None if a code is unknown."""
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z]{2}", text):
        return COUNTRY_CODES.get(text.upper())
    return text if len(text) >= 3 else None


def validate(data: object, filename: str = "") -> list[str]:
    """Errors in one submission (empty list = valid). Offline: no network calls."""
    if not isinstance(data, dict):
        return ["File must contain a YAML mapping (key: value pairs)"]
    errors = []
    is_github = "host" not in data or str(data.get("host") or "").strip().lower() == GITHUB
    required = ["country", "account_type", "description", "evidence"]
    required += ["org"] if is_github and "owner" not in data else ["owner"]
    if not is_github:
        required += ["platform"]
    for name in required:
        if data.get(name) in (None, ""):
            errors.append(f"Missing required field: '{name}'")
    if errors:
        return errors

    if is_github:
        login = str(data.get("org") or data.get("owner")).strip()
        if not GITHUB_LOGIN_RE.match(login):
            errors.append(f"'{login}' doesn't look like a GitHub login "
                          "(letters, digits and hyphens, no leading or trailing hyphen, max 39)")
    else:
        host = str(data["host"]).strip().lower()
        if not HOST_RE.match(host):
            errors.append(f"'host' value '{host}' should be a server name such as gitlab.com")
        if str(data["platform"]).strip().lower() not in PLATFORMS:
            errors.append(f"'platform' must be one of {', '.join(PLATFORMS)}")
        owner = str(data["owner"]).strip().strip("/")
        if not OWNER_RE.match(owner):
            errors.append(f"'owner' value '{owner}' should be the group or organisation path, "
                          "such as commonground or my-group/sub-group")
        twin = data.get("same_as_github")
        if twin and not GITHUB_LOGIN_RE.match(str(twin).strip()):
            errors.append("'same_as_github' should be a GitHub login, or left empty")

    if country_label(data["country"]) is None:
        errors.append(f"'country' value '{data['country']}' is not a known two-letter code; "
                      "write the country name instead (for example Greece)")
    if data["account_type"] not in ACCOUNT_TYPES:
        errors.append(f"'account_type' must be 'org' or 'user', got '{data['account_type']}'")
    if str(data["description"]).strip() in PLACEHOLDER_DESCRIPTIONS:
        errors.append("'description' must be a non-empty, non-placeholder string")
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not any(
        isinstance(e, str) and e.strip().startswith("https://") and "example" not in e
        for e in evidence
    ):
        errors.append("'evidence' must list at least one real https:// link confirming "
                      "government status")
    if filename and not FILENAME_RE.match(filename):
        errors.append(f"Filename '{filename}' should be lower case letters, digits and "
                      "hyphens ending in .yaml (for example nl-commonground.yaml)")
    return errors


def parse(data: dict, filename: str = "") -> Submission:
    """A validated mapping as a Submission."""
    host = str(data.get("host") or GITHUB).strip().lower()
    if host == GITHUB:
        owner = str(data.get("org") or data.get("owner")).strip()
        platform = "github"
    else:
        owner = str(data["owner"]).strip().strip("/")
        platform = str(data["platform"]).strip().lower()
    twin = str(data.get("same_as_github") or "").strip() or None
    return Submission(host=host, owner=owner, country=country_label(data["country"]),
                      account_type=str(data.get("account_type", "org")).strip(),
                      platform=platform, same_as_github=twin, file=filename)


def validate_file(path: Path) -> list[str]:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]
    return validate(data, Path(path).name)


def load_submissions(directory: Path = SUBMISSIONS_DIR,
                     on_invalid=None) -> list[Submission]:
    """Every valid submission in the folder; invalid files are reported and skipped."""
    found = []
    for path in sorted(Path(directory).glob("*.yaml")):
        errors = validate_file(path)
        if errors:
            if on_invalid:
                on_invalid(path, errors)
            continue
        found.append(parse(yaml.safe_load(path.read_text()), path.name))
    return found


def _get_json(url: str, timeout: float = 20) -> object:
    headers = {"User-Agent": "open-govtech-index submission check"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def check_online(sub: Submission, get_json=_get_json) -> tuple[list[str], list[str]]:
    """(errors, warnings) from asking the platform's API whether the account exists.

    Only a 404 is an error. A server that is down or rate limits the check is a warning:
    the reviewer checks the account by hand anyway.
    """
    try:
        if sub.is_github:
            get_json(f"https://api.github.com/users/{quote(sub.owner)}")
        elif sub.platform == "forgejo":
            get_json(f"https://{sub.host}/api/v1/version")
            get_json(f"https://{sub.host}/api/v1/users/{quote(sub.owner, safe='')}")
        else:
            path = quote(sub.owner, safe="")
            try:
                get_json(f"https://{sub.host}/api/v4/groups/{path}?with_projects=false")
            except urllib.error.HTTPError as e:
                if e.code != 404 or sub.account_type != "user":
                    raise
                if not get_json(f"https://{sub.host}/api/v4/users?username={path}"):
                    raise urllib.error.HTTPError(e.url, 404, "no such user", e.headers, None) from e
    except urllib.error.HTTPError as e:
        if e.code == 404:
            message = (f"{sub.host}/{sub.owner} was not found through the {sub.platform} API; "
                       "check the address, or whether the platform is right")
            return [message], []
        return [], [f"{sub.host} answered HTTP {e.code} when checking {sub.owner}; not checked"]
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        message = f"Could not read the {sub.platform} API on {sub.host} ({type(e).__name__})"
        return [], [message + "; not checked"]
    return [], []
