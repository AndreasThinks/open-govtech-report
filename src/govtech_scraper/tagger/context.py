"""Builds LLM context from repository data within a token budget.

Priority order for context assembly:
1. Metadata (name, description, language, GitHub topics) — ~100 tokens
2. Dependency manifest (requirements.txt, package.json, etc.) — ~200 tokens
3. File tree (directory listing) — ~300 tokens  
4. README (truncated) — up to ~800 tokens
5. Entry point file (main.py, app.py, index.js) — remainder of budget

Currently works with data already in the DB (metadata + README).
Future: can fetch file trees and dependency manifests from GitHub API.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Rough chars-per-token estimate for English text
CHARS_PER_TOKEN = 4

# Well-known entry point filenames
ENTRY_POINTS = [
    "main.py", "app.py", "server.py", "index.py", "run.py", "cli.py",
    "index.js", "app.js", "server.js", "main.js",
    "main.go", "cmd/main.go",
    "main.rs", "src/main.rs", "src/lib.rs",
    "Program.cs", "Startup.cs",
    "index.ts", "app.ts", "main.ts",
    "Main.java", "Application.java",
    "index.php", "app.php",
]

# Well-known dependency manifest filenames
DEPENDENCY_FILES = [
    "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml", "Pipfile",
    "package.json", "yarn.lock",
    "Gemfile", "Cargo.toml", "go.mod", "go.sum",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "mix.exs",
    "*.csproj", "*.sln",
]


def _truncate_to_budget(text: str, max_tokens: int) -> str:
    """Truncate text to approximate token budget."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate."""
    return len(text) // CHARS_PER_TOKEN


def build_metadata_section(repo) -> str:
    """Build the metadata section from a Repository object."""
    parts = [f"Repository: {repo.owner}/{repo.name}"]
    if repo.description:
        parts.append(f"Description: {repo.description}")
    if repo.language:
        parts.append(f"Primary language: {repo.language}")
    if repo.topics:
        topics = repo.topics if isinstance(repo.topics, list) else []
        if topics:
            parts.append(f"GitHub topics: {', '.join(topics)}")
    parts.append(f"Stars: {repo.stars}, Forks: {repo.forks}")
    if repo.archived:
        parts.append("Status: ARCHIVED")
    if repo.fork:
        parts.append(f"Fork of: {repo.fork_source or 'unknown'}")
    if repo.license:
        parts.append(f"License: {repo.license}")
    return "\n".join(parts)


def build_context(
    repo,
    readme_content: Optional[str] = None,
    file_tree: Optional[str] = None,
    dependency_manifest: Optional[str] = None,
    entry_point_code: Optional[str] = None,
    max_tokens: int = 2000,
) -> str:
    """Build LLM context for a repository within a token budget.
    
    Assembles context in priority order, allocating token budget
    to each section. Returns a formatted string ready for the prompt.
    """
    sections = []
    remaining_tokens = max_tokens

    # 1. Metadata (always included, ~100 tokens)
    metadata = build_metadata_section(repo)
    metadata_tokens = _estimate_tokens(metadata)
    sections.append(("METADATA", metadata))
    remaining_tokens -= metadata_tokens

    # 2. Dependency manifest (~200 tokens budget)
    if dependency_manifest and remaining_tokens > 50:
        dep_budget = min(200, remaining_tokens)
        dep_text = _truncate_to_budget(dependency_manifest, dep_budget)
        sections.append(("DEPENDENCIES", dep_text))
        remaining_tokens -= _estimate_tokens(dep_text)

    # 3. File tree (~300 tokens budget)
    if file_tree and remaining_tokens > 50:
        tree_budget = min(300, remaining_tokens)
        tree_text = _truncate_to_budget(file_tree, tree_budget)
        sections.append(("FILE STRUCTURE", tree_text))
        remaining_tokens -= _estimate_tokens(tree_text)

    # 4. README (up to ~800 tokens)
    if readme_content and remaining_tokens > 50:
        readme_budget = min(800, remaining_tokens)
        readme_text = _truncate_to_budget(readme_content, readme_budget)
        sections.append(("README", readme_text))
        remaining_tokens -= _estimate_tokens(readme_text)

    # 5. Entry point code (remainder of budget)
    if entry_point_code and remaining_tokens > 50:
        code_text = _truncate_to_budget(entry_point_code, remaining_tokens)
        sections.append(("MAIN SOURCE FILE", code_text))

    # Format
    output_parts = []
    for title, content in sections:
        output_parts.append(f"=== {title} ===")
        output_parts.append(content)
        output_parts.append("")

    return "\n".join(output_parts)
