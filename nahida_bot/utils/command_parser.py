#
# Created by Renatus Madrigal on 4/14/2025
#

from typing import Any, Dict, List, Union, Optional, get_args, get_origin


def _check_optional(arg_type: Any) -> bool:
    """
    Check if the argument type is optional.

    :param arg_type: The argument type to check.
    :return: True if the argument type is optional, False otherwise.
    """
    return get_origin(arg_type) == Union and type(None) in get_args(arg_type)


def _check_union(arg_type: Any) -> bool:
    """
    Check if the argument type is a union.

    :param arg_type: The argument type to check.
    :return: True if the argument type is a union, False otherwise.
    """
    return get_origin(arg_type) == Union and len(get_args(arg_type)) > 1 and type(None) not in get_args(arg_type)


def check_true(cmd: str) -> bool:
    """
    Check if the command is 'true'.

    :param cmd: The command string to check.
    :return: True if the command is 'true', False otherwise.
    """
    true_list = ["true", "yes", "1", "on", "enable", "allow"]
    false_list = ["false", "no", "0", "off", "disable", "deny"]
    if cmd.lower() in true_list:
        return True
    elif cmd.lower() in false_list:
        return False
    else:
        try:
            return bool(int(cmd))
        except ValueError:
            return False


def _check_list(arg_type: Any) -> bool:
    """
    Check if the argument type is a list.

    :param arg_type: The argument type to check.
    :return: True if the argument type is a list, False otherwise.
    """
    return get_origin(arg_type) == List or get_origin(arg_type) == list


def _basic_types(arg_type: Any) -> bool:
    """
    Check if the argument type is a basic type (str, int, float, bool).

    :param arg_type: The argument type to check.
    :return: True if the argument type is a basic type, False otherwise.
    """
    return arg_type in [str, int, float, bool]


def _basic_type_handler(arg_type: Any, arg: str) -> Union[str, int, float, bool]:
    """
    Handle the conversion of basic types.

    :param arg_type: The argument type to convert to.
    :param arg: The argument string to convert.
    :return: The converted argument.
    """
    if arg_type == str:
        return arg
    elif arg_type == int:
        return int(arg)
    elif arg_type == float:
        return float(arg)
    elif arg_type == bool:
        return check_true(arg)
    else:
        raise ValueError(f"Unsupported type: {arg_type}")

def split_arguments(command: str) -> List[str]:
    """
    Split the command string into arguments.

    :param command: The command string to split.
    :return: A list of arguments.
    """
    current_argument = ""
    split_command = []
    in_quote = False
    for char in command:
        if char == '"' or char == "'":
            in_quote = not in_quote
        elif char == " " and not in_quote:
            if current_argument:
                split_command.append(current_argument)
                current_argument = ""
        else:
            current_argument += char
    if current_argument:
        split_command.append(current_argument)
    return split_command

class Parser:
    """
    A class to parse and handle commands
    """

    def __init__(self, schema: Dict[str, Any]) -> None:
        """
        Initialize the Parser with a schema.

        :param schema: A dictionary representing the command schema.
        """
        self.schema = schema

    def parse(self, command: str) -> Optional[Dict[str, Any]]:
        """
        Parse the command string based on the schema.

        :param command: The command string to parse.
        :return: A dictionary of parsed arguments or None if parsing fails.
        """
        # TODO: Fix the parser
        split_command = split_arguments(command)

        # Parse the command arguments
        parsed_args = {}
        parsing_list = False
        key_idx = 0
        for arg in split_command:
            cur_key = list(self.schema.keys())[key_idx]
            arg_type = list(self.schema.values())[key_idx]
            if _check_optional(arg_type):
                while arg.lower() != cur_key.lower():
                    if key_idx + 1 >= len(self.schema) or not _check_optional(arg_type):
                        break
                    key_idx += 1
                    cur_key = list(self.schema.keys())[key_idx]
                    arg_type = list(self.schema.values())[key_idx]
                else:
                    # An optional argument is either a list or a boolean
                    if arg_type == Optional[bool]:
                        parsed_args[cur_key] = True
                        key_idx += 1
                    else:
                        parsing_list = True
                        parsed_args[cur_key] = []
                    continue
            elif parsing_list and arg.lower() in self.schema:
                parsing_list = False
                key_idx += 1
                arg_type = list(self.schema.values())[key_idx]
                cur_key = list(self.schema.keys())[key_idx]
            if _basic_types(arg_type):
                try:
                    parsed_args[cur_key] = _basic_type_handler(arg_type, arg)
                    key_idx += 1
                except ValueError:
                    return None
            elif _check_union(arg_type):
                for t in get_args(arg_type):
                    try:
                        parsed_args[cur_key] = t(arg)
                        key_idx += 1
                        break
                    except ValueError:
                        continue
            elif _check_list(arg_type) and not parsing_list:
                parsing_list = True
                parsed_args[cur_key] = []
            elif parsing_list and not arg.lower() in self.schema:
                try:
                    parsed_args[cur_key].append(get_args(arg_type)[0](arg))
                except IndexError:
                    return None
                except ValueError:
                    return None
            else:
                return None

        for key in self.schema.keys():
            if key not in parsed_args:
                parsed_args[key] = None

        return parsed_args

    def schema_to_help(self):
        """
        Convert the schema to a help string.

        :return: A string representing the help message.
        """
        help_str = "Command schema:\n"
        for key, value in self.schema.items():
            if _check_list(value):
                help_str += f"  {key}: List of {get_args(value)[0].__name__}\n"
            else:
                help_str += f"  {key}: {value.__name__}\n"
        return help_str
