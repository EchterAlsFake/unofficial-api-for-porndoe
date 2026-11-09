"""Exercise the API against the live site and CDN using real network requests."""

import asyncio
from contextlib import aclosing
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from base_api import DownloadConfigRAW
from base_api.modules.config import IteratorConfig
from base_api.modules.errors import DataNotLoadedError, MediaLoadError, is_resource_gone

from porndoe_api import Channel, Pornstar, Video
from porndoe_api.modules.sorting import Sort, SortChannel, SortPornstar
from conftest import CHANNEL_URL, STAR_URL, VIDEO_URL

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_video(live_client):
    return await live_client.get_video(VIDEO_URL)


async def collect(stream):
    async with aclosing(stream):
        rows = [result.unwrap() async for result in stream]
    assert rows, "Live listing yielded no objects"
    return rows


async def test_video_metadata(live_video):
    video = live_video
    assert video.title == "Blonde Hooks Up With Bbc After Party"
    assert video.video_id == "pd4y7n5e2j8r" and video.internal_video_id == "1554419"
    assert video.duration == 2071 and video.length == "34:31"
    assert video.author_name == "BLACKED RAW"
    assert video.publish_date.startswith("2021-11-25")
    assert int(video.views) > 0 and int(video.likes) > 0
    assert int(video.rating_votes) == int(video.likes) + int(video.dislikes)
    assert video.thumbnail_url.startswith("https://") and video.preview_video_url.endswith(".mp4")
    assert video.categories and video.tags and len(video.pornstars_urls) == 2
    assert video.direct_download_urls and video.video_qualities == sorted(video.direct_download_urls)
    assert all(url.startswith("https://") and "/signup" not in url for url in video.direct_download_urls.values())


async def test_lazy_loading_real_source(live_client):
    video = await live_client.get_video(VIDEO_URL, load_html=False)
    with pytest.raises(DataNotLoadedError):
        _ = video.title
    await video.load_fields("title", "direct_download_urls")
    assert video.title and video.direct_download_urls and video.loaded_sources == {"html"}


@pytest.mark.parametrize("sort", list(Sort))
async def test_search_sorting(live_client, sort):
    rows = await collect(live_client.search("eva elfie", sorting_sort=sort, pages=1))
    assert len(rows) >= 10
    assert all(isinstance(v, Video) and v.title and v.duration for v in rows)
    assert all(urlsplit(v.url).path.startswith("/watch/") for v in rows)


async def test_search_pagination(live_client):
    first = await collect(live_client.search("eva elfie", pages=1))
    second = await collect(live_client.search("eva elfie", pages=1, start_page=2))
    assert {v.url for v in first} != {v.url for v in second}


@pytest.mark.parametrize("url", ["https://porndoe.com/videos", "https://porndoe.com/category/32/hd-videos",
                                 "https://porndoe.com/tag/2240/american-porn"])
async def test_public_video_listings(live_client, url):
    rows = await collect(live_client.get_videos(url, pages=1))
    assert all(v.title and v.thumbnail_url and v.preview_video_url for v in rows)


async def test_profile_metadata_and_videos(live_client):
    star = await live_client.get_pornstar(STAR_URL)
    channel = await live_client.get_channel(CHANNEL_URL)
    assert star.name == "Eva Elfie" and star.birth_date == "2000-05-27"
    assert star.country == "Russia" and star.measurements and star.description
    assert star.total_videos > 24 and star.total_pages > 1
    assert channel.name == "BLACKED RAW" and channel.website.lower() == "https://blackedraw.com"
    assert channel.total_videos > 30 and channel.rank > 0 and channel.description
    for profile in (star, channel):
        first = await collect(profile.videos(pages=1))
        second = await collect(profile.videos(pages=1, start_page=2))
        assert {v.url for v in first} != {v.url for v in second}


async def test_profile_all_pages(live_client):
    star = await live_client.get_pornstar(STAR_URL)
    rows = await collect(star.videos(pages="all"))
    assert len(rows) == star.total_videos
    assert len({row.video_id for row in rows}) == star.total_videos


async def test_related_profiles(live_video):
    author = await live_video.get_author
    assert isinstance(author, Channel) and author.name == "BLACKED RAW"
    stars = [s async for s in live_video.get_pornstars]
    assert {s.name for s in stars} == {"Mia Melano", "Rob Piper"}
    assert all(s.core is live_video.core for s in stars)


async def test_profile_cross_listings(live_client):
    star = await live_client.get_pornstar(STAR_URL)
    channels = await collect(star.get_channels(pages=1))
    assert all(isinstance(c, Channel) and c.name for c in channels)
    channel = await live_client.get_channel(CHANNEL_URL)
    stars = await collect(channel.get_pornstars(pages=1))
    assert all(isinstance(s, Pornstar) and s.name for s in stars)


@pytest.mark.parametrize("sort", [SortChannel.Sort_ranking, SortChannel.Sort_name])
async def test_channel_directory(live_client, sort):
    rows = await collect(live_client.get_channels(pages=1, sorting_sort=sort))
    assert all(isinstance(c, Channel) and c.name and c.thumbnail_url for c in rows)


@pytest.mark.parametrize("sort", [SortPornstar.Sort_famous, SortPornstar.Sort_most_videos])
async def test_pornstar_directory(live_client, sort):
    rows = await collect(live_client.get_pornstars(pages=1, sorting_sort=sort))
    assert all(isinstance(s, Pornstar) and s.name and s.total_videos for s in rows)


@pytest.mark.parametrize("filters", [{"gender": "male"}, {"country": "ru"},
                                     {"gender": "male", "country": "us"}])
async def test_pornstar_directory_filters(live_client, filters):
    rows = await collect(live_client.get_pornstars(pages=1, **filters))
    assert all(isinstance(star, Pornstar) and star.name for star in rows)
    await rows[0].load_sources("html")
    if filters.get("gender") == "male":
        assert rows[0].gender == "M"
    if filters.get("country") == "ru":
        assert rows[0].country == "Russia"
    if filters.get("country") == "us":
        assert rows[0].country == "United States"


@pytest.mark.parametrize("section", ["exclusive", "trending"])
async def test_channel_directory_sections(live_client, section):
    rows = await collect(live_client.get_channels(pages=1, section=section))
    assert all(isinstance(channel, Channel) and channel.name for channel in rows)


async def test_categories(live_client):
    categories = await live_client.get_categories()
    assert len(categories) > 50
    assert all(c.name and c.total_videos and c.thumbnail_url for c in categories)
    await categories[0].load_sources("html")
    assert categories[0].name and categories[0].total_pages > 1
    rows = await collect(categories[0].videos(pages=1))
    assert rows[0].title


async def test_related_and_concurrent_video_loading(live_client, live_video):
    rows = await collect(live_video.get_related_videos())
    videos = await asyncio.gather(*(live_client.get_video(v.url) for v in rows[:2]))
    assert len({v.internal_video_id for v in videos}) == 2
    assert all(v.title and v.direct_download_urls for v in videos)


async def test_iterator_full_loading_and_early_close(live_client):
    config = IteratorConfig(load_specific_sources=("html",), max_item_concurrency=1, max_page_concurrency=1)
    async with aclosing(live_client.search("eva elfie", pages=1, iterator_config=config)) as stream:
        result = await anext(stream)
        video = result.unwrap()
        assert video.direct_download_urls and video.internal_video_id
        assert video.loaded_sources == {"html"}


async def test_real_cdn_range(live_client, live_video):
    url = live_video.direct_download_urls[min(live_video.video_qualities)]
    response = await live_client.core.request(url, headers={"Range": "bytes=0-4095"})
    assert response.status_code == 206
    assert len(response.content) == 4096 and b"ftyp" in response.content[:64]


async def test_real_mp4_download(live_video, tmp_path):
    destination = tmp_path / "porndoe.mp4"
    progress = []
    config = DownloadConfigRAW(
        quality="worst", path=str(destination), no_title=True, max_workers=2,
        max_retries=1, callback=lambda downloaded, total: progress.append((downloaded, total)),
    )
    assert await live_video.download(config) is True
    assert destination.stat().st_size > 1_000_000
    with destination.open("rb") as video_file:
        assert b"ftyp" in video_file.read(64)
    assert progress and progress[-1][0] == progress[-1][1] == destination.stat().st_size


async def test_real_not_found_is_reported(live_client):
    with pytest.raises(MediaLoadError) as error:
        await live_client.get_video("https://porndoe.com/watch/pd000000000000000000000")
    assert is_resource_gone(error.value)
