"""Download govtech.db from HuggingFace dataset, fall back gracefully."""

import os
import sys
import urllib.request
import pathlib


def main():
    token = os.environ.get("HF_TOKEN", "")
    hf_url = "https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/main/data/govtech.db"
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    req = urllib.request.Request(hf_url, headers=headers)
    try:
        print("Downloading govtech.db from HuggingFace dataset...")
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = resp.read()
        pathlib.Path("govtech.db").write_bytes(data)
        size_mb = len(data) / 1024 / 1024
        print(f"Downloaded {size_mb:.1f} MB from HuggingFace")
    except Exception as e:
        print(f"HuggingFace download failed: {e}")
        print("Will start with empty database.")


if __name__ == "__main__":
    main()
