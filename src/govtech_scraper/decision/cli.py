"""`govtech-scraper decide ...`: the decision-model tagging experiment."""

import asyncio
import os
import sys

import click
from dotenv import load_dotenv
from tqdm import tqdm

from ..db import Database

BACKENDS = ("space", "kev", "jev")


def build_client(
    backend: str, base_url: str | None, model: str | None, space: str | None, batch_size: int
):
    """Pick a System One client from CLI options and environment variables."""
    from .client import JEV_BASE_URL, HttpSystemOneClient, SpaceClient

    load_dotenv()
    if backend == "space":
        space_id = space or os.getenv("DECISION_SPACE_ID")
        if not space_id:
            raise click.UsageError(
                "Set --space or DECISION_SPACE_ID (e.g. AndreasThinks/govtech-decision-scorer)"
            )
        return SpaceClient(space_id, hf_token=os.getenv("HF_TOKEN"), batch_size=batch_size)
    if backend == "kev":
        return HttpSystemOneClient(
            base_url=base_url or os.getenv("KEV_BASE_URL", "http://127.0.0.1:8009"),
            api_key=os.getenv("KEV_API_KEY"),
            model=model or "kev-latest",
        )
    api_key = os.getenv("TYPESAFE_API_KEY")
    if not api_key:
        raise click.UsageError("Set TYPESAFE_API_KEY to use hosted Jev")
    return HttpSystemOneClient(
        base_url=base_url or JEV_BASE_URL, api_key=api_key, model=model or "jev-latest"
    )


def backend_options(func):
    func = click.option(
        "--batch-size",
        type=int,
        default=64,
        show_default=True,
        help="Repos per scoring batch (and per Space GPU call)",
    )(func)
    func = click.option("--space", default=None, help="Hugging Face Space id for --backend space")(
        func
    )
    func = click.option(
        "--model", default=None, help="Model id sent to the endpoint (kev-latest, jev-latest)"
    )(func)
    func = click.option(
        "--base-url", default=None, help="Endpoint base URL for --backend kev or jev"
    )(func)
    func = click.option(
        "--backend",
        type=click.Choice(BACKENDS),
        default="space",
        show_default=True,
        help="space: our ZeroGPU Space; kev: any kev.serve endpoint; jev: TypeSafe's hosted API",
    )(func)
    return func


@click.group()
def decide() -> None:
    """Decision-model tagging against the fixed COFOG taxonomy (experimental)."""


@decide.command("taxonomy")
@click.option(
    "--path",
    default=None,
    type=click.Path(exists=True),
    help="Taxonomy YAML (default: the shipped one)",
)
def taxonomy_command(path: str | None) -> None:
    """Validate the taxonomy and print a summary."""
    from .taxonomy import load_taxonomy

    taxonomy = load_taxonomy(path)
    click.echo(
        f"Taxonomy v{taxonomy.version}: {len(taxonomy.domains)} domains, "
        f"{len(taxonomy.tags)} tags, {len(taxonomy.kinds)} kinds"
    )
    for domain in taxonomy.domains:
        click.echo(f"  {domain.id} {domain.name}: {', '.join(t.id for t in domain.tags)}")


@decide.command("tag")
@backend_options
@click.option("--limit", type=int, default=None, help="Score at most this many repos")
@click.option("--rescore", is_flag=True, help="Score again even if nothing changed")
@click.option(
    "--max-domains",
    type=int,
    default=3,
    show_default=True,
    help="Ask about the tags of this many likely domains (11 = every tag)",
)
@click.option(
    "--gold",
    "gold_path",
    default=None,
    type=click.Path(exists=True),
    help="Only score the repos listed in this gold CSV",
)
@click.pass_context
def tag_command(
    ctx, backend, base_url, model, space, batch_size, limit, rescore, max_domains, gold_path
) -> None:
    """Score new or changed repositories and store the probabilities."""
    from . import run_decision_tagging
    from .evaluate import read_gold
    from .taxonomy import load_taxonomy

    client = build_client(backend, base_url, model, space, batch_size)
    taxonomy = load_taxonomy()
    urls = [row["html_url"] for row in read_gold(gold_path)] if gold_path else None
    db = Database(ctx.obj["db_path"])
    try:
        pbar = tqdm(desc=f"Scoring with {client.model}", unit="repo")
        summary = asyncio.run(
            run_decision_tagging(
                db,
                client,
                taxonomy,
                limit=limit,
                rescore=rescore,
                batch_size=batch_size,
                max_domains=max_domains,
                progress=pbar.update,
                urls=urls,
            )
        )
        pbar.close()
    finally:
        db.close()

    click.echo(
        f"Scored: {summary.scored}  failed: {summary.failed}  "
        f"unchanged: {summary.skipped_unchanged}  empty: {summary.skipped_empty}"
    )
    for error in summary.errors[:10]:
        click.echo(f"  {error}", err=True)
    if summary.stopped_early:
        click.echo(f"Stopped early: {summary.stopped_early}. Re-run later to resume.", err=True)
        sys.exit(2)


@decide.command("show")
@click.argument("html_url")
@click.option(
    "--model", required=True, help="Model id the run was stored under (see `decide runs`)"
)
@click.option("--threshold", type=float, default=0.5, show_default=True)
@click.pass_context
def show_command(ctx, html_url: str, model: str, threshold: float) -> None:
    """Print the stored result for one repository."""
    from .store import DecisionStore
    from .taxonomy import load_taxonomy

    taxonomy = load_taxonomy()
    db = Database(ctx.obj["db_path"])
    try:
        result = DecisionStore(db).load(html_url, taxonomy.version, model)
    finally:
        db.close()
    if result is None:
        raise click.ClickException("No stored result for that repository and model")
    if result.error:
        raise click.ClickException(result.error)
    click.echo(f"Domain: {result.domain} ({result.domain_probabilities.get(result.domain, 0):.2f})")
    click.echo(f"Kind: {result.kind} ({result.kind_probabilities.get(result.kind, 0):.2f})")
    click.echo(f"Substantive: {result.substantive:.2f}")
    for tag, p in sorted(result.tag_probabilities.items(), key=lambda kv: -kv[1]):
        marker = "*" if p >= threshold else " "
        click.echo(f" {marker} {p:.2f} {tag}")


@decide.command("runs")
@click.pass_context
def runs_command(ctx) -> None:
    """List stored runs by taxonomy version and model."""
    from .store import DecisionStore

    db = Database(ctx.obj["db_path"])
    try:
        for version, model, count in DecisionStore(db).models():
            click.echo(f"v{version}  {model}  {count} repos")
    finally:
        db.close()


@decide.command("sample")
@click.option(
    "--out", "out_path", default="decision-gold.csv", show_default=True, type=click.Path()
)
@click.option("-n", "n", type=int, default=300, show_default=True)
@click.option("--seed", type=int, default=7, show_default=True)
@click.pass_context
def sample_command(ctx, out_path: str, n: int, seed: int) -> None:
    """Write a random sample of repos to label as a gold set."""
    from .evaluate import write_sample

    db = Database(ctx.obj["db_path"])
    try:
        written = write_sample(db, out_path, n=n, seed=seed)
    finally:
        db.close()
    click.echo(
        f"Wrote {written} repos to {out_path}. Fill gold_domain, gold_kind and gold_tags "
        "(semicolon-separated), or run `decide label-gold` first."
    )


@decide.command("label-gold")
@click.argument("gold_path", type=click.Path(exists=True))
@click.option(
    "--model", default="google/gemini-2.5-pro", show_default=True, help="OpenRouter model"
)
@click.option("--overwrite", is_flag=True, help="Relabel rows that already have gold labels")
@click.pass_context
def label_gold_command(ctx, gold_path: str, model: str, overwrite: bool) -> None:
    """Pre-fill the gold CSV with one strong-LLM pass. Spot-check the result."""
    from .evaluate import label_with_llm, read_gold, write_gold
    from .taxonomy import load_taxonomy

    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise click.UsageError("Set OPENROUTER_API_KEY")
    rows = read_gold(gold_path)
    db = Database(ctx.obj["db_path"])
    try:
        labelled = asyncio.run(
            label_with_llm(db, rows, load_taxonomy(), api_key, model, overwrite=overwrite)
        )
    finally:
        db.close()
    write_gold(gold_path, rows)
    click.echo(f"Labelled {labelled} rows with {model}. Review them before trusting the eval.")


@decide.command("eval")
@click.argument("gold_path", type=click.Path(exists=True))
@click.option(
    "--model",
    "models",
    multiple=True,
    required=True,
    help="Stored model id to evaluate; repeat to compare runs",
)
@click.pass_context
def eval_command(ctx, gold_path: str, models: tuple[str, ...]) -> None:
    """Compare stored runs against a gold CSV."""
    from .evaluate import evaluate, read_gold
    from .store import DecisionStore
    from .taxonomy import load_taxonomy

    def pct(value):
        return "  n/a" if value is None else f"{value * 100:5.1f}%"

    taxonomy = load_taxonomy()
    gold = read_gold(gold_path)
    db = Database(ctx.obj["db_path"])
    try:
        store = DecisionStore(db)
        for model in models:
            report = evaluate(gold, store, taxonomy.version, model)
            click.echo(
                f"\n{model} (taxonomy v{report.taxonomy_version}): "
                f"{report.scored_rows} of {report.gold_rows} gold repos scored"
            )
            click.echo(
                f"  domain accuracy {pct(report.domain_accuracy)}   "
                f"top-2 {pct(report.domain_top2_accuracy)}   kind accuracy {pct(report.kind_accuracy)}"
            )
            click.echo("  threshold  precision  recall     F1  no-tag rate")
            for s in report.tag_scores:
                click.echo(
                    f"     {s.threshold:.1f}      {pct(s.precision)}  {pct(s.recall)}  "
                    f"{pct(s.f1)}  {pct(s.unclassified_rate)}"
                )
    finally:
        db.close()
