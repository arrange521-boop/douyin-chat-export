import asyncio

from extractor.web_scraper import CHAT_URL, SEL_CONV_ITEM, WebChatScraper


class _ConversationPage:
    def __init__(self):
        self.url = CHAT_URL
        self.wait_calls = []

    async def goto(self, url, wait_until=None):
        self.url = url

    async def wait_for_selector(self, selector, **kwargs):
        self.wait_calls.append((selector, kwargs))


def test_chat_navigation_waits_for_attached_rows():
    scraper = object.__new__(WebChatScraper)
    scraper.page = _ConversationPage()

    asyncio.run(scraper.navigate_to_chat())

    assert scraper.page.wait_calls == [
        (SEL_CONV_ITEM, {"timeout": 20000, "state": "attached"})
    ]
