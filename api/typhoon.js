export default async function handler(req, res) {
  try {
    const key = process.env.CWA_API_KEY;
    if (!key) {
      return res.status(500).json({
        ok: false,
        error: "CWA_API_KEY 尚未設定"
      });
    }

    // 中央氣象署「熱帶氣旋分析與預報」：
    // 包含過去位置、目前狀況與未來預報位置。
    const url =
      "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-005" +
      "?Authorization=" + encodeURIComponent(key) +
      "&format=XML";

    const response = await fetch(url, {
      headers: { "Accept": "application/xml,text/xml,*/*" },
      cache: "no-store"
    });

    if (!response.ok) {
      return res.status(response.status).json({
        ok: false,
        error: `CWA HTTP ${response.status}`
      });
    }

    const xml = await response.text();

    if (!xml || !xml.includes("tropicalCyclones")) {
      return res.status(502).json({
        ok: false,
        error: "中央氣象署回傳格式不是預期的熱帶氣旋 XML"
      });
    }

    const clean = (v = "") =>
      v.replace(/<!\\[CDATA\\[|\\]\\]>/g, "")
       .replace(/&amp;/g, "&")
       .replace(/&lt;/g, "<")
       .replace(/&gt;/g, ">")
       .replace(/&quot;/g, '"')
       .trim();

    const tag = (block, name, attr = "") => {
      const re = new RegExp(
        `<${name}${attr ? `\\s+${attr}` : ""}[^>]*>([\\s\\S]*?)<\\/${name}>`,
        "i"
      );
      const m = block.match(re);
      return m ? clean(m[1]) : "";
    };

    const selfClosingAttr = (block, name, attrName) => {
      const re = new RegExp(
        `<${name}[^>]*\\s${attrName}=["']([^"']+)["'][^>]*/?>`,
        "i"
      );
      const m = block.match(re);
      return m ? clean(m[1]) : "";
    };

    const num = v => {
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };

    const pos = v => {
      const m = String(v || "").match(/(-?\\d+(?:\\.\\d+)?)\\s*,\\s*(-?\\d+(?:\\.\\d+)?)/);
      return m ? [Number(m[1]), Number(m[2])] : null;
    };

    const sectionBlocks = xml.match(/<tropicalCyclone[\\s\\S]*?<\\/tropicalCyclone>/gi) || [];
    const cyclones = [];

    for (const block of sectionBlocks) {
      const englishName =
        tag(block, "typhoon_name") ||
        tag(block, "typhoonName") ||
        tag(block, "international_name") || "";

      const chineseName =
        tag(block, "cwa_typhoon_name") ||
        tag(block, "cwaTyphoonName") ||
        tag(block, "chinese_name") || "";

      const typhoonNo =
        tag(block, "cwa_ty_no") ||
        tag(block, "cwa_typhoon_no") ||
        tag(block, "typhoon_no") || "";

      const analyses = [];
      const analysisBlocks = block.match(/<analysis>[\\s\\S]*?<\\/analysis>/gi) || [];
      for (const a of analysisBlocks) {
        const p = pos(tag(a, "position"));
        if (!p) continue;
        analyses.push({
          time: tag(a, "time"),
          lat: p[0],
          lon: p[1],
          wind: num(selfClosingAttr(a, "max_winds", "unit")) !== null
            ? num(tag(a, "max_winds"))
            : num(tag(a, "max_winds")),
          gust: num(tag(a, "gust")),
          pressure: num(tag(a, "pressure")),
          r7: num(tag(a, "radius_of_15mps")),
          scale: tag(a, "scale")
        });
      }

      const forecasts = [];
      const predictionBlocks = block.match(/<prediction>[\\s\\S]*?<\\/prediction>/gi) || [];
      for (const f of predictionBlocks) {
        const p = pos(tag(f, "position"));
        if (!p) continue;
        forecasts.push({
          time: tag(f, "time"),
          lat: p[0],
          lon: p[1],
          wind: num(tag(f, "max_winds")),
          gust: num(tag(f, "gust")),
          pressure: num(tag(f, "pressure")),
          r7: num(tag(f, "radius_of_15mps"))
        });
      }

      if (!analyses.length && !forecasts.length) continue;

      const current = analyses[analyses.length - 1] || forecasts[0];

      cyclones.push({
        number: typhoonNo,
        name: chineseName || englishName || "未命名",
        englishName: englishName || "",
        status: current.scale || "熱帶氣旋",
        pressure: current.pressure,
        wind: current.wind,
        gust: current.gust,
        r7: current.r7,
        lat: current.lat,
        lon: current.lon,
        history: analyses.map(x => [x.lat, x.lon]),
        forecast: forecasts.map(x => [x.lat, x.lon]),
        analysis: analyses,
        forecastDetail: forecasts
      });
    }

    const updatedAt =
      tag(xml, "identifier") ||
      tag(xml, "Identifier") ||
      new Date().toISOString();

    return res.status(200)
      .setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600")
      .json({
        ok: true,
        source: "中央氣象署",
        updatedAt,
        typhoons: cyclones,
        typhoon: cyclones[0] || null
      });

  } catch (error) {
    return res.status(500).json({
      ok: false,
      error: "取得颱風資料失敗",
      detail: error?.message || String(error)
    });
  }
}
