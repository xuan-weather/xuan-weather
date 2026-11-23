/* Xuan Weather station popup tabs v1 — additive enhancement only. */
(function(){
'use strict';
if(window.__xuanStationPopupTabsLoaded)return;
window.__xuanStationPopupTabsLoaded=true;
const css='.xuan-popup-tabbed{padding:9px!important;min-width:0}\n'+
'.xuan-popup-tab-pages{position:relative;min-height:240px;touch-action:pan-y}\n'+
'.xuan-popup-tab-page{display:none;min-height:230px}\n'+
'.xuan-popup-tab-page.is-active{display:block;animation:xuanPopupTabFade .16s ease-out}\n'+
'@keyframes xuanPopupTabFade{from{opacity:.55;transform:translateX(4px)}to{opacity:1;transform:translateX(0)}}\n'+
'.xuan-popup-tab-title{font-size:13px;font-weight:900;color:var(--popup-text,#0f172a);margin:2px 0 10px;padding-bottom:8px;border-bottom:1px solid rgba(148,163,184,.24)}\n'+
'.xuan-popup-extra-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}\n'+
'.xuan-popup-extra-card{display:flex;flex-direction:column;justify-content:center;gap:4px;min-width:0;min-height:54px;padding:8px;border:1px solid rgba(148,163,184,.2);border-radius:9px;background:rgba(148,163,184,.075);overflow-wrap:anywhere}\n'+
'.xuan-popup-extra-card span{font-size:10px;line-height:1.25;color:var(--popup-sub,#475569)}\n'+
'.xuan-popup-extra-card strong{font-size:13px;line-height:1.3;font-weight:850;color:var(--popup-text,#0f172a)}\n'+
'.xuan-popup-tab-note{margin-top:9px;font-size:9px;line-height:1.45;color:var(--text-muted,#64748b)}\n'+
'.xuan-popup-tab-nav{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:9px;padding-top:8px;border-top:1px solid rgba(148,163,184,.2)}\n'+
'.xuan-popup-tab-dots{display:flex;align-items:center;justify-content:center;gap:6px}\n'+
'.xuan-popup-tab-dot{width:7px;height:7px;min-width:7px;padding:0;border:0;border-radius:50%;background:#cbd5e1;opacity:.8;cursor:pointer}\n'+
'.xuan-popup-tab-dot.active{width:18px;border-radius:6px;background:#0ea5e9;opacity:1}\n'+
'.xuan-popup-tab-count{font-size:10px;font-weight:800;color:var(--text-muted,#64748b);white-space:nowrap}\n'+
'.xuan-popup-tab-arrow{width:22px;height:22px;border:0;border-radius:7px;background:rgba(148,163,184,.14);color:var(--popup-text,#0f172a);font-size:18px;line-height:1;cursor:pointer}\n'+
'.xuan-popup-tabbed .station-popup-history{margin-top:8px!important}\n'+
'@media(max-width:600px){.xuan-popup-tab-pages{min-height:236px}.xuan-popup-tab-page{min-height:226px}.xuan-popup-extra-card{min-height:50px;padding:7px}.xuan-popup-extra-card strong{font-size:12px}.xuan-popup-tab-dots{gap:5px}}';
const style=document.createElement('style');style.id='xuan-station-popup-tabbed-v1';style.textContent=css;document.head.appendChild(style);
function esc(v){const t=v==null?'--':String(v);return typeof window.escapeQuickRankingText==='function'?window.escapeQuickRankingText(t):t.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmt(v,d){const n=Number(v);return Number.isFinite(n)?n.toFixed(d==null?1:d):'--';}
function card(label,value){return '<div class="xuan-popup-extra-card"><span>'+label+'</span><strong>'+value+'</strong></div>';}
function page(title,body,note){return '<section class="xuan-popup-tab-page"><div class="xuan-popup-tab-title">'+title+'</div><div class="xuan-popup-extra-grid">'+body+'</div>'+(note?'<div class="xuan-popup-tab-note">'+note+'</div>':'')+'</section>';}
function getRecent(station){try{const id=window.getStationHistoryId(station);const src=typeof stationHistoryCache!=='undefined'?stationHistoryCache:{};const a=Array.isArray(src[id])?src[id]:[];const cut=Date.now()-86400000;return a.filter(p=>Number(p.ts)>=cut);}catch(e){return [];}}
function buildXuanStationPopupTabs(station,data,county,town,wxInfo,popForStation,mapRain,mapRain2d,mapRain3d){
const history=getRecent(station),temps=history.map(p=>Number(p.temp)).filter(n=>Number.isFinite(n)&&n!==0),hum=history.map(p=>Number(p.humidity)).filter(n=>Number.isFinite(n)&&n>=0&&n<=100),winds=history.map(p=>Number(p.wind)).filter(n=>Number.isFinite(n)&&n>=0),gusts=history.map(p=>Number(p.windGust)).filter(n=>Number.isFinite(n)&&n>=0);
const max=a=>a.length?Math.max.apply(null,a):NaN,min=a=>a.length?Math.min.apply(null,a):NaN,avg=a=>a.length?a.reduce((x,y)=>x+y,0)/a.length:NaN;
const coords=window.getStationLatLon(station);let nearest=null,nearestKm=Infinity;
try{const rows=typeof airQualityData!=='undefined'&&Array.isArray(airQualityData)?airQualityData:[];rows.forEach(raw=>{const r=window.normalizeAirQualityRecord(raw),lat=Number(r.Latitude),lon=Number(r.Longitude),aqi=Number(r.AQI);if(!Number.isFinite(lat)||!Number.isFinite(lon)||!Number.isFinite(aqi)||!Number.isFinite(coords.lat)||!Number.isFinite(coords.lon))return;const km=window.calculateDistance(coords.lat,coords.lon,lat,lon);if(Number.isFinite(km)&&km<nearestKm){nearestKm=km;nearest=r;}});}catch(e){}
const airOk=!!nearest&&nearestKm<=30,we=station.WeatherElement||{};
const pr=Number.parseFloat(we.AirPressure??we.AtmosphericPressure??we.Pressure??we.PRES??station.AirPressure),vis=Number.parseFloat(we.Visibility??we.VisibilityDistance??we.VIS??station.Visibility),pressureOk=Number.isFinite(pr)&&pr>=800&&pr<=1100,visibilityOk=Number.isFinite(vis)&&vis>=0;
let wx=null,minT=null,maxT=null,pop=NaN,period='依官方預報資料';
try{wx=typeof forecastWxData!=='undefined'?forecastWxData[county]:null;minT=typeof forecastMinTData!=='undefined'?forecastMinTData[county]:null;maxT=typeof forecastMaxTData!=='undefined'?forecastMaxTData[county]:null;const rec=window.getTownshipPopRecord(county,town);if(rec&&Number.isFinite(Number(rec.pop)))pop=Number(rec.pop);if(rec&&rec.start&&rec.end)period=window.formatObsTime(rec.start)+' ～ '+window.formatObsTime(rec.end);else if(typeof countyForecastPeriodStart!=='undefined'&&countyForecastPeriodStart&&countyForecastPeriodEnd)period=window.formatObsTime(countyForecastPeriodStart)+' ～ '+window.formatObsTime(countyForecastPeriodEnd);}catch(e){}
const obsDate=data.obsTime?new Date(data.obsTime):null,obsValid=!!obsDate&&Number.isFinite(obsDate.getTime()),obsAge=obsValid?(Date.now()-obsDate.getTime())/60000:Infinity,obsStatus=!obsValid?'unknown':obsAge>90?'stale':'normal';
const obs=obsValid?new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',hour:'2-digit',minute:'2-digit',hour12:false}).format(obsDate):'--:--';
const apparent=window.calculateObservedApparentTemperature(station,data),windDir=window.getWindDirectionText(data.windDir),windLevel=window.getWindLevel(data.windSpeed),gustLevel=window.getWindLevel(data.windGust);
const current='<div class="station-popup-head"><div class="station-popup-pin">📍</div><div class="station-popup-title"><div class="station-popup-name-row"><h4>'+esc(station.StationName||'氣象測站')+'</h4><div class="station-popup-update-status '+obsStatus+'"><span class="station-popup-update-dot"></span><span class="station-popup-update-time">'+obs+'</span></div></div><div class="station-popup-area">'+esc(county+town||'臺灣')+' · CWA 即時觀測</div></div></div>'+
'<div class="station-popup-hero"><div><div class="station-popup-hero-label">🌡️ 目前氣溫</div><div class="station-popup-temp">'+(Number.isFinite(data.temp)?data.temp.toFixed(1):'--')+'<small> °C</small></div></div><div class="station-popup-weather"><strong>'+esc(wxInfo&&wxInfo.text||'--')+'</strong><span>目前天氣</span></div></div>'+
'<div class="station-popup-metrics"><p><b>🌧️ 降雨機率</b><strong>'+esc(popForStation)+' %</strong></p><p><b>💧 相對濕度</b><strong>'+(data.humidity!=='--'?esc(data.humidity)+' %':'--')+'</strong></p><p><b>🌡️ 體感溫度（估算）</b><strong>'+(Number.isFinite(apparent)?apparent.toFixed(1)+' °C':'--')+'</strong></p><p><b>☔ 日雨量</b><strong>'+(Number.isFinite(mapRain)?fmt(mapRain)+' mm':'--')+'</strong></p><p><b>🗓️ 2 日雨量</b><strong>'+(Number.isFinite(mapRain2d)?fmt(mapRain2d)+' mm':'--')+'</strong></p><p><b>🗓️ 3 日雨量</b><strong>'+(Number.isFinite(mapRain3d)?fmt(mapRain3d)+' mm':'--')+'</strong></p><p><b>💨 風向風速</b><strong>'+esc(windDir)+' '+(Number.isFinite(data.windSpeed)?fmt(data.windSpeed)+' m/s（'+esc(windLevel)+'）':'--')+'</strong></p><p><b>🌬️ 最大陣風</b><strong>'+(Number.isFinite(data.windGust)?fmt(data.windGust)+' m/s（'+esc(gustLevel)+'）':'--')+'</strong></p></div>'+
'<button class="station-popup-history" onclick="openStationHistory(weatherStationData.find(s => String(s.StationId) === \''+String(station.StationId||'').replace(/'/g,"\\'")+'\'))"><i class="fas fa-chart-line"></i> 查看最近 24 小時歷史趨勢</button>';
const air=airOk?card('AQI 空氣品質指數',esc(nearest.AQI))+card('空品等級',esc(nearest.Status||'--'))+card('PM2.5',esc(nearest['PM2.5'])+' μg/m³')+card('PM10',esc(nearest.PM10)+' μg/m³')+card('主要污染物',esc(nearest.Pollutant))+card('最近空品測站',esc(nearest.SiteName)):card('附近空品測站','暫無資料')+card('搜尋範圍','30 公里');
const airNote=airOk?'最近環境部空品測站：'+esc(nearest.SiteName)+'，距離約 '+nearestKm.toFixed(1)+' 公里。發布時間：'+esc(nearest.publishtime):'附近 30 公里內沒有有效 AQI 測站；空品資料來自環境部配對測站，不代表此氣象站直接量測。';
const pressure=card('目前氣壓',pressureOk?pr.toFixed(1)+' hPa':'暫無資料')+card('測站觀測時間',esc(data.obsTime||'--'));
const visibility=card('目前能見度',visibilityOk?vis.toFixed(1)+' km':'暫無資料')+card('測站觀測時間',esc(data.obsTime||'--'));
const extremes=card('過去 24 小時最高溫',temps.length?fmt(max(temps))+' °C':'暫無資料')+card('過去 24 小時最低溫',temps.length?fmt(min(temps))+' °C':'暫無資料')+card('過去 24 小時最大陣風',gusts.length?fmt(max(gusts))+' m/s':'暫無資料')+card('過去 24 小時最高風速',winds.length?fmt(max(winds))+' m/s':'暫無資料')+card('有效歷史樣本',history.length+' 筆');
const forecast=card('預報天氣',esc(wx&&wx.text||'暫無資料'))+card('預報最低溫',minT!=null&&Number.isFinite(Number(minT))?fmt(minT)+' °C':'暫無資料')+card('預報最高溫',maxT!=null&&Number.isFinite(Number(maxT))?fmt(maxT)+' °C':'暫無資料')+card('降雨機率',Number.isFinite(pop)?Math.round(pop)+' %':'暫無資料')+card('預報地區',esc(county+town))+card('預報時段',esc(period));
const stats=card('24 小時平均氣溫',temps.length?fmt(avg(temps))+' °C':'暫無資料')+card('24 小時溫差',temps.length?fmt(max(temps)-min(temps))+' °C':'暫無資料')+card('24 小時平均濕度',hum.length?fmt(avg(hum),0)+' %':'暫無資料')+card('24 小時平均風速',winds.length?fmt(avg(winds))+' m/s':'暫無資料')+card('今日累積雨量',Number.isFinite(mapRain)?fmt(mapRain)+' mm':'暫無資料')+card('歷史樣本數',history.length+' 筆');
const pages=['<section class="xuan-popup-tab-page is-active">'+current+'</section>',page('😷 空氣品質',air,airNote),page('🌡️ 氣壓監測',pressure,'僅顯示 CWA 測站原始氣壓欄位，沒有原始觀測值時不推算。'),page('🌫️ 能見度監測',visibility,'僅顯示 CWA 測站原始能見度欄位，沒有原始觀測值時不推算。'),page('🏆 測站歷史極值',extremes,history.length?'依本站保存的近 24 小時紀錄計算，並非氣象署歷年極值。':'歷史樣本尚在累積。'),page('🕒 未來天氣預報',forecast,'沿用現有 CWA 鄉鎮／縣市預報資料；預報與即時觀測分開呈現。'),page('📅 天氣統計',stats,'統計本站保存的最近 24 小時樣本；雨量採今日累積值，不重複加總。')];
return '<div class="popup-card xuan-station-popup xuan-popup-v2 xuan-popup-tabbed"><div class="xuan-popup-tab-pages">'+pages.join('')+'</div><div class="xuan-popup-tab-nav" aria-label="測站資訊分頁"><button type="button" class="xuan-popup-tab-arrow" onclick="xuanSetStationPopupTab(this,\\'prev\\')" aria-label="上一頁">‹</button><div class="xuan-popup-tab-dots">'+pages.map((p,i)=>'<button type="button" class="xuan-popup-tab-dot '+(i===0?'active':'')+'" onclick="xuanSetStationPopupTab(this,'+i+')" aria-label="第 '+(i+1)+' 頁"></button>').join('')+'</div><span class="xuan-popup-tab-count">1 / 7</span><button type="button" class="xuan-popup-tab-arrow" onclick="xuanSetStationPopupTab(this,\\'next\\')" aria-label="下一頁">›</button></div></div>';
}
window.buildXuanStationPopupTabs=buildXuanStationPopupTabs;
window.xuanSetStationPopupTab=function(button,direction){
const root=button.closest('.xuan-popup-tabbed')||button.closest('.leaflet-popup-content')?.querySelector('.xuan-popup-tabbed');if(!root)return;
const pages=Array.from(root.querySelectorAll('.xuan-popup-tab-page')),dots=Array.from(root.querySelectorAll('.xuan-popup-tab-dot')),current=Math.max(0,pages.findIndex(p=>p.classList.contains('is-active')));
const next=direction==='prev'||direction==='next'?Math.max(0,Math.min(pages.length-1,current+(direction==='next'?1:-1))):Math.max(0,Math.min(pages.length-1,Number(direction)||0));
pages.forEach((p,i)=>p.classList.toggle('is-active',i===next));dots.forEach((d,i)=>d.classList.toggle('active',i===next));const count=root.querySelector('.xuan-popup-tab-count');if(count)count.textContent=(next+1)+' / '+pages.length;
};
(function(){let sx=0,sy=0,root=null;document.addEventListener('touchstart',e=>{const p=e.target.closest&&e.target.closest('.xuan-popup-tab-page');if(!p)return;root=p.closest('.xuan-popup-tabbed');sx=e.touches[0].clientX;sy=e.touches[0].clientY;},{passive:true});document.addEventListener('touchend',e=>{if(!root)return;const dx=e.changedTouches[0].clientX-sx,dy=e.changedTouches[0].clientY-sy;if(Math.abs(dx)>45&&Math.abs(dx)>Math.abs(dy)*1.2){const active=root.querySelector('.xuan-popup-tab-dot.active');window.xuanSetStationPopupTab(active||root,dx<0?'next':'prev');}root=null;},{passive:true});})();
})();