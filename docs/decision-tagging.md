# Decision-model tagging (experimental)

An alternative to the generative `tagger/` pipeline. A decision model reads a repository's
metadata and returns probabilities over options we supply. It never writes text. So the tag
vocabulary is a reviewed file, `src/govtech_scraper/decision/taxonomy.yaml`, and there is nothing
to deduplicate or cluster.

The results go in their own tables (`decision_results`, `decision_probabilities`). The existing
LLM tags in `repository_tags` are never touched, so both pipelines can run side by side.

## Taxonomy

- **Domain:** one of the ten UN COFOG functions of government (`01` to `10`), plus `00` for
  cross-government capabilities such as identity, payments or design systems.
- **Tags:** 71 tags, each with a one-line definition and grouped under a domain. The model reads
  the definitions.
- **Kind:** what sort of repository it is, such as a library, dataset or web application.

Bump `version` whenever a tag is added, removed or redefined. Results are stored per version,
so a new version rescores everything.

`govtech-scraper decide taxonomy` validates the file and prints it.

## How a repository is scored

1. A choice question for the domain, a choice question for the kind, and a yes/no question for
   whether the repo is substantive.
2. A yes/no question for each tag in the three most likely domains, plus every `00` capability
   tag. Use `--max-domains 11` to ask about every tag.

A tag applies when its probability is at least 0.5. Every probability is stored, so you can pick
a different threshold later. Repos are rescored only when their text or the taxonomy version
changes.

## Backends

All three speak TypeSafe's System One format (`POST /v1/systemone`).

| `--backend` | What it is | Needs |
| --- | --- | --- |
| `space` (default) | Kev-4B on our own ZeroGPU Space, many repos per GPU call | `HF_TOKEN`, `DECISION_SPACE_ID`, `uv sync --extra decision` |
| `kev` | Any `kev.serve` endpoint: your Mac, a GPU box or Modal | `KEV_BASE_URL` (default `http://127.0.0.1:8009`), optional `KEV_API_KEY` |
| `jev` | TypeSafe's hosted Jev | `TYPESAFE_API_KEY` |

Each backend's results are stored under its own model id, so runs can be compared.

### Setting up the Space

```bash
hf auth login                                   # a PRO account, for ZeroGPU
hf repos create AndreasThinks/govtech-decision-scorer --repo-type space --space-sdk gradio --private
# In the Space settings, choose ZeroGPU hardware.
spaces/decision-scorer/publish.sh AndreasThinks/govtech-decision-scorer
```

The first start downloads Kev-4B and its Qwen3.5 base. Calls made with your `HF_TOKEN` use your
own ZeroGPU quota, which is 40 minutes a day on PRO. When the quota runs out, the run stops and
saves its progress. Run it again the next day to carry on.

### Running Kev locally instead

```bash
git clone https://github.com/jaredpalmer/kev.git && cd kev
uv sync --extra serve
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8009
# In this repo:
uv run govtech-scraper decide tag --backend kev --limit 50
```

## Testing whether it works

```bash
uv run govtech-scraper decide sample -n 300 --out decision-gold.csv     # random repos to label
uv run govtech-scraper decide label-gold decision-gold.csv              # optional strong-LLM pre-label (OpenRouter)
# Open the CSV and correct gold_domain, gold_kind and gold_tags (separate tags with semicolons).
uv run govtech-scraper decide tag --gold decision-gold.csv               # score just those repos
uv run govtech-scraper decide runs                                       # see the stored model ids
uv run govtech-scraper decide eval decision-gold.csv --model space:AndreasThinks/govtech-decision-scorer
uv run govtech-scraper decide show https://github.com/alphagov/notifications-api --model <model id>
```

`decide eval` reports domain accuracy (top-1 and top-2), kind accuracy, and tag precision,
recall and F1 at thresholds from 0.3 to 0.8. Pass `--model` more than once to compare backends
on the same gold set.

## Open questions

- Accuracy on short, metadata-only inputs is unknown until the gold-set eval runs.
- README text would likely help more than any model change. `build_state` already accepts it,
  but README fetching is not implemented yet.
- Not yet tested: Space throughput, and whether a scheduled job can rely on it.
