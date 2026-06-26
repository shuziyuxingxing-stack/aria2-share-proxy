import json
import random
import re
import sys
import time
import http.cookiejar
from pathlib import Path
from typing import Dict

import requests
requests.packages.urllib3.disable_warnings()


HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) '
        'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
    ),
    'Referer': 'https://v.kuaishou.com/',
}
INIT_STATE_PAT = re.compile(r'window\.INIT_STATE\s*=\s*(.*?)</script>', re.S)


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


def choose_url(items, key='url') -> str:
    if not isinstance(items, list) or not items:
        return ''
    candidates = [item.get(key, '') for item in items if isinstance(item, dict) and item.get(key)]
    return random.choice(candidates) if candidates else ''


def get_redirect_url(url: str, session: requests.Session) -> str:
    response = session.get(url, headers=HEADERS, allow_redirects=True, timeout=20, verify=False)
    return response.url


def parse_init_state(text: str) -> Dict[str, object]:
    matched = INIT_STATE_PAT.search(text)
    if not matched:
        raise RuntimeError('failed to parse kuaishou INIT_STATE')
    return json.loads(matched.group(1).strip())


def pick_photo(init_state: Dict[str, object]) -> Dict[str, object]:
    for value in init_state.values():
        if isinstance(value, dict) and isinstance(value.get('photo'), dict):
            return value['photo']
    raise RuntimeError('kuaishou page does not contain photo payload')


def build_image_urls(photo: Dict[str, object]) -> list[str]:
    ext_params = photo.get('ext_params') if isinstance(photo.get('ext_params'), dict) else {}
    atlas = ext_params.get('atlas') if isinstance(ext_params, dict) else {}
    cdn_list = atlas.get('cdnList') if isinstance(atlas, dict) else []
    route_list = atlas.get('list') if isinstance(atlas, dict) else []
    if not isinstance(cdn_list, list) or not isinstance(route_list, list) or not cdn_list or not route_list:
        return []
    cdn_hosts = [item.get('cdn', '') for item in cdn_list if isinstance(item, dict) and item.get('cdn')]
    if not cdn_hosts:
        return []
    cdn = random.choice(cdn_hosts)
    return [f'https://{cdn}/{route}' for route in route_list if isinstance(route, str) and route]


def resolve(url: str, cookies_file: str) -> Dict[str, object]:
    session = requests.Session()
    session.cookies.update(load_cookies(cookies_file))

    real_url = get_redirect_url(url, session).replace('/fw/long-video/', '/fw/photo/')
    response = session.get(real_url, headers=HEADERS, timeout=20, verify=False)
    response.raise_for_status()
    photo = pick_photo(parse_init_state(response.text))

    title = str(photo.get('caption') or '')
    timestamp = int(photo.get('timestamp') or time.time() * 1000)

    video_url = choose_url(photo.get('mainMvUrls'))
    if video_url:
        return {
            'url': video_url,
            'filename': sanitize_filename(f'{title or timestamp}.mp4'),
            'headers': [
                'Referer: https://v.kuaishou.com/',
                f'User-Agent: {HEADERS["User-Agent"]}',
            ],
            'extractor': 'kuaishou_init_state',
            'title': title,
        }

    image_urls = build_image_urls(photo)
    if image_urls:
        return {
            'url': image_urls[0],
            'filename': sanitize_filename(f'{title or timestamp}.jpg'),
            'headers': [
                'Referer: https://v.kuaishou.com/',
                f'User-Agent: {HEADERS["User-Agent"]}',
            ],
            'extractor': 'kuaishou_init_state',
            'title': title,
        }

    raise RuntimeError('kuaishou resolver found no aria2-friendly direct url')


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('usage: kuaishou_resolver.py <url> [cookies_file]')
    url = sys.argv[1]
    cookies_file = sys.argv[2] if len(sys.argv) > 2 else ''
    result = resolve(url, cookies_file)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
