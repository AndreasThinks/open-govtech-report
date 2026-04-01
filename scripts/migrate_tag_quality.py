#!/usr/bin/env python3
"""One-off migration: clean up tag quality issues in existing data.

Two operations:
  1. Delete language-name tech-stack tags (python, javascript etc) from
     repository_tags — these duplicate the language field.

  2. String-similarity dedup pass over existing taxonomy:
     For each tag pair with token_sort_ratio >= 82, ask the LLM whether
     they're the same concept. If yes, merge the less-used into the more-used.
     Uses the same _ask_dedup logic as the live pipeline.

Usage:
    uv run python scripts/migrate_tag_quality.py [--db govtech.db] [--dry-run] [--skip-dedup]
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
import click
from dotenv import load_dotenv
from rapidfuzz import fuzz
from tqdm import tqdm

# Make sure we can import the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from govtech_scraper.db import Database
from govtech_scraper.tagger.taxonomy import Taxonomy
from govtech_scraper.tagger.dedup import TagReconciler
from govtech_scraper.tagger.embeddings import OpenRouterEmbeddings
from govtech_scraper.tagger import _LANGUAGE_NOISE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate")

STRING_THRESHOLD = 82  # rapidfuzz token_sort_ratio


# ---------------------------------------------------------------------------
# Step 1: Delete language-noise tech-stack tags
# ---------------------------------------------------------------------------

def step1_delete_language_tags(db: Database, dry_run: bool) -> int:
    ph = ",".join(["?"] * len(_LANGUAGE_NOISE))
    rows = db.conn.execute(
        f"""SELECT tag, COUNT(DISTINCT html_url) as repos
            FROM repository_tags
            WHERE tag IN ({ph}) AND source = 'tech-stack'
            GROUP BY tag ORDER BY repos DESC""",
        list(_LANGUAGE_NOISE),
    ).fetchall()

    if not rows:
        logger.info("Step 1: no language-noise tech-stack tags found.")
        return 0

    total_rows = sum(r["repos"] for r in rows)
    logger.info(f"Step 1: found {len(rows)} language-noise tags across {total_rows:,} repo-tag rows")
    for r in rows:
        logger.info(f"  {r['repos']:6,}  {r['tag']}")

    if dry_run:
        logger.info("  [dry-run] would delete these rows")
        return total_rows

    ph2 = ",".join(["?"] * len(_LANGUAGE_NOISE))
    db.conn.execute(
        f"DELETE FROM repository_tags WHERE tag IN ({ph2}) AND source = 'tech-stack'",
        list(_LANGUAGE_NOISE),
    )
    db.conn.commit()
    logger.info(f"Step 1: deleted {total_rows:,} language-noise tag rows.")
    return total_rows


# ---------------------------------------------------------------------------
# Step 2: String-similarity dedup over existing taxonomy
# ---------------------------------------------------------------------------

async def step2_string_dedup(db: Database, api_key: str, dry_run: bool) -> dict:
    taxonomy = Taxonomy(db)
    logger.info(f"Step 2: loaded {taxonomy.size:,} tags from taxonomy")

    all_tags = taxonomy.all_tags()
    n = len(all_tags)

    # Find all pairs with string similarity >= threshold
    # For 11k tags this is O(n²) string comparisons — fast in Python with rapidfuzz
    logger.info(f"Step 2: scanning {n:,} tags for string-similar pairs...")
    candidates = []
    for i in range(n):
        for j in range(i + 1, n):
            score = fuzz.token_sort_ratio(all_tags[i], all_tags[j])
            if score >= STRING_THRESHOLD:
                # Skip exact matches (already handled)
                if all_tags[i] != all_tags[j]:
                    # Skip pairs already covered by existing embedding dedup
                    # (if both have embeddings and cosine sim >= 0.80, pipeline already saw them)
                    candidates.append((all_tags[i], all_tags[j], score / 100.0))

    logger.info(f"Step 2: {len(candidates):,} candidate pairs found at string threshold {STRING_THRESHOLD}")

    if not candidates:
        logger.info("Step 2: nothing to do.")
        return {"candidates": 0, "merged": 0, "kept": 0, "errors": 0}

    # Show sample
    candidates.sort(key=lambda x: -x[2])
    logger.info("Top 20 candidates:")
    for a, b, s in candidates[:20]:
        logger.info(f"  {s:.2f}  {a}  <->  {b}")

    if dry_run:
        logger.info(f"[dry-run] would ask LLM to confirm {len(candidates)} pairs")
        return {"candidates": len(candidates), "merged": 0, "kept": 0, "errors": 0}

    # Ask LLM to confirm each pair
    embedding_provider = OpenRouterEmbeddings(api_key=api_key)
    reconciler = TagReconciler(
        taxonomy=taxonomy,
        embedding_provider=embedding_provider,
        api_key=api_key,
    )

    stats = {"candidates": len(candidates), "merged": 0, "kept": 0, "errors": 0}

    async with aiohttp.ClientSession() as session:
        for tag_a, tag_b, score in tqdm(candidates, desc="Dedup LLM calls"):
            # Skip if either tag was already merged in this run
            if not taxonomy.has_tag(tag_a) or not taxonomy.has_tag(tag_b):
                continue

            try:
                entry_a = taxonomy.get_tag(tag_a)
                entry_b = taxonomy.get_tag(tag_b)
                usage_a = entry_a.usage_count if entry_a else 0
                usage_b = entry_b.usage_count if entry_b else 0

                # Put the more-used tag as "existing", less-used as "new"
                if usage_a >= usage_b:
                    existing, new_tag, existing_usage = tag_a, tag_b, usage_a
                else:
                    existing, new_tag, existing_usage = tag_b, tag_a, usage_b

                decision = await reconciler._ask_dedup(
                    new_tag=new_tag,
                    existing_tag=existing,
                    repo_summary=f"string-similarity migration (ratio={score:.2f})",
                    usage_count=existing_usage,
                    session=session,
                )

                if decision.same_concept:
                    preferred = decision.preferred_tag
                    # Normalise: preferred must be one of the two tags
                    if preferred not in (tag_a, tag_b):
                        preferred = existing  # fallback to more-used

                    loser = tag_b if preferred == tag_a else tag_a
                    if taxonomy.has_tag(loser) and taxonomy.has_tag(preferred):
                        taxonomy.merge_tag(loser, preferred)
                        logger.info(f"  MERGED '{loser}' -> '{preferred}' (ratio={score:.2f})")
                        stats["merged"] += 1
                    else:
                        stats["kept"] += 1
                else:
                    logger.debug(f"  KEPT '{tag_a}' / '{tag_b}' (ratio={score:.2f}): {decision.reasoning}")
                    stats["kept"] += 1

            except Exception as e:
                logger.warning(f"  ERROR on {tag_a}/{tag_b}: {e}")
                stats["errors"] += 1
                # Brief backoff on errors
                await asyncio.sleep(2)

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--db", default="govtech.db", show_default=True, help="Path to SQLite DB")
@click.option("--dry-run", is_flag=True, help="Show what would happen without making changes")
@click.option("--skip-dedup", is_flag=True, help="Skip the string-similarity dedup step")
@click.option("--skip-language", is_flag=True, help="Skip the language-tag deletion step")
@click.option("-v", "--verbose", is_flag=True)
def main(db, dry_run, skip_dedup, skip_language, verbose):
    """Clean up tag quality issues in existing data."""
    load_dotenv()

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if dry_run:
        logger.info("=== DRY RUN — no changes will be made ===")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key and not skip_dedup:
        logger.error("OPENROUTER_API_KEY not set. Use --skip-dedup or set the key.")
        sys.exit(1)

    db_path = Path(db)
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)

    database = Database(str(db_path))

    t0 = time.time()

    # Step 1
    if not skip_language:
        deleted = step1_delete_language_tags(database, dry_run)
    else:
        logger.info("Step 1: skipped (--skip-language)")
        deleted = 0

    # Step 2
    if not skip_dedup:
        stats = asyncio.run(step2_string_dedup(database, api_key, dry_run))
    else:
        logger.info("Step 2: skipped (--skip-dedup)")
        stats = {}

    elapsed = time.time() - t0

    # Summary
    click.echo()
    click.echo("=== Migration complete ===")
    click.echo(f"  Language tag rows deleted: {deleted:,}")
    if stats:
        click.echo(f"  Dedup candidates:          {stats.get('candidates', 0):,}")
        click.echo(f"  Tags merged:               {stats.get('merged', 0):,}")
        click.echo(f"  Pairs kept separate:       {stats.get('kept', 0):,}")
        click.echo(f"  Errors:                    {stats.get('errors', 0):,}")
    click.echo(f"  Elapsed:                   {elapsed:.0f}s")

    database.close()


if __name__ == "__main__":
    main()
