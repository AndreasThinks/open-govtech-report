#!/bin/bash

# Run the scraper in the background
python main.py &

# Start Datasette with our custom templates and plugins
datasette government_repos.db \
  --host 0.0.0.0 \
  --port 8001 \
  --template-dir templates \
  --plugins-dir plugins \
  --metadata metadata.json \
  --setting sql_time_limit_ms 5000 \
  --setting allow_download off \
  --setting default_cache_ttl 300
