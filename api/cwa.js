// Vercel Serverless Function: CWA data proxy.
// The browser calls /api/cwa?dataset=<dataset-id>.
// Only the allowlisted datasets used by this dashboard are accepted.
const ALLOWED_DATASETS = new Set([
  'O-A0001-001', 'O-A0002-001', 'O-A0003-001',
  'F-C0032-001', 'W-C0033-001', 'W-C0034-001',
  'E-A0015-001', 'E-A0016-001'
]);

module.exports = async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const dataset = String(req.query?.dataset || '').trim();
  if (!ALLOWED_DATASETS.has(dataset)) {
    return res.status(400).json({ error: 'Invalid dataset' });
  }

  const upstreamUrl = `https://mingxuan.904037.xyz/api/v1/rest/datastore/${encodeURIComponent(dataset)}`;

  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store'
    });
    const text = await upstream.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; }
    catch (_) {
      return res.status(502).json({
        error: 'Bad Gateway',
        message: `CWA 上游回傳非 JSON（HTTP ${upstream.status}）。`
      });
    }

    if (!upstream.ok) {
      return res.status(upstream.status).json({
        error: 'CWA Upstream Error',
        message: data?.message || data?.error || `CWA 上游請求失敗（HTTP ${upstream.status}）。`
      });
    }

    res.setHeader('Cache-Control', 'no-store, max-age=0');
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    return res.status(200).send(JSON.stringify(data));
  } catch (error) {
    console.error('CWA proxy error:', error);
    return res.status(502).json({
      error: 'Bad Gateway',
      message: '無法連線至 CWA 資料服務，請稍後再試。'
    });
  }
};
