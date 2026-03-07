#
# Created by Renatus Madrigal on 2025-03-07
# Last modified: 2025-03-07 冷却惩罚改为概率减半，其他逻辑不变
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

# 活跃群奖励参数
ACTIVE_WINDOW = 5 * 60          # 5分钟时间窗口
ACTIVE_MSG_COUNT = 10           # 5分钟内消息数超过10视为活跃
ACTIVE_PROB_BONUS = 1.2         # 概率提高20%
ACTIVE_INTERVAL_BONUS = 0.5     # 间隔缩短为50%
MIN_INTERVAL_ACTIVE = 30        # 活跃群最短间隔（秒），避免过于频繁

# ---------- 状态存储 ----------
state_file = get_json("random_speaker", "state")  # 使用 JSONHandler
enabled_groups: Dict[str, bool] = {}  # 群ID -> 是否启用，默认所有群启用

# 消息缓存：group_id -> deque of (user_id, message, time)
message_cache: Dict[str, Deque] = {}

# 上次发言时间记录（用于防止刷屏）
last_speak_time: Dict[str, datetime] = {}

# 发言后是否有新消息（来自他人）
has_new_message_after_speak: Dict[str, bool] = {}

# 每个群从上次发言后累计收到的其他成员消息数（用于动态缩短间隔）
group_msg_counter: Dict[str, int] = {}

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
    """将群消息加入缓存，并处理动态缩短间隔"""
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

    # 标记发言后是否有新消息（如果不是机器人自己发的）
    bot_self_id = str(nonebot.get_bot().self_id)
    if str(event.user_id) != bot_self_id:
        has_new_message_after_speak[group_id] = True

        # 更新计数器（可用于其他用途）
        group_msg_counter[group_id] = group_msg_counter.get(group_id, 0) + 1

        # 每条消息都触发间隔缩短（不再限流）
        asyncio.create_task(try_shorten_interval())

def get_recent_messages(group_id: str, limit: int = 20) -> str:
    """获取最近的消息文本，格式为：昵称: 消息"""
    if group_id not in message_cache:
        return ""
    recent = list(message_cache[group_id])[-limit:]
    lines = [f"{msg['name']}: {msg['message']}" for msg in recent]
    return "\n".join(lines)

def is_group_active(group_id: str) -> bool:
    """判断群是否活跃（最近5分钟内其他成员消息数超过阈值）"""
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
    # 避免剩余时间过短导致频繁重调度，但已取消限流，仍保留30秒最小剩余保护
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

    # 检查是否为群消息
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

    # 获取所有启用了该功能的群
    target_groups = [gid for gid, enabled in enabled_groups.items() if enabled]
    logger.info(f"候选群列表: {target_groups}")

    if not target_groups:
        logger.info("没有启用随机发言的群，重新调度")
        await reschedule(active_any=False)
        return

    any_active = False  # 标记是否有任一群活跃

    for group_id in target_groups:
        logger.info(f"检查群 {group_id}")

        # 获取最近消息
        recent = get_recent_messages(group_id, limit=20)
        if not recent:
            logger.info(f"群 {group_id} 暂无消息缓存，跳过本次检查")
            continue

        # 判断群是否活跃
        is_active = is_group_active(group_id)
        if is_active:
            any_active = True
            logger.info(f"群 {group_id} 处于活跃状态，将应用概率奖励")

        # ---------- 发言概率评分 ----------
        # 基础概率 0.3
        base_prob = 0.3

        # 冷却惩罚：1小时内已发言则概率减半
        last_time = last_speak_time.get(group_id)
        if last_time and datetime.now() - last_time < timedelta(hours=1):
            base_prob *= 0.5
            logger.info(f"群 {group_id} 1小时内已发言，概率降低为原来的0.5")

        # 消息过少惩罚：消息缓存少于5条则直接归零
        msg_count = len(message_cache.get(group_id, []))
        if msg_count < 5:
            base_prob = 0
            logger.info(f"群 {group_id} 消息数量过少（{msg_count}条），概率归零")

        # 活跃群概率奖励（提高20%，但不超过1.0）
        if is_active:
            base_prob = min(base_prob * ACTIVE_PROB_BONUS, 1.0)
            logger.info(f"群 {group_id} 活跃，概率提高至 {base_prob:.2f}")

        # 无人互动惩罚：上次发言后无新消息则直接归零
        if not has_new_message_after_speak.get(group_id, True):
            base_prob = 0
            logger.info(f"群 {group_id} 上次发言后无新消息，概率归零")

        logger.info(f"群 {group_id} 最终发言概率: {base_prob:.2f}")

        if base_prob == 0:
            continue

        # 随机决定是否发言
        if random.random() > base_prob:
            logger.info(f"群 {group_id} 随机未通过（概率 {base_prob:.2f}），不发言")
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

            # 发送到群
            bot = nonebot.get_bot()
            await bot.send_group_msg(group_id=int(group_id), message=reply)
            logger.info(f"已向群 {group_id} 发送回复：{reply[:30]}...")

            # 记录发言时间，重置无人发言标志和消息计数器
            last_speak_time[group_id] = datetime.now()
            has_new_message_after_speak[group_id] = False
            group_msg_counter[group_id] = 0  # 重置计数器

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
            interval = MIN_INTERVAL_ACTIVE
        logger.info(f"存在活跃群，下次间隔缩短为 {interval} 秒")
    run_time = datetime.now() + timedelta(seconds=interval)
    logger.info(f"下次随机发言任务将在 {run_time.strftime('%Y-%m-%d %H:%M:%S')} 执行（间隔 {interval//60} 分钟）")
    scheduler.add_job(
        random_speak_job,
        "date",
        run_date=run_time,
        id="random_speak_job",
        replace_existing=True
    )

# ---------- 插件加载时启动任务 ----------
@driver.on_startup
async def start_random_speaker():
    """启动时调度第一次任务"""
    # 取消可能存在的旧任务
    try:
        scheduler.remove_job("random_speak_job")
    except:
        pass
    # 1分钟后第一次执行
    run_time = datetime.now() + timedelta(seconds=60)
    scheduler.add_job(
        random_speak_job,
        "date",
        run_date=run_time,
        id="random_speak_job"
    )
    logger.info("随机发言任务已调度，首次执行时间：{}".format(run_time.strftime("%Y-%m-%d %H:%M:%S")))

# ---------- 插件卸载时清理 ----------
@driver.on_shutdown
async def stop_random_speaker():
    """停止时移除任务"""
    try:
        scheduler.remove_job("random_speak_job")
    except:
        pass