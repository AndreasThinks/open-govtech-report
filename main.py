import asyncio
import aiohttp
import pandas as pd
from dotenv import load_dotenv
import os
from scrape_repos import fetch_gov_github_accounts, fetch_all_repository_details
from tqdm.asyncio import tqdm_asyncio
from datetime import datetime, timedelta

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
    url = "https://raw.githubusercontent.com/github/government.github.com/gh-pages/_data/governments.yml"

    all_repos_file = "all_government_repositories.parquet"
    classified_repos_file = "classified_government_repositories.parquet"

    if is_file_valid(all_repos_file) and is_file_valid(classified_repos_file):
        print("Using existing files as they are less than a week old and have sufficient entries.")
        repos_df = pd.read_parquet(all_repos_file)
        classified_df = pd.read_parquet(classified_repos_file)
    else:
        accounts = await fetch_gov_github_accounts(url)
        all_repos = await fetch_all_repository_details(accounts, github_token)

        print(f"Total repositories: {len(all_repos)}")

        if all_repos:
            repos_df = pd.DataFrame(all_repos)
            repos_df.to_parquet(all_repos_file, index=False)
            print("Data saved as parquet file.")
            try:
                repos_df.to_csv(all_repos_file.replace('.parquet', '.csv'), index=False)
                print("Data saved as CSV file.")
            except Exception as e:
                print(f"Error saving CSV file: {e}")

        else:
            print("No repository data collected.")
            return

if __name__ == "__main__":
    asyncio.run(main())
