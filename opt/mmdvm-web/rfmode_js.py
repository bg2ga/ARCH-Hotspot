# -*- coding: utf-8 -*-
"""射频模式配置页的前端（独立 <script> 块内容，与主脚本隔离）"""

RFMODE_SECTION_JS = r"""
var RFM_MODES = ['DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG','NONE'];
var RFM_COL = {'DMR':'#0ea5e9','D-STAR':'#8b5cf6','YSF':'#f97316','NXDN':'#2563eb',
               'P25':'#e11d48','FM':'#10b981','POCSAG':'#f59e0b','NONE':'#64748b'};
function rfmQ(id){ return document.getElementById(id); }
function rfmMHz(hz){ hz = parseInt(hz,10)||0; return hz>0 ? (hz/1e6).toFixed(4) : '-'; }
function rfmSvg(m){
  var M=String(m||'').toUpperCase(), col=RFM_COL[M]||'#64748b', g='';
  if(M==='DMR') g='<rect x="9" y="15" width="22" height="4" rx="2" fill="#fff"/><rect x="9" y="23" width="14" height="4" rx="2" fill="#fff" opacity=".75"/>';
  else if(M==='D-STAR') g='<path d="M20 10l2.6 6.1 6.4.6-4.9 4.3 1.5 6.4L20 24l-5.6 3.4 1.5-6.4-4.9-4.3 6.4-.6z" fill="#fff"/>';
  else if(M==='YSF') g='<path d="M20 10l8 10-8 10-8-10z" fill="#fff"/>';
  else if(M==='NXDN') g='<path d="M13 27V13h4.2l5.6 8V13H27v14h-4.2l-5.6-8v8z" fill="#fff"/>';
  else if(M==='P25') g='<path d="M20 10l9 3.4v7.1c0 5-3.7 8.2-9 9.5-5.3-1.3-9-4.5-9-9.5v-7.1z" fill="#fff"/>';
  else if(M==='FM') g='<path d="M9 20c2.6-6 5.2-6 7.8 0s5.2 6 7.8 0 5.2-6 5.4 0" stroke="#fff" stroke-width="3" fill="none" stroke-linecap="round"/>';
  else g='<circle cx="20" cy="20" r="6" fill="#fff" opacity=".7"/>';
  return '<svg viewBox="0 0 40 40" width="30" height="30" xmlns="http://www.w3.org/2000/svg" style="flex:0 0 30px"><rect x="2" y="2" width="36" height="36" rx="9" fill="'+col+'"/>'+g+'</svg>';
}
function rfmPost(url, body, cb){
  fetch(url, {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})})
    .then(function(r){ return r.json(); })
    .then(function(d){ cb(d); })
    .catch(function(e){ if(typeof toast==='function') toast('失败：'+e,false); });
}
function rfmLoad(){
  fetch('/api/mc/channels', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(d){
    if(!d || d.ok===false) return;
    var chs=(d.channels||[]).filter(function(c){ return String(c.mode||'NONE').toUpperCase()!=='NONE'; });
    var b=rfmQ('rfmBanner');
    if(b) b.textContent = chs.map(function(c){
      return 'CH'+c.n+' '+c.mode+' '+rfmMHz(c.rx)+'/'+rfmMHz(c.tx);
    }).join('  ｜  ');
    var box=rfmQ('rfmChans');
    if(!box) return;
    var h='<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px">';
    chs.forEach(function(c,i){
      var run=!!(c.iq && c.host);
      var opts=RFM_MODES.map(function(m){
        return '<option value="'+m+'"'+(String(c.mode).toUpperCase()===m?' selected':'')+'>'+m+'</option>';
      }).join('');
      h += '<div class="fld" data-n="'+c.n+'" style="border:1px solid var(--border);border-radius:10px;padding:11px;background:var(--card2)">'
        + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
        + '<span style="width:8px;height:8px;border-radius:9px;flex:0 0 8px;background:'+(run?'var(--green2)':'var(--amber2)')+'"></span>'
        + '<b style="font-size:12.5px;color:#7dd3fc">通道 '+(i+1)+'</b>'
        + '<span style="margin-left:auto;display:flex;align-items:center;gap:7px;color:#fff;font-weight:800;font-size:13px">'
        + rfmSvg(c.mode) + String(c.mode) + '</span></div>'
        + '<div style="display:flex;gap:7px;align-items:center;margin-bottom:7px">'
        + '<span class="hint" style="flex:0 0 26px">RX</span>'
        + '<input class="rfm-rx" type="number" step="25000" value="'+(c.rx||0)+'" style="flex:1;min-width:0">'
        + '<span class="hint" style="flex:0 0 58px;text-align:right">'+rfmMHz(c.rx)+'</span></div>'
        + '<div style="display:flex;gap:7px;align-items:center;margin-bottom:9px">'
        + '<span class="hint" style="flex:0 0 26px">TX</span>'
        + '<input class="rfm-tx" type="number" step="25000" value="'+(c.tx||0)+'" style="flex:1;min-width:0">'
        + '<span class="hint" style="flex:0 0 58px;text-align:right">'+rfmMHz(c.tx)+'</span></div>'
        + '<div style="display:flex;gap:6px;align-items:center">'
        + '<select class="rfm-mode" style="flex:1;min-width:0">'+opts+'</select>'
        + '<button class="btn btn-green btn-sm" onclick="rfmSwitch('+c.n+')">切换</button>'
        + '<button class="btn btn-ghost btn-sm" onclick="rfmRestart('+c.n+')">重启</button>'
        + '</div></div>';
    });
    h += '</div>';
    box.innerHTML = h;
  }).catch(function(){});
}
function rfmSwitch(n){
  var card=document.querySelector('#rfmChans .fld[data-n="'+n+'"]');
  if(!card) return;
  var mode=card.querySelector('.rfm-mode').value;
  if(!confirm('把射频通道 '+n+' 切换为 '+mode+' ？将重启该通道。')) return;
  if(typeof toast==='function') toast('通道 '+n+' 正在切换…');
  rfmPost('/api/mc/switch',{ch:n,mode:mode},function(d){
    var ok=!!(d&&d.ok);
    if(typeof toast==='function') toast(ok?('通道 '+n+' 已切换为 '+mode):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok) setTimeout(rfmLoad,1800);
  });
}
function rfmRestart(n){
  if(!confirm('重启射频通道 '+n+' 的基带与主机？')) return;
  rfmPost('/api/mc/restart',{ch:n},function(d){
    var ok=!!(d&&d.ok);
    if(typeof toast==='function') toast(ok?('通道 '+n+' 已重启'):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok) setTimeout(rfmLoad,1800);
  });
}
function rfmRestartAll(){
  if(!confirm('重启 MMDVM-Multi 引擎与全部通道？约 20~40 秒。')) return;
  rfmPost('/api/mc/multi-restart',{},function(d){
    var ok=!!(d&&d.ok);
    if(typeof toast==='function') toast(ok?'引擎与全部通道已重启':('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok) setTimeout(rfmLoad,5000);
  });
}
function rfmSaveFreq(){
  var cards=document.querySelectorAll('#rfmChans .fld[data-n]');
  var list=[], bad=null;
  Array.prototype.forEach.call(cards,function(cd){
    var n=parseInt(cd.getAttribute('data-n'),10);
    var rx=parseInt(cd.querySelector('.rfm-rx').value,10)||0;
    var tx=parseInt(cd.querySelector('.rfm-tx').value,10)||0;
    if(rx&&rx%25000!==0) bad='通道 '+n+' 接收频率非 25kHz 整数倍';
    if(tx&&tx%25000!==0) bad='通道 '+n+' 发射频率非 25kHz 整数倍';
    list.push({n:n,rx:rx,tx:tx});
  });
  if(bad){ if(typeof toast==='function') toast(bad,false); return; }
  var m=rfmQ('rfmMsg'); if(m) m.textContent='正在应用…';
  rfmPost('/api/mc/plan-apply',{channels:cards.length,list:list},function(res){
    var ok=!!(res&&res.ok!==false);
    var t=String((res&&(res.message||res.out||res.error))||'');
    if(m) m.textContent=(ok?'✅ ':'❌ ')+t.slice(0,100);
    if(typeof toast==='function') toast(ok?'频率已应用':'失败：'+t.slice(0,60),ok);
    if(ok) setTimeout(rfmLoad,2500);
  });
}
function rfmPowerChanged(){
  var sel=rfmQ('rfmPower'), tg=rfmQ('rfmTxGain');
  if(!sel||!tg) return;
  tg.value = (sel.value==='0') ? 10 : 30;
}
function rfmSavePower(){
  var p=(rfmQ('rfmPower')||{}).value, rx=(rfmQ('rfmRxGain')||{}).value, tx=(rfmQ('rfmTxGain')||{}).value;
  var m=rfmQ('rfmPwrMsg'); if(m) m.textContent='正在保存…';
  rfmPost('/api/save/rf',{power:p, rxgain:rx, txgain:tx},function(d){
    var ok=!!(d&&d.ok!==false);
    var t=String((d&&(d.message||d.out||d.error))||'');
    if(m) m.textContent=(ok?'✅ 已保存':'❌ ')+t.slice(0,90);
    if(typeof toast==='function') toast(ok?'功率设置已保存':'失败：'+t.slice(0,60),ok);
  });
}
document.addEventListener('change', function(e){
  if(e.target && e.target.id==='rfmPower') rfmPowerChanged();
});
(function(){ var s=rfmQ('rfmPower'); if(s) rfmPowerChanged(); })();
"""
