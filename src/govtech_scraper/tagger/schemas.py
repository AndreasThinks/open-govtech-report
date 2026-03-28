"""Pydantic models for structured LLM output."""

from pydantic import BaseModel, Field


class TagSuggestion(BaseModel):
    """A single suggested tag with metadata."""
    tag: str = Field(description="Lowercase, hyphenated tag (e.g. 'data-pipeline', 'web-scraper')")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0-1")
    reasoning: str = Field(description="One-line explanation of why this tag fits")


class TaggingResult(BaseModel):
    """Complete tagging output for a repository."""
    tags: list[TagSuggestion] = Field(max_length=8, description="Suggested tags, max 8")
    primary_category: str = Field(description="Single best-fit domain category")
    tech_stack: list[str] = Field(default_factory=list, description="Detected technologies/frameworks")


class DeduplicationDecision(BaseModel):
    """LLM decision on whether two tags represent the same concept."""
    same_concept: bool = Field(description="Whether the two tags mean the same thing")
    preferred_tag: str = Field(description="Which tag to keep if they're the same concept")
    reasoning: str = Field(description="Brief explanation")
