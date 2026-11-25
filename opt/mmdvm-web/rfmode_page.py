# -*- coding: utf-8 -*-
"""射频模式配置（单页精简版）：
   · 6 个宫格，2 行 × 3 列
   · 每个宫格：图标+模式名 + RX/TX 可编辑频率 + 切模式/重启
   · 顶部：引擎状态 + 重新读取 / 全部重启 / 保存并应用频率
   · 已删除：标题、通道/SDR 徽标、硬件基础配置、运行日志 页签"""

RFMODE_HTML = r"""
<style>
:root{--mono:Consolas,"Cascadia Mono","DejaVu Sans Mono",monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#eaf2ff;font:13.5px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;padding:14px}
body.embed{padding:6px 10px 10px}
.bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.bar .spacer{flex:1}
.badge{font-size:11px;padding:4px 12px;border-radius:99px;background:#17233a;border:1px solid #26354f;color:#8fa6c4;font-weight:600}
.badge.ok{background:#0e2f22;border-color:#1d6b4a;color:#4ade80}
.badge.bad{background:#331616;border-color:#7f2222;color:#f87171}
.btn{cursor:pointer;border:1px solid #2a3a56;background:#16264a;color:#cfe0f5;padding:7px 14px;
  border-radius:8px;font-size:12.5px;font-weight:650;font-family:inherit;transition:.15s;white-space:nowrap}
.btn:hover{border-color:#3b5686;color:#fff}
.btn:active{transform:translateY(1px)}
.btn.g{background:#0e2f22;border-color:#1d6b4a;color:#a7f3d0}
.btn.sm{padding:5px 10px;font-size:11.5px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
@media(max-width:980px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){.grid{grid-template-columns:1fr}}
.ch{background:linear-gradient(180deg,#111d33,#0c1526);border:1px solid #1e2b44;border-radius:12px;
  padding:14px;transition:.16s}
.ch:hover{border-color:#2a3d5c}
.ch.on{border-color:rgba(16,185,129,.42)}
.ch .hd{display:flex;align-items:center;gap:9px;margin-bottom:11px}
.ch .dot{width:8px;height:8px;border-radius:99px;flex:0 0 8px}
.ch .n{font-size:13px;font-weight:800;color:#7dd3fc;font-family:var(--mono)}
.ch .mode{margin-left:auto;display:flex;align-items:center;gap:8px;font-weight:800;font-size:14px;
  color:#fff;font-family:var(--mono)}
.ch .mode svg{width:34px;height:34px;flex:0 0 34px}
.frow{display:flex;align-items:center;gap:8px;margin-top:7px}
.frow label{flex:0 0 30px;font-size:10px;color:#6f86a6;font-weight:700;letter-spacing:.5px}
.frow input{flex:1;min-width:0;background:#0a1322;border:1px solid #2a3a56;color:#dfe8f5;
  padding:6px 9px;border-radius:8px;font-size:12.5px;font-family:var(--mono)}
.frow input:focus{outline:none;border-color:#22d3ee}
.frow .mhz{flex:0 0 74px;font-size:11px;color:#8fa6c4;font-family:var(--mono);text-align:right}
.ops{display:flex;gap:7px;align-items:center;margin-top:11px}
.ops select{flex:1;min-width:0;background:#0a1322;border:1px solid #2a3a56;color:#dfe8f5;
  padding:6px 9px;border-radius:8px;font-size:12.5px;font-family:inherit}
.hint{font-size:11px;color:#6f86a6}
</style>

<div>
  <div class="bar">
    <span class="badge" id="bEng">引擎 —</span>
    <span class="spacer"></span>
    <button class="btn" onclick="load()">⟳ 重新读取</button>
    <button class="btn" onclick="restartAll()">⟲ 全部重启</button>
    <button class="btn g" onclick="saveFreq()">⚡ 保存并应用频率</button>
  </div>

  <div class="grid" id="grid"><div class="hint">正在读取通道…</div></div>

  <div class="bar" style="margin-top:14px;border-top:1px solid #1e2b44;padding-top:12px">
    <span class="badge">🔧 全局射频载波与硬件基础配置 (RF &amp; Station)</span>
    <span class="spacer"></span>
    <button class="btn" onclick="openHw()">打开</button>
    <button class="btn" onclick="embedHw()">本页内嵌</button>
  </div>
  <div id="hwBox" style="margin-top:10px"></div>

  <div class="hint" id="msg" style="margin-top:10px"></div>
</div>
"""

RFMODE_JS = r"""
var $=function(id){return document.getElementById(id);};
var MODES=['DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG','NONE'];
var DATA=null;
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
function mhz(hz){hz=parseInt(hz,10)||0;return hz>0?(hz/1e6).toFixed(4):'—';}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function toast(msg,ok){
  var t=$('toast');
  if(!t){t=document.createElement('div');t.id='toast';document.body.appendChild(t);}
  t.textContent=String(msg==null?'':msg);
  t.style.cssText='position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:99999;'
    +'background:rgba(13,21,32,.97);border:1px solid '+(ok===false?'rgba(239,68,68,.6)':'#2b3f57')+';'
    +'color:#eaf2ff;padding:18px 32px;border-radius:13px;text-align:center;max-width:min(560px,86vw);'
    +'font-size:15px;font-weight:600;line-height:1.6;display:block;opacity:0;transition:opacity .3s ease';
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
function load(){
  api('/api/mc/channels').then(function(d){
    if(!d||d.ok===false)return;
    DATA=d;
    var eng=d.multi||{};
    var b=$('bEng');if(b){b.textContent='引擎 '+(eng.active?'运行中':'停止');b.className='badge '+(eng.active?'ok':'bad');}
    render(d);
  }).catch(function(e){toast('读取失败：'+e,false);});
}
function render(d){
  var chs=(d.channels||[]).filter(function(c){return String(c.mode||'NONE').toUpperCase()!=='NONE';});
  $('grid').innerHTML = chs.map(function(c,i){
    var run=!!(c.iq&&c.host);
    var opts=MODES.map(function(m){return '<option value="'+m+'"'+(String(c.mode).toUpperCase()===m?' selected':'')+'>'+m+'</option>';}).join('');
    return '<div class="ch '+(run?'on':'')+'" data-n="'+c.n+'">'
      +'<div class="hd"><span class="dot" style="background:'+(run?'#10b981':'#fbbf24')+'"></span>'
      +'<span class="n">通道 '+(i+1)+'</span>'
      +'<span class="mode">'+icon(c.mode)+esc(c.mode)+'</span></div>'
      +'<div class="frow"><label>RX</label>'
      +'<input type="number" step="25000" class="frx" value="'+(c.rx||0)+'">'
      +'<span class="mhz">'+mhz(c.rx)+' MHz</span></div>'
      +'<div class="frow"><label>TX</label>'
      +'<input type="number" step="25000" class="ftx" value="'+(c.tx||0)+'">'
      +'<span class="mhz">'+mhz(c.tx)+' MHz</span></div>'
      +'<div class="ops"><select class="fmode">'+opts+'</select>'
      +'<button class="btn sm g" onclick="doSwitch('+c.n+')">切换</button>'
      +'<button class="btn sm" onclick="doRestart('+c.n+')">重启</button></div>'
      +'</div>';
  }).join('') || '<div class="hint">无启用通道</div>';
}
function doSwitch(n){
  var card=document.querySelector('.ch[data-n="'+n+'"]');
  if(!card)return;
  var mode=card.querySelector('.fmode').value;
  if(!confirm('把射频通道 '+n+' 切换为 '+mode+' ？将重启该通道。'))return;
  toast('通道 '+n+' 正在切换…');
  api('/api/mc/switch',{method:'POST',body:{ch:n,mode:mode}}).then(function(d){
    var ok=!!(d&&d.ok);
    toast(ok?('通道 '+n+' 已切换为 '+mode):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok)setTimeout(load,1800);
  }).catch(function(e){toast('失败：'+e,false);});
}
function doRestart(n){
  if(!confirm('重启射频通道 '+n+' 的基带与主机？'))return;
  toast('通道 '+n+' 正在重启…');
  api('/api/mc/restart',{method:'POST',body:{ch:n}}).then(function(d){
    var ok=!!(d&&d.ok);
    toast(ok?('通道 '+n+' 已重启'):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok)setTimeout(load,1800);
  }).catch(function(e){toast('失败：'+e,false);});
}
function restartAll(){
  if(!confirm('重启 MMDVM-Multi 引擎与全部通道？约 20~40 秒。'))return;
  toast('正在重启全部通道…');
  api('/api/mc/multi-restart',{method:'POST',body:{}}).then(function(d){
    var ok=!!(d&&d.ok);
    toast(ok?'引擎与全部通道已重启':('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok)setTimeout(load,5000);
  }).catch(function(e){toast('失败：'+e,false);});
}
function saveFreq(){
  var msg=$('msg');
  var cards=document.querySelectorAll('#grid .ch');
  var list=[]; var bad=null;
  cards.forEach(function(card){
    var n=parseInt(card.getAttribute('data-n'),10);
    var rx=parseInt(card.querySelector('.frx').value,10)||0;
    var tx=parseInt(card.querySelector('.ftx').value,10)||0;
    if(rx&&rx%25000!==0)bad='通道 '+n+' 接收频率非 25kHz 整数倍';
    if(tx&&tx%25000!==0)bad='通道 '+n+' 发射频率非 25kHz 整数倍';
    list.push({n:n,rx:rx,tx:tx});
  });
  if(bad){toast(bad,false);return;}
  if(msg)msg.textContent='正在应用（约 30~60 秒）…';
  api('/api/mc/plan-apply',{method:'POST',body:{channels:cards.length,list:list}}).then(function(res){
    var ok=!!(res&&res.ok!==false);
    var t=String((res&&(res.message||res.out||res.error))||'');
    if(msg)msg.textContent=(ok?'✅ ':'❌ ')+t.slice(0,120);
    toast(ok?'频率已应用':'失败：'+t.slice(0,60),ok);
    if(ok)setTimeout(load,2500);
  }).catch(function(e){if(msg)msg.textContent='网络错误：'+e;});
}
document.addEventListener('DOMContentLoaded', load);
if(document.readyState!=='loading')load();
function openHw(){ location.href='/multi'; }
function embedHw(){
  var box=$('hwBox');if(!box)return;
  box.innerHTML='<div class="hint">嵌入式页面已停用。请<button class="sm primary" onclick="openHw()">在新窗口打开多通道控制台</button></div>';
}
"""

