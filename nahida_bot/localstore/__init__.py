#
# Created by Renatus Madrigal on 03/28/2025
#

from typing import Optional
from nahida_bot.localstore.localstore import BaseLocalStore

_localstore: Optional[BaseLocalStore] = None

def get_localstore() -> BaseLocalStore:
    """
    Get the local store instance.
    """
    global _localstore
    if _localstore is None:
        raise RuntimeError("Local store not initialized")
    return _localstore

def init_localstore(localstore: BaseLocalStore) -> None:
    """
    Initialize the local store instance.
    """
    global _localstore
    if _localstore is not None:
        raise RuntimeError("Local store already initialized")
    _localstore = localstore