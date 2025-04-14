#
# Created by Renatus Madrigal on 4/14/2025
#

from typing import Any, Dict, List, Union, Optional, get_args, get_origin
from nahida_bot.utils.command_parser import Parser


def test_parser_init():
    schema = {
        "arg1": str,
        "arg2": int,
        "arg3": bool,
        "arg4": Optional[str],
        "arg5": List[str],
        "arg6": Union[str, int],
    }
    parser = Parser(schema)
    assert parser.schema == schema


def test_parser_basic_types():
    schema = {
        "arg1": str,
        "arg2": int,
        "arg3": bool,
    }
    parser = Parser(schema)

    assert parser.parse("   hello 1 ALLOW ") == {
        "arg1": "hello",
        "arg2": 1,
        "arg3": True,
    }

    assert parser.parse("   hello 1 false ") == {
        "arg1": "hello",
        "arg2": 1,
        "arg3": False,
    }

    assert parser.parse("   'hello' 1 123 ") == {
        "arg1": "hello",
        "arg2": 1,
        "arg3": True,
    }


def test_parser_optional():
    schema = {
        "arg1": str,
        "flag": Optional[bool],
        "arg2": int,
    }
    parser = Parser(schema)

    assert parser.parse("   hello flag 1 ") == {
        "arg1": "hello",
        "flag": True,
        "arg2": 1,
    }

    assert parser.parse("   hello  1 ") == {
        "arg1": "hello",
        "flag": None,
        "arg2": 1,
    }

    assert parser.parse("   hello  ") == {
        "arg1": "hello",
        "flag": None,
        "arg2": None
    }


def test_parser_list():
    schema = {
        "arg1": str,
        "arg2": List[str],
        "arg3": Optional[List[str]],
    }
    parser = Parser(schema)

    assert parser.parse("   hello arg2 1 2 3 4 5 ") == {
        "arg1": "hello",
        "arg2": ["1", "2", "3", "4", "5"],
        "arg3": None,
    }

    assert parser.parse("   hello arg2 1 2 3 4 5 arg3 flag ") == {
        "arg1": "hello",
        "arg2": ["1", "2", "3", "4", "5"],
        "arg3": ["flag"],
    }
