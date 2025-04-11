#
# Created by Renatus Madrigal on 04/11/2025
#

import os
import json
from pathlib import Path
from datetime import datetime

CACHE_DAYS = 1


class CacheManager:
    def __init__(self, cache_path: str):
        os.mkdir(cache_path, exist_ok=True)
        self.cache_path = cache_path
        self.record_file = os.path.join(cache_path, "cache_record.json")
        if not os.path.exists(self.record_file):
            # Create an empty JSON file if it doesn't exist
            with open(self.record_file, "w", encoding="utf-8") as f:
                f.write("{}")

    class Cache:
        def __init__(self,
                     cache_path: str,
                     plugin_name: str,
                     file_path: str):
            self.plugin_name = plugin_name
            self.cache_path = cache_path
            self.file_path = file_path

        def get_file_diff_time(self, name: str):
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
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if self.plugin_name in data:
                removed_list = [record for record in data[self.plugin_name]
                                if self.get_file_diff_time(record["name"]) <= CACHE_DAYS]
                full_paths = [
                    os.path.join(self.cache_path,
                                 self.plugin_name, record["name"])
                    for record in data[self.plugin_name]
                    if self.get_file_diff_time(record["name"]) > CACHE_DAYS
                ]
                data[self.plugin_name] = removed_list
                for full_path in full_paths:
                    try:
                        os.remove(full_path)
                    except FileNotFoundError:
                        pass
                with open(self.file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)

        def add_file(self, name: str):
            full_path = os.path.join(
                self.cache_path,
                self.plugin_name,
                name
            )
            if not os.path.exists(full_path):
                return
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if self.plugin_name not in data:
                data[self.plugin_name] = []
            cache_record = {
                "name": name,
                "time": os.path.getmtime(full_path),
            }
            data[self.plugin_name].append(cache_record)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            self.clean_cache()

    def register_plugin(self, plugin_name: str):
        return self.Cache(self.cache_path, plugin_name, self.record_file)
