#
# Created by Copilot on 03/01/2026
#

import nonebot
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg, EventParam, EventMessage
from nonebot.rule import to_me
from nonebot.log import logger
from nonebot.exception import FinishedException
from nahida_bot.scheduler import scheduler
from nahida_bot.utils.plugin_registry import plugin_registry
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict
import json
import os

# Register the plugin
reminder_plugin = plugin_registry.register_plugin(
    name="催更插件", description="定时催更插件，支持设置提醒消息和定时发送"
)

# Register features
plugin_registry.add_feature(
    plugin_name="催更插件",
    feature_name="设置催更",
    description="设置定时催更提醒",
    commands=["reminder", "催更"],
)

# Data storage path
REMINDER_DATA_DIR = "data/reminders"
os.makedirs(REMINDER_DATA_DIR, exist_ok=True)
REMINDER_FILE = os.path.join(REMINDER_DATA_DIR, "reminders.json")

# Timezone configuration (UTC+8 for Asia/Shanghai)
SCHEDULER_TIMEZONE = timezone(timedelta(hours=8))


def load_reminders() -> Dict:
    """Load reminders from file"""
    if os.path.exists(REMINDER_FILE):
        try:
            with open(REMINDER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load reminders: {e}")
            return {}
    return {}


def save_reminders(reminders: Dict) -> None:
    """Save reminders to file"""
    try:
        with open(REMINDER_FILE, "w", encoding="utf-8") as f:
            json.dump(reminders, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save reminders: {e}")


def parse_reminder_command(args: Message) -> Optional[Dict]:
    """
    Parse reminder command from message arguments.

    Args:
        args: Message from CommandArg() - parameters without command prefix
              Format: <hours> [@mentions] <message>
              Each segment can be text or at type

    Returns: {
        'hours': int,
        'message': str (plain text message with @qq at the places),
        'mentions': List[str] (user IDs),
    }
    """
    mentions = []
    text_parts = []

    # Process each segment in the message
    for segment in args:
        logger.debug(f"Processing segment: type={segment.type}, data={segment.data}")

        if segment.type == "at":
            qq_id = segment.data.get("qq")
            if qq_id:
                mentions.append(qq_id)
                text_parts.append(f"@{qq_id}")
        elif segment.type == "text":
            text = segment.data.get("text", "")
            if text:
                text_parts.append(text)

    # Join all text parts
    full_text = " ".join(text_parts).strip()
    tokens = full_text.split()

    logger.debug(f"Parsed tokens: {tokens}, mentions: {mentions}")

    # First token should be hours
    if len(tokens) < 1:
        return None

    try:
        hours = float(tokens[0])
    except ValueError:
        return None

    if hours <= 0:
        return None

    # Rest is the message content (skip tokens[0] which is hour count)
    # Extract message text by removing the first token (hours)
    message_text = " ".join(tokens[1:]).strip()

    if not message_text:
        return None

    return {
        "hours": hours,
        "message": message_text,  # Store as plain text string
        "mentions": mentions,
    }


def generate_job_id(group_id: int, job_index: int) -> str:
    """Generate unique job ID"""
    return f"reminder_{group_id}_{job_index}"


def schedule_reminder(
    group_id: int, hours: float, message: str, mentions: List[str], user_id: int
) -> str:
    """
    Schedule a reminder job.

    Returns: job_id
    """
    reminders = load_reminders()

    # Create group entry if needed
    group_key = str(group_id)
    if group_key not in reminders:
        reminders[group_key] = []

    # Generate job index
    job_index = len(reminders[group_key])
    job_id = generate_job_id(group_id, job_index)

    # Calculate trigger time with timezone awareness
    trigger_time = datetime.now(SCHEDULER_TIMEZONE) + timedelta(hours=hours)

    # Store reminder record
    reminder_record = {
        "job_id": job_id,
        "user_id": user_id,
        "hours": hours,
        "message": message,  # Store as plain text string
        "mentions": mentions,
        "created_at": datetime.now(SCHEDULER_TIMEZONE).isoformat(),
        "trigger_at": trigger_time.isoformat(),
        "triggered_count": 0,
    }

    reminders[group_key].append(reminder_record)
    save_reminders(reminders)

    # Schedule the job to run at trigger_time, then repeat every 'hours' hours
    @scheduler.scheduled_job(
        "interval",
        hours=hours,
        start_date=trigger_time,
        id=job_id,
        timezone="Asia/Shanghai",
    )
    async def send_reminder():
        try:
            bot = nonebot.get_bot()

            # Reconstruct message with MessageSegments for @mentions
            reminders = load_reminders()
            reminder_data = None
            for reminder in reminders.get(str(group_id), []):
                if reminder["job_id"] == job_id:
                    reminder_data = reminder
                    break

            if not reminder_data:
                logger.error(f"Reminder data not found for {job_id}")
                return

            # Build message with @mentions
            msg_to_send = Message()
            msg_text = reminder_data["message"]
            mention_list = reminder_data["mentions"]

            # Parse message text and reconstruct with MessageSegment.at()
            # Replace @qq with actual MessageSegment.at() for each mention
            remaining_text = msg_text
            for qq_id in mention_list:
                at_pattern = f"@{qq_id}"
                if at_pattern in remaining_text:
                    # Split at the @qq position
                    parts = remaining_text.split(at_pattern, 1)
                    if parts[0]:
                        msg_to_send.append(MessageSegment.text(parts[0]))
                    msg_to_send.append(MessageSegment.at(int(qq_id)))
                    remaining_text = parts[1] if len(parts) > 1 else ""

            # Append remaining text
            if remaining_text:
                msg_to_send.append(MessageSegment.text(remaining_text))

            # If no mentions, just add the text
            if not mention_list and msg_text:
                msg_to_send = Message(MessageSegment.text(msg_text))

            await bot.send_group_msg(group_id=group_id, message=msg_to_send)

            # Mark as triggered
            for reminder in reminders.get(str(group_id), []):
                if reminder["job_id"] == job_id:
                    reminder["triggered_count"] = reminder.get("triggered_count", 0) + 1
                    save_reminders(reminders)
                    break

            logger.info(f"Reminder {job_id} sent successfully")
        except Exception as e:
            logger.error(f"Failed to send reminder {job_id}: {e}")

    logger.info(f"Scheduled reminder {job_id} for group {group_id} in {hours} hours")
    return job_id


# Command handlers
reminder_cmd = on_command(
    "催更", aliases={"reminder"}, rule=to_me(), priority=5, block=True
)

reminder_list_cmd = on_command(
    "催更列表", aliases={"提醒列表", "rmd_ls"}, rule=to_me(), priority=5, block=True
)

reminder_cancel_cmd = on_command(
    "取消催更", aliases={"消除提醒", "rmd_cancel"}, rule=to_me(), priority=5, block=True
)


@reminder_cmd.handle()
async def handle_reminder_command(
    args: Message = CommandArg(),
    event: GroupMessageEvent = EventParam(),
):
    """Handle reminder command"""

    # Parse the command arguments
    # args: parameters without command prefix, e.g., "24 @user1 message"
    parsed = parse_reminder_command(args)

    if not parsed:
        await reminder_cmd.finish(
            "使用方法：@我 催更 <小时数> <消息>\n"
            "例如：@我 催更 24 记得更新啊各位\n"
            "或者：@我 催更 12 @用户1 @用户2 快点更新"
        )

    hours = parsed["hours"]
    message = parsed["message"]
    mentions = parsed["mentions"]

    if hours > 720:  # More than 30 days
        await reminder_cmd.finish("时间间隔过长，最多支持30天（720小时）")

    try:
        job_id = schedule_reminder(
            group_id=event.group_id,
            hours=hours,
            message=message,
            mentions=mentions,
            user_id=event.user_id,
        )

        await reminder_cmd.finish(
            f"✅ 已设置催更提醒！\n"
            f"⏰ {hours}小时后发送\n"
            f"📝 内容：{message}\n"
            f"🆔 提醒ID：{job_id}"
        )
    except FinishedException:
        pass  # Command already finished, do nothing
    except Exception as e:
        logger.error(f"Failed to schedule reminder: {e}")
        await reminder_cmd.finish(f"❌ 设置提醒失败：{str(e)}")


@reminder_list_cmd.handle()
async def handle_list_reminders(event: GroupMessageEvent = EventParam()):
    """List all pending reminders for the group"""
    reminders = load_reminders()
    group_key = str(event.group_id)

    if group_key not in reminders or not reminders[group_key]:
        await reminder_list_cmd.finish("此群暂无待发送的催更提醒")

    group_reminders = reminders[group_key]
    msg_list = ["📋 这个群的催更提醒列表：\n"]

    pending_count = 0
    for i, reminder in enumerate(group_reminders):
        # if reminder.get("triggered_count", 0) > 0:
        #     continue

        pending_count += 1
        trigger_time = datetime.fromisoformat(reminder["trigger_at"])
        # Handle timezone-aware datetimes for time remaining calculation
        if trigger_time.tzinfo is None:
            # If no timezone info, assume it's in scheduler timezone
            trigger_time = trigger_time.replace(tzinfo=SCHEDULER_TIMEZONE)
        current_time = datetime.now(SCHEDULER_TIMEZONE)
        time_left = trigger_time - current_time

        msg_list.append(
            f"[{reminder['job_id']}]\n"
            f"  ⏰ 剩余时间：{time_left.total_seconds() / 3600:.1f}小时\n"
            f"  📝 内容：{reminder['message'][:50]}{'...' if len(reminder['message']) > 50 else ''}\n"
        )

    if pending_count == 0:
        await reminder_list_cmd.finish("此群暂无待发送的催更提醒")

    msg_list.append(f"\n共 {pending_count} 条待发送的提醒")
    await reminder_list_cmd.finish("".join(msg_list))


@reminder_cancel_cmd.handle()
async def handle_cancel_reminder(
    args: Message = CommandArg(), event: GroupMessageEvent = EventParam()
):
    """Cancel a reminder by job_id"""
    job_id = args.extract_plain_text().strip()

    if not job_id:
        await reminder_cancel_cmd.finish(
            "请提供要取消的提醒ID\n用法：@我 取消催更 <提醒ID>"
        )

    reminders = load_reminders()
    group_key = str(event.group_id)

    if group_key not in reminders:
        await reminder_cancel_cmd.finish("此群没有任何提醒记录")

    # Find and remove the reminder
    found = False
    for reminder in reminders[group_key]:
        if reminder["job_id"] == job_id:
            reminders[group_key].remove(reminder)
            found = True

            # Cancel the scheduled job
            try:
                scheduler.remove_job(job_id)
                logger.info(f"Cancelled reminder job: {job_id}")
            except Exception as e:
                logger.warning(f"Job {job_id} might already be cancelled: {e}")

            save_reminders(reminders)
            break

    if found:
        await reminder_cancel_cmd.finish(f"✅ 已取消提醒 {job_id}")
    else:
        await reminder_cancel_cmd.finish(f"❌ 找不到提醒ID {job_id}")
