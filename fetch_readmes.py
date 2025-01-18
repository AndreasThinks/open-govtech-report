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
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

# Load environment variables
load_dotenv('.env')
app_id = os.getenv('GITHUB_APP_ID')
installation_id = os.getenv('GITHUB_INSTALLATION_ID')
private_key = os.getenv('GITHUB_PRIVATE_KEY')

if not all([app_id, installation_id, private_key]):
    print("Error: GitHub App credentials not found in environment variables")
    sys.exit(1)

def clean_private_key():
    """Clean and process the private key from environment variable"""
    global private_key
    
    if not private_key:
        return
        
    # Read the key directly from the .env file to handle multiline string
    try:
        with open('.env', 'r') as f:
            lines = f.readlines()
            key_lines = []
            in_key = False
            
            for line in lines:
                if 'GITHUB_PRIVATE_KEY=' in line:
                    # Start of key
                    in_key = True
                    # Remove the variable name and opening quote
                    line = line.split('GITHUB_PRIVATE_KEY=')[1].strip()
                    if line.startswith('"'):
                        line = line[1:]
                    key_lines.append(line)
                elif in_key:
                    # Part of multiline key
                    line = line.strip()
                    if line.endswith('"'):
                        # End of key
                        line = line[:-1]
                        key_lines.append(line)
                        break
                    else:
                        key_lines.append(line)
            
            if key_lines:
                private_key = '\n'.join(key_lines)
    except Exception as e:
        print(f"Warning: Could not read key from .env file: {str(e)}")

# Process the private key
clean_private_key()

def generate_jwt():
    """Generate a JWT for GitHub App authentication"""
    if not private_key:
        raise ValueError("Private key is required")
    
    try:
        # Load and validate the key using cryptography
        key_bytes = private_key.strip().encode()
        private_key_obj = serialization.load_pem_private_key(
            key_bytes,
            password=None,
            backend=default_backend()
        )
        
        # Convert to PEM format for PyJWT
        pem_key = private_key_obj.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        now = datetime.utcnow()
        payload = {
            'iat': now,
            'exp': now + timedelta(minutes=1),  # Shorter expiration time
            'iss': app_id
        }
        return jwt.encode(payload, pem_key, algorithm='RS256')
    except Exception as e:
        print(f"Error generating JWT: {str(e)}")
        raise

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

async def fetch_repo_data(session: aiohttp.ClientSession, repo_url: str, initial_headers=None) -> Optional[Dict[str, Optional[str | int]]]:
    """Fetch repository data including README and commit count using GraphQL"""
    # Extract owner and repo name from URL
    # Example: https://github.com/owner/repo -> owner, repo
    parts = repo_url.split('github.com/')[1].split('/')
    owner, repo_name = parts[0], parts[1]
    
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
    
    # Single GraphQL query to get all repository info
    info_query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        # Get repository info
        defaultBranchRef {
          name
          target {
            ... on Commit {
              history(first: 0) {
                totalCount
              }
            }
          }
        }
        # Fallback to first branch if default not set
        refs(refPrefix: "refs/heads/", first: 1, orderBy: {field: ALPHABETICAL, direction: ASC}) {
          nodes {
            name
            target {
              ... on Commit {
                history(first: 0) {
                  totalCount
                }
              }
            }
          }
        }
        # Try different README filenames
        readmeMd: object(expression: "HEAD:README.md") {
          ... on Blob {
            text
            byteSize
          }
        }
        readmeRst: object(expression: "HEAD:README.rst") {
          ... on Blob {
            text
            byteSize
          }
        }
        readmeLower: object(expression: "HEAD:readme.md") {
          ... on Blob {
            text
            byteSize
          }
        }
        readmeTitle: object(expression: "HEAD:Readme.md") {
          ... on Blob {
            text
            byteSize
          }
        }
      }
    }
    """
    
    variables = {
        "owner": owner,
        "name": repo_name
    }
    
    try:
        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            await check_rate_limit()
            
            async with rate_limit:
                async with session.post(
                    'https://api.github.com/graphql',
                    headers=headers,
                    json={'query': info_query, 'variables': variables}
                ) as response:
                    update_rate_limit(response.headers)
                    
                    if response.status == 200:
                        data = await response.json()
                        if 'errors' in data:
                            print(f"GraphQL errors for {repo_url}: {data['errors']}")
                            return {
                                'readme_content': '',
                                'readme_encoding': None,
                                'readme_size': 0,
                                'readme_url': None,
                                'repo_url': repo_url,
                                'commit_count': 0
                            }
                        
                        result = data.get('data', {}).get('repository', {})
                        
                        # Get repository info
                        commit_count = 0
                        
                        # Try to get commit count from default branch first
                        commit_count = 0
                        if result.get('defaultBranchRef'):
                            branch_ref = result['defaultBranchRef']
                            if branch_ref.get('target') and branch_ref['target'].get('history'):
                                commit_count = branch_ref['target']['history'].get('totalCount', 0)
                        
                        # If no commit count yet, try first branch
                        if commit_count == 0 and result.get('refs') and result['refs'].get('nodes'):
                            nodes = result['refs']['nodes']
                            if nodes and len(nodes) > 0:
                                branch_ref = nodes[0]
                                if branch_ref.get('target') and branch_ref['target'].get('history'):
                                    commit_count = branch_ref['target']['history'].get('totalCount', 0)
                        
                        # If still no commits, repository is likely empty
                        if commit_count == 0:
                            print(f"\nRepository appears empty: {repo_url}")
                            
                        # Debug print for specific repo
                        if repo_url == "https://github.com/CIFASIS/QuickFuzz":
                            print("\nDebug GraphQL response for QuickFuzz:")
                            print("defaultBranchRef:", result.get('defaultBranchRef'))
                            print("first branch:", result.get('refs', {}).get('nodes', [None])[0] if result.get('refs') and result['refs'].get('nodes') else None)
                            print("commit_count:", commit_count)
                        
                        # Get README data - try different variants
                        readme_variants = {
                            'README.md': result.get('readmeMd'),
                            'README.rst': result.get('readmeRst'),
                            'readme.md': result.get('readmeLower'),
                            'Readme.md': result.get('readmeTitle')
                        }
                        
                        # Find first non-null README
                        readme_content = ''
                        readme_size = 0
                        readme_file = None
                        for filename, content in readme_variants.items():
                            if content:
                                readme_content = content.get('text', '')
                                readme_size = content.get('byteSize', 0)
                                readme_file = filename
                                break
                        
                        if readme_content:
                            # Clean null bytes from readme content
                            cleaned_content = readme_content.replace('\x00', '')
                            return {
                                'readme_content': cleaned_content,
                                'readme_encoding': 'utf-8',  # GraphQL returns decoded text
                                'readme_size': readme_size,
                                'readme_url': f"{repo_url}/blob/master/{readme_file}",
                                'repo_url': repo_url,
                                'commit_count': commit_count
                            }
                        else:
                            return {
                                'readme_content': '',
                                'readme_encoding': None,
                                'readme_size': 0,
                                'readme_url': None,
                                'repo_url': repo_url,
                                'commit_count': commit_count
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
                        print(f"Error fetching repository data for {repo_url}: Status {response.status}")
                        return None
            
            if retry_count == max_retries:
                print(f"Max retries reached for {repo_url}")
                return None
    except Exception as e:
        print(f"Exception while fetching repository data for {repo_url}: {str(e)}")
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
        task = fetch_repo_data(session, repo['html_url'], headers)
        tasks.append(task)
    return await asyncio.gather(*tasks)

async def fetch_all_readmes(repos_df: pd.DataFrame, chunk_size: int = 20, force_update: bool = False) -> List[Optional[Dict[str, Optional[str | int]]]]:
    """Fetch READMEs for all repositories"""
    # Convert DataFrame to list of dicts for processing
    repos = repos_df.to_dict('records')
    chunks = [repos[i:i + chunk_size] for i in range(0, len(repos), chunk_size)]
    
    all_readmes = []
    # Process chunks in parallel with a semaphore to limit concurrency
    sem = asyncio.Semaphore(5)  # Process 5 chunks simultaneously
    
    async def process_chunk_with_session(session: aiohttp.ClientSession, chunk: List[Dict], pbar: tqdm) -> List[Dict]:
        async with sem:
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
                        'repo_url': repo['html_url'],
                        'commit_count': 0
                    })
                else:
                    valid_results.append(result)
            pbar.update(len(chunk))
            return valid_results
    
    async with aiohttp.ClientSession() as session:
        with tqdm(total=len(repos), desc="Fetching READMEs") as pbar:
            # Create tasks for all chunks
            tasks = [process_chunk_with_session(session, chunk, pbar) for chunk in chunks]
            # Process all chunks and gather results
            chunk_results = await asyncio.gather(*tasks)
            # Flatten results
            all_readmes = [item for sublist in chunk_results for item in sublist]
    
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
            
            # Create new data with only the necessary columns from df_to_process
            new_data = df_to_process[['html_url', 'updated_at']].copy()
            
            # Add README data columns
            for col in ['readme_content', 'readme_encoding', 'readme_size', 'readme_url', 'repo_url']:
                new_data[col] = new_readme_df[col]
            
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
