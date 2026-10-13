#!/bin/sh
# Download the database, then serve the site, REST API and MCP endpoint.
# GOVTECH_DB_URL picks a different database file, e.g. one carrying a decision-model run that
# has not reached the weekly publication yet. HF_TOKEN, if set, is sent so the file can be private.
set -e
python -c "
import os
from govtech_scraper.publication import restore_database
restore_database(
    os.environ.get('GOVTECH_DB_URL') or
    'https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/main/data/govtech.db',
    'govtech.db', token=os.environ.get('HF_TOKEN') or None)
"
exec govtech-scraper --db govtech.db serve-api --host 0.0.0.0 --port 7860
