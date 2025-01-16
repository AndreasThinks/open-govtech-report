import aiohttp
import asyncio
from aiolimiter import AsyncLimiter
import pandas as pd
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from repo_operations import RepositoryCache
from tqdm import tqdm
import sys
import argparse
from typing import Optional, Dict, List
import pytz
import jwt

# Load environment variables
load_dotenv('.env')
app_id = os.getenv('GITHUB_APP_ID')
installation_id = os.getenv('GITHUB_INSTALLATION_ID')
private_key_path = 'open-govtech-report.2025-01-15.private-key.pem'

if not all([app_id, installation_id]):
    print("Error: GitHub App credentials not found in environment variables")
    sys.exit(1)

def generate_jwt():
    """Generate a JWT for GitHub App authentication"""
    with open(private_key_path, 'r') as key_file:
        private_key = key_file.read()
    
    now = datetime.utcnow()
    payload = {
        'iat': now,
        'exp': now + timedelta(minutes=1),  # Shorter expiration time
        'iss': app_id
    }
    return jwt.encode(payload, private_key, algorithm='RS256')

async def get_installation_token(session):
    """Get an installation access token for the GitHub App"""
    jwt_token = generate_jwt()
    headers = {
        'Authorization': f'Bearer {jwt_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    url = f'https://api.github.com/app/installations/{installation_id}/access_tokens'
    
    async with session.post(url, headers=headers) as response:
        if response.status == 201:
            data = await response.json()
            return data['token']
        else:
            print(f"Error getting installation token: {response.status}")
            print(await response.text())
            return None

# Initialize cache and rate limiter
repo_cache = RepositoryCache()
rate_limit = AsyncLimiter(14500, 3600)  # GitHub App rate limit with buffer (15,000 per hour)

# Track rate limit status
rate_limit_remaining = 15000  # GitHub Apps have higher rate limits
rate_limit_reset = None

def is_file_valid(file_path: str, min_entries: int = 100) -> bool:
    """Check if a file exists, is recent, and has minimum entries"""
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

def update_rate_limit(response_headers) -> None:
    """Update rate limit tracking from response headers"""
    global rate_limit_remaining, rate_limit_reset
    
    remaining = response_headers.get('X-RateLimit-Remaining')
    reset = response_headers.get('X-RateLimit-Reset')
    
    if remaining is not None:
        rate_limit_remaining = int(remaining)
    if reset is not None:
        rate_limit_reset = datetime.fromtimestamp(int(reset))

async def check_rate_limit() -> bool:
    """Check and handle rate limit before making a request"""
    global rate_limit_remaining, rate_limit_reset
    
    if rate_limit_remaining < 100 and rate_limit_reset:
        wait_time = (rate_limit_reset - datetime.now()).total_seconds()
        if wait_time > 0:
            print(f"Rate limit low ({rate_limit_remaining}). Waiting {wait_time:.0f}s until reset.")
            await asyncio.sleep(wait_time + 1)
            return True
    return False

async def fetch_readme(session: aiohttp.ClientSession, repo_url: str, initial_headers=None) -> Optional[Dict[str, Optional[str | int]]]:
    """Fetch README content for a repository"""
    # Convert HTML URL to API URL for README
    # Example: https://github.com/owner/repo -> https://api.github.com/repos/owner/repo/readme
    api_url = repo_url.replace("github.com", "api.github.com/repos") + "/readme"
    
    # Use provided headers or get new ones if needed
    headers = initial_headers
    if not headers:
        token = await get_installation_token(session)
        if not token:
            print(f"Failed to get installation token for {repo_url}")
            return None
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github.v3+json'
        }
    
    try:
        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            await check_rate_limit()
            async with rate_limit:
                async with session.get(api_url, headers=headers) as response:
                    update_rate_limit(response.headers)
                    
                    if response.status == 200:
                        data = await response.json()
                        # The content is base64 encoded, but we'll store it that way to preserve special characters
                        return {
                            'readme_content': data.get('content', ''),
                            'readme_encoding': data.get('encoding', 'base64'),
                            'readme_size': data.get('size', 0),
                            'readme_url': data.get('html_url', ''),
                            'repo_url': repo_url  # Add repository URL
                        }
                    elif response.status == 404:
                        return {
                            'readme_content': '',
                            'readme_encoding': None,
                            'readme_size': 0,
                            'readme_url': None,
                            'repo_url': repo_url  # Add repository URL even for 404s
                        }
                    elif response.status == 401:
                        # Token might have expired, get a new one
                        print(f"Token expired for {repo_url}. Getting new token...")
                        token = await get_installation_token(session)
                        if not token:
                            return None
                        headers = {
                            'Authorization': f'Bearer {token}',
                            'Accept': 'application/vnd.github.v3+json'
                        }
                        retry_count += 1
                        continue
                    else:
                        print(f"Error fetching README for {repo_url}: Status {response.status}")
                        return None
            
            if retry_count == max_retries:
                print(f"Max retries reached for {repo_url}")
                return None
    except Exception as e:
        print(f"Exception while fetching README for {repo_url}: {str(e)}")
        return None

async def process_repos_chunk(session: aiohttp.ClientSession, chunk: List[Dict]) -> List[Optional[Dict[str, Optional[str | int]]]]:
    """Process a chunk of repositories in parallel"""
    # Get a fresh token for each chunk
    token = await get_installation_token(session)
    if not token:
        print("Failed to get installation token for chunk")
        return []
        
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    tasks = []
    for repo in chunk:
        # Pass the headers to avoid each task getting its own token
        task = fetch_readme(session, repo['html_url'], headers)
        tasks.append(task)
    return await asyncio.gather(*tasks)

async def fetch_all_readmes(repos_df: pd.DataFrame, chunk_size: int = 5, force_update: bool = False) -> List[Optional[Dict[str, Optional[str | int]]]]:
    """Fetch READMEs for all repositories"""
    # Convert DataFrame to list of dicts for processing
    repos = repos_df.to_dict('records')
    chunks = [repos[i:i + chunk_size] for i in range(0, len(repos), chunk_size)]
    
    all_readmes = []
    async with aiohttp.ClientSession() as session:
        with tqdm(total=len(repos), desc="Fetching READMEs") as pbar:
            for chunk in chunks:
                results = await process_repos_chunk(session, chunk)
                # Filter out None results and create empty README entries for failed fetches
                valid_results = []
                for repo, result in zip(chunk, results):
                    if result is None:
                        # Create empty README entry if fetch failed
                        valid_results.append({
                            'readme_content': '',
                            'readme_encoding': None,
                            'readme_size': 0,
                            'readme_url': None,
                            'repo_url': repo['html_url']
                        })
                    else:
                        valid_results.append(result)
                all_readmes.extend(valid_results)
                pbar.update(len(chunk))
    
    return all_readmes

async def main(force_update: bool = False) -> None:
    try:
        # Generate filenames with current date
        current_date = datetime.now().strftime('%Y%m%d')
        output_base = f'all_government_repositories_with_readmes_{current_date}'
        
        # Check for existing README data
        existing_files = [f for f in os.listdir('.') if f.startswith('all_government_repositories_with_readmes_') and f.endswith('.parquet')]
        existing_readme_df = None
        
        if existing_files and not force_update:
            latest_file = sorted(existing_files, reverse=True)[0]
            if latest_file != f"{output_base}.parquet":
                print(f"Loading existing README data from {latest_file}...")
                existing_readme_df = pd.read_parquet(latest_file)
                print(f"Loaded {len(existing_readme_df)} existing repositories with READMEs")
        
        # Read the latest repository data
        repo_files = sorted([f for f in os.listdir('.') if f.startswith('all_government_repositories_') 
                           and not f.startswith('all_government_repositories_with') 
                           and f.endswith('.parquet')], reverse=True)
        
        if not repo_files:
            print("Error: No repository data file found")
            return
            
        latest_repo_file = repo_files[0]
        print(f"Reading repository data from {latest_repo_file}...")
        df = pd.read_parquet(latest_repo_file)
        
        # If we have existing README data, only process repositories that:
        # 1. Are new (not in existing data)
        # 2. Have been updated since last README fetch
        if existing_readme_df is not None and not force_update:
            existing_urls = set(existing_readme_df['html_url'])
            df['in_existing'] = df['html_url'].isin(existing_urls)
            
            if 'updated_at' in existing_readme_df.columns:
                existing_update_times = existing_readme_df.set_index('html_url')['updated_at']
                df['needs_update'] = df.apply(
                    lambda row: row['html_url'] not in existing_urls or 
                    pd.to_datetime(row['updated_at']) > pd.to_datetime(existing_update_times.get(row['html_url'], '1970-01-01')),
                    axis=1
                )
                df_to_process = df[df['needs_update']]
            else:
                df_to_process = df[~df['in_existing']]
        else:
            df_to_process = df
        
        total_repos = len(df)
        repos_to_process = len(df_to_process)
        print(f"\nTotal repositories: {total_repos}")
        print(f"Repositories to process: {repos_to_process}")
        
        if repos_to_process == 0:
            print("No new repositories to process")
            return
        
        # Fetch READMEs
        print(f"\nFetching READMEs...")
        readmes = await fetch_all_readmes(df_to_process, force_update=force_update)
        
        # Create DataFrame with README data
        new_readme_df = pd.DataFrame(readmes)
        
        # Combine with existing data
        if existing_readme_df is not None and not force_update:
            # Update existing entries and add new ones
            result_df = existing_readme_df.copy()
            new_data = df_to_process.copy()
            new_data[['readme_content', 'readme_encoding', 'readme_size', 'readme_url', 'repo_url']] = new_readme_df
            
            # Remove updated entries from result_df and append new data
            result_df = result_df[~result_df['html_url'].isin(new_data['html_url'])]
            result_df = pd.concat([result_df, new_data], ignore_index=True)
        else:
            # Create new DataFrame with all data
            result_df = df.copy()
            result_df[['readme_content', 'readme_encoding', 'readme_size', 'readme_url', 'repo_url']] = pd.DataFrame(readmes)
        
        # Save results
        print("\nSaving data...")
        result_df.to_parquet(f'{output_base}.parquet')
        result_df.to_csv(f'{output_base}.csv', index=False)
        
        # Print statistics
        print("\nFinal Statistics:")
        print(f"Total repositories: {len(result_df)}")
        print(f"Repositories with READMEs: {len(result_df[result_df['readme_content'].notna()])}")
        print(f"Average README size: {result_df['readme_size'].mean():.0f} bytes")
        print(f"Largest README: {result_df['readme_size'].max():,} bytes")
        print(f"Empty READMEs: {len(result_df[result_df['readme_content'] == ''])}")
        
        print(f"\nData saved successfully to:")
        print(f"- {output_base}.parquet")
        print(f"- {output_base}.csv")
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Fetch READMEs for government GitHub repositories')
    parser.add_argument('--force-update', action='store_true',
                      help='Force update all READMEs regardless of cache')
    args = parser.parse_args()
    
    try:
        asyncio.run(main(force_update=args.force_update))
    except Exception as e:
        print(f"\nFatal error: {str(e)}")
        sys.exit(1)
