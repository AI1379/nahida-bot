#
# Created by Renatus Madrigal on 2025-03-07
# Last modified: 2025-03-07 改为连续活跃/冷却机制
#

import random
import asyncio
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Deque, Optional

import nonebot
from nonebot import on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg, EventParam
from nonebot.rule import to_me
from openai import AsyncOpenAI

from nahida_bot.scheduler import scheduler
from nahida_bot.localstore import get_json
from nahida_bot.utils.plugin_registry import plugin_registry

# ---------- 插件注册 ----------
plugin = plugin_registry.register_plugin(
    name="随机发言",
    description="定时分析聊天记录，主动生成回复"
)
plugin_registry.add_feature(
    plugin_name="随机发言",
    feature_name="发言控制",
    description="启用/禁用随机发言",
    commands=["/randspeaker"]
)

# ---------- 配置 ----------
driver = nonebot.get_driver()
config = driver.config

# OpenAI 配置
OPENAI_URL = getattr(config, "openai_api_url", None)
OPENAI_TOKEN = getattr(config, "openai_api_token", None)
OPENAI_MODEL = getattr(config, "openai_model_name", "deepseek-ai/DeepSeek-V3.2-Exp")
if not OPENAI_URL or not OPENAI_TOKEN:
    logger.warning("随机发言插件：缺少 OpenAI 配置，将无法生成回复")

# 随机间隔范围（秒）—— 正常值：30分钟 ~ 2小时
MIN_INTERVAL = 30 * 60      # 30分钟
MAX_INTERVAL = 2 * 60 * 60  # 2小时

# 消息缓存大小
MAX_HISTORY_PER_GROUP = 100

# 空缓存时的等待时间（秒）
EMPTY_CACHE_WAIT = 5 * 60   # 5分钟

# 活跃群间隔奖励参数（用于缩短任务间隔，基于5分钟内消息数）
ACTIVE_WINDOW = 5 * 60          # 5分钟时间窗口
ACTIVE_MSG_COUNT = 10           # 5分钟内消息数超过10视为活跃
ACTIVE_INTERVAL_BONUS = 0.5     # 间隔缩短为50%
MIN_INTERVAL_ACTIVE = 30        # 活跃群最短间隔（秒），避免过于频繁

# 连续变量参数
BASE_PROB = 0.3                 # 基础发言概率
ACTIVE_INCREMENT = 0.05         # 每条消息活跃加成
COOLDOWN_DECREMENT = 0.02       # 每条消息冷却减少
ACTIVE_DECREMENT = 0.5          # 发言后活跃减少
COOLDOWN_INCREMENT = 0.5        # 发言后冷却增加
MAX_COOLDOWN = 0.5              # 冷却上限

# ---------- 状态存储 ----------
state_file = get_json("random_speaker", "state")  # 使用 JSONHandler
enabled_groups: Dict[str, bool] = {}  # 群ID -> 是否启用，默认所有群启用

# 消息缓存：group_id -> deque of (user_id, message, time)
message_cache: Dict[str, Deque] = {}

# 上次发言时间记录（用于无人互动判定）
last_speak_time: Dict[str, datetime] = {}

# 发言后是否有新消息（来自他人）
has_new_message_after_speak: Dict[str, bool] = {}

# 连续变量
active_bonus: Dict[str, float] = {}      # 活跃加成
cooldown_penalty: Dict[str, float] = {}  # 冷却惩罚

# ---------- 辅助函数 ----------
def load_state():
    """从 JSON 文件加载状态"""
    global enabled_groups
    data = state_file.get("enabled_groups", {})
    enabled_groups = {str(k): bool(v) for k, v in data.items()}
    logger.info(f"已加载状态，enabled_groups: {enabled_groups}")

def save_state():
    """保存状态到 JSON 文件"""
    state_file["enabled_groups"] = enabled_groups
    logger.info(f"状态已保存，enabled_groups: {enabled_groups}")

# 初始化加载
load_state()

def is_group_enabled(group_id: str) -> bool:
    """检查群是否启用了随机发言"""
    return enabled_groups.get(str(group_id), True)  # 默认启用

def add_message_to_cache(event: GroupMessageEvent):
    """将群消息加入缓存，并更新连续变量"""
    group_id = str(event.group_id)
    if group_id not in message_cache:
        message_cache[group_id] = deque(maxlen=MAX_HISTORY_PER_GROUP)

    # 获取发送者昵称
    sender_name = event.sender.card or event.sender.nickname or f"用户{event.user_id}"
    message_cache[group_id].append({
        "user_id": str(event.user_id),
        "name": sender_name,
        "message": event.get_plaintext(),
        "time": event.time
    })
    logger.debug(f"已缓存群 {group_id} 的消息，当前缓存数: {len(message_cache[group_id])}")

    # 如果不是机器人自己发的消息，更新连续变量和互动标记
    bot_self_id = str(nonebot.get_bot().self_id)
    if str(event.user_id) != bot_self_id:
        has_new_message_after_speak[group_id] = True

        # 更新连续变量
        active_bonus[group_id] = active_bonus.get(group_id, 0) + ACTIVE_INCREMENT
        cooldown_penalty[group_id] = max(0, cooldown_penalty.get(group_id, 0) - COOLDOWN_DECREMENT)
        logger.debug(f"群 {group_id} 活跃加成 {active_bonus[group_id]:.2f}，冷却惩罚 {cooldown_penalty[group_id]:.2f}")

        # 每条消息触发动态缩短
        asyncio.create_task(try_shorten_interval())

def get_recent_messages(group_id: str, limit: int = 20) -> str:
    """获取最近的消息文本，格式为：昵称: 消息"""
    if group_id not in message_cache:
        return ""
    recent = list(message_cache[group_id])[-limit:]
    lines = [f"{msg['name']}: {msg['message']}" for msg in recent]
    return "\n".join(lines)

def is_group_active(group_id: str) -> bool:
    """判断群是否活跃（最近5分钟内其他成员消息数超过阈值），用于间隔奖励"""
    if group_id not in message_cache:
        return False
    now = time.time()
    window_start = now - ACTIVE_WINDOW
    bot_self_id = str(nonebot.get_bot().self_id)
    count = sum(1 for msg in message_cache[group_id] 
                if msg['time'] >= window_start and msg['user_id'] != bot_self_id)
    return count >= ACTIVE_MSG_COUNT

async def try_shorten_interval():
    """尝试将下次全局任务执行时间提前一半（动态缩短），无限流"""
    job = scheduler.get_job("random_speak_job")
    if not job:
        return
    next_run = job.next_run_time
    if not next_run:
        return

    now_aware = datetime.now(next_run.tzinfo)
    if next_run <= now_aware:
        return
    remaining = (next_run - now_aware).total_seconds()
    if remaining > 30:
        new_remaining = remaining * 0.5
        new_run_time = now_aware + timedelta(seconds=new_remaining)
        scheduler.reschedule_job("random_speak_job", trigger="date", run_date=new_run_time)
        logger.info(f"检测到新消息，下次任务提前至 {new_run_time.strftime('%Y-%m-%d %H:%M:%S')}")

# ---------- 消息监听 ----------
message_listener = on_message(priority=100, block=False)

@message_listener.handle()
async def cache_message_listener(event: GroupMessageEvent):
    """缓存群消息"""
    add_message_to_cache(event)

# ---------- 开关命令 ----------
randspeaker_cmd = on_command("randspeaker", priority=5, block=True)

@randspeaker_cmd.handle()
async def handle_randspeaker(
    event: MessageEvent,
    args: Message = CommandArg()
):
    """启用/禁用随机发言"""
    arg = args.extract_plain_text().strip().lower()
    if arg not in ("on", "off"):
        await randspeaker_cmd.finish("用法：/randspeaker on 或 /randspeaker off")

    if not isinstance(event, GroupMessageEvent):
        await randspeaker_cmd.finish("该命令只能在群聊中使用")
    group_id = str(event.group_id)

    enabled = arg == "on"
    enabled_groups[group_id] = enabled
    save_state()
    logger.info(f"群 {group_id} 随机发言已{'启用' if enabled else '禁用'}")
    await randspeaker_cmd.finish(f"随机发言已{'启用' if enabled else '禁用'}")

# ---------- 定时任务 ----------
async def random_speak_job():
    """定时任务：分析所有启用的群，对每个群独立进行发言判定"""
    logger.debug("随机发言任务触发")
    logger.info(f"当前 enabled_groups: {enabled_groups}")

    target_groups = [gid for gid, enabled in enabled_groups.items() if enabled]
    logger.info(f"候选群列表: {target_groups}")

    if not target_groups:
        logger.info("没有启用随机发言的群，重新调度")
        await reschedule(active_any=False)
        return

    any_active = False  # 标记是否有任一群活跃（用于间隔奖励）

    for group_id in target_groups:
        logger.info(f"检查群 {group_id}")

        recent = get_recent_messages(group_id, limit=20)
        if not recent:
            logger.info(f"群 {group_id} 暂无消息缓存，跳过本次检查")
            continue

        # 判断群是否活跃（用于间隔奖励）
        is_active = is_group_active(group_id)
        if is_active:
            any_active = True
            logger.info(f"群 {group_id} 处于活跃状态（间隔奖励）")

        # ---------- 硬性惩罚 ----------
        # 消息过少惩罚
        msg_count = len(message_cache.get(group_id, []))
        if msg_count < 5:
            logger.info(f"群 {group_id} 消息数量过少（{msg_count}条），跳过")
            continue

        # 无人互动惩罚
        if not has_new_message_after_speak.get(group_id, True):
            logger.info(f"群 {group_id} 上次发言后无新消息，跳过")
            continue

        # ---------- 计算连续变量 ----------
        ab = active_bonus.get(group_id, 0)
        cp = cooldown_penalty.get(group_id, 0)
        prob = max(0, min(1, BASE_PROB + ab - cp))
        logger.info(f"群 {group_id} 活跃加成 {ab:.2f}，冷却惩罚 {cp:.2f}，计算概率 {prob:.2f}")

        # 随机决定
        if random.random() > prob:
            logger.info(f"群 {group_id} 随机未通过（概率 {prob:.2f}），不发言")
            continue

        logger.info(f"决定在群 {group_id} 发言")

        # ---------- 生成回复 ----------
        if not OPENAI_URL or not OPENAI_TOKEN:
            logger.error("OpenAI 未配置，无法生成回复")
            continue

        try:
            client = AsyncOpenAI(api_key=OPENAI_TOKEN, base_url=OPENAI_URL)
            system_prompt = (
                "你是一个活跃在群聊中的成员，请根据最近的聊天记录，生成一条合适的回复。"
                "回复要自然、简短，符合群聊氛围，不要使用markdown。"
            )
            user_prompt = f"最近的聊天记录：\n{recent}\n\n请生成一条回复："

            response = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=100,
                temperature=0.9
            )
            reply = response.choices[0].message.content.strip()
            if not reply:
                logger.warning("生成的回复为空")
                continue

            bot = nonebot.get_bot()
            await bot.send_group_msg(group_id=int(group_id), message=reply)
            logger.info(f"已向群 {group_id} 发送回复：{reply[:30]}...")

            # 发言后更新连续变量和标记
            last_speak_time[group_id] = datetime.now()
            has_new_message_after_speak[group_id] = False

            # 更新连续变量：活跃减0.5，冷却加0.5（不超过上限）
            active_bonus[group_id] = max(0, ab - ACTIVE_DECREMENT)
            cooldown_penalty[group_id] = min(MAX_COOLDOWN, cp + COOLDOWN_INCREMENT)
            logger.info(f"群 {group_id} 发言后：活跃 {active_bonus[group_id]:.2f}，冷却 {cooldown_penalty[group_id]:.2f}")

        except Exception as e:
            logger.error(f"生成或发送回复失败：{e}")

    # ---------- 重新调度 ----------
    await reschedule(active_any=any_active)

async def reschedule(active_any: bool = False):
    """计算随机间隔并重新添加任务，如果任一群活跃则应用间隔奖励"""
    interval = random.randint(MIN_INTERVAL, MAX_INTERVAL)
    if active_any:
        interval = int(interval * ACTIVE_INTERVAL_BONUS)
        if interval < MIN_INTERVAL_ACTIVE:
间隔 = 最小活动间隔
        logger.info(f"存在活跃群，下次间隔缩短为 {interval} 秒")
运行时间 = datetime.现在()+timedelta秒数=间隔
    logger.info(f"下次随机发言任务将在 {run_time.strftime('%Y-%m-%d %H:%M:%S')} 执行（间隔 {interval//60} 分钟）")
调度器.添加任务(
随机发言任务,
        "日期",
运行日期=运行时间,
任务ID="随机发言任务",
替换现有任务=True
    )

# ---------- 插件加载时启动任务 ----------
@driver.on_startup
async def start_random_speaker():
    try:
        scheduler.remove_job("random_speak_job")
    except:
        通过
    run_time = datetime.now() + timedelta(seconds=60)
调度器.添加任务(
随机发言任务,
        "日期",
运行日期=运行时间,
        id="random_speak_job"
    )
    logger.info(f"随机发言任务已调度，首次执行时间：{run_time.strftime('%Y-%m-%d %H:%M:%S')}")

@driver.on_shutdown
async def stop_random_speaker():
    try:
        scheduler.remove_job("random_speak_job")
    except:
        通过
