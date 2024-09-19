import git
import subprocess
import os
import shutil


import os
import shutil
import re
from urllib.parse import urlparse
import git
from anthropic import Anthropic, HUMAN_PROMPT, AI_PROMPT
from dotenv import load_dotenv

load_dotenv('.env')

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def analyse_repo(url: str, output_dir: str):
    """Analyse the repository details from the given URL."""
    # Extract repo name and owner from URL
    def get_repo_info(url):
        parsed_url = urlparse(url)
        path_parts = parsed_url.path.strip('/').split('/')
        if len(path_parts) >= 2:
            owner = path_parts[-2]
            repo = path_parts[-1].replace('.git', '')
            return owner, repo
        else:
            return None, None

    owner, repo = get_repo_info(url)
    if not owner or not repo:
        print("Invalid repository URL.")
        return

    # Define local paths
    local_repo_path = os.path.join(output_dir, repo)

    # Clone the repository
    print(f"Cloning repository {url} to {local_repo_path}")
    git.Repo.clone_from(url, local_repo_path)

    # Get all file names in the repository
    file_names = []
    for root, dirs, files in os.walk(local_repo_path):
        for file in files:
            file_path = os.path.join(root, file)
            # Exclude hidden files and directories
            if '/.' not in file_path and '\\.' not in file_path:
                relative_path = os.path.relpath(file_path, local_repo_path)
                file_names.append(relative_path)

    # Convert the list of file names to text
    file_list_text = '\n'.join(file_names)

    # Prepare the prompt for prioritizing files
    prompt = f"""Given the following list of file names from a repository, please pick the key files that should be prioritized for understanding the repository. List the file names in order of priority.

    {file_list_text}

    Your output should be a list of file names, in order of priority."""

    # Call the LLM to prioritize files
    response = client.completions.create(
        model="claude-v1",  # or the appropriate Claude model
        prompt=f"{HUMAN_PROMPT}You are a helpful assistant that identifies key files in a repository.\n\n{prompt}{AI_PROMPT}",
        max_tokens_to_sample=500,
        temperature=0.7
    )

    prioritized_files_text = response.completion  # Access the attribute directly

    # Extract file names from the response
    def extract_file_names(text):
        lines = text.strip().split('\n')
        file_names = []
        for line in lines:
            # Remove numbering or bullets
            line = re.sub(r'^\s*[\d\.\-\*]+\s*', '', line)
            file_names.append(line.strip())
        return file_names

    prioritized_files = extract_file_names(prioritized_files_text)

    # Read README if available
    readme_content = ''
    readme_files = ['README.md', 'README.rst', 'README.txt', 'README']
    for readme_file in readme_files:
        readme_path = os.path.join(local_repo_path, readme_file)
        if os.path.isfile(readme_path):
            with open(readme_path, 'r', encoding='utf-8', errors='ignore') as f:
                readme_content = f.read()
            break

    # Read content of prioritized files, up to a character limit
    max_chars = 8000
    total_chars = 0
    files_content = ''
    for file_name in prioritized_files:
        file_path = os.path.join(local_repo_path, file_name)
        if os.path.isfile(file_path):
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                content_length = len(content)
                if total_chars + content_length > max_chars:
                    # Truncate the content to fit the limit
                    content = content[:(max_chars - total_chars)]
                    files_content += f'\n\nFile: {file_name}\n{content}'
                    break
                else:
                    files_content += f'\n\nFile: {file_name}\n{content}'
                    total_chars += content_length

    # Prepare the final prompt for summarization
    final_prompt = f"""You are to analyze the following repository and produce a summary of what the repository is trying to achieve.

    Repository Name: {repo}
    Organization/Owner: {owner}

    """

    if readme_content:
        final_prompt += f"README:\n{readme_content}\n\n"

    final_prompt += f"The repository contains the following key files:\n{', '.join(prioritized_files)}\n\n"

    final_prompt += f"The content of the key files is as follows:\n{files_content}\n\n"

    final_prompt += "Please provide a concise summary of what this repository is trying to achieve."

    # Call the LLM to get the summary
    response_summary = client.completions.create(
        model="claude-v1",  # or the appropriate Claude model
        prompt=f"{HUMAN_PROMPT}You are a helpful assistant that summarizes code repositories.\n\n{final_prompt}{AI_PROMPT}",
        max_tokens_to_sample=500,
        temperature=0.7
    )

    print(type(response_summary))  # Debugging: Check the type of response_summary
    print(response_summary)  # Debugging: Check the content of response_summary

    # Assuming response_summary is an object, access its attributes correctly
    summary_text = response_summary.completion  # Adjust this line based on the actual attribute name

    # Store the summary to a text file
    output_file = os.path.join(output_dir, f"{repo}_summary.txt")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(summary_text)

    # Delete the cloned repository from local storage
    shutil.rmtree(local_repo_path)
    print(f"Summary saved to {output_file} and repository deleted from local storage.")

def clone_repo_locally(url : str, output_dir : str):
    """ Clone the repository locally to the output directory."""

    try:
        git.Repo.clone_from(url, output_dir)
        print(f"Repository cloned successfully to {output_dir}")
    except git.GitCommandError as e:
        print(f"Error cloning repository: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

def convert_repo_to_text(repo_dir: str):
    """ Convert the repository to a text file."""
    try:
        output_dir = os.path.join(repo_dir, "text_output")
        os.makedirs(output_dir, exist_ok=True)

        # Change to the repository directory
        os.chdir(repo_dir)

        # Run the repo-to-text command
        subprocess.run(["repo-to-text", "--output-dir", output_dir], check=True)

        print(f"Repository converted to text successfully. Output saved in {output_dir}")
        return output_dir
    except subprocess.CalledProcessError as e:
        print(f"Error running repo-to-text: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

    return None



test_url = "https://github.com/argob/accesibilidad-web"
repo_name = "accesibilidad-web"

clone_repo_locally(test_url, repo_name)
convert_repo_to_text(repo_name)

original_output_dir = os.path.join(repo_name, "text_output")

new_output_dir = "output/text_outputs"
# copy the original text output to the new
#
def copy_text_output(source_dir: str, destination_dir: str, new_file_name: str = None):
    """Copy the text output from source directory to destination directory with an optional new name."""
    os.makedirs(destination_dir, exist_ok=True)

    # Find the first (and usually only) text file in the source directory
    text_files = [f for f in os.listdir(source_dir) if f.endswith('.txt')]

    if not text_files:
        print(f"No text files found in {source_dir}")
        return

    source_file = os.path.join(source_dir, text_files[0])

    if new_file_name:
        destination_file = os.path.join(destination_dir, f"{new_file_name}.txt")
    else:
        destination_file = os.path.join(destination_dir, text_files[0])

    shutil.copy2(source_file, destination_file)
    print(f"Text output copied to {destination_file}")

# Call the function to copy the text output
copy_text_output(original_output_dir, "output", new_file_name=repo_name)
