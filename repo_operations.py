import json
import os
from datetime import datetime
from typing import Dict, Optional

class RepositoryCache:
    def __init__(self, cache_file: str = ".repo_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict:
        """Load cache from file or create new if doesn't exist"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {"etags": {}, "last_modified": {}, "last_check": {}}
        return {"etags": {}, "last_modified": {}, "last_check": {}}

    def save_cache(self):
        """Save cache to file"""
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache, f)

    def get_etag(self, cache_key: str) -> Optional[str]:
        """Get ETag for a given cache key"""
        return self.cache["etags"].get(cache_key)

    def set_etag(self, cache_key: str, etag: str):
        """Set ETag for a given cache key"""
        self.cache["etags"][cache_key] = etag
        self.save_cache()

    def get_last_modified(self, cache_key: str) -> Optional[str]:
        """Get last modified timestamp for a given cache key"""
        return self.cache["last_modified"].get(cache_key)

    def set_last_modified(self, cache_key: str, timestamp: str):
        """Set last modified timestamp for a given cache key"""
        self.cache["last_modified"][cache_key] = timestamp
        self.save_cache()

    def get_last_check(self, cache_key: str) -> Optional[str]:
        """Get last check timestamp for a given cache key"""
        return self.cache["last_check"].get(cache_key)

    def set_last_check(self, cache_key: str):
        """Set last check timestamp for a given cache key"""
        self.cache["last_check"][cache_key] = datetime.utcnow().isoformat()
        self.save_cache()

    def should_update(self, cache_key: str, force_update: bool = False) -> bool:
        """
        Determine if a repository should be updated based on:
        1. Force update flag
        2. Last check time (if > 24 hours, should check)
        3. Last modified time (if exists)
        """
        if force_update:
            return True

        last_check = self.get_last_check(cache_key)
        if not last_check:
            return True

        # Check if 24 hours have passed since last check
        last_check_time = datetime.fromisoformat(last_check)
        hours_since_check = (datetime.utcnow() - last_check_time).total_seconds() / 3600
        return hours_since_check >= 24
