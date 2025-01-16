"""
Database operations module supporting both SQLite and PostgreSQL backends.
Configuration is handled through environment variables:
- DB_TYPE: 'sqlite' or 'postgresql'
- POSTGRES_URL: PostgreSQL connection URL (required if DB_TYPE=postgresql)
- SQLITE_PATH: Path to SQLite database file (default: government_repos.db)
"""

import os
from datetime import datetime
from typing import Optional
import pandas as pd
from sqlalchemy import create_engine, text, MetaData, Table, Column, Integer, String, Boolean, DateTime, Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class DatabaseManager:
    def __init__(self):
        """
        Initialize database connection based on environment variables.
        Falls back to SQLite if PostgreSQL connection is not configured or fails.
        """
        self.db_type = os.getenv('DB_TYPE', 'sqlite').lower()
        self.sqlite_path = os.getenv('SQLITE_PATH', 'government_repos.db')
        self.engine: Optional[Engine] = None
        self.setup_database_connection()
        self.init_db()

    def setup_database_connection(self):
        """Set up the database connection with fallback to SQLite."""
        if self.db_type == 'postgresql':
            postgres_url = os.getenv('POSTGRES_URL')
            if not postgres_url:
                logger.warning("PostgreSQL URL not found, falling back to SQLite")
                self.db_type = 'sqlite'
            else:
                try:
                    self.engine = create_engine(postgres_url)
                    # Test connection
                    with self.engine.connect() as conn:
                        conn.execute(text('SELECT 1'))
                        conn.commit()
                    logger.info("Successfully connected to PostgreSQL")
                    return
                except OperationalError as e:
                    logger.error(f"PostgreSQL connection failed: {e}")
                    logger.warning("Falling back to SQLite")
                    self.db_type = 'sqlite'
        
        # SQLite fallback
        self.engine = create_engine(f'sqlite:///{self.sqlite_path}')
        logger.info(f"Using SQLite database at {self.sqlite_path}")

    def init_db(self):
        """Initialize the database with the required schema."""
        if not self.engine:
            raise RuntimeError("Database engine not initialized")
            
        metadata = MetaData()
        
        # Define the repository_snapshots table
        Table('repository_snapshots', metadata,
            Column('name', String),
            Column('description', String),
            Column('stars', Integer),
            Column('forks', Integer),
            Column('language', String),
            Column('username', String),
            Column('country', String),
            Column('html_url', String),
            Column('created_at', String),
            Column('updated_at', String),
            Column('archived', Boolean),
            Column('fork', Boolean),
            Column('fork_source', String),
            Column('size_kb', Integer),
            Column('open_issues', Integer),
            Column('watchers', Integer),
            Column('default_branch', String),
            Column('license', String),
            Column('readme_content', String),
            Column('readme_size', Integer),
            Column('readme_encoding', String),
            Column('scrape_timestamp', String),
            extend_existing=True
        )
        
        # Create tables
        metadata.create_all(self.engine)
        logger.info("Database schema initialized")

    def save_repositories(self, input_df: pd.DataFrame, readme_df: Optional[pd.DataFrame] = None):
        """Save repository data to database with current timestamp."""
        if not self.engine:
            raise RuntimeError("Database engine not initialized")
            
        columns_to_store = [
            'name', 'description', 'stars', 'forks', 'language', 'username',
            'country', 'html_url', 'created_at', 'updated_at', 'archived',
            'fork', 'fork_source', 'size_kb', 'open_issues', 'watchers',
            'default_branch', 'license'
        ]
        
        # Process input data
        df = input_df[columns_to_store].copy()
        
        # Handle README data if provided
        if readme_df is not None:
            readme_df = readme_df.copy()
            readme_df = readme_df.rename(columns={
                'repo_url': 'html_url',
                'readme_url': 'readme_url'  # Not stored in database
            })
            
            df = pd.merge(
                df,
                readme_df[['html_url', 'readme_content', 'readme_size', 'readme_encoding']],
                on='html_url',
                how='left'
            )
        else:
            df['readme_content'] = None
            df['readme_size'] = None
            df['readme_encoding'] = None
        
        # Add timestamp
        df['scrape_timestamp'] = datetime.utcnow().isoformat()
        
        try:
            # Save to database
            df.to_sql('repository_snapshots', self.engine, 
                     if_exists='append', index=False,
                     method='multi' if self.db_type == 'postgresql' else None)
            logger.info(f"Saved {len(df)} repositories to database")
        except SQLAlchemyError as e:
            logger.error(f"Error saving to database: {e}")
            raise

    def get_latest_snapshot(self) -> pd.DataFrame:
        """Get the most recent snapshot of all repositories."""
        if not self.engine:
            raise RuntimeError("Database engine not initialized")
            
        query = """
        WITH LatestTimestamps AS (
            SELECT html_url, MAX(scrape_timestamp) as max_timestamp
            FROM repository_snapshots
            GROUP BY html_url
        )
        SELECT r.*
        FROM repository_snapshots r
        JOIN LatestTimestamps lt
            ON r.html_url = lt.html_url
            AND r.scrape_timestamp = lt.max_timestamp
        """
        return pd.read_sql_query(query, self.engine)

    def get_snapshots_for_repo(self, html_url: str) -> pd.DataFrame:
        """Get all snapshots for a specific repository."""
        if not self.engine:
            raise RuntimeError("Database engine not initialized")
            
        query = """
        SELECT *
        FROM repository_snapshots
        WHERE html_url = :html_url
        ORDER BY scrape_timestamp DESC
        """
        return pd.read_sql_query(query, self.engine, params={'html_url': html_url})

    def close(self):
        """Close the database connection."""
        if self.engine is not None:
            self.engine.dispose()
            logger.info("Database connection closed")
