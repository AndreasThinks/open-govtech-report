"""
Example script demonstrating how to use the DatabaseManager with both SQLite and PostgreSQL.
This can also be used as a cron job by setting up appropriate environment variables.
"""

import os
import pandas as pd
from dotenv import load_dotenv
import logging
from db_operations import DatabaseManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    # Load environment variables
    load_dotenv()
    
    try:
        # Initialize database manager (will use PostgreSQL if configured, otherwise SQLite)
        db = DatabaseManager()
        
        # Example: Create sample data
        sample_data = pd.DataFrame({
            'name': ['test-repo'],
            'description': ['Test repository'],
            'stars': [10],
            'forks': [5],
            'language': ['Python'],
            'username': ['test-user'],
            'country': ['US'],
            'html_url': ['https://github.com/test-user/test-repo'],
            'created_at': ['2024-01-01T00:00:00Z'],
            'updated_at': ['2024-01-01T00:00:00Z'],
            'archived': [False],
            'fork': [False],
            'fork_source': [None],
            'size_kb': [1000],
            'open_issues': [2],
            'watchers': [10],
            'default_branch': ['main'],
            'license': ['MIT']
        })
        
        # Save repositories
        db.save_repositories(sample_data)
        logger.info("Saved sample data")
        
        # Get latest snapshot
        latest = db.get_latest_snapshot()
        logger.info(f"Retrieved {len(latest)} repositories from latest snapshot")
        
        # Close connection
        db.close()
        
    except Exception as e:
        logger.error(f"Error in database operations: {e}")
        raise

if __name__ == "__main__":
    main()
