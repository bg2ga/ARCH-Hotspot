# -*- coding: utf-8 -*-
"""FM 联网配置页面（精简版 1.0.121）。"""

FMO_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARCH · FM 联网配置</title>
<script>try{var q=new URLSearchParams(location.search).get("theme"),m=localStorage.getItem("arch-theme-mode");var light=(q==="light"||q==="day"||(q!=="dark"&&q!=="night"&&(m==="day"||m==="light")));document.documentElement.classList.toggle("light",light);}catch(e){}</script>
<script>
window.addEventListener('message',function(ev){if(ev.origin!==location.origin)return;var d=ev.data||{};if(d.type==='arch-theme'){var light=(d.theme==='light'||d.theme==='day');document.documentElement.classList.toggle('light',light);}});
</script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#dfe8f5;font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif;padding:14px}
body.embed{padding:6px 10px 10px}body.embed .top{display:none}
.top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}.top h1{font-size:18px;font-weight:650}
.badge{font-size:12px;padding:3px 9px;border-radius:99px;background:#17233a;border:1px solid #26354f;color:#8fa6c4}
.badge.ok{background:#0e2f22;border-color:#1d6b4a;color:#4ade80}.badge.bad{background:#331616;border-color:#7f2222;color:#f87171}
.card{background:#101a2e;border:1px solid #1e2b44;border-radius:11px;padding:14px;margin-bottom:12px}.card>h2{font-size:14px;font-weight:640;color:#9fb6d6;margin-bottom:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.kv{background:#0c1526;border:1px solid #1b2840;border-radius:8px;padding:9px 11px}.k{font-size:11.5px;color:#7d93b2}.v{font-size:14px;font-weight:600;margin-top:3px;word-break:break-all}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row+.row{margin-top:9px}.lbl{font-size:12.5px;color:#9fb6d6;min-width:86px}
input,select{background:#0c1526;border:1px solid #2a3a56;color:#dfe8f5;padding:6px 9px;border-radius:7px;font-size:13px;min-width:120px}
button{cursor:pointer;border:1px solid #2a3a56;background:#16264a;color:#cfe0f5;padding:6px 12px;border-radius:7px;font-size:13px}
button:hover{background:#1d3260}.hint{font-size:12.5px;color:#7d93b2;margin-top:8px}
#toast{display:none;position:fixed;left:50%;transform:translateX(-50%);bottom:20px;padding:9px 14px;border-radius:10px;background:#0e2f22;border:1px solid #1d6b4a;color:#bbf7d0;z-index:99;max-width:min(520px,90vw);text-align:center}
#toast.bad{background:#331616;border-color:#7f2222;color:#fca5a5}
html.light body{background:#f2efe8;color:#16243a}html.light .card,html.light .kv{background:#fbfaf7;border-color:#d8d3c7}html.light .card>h2,html.light .lbl{color:#35516f}html.light .k{color:#60758d}html.light input,html.light select{background:#fff;color:#16243a;border-color:#cbd5e1}html.light button{background:#eef3f9;color:#1e3a5f;border-color:#cbd5e1}
</style>
</head>
<body class="embed">
<div class="top"><h1>FM 联网配置</h1><span id="svcBadge" class="badge">读取中</span></div>

<div class="card"><h2>服务状态</h2>
<div class="grid">
<div class="kv"><div class="k">桥接服务</div><div class="v" id="vSvc">--</div></div>
<div class="kv"><div class="k">证书</div><div class="v" id="vCert">--</div></div>
<div class="kv"><div class="k">服务器</div><div class="v" id="vSrv">--</div></div>
<div class="kv"><div class="k">FM 通道 / 频率</div><div class="v" id="vChan">--</div></div>
</div>
<div class="row" style="margin-top:10px"><button onclick="restartBridge()">重启 FMO 桥接</button><button onclick="serviceAction('stop')">停止</button><button onclick="loadStatus()">刷新</button></div>
</div>

<div class="card"><h2>服务器与身份</h2>
<div class="grid">
<label class="kv"><div class="k">注册服务器</div><input id="srvHost" style="width:100%" value="fmo.hljrsa.cn" placeholder="fmo.hljrsa.cn"></label>
<label class="kv"><div class="k">注册端口</div><input id="srvRegPort" type="number" style="width:100%" value="8081" placeholder="8081"></label>
<label class="kv"><div class="k">本机呼号</div><input id="cliCs" style="width:100%"></label>
</div>
<div class="row" style="margin-top:10px"><button onclick="doPing()">探测服务器</button><button onclick="doRegister()">申请 / 重新签发证书</button><button onclick="unregisterCert()">注销证书</button></div>
<div class="hint">注册成功后会自动启动 FMO 桥接。接收亚音固定开启，发射亚音单独设置。</div>
</div>

<div class="card"><h2>FM 哑音 (CTCSS)</h2>
<div class="grid">
<label class="kv"><div class="k">接收 / 发射哑音</div><select id="ctTone" style="width:100%"></select></label>
<label class="kv"><div class="k">静噪级别</div><select id="sqLevel" style="width:100%"><option>0</option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option><option>8</option><option>9</option></select></label>
<label class="kv"><div class="k">发射哑音</div><label class="row" style="margin-top:6px"><input id="ctNeedTx" type="checkbox"><span>开启</span></label></label>
</div>
<div class="row" style="margin-top:10px"><button onclick="applyCtcss()">应用哑音设置</button><span id="ctState" class="hint" style="margin:0"></span></div>
</div>

<div class="card"><h2>尾音确认音</h2>
<div class="grid">
<label class="kv"><div class="k">启用</div><label class="row" style="margin-top:6px"><input id="toneOn" type="checkbox"><span>发射结束后回一声</span></label></label>
<label class="kv"><div class="k">频率 Hz</div><input id="toneFreq" type="number" step="50" style="width:100%"></label>
<label class="kv"><div class="k">持续时间 ms</div><input id="toneMs" type="number" step="20" style="width:100%"></label>
<label class="kv"><div class="k">延迟 ms</div><input id="toneDelay" type="number" step="20" style="width:100%"></label>
<label class="kv"><div class="k">增益 0-1</div><input id="toneGain" type="number" step="0.05" style="width:100%"></label>
</div>
<div class="row" style="margin-top:10px"><button onclick="saveTone()">保存尾音</button><button onclick="toneTest()">测试</button><span id="toneChan" class="hint" style="margin:0"></span></div>
</div>

<div id="toast"></div>
<script>
function $i(i){return document.getElementById(i)}function num(i){return parseFloat($i(i).value||0)}function chk(i){return !!$i(i).checked}function setV(i,v){if(v!==null&&v!==undefined)$i(i).value=v}
function toast(m,ok){var t=$i("toast");t.textContent=m;t.className=ok===false?"bad":"";t.style.display="block";clearTimeout(window._tt);window._tt=setTimeout(function(){t.style.display="none"},4200)}
function post(u,b){return fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})}).then(function(r){return r.json()})}
function loadStatus(){fetch("/api/fmo/status").then(function(r){return r.json()}).then(function(d){if(!d||d.ok===false){$i("svcBadge").textContent="读取失败";$i("svcBadge").className="badge bad";return}var svc=(d.services||{})["fmo-bridge"]||"inactive",reg=!!(d.cert&&d.cert.exists),c=d.cfg||{},s=c.server||{},cl=c.client||{},fm=c.fm||{};$i("svcBadge").textContent=reg?(svc==="active"?"桥接运行中":"桥接未运行"):"证书未注册";$i("svcBadge").className="badge "+(reg&&svc==="active"?"ok":"bad");$i("vSvc").textContent=svc==="active"?"运行中":"未运行";$i("vCert").textContent=(d.cert&&d.cert.callsign||cl.callsign||"--")+" / "+((d.cert&&d.cert.uid!=null)?d.cert.uid:(cl.uid==null?"--":cl.uid));$i("vSrv").textContent=(s.callsign||"--")+" / "+(s.uid==null?"--":s.uid);$i("vChan").textContent=(fm.tx_mhz?("TX "+fm.tx_mhz+" / RX "+fm.rx_mhz):"未检测到 FM 通道");setV("srvHost",s.mqtt_host);setV("srvRegPort",s.reg_port||8081);if(cl.callsign&&document.activeElement!==$i("cliCs"))setV("cliCs",cl.callsign)}).catch(function(e){toast("读取失败: "+e,false)})}
function doPing(){var h=$i("srvHost").value.trim();if(!h){toast("请先填服务器地址",false);return}toast("正在探测服务器…");post("/api/fmo/ping",{host:h,port:num("srvRegPort")||8081}).then(function(r){if(!r.ok){toast("探测失败: "+(r.error||""),false);return}var s=(r.info&&r.info.server)||{};toast("服务器 "+(s.callsign||"?")+" uid="+(s.uid==null?"?":s.uid),true)}).catch(function(e){toast("探测失败: "+e,false)})}
function doRegister(){var h=$i("srvHost").value.trim(),p=num("srvRegPort")||8081,cs=$i("cliCs").value.trim().toUpperCase();if(!cs){toast("请填写本机呼号",false);return}if(!confirm("为呼号 "+cs+" 申请或重新签发 FMO 证书？"))return;toast("正在注册，请稍候…");post("/api/fmo/register",{host:h,port:p,callsign:cs,uid:0}).then(function(r){if(!r.ok){toast("注册失败: "+(r.error||""),false);return}var c=r.client||{};$i("cliCs").value=c.callsign||cs;toast(r.message||("注册成功："+(c.callsign||cs)),true);setTimeout(loadStatus,1800)}).catch(function(e){toast("注册失败: "+e,false)})}
function unregisterCert(){if(!confirm("确定注销本机 FMO 证书？"))return;post("/api/fmo/unregister",{}).then(function(r){toast(r.ok?(r.message||"已注销"):("注销失败: "+(r.error||"")),r.ok);setTimeout(loadStatus,1200)}).catch(function(e){toast("注销失败: "+e,false)})}
function serviceAction(a){post("/api/fmo/service",{action:a,what:"bridge"}).then(function(r){toast(r.ok?"操作成功":("失败: "+(r.error||"")),r.ok);setTimeout(loadStatus,1200)}).catch(function(e){toast("操作失败: "+e,false)})}
function restartBridge(){post("/api/fmo/restart",{what:"bridge"}).then(function(r){toast(r.ok?"桥接已重启":("失败: "+(r.error||"")),r.ok);setTimeout(loadStatus,1600)}).catch(function(e){toast("重启失败: "+e,false)})}
function fillTones(sel,tones,common,val){var h='<optgroup label="常用">';for(var i=0;i<common.length;i++)h+='<option value="'+common[i]+'">'+common[i]+' Hz</option>';h+='</optgroup><optgroup label="全部标准哑音">';for(var j=0;j<tones.length;j++)h+='<option value="'+tones[j]+'">'+tones[j]+' Hz</option>';h+='</optgroup>';sel.innerHTML=h;sel.value=(val&&val!=="off")?val:String(common[0]||88.5);if(sel.selectedIndex<0)sel.value=String(common[0]||88.5)}
function loadCtcss(){fetch("/api/fmo/ctcss").then(function(r){return r.json()}).then(function(r){if(!r.ok)return;var c=r.current||{};fillTones($i("ctTone"),r.tones||[],r.common||[],c.tone);$i("ctNeedTx").checked=!!c.need_tx;var lv=parseInt(c.squelch_level);$i("sqLevel").value=String(isNaN(lv)?3:Math.max(0,Math.min(9,lv)));$i("ctState").textContent=(c.tone||"88.5")+" Hz / 静噪 "+$i("sqLevel").value}).catch(function(){})}
function applyCtcss(){var t=$i("ctTone").value,lv=parseInt($i("sqLevel").value)||0,tx=$i("ctNeedTx").checked;if(!t||t==="off"){toast("请选择哑音频率",false);return}post("/api/fmo/apply-ctcss",{tone:t,need_rx:true,need_tx:tx,squelch_level:lv}).then(function(r){toast(r.ok?"哑音已应用":("失败: "+(r.error||"")),r.ok);if(r.ok)setTimeout(loadCtcss,2500)}).catch(function(e){toast("应用失败: "+e,false)})}
function loadTone(){fetch("/api/fmo/tailtone").then(function(r){return r.json()}).then(function(t){if(!t||!t.ok)return;$i("toneOn").checked=!!t.enable;setV("toneFreq",t.freq);setV("toneMs",t.ms);setV("toneDelay",t.delay_ms);setV("toneGain",t.gain);$i("toneChan").textContent=t.channel?("当前 FM 通道 CH"+t.channel):""}).catch(function(){})}
function saveTone(){post("/api/fmo/tailtone/save",{enable:chk("toneOn"),freq:num("toneFreq"),ms:Math.round(num("toneMs")),delay_ms:Math.round(num("toneDelay")),gain:num("toneGain")}).then(function(r){toast(r.ok?"尾音已保存":("失败: "+(r.error||"")),r.ok);if(r.ok)setTimeout(loadTone,2200)}).catch(function(e){toast("保存失败: "+e,false)})}
function toneTest(){post("/api/fmo/tone-test",{}).then(function(r){toast(r.ok?(r.hint||"已发送"):("失败: "+(r.error||"")),r.ok)}).catch(function(e){toast("测试失败: "+e,false)})}
loadStatus();loadCtcss();loadTone();setInterval(loadStatus,10000);
</script>
</body>
</html>'''
