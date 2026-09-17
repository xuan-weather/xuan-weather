// Vercel Serverless Function: CWA data proxy
// GitHub Pages -> Vercel -> CWA
// 只允許儀表板使用的 CWA 資料集，API Key 不放在前端。

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
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept');
  res.setHeader('Access-Control-Max-Age', '86400');
}

module.exports = async function handler(req, res) {
  // 允許 GitHub Pages 跨網域請求 Vercel API
  setCors(res);

  // 瀏覽器 CORS 預檢
  if (req.method === 'OPTIONS') {
    return res.status(204).end();
  }

  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET, OPTIONS');
    return res.status(405).json({
      error: 'Method Not Allowed'
    });
  }

  const dataset = String(req.query?.dataset || '').trim();

  if (!ALLOWED_DATASETS.has(dataset)) {
    return res.status(400).json({
      error: 'Invalid dataset',
      message: '不允許的 CWA dataset。'
    });
  }

  const upstreamUrl =
    `https://mingxuan.904037.xyz/api/v1/rest/datastore/${encodeURIComponent(dataset)}`;

  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'GET',
      headers: {
        Accept: 'application/json'
      },
      cache: 'no-store'
    });

    const text = await upstream.text();

    let data = null;

    try {
      const normalizedText = String(text || '')
        .replace(/^\s*(?:\uFEFF|\u200B|\u200C|\u200D)+/, '')
        .trim();

      data = normalizedText
        ? JSON.parse(normalizedText)
        : null;

    } catch (error) {
      console.error('CWA JSON parse error:', error);

      return res.status(502).json({
        error: 'Bad Gateway',
        message: `CWA 上游回傳非 JSON（HTTP ${upstream.status}）。`
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
    console.error('CWA proxy error:', error);

    return res.status(502).json({
      error: 'Bad Gateway',
      message: '無法連線至 CWA 資料服務，請稍後再試。'
    });
  }
};
