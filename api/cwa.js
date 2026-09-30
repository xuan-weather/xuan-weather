// Vercel Serverless Function: Xuan Weather → CWA official realtime proxy
// Browser -> /api/cwa?dataset=O-A0003-001
// CWA_API_KEY stays in Vercel Environment Variables only.

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
  res.setHeader('Vary', 'Origin');
}

module.exports = async function handler(req, res) {
  setCors(res);

  if (req.method === 'OPTIONS') {
    return res.status(204).end();
  }

  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET, OPTIONS');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const dataset = String(req.query?.dataset || '').trim();

  if (!ALLOWED_DATASETS.has(dataset)) {
    return res.status(400).json({
      error: 'Invalid dataset',
      message: '不允許的 CWA dataset。'
    });
  }

  const apiKey = String(process.env.CWA_API_KEY || '').trim();

  if (!apiKey) {
    return res.status(500).json({
      error: 'Server configuration error',
      message: 'Vercel 尚未設定 CWA_API_KEY。'
    });
  }

  const nonce = Date.now().toString();

  const upstreamUrl =
    `https://opendata.cwa.gov.tw/api/v1/rest/datastore/${encodeURIComponent(dataset)}?Authorization=${encodeURIComponent(apiKey)}&format=JSON&_=${nonce}`;

  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        'Cache-Control': 'no-cache',
        Pragma: 'no-cache'
      },
      cache: 'no-store'
    });

    const text = await upstream.text();

    let data = null;

    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      return res.status(502).json({
        error: 'Bad Gateway',
        message: `CWA 回傳非 JSON（HTTP ${upstream.status}）。`
      });
    }

    if (!upstream.ok) {
      return res.status(upstream.status).json({
        error: 'CWA API Error',
        message:
          data?.message ||
          data?.error ||
          data?.result?.message ||
          `CWA API 請求失敗（HTTP ${upstream.status}）。`
      });
    }

    res.setHeader(
      'Cache-Control',
      'no-store, max-age=0, must-revalidate'
    );

    res.setHeader('CDN-Cache-Control', 'no-store');
    res.setHeader('Vercel-CDN-Cache-Control', 'no-store');
    res.setHeader('Pragma', 'no-cache');
    res.setHeader('Expires', '0');
    res.setHeader(
      'Content-Type',
      'application/json; charset=utf-8'
    );

    return res.status(200).send(JSON.stringify(data));

  } catch (error) {

    console.error('CWA proxy error:', error);

    return res.status(502).json({
      error: 'Bad Gateway',
      message: '無法連線至中央氣象署資料服務，請稍後再試。'
    });
  }
};
