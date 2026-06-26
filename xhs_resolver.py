import json
import re
import sys
import time
import http.cookiejar
from pathlib import Path
from typing import Dict

import requests
requests.packages.urllib3.disable_warnings()

SHORT_RE = re.compile(r'https?://(?:www\.)?xhslink\.com/[A-Za-z0-9._?%&+=/#@-]+', re.I)
LONG_RE = re.compile(r'https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/[0-9A-Za-z]+[A-Za-z0-9._%?&+=/#@-]*', re.I)
NOTE_ID_RE = re.compile(r'/(?:explore|discovery/item)/(?P<id>[0-9A-Za-z]+)', re.I)
INITIAL_STATE_RE = re.compile(r'window\.__INITIAL_STATE__=(.*?)</script>', re.S)

DESKTOP_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36'
    ),
    'Accept': (
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,'
        'image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
    ),
}
MOBILE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) '
        'AppleWebKit/605.1.15 (KHTML, like Gecko) '
        'Version/16.6 Mobile/15E148 Safari/604.1 Edg/132.0.0.0'
    ),
    'Origin': 'https://www.xiaohongshu.com',
    'X-Requested-With': 'XMLHttpRequest',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Dest': 'empty',
}
DOWNLOAD_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'


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


def ensure_url(url: str) -> str:
    return url if url.startswith('http') else 'https://' + url


def resolve_short(url: str, session: requests.Session) -> str:
    response = session.get(url, headers=MOBILE_HEADERS, allow_redirects=False, timeout=15, verify=False)
    if response.is_redirect or response.is_permanent_redirect:
        location = response.headers.get('Location', '')
        if location:
            return location
    raise RuntimeError('cannot resolve xiaohongshu short url')


def normalize_source_url(url: str, session: requests.Session) -> str:
    if SHORT_RE.search(url):
        return resolve_short(url, session)
    return url


def extract_note_id(url: str) -> str:
    matched = NOTE_ID_RE.search(url)
    if not matched:
        raise RuntimeError('cannot extract xiaohongshu note id')
    return matched.group('id')


def fetch_html(url: str, headers: Dict[str, str], session: requests.Session) -> str:
    response = session.get(url, headers=headers, allow_redirects=True, timeout=20, verify=False)
    if response.status_code != 200:
        raise RuntimeError(f'xiaohongshu page status {response.status_code}')
    return response.text


def extract_initial_state(html: str) -> Dict[str, object]:
    matched = INITIAL_STATE_RE.search(html)
    if not matched:
        raise RuntimeError('xiaohongshu initial state not found')
    raw = matched.group(1).replace('undefined', 'null')
    return json.loads(raw)


def extract_file_id(img: Dict[str, object]) -> str | None:
    file_id = img.get('fileId') or img.get('file_id') or img.get('traceId')
    if isinstance(file_id, str) and file_id:
        return file_id.split('!', 1)[0]
    for key in ('urlDefault', 'url', 'urlPre'):
        value = img.get(key)
        if not isinstance(value, str) or not value:
            continue
        if 'spectrum/' in value:
            matched = re.search(r'spectrum/([a-zA-Z0-9]+)', value)
            if matched:
                return matched.group(1)
        base = value.split('?', 1)[0].split('!', 1)[0].rsplit('/', 1)[-1]
        if len(base) > 20 and re.match(r'^[a-zA-Z0-9]+$', base):
            return base
    return None


def get_original_image_url(img: Dict[str, object]) -> str:
    value = img.get('urlDefault') or img.get('url') or ''
    if not isinstance(value, str):
        return ''
    return value.split('!', 1)[0]


def build_ci_image_url(file_id: str) -> str:
    return f'https://ci.xiaohongshu.com/{file_id}?imageView2/format/png'


def extract_video_url(note: Dict[str, object]) -> str:
    video = note.get('video') if isinstance(note.get('video'), dict) else None
    media = video.get('media') if isinstance(video, dict) else None
    stream = media.get('stream') if isinstance(media, dict) else None
    if not isinstance(stream, dict):
        return ''
    for codec in ('h265', 'h264', 'av1', 'h266'):
        streams = stream.get(codec)
        if isinstance(streams, list) and streams:
            first = streams[0] if isinstance(streams[0], dict) else {}
            master = first.get('masterUrl')
            if isinstance(master, str) and master:
                return master
    return ''


def build_result(note: Dict[str, object], source_url: str, profile: str) -> Dict[str, object]:
    title = str(note.get('title') or note.get('shareTitle') or '')
    desc = str(note.get('desc') or note.get('shareDesc') or '')
    user = note.get('user') or note.get('author') or {}
    author = ''
    if isinstance(user, dict):
        author = str(user.get('nickname') or user.get('nickName') or user.get('name') or '')

    video_url = extract_video_url(note)
    if video_url:
        name = sanitize_filename(f'{title or author or int(time.time())}.mp4')
        return {
            'url': video_url,
            'filename': name,
            'headers': [
                'Referer: https://www.xiaohongshu.com/',
                f'User-Agent: {DOWNLOAD_UA}',
            ],
            'extractor': f'{profile}_xiaohongshu_video',
            'title': title or desc,
        }

    image_list = note.get('imageList') if isinstance(note.get('imageList'), list) else []
    if image_list:
        first = image_list[0] if isinstance(image_list[0], dict) else {}
        file_id = extract_file_id(first)
        image_url = build_ci_image_url(file_id) if file_id else get_original_image_url(first)
        if image_url:
            name = sanitize_filename(f'{title or author or int(time.time())}.jpg')
            return {
                'url': image_url,
                'filename': name,
                'headers': [
                    'Referer: https://www.xiaohongshu.com/',
                    f'User-Agent: {DOWNLOAD_UA}',
                ],
                'extractor': f'{profile}_xiaohongshu_image',
                'title': title or desc,
            }

    raise RuntimeError('xiaohongshu resolver found no aria2-friendly direct url')


def parse_explore(url: str, note_id: str, session: requests.Session, profile: str) -> Dict[str, object]:
    html = fetch_html(url, DESKTOP_HEADERS, session)
    state = extract_initial_state(html)
    note = state.get('note', {}).get('noteDetailMap', {}).get(note_id, {}).get('note', {})
    if not isinstance(note, dict) or not note:
        raise RuntimeError('xiaohongshu note detail not found in explore page')
    return build_result(note, url, profile)


def parse_discovery(url: str, session: requests.Session, profile: str) -> Dict[str, object]:
    html = fetch_html(url, MOBILE_HEADERS, session)
    state = extract_initial_state(html)
    note_data = state.get('noteData')
    if not isinstance(note_data, dict):
        raise RuntimeError('xiaohongshu noteData not found')
    note = note_data.get('data', {}).get('noteData', {})
    if not isinstance(note, dict) or not note:
        raise RuntimeError('xiaohongshu discovery noteData is empty')
    return build_result(note, url, profile)


def resolve(url: str, profile: str, cookies_file: str) -> Dict[str, object]:
    session = requests.Session()
    session.cookies.update(load_cookies(cookies_file))
    source_url = normalize_source_url(ensure_url(url), session)
    note_id = extract_note_id(source_url)
    query = source_url.split('?', 1)[1] if '?' in source_url else ''
    explore_url = f'https://www.xiaohongshu.com/explore/{note_id}'
    if query:
        explore_url += '?' + query

    try:
        return parse_explore(explore_url, note_id, session, profile)
    except Exception:
        discovery_url = source_url
        if '/discovery/item/' not in discovery_url:
            discovery_url = f'https://www.xiaohongshu.com/discovery/item/{note_id}'
            if query:
                discovery_url += '?' + query
        return parse_discovery(discovery_url, session, profile)


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit('usage: xhs_resolver.py <url> <profile> [cookies_file]')
    url = sys.argv[1]
    profile = sys.argv[2]
    cookies_file = sys.argv[3] if len(sys.argv) > 3 else ''
    result = resolve(url, profile, cookies_file)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
