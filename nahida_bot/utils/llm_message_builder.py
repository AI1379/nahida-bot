#
# Created by Renatus Madrigal on 02/20/2026
#

from typing import List, Self
from openai.types.chat import ChatCompletionMessageParam


class LLMMessageBuilder:
    """A utility class to build messages for LLM input."""

    def __init__(self):
        self.messages: List[ChatCompletionMessageParam] = []

    def add_user_message(self, content: str, name: str = "") -> Self:
        """Add a user message."""
        self.messages.append({"role": "user", "content": content, "name": name})
        return self

    def add_assistant_message(self, content: str, name: str = "") -> Self:
        """Add an assistant message."""
        self.messages.append({"role": "assistant", "content": content, "name": name})
        return self

    def add_system_message(self, content: str, name: str = "") -> Self:
        """Add a system message."""
        self.messages.append({"role": "system", "content": content, "name": name})
        return self

    def build(self) -> List[ChatCompletionMessageParam]:
        """Build the message list for LLM input."""
        return self.messages
