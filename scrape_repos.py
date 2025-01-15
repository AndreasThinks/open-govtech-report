import aiohttp
import yaml
import os
from dotenv import load_dotenv
import pandas as pd
import asyncio
from aiolimiter import AsyncLimiter
from datetime import datetime
import math
from repo_operations import RepositoryCache

# Load environment variables from .env file
load_dotenv('.env')

# Access environment variables
github_token = os.getenv('GITHUB_TOKEN')

# Initialize persistent cache
repo_cache = RepositoryCache()

# Create a rate limiter: 5000 requests per hour (GitHub's rate limit)
# Using 4500 to leave some buffer for other potential API calls
rate_limit = AsyncLimiter(4500, 3600)

# Track rate limit status
rate_limit_remaining = 5000
rate_limit_reset = None

async def fetch_gov_github_accounts(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                text = await response.text()
                return yaml.safe_load(text)
    return None

def update_rate_limit(response_headers):
    """Update rate limit tracking from response headers"""
    global rate_limit_remaining, rate_limit_reset
    
    remaining = response_headers.get('X-RateLimit-Remaining')
    reset = response_headers.get('X-RateLimit-Reset')
    
    if remaining is not None:
        rate_limit_remaining = int(remaining)
    if reset is not None:
        rate_limit_reset = datetime.fromtimestamp(int(reset))

async def fetch_repository_details_async(session, username, token, country, force_update=False):
    """Fetch repository details for a given username."""
    cache_key = f"{username}_{country}"
    
    # Check if we should update based on cache policy
    if not repo_cache.should_update(cache_key, force_update):
        print(f"Using cached data for {username} (last check within 24h)")
        return []

    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }

    # Add ETag if we have it cached
    etag = repo_cache.get_etag(cache_key)
    if etag:
        headers['If-None-Match'] = etag

    # Sort by created date to optimize for our use case
    base_url = f"https://api.github.com/users/{username}/repos?sort=created&direction=desc&per_page=100"
    full_repo_details = []
    page = 1
    max_retries = 5
    initial_backoff = 1

    while True:
        url = f"{base_url}&page={page}"
        retry_count = 0
        backoff = initial_backoff

        while retry_count < max_retries:
            try:
                async with rate_limit:
                    async with session.get(url, headers=headers) as repos_response:
                        # Update rate limit tracking
                        update_rate_limit(repos_response.headers)
                        
                        # Handle rate limit approaching
                        if rate_limit_remaining < 100 and rate_limit_reset:
                            wait_time = (rate_limit_reset - datetime.now()).total_seconds()
                            if wait_time > 0:
                                print(f"Rate limit low ({rate_limit_remaining}). Waiting {wait_time:.0f}s until reset.")
                                await asyncio.sleep(wait_time + 1)

                        # Handle response
                        if repos_response.status == 200:
                            # Update cache with new ETag and last check time
                            new_etag = repos_response.headers.get('ETag')
                            if new_etag:
                                repo_cache.set_etag(cache_key, new_etag)
                            
                            # Update last modified time from response headers
                            last_modified = repos_response.headers.get('Last-Modified')
                            if last_modified:
                                repo_cache.set_last_modified(cache_key, last_modified)
                            
                            # Update last check time
                            repo_cache.set_last_check(cache_key)

                            repos_data = await repos_response.json()
                            if not repos_data:  # No more repos to fetch
                                break

                            for repo in repos_data:
                                repo_details = {
                                    'name': repo['name'],
                                    'description': repo['description'] or "No description",
                                    'stars': repo['stargazers_count'],
                                    'forks': repo['forks'],
                                    'language': repo['language'] or "None specified",
                                    'username': username,
                                    'country': country,
                                    'html_url': repo['html_url'],
                                    'created_at': repo['created_at'],
                                    'updated_at': repo['updated_at'],
                                    'archived': repo['archived'],
                                    'fork': repo['fork'],
                                    'fork_source': repo['parent']['full_name'] if repo['fork'] and 'parent' in repo else None,
                                    'size_kb': repo['size'],
                                    'open_issues': repo['open_issues_count'],
                                    'watchers': repo['watchers_count'],
                                    'default_branch': repo['default_branch'],
                                    'license': repo['license']['spdx_id'] if repo['license'] else None
                                }
                                full_repo_details.append(repo_details)

                            print(f"Fetched {len(repos_data)} repositories for {username} (Page {page})")
                            page += 1
                            break  # Successful request, move to next page

                        elif repos_response.status == 304:  # Not Modified
                            print(f"Data not modified for {username}")
                            repo_cache.set_last_check(cache_key)
                            return []

                        elif repos_response.status == 403:
                            response_text = await repos_response.text()
                            if 'secondary rate limit' in response_text.lower():
                                retry_after = int(repos_response.headers.get('Retry-After', backoff))
                                print(f"Secondary rate limit hit for {username}. Retrying after {retry_after} seconds.")
                                await asyncio.sleep(retry_after)
                                backoff = min(backoff * 2, 60)  # Exponential backoff, max 60 seconds
                                retry_count += 1
                            else:
                                print(f"Error 403 fetching repos for {username}: {response_text}")
                                return full_repo_details
                        else:
                            print(f"Error fetching repos for {username}: Status {repos_response.status}")
                            print(f"Response: {await repos_response.text()}")
                            return full_repo_details

            except Exception as e:
                print(f"Exception while fetching repos for {username}: {str(e)}")
                retry_count += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)  # Exponential backoff, max 60 seconds

        if retry_count == max_retries:
            print(f"Max retries reached for {username}. Moving to next page.")
            break

    # Update last check time after successful fetch
    repo_cache.set_last_check(cache_key)
    return full_repo_details

async def process_account_chunk(session, chunk, token, country, force_update=False):
    """Process a chunk of accounts in parallel"""
    tasks = [fetch_repository_details_async(session, username, token, country, force_update) for username in chunk]
    return await asyncio.gather(*tasks)

async def fetch_all_repository_details(accounts, token, force_update=False):
    """Fetch all repository details with deduplication and chunked processing."""
    all_repos = []
    processed_accounts = set()
    chunk_size = 5  # Process 5 accounts at a time to balance speed and rate limits
    
    async with aiohttp.ClientSession() as session:
        for country, usernames in accounts.items():
            # Filter out duplicates and create chunks
            unique_usernames = [u for u in usernames if u not in processed_accounts]
            chunks = [unique_usernames[i:i + chunk_size] for i in range(0, len(unique_usernames), chunk_size)]
            
            for chunk in chunks:
                print(f"Processing chunk of {len(chunk)} accounts from {country}...")
                results = await process_account_chunk(session, chunk, token, country, force_update)
                processed_accounts.update(chunk)
                
                # Collect repositories from chunk
                for result in results:
                    if result:
                        all_repos.extend(result)
                
                # Print progress
                print(f"Progress: {len(processed_accounts)} accounts processed, {len(all_repos)} repos found")
        
        # Deduplicate repositories based on html_url
        unique_repos = []
        seen_urls = set()
        for repo in all_repos:
            if repo['html_url'] not in seen_urls:
                seen_urls.add(repo['html_url'])
                unique_repos.append(repo)
        
        print(f"Found {len(all_repos)} total repositories")
        print(f"After deduplication: {len(unique_repos)} unique repositories")
        
        return unique_repos
