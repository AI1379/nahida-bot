#
# Test for reminder plugin
#

import pytest
import json
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock
from nahida_bot.plugins.reminder import (
    parse_reminder_command,
    load_reminders,
    save_reminders,
    generate_job_id,
)

# Setup test data directory
TEST_DATA_DIR = "data/reminders"


@pytest.fixture(autouse=True)
def cleanup():
    """Cleanup before and after tests"""
    yield
    # Cleanup after test
    if os.path.exists("data/reminders/reminders.json"):
        os.remove("data/reminders/reminders.json")


def test_parse_reminder_command_basic():
    """Test basic reminder command parsing"""
    from nonebot.adapters.onebot.v11 import Message
    
    # Create a mock message
    msg = Message()
    # Add text segments: "催更 24 快点更新"
    msg.append({
        "type": "text",
        "data": {"text": "催更 24 快点更新"}
    })
    
    result = parse_reminder_command(msg)
    
    assert result is not None
    assert result["hours"] == 24
    assert result["message"] == "快点更新"
    assert result["mentions"] == []


def test_parse_reminder_command_with_mentions():
    """Test reminder command parsing with @mentions"""
    from nonebot.adapters.onebot.v11 import Message
    
    msg = Message()
    msg.append({
        "type": "text",
        "data": {"text": "催更 12"}
    })
    msg.append({
        "type": "at",
        "data": {"qq": "123456"}
    })
    msg.append({
        "type": "text",
        "data": {"text": "快点更新"}
    })
    
    result = parse_reminder_command(msg)
    
    assert result is not None
    assert result["hours"] == 12
    assert "123456" in result["mentions"]
    assert "快点更新" in result["message"]


def test_parse_reminder_command_invalid():
    """Test parsing invalid reminder commands"""
    from nonebot.adapters.onebot.v11 import Message
    
    # No hour specified
    msg = Message()
    msg.append({
        "type": "text",
        "data": {"text": "催更 无效"}
    })
    
    result = parse_reminder_command(msg)
    assert result is None


def test_load_save_reminders():
    """Test loading and saving reminders"""
    data = {
        "123": [
            {
                "job_id": "reminder_123_0",
                "user_id": 456,
                "hours": 24,
                "message": "test",
                "mentions": [],
                "created_at": datetime.now().isoformat(),
                "trigger_at": (datetime.now() + timedelta(hours=24)).isoformat(),
                "triggered": False
            }
        ]
    }
    
    # Save
    save_reminders(data)
    assert os.path.exists("data/reminders/reminders.json")
    
    # Load
    loaded = load_reminders()
    assert loaded == data


def test_generate_job_id():
    """Test job ID generation"""
    job_id = generate_job_id(123, 0)
    assert job_id == "reminder_123_0"
    
    job_id = generate_job_id(456, 5)
    assert job_id == "reminder_456_5"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
