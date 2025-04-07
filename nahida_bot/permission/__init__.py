#
# Created by Renatus Madrigal on 04/01/2025
#

"""
The Permission module is used to manage the permission of users and the state of
the plugins.
We use the localstore module to store the data of permissions.

The permission system has three levels:

- Feature level: The default permission of a feature for all users.
- Group level: The permission of a feature for a specific group.
- User level: The permission of a feature for a specific user.

Feature level permissions are stored in the `features` table, group level
permissions are stored in the `groups` table, and user level permissions are
stored in the `users` table.

The `features` table has the following columns:

- `id`: The id of the record (INTEGER PRIMARY KEY AUTOINCREMENT)
- `plugin`: The name of the plugin (TEXT NOT NULL)
- `feature`: The name of the feature (TEXT NOT NULL)
- `admin`: The permission of the feature for admins (INTEGER NOT NULL)
- `group`: The permission of the feature for group members (INTEGER NOT NULL)
- `user`: The permission of the feature for users (INTEGER NOT NULL)

The `groups` table has the following columns:

- `id`: The id of the record (INTEGER PRIMARY KEY AUTOINCREMENT)
- `plugin`: The name of the plugin (TEXT NOT NULL)
- `feature`: The name of the feature (TEXT NOT NULL)
- `group_id`: The id of the group (TEXT NOT NULL)
- `state`: The permission of the feature for the group (INTEGER NOT NULL)

The `users` table has the following columns:

- `id`: The id of the record (INTEGER PRIMARY KEY AUTOINCREMENT)
- `plugin`: The name of the plugin (TEXT NOT NULL)
- `feature`: The name of the feature (TEXT NOT NULL)
- `user_id`: The id of the user (TEXT NOT NULL)
- `state`: The permission of the feature for the user (INTEGER NOT NULL)

On an event, the permission of a feature is checked in the following order:

1. Feature level permission. If the feature is disabled for all users, the event
   is ignored.
2. Group level permission. If the group level permission is enabled, the event
   is processed with the group level permission.
3. User level permission. If the user level permission is enabled, the event
   is processed with the user level permission. If the user is not found in the
   table, the event is processed with the group level permission.

Besides, the `superusers` table is used to store the superuser ids. The superuser
is allowed to use all features regardless of the permission level.

"""

from nahida_bot.localstore.sqlite3 import SQLite3DB, PRIMARY_KEY_TYPE, TEXT, INTEGER
from nahida_bot.localstore import register, LocalStoreManager
from typing import Dict, Any, Optional, Tuple, List
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent
from nonebot.adapters import Event

_permission_store: Optional[SQLite3DB] = None

ALLOW = 1
DENY = 0

DEFAULT_PERMISSION = {
    "admin": ALLOW,
    "group": ALLOW,
    "user": ALLOW,
}


def _insert_if_not_exists(table: str, record: Dict[str, str]):
    """
    Insert a record into the table if it does not exist.
    If the record exists, update it with the new values.
    """
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")

    # Check if the record exists
    existing_record = _permission_store.select(table, record)
    if not existing_record:
        # Insert the new record
        _permission_store.insert(table, record)


def _delete_if_exists(table: str, record: Dict[str, str]):
    """
    Delete a record from the table if it exists.
    If the record does not exist, do nothing.
    """
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")

    # Check if the record exists
    existing_record = _permission_store.select(table, record)
    if existing_record:
        # Delete the record
        _permission_store.delete(table, record)


def init(superuser: str = None, db_name: str = "permission", localstore: LocalStoreManager = None):
    global _permission_store
    if localstore is None:
        _permission_store = register(db_name, SQLite3DB)
    else:
        _permission_store = localstore.register(db_name, SQLite3DB)
    _create_tables(superuser)


def _create_tables(superuser: str = None):
    _permission_store.create_table("features", {
        "id": PRIMARY_KEY_TYPE,
        "plugin": TEXT,
        "feature": TEXT,
        "admin": INTEGER,
        "group_perm": INTEGER,
        "user": INTEGER,
    })
    _permission_store.create_table("groups", {
        "id": PRIMARY_KEY_TYPE,
        "plugin": TEXT,
        "feature": TEXT,
        "group_id": TEXT,
        "state": INTEGER,
    })
    _permission_store.create_table("users", {
        "id": PRIMARY_KEY_TYPE,
        "plugin": TEXT,
        "feature": TEXT,
        "user_id": TEXT,
        "state": INTEGER,
    })
    _permission_store.create_table("superusers", {
        "id": PRIMARY_KEY_TYPE,
        "user_id": TEXT,
    })
    if superuser:
        _insert_if_not_exists("superusers", {
            "user_id": superuser,
        })


def set_superuser(user_id: str):
    """Set a user as superuser"""
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    _insert_if_not_exists("superusers", {
        "user_id": user_id,
    })


def add_superuser(user_id: str):
    """Add a user to superuser"""
    set_superuser(user_id)


def remove_superuser(user_id: str):
    """Remove a user from superuser"""
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    _delete_if_exists("superusers", {
        "user_id": user_id,
    })


def update_feature_permission(plugin: str, feature: str, admin: int = -1, group: int = -1, user: int = -1):
    """Update the permission of a feature"""
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    if admin == -1 and group == -1 and user == -1:
        return
    current_record = _permission_store.select("features", {
        "plugin": plugin,
        "feature": feature,
    })
    if not current_record:
        _permission_store.insert("features", {
            "plugin": plugin,
            "feature": feature,
            "admin": admin if admin != -1 else DEFAULT_PERMISSION["admin"],
            "group_perm": group if group != -1 else DEFAULT_PERMISSION["group"],
            "user": user if user != -1 else DEFAULT_PERMISSION["user"],
        })
    else:
        current_record = current_record[0]
        _permission_store.update("features", {
            "admin": admin if admin != -1 else current_record[3],
            "group_perm": group if group != -1 else current_record[4],
            "user": user if user != -1 else current_record[5],
        }, {
            "plugin": plugin,
            "feature": feature,
        })


def update_group_permission(plugin: str, feature: str, group_id: str, state: int):
    """Update the permission of a feature for a group"""
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    current_record = _permission_store.select("groups", {
        "plugin": plugin,
        "feature": feature,
        "group_id": group_id,
    })
    if not current_record:
        _permission_store.insert("groups", {
            "plugin": plugin,
            "feature": feature,
            "group_id": group_id,
            "state": state,
        })
    else:
        current_record = current_record[0]
        _permission_store.update("groups", {
            "state": state,
        }, {
            "plugin": plugin,
            "feature": feature,
            "group_id": group_id,
        })


def update_user_permission(plugin: str, feature: str, user_id: str, state: int):
    """Update the permission of a feature for a user"""
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    current_record = _permission_store.select("users", {
        "plugin": plugin,
        "feature": feature,
        "user_id": user_id,
    })
    if not current_record:
        _permission_store.insert("users", {
            "plugin": plugin,
            "feature": feature,
            "user_id": user_id,
            "state": state,
        })
    else:
        current_record = current_record[0]
        _permission_store.update("users", {
            "state": state,
        }, {
            "plugin": plugin,
            "feature": feature,
            "user_id": user_id,
        })


def _check_superuser(user_id: str) -> bool:
    """Check if a user is superuser"""
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    superuser = _permission_store.select("superusers", {
        "user_id": user_id,
    })
    return bool(superuser)


def _check_admin(event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        return (event.sender.role == "admin" or
                event.sender.role == "owner" or
                _check_superuser(event.sender.user_id))
    return _check_superuser(event.sender.user_id)


def _group_handler(event: GroupMessageEvent, plugin: str, feature: str) -> bool:
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")

    if _check_superuser(event.sender.user_id):
        return True

    feature_record = _permission_store.select("features", {
        "plugin": plugin,
        "feature": feature,
    })
    # Not found or disabled
    if not feature_record or feature_record[0][4] == 0:
        return False

    group_id = event.group_id
    group_record = _permission_store.select("groups", {
        "plugin": plugin,
        "feature": feature,
        "group_id": group_id,
    })
    if _check_admin(event):
        group_state = feature_record[0][3] == 1 if group_record else DEFAULT_PERMISSION["admin"]
    else:
        group_state = group_record[0][4] == 1 if group_record else DEFAULT_PERMISSION["group"]

    user_id = event.sender.user_id
    user_record = _permission_store.select("users", {
        "plugin": plugin,
        "feature": feature,
        "user_id": user_id,
    })
    user_state = user_record[0][4] == 1 if user_record else DEFAULT_PERMISSION["user"]

    if group_state and user_state:
        return True

    return False


def _private_handler(event: MessageEvent, plugin: str, feature: str) -> bool:
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")

    if _check_superuser(event.sender.user_id):
        return True

    feature_record = _permission_store.select("features", {
        "plugin": plugin,
        "feature": feature,
    })
    # Not found or disabled
    if not feature_record or feature_record[0][5] == 0:
        return False

    user_id = event.sender.user_id
    user_record = _permission_store.select("users", {
        "plugin": plugin,
        "feature": feature,
        "user_id": user_id,
    })
    user_state = user_record[0][4] == 1 if user_record else DEFAULT_PERMISSION["user"]

    if user_state:
        return True
    return False


def check_permission(event: MessageEvent, plugin: str, feature: str) -> bool:
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    if isinstance(event, GroupMessageEvent):
        return _group_handler(event, plugin, feature)
    else:
        return _private_handler(event, plugin, feature)


def get_checker(plugin: str, feature: str):
    async def checker(event: Event):
        if isinstance(event, MessageEvent):
            return check_permission(event, plugin, feature)
        return False

    return checker
