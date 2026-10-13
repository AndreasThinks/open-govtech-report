---
title: Open GovTech Index
emoji: 🏛️
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
short_description: Government open source by theme, with a REST and MCP API
license: mit
datasets:
  - AndreasThinks/government-github-repos
---

# Open GovTech Index

The Open GovTech Index site, plus read-only REST and MCP access to public repositories owned by accounts in the
[government.github.com](https://github.com/github/government.github.com) registry, classified
against the UN COFOG functions of government.

- Site: `/`
- REST: `/v1/overview`, `/v1/themes`, `/v1/themes/{id}`, `/v1/repos`, `/v1/repos/{owner}/{name}/context`, `/v1/repos/{owner}/{name}`, `/v1/countries`, `/v1/taxonomy`, `/v1/stats`
- OpenAPI docs: `/docs`
- MCP (streamable HTTP, stateless): `/mcp/`

Space variables: `GOVTECH_REF` (branch, tag or commit to install, default `main`) and
`GOVTECH_DB_URL` (database to serve, default the public dataset). A private `GOVTECH_DB_URL`
needs an `HF_TOKEN` secret.

The database is downloaded from the public dataset each time the Space starts. Source:
[open-govtech-report](https://github.com/AndreasThinks/open-govtech-report).
