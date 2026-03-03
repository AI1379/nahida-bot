#
# 权限系统
#
# 基于黑名单(ban)模型：所有功能默认允许，除非被ban。
# Superuser可以通过私聊控制其他用户和群的权限。
# 使用SQLite3v2+Pydantic实现

from pathlib import Path
from typing import Optional, Dict, Any
from pydantic import BaseModel
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
from nonebot.adapters import Event
from nonebot.log import logger
from nahida_bot.localstore.sqlite3_v2 import SQLite3DBv2


# ============ Pydantic 模型 ============

class BanUser(BaseModel):
    """用户黑名单"""
    id: Optional[int] = None
    user_id: str
    plugin: str
    feature: str


class BanGroup(BaseModel):
    """群黑名单"""
    id: Optional[int] = None
    group_id: str
    plugin: str
    feature: str


class SuperUser(BaseModel):
    """超级用户"""
    id: Optional[int] = None
    user_id: str


# ============ 权限存储类 ============

class PermissionStore:
    """管理黑名单式的权限系统，使用SQLite3v2存储"""
    
    def __init__(self, db_path: Path | str):
        """初始化权限存储
        
        Args:
            db_path: 数据库文件路径
        """
        if isinstance(db_path, str):
            db_path = Path(db_path)
        
        # 初始化SQLite3v2表
        self._db_path = db_path
        self.ban_user_db = SQLite3DBv2(BanUser, db_path, table_name="ban_user")
        self.ban_group_db = SQLite3DBv2(BanGroup, db_path, table_name="ban_group")
        self.superuser_db = SQLite3DBv2(SuperUser, db_path, table_name="superuser")
        
        logger.info(f"权限存储初始化完成: {db_path}")

    def add_superuser(self, user_id: str) -> bool:
        """添加superuser，重复添加时不会报错"""
        try:
            user_id_str = str(user_id)
            # 检查是否已存在
            existing = self.superuser_db.get_where(user_id=user_id_str)
            if existing:
                return False
            
            self.superuser_db.insert(SuperUser(user_id=user_id_str))
            logger.info(f"添加superuser: {user_id}")
            return True
        except Exception as e:
            logger.error(f"添加superuser失败: {e}")
            raise RuntimeError("Failed to add superuser")

    def remove_superuser(self, user_id: str) -> bool:
        """移除superuser，不存在时不会报错"""
        try:
            user_id_str = str(user_id)
            deleted = self.superuser_db.delete_where(user_id=user_id_str)
            logger.info(f"移除superuser: {user_id}")
            return deleted > 0
        except Exception as e:
            logger.error(f"移除superuser失败: {e}")
            raise RuntimeError("Failed to remove superuser")

    def _is_superuser(self, user_id: str) -> bool:
        """检查用户是否是superuser"""
        try:
            user_id_str = str(user_id)
            return self.superuser_db.get_where(user_id=user_id_str) is not None
        except Exception as e:
            logger.error(f"检查superuser失败: {e}")
            return False

    def ban_user(self, user_id: str, plugin: str, feature: str) -> bool:
        """Ban用户的功能，重复ban时不会报错"""
        try:
            user_id_str = str(user_id)
            # 检查是否已存在
            existing = self.ban_user_db.get_where(
                user_id=user_id_str,
                plugin=plugin,
                feature=feature
            )
            if existing:
                return False
            
            self.ban_user_db.insert(BanUser(
                user_id=user_id_str,
                plugin=plugin,
                feature=feature
            ))
            logger.info(f"Ban用户 {user_id} 的功能 {plugin}.{feature}")
            return True
        except Exception as e:
            logger.error(f"Ban用户失败: {e}")
            raise RuntimeError("Failed to ban user")

    def _is_user_banned(self, user_id: str, plugin: str, feature: str) -> bool:
        """检查用户是否被ban了某个功能"""
        try:
            user_id_str = str(user_id)
            return self.ban_user_db.get_where(
                user_id=user_id_str,
                plugin=plugin,
                feature=feature
            ) is not None
        except Exception as e:
            logger.error(f"检查用户ban状态失败: {e}")
            return False

    def unban_user(self, user_id: str, plugin: str, feature: str) -> bool:
        """Unban用户的功能"""
        try:
            user_id_str = str(user_id)
            deleted = self.ban_user_db.delete_where(
                user_id=user_id_str,
                plugin=plugin,
                feature=feature
            )
            logger.info(f"Unban用户 {user_id} 的功能 {plugin}.{feature}")
            return deleted > 0
        except Exception as e:
            logger.error(f"Unban用户失败: {e}")
            raise RuntimeError("Failed to unban user")

    def ban_group(self, group_id: str, plugin: str, feature: str) -> bool:
        """Ban群的功能，重复ban时不会报错"""
        try:
            group_id_str = str(group_id)
            # 检查是否已存在
            existing = self.ban_group_db.get_where(
                group_id=group_id_str,
                plugin=plugin,
                feature=feature
            )
            if existing:
                return False
            
            self.ban_group_db.insert(BanGroup(
                group_id=group_id_str,
                plugin=plugin,
                feature=feature
            ))
            logger.info(f"Ban群 {group_id} 的功能 {plugin}.{feature}")
            return True
        except Exception as e:
            logger.error(f"Ban群失败: {e}")
            raise RuntimeError("Failed to ban group")

    def _is_group_banned(self, group_id: str, plugin: str, feature: str) -> bool:
        """检查群是否被ban了某个功能"""
        try:
            group_id_str = str(group_id)
            return self.ban_group_db.get_where(
                group_id=group_id_str,
                plugin=plugin,
                feature=feature
            ) is not None
        except Exception as e:
            logger.error(f"检查群ban状态失败: {e}")
            return False

    def unban_group(self, group_id: str, plugin: str, feature: str) -> bool:
        """Unban群的功能"""
        try:
            group_id_str = str(group_id)
            deleted = self.ban_group_db.delete_where(
                group_id=group_id_str,
                plugin=plugin,
                feature=feature
            )
            logger.info(f"Unban群 {group_id} 的功能 {plugin}.{feature}")
            return deleted > 0
        except Exception as e:
            logger.error(f"Unban群失败: {e}")
            raise RuntimeError("Failed to unban group")

    def check_permission(self, event: Event, plugin: str, feature: str) -> bool:
        """检查是否有权限，基于黑名单模型
        
        返回True表示允许，False表示拒绝
        
        权限检查顺序：
        1. Superuser全部允许
        2. 用户被ban → 拒绝
        3. 群消息且群被ban → 拒绝
        4. 否则允许
        """
        if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
            return True  # 非消息事件默认允许

        user_id = str(event.sender.user_id)

        # 1. Superuser全部允许
        if self._is_superuser(user_id):
            return True

        # 2. 检查用户黑名单
        if self._is_user_banned(user_id, plugin, feature):
            return False

        # 3. 群消息检查群黑名单
        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
            if self._is_group_banned(group_id, plugin, feature):
                return False

        # 否则允许
        return True

    def get_all_bans(self) -> Dict[str, Any]:
        """获取所有ban记录"""
        try:
            user_bans = self.ban_user_db.all()
            group_bans = self.ban_group_db.all()
            superusers = self.superuser_db.all()
            
            return {
                "user_bans": user_bans,
                "group_bans": group_bans,
                "superusers": superusers,
            }
        except Exception as e:
            logger.error(f"获取ban记录失败: {e}")
            return {"user_bans": [], "group_bans": [], "superusers": []}

    def clear(self):
        """清除所有权限数据，仅用于测试"""
        try:
            self.ban_user_db.clear()
            self.ban_group_db.clear()
            self.superuser_db.clear()
            logger.info("已清除所有权限数据")
        except Exception as e:
            logger.error(f"清除数据失败: {e}")

    def close(self):
        """关闭数据库连接"""
        try:
            self.ban_user_db.close()
            self.ban_group_db.close()
            self.superuser_db.close()
        except Exception as e:
            logger.error(f"关闭权限存储失败: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ============ 全局实例和接口函数 ============

_permission_store: Optional[PermissionStore] = None


def init(db_path: Path | str = "data/permission.db"):
    """初始化权限系统
    
    Args:
        db_path: SQLite数据库文件路径
    """
    global _permission_store
    if isinstance(db_path, str):
        db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _permission_store = PermissionStore(db_path)


def add_superuser(user_id: str) -> bool:
    """添加superuser"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    return _permission_store.add_superuser(user_id)


def remove_superuser(user_id: str) -> bool:
    """移除superuser"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    return _permission_store.remove_superuser(user_id)


def ban_user(user_id: str, plugin: str, feature: str) -> bool:
    """Ban用户的功能"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    return _permission_store.ban_user(user_id, plugin, feature)


def unban_user(user_id: str, plugin: str, feature: str) -> bool:
    """Unban用户的功能"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    return _permission_store.unban_user(user_id, plugin, feature)


def ban_group(group_id: str, plugin: str, feature: str) -> bool:
    """Ban群的功能"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    return _permission_store.ban_group(group_id, plugin, feature)


def unban_group(group_id: str, plugin: str, feature: str) -> bool:
    """Unban群的功能"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    return _permission_store.unban_group(group_id, plugin, feature)


def check_permission(event: Event, plugin: str, feature: str) -> bool:
    """检查权限"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    return _permission_store.check_permission(event, plugin, feature)


def get_checker(plugin: str, feature: str):
    """获取权限检查器，用于nonebot rule"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    
    store = _permission_store
    async def checker(event: Event):
        return store.check_permission(event, plugin, feature)
    
    return checker


def get_all_bans() -> Dict[str, Any]:
    """获取所有ban记录"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    return _permission_store.get_all_bans()


def _clear_all_data():
    """清除所有ban数据，仅用于测试"""
    if _permission_store is None:
        raise RuntimeError("Permission store not initialized. Call init() first.")
    _permission_store.clear()
