// api/cwa.js
// Xuan Weather｜中央氣象署官方即時同步 API
// 放置位置：GitHub 專案 /api/cwa.js
// Vercel endpoint：/api/cwa?dataset=O-A0003-001
// Vercel Environment Variable：CWA_API_KEY

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

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const dataset = String(req.query?.dataset || '').trim();

  if (!ALLOWED_DATASETS.has(dataset)) {
    return res.status(400).json({
      error: 'Invalid dataset',
      allowed: Array.from(ALLOWED_DATASETS)
    });
  }

  const apiKey = String(process.env.CWA_API_KEY || '').trim();

  if (!apiKey) {
    return res.status(500).json({
      error: 'CWA_API_KEY is not configured on Vercel'
    });
  }

  const upstreamUrl =
    `https://opendata.cwa.gov.tw/api/v1/rest/datastore/${encodeURIComponent(dataset)}`;

  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: apiKey
      },
      cache: 'no-store'
    });

    const text = await upstream.text();

    let data;
    try {
      data = JSON.parse(text);
    } catch {
      return res.status(502).json({
        error: 'CWA returned non-JSON response',
        status: upstream.status,
        preview: text.slice(0, 500)
      });
    }

    if (!upstream.ok) {
      return res.status(upstream.status).json({
        error: 'CWA API request failed',
        status: upstream.status,
        data
      });
    }

    res.setHeader('Cache-Control', 'no-store, max-age=0, must-revalidate');
    res.setHeader('CDN-Cache-Control', 'no-store');
    res.setHeader('Vercel-CDN-Cache-Control', 'no-store');
    res.setHeader('Pragma', 'no-cache');
    res.setHeader('Expires', '0');

    return res.status(200).json(data);
  } catch (error) {
    return res.status(500).json({
      error: 'CWA upstream request failed',
      message: error?.message || String(error)
    });
  }
}
