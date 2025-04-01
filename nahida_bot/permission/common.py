#
# Created by Renatus Madrigal on 04/01/2025
#

from enum import Enum

class PermissionLevel(Enum):
    """
    Enum for permission levels.
    """
    USER = 0
    ADMIN = 1
    SUPER_ADMIN = 2