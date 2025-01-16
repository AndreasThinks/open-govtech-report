import asyncio
import aiohttp
import pandas as pd
from dotenv import load_dotenv
import os
from db_operations import DatabaseManager
from scrape_repos import fetch_gov_github_accounts, fetch_all_repository_details
from fetch_readmes import fetch_all_readmes
from datetime import datetime, timedelta
import sys
from tqdm import tqdm
import pytz
from typing import Optional, Dict, List

# Load environment variables from .env file
load_dotenv('.env')

# No need to check for GITHUB_TOKEN as we're using GitHub App authentication

def is_file_valid(file_path: str, min_entries: int = 100) -> bool:
    if not os.path.exists(file_path):
        return False

    file_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(file_path))
    if file_age > timedelta(days=7):
        return False

    if file_path.endswith('.parquet'):
        df = pd.read_parquet(file_path)
    elif file_path.endswith('.csv'):
        df = pd.read_csv(file_path)
    else:
        return False

    return len(df) >= min_entries

async def fetch_repositories(force_update: bool = False, limit: Optional[int] = None) -> Optional[pd.DataFrame]:
    url = "https://raw.githubusercontent.com/github/government.github.com/gh-pages/_data/governments.yml"

    # Generate filenames with current date
    current_date = datetime.now().strftime('%Y%m%d')
    all_repos_file = f"all_government_repositories_{current_date}.parquet"
    all_repos_csv_file = f"all_government_repositories_{current_date}.csv"

    # Load most recent existing data if available
    existing_repos: Optional[pd.DataFrame] = None
    existing_files = [f for f in os.listdir('.') if f.startswith('all_government_repositories_') 
                     and not f.startswith('all_government_repositories_with') 
                     and f.endswith('.parquet')]
    
    if existing_files:
        # Sort files by date (newest first)
        latest_file = sorted(existing_files, reverse=True)[0]
        if latest_file != all_repos_file:  # Don't load if same date
            print(f"Loading existing repository data from {latest_file}...")
            existing_repos = pd.read_parquet(latest_file)
            print(f"Loaded {len(existing_repos)} existing repositories")

    # Fetch government accounts
    print("\nFetching list of government accounts...")
    accounts = await fetch_gov_github_accounts(url)
    if not accounts:
        print("Error: Failed to fetch government accounts")
        return None

    # If limit is set, only take first N accounts
    if limit:
        total_repos = 0
        limited_accounts = {}
        for country, usernames in accounts.items():
            limited_accounts[country] = []
            for username in usernames:
                if total_repos >= limit:
                    break
                limited_accounts[country].append(username)
                total_repos += 1  # This is an estimate, actual repos may be more/less
            if total_repos >= limit:
                break
        accounts = limited_accounts

    # Calculate total accounts for progress tracking
    total_accounts = sum(len(usernames) for usernames in accounts.values())
    print(f"\nFound {total_accounts} total government accounts across {len(accounts)} countries")
    
    # Create progress bar
    progress_bar = tqdm(total=total_accounts, desc="Fetching repositories", unit="account")

    # Define progress callback
    def update_progress(accounts_processed: int) -> None:
        progress_bar.update(accounts_processed)
        
    print("\nFetching repository details (this may take a while)...")
    if force_update:
        print("Force update enabled - checking all repositories")
    
    try:
        new_repos = await fetch_all_repository_details(
            accounts,
            force_update=force_update,
            progress_callback=update_progress
        )
    except Exception as e:
        progress_bar.close()
        print(f"\nError fetching repository details: {str(e)}")
        raise
    
    progress_bar.close()
    
    if not new_repos:
        print("\nNo new repository data collected")
        return None

    # Create DataFrame from new repos
    new_df = pd.DataFrame(new_repos)
    
    # Combine with existing data and deduplicate
    if existing_repos is not None:
        print("\nMerging with existing data and deduplicating...")
        combined_df = pd.concat([existing_repos, new_df], ignore_index=True)
        repos_df = combined_df.drop_duplicates(subset=['html_url'], keep='last')
        
        # Print statistics
        print(f"Combined: {len(combined_df)} repositories")
        print(f"After final deduplication: {len(repos_df)} repositories")
        print(f"New repositories added: {len(repos_df) - len(existing_repos)}")
    else:
        repos_df = new_df
        print(f"\nNew repositories: {len(repos_df)}")

    # Save the final deduplicated data
    print("\nSaving data...")
    repos_df.to_parquet(all_repos_file, index=False)
    repos_df.to_csv(all_repos_csv_file, index=False)
    
    # Print final statistics
    print("\nFinal Statistics:")
    print(f"Total repositories: {len(repos_df)}")
    print(f"Total size: {repos_df['size_kb'].sum() / 1024 / 1024:,.2f} GB")
    print(f"Archived repositories: {repos_df['archived'].sum()}")
    print(f"Forked repositories: {repos_df['fork'].sum()}")
    print(f"Open issues: {repos_df['open_issues'].sum():,}")
    print(f"Watchers: {repos_df['watchers'].sum():,}")
    print(f"Unique languages: {repos_df['language'].nunique()}")
    print(f"Total stars: {repos_df['stars'].sum():,}")
    print(f"Total forks: {repos_df['forks'].sum():,}")
    
    print("\nTop 5 languages:")
    print(repos_df['language'].value_counts().head())
    
    print("\nTop 5 licenses:")
    print(repos_df['license'].value_counts().head())
    
    print("\nMost common default branches:")
    print(repos_df['default_branch'].value_counts().head())
    
    if repos_df['fork'].sum() > 0:
        print("\nTop 5 forked sources:")
        print(repos_df[repos_df['fork']]['fork_source'].value_counts().head())
        
    # Calculate average repository age and activity metrics
    utc = pytz.UTC
    now = datetime.now(utc)
    
    # Convert timestamps to datetime with UTC timezone
    # GitHub API returns timestamps in UTC, so we just need to parse them
    repos_df['created_at'] = pd.to_datetime(repos_df['created_at'])
    repos_df['updated_at'] = pd.to_datetime(repos_df['updated_at'])
    
    # Calculate age and activity metrics
    now_series = pd.Series([now] * len(repos_df))
    repos_df['age_days'] = (now_series - repos_df['created_at']).dt.total_seconds() / (24 * 3600)
    repos_df['last_updated_days'] = (now_series - repos_df['updated_at']).dt.total_seconds() / (24 * 3600)
    
    avg_age = repos_df['age_days'].mean()
    print(f"\nAverage repository age: {avg_age / 365.25:.1f} years")
    
    active_repos = repos_df[repos_df['last_updated_days'] <= 30]
    print(f"Active repositories (updated in last 30 days): {len(active_repos)} ({len(active_repos) / len(repos_df) * 100:.1f}%)")
    
    print("\nRepository data saved successfully to:")
    print(f"- {all_repos_file}")
    print(f"- {all_repos_csv_file}")
    
    return repos_df

async def main(force_update: bool = False, readmes_only: bool = False, limit: Optional[int] = None) -> None:
    try:
        # Get repository data
        if readmes_only:
            print("\n=== Loading Repository Data ===")
            repos_df = pd.read_parquet('all_government_repositories_20250115.parquet')
            if limit:
                repos_df = repos_df.head(limit)
            print(f"Loaded {len(repos_df)} repositories")
        else:
            print("\n=== Fetching Repositories ===")
            repos_df = await fetch_repositories(force_update=force_update, limit=limit)
            if repos_df is None:
                print("Error: Failed to fetch repositories")
                return
            
        # Step 2: Fetch READMEs
        print("\n=== Fetching READMEs ===")
        readmes = await fetch_all_readmes(repos_df, force_update=force_update)
        
        if not readmes:
            print("Error: Failed to fetch READMEs")
            return
            
        # Create DataFrame with README data
        readme_df = pd.DataFrame(readmes)
        
        # Save updated data to SQLite with READMEs
        print("\nSaving updated data with READMEs to SQLite database...")
        db = DatabaseManager()
        db.save_repositories(repos_df, readme_df)
        
        # Step 3: Join repositories and READMEs data
        print("\n=== Combining Data ===")
        
        # Print column info for debugging
        print("\nREADME DataFrame columns:", readme_df.columns.tolist())
        print("Number of READMEs:", len(readme_df))
        
        # Create the combined dataset by merging on repository URL
        combined_df = repos_df.merge(
            readme_df,
            left_on='html_url',
            right_on='repo_url',
            how='left'
        )
        
        # Drop the duplicate repo_url column from the merge
        if 'repo_url' in combined_df.columns:
            combined_df = combined_df.drop('repo_url', axis=1)
        
        # Save combined data
        current_date = datetime.now().strftime('%Y%m%d')
        combined_file = f"all_government_repositories_combined_{current_date}"
        combined_df.to_parquet(f"{combined_file}.parquet")
        combined_df.to_csv(f"{combined_file}.csv", index=False)
        
        # Print README statistics
        print("\nREADME Statistics:")
        # Count non-empty READMEs
        has_readme = combined_df['readme_content'].notna() & (combined_df['readme_content'] != '')
        readme_count = has_readme.sum()
        
        print(f"Repositories with READMEs: {readme_count} ({readme_count / len(combined_df) * 100:.1f}%)")
        
        # Calculate size statistics only for repositories that have READMEs
        readme_sizes = combined_df[has_readme]['readme_size']
        if not readme_sizes.empty:
            print(f"Average README size: {readme_sizes.mean():.0f} bytes")
            print(f"Largest README: {readme_sizes.max():,} bytes")
        else:
            print("Average README size: 0 bytes")
            print("Largest README: 0 bytes")
            
        # Count empty READMEs (have an entry but no content)
        empty_readmes = (combined_df['readme_content'] == '') & combined_df['readme_content'].notna()
        print(f"Empty READMEs: {empty_readmes.sum()}")
        
        print("\nCombined data saved successfully to:")
        print(f"- {combined_file}.parquet")
        print(f"- {combined_file}.csv")
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")
        raise

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Fetch government GitHub repositories')
    parser.add_argument('--force-update', action='store_true', 
                      help='Force update all repositories regardless of cache')
    parser.add_argument('--readmes-only', action='store_true',
                      help='Skip repository fetching and only fetch/update READMEs')
    parser.add_argument('--limit', type=int,
                      help='Limit the number of repositories to process')
    args = parser.parse_args()
    
    try:
        # Only pass limit if explicitly set
        limit = args.limit if args.limit is not None else None
        asyncio.run(main(force_update=args.force_update, readmes_only=args.readmes_only, limit=limit))
    except Exception as e:
        print(f"\nFatal error: {str(e)}")
        sys.exit(1)
