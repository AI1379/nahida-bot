#
# 权限系统测试
#

import nahida_bot.permission as permission
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
from nonebot.adapters.onebot.v11.event import Sender
import os
import asyncio


data_path = os.path.join(os.path.dirname(__file__), "data")
db_path = os.path.join(data_path, "permission_test.db")
permission.init(db_path)

# 清除测试数据
permission._clear_all_data()


def create_test_event(user_id: str, group_id: str | None = None, sender_role: str = "member"):
    """创建事件实例用于测试"""
    if group_id:
        return GroupMessageEvent(
            user_id=user_id,
            group_id=group_id,
            sender=Sender(user_id=user_id, role=sender_role),
            message="test",
            time=0,
            sub_type="group",
            message_type="group",
            post_type="message",
            self_id="000000",
            message_id="1234567890",
            raw_message="test",
            font=0,
        )
    else:
        return PrivateMessageEvent(
            user_id=user_id,
            sender=Sender(user_id=user_id),
            message="test",
            time=0,
            sub_type="friend",
            message_type="private",
            post_type="message",
            self_id="000000",
            message_id="1234567890",
            raw_message="test",
            font=0,
        )


def teardown_function():
    """在每个测试后清除数据"""
    permission._clear_all_data()


def test_superuser_basic():
    """测试superuser基础功能"""
    permission._clear_all_data()
    user_id = "112233"

    # 初始不是superuser
    event = create_test_event(user_id)
    assert permission.check_permission(event, "plugin", "feature") is True

    # 添加superuser
    permission.add_superuser(user_id)

    # 即使被ban也应该允许
    permission.ban_user(user_id, "plugin", "feature")
    assert permission.check_permission(event, "plugin", "feature") is True

    # 移除superuser后遵守ban规则
    permission.remove_superuser(user_id)
    assert permission.check_permission(event, "plugin", "feature") is False


def test_ban_user():
    """测试用户ban功能"""
    user_id = "223344"
    event = create_test_event(user_id)

    # 默认允许
    assert permission.check_permission(event, "test_plugin", "test_feature") is True

    # Ban用户的功能
    permission.ban_user(user_id, "test_plugin", "test_feature")
    assert permission.check_permission(event, "test_plugin", "test_feature") is False

    # Unban后恢复
    permission.unban_user(user_id, "test_plugin", "test_feature")
    assert permission.check_permission(event, "test_plugin", "test_feature") is True


def test_ban_group():
    """测试群ban功能"""
    user_id = "334455"
    group_id = "111222"
    event = create_test_event(user_id, group_id=group_id)

    # 默认允许
    assert permission.check_permission(event, "test_plugin", "test_feature2") is True

    # Ban群的功能
    permission.ban_group(group_id, "test_plugin", "test_feature2")
    assert permission.check_permission(event, "test_plugin", "test_feature2") is False

    # 私聊不受影响
    private_event = create_test_event(user_id)
    assert (
        permission.check_permission(private_event, "test_plugin", "test_feature2")
        is True
    )

    # Unban后恢复
    permission.unban_group(group_id, "test_plugin", "test_feature2")
    assert permission.check_permission(event, "test_plugin", "test_feature2") is True


def test_permission_priority():
    """测试权限优先级：superuser > user ban > group ban"""
    user_id = "445566"
    group_id = "222333"
    event = create_test_event(user_id, group_id=group_id)

    # Ban群的功能
    permission.ban_group(group_id, "plugin", "feature")
    assert permission.check_permission(event, "plugin", "feature") is False

    # Ban用户的功能
    permission.ban_user(user_id, "plugin", "feature")
    assert permission.check_permission(event, "plugin", "feature") is False

    # 添加superuser后全部允许
    permission.add_superuser(user_id)
    assert permission.check_permission(event, "plugin", "feature") is True

    permission.remove_superuser(user_id)
    permission.unban_group(group_id, "plugin", "feature")
    permission.unban_user(user_id, "plugin", "feature")


def test_get_checker():
    """测试权限检查器函数"""
    permission._clear_all_data()
    user_id = "556677"
    event = create_test_event(user_id)

    checker = permission.get_checker("test_plugin", "test_feature3")

    # 初始允许
    assert asyncio.run(checker(event)) is True

    # Ban后拒绝
    permission.ban_user(user_id, "test_plugin", "test_feature3")
    assert asyncio.run(checker(event)) is False

    permission.unban_user(user_id, "test_plugin", "test_feature3")
    assert asyncio.run(checker(event)) is True


def test_get_all_bans():
    """测试获取所有ban列表"""
    user_id1 = "667788"
    user_id2 = "778899"
    group_id = "333444"

    permission.ban_user(user_id1, "p1", "f1")
    permission.ban_user(user_id2, "p2", "f2")
    permission.ban_group(group_id, "p3", "f3")
    permission.add_superuser(user_id1)

    bans = permission.get_all_bans()

    assert len(bans["user_bans"]) >= 2
    assert len(bans["group_bans"]) >= 1
    assert len(bans["superusers"]) >= 1

    # 清理
    permission.unban_user(user_id1, "p1", "f1")
    permission.unban_user(user_id2, "p2", "f2")
    permission.unban_group(group_id, "p3", "f3")
    permission.remove_superuser(user_id1)
