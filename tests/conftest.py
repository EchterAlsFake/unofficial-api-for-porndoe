import pytest_asyncio

from base_api import BaseCore
from base_api.modules.config import RuntimeConfig
from porndoe_api import Client

VIDEO_URL = "https://porndoe.com/watch/pd4y7n5e2j8r"
STAR_URL = "https://porndoe.com/pornstars-profile/eva-elfie"
CHANNEL_URL = "https://porndoe.com/channel-profile/blacked-raw"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_client():
    runtime = RuntimeConfig()
    runtime.request_delay = 2
    runtime.videos_concurrency = 1
    runtime.pages_concurrency = 1
    runtime.request_attempts = 2
    async with BaseCore(runtime) as core, Client(core) as client:
        yield client
