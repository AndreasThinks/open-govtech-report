# Contributing to the GovTech Dataset

The dataset is built from the [government.github.com](https://github.com/github/government.github.com) registry. If you know of a government GitHub organisation that isn't in that upstream list, you can submit it here and it will be included in the next scrape.

## Submitting a missing organisation

### 1. Check it isn't already included

Before submitting, check:

- [github/government.github.com](https://github.com/github/government.github.com/blob/gh-pages/_data/governments.yml) — the upstream registry
- [submissions/pending/](submissions/pending/) — already-accepted submissions in this repo

If it's in either of those, it's already in the dataset or will be soon.

### 2. Create a submission file

Copy the template and fill it in:

```bash
cp submissions/TEMPLATE.yaml submissions/pending/<country-code>-<org-name>.yaml
# Example:
cp submissions/TEMPLATE.yaml submissions/pending/gb-nhs-digital.yaml
```

**Filename convention:** `<ISO-country-code>-<github-org-name>.yaml` — all lowercase, hyphens only.

Fill in all required fields:

```yaml
org: nhs-digital           # exact GitHub org/user login
country: GB                # ISO 3166-1 alpha-2 country code
account_type: org          # "org" or "user"
description: "UK National Health Service digital services"
evidence:
  - https://digital.nhs.uk
  - https://en.wikipedia.org/wiki/NHS_Digital
```

### 3. Open a pull request

Open a PR with your file. The automated validation will check that:

- All required fields are present
- The org name is a valid GitHub login format
- The country code is a valid ISO 3166-1 alpha-2 code
- At least one evidence link is provided
- The filename matches the country code

The PR template will guide you through what's needed.

### What counts as evidence?

The goal is to confirm this is a real government entity, not a personal project or a private company. Good evidence includes:

- Official government website listing the org
- Wikipedia article describing the organisation
- Entry in a national government register
- Official press release or announcement

### What happens after merge?

Once the PR is merged, the organisation is picked up automatically on the next weekly scrape (Sundays at 02:00 UTC). It will appear in the dataset and the [live dashboard](https://huggingface.co/spaces/AndreasThinks/govtech-dashboard) after that run completes.

---

## Notes

- This is a solo-maintained project — there's no guaranteed turnaround time on reviews
- The maintainer may ask for additional evidence or close submissions that can't be verified
- Submissions that duplicate entries in the upstream registry will be deduplicated automatically
