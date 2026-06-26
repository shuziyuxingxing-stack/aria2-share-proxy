const proxyRpcUrl = 'http://100.x.y.z:16800/jsonrpc';
const token = 'your-client-secret';

async function aria2AddUri(url, headers = [], out = '') {
  const payload = {
    jsonrpc: '2.0',
    id: Date.now().toString(),
    method: 'aria2.addUri',
    params: [
      `token:${token}`,
      [url],
      {
        header: headers,
        ...(out ? { out } : {}),
      },
    ],
  };

  const response = await fetch(proxyRpcUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  return response.json();
}

export async function directDownload(url, out = '') {
  return aria2AddUri(url, [
    'X-Proxy-Download-Mode: direct',
  ], out);
}

export async function parseDownloadWithResolver(url, out = '') {
  return aria2AddUri(url, [
    'X-Proxy-Download-Mode: parse',
    'X-Proxy-Parse-Method: plugin_link_resolver',
  ], out);
}

export async function parseDownloadWithParser(url, out = '') {
  return aria2AddUri(url, [
    'X-Proxy-Download-Mode: parse',
    'X-Proxy-Parse-Method: plugin_parser',
  ], out);
}

export async function parseDownloadWithYtDlp(url, out = '', cookiesFile = '') {
  const headers = [
    'X-Proxy-Download-Mode: parse',
    'X-Proxy-Parse-Method: yt_dlp',
    'X-Proxy-Ytdlp-Format: b/best',
  ];

  if (cookiesFile) {
    headers.push(`X-Proxy-Ytdlp-Cookies-File: ${cookiesFile}`);
  }

  return aria2AddUri(url, headers, out);
}
