#
# Created by Renatus Madrigal on 02/20/2026
#

from typing import Generic, TypeVar

T = TypeVar("T")


class UnwrapError(Exception):
    """Custom exception for unwrap errors."""

    def __init__(self):
        super().__init__("Failed to unwrap the Optional value. The value is None.")


class UnwrapHelper:
    """Helper class to unwrap Optional values."""

    @staticmethod
    def unwrap(value):
        """Unwrap the Optional value or raise UnwrapError if it is None."""
        if value is None:
            raise UnwrapError()
        return value

    def __call__(self, value: T | None) -> T:
        """Allow the instance to be called to unwrap values."""
        return self.unwrap(value)

    def __ror__(self, value: T | None) -> T:
        """Allow using the | operator to unwrap values."""
        return self.unwrap(value)


class UnwrapOrHelper(Generic[T]):
    """Helper class to provide a default value when unwrapping Optional values."""

    def __init__(self, default: T):
        self.default = default

    def unwrap(self, value: T | None) -> T:
        """Unwrap the Optional value or return the default if it is None."""
        return value if value is not None else self.default

    def __call__(self, value: T | None) -> T:
        """Allow the instance to be called to unwrap values with a default."""
        return self.unwrap(value)

    def __ror__(self, value: T | None) -> T:
        """Allow using the | operator to unwrap values with a default."""
        return self.unwrap(value)


class UnwrapOrThrowHelper(Generic[T]):
    """Helper class to throw a custom exception when unwrapping Optional values."""

    def __init__(self, exception: Exception):
        self.exception = exception

    def unwrap(self, value: T | None) -> T:
        """Unwrap the Optional value or raise the custom exception if it is None."""
        if value is None:
            raise self.exception
        return value

    def __call__(self, value: T | None) -> T:
        """Allow the instance to be called to unwrap values with a custom exception."""
        return self.unwrap(value)

    def __ror__(self, value: T | None) -> T:
        """Allow using the | operator to unwrap values with a custom exception."""
        return self.unwrap(value)


unwrap = UnwrapHelper()
unwrap_or = UnwrapOrHelper
unwrap_or_throw = UnwrapOrThrowHelper
