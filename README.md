# aria2-share-proxy

一个给 `aria2` JSON-RPC 前面加“分享链接解析层”的轻量代理，适用于这样的场景：

- 旧电脑长期运行 `aria2`
- 新电脑通过 `Tailscale` 内网访问旧电脑
- 新电脑有自己的前端 UI
- 用户既想下载直链，也想把分享链接先解析成直链再交给 `aria2`

这不是一个通用公网下载服务，而是一个有条件的内网方案：你需要已经打通 `Tailscale` 组网，并且旧电脑能够稳定运行 `aria2` 与本代理。

## 适用架构

- 旧电脑：运行 `aria2` 和 `aria2-share-proxy`
- 新电脑：运行前端页面，通过 `Tailscale IP:16800/jsonrpc` 调用代理
- 代理：判断是 `直接下载` 还是 `解析下载`，必要时先解析出直链，再转发给 `aria2`

## 功能边界

下载分两条路径：

1. `直接下载`
2. `解析下载`

约束是强制区分的：

- `直接下载` 只适用于直链，不经过解析。
- `解析下载` 只适用于分享链接，代理先解析成直链，再推送给 `aria2`。
- 直链误走 `解析下载`：代理直接报错，避免误解析。
- 分享链接误走 `直接下载`：代理不拦截，`aria2` 会自己失败。

## 三种解析路径

### 1. `yt_dlp`

适用：

- 需要 `yt-dlp` 才能稳定提取媒体直链的平台
- 或用户明确指定走 `yt_dlp`

特点：

- 最通用
- 但通常需要前端额外提供 cookies、format、extra args 等信息
- 如果平台本身需要登录态，这条路依然会受 cookies 时效影响

### 2. `plugin_link_resolver`

当前支持：

- B站
- 抖音
- 小红书

特点：

- 不要求前端填写额外个人信息
- 更适合国内常见分享链接的轻量解析
- 解析结果仍然是直链，最后交给 `aria2`

### 3. `plugin_parser`

当前接入：

- B站
- 抖音
- 快手
- 小红书
- X / Twitter

说明：

- 这条路径是独立于 `plugin_link_resolver` 的另一套解析思路
- 它不是把链接转给 resolver，而是单独按 parser 风格解析出媒体直链
- 当前仓库为了适配 `aria2`，只保留“能稳定落成单一可下载直链”的平台实现

## 浏览器前端支持

代理已经补齐浏览器跨域访问所需能力：

- `OPTIONS /jsonrpc`
- `Access-Control-Allow-Origin`
- `Access-Control-Allow-Methods`
- `Access-Control-Allow-Headers`
- `Access-Control-Allow-Private-Network`

所以新电脑前端页面可以直接调用旧电脑代理，只要地址指向：

- `http://<旧电脑Tailscale-IP>:16800/jsonrpc`

健康检查：

- `http://<旧电脑Tailscale-IP>:16800/health`

## 前端调用约定

前端调用 `aria2.addUri` 时，在 `options.header` 中加入控制头。代理会识别这些控制头，并在转发给 `aria2` 前剥离掉。

### 下载模式

- `X-Proxy-Download-Mode: direct`
- `X-Proxy-Download-Mode: parse`

也支持：

- `X-Proxy-Download-Mode: auto`

但既然前端已经拆成两个入口，建议显式传 `direct` 或 `parse`。

### 解析方式

仅当 `X-Proxy-Download-Mode: parse` 时再传：

- `X-Proxy-Parse-Method: yt_dlp`
- `X-Proxy-Parse-Method: plugin_link_resolver`
- `X-Proxy-Parse-Method: plugin_parser`

### `yt_dlp` 可选附加头

- `X-Proxy-Ytdlp-Cookies-File: <cookies 文件路径>`
- `X-Proxy-Ytdlp-Format: <yt-dlp format>`
- `X-Proxy-Ytdlp-Extra-Args: <额外参数字符串>`

如果前端不传，就使用 `proxy_config.json` 里的默认值。

## 依赖

- Python 3.10+
- `aria2`，并开启 JSON-RPC
- `yt-dlp`（仅当你要走 `yt_dlp` 路径）
- Python 依赖：

```bash
pip install -r requirements.txt
```

## 配置

仓库只提供示例配置：

- `proxy_config.example.json`

首次使用时复制为：

- `proxy_config.json`

然后按你自己的机器环境修改：

- `client_secret`
- `backend_secret`
- `backend_rpc_url`
- `yt_dlp_path`
- `bili_python_path`
- `script_python_path`
- 各平台 cookies 路径（如果你确实要用）

## 启动

本机启动：

```powershell
C:\Users\86152\aria2-share-proxy\start_proxy.ps1
```

默认监听：

- `0.0.0.0:16800`

## 示例

### 直链直接下载

```text
X-Proxy-Download-Mode: direct
```

### B站分享链接走 `plugin_link_resolver`

```text
X-Proxy-Download-Mode: parse
X-Proxy-Parse-Method: plugin_link_resolver
```

### X / Twitter 分享链接走 `plugin_parser`

```text
X-Proxy-Download-Mode: parse
X-Proxy-Parse-Method: plugin_parser
```

### YouTube 分享链接走 `yt_dlp`

```text
X-Proxy-Download-Mode: parse
X-Proxy-Parse-Method: yt_dlp
X-Proxy-Ytdlp-Cookies-File: D:\cookies\youtube.txt
X-Proxy-Ytdlp-Format: b/best
```

## 仓库内容

- `proxy_server.py`: JSON-RPC 代理
- `bili_resolver.py`: B站解析
- `douyin_resolver.py`: 抖音解析
- `kuaishou_resolver.py`: 快手解析
- `xhs_resolver.py`: 小红书解析
- `plugin_parser_adapter.py`: parser 风格解析适配层
- `start_proxy.ps1`: Windows 启动脚本
- `proxy_config.example.json`: 示例配置
- `requirements.txt`: Python 依赖

## 注意

- 这套方案默认服务于 `Tailscale` 内网环境，不建议直接裸露到公网。
- `yt_dlp` 路径是否可用，取决于目标平台是否要求登录态，以及你是否提供了仍然有效的 cookies。
- 解析成功只代表“拿到了直链并成功推送给 `aria2`”；实际下载是否完成，还要看源站直链是否仍然有效、网络是否通畅、目标资源是否允许断点续传。
