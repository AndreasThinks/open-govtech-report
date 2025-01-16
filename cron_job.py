#!/usr/bin/env python3
"""
Cron job script for database operations.
This script can be scheduled to run periodically to update the database.

Example crontab entry (runs every hour):
0 * * * * /usr/bin/python3 /path/to/cron_job.py

Make sure to set up environment variables in your crontab or use a wrapper script:
DB_TYPE=postgresql
POSTGRES_URL=postgresql://username:password@host:port/database
"""

import sys
import logging
import asyncio
from db_operations import DatabaseManager
import scrape_repos
import pandas as pd

# Configure logging to file and stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cron_job.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

async def main():
    try:
        # Initialize database manager
        db = DatabaseManager()
        
        # Get government GitHub accounts (you'll need to provide the URL)
        accounts = await scrape_repos.fetch_gov_github_accounts(
            "https://raw.githubusercontent.com/github/government.github.com/gh-pages/_data/governments.yml"
        )
        
        if not accounts:
            logger.error("Failed to fetch government accounts")
            sys.exit(1)
        
        # Fetch repository details
        repo_data = await scrape_repos.fetch_all_repository_details(
            accounts,
            force_update=False,  # Set to True to force refresh cache
            progress_callback=lambda x: logger.info(f"Processed {x} accounts")
        )
        
        # Convert to DataFrame
        df = pd.DataFrame(repo_data)
        
        # Save to database
        db.save_repositories(df)
        
        # Close database connection
        db.close()
        
        logger.info("Cron job completed successfully")
        
    except Exception as e:
        logger.error(f"Error in cron job: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
