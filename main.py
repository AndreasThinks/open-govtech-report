import asyncio
import aiohttp
import pandas as pd
from dotenv import load_dotenv
import os
from scrape_repos import fetch_gov_github_accounts, fetch_all_repository_details
from datetime import datetime, timedelta
import sys
from tqdm import tqdm

# Load environment variables from .env file
load_dotenv('.env')

# Access environment variables
github_token = os.getenv('GITHUB_TOKEN')

def is_file_valid(file_path, min_entries=100):
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

async def main(force_update=False):
    try:
        url = "https://raw.githubusercontent.com/github/government.github.com/gh-pages/_data/governments.yml"

        # Generate filenames with current date
        current_date = datetime.now().strftime('%Y%m%d')
        all_repos_file = f"all_government_repositories_{current_date}.parquet"
        all_repos_csv_file = f"all_government_repositories_{current_date}.csv"

        # Load most recent existing data if available
        existing_repos = None
        existing_files = [f for f in os.listdir('.') if f.startswith('all_government_repositories_') and f.endswith('.parquet')]
        
        if existing_files:
            # Sort files by date (newest first)
            latest_file = sorted(existing_files, reverse=True)[0]
            if latest_file != all_repos_file:  # Don't load if same date
                print(f"Loading existing repository data from {latest_file}...")
                existing_repos = pd.read_parquet(latest_file)
                print(f"Loaded {len(existing_repos)} existing repositories")

        # Fetch government accounts
        print("Fetching list of government accounts...")
        accounts = await fetch_gov_github_accounts(url)
        if not accounts:
            print("Failed to fetch government accounts")
            return

        # Calculate total accounts for progress tracking
        total_accounts = sum(len(usernames) for usernames in accounts.values())
        print(f"\nFound {total_accounts} total government accounts across {len(accounts)} countries")
        
        # Create progress bar
        progress_bar = tqdm(total=total_accounts, desc="Fetching repositories", unit="account")

        # Fetch new data with progress tracking
        def progress_callback(accounts_processed):
            progress_bar.update(accounts_processed)
            
        print("\nFetching repository details (this may take a while)...")
        if force_update:
            print("Force update enabled - checking all repositories")
        new_repos = await fetch_all_repository_details(accounts, github_token, force_update)
        
        progress_bar.close()
        
        if not new_repos:
            print("No new repository data collected")
            return

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
        now = pd.Timestamp.now()
        repos_df['created_at'] = pd.to_datetime(repos_df['created_at'])
        repos_df['updated_at'] = pd.to_datetime(repos_df['updated_at'])
        
        # Calculate age using timedelta
        repos_df['age_days'] = repos_df['created_at'].apply(lambda x: (now - x).days)
        avg_age = repos_df['age_days'].mean()
        print(f"\nAverage repository age: {avg_age / 365.25:.1f} years")
        
        # Calculate activity metrics using timedelta
        repos_df['last_updated_days'] = repos_df['updated_at'].apply(lambda x: (now - x).days)
        active_repos = repos_df[repos_df['last_updated_days'] <= 30]
        print(f"Active repositories (updated in last 30 days): {len(active_repos)} ({len(active_repos) / len(repos_df) * 100:.1f}%)")
        
        print("\nData saved successfully to:")
        print(f"- {all_repos_file}")
        print(f"- {all_repos_csv_file}")

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
    args = parser.parse_args()
    
    asyncio.run(main(force_update=args.force_update))
