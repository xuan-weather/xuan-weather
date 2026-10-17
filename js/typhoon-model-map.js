/* Xuan Weather: render only verified coordinates from typhoon-model-tracks.json.
 * Requires Leaflet (L) to be loaded first. No synthetic/demo tracks are created.
 */
(function (global) {
  'use strict';
  const COLORS = {
    'ECMWF IFS':'#2e9cff','ECMWF AIFS':'#b35cff','AIFS ENS':'#28d7c3',
    'GFS':'#ff805e','GEFS':'#ff9f43','WN3C':'#00a77a','WN2C':'#28d7c3',
    'GEML':'#9be52f','AIGEFS':'#f044a1','AICON':'#765cff'
  };
  function safeText(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  async function attach(map, url, options) {
    if (!global.L || !map) throw new Error('Leaflet map 尚未初始化');
    const cfg = options || {};
    const layers = new Map();
    const data = await (await fetch(url, {cache:'no-store'})).json();
    const tracks = Array.isArray(data.tracks) ? data.tracks : [];
    tracks.forEach(track => {
      if (!Array.isArray(track.points) || track.points.length < 2) return;
      const pts = track.points.filter(p => Number.isFinite(+p.lat) && Number.isFinite(+p.lon) && +p.lat >= -90 && +p.lat <= 90 && +p.lon >= -180 && +p.lon <= 360);
      if (pts.length < 2) return;
      const color = COLORS[track.model] || '#dce6f1';
      const group = L.layerGroup();
      const line = L.polyline(pts.map(p => [+p.lat,+p.lon]), {color, weight:3, opacity:.95}).bindPopup(
        '<b>'+safeText(track.model)+'</b><br>'+safeText(track.stormName || track.stormId || '熱帶氣旋')+
        '<br>起報時間：'+safeText(track.cycleTime || '未知')+
        '<br>資料來源：<a href="'+safeText(track.source || '#')+'" target="_blank" rel="noopener">查看來源</a>');
      group.addLayer(line);
      pts.forEach(p => {
        if (+p.leadHours > 0 && +p.leadHours % 24 === 0) {
          L.circleMarker([+p.lat,+p.lon], {radius:3,color,weight:1,fillColor:color,fillOpacity:1})
            .bindTooltip('+'+p.leadHours+'h · '+safeText(track.model)).addTo(group);
        }
      });
      if (cfg.addToMap !== false) group.addTo(map);
      const key = track.model + '|' + (track.stormId || track.stormName || 'storm') + '|' + track.cycleTime;
      layers.set(key, {model:track.model, layer:group, track});
    });
    return {
      data, layers,
      setModelVisible(model, visible) { layers.forEach(item => { if(item.model===model){ if(visible) item.layer.addTo(map); else map.removeLayer(item.layer); } }); },
      showAll() { layers.forEach(item => item.layer.addTo(map)); },
      hideAll() { layers.forEach(item => map.removeLayer(item.layer)); },
      fitTracks() { const all=[]; layers.forEach(item=>item.layer.eachLayer(l=>{if(l.getLatLngs) {const ll=l.getLatLngs(); if(Array.isArray(ll)) ll.forEach(p=>{if(p && Number.isFinite(p.lat)) all.push(p);});}})); if(all.length) map.fitBounds(L.latLngBounds(all).pad(.08)); }
    };
  }
  global.XuanTyphoonTracks = {attach, colors:COLORS};
})(window);
