"""Gold-set evaluation for decision-model tagging.

Workflow:
1. `decide sample` writes a CSV of random repos with empty gold columns.
2. `decide label-gold` optionally pre-fills the gold columns with one strong LLM pass
   (OpenRouter), constrained to taxonomy ids. Spot-check and correct by hand.
3. `decide eval` compares a stored model run against the gold CSV.
"""

import asyncio
import csv
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from ..db import Database
from .scorer import build_state
from .store import DecisionStore, row_to_repository
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

GOLD_COLUMNS = [
    "html_url",
    "repository",
    "description",
    "topics",
    "language",
    "gold_domain",
    "gold_kind",
    "gold_tags",
    "gold_source",
    "notes",
]

THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)


def _split_tags(value: str) -> set[str]:
    return {t.strip() for t in (value or "").replace(",", ";").split(";") if t.strip()}


def write_sample(db: Database, out_path: str | Path, n: int = 300, seed: int = 7) -> int:
    """Write a CSV of `n` random, non-empty current repositories with blank gold columns."""
    rows = db.conn.execute(
        """SELECT * FROM repositories
           WHERE COALESCE(current, 1) = 1
             AND (COALESCE(TRIM(description), '') != '' OR COALESCE(topics, '[]') != '[]')
           ORDER BY html_url"""
    ).fetchall()
    picked = random.Random(seed).sample(rows, min(n, len(rows)))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        writer.writeheader()
        for row in picked:
            repo = row_to_repository(row)
            writer.writerow(
                {
                    "html_url": repo.html_url,
                    "repository": f"{repo.owner}/{repo.name}",
                    "description": repo.description or "",
                    "topics": ", ".join(repo.topics),
                    "language": repo.language or "",
                }
            )
    return len(picked)


def read_gold(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_gold(path: str | Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=GOLD_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------- LLM pre-labelling

LABEL_SYSTEM = """You label government GitHub repositories against a fixed taxonomy.
Pick exactly one domain id, exactly one kind id, and every tag id that is a central part of what
the repository does (zero to five). Use only ids from the lists given. Be conservative: when the
text is too thin to tell, choose fewer tags."""


def _taxonomy_prompt(taxonomy: Taxonomy) -> str:
    lines = ["DOMAINS (id: name - definition)"]
    lines += [f"{d.id}: {d.name} - {d.definition}" for d in taxonomy.domains]
    lines.append("\nKINDS (id - definition)")
    lines += [f"{k.id} - {k.definition}" for k in taxonomy.kinds]
    lines.append("\nTAGS (id [domain] - definition)")
    lines += [f"{t.id} [{t.domain_id}] - {t.definition}" for t in taxonomy.tags]
    return "\n".join(lines)


def _label_schema(taxonomy: Taxonomy) -> dict:
    return {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "enum": [d.id for d in taxonomy.domains]},
            "kind": {"type": "string", "enum": [k.id for k in taxonomy.kinds]},
            "tags": {
                "type": "array",
                "items": {"type": "string", "enum": [t.id for t in taxonomy.tags]},
            },
        },
        "required": ["domain", "kind", "tags"],
        "additionalProperties": False,
    }


async def label_with_llm(
    db: Database,
    rows: list[dict],
    taxonomy: Taxonomy,
    api_key: str,
    model: str,
    base_url: str = "https://openrouter.ai/api/v1",
    concurrency: int = 8,
    overwrite: bool = False,
) -> int:
    """Fill empty gold columns with a strong LLM's labels. Returns how many rows were labelled."""
    system = f"{LABEL_SYSTEM}\n\n{_taxonomy_prompt(taxonomy)}"
    schema = _label_schema(taxonomy)
    semaphore = asyncio.Semaphore(concurrency)
    labelled = 0

    async def one(session: aiohttp.ClientSession, row: dict) -> None:
        nonlocal labelled
        if row.get("gold_domain") and not overwrite:
            return
        repo_row = db.conn.execute(
            "SELECT * FROM repositories WHERE html_url=?", (row["html_url"],)
        ).fetchone()
        if repo_row is None:
            return
        state = build_state(row_to_repository(repo_row), db.get_readme(row["html_url"]))
        body = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(state, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "gold_label", "strict": True, "schema": schema},
            },
        }
        async with semaphore:
            for attempt in range(3):
                try:
                    async with session.post(f"{base_url}/chat/completions", json=body) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                        data = await resp.json()
                    label = json.loads(data["choices"][0]["message"]["content"])
                    row["gold_domain"] = label["domain"]
                    row["gold_kind"] = label["kind"]
                    row["gold_tags"] = ";".join(label["tags"])
                    row["gold_source"] = f"llm:{model}"
                    labelled += 1
                    return
                except Exception as e:
                    logger.warning(f"Labelling {row['html_url']} failed ({type(e).__name__}: {e})")
                    await asyncio.sleep(2**attempt)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(
        headers=headers, timeout=aiohttp.ClientTimeout(total=120)
    ) as session:
        await asyncio.gather(*(one(session, row) for row in rows))
    return labelled


# --------------------------------------------------------------------------- metrics


@dataclass
class TagScores:
    threshold: float
    precision: float
    recall: float
    f1: float
    unclassified_rate: float


@dataclass
class EvalReport:
    model: str
    taxonomy_version: int
    gold_rows: int
    scored_rows: int
    domain_accuracy: float | None
    domain_top2_accuracy: float | None
    kind_accuracy: float | None
    tag_scores: list[TagScores]

    def best_threshold(self) -> TagScores | None:
        return max(self.tag_scores, key=lambda s: s.f1, default=None)


def _ratio(hits: int, total: int) -> float | None:
    return hits / total if total else None


def evaluate(
    gold_rows: list[dict],
    store: DecisionStore,
    taxonomy_version: int,
    model: str,
    thresholds: tuple[float, ...] = THRESHOLDS,
) -> EvalReport:
    pairs = []
    for row in gold_rows:
        if not row.get("gold_domain"):
            continue
        result = store.load(row["html_url"], taxonomy_version, model)
        if result is not None and result.error is None:
            pairs.append((row, result))

    domain_hits = sum(1 for g, r in pairs if r.domain == g["gold_domain"])
    top2_hits = sum(
        1
        for g, r in pairs
        if g["gold_domain"]
        in sorted(r.domain_probabilities, key=lambda d: -r.domain_probabilities[d])[:2]
    )
    kind_pairs = [(g, r) for g, r in pairs if g.get("gold_kind")]
    kind_hits = sum(1 for g, r in kind_pairs if r.kind == g["gold_kind"])

    tag_scores = []
    for threshold in thresholds:
        tp = fp = fn = empty = 0
        for g, r in pairs:
            gold = _split_tags(g.get("gold_tags", ""))
            predicted = set(r.tags(threshold))
            tp += len(gold & predicted)
            fp += len(predicted - gold)
            fn += len(gold - predicted)
            empty += not predicted
        precision = _ratio(tp, tp + fp) or 0.0
        recall = _ratio(tp, tp + fn) or 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        tag_scores.append(
            TagScores(threshold, precision, recall, f1, _ratio(empty, len(pairs)) or 0.0)
        )

    return EvalReport(
        model=model,
        taxonomy_version=taxonomy_version,
        gold_rows=sum(1 for r in gold_rows if r.get("gold_domain")),
        scored_rows=len(pairs),
        domain_accuracy=_ratio(domain_hits, len(pairs)),
        domain_top2_accuracy=_ratio(top2_hits, len(pairs)),
        kind_accuracy=_ratio(kind_hits, len(kind_pairs)),
        tag_scores=tag_scores,
    )
