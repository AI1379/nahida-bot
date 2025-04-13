#
# Created by Renatus Madrigal on 04/11/2025
#

import os
import json
from pathlib import Path
from datetime import datetime

DEFAULT_CACHE_DAYS = 1


class CacheManager:
    def __init__(self, cache_path: str, cache_days: int = DEFAULT_CACHE_DAYS):
        os.makedirs(cache_path, exist_ok=True)
        self.cache_path = cache_path
        self.record_file = os.path.join(cache_path, "cache_record.json")
        if not os.path.exists(self.record_file):
            # Create an empty JSON file if it doesn't exist
            with open(self.record_file, "w", encoding="utf-8") as f:
                f.write("{}")
        self.cache_days = cache_days

    class PluginCache:
        def __init__(self,
                     cache_path: str,
                     plugin_name: str,
                     file_path: str,
                     cache_days: int):
            self.plugin_name = plugin_name
            self.cache_path = cache_path
            self.record_file = file_path
            self.cache_days = cache_days

        def _get_file_diff_time(self, name: str):
            full_path = os.path.join(
                self.cache_path,
                self.plugin_name,
                name
            )
            if not os.path.exists(full_path):
                return 0
            timestamp = Path(full_path).stat().st_mtime
            mtime = datetime.fromtimestamp(timestamp)
            now = datetime.now()
            return (now - mtime).days

        def clean_cache(self):
            """Clean the cache by removing files older than self.cache_days."""
            with open(self.record_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if self.plugin_name in data:
                removed_list = [record for record in data[self.plugin_name]
                                if self._get_file_diff_time(record["name"]) <= self.cache_days]
                full_paths = [
                    os.path.join(self.cache_path,
                                 self.plugin_name, record["name"])
                    for record in data[self.plugin_name]
                    if self._get_file_diff_time(record["name"]) > self.cache_days
                ]
                data[self.plugin_name] = removed_list
                for full_path in full_paths:
                    try:
                        os.remove(full_path)
                    except FileNotFoundError:
                        pass
                with open(self.record_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)

        def push_file(self, name: str):
            """Add a file to the cache record."""
            """Note that this function does not do anything to the file."""
            full_path = os.path.join(
                self.cache_path,
                self.plugin_name,
                name
            )
            if not os.path.exists(full_path):
                return
            with open(self.record_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if self.plugin_name not in data:
                data[self.plugin_name] = []
            cache_record = {
                "name": name,
                "time": os.path.getmtime(full_path),
            }
            data[self.plugin_name].append(cache_record)
            with open(self.record_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            self.clean_cache()
            
        def add_file(self, name:str, op, mode="w"):
            """Add a file."""
            full_path = os.path.join(
                self.cache_path,
                self.plugin_name,
                name
            )
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, mode) as f:
                op(f)
            self.push_file(name)
            return full_path

    def register_plugin(self, plugin_name: str):
        return self.PluginCache(self.cache_path,
                          plugin_name,
                          self.record_file,
                          self.cache_days)
