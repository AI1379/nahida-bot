#
# Created by Renatus Madrigal on 03/24/2025
#

from nonebot import on_command, on_message
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.params import CommandArg, Command, EventMessage, EventParam
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.log import logger
import types
from inspect import ismethod, isclass, ismodule


def print_dict(d: dict, max_depth=3, current_depth=0, visited=None):
    """
    Recursively prints the contents of a dictionary up to a specified depth.

    :param d: The dictionary to print.
    :param max_depth: The maximum depth to which the dictionary should be printed.
    :param current_depth: The current depth in the recursion.
    :param visited: A set of visited object IDs to avoid infinite recursion.
    """
    if visited is None:
        visited = set()

    if current_depth >= max_depth:
        print("  " * current_depth + "└── [Max Depth Reached]")
        return

    for key, value in d.items():
        if isinstance(value, dict):
            print("  " * current_depth + f"├── {key}: dict")
            print_dict(value, max_depth=max_depth,
                       current_depth=current_depth + 1, visited=visited)
        else:
            print("  " * current_depth + f"├── {key}: {type(value).__name__}")
            print_attributes(
                value,
                max_depth=max_depth,
                current_depth=current_depth + 1,
                visited=visited
            )


def print_attributes(obj, max_depth=3, current_depth=0, visited=None):
    """
    Recursively prints the attributes of an object up to a specified depth.

    :param obj: The object whose attributes are to be printed.
    :param max_depth: The maximum depth to which attributes should be printed.
    :param current_depth: The current depth in the recursion.
    :param visited: A set of visited object IDs to avoid infinite recursion.
    """
    if visited is None:
        visited = set()

    if current_depth >= max_depth:
        print("  " * current_depth + "└── [Max Depth Reached]")
        return

    obj_id = id(obj)
    if obj_id in visited:
        print("  " * current_depth + "└── [Traversed]")
        return
    visited.add(obj_id)

    if isinstance(obj, dict):
        print_dict(obj, max_depth=max_depth,
                   current_depth=current_depth, visited=visited)
        return

    attrs = [a for a in dir(obj) if not a.startswith("__")]

    for attr_name in attrs:
        try:
            attr_value = getattr(obj, attr_name)
        except Exception as e:
            print(f"  Failed to get attribute {attr_name} : {str(e)}")
            continue

        if (
            ismethod(attr_value) or
            isclass(attr_value) or
            ismodule(attr_value) or
            isinstance(attr_value, (types.BuiltinFunctionType, types.BuiltinMethodType)) or
            isinstance(attr_value, (int, float, str, bool, bytes))
        ):
            continue

        indent = "  " * current_depth
        print(f"{indent}├── {attr_name}: {type(attr_value).__name__}")

        print_attributes(
            attr_value,
            max_depth=max_depth,
            current_depth=current_depth + 1,
            visited=visited
        )


echo = on_command("echo", rule=to_me(), priority=5, block=True)


@echo.handle()
async def handle_first_receive(args: Message = CommandArg()):
    if msg := args.extract_plain_text():
        await echo.finish(msg)
    else:
        await echo.finish("你好像没有说话喵~")

"""
message_log = on_message(priority=5, block=True)


@message_log.handle()
async def handle_message_log(args: Message = EventMessage(), event: MessageEvent = EventParam()):
    logger.debug(f"Received message: {args}")
    logger.debug(f"Received event: {event}")
    logger.debug(f"Sender role: {event.sender.role}")
    logger.debug(f"Sender: {event.sender}")
"""
