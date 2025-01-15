import pandas as pd
from anthropic import Anthropic
import os
from dotenv import load_dotenv
from tqdm import tqdm
import time
import requests
import base64
from typing import Optional, Tuple
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import WebDriverException

# Load environment variables
load_dotenv()

# Initialize Anthropic client
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

def get_readme_content(repo_owner: str, repo_name: str) -> Optional[str]:
    """Fetch README content directly from GitHub API."""
    headers = {'Authorization': f'token {GITHUB_TOKEN}'}
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/readme'
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            content = response.json()['content']
            return base64.b64decode(content).decode('utf-8')
        return None
    except Exception as e:
        print(f"Error fetching README for {repo_owner}/{repo_name}: {str(e)}")
        return None



def take_repo_screenshot(repo_url: str, repo_owner: str, repo_name: str) -> Optional[str]:
    """Take a full-page screenshot of the repository using Selenium with Chrome."""
    screenshot_dir = 'repo_screenshots'
    os.makedirs(screenshot_dir, exist_ok=True)
    
    screenshot_path = f"{screenshot_dir}/{repo_owner}_{repo_name}.png"
    
    # If screenshot already exists, return its path
    if os.path.exists(screenshot_path):
        return screenshot_path
        
    driver = None
    try:
        print(f"\nTaking screenshot of {repo_url}")
        
        # Configure Chrome options
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        
        # Initialize the driver with ChromeDriverManager
        # FIX: Use webdriver_manager to manage ChromeDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Navigate to the repository
        driver.get(repo_url)
        
        # Wait for the main content to load
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception as e:
            print(f"Warning: Timeout waiting for page load: {str(e)}")
        
        # Get the total height of the page
        try:
            total_height = driver.execute_script(
                "return Math.max( document.body.scrollHeight, document.body.offsetHeight, "
                "document.documentElement.clientHeight, document.documentElement.scrollHeight, "
                "document.documentElement.offsetHeight );"
            )
            driver.set_window_size(1920, total_height)
        except Exception as e:
            print(f"Warning: Error setting window size: {str(e)}")
        
        # Take screenshot
        driver.save_screenshot(screenshot_path)
        
        if os.path.exists(screenshot_path):
            print(f"Screenshot saved successfully to {screenshot_path}")
            return screenshot_path
        else:
            print("Error: Screenshot file was not created")
            return None
        
    except WebDriverException as e:
        print(f"WebDriverException: Ensure Chrome is installed and matches ChromeDriver. Details: {str(e)}")
    except Exception as e:
        print(f"Error taking screenshot for {repo_url}: {str(e)}")
    finally:
        if driver:
            driver.quit()

    return None

def get_repo_content(repo_url: str, repo_owner: str, repo_name: str) -> Tuple[str, bool]:
    """Get repository content either from README or screenshot."""
    # First try to get README
    readme_content = get_readme_content(repo_owner, repo_name)
    if readme_content:
        return readme_content, True
    
    # If no README, take screenshot
    screenshot_path = take_repo_screenshot(repo_url, repo_owner, repo_name)
    if screenshot_path:
        return f"[Repository screenshot saved to {screenshot_path}]", False
        
    return "No content could be retrieved", False

def get_llm_description(content: str, repo_name: str, repo_url: str, is_readme: bool) -> Optional[str]:
    """Get an LLM-generated description of the repository."""
    
    if is_readme:
        prompt = f"""You are a technical analyst who specializes in understanding government technology repositories. 
        I will provide you with the README content of a government repository, and I would like you to generate a clear, 
        detailed description of what the repository is trying to achieve.

        Repository Name: {repo_name}
        Repository URL: {repo_url}

        README Content:
        {content}

        Please provide a concise but informative description of what this repository is trying to achieve. 
        Focus on the main purpose and key functionalities. Keep the description under 200 words."""
    else:
        prompt = f"""You are a technical analyst who specializes in understanding government technology repositories. 
        I will provide you with information about a government repository, and I would like you to generate a clear, 
        detailed description of what the repository is trying to achieve based on the available visual information.

        Repository Name: {repo_name}
        Repository URL: {repo_url}

        Repository Information:
        {content}

        Please provide a concise but informative description of what this repository appears to be trying to achieve. 
        Focus on the main purpose and key functionalities that can be inferred. Keep the description under 200 words."""

    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=4096,  # Maximum allowed for claude-3-haiku
            temperature=0.7,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return str(response.content)
    except Exception as e:
        print(f"Error getting LLM description for {repo_name}: {str(e)}")
        return None

def main(test_mode: bool = False):
    # Read the parquet file
    print("Reading repository data...")
    df = pd.read_parquet('all_government_repositories_20250115.parquet')
    
    # Add new columns if they don't exist
    if 'llm_description' not in df.columns:
        df['llm_description'] = None
    if 'content_source' not in df.columns:
        df['content_source'] = None
    
    # Create output directories if they don't exist
    os.makedirs('repo_contents', exist_ok=True)
    os.makedirs('repo_screenshots', exist_ok=True)
    
    # Process repositories
    print("\nProcessing repositories...")
    processed_count = 0
    
    # In test mode, process specific repositories
    if test_mode:
        print("Running in test mode")
        # Create a test DataFrame with specific repositories
        test_data = {
            'html_url': [
                'https://github.com/healthgovau/health-design-system-starter-kit',  # Has README
                'https://github.com/govau/test-no-readme',  # Non-existent repo to force screenshot
            ],
            'name': ['health-design-system-starter-kit', 'test-no-readme'],
            'llm_description': [None, None],
            'content_source': [None, None]
        }
        df = pd.DataFrame(test_data)
        
    for idx, row in tqdm(df.iterrows(), total=len(df)):
        # Skip if already has LLM description
        if pd.notna(df.at[idx, 'llm_description']):
            continue
            
        try:
            # Extract owner and name from html_url
            # Format: https://github.com/owner/name
            parts = row['html_url'].split('/')
            repo_owner = parts[-2]
            repo_name = parts[-1]
            
            # Get repository content
            content, is_readme = get_repo_content(row['html_url'], repo_owner, repo_name)
            
            # Save content source information
            df.at[idx, 'content_source'] = 'README' if is_readme else 'Screenshot'
            
            print(f"\nProcessing {repo_name}")
            print(f"Content source: {'README' if is_readme else 'Screenshot'}")
            if content:
                print("Content retrieved successfully")
                # Get LLM description
                llm_description = get_llm_description(content, row['name'], row['html_url'], is_readme)
            else:
                print("No content could be retrieved")
                continue
            
            if llm_description:
                # Update DataFrame
                df.at[idx, 'llm_description'] = llm_description
                processed_count += 1
                
                # Save progress periodically
                if processed_count % 10 == 0:
                    df.to_parquet('all_government_repositories_with_llm.parquet', index=False)
                    df.to_csv('all_government_repositories_with_llm.csv', index=False)
            
            # Sleep to avoid rate limiting
            time.sleep(1)
            
        except Exception as e:
            print(f"\nError processing {row['html_url']}: {str(e)}")
            continue
    
    # Save final results
    print("\nSaving final results...")
    df.to_parquet('all_government_repositories_with_llm.parquet', index=False)
    df.to_csv('all_government_repositories_with_llm.csv', index=False)
    
    print("\nProcess completed!")
    print(f"Total repositories processed: {len(df[df['llm_description'].notna()])}")
    print("\nContent source breakdown:")
    print(df['content_source'].value_counts())

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Generate LLM descriptions for government repositories')
    parser.add_argument('--test', action='store_true', help='Run in test mode (process only 2 repositories)')
    args = parser.parse_args()
    
    main(test_mode=args.test)
