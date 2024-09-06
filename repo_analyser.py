import git
import subprocess
import os
import shutil


def analyse_repo(url : str, output_dir : str):
    """ Analyse the repository details from the given URL."""

    # download it locally

    # convert the folder structure to a text file, starting with just file names.
    #
    # pass that to an LLM, and ask it to pick file names to prioritise.
    #
    # for each file, convert to a text
    #
    # pass a few files to the LLM
    # readme if available
    # repo name and organisation
    # the key files that have been identified.
    # cut it offat token limit
    # ask the LLM to produce a summary of what the repo is trying to achieve
    # store the text file
    # delete the repo from local storage


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
