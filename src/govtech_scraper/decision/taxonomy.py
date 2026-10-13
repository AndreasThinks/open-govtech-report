"""Fixed, versioned taxonomy that decision models score repositories against."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_TAXONOMY_PATH = Path(__file__).with_name("taxonomy.yaml")

_ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_DOMAIN_ID_PATTERN = re.compile(r"^\d{2}$")

# System One choice questions accept at most 255 options.
MAX_CHOICE_OPTIONS = 255


@dataclass(frozen=True)
class Tag:
    id: str
    definition: str
    domain_id: str


@dataclass(frozen=True)
class Domain:
    id: str
    name: str
    definition: str
    tags: tuple[Tag, ...]


@dataclass(frozen=True)
class Kind:
    id: str
    definition: str


@dataclass(frozen=True)
class Taxonomy:
    version: int
    domains: tuple[Domain, ...]
    kinds: tuple[Kind, ...]

    @property
    def tags(self) -> list[Tag]:
        return [tag for domain in self.domains for tag in domain.tags]

    def domain(self, domain_id: str) -> Domain:
        for domain in self.domains:
            if domain.id == domain_id:
                return domain
        raise KeyError(domain_id)

    def tag(self, tag_id: str) -> Tag:
        for tag in self.tags:
            if tag.id == tag_id:
                return tag
        raise KeyError(tag_id)


class TaxonomyError(ValueError):
    """Raised when the taxonomy file is malformed."""


def _require(entry: dict, key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaxonomyError(f"{where}: missing '{key}'")
    return value.strip()


def parse_taxonomy(raw: dict) -> Taxonomy:
    """Validate and convert a parsed YAML mapping into a Taxonomy."""
    if not isinstance(raw, dict):
        raise TaxonomyError("taxonomy must be a mapping")
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise TaxonomyError("'version' must be a positive integer")

    seen_ids: set[str] = set()

    def claim(item_id: str, where: str) -> None:
        if item_id in seen_ids:
            raise TaxonomyError(f"{where}: duplicate id '{item_id}'")
        seen_ids.add(item_id)

    domains = []
    for entry in raw.get("domains") or []:
        domain_id = _require(entry, "id", "domain")
        if not _DOMAIN_ID_PATTERN.match(domain_id):
            raise TaxonomyError(f"domain '{domain_id}': id must be two digits")
        claim(domain_id, "domain")
        tags = []
        for tag_entry in entry.get("tags") or []:
            tag_id = _require(tag_entry, "id", f"domain {domain_id} tag")
            if not _ID_PATTERN.match(tag_id):
                raise TaxonomyError(f"tag '{tag_id}': id must be lowercase and hyphenated")
            claim(tag_id, f"domain {domain_id}")
            tags.append(Tag(tag_id, _require(tag_entry, "definition", tag_id), domain_id))
        if not tags:
            raise TaxonomyError(f"domain '{domain_id}' has no tags")
        domains.append(
            Domain(
                domain_id,
                _require(entry, "name", domain_id),
                _require(entry, "definition", domain_id),
                tuple(tags),
            )
        )

    kinds = []
    for entry in raw.get("kinds") or []:
        kind_id = _require(entry, "id", "kind")
        if not _ID_PATTERN.match(kind_id):
            raise TaxonomyError(f"kind '{kind_id}': id must be lowercase and hyphenated")
        claim(kind_id, "kinds")
        kinds.append(Kind(kind_id, _require(entry, "definition", kind_id)))

    if not domains:
        raise TaxonomyError("taxonomy has no domains")
    if not kinds:
        raise TaxonomyError("taxonomy has no kinds")
    if len(domains) > MAX_CHOICE_OPTIONS or len(kinds) > MAX_CHOICE_OPTIONS:
        raise TaxonomyError(f"choice questions allow at most {MAX_CHOICE_OPTIONS} options")

    return Taxonomy(version, tuple(domains), tuple(kinds))


def load_taxonomy(path: str | Path | None = None) -> Taxonomy:
    """Load the taxonomy YAML, defaulting to the one shipped with the package."""
    with open(path or DEFAULT_TAXONOMY_PATH, encoding="utf-8") as f:
        return parse_taxonomy(yaml.safe_load(f))
