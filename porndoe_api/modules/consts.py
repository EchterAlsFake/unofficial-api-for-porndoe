"""HTML extractors shared by the client and media objects."""

import base64
import html
import json
import math
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

BASE_URL = "https://porndoe.com"


def text(tree, selector):
    node = tree.css_first(selector)
    return " ".join(node.text(separator=" ", strip=True).split()) if node else None


def attr(tree, selector, attribute):
    node = tree.css_first(selector)
    return node.attributes.get(attribute) if node else None


def link(node, base_url=BASE_URL):
    if node is None:
        return None
    path = node.attributes.get("href")
    if not path and (encoded := node.attributes.get("v-follow")):
        path = base64.b64decode(encoded.strip("'\"")).decode()
    return urljoin(base_url, path) if path else None


def image(tree, base_url=BASE_URL):
    node = tree.css_first("[data-src], img[src]")
    value = (node.attributes.get("data-src") or node.attributes.get("src")) if node else None
    return urljoin(base_url, value) if value else None


def json_graph(tree):
    nodes = []
    for script in tree.css('script[type="application/ld+json"]'):
        data = json.loads(script.text())
        nodes.extend(data if isinstance(data, list) else data.get("@graph", [data]))
    return nodes


def duration_seconds(value):
    if not value:
        return None
    if ":" in value:
        return sum(int(n) * 60**i for i, n in enumerate(reversed(value.split(":"))))
    match = re.fullmatch(r"P?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    return sum(int(n or 0) * m for n, m in zip(match.groups(), (3600, 60, 1))) if match else None


def format_length(seconds):
    if seconds is None:
        return None
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def count(value):
    match = re.search(r"\d[\d,.\u00a0\u202f]*", value or "")
    return int(re.sub(r"\D", "", match[0])) if match else None


def pagination(tree):
    pages = [int(parse_qs(urlsplit(n.attributes["href"]).query).get("page", [1])[0])
             for n in tree.css('.pager a[href]')]
    # The final page is a span, not a link. Counting links alone loses that page
    # when an all-pages iterator starts on the last page.
    pages.extend(count(n.text()) for n in tree.css('.pager .pager-active') if count(n.text()) is not None)
    showing = None
    for node in tree.css('.-flex .-grow, .-chp-above-left'):
        # The surrounding words are translated, but the numeric range is stable.
        showing = re.search(r"(\d[\d,.]*)\s*[-–]\s*(\d[\d,.]*)\D+?(\d[\d,.]*)", node.text())
        if showing:
            break
    total = count(showing[3]) if showing else None
    per_page = max(0, count(showing[2]) - count(showing[1]) + 1) if showing else None
    total_pages = max(pages, default=1)
    if total and per_page and not pages:
        total_pages = math.ceil(total / per_page)
    return {"total_videos": total, "per_page": per_page, "total_pages": total_pages}


def extractor_video_list(content, base_url=BASE_URL):
    tree = LexborHTMLParser(content)
    metadata = {}
    for graph_node in json_graph(tree):
        for video in graph_node.get("hasPart", []):
            if video.get("@type") == "VideoObject" and video.get("contentUrl"):
                metadata[urlsplit(video["contentUrl"]).path] = video
    rows = []
    seen = set()
    for node in tree.css(".video-item"):
        anchor = node.css_first(".video-item-link[href]")
        url = link(anchor, base_url)
        if not url or "/watch/" not in url or url in seen:
            continue
        seen.add(url)
        data = metadata.get(urlsplit(url).path, {})
        author = node.css_first(".video-item-site")
        producer = data.get("producer") or data.get("publisher") or {}
        author_url = link(author, base_url)
        if not author_url and producer.get("url"):
            author_url = urljoin(base_url, urlsplit(producer["url"]).path)
        stars = [{"name": n.attributes.get("title") or n.text(strip=True), "url": link(n, base_url)}
                 for n in node.css(".video-item-actor")]
        seconds = duration_seconds(node.attributes.get("data-duration"))
        rows.append({
            "url": url, "video_id": urlsplit(url).path.rsplit("/", 1)[-1],
            "title": anchor.attributes.get("aria-label") or text(node, ".video-item-title"),
            "thumbnail_url": image(node, base_url),
            "preview_video_url": anchor.attributes.get("data-source"),
            "length": format_length(seconds), "duration": seconds,
            "views": node.attributes.get("data-views"),
            "author_name": author.text(strip=True) if author else producer.get("name"),
            "author_link": author_url, "pornstars": stars,
            "publish_date": data.get("uploadDate"),
            "pornstars_urls": [n["url"] for n in stars if n["url"]],
            "is_hd": node.attributes.get("data-hd") == "true",
            "is_vr": node.attributes.get("data-vr") == "true",
            "is_exclusive": node.attributes.get("data-exclusive") == "true",
        })
    return rows


def extractor_pornstars(content, base_url=BASE_URL):
    tree = LexborHTMLParser(content)
    rows = []
    for node in tree.css(".actors-list-item"):
        anchor = node.css_first("a[title]")
        # Channel cards open a channel-filtered /movies/ listing first; their
        # actual profile link is the encoded 'View all videos' button.
        profile_url = next((url for n in node.css("a") if (url := link(n, base_url))
                            and urlsplit(url).path.startswith("/pornstars-profile/")), None)
        if anchor and profile_url:
            rows.append({"url": profile_url, "name": anchor.attributes.get("title"),
                         "thumbnail_url": image(node, base_url),
                         "total_videos": count(text(node, ".actors-list-item-footer .-grow")),
                         "rank": count(text(node, ".actors-list-item-footer span:last-child"))})
    return rows


def extractor_channels(content, base_url=BASE_URL):
    tree = LexborHTMLParser(content)
    return [{"url": link(anchor, base_url), "name": anchor.attributes.get("title"),
             "thumbnail_url": image(anchor.parent, base_url),
             "rank": count(text(anchor.parent, ".channels-item-rank")),
             "total_videos_views": text(anchor.parent, ".channels-item-views")}
            for anchor in tree.css(".channels-item-title[href]")]


def extractor_categories(content, base_url=BASE_URL):
    tree = LexborHTMLParser(content)
    return [{"url": link(node, base_url), "name": node.attributes.get("title"),
             "thumbnail_url": image(node, base_url),
             "total_videos": count(text(node, ".-ctlc-item-under-heading"))}
            for node in tree.css("a.-ctlc-item[href]")]


def extract_video(content, url):
    tree = LexborHTMLParser(content)
    data = next((n for n in json_graph(tree) if n.get("@type") == "VideoObject"), {})
    title = data.get("name") or text(tree, "h1.-heading")
    if not title:
        raise ValueError(f"Video metadata not found at {url}")
    author = data.get("producer") or data.get("publisher") or {}
    stars = [{"name": n.get("name"), "url": n.get("url")} for n in data.get("actor", [])]
    seconds = duration_seconds(data.get("duration"))
    categories = [{"name": n.text(strip=True), "url": link(n, url)}
                  for n in tree.css('.-pc-item[href^="/category/"]')]
    tag_nodes = tree.css('.-pc-item[href^="/tag/"]')
    stats = [n.text(strip=True) for n in tree.css(".vpa-stats-grid > div") if n.text(strip=True)]
    views = data.get("interactionStatistic", {}).get("userInteractionCount")
    likes = next((s.split()[0] for s in stats if "Likes" in s), None)
    return {
        "video_id": urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1],
        "title": html.unescape(title), "description": html.unescape(data.get("description") or
            attr(tree, 'meta[name="description"]', "content") or title),
        "thumbnail_url": data.get("thumbnailUrl"), "publish_date": data.get("uploadDate"),
        "publish_date_relative": stats[-1] if stats else None,
        # JSON-LD contentUrl is the watch page, not a direct media URL.
        "content_url": data.get("contentUrl") or url,
        "duration": seconds, "length": format_length(seconds),
        "author_name": author.get("name"), "author_link": author.get("url"),
        "pornstars": stars, "pornstars_urls": [n["url"] for n in stars if n["url"]],
        "categories": categories, "tags": [n.text(strip=True) for n in tag_nodes],
        "tags_urls": [link(n, url) for n in tag_nodes],
        "views": str(views) if views is not None else None, "likes": likes,
        "dislikes": None, "rating_votes": None, "rating_percent": None,
        "video_quality": data.get("videoQuality"), "is_hd": data.get("videoQuality") == "HD",
        "is_vr": None, "is_exclusive": None,
        "license_url": data.get("license"),
        "preview_video_url": None, "direct_download_urls": {}, "video_qualities": [],
        "m3u8_base_url": None, "hls_download_urls": {}, "internal_video_id": None,
    }


def extract_player(data, url):
    if not data.get("success"):
        raise ValueError(f"Player service failed for {url}: {data.get('error')}")
    payload = data["payload"]
    player, video = payload.get("player"), payload.get("video")
    if not player or not video or urlsplit(urljoin(url, player["link"])).path != urlsplit(url).path:
        raise ValueError(f"Player service returned no matching video for {url}")
    sources = player.get("sources", {})
    mp4 = {int(s["height"]): urljoin(url, s["link"]) for s in sources.get("mp4", [])
           if s.get("type") == "video" and s.get("link") and s.get("height")}
    hls = {int(s["height"]): urljoin(url, s["link"]) for s in sources.get("hls", [])
           if s.get("type") == "video" and s.get("link") and s.get("height")}
    likes, dislikes = video.get("likes", {}).get("counter"), video.get("dislikes", {}).get("counter")
    votes = likes + dislikes if likes is not None and dislikes is not None else None
    return {
        "internal_video_id": str(video["id"]),
        "preview_video_url": player.get("preview", {}).get("url"),
        "thumbnail_url": player.get("poster"), "direct_download_urls": mp4,
        "video_qualities": sorted(mp4), "hls_download_urls": hls,
        "m3u8_base_url": hls[max(hls)] if hls else None,
        "likes": str(likes) if likes is not None else None,
        "dislikes": str(dislikes) if dislikes is not None else None,
        "rating_votes": str(votes) if votes is not None else None,
        "rating_percent": f"{100 * likes / votes:.1f}" if votes else None,
    }


def extract_profile(content, kind):
    tree = LexborHTMLParser(content)
    data = next((n for n in json_graph(tree) if n.get("@type") == kind), {})
    if not data.get("name"):
        raise ValueError(f"{kind} metadata not found")
    description = data.get("description") or ""
    result = {
        "name": data["name"], "description": LexborHTMLParser(description).text(separator=" ", strip=True),
        "thumbnail_url": data.get("image") or data.get("logo"),
        "external_urls": data.get("sameAs", []), **pagination(tree),
    }
    if kind == "Person":
        # Labels observed on the site's English, German, French, Spanish,
        # Italian and Brazilian Portuguese pages.
        labels = {
            "age": ("Age", "Das Alter", "Âge", "Años", "Età", "Era"),
            "country": ("Birth Place", "Geburtsort", "Lieu de naissance", "Lugar de nacimiento",
                        "Luogo di nascita", "Local de nascimento"),
            "measurements": ("Measurements", "Maße", "Mesures", "Medidas", "Dimensioni"),
            "ethnicity": ("Ethnicity", "Ethnizität", "Ethnicité", "Etnicidad", "Etnia"),
        }
        fields = {label.casefold(): name for name, aliases in labels.items() for label in aliases}
        info = {fields.get((text(n, ".-mhi-strong") or "").casefold()): text(n, ".-mhi-text")
                for n in tree.css(".-mhi-item")}
        result.update({"rank": count(text(tree, ".-mhm-info-strong")),
                       "age": count(info.get("age")), "country": info.get("country"),
                       "measurements": info.get("measurements"), "ethnicity": info.get("ethnicity"),
                       "birth_date": data.get("birthDate"), "birth_place": data.get("nationality"),
                       "gender": data.get("gender")})
    else:
        result.update({"rank": count(text(tree, ".-stats-rank .-first")),
                       "total_videos_views": text(tree, ".-stats-views .-first"),
                       "website": next(iter(data.get("sameAs", [])), None)})
    return result
