# -*- coding: utf-8 -*-
"""射频模式配置（统一页）—— 复用主控制台组件类，外观与 DMR 页一致
   ① 台站与射频参数：呼号 + 功率档位 + 收发增益（原「硬件基础配置」的呼号并到这里）
   ② 信道规划：通道数量(1-7) + 中心频率(MHz) + 间距(kHz) + 各通道模式/频率
   ③ 各通道状态与模式切换：永远列出 1-7 全部通道（含未启用），可随时调回多通道"""

RFMODE2_SECTION = r"""
    <section class="page proto-page" id="page-rfmode" style="display:none">

      <!-- ===== ② 台站与射频参数 ===== -->
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--green-dim);color:var(--green2)">&#128295;</div>
            <div>
              <div class="card-title">台站与射频参数（呼号 &middot; 功率 &middot; 增益）</div>
              <div class="card-sub">热点呼号写入 MMDVM-Host 与各网关；功率/增益写入 MMDVM-IQ.ini [Soapy]，全模式通用</div>
            </div>
          </div>
          <span class="card-badge">SX1255</span>
        </div>

        <div class="form-grid">
          <div class="fld"><label>本机呼号 (Callsign)</label>
            <input id="rfmCall" placeholder="NOCALL" maxlength="10" autocomplete="off">
            <div class="hint">一定要与 BrandMeister / 网关注册的呼号一致（会写进 MMDVM-Host 与各网关）</div></div>
          <div class="fld"><label>射频发射功率档位 (SX1255)</label>
            <select id="rfmPower">
              <option value="7">大功率 &#8212; 7 级 / +7 dBm / 5 mW（远距离、抗干扰）</option>
              <option value="0">小功率 &#8212; 0 级 / 0 dBm / 1 mW（近距离、更健康）</option>
            </select>
            <div class="hint">切换档位会自动把 TxGain 设为 30dB（大功率）或 10dB（小功率）</div></div>
          <div class="fld"><label>SX1255 硬件基带接收增益 (RxGain 0-60 dB)</label>
            <input id="rfmRxGain" type="number" min="0" max="60" value="50">
            <div class="hint">MMDVM-IQ.ini [Soapy]，推荐 50 dB（此值为当前配置，已自动读取）</div></div>
          <div class="fld"><label>SX1255 硬件基带发射增益 (TxGain 0-40 dB)</label>
            <input id="rfmTxGain" type="number" min="0" max="40" value="30">
            <div class="hint">大功率推荐 30dB / 小功率推荐 10dB</div></div>
        </div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-green btn-sm" onclick="rfmSaveStation()">&#128190; 保存呼号 / 功率 / 增益</button>
          <span class="hint" id="rfmStMsg" style="margin-left:10px"></span>
        </div>
      </div>

      <!-- ===== ③ 信道规划 ===== -->
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--amber-dim);color:var(--amber2)">&#128226;</div>
            <div>
              <div class="card-title">信道规划（多信道同时收发）</div>
              <div class="card-sub">先定通道数量与中心频率，再给每路选协议；频率可直接填 MHz —— 系统自动换算成 Hz 并写入 MMDVM-Multi.ini 与各通道配置</div>
            </div>
          </div>
          <span class="card-badge">统一频率架构</span>
        </div>

        <div class="freq-banner">
          <div class="freq-banner-ico">&#128225;</div>
          <div class="freq-banner-txt">
            <b>当前各通道实际频率</b>
            <span id="rfmBanner">读取中&hellip;</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="rfmPlanLoad()">&#8635; 重新读取</button>
        </div>

        <div class="form-grid" style="margin-top:12px">
          <div class="fld"><label>通道数量 (1 - 6)</label>
            <select id="rfmNCh" onchange="rfmNChChanged()"></select>
            <div class="hint">调小 = 停用多余通道（<b>配置会保留</b>）；以后调回大数字再保存即可恢复多通道</div></div>
          <div class="fld"><label>接收中心频率 (MHz)</label>
            <input id="rfmCrx" type="text" inputmode="decimal" placeholder="434.450" oninput="rfmCenterTyped()">
            <div class="hint" id="rfmCrxHz">例：434.450</div></div>
          <div class="fld"><label>发射中心频率 (MHz)</label>
            <input id="rfmCtx" type="text" inputmode="decimal" placeholder="439.450" oninput="rfmCenterTyped()">
            <div class="hint" id="rfmCtxHz">例：439.450</div></div>
          <div class="fld"><label>通道间距 (kHz)</label>
            <select id="rfmGap" onchange="rfmPlanFreqs()">
              <option value="25">25 kHz（标准，6 路以内用这个）</option>
              <option value="12.5">12.5 kHz（窄带）</option>
            </select>
            <div class="hint">SX1255 各通道按 25 kHz 网格排列，中心频率决定整体位置</div></div>
        </div>

        <div id="rfmRows" style="margin-top:12px"><div class="sp-empty">正在读取通道规划&hellip;</div></div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-green btn-sm" onclick="rfmPlanApply(false)">&#9889; 保存并应用（写入配置 + 重启受影响通道）</button>
          <button class="btn btn-ghost btn-sm" onclick="rfmPlanApply(true)">仅保存规划</button>
          <span class="hint" id="rfmPlanMsg" style="margin-left:10px"></span>
        </div>
        <div class="hint" style="margin-top:9px;line-height:1.75">
          <b style="color:#fbbf24">频率输入习惯：</b>直接按 MHz 填 ——
          25 kHz 步进写 <b>439.500</b>（= 439500000 Hz），12.5 kHz 步进写 <b>439.5125</b>（= 439512500 Hz）。<br>
          改任意一路的频率时，会以「它」为准反推中心频率，其它通道按硬件网格跟着走；
          表格右侧显示的 MHz 就是设备真实收发频率，对讲机请照它设置。
        </div>
      </div>

      <!-- ===== ③ 各通道状态与切换 ===== -->
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(56,189,248,.12);color:#38bdf8">&#128251;</div>
            <div>
              <div class="card-title">各通道状态与模式切换</div>
              <div class="card-sub">列出全部 1 - 7 路（含未启用），可随时切模式 / 重启；未启用的通道把「通道数量」调大后保存即可恢复</div>
            </div>
          </div>
          <span class="card-badge">MMDVM-Multi</span>
        </div>

        <div id="rfmChans"><div class="sp-empty">正在读取通道&hellip;</div></div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-ghost btn-sm" onclick="rfmLoad()">&#8635; 刷新状态</button>
          <button class="btn btn-ghost btn-sm" onclick="rfmRestartAll()">&#8634; 全部重启</button>
          <span class="hint" id="rfmMsg" style="margin-left:10px"></span>
        </div>
      </div>

      <!-- ===== ⑤ 引擎 / 服务 / 日志（诊断） ===== -->
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(148,163,184,.12);color:#94a3b8">&#128202;</div>
            <div>
              <div class="card-title">引擎 / 服务 / 日志（诊断）</div>
              <div class="card-sub">引擎实时参数、各服务运行状态、按通道 / 网关查看日志（诊断用）</div>
            </div>
          </div>
          <span class="card-badge" id="mcChBadge">--</span>
        </div>

        <div class="form-grid" id="mcEngBox"><div class="hint">正在读取引擎状态&hellip;</div></div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-ghost btn-sm" onclick="mcRefreshAll()">&#8635; 刷新状态</button>
          <button class="btn btn-ghost btn-sm" onclick="mcRestartEngine()">&#8634; 重启 Multi 引擎</button>
          <span class="hint" id="mcMsg" style="margin-left:10px"></span>
        </div>

        <div style="margin-top:14px">
          <div class="hint" style="margin-bottom:7px"><b style="color:#7dd3fc">服务状态</b> <span id="mcSvcBadge"></span></div>
          <div class="form-grid" id="mcSvcBox"><div class="hint">正在读取服务&hellip;</div></div>
        </div>

        <div class="form-grid" style="margin-top:14px">
          <div class="fld"><label>日志：通道</label><select id="mcLogCh"></select></div>
          <div class="fld"><label>服务</label>
            <select id="mcLogSvc">
              <option value="mmdvmhost">MMDVM-Host</option>
              <option value="mmdvm-iq">MMDVM-IQ</option>
              <option value="mmdvm-multi">MMDVM-Multi</option>
              <option value="dmrgateway">DMRGateway</option>
              <option value="ircddbgatewayd">ircDDBGateway (D-STAR)</option>
              <option value="ysfgateway">YSFGateway</option>
              <option value="p25gateway">P25Gateway</option>
              <option value="nxdngateway">NXDNGateway</option>
              <option value="fmo-bridge">FMO Bridge（FM 联网桥）</option>
            </select></div>
          <div class="fld"><label>行数</label>
            <select id="mcLogLines">
              <option value="100">100 行</option>
              <option value="200" selected>200 行</option>
              <option value="500">500 行</option>
            </select></div>
        </div>
        <div class="mc-foot" style="margin:10px 0 0">
          <button class="btn btn-green btn-sm" onclick="mcLoadLog()">&#128203; 加载日志</button>
        </div>
        <pre id="mcLogBox" style="margin-top:10px;max-height:340px;overflow:auto">点击「加载日志」查看。</pre>
      </div>

    </section>
"""
