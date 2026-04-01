#!/bin/bash
# Wait for the tagger process to finish, then publish to HuggingFace.
# Usage: called by cron, expects TAGGER_PID as argument or detects automatically.

set -euo pipefail

LOGFILE="/tmp/govtech-publish.log"
DB_PATH="/home/andreasclaw/projects/govtech-scraper/govtech.db"
SCRIPT_DIR="/home/andreasclaw/projects/govtech-scraper"

log() { echo "$(date '+%H:%M:%S') $*" | tee -a "$LOGFILE"; }

log "=== publish_when_tagged.sh started ==="

# Source env for HF_TOKEN and OPENROUTER_API_KEY
set -a
source /home/andreasclaw/.hermes/.env
set +a

# Wait for tagger to finish
TAGGER_PID="${1:-}"
if [ -n "$TAGGER_PID" ]; then
    log "Waiting for tagger PID $TAGGER_PID to finish..."
    while kill -0 "$TAGGER_PID" 2>/dev/null; do
        sleep 30
    done
    log "Tagger PID $TAGGER_PID has exited."
else
    log "No PID given, checking if tagger is still running..."
    while pgrep -f "govtech-scraper.*tag" > /dev/null 2>&1; do
        log "Tagger still running, sleeping 60s..."
        sleep 60
    done
    log "Tagger is no longer running."
fi

# Quick sanity check — confirm tagging actually progressed
cd "$SCRIPT_DIR"
TAGGED=$(python3 -c "
import sqlite3
conn = sqlite3.connect('govtech.db')
tagged = conn.execute('SELECT COUNT(DISTINCT html_url) FROM repository_tags').fetchone()[0]
total = conn.execute('SELECT COUNT(*) FROM repositories').fetchone()[0]
print(f'{tagged}/{total}')
conn.close()
")
log "Tagged repos: $TAGGED"

# Export data files
log "Exporting CSV and Parquet..."
uv run python3 -c "
from govtech_scraper.db import Database
from govtech_scraper.export import export_to_csv, export_to_parquet, export_tag_groups_csv, export_tag_group_members_csv
export_to_csv('govtech.db', 'government_repos_latest.csv')
print('CSV done')
export_tag_groups_csv('govtech.db', 'tag_groups.csv')
print('tag_groups CSV done')
export_tag_group_members_csv('govtech.db', 'tag_group_members.csv')
print('tag_group_members CSV done')
try:
    export_to_parquet('govtech.db', 'government_repos_latest.parquet')
    print('Parquet done')
except Exception as e:
    print(f'Parquet skipped: {e}')
" 2>&1 | tee -a "$LOGFILE"

# Publish to HuggingFace
log "Publishing to HuggingFace..."
uv run python3 scripts/publish_hf.py 2>&1 | tee -a "$LOGFILE"

log "=== Publish complete ==="
