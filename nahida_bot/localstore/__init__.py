#
# Created by Renatus Madrigal on 03/28/2025
#

from typing import Optional
from nahida_bot.localstore.localstore_manager import LocalStoreManager
from nahida_bot.localstore.sqlite3_v2 import SQLite3DBv2, SQLiteTypeAdapter, TableSchema
import os

_localstore_manager: Optional[LocalStoreManager] = None


def init(path: str):
    """
    Get the localstore manager object
    :param data_path the relative path to the data dir.
    """
    global _localstore_manager
    if _localstore_manager is None:
        _localstore_manager = LocalStoreManager(path)


def get_localstore_manager() -> LocalStoreManager:
    """
    Get the localstore manager object
    :param data_path the relative path to the data dir.
    """
    global _localstore_manager
    if _localstore_manager is None:
        raise RuntimeError("Local store object is not initialized")
    return _localstore_manager


def register[T](plugin_name: str, store: type[T]) -> T:
    if _localstore_manager is None:
        raise RuntimeError("Local store object is not initialized")
    return _localstore_manager.register(plugin_name, store)


def get_json(plugin_name: str, filename: str):
    if _localstore_manager is None:
        raise RuntimeError("Local store object is not initialized")
    return _localstore_manager.get_json(plugin_name, filename)


def get_store(plugin_name: str):
    return _localstore_manager.get_store(plugin_name)


def register_cache(plugin_name: str):
    return _localstore_manager.register_cache(plugin_name)


# Export SQLite3DBv2 and related classes for easy import
__all__ = [
    'LocalStoreManager',
    'SQLite3DBv2',
    'SQLiteTypeAdapter',
    'TableSchema',
    'init',
    'get_localstore_manager',
    'register',
    'get_json',
    'get_store',
    'register_cache',
]

