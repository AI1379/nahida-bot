#
# Created by Renatus Madrigal on 04/03/2025
#

import pytest
from nahida_bot.localstore import LocalStoreManager
import nahida_bot.permission as permission
from nahida_bot.localstore.sqlite3 import SQLite3DB
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent
from nonebot.adapters.onebot.v11.event import Sender
import os
import asyncio

data_path = os.path.join(os.path.dirname(__file__), "data")

localstore = LocalStoreManager(data_path)

permission.init(localstore=localstore)

store = permission._permission_store
store.reset()
permission._create_tables("000000")


def test_permission_init():
    assert isinstance(store, SQLite3DB)
    assert store.db_path == os.path.join(data_path, "permission.db")
    assert store.connection is not None


def test_superuser():
    assert permission._check_superuser("000000") is True
    permission.set_superuser("123456")
    assert permission._check_superuser("123456") is True
    assert permission._check_superuser("654321") is False
    permission.set_superuser("654321")
    assert permission._check_superuser("654321") is True
    permission.remove_superuser("123456")
    assert permission._check_superuser("123456") is False


def test_admin():
    user_id = "113355"
    group_msg = GroupMessageEvent(
        user_id=user_id,
        group_id="654321",
        sender=Sender(user_id=user_id, role="admin"),
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
    assert permission._check_admin(group_msg) is True
    group_msg.sender.role = "member"
    assert permission._check_admin(group_msg) is False
    group_msg.sender.role = "owner"
    assert permission._check_admin(group_msg) is True

    private_msg = MessageEvent(
        user_id=user_id,
        group_id="654321",
        sender=Sender(user_id=user_id, role="admin"),
        message="test",
        time=0,
        sub_type="private",
        message_type="private",
        post_type="message",
        self_id="000000",
        message_id="1234567890",
        raw_message="test",
        font=0,
    )
    assert permission._check_admin(private_msg) is False
    permission.add_superuser(user_id)
    assert permission._check_admin(group_msg) is True


def test_update_permission():
    user_id = "114514"
    group_id = "654321"
    group_msg = GroupMessageEvent(
        user_id=user_id,
        group_id=group_id,
        sender=Sender(user_id=user_id, role="admin"),
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
    private_msg = MessageEvent(
        user_id=user_id,
        sender=Sender(user_id=user_id, role="admin"),
        message="test",
        time=0,
        sub_type="private",
        message_type="private",
        post_type="message",
        self_id="000000",
        message_id="1234567890",
        raw_message="test",
        font=0,
    )
    plugin = "test_plugin"
    feature = "test_feature_A"
    feature2 = "test_feature_B"
    permission.update_feature_permission(
        plugin,
        feature,
        admin=permission.ALLOW,
        group=permission.ALLOW,
        user=permission.ALLOW
    )
    assert permission.check_permission(
        group_msg, plugin, feature) == permission.ALLOW

    permission.update_feature_permission(
        plugin,
        feature2,
        admin=permission.ALLOW,
        group=permission.DENY,
        user=permission.ALLOW
    )
    assert permission.check_permission(
        group_msg, plugin, feature2) == permission.DENY
    group_msg.sender.role = "member"
    assert permission.check_permission(
        group_msg, plugin, feature2) == permission.DENY
    
    assert permission.check_permission(
        private_msg, plugin, feature) == permission.ALLOW
    
    assert permission.check_permission(
        private_msg, plugin, feature2) == permission.ALLOW
    
    permission.update_group_permission(
        plugin=plugin,
        feature=feature,
        group_id=group_id,
        state=permission.DENY
    )
    assert permission.check_permission(
        group_msg, plugin, feature) == permission.DENY
    
    permission.update_group_permission(
        plugin=plugin,
        feature=feature,
        group_id=group_id,
        state=permission.ALLOW
    )
    assert permission.check_permission(
        group_msg, plugin, feature) == permission.ALLOW
    assert permission.check_permission(
        private_msg, plugin, feature) == permission.ALLOW
    permission.update_user_permission(
        plugin=plugin,
        feature=feature,
        user_id=user_id,
        state=permission.DENY
    )
    assert permission.check_permission(
        group_msg, plugin, feature) == permission.DENY
    assert permission.check_permission(
        private_msg, plugin, feature) == permission.DENY
    
    permission.add_superuser(user_id)
    assert permission.check_permission(
        group_msg, plugin, feature) == permission.ALLOW
    
def test_checker():
    user_id = "123456"
    group_id = "654321"
    group_msg = GroupMessageEvent(
        user_id=user_id,
        group_id=group_id,
        sender=Sender(user_id=user_id, role="admin"),
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
    private_msg = MessageEvent(
        user_id=user_id,
        sender=Sender(user_id=user_id, role="admin"),
        message="test",
        time=0,
        sub_type="private",
        message_type="private",
        post_type="message",
        self_id="000000",
        message_id="1234567890",
        raw_message="test",
        font=0,
    )
    plugin = "test_plugin"
    feature = "test_feature_A"
    checker = permission.get_checker(plugin, feature)
    
    permission.update_feature_permission(
        plugin,
        feature,
        admin=permission.ALLOW,
        group=permission.ALLOW,
        user=permission.ALLOW
    )
    assert asyncio.run(checker(group_msg)) is True
    assert asyncio.run(checker(private_msg)) is True
