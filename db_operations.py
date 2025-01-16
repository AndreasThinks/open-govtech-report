import sqlite3
from datetime import datetime
import pandas as pd

class DatabaseManager:
    def __init__(self, db_path: str = "government_repos.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initialize the SQLite database with the required schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repository_snapshots (
                    name TEXT,
                    description TEXT,
                    stars INTEGER,
                    forks INTEGER,
                    language TEXT,
                    username TEXT,
                    country TEXT,
                    html_url TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    archived BOOLEAN,
                    fork BOOLEAN,
                    fork_source TEXT,
                    size_kb INTEGER,
                    open_issues INTEGER,
                    watchers INTEGER,
                    default_branch TEXT,
                    license TEXT,
                    readme_content TEXT,
                    readme_size INTEGER,
                    readme_encoding TEXT,
                    scrape_timestamp TEXT,
                    PRIMARY KEY (html_url, scrape_timestamp)
                )
            """)

    def save_repositories(self, input_df: pd.DataFrame, readme_df: pd.DataFrame = None):
        """Save repository data to SQLite database with current timestamp."""
        # Create a copy of the DataFrame with only the columns we want to store
        columns_to_store = [
            'name', 'description', 'stars', 'forks', 'language', 'username',
            'country', 'html_url', 'created_at', 'updated_at', 'archived',
            'fork', 'fork_source', 'size_kb', 'open_issues', 'watchers',
            'default_branch', 'license'
        ]
        
        # If we have README data, merge it with the repository data
        if readme_df is not None:
            readme_df = readme_df.copy()
            # Rename columns to match our schema
            readme_df = readme_df.rename(columns={
                'repo_url': 'html_url',
                'readme_url': 'readme_url'  # We don't store this in SQLite
            })
            
            # Merge repository data with README data
            df = pd.merge(
                input_df[columns_to_store].copy(),
                readme_df[['html_url', 'readme_content', 'readme_size', 'readme_encoding']],
                on='html_url',
                how='left'
            )
        else:
            df = input_df[columns_to_store].copy()
            # Add empty README columns
            df['readme_content'] = None
            df['readme_size'] = None
            df['readme_encoding'] = None
        
        # Add scrape timestamp
        df['scrape_timestamp'] = datetime.utcnow().isoformat()

        # Convert boolean columns to integers (SQLite doesn't have a boolean type)
        df['archived'] = df['archived'].astype(int)
        df['fork'] = df['fork'].astype(int)

        with sqlite3.connect(self.db_path) as conn:
            # Save to database
            df.to_sql('repository_snapshots', conn, if_exists='append', index=False)

    def get_latest_snapshot(self) -> pd.DataFrame:
        """Get the most recent snapshot of all repositories."""
        with sqlite3.connect(self.db_path) as conn:
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
            return pd.read_sql_query(query, conn)

    def get_snapshots_for_repo(self, html_url: str) -> pd.DataFrame:
        """Get all snapshots for a specific repository."""
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT *
            FROM repository_snapshots
            WHERE html_url = ?
            ORDER BY scrape_timestamp DESC
            """
            return pd.read_sql_query(query, conn, params=(html_url,))
