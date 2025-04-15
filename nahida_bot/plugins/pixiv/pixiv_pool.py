#
# Created by Renatus Madrigal on 4/15/2025
#

from pixivpy3 import AppPixivAPI
from nonebot.log import logger
from nahida_bot.scheduler import scheduler


class PixivPool:
    def __init__(self, refresh_tokens: list = None):
        """
        Initialize the PixivPool with a list of refresh tokens.
        :param refresh_tokens: A list of refresh tokens to add to the pool.
        """
        self._pool = {}
        self.current_token = None
        self.add_tokens(refresh_tokens)

    def add_token(self, refresh_token: str):
        """
        Add a refresh token to the pool.

        :param refresh_token: The refresh token to add.
        """
        logger.info(f"Adding refresh token: {refresh_token}")
        if refresh_token not in self._pool:
            self._pool[refresh_token] = AppPixivAPI()
            self._pool[refresh_token].auth(refresh_token=refresh_token)

            @scheduler.scheduled_job("cron", hour="*")
            async def auto_refresh():
                """
                Automatically refresh the token every hour.
                """
                logger.info(f"Refreshing token: {refresh_token}")
                self._pool[refresh_token].auth(refresh_token=refresh_token)

    def add_tokens(self, refresh_tokens: list):
        """
        Add multiple refresh tokens to the pool.

        :param refresh_tokens: A list of refresh tokens to add.
        """
        for token in refresh_tokens:
            self.add_token(token)

    def all_api(self):
        """
        Get all AppPixivAPI instances in the pool.

        :return: A generator that yields AppPixivAPI instances.
        """
        while True:
            for refresh_token in self._pool:
                self._pool[refresh_token].auth(refresh_token=refresh_token)
                yield refresh_token, self._pool[refresh_token]
