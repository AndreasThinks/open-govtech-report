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

async def main():
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
        new_repos = await fetch_all_repository_details(accounts, github_token)
        
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
        print(f"Unique languages: {repos_df['language'].nunique()}")
        print(f"Total stars: {repos_df['stars'].sum():,}")
        print(f"Total forks: {repos_df['forks'].sum():,}")
        print("\nTop 5 languages:")
        print(repos_df['language'].value_counts().head())
        
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
    asyncio.run(main())
