import json
import mimetypes
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, List, Tuple


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "proxy_config.json")
LOG_PATH = os.path.join(BASE_DIR, "proxy.log")
BILI_RESOLVER_PATH = os.path.join(BASE_DIR, "bili_resolver.py")
DOUYIN_RESOLVER_PATH = os.path.join(BASE_DIR, "douyin_resolver.py")
KUAISHOU_RESOLVER_PATH = os.path.join(BASE_DIR, "kuaishou_resolver.py")
XHS_RESOLVER_PATH = os.path.join(BASE_DIR, "xhs_resolver.py")
PLUGIN_PARSER_ADAPTER_PATH = os.path.join(BASE_DIR, "plugin_parser_adapter.py")

DIRECT_EXTENSIONS = {
    ".7z", ".aac", ".apk", ".avi", ".bin", ".dmg", ".doc", ".docx", ".epub",
    ".exe", ".flac", ".flv", ".gif", ".gz", ".iso", ".jpeg", ".jpg", ".m4a",
    ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".ogg", ".pdf",
    ".png", ".rar", ".srt", ".tar", ".torrent", ".ts", ".txt", ".wav", ".webm",
    ".wmv", ".xls", ".xlsx", ".zip", ".m3u8", ".mpd"
}

DIRECT_CONTENT_TYPES = (
    "application/octet-stream",
    "application/pdf",
    "application/zip",
    "application/x-bittorrent",
    "audio/",
    "image/",
    "video/",
    "multipart/byteranges",
)

TEXTUAL_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/json",
    "application/javascript",
    "text/javascript",
)

INVALID_FILENAME_CHARS = r'[<>:"/\\|?*\x00-\x1f]'

CONTROL_HEADER_DOWNLOAD_MODE = "x-proxy-download-mode"
CONTROL_HEADER_PARSE_METHOD = "x-proxy-parse-method"
CONTROL_HEADER_YTDLP_COOKIES_FILE = "x-proxy-ytdlp-cookies-file"
CONTROL_HEADER_YTDLP_FORMAT = "x-proxy-ytdlp-format"
CONTROL_HEADER_YTDLP_EXTRA_ARGS = "x-proxy-ytdlp-extra-args"

DOWNLOAD_MODE_AUTO = "auto"
DOWNLOAD_MODE_DIRECT = "direct"
DOWNLOAD_MODE_PARSE = "parse"

PARSE_METHOD_YT_DLP = "yt_dlp"
PARSE_METHOD_LINK_RESOLVER = "plugin_link_resolver"
PARSE_METHOD_PLUGIN_PARSER = "plugin_parser"

PLATFORM_BILIBILI = "bilibili"
PLATFORM_DOUYIN = "douyin"
PLATFORM_KUAISHOU = "kuaishou"
PLATFORM_XHS = "xiaohongshu"
PLATFORM_TWITTER = "twitter"
PLATFORM_GENERIC = "generic"

BILIBILI_HOSTS = ("b23.tv", "bili2233.cn", "bilibili.com")
DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")
KUAISHOU_HOSTS = ("kuaishou.com", "chenzhongtech.com")
XHS_HOSTS = ("xhslink.com", "xiaohongshu.com")
TWITTER_HOSTS = ("x.com", "twitter.com")


def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(
            "Missing proxy_config.json. Copy proxy_config.example.json to proxy_config.json and fill in your local settings."
        )
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as handle:
        loaded = json.load(handle)

    loaded.setdefault("listen_host", "0.0.0.0")
    loaded.setdefault("listen_port", 16800)
    loaded.setdefault("probe_timeout_seconds", 12)
    loaded.setdefault("default_download_mode", DOWNLOAD_MODE_AUTO)
    loaded.setdefault("default_parse_method", PARSE_METHOD_LINK_RESOLVER)
    loaded.setdefault("script_python_path", loaded.get("bili_python_path", "python"))
    loaded.setdefault("yt_dlp_format", "b/best")
    loaded.setdefault("yt_dlp_timeout_seconds", 90)
    loaded.setdefault("yt_dlp_cookies_file", loaded.get("cookies_file", ""))
    loaded.setdefault("extra_yt_dlp_args", [])
    loaded.setdefault("bili_cookies_file", "")
    loaded.setdefault("douyin_cookies_file", "")
    loaded.setdefault("kuaishou_cookies_file", "")
    loaded.setdefault("xhs_cookies_file", "")
    loaded.setdefault("bili_resolver_timeout_seconds", 60)
    loaded.setdefault("douyin_resolver_timeout_seconds", 45)
    loaded.setdefault("kuaishou_resolver_timeout_seconds", 45)
    loaded.setdefault("xhs_resolver_timeout_seconds", 45)
    loaded.setdefault(
        "share_domains",
        [
            "bilibili.com",
            "b23.tv",
            "bili2233.cn",
            "douyin.com",
            "iesdouyin.com",
            "kuaishou.com",
            "chenzhongtech.com",
            "xiaohongshu.com",
            "xhslink.com",
            "x.com",
            "twitter.com",
            "youtube.com",
            "youtu.be",
            "tiktok.com",
            "instagram.com",
            "facebook.com",
        ],
    )
    loaded.setdefault(
        "direct_host_hints",
        ["cdn", "download", "media", "file", "files", "video", "vod", "stream", "oss", "alicdn", "cos", "s3"],
    )
    return loaded


CONFIG = load_config()


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8-sig") as handle:
        handle.write(line + "\n")


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(INVALID_FILENAME_CHARS, "_", (name or "").strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned[:180]


def json_rpc_error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def forward_rpc(payload: Any) -> Tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        CONFIG["backend_rpc_url"],
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=CONFIG["probe_timeout_seconds"]) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)


def replace_secret(params: List[Any]) -> List[Any]:
    updated = list(params)
    backend_secret = CONFIG.get("backend_secret", "")
    client_secret = CONFIG.get("client_secret", "")

    if updated and isinstance(updated[0], str) and updated[0].startswith("token:"):
        provided = updated[0][6:]
        if client_secret and provided != client_secret:
            raise PermissionError("RPC secret mismatch")
        if backend_secret:
            updated[0] = f"token:{backend_secret}"
        return updated

    if client_secret:
        raise PermissionError("Missing RPC secret")

    if backend_secret:
        return [f"token:{backend_secret}", *updated]
    return updated


def host_matches(host: str, patterns: Iterable[str]) -> bool:
    host = (host or "").lower()
    return any(host == item or host.endswith("." + item) for item in patterns)


def looks_like_direct_by_extension(parsed: urllib.parse.ParseResult) -> bool:
    path = urllib.parse.unquote(parsed.path or "")
    _, ext = os.path.splitext(path.lower())
    if ext in DIRECT_EXTENSIONS:
        return True
    guessed, _ = mimetypes.guess_type(path)
    return bool(guessed and guessed.startswith(("audio/", "video/", "image/")))


def looks_like_direct_by_host(host: str) -> bool:
    host = (host or "").lower()
    return any(token in host for token in CONFIG.get("direct_host_hints", []))


def detect_platform(host: str) -> str:
    if host_matches(host, BILIBILI_HOSTS):
        return PLATFORM_BILIBILI
    if host_matches(host, DOUYIN_HOSTS):
        return PLATFORM_DOUYIN
    if host_matches(host, KUAISHOU_HOSTS):
        return PLATFORM_KUAISHOU
    if host_matches(host, XHS_HOSTS):
        return PLATFORM_XHS
    if host_matches(host, TWITTER_HOSTS):
        return PLATFORM_TWITTER
    return PLATFORM_GENERIC


def probe_url(url: str) -> Tuple[bool, str]:
    headers = {"User-Agent": "aria2-share-proxy/2.0"}
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, headers=headers, method=method)
        if method == "GET":
            request.add_header("Range", "bytes=0-0")
        try:
            with urllib.request.urlopen(request, timeout=CONFIG["probe_timeout_seconds"]) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                disposition = (response.headers.get("Content-Disposition") or "").lower()
                if "attachment" in disposition or "filename=" in disposition:
                    return True, f"probe:{method}:content-disposition"
                if any(content_type.startswith(prefix) for prefix in DIRECT_CONTENT_TYPES):
                    return True, f"probe:{method}:{content_type}"
                if any(content_type.startswith(prefix) for prefix in TEXTUAL_CONTENT_TYPES):
                    return False, f"probe:{method}:{content_type}"
                if content_type:
                    return False, f"probe:{method}:{content_type}"
        except urllib.error.HTTPError as exc:
            content_type = (exc.headers.get("Content-Type") or "").lower()
            if exc.code in (401, 403) and any(content_type.startswith(prefix) for prefix in TEXTUAL_CONTENT_TYPES):
                return False, f"probe:{method}:http-{exc.code}-text"
            if exc.code in (405, 501):
                continue
            return False, f"probe:{method}:http-{exc.code}"
        except Exception as exc:
            return False, f"probe:{method}:error:{exc.__class__.__name__}"
    return False, "probe:unknown"


def decide_url_mode(url: str) -> Tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()

    if scheme in {"magnet", "ftp", "sftp"}:
        return DOWNLOAD_MODE_DIRECT, f"scheme:{scheme}"
    if scheme not in {"http", "https"}:
        return DOWNLOAD_MODE_DIRECT, f"scheme:{scheme or 'unknown'}"
    if host_matches(host, CONFIG.get("share_domains", [])):
        return DOWNLOAD_MODE_PARSE, f"share-host:{host}"
    if looks_like_direct_by_extension(parsed):
        return DOWNLOAD_MODE_DIRECT, "path-extension"
    if looks_like_direct_by_host(host):
        return DOWNLOAD_MODE_DIRECT, f"host-hint:{host}"

    is_direct, reason = probe_url(url)
    return (DOWNLOAD_MODE_DIRECT if is_direct else DOWNLOAD_MODE_PARSE), reason


def normalize_download_mode(value: str) -> str:
    cleaned = (value or "").strip().lower()
    aliases = {
        "": DOWNLOAD_MODE_AUTO,
        "auto": DOWNLOAD_MODE_AUTO,
        "default": DOWNLOAD_MODE_AUTO,
        "direct": DOWNLOAD_MODE_DIRECT,
        "raw": DOWNLOAD_MODE_DIRECT,
        "parse": DOWNLOAD_MODE_PARSE,
        "resolver": DOWNLOAD_MODE_PARSE,
        "resolved": DOWNLOAD_MODE_PARSE,
    }
    if cleaned not in aliases:
        raise RuntimeError(f"unsupported download mode: {value}")
    return aliases[cleaned]


def normalize_parse_method(value: str) -> str:
    cleaned = (value or "").strip().lower()
    aliases = {
        "": PARSE_METHOD_LINK_RESOLVER,
        "default": PARSE_METHOD_LINK_RESOLVER,
        "yt-dlp": PARSE_METHOD_YT_DLP,
        "ytdlp": PARSE_METHOD_YT_DLP,
        "yt_dlp": PARSE_METHOD_YT_DLP,
        "plugin_link_resolver": PARSE_METHOD_LINK_RESOLVER,
        "link_resolver": PARSE_METHOD_LINK_RESOLVER,
        "plugin-link-resolver": PARSE_METHOD_LINK_RESOLVER,
        "plugin_parser": PARSE_METHOD_PLUGIN_PARSER,
        "parser": PARSE_METHOD_PLUGIN_PARSER,
        "plugin-parser": PARSE_METHOD_PLUGIN_PARSER,
        "万能解析器": PARSE_METHOD_PLUGIN_PARSER,
    }
    if cleaned not in aliases:
        raise RuntimeError(f"unsupported parse method: {value}")
    return aliases[cleaned]


def split_header_line(header_line: str) -> Tuple[str, str]:
    if ":" not in header_line:
        return header_line.strip(), ""
    name, value = header_line.split(":", 1)
    return name.strip(), value.strip()


def extract_control_headers(options: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(options, dict):
        return {}

    headers = options.get("header", [])
    if isinstance(headers, str):
        header_lines = [headers]
    elif isinstance(headers, list):
        header_lines = [item for item in headers if isinstance(item, str)]
    else:
        header_lines = []

    forwarded_headers: List[str] = []
    control: Dict[str, str] = {}
    for header_line in header_lines:
        name, value = split_header_line(header_line)
        if name.lower().startswith("x-proxy-"):
            control[name.lower()] = value
            continue
        forwarded_headers.append(header_line)

    if header_lines and len(forwarded_headers) != len(header_lines):
        if forwarded_headers:
            options["header"] = forwarded_headers
        else:
            options.pop("header", None)
    return control


def parse_extra_args(raw: str) -> List[str]:
    cleaned = (raw or "").strip()
    if not cleaned:
        return []
    return [item for item in shlex.split(cleaned, posix=False) if item]


def get_script_python() -> str:
    return CONFIG.get("script_python_path") or CONFIG.get("bili_python_path") or "python"


def pick_headers(candidate: Dict[str, Any], root: Dict[str, Any]) -> List[str]:
    headers = candidate.get("http_headers") or root.get("http_headers") or {}
    result = []
    for key, value in headers.items():
        if value is None:
            continue
        result.append(f"{key}: {value}")
    return result


def pick_filename(candidate: Dict[str, Any], root: Dict[str, Any]) -> str:
    for key in ("_filename", "filename", "filepath"):
        value = candidate.get(key)
        if value:
            return sanitize_filename(os.path.basename(value))
    title = root.get("title")
    ext = candidate.get("ext") or root.get("ext")
    if title and ext:
        return sanitize_filename(f"{title}.{ext}")
    if title:
        return sanitize_filename(title)
    return ""


def run_json_resolver(command: List[str], timeout_seconds: int, label: str) -> Dict[str, Any]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(stderr or f"{label} exited with code {completed.returncode}")
    payload = json.loads(completed.stdout)
    payload["filename"] = sanitize_filename(payload.get("filename", ""))
    return payload


def resolve_share_url(url: str, control_headers: Dict[str, str]) -> Dict[str, Any]:
    command = [
        CONFIG["yt_dlp_path"],
        "--ignore-config",
        "--no-warnings",
        "--no-playlist",
        "--dump-single-json",
        "--format",
        control_headers.get(CONTROL_HEADER_YTDLP_FORMAT, "").strip() or CONFIG.get("yt_dlp_format", "b/best"),
    ]

    cookies_file = (
        control_headers.get(CONTROL_HEADER_YTDLP_COOKIES_FILE, "").strip()
        or CONFIG.get("yt_dlp_cookies_file", "").strip()
        or CONFIG.get("cookies_file", "").strip()
    )
    if cookies_file:
        command.extend(["--cookies", cookies_file])

    command.extend(CONFIG.get("extra_yt_dlp_args", []))
    command.extend(parse_extra_args(control_headers.get(CONTROL_HEADER_YTDLP_EXTRA_ARGS, "")))
    command.append(url)

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=CONFIG["yt_dlp_timeout_seconds"],
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(stderr or f"yt-dlp exited with code {completed.returncode}")

    payload = json.loads(completed.stdout)

    candidates: List[Dict[str, Any]] = []
    top_level_url = payload.get("url")
    if isinstance(top_level_url, str):
        candidates.append(payload)

    for key in ("requested_downloads", "requested_formats"):
        for item in payload.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                candidates.append(item)

    unique_urls: List[Dict[str, Any]] = []
    seen = set()
    for item in candidates:
        resolved = item.get("url")
        if resolved and resolved not in seen:
            seen.add(resolved)
            unique_urls.append(item)

    if not unique_urls:
        raise RuntimeError("yt-dlp did not return a direct media URL")
    if len(unique_urls) > 1:
        raise RuntimeError("yt-dlp returned multiple media streams; this URL is not suitable for direct aria2 download")

    chosen = unique_urls[0]
    return {
        "url": chosen["url"],
        "filename": pick_filename(chosen, payload),
        "headers": pick_headers(chosen, payload),
        "extractor": payload.get("extractor_key") or payload.get("extractor") or "yt_dlp",
        "title": payload.get("title") or "",
    }


def resolve_bilibili_url(url: str) -> Dict[str, Any]:
    command = [get_script_python(), BILI_RESOLVER_PATH, url]
    cookies_file = (CONFIG.get("bili_cookies_file") or CONFIG.get("yt_dlp_cookies_file") or CONFIG.get("cookies_file") or "").strip()
    if cookies_file:
        command.append(cookies_file)
    return run_json_resolver(command, CONFIG["bili_resolver_timeout_seconds"], "bilibili resolver")


def resolve_douyin_url(url: str) -> Dict[str, Any]:
    command = [get_script_python(), DOUYIN_RESOLVER_PATH, url]
    cookies_file = (CONFIG.get("douyin_cookies_file") or CONFIG.get("yt_dlp_cookies_file") or CONFIG.get("cookies_file") or "").strip()
    if cookies_file:
        command.append(cookies_file)
    return run_json_resolver(command, CONFIG["douyin_resolver_timeout_seconds"], "douyin resolver")


def resolve_kuaishou_url(url: str) -> Dict[str, Any]:
    command = [get_script_python(), KUAISHOU_RESOLVER_PATH, url]
    cookies_file = (CONFIG.get("kuaishou_cookies_file") or CONFIG.get("yt_dlp_cookies_file") or CONFIG.get("cookies_file") or "").strip()
    if cookies_file:
        command.append(cookies_file)
    return run_json_resolver(command, CONFIG["kuaishou_resolver_timeout_seconds"], "kuaishou resolver")


def resolve_xhs_url(url: str, profile: str) -> Dict[str, Any]:
    command = [get_script_python(), XHS_RESOLVER_PATH, url, profile]
    cookies_file = (CONFIG.get("xhs_cookies_file") or CONFIG.get("yt_dlp_cookies_file") or CONFIG.get("cookies_file") or "").strip()
    if cookies_file:
        command.append(cookies_file)
    return run_json_resolver(command, CONFIG["xhs_resolver_timeout_seconds"], "xiaohongshu resolver")


def resolve_with_plugin_parser_adapter(url: str, platform: str) -> Dict[str, Any]:
    command = [get_script_python(), PLUGIN_PARSER_ADAPTER_PATH, platform, url]

    cookies_file = ""
    timeout_seconds = CONFIG["yt_dlp_timeout_seconds"]
    if platform == PLATFORM_BILIBILI:
        cookies_file = (CONFIG.get("bili_cookies_file") or CONFIG.get("yt_dlp_cookies_file") or CONFIG.get("cookies_file") or "").strip()
        timeout_seconds = CONFIG["bili_resolver_timeout_seconds"]
    elif platform == PLATFORM_DOUYIN:
        cookies_file = (CONFIG.get("douyin_cookies_file") or CONFIG.get("yt_dlp_cookies_file") or CONFIG.get("cookies_file") or "").strip()
        timeout_seconds = CONFIG["douyin_resolver_timeout_seconds"]
    elif platform == PLATFORM_KUAISHOU:
        cookies_file = (CONFIG.get("kuaishou_cookies_file") or CONFIG.get("yt_dlp_cookies_file") or CONFIG.get("cookies_file") or "").strip()
        timeout_seconds = CONFIG["kuaishou_resolver_timeout_seconds"]
    elif platform == PLATFORM_XHS:
        cookies_file = (CONFIG.get("xhs_cookies_file") or CONFIG.get("yt_dlp_cookies_file") or CONFIG.get("cookies_file") or "").strip()
        timeout_seconds = CONFIG["xhs_resolver_timeout_seconds"]

    if cookies_file:
        command.append(cookies_file)
    return run_json_resolver(command, timeout_seconds, "plugin parser adapter")


def resolve_with_link_resolver(url: str, platform: str) -> Dict[str, Any]:
    if platform == PLATFORM_BILIBILI:
        result = resolve_bilibili_url(url)
        result["extractor"] = result.get("extractor") or "plugin_link_resolver_bilibili"
        return result
    if platform == PLATFORM_DOUYIN:
        result = resolve_douyin_url(url)
        result["extractor"] = "plugin_link_resolver_douyin"
        return result
    if platform == PLATFORM_XHS:
        return resolve_xhs_url(url, profile="plugin_link_resolver")
    raise RuntimeError("plugin_link_resolver only supports B站 / 抖音 / 小红书")


def resolve_with_plugin_parser(url: str, platform: str) -> Dict[str, Any]:
    if platform in {PLATFORM_BILIBILI, PLATFORM_DOUYIN, PLATFORM_KUAISHOU, PLATFORM_XHS, PLATFORM_TWITTER}:
        return resolve_with_plugin_parser_adapter(url, platform)
    raise RuntimeError("plugin_parser 当前已接入 B站 / 抖音 / 快手 / 小红书 / X(Twitter)；其他平台请改用 yt-dlp")


def resolve_via_method(url: str, parse_method: str, platform: str, control_headers: Dict[str, str]) -> Dict[str, Any]:
    if parse_method == PARSE_METHOD_YT_DLP:
        return resolve_share_url(url, control_headers)
    if parse_method == PARSE_METHOD_LINK_RESOLVER:
        return resolve_with_link_resolver(url, platform)
    if parse_method == PARSE_METHOD_PLUGIN_PARSER:
        return resolve_with_plugin_parser(url, platform)
    raise RuntimeError(f"unsupported parse method after normalization: {parse_method}")


def merge_resolved_options(logical_params: List[Any], resolved: Dict[str, Any]) -> List[Any]:
    new_logical_params = list(logical_params)
    new_logical_params[0] = [resolved["url"]]

    if len(new_logical_params) >= 2 and isinstance(new_logical_params[1], dict):
        options = dict(new_logical_params[1])
    else:
        options = {}
        if len(new_logical_params) >= 2:
            new_logical_params.insert(1, options)
        else:
            new_logical_params.append(options)

    if resolved.get("filename") and not options.get("out"):
        options["out"] = resolved["filename"]

    existing_headers = options.get("header", [])
    if isinstance(existing_headers, str):
        existing_headers = [existing_headers]
    elif not isinstance(existing_headers, list):
        existing_headers = []

    merged_headers = list(existing_headers)
    for header in resolved.get("headers", []):
        if header not in merged_headers:
            merged_headers.append(header)
    if merged_headers:
        options["header"] = merged_headers

    return new_logical_params


def massage_add_uri(request_obj: Dict[str, Any]) -> Dict[str, Any]:
    params = request_obj.get("params")
    if not isinstance(params, list):
        return request_obj

    updated_params = replace_secret(params)
    logical_params = updated_params[1:] if updated_params and isinstance(updated_params[0], str) and updated_params[0].startswith("token:") else updated_params
    if not logical_params or not isinstance(logical_params[0], list):
        request_obj["params"] = updated_params
        return request_obj

    uri_list = logical_params[0]
    if len(uri_list) != 1 or not isinstance(uri_list[0], str):
        request_obj["params"] = updated_params
        return request_obj

    options_ref = logical_params[1] if len(logical_params) >= 2 and isinstance(logical_params[1], dict) else None
    control_headers = extract_control_headers(options_ref) if options_ref is not None else {}

    source_url = uri_list[0]
    parsed_source = urllib.parse.urlparse(source_url)
    source_host = (parsed_source.hostname or "").lower()
    platform = detect_platform(source_host)

    requested_mode = normalize_download_mode(control_headers.get(CONTROL_HEADER_DOWNLOAD_MODE, CONFIG.get("default_download_mode", DOWNLOAD_MODE_AUTO)))
    requested_parse_method = normalize_parse_method(control_headers.get(CONTROL_HEADER_PARSE_METHOD, CONFIG.get("default_parse_method", PARSE_METHOD_LINK_RESOLVER)))
    detected_mode, detected_reason = decide_url_mode(source_url)

    if requested_mode == DOWNLOAD_MODE_AUTO:
        effective_mode = detected_mode
        mode_reason = f"auto:{detected_reason}"
    else:
        effective_mode = requested_mode
        mode_reason = f"request:{requested_mode}"

    if effective_mode == DOWNLOAD_MODE_DIRECT:
        log(f"direct passthrough: {source_url} ({mode_reason}; platform={platform})")
        request_obj["params"] = updated_params
        return request_obj

    if effective_mode != DOWNLOAD_MODE_PARSE:
        raise RuntimeError(f"unsupported effective mode: {effective_mode}")

    if requested_mode == DOWNLOAD_MODE_PARSE and detected_mode == DOWNLOAD_MODE_DIRECT:
        raise RuntimeError("当前链接看起来是直链，请改用直接下载，不要走解析下载")

    resolved = resolve_via_method(source_url, requested_parse_method, platform, control_headers)
    new_logical_params = merge_resolved_options(logical_params, resolved)

    if updated_params and isinstance(updated_params[0], str) and updated_params[0].startswith("token:"):
        request_obj["params"] = [updated_params[0], *new_logical_params]
    else:
        request_obj["params"] = new_logical_params

    log(
        "share parsed: "
        f"{source_url} -> {resolved['url']} "
        f"(mode={effective_mode}; method={requested_parse_method}; platform={platform}; detect={detected_reason}; extractor={resolved.get('extractor', '')})"
    )
    return request_obj


def prepare_request(request_obj: Dict[str, Any]) -> Dict[str, Any]:
    method = request_obj.get("method")
    params = request_obj.get("params")

    if not isinstance(params, list):
        return request_obj

    if method == "aria2.addUri":
        return massage_add_uri(request_obj)

    request_obj["params"] = replace_secret(params)
    return request_obj


class RpcHandler(BaseHTTPRequestHandler):
    server_version = "aria2-share-proxy/2.0"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._write_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        body = ""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body)
            prepared = self._prepare_payload(payload)
            status, response_body = forward_rpc(prepared)
            self._write_json(status, response_body)
        except PermissionError as exc:
            request_id = None
            try:
                request_id = json.loads(body).get("id")
            except Exception:
                pass
            self._write_json(200, json_rpc_error(request_id, -32001, str(exc)))
        except subprocess.TimeoutExpired:
            request_id = None
            try:
                request_id = json.loads(body).get("id")
            except Exception:
                pass
            self._write_json(200, json_rpc_error(request_id, -32002, "resolver timeout"))
        except Exception as exc:
            log(f"request failed: {exc}")
            request_id = None
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    request_id = parsed.get("id")
            except Exception:
                pass
            self._write_json(200, json_rpc_error(request_id, -32000, str(exc)))

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            payload = {
                "ok": True,
                "service": "aria2-share-proxy",
                "version": "2.0",
                "default_download_mode": CONFIG.get("default_download_mode", DOWNLOAD_MODE_AUTO),
                "default_parse_method": CONFIG.get("default_parse_method", PARSE_METHOD_LINK_RESOLVER),
            }
            self._write_json(200, payload)
            return
        self._write_json(404, {"ok": False})

    def log_message(self, format_str: str, *args: Any) -> None:
        log(format_str % args)

    def _prepare_payload(self, payload: Any) -> Any:
        if isinstance(payload, list):
            return [prepare_request(dict(item)) if isinstance(item, dict) else item for item in payload]
        if isinstance(payload, dict):
            return prepare_request(dict(payload))
        raise ValueError("Invalid JSON-RPC payload")

    def _write_cors_headers(self) -> None:
        origin = self.headers.get("Origin") or "*"
        requested_headers = self.headers.get("Access-Control-Request-Headers") or "Content-Type"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", requested_headers)
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")

    def _write_json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._write_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    host = CONFIG["listen_host"]
    port = int(CONFIG["listen_port"])
    log(f"starting proxy on {host}:{port}, backend={CONFIG['backend_rpc_url']}")
    server = ThreadingHTTPServer((host, port), RpcHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()



