// Vercel Serverless Function
// Environment variable required on the server only:
// MOENV_API_KEY=<your Environment Ministry API Key>

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const apiKey = String(process.env.MOENV_API_KEY || '').trim();

  if (!apiKey) {
    return res.status(500).json({
      error: 'Server configuration error',
      message: '後端尚未設定 MOENV_API_KEY。'
    });
  }

  const params = new URLSearchParams({
    format: 'json',
    offset: '0',
    limit: '1000',
    api_key: apiKey
  });

  const upstreamUrl =
    `https://data.moenv.gov.tw/api/v2/aqx_p_432?${params.toString()}`;

  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store'
    });

    const text = await upstream.text();

    let data;

    try {
      // MOENV may return UTF-8 BOM before the JSON body.
      // Strip BOM/leading whitespace before parsing so a valid HTTP 200
      // JSON response is not incorrectly reported as "non-JSON".
      const normalizedText = String(text || '')
        .replace(/^\uFEFF/, '')
        .trim();

      data = normalizedText ? JSON.parse(normalizedText) : null;
    } catch {
      return res.status(502).json({
        error: 'Bad Gateway',
        message: `環境部 API 回傳非 JSON（HTTP ${upstream.status}）。`
      });
    }

    // Never return the API key or upstream request URL to the browser.
    if (!upstream.ok) {
      const message =
        data?.message ||
        data?.error ||
        data?.detail ||
        '環境部 API 請求失敗。';

      return res.status(upstream.status).json({
        error: 'MOENV API Error',
        message
      });
    }

    res.setHeader('Cache-Control', 'no-store, max-age=0');
    res.setHeader(
      'Content-Type',
      'application/json; charset=utf-8'
    );

    return res.status(200).send(JSON.stringify(data));

  } catch (error) {
    console.error('MOENV AQI proxy error:', error);

    return res.status(502).json({
      error: 'Bad Gateway',
      message: '無法連線至環境部 AQI API，請稍後再試。'
    });
  }
}
