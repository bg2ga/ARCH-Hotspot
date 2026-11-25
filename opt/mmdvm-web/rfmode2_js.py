# -*- coding: utf-8 -*-
"""射频模式配置页前端（独立 <script> 块，与主脚本隔离）
   频率输入习惯：填 MHz（439.500 / 439.5125）→ 自动换算 Hz。
   本文件分块拼接，见同目录 make_rfmode2_js.py"""

RFMODE2_JS = r"""
/* ==================== 频率输入习惯（与主控制台同一套） ====================
   输入 439.500 -> 439500000 Hz ；输入 439.5125 -> 439512500 Hz
   显示 439.500 / 439.5125。主控制台已有 window.FREQ_HZ / FREQ_MHZ，这里做兜底。 */
var RFM_HZ = (typeof window !== 'undefined' && window.FREQ_HZ) ? window.FREQ_HZ : function(v){
  if(v===null||v===undefined) return 0;
  var s=String(v).trim().replace(/[^0-9.]/g,'');
  if(s==='') return 0;
  var f=parseFloat(s); if(!isFinite(f)||f<=0) return 0;
  var mhz=(s.indexOf('.')>=0||f<100000);
  return Math.round(mhz? f*1000000 : f);
};
var RFM_MHZ = (typeof window !== 'undefined' && window.FREQ_MHZ) ? window.FREQ_MHZ : function(hz){
  hz=parseInt(hz,10); if(!isFinite(hz)||hz<=0) return '';
  var d=(hz%12500===0 && hz%25000!==0)?4:3;
  return (hz/1000000).toFixed(d);
};

var RFM_MODES = ['DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG','NONE'];
var RFM_COL = {'DMR':'#0ea5e9','D-STAR':'#8b5cf6','YSF':'#f97316','NXDN':'#2563eb',
               'P25':'#e11d48','FM':'#10b981','POCSAG':'#f59e0b','NONE':'#64748b'};
var RFM_MAX = 6;          /* ★ 只用 6 路通道（第 7 路用不上，已去掉） */
var RFM_PLAN = null;      /* 最近一次 /api/mc/plan 的结果 */
var RFM_CHS = [];         /* 最近一次 /api/mc/channels 的通道表 */
var RFM_ID = '';          /* 台站 DMR Id（保存 RF 时要回传，避免被清空） */

/* ---- 模式互斥规则：DMR 可重复选，其它模式只能选一次 ---- */
function rfmApplyModeFilter2(){
  var allSels = document.querySelectorAll('.rfm-mode2, .rfm-mode');
  /* 只对“已启用”的射频通道做互斥；未启用通道只是历史规划，不算占用。 */
  var sels = Array.prototype.filter.call(allSels, function(sel){ return !sel.disabled; });
  if(!sels.length) return;
  /* 先收集所有“已启用”通道当前选中的非 DMR 模式 */
  var used = {};
  sels.forEach(function(sel){
    var v = sel.value;
    if(v && v !== 'DMR' && v !== 'NONE') used[v] = true;
  });
  /* 再对每个 select 重新过滤 */
  sels.forEach(function(sel){
    var cur = sel.value;
    for(var k = 0; k < sel.options.length; k++){
      var opt = sel.options[k], m = opt.value;
      if(m === 'DMR' || m === 'NONE'){
        opt.disabled = false; opt.style.color = '';
      } else if(used[m] && m !== cur){
        opt.disabled = true; opt.style.color = '#555';
      } else {
        opt.disabled = false; opt.style.color = '';
      }
    }
  });
}
var RFM_RX = '', RFM_TX = '';   /* MMDVM-Host 里的频率原值（回传，不改动） */

function rfmQ(id){ return document.getElementById(id); }
function rfmSvg(m){
  var M=String(m||'').toUpperCase(), col=RFM_COL[M]||'#64748b', g='';
  if(M==='DMR') g='<rect x="9" y="15" width="22" height="4" rx="2" fill="#fff"/><rect x="9" y="23" width="14" height="4" rx="2" fill="#fff" opacity=".75"/>';
  else if(M==='D-STAR') g='<path d="M20 10l2.6 6.1 6.4.6-4.9 4.3 1.5 6.4L20 24l-5.6 3.4 1.5-6.4-4.9-4.3 6.4-.6z" fill="#fff"/>';
  else if(M==='YSF') g='<path d="M20 10l8 10-8 10-8-10z" fill="#fff"/>';
  else if(M==='NXDN') g='<path d="M13 27V13h4.2l5.6 8V13H27v14h-4.2l-5.6-8v8z" fill="#fff"/>';
  else if(M==='P25') g='<path d="M20 10l9 3.4v7.1c0 5-3.7 8.2-9 9.5-5.3-1.3-9-4.5-9-9.5v-7.1z" fill="#fff"/>';
  else if(M==='FM') g='<path d="M9 20c2.6-6 5.2-6 7.8 0s5.2 6 7.8 0 5.2-6 5.4 0" stroke="#fff" stroke-width="3" fill="none" stroke-linecap="round"/>';
  else g='<circle cx="20" cy="20" r="6" fill="#fff" opacity=".7"/>';
  return '<svg viewBox="0 0 40 40" width="28" height="28" xmlns="http://www.w3.org/2000/svg" style="flex:0 0 28px"><rect x="2" y="2" width="36" height="36" rx="9" fill="'+col+'"/>'+g+'</svg>';
}
function rfmPost(url, body, cb){
  fetch(url, {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})})
    .then(function(r){ return r.json(); })
    .then(function(d){ cb(d); })
    .catch(function(e){ if(typeof toast==='function') toast('失败：'+e,false); });
}
function rfmMsg(id, txt, ok){
  var e=rfmQ(id); if(e) e.textContent=txt||'';
  if(txt && typeof toast==='function') toast(txt, ok);
}
/* ==================== 硬件网格（与后端 channel-setup.py 一致） ====================
   ch1=中心, ch2=+25k, ch3=-25k, ch4=-50k, ch5=+75k …（SampleRate/25000 = PFB bin 数） */
function rfmGridStep(i, sr){
  sr = parseInt(sr,10) || parseInt((RFM_PLAN&&RFM_PLAN.sample_rate)||0,10) || 300000;
  var n = Math.max(1, Math.round(sr/25000));
  var mf = Math.max(1, Math.min(Math.floor(n/2), 4));
  var p = i-1, b = (p<mf) ? p : (n-1)-(p-mf);
  return (b*2<=n) ? b : b-n;
}
function rfmSpacingHz(){
  var v = parseFloat((rfmQ('rfmGap')||{}).value);
  return isFinite(v)&&v>0 ? Math.round(v*1000) : 25000;
}
function rfmCenterRx(){ return RFM_HZ((rfmQ('rfmCrx')||{}).value); }
function rfmCenterTx(){ return RFM_HZ((rfmQ('rfmCtx')||{}).value); }
function rfmChanRx(i){ return rfmCenterRx() + rfmGridStep(i)*rfmSpacingHz(); }
function rfmChanTx(i){ return rfmCenterTx() + rfmGridStep(i)*rfmSpacingHz(); }
function rfmNChSel(){
  var v = parseInt((rfmQ('rfmNCh')||{}).value,10);
  return (isFinite(v)&&v>=1) ? Math.min(RFM_MAX, v) : 6;
}

/* ==================== 台站参数（呼号 / 功率 / 增益） ==================== */
function rfmFlat(d){
  var flat={};
  Object.keys(d||{}).forEach(function(k){
    var v=d[k];
    if(v && typeof v==='object'){ Object.keys(v).forEach(function(k2){ flat[k2]=v[k2]; flat[String(k2).toLowerCase()]=v[k2]; }); }
    else { flat[k]=v; }
  });
  return flat;
}
function rfmPick(flat){
  for(var i=1;i<arguments.length;i++){
    var k=arguments[i], lk=String(k).toLowerCase();
    if(flat[k]!==undefined && flat[k]!==null && String(flat[k])!=='') return flat[k];
    if(flat[lk]!==undefined && flat[lk]!==null && String(flat[lk])!=='') return flat[lk];
  }
  return '';
}
function rfmLoadStation(){
  fetch('/api/config/RF', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(d){
    var f=rfmFlat(d);
    var c=rfmPick(f,'Callsign','callsign');
    if(c && c!=='NOCALL' && rfmQ('rfmCall')) rfmQ('rfmCall').value=String(c).toUpperCase();
    var p=rfmPick(f,'Power','power');
    if(p!=='' && rfmQ('rfmPower')) rfmQ('rfmPower').value=(String(p).trim()==='0'?'0':'7');
    var rx=rfmPick(f,'RxGain','rxgain');
    if(rx!=='' && rfmQ('rfmRxGain')) rfmQ('rfmRxGain').value=String(rx);
    var tx=rfmPick(f,'TxGain','txgain');
    if(tx!=='' && rfmQ('rfmTxGain')) rfmQ('rfmTxGain').value=String(tx);
    /* 缓存台站 Id 与 MMDVM-Host 频率原值：保存时原样回传，不动无关配置 */
    RFM_ID = String(rfmPick(f,'Id','id')||'');
    RFM_RX = String(rfmPick(f,'RXFrequency','rxfreq')||'');
    RFM_TX = String(rfmPick(f,'TXFrequency','txfreq')||'');
  }).catch(function(){});
}
function rfmPowerChanged(){
  var sel=rfmQ('rfmPower'), tg=rfmQ('rfmTxGain');
  if(!sel||!tg) return;
  tg.value = (String(sel.value).trim()==='0') ? 10 : 30;
}
function rfmSaveStation(){
  var call=String((rfmQ('rfmCall')||{}).value||'').trim().toUpperCase();
  var p=(rfmQ('rfmPower')||{}).value, rx=(rfmQ('rfmRxGain')||{}).value, tx=(rfmQ('rfmTxGain')||{}).value;
  if(!call){ rfmMsg('rfmStMsg','请先填写本机呼号', false); return; }
  if(call==='NOCALL'){ rfmMsg('rfmStMsg','呼号不能是 NOCALL', false); return; }
  rfmMsg('rfmStMsg','正在保存…');
  /* ★ 接口是 /api/save/RF（大写！），字段名与页面表单一致：
     callsign / id / rxfreq / txfreq / duplex / power / rxgain / txgain
     —— id 与频率用读回来的原值回传，不改动无关配置 */
  rfmPost('/api/save/RF',{callsign:call,
                          id: RFM_ID || String((RFM_PLAN&&RFM_PLAN.dmr_id)||''),
                          rxfreq: RFM_RX, txfreq: RFM_TX, duplex:'1',
                          power:String(p), rxgain:String(rx), txgain:String(tx)}, function(d){
    var ok=!!(d&&d.ok!==false);
    var t=String((d&&(d.message||d.out||d.error))||'');
    if(!ok){ rfmMsg('rfmStMsg','❌ '+t.slice(0,80), false); return; }
    var body=rfmPlanPayload(call);      /* 呼号同时同步到各网关（仅保存，不重启） */
    if(!body){ rfmMsg('rfmStMsg','✅ 呼号/功率/增益已保存'); return; }
    rfmPost('/api/mc/plan-save', body, function(r2){
      var ok2=!!(r2&&r2.ok!==false);
      rfmMsg('rfmStMsg', ok2? '✅ 已保存，并同步到各网关'
                            : '⚠ 已保存，但网关呼号同步失败：'+String((r2&&(r2.out||r2.error))||'').slice(0,60), ok2);
      if(RFM_PLAN) RFM_PLAN.callsign=call;
    });
  });
}
/* ==================== 信道规划：读取 / 渲染 ==================== */
function rfmFillNCh(n){
  var sel=rfmQ('rfmNCh'); if(!sel) return;
  var h='';
  for(var i=1;i<=RFM_MAX;i++){
    h+='<option value="'+i+'"'+(i===n?' selected':'')+'>'+i+' 路通道'+(i===1?'（单通道）':'（同时收发）')+'</option>';
  }
  sel.innerHTML=h;
}
function rfmPlanLoad(){
  fetch('/api/mc/plan', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(d){
    if(!d || d.ok===false){
      var bx=rfmQ('rfmRows');
      if(bx) bx.innerHTML='<div class="sp-empty">读取规划失败：'+String((d&&(d.out||d.error))||'')+'</div>';
      return;
    }
    RFM_PLAN=d;
    var n=parseInt(d.channels||1,10)||1;
    rfmFillNCh(n);
    if(rfmQ('rfmCrx')) rfmQ('rfmCrx').value=RFM_MHZ(d.center_rx)||RFM_MHZ(434450000);
    if(rfmQ('rfmCtx')) rfmQ('rfmCtx').value=RFM_MHZ(d.center_tx)||RFM_MHZ(439450000);
    if(rfmQ('rfmGap')){
      var sp=parseInt(d.spacing||25000,10);
      rfmQ('rfmGap').value=(sp<20000)?'12.5':'25';
    }
    if(rfmQ('rfmCall') && d.callsign && !rfmQ('rfmCall').value) rfmQ('rfmCall').value=String(d.callsign).toUpperCase();
    rfmRenderRows();
    var m=rfmQ('rfmPlanMsg'); if(m) m.textContent='';
  }).catch(function(e){ if(typeof toast==='function') toast('读取规划失败：'+e,false); });
}
function rfmRenderRows(){
  var box=rfmQ('rfmRows'); if(!box) return;
  var n=rfmNChSel();
  var plan={}; ((RFM_PLAN&&RFM_PLAN.list)||[]).forEach(function(c){ plan[parseInt(c.n,10)]=c; });
  var h='<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px">';
  for(var i=1;i<=RFM_MAX;i++){
    var c=plan[i]||{n:i,mode:'NONE'};
    var on=(i<=n);
    var opts=RFM_MODES.map(function(m){
      return '<option value="'+m+'"'+(String(c.mode||'NONE').toUpperCase()===m?' selected':'')+'>'+m+'</option>';
    }).join('');
    h+='<div class="fld" data-n="'+i+'" style="border:1px solid var(--border);border-radius:10px;padding:11px;background:var(--card2);'+(on?'':'opacity:.45')+'">'
      +   '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
      +     '<span style="width:8px;height:8px;border-radius:9px;flex:0 0 8px;background:'+(on?'var(--green2)':'#64748b')+'"></span>'
      +     '<b style="font-size:12.5px;color:#7dd3fc">通道 '+i+'</b>'
      +     (on?''+'<span class="hint">已启用</span>':'<span class="hint">未启用</span>')
      +     '<span style="margin-left:auto;display:flex;align-items:center;gap:7px;color:#fff;font-weight:800;font-size:12.5px">'
      +     rfmSvg(c.mode)+String(c.mode||'NONE')+'</span>'
      +   '</div>'
      +   '<div style="display:flex;gap:7px;align-items:center;margin-bottom:7px">'
      +     '<span class="hint" style="flex:0 0 26px">RX</span>'
      +     '<input class="rfm-rx" type="text" inputmode="decimal" value="'+RFM_MHZ(rfmChanRx(i))+'" style="flex:1;min-width:0" onchange="rfmRowEdited('+i+')">'
      +     '<span class="rfm-fq hint" style="flex:0 0 96px;text-align:right">'+RFM_MHZ(rfmChanRx(i))+'</span>'
      +   '</div>'
      +   '<div style="display:flex;gap:7px;align-items:center;margin-bottom:9px">'
      +     '<span class="hint" style="flex:0 0 26px">TX</span>'
      +     '<input class="rfm-tx" type="text" inputmode="decimal" value="'+RFM_MHZ(rfmChanTx(i))+'" style="flex:1;min-width:0" onchange="rfmRowEdited('+i+')">'
      +     '<span class="rfm-fq hint" style="flex:0 0 96px;text-align:right">'+RFM_MHZ(rfmChanTx(i))+'</span>'
      +   '</div>'
      +   '<div style="display:flex;gap:6px;align-items:center">'
      +     '<select class="rfm-mode" style="flex:1;min-width:0"'+(on?'':' disabled')+' onchange="rfmApplyModeFilter2()">'+opts+'</select>'
      +     '<button class="btn btn-ghost btn-sm" onclick="rfmRowGrid('+i+')" title="按中心频率 + 硬件网格重算">按间距算</button>'
      +   '</div>'
      + '</div>';
  }
  h+='</div>';
  box.innerHTML=h;
  rfmPlanFreqs();
  rfmApplyModeFilter2();
}
function rfmPlanFreqs(){
  var box=rfmQ('rfmRows'); if(!box) return;
  var n=rfmNChSel();
  var cards=box.querySelectorAll('.fld[data-n]');
  Array.prototype.forEach.call(cards,function(cd){
    var i=parseInt(cd.getAttribute('data-n'),10), on=(i<=n);
    cd.style.opacity = on? '' : '.45';
    var r=cd.querySelector('.rfm-rx'), t=cd.querySelector('.rfm-tx');
    var md=cd.querySelector('.rfm-mode'); if(md) md.disabled=!on;
    var fq=cd.querySelectorAll('.rfm-fq');
    if(r) r.value=RFM_MHZ(rfmChanRx(i));
    if(t) t.value=RFM_MHZ(rfmChanTx(i));
    if(fq.length>=2){ fq[0].textContent=RFM_MHZ(rfmChanRx(i)); fq[1].textContent=RFM_MHZ(rfmChanTx(i)); }
  });
  if(rfmQ('rfmCrxHz')) rfmQ('rfmCrxHz').textContent='= '+rfmCenterRx()+' Hz';
  if(rfmQ('rfmCtxHz')) rfmQ('rfmCtxHz').textContent='= '+rfmCenterTx()+' Hz';
  rfmBanner();
}
function rfmCenterTyped(){ rfmPlanFreqs(); }
function rfmNChChanged(){ rfmRenderRows(); }
function rfmRowGrid(i){
  var cd=document.querySelector('#rfmRows .fld[data-n="'+i+'"]'); if(!cd) return;
  var r=cd.querySelector('.rfm-rx'), t=cd.querySelector('.rfm-tx');
  if(r) r.value=RFM_MHZ(rfmChanRx(i));
  if(t) t.value=RFM_MHZ(rfmChanTx(i));
  rfmPlanFreqs();
}
/* 用户改某一路 -> 以该路为准反推中心频率，其它通道按网格跟着走（与后端算法一致） */
function rfmRowEdited(i){
  var cd=document.querySelector('#rfmRows .fld[data-n="'+i+'"]'); if(!cd) return;
  var st=rfmGridStep(i), sp=rfmSpacingHz();
  var r=RFM_HZ((cd.querySelector('.rfm-rx')||{}).value);
  var t=RFM_HZ((cd.querySelector('.rfm-tx')||{}).value);
  if(r>0 && rfmQ('rfmCrx')) rfmQ('rfmCrx').value=RFM_MHZ(r-st*sp);
  if(t>0 && rfmQ('rfmCtx')) rfmQ('rfmCtx').value=RFM_MHZ(t-st*sp);
  rfmPlanFreqs();
}
/* ==================== 提交规划（必须带 center/spacing/sample_rate，后端按它们算每路频率） ==================== */
function rfmPlanPayload(callOverride){
  var cx=rfmCenterRx(), ct=rfmCenterTx();
  if(!cx||!ct) return null;
  var n=rfmNChSel(), sp=rfmSpacingHz();
  var old={}; ((RFM_PLAN&&RFM_PLAN.list)||[]).forEach(function(c){ old[parseInt(c.n,10)]=c; });
  var list=[];
  for(var i=1;i<=RFM_MAX;i++){
    var sel=document.querySelector('#rfmRows .fld[data-n="'+i+'"] .rfm-mode');
    var mode = sel ? sel.value : String((old[i]&&old[i].mode)||'NONE').toUpperCase();
    /* ★ 超出通道数的通道：模式照原样提交（后端只启停服务，不动保存的模式）
       —— 这样「通道数量」调小再调大时，各通道原来的协议还在，可以完整恢复多通道 */
    list.push({n:i, mode:mode, rx:cx+rfmGridStep(i)*sp, tx:ct+rfmGridStep(i)*sp});
  }
  var call = (callOverride!==undefined) ? callOverride
             : String((rfmQ('rfmCall')||{}).value||'').trim().toUpperCase();
  return {channels:n, center_rx:cx, center_tx:ct, spacing:sp,
          sample_rate:parseInt((RFM_PLAN&&RFM_PLAN.sample_rate)||150000,10)||150000,
          callsign: call || String((RFM_PLAN&&RFM_PLAN.callsign)||''),
          dmr_id: String((RFM_PLAN&&RFM_PLAN.dmr_id)||''),
          list:list};
}
function rfmPlanApply(onlySave){
  var body=rfmPlanPayload();
  if(!body){ rfmMsg('rfmPlanMsg','请先填写中心频率（MHz）', false); return; }
  if(!onlySave && !confirm('将写入 MMDVM-Multi.ini 与各通道配置，并重启受影响的通道。\n大约需要 20-60 秒。\n\n请遵守所在国家或地区关于无线电发射设备、频率使用和操作资格的法律法规。因违规使用造成的后果由操作者承担，与本软件及作者无关。\n\n继续？')) return;
  rfmMsg('rfmPlanMsg', onlySave?'正在保存规划…':'正在应用规划（约 20-60 秒）…');
  rfmPost('/api/mc/plan-'+(onlySave?'save':'apply'), body, function(d){
    var ok=!!(d&&d.ok!==false);
    var t=String((d&&(d.out||d.message||d.error))||'').trim();
    rfmMsg('rfmPlanMsg',(ok?'✅ ':'❌ ')+t.slice(0,120)+(ok?' ｜ 请遵守当地无线电发射法规，违规后果由操作者承担，与本软件无关。':''), ok);
    if(ok) setTimeout(function(){ rfmPlanLoad(); rfmLoad(); rfmModeStatus(); }, 2500);
  });
}
/* ==================== 各通道状态（永远列出 1-6，含未启用） ==================== */
function rfmBanner(){
  var b=rfmQ('rfmBanner'); if(!b) return;
  var n=rfmNChSel(), arr=[];
  for(var i=1;i<=n;i++){ arr.push('CH'+i+' '+RFM_MHZ(rfmChanRx(i))+'/'+RFM_MHZ(rfmChanTx(i))); }
  b.textContent = arr.join('  ｜  ') || '（未配置）';
}
function rfmLoad(){
  fetch('/api/mc/channels', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(d){
    if(!d || d.ok===false) return;
    var live={};
    (d.channels||[]).forEach(function(c){ live[parseInt(c.n,10)]=c; });
    RFM_CHS=d.channels||[];
    rfmApplyModeFilter2();
    var planList={}; ((RFM_PLAN&&RFM_PLAN.list)||[]).forEach(function(c){ planList[parseInt(c.n,10)]=c; });
    if(typeof mcSeqBuild==='function') mcSeqBuild(d.channels||[]);
    var box=rfmQ('rfmChans'); if(!box) return;
    var h='<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px">';
    for(var i=1;i<=RFM_MAX;i++){
      var c=live[i]||null;
      var mode=c?String(c.mode||'NONE'):String((planList[i]&&planList[i].mode)||'NONE');
      var rx=(c&&c.rx)?c.rx:((planList[i]&&planList[i].rx)||0);
      var tx=(c&&c.tx)?c.tx:((planList[i]&&planList[i].tx)||0);
      var run=!!(c&&c.iq&&c.host), on=!!c;
      var curMode=mode.toUpperCase();
      var usedElsewhere={};
      for(var j=1;j<=RFM_MAX;j++){
        if(j===i) continue;
        var c2=live[j]||null;
        var m2=c2?String(c2.mode||'NONE').toUpperCase():String((planList[j]&&planList[j].mode)||'NONE').toUpperCase();
        if(m2&&m2!=='DMR'&&m2!=='NONE') usedElsewhere[m2]=true;
      }
      var opts=RFM_MODES.map(function(m){
        var dis=(m!=='DMR'&&m!=='NONE'&&usedElsewhere[m]&&m!==curMode);
        return '<option value="'+m+'"'+(curMode===m?' selected':'')+(dis?' disabled style="color:#555"':'')+'>'+m+'</option>';
      }).join('');
      h+='<div class="fld" data-ch="'+i+'" style="border:1px solid var(--border);border-radius:10px;padding:11px;background:var(--card2);'+(on?'':'opacity:.5')+'">'
        +   '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
        +     '<span style="width:8px;height:8px;border-radius:9px;flex:0 0 8px;background:'+(run?'var(--green2)':(on?'var(--amber2)':'#64748b'))+'"></span>'
        +     '<b style="font-size:12.5px;color:#7dd3fc">通道 '+i+'</b>'
        +     (on?'':'<span class="hint">未启用</span>')
        +     '<span style="margin-left:auto;display:flex;align-items:center;gap:7px;color:#fff;font-weight:800;font-size:12.5px">'
        +     rfmSvg(mode)+mode+'</span>'
        +   '</div>'
        +   '<div class="hint" style="margin-bottom:8px">RX '+RFM_MHZ(rx)+' / TX '+RFM_MHZ(tx)+' MHz'
        +     (on?((c.iq?' ｜ IQ ✓':' ｜ IQ ✗')+(c.host?' Host ✓':' Host ✗')):'')+'</div>'
        +   '<div style="display:flex;gap:6px;align-items:center">'
        +     '<select class="rfm-mode2" style="flex:1;min-width:0"'+(on?'':' disabled')+' onchange="rfmApplyModeFilter2()">'+opts+'</select>'
        +     '<button class="btn btn-green btn-sm"'+(on?'':' disabled')+' onclick="rfmSwitch('+i+')">切换</button>'
        +     '<button class="btn btn-ghost btn-sm"'+(on?'':' disabled')+' onclick="rfmRestart('+i+')">重启</button>'
        +   '</div>'
        + '</div>';
    }
    h+='</div>';
    box.innerHTML=h;
    rfmBanner();
  }).catch(function(){});
}
function rfmSwitch(n){
  var card=document.querySelector('#rfmChans .fld[data-ch="'+n+'"]'); if(!card) return;
  var c2=card.querySelector('.rfm-mode2'); var mode=c2?c2.value:'NONE';
  if(!confirm('把射频通道 '+n+' 切换为 '+mode+' ？将重启该通道。')) return;
  if(typeof toast==='function') toast('通道 '+n+' 正在切换…');
  rfmPost('/api/mc/switch',{ch:n,mode:mode},function(d){
    var ok=!!(d&&d.ok);
    if(typeof toast==='function') toast(ok?('通道 '+n+' 已切换为 '+mode):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok) setTimeout(function(){ rfmLoad(); rfmPlanLoad(); }, 1800);
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
    if(ok) setTimeout(function(){ rfmLoad(); rfmPlanLoad(); }, 5000);
  });
}
document.addEventListener('change', function(e){
  if(e.target && e.target.id==='rfmPower') rfmPowerChanged();
});
(function(){ var s=rfmQ('rfmPower'); if(s) rfmPowerChanged(); })();
/* 兜底：规划读取失败时页面也能用（默认中心频率 + 通道数选择器 + 通道行） */
(function(){
  if(!rfmQ('rfmNCh')) return;
  if(!rfmQ('rfmNCh').innerHTML) rfmFillNCh(6);
  if(rfmQ('rfmCrx') && !rfmQ('rfmCrx').value) rfmQ('rfmCrx').value='434.450';
  if(rfmQ('rfmCtx') && !rfmQ('rfmCtx').value) rfmQ('rfmCtx').value='439.450';
  if(rfmQ('rfmRows') && !document.querySelector('#rfmRows .fld[data-n]')) rfmRenderRows();
})();
"""

