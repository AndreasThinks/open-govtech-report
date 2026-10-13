---
title: GovTech Decision Scorer
emoji: 🏛️
colorFrom: gray
colorTo: blue
sdk: gradio
sdk_version: 6.28.0
python_version: "3.12"
app_file: app.py
short_description: Kev decision model batch scoring for open-govtech-report
startup_duration_timeout: 1h
license: apache-2.0
models:
  - jaredpalmer/kev-4b
  - Qwen/Qwen3.5-4B-Base
---

# GovTech decision scorer

Batch endpoint used by `govtech-scraper decide tag --backend space`. It runs
[Kev-4B](https://huggingface.co/jaredpalmer/kev-4b), an open-weight decision model that speaks
TypeSafe's System One format, on ZeroGPU.

`score_batch` takes a JSON list of `{"state": ..., "questions": {...}}` requests and returns one
response per request, so a whole batch of repositories shares one GPU allocation.

Source: [open-govtech-report/spaces/decision-scorer](https://github.com/AndreasThinks/open-govtech-report/tree/main/spaces/decision-scorer).
Publish with `spaces/decision-scorer/publish.sh`.
