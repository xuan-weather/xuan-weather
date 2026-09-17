// Vercel Serverless Function: CWA data proxy
// GitHub Pages -> Vercel -> CWA Open Data
// CWA API Key 只存在 Vercel Environment Variables，不放在前端。

const ALLOWED_DATASETS = new Set([
  'O-A0001-001',
  'O-A0002-001',
  'O-A0003-001',
  'F-C0032-001',
  'W-C0033-001',
  'W-C0034-001',
  'E-A0015-001',
  'E-A0016-001'
]);

function setCors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader(
    'Access-Control-Allow-Headers',
    'Content-Type, Accept'
  );
  res.setHeader('Access-Control-Max-Age', '86400');
}

module.exports = async function handler(req, res) {
  setCors(res);

  // CORS 預檢
  if (req.method === 'OPTIONS') {
    return res.status(204).end();
  }

  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET, OPTIONS');

    return res.status(405).json({
      error: 'Method Not Allowed'
    });
  }

  const dataset = String(
    req.query?.dataset || ''
  ).trim();

  if (!ALLOWED_DATASETS.has(dataset)) {
    return res.status(400).json({
      error: 'Invalid dataset',
      message: '不允許的 CWA dataset。'
    });
  }

  // 從 Vercel Environment Variables 取得 CWA API Key
  const apiKey = String(
    process.env.CWA_API_KEY || ''
  ).trim();

  if (!apiKey) {
    return res.status(500).json({
      error: 'Server configuration error',
      message: 'Vercel 尚未設定 CWA_API_KEY。'
    });
  }

  // 官方中央氣象署 Open Data API
  const upstreamUrl =
    `https://opendata.cwa.gov.tw/api/v1/rest/datastore/${encodeURIComponent(dataset)}`;

  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
        'Authorization': apiKey
      },
      cache: 'no-store'
    });

    const text = await upstream.text();

    let data = null;

    try {
      const normalizedText = String(text || '')
        .replace(
          /^\s*(?:\uFEFF|\u200B|\u200C|\u200D)+/,
          ''
        )
        .trim();

      data = normalizedText
        ? JSON.parse(normalizedText)
        : null;

    } catch (error) {
      console.error(
        'CWA JSON parse error:',
        error
      );

      return res.status(502).json({
        error: 'Bad Gateway',
        message:
          `CWA 回傳內容無法解析為 JSON（HTTP ${upstream.status}）。`
      });
    }

    if (!upstream.ok) {
      return res.status(upstream.status).json({
        error: 'CWA Upstream Error',
        message:
          data?.message ||
          data?.error ||
          `CWA 上游請求失敗（HTTP ${upstream.status}）。`
      });
    }

    res.setHeader(
      'Cache-Control',
      'no-store, max-age=0'
    );

    res.setHeader(
      'Content-Type',
      'application/json; charset=utf-8'
    );

    return res
      .status(200)
      .send(JSON.stringify(data));

  } catch (error) {
    console.error(
      'CWA proxy error:',
      error
    );

    return res.status(502).json({
      error: 'Bad Gateway',
      message:
        '無法連線至中央氣象署 API，請稍後再試。'
    });
  }
};
