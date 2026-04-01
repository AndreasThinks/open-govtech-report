"""Agentic tagging pipeline for government repositories.

Public API:
    tag_repo()  — tag a single repository
    tag_batch() — tag a batch of repositories with resume support
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional, Callable

import aiohttp

from ..models import Repository
from ..db import Database
from .context import build_context
from .suggest import TagSuggester, CreditExhaustedError
from .dedup import TagReconciler
from .taxonomy import Taxonomy
from .embeddings import OpenRouterEmbeddings

logger = logging.getLogger(__name__)

# Number of repos to tag before the dedup step kicks in
COLD_START_THRESHOLD = 50

# Language names that duplicate the `language` field — skip saving as tech-stack tags
_LANGUAGE_NOISE = frozenset([
    "javascript", "python", "java", "typescript", "html", "css", "php",
    "ruby", "shell", "r", "scala", "c#", "kotlin", "go", "rust", "c",
    "c++", "perl", "swift", "matlab", "bash", "json", "xml", "yaml",
    "sql", "makefile",
])


@dataclass
class TagResult:
    """Result of tagging a single repo."""
    html_url: str
    tags: list[str]
    primary_category: str
    tech_stack: list[str]
    was_deduped: bool


@dataclass
class BatchResult:
    """Summary of a batch tagging run."""
    total_processed: int
    total_skipped: int
    total_new_tags: int
    total_dedup_merges: int
    errors: list[str]


@dataclass
class SuggestionPhaseResult:
    """Result of the suggestion phase for a single repo."""
    repo: Repository
    suggestions: any  # TaggingResult
    readme_content: Optional[str]


async def _suggest_phase(
    repo: Repository,
    db: Database,
    suggester: TagSuggester,
    taxonomy: Taxonomy,
    session: aiohttp.ClientSession,
) -> SuggestionPhaseResult:
    """Phase 1: Get LLM suggestions (can run in parallel)."""
    # Build context
    readme_content = db.get_readme(repo.html_url)
    context = build_context(
        repo,
        readme_content=readme_content,
        file_tree=None,
        dependency_manifest=None,
        entry_point_code=None,
    )

    # Get LLM suggestions, hinting at existing vocabulary
    top_tags = taxonomy.get_top_tags(50) if taxonomy.size > 0 else None
    suggestions = await suggester.suggest(context, existing_tags=top_tags, session=session)

    return SuggestionPhaseResult(
        repo=repo,
        suggestions=suggestions,
        readme_content=readme_content,
    )


async def _reconcile_phase(
    suggestion_result: SuggestionPhaseResult,
    db: Database,
    reconciler: Optional[TagReconciler],
    taxonomy: Taxonomy,
    session: aiohttp.ClientSession,
) -> TagResult:
    """Phase 2: Reconcile against taxonomy (must run sequentially).
    
    Awaited one at a time to keep taxonomy mutations serial.
    """
    repo = suggestion_result.repo
    suggestions = suggestion_result.suggestions

    # Step 2: Reconcile against taxonomy (if past cold start)
    was_deduped = False
    if reconciler and taxonomy.size >= COLD_START_THRESHOLD:
        repo_summary = f"{repo.owner}/{repo.name}: {repo.description or 'no description'}"
        final_tags = await reconciler.reconcile(suggestions, repo_summary, session=session)
        was_deduped = True
    else:
        # Cold start: accept all suggestions, build the taxonomy
        final_tags = []
        tag_strings = [s.tag for s in suggestions.tags]
        if tag_strings:
            embeddings = await reconciler.embeddings.embed(tag_strings, session=session) if reconciler else [None] * len(tag_strings)
            for suggestion, embedding in zip(suggestions.tags, embeddings):
                taxonomy.add_tag(suggestion.tag, embedding=embedding)
                final_tags.append(suggestion.tag)

    # Save tags to database
    for tag in final_tags:
        confidence = next(
            (s.confidence for s in suggestions.tags if s.tag == tag), 0.8
        )
        db.save_repo_tag(repo.html_url, tag, confidence, source="llm")

    # Save tech stack as tags with a different source, skipping bare language names
    for tech in suggestions.tech_stack:
        tech_tag = tech.lower().replace(" ", "-")
        if tech_tag in _LANGUAGE_NOISE:
            logger.debug(f"Skipping language-noise tech-stack tag: {tech_tag}")
            continue
        db.save_repo_tag(repo.html_url, tech_tag, 1.0, source="tech-stack")

    return TagResult(
        html_url=repo.html_url,
        tags=final_tags,
        primary_category=suggestions.primary_category,
        tech_stack=suggestions.tech_stack,
        was_deduped=was_deduped,
    )


async def tag_repo(
    repo: Repository,
    db: Database,
    suggester: TagSuggester,
    reconciler: Optional[TagReconciler],
    taxonomy: Taxonomy,
    readme_content: Optional[str] = None,
    file_tree: Optional[str] = None,
    dependency_manifest: Optional[str] = None,
    entry_point_code: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> TagResult:
    """Tag a single repository.
    
    Args:
        repo: Repository to tag
        db: Database instance
        suggester: LLM tag suggester
        reconciler: Tag reconciler (None during cold start)
        taxonomy: Tag taxonomy
        readme_content: Optional README text
        file_tree: Optional directory listing
        dependency_manifest: Optional dependency file contents
        entry_point_code: Optional main source file contents
        session: Optional aiohttp session for reuse
    
    Returns:
        TagResult with final tags
    """
    # Build context
    context = build_context(
        repo,
        readme_content=readme_content,
        file_tree=file_tree,
        dependency_manifest=dependency_manifest,
        entry_point_code=entry_point_code,
    )

    # Step 1: Get LLM suggestions, hinting at existing vocabulary
    top_tags = taxonomy.get_top_tags(50) if taxonomy.size > 0 else None
    suggestions = await suggester.suggest(context, existing_tags=top_tags, session=session)

    # Step 2: Reconcile against taxonomy (if past cold start)
    was_deduped = False
    if reconciler and taxonomy.size >= COLD_START_THRESHOLD:
        repo_summary = f"{repo.owner}/{repo.name}: {repo.description or 'no description'}"
        final_tags = await reconciler.reconcile(suggestions, repo_summary, session=session)
        was_deduped = True
    else:
        # Cold start: accept all suggestions, build the taxonomy
        final_tags = []
        tag_strings = [s.tag for s in suggestions.tags]
        if tag_strings:
            embeddings = await reconciler.embeddings.embed(tag_strings, session=session) if reconciler else [None] * len(tag_strings)
            for suggestion, embedding in zip(suggestions.tags, embeddings):
                taxonomy.add_tag(suggestion.tag, embedding=embedding)
                final_tags.append(suggestion.tag)

    # Save tags to database
    for tag in final_tags:
        confidence = next(
            (s.confidence for s in suggestions.tags if s.tag == tag), 0.8
        )
        db.save_repo_tag(repo.html_url, tag, confidence, source="llm")

    # Save tech stack as tags with a different source, skipping bare language names
    for tech in suggestions.tech_stack:
        tech_tag = tech.lower().replace(" ", "-")
        if tech_tag in _LANGUAGE_NOISE:
            logger.debug(f"Skipping language-noise tech-stack tag: {tech_tag}")
            continue
        db.save_repo_tag(repo.html_url, tech_tag, 1.0, source="tech-stack")

    return TagResult(
        html_url=repo.html_url,
        tags=final_tags,
        primary_category=suggestions.primary_category,
        tech_stack=suggestions.tech_stack,
        was_deduped=was_deduped,
    )


async def tag_batch(
    db: Database,
    api_key: str,
    model: str = "qwen/qwen3-32b",
    embedding_model: str = "openai/text-embedding-3-small",
    batch_size: int = 50,
    limit: Optional[int] = None,
    retag: bool = False,
    progress_callback: Optional[Callable[[int], None]] = None,
    concurrency: int = 10,
) -> BatchResult:
    """Tag repositories in batches with resume support.
    
    Args:
        db: Database instance
        api_key: OpenRouter API key
        model: LLM model for tag suggestion
        embedding_model: Model for embeddings
        batch_size: Number of repos to process before committing
        limit: Max repos to process (None for all)
        retag: If True, re-tag already tagged repos
        progress_callback: Called with count after each repo
        concurrency: Number of parallel suggestion calls (default 10)
    
    Returns:
        BatchResult with statistics
    """
    # Initialize components
    suggester = TagSuggester(api_key=api_key, model=model)
    embedding_provider = OpenRouterEmbeddings(api_key=api_key, model=embedding_model)
    taxonomy = Taxonomy(db)
    reconciler = TagReconciler(
        taxonomy=taxonomy,
        embedding_provider=embedding_provider,
        api_key=api_key,
        model=model,
    )

    # Get repos to tag
    if retag:
        repos_data = db.get_all_repos()
    else:
        repos_data = db.get_untagged_repos()

    if limit:
        repos_data = repos_data[:limit]

    # Optimization 3: Filter out empty repos
    filtered_repos = []
    for repo_row in repos_data:
        has_description = repo_row["description"] is not None and repo_row["description"].strip()
        has_readme = db.get_readme(repo_row["html_url"]) is not None
        has_language = repo_row["language"] is not None
        has_stars = repo_row["stars"] > 0
        
        if not has_description and not has_readme and not has_language and not has_stars:
            continue
        
        filtered_repos.append(repo_row)
    
    skipped_count = len(repos_data) - len(filtered_repos)
    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} empty repos (no description, README, language, or stars)")
    
    repos_data = filtered_repos
    total = len(repos_data)
    logger.info(f"Tagging {total} repositories (taxonomy has {taxonomy.size} tags)")

    result = BatchResult(
        total_processed=0,
        total_skipped=skipped_count,
        total_new_tags=0,
        total_dedup_merges=0,
        errors=[],
    )

    initial_taxonomy_size = taxonomy.size

    # Create shared aiohttp session (Optimization 2)
    async with aiohttp.ClientSession() as session:
        # Process in batches with parallel suggest, sequential reconcile (Optimization 1)
        semaphore = asyncio.Semaphore(concurrency)
        consecutive_credit_errors = 0
        
        for batch_start in range(0, total, concurrency):
            batch_end = min(batch_start + concurrency, total)
            batch_repos = repos_data[batch_start:batch_end]
            
            # Phase 1: Parallel suggestion
            async def suggest_with_semaphore(repo_row):
                nonlocal consecutive_credit_errors
                async with semaphore:
                    try:
                        # Build Repository object from DB row
                        repo = Repository(
                            owner=repo_row["owner"],
                            name=repo_row["name"],
                            html_url=repo_row["html_url"],
                            country=repo_row["country"],
                            description=repo_row["description"],
                            language=repo_row["language"],
                            stars=repo_row["stars"],
                            forks=repo_row["forks"],
                            watchers=repo_row["watchers"],
                            open_issues=repo_row["open_issues"],
                            size_kb=repo_row["size_kb"],
                            license=repo_row["license"],
                            topics=json.loads(repo_row["topics"]) if repo_row["topics"] else [],
                            default_branch=repo_row["default_branch"],
                            fork=bool(repo_row["fork"]),
                            archived=bool(repo_row["archived"]),
                            created_at=repo_row["created_at"],
                            updated_at=repo_row["updated_at"],
                            pushed_at=repo_row["pushed_at"],
                            commit_count=repo_row["commit_count"],
                            fork_source=repo_row["fork_source"],
                        )
                        suggestion_result = await _suggest_phase(repo, db, suggester, taxonomy, session)
                        # Success - reset credit error counter
                        consecutive_credit_errors = 0
                        return suggestion_result
                    except CreditExhaustedError as e:
                        consecutive_credit_errors += 1
                        error_msg = f"{repo_row['html_url']}: credit exhaustion (402): {e}"
                        logger.error(f"Credit exhaustion in suggest phase: {error_msg}")
                        result.errors.append(error_msg)
                        return None
                    except Exception as e:
                        error_msg = f"{repo_row['html_url']}: {e}"
                        logger.error(f"Error in suggest phase: {error_msg}")
                        return None
            
            # Run suggestions in parallel
            suggestion_results = await asyncio.gather(
                *[suggest_with_semaphore(repo_row) for repo_row in batch_repos],
                return_exceptions=False
            )
            
            # Check circuit breaker: if 3+ consecutive credit errors, stop
            if consecutive_credit_errors >= 3:
                logger.error(
                    "OpenRouter credits exhausted. Top up at https://openrouter.ai/settings/credits "
                    "and re-run. Progress is saved — will resume from where it stopped."
                )
                break
            
            # Phase 2: Sequential reconciliation (to maintain taxonomy consistency)
            for i, suggestion_result in enumerate(suggestion_results):
                if suggestion_result is None:
                    result.errors.append(f"{batch_repos[i]['html_url']}: suggestion phase failed")
                    continue
                
                try:
                    tag_result = await _reconcile_phase(
                        suggestion_result, db, reconciler, taxonomy, session
                    )
                    
                    result.total_processed += 1
                    logger.debug(
                        f"[{batch_start + i + 1}/{total}] {suggestion_result.repo.owner}/{suggestion_result.repo.name}: "
                        f"{', '.join(tag_result.tags)} "
                        f"({'deduped' if tag_result.was_deduped else 'cold start'})"
                    )
                except Exception as e:
                    error_msg = f"{suggestion_result.repo.html_url}: {e}"
                    logger.error(f"Error in reconcile phase: {error_msg}")
                    result.errors.append(error_msg)
                
                if progress_callback:
                    progress_callback(1)

    result.total_new_tags = taxonomy.size - initial_taxonomy_size

    logger.info(
        f"Batch complete: {result.total_processed} tagged, "
        f"{result.total_skipped} skipped, "
        f"{len(result.errors)} errors, "
        f"{result.total_new_tags} new tags"
    )

    return result
