import asyncio
import http.cookiejar
import json
import os
import sys
from typing import Dict

from bilibili_api import Credential
from bilibili_api.utils.parse_link import ResourceType, parse_link
from bilibili_api.video import VideoDownloadURLDataDetecter
import requests


B23_HOSTS = ('b23.tv', 'bili2233.cn')
BILIBILI_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://www.bilibili.com/',
}


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
    if not path or not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read().strip()
    if not raw:
        return {}

    if "\n" not in raw and ";" in raw:
        return parse_cookie_header(raw)

    jar = http.cookiejar.MozillaCookieJar()
    jar.load(path, ignore_discard=True, ignore_expires=True)
    result: Dict[str, str] = {}
    for cookie in jar:
        result[cookie.name] = cookie.value
    return result


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
            headers=BILIBILI_HEADERS,
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


async def resolve(url: str, cookies_file: str) -> Dict[str, str]:
    url = normalize_bilibili_url(url)
    cookies = load_cookies(cookies_file)
    credential = build_credential(cookies)
    obj, resource_type = await parse_link(url, credential=credential)
    if resource_type != ResourceType.VIDEO:
        raise RuntimeError(f"unsupported bilibili resource type: {resource_type}")

    title = ""
    try:
        info = await obj.get_info()
        title = info.get("title") or ""
    except Exception:
        pass

    download_data = await obj.get_download_url(page_index=0, html5=True)
    detector = VideoDownloadURLDataDetecter(download_data)
    streams = detector.detect_all()
    if not streams:
        raise RuntimeError("bilibili_api returned no downloadable stream")

    chosen = streams[0]
    stream_url = getattr(chosen, "url", "")
    if not stream_url:
        raise RuntimeError("bilibili_api returned an empty stream url")

    headers = [
        "Referer: https://www.bilibili.com/",
        "User-Agent: Mozilla/5.0",
    ]
    filename = f"{title}.mp4" if title else ""
    return {
        "url": stream_url,
        "filename": filename,
        "headers": headers,
        "extractor": "bilibili_api_html5",
        "title": title,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: bili_resolver.py <url> [cookies_file]")
    url = sys.argv[1]
    cookies_file = sys.argv[2] if len(sys.argv) > 2 else ""
    result = asyncio.run(resolve(url, cookies_file))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
