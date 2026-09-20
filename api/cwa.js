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

module.exports = async function handler(req, res) {

  // ===== CORS：允許 GitHub Pages 呼叫 Vercel API =====
  res.setHeader(
    'Access-Control-Allow-Origin',
    'https://xuan-weather.github.io'
  );
  res.setHeader(
    'Access-Control-Allow-Methods',
    'GET, OPTIONS'
  );
  res.setHeader(
    'Access-Control-Allow-Headers',
    'Content-Type'
  );

  // 處理瀏覽器 CORS 預檢
  if (req.method === 'OPTIONS') {
    return res.status(204).end();
  }

  // 只允許 GET
  if (req.method !== 'GET') {
    return res.status(405).json({
      success: false,
      error: 'Method Not Allowed'
    });
  }

  // 取得資料集名稱
  const dataset = String(req.query?.dataset || '').trim();

  if (!dataset) {
    return res.status(400).json({
      success: false,
      error: 'Missing dataset'
    });
  }

  // 防止任意資料集被代理
  if (!ALLOWED_DATASETS.has(dataset)) {
    return res.status(400).json({
      success: false,
      error: `Dataset not allowed: ${dataset}`
    });
  }

  // ===== CWA API Key：只放在 Vercel Server 端 =====
  const apiKey = String(process.env.CWA_API_KEY || '').trim();

  if (!apiKey) {
    return res.status(500).json({
      success: false,
      error: 'CWA_API_KEY is not configured'
    });
  }

  // ===== 官方 CWA API =====
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

    let data;

    try {
      data = JSON.parse(text);
    } catch (parseError) {
      return res.status(502).json({
        success: false,
        error: 'CWA API returned invalid JSON',
        upstreamStatus: upstream.status
      });
    }

    if (!upstream.ok) {
      return res.status(upstream.status).json(data);
    }

    // 不讓瀏覽器或 CDN 快取舊的 CWA 即時資料
    res.setHeader(
      'Cache-Control',
      'no-store, max-age=0, must-revalidate'
    );

    res.setHeader(
      'Content-Type',
      'application/json; charset=utf-8'
    );

    return res.status(200).send(JSON.stringify(data));

  } catch (error) {

    console.error('CWA API Error:', error);

    return res.status(502).json({
      success: false,
      error: 'Failed to fetch CWA API',
      message: String(error?.message || error)
    });
  }
};
