"""Split government.github.com registry labels into a country and a level of government.

The registry labels accounts with one string that mixes countries ("Norway"), parts of a
government ("U.K. Councils", "U.S. City") and international bodies ("United Nations"). The site
shows the label as published, but groups and filters by country and level.
"""

import re

NATIONAL, REGIONAL, LOCAL, TRIBAL, INTERNATIONAL = (
    "national",
    "regional",
    "local",
    "tribal",
    "international",
)
LEVELS = (NATIONAL, REGIONAL, LOCAL, TRIBAL, INTERNATIONAL)

# Labels whose country or level differs from "the label is a country, national government".
_KNOWN: dict[str, tuple[str, str]] = {
    "U.K. Central": ("United Kingdom", NATIONAL),
    "U.K. Councils": ("United Kingdom", LOCAL),
    "U.S. Federal": ("United States", NATIONAL),
    "U.S. Military and Intelligence": ("United States", NATIONAL),
    "U.S. States": ("United States", REGIONAL),
    "U.S. City": ("United States", LOCAL),
    "U.S. County": ("United States", LOCAL),
    "U.S. Special District": ("United States", LOCAL),
    "U.S. Local Law Enforcement": ("United States", LOCAL),
    "U.S. Tribal Nations": ("United States", TRIBAL),
    "United Nations": ("International", INTERNATIONAL),
    "European Union": ("International", INTERNATIONAL),
    "International": ("International", INTERNATIONAL),
    "Luxemburg": ("Luxembourg", NATIONAL),
    "French Polynesia": ("France", REGIONAL),
}


def place(label: str | None) -> dict:
    """{"label", "slug", "country", "level"} for one registry label."""
    label = label or "Unknown"
    country, level = _KNOWN.get(label, (label, NATIONAL))
    return {"label": label, "slug": slug(label), "country": country, "level": level}


def slug(label: str) -> str:
    """URL id for a label: "U.K. Central" -> "uk-central"."""
    return re.sub(r"[^a-z0-9]+", "-", label.lower().replace(".", "")).strip("-") or "unknown"
