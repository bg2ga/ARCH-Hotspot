# -*- coding: utf-8 -*-
"""全新自包含控制台（推翻重做，干净统一，只依赖现有 /api/* 接口）"""

CONSOLE_CSS = r"""
:root{--bg:#0b1220;--card:#111d33;--card2:#0c1526;--border:#1e2b44;--border2:#2a3d5c;
--txt:#eaf2ff;--dim:#8fa6c4;--muted:#6f86a6;
--mono:Consolas,"Cascadia Mono","DejaVu Sans Mono",monospace;
--acc:#22d3ee;--ok:#10b981;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:var(--bg);color:var(--txt);font:13.5px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;display:flex;min-height:100vh}
.side{width:224px;flex:0 0 224px;background:#0a111e;border-right:1px solid var(--border);padding:14px 10px;overflow-y:auto;position:sticky;top:0;height:100vh}
.brand{display:flex;align-items:center;gap:10px;padding:4px 6px 14px;border-bottom:1px solid var(--border);margin-bottom:10px}
.brand .logo{width:38px;height:38px;border-radius:50%;flex:0 0 38px;display:grid;place-items:center;background:linear-gradient(135deg,#0ea5e9,#8b5cf6);color:#fff;font-weight:800;font-size:15px}
.brand .nm{font-size:14.5px;font-weight:800;color:#fff;line-height:1.2}
.brand .sub{font-size:10.5px;color:var(--muted)}
.nav{display:flex;flex-direction:column;gap:2px}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:8px;color:#c8d2df;font-size:13.5px;font-weight:600;cursor:pointer;transition:.16s;user-select:none}
.nav a:hover{background:#101a2e;color:#fff}
.nav a.on{background:linear-gradient(135deg,#0e7490,#7c3aed);color:#fff;font-weight:700}
.nav a .ico{width:17px;text-align:center;font-size:14px}
.main{flex:1;padding:18px 20px 40px;overflow-y:auto;min-width:0}
.topbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.topbar h1{font-size:18px;font-weight:750;letter-spacing:.3px}
.topbar .spacer{flex:1}
.badge{font-size:11px;padding:4px 11px;border-radius:99px;background:var(--card2);border:1px solid var(--border);color:var(--dim);font-weight:600}
.badge.ok{background:#0e2f22;border-color:#1d6b4a;color:#4ade80}
.badge.bad{background:#331616;border-color:#7f2222;color:#f87171}
.badge.warn{background:#33280e;border-color:#7a5a1d;color:#fbbf24}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;border-bottom:1px solid var(--border)}
.tab{padding:9px 17px;font-size:13.5px;font-weight:650;color:var(--dim);cursor:pointer;border:1px solid transparent;border-bottom:none;border-radius:9px 9px 0 0;transition:.15s;user-select:none}
.tab:hover{color:var(--txt);background:var(--card)}
.tab.on{color:#fff;background:var(--card);border-color:var(--border);margin-bottom:-1px}
.pane{display:none}
.pane.on{display:block}
.card{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--border);border-radius:12px;padding:15px;margin-bottom:14px}
.card h2{font-size:13px;font-weight:700;color:var(--dim);margin-bottom:12px;letter-spacing:.4px;display:flex;align-items:center;gap:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
@media(max-width:720px){.grid{grid-template-columns:1fr}}
.ch{border:1px solid var(--border);border-radius:11px;padding:13px 14px;background:linear-gradient(180deg,var(--card),var(--card2));transition:.16s}
.ch:hover{border-color:var(--border2)}
.ch.on{border-color:rgba(16,185,129,.4)}
.ch .hd{display:flex;align-items:center;gap:9px;margin-bottom:10px}
.ch .dot{width:8px;height:8px;border-radius:99px;flex:0 0 8px}
.ch .n{font-size:13px;font-weight:800;color:#7dd3fc;font-family:var(--mono)}
.ch .mode{margin-left:auto;display:flex;align-items:center;gap:8px;font-weight:800;font-size:14px;color:#fff;font-family:var(--mono)}
.ch .mode svg{width:34px;height:34px;flex:0 0 34px}
.ch .freq{display:flex;gap:12px;font-family:var(--mono);margin-bottom:10px}
.ch .freq .k{font-size:10px;color:var(--muted);font-weight:700;letter-spacing:.5px;margin-bottom:1px}
.ch .freq .v{font-size:13px;color:var(--txt);font-weight:700}
.ch .freq .col{flex:1}
.ch .ops{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
select,input[type=number]{background:var(--card2);border:1px solid var(--border);color:var(--txt);padding:6px 9px;border-radius:8px;font-size:12.5px;font-family:inherit}
select:focus,input:focus{outline:none;border-color:var(--acc)}
select.grow{flex:1;min-width:0}
input[type=number]{font-family:var(--mono)}
.btn{cursor:pointer;border:1px solid var(--border);background:var(--card);color:var(--txt);padding:7px 13px;border-radius:8px;font-size:12.5px;font-weight:650;font-family:inherit;transition:.14s;white-space:nowrap}
.btn:hover{border-color:var(--border2);color:#fff}
.btn:active{transform:translateY(1px)}
.btn.sm{padding:5px 10px;font-size:11.5px}
.btn.g{background:#0e2f22;border-color:#1d6b4a;color:#a7f3d0}
.btn.r{background:#331616;border-color:#7f2222;color:#fecaca}
.btn.b{background:#0a2733;border-color:#155e75;color:#7dd3fc}
.kv{display:flex;gap:8px;flex-wrap:wrap}
.kv .item{flex:1;min-width:120px;background:var(--card2);border:1px solid var(--border);border-radius:9px;padding:10px 12px}
.kv .k{font-size:10px;color:var(--muted);font-weight:700;letter-spacing:.5px;text-transform:uppercase}
.kv .v{font-size:15px;font-weight:750;color:var(--txt);margin-top:2px;font-family:var(--mono)}
.qso-item{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:9px;background:var(--card2);border:1px solid var(--border);margin-bottom:7px;flex-wrap:wrap}
.qso-call{font-weight:800;font-size:13.5px;color:#fff;font-family:var(--mono)}
.qso-tag{font-size:10.5px;padding:2px 8px;border-radius:99px;font-weight:700}
.qso-tag.mode{background:#0a2733;border:1px solid #155e75;color:#7dd3fc}
.qso-tag.src{background:#171c2e;border:1px solid #26354f;color:var(--dim)}
.qso-time{margin-left:auto;font-size:11px;color:var(--muted);font-family:var(--mono)}
.qso-meta{font-size:11px;color:var(--muted)}
.qso-empty{padding:16px;text-align:center;color:var(--muted);font-size:12px}
.log{background:#070d18;border:1px solid var(--border);border-radius:10px;padding:12px;max-height:460px;overflow:auto;font-family:var(--mono);font-size:11.5px;line-height:1.6;color:var(--dim);white-space:pre-wrap}
.hint{font-size:11px;color:var(--muted);line-height:1.6}
.note{background:var(--card2);border:1px solid var(--border);border-radius:9px;padding:11px 13px;font-size:11.5px;color:var(--dim);line-height:1.7}
.note b{color:#6ee7b7}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.mt{margin-top:10px}.mb{margin-bottom:10px}
"""

CONSOLE_BODY = r"""
<aside class="side">
  <div class="brand">
    <div class="logo">M</div>
    <div><div class="nm">ARCH 射频核心</div><div class="sub" id="bCallsign">—</div></div>
  </div>
  <nav class="nav" id="nav">
    <a data-p="overview" class="on"><span class="ico">📊</span>总览</a>
    <a data-p="rfmode"><span class="ico">📻</span>射频模式</a>
    <a data-p="qso"><span class="ico">📡</span>通联</a>
    <a data-p="log"><span class="ico">📜</span>运行日志</a>
    <a href="/fmo"><span class="ico">🔗</span>FMO 桥接</a>
  </nav>
</aside>

<main class="main">
  <div class="topbar">
    <h1 id="pgTitle">总览</h1>
    <span class="spacer"></span>
    <span class="badge" id="bEng">引擎 —</span>
    <span class="badge" id="bCh">通道 —</span>
    <span class="badge" id="bSdr">SDR —</span>
    <button class="btn b" onclick="doRefresh()">⟳ 刷新</button>
  </div>

  <!-- ============ 总览 ============ -->
  <div class="pane on" id="p-overview">
    <div class="kv" id="ovStats"></div>
    <div class="card mt">
      <h2>频率模式列表</h2>
      <div class="grid" id="ovChans"><div class="qso-empty">读取中…</div></div>
    </div>
    <div class="card">
      <h2>实时通联</h2>
      <div id="ovQso"><div class="qso-empty">读取中…</div></div>
    </div>
  </div>

  <!-- ============ 射频模式 ============ -->
  <div class="pane" id="p-rfmode">
    <div class="card">
      <h2>射频通道</h2>
      <div class="grid" id="rfChans"><div class="qso-empty">读取中…</div></div>
    </div>
    <div class="card">
      <h2>频率设置</h2>
      <div class="note mb">修改后点「保存并应用」：写入 MMDVM-Multi.ini 与各通道 ini，并只重启受影响通道。频率须为 25 kHz 整数倍。</div>
      <div id="rfFreq"></div>
      <div class="row mt">
        <button class="btn g" onclick="saveFreq(false)">💾 仅保存</button>
        <button class="btn g" onclick="saveFreq(true)">⚡ 保存并应用</button>
        <span class="hint" id="rfFreqMsg"></span>
      </div>
    </div>
    <div class="card">
      <h2>硬件基础配置（RF & Station）</h2>
      <div class="note">全局射频载波与硬件基础配置保持原实现不变。</div>
      <div class="row mt">
        <button class="btn b" onclick="openHw()">🔧 打开硬件基础配置</button>
        <button class="btn" onclick="embedHw()">📄 本页内嵌</button>
      </div>
      <div id="hwBox" class="mt"></div>
    </div>
  </div>

  <!-- ============ 通联 ============ -->
  <div class="pane" id="p-qso">
    <div class="card"><h2>实时通联列表</h2><div id="qsoList"><div class="qso-empty">读取中…</div></div></div>
  </div>

  <!-- ============ 运行日志 ============ -->
  <div class="pane" id="p-log">
    <div class="card">
      <h2>运行日志</h2>
      <div class="row mb">
        <select id="logCh"></select>
        <select id="logSvc">
          <option value="mmdvmhost">Host</option>
          <option value="mmdvm-iq">IQ</option>
          <option value="mmdvm-multi">引擎</option>
        </select>
        <button class="btn" onclick="loadLog()">⟳ 刷新</button>
        <label class="hint" style="display:flex;align-items:center;gap:5px"><input type="checkbox" id="logAuto" checked>自动刷新 5s</label>
      </div>
      <div class="log" id="logBox">（选择后自动加载）</div>
    </div>
  </div>
</main>
"""

CONSOLE_JS = r"""
var $=function(id){return document.getElementById(id);};
var MODES=['DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG','NONE'];
var CACHE=null;
function icon(m){
  var m2=String(m||'').toUpperCase();
  var col={DMR:'#0ea5e9','D-STAR':'#8b5cf6',YSF:'#f97316',NXDN:'#2563eb',P25:'#e11d48',FM:'#10b981',POCSAG:'#f59e0b'}[m2]||'#64748b';
  var g='';
  if(m2==='DMR')g='<rect x="9" y="15" width="22" height="4" rx="2" fill="#fff"/><rect x="9" y="23" width="14" height="4" rx="2" fill="#fff" opacity=".75"/>';
  else if(m2==='D-STAR')g='<path d="M20 10l2.6 6.1 6.4.6-4.9 4.3 1.5 6.4L20 24l-5.6 3.4 1.5-6.4-4.9-4.3 6.4-.6z" fill="#fff"/>';
  else if(m2==='YSF')g='<path d="M20 10l8 10-8 10-8-10z" fill="#fff"/>';
  else if(m2==='NXDN')g='<path d="M13 27V13h4.2l5.6 8V13H27v14h-4.2l-5.6-8v8z" fill="#fff"/>';
  else if(m2==='P25')g='<path d="M20 10l9 3.4v7.1c0 5-3.7 8.2-9 9.5-5.3-1.3-9-4.5-9-9.5v-7.1z" fill="#fff"/>';
  else if(m2==='FM')g='<path d="M9 20c2.6-6 5.2-6 7.8 0s5.2 6 7.8 0 5.2-6 5.4 0" stroke="#fff" stroke-width="3" fill="none" stroke-linecap="round"/>';
  else if(m2==='POCSAG')g='<rect x="13" y="11" width="14" height="18" rx="3" fill="#fff"/><rect x="16" y="14" width="8" height="6" rx="1" fill="'+col+'"/>';
  else g='<circle cx="20" cy="20" r="6" fill="#fff" opacity=".7"/>';
  return '<svg viewBox="0 0 40 40" width="34" height="34" xmlns="http://www.w3.org/2000/svg"><rect x="2" y="2" width="36" height="36" rx="9" fill="'+col+'"/>'+g+'</svg>';
}
function mhz(hz){hz=parseInt(hz,10)||0;return hz>0?(hz/1e6).toFixed(4)+' MHz':'—';}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function toast(msg,ok){
  var t=$('toast');
  if(!t){t=document.createElement('div');t.id='toast';document.body.appendChild(t);}
  t.textContent=String(msg==null?'':msg);
  t.style.cssText='position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:99999;'
    +'background:rgba(13,21,32,.97);border:1px solid '+(ok===false?'rgba(239,68,68,.6)':'#2b3f57')+';'
    +'color:#eaf2ff;padding:18px 32px;border-radius:13px;text-align:center;max-width:min(560px,86vw);'
    +'font-size:15px;font-weight:600;line-height:1.6;display:block;opacity:0;pointer-events:none;'
    +'transition:opacity .3s ease';
  requestAnimationFrame(function(){t.style.opacity='1';});
  clearTimeout(window.__t1);clearTimeout(window.__t2);
  window.__t1=setTimeout(function(){t.style.opacity='0';},2000);
  window.__t2=setTimeout(function(){t.style.display='none';},2400);
}
function api(path,opts){
  var o=opts||{};
  return fetch(path,{credentials:'same-origin',method:o.method||'GET',
    headers:o.body?{'Content-Type':'application/json'}:undefined,
    body:o.body?JSON.stringify(o.body):undefined}).then(function(r){return r.json();});
}
function navGo(p,title){
  var pages=['overview','rfmode','qso','log'];
  document.querySelectorAll('#nav a[data-p]').forEach(function(a){
    a.classList.toggle('on',a.getAttribute('data-p')===p);});
  pages.forEach(function(x){var el=$('p-'+x);if(el)el.classList.toggle('on',x===p);});
  if(title)$('pgTitle').textContent=title;
  if(p==='rfmode')loadFreq();
  if(p==='log')loadLog();
}
document.addEventListener('click',function(e){
  var a=e.target.closest&&e.target.closest('#nav a[data-p]');
  if(a){var p=a.getAttribute('data-p');
    navGo(p,{overview:'总览',rfmode:'射频模式',qso:'通联',log:'运行日志'}[p]);}
});
function doRefresh(){
  api('/api/mc/channels').then(function(d){
    if(!d||d.ok===false)return; CACHE=d; renderAll(d);
  }).catch(function(e){toast('读取失败：'+e,false);});
  api('/api/qso').then(function(d){if(d&&d.qso)renderQso(d.qso);}).catch(function(){});
}
function renderAll(d){
  var chs=(d.channels||[]).filter(function(c){return String(c.mode||'NONE').toUpperCase()!=='NONE';});
  var eng=d.multi||{};
  var runN=chs.filter(function(c){return c.iq&&c.host;}).length;
  var b=$('bEng');if(b){b.textContent='引擎 '+(eng.active?'运行中':'停止');b.className='badge '+(eng.active?'ok':'bad');}
  var b2=$('bCh');if(b2)b2.textContent='通道 '+runN+' / '+chs.length;
  var b3=$('bSdr');if(b3)b3.textContent='SDR '+(eng.sample_rate?(eng.sample_rate/1000)+' kS/s':'—');
  var b4=$('bCallsign');if(b4)b4.textContent=eng.callsign||'';
  $('ovChans').innerHTML=chs.map(chanCardOverview).join('')||'<div class="qso-empty">无启用通道</div>';
  $('rfChans').innerHTML=chs.map(chanCardRf).join('')||'<div class="qso-empty">无启用通道</div>';
  $('ovStats').innerHTML=
    '<div class="item"><div class="k">引擎</div><div class="v" style="color:'+(eng.active?'#4ade80':'#f87171')+'">'+(eng.active?'运行中':'停止')+'</div></div>'
    +'<div class="item"><div class="k">在跑通道</div><div class="v">'+runN+' / '+chs.length+'</div></div>'
    +'<div class="item"><div class="k">采样率</div><div class="v">'+(eng.sample_rate?Math.round(eng.sample_rate/1000)+' kHz':'—')+'</div></div>';
  var sel=$('logCh');
  if(sel)sel.innerHTML=chs.map(function(c,i){return '<option value="'+c.n+'">射频通道 '+(i+1)+' ('+c.mode+')</option>';}).join('')+'<option value="">（引擎级）</option>';
}
function chanCardOverview(c,i){
  var run=!!(c.iq&&c.host);
  return '<div class="ch '+(run?'on':'')+'">'
    +'<div class="hd"><span class="dot" style="background:'+(run?'var(--ok)':'var(--warn)')+'"></span>'
    +'<span class="n">通道 '+(i+1)+'</span>'
    +'<span class="mode">'+icon(c.mode)+esc(c.mode)+'</span></div>'
    +'<div class="freq">'
    +'<div class="col"><div class="k">接收 RX</div><div class="v">'+mhz(c.rx)+'</div></div>'
    +'<div class="col"><div class="k">发射 TX</div><div class="v">'+mhz(c.tx)+'</div></div>'
    +'<div class="col"><div class="k">状态</div><div class="v" style="color:'+(run?'#4ade80':'#fbbf24')+'">'+(run?'运行中':'已停止')+'</div></div>'
    +'</div></div>';
}
function chanCardRf(c,i){
  var run=!!(c.iq&&c.host);
  var opts=MODES.map(function(m){return '<option value="'+m+'"'+(String(c.mode).toUpperCase()===m?' selected':'')+'>'+m+'</option>';}).join('');
  return '<div class="ch '+(run?'on':'')+'">'
    +'<div class="hd"><span class="dot" style="background:'+(run?'var(--ok)':'var(--warn)')+'"></span>'
    +'<span class="n">通道 '+(i+1)+'</span>'
    +'<span class="mode">'+icon(c.mode)+esc(c.mode)+'</span></div>'
    +'<div class="freq">'
    +'<div class="col"><div class="k">RX</div><div class="v">'+mhz(c.rx)+'</div></div>'
    +'<div class="col"><div class="k">TX</div><div class="v">'+mhz(c.tx)+'</div></div>'
    +'</div>'
    +'<div class="ops"><select class="grow" data-ch="'+c.n+'">'+opts+'</select>'
    +'<button class="btn sm g" onclick="doSwitch('+c.n+')">切换</button>'
    +'<button class="btn sm" onclick="doRestart('+c.n+')">重启</button></div>'
    +'</div>';
}
function renderQso(items){
  var h=items.map(function(q){
    return '<div class="qso-item"><span class="qso-call">'+esc(q.call||'-')+'</span>'
      +'<span class="qso-tag mode">'+esc(q.mode||'')+'</span>'
      +'<span class="qso-tag src">'+esc(q.src||'')+'</span>'
      +'<span class="qso-meta">→ '+esc(q.target||'-')+' · '+esc(q.duration||'-')+'</span>'
      +'<span class="qso-time">'+esc(q.time||'')+'</span></div>';
  }).join('');
  var el=$('qsoList');if(el)el.innerHTML=h||'<div class="qso-empty">暂无通联</div>';
  var el2=$('ovQso');if(el2)el2.innerHTML=(items.slice(0,8).map(function(q){
    return '<div class="qso-item"><span class="qso-call">'+esc(q.call||'-')+'</span>'
      +'<span class="qso-tag mode">'+esc(q.mode||'')+'</span>'
      +'<span class="qso-meta">→ '+esc(q.target||'-')+'</span>'
      +'<span class="qso-time">'+esc(q.time||'')+'</span></div>';
  }).join(''))||'<div class="qso-empty">暂无通联</div>';
}
function doSwitch(n){
  var sel=document.querySelector('.ch select[data-ch="'+n+'"]');
  if(!sel)return;
  var mode=sel.value;
  if(!confirm('把射频通道 '+n+' 切换为 '+mode+' ？将重启该通道。'))return;
  toast('通道 '+n+' 正在切换…');
  api('/api/mc/switch',{method:'POST',body:{ch:n,mode:mode}}).then(function(d){
    var ok=!!(d&&d.ok);
    toast(ok?('通道 '+n+' 已切换为 '+mode):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok)setTimeout(doRefresh,1800);
  }).catch(function(e){toast('失败：'+e,false);});
}
function doRestart(n){
  if(!confirm('重启射频通道 '+n+' 的基带与主机？'))return;
  toast('通道 '+n+' 正在重启…');
  api('/api/mc/restart',{method:'POST',body:{ch:n}}).then(function(d){
    var ok=!!(d&&d.ok);
    toast(ok?('通道 '+n+' 已重启'):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok)setTimeout(doRefresh,1800);
  }).catch(function(e){toast('失败：'+e,false);});
}
function restartAll(){
  if(!confirm('重启 MMDVM-Multi 引擎与全部通道？约 20~40 秒。'))return;
  toast('正在重启全部通道…');
  api('/api/mc/multi-restart',{method:'POST',body:{}}).then(function(d){
    var ok=!!(d&&d.ok);
    toast(ok?'引擎与全部通道已重启':('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok)setTimeout(doRefresh,5000);
  }).catch(function(e){toast('失败：'+e,false);});
}
function loadFreq(){
  var box=$('rfFreq');if(!box)return;
  api('/api/mc/plan').then(function(d){
    if(!d||d.ok===false||!d.list){box.innerHTML='<div class="note">读取规划失败</div>';return;}
    window.__plan=d;
    box.innerHTML='<div class="grid">'+(d.list||[]).map(function(c,i){
      var has=String(c.mode||'NONE').toUpperCase()!=='NONE';
      return '<div class="ch '+(has?'on':'')+'">'
        +'<div class="hd"><span class="n">通道 '+(i+1)+'</span>'
        +'<span class="mode">'+icon(c.mode)+esc(c.mode)+'</span></div>'
        +'<div class="ops"><span class="hint" style="flex:0 0 58px">接收 Hz</span>'
        +'<input type="number" step="25000" class="grow" id="fRx'+c.n+'" value="'+(c.rx||0)+'"></div>'
        +'<div class="ops mt"><span class="hint" style="flex:0 0 58px">发射 Hz</span>'
        +'<input type="number" step="25000" class="grow" id="fTx'+c.n+'" value="'+(c.tx||0)+'"></div>'
        +'<div class="hint mt">'+mhz(c.rx)+' / '+mhz(c.tx)+'</div>'
        +'</div>';
    }).join('')+'</div>';
  }).catch(function(e){box.innerHTML='<div class="note">网络错误：'+e+'</div>';});
}
function saveFreq(apply){
  var d=window.__plan;if(!d||!d.list){toast('请先刷新频率',false);return;}
  var bad=null;
  d.list.forEach(function(c){
    var rx=parseInt(($('fRx'+c.n)||{}).value,10)||0;
    var tx=parseInt(($('fTx'+c.n)||{}).value,10)||0;
    if(rx&&rx%25000!==0)bad='通道 '+c.n+' 接收频率非 25kHz 整数倍';
    if(tx&&tx%25000!==0)bad='通道 '+c.n+' 发射频率非 25kHz 整数倍';
    c.rx=rx;c.tx=tx;
  });
  if(bad){toast(bad,false);return;}
  var msg=$('rfFreqMsg');if(msg)msg.textContent=apply?'正在应用（约 30~60 秒）…':'正在保存…';
  api('/api/mc/plan-'+(apply?'apply':'save'),{method:'POST',body:d}).then(function(res){
    var ok=!!(res&&res.ok!==false);
    var t=String((res&&(res.message||res.out||res.error))||'');
    if(msg)msg.textContent=(ok?'✅ ':'❌ ')+t.slice(0,90);
    toast(ok?(apply?'频率已应用':'频率已保存'):('失败：'+t.slice(0,60)),ok);
    if(ok)setTimeout(doRefresh,2500);
  }).catch(function(e){if(msg)msg.textContent='网络错误：'+e;});
}
var __logTimer=null;
function loadLog(){
  var ch=($('logCh')||{}).value, svc=($('logSvc')||{}).value||'mmdvmhost';
  var box=$('logBox');if(!box)return;
  if(svc==='mmdvm-multi')ch='';
  var url='/api/mc/logs?svc='+encodeURIComponent(svc)+'&lines=200'+(ch?'&ch='+encodeURIComponent(ch):'');
  fetch(url,{credentials:'same-origin'}).then(function(r){return r.json();}).then(function(d){
    box.textContent=String((d&&(d.log||d.out||d.text))||'')||'（无日志）';
    box.scrollTop=box.scrollHeight;
  }).catch(function(e){box.textContent='读取失败：'+e;});
  if(__logTimer)clearInterval(__logTimer);
  __logTimer=setInterval(function(){
    var pn=$('p-log');if(pn&&pn.classList.contains('on')&&($('logAuto')||{}).checked)loadLog();
  },5000);
}
function openHw(){ location.href='/multi'; }
function embedHw(){
  var box=$('hwBox');if(!box)return;
  box.innerHTML='<div class="hint">嵌入式页面已停用。请<button class="sm primary" onclick="openHw()">在新窗口打开多通道控制台</button></div>';
}
function init(){
  doRefresh();
  setInterval(doRefresh, 8000);
  setInterval(function(){ api('/api/qso').then(function(d){if(d&&d.qso)renderQso(d.qso);}).catch(function(){}); }, 4000);
}
document.addEventListener('DOMContentLoaded', init);
if(document.readyState!=='loading')init();
"""

CONSOLE_STANDALONE = (
    "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
    "<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
    "<title>ARCH业余无线电融合中枢 · 控制台</title>\n"
    "<style>\n" + CONSOLE_CSS + "\n</style>\n</head>\n<body>\n"
    + CONSOLE_BODY +
    "\n<script>\n" + CONSOLE_JS + "\n</script>\n</body>\n</html>\n"
)

if __name__ == "__main__":
    open("/tmp/_console_chk.html", "w", encoding="utf-8").write(CONSOLE_STANDALONE)
    print("整页 %d 字节   CSS %d / BODY %d / JS %d" % (
        len(CONSOLE_STANDALONE), len(CONSOLE_CSS), len(CONSOLE_BODY), len(CONSOLE_JS)))
    for k in ("<style>", "</style>", "<script>", "</script>", "<body>", "</body>",
              "doRefresh", "renderAll", "doSwitch", "doRestart", "saveFreq", "loadLog",
              "renderQso", "navGo"):
        print("  %-12s %d" % (k, CONSOLE_STANDALONE.count(k)))

