"""Compare live profile feeds with every video card actually served by the site."""

from urllib.parse import urljoin, urlsplit

import pytest
from base_api import update_url_query
from selectolax.lexbor import LexborHTMLParser

from porndoe_api.modules.consts import pagination
from conftest import CHANNEL_URL, STAR_URL, VIDEO_URL

pytestmark = pytest.mark.asyncio(loop_scope="module")
HOSTS = ("porndoe.com", "de.porndoe.com", "fr.porndoe.com", "es.porndoe.com",
         "it.porndoe.com", "br.porndoe.com")


async def page_videos(core, url):
    tree = LexborHTMLParser(await core.fetch_text(url))
    ids = {urlsplit(anchor.attributes["href"]).path.rstrip("/").rsplit("/", 1)[-1]
           for anchor in tree.css('.videos-list a[href*="/watch/"]')}
    return tree, ids


async def stream_videos(stream):
    return [result.unwrap() async for result in stream]


@pytest.mark.parametrize("host", HOSTS)
async def test_regional_pasted_profile_matches_every_card(live_client, host):
    base = f"https://{host}/pornstars-profile/eva-elfie"
    tree, expected = await page_videos(live_client.core, base + "?sort=views&page=1")
    assert expected
    star = await live_client.get_pornstar(f"  {base}/?sort=views&page=4#videos\n")
    # A pasted page/fragment must not turn the profile's metadata into last-page data.
    assert star.name == "Eva Elfie"
    assert star.total_videos == int(tree.css(".-mhm-info-strong")[1].text(strip=True))
    assert star.per_page == len(expected)
    assert star.age and star.country and star.ethnicity
    assert star.measurements == "32C-26-30"
    rows = await stream_videos(star.videos(pages=1))
    assert {row.video_id for row in rows} == expected
    assert len(rows) == len(expected)
    assert all(urlsplit(row.url).hostname == host for row in rows)
    last = star.total_pages
    _, expected_last = await page_videos(live_client.core, base + f"?sort=views&page={last}")
    last_rows = await stream_videos(star.videos(pages="all", start_page=last))
    assert expected_last
    assert {row.video_id for row in last_rows} == expected_last
    assert len(last_rows) == len(expected_last)


@pytest.mark.parametrize("host", HOSTS)
async def test_regional_video_and_profile_links(live_client, host):
    url = VIDEO_URL.replace("porndoe.com", host)
    video = await live_client.get_video(url)
    assert video.duration == 2071 and video.video_qualities
    assert urlsplit(video.author_link).hostname == host
    assert video.pornstars_urls
    assert all(urlsplit(link).hostname == host for link in video.pornstars_urls)


async def test_all_profile_pages_match_site_navigation(live_client):
    expected = set()
    url = STAR_URL + "?page=1"
    page_count = 0
    # Follow the site's own Next links, independently of the API's page builder.
    while url:
        tree, ids = await page_videos(live_client.core, url)
        assert ids and not expected.intersection(ids)
        expected.update(ids)
        page_count += 1
        assert page_count < 20, "Unexpectedly large fixture; review the live pagination"
        next_page = tree.css_first(".pager-next[href]")
        url = urljoin(url, next_page.attributes["href"]) if next_page else None
    star = await live_client.get_pornstar(STAR_URL)
    rows = await stream_videos(star.videos(pages="all"))
    assert star.total_pages == page_count
    assert star.total_videos == len(expected)
    assert len(rows) == len(expected)
    assert {row.video_id for row in rows} == expected


@pytest.mark.parametrize("slug", ["lana-rhoades-1", "rob-piper"])
async def test_other_pornstars_first_and_last_page(live_client, slug):
    url = "https://porndoe.com/pornstars-profile/" + slug
    star = await live_client.get_pornstar(url)
    assert star.name and star.total_videos > 0
    for page in {1, star.total_pages}:
        _, expected = await page_videos(live_client.core, update_url_query(url, page=page))
        rows = await stream_videos(star.videos(pages=1, start_page=page))
        assert expected
        assert len(rows) == len(expected)
        assert {row.video_id for row in rows} == expected


async def test_single_video_profile_and_page_past_end(live_client):
    url = "https://porndoe.com/pornstars-profile/kat-hunter"
    star = await live_client.get_pornstar(url)
    _, expected = await page_videos(live_client.core, url + "?page=1")
    assert star.total_pages == 1 and star.total_videos == len(expected) == 1
    rows = await stream_videos(star.videos(pages="all"))
    assert {row.video_id for row in rows} == expected
    tree, past_end = await page_videos(live_client.core, url + "?page=2")
    assert not past_end
    assert pagination(tree)["per_page"] == 0
    assert await stream_videos(star.videos(pages="all", start_page=2)) == []


async def test_channel_all_pages_starting_on_final_page(live_client):
    channel = await live_client.get_channel(CHANNEL_URL)
    tree, expected = await page_videos(live_client.core, update_url_query(CHANNEL_URL, page=channel.total_pages))
    active = int(tree.css_first(".pager-active").text(strip=True))
    assert active == channel.total_pages
    rows = await stream_videos(channel.videos(pages="all", start_page=active))
    assert expected
    assert len(rows) == len(expected)
    assert {row.video_id for row in rows} == expected
