#
# Created by Renatus Madrigal on 03/30/2025
#

class LocalStoreManager:
    def __init__(self, data_path: str):
        self.table = {}
        self.data_path = data_path

    def register(self, plugin_name: str, store: type):
        self.table[plugin_name] = store(self.data_path, plugin_name)
        return self.table[plugin_name]

    def get_store(self, plugin_name: str):
        return self.table[plugin_name]

    # TODO: Implement a cache handler
    def register_cache(self, plugin_name: str):
        pass
