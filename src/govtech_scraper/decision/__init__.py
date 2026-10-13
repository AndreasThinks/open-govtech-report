"""Decision-model tagging: score repositories against a fixed COFOG taxonomy.

An experimental alternative to the generative `tagger` package. A decision model (TypeSafe's
Jev, or the open-weight Kev) only returns probabilities over options we give it, so the
vocabulary is a reviewed file (taxonomy.yaml) rather than something the model invents.

Public API:
    load_taxonomy()        — the shipped taxonomy, validated
    run_decision_tagging() — score new or changed repositories and store the results
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..db import Database
from .client import BackendExhaustedError, DecisionClient, HttpSystemOneClient, SpaceClient
from .scorer import DecisionResult, build_state, input_hash, score_repos
from .store import DecisionStore, row_to_repository
from .taxonomy import Taxonomy, load_taxonomy

logger = logging.getLogger(__name__)

__all__ = [
    "BackendExhaustedError",
    "DecisionClient",
    "DecisionResult",
    "DecisionStore",
    "HttpSystemOneClient",
    "RunSummary",
    "SpaceClient",
    "Taxonomy",
    "load_taxonomy",
    "run_decision_tagging",
    "score_repos",
]


@dataclass
class RunSummary:
    scored: int = 0
    failed: int = 0
    skipped_unchanged: int = 0
    skipped_empty: int = 0
    stopped_early: str | None = None
    errors: list[str] = field(default_factory=list)


def _is_empty(repo, readme: str | None) -> bool:
    return not (repo.description or "").strip() and not repo.topics and not readme


async def run_decision_tagging(
    db: Database,
    client: DecisionClient,
    taxonomy: Taxonomy | None = None,
    limit: int | None = None,
    rescore: bool = False,
    batch_size: int = 64,
    max_domains: int = 3,
    progress: Callable[[int], None] | None = None,
    urls: list[str] | None = None,
) -> RunSummary:
    """Score repositories whose text changed since they were last scored with this model and taxonomy."""
    taxonomy = taxonomy or load_taxonomy()
    store = DecisionStore(db)
    summary = RunSummary()
    done = {} if rescore else store.scored_hashes(taxonomy.version, client.model)

    if urls:
        placeholders = ",".join("?" * len(urls))
        rows = db.conn.execute(
            f"SELECT * FROM repositories WHERE html_url IN ({placeholders})", urls
        )
    else:
        rows = db.conn.execute(
            "SELECT * FROM repositories WHERE COALESCE(current, 1) = 1 ORDER BY stars DESC"
        )

    todo = []
    for row in rows:
        repo = row_to_repository(row)
        readme = db.get_readme(repo.html_url)
        if _is_empty(repo, readme):
            summary.skipped_empty += 1
            continue
        if done.get(repo.html_url) == input_hash(build_state(repo, readme), taxonomy):
            summary.skipped_unchanged += 1
            continue
        todo.append((repo, readme))
        if limit and len(todo) >= limit:
            break

    logger.info(
        f"Scoring {len(todo)} repos with {client.model} against taxonomy v{taxonomy.version} "
        f"({summary.skipped_unchanged} unchanged, {summary.skipped_empty} empty skipped)"
    )

    for start in range(0, len(todo), batch_size):
        batch = todo[start : start + batch_size]
        repos = [repo for repo, _ in batch]
        readmes = {repo.html_url: readme for repo, readme in batch if readme}
        try:
            results = await score_repos(repos, client, taxonomy, readmes, max_domains=max_domains)
        except BackendExhaustedError as e:
            summary.stopped_early = str(e)
            logger.error(f"Stopping: {e}. Progress is saved; re-run to resume.")
            break
        store.save(results, taxonomy.version, client.model)
        for r in results:
            if r.error:
                summary.failed += 1
                summary.errors.append(f"{r.html_url}: {r.error}")
            else:
                summary.scored += 1
        if progress:
            progress(len(batch))

    return summary
