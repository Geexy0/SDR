"""Phase 1 landing page — hardware status + mode controls (no full C2 UI yet)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, Response

from pp_dashboard import __version__
from pp_dashboard.web.spa import FRONTEND_DIST, frontend_dist_ready, spa_index

router = APIRouter(tags=["web"])

_HOME_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>PortaPack / HackRF · Phase 5</title>
  <link rel="icon" href="/favicon.svg" type="image/svg+xml"/>
  <style>
    :root {{
      --bg: #0e1116;
      --panel: #161b22;
      --line: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --ok: #3fb950;
      --warn: #d29922;
      --bad: #f85149;
      --accent: #58a6ff;
      --mono: "IBM Plex Mono", "JetBrains Mono", ui-monospace, monospace;
      --sans: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; color: var(--text);
      background:
        radial-gradient(1200px 600px at 10% -10%, #1a2332 0%, transparent 55%),
        radial-gradient(900px 500px at 100% 0%, #1c1520 0%, transparent 50%),
        var(--bg);
      font-family: var(--sans);
    }}
    main {{ max-width: 920px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }}
    .brand {{
      font-size: clamp(1.6rem, 4vw, 2.2rem); font-weight: 650; letter-spacing: -0.02em;
      margin: 0 0 0.35rem;
    }}
    .sub {{ color: var(--muted); margin: 0 0 1.75rem; max-width: 42rem; line-height: 1.45; }}
    .panel {{
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      border: 1px solid var(--line); border-radius: 12px; padding: 1.1rem 1.2rem;
      backdrop-filter: blur(8px);
    }}
    .row {{ display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }}
    .grid {{ display: grid; gap: 1rem; grid-template-columns: 1fr; }}
    @media (min-width: 720px) {{ .grid {{ grid-template-columns: 1.3fr 0.7fr; }} }}
    .label {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; }}
    .value {{ font-family: var(--mono); font-size: 1.05rem; margin-top: 0.2rem; }}
    .pill {{
      display: inline-flex; align-items: center; gap: 0.4rem;
      font-family: var(--mono); font-size: 0.85rem;
      padding: 0.35rem 0.7rem; border-radius: 999px; border: 1px solid var(--line);
      background: #0d1117;
    }}
    .dot {{ width: 0.55rem; height: 0.55rem; border-radius: 50%; background: var(--muted); }}
    .dot.ok {{ background: var(--ok); box-shadow: 0 0 10px color-mix(in srgb, var(--ok) 60%, transparent); }}
    .dot.warn {{ background: var(--warn); }}
    .dot.bad {{ background: var(--bad); }}
    button, .linkbtn {{
      appearance: none; border: 1px solid var(--line); background: #21262d; color: var(--text);
      border-radius: 8px; padding: 0.55rem 0.9rem; font: inherit; cursor: pointer;
      text-decoration: none; display: inline-flex; align-items: center; gap: 0.35rem;
    }}
    button.primary {{ background: #1f6feb; border-color: #388bfd; }}
    button:disabled {{ opacity: 0.45; cursor: not-allowed; }}
    button:hover:not(:disabled), .linkbtn:hover {{ filter: brightness(1.08); }}
    pre {{
      margin: 0.75rem 0 0; padding: 0.85rem; overflow: auto; max-height: 220px;
      background: #0d1117; border: 1px solid var(--line); border-radius: 8px;
      font-family: var(--mono); font-size: 0.78rem; color: #c9d1d9;
    }}
    .links {{ margin-top: 1.25rem; display: flex; flex-wrap: wrap; gap: 0.6rem; }}
    .err {{ color: var(--bad); font-family: var(--mono); font-size: 0.85rem; min-height: 1.2em; }}
    footer {{ margin-top: 1.5rem; color: var(--muted); font-size: 0.85rem; }}
  </style>
</head>
<body>
  <main>
    <h1 class="brand">PortaPack H4M · HackRF Pro</h1>
    <p class="sub">v0.7.0 Cyber-Deck — arbiter, RF, Mayhem, Sub-GHz workbench, mission recon, geoloc/DF, TPMS, gated TX.
      Prefer the React SPA at <code>/</code> when <code>frontend/dist</code> is built.
      Sweep &amp; TX need <code>HACKRF_NATIVE</code>; screen mirror needs <code>MAYHEM_SERIAL</code>.</p>

    <div class="grid">
      <section class="panel">
        <div class="row" style="justify-content: space-between;">
          <div>
            <div class="label">Hardware state</div>
            <div class="pill" id="state-pill"><span class="dot" id="dot"></span><span id="state">…</span></div>
          </div>
          <button type="button" id="refresh">Refresh</button>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.9rem; margin-top:1rem;">
          <div><div class="label">Serial</div><div class="value" id="serial">—</div></div>
          <div><div class="label">Subprocess</div><div class="value" id="proc">—</div></div>
          <div><div class="label">Mayhem</div><div class="value" id="mayhem">—</div></div>
          <div><div class="label">Native</div><div class="value" id="native">—</div></div>
        </div>
        <div class="err" id="err"></div>
        <pre id="raw">Loading…</pre>
      </section>

      <section class="panel">
        <div class="label">Mode switch</div>
        <p class="sub" style="margin:0.4rem 0 0.9rem;font-size:0.92rem;">
          Dual-mode USB mutex — only one mode at a time. Switching re-enumerates the device.
        </p>
        <div class="row">
          <button type="button" class="primary" id="to-native">→ HACKRF_NATIVE</button>
          <button type="button" id="to-mayhem">→ MAYHEM_SERIAL</button>
          <button type="button" id="stop">Stop SDR job</button>
        </div>
        <div class="links">
          <a class="linkbtn" href="/docs">OpenAPI /docs</a>
          <a class="linkbtn" href="/healthz">/healthz</a>
          <a class="linkbtn" href="/api/hardware/status">/api/hardware/status</a>
        </div>
      </section>
    </div>

    <section class="panel" style="margin-top:1rem;">
      <div class="label">Phase 2 · Active RF</div>
      <p class="sub" style="margin:0.4rem 0 0.9rem;font-size:0.92rem;">
        Lab TX only. Default amp off. Watchdog caps jam/loop duration (≤60s).
      </p>
      <div class="row" style="margin-bottom:0.75rem;">
        <label class="label">f<sub>c</sub> Hz
          <input id="freq" type="number" value="433920000" style="display:block;margin-top:0.25rem;width:11rem;background:#0d1117;border:1px solid var(--line);color:var(--text);border-radius:6px;padding:0.4rem;font-family:var(--mono);"/>
        </label>
        <label class="label">TX VGA
          <input id="txvga" type="number" value="20" min="0" max="47" style="display:block;margin-top:0.25rem;width:5rem;background:#0d1117;border:1px solid var(--line);color:var(--text);border-radius:6px;padding:0.4rem;font-family:var(--mono);"/>
        </label>
        <label class="label">Duration s
          <input id="dur" type="number" value="5" min="0.5" max="60" step="0.5" style="display:block;margin-top:0.25rem;width:5rem;background:#0d1117;border:1px solid var(--line);color:var(--text);border-radius:6px;padding:0.4rem;font-family:var(--mono);"/>
        </label>
      </div>
      <div class="row">
        <button type="button" id="jam-cw">Jam CW/AWGN</button>
        <button type="button" id="jam-chirp">Jam Chirp Barrage</button>
        <button type="button" id="cap">Capture IQ</button>
      </div>
      <div class="err" id="rf-err"></div>
      <pre id="rf-raw" style="max-height:160px;">Phase 2 result…</pre>
    </section>

    <section class="panel" style="margin-top:1rem;">
      <div class="label">Phase 3 · Mayhem serial</div>
      <p class="sub" style="margin:0.4rem 0 0.9rem;font-size:0.92rem;">
        Remote GUI via <code>screenframeshort</code> · virtual buttons · featured app launcher.
      </p>
      <div class="row" style="align-items:flex-start;gap:1rem;">
        <canvas id="mayhem-canvas" width="240" height="320"
          style="width:240px;height:320px;image-rendering:pixelated;background:#000;border:1px solid var(--line);border-radius:8px;cursor:crosshair;"></canvas>
        <div style="flex:1;min-width:220px;">
          <div class="row" style="margin-bottom:0.6rem;">
            <button type="button" class="primary" id="screen-start">Start mirror</button>
            <button type="button" id="screen-stop">Stop</button>
            <button type="button" id="apps-refresh">Refresh apps</button>
          </div>
          <div class="label">D-pad / Select / Encoder</div>
          <div class="row" style="margin:0.4rem 0 0.8rem;">
            <button type="button" data-btn="4">Up</button>
            <button type="button" data-btn="2">Left</button>
            <button type="button" data-btn="5">Select</button>
            <button type="button" data-btn="1">Right</button>
            <button type="button" data-btn="3">Down</button>
            <button type="button" data-btn="7">Enc←</button>
            <button type="button" data-btn="8">Enc→</button>
          </div>
          <div class="row" style="margin-bottom:0.6rem;">
            <input id="kb-text" placeholder="keyboard text" style="flex:1;min-width:8rem;background:#0d1117;border:1px solid var(--line);color:var(--text);border-radius:6px;padding:0.45rem;font-family:var(--mono);"/>
            <button type="button" id="kb-send">Send keys</button>
          </div>
          <div class="label">Featured apps</div>
          <div class="row" id="featured-apps" style="margin-top:0.4rem;"></div>
          <div class="err" id="mayhem-err"></div>
        </div>
      </div>
    </section>

    <section class="panel" style="margin-top:1rem;">
      <div class="label">Phase 4 · Sub-GHz</div>
      <p class="sub" style="margin:0.4rem 0 0.9rem;font-size:0.92rem;">
        rtl_433 sniff · pulse analyze · OOK bruteforce (lab DUT only) · .sub convert.
      </p>
      <div class="row">
        <button type="button" id="sg-sniff">Start sniff 433.92</button>
        <button type="button" id="sg-sniff-stop">Stop sniff</button>
        <button type="button" id="sg-packets">List packets</button>
        <button type="button" id="sg-bf">Bruteforce PT2262 0..15</button>
        <button type="button" id="sg-bf-stop">Stop BF (±1s keys)</button>
      </div>
      <div class="err" id="sg-err"></div>
      <pre id="sg-raw" style="max-height:160px;">Sub-GHz…</pre>
    </section>

    <section class="panel" style="margin-top:1rem;">
      <div class="label">Phase 5 · Waterfall / Recon / Spoof</div>
      <p class="sub" style="margin:0.4rem 0 0.9rem;font-size:0.92rem;">
        <code>hackrf_sweep -B</code> waterfall · POCSAG/ADS-B/AIS · GPS/pager/FM synthesize (SCOPE gated).
      </p>
      <canvas id="wf-canvas" width="640" height="160"
        style="width:100%;max-width:640px;height:160px;background:#000;border:1px solid var(--line);border-radius:8px;image-rendering:pixelated;"></canvas>
      <div class="row" style="margin-top:0.6rem;">
        <button type="button" class="primary" id="wf-start">Sweep 400–450 MHz</button>
        <button type="button" id="wf-stop">Stop sweep</button>
        <button type="button" id="dec-pocsag">POCSAG</button>
        <button type="button" id="dec-adsb">ADS-B</button>
        <button type="button" id="synth-pager">TX LRS pager</button>
        <button type="button" id="synth-fm">TX FM/RDS</button>
      </div>
      <div class="err" id="recon-err"></div>
      <pre id="recon-raw" style="max-height:140px;">Recon…</pre>
    </section>

    <footer>pp-dashboard v{__version__} · bind 127.0.0.1:8765 · lab use only</footer>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const busyStates = new Set(["SWITCHING", "BUSY_TX", "BUSY_RX", "MULTIPLEX_RX"]);

    function paint(data) {{
      const state = data.state || "UNKNOWN";
      $("state").textContent = state;
      $("serial").textContent = data.serial_port || "—";
      $("mayhem").textContent = String(!!data.mayhem_serial);
      $("native").textContent = String(!!data.hackrf_native);
      $("proc").textContent = data.subprocess_running
        ? (data.subprocess_argv || []).join(" ") || "running"
        : "idle";
      $("raw").textContent = JSON.stringify(data, null, 2);
      const dot = $("dot");
      dot.className = "dot";
      if (state === "MAYHEM_SERIAL" || state === "HACKRF_NATIVE") dot.classList.add("ok");
      else if (busyStates.has(state)) dot.classList.add("warn");
      else dot.classList.add("bad");
      const switching = busyStates.has(state);
      $("to-native").disabled = switching;
      $("to-mayhem").disabled = switching;
    }}

    async function refresh() {{
      $("err").textContent = "";
      try {{
        const r = await fetch("/api/hardware/status");
        if (!r.ok) throw new Error(await r.text());
        paint(await r.json());
      }} catch (e) {{
        $("err").textContent = String(e);
      }}
    }}

    async function setMode(mode) {{
      $("err").textContent = "";
      $("to-native").disabled = true;
      $("to-mayhem").disabled = true;
      try {{
        const r = await fetch("/api/hardware/mode", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ mode }}),
        }});
        const body = await r.json().catch(() => ({{ detail: r.statusText }}));
        if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
        paint(body);
      }} catch (e) {{
        $("err").textContent = String(e);
        await refresh();
      }}
    }}

    async function stopJob() {{
      $("err").textContent = "";
      try {{
        const r = await fetch("/api/hardware/stop", {{ method: "POST" }});
        const body = await r.json().catch(() => ({{ detail: r.statusText }}));
        if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
        paint(body);
      }} catch (e) {{
        $("err").textContent = String(e);
      }}
    }}

    async function postJson(url, payload) {{
      $("rf-err").textContent = "";
      const r = await fetch(url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload),
      }});
      const body = await r.json().catch(() => ({{ detail: r.statusText }}));
      $("rf-raw").textContent = JSON.stringify(body, null, 2);
      if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
      await refresh();
      return body;
    }}

    $("jam-cw").onclick = async () => {{
      try {{
        await postJson("/api/tx/jammer", {{
          mode: "CW_AWGN",
          frequency_hz: Number($("freq").value),
          sample_rate: 10000000,
          tx_vga: Number($("txvga").value),
          amp: 0,
          amplitude: 64,
          max_duration_sec: Number($("dur").value),
        }});
      }} catch (e) {{ $("rf-err").textContent = String(e); }}
    }};
    $("jam-chirp").onclick = async () => {{
      try {{
        await postJson("/api/tx/jammer", {{
          mode: "CHIRP_BARRAGE",
          frequency_hz: Number($("freq").value),
          sample_rate: 10000000,
          tx_vga: Number($("txvga").value),
          amp: 0,
          bandwidth_hz: 2000000,
          sweep_period_s: 0.001,
          max_duration_sec: Number($("dur").value),
        }});
      }} catch (e) {{ $("rf-err").textContent = String(e); }}
    }};
    $("cap").onclick = async () => {{
      try {{
        await postJson("/api/rf/capture", {{
          frequency_hz: Number($("freq").value),
          sample_rate: 10000000,
          lna: 16,
          vga: 20,
          amp: 0,
          num_samples: 2000000,
          wait: true,
        }});
      }} catch (e) {{ $("rf-err").textContent = String(e); }}
    }};

    let screenWs = null;
    const canvas = $("mayhem-canvas");
    const ctx = canvas.getContext("2d");

    function paintFrame(buf) {{
      const view = new DataView(buf);
      if (view.byteLength < 8) return;
      const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
      if (magic !== "MHSC") return;
      const w = view.getUint16(4);
      const h = view.getUint16(6);
      const rgba = new Uint8ClampedArray(buf, 8);
      if (rgba.length < w * h * 4) return;
      if (canvas.width !== w || canvas.height !== h) {{
        canvas.width = w; canvas.height = h;
      }}
      ctx.putImageData(new ImageData(rgba, w, h), 0, 0);
    }}

    $("screen-start").onclick = () => {{
      $("mayhem-err").textContent = "";
      if (screenWs) screenWs.close();
      const proto = location.protocol === "https:" ? "wss" : "ws";
      screenWs = new WebSocket(`${{proto}}://${{location.host}}/ws/mayhem-screen`);
      screenWs.binaryType = "arraybuffer";
      screenWs.onmessage = (ev) => {{
        if (typeof ev.data === "string") {{
          try {{
            const msg = JSON.parse(ev.data);
            if (msg.type === "error") $("mayhem-err").textContent = msg.message || "error";
          }} catch (_) {{}}
          return;
        }}
        paintFrame(ev.data);
      }};
      screenWs.onerror = () => {{ $("mayhem-err").textContent = "screen websocket error"; }};
    }};
    $("screen-stop").onclick = () => {{
      if (screenWs) {{ screenWs.send("stop"); screenWs.close(); screenWs = null; }}
    }};

    document.querySelectorAll("[data-btn]").forEach((btn) => {{
      btn.onclick = async () => {{
        try {{
          const r = await fetch("/api/mayhem/input", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ kind: "button", button: Number(btn.dataset.btn) }}),
          }});
          const body = await r.json();
          if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
        }} catch (e) {{ $("mayhem-err").textContent = String(e); }}
      }};
    }});

    canvas.onclick = async (ev) => {{
      const rect = canvas.getBoundingClientRect();
      const x = Math.floor((ev.clientX - rect.left) * (canvas.width / rect.width));
      const y = Math.floor((ev.clientY - rect.top) * (canvas.height / rect.height));
      try {{
        const r = await fetch("/api/mayhem/input", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ kind: "touch", x, y }}),
        }});
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
      }} catch (e) {{ $("mayhem-err").textContent = String(e); }}
    }};

    $("kb-send").onclick = async () => {{
      try {{
        const r = await fetch("/api/mayhem/input", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ kind: "keyboard", text: $("kb-text").value }}),
        }});
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
      }} catch (e) {{ $("mayhem-err").textContent = String(e); }}
    }};

    async function loadApps() {{
      try {{
        const r = await fetch("/api/mayhem/apps");
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
        const box = $("featured-apps");
        box.innerHTML = "";
        (body.featured || []).forEach((id) => {{
          const b = document.createElement("button");
          b.type = "button";
          b.textContent = id;
          b.onclick = async () => {{
            try {{
              const rr = await fetch("/api/mayhem/app", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ app_id: id }}),
              }});
              const jj = await rr.json();
              if (!rr.ok) throw new Error(jj.detail || JSON.stringify(jj));
            }} catch (e) {{ $("mayhem-err").textContent = String(e); }}
          }};
          box.appendChild(b);
        }});
      }} catch (e) {{ $("mayhem-err").textContent = String(e); }}
    }}
    $("apps-refresh").onclick = loadApps;

    async function sg(url, payload) {{
      $("sg-err").textContent = "";
      const r = await fetch(url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: payload ? JSON.stringify(payload) : undefined,
      }});
      const body = await r.json().catch(() => ({{ detail: r.statusText }}));
      $("sg-raw").textContent = JSON.stringify(body, null, 2);
      if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
      return body;
    }}
    $("sg-sniff").onclick = async () => {{
      try {{
        await sg("/api/subghz/sniff", {{ frequency_hz: 433920000, sample_rate: 2000000, max_duration_sec: 30 }});
      }} catch (e) {{ $("sg-err").textContent = String(e); }}
    }};
    $("sg-sniff-stop").onclick = async () => {{
      try {{ await sg("/api/subghz/sniff/stop"); }} catch (e) {{ $("sg-err").textContent = String(e); }}
    }};
    $("sg-packets").onclick = async () => {{
      try {{
        const r = await fetch("/api/subghz/packets?limit=20");
        const body = await r.json();
        $("sg-raw").textContent = JSON.stringify(body, null, 2);
        if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
      }} catch (e) {{ $("sg-err").textContent = String(e); }}
    }};
    $("sg-bf").onclick = async () => {{
      try {{
        await sg("/api/subghz/bruteforce", {{
          mode: "framed", family: "PT2262", nbits: 8, key_start: 0, key_end: 15,
          repeats: 2, frequency_hz: 433920000, sample_rate: 2000000, tx_vga: 20, amp: 0,
          max_duration_sec: 10
        }});
      }} catch (e) {{ $("sg-err").textContent = String(e); }}
    }};
    $("sg-bf-stop").onclick = async () => {{
      try {{ await sg("/api/subghz/bruteforce/stop"); }} catch (e) {{ $("sg-err").textContent = String(e); }}
    }};

    let wfWs = null;
    const wfCanvas = $("wf-canvas");
    const wfCtx = wfCanvas.getContext("2d");
    let wfY = 0;
    async function recon(url, payload) {{
      $("recon-err").textContent = "";
      const r = await fetch(url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: payload ? JSON.stringify(payload) : undefined,
      }});
      const body = await r.json().catch(() => ({{ detail: r.statusText }}));
      $("recon-raw").textContent = JSON.stringify(body, null, 2);
      if (!r.ok) throw new Error(body.detail || JSON.stringify(body));
      return body;
    }}
    function paintWfRow(buf) {{
      const v = new DataView(buf);
      if (String.fromCharCode(v.getUint8(0),v.getUint8(1),v.getUint8(2),v.getUint8(3)) !== "WF32") return;
      const n = v.getUint32(4);
      const row = new Uint8Array(buf, 36, n);
      if (wfCanvas.width !== n) wfCanvas.width = n;
      const img = wfCtx.createImageData(n, 1);
      for (let i = 0; i < n; i++) {{
        const t = row[i];
        img.data[i*4] = t; img.data[i*4+1] = Math.max(0, t-40); img.data[i*4+2] = 40; img.data[i*4+3] = 255;
      }}
      wfCtx.putImageData(img, 0, wfY % wfCanvas.height);
      wfY++;
    }}
    $("wf-start").onclick = async () => {{
      try {{
        await recon("/api/recon/sweep", {{
          start_mhz: 400, stop_mhz: 450, bin_width_hz: 100000, lna: 16, vga: 20, amp: 0,
          max_duration_sec: 60, target_fps: 30,
          trigger_enabled: true, trigger_f_lo_hz: 433000000, trigger_f_hi_hz: 434000000,
          trigger_rssi_db: -35, trigger_action: "log"
        }});
        if (wfWs) wfWs.close();
        const proto = location.protocol === "https:" ? "wss" : "ws";
        wfWs = new WebSocket(`${{proto}}://${{location.host}}/ws/waterfall`);
        wfWs.binaryType = "arraybuffer";
        wfWs.onopen = () => wfWs.send("attach");
        wfWs.onmessage = (ev) => {{ if (typeof ev.data !== "string") paintWfRow(ev.data); }};
      }} catch (e) {{ $("recon-err").textContent = String(e); }}
    }};
    $("wf-stop").onclick = async () => {{
      try {{ await recon("/api/recon/sweep/stop"); if (wfWs) wfWs.close(); }}
      catch (e) {{ $("recon-err").textContent = String(e); }}
    }};
    $("dec-pocsag").onclick = async () => {{
      try {{ await recon("/api/recon/decoder", {{ kind: "pocsag", frequency_hz: 466075000 }}); }}
      catch (e) {{ $("recon-err").textContent = String(e); }}
    }};
    $("dec-adsb").onclick = async () => {{
      try {{ await recon("/api/recon/decoder", {{ kind: "adsb", frequency_hz: 1090000000 }}); }}
      catch (e) {{ $("recon-err").textContent = String(e); }}
    }};
    $("synth-pager").onclick = async () => {{
      try {{ await recon("/api/tx/synthesize", {{ kind: "lrs_pager", pager_id: 7, frequency_hz: 433920000, tx_vga: 15, max_duration_sec: 5 }}); }}
      catch (e) {{ $("recon-err").textContent = String(e); }}
    }};
    $("synth-fm").onclick = async () => {{
      try {{ await recon("/api/tx/synthesize", {{ kind: "fm_rds", fm_frequency_hz: 100000000, ps: "LAB-FM", rt: "PP-LAB", tx_vga: 10, max_duration_sec: 5 }}); }}
      catch (e) {{ $("recon-err").textContent = String(e); }}
    }};

    $("refresh").onclick = refresh;
    $("to-native").onclick = () => setMode("HACKRF_NATIVE");
    $("to-mayhem").onclick = () => setMode("MAYHEM_SERIAL");
    $("stop").onclick = stopJob;
    refresh();
    setInterval(refresh, 4000);
  </script>
</body>
</html>
"""

_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#0e1116"/>
  <path d="M6 22 L16 8 L26 22" fill="none" stroke="#58a6ff" stroke-width="2.5"/>
  <circle cx="16" cy="22" r="2.2" fill="#3fb950"/>
</svg>
"""


@router.get("/", response_model=None)
async def home() -> FileResponse | HTMLResponse:
    """Prefer the React Cyber-Deck build; fall back to Phase HTML panels."""
    if frontend_dist_ready():
        return spa_index()
    return HTMLResponse(_HOME_HTML)


@router.get("/legacy", response_class=HTMLResponse)
async def legacy_home() -> HTMLResponse:
    return HTMLResponse(_HOME_HTML)


@router.get("/favicon.svg")
async def favicon() -> Response:
    dist_fav = FRONTEND_DIST / "favicon.svg"
    if dist_fav.is_file():
        return FileResponse(dist_fav)
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


@router.get("/favicon.ico")
async def favicon_ico() -> Response:
    # Browsers probe .ico; point them at the SVG without a binary asset.
    return Response(status_code=204)
