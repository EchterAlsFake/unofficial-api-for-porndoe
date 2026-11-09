from __future__ import annotations

import argparse
import asyncio
import json
import logging
from contextlib import aclosing
from dataclasses import dataclass
from functools import partial
from typing import AsyncGenerator, ClassVar
from urllib.parse import urljoin, urlsplit, urlunsplit

from base_api import (
    BaseCore, BaseMedia, DownloadConfigHLS, DownloadConfigRAW, MediaLoadError,
    MediaLoadErrors, ScrapeResult, media_field,
    scrape_stream, update_url_query,
)
from base_api.modules.config import IteratorConfig
from base_api.modules.logger import configure_app_logging, get_logger
from base_api.modules.provider import download_errors, fetch_content, prepare_download_config
from base_api.modules.static_functions import choose_quality_from_list
from base_api.modules.type_hints import DownloadReport
from selectolax.lexbor import LexborHTMLParser

from porndoe_api.modules import errors as provider_errors
from porndoe_api.modules.consts import (
    BASE_URL, attr, extract_player, extract_profile, extract_video,
    extractor_categories, extractor_channels, extractor_pornstars,
    extractor_video_list, pagination, text,
)
from porndoe_api.modules.errors import DownloadFailed
from porndoe_api.modules.sorting import Sort, SortCategory, SortChannel, SortPornstar

logger = get_logger(__name__)


async def get_html_content(core: BaseCore, url: str, *, owner=None) -> str:
    content = await fetch_content(core, url, logger=logger, owner=owner, error_types=provider_errors)
    requested_section = urlsplit(url).path.strip("/").split("/")[0]
    resolved_section = urlsplit(getattr(content, "url", url)).path.strip("/").split("/")[0]
    if (requested_section in {"watch", "pornstars-profile", "channel-profile"}
            and resolved_section != requested_section):
        raise provider_errors.NotFound(f"Resource redirected to a listing: {url}", url=url)
    return content


def _url(url: str, section: str | None = None) -> str:
    parsed = urlsplit(urljoin(BASE_URL, url.strip()))
    if parsed.scheme not in {"http", "https"} or not (
        parsed.hostname == "porndoe.com" or (parsed.hostname or "").endswith(".porndoe.com")
    ):
        raise ValueError("A porndoe.com URL is required")
    if section and (not parsed.path.startswith(f"/{section}/") or
                    len(parsed.path.strip("/").split("/")) != 2):
        raise ValueError(f"An individual /{section}/ URL is required")
    return urlunsplit(parsed._replace(path=parsed.path.rstrip("/") or "/", fragment=""))


async def _listing(core, url, constructor, extractor, pages, iterator_config, start_page=1):
    if (isinstance(pages, bool) or (pages != "all" and (not isinstance(pages, int) or pages < 1))
            or isinstance(start_page, bool) or not isinstance(start_page, int) or start_page < 1):
        raise ValueError("pages must be a positive integer or 'all'; start_page must be positive")
    url = update_url_query(_url(url), page=None)
    if pages == "all":
        first = await get_html_content(core, update_url_query(url, page=start_page))
        info = await asyncio.to_thread(pagination, LexborHTMLParser(first))
        pages = max(0, info["total_pages"] - start_page + 1)
    page_urls = [update_url_query(url, page=p) for p in range(start_page, start_page + pages)]
    stream = scrape_stream(
        core=core, constructor=constructor, target_page_urls=page_urls,
        item_extractor=partial(extractor, base_url=url),
        # Listing summaries already contain usable fields. Loading full pages is
        # opt-in, using the same IteratorConfig contract as the other providers.
        iterator_config=iterator_config if iterator_config is not None else IteratorConfig(),
    )
    async with aclosing(stream):
        async for result in stream:
            yield result


@dataclass(slots=True, kw_only=True)
class Video(BaseMedia):
    url: str
    core: BaseCore
    video_id: str | None = media_field("html")
    internal_video_id: str | None = media_field("html")
    title: str | None = media_field("html")
    description: str | None = media_field("html")
    thumbnail_url: str | None = media_field("html")
    preview_video_url: str | None = media_field("html")
    publish_date: str | None = media_field("html")
    publish_date_relative: str | None = media_field("html")
    content_url: str | None = media_field("html")
    duration: int | None = media_field("html")
    length: str | None = media_field("html")
    author_name: str | None = media_field("html")
    author_link: str | None = media_field("html")
    pornstars: list[dict[str, str]] | None = media_field("html")
    pornstars_urls: list[str] | None = media_field("html")
    categories: list[dict[str, str]] | None = media_field("html")
    tags: list[str] | None = media_field("html")
    tags_urls: list[str] | None = media_field("html")
    views: str | None = media_field("html")
    likes: str | None = media_field("html")
    dislikes: str | None = media_field("html")
    rating_votes: str | None = media_field("html")
    rating_percent: str | None = media_field("html")
    video_quality: str | None = media_field("html")
    is_hd: bool | None = media_field("html")
    is_vr: bool | None = media_field("html")
    is_exclusive: bool | None = media_field("html")
    license_url: str | None = media_field("html")
    direct_download_urls: dict[int, str] | None = media_field("html")
    video_qualities: list[int] | None = media_field("html")
    hls_download_urls: dict[int, str] | None = media_field("html")
    m3u8_base_url: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        url = _url(self.url, "watch")
        content = await get_html_content(self.core, url, owner=self)
        page_url = self.resolved_url or url
        metadata = await asyncio.to_thread(self._extract_html, content, page_url)
        # The service selects the video from Referer. Per-request headers and a
        # distinct id keep concurrent videos (and the core cache) independent.
        service_url = update_url_query(urljoin(page_url, "/service/index.json"),
                                       device="desktop", height=1080, width=1920,
                                       page="video", id=metadata["video_id"])
        service = await self.core.fetch_text(
            service_url, headers={"Referer": page_url, "X-Requested-With": "XMLHttpRequest"}
        )
        metadata.update(await asyncio.to_thread(extract_player, json.loads(service), page_url))
        return metadata

    _extract_html = staticmethod(extract_video)

    @download_errors(DownloadFailed)
    async def download(self, configuration: DownloadConfigRAW | DownloadConfigHLS) -> bool | DownloadReport:
        await self.load_sources("html")
        config = prepare_download_config(configuration, self.title or "video")
        if isinstance(config, DownloadConfigHLS):
            urls = self.hls_download_urls or {}
            if not urls:
                raise DownloadFailed("No HLS sources available; use DownloadConfigRAW for MP4")
            height = choose_quality_from_list(urls, config.quality)
            config.m3u8_base_url = urls[height]
            return await self.core.download(configuration=config)
        urls = self.direct_download_urls or {}
        if not urls:
            raise DownloadFailed(f"No playable MP4 sources available for {self.url}")
        height = choose_quality_from_list(urls, config.quality)
        return await self.core.legacy_download(url=urls[height], configuration=config)

    @property
    async def get_author(self, load_html: bool = True) -> Channel | None:
        url = await self.get_field("author_link")
        if not url:
            return None
        channel = Channel(url=url, core=self.core)
        if load_html:
            await channel.load_sources("html")
        return channel

    @property
    async def get_pornstars(self, load_html: bool = True) -> AsyncGenerator[Pornstar, None]:
        for url in await self.get_field("pornstars_urls") or []:
            star = Pornstar(url=url, core=self.core)
            if load_html:
                await star.load_sources("html")
            yield star

    def get_related_videos(self, iterator_config: IteratorConfig | None = None) -> AsyncGenerator[ScrapeResult[Video], None]:
        return _listing(self.core, self.url, Video, extractor_video_list, 1, iterator_config)


@dataclass(slots=True, kw_only=True)
class BaseChannelPornstar(BaseMedia):
    url: str
    core: BaseCore
    name: str | None = media_field("html")
    description: str | None = media_field("html")
    thumbnail_url: str | None = media_field("html")
    total_videos: int | None = media_field("html")
    total_pages: int | None = media_field("html")
    per_page: int | None = media_field("html")
    rank: int | None = media_field("html")
    external_urls: list[str] | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}
    section: ClassVar[str]
    schema_type: ClassVar[str]

    async def _load_html(self) -> dict[str, object]:
        url = update_url_query(_url(self.url, self.section), page=None)
        content = await get_html_content(self.core, url, owner=self)
        return await asyncio.to_thread(self._extract_html, content)

    @classmethod
    def _extract_html(cls, content):
        return extract_profile(content, cls.schema_type)

    def videos(self, pages: int | str = 2, sorting_sort: str | Sort | None = None,
               iterator_config: IteratorConfig | None = None, start_page: int = 1) -> AsyncGenerator[ScrapeResult[Video], None]:
        url = _url(self.url, self.section)
        if sorting_sort is not None:
            url = update_url_query(url, sort=sorting_sort or None)
        return _listing(self.core, url, Video, extractor_video_list, pages, iterator_config, start_page)


@dataclass(slots=True, kw_only=True)
class Pornstar(BaseChannelPornstar):
    age: int | None = media_field("html")
    country: str | None = media_field("html")
    birth_date: str | None = media_field("html")
    birth_place: str | None = media_field("html")
    gender: str | None = media_field("html")
    measurements: str | None = media_field("html")
    ethnicity: str | None = media_field("html")

    section: ClassVar[str] = "pornstars-profile"
    schema_type: ClassVar[str] = "Person"

    def get_channels(self, pages: int | str = 2, iterator_config: IteratorConfig | None = None
                     ) -> AsyncGenerator[ScrapeResult[Channel], None]:
        parsed = urlsplit(_url(self.url, self.section))
        slug = parsed.path.rsplit("/", 1)[-1]
        url = urlunsplit(parsed._replace(path=f"/pornstar/{slug}/channels", query=""))
        return _listing(self.core, url, Channel, extractor_channels, pages, iterator_config)


@dataclass(slots=True, kw_only=True)
class Channel(BaseChannelPornstar):
    website: str | None = media_field("html")
    total_videos_views: str | None = media_field("html")

    section: ClassVar[str] = "channel-profile"
    schema_type: ClassVar[str] = "Organization"

    def get_pornstars(self, gender: str = "female", pages: int | str = 2,
                     iterator_config: IteratorConfig | None = None) -> AsyncGenerator[ScrapeResult[Pornstar], None]:
        if gender not in {"female", "male", "models"}:
            raise ValueError("gender must be female, male or models")
        parsed = urlsplit(_url(self.url, self.section))
        slug = parsed.path.rsplit("/", 1)[-1]
        url = urlunsplit(parsed._replace(path=f"/channel/{slug}/{gender}", query=""))
        return _listing(self.core, url, Pornstar, extractor_pornstars, pages, iterator_config)


@dataclass(slots=True, kw_only=True)
class Category(BaseMedia):
    url: str
    core: BaseCore
    name: str | None = media_field("html")
    thumbnail_url: str | None = media_field("html")
    total_videos: int | None = media_field("html")
    per_page: int | None = media_field("html")
    total_pages: int | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        content = await get_html_content(self.core, _url(self.url), owner=self)
        tree = LexborHTMLParser(content)
        return {"name": text(tree, "h1.-heading"),
                "thumbnail_url": attr(tree, 'meta[property="og:image"]', "content"), **pagination(tree)}

    def videos(self, pages: int | str = 2, sorting_sort: str | Sort | None = None,
               iterator_config: IteratorConfig | None = None, start_page: int = 1) -> AsyncGenerator[ScrapeResult[Video], None]:
        url = self.url if sorting_sort is None else update_url_query(self.url, sort=sorting_sort or None)
        return _listing(self.core, url, Video, extractor_video_list, pages, iterator_config, start_page)


class Client:
    def __init__(self, core: BaseCore | None = None) -> None:
        self.core = core if core is not None else BaseCore()
        self._owns_core = core is None
        self.core.initialize_session()

    async def __aenter__(self) -> Client:
        self.core.initialize_session()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def close(self) -> None:
        """Close a default core; callers retain ownership of supplied cores."""
        if self._owns_core:
            await self.core.close()

    async def get_video(self, url: str, load_html: bool = True) -> Video:
        video = Video(url=_url(url, "watch"), core=self.core)
        if load_html:
            await video.load_sources("html")
        return video

    async def get_pornstar(self, url: str, load_html: bool = True) -> Pornstar:
        star = Pornstar(url=_url(url, "pornstars-profile"), core=self.core)
        if load_html:
            await star.load_sources("html")
        return star

    async def get_channel(self, url: str, load_html: bool = True) -> Channel:
        channel = Channel(url=_url(url, "channel-profile"), core=self.core)
        if load_html:
            await channel.load_sources("html")
        return channel

    def search(self, query: str, sorting_sort: str | Sort = Sort.Sort_relevance,
               pages: int | str = 2, iterator_config: IteratorConfig | None = None,
               start_page: int = 1) -> AsyncGenerator[ScrapeResult[Video], None]:
        url = update_url_query(BASE_URL + "/search", keywords=query, sort=sorting_sort or None)
        return _listing(self.core, url, Video, extractor_video_list, pages, iterator_config, start_page)

    def get_videos(self, url: str = BASE_URL + "/videos", pages: int | str = 2,
                   sorting_sort: str | Sort | None = None,
                   iterator_config: IteratorConfig | None = None, start_page: int = 1) -> AsyncGenerator[ScrapeResult[Video], None]:
        """Browse any public video listing, including category and tag URLs."""
        if sorting_sort is not None:
            url = update_url_query(url, sort=sorting_sort or None)
        return _listing(self.core, url, Video, extractor_video_list, pages, iterator_config, start_page)

    def get_pornstars(self, pages: int | str = 2, sorting_sort: str | SortPornstar = SortPornstar.Sort_famous,
                     iterator_config: IteratorConfig | None = None, gender: str | None = None,
                     country: str | None = None, start_page: int = 1) -> AsyncGenerator[ScrapeResult[Pornstar], None]:
        url = BASE_URL + "/pornstars"
        if country:
            url += f"/country/{country}"
            url = update_url_query(url, gender=gender if gender != "female" else None)
        elif gender and gender != "female":
            url += f"/gender/{gender}"
        return _listing(self.core, update_url_query(url, sort=sorting_sort or None),
                        Pornstar, extractor_pornstars, pages, iterator_config, start_page)

    def get_channels(self, pages: int | str = 2, sorting_sort: str | SortChannel = SortChannel.Sort_ranking,
                     iterator_config: IteratorConfig | None = None, section: str = "",
                     start_page: int = 1) -> AsyncGenerator[ScrapeResult[Channel], None]:
        if section not in {"", "exclusive", "trending"}:
            raise ValueError("section must be empty, exclusive or trending")
        url = BASE_URL + "/channels" + (f"/{section}" if section else "")
        return _listing(self.core, update_url_query(url, sort=sorting_sort or None),
                        Channel, extractor_channels, pages, iterator_config, start_page)

    async def get_categories(self, sorting_sort: str | SortCategory = SortCategory.Sort_popular) -> list[Category]:
        url = update_url_query(BASE_URL + "/categories", sort=sorting_sort or None)
        content = await get_html_content(self.core, url)
        rows = await asyncio.to_thread(extractor_categories, content)
        return [Category(core=self.core, **row) for row in rows]


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Porndoe API command line downloader")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--download", metavar="URL")
    source.add_argument("--file", metavar="FILE", help="Text file containing one video URL per line")
    parser.add_argument("--output", required=True, help="Output directory, or filename with --no-title")
    parser.add_argument("--quality", default="best", help="best, half, worst, or a numeric height")
    parser.add_argument("--no-title", action="store_true")
    return parser


async def run_main(args_list: list[str] | None = None) -> None:
    args = create_parser().parse_args(args_list)
    if args.file:
        with open(args.file, encoding="utf-8") as source:
            urls = [line.strip() for line in source if line.strip()]
    else:
        urls = [args.download]
    config = DownloadConfigRAW(path=args.output, quality=args.quality, no_title=args.no_title)
    async with Client() as client:
        for url in urls:
            video = await client.get_video(url)
            await video.download(config)


def main() -> None:
    configure_app_logging(level=logging.INFO)
    try:
        asyncio.run(run_main())
    except KeyboardInterrupt:
        pass
