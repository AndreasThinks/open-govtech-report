"""Tests for context builder."""

from govtech_scraper.tagger.context import (
    build_context,
    build_metadata_section,
    _truncate_to_budget,
    _estimate_tokens,
)


def test_metadata_section(sample_repo):
    section = build_metadata_section(sample_repo)
    assert "gov-org/data-portal" in section
    assert "Python" in section
    assert "open-data" in section
    assert "150" in section


def test_metadata_archived_repo(sample_repo):
    sample_repo.archived = True
    section = build_metadata_section(sample_repo)
    assert "ARCHIVED" in section


def test_metadata_fork(sample_repo):
    sample_repo.fork = True
    sample_repo.fork_source = "original/repo"
    section = build_metadata_section(sample_repo)
    assert "Fork of: original/repo" in section


def test_build_context_metadata_only(sample_repo):
    ctx = build_context(sample_repo)
    assert "=== METADATA ===" in ctx
    assert "gov-org/data-portal" in ctx


def test_build_context_with_readme(sample_repo):
    readme = "# Data Portal\n\nA portal for government open data."
    ctx = build_context(sample_repo, readme_content=readme)
    assert "=== README ===" in ctx
    assert "Data Portal" in ctx


def test_build_context_with_all_sections(sample_repo):
    ctx = build_context(
        sample_repo,
        readme_content="# README content",
        file_tree="src/\n  main.py\n  utils.py",
        dependency_manifest="flask>=2.0\npandas>=1.5",
        entry_point_code="from flask import Flask\napp = Flask(__name__)",
    )
    assert "=== METADATA ===" in ctx
    assert "=== DEPENDENCIES ===" in ctx
    assert "=== FILE STRUCTURE ===" in ctx
    assert "=== README ===" in ctx
    assert "=== MAIN SOURCE FILE ===" in ctx


def test_truncation():
    long_text = "a" * 10000
    truncated = _truncate_to_budget(long_text, 100)  # 100 tokens = ~400 chars
    assert len(truncated) < 10000
    assert "truncated" in truncated


def test_no_truncation_short_text():
    short = "hello world"
    result = _truncate_to_budget(short, 100)
    assert result == short


def test_token_budget_respected(sample_repo):
    """Context should respect the token budget even with lots of content."""
    long_readme = "x" * 50000
    long_code = "y" * 50000
    ctx = build_context(
        sample_repo,
        readme_content=long_readme,
        entry_point_code=long_code,
        max_tokens=500,
    )
    # Rough check: 500 tokens * 4 chars = 2000 chars, allow some overhead for headers
    assert len(ctx) < 5000
