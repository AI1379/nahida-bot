#
# Created by Renatus Madrigal on 03/30/2025
#

import os
from nahida_bot.localstore.cache_manager import CacheManager


class LocalStoreManager:
    def __init__(self, data_path: str):
        self.table = {}
        self.cache_table = {}
        self.data_path = data_path
        self.cache_path = os.path.join(data_path, "cache")
        os.makedirs(self.cache_path, exist_ok=True)
        self.cache_manager = CacheManager(self.cache_path)

    def register(self, plugin_name: str, store: type):
        if plugin_name not in self.table:
            self.table[plugin_name] = store(self.data_path, plugin_name)
        return self.table[plugin_name]

    def get_store(self, plugin_name: str):
        return self.table[plugin_name]

    def register_cache(self, plugin_name: str) -> CacheManager.PluginCache:
        if plugin_name not in self.cache_table:
            self.cache_table[plugin_name] = self.cache_manager.register_plugin(
                plugin_name
            )
        return self.cache_table[plugin_name]
