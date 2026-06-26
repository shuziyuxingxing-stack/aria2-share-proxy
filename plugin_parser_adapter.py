import asyncio
import http.cookiejar
import json
import random
import re
import sys
import time
from itertools import chain
from pathlib import Path
from typing import Any, Dict

import requests
from bilibili_api import Credential, request_settings, select_client
from bilibili_api.utils.parse_link import ResourceType, parse_link
from bilibili_api.video import VideoDownloadURLDataDetecter
from bs4 import BeautifulSoup, Tag

requests.packages.urllib3.disable_warnings()
select_client("curl_cffi")
request_settings.set("impersonate", "chrome131")

BILIBILI_DOWNLOAD_UA = "Mozilla/5.0"
B23_HOSTS = ('b23.tv', 'bili2233.cn')
DOUYIN_IOS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://www.douyin.com/",
}
DOUYIN_ANDROID_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": "https://www.iesdouyin.com/",
}
KUAISHOU_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://v.kuaishou.com/",
}
XHS_DESKTOP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
}
XHS_MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1 Edg/132.0.0.0"
    ),
    "Origin": "https://www.xiaohongshu.com",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}
XHS_DOWNLOAD_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TWITTER_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://xdown.app",
    "Referer": "https://xdown.app/",
    "User-Agent": "Mozilla/5.0",
}
TWITTER_DOWNLOAD_UA = "Mozilla/5.0"

DOUYIN_SHORT_RE = re.compile(r"https?://(?:v|jx)\.douyin\.com/[A-Za-z0-9_\-]+", re.I)
DOUYIN_VIDEO_RE = re.compile(r"douyin\.com/(?P<ty>video|note)/(?P<vid>\d+)", re.I)
DOUYIN_SHARE_RE = re.compile(r"(?:iesdouyin\.com|m\.douyin\.com)/share/(?P<ty>slides|video|note)/(?P<vid>\d+)", re.I)
DOUYIN_JINGXUAN_RE = re.compile(r"jingxuan\.douyin\.com/m/(?P<ty>slides|video|note)/(?P<vid>\d+)", re.I)
DOUYIN_ROUTER_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", re.S)
KUAISHOU_INIT_RE = re.compile(r"window\.INIT_STATE\s*=\s*(.*?)</script>", re.S)
XHS_SHORT_RE = re.compile(r"https?://(?:www\.)?xhslink\.com/[A-Za-z0-9._?%&+=/#@-]+", re.I)
XHS_NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item)/(?P<id>[0-9A-Za-z]+)", re.I)
XHS_INITIAL_STATE_RE = re.compile(r"window\.__INITIAL_STATE__=(.*?)</script>", re.S)


def parse_cookie_header(raw: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            result[name] = value
    return result


def load_cookies(path: str) -> Dict[str, str]:
    if not path:
        return {}
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return {}
    if not raw:
        return {}
    if "\n" not in raw and ";" in raw:
        return parse_cookie_header(raw)
    jar = http.cookiejar.MozillaCookieJar()
    jar.load(path, ignore_discard=True, ignore_expires=True)
    return {cookie.name: cookie.value for cookie in jar}


def sanitize_filename(value: str) -> str:
    bad = '<>:"/\\|?*'
    text = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in (value or "").strip())
    return text.rstrip(". ")[:180]


def first_url(urls: Any) -> str:
    if not isinstance(urls, list):
        return ""
    for item in urls:
        if isinstance(item, str) and item:
            return item
    return ""


def build_credential(cookies: Dict[str, str]) -> Credential:
    if not cookies:
        return Credential()
    try:
        return Credential.from_cookies(cookies)
    except Exception:
        return Credential(sessdata=cookies.get("SESSDATA"))


def normalize_bilibili_url(url: str) -> str:
    lowered = url.lower()
    if not any(host in lowered for host in B23_HOSTS):
        return url

    try:
        response = requests.get(
            url,
            headers={
                'User-Agent': BILIBILI_DOWNLOAD_UA,
                'Referer': 'https://www.bilibili.com/',
            },
            allow_redirects=True,
            timeout=15,
        )
    except Exception as exc:
        raise RuntimeError(f"b23 短链展开失败: {exc}") from exc

    final_url = response.url or url
    final_lowered = final_url.lower()
    if any(host in final_lowered for host in B23_HOSTS):
        text = (response.text or "").strip()
        if '"code":-404' in text or '"message":"啥都木有"' in text:
            raise RuntimeError("b23 短链无效或内容不存在")
        raise RuntimeError("b23 短链未能跳转到有效的 B 站视频页面")
    return final_url


async def resolve_bilibili(url: str, cookies_file: str) -> Dict[str, Any]:
    url = normalize_bilibili_url(url)
    cookies = load_cookies(cookies_file)
    credential = build_credential(cookies)
    obj, resource_type = await parse_link(url, credential=credential)
    if resource_type != ResourceType.VIDEO:
        raise RuntimeError(f"plugin_parser 当前仅支持 B站视频链接，不支持资源类型: {resource_type}")

    info = await obj.get_info()
    title = str(info.get("title") or "")

    download_data = await obj.get_download_url(page_index=0, html5=True)
    detector = VideoDownloadURLDataDetecter(download_data)
    streams = detector.detect_all()
    chosen_url = ""
    for stream in streams:
        candidate = getattr(stream, "url", "")
        if isinstance(candidate, str) and candidate:
            chosen_url = candidate
            break
    if not chosen_url:
        raise RuntimeError("plugin_parser B站适配未拿到 aria2 可用的单一直链")

    return {
        "url": chosen_url,
        "filename": sanitize_filename(f"{title}.mp4" if title else ""),
        "headers": [
            "Referer: https://www.bilibili.com/",
            f"User-Agent: {BILIBILI_DOWNLOAD_UA}",
        ],
        "extractor": "plugin_parser_bilibili_html5_adapter",
        "title": title,
    }


def resolve_douyin_short(url: str, session: requests.Session) -> str:
    response = session.get(url, headers=DOUYIN_IOS_HEADERS, allow_redirects=False, timeout=15, verify=False)
    if response.is_redirect or response.is_permanent_redirect:
        location = response.headers.get("Location", "")
        if location:
            return location
    raise RuntimeError(f"无法解析抖音短链: {url}")


def extract_douyin_identity(url: str, session: requests.Session) -> tuple[str, str]:
    current = url
    match = DOUYIN_SHORT_RE.search(url)
    if match:
        current = resolve_douyin_short(match.group(0), session)

    for pattern in (DOUYIN_VIDEO_RE, DOUYIN_SHARE_RE, DOUYIN_JINGXUAN_RE):
        found = pattern.search(current)
        if found:
            return found.group("ty").lower(), found.group("vid")
    raise RuntimeError("plugin_parser 抖音适配无法识别该链接")


def build_douyin_video_urls(ty: str, vid: str) -> list[str]:
    if ty == "slides":
        return [f"https://www.iesdouyin.com/share/slides/{vid}"]
    return [
        f"https://m.douyin.com/share/{ty}/{vid}",
        f"https://www.iesdouyin.com/share/{ty}/{vid}",
    ]


def parse_douyin_router_data(text: str) -> Dict[str, Any]:
    matched = DOUYIN_ROUTER_RE.search(text)
    if not matched:
        raise RuntimeError("plugin_parser 抖音适配未找到 window._ROUTER_DATA")
    return json.loads(matched.group(1).strip())


def pick_douyin_item(router_data: Dict[str, Any]) -> Dict[str, Any]:
    loader_data = router_data.get("loaderData") or {}
    for key in ("video_(id)/page", "note_(id)/page"):
        page = loader_data.get(key)
        item_list = (((page or {}).get("videoInfoRes") or {}).get("item_list")) or []
        if isinstance(item_list, list) and item_list:
            first = item_list[0]
            if isinstance(first, dict):
                return first
    raise RuntimeError("plugin_parser 抖音适配未找到视频数据")


def build_douyin_result(item: Dict[str, Any], extractor: str) -> Dict[str, Any]:
    title = str(item.get("desc") or "")
    author = ((item.get("author") or {}).get("nickname")) or ""
    timestamp = int(item.get("create_time") or time.time())

    images = item.get("images") if isinstance(item.get("images"), list) else []
    if images:
        first = images[0] if isinstance(images[0], dict) else {}
        image_url = first_url(first.get("url_list"))
        if image_url:
            return {
                "url": image_url,
                "filename": sanitize_filename(f"{title or author or timestamp}.jpg"),
                "headers": [
                    "Referer: https://www.douyin.com/",
                    f"User-Agent: {DOUYIN_ANDROID_HEADERS['User-Agent']}",
                ],
                "extractor": extractor,
                "title": title,
            }

    video = item.get("video") if isinstance(item.get("video"), dict) else None
    if video:
        play_addr = video.get("play_addr") if isinstance(video.get("play_addr"), dict) else {}
        media_url = first_url(play_addr.get("url_list"))
        if media_url:
            media_url = media_url.replace("playwm", "play")
            return {
                "url": media_url,
                "filename": sanitize_filename(f"{title or author or timestamp}.mp4"),
                "headers": [
                    "Referer: https://www.douyin.com/",
                    f"User-Agent: {DOUYIN_IOS_HEADERS['User-Agent']}",
                ],
                "extractor": extractor,
                "title": title,
            }

    raise RuntimeError("plugin_parser 抖音适配未找到 aria2 可下载直链")


def resolve_douyin(url: str, cookies_file: str) -> Dict[str, Any]:
    session = requests.Session()
    session.cookies.update(load_cookies(cookies_file))
    ty, vid = extract_douyin_identity(url, session)

    if ty == "slides":
        response = session.get(
            "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/",
            params={"aweme_ids": f"[{vid}]", "request_source": "200"},
            headers=DOUYIN_ANDROID_HEADERS,
            timeout=20,
            verify=False,
        )
        response.raise_for_status()
        payload = response.json()
        details = payload.get("aweme_details") or []
        if not details or not isinstance(details[0], dict):
            raise RuntimeError("plugin_parser 抖音幻灯片适配未返回有效详情")
        return build_douyin_result(details[0], "plugin_parser_douyin_slides")

    last_error: Exception | None = None
    for candidate in build_douyin_video_urls(ty, vid):
        try:
            response = session.get(candidate, headers=DOUYIN_IOS_HEADERS, timeout=20, verify=False)
            if response.status_code != 200:
                raise RuntimeError(f"status={response.status_code}")
            return build_douyin_result(
                pick_douyin_item(parse_douyin_router_data(response.text)),
                "plugin_parser_douyin_router",
            )
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "plugin_parser 抖音适配失败"))


def resolve_kuaishou(url: str, cookies_file: str) -> Dict[str, Any]:
    session = requests.Session()
    session.cookies.update(load_cookies(cookies_file))

    redirected = session.get(url, headers=KUAISHOU_HEADERS, allow_redirects=True, timeout=20, verify=False).url
    real_url = redirected.replace("/fw/long-video/", "/fw/photo/")
    response = session.get(real_url, headers=KUAISHOU_HEADERS, timeout=20, verify=False)
    response.raise_for_status()

    matched = KUAISHOU_INIT_RE.search(response.text)
    if not matched:
        raise RuntimeError("plugin_parser 快手适配未找到 INIT_STATE")
    init_state = json.loads(matched.group(1).strip())

    photo: Dict[str, Any] | None = None
    for value in init_state.values():
        if isinstance(value, dict) and isinstance(value.get("photo"), dict):
            photo = value["photo"]
            break
    if not photo:
        raise RuntimeError("plugin_parser 快手适配未找到 photo 数据")

    title = str(photo.get("caption") or "")
    timestamp = int(photo.get("timestamp") or time.time() * 1000)
    video_urls = photo.get("mainMvUrls") if isinstance(photo.get("mainMvUrls"), list) else []
    if video_urls:
        choices = [item.get("url") for item in video_urls if isinstance(item, dict) and item.get("url")]
        if choices:
            return {
                "url": random.choice(choices),
                "filename": sanitize_filename(f"{title or timestamp}.mp4"),
                "headers": [
                    "Referer: https://v.kuaishou.com/",
                    f"User-Agent: {KUAISHOU_HEADERS['User-Agent']}",
                ],
                "extractor": "plugin_parser_kuaishou_video",
                "title": title,
            }

    atlas = (((photo.get("ext_params") or {}).get("atlas")) or {}) if isinstance(photo.get("ext_params"), dict) else {}
    cdn_list = atlas.get("cdnList") if isinstance(atlas.get("cdnList"), list) else []
    route_list = atlas.get("list") if isinstance(atlas.get("list"), list) else []
    cdn_hosts = [item.get("cdn") for item in cdn_list if isinstance(item, dict) and item.get("cdn")]
    if cdn_hosts and route_list:
        image_url = f"https://{random.choice(cdn_hosts)}/{route_list[0]}"
        return {
            "url": image_url,
            "filename": sanitize_filename(f"{title or timestamp}.jpg"),
            "headers": [
                "Referer: https://v.kuaishou.com/",
                f"User-Agent: {KUAISHOU_HEADERS['User-Agent']}",
            ],
            "extractor": "plugin_parser_kuaishou_image",
            "title": title,
        }

    raise RuntimeError("plugin_parser 快手适配未找到 aria2 可下载直链")


def ensure_url(url: str) -> str:
    return url if url.startswith("http") else "https://" + url


def resolve_xhs_short(url: str, session: requests.Session) -> str:
    response = session.get(url, headers=XHS_MOBILE_HEADERS, allow_redirects=False, timeout=15, verify=False)
    if response.is_redirect or response.is_permanent_redirect:
        location = response.headers.get("Location", "")
        if location:
            return location
    raise RuntimeError("plugin_parser 小红书适配无法解析短链")


def normalize_xhs_source(url: str, session: requests.Session) -> str:
    if XHS_SHORT_RE.search(url):
        return resolve_xhs_short(url, session)
    return url


def extract_xhs_note_id(url: str) -> str:
    matched = XHS_NOTE_ID_RE.search(url)
    if not matched:
        raise RuntimeError("plugin_parser 小红书适配无法提取 note id")
    return matched.group("id")


def parse_xhs_initial_state(html: str) -> Dict[str, Any]:
    matched = XHS_INITIAL_STATE_RE.search(html)
    if not matched:
        raise RuntimeError("plugin_parser 小红书适配未找到 INITIAL_STATE")
    return json.loads(matched.group(1).replace("undefined", "null"))


def extract_xhs_file_id(img: Dict[str, Any]) -> str | None:
    file_id = img.get("fileId") or img.get("file_id") or img.get("traceId")
    if isinstance(file_id, str) and file_id:
        return file_id.split("!", 1)[0]
    for key in ("urlDefault", "url", "urlPre"):
        value = img.get(key)
        if not isinstance(value, str) or not value:
            continue
        base = value.split("?", 1)[0].split("!", 1)[0].rsplit("/", 1)[-1]
        if len(base) > 20 and re.match(r"^[a-zA-Z0-9]+$", base):
            return base
    return None


def extract_xhs_video_url(note: Dict[str, Any]) -> str:
    video = note.get("video") if isinstance(note.get("video"), dict) else None
    media = video.get("media") if isinstance(video, dict) else None
    stream = media.get("stream") if isinstance(media, dict) else None
    if not isinstance(stream, dict):
        return ""
    for codec in ("h265", "h264", "av1", "h266"):
        streams = stream.get(codec)
        if isinstance(streams, list) and streams:
            first = streams[0] if isinstance(streams[0], dict) else {}
            master = first.get("masterUrl")
            if isinstance(master, str) and master:
                return master
    return ""


def build_xhs_result(note: Dict[str, Any], extractor: str) -> Dict[str, Any]:
    title = str(note.get("title") or note.get("shareTitle") or "")
    desc = str(note.get("desc") or note.get("shareDesc") or "")
    user = note.get("user") or note.get("author") or {}
    author = ""
    if isinstance(user, dict):
        author = str(user.get("nickname") or user.get("nickName") or user.get("name") or "")

    video_url = extract_xhs_video_url(note)
    if video_url:
        return {
            "url": video_url,
            "filename": sanitize_filename(f"{title or author or int(time.time())}.mp4"),
            "headers": [
                "Referer: https://www.xiaohongshu.com/",
                f"User-Agent: {XHS_DOWNLOAD_UA}",
            ],
            "extractor": extractor,
            "title": title or desc,
        }

    image_list = note.get("imageList") if isinstance(note.get("imageList"), list) else []
    if image_list:
        first = image_list[0] if isinstance(image_list[0], dict) else {}
        file_id = extract_xhs_file_id(first)
        image_url = f"https://ci.xiaohongshu.com/{file_id}?imageView2/format/png" if file_id else str(first.get("urlDefault") or first.get("url") or "")
        if image_url:
            return {
                "url": image_url.split("!", 1)[0],
                "filename": sanitize_filename(f"{title or author or int(time.time())}.jpg"),
                "headers": [
                    "Referer: https://www.xiaohongshu.com/",
                    f"User-Agent: {XHS_DOWNLOAD_UA}",
                ],
                "extractor": extractor,
                "title": title or desc,
            }

    raise RuntimeError("plugin_parser 小红书适配未找到 aria2 可下载直链")


def resolve_xhs(url: str, cookies_file: str) -> Dict[str, Any]:
    session = requests.Session()
    session.cookies.update(load_cookies(cookies_file))
    source_url = normalize_xhs_source(ensure_url(url), session)
    note_id = extract_xhs_note_id(source_url)
    query = source_url.split("?", 1)[1] if "?" in source_url else ""

    explore_url = f"https://www.xiaohongshu.com/explore/{note_id}"
    if query:
        explore_url += "?" + query

    try:
        html = session.get(explore_url, headers=XHS_DESKTOP_HEADERS, timeout=20, verify=False).text
        state = parse_xhs_initial_state(html)
        note = (((state.get("note") or {}).get("noteDetailMap") or {}).get(note_id) or {}).get("note") or {}
        if isinstance(note, dict) and note:
            return build_xhs_result(note, "plugin_parser_xiaohongshu_explore")
    except Exception:
        pass

    discovery_url = source_url if "/discovery/item/" in source_url else f"https://www.xiaohongshu.com/discovery/item/{note_id}"
    if query and "?" not in discovery_url:
        discovery_url += "?" + query
    html = session.get(discovery_url, headers=XHS_MOBILE_HEADERS, timeout=20, verify=False).text
    state = parse_xhs_initial_state(html)
    note_data = state.get("noteData") if isinstance(state.get("noteData"), dict) else {}
    note = (((note_data.get("data") or {}).get("noteData")) or {}) if isinstance(note_data, dict) else {}
    if not isinstance(note, dict) or not note:
        raise RuntimeError("plugin_parser 小红书适配未找到 noteData")
    return build_xhs_result(note, "plugin_parser_xiaohongshu_discovery")


def resolve_twitter(url: str, cookies_file: str) -> Dict[str, Any]:
    session = requests.Session()
    cookies = load_cookies(cookies_file)
    if cookies:
        session.cookies.update(cookies)

    response = session.post(
        "https://xdown.app/api/ajaxSearch",
        data={"q": url, "lang": "zh-cn"},
        headers=TWITTER_API_HEADERS,
        timeout=20,
        verify=False,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise RuntimeError("plugin_parser X/Twitter 适配解析失败")

    html_content = payload.get("data")
    if not isinstance(html_content, str) or not html_content.strip():
        raise RuntimeError("plugin_parser X/Twitter 适配未返回有效 HTML")

    soup = BeautifulSoup(html_content, "html.parser")
    title_tag = soup.find("h3")
    title = title_tag.get_text(strip=True) if isinstance(title_tag, Tag) else ""

    cover_url = ""
    thumb_tag = soup.find("img")
    if isinstance(thumb_tag, Tag):
        src = thumb_tag.get("src")
        if isinstance(src, str):
            cover_url = src

    video_url = ""
    image_urls: list[str] = []
    dynamic_urls: list[str] = []
    for tag in chain(soup.find_all("a", class_="tw-button-dl"), soup.find_all("a", class_="abutton")):
        if not isinstance(tag, Tag):
            continue
        href = tag.get("href")
        if not isinstance(href, str) or not href:
            continue
        text = tag.get_text(strip=True)
        if "下载 MP4" in text:
            video_url = href
            break
        if "下载图片" in text:
            image_urls.append(href)
        elif "下载 gif" in text:
            dynamic_urls.append(href)

    if video_url:
        return {
            "url": video_url,
            "filename": sanitize_filename(f"{title or int(time.time())}.mp4"),
            "headers": [
                "Referer: https://xdown.app/",
                f"User-Agent: {TWITTER_DOWNLOAD_UA}",
            ],
            "extractor": "plugin_parser_twitter_xdown_video",
            "title": title,
        }

    if image_urls:
        return {
            "url": image_urls[0],
            "filename": sanitize_filename(f"{title or int(time.time())}.jpg"),
            "headers": [
                "Referer: https://xdown.app/",
                f"User-Agent: {TWITTER_DOWNLOAD_UA}",
            ],
            "extractor": "plugin_parser_twitter_xdown_image",
            "title": title,
        }

    if dynamic_urls:
        ext = ".mp4" if dynamic_urls[0].lower().endswith(".mp4") else ".gif"
        return {
            "url": dynamic_urls[0],
            "filename": sanitize_filename(f"{title or int(time.time())}{ext}"),
            "headers": [
                "Referer: https://xdown.app/",
                f"User-Agent: {TWITTER_DOWNLOAD_UA}",
            ],
            "extractor": "plugin_parser_twitter_xdown_dynamic",
            "title": title,
        }

    if cover_url:
        return {
            "url": cover_url,
            "filename": sanitize_filename(f"{title or int(time.time())}.jpg"),
            "headers": [
                "Referer: https://xdown.app/",
                f"User-Agent: {TWITTER_DOWNLOAD_UA}",
            ],
            "extractor": "plugin_parser_twitter_xdown_cover",
            "title": title,
        }

    raise RuntimeError("plugin_parser X/Twitter 适配未找到 aria2 可下载直链")


def resolve(platform: str, url: str, cookies_file: str) -> Dict[str, Any]:
    platform = (platform or "").strip().lower()
    if platform == "bilibili":
        return asyncio.run(resolve_bilibili(url, cookies_file))
    if platform == "douyin":
        return resolve_douyin(url, cookies_file)
    if platform == "kuaishou":
        return resolve_kuaishou(url, cookies_file)
    if platform == "xiaohongshu":
        return resolve_xhs(url, cookies_file)
    if platform == "twitter":
        return resolve_twitter(url, cookies_file)
    raise RuntimeError("plugin_parser 当前已接入 B站 / 抖音 / 快手 / 小红书 / X(Twitter)；其他平台请改用 yt-dlp")


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: plugin_parser_adapter.py <platform> <url> [cookies_file]")
    platform = sys.argv[1]
    url = sys.argv[2]
    cookies_file = sys.argv[3] if len(sys.argv) > 3 else ""
    result = resolve(platform, url, cookies_file)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
