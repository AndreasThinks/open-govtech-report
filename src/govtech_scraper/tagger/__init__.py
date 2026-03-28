"""Agentic tagging pipeline for government repositories.

Public API:
    tag_repo()  — tag a single repository
    tag_batch() — tag a batch of repositories with resume support
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional, Callable

from ..models import Repository
from ..db import Database
from .context import build_context
from .suggest import TagSuggester
from .dedup import TagReconciler
from .taxonomy import Taxonomy
from .embeddings import OpenRouterEmbeddings

logger = logging.getLogger(__name__)

# Number of repos to tag before the dedup step kicks in
COLD_START_THRESHOLD = 50


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
    suggestions = await suggester.suggest(context, existing_tags=top_tags)

    # Step 2: Reconcile against taxonomy (if past cold start)
    was_deduped = False
    if reconciler and taxonomy.size >= COLD_START_THRESHOLD:
        repo_summary = f"{repo.owner}/{repo.name}: {repo.description or 'no description'}"
        final_tags = await reconciler.reconcile(suggestions, repo_summary)
        was_deduped = True
    else:
        # Cold start: accept all suggestions, build the taxonomy
        final_tags = []
        tag_strings = [s.tag for s in suggestions.tags]
        if tag_strings:
            embeddings = await reconciler.embeddings.embed(tag_strings) if reconciler else [None] * len(tag_strings)
            for suggestion, embedding in zip(suggestions.tags, embeddings):
                taxonomy.add_tag(suggestion.tag, embedding=embedding)
                final_tags.append(suggestion.tag)

    # Save tags to database
    for tag in final_tags:
        confidence = next(
            (s.confidence for s in suggestions.tags if s.tag == tag), 0.8
        )
        db.save_repo_tag(repo.html_url, tag, confidence, source="llm")

    # Save tech stack as tags with a different source
    for tech in suggestions.tech_stack:
        tech_tag = tech.lower().replace(" ", "-")
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
    model: str = "google/gemini-flash-1.5",
    embedding_model: str = "openai/text-embedding-3-small",
    batch_size: int = 50,
    limit: Optional[int] = None,
    retag: bool = False,
    progress_callback: Optional[Callable[[int], None]] = None,
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

    total = len(repos_data)
    logger.info(f"Tagging {total} repositories (taxonomy has {taxonomy.size} tags)")

    result = BatchResult(
        total_processed=0,
        total_skipped=0,
        total_new_tags=0,
        total_dedup_merges=0,
        errors=[],
    )

    initial_taxonomy_size = taxonomy.size

    for i, repo_row in enumerate(repos_data):
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

            # Get README if available
            readme_content = db.get_readme(repo.html_url)

            tag_result = await tag_repo(
                repo=repo,
                db=db,
                suggester=suggester,
                reconciler=reconciler,
                taxonomy=taxonomy,
                readme_content=readme_content,
            )

            result.total_processed += 1
            logger.debug(
                f"[{i+1}/{total}] {repo.owner}/{repo.name}: "
                f"{', '.join(tag_result.tags)} "
                f"({'deduped' if tag_result.was_deduped else 'cold start'})"
            )

        except Exception as e:
            error_msg = f"{repo_row['html_url']}: {e}"
            logger.error(f"Error tagging: {error_msg}")
            result.errors.append(error_msg)

        if progress_callback:
            progress_callback(1)

    result.total_new_tags = taxonomy.size - initial_taxonomy_size

    logger.info(
        f"Batch complete: {result.total_processed} tagged, "
        f"{len(result.errors)} errors, "
        f"{result.total_new_tags} new tags"
    )

    return result
