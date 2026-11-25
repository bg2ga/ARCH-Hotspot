# -*- coding: utf-8 -*-
"""硬件基础配置页前端（只做监控；规划/呼号已移到射频模式配置页）
   注意：主控制台的 freqCalc / MC_DYN_CH 依赖本文件里的 chGridStep 与 PLN 变量，
        所以这两个必须保留（名字不能改）。"""

MULTI2_JS = r"""
var PLN = {channels:0, sample_rate:300000, callsign:'', list:[]};   /* 主控制台读它（规划摘要） */
var CH_GAP = 25000;
function chGridStep(i, sr){
  sr = parseInt(sr,10) || parseInt((typeof PLN==='object'&&PLN&&PLN.sample_rate)||0,10) || 300000;
  var n = Math.max(1, Math.round(sr/CH_GAP));
  var mf = Math.max(1, Math.min(Math.floor(n/2)-1, 4));
  var p = i-1, b = (p<mf) ? p : (n-1)-(p-mf);
  return (b*2<=n) ? b : b-n;
}
var MC_HZ = (typeof window!=='undefined' && window.FREQ_HZ) ? window.FREQ_HZ : function(v){
  var s=String(v==null?'':v).trim().replace(/[^0-9.]/g,''); var f=parseFloat(s);
  if(!isFinite(f)||f<=0) return 0;
  return Math.round((s.indexOf('.')>=0||f<100000)? f*1000000 : f);
};
var MC_MHZ = (typeof window!=='undefined' && window.FREQ_MHZ) ? window.FREQ_MHZ : function(hz){
  hz=parseInt(hz,10); if(!isFinite(hz)||hz<=0) return '';
  return (hz/1000000).toFixed((hz%12500===0&&hz%25000!==0)?4:3);
};
function mcI(id){ return document.getElementById(id); }
function mcEsc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function mcDot(on){ return '<span style="display:inline-block;width:7px;height:7px;border-radius:9px;margin-right:5px;background:'+(on?'var(--green2)':'#ef4444')+'"></span>'; }
function mcKV(k,v){ return '<div class="fld" style="background:var(--card2);border:1px solid var(--border);border-radius:9px;padding:9px 11px">'
  + '<div class="hint" style="margin-bottom:3px">'+mcEsc(k)+'</div>'
  + '<div style="font-size:13.5px;font-weight:700;color:#dfe8f5">'+mcEsc(v)+'</div></div>'; }
function mcMsg(t, ok){
  var e=mcI('mcMsg'); if(e) e.textContent=t||'';
  if(t && typeof toast==='function') toast(t, ok);
}
function mcPlanLite(){
  return fetch('/api/mc/plan', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(p){
    if(p && p.ok!==false && p.list){ PLN=p; if(!PLN.sample_rate) PLN.sample_rate=300000; }
    return PLN;
  }).catch(function(){ return PLN; });
}
function mcRefreshAll(){
  mcPlanLite();
  mcModeLite(function(){
    fetch('/api/mc/channels', {credentials:'same-origin'}).then(function(r){ return r.json(); })
    .then(function(d){
      if(!d || d.ok===false){ mcMsg('读取失败'+String((d&&(d.error||d.out))||''), false); return; }
      if(typeof mcSeqBuild==='function') mcSeqBuild(d.channels||[]);
      mcRenderEng(d); mcRenderChs(d); mcRenderSvcs(d);
    }).catch(function(e){ mcMsg('网络错误: '+e, false); });
  });
}
/* 当前运行模式：常规多通道射频收发 */
var MC_MODE_TXT = '--';
function mcModeLite(cb){ MC_MODE_TXT='正常模式'; if(cb) cb(); }
function mcRenderEng(d){
  var e=d.multi||{}, box=mcI('mcEngBox'); if(!box) return;
  var nch = parseInt((PLN&&PLN.channels)||0,10) || (d.channels||[]).length;
  box.innerHTML = mcKV('当前模式', MC_MODE_TXT)
    + mcKV('引擎状态', e.active?'运行中':'已停止')
    + mcKV('运行通道数', nch + ' / 配置 ' + (e.channels||'-'))
    + mcKV('采样率', e.rate? (e.rate+' Hz'):'--')
    + mcKV('SDR 类型', e.sdr||'--')
    + mcKV('RX 中心', e.rx? (MC_MHZ(e.rx)+' MHz'):'--')
    + mcKV('TX 中心', e.tx? (MC_MHZ(e.tx)+' MHz'):'--')
    + mcKV('RX 增益', (e.rxg===''||e.rxg==null)?'--':e.rxg+' dB')
    + mcKV('TX 增益', (e.txg===''||e.txg==null)?'--':e.txg+' dB');
  var b=mcI('mcChBadge'); if(b) b.textContent='IQ/Host '+(d.channels||[]).length+' 路';
}
function mcRenderChs(d){
  var cs=d.channels||[], box=mcI('mcChBox'); if(!box) return;
  var h='';
  for(var i=0;i<cs.length;i++){
    var c=cs[i];
    h+='<div class="fld" style="border:1px solid var(--border);border-radius:10px;padding:11px;background:var(--card2);margin-bottom:9px">'
      + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:7px">'
      +   '<b style="font-size:12.5px;color:#7dd3fc">射频通道 '+(typeof mcSeq==='function'?mcSeq(c.n):c.n)+'</b>'
      +   '<span class="hint">'+mcEsc(c.mode)+'</span>'
      +   '<span style="margin-left:auto" class="hint">'+mcDot(c.iq)+'IQ '+mcDot(c.host)+'Host</span>'
      + '</div>'
      + '<div class="form-grid" style="margin:0">'
      +   mcKV('RX 频率', c.rx? (MC_MHZ(c.rx)+' MHz') : '--')
      +   mcKV('TX 频率', c.tx? (MC_MHZ(c.tx)+' MHz') : '--')
      +   mcKV('Multi 端口', c.mport)+mcKV('Host 端口', c.hport)
      + '</div>'
      + '<div class="hint" style="margin-top:8px">最近通联：'+mcEsc(c.lastq||'（无）')+'</div>'
      + '<div class="mc-foot" style="margin:8px 0 0">'
      +   '<button class="btn btn-ghost btn-sm" onclick="mcRestartCh('+c.n+')">&#8634; 重启本通道</button>'
      + '</div></div>';
  }
  box.innerHTML = h || '<div class="sp-empty">未检测到通道配置</div>';
}
function mcRenderSvcs(d){
  var sv=d.services||[], box=mcI('mcSvcBox'); if(!box) return;
  var h='';
  for(var i=0;i<sv.length;i++){ h+=mcKV(sv[i].name, (sv[i].active?'● 运行':'○ 停止')); }
  box.innerHTML = h || '<div class="hint">无服务信息</div>';
  var b=mcI('mcSvcBadge');
  if(b){ var on=sv.filter(function(x){return x.active;}).length; b.textContent=on+' / '+sv.length+' 在运行'; }
  mcFillLogCh(d.channels||[]);
}
function mcFillLogCh(cs){
  var sel=mcI('mcLogCh'); if(!sel) return;
  var cur=sel.value, h='<option value="0">（引擎级）</option>';
  if(typeof mcSeqBuild==='function') mcSeqBuild(cs);
  for(var i=0;i<cs.length;i++){
    if(String(cs[i].mode||'NONE').toUpperCase()==='NONE') continue;
    h+='<option value="'+cs[i].n+'">'+((typeof mcSeq==='function')?mcSeq(cs[i].n):cs[i].n)+'. '+mcEsc(cs[i].mode)+'</option>';
  }
  sel.innerHTML=h;
  if(cur) sel.value=cur;
}
function mcPostJson(url, body, cb){
  fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
             body:JSON.stringify(body||{})})
    .then(function(r){ return r.json(); }).then(function(d){ cb(d); })
    .catch(function(e){ mcMsg('网络错误: '+e, false); });
}
function mcRestartCh(n){
  mcMsg('通道 '+n+' 正在重启…');
  mcPostJson('/api/mc/restart',{ch:n},function(d){
    var ok=!!(d&&d.ok);
    mcMsg(ok?('通道 '+n+' 已重启'):('失败：'+String((d&&(d.error||d.out))||'')), ok);
    if(ok) setTimeout(mcRefreshAll, 2500);
  });
}
function mcRestartEngine(){
  if(!confirm('重启 MMDVM-Multi 引擎与全部通道？约 20~40 秒。')) return;
  mcMsg('正在重启引擎…');
  mcPostJson('/api/mc/multi-restart',{},function(d){
    var ok=!!(d&&d.ok);
    mcMsg(ok?'引擎与全部通道已重启':('失败：'+String((d&&(d.error||d.out))||'')), ok);
    if(ok) setTimeout(mcRefreshAll, 6000);
  });
}
function mcLoadLog(){
  var ch=(mcI('mcLogCh')||{}).value||'0', svc=(mcI('mcLogSvc')||{}).value||'mmdvmhost',
      ln=(mcI('mcLogLines')||{}).value||'200';
  var box=mcI('mcLogBox'); if(box) box.textContent='加载中…';
  fetch('/api/mc/logs?ch='+encodeURIComponent(ch)+'&svc='+encodeURIComponent(svc)+'&lines='+encodeURIComponent(ln),
        {credentials:'same-origin'})
  .then(function(r){ return r.json(); })
  .then(function(d){
    if(box) box.textContent = (d&&(d.text||d.out)) || ('读取失败：'+String((d&&d.error)||''));
  }).catch(function(e){ if(box) box.textContent='网络错误: '+e; });
}
/* 本文件提供「射频模式配置」页诊断卡所需的监控函数（原独立监控页已并入该页） */
"""

