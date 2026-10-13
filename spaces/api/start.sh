#!/bin/sh
# Download the latest validated database from the public dataset, then serve it.
set -e
python -c "
from govtech_scraper.publication import restore_database
restore_database(
    'https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/main/data/govtech.db',
    'govtech.db')
"
exec govtech-scraper --db govtech.db serve-api --host 0.0.0.0 --port 7860
