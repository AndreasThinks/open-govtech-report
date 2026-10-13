"""Shared repository cohort, independent of Streamlit and network access."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Filters:
    countries: tuple = ()
    languages: tuple = ()
    orgs: tuple = ()
    tags: tuple = ()
    include_archived: bool = False
    include_forks: bool = True
    min_stars: int = 0
    activity_days: int | None = None
    search: str = ''


def build_where(filters, alias='r'):
    if alias != 'r':
        raise ValueError('Repository alias must be r')
    conditions, params = [], []
    for column, values in [('country', filters.countries), ('owner', filters.orgs), ('language', filters.languages)]:
        if values:
            conditions.append(f'r.{column} IN ({",".join("?" for _ in values)})')
            params.extend(values)
    if filters.tags:
        conditions.append('EXISTS (SELECT 1 FROM repository_tags selected_tag WHERE selected_tag.html_url=r.html_url '
                          f'AND selected_tag.tag IN ({",".join("?" for _ in filters.tags)}))')
        params.extend(filters.tags)
    if not filters.include_archived:
        conditions.append('COALESCE(r.archived,0)=0')
    if not filters.include_forks:
        conditions.append('COALESCE(r.fork,0)=0')
    if filters.min_stars:
        conditions.append('r.stars>=?')
        params.append(filters.min_stars)
    if filters.activity_days:
        conditions.append('r.pushed_at>=?')
        params.append((datetime.now(timezone.utc)-timedelta(days=filters.activity_days)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    if filters.search:
        conditions.append('(r.name LIKE ? OR r.description LIKE ?)')
        params.extend([f'%{filters.search}%'] * 2)
    return ('WHERE ' + ' AND '.join(conditions) if conditions else ''), params
