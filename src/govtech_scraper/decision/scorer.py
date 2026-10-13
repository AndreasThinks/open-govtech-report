"""Score repositories against the fixed taxonomy with a decision model.

Two requests per repository, both batched across repos:

1. Domain (COFOG division), kind of software, and whether the repo is substantive.
2. A yes/no question for every tag in the most likely domains, plus the cross-government
   capability tags, which apply whatever the domain.

The model never writes text, so there is nothing to parse or deduplicate: every answer is
a probability over options we supplied.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field

from ..models import Repository
from .client import DecisionClient
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

CAPABILITIES_DOMAIN_ID = "00"
README_CHAR_BUDGET = 3000

DOMAIN_INSTRUCTIONS = (
    "Which area of government does this repository mainly serve? Choose "
    "'Cross-government capabilities' when it is a shared building block not tied to one policy area."
)
KIND_INSTRUCTIONS = "What kind of repository is this?"
SUBSTANTIVE_INSTRUCTIONS = (
    "Is this a substantive software, data or documentation project, rather than an empty, "
    "placeholder, personal test or training-exercise repository?"
)


@dataclass
class DecisionResult:
    """Everything the model said about one repository."""

    html_url: str
    input_hash: str
    domain: str | None = None
    domain_probabilities: dict[str, float] = field(default_factory=dict)
    kind: str | None = None
    kind_probabilities: dict[str, float] = field(default_factory=dict)
    substantive: float | None = None
    tag_probabilities: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    def tags(self, threshold: float = 0.5) -> list[str]:
        ranked = sorted(self.tag_probabilities.items(), key=lambda kv: -kv[1])
        return [tag for tag, p in ranked if p >= threshold]

    def unclassified(self, threshold: float = 0.5) -> bool:
        return self.error is None and not self.tags(threshold)


def build_state(repo: Repository, readme: str | None = None) -> dict:
    """The document the model reads. Empty fields are left out."""
    state: dict[str, str] = {"repository": f"{repo.owner}/{repo.name}"}
    if repo.country:
        state["government"] = repo.country
    if repo.description:
        state["description"] = repo.description.strip()
    if repo.language:
        state["main language"] = repo.language
    topics = repo.topics if isinstance(repo.topics, list) else []
    if topics:
        state["topics"] = ", ".join(topics)
    if repo.fork:
        state["fork of"] = repo.fork_source or "unknown"
    if repo.archived:
        state["status"] = "archived"
    if readme:
        text = readme.strip()
        state["readme"] = text[:README_CHAR_BUDGET] + (
            " [truncated]" if len(text) > README_CHAR_BUDGET else ""
        )
    return state


def input_hash(state: dict, taxonomy: Taxonomy) -> str:
    """Changes when the repo's text or the taxonomy version changes, so only those get rescored."""
    payload = json.dumps({"state": state, "taxonomy": taxonomy.version}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def stage1_request(state: dict, taxonomy: Taxonomy) -> dict:
    return {
        "state": state,
        "questions": {
            "domain": {
                "type": "choice",
                "instructions": DOMAIN_INSTRUCTIONS,
                "criteria": {d.name: d.definition for d in taxonomy.domains},
            },
            "kind": {
                "type": "choice",
                "instructions": KIND_INSTRUCTIONS,
                "criteria": {k.id: k.definition for k in taxonomy.kinds},
            },
            "substantive": {"type": "noul", "instructions": SUBSTANTIVE_INSTRUCTIONS},
        },
    }


def candidate_domains(
    domain_probabilities: dict[str, float],
    max_domains: int = 3,
    floor: float = 0.15,
) -> list[str]:
    """The most likely domains (always at least one) whose tags are worth asking about."""
    ranked = sorted(domain_probabilities.items(), key=lambda kv: -kv[1])
    picked = [d for i, (d, p) in enumerate(ranked) if i == 0 or p >= floor]
    return picked[: max(1, max_domains)]


def stage2_request(state: dict, taxonomy: Taxonomy, domain_ids: list[str]) -> dict:
    wanted = set(domain_ids) | {CAPABILITIES_DOMAIN_ID}
    questions = {}
    for domain in taxonomy.domains:
        if domain.id not in wanted:
            continue
        for tag in domain.tags:
            questions[f"tag:{tag.id}"] = {
                "type": "noul",
                "instructions": (
                    f"Topic '{tag.id.replace('-', ' ')}': {tag.definition} "
                    "Is this topic a central part of what the repository does?"
                ),
            }
    return {"state": state, "questions": questions}


def _answer(response: dict, question_id: str) -> dict:
    answer = (response.get("answers") or {}).get(question_id)
    if not isinstance(answer, dict):
        raise ValueError(f"response has no answer for '{question_id}'")
    return answer


def apply_stage1(result: DecisionResult, response: dict, taxonomy: Taxonomy) -> None:
    if "error" in response:
        result.error = f"stage 1: {response['error']}"
        return
    try:
        by_name = {d.name: d.id for d in taxonomy.domains}
        domain = _answer(response, "domain")
        result.domain_probabilities = {
            by_name[name]: float(p)
            for name, p in domain["probabilities"].items()
            if name in by_name
        }
        result.domain = by_name.get(domain["choice"])
        kind = _answer(response, "kind")
        result.kind_probabilities = {k: float(p) for k, p in kind["probabilities"].items()}
        result.kind = kind["choice"]
        result.substantive = float(_answer(response, "substantive")["noul"])
    except (KeyError, TypeError, ValueError) as e:
        result.error = f"stage 1: malformed response ({e})"


def apply_stage2(result: DecisionResult, response: dict) -> None:
    if "error" in response:
        result.error = f"stage 2: {response['error']}"
        return
    try:
        for question_id, answer in (response.get("answers") or {}).items():
            if question_id.startswith("tag:"):
                result.tag_probabilities[question_id[4:]] = float(answer["noul"])
    except (KeyError, TypeError, ValueError) as e:
        result.error = f"stage 2: malformed response ({e})"


async def score_repos(
    repos: list[Repository],
    client: DecisionClient,
    taxonomy: Taxonomy,
    readmes: dict[str, str] | None = None,
    max_domains: int = 3,
    domain_floor: float = 0.15,
) -> list[DecisionResult]:
    """Score a batch of repositories. Order of results matches `repos`."""
    readmes = readmes or {}
    states = [build_state(r, readmes.get(r.html_url)) for r in repos]
    results = [DecisionResult(r.html_url, input_hash(s, taxonomy)) for r, s in zip(repos, states)]

    stage1 = await client.decide_many([stage1_request(s, taxonomy) for s in states])
    for result, response in zip(results, stage1):
        apply_stage1(result, response, taxonomy)

    pending = [(result, state) for result, state in zip(results, states) if result.error is None]
    stage2 = await client.decide_many(
        [
            stage2_request(
                state,
                taxonomy,
                (
                    [domain.id for domain in taxonomy.domains]
                    if max_domains >= len(taxonomy.domains)
                    else candidate_domains(result.domain_probabilities, max_domains, domain_floor)
                ),
            )
            for result, state in pending
        ]
    )
    for (result, _), response in zip(pending, stage2):
        apply_stage2(result, response)

    failed = sum(1 for r in results if r.error)
    if failed:
        logger.warning(f"{failed}/{len(results)} repos failed to score in this batch")
    return results
