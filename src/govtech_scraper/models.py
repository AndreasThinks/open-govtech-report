"""Typed data models for repository metadata."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GovernmentAccount:
    """A government GitHub account (org or user)."""
    username: str
    country: str
    account_type: str = "org"  # "org" or "user", determined at fetch time


@dataclass
class Repository:
    """Repository metadata snapshot."""
    owner: str
    name: str
    html_url: str
    country: str
    description: Optional[str] = None
    language: Optional[str] = None
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues: int = 0
    size_kb: int = 0
    license: Optional[str] = None
    topics: list[str] = field(default_factory=list)
    default_branch: str = "main"
    fork: bool = False
    archived: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    pushed_at: Optional[str] = None
    commit_count: int = 0
    fork_source: Optional[str] = None
