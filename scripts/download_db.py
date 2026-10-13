"""Restore history atomically; failed restore is always fatal."""
import os
import urllib.request  # kept visible for boundary mocking
from govtech_scraper.publication import restore_database


def main():
    restore_database(
        'https://huggingface.co/datasets/AndreasThinks/government-github-repos/resolve/main/data/govtech.db',
        'govtech.db', token=os.getenv('HF_TOKEN'))
    print('Validated database restored')


if __name__ == '__main__':
    main()
