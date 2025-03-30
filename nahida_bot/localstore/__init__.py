#
# Created by Renatus Madrigal on 03/28/2025
#

from typing import Optional
from nahida_bot.localstore.localstore import BaseLocalStore
from nahida_bot.localstore.localstore_manager import LocalStoreManager

_localstore_manager: Optional[LocalStoreManager] = None

def get_localstore_manager() -> LocalStoreManager:
    global _localstore_manager
    if _localstore_manager is None:
        raise RuntimeError("LocalStoreManager not initialized")
    return _localstore_manager

def set_localstore_manager(manager: LocalStoreManager) -> None:
    global _localstore_manager
    if _localstore_manager is not None:
        raise RuntimeError("LocalStoreManager already initialized")
    _localstore_manager = manager