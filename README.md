# aria2-share-proxy

A lightweight JSON-RPC proxy in front of `aria2` for mixed download workflows inside a `Tailscale` network.

It is designed for this exact topology:

- an old PC runs `aria2` continuously
- a new PC uses a browser UI or local frontend
- the new PC reaches the old PC through `Tailscale`
- direct links should go straight to `aria2`
- share links should first be resolved into direct media URLs, then pushed to `aria2`

This project is not intended to be an open public download gateway. It is a conditional private-network setup for users who already have a working `Tailscale` mesh and a self-hosted `aria2` node.

## Overview

There are two download paths:

1. `direct`
2. `parse`

The split is intentional:

- `direct` is for real direct URLs only
- `parse` is for share links only
- sending a direct URL into `parse` returns an explicit error
- sending a share link into `direct` will still fail in `aria2`, because no resolver is involved

## Resolver paths

### `yt_dlp`

Use this when:

- the target platform is best handled by `yt-dlp`
- the user explicitly selects `yt_dlp`

Properties:

- broadest platform coverage
- may require cookies, format, or extra extractor args
- still depends on login state when the upstream platform requires it

### `plugin_link_resolver`

Currently wired for:

- Bilibili
- Douyin
- Xiaohongshu

Properties:

- no user-specific frontend configuration required by default
- good default path for common CN share links
- returns a direct media URL and then forwards it to `aria2`

### `plugin_parser`

Currently wired for:

- Bilibili
- Douyin
- Kuaishou
- Xiaohongshu
- X / Twitter

Properties:

- independent resolver path, not a wrapper around `plugin_link_resolver`
- follows parser-style extraction logic and returns direct media URLs
- only platforms that fit the `single downloadable direct URL -> aria2` model are included here

## Browser frontend support

The proxy already handles browser preflight and private-network access:

- `OPTIONS /jsonrpc`
- `Access-Control-Allow-Origin`
- `Access-Control-Allow-Methods`
- `Access-Control-Allow-Headers`
- `Access-Control-Allow-Private-Network`

So a browser frontend on the new PC can call the old PC directly through:

- `http://<old-pc-tailscale-ip>:16800/jsonrpc`

Health endpoint:

- `http://<old-pc-tailscale-ip>:16800/health`

## Frontend integration contract

When the frontend calls `aria2.addUri`, put the proxy control headers into `options.header`. The proxy strips them before forwarding the request to `aria2`.

### Download mode

- `X-Proxy-Download-Mode: direct`
- `X-Proxy-Download-Mode: parse`

`auto` is also supported:

- `X-Proxy-Download-Mode: auto`

But if your UI already separates the two flows, use `direct` or `parse` explicitly.

### Parse method

Only for `X-Proxy-Download-Mode: parse`:

- `X-Proxy-Parse-Method: yt_dlp`
- `X-Proxy-Parse-Method: plugin_link_resolver`
- `X-Proxy-Parse-Method: plugin_parser`

### Optional `yt_dlp` headers

- `X-Proxy-Ytdlp-Cookies-File: <cookies file path>`
- `X-Proxy-Ytdlp-Format: <yt-dlp format>`
- `X-Proxy-Ytdlp-Extra-Args: <extra args string>`

If they are not passed by the frontend, the proxy falls back to values from `proxy_config.json`.

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Prepare config

Copy:

- `proxy_config.example.json`

To:

- `proxy_config.json`

Then edit it for your own machine:

- `client_secret`
- `backend_secret`
- `backend_rpc_url`
- `yt_dlp_path`
- `bili_python_path`
- `script_python_path`
- cookies paths if you really need them

### 3. Start the proxy

```powershell
C:\Users\86152\aria2-share-proxy\start_proxy.ps1
```

Default listen address:

- `0.0.0.0:16800`

## Example requests

### Direct download

```text
X-Proxy-Download-Mode: direct
```

### Bilibili share link via `plugin_link_resolver`

```text
X-Proxy-Download-Mode: parse
X-Proxy-Parse-Method: plugin_link_resolver
```

### X / Twitter share link via `plugin_parser`

```text
X-Proxy-Download-Mode: parse
X-Proxy-Parse-Method: plugin_parser
```

### YouTube share link via `yt_dlp`

```text
X-Proxy-Download-Mode: parse
X-Proxy-Parse-Method: yt_dlp
X-Proxy-Ytdlp-Cookies-File: D:\cookies\youtube.txt
X-Proxy-Ytdlp-Format: b/best
```

## Frontend example

A minimal browser-side `fetch` example is included here:

- `examples/frontend-fetch-example.js`

It shows:

- direct download
- parse download with `plugin_link_resolver`
- parse download with `plugin_parser`
- parse download with `yt_dlp`

## Files

- `proxy_server.py`: JSON-RPC proxy
- `bili_resolver.py`: Bilibili resolver
- `douyin_resolver.py`: Douyin resolver
- `kuaishou_resolver.py`: Kuaishou resolver
- `xhs_resolver.py`: Xiaohongshu resolver
- `plugin_parser_adapter.py`: parser-style adapter layer
- `start_proxy.ps1`: Windows startup script
- `proxy_config.example.json`: sample config
- `requirements.txt`: Python dependencies
- `examples/frontend-fetch-example.js`: browser-side integration example

## Notes

- This project is intended for `Tailscale` private-network usage and should not be exposed directly to the public Internet.
- `yt_dlp` availability depends on the upstream platform and whether valid cookies are required.
- A successful resolve only means the proxy obtained a direct URL and pushed it into `aria2`. Final download success still depends on source availability, network reachability, and whether the upstream resource remains valid.
