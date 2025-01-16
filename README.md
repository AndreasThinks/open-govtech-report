# Open GovTech Repository Analyzer

A Python tool for analyzing government GitHub repositories at scale. This tool efficiently scrapes repository data and READMEs from government GitHub accounts worldwide, handling rate limits and authentication through GitHub Apps.

## Features

- Fetches repositories from government GitHub accounts worldwide
- Downloads and analyzes repository READMEs
- Uses GitHub App authentication for higher rate limits (15,000 requests/hour)
- Implements intelligent caching to minimize API requests
- Handles rate limiting and token refresh automatically
- Supports incremental updates

## Setup

1. Create a GitHub App:
   - Go to GitHub Developer Settings
   - Create a new GitHub App
   - Set permissions:
     - Repository contents: Read-only
     - Metadata: Read-only
   - Install the app in your account
   - Note down:
     - App ID
     - Installation ID
     - Private key (download the .pem file)

2. Configure environment:
   ```bash
   # Create .env file
   GITHUB_APP_ID=your_app_id
   GITHUB_INSTALLATION_ID=your_installation_id
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Place your private key file in the project directory as:
   ```
   open-govtech-report.2025-01-15.private-key.pem
   ```

## Usage

### Basic Usage

```bash
# Fetch new repositories and READMEs
python main.py

# Force update all repositories
python main.py --force-update

# Only update READMEs
python main.py --readmes-only

# Limit the number of repositories (for testing)
python main.py --limit 100
```

### Command Line Options

- `--force-update`: Ignore cache and update all repositories
- `--readmes-only`: Skip repository fetching, only update READMEs
- `--limit N`: Process only N repositories (useful for testing)

## How It Works

The tool operates in three main stages:

1. **Repository Discovery** (`scrape_repos.py`):
   - Fetches list of government accounts from GitHub's government.github.com
   - Retrieves repository metadata for each account
   - Uses chunked processing and rate limiting
   - Caches results to minimize API requests

2. **README Fetching** (`fetch_readmes.py`):
   - Downloads README content for each repository
   - Handles base64 encoding and special characters
   - Implements retry logic for failed requests
   - Processes repositories in parallel chunks

3. **Data Management** (`repo_operations.py`):
   - Manages caching of API responses
   - Tracks ETags and Last-Modified headers
   - Determines when to update cached data
   - Handles token refresh and rate limits

## Output Files

The tool generates several output files:

1. Repository Data:
   ```
   all_government_repositories_YYYYMMDD.parquet
   all_government_repositories_YYYYMMDD.csv
   ```
   Contains repository metadata including:
   - Name, description, language
   - Stars, forks, watchers
   - Creation and update dates
   - Size and license information

2. README Data:
   ```
   all_government_repositories_combined_YYYYMMDD.parquet
   all_government_repositories_combined_YYYYMMDD.csv
   ```
   Includes repository data plus:
   - README content (base64 encoded)
   - README size and encoding
   - README URL

## Cache Management

The tool uses `.repo_cache.json` to store:
- ETags for conditional requests
- Last-modified timestamps
- Last check times

This minimizes API requests and helps stay within rate limits.

## Rate Limiting

- Uses GitHub App authentication for 15,000 requests/hour
- Implements a rate limiter with 14,500 requests/hour (buffer)
- Handles secondary rate limits with exponential backoff
- Automatically waits when approaching limits

## Error Handling

- Retries failed requests with exponential backoff
- Handles token expiration and refresh
- Creates empty entries for missing READMEs
- Preserves partial results on interruption

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - feel free to use and modify as needed.
