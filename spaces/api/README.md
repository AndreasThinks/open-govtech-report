---
title: Government GitHub Repositories API
emoji: 🏛️
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
short_description: Open REST and MCP API over government GitHub repositories
license: mit
datasets:
  - AndreasThinks/government-github-repos
---

# Government GitHub repositories API

Read-only REST and MCP access to public repositories owned by accounts in the
[government.github.com](https://github.com/github/government.github.com) registry, classified
against the UN COFOG functions of government.

- REST: `/v1/repos`, `/v1/repos/{owner}/{name}`, `/v1/countries`, `/v1/taxonomy`, `/v1/stats`
- OpenAPI docs: `/docs`
- MCP (streamable HTTP, stateless): `/mcp/`

The database is downloaded from the public dataset each time the Space starts. Source:
[open-govtech-report](https://github.com/AndreasThinks/open-govtech-report).
