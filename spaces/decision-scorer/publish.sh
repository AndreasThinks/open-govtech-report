#!/usr/bin/env bash
# Publish the decision scorer Space, vendoring Kev's loader and API modules at a pinned commit.
#   spaces/decision-scorer/publish.sh [space_id]
# Needs `hf auth login` with a PRO account. First time only:
#   hf repos create <you>/govtech-decision-scorer --type space --space-sdk gradio --private
#   then pick "ZeroGPU" hardware in the Space settings.
set -euo pipefail
cd "$(dirname "$0")"
SPACE="${1:-AndreasThinks/govtech-decision-scorer}"
KEV_REF="fc4a17e194bca35a48c57211392ec49ea54a94ac"   # same commit as Kev's own Modal deploy script
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp app.py requirements.txt README.md "$STAGE/"
mkdir -p "$STAGE/kev"
for module in __init__ api checkpoint model; do
  curl -fsSL "https://raw.githubusercontent.com/jaredpalmer/kev/$KEV_REF/kev/$module.py" -o "$STAGE/kev/$module.py"
done
python3 -m py_compile "$STAGE/app.py"
hf upload "$SPACE" "$STAGE" . --repo-type space --commit-message "Publish decision scorer (kev@${KEV_REF:0:8})"
echo "https://huggingface.co/spaces/$SPACE"
