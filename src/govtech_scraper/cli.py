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
    finally:
        db.close()
