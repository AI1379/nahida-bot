#
# Created by Renatus Madrigal on 4/15/2025
#

from gppt import GetPixivToken
from gppt.consts import REDIRECT_URI
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC  # noqa: N812
from selenium.webdriver.support.ui import WebDriverWait


class HookedGetPixivToken(GetPixivToken):
    def _GetPixivToken__wait_for_redirect(self) -> None:
        try:
            WebDriverWait(self.driver, 600).until(EC.url_matches(f"^{REDIRECT_URI}"))
        except TimeoutException as err:
            self.driver.close()
            msg = "Failed to login. Please check your information or proxy. (Maybe restricted by pixiv?)"
            raise ValueError(msg) from err

g = HookedGetPixivToken()
refresh_token = g.login()["refresh_token"]
print(refresh_token)
