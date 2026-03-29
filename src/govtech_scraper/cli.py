"""CLI entry point for govtech-scraper."""

import asyncio
import logging
import os
import sys

import aiohttp
import click
from dotenv import load_dotenv
from tqdm import tqdm

from .accounts import fetch_government_accounts
from .auth import GitHubAuth
from .db import Database
from .repos import fetch_all_repos


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_auth() -> GitHubAuth:
    """Load GitHub App credentials from environment."""
    load_dotenv()
    app_id = os.getenv("GITHUB_APP_ID")
    installation_id = os.getenv("GITHUB_INSTALLATION_ID")
    private_key = os.getenv("GITHUB_PRIVATE_KEY")

    if not all([app_id, installation_id, private_key]):
        click.echo("Error: GitHub App credentials not found.", err=True)
        click.echo("Set GITHUB_APP_ID, GITHUB_INSTALLATION_ID, and GITHUB_PRIVATE_KEY", err=True)
        sys.exit(1)

    return GitHubAuth(app_id, installation_id, private_key)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option("--db", "db_path", default="govtech.db", help="Database path")
@click.pass_context
def main(ctx: click.Context, verbose: bool, db_path: str) -> None:
    """GovTech Scraper - Government GitHub repository cataloger."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path


@main.command()
@click.pass_context
def accounts(ctx: click.Context) -> None:
    """Fetch and store government GitHub accounts."""
    db = Database(ctx.obj["db_path"])
    try:
        async def _run():
            async with aiohttp.ClientSession() as session:
                accts = await fetch_government_accounts(session)
                db.save_accounts(accts)
                click.echo(f"Saved {len(accts)} government accounts")

        asyncio.run(_run())
    finally:
        db.close()


@main.command()
@click.option("--force", is_flag=True, help="Ignore cache, fetch everything")
@click.option("--limit", type=int, default=None, help="Limit accounts to process (for testing)")
@click.pass_context
def scrape(ctx: click.Context, force: bool, limit: int | None) -> None:
    """Scrape repository metadata for all government accounts."""
    auth = load_auth()
    db = Database(ctx.obj["db_path"])

    try:
        async def _run():
            async with aiohttp.ClientSession() as session:
                # Step 1: Fetch accounts
                click.echo("Fetching government accounts...")
                accts = await fetch_government_accounts(session)
                db.save_accounts(accts)
                click.echo(f"Found {len(accts)} accounts")

                # Step 2: Fetch repos
                target = limit or len(accts)
                click.echo(f"Fetching repos for {target} accounts...")
                pbar = tqdm(total=target, desc="Accounts", unit="acct")

                repos = await fetch_all_repos(
                    session, auth, accts, db,
                    force=force, limit=limit,
                    progress_callback=lambda n: pbar.update(n),
                )
                pbar.close()

                # Step 3: Save
                if repos:
                    db.save_repositories(repos)
                    click.echo(f"\nSaved {len(repos)} repositories")
                    click.echo(f"Total in database: {db.get_repo_count()}")
                else:
                    click.echo("\nNo repositories found")

        asyncio.run(_run())
    finally:
        db.close()


@main.command()
@click.option("--limit", type=int, default=None, help="Limit repos to tag (for testing)")
@click.option("--retag", is_flag=True, help="Re-tag already tagged repos")
@click.option("--model", default="qwen/qwen3-32b", help="LLM model for tag suggestion")
@click.option(
    "--embedding-model",
    default="openai/text-embedding-3-small",
    help="Model for tag embeddings",
)
@click.pass_context
def tag(
    ctx: click.Context,
    limit: int | None,
    retag: bool,
    model: str,
    embedding_model: str,
) -> None:
    """Tag repositories using LLM + embedding deduplication."""
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        click.echo("Error: OPENROUTER_API_KEY not set.", err=True)
        click.echo("Set it in .env or environment.", err=True)
        sys.exit(1)

    db = Database(ctx.obj["db_path"])
    try:
        from .tagger import tag_batch

        async def _run():
            # Count what we're working with
            if retag:
                total = len(db.get_all_repos())
            else:
                total = len(db.get_untagged_repos())

            if limit:
                total = min(total, limit)

            if total == 0:
                click.echo("No repositories to tag.")
                return

            click.echo(f"Tagging {total} repositories with {model}...")
            pbar = tqdm(total=total, desc="Tagging", unit="repo")

            result = await tag_batch(
                db=db,
                api_key=api_key,
                model=model,
                embedding_model=embedding_model,
                limit=limit,
                retag=retag,
                progress_callback=lambda n: pbar.update(n),
            )
            pbar.close()

            click.echo(f"\nTagged: {result.total_processed}")
            click.echo(f"New tags created: {result.total_new_tags}")
            if result.errors:
                click.echo(f"Errors: {len(result.errors)}")
                # Check if any errors are credit exhaustion
                credit_errors = [e for e in result.errors if "credit" in e.lower() or "402" in e]
                if credit_errors:
                    click.echo(
                        f"\nCredit limit reached. {result.total_processed} repos tagged before stopping. "
                        f"Top up credits and re-run — progress is saved automatically.",
                        err=True
                    )

            # Show tag stats
            tag_stats = db.get_tag_stats()
            click.echo(f"\nTaxonomy: {tag_stats['total_tags']} tags")
            click.echo(f"Tagged repos: {tag_stats['total_tagged_repos']}")
            click.echo(f"Avg tags/repo: {tag_stats['avg_tags_per_repo']}")
            if tag_stats["top_tags"]:
                click.echo("\nTop tags:")
                for tag_name, count in tag_stats["top_tags"][:10]:
                    click.echo(f"  {tag_name}: {count}")

        asyncio.run(_run())
    finally:
        db.close()


@main.command()
@click.option("--recalculate", is_flag=True, help="Recalculate all groups from scratch")
@click.option("--model", default="qwen/qwen3-32b", help="LLM model for naming groups")
@click.option(
    "--distance-threshold",
    type=float,
    default=1.0,
    help="Clustering distance threshold",
)
@click.option(
    "--min-cluster-size", type=int, default=3, help="Minimum tags per group"
)
@click.pass_context
def group(
    ctx: click.Context,
    recalculate: bool,
    model: str,
    distance_threshold: float,
    min_cluster_size: int,
) -> None:
    """Group tags into hierarchical categories using embedding clustering."""
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        click.echo("Error: OPENROUTER_API_KEY not set.", err=True)
        click.echo("Set it in .env or environment.", err=True)
        sys.exit(1)

    db = Database(ctx.obj["db_path"])
    try:
        from .tagger.taxonomy import Taxonomy
        from .tagger.hierarchy import TagGrouper

        async def _run():
            taxonomy = Taxonomy(db)
            
            if taxonomy.size == 0:
                click.echo("No tags found in taxonomy. Run 'tag' command first.")
                return

            click.echo(f"Grouping {taxonomy.size} tags using {model}...")
            click.echo(
                f"Parameters: distance_threshold={distance_threshold}, "
                f"min_cluster_size={min_cluster_size}"
            )

            grouper = TagGrouper(
                api_key=api_key,
                model=model,
                min_cluster_size=min_cluster_size,
                distance_threshold=distance_threshold,
            )

            async with aiohttp.ClientSession() as session:
                groups = await grouper.build_groups(
                    taxonomy, db, session=session, recalculate=recalculate
                )

            if groups:
                click.echo(f"\nCreated {len(groups)} tag groups:")
                for group in groups:
                    click.echo(f"\n  {group['name']} ({len(group['tags'])} tags)")
                    click.echo(f"    {group['description']}")
                    if len(group['tags']) <= 10:
                        click.echo(f"    Tags: {', '.join(group['tags'])}")
                    else:
                        click.echo(
                            f"    Tags: {', '.join(group['tags'][:10])}, "
                            f"... (+{len(group['tags']) - 10} more)"
                        )
            else:
                click.echo("No groups created (not enough tags with embeddings)")

        asyncio.run(_run())
    finally:
        db.close()


@main.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show database statistics."""
    db = Database(ctx.obj["db_path"])
    try:
        count = db.get_repo_count()
        acct_count = db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        country_count = db.conn.execute(
            "SELECT COUNT(DISTINCT country) FROM accounts"
        ).fetchone()[0]
        click.echo(f"Accounts: {acct_count}")
        click.echo(f"Countries: {country_count}")
        click.echo(f"Repositories: {count}")

        # Top languages
        rows = db.conn.execute(
            "SELECT language, COUNT(*) as cnt FROM repositories "
            "WHERE language IS NOT NULL GROUP BY language ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        if rows:
            click.echo("\nTop languages:")
            for row in rows:
                click.echo(f"  {row['language']}: {row['cnt']}")

        # Tag stats
        tag_stats = db.get_tag_stats()
        if tag_stats["total_tags"] > 0:
            click.echo(f"\nTaxonomy: {tag_stats['total_tags']} tags")
            click.echo(f"Tagged repos: {tag_stats['total_tagged_repos']}")
            click.echo(f"Avg tags/repo: {tag_stats['avg_tags_per_repo']}")
            if tag_stats["top_tags"]:
                click.echo("\nTop tags:")
                for tag_name, cnt in tag_stats["top_tags"][:10]:
                    click.echo(f"  {tag_name}: {cnt}")

        # Group stats
        group_count = db.conn.execute("SELECT COUNT(*) FROM tag_groups").fetchone()[0]
        if group_count > 0:
            click.echo(f"\nTag groups: {group_count}")
            groups = db.get_tag_groups()
            for group in groups[:10]:
                members = db.get_group_members(group["id"])
                click.echo(f"  {group['name']}: {len(members)} tags")
    finally:
        db.close()
