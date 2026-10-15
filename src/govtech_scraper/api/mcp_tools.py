"""MCP tools over the catalogue, so AI assistants can query the dataset.

Served over streamable HTTP at /mcp by the API app, or over stdio with
`govtech-scraper serve-mcp` for local clients such as Claude Desktop.
"""

from mcp.server.mcpserver import MCPServer

from .insights import Insights
from .queries import Catalogue

INSTRUCTIONS = """Query a catalogue of public repositories owned by government accounts listed
in the government.github.com registry, on government GitLab and Forgejo servers, and in accepted
community submissions. Repositories outside GitHub have a host-qualified owner such as
"gitlab.com/commonground" and are not classified yet. It is not a census of all government code. Repositories are classified against the UN COFOG functions of government
(domain ids "01" to "10", plus "00" for cross-government capabilities) and a fixed tag list;
call get_taxonomy for the ids. Evidence-mode classifications expose literal evidence and have
null probabilities; legacy probability runs remain model scores, not ground truth."""


def build_mcp(catalogue: Catalogue, insights: Insights | None = None) -> MCPServer:
    insights = insights or Insights(catalogue)
    mcp = MCPServer(
        name="govtech-repos",
        title="Government GitHub repositories",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/AndreasThinks/open-govtech-report",
    )

    @mcp.tool(title="Search repositories")
    def search_repositories(
        query: str | None = None,
        country: str | None = None,
        language: str | None = None,
        topic: str | None = None,
        domain: str | None = None,
        tag: str | None = None,
        kind: str | None = None,
        min_stars: int = 0,
        include_forks: bool = False,
        include_archived: bool = False,
        sort: str = "stars",
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Search government repositories.

        Args:
            query: Text matched against owner, name, description and topics.
            country: Country or registry label exactly as listed by list_countries.
            language: Primary language, e.g. "Python".
            topic: A GitHub topic, e.g. "open-data".
            domain: COFOG domain id from get_taxonomy, e.g. "07" for health.
            tag: Taxonomy tag id from get_taxonomy, e.g. "digital-identity".
            kind: Kind id from get_taxonomy, e.g. "library".
            min_stars: Minimum star count.
            include_forks: Include forks (off by default to show original work).
            include_archived: Include archived repositories.
            sort: One of stars, recently_pushed, newest, name.
            limit: Results per page, at most 100.
            offset: Results to skip, for paging.
        """
        return catalogue.search_repos(
            q=query,
            country=country,
            language=language,
            topic=topic,
            domain=domain,
            tag=tag,
            kind=kind,
            min_stars=min_stars,
            include_forks=include_forks,
            include_archived=include_archived,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(title="Get repository")
    def get_repository(owner: str, name: str) -> dict:
        """Full details for one repository, including its tags and COFOG classification.

        Args:
            owner: GitHub account, e.g. "alphagov".
            name: Repository name, e.g. "notifications-api".
        """
        repo = catalogue.get_repo(owner, name)
        if repo is None:
            raise ValueError(f"{owner}/{name} is not in the catalogue")
        return repo

    @mcp.tool(title="List countries")
    def list_countries() -> list[dict]:
        """Countries and registry labels, with repository, account and star counts."""
        return catalogue.list_countries()

    @mcp.tool(title="Get taxonomy")
    def get_taxonomy() -> dict:
        """The COFOG domains, tags and kinds used for classification, with repository counts."""
        return catalogue.taxonomy_summary()

    @mcp.tool(title="Catalogue statistics")
    def get_statistics() -> dict:
        """Headline numbers: repositories, accounts, countries, top languages and coverage."""
        return catalogue.stats()

    @mcp.tool(title="List themes")
    def list_themes() -> dict:
        """Every COFOG domain and tag with repository counts, 12-month growth and country spread.

        Use it to see which themes are large or growing fast."""
        return insights.themes()

    @mcp.tool(title="Get theme")
    def get_theme(theme: str) -> dict:
        """One domain or tag in detail: new repositories per month, countries, adopting accounts,
        related tags and top repositories.

        Args:
            theme: A domain id such as "07" or a tag id such as "digital-identity".
        """
        detail = insights.theme(theme)
        if detail is None:
            raise ValueError(f"{theme} is not a domain or tag; call get_taxonomy for the ids")
        return detail

    return mcp
