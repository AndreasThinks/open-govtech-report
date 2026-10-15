# Contributing to the GovTech Dataset

The dataset is built from the [government.github.com](https://github.com/github/government.github.com) registry and a few government code servers outside GitHub. If you know of a government account that is missing, on GitHub or on another code platform, you can submit it here and it will be included in the next scrape.

The easiest way is the [Add an account](https://govtechindex.cloud/submit) page, which writes the file and opens it on GitHub for you. The steps below do the same by hand.

## Submitting a missing account

### Which platforms?

- **GitHub** accounts.
- **GitLab** groups and users, on gitlab.com or any self-hosted GitLab server.
- **Forgejo and Gitea** organisations and users, including Codeberg.

These are the platforms whose public APIs list an account's repositories without a token. Other platforms (Bitbucket, SourceHut) are not read yet; email contact@andreasthinks.me and we will note them.

Submit an account, not a single repository: every public repository in it is read each week. A whole government server (such as code.overheid.nl) is added by a maintainer to `src/govtech_scraper/forge_hosts.yaml`; open an issue to suggest one.

### 1. Check it isn't already included

Before submitting, check:

- [github/government.github.com](https://github.com/github/government.github.com/blob/gh-pages/_data/governments.yml) — the upstream registry
- [submissions/pending/](submissions/pending/) — already-accepted submissions in this repo
- [forge_hosts.yaml](src/govtech_scraper/forge_hosts.yaml) — government servers read in full

If it's in any of those, it's already in the dataset or will be soon.

### 2. Create a submission file

Copy the template and fill it in:

```bash
cp submissions/TEMPLATE.yaml submissions/pending/<country>-<account>.yaml
# Example:
cp submissions/TEMPLATE.yaml submissions/pending/gb-nhs-digital.yaml
```

**Filename convention:** lower case letters, digits and hyphens, ending in `.yaml`.

A GitHub account:

```yaml
org: nhs-digital           # exact GitHub org/user login
country: GB                # ISO code, country name, or registry label
account_type: org          # "org" or "user"
description: "UK National Health Service digital services"
evidence:
  - https://digital.nhs.uk
```

An account on another platform:

```yaml
host: gitlab.com           # the server
platform: gitlab           # gitlab, or forgejo (Forgejo, Gitea, Codeberg)
owner: commonground        # group or organisation path
same_as_github: ""         # optional: the same body's GitHub login, to skip copies
country: NL
account_type: org
description: "Common Ground, the shared platform of Dutch municipalities"
evidence:
  - https://commonground.nl
```

Repositories that are copies of ones already in the index (mirrors or migrations, or same-named repos in the `same_as_github` account) are skipped, so a body on both GitHub and another platform is not counted twice.

### 3. Open a pull request

Open a PR with your file. The automated validation will check that:

- All required fields are present
- The account name has a valid format for its platform
- The country is a known ISO code, or a name
- At least one https:// evidence link is provided
- The account exists, by asking the platform's API

The PR template will guide you through what's needed.

### What counts as evidence?

The goal is to confirm this is a real government entity, not a personal project or a private company. Good evidence includes:

- Official government website listing the org
- Wikipedia article describing the organisation
- Entry in a national government register
- Official press release or announcement

### What happens after merge?

Once the PR is merged, the account is picked up automatically on the next weekly scrape (Sundays at 02:00 UTC). It will appear in the dataset and on [govtechindex.cloud](https://govtechindex.cloud) after that run completes. Repositories from platforms other than GitHub are counted and searchable straight away, but are not sorted into themes yet.

---

## Notes

- This is a solo-maintained project — there's no guaranteed turnaround time on reviews
- The maintainer may ask for additional evidence or close submissions that can't be verified
- Submissions that duplicate entries in the upstream registry will be deduplicated automatically
