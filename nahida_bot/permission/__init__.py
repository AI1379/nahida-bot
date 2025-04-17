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
- `group_perm`: The permission of the feature for group members (INTEGER NOT NULL)
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
from nahida_bot.utils.command_parser import check_true
from typing import Dict, Any, Optional, Tuple, List, Union
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent
from nonebot.adapters import Event
from nonebot.rule import to_me
from nonebot.log import logger
import sqlite3

ALLOW = 1
DENY = 0

DEFAULT_PERMISSION = {
    "admin": ALLOW,
    "group": ALLOW,
    "user": ALLOW,
}

class PermissionError(Exception):
    """Base exception for permission-related errors"""
    pass

class PermissionStore:
    def __init__(self, db_name: str = "permission", localstore: LocalStoreManager = None):
        if localstore is None:
            self._store = register(db_name, SQLite3DB)
        else:
            self._store = localstore.register(db_name, SQLite3DB)
        self._create_tables()

    def _create_tables(self):
        """Create the necessary tables for the permission system"""
        try:
            self._store.create_table("features", {
                "id": PRIMARY_KEY_TYPE,
                "plugin": TEXT,
                "feature": TEXT,
                "admin": INTEGER,
                "group_perm": INTEGER,  # Using group_perm to avoid SQLite keyword conflict
                "user": INTEGER,
            })
            self._store.create_table("groups", {
                "id": PRIMARY_KEY_TYPE,
                "plugin": TEXT,
                "feature": TEXT,
                "group_id": TEXT,
                "state": INTEGER,
            })
            self._store.create_table("users", {
                "id": PRIMARY_KEY_TYPE,
                "plugin": TEXT,
                "feature": TEXT,
                "user_id": TEXT,
                "state": INTEGER,
            })
            self._store.create_table("superusers", {
                "id": PRIMARY_KEY_TYPE,
                "user_id": TEXT,
            })
        except sqlite3.Error as e:
            logger.error(f"Error creating tables: {e}")
            raise PermissionError("Failed to create permission tables")

    def _validate_permission(self, value: int) -> bool:
        """Validate that a permission value is either ALLOW or DENY"""
        return value in (ALLOW, DENY)

    def _validate_input(self, plugin: str, feature: str, user_id: Optional[str] = None, group_id: Optional[str] = None):
        """Validate input parameters"""
        if not plugin or not isinstance(plugin, str):
            raise ValueError("Plugin name must be a non-empty string")
        if not feature or not isinstance(feature, str):
            raise ValueError("Feature name must be a non-empty string")
        if user_id is not None and not isinstance(user_id, str):
            raise ValueError("User ID must be a string")
        if group_id is not None and not isinstance(group_id, str):
            raise ValueError("Group ID must be a string")

    def _insert_if_not_exists(self, table: str, record: Dict[str, str]):
        """Insert a record into the table if it does not exist."""
        try:
            existing_record = self._store.select(table, record)
            if not existing_record:
                self._store.insert(table, record)
        except sqlite3.Error as e:
            logger.error(f"Error in _insert_if_not_exists: {e}")
            raise PermissionError(f"Failed to insert record into {table}")

    def _delete_if_exists(self, table: str, record: Dict[str, str]):
        """Delete a record from the table if it exists."""
        try:
            existing_record = self._store.select(table, record)
            if existing_record:
                self._store.delete(table, record)
        except sqlite3.Error as e:
            logger.error(f"Error in _delete_if_exists: {e}")
            raise PermissionError(f"Failed to delete record from {table}")

    def set_superuser(self, user_id: str):
        """Set a user as superuser"""
        self._validate_input("system", "superuser", user_id=user_id)
        self._insert_if_not_exists("superusers", {"user_id": str(user_id)})

    def remove_superuser(self, user_id: str):
        """Remove a user from superuser"""
        self._validate_input("system", "superuser", user_id=user_id)
        self._delete_if_exists("superusers", {"user_id": str(user_id)})

    def update_feature_permission(self, plugin: str, feature: str, admin: int = -1, group: int = -1, user: int = -1):
        """Update the permission of a feature"""
        self._validate_input(plugin, feature)
        
        if admin != -1 and not self._validate_permission(admin):
            raise ValueError("Admin permission must be either ALLOW or DENY")
        if group != -1 and not self._validate_permission(group):
            raise ValueError("Group permission must be either ALLOW or DENY")
        if user != -1 and not self._validate_permission(user):
            raise ValueError("User permission must be either ALLOW or DENY")

        if admin == -1 and group == -1 and user == -1:
            return

        try:
            current_record = self._store.select("features", {
                "plugin": plugin,
                "feature": feature,
            })

            if not current_record:
                self._store.insert("features", {
                    "plugin": plugin,
                    "feature": feature,
                    "admin": admin if admin != -1 else DEFAULT_PERMISSION["admin"],
                    "group_perm": group if group != -1 else DEFAULT_PERMISSION["group"],
                    "user": user if user != -1 else DEFAULT_PERMISSION["user"],
                })
            else:
                current_record = current_record[0]
                self._store.update("features", {
                    "admin": admin if admin != -1 else current_record["admin"],
                    "group_perm": group if group != -1 else current_record["group_perm"],
                    "user": user if user != -1 else current_record["user"],
                }, {
                    "plugin": plugin,
                    "feature": feature,
                })
        except sqlite3.Error as e:
            logger.error(f"Error updating feature permission: {e}")
            raise PermissionError("Failed to update feature permission")

    def update_group_permission(self, plugin: str, feature: str, group_id: str, state: int):
        """Update the permission of a feature for a group"""
        self._validate_input(plugin, feature, group_id=group_id)
        if not self._validate_permission(state):
            raise ValueError("State must be either ALLOW or DENY")

        try:
            current_record = self._store.select("groups", {
                "plugin": plugin,
                "feature": feature,
                "group_id": group_id,
            })

            if not current_record:
                self._store.insert("groups", {
                    "plugin": plugin,
                    "feature": feature,
                    "group_id": group_id,
                    "state": state,
                })
            else:
                self._store.update("groups", {
                    "state": state,
                }, {
                    "plugin": plugin,
                    "feature": feature,
                    "group_id": group_id,
                })
        except sqlite3.Error as e:
            logger.error(f"Error updating group permission: {e}")
            raise PermissionError("Failed to update group permission")

    def update_user_permission(self, plugin: str, feature: str, user_id: str, state: int):
        """Update the permission of a feature for a user"""
        self._validate_input(plugin, feature, user_id=user_id)
        if not self._validate_permission(state):
            raise ValueError("State must be either ALLOW or DENY")

        try:
            current_record = self._store.select("users", {
                "plugin": plugin,
                "feature": feature,
                "user_id": user_id,
            })

            if not current_record:
                self._store.insert("users", {
                    "plugin": plugin,
                    "feature": feature,
                    "user_id": user_id,
                    "state": state,
                })
            else:
                self._store.update("users", {
                    "state": state,
                }, {
                    "plugin": plugin,
                    "feature": feature,
                    "user_id": user_id,
                })
        except sqlite3.Error as e:
            logger.error(f"Error updating user permission: {e}")
            raise PermissionError("Failed to update user permission")

    def _check_superuser(self, user_id: Union[str, int]) -> bool:
        """Check if a user is superuser"""
        try:
            superuser = self._store.select("superusers", {"user_id": str(user_id)})
            return bool(superuser)
        except sqlite3.Error as e:
            logger.error(f"Error checking superuser: {e}")
            return False

    def _check_admin(self, event: MessageEvent) -> bool:
        """Check if a user is an admin"""
        if isinstance(event, GroupMessageEvent):
            return (event.sender.role == "admin" or
                    event.sender.role == "owner" or
                    self._check_superuser(event.sender.user_id))
        return self._check_superuser(event.sender.user_id)

    def _group_handler(self, event: GroupMessageEvent, plugin: str, feature: str) -> bool:
        """Handle group message permission check"""
        try:
            if self._check_superuser(event.sender.user_id):
                return True

            feature_record = self._store.select("features", {
                "plugin": plugin,
                "feature": feature,
            })
            if not feature_record:
                return False

            feature_data = feature_record[0]
            if feature_data["group_perm"] == DENY:
                return False

            default_group = feature_data["group_perm"] == ALLOW
            default_user = feature_data["user"] == ALLOW

            group_id = event.group_id
            group_record = self._store.select("groups", {
                "plugin": plugin,
                "feature": feature,
                "group_id": group_id,
            })

            if self._check_admin(event):
                group_state = feature_data["admin"] == ALLOW
            else:
                group_state = group_record[0]["state"] == ALLOW if group_record else default_group

            user_id = event.sender.user_id
            user_record = self._store.select("users", {
                "plugin": plugin,
                "feature": feature,
                "user_id": user_id,
            })
            user_state = user_record[0]["state"] == ALLOW if user_record else default_user

            logger.debug(f"Group state: {group_state}, User state: {user_state}")

            return group_state and user_state
        except sqlite3.Error as e:
            logger.error(f"Error in group handler: {e}")
            return False

    def _private_handler(self, event: MessageEvent, plugin: str, feature: str) -> bool:
        """Handle private message permission check"""
        try:
            if self._check_superuser(event.sender.user_id):
                return True

            feature_record = self._store.select("features", {
                "plugin": plugin,
                "feature": feature,
            })
            if not feature_record:
                return False

            feature_data = feature_record[0]
            if feature_data["user"] == DENY:
                return False

            user_id = event.sender.user_id
            user_record = self._store.select("users", {
                "plugin": plugin,
                "feature": feature,
                "user_id": user_id,
            })
            user_state = user_record[0]["state"] == ALLOW if user_record else DEFAULT_PERMISSION["user"]

            return user_state
        except sqlite3.Error as e:
            logger.error(f"Error in private handler: {e}")
            return False

    def check_permission(self, event: MessageEvent, plugin: str, feature: str) -> bool:
        """Check if a user has permission to use a feature"""
        self._validate_input(plugin, feature)
        if isinstance(event, GroupMessageEvent):
            return self._group_handler(event, plugin, feature)
        else:
            return self._private_handler(event, plugin, feature)

    def get_checker(self, plugin: str, feature: str):
        """Get a permission checker function"""
        self._validate_input(plugin, feature)
        async def checker(event: Event):
            if isinstance(event, MessageEvent):
                return self.check_permission(event, plugin, feature)
            return False
        return checker

    def get_checker_generator(self, plugin: str, admin: int = ALLOW, group: int = ALLOW, user: int = ALLOW):
        """Get a permission checker generator"""
        self._validate_input(plugin, "any")
        if not all(self._validate_permission(x) for x in (admin, group, user)):
            raise ValueError("All permission values must be either ALLOW or DENY")
            
        def checker_generator(feature: str):
            self.update_feature_permission(plugin, feature, admin, group, user)
            return self.get_checker(plugin, feature) & to_me()
        return checker_generator

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        try:
            self._store.close()
        except Exception as e:
            logger.error(f"Error closing permission store: {e}")

# Global instance
_permission_store: Optional[PermissionStore] = None

def init(superuser: str = None, db_name: str = "permission", localstore: LocalStoreManager = None):
    """Initialize the permission system"""
    global _permission_store
    _permission_store = PermissionStore(db_name, localstore)
    if superuser:
        _permission_store.set_superuser(superuser)

def check_user_id(value: str) -> bool:
    """Check if the value is a valid user id"""
    try:
        value = int(value)
        return True
    except ValueError:
        return False

# Wrapper functions for backward compatibility
def set_superuser(user_id: str):
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    _permission_store.set_superuser(user_id)

def add_superuser(user_id: str):
    set_superuser(user_id)

def remove_superuser(user_id: str):
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    _permission_store.remove_superuser(user_id)

def update_feature_permission(plugin: str, feature: str, admin: int = -1, group: int = -1, user: int = -1):
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    _permission_store.update_feature_permission(plugin, feature, admin, group, user)

def update_group_permission(plugin: str, feature: str, group_id: str, state: int):
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    _permission_store.update_group_permission(plugin, feature, group_id, state)

def update_user_permission(plugin: str, feature: str, user_id: str, state: int):
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    _permission_store.update_user_permission(plugin, feature, user_id, state)

def check_permission(event: MessageEvent, plugin: str, feature: str) -> bool:
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    return _permission_store.check_permission(event, plugin, feature)

def get_checker(plugin: str, feature: str):
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    return _permission_store.get_checker(plugin, feature)

def get_checker_generator(plugin: str, admin: int = ALLOW, group: int = ALLOW, user: int = ALLOW):
    if _permission_store is None:
        raise ValueError("Permission store is not initialized.")
    return _permission_store.get_checker_generator(plugin, admin, group, user)
