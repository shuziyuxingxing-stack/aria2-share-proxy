import json
import re
import sys
import time
import http.cookiejar
from pathlib import Path
from typing import Dict, List

import requests
requests.packages.urllib3.disable_warnings()


IOS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://www.douyin.com/",
}
ANDROID_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": "https://www.iesdouyin.com/",
}
SHORT_PAT = re.compile(r'https?://(?:v|jx)\.douyin\.com/[A-Za-z0-9_\-]+', re.I)
VIDEO_PAT = re.compile(r'douyin\.com/(?P<ty>video|note)/(?P<vid>\d+)', re.I)
SHARE_PAT = re.compile(r'(?:iesdouyin\.com|m\.douyin\.com)/share/(?P<ty>slides|video|note)/(?P<vid>\d+)', re.I)
JINGXUAN_PAT = re.compile(r'jingxuan\.douyin.com/m/(?P<ty>slides|video|note)/(?P<vid>\d+)', re.I)
ROUTER_PAT = re.compile(r'window\._ROUTER_DATA\s*=\s*(.*?)</script>', re.S)


def parse_cookie_header(raw: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for part in raw.split(';'):
        if '=' not in part:
            continue
        name, value = part.split('=', 1)
        name = name.strip()
        value = value.strip()
        if name:
            result[name] = value
    return result


def load_cookies(path: str) -> Dict[str, str]:
    if not path:
        return {}
    try:
        raw = Path(path).read_text(encoding='utf-8').strip()
    except Exception:
        return {}
    if not raw:
        return {}
    if '\n' not in raw and ';' in raw:
        return parse_cookie_header(raw)
    jar = http.cookiejar.MozillaCookieJar()
    jar.load(path, ignore_discard=True, ignore_expires=True)
    return {cookie.name: cookie.value for cookie in jar}


def sanitize_filename(value: str) -> str:
    bad = '<>:"/\\|?*'
    text = ''.join('_' if ch in bad or ord(ch) < 32 else ch for ch in (value or '').strip())
    return text.rstrip('. ')[:180]


def first_url(urls):
    if not isinstance(urls, list):
        return ''
    for item in urls:
        if isinstance(item, str) and item:
            return item
    return ''


def get_nested(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def resolve_short(url: str, session: requests.Session) -> str:
    response = session.get(url, headers=IOS_HEADERS, allow_redirects=False, timeout=15, verify=False)
    if response.is_redirect or response.is_permanent_redirect:
        location = response.headers.get('Location', '')
        if location:
            return location
    raise RuntimeError(f'cannot resolve douyin short url: {url}')


def extract_identity(url: str, session: requests.Session):
    current = url
    match = SHORT_PAT.search(url)
    if match:
        current = resolve_short(match.group(0), session)

    for pattern in (VIDEO_PAT, SHARE_PAT, JINGXUAN_PAT):
        matched = pattern.search(current)
        if matched:
            return matched.group('ty').lower(), matched.group('vid')
    raise RuntimeError('unsupported douyin url')


def build_candidate_urls(ty: str, vid: str) -> List[str]:
    if ty == 'slides':
        return [f'https://www.iesdouyin.com/share/slides/{vid}']
    return [
        f'https://m.douyin.com/share/{ty}/{vid}',
        f'https://www.iesdouyin.com/share/{ty}/{vid}',
    ]


def parse_router_payload(text: str) -> Dict[str, object]:
    matched = ROUTER_PAT.search(text)
    if not matched:
        raise RuntimeError('cannot find window._ROUTER_DATA')
    return json.loads(matched.group(1).strip())


def pick_video_data(router_data: Dict[str, object]) -> Dict[str, object]:
    loader = get_nested(router_data, 'loaderData') or {}
    for key in ('video_(id)/page', 'note_(id)/page'):
        video_page = loader.get(key)
        item_list = get_nested(video_page, 'videoInfoRes', 'item_list')
        if isinstance(item_list, list) and item_list:
            first = item_list[0]
            if isinstance(first, dict):
                return first
    raise RuntimeError('cannot find douyin video data')


def parse_video_page(url: str, session: requests.Session) -> Dict[str, object]:
    response = session.get(url, headers=IOS_HEADERS, timeout=20, verify=False)
    if response.status_code != 200:
        raise RuntimeError(f'douyin page status {response.status_code}')
    router_data = parse_router_payload(response.text)
    return pick_video_data(router_data)


def parse_slides(vid: str, session: requests.Session) -> Dict[str, object]:
    response = session.get(
        'https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/',
        params={'aweme_ids': f'[{vid}]', 'request_source': '200'},
        headers=ANDROID_HEADERS,
        timeout=20,
        verify=False,
    )
    response.raise_for_status()
    payload = response.json()
    details = payload.get('aweme_details') or []
    if not details:
        raise RuntimeError('douyin slides api returned no details')
    first = details[0]
    if not isinstance(first, dict):
        raise RuntimeError('douyin slides api returned invalid detail')
    return first


def build_result(data: Dict[str, object], extractor: str) -> Dict[str, object]:
    title = str(data.get('desc') or '')
    author = get_nested(data, 'author', 'nickname') or ''
    timestamp = int(data.get('create_time') or time.time())

    video = data.get('video') if isinstance(data.get('video'), dict) else None
    if video:
        play_addr = video.get('play_addr') if isinstance(video.get('play_addr'), dict) else {}
        media_url = first_url(play_addr.get('url_list'))
        if media_url:
            media_url = media_url.replace('playwm', 'play')
            return {
                'url': media_url,
                'filename': sanitize_filename(f'{title or author or timestamp}.mp4'),
                'headers': [
                    'Referer: https://www.douyin.com/',
                    f'User-Agent: {IOS_HEADERS["User-Agent"]}',
                ],
                'extractor': extractor,
                'title': title,
            }

    images = data.get('images')
    if isinstance(images, list) and images:
        first_image = images[0] if isinstance(images[0], dict) else {}
        image_url = first_url(first_image.get('url_list'))
        if image_url:
            return {
                'url': image_url,
                'filename': sanitize_filename(f'{title or author or timestamp}.jpg'),
                'headers': [
                    'Referer: https://www.douyin.com/',
                    f'User-Agent: {ANDROID_HEADERS["User-Agent"]}',
                ],
                'extractor': extractor,
                'title': title,
            }

    raise RuntimeError('douyin resolver found no aria2-friendly direct url')


def resolve(url: str, cookies_file: str) -> Dict[str, object]:
    session = requests.Session()
    session.cookies.update(load_cookies(cookies_file))
    ty, vid = extract_identity(url, session)

    if ty == 'slides':
        data = parse_slides(vid, session)
        return build_result(data, 'douyin_slides_api')

    last_error = None
    for candidate in build_candidate_urls(ty, vid):
        try:
            data = parse_video_page(candidate, session)
            return build_result(data, 'douyin_router_data')
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(str(last_error or 'douyin resolve failed'))


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('usage: douyin_resolver.py <url> [cookies_file]')
    url = sys.argv[1]
    cookies_file = sys.argv[2] if len(sys.argv) > 2 else ''
    result = resolve(url, cookies_file)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
