import aiohttp
import yaml
import os
from dotenv import load_dotenv
import pandas as pd
from cachetools import TTLCache
import asyncio
from aiolimiter import AsyncLimiter

# Load environment variables from .env file
load_dotenv('.env')

# Access environment variables
github_token = os.getenv('GITHUB_TOKEN')

# Create a cache with a 1-hour TTL
cache = TTLCache(maxsize=1000, ttl=3600)

# Create a rate limiter: 30 requests per minute
rate_limit = AsyncLimiter(1000, 3600)

async def fetch_gov_github_accounts(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                text = await response.text()
                return yaml.safe_load(text)
    return None

async def fetch_repository_details_async(session, username, token, country, csv_file, parquet_file):
    cache_key = f"{username}_{country}"
    if cache_key in cache:
        return cache[cache_key]

    headers = {'Authorization': f'token {token}'}
    base_url = f"https://api.github.com/users/{username}/repos"
    full_repo_details = []
    page = 1
    per_page = 100  # GitHub API allows up to 100 items per page
    max_retries = 5
    initial_backoff = 1

    while True:
        url = f"{base_url}?page={page}&per_page={per_page}"
        retry_count = 0
        backoff = initial_backoff

        while retry_count < max_retries:
            try:
                async with rate_limit:
                    async with session.get(url, headers=headers) as repos_response:
                        if repos_response.status == 200:
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
                                    'html_url': repo['html_url']
                                }
                                full_repo_details.append(repo_details)

                            # Write to CSV incrementally
                            df = pd.DataFrame(full_repo_details)
                            df.to_csv(csv_file, mode='a', header=not os.path.exists(csv_file), index=False)

                            # Write to Parquet incrementally
                            if os.path.exists(parquet_file):
                                existing_df = pd.read_parquet(parquet_file)
                                df = pd.concat([existing_df, df])
                            df.to_parquet(parquet_file, index=False)

                            print(f"Fetched {len(full_repo_details)} repositories for {username} (Page {page})")
                            page += 1
                            break  # Successful request, move to next page
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

    cache[cache_key] = full_repo_details
    return full_repo_details

async def fetch_all_repository_details(accounts, token, csv_file, parquet_file):
    all_repos = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        for country, usernames in accounts.items():
            for username in usernames:
                task = fetch_repository_details_async(session, username, token, country, csv_file, parquet_file)
                tasks.append(task)

        results = await asyncio.gather(*tasks)
        all_repos = [repo for result in results if result for repo in result]

    return all_repos
