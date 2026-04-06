import React, { useState, useEffect, useRef, useMemo } from "react";

// ─── КОНФИГУРАЦИЯ ─────────────────────────────────────────────────────────────
const API_BASE = "/api";

const STORE_COLORS = {
  "iPrice.Store":"#a78bfa","МОБИЛАКС":"#c084fc",
  "REM-GSM":"#60a5fa","ДИСКИ":"#f97316","ТЕХНО":"#f472b6","Склад":"#94a3b8",
};
const STORE_GRADIENTS = {
  "iPrice.Store":"linear-gradient(135deg,#a78bfa,#7c3aed)",
  "МОБИЛАКС":"linear-gradient(135deg,#c084fc,#9333ea)",
  "REM-GSM":"linear-gradient(135deg,#60a5fa,#2563eb)",
  "ДИСКИ":"linear-gradient(135deg,#fb923c,#ea580c)",
  "ТЕХНО":"linear-gradient(135deg,#f472b6,#db2777)",
  "Склад":"linear-gradient(135deg,#94a3b8,#475569)",
};
const STORES = ["iPrice.Store","МОБИЛАКС","REM-GSM","ДИСКИ","ТЕХНО","Склад"];

// ─── СЕССИЯ ───────────────────────────────────────────────────────────────────
const Session = {
  get:   () => JSON.parse(sessionStorage.getItem("pb") || "null"),
  set:   (v) => sessionStorage.setItem("pb", JSON.stringify(v)),
  clear: () => sessionStorage.removeItem("pb"),
};

function _detailFromApi(errBody) {
  const d = errBody?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
  return errBody?.message || "Ошибка запроса";
}

async function apiFetch(path, { token, json, body, ...opts } = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  }
  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers, body: body ?? opts.body });
  const data = res.headers.get("content-type")?.includes("application/json")
    ? await res.json().catch(() => ({}))
    : await res.text();
  if (!res.ok) {
    const err = new Error(typeof data === "object" ? _detailFromApi(data) : data || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

// ─── МАТРИЦА ПРАВ ─────────────────────────────────────────────────────────────
const Access = {
  isInfo: (u) => u?.role === "info",
  seesAllStores: (u) => ["admin", "staff", "info"].includes(u?.role),
  /** Редактирование и чекбокс Авито: не «Инфо»; staff — только свой магазин; admin — все */
  canEdit: (u, p) =>
    u.role !== "info" && (u.role === "admin" || p.store_name === u.store_name),
  /** Учётная цена и прибыль в каталоге: не «Инфо»; staff — только свой магазин; admin — все */
  canSeeCost: (u, p) =>
    u.role === "info"
      ? false
      : u.role === "admin" || p.store_name === u.store_name,
  /** Карточка товара (модель / кнопка): admin и info — любой магазин; staff — только свой */
  canOpenProductCard: (u, p) =>
    u.role === "admin" ||
    u.role === "info" ||
    (u.role === "staff" && p.store_name === u.store_name),
  canSeePD: (u) => u.role === "admin",
  isAdmin: (u) => u.role === "admin",
  canManagePurchaseDocs: (u, p) =>
    u.role === "admin" || (u.role === "staff" && p.store_name === u.store_name),
};

// ─── MOCK ДАННЫЕ ──────────────────────────────────────────────────────────────
// ─── CSS ──────────────────────────────────────────────────────────────────────
const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0c0c0e;--bg2:#141416;--bg3:#1c1c20;--bg4:#26262b;
  --border:#2a2a30;--border2:#3a3a42;
  --text:#ececf0;--muted:#9494a6;
  --accent:#10b981;--accent2:#34d399;--accent-glow:rgba(16,185,129,.2);
  --cyan:#22d3ee;--cyan-dim:rgba(34,211,238,.1);
  --success:#34d399;--success-dim:rgba(52,211,153,.1);
  --warn:#f59e0b;--warn-dim:rgba(245,158,11,.1);
  --danger:#ef4444;--danger-dim:rgba(239,68,68,.1);
  --gradient:linear-gradient(135deg,#10b981,#34d399);
  --gradient2:linear-gradient(135deg,#06b6d4,#10b981);
  --mono:'JetBrains Mono',monospace;--sans:'Inter',system-ui,sans-serif;--r:8px;
  --shadow:0 1px 3px rgba(0,0,0,.4),0 0 0 1px rgba(255,255,255,.04);
  --shadow-lg:0 8px 32px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.06);
}
html{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;min-height:100vh;line-height:1.5}
::selection{background:rgba(16,185,129,.3);color:#fff}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--muted)}

@keyframes fadeUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes shimmer{0%{background-position:-200% 0}100%{background-position:200% 0}}
@keyframes glow{0%,100%{box-shadow:0 0 12px rgba(16,185,129,.2)}50%{box-shadow:0 0 24px rgba(16,185,129,.4)}}
@keyframes priceGlow{0%,100%{text-shadow:0 0 6px rgba(239,68,68,.2)}50%{text-shadow:0 0 14px rgba(239,68,68,.5)}}
.price-danger{animation:priceGlow 2s ease-in-out infinite}
@keyframes toastIn{from{opacity:0;transform:translate(-50%,10px)}to{opacity:1;transform:translate(-50%,0)}}
@keyframes toastOut{from{opacity:1;transform:translate(-50%,0)}to{opacity:0;transform:translate(-50%,-10px)}}
.copy-toast{position:fixed;bottom:32px;left:50%;transform:translateX(-50%);background:var(--bg3);color:var(--accent2);border:1px solid rgba(16,185,129,.3);border-radius:8px;padding:8px 18px;font-size:12px;font-weight:500;z-index:9999;pointer-events:none;animation:toastIn .25s ease,toastOut .3s ease 2.7s forwards;box-shadow:0 4px 20px rgba(0,0,0,.4)}
.spinner{width:16px;height:16px;border:2px solid rgba(255,255,255,.15);border-top-color:var(--accent2);border-radius:50%;animation:spin .6s linear infinite;display:inline-block;vertical-align:middle}

/* LOGIN */
.lw{min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);background-image:radial-gradient(ellipse at 30% 50%,rgba(16,185,129,.06) 0%,transparent 60%)}
.lb{width:400px;background:rgba(20,20,22,.85);backdrop-filter:blur(12px);border:1px solid var(--border);border-radius:12px;padding:40px 36px;box-shadow:var(--shadow-lg);animation:fadeUp .4s ease}
.logo{display:flex;align-items:center;gap:14px;margin-bottom:30px}
.logo-icon{width:52px;height:52px;background:linear-gradient(145deg,rgba(16,185,129,.14),rgba(6,182,212,.06));border:1px solid rgba(16,185,129,.35);border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:26px;color:var(--accent2);box-shadow:0 6px 24px rgba(0,0,0,.25),0 0 0 1px rgba(255,255,255,.04) inset,0 1px 0 rgba(255,255,255,.07) inset}
.logo-text{font-family:var(--sans);font-size:26px;font-weight:500;letter-spacing:-.6px;color:var(--text);text-shadow:0 0 24px rgba(236,236,240,.12);display:flex;align-items:baseline;gap:3px;line-height:1.05;flex-wrap:nowrap}
.logo-brand-base{font-weight:600;letter-spacing:-.04em;color:rgba(244,244,245,.96)}
.logo-brand-stock{font-weight:800;letter-spacing:-.03em;background:linear-gradient(118deg,#10b981 0%,#34d399 38%,#2dd4bf 62%,#06b6d4 100%);background-size:220% 220%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;animation:logoShift 5.5s ease-in-out infinite;filter:drop-shadow(0 0 12px rgba(16,185,129,.38))}
@keyframes logoShift{0%,100%{background-position:0% 40%}50%{background-position:100% 60%}}
.ltitle{font-size:22px;font-weight:700;margin-bottom:6px;letter-spacing:-.3px}
.lsub{color:var(--muted);font-size:13px;margin-bottom:26px}
.field{margin-bottom:16px}
.field label{display:block;font-size:11px;font-weight:600;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.6px}
.field input,.field select{width:100%;padding:10px 13px;background:var(--bg3);border:1px solid var(--border);border-radius:var(--r);color:var(--text);font-family:var(--sans);font-size:13px;outline:none;transition:all .2s}
.field input:focus,.field select:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow)}
.field input::placeholder{color:var(--muted)}
.btn{padding:10px 18px;border-radius:var(--r);font-size:13px;font-weight:600;cursor:pointer;border:none;transition:all .2s;display:inline-flex;align-items:center;gap:7px;font-family:var(--sans);letter-spacing:.01em}
.btn-full{width:100%;justify-content:center}
.btn-primary{background:var(--gradient);color:#fff;box-shadow:0 2px 12px rgba(16,185,129,.3)}
.btn-primary:hover:not(:disabled){box-shadow:0 4px 20px rgba(16,185,129,.45);transform:translateY(-1px)}
.btn-primary:active:not(:disabled){transform:translateY(0)}
.btn-primary:disabled{opacity:.45;cursor:default;transform:none}
.btn-outline{background:transparent;border:1px solid var(--border);color:var(--text)}
.btn-outline:hover{border-color:var(--accent);color:var(--accent2);background:rgba(16,185,129,.06)}
.btn-sm{padding:6px 12px;font-size:12px;border-radius:8px}
.btn-photo{background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);color:var(--accent2)}
.btn-photo:hover{background:rgba(16,185,129,.18);border-color:var(--accent)}
.btn-doc{background:var(--success-dim);border:1px solid rgba(52,211,153,.3);color:var(--success)}
.btn-doc:hover{background:rgba(52,211,153,.2)}
.btn-ghost{background:none;border:none;color:var(--muted);cursor:pointer;padding:6px;border-radius:6px;transition:all .15s;font-size:14px}
.btn-ghost:hover{color:var(--danger);background:var(--danger-dim)}
.err{background:var(--danger-dim);border:1px solid rgba(239,68,68,.25);border-radius:var(--r);padding:10px 13px;font-size:13px;color:#fca5a5;margin-bottom:14px;animation:fadeIn .2s}

/* SHELL */
.shell{display:flex;height:100vh;overflow:hidden;position:relative}
.shell::after{content:"";position:absolute;top:56px;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(16,185,129,.4),transparent);z-index:10;pointer-events:none}
.sidebar{width:250px;flex-shrink:0;background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;transition:width .25s cubic-bezier(.4,0,.2,1)}
.sidebar.collapsed{width:64px}
.sidebar.collapsed .logo-text,.sidebar.collapsed .sb-info,.sidebar.collapsed .sb-store-sw{display:none}
.sidebar.collapsed .sb-logo{justify-content:center;padding:14px 10px 12px}
.sidebar.collapsed .nav-item{justify-content:center;padding:13px 0;margin:3px 8px;width:calc(100% - 16px)}
.sidebar.collapsed .nav-item .nav-label{display:none}
.sidebar.collapsed .nav-item .nav-icon{font-size:18px}
.sidebar.collapsed .sb-user{justify-content:center;padding:14px 8px}
.sidebar.collapsed .av{width:38px;height:38px}
.sb-collapse-btn{display:none;background:none;border:none;color:var(--muted);cursor:pointer;padding:2px 6px;border-radius:6px;font-size:12px;transition:all .2s;line-height:1}
.sb-collapse-btn:hover{background:rgba(16,185,129,.1);color:var(--text)}
@media(min-width:769px){.sb-collapse-btn{display:flex;align-items:center;justify-content:center}}
.sidebar.collapsed .sb-sec{justify-content:center}
.sb-logo{padding:14px 18px 12px;border-bottom:none;display:flex;align-items:center;gap:12px}
.sb-nav{flex:1;overflow-y:auto;padding:2px 0}
.sb-sec{padding:4px 14px 4px;font-size:10px;font-weight:700;color:rgba(16,185,129,.7);text-transform:uppercase;letter-spacing:1.4px;display:flex;align-items:center;justify-content:space-between}
.nav-divider{height:1px;margin:6px 18px;background:linear-gradient(90deg,transparent,rgba(16,185,129,.25),transparent)}
.nav-item{display:flex;align-items:center;gap:10px;padding:13px 18px;margin:3px 10px;border-radius:10px;color:rgba(236,236,240,.7);font-size:14px;font-weight:500;cursor:pointer;border:none;background:none;width:calc(100% - 20px);text-align:left;transition:all .2s;letter-spacing:.1px}
.nav-icon{font-size:15px;flex-shrink:0;width:20px;text-align:center}
.nav-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav-item:hover{background:rgba(16,185,129,.08);color:#e2e8f0}
.nav-item.active{background:linear-gradient(135deg,rgba(16,185,129,.22) 0%,rgba(16,185,129,.1) 100%);color:#6ee7b7;font-weight:700;box-shadow:inset 3px 0 0 #10b981;border:1px solid rgba(16,185,129,.2)}
.sb-store-sw{padding:12px 14px;border-top:1px solid rgba(16,185,129,.1)}
.store-sel{width:100%;padding:9px 11px;background:rgba(20,20,22,.6);border:1px solid rgba(16,185,129,.2);border-radius:8px;color:var(--text);font-family:var(--sans);font-size:12px;cursor:pointer;outline:none;transition:all .2s}
.store-sel:focus{border-color:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.15)}
.sb-user{padding:14px 14px;padding-bottom:calc(14px + env(safe-area-inset-bottom,0px));border-top:1px solid rgba(16,185,129,.1);display:flex;align-items:center;gap:10px}
.av{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0;transition:transform .15s}
.av:hover{transform:scale(1.08)}
.av-admin{background:rgba(16,185,129,.18);border:1px solid rgba(16,185,129,.35);color:var(--accent2)}
.av-staff{background:var(--success-dim);border:1px solid rgba(52,211,153,.3);color:var(--success)}
.sb-info{flex:1;min-width:0}
.sb-name{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sb-role{font-size:10px;color:var(--muted);margin-top:1px}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden;background:var(--bg)}
.topbar{padding:0 20px;border-bottom:1px solid var(--border);display:flex;align-items:flex-end;gap:10px;height:56px;flex-shrink:0;background:var(--bg2);padding-bottom:10px}
.topbar-title{font-size:18px;font-weight:500;letter-spacing:-.2px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;text-align:center;color:var(--text);text-shadow:0 0 20px rgba(236,236,240,.15)}
.topbar-store-sel{font-size:10px;padding:4px 8px;border-radius:20px;font-family:var(--mono);white-space:nowrap;font-weight:500;background:var(--bg3);color:var(--muted);border:1px solid var(--border);cursor:pointer;outline:none;max-width:150px}
.badge{font-size:10px;padding:4px 10px;border-radius:20px;font-family:var(--mono);white-space:nowrap;font-weight:500}
.b-store{background:var(--bg3);color:var(--muted);border:1px solid var(--border)}
.b-admin{background:rgba(16,185,129,.12);color:var(--accent2);border:1px solid rgba(16,185,129,.3)}
.b-staff{background:var(--success-dim);color:var(--success);border:1px solid rgba(52,211,153,.3)}
.content{flex:1;overflow-y:auto;padding:16px 20px}

/* BANNER */
.banner{padding:10px 14px;border-radius:var(--r);font-size:12px;margin-bottom:14px;display:flex;align-items:flex-start;gap:8px;border:1px solid;line-height:1.6;animation:fadeIn .3s}
.ban-admin{background:rgba(16,185,129,.07);border-color:rgba(16,185,129,.25);color:#6ee7b7}
.ban-staff{background:var(--success-dim);border-color:rgba(52,211,153,.25);color:#6ee7b7}
.ban-ro{background:rgba(119,119,138,.07);border-color:rgba(119,119,138,.2);color:var(--muted)}
.an-h{font-size:11px;font-weight:700;color:var(--text);margin-bottom:7px;text-transform:uppercase;letter-spacing:.5px}

/* STATS */
.stats-bar{display:flex;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.sc{background:rgba(20,20,22,.7);backdrop-filter:blur(8px);border:1px solid var(--border);border-radius:12px;padding:12px 16px;flex:1;min-width:110px;transition:all .2s;position:relative;overflow:hidden}
.sc:hover{border-color:var(--border2);transform:translateY(-1px)}
.sc::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--gradient);opacity:0;transition:opacity .2s}
.sc:hover::before{opacity:1}
.sc-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;font-weight:600;margin-bottom:4px}
.sc-val{font-family:var(--mono);font-size:18px;font-weight:600}

/* FILTERS */
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.fi{flex:1;min-width:160px;padding:9px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);color:var(--text);font-family:var(--sans);font-size:13px;outline:none;transition:all .2s}
.fi:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow)}
.fi::placeholder{color:var(--muted)}
.fs{padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);color:var(--text);font-size:12px;cursor:pointer;outline:none;transition:all .2s}
.fs:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow)}
.fc{font-size:11px;color:var(--accent2);font-family:var(--mono);font-weight:500}

/* LEGEND */
.legend{display:flex;gap:14px;margin-bottom:12px;flex-wrap:wrap}
.leg-item{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}
.leg-dot{width:10px;height:10px;border-radius:3px;flex-shrink:0}

/* TABLE */
.tw{background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:hidden;overflow:clip;box-shadow:var(--shadow)}
.pt{width:100%;border-collapse:collapse;border-spacing:0}
.pt th,.pt td{border-left:none;border-right:none}
.pt th{padding:10px 12px;text-align:left;font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.9px;background:var(--bg4);border-bottom:2px solid var(--border2);white-space:nowrap;position:sticky;top:0;z-index:2;box-shadow:0 2px 6px rgba(0,0,0,.45)}
.pt th.thl{color:var(--border2)}
.pt td{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.07);font-size:13px;vertical-align:middle;transition:background .12s}
.pt-thumb-cell{width:52px;padding:6px 8px!important;vertical-align:middle}
.pt-thumb{width:42px;height:42px;object-fit:cover;border-radius:8px;border:1px solid var(--border);display:block;background:var(--bg3);transition:transform .15s}
.pt-thumb:hover{transform:scale(1.1)}
.toggle-sw{position:relative;width:34px;height:18px;display:inline-block;cursor:pointer;flex-shrink:0}
.toggle-sw input{opacity:0;width:0;height:0;position:absolute}
.toggle-sw .sw-track{position:absolute;inset:0;background:var(--bg4);border-radius:9px;transition:background .25s ease}
.toggle-sw input:checked+.sw-track{background:var(--accent)}
.toggle-sw .sw-track::after{content:"";position:absolute;top:2px;left:2px;width:14px;height:14px;background:#fff;border-radius:50%;transition:transform .25s ease;box-shadow:0 1px 3px rgba(0,0,0,.3)}
.toggle-sw input:checked+.sw-track::after{transform:translateX(16px)}
.toggle-sw input:disabled+.sw-track{opacity:.35;cursor:not-allowed}
.avito-cb{display:inline-flex;align-items:center;justify-content:center;cursor:pointer;accent-color:var(--accent)}
.avito-cb input:disabled{cursor:not-allowed;opacity:.5}
.pt tr:last-child td{border-bottom:none}
.pt tr:hover td{background:rgba(16,185,129,.04)}
.pt tr.own td{border-left:1px solid rgba(52,211,153,.5)}
.pt tr.rep td{background:var(--danger-dim)}
.tm{font-weight:600;cursor:pointer;color:var(--accent2);transition:color .15s}
.imei-btn{display:inline-block;padding:6px 14px;border-radius:8px;border:1px solid rgba(16,185,129,.25);background:rgba(16,185,129,.08);color:var(--accent2);font-family:var(--mono);font-size:11px;font-weight:600;cursor:pointer;transition:all .2s ease;box-shadow:0 1px 3px rgba(0,0,0,.2)}
.imei-btn:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(16,185,129,.3);background:rgba(16,185,129,.15);border-color:var(--accent)}
.imei-btn:active{transform:translateY(0);box-shadow:0 1px 3px rgba(0,0,0,.2)}
.tm:hover{color:var(--cyan);text-decoration:none}
.tm.tm-disabled{cursor:default;color:var(--text);opacity:0.85}
.tm.tm-disabled:hover{text-decoration:none;color:var(--text)}
.ts{font-family:var(--mono);font-size:10px;color:var(--muted);margin-top:3px}
.mono{font-family:var(--mono);font-size:11px;color:var(--muted)}
.tr{text-align:right;font-family:var(--mono);font-size:12px;font-weight:600;white-space:nowrap}
.trm{text-align:right;font-family:var(--mono);font-size:11px;color:var(--muted);white-space:nowrap}
.tlk{text-align:right;color:var(--border2);font-size:14px;cursor:default}
.pp{color:var(--success)}.pn{color:var(--danger)}.pm{color:var(--muted)}
.sdot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;flex-shrink:0;box-shadow:0 0 6px currentColor}
.chip{display:inline-block;padding:3px 9px;border-radius:6px;font-size:10px;font-weight:600;white-space:nowrap;letter-spacing:.02em}
.ce{background:var(--success-dim);color:#6ee7b7}
.cg{background:rgba(16,185,129,.12);color:#6ee7b7}
.cf{background:var(--warn-dim);color:#fcd34d}
.cb{background:var(--danger-dim);color:#fca5a5}
.cr{background:var(--danger-dim);color:var(--danger);border:1px solid rgba(239,68,68,.3)}
.cs{background:var(--bg3);color:var(--muted);border:1px solid var(--border)}
.act{padding:4px 10px;border-radius:6px;border:1px solid var(--border);background:none;color:var(--muted);cursor:pointer;font-size:11px;font-weight:500;transition:all .15s}
.act:hover{border-color:var(--accent);color:var(--accent2);background:rgba(16,185,129,.08)}
.act.lk{opacity:.25;cursor:not-allowed}
.act.lk:hover{border-color:var(--border);color:var(--muted);background:none}
.mc{font-size:13px;font-family:var(--mono);color:var(--muted);white-space:nowrap}

/* PAGER */
.pager{display:flex;align-items:center;justify-content:space-between;padding:13px 0 0}
.pi{font-size:11px;color:var(--muted);font-family:var(--mono)}
.pb-btn{padding:6px 14px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:12px;font-weight:500;cursor:pointer;transition:all .15s}
.pb-btn:hover:not(:disabled){border-color:var(--accent);color:var(--accent2);background:rgba(16,185,129,.06)}
.pb-btn:disabled{opacity:.35;cursor:default}

/* CARD */
.cg2{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.panel{background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px;box-shadow:var(--shadow);transition:border-color .2s}
.panel:hover{border-color:var(--border2)}
.ph{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;background:var(--bg3)}
.pt2{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;flex:1}
.pb2{padding:16px}
.ir{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(42,42,48,.5)}
.ir:last-child{border-bottom:none}
.ik{font-size:12px;color:var(--muted)}
.iv{font-size:12px;font-weight:600;font-family:var(--mono)}

/* PHOTOS */
.pgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}
.pthumb{aspect-ratio:1;background:var(--bg3);border:1px solid var(--border);border-radius:10px;display:flex;align-items:center;justify-content:center;cursor:pointer;position:relative;overflow:hidden;transition:all .2s;font-size:28px}
.pthumb:hover{border-color:var(--accent);transform:scale(1.03);box-shadow:0 4px 16px rgba(16,185,129,.2)}
.pthumb.empty{border-style:dashed;cursor:default;opacity:.2}
.pthumb.empty:hover{border-color:var(--border);transform:none;box-shadow:none}
.pdel{position:absolute;top:4px;right:4px;background:rgba(10,14,26,.85);border:none;border-radius:6px;color:var(--danger);cursor:pointer;font-size:11px;padding:3px 6px;display:none;line-height:1;backdrop-filter:blur(4px)}
.pthumb:hover .pdel{display:block}
.pmb{position:absolute;bottom:4px;left:4px;background:var(--gradient);color:#fff;font-size:9px;font-weight:700;padding:3px 7px;border-radius:4px;pointer-events:none}

/* DOCS */
.dlist{display:flex;flex-direction:column;gap:6px;margin-bottom:10px}
.di{display:flex;align-items:center;gap:10px;padding:10px 12px;background:var(--bg3);border:1px solid var(--border);border-radius:8px;transition:border-color .15s}
.di:hover{border-color:var(--border2)}
.dic{flex:1;min-width:0}
.dn{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dm{font-size:10px;color:var(--muted);margin-top:2px}
.dtb{font-size:10px;padding:3px 8px;border-radius:4px;font-weight:600;white-space:nowrap}
.dt-r{background:var(--success-dim);color:#6ee7b7}
.dt-c{background:rgba(16,185,129,.12);color:#6ee7b7}
.dt-p{background:var(--danger-dim);color:#fca5a5}
.dt-o{background:var(--bg4);color:var(--muted)}
.da{display:flex;gap:4px;flex-shrink:0}
.db{background:none;border:1px solid var(--border);border-radius:6px;color:var(--muted);cursor:pointer;font-size:11px;padding:4px 9px;transition:all .15s}
.db:hover{border-color:var(--accent);color:var(--accent2)}
.db.d:hover{border-color:var(--danger);color:var(--danger)}

/* UPLOAD ZONE */
.uz{border:2px dashed var(--border2);border-radius:12px;padding:22px 14px;text-align:center;cursor:pointer;transition:all .2s;background:rgba(16,185,129,.02)}
.uz:hover,.uz.drag{border-color:var(--accent);background:rgba(16,185,129,.06)}
.uz-ico{font-size:26px;margin-bottom:8px}
.uz-txt{font-size:12px;color:var(--muted);line-height:1.6}
.uz-txt b{color:var(--accent2)}
.uz-h{font-size:10px;color:var(--muted);margin-top:4px}
.fc2{font-size:12px;color:var(--success);margin-top:6px;font-family:var(--mono);font-weight:500}
.prog{margin-top:10px}
.pl{font-size:11px;color:var(--muted);margin-bottom:6px}
.pb3{height:4px;background:var(--bg3);border-radius:2px;overflow:hidden}
.pf{height:100%;background:var(--gradient);border-radius:2px;transition:width .3s ease}

/* MODAL */
.mo{position:fixed;inset:0;background:rgba(0,0,0,.75);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;z-index:200;padding:20px;animation:fadeIn .2s}
.md{background:var(--bg2);border:1px solid var(--border);border-radius:12px;width:440px;max-width:100%;padding:24px;animation:fadeUp .3s ease;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg)}
.mt{font-size:17px;font-weight:700;margin-bottom:5px;letter-spacing:-.2px}
.ms{font-size:12px;color:var(--muted);margin-bottom:20px;line-height:1.6}
.mf{display:flex;gap:8px;margin-top:20px;justify-content:flex-end}
.cb{background:var(--danger-dim);border:1px solid rgba(239,68,68,.2);border-radius:8px;padding:10px 12px;margin-bottom:14px;display:flex;gap:9px;align-items:flex-start}
.cb input[type=checkbox]{margin-top:2px;accent-color:var(--accent);flex-shrink:0}
.cb label{font-size:12px;color:#fca5a5;line-height:1.6;cursor:pointer}
.152note{background:rgba(239,68,68,.05);border:1px solid rgba(239,68,68,.15);border-radius:8px;padding:10px 12px;font-size:11px;color:var(--muted);line-height:1.7;margin-top:12px}
.152note strong{color:#fca5a5}

/* HISTORY */
.hr2{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(42,42,48,.5);font-size:12px}
.hr2:last-child{border-bottom:none}
.ht{color:var(--muted);white-space:nowrap;font-family:var(--mono);font-size:10px;padding-top:2px}
.hx{flex:1;color:var(--text)}
.hw{color:var(--muted);font-size:11px;white-space:nowrap}

/* IMPORT */
.ir2{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px}
.ir2-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(42,42,48,.5);font-size:12px}
.ir2-row:last-child{border-bottom:none}
.ik2{color:var(--muted)}.iv2{font-family:var(--mono);font-weight:600}

/* PLACEHOLDER */

/* CPW */
.cpww{position:fixed;inset:0;z-index:60;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.6);backdrop-filter:blur(8px)}
.cpwb{width:420px;max-height:90vh;overflow-y:auto;background:var(--bg2);border:1px solid var(--border);border-radius:16px;padding:36px;box-shadow:var(--shadow-lg);animation:fadeUp .3s ease}
.cpw-badge{display:inline-flex;align-items:center;gap:7px;background:var(--warn-dim);border:1px solid rgba(251,191,36,.3);border-radius:20px;padding:5px 14px;font-size:12px;font-weight:600;color:var(--warn);margin-bottom:20px}

/* AVITO */
.btn-avito{background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);color:var(--accent2);font-size:12px;font-weight:600}
.btn-avito:hover{background:rgba(16,185,129,.2);border-color:var(--accent)}
.btn-avito-off{background:var(--danger-dim);border:1px solid rgba(239,68,68,.25);color:var(--danger);font-size:12px;font-weight:600}
.btn-avito-off:hover{background:rgba(239,68,68,.18)}
.avito-settings{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:16px}
.avito-settings .field{margin-bottom:12px}

/* SOLD */
.pt tr.sold td{opacity:.75}
.pt tr.sold:hover td{opacity:.9}
.chip-sold{background:rgba(119,119,138,.12);color:var(--muted);border:1px solid var(--border)}
.sold-badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;padding:3px 9px;border-radius:6px;background:rgba(119,119,138,.1);color:var(--muted);border:1px solid var(--border);white-space:nowrap;font-weight:500}
.sold-banner{background:rgba(119,119,138,.06);border:1px solid rgba(119,119,138,.18);border-radius:var(--r);padding:10px 14px;font-size:12px;margin-bottom:14px;display:flex;align-items:center;gap:8px;color:var(--muted);line-height:1.6}

/* MOBILE HAMBURGER */
.hamburger{display:none;background:none;border:none;color:var(--text);font-size:22px;cursor:pointer;padding:4px 6px;line-height:1;flex-shrink:0;border-radius:6px;transition:background .15s}
.hamburger:hover{background:var(--bg3)}
.sidebar-overlay{display:none}

/* RESPONSIVE */
@media(max-width:768px){
  .hamburger{display:flex;align-items:center;justify-content:center}
  .shell{flex-direction:column;height:100vh;height:100dvh}
  .sidebar{position:fixed;top:0;left:-280px;width:270px;height:100vh;height:100dvh;z-index:300;transition:left .3s cubic-bezier(.4,0,.2,1);box-shadow:none;background:var(--bg2)}
  .sidebar.open{left:0;box-shadow:8px 0 32px rgba(0,0,0,.7)}
  .sidebar-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:299;display:none;backdrop-filter:blur(2px)}
  .sidebar-overlay.open{display:block;animation:fadeIn .2s}
  .main{width:100%;height:100vh;height:100dvh}
  .topbar{padding:0 10px;gap:6px;height:52px;align-items:center;padding-bottom:0}
  .topbar-title{font-size:14px;text-align:left}
  .topbar-role-badge{display:none}
  .topbar-store-sel{max-width:110px;font-size:10px}
  .shell::after{top:52px}
  .content{padding:10px 10px;padding-bottom:calc(10px + env(safe-area-inset-bottom,0px))}
  .lb{width:calc(100vw - 32px);max-width:400px;padding:28px 22px}
  .cpwb{width:calc(100vw - 32px);max-width:420px;padding:26px 20px}
  .stats-bar{gap:6px}
  .sc{min-width:calc(50% - 3px);flex:none;padding:8px 10px}
  .sc-val{font-size:15px}
  .sc-label{font-size:9px}
  .filters{gap:6px}
  .fi{min-width:calc(50% - 3px);flex-basis:calc(50% - 3px)}
  .fs{flex:1;min-width:0;font-size:11px}
  .legend{flex-direction:column;gap:5px;font-size:10px}
  .tw{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:8px}
  .pt{min-width:660px}
  .pt th{position:static;top:auto;z-index:auto}
  .pt th,.pt td{padding:6px 8px;font-size:11px}
  .pt-thumb-cell{width:34px;padding:3px 5px!important}
  .pt-thumb{width:30px;height:30px;border-radius:6px}
  .act{padding:3px 7px;font-size:10px}
  .cg2{grid-template-columns:1fr}
  .mo{padding:10px}
  .md{width:calc(100vw - 20px);max-width:440px;padding:16px}
  .banner{font-size:11px;padding:8px 11px}
  .pgrid{grid-template-columns:repeat(3,1fr);gap:6px}
  .pager{flex-wrap:wrap;gap:8px}
  .panel{border-radius:8px}
  .ph{padding:10px 13px}
  .pb2{padding:12px}
  .badge{max-width:80px;overflow:hidden;text-overflow:ellipsis}
}

@media(max-width:400px){
  .topbar-title{font-size:13px}
  .badge{font-size:9px;padding:3px 6px;max-width:60px}
  .sc{min-width:calc(50% - 3px)}
  .sc-val{font-size:14px}
  .sc-label{font-size:9px}
  .pt{min-width:560px}
  .fi{min-width:100%;flex-basis:100%}
  .pgrid{grid-template-columns:repeat(2,1fr)}
}
`;

// ─── SVG ICONS ───────────────────────────────────────────────────────────────
const I={p:{xmlns:"http://www.w3.org/2000/svg",width:18,height:18,viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:1.8,strokeLinecap:"round",strokeLinejoin:"round"}};
const Icon={
  box:()=><svg {...I.p}><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>,
  plus:()=><svg {...I.p}><circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/></svg>,
  check:()=><svg {...I.p}><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/></svg>,
  mega:()=><svg {...I.p}><path d="m3 11 18-5v12L3 13v-2z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/></svg>,
  chart:()=><svg {...I.p}><line x1="12" x2="12" y1="20" y2="10"/><line x1="18" x2="18" y1="20" y2="4"/><line x1="6" x2="6" y1="20" y2="16"/></svg>,
  competitors:()=><svg {...I.p}><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>,
  users:()=><svg {...I.p}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>,
  gear:()=><svg {...I.p}><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>,
  logo:()=><svg {...I.p} width={26} height={26} viewBox="0 0 24 24"><line x1="2.5" y1="20.75" x2="21.5" y2="20.75" strokeWidth={1.65} opacity=".9"/><rect x="3.75" y="11.25" width="5.25" height="9" rx="1.15"/><rect x="9.375" y="7.25" width="5.25" height="13" rx="1.15"/><rect x="15" y="3.25" width="5.25" height="17" rx="1.15"/></svg>,
  logs:()=><svg {...I.p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>,
  camera:()=><svg {...I.p} width={14} height={14}><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>,
  file:()=><svg {...I.p} width={14} height={14}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>,
  clip:()=><svg {...I.p} width={14} height={14}><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>,
  trash:()=><svg {...I.p} width={14} height={14}><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>,
  card:()=><svg {...I.p} width={14} height={14}><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg>,
};

// ─── МЕЛКИЕ КОМПОНЕНТЫ ────────────────────────────────────────────────────────
const fmt = (n) => n != null ? n.toLocaleString("ru") + " ₽" : "—";
const fmtDt = (s) => { if(!s) return "—"; const d=new Date(s); const p=(v)=>String(v).padStart(2,"0"); return `${p(d.getDate())}.${p(d.getMonth()+1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`; };
const ini = (n) => n ? n[0].toUpperCase() : "?";
function copyText(text) {
  navigator.clipboard.writeText(text);
  const el = document.createElement("div");
  el.className = "copy-toast";
  el.textContent = "Скопировано";
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function Chip({ condition, repair, sold }) {
  if (sold)         return <span className="chip chip-sold">Продан</span>;
  if (repair)       return <span className="chip cr">⚠ Ремонт</span>;
  if (!condition)   return <span className="chip cs">—</span>;
  const map = {"Отличное":"ce","Как новый":"ce","Хорошее":"cg","Среднее":"cf","Плохое":"cb"};
  return <span className={`chip ${map[condition]||"cf"}`}>{condition}</span>;
}

// ─── LOGIN ────────────────────────────────────────────────────────────────────
function LogoWordmark({ compact }) {
  return (
    <div className="logo-text" style={compact ? { fontSize: 22 } : undefined}>
      <span className="logo-brand-base">Base</span>
      <span className="logo-brand-stock">Stock</span>
    </div>
  );
}

function LoginScreen({ onLogin }) {
  const [u,setU]=useState(""); const [p,setP]=useState(""); const [loading,setLoading]=useState(false); const [err,setErr]=useState("");
  const submit = async (e) => {
    e?.preventDefault(); if(!u||!p) return setErr("Введите логин и пароль");
    setLoading(true); setErr("");
    try {
      const body = new URLSearchParams();
      body.set("username", u.trim());
      body.set("password", p);
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setLoading(false);
        return setErr(typeof data.detail === "string" ? data.detail : "Неверный логин или пароль");
      }
      onLogin(data.user, data.access_token, data.refresh_token, data.must_change_password);
    } catch {
      setErr("Сеть недоступна. Запустите API и проверьте прокси Vite.");
    }
    setLoading(false);
  };
  return (
    <div className="lw"><div className="lb">
      <div className="logo"><div className="logo-icon"><Icon.logo/></div><LogoWordmark /></div>
      <div className="ltitle">Вход в систему</div>
      <div className="lsub">Учёт и аналитика б/у техники</div>
      {err && <div className="err">{err}</div>}
      <form onSubmit={submit}>
        <div className="field"><label>Логин</label><input autoFocus placeholder="artem" value={u} onChange={e=>setU(e.target.value)}/></div>
        <div className="field"><label>Пароль</label><input type="password" placeholder="••••••••" value={p} onChange={e=>setP(e.target.value)}/></div>
        <button className="btn btn-primary btn-full" disabled={loading}>{loading?<span className="spinner"/>:"Войти"}</button>
      </form>
    </div></div>
  );
}

// ─── CHANGE PASSWORD ──────────────────────────────────────────────────────────
function ChangePasswordScreen({ user, token, onDone }) {
  const [f,setF]=useState({cur:"",p1:"",p2:""}); const [err,setErr]=useState(""); const [loading,setLoading]=useState(false);
  const submit = async (e) => {
    e.preventDefault(); setErr("");
    if (!f.cur) return setErr("Введите текущий пароль");
    if (f.p1.length<8) return setErr("Минимум 8 символов");
    if (f.p1!==f.p2)  return setErr("Пароли не совпадают");
    setLoading(true);
    try {
      await apiFetch("/auth/change-password", { token, method: "POST", json: { current_password: f.cur, new_password: f.p1 } });
      onDone();
    } catch (x) {
      setErr(x.message || "Ошибка");
    }
    setLoading(false);
  };
  return (
    <div className="cpww"><div className="cpwb">
      <div className="cpw-badge">⚠ Временный пароль</div>
      <div className="ltitle" style={{marginBottom:4}}>Смена пароля</div>
      <div className="lsub" style={{marginBottom:20}}>Привет, {user.full_name}! Установите постоянный пароль.</div>
      {err && <div className="err">{err}</div>}
      <form onSubmit={submit}>
        <div className="field"><label>Текущий пароль</label><input type="password" placeholder="temp" value={f.cur} onChange={e=>setF(x=>({...x,cur:e.target.value}))}/></div>
        <div className="field"><label>Новый пароль</label><input type="password" placeholder="Минимум 8 символов" value={f.p1} onChange={e=>setF(x=>({...x,p1:e.target.value}))}/></div>
        <div className="field"><label>Повторите</label><input type="password" value={f.p2} onChange={e=>setF(x=>({...x,p2:e.target.value}))}/></div>
        <button className="btn btn-primary btn-full" disabled={loading}>{loading?<span className="spinner"/>:"Установить пароль"}</button>
      </form>
    </div></div>
  );
}

// ─── ЛИЧНЫЙ КАБИНЕТ (имя + пароль) ─────────────────────────────────────────────
function AccountModal({ user, token, onClose, onSaved }) {
  const [fullName, setFullName] = useState(user.full_name || "");
  const [username, setUsername] = useState(user.username || "");
  const [pwd, setPwd] = useState({ cur: "", p1: "", p2: "" });
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPwd, setSavingPwd] = useState(false);

  useEffect(() => {
    setFullName(user.full_name || "");
    setUsername(user.username || "");
  }, [user.full_name, user.username, user.id]);

  const saveProfile = async (e) => {
    e.preventDefault();
    setErr("");
    setOk("");
    setSavingProfile(true);
    try {
      const json = { full_name: fullName.trim() || null };
      if (username.trim() !== user.username) json.username = username.trim();
      await apiFetch("/auth/me", { token, method: "PATCH", json });
      setOk("Профиль сохранён");
      onSaved();
    } catch (x) {
      setErr(x.message || "Ошибка");
    }
    setSavingProfile(false);
  };

  const savePwd = async (e) => {
    e.preventDefault();
    setErr("");
    setOk("");
    if (!pwd.cur) return setErr("Введите текущий пароль");
    if (pwd.p1.length < 8) return setErr("Новый пароль — минимум 8 символов");
    if (pwd.p1 !== pwd.p2) return setErr("Пароли не совпадают");
    setSavingPwd(true);
    try {
      await apiFetch("/auth/change-password", { token, method: "POST", json: { current_password: pwd.cur, new_password: pwd.p1 } });
      setPwd({ cur: "", p1: "", p2: "" });
      setOk("Пароль изменён");
      onSaved();
    } catch (x) {
      setErr(x.message || "Ошибка");
    }
    setSavingPwd(false);
  };

  return (
    <div className="cpww" style={{ zIndex: 60 }} onClick={onClose} role="presentation">
      <div className="cpwb" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <div className="ltitle" style={{ marginBottom: 14 }}>Личный кабинет</div>
        {err && <div className="err" style={{ marginBottom: 10 }}>{err}</div>}
        {ok && <div style={{ marginBottom: 10, color: "var(--success)", fontSize: 13 }}>{ok}</div>}

        <form onSubmit={saveProfile} style={{ marginBottom: 22 }}>
          <div className="field">
            <label>Логин</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Логин для входа" />
          </div>
          <div className="field">
            <label>Отображаемое имя</label>
            <input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Как к вам обращаться" />
          </div>
          <button type="submit" className="btn btn-primary btn-sm" disabled={savingProfile || savingPwd}>
            {savingProfile ? <span className="spinner" /> : "Сохранить профиль"}
          </button>
        </form>

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 16, marginBottom: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>Смена пароля</div>
        </div>
        <form onSubmit={savePwd}>
          <div className="field">
            <label>Текущий пароль</label>
            <input type="password" autoComplete="current-password" value={pwd.cur} onChange={(e) => setPwd((p) => ({ ...p, cur: e.target.value }))} />
          </div>
          <div className="field">
            <label>Новый пароль</label>
            <input type="password" autoComplete="new-password" value={pwd.p1} onChange={(e) => setPwd((p) => ({ ...p, p1: e.target.value }))} minLength={8} />
          </div>
          <div className="field">
            <label>Повторите новый</label>
            <input type="password" autoComplete="new-password" value={pwd.p2} onChange={(e) => setPwd((p) => ({ ...p, p2: e.target.value }))} />
          </div>
          <button type="submit" className="btn btn-outline btn-sm" disabled={savingProfile || savingPwd}>
            {savingPwd ? <span className="spinner" /> : "Сменить пароль"}
          </button>
        </form>

        <button type="button" className="btn btn-outline" style={{ marginTop: 16, width: "100%" }} onClick={onClose}>
          Закрыть
        </button>
      </div>
    </div>
  );
}

// ─── UPLOAD ZONE КОМПОНЕНТ ────────────────────────────────────────────────────
function UploadZone({ icon, text, hint, onFile, file, uploading, progress }) {
  const [drag,setDrag]=useState(false); const ref=useRef();
  const label = file && (typeof file === "string" ? file : file.name);
  return (
    <>
      <div className={`uz${drag?" drag":""}`}
        onClick={()=>ref.current.click()}
        onDragOver={e=>{e.preventDefault();setDrag(true)}}
        onDragLeave={()=>setDrag(false)}
        onDrop={e=>{e.preventDefault();setDrag(false);onFile(e.dataTransfer.files[0])}}>
        <div className="uz-ico">{label?"✅":icon}</div>
        <div className="uz-txt"><b>{label?"Файл выбран":text}</b></div>
        <div className="uz-h">{hint}</div>
      </div>
      <input ref={ref} type="file" style={{display:"none"}} onChange={e=>onFile(e.target.files[0])}/>
      {label && <div className="fc2">✓ {label}</div>}
      {uploading && <div className="prog"><div className="pl">Загрузка{progress<100?"...":""}</div><div className="pb3"><div className="pf" style={{width:progress+"%"}}/></div></div>}
    </>
  );
}

// ─── PHOTO MODAL ──────────────────────────────────────────────────────────────
function PhotoModal({ product, productId, token, onClose, onDone }) {
  const [file,setFile]=useState(null); const [uploading,setUploading]=useState(false); const [progress,setProgress]=useState(0); const [err,setErr]=useState("");
  const upload = async () => {
    if (!file) return; setUploading(true); setErr(""); setProgress(10);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/photos/product/${productId}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка загрузки");
      setProgress(100);
      onDone(data);
      onClose();
    } catch (x) {
      setErr(x.message || "Ошибка");
    }
    setUploading(false);
  };
  return (
    <div className="mo" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="md">
        <div className="mt">📷 Добавить фотографию</div>
        <div className="ms">К товару: <strong>{product.model} {product.storage}</strong></div>
        {err && <div className="err" style={{marginBottom:10}}>{err}</div>}
        <UploadZone icon="🖼" text="Нажмите или перетащите фотографию" hint="JPG, PNG, WEBP · максимум 10 МБ" onFile={setFile} file={file} uploading={uploading} progress={progress}/>
        <div className="mf">
          <button className="btn btn-outline btn-sm" onClick={onClose}>Отмена</button>
          <button className="btn btn-primary btn-sm" onClick={upload} disabled={!file||uploading} style={{opacity:!file||uploading?.5:1}}>
            {uploading?<><span className="spinner"/> Загрузка...</>:"Загрузить"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Просмотр фото из каталога (кнопка «📷 Фото») — без перехода в полную карточку */
function PhotoGalleryModal({ productId, token, onClose, onOpenCard, user }) {
  const [product, setProduct] = useState(null);
  const [loadErr, setLoadErr] = useState("");
  const [bigIdx, setBigIdx] = useState(null);
  const [uploadFile, setUploadFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState("");
  const [uploadProgress, setUploadProgress] = useState(0);
  const fileRef = useRef(null);

  const loadProduct = async () => {
    setLoadErr("");
    try {
      const d = await apiFetch(`/products/${productId}`, { token });
      setProduct(d);
    } catch (e) {
      setLoadErr(e.message || "Ошибка загрузки");
    }
  };

  useEffect(() => {
    let c = true;
    loadProduct().then(() => { if (!c) setProduct(null); });
    return () => { c = false; };
  }, [productId, token]);

  const doUpload = async (file) => {
    setUploading(true); setUploadErr(""); setUploadProgress(10);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/photos/product/${productId}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка загрузки");
      setUploadProgress(100);
      setUploadFile(null);
      await loadProduct();
    } catch (x) {
      setUploadErr(x.message || "Ошибка");
    }
    setUploading(false);
  };

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (bigIdx != null) setBigIdx(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [bigIdx, onClose]);

  const photos = product?.photos || [];
  const title = product
    ? [product.brand, product.model, product.storage].filter(Boolean).join(" ").trim() || product.imei
    : "…";

  return (
    <>
      <div className="mo" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <div className="md" style={{ maxWidth: 720, maxHeight: "90vh", overflow: "auto" }} onClick={(e) => e.stopPropagation()}>
          <div className="mt">📷 Фотографии</div>
          <div className="ms">{title}</div>
          {loadErr && <div className="err" style={{ marginBottom: 10 }}>{loadErr}</div>}
          {!product && !loadErr && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--muted)" }}>
              <span className="spinner" /> Загрузка…
            </div>
          )}
          {product && photos.length === 0 && (
            <div style={{ color: "var(--muted)", padding: "20px 0", textAlign: "center" }}>Нет фотографий</div>
          )}
          {product && photos.length > 0 && (
            <div className="pgrid" style={{ marginTop: 8 }}>
              {photos.map((ph, i) => (
                <button
                  key={ph.id}
                  type="button"
                  className="pthumb"
                  style={{ border: "none", padding: 0, cursor: "zoom-in" }}
                  onClick={() => setBigIdx(i)}
                  title="Увеличить"
                >
                  <img src={ph.url} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  {ph.is_main && <div className="pmb">ГЛАВНОЕ</div>}
                </button>
              ))}
            </div>
          )}
          {product && user && Access.canEdit(user, { store_name: product.store_name }) && !product.is_sold && (
            <div style={{marginTop:12}}>
              {uploadErr && <div className="err" style={{marginBottom:8}}>{uploadErr}</div>}
              <input ref={fileRef} type="file" accept="image/*" style={{display:"none"}} onChange={e=>{if(e.target.files[0])doUpload(e.target.files[0]);}}/>
              <button type="button" className="btn btn-sm btn-primary" disabled={uploading} onClick={()=>fileRef.current?.click()} style={{display:"inline-flex",alignItems:"center",gap:5}}>
                {uploading?<><span className="spinner"/> Загрузка...</>:<><Icon.camera/> Загрузить фото</>}
              </button>
            </div>
          )}
          <div className="mf">
            <button type="button" className="btn btn-outline btn-sm" onClick={onClose}>
              Закрыть
            </button>
            {typeof onOpenCard === "function" && (
              <button
                type="button"
                className="btn btn-primary btn-sm"
                style={{display:"inline-flex",alignItems:"center",gap:4}}
                onClick={() => {
                  onClose();
                  onOpenCard(productId);
                }}
              >
                <Icon.card/> Карточка
              </button>
            )}
          </div>
        </div>
      </div>
      {bigIdx != null && photos[bigIdx] && (
        <div
          role="presentation"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 210,
            background: "rgba(0,0,0,.92)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16,
            cursor: "zoom-out",
          }}
          onClick={() => setBigIdx(null)}
        >
          <img
            src={photos[bigIdx].url}
            alt=""
            style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", cursor: "default" }}
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}

/** Просмотр документов закупки из каталога */
function PurchaseDocsListModal({ productId, token, user, onClose, onOpenCard }) {
  const [product, setProduct] = useState(null);
  const [loadErr, setLoadErr] = useState("");
  const [actionErr, setActionErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState("");
  const [docType, setDocType] = useState("receipt");
  const docFileRef = useRef(null);

  const refreshProduct = async () => {
    const d = await apiFetch(`/products/${productId}`, { token });
    setProduct(d);
  };

  useEffect(() => {
    let c = true;
    (async () => {
      setLoadErr("");
      try {
        const d = await apiFetch(`/products/${productId}`, { token });
        if (!c) return;
        setProduct(d);
      } catch (e) {
        if (c) setLoadErr(e.message || "Ошибка загрузки");
      }
    })();
    return () => { c = false; };
  }, [productId, token]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const row = product
    ? {
        ...product,
        retail: product.price_retail,
        cost: product.price_cost,
        repair: product.in_repair,
        sold: product.is_sold,
        photos: product.photos_count,
        docs: product.docs_count,
        avito: product.avito_published,
      }
    : null;
  const canManageDocs = product && row && Access.canManagePurchaseDocs(user, row) && !product.is_sold;
  const docs = product?.docs || [];

  const downloadDoc = async (docId, fname) => {
    setActionErr("");
    try {
      const r = await fetch(`${API_BASE}/purchase-docs/${docId}/file`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось скачать");
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fname || "document";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setActionErr(e.message || "Ошибка");
    }
  };

  const delDoc = async (id) => {
    if (!canManageDocs || busy) return;
    setBusy(true);
    setActionErr("");
    try {
      await apiFetch(`/purchase-docs/${id}`, { token, method: "DELETE" });
      await refreshProduct();
    } catch (e) {
      setActionErr(e.message || "Ошибка");
    }
    setBusy(false);
  };

  const uploadDoc = async (file) => {
    setUploading(true); setUploadErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("doc_type", docType);
      const res = await fetch(`${API_BASE}/purchase-docs/product/${productId}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка загрузки");
      await refreshProduct();
    } catch (e) {
      setUploadErr(e.message || "Ошибка");
    }
    setUploading(false);
  };

  const title = product
    ? [product.brand, product.model, product.storage].filter(Boolean).join(" ").trim() || product.imei
    : "…";

  return (
    <div className="mo" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="md" style={{ maxWidth: 560, maxHeight: "90vh", overflow: "auto" }} onClick={(e) => e.stopPropagation()}>
        <div className="mt" style={{display:"flex",alignItems:"center",gap:8}}><Icon.file/> Документы закупки</div>
        <div className="ms">{title}</div>
        {loadErr && <div className="err" style={{ marginBottom: 10 }}>{loadErr}</div>}
        {actionErr && <div className="err" style={{ marginBottom: 10 }}>{actionErr}</div>}
        {!product && !loadErr && (
          <div style={{ padding: 24, textAlign: "center", color: "var(--muted)" }}>
            <span className="spinner" /> Загрузка…
          </div>
        )}
        {product && (
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12, lineHeight: 1.45 }}>
            Файлы на сервере (папка по IMEI). Скачать могут сотрудники с доступом к товару.
          </div>
        )}
        {product && docs.length === 0 && (
          <div style={{ color: "var(--muted)", padding: "12px 0", textAlign: "center" }}>Документов нет</div>
        )}
        {product && docs.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 8 }}>
            {docs.map((d) => (
              <div
                key={d.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                  flexWrap: "wrap",
                  padding: "8px 10px",
                  background: "var(--bg3)",
                  borderRadius: 8,
                  border: "1px solid var(--border)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{d.doc_type_label}</div>
                  <div style={{ fontSize: 11, color: "var(--muted)" }}>
                    {d.supplier_name || "—"} · {d.filename}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <button type="button" className="btn btn-outline btn-sm" onClick={() => downloadDoc(d.id, d.filename)}>
                    Скачать
                  </button>
                  {canManageDocs && (
                    <button type="button" className="btn-ghost" style={{ fontSize: 16 }} title="Удалить" onClick={() => delDoc(d.id)} disabled={busy}>
                      🗑
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        {canManageDocs && (
          <div style={{marginTop:12,padding:"12px",background:"var(--bg3)",borderRadius:8,border:"1px solid var(--border)"}}>
            {uploadErr && <div className="err" style={{marginBottom:8}}>{uploadErr}</div>}
            <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
              <select value={docType} onChange={e=>setDocType(e.target.value)} style={{padding:"6px 8px",background:"var(--bg2)",border:"1px solid var(--border)",borderRadius:6,color:"var(--text)",fontSize:11}}>
                <option value="receipt">Чек / Квитанция</option>
                <option value="contract">Договор купли-продажи</option>
                <option value="passport_copy">Копия паспорта</option>
                <option value="other">Другой документ</option>
              </select>
              <input ref={docFileRef} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp" style={{display:"none"}} onChange={e=>{if(e.target.files[0])uploadDoc(e.target.files[0]);}}/>
              <button type="button" className="btn btn-sm btn-doc" disabled={uploading} onClick={()=>docFileRef.current?.click()} style={{display:"inline-flex",alignItems:"center",gap:4}}>
                {uploading?<><span className="spinner"/> Загрузка...</>:<><Icon.clip/> Загрузить документ</>}
              </button>
            </div>
          </div>
        )}
        <div className="mf">
          <button type="button" className="btn btn-outline btn-sm" onClick={onClose}>
            Закрыть
          </button>
          {typeof onOpenCard === "function" && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              style={{display:"inline-flex",alignItems:"center",gap:4}}
              onClick={() => {
                onClose();
                onOpenCard(productId);
              }}
            >
              <Icon.card/> Карточка
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── DOC MODAL ────────────────────────────────────────────────────────────────
function DocModal({ product, productId, token, onClose, onDone }) {
  const [type,setType]=useState(""); const [supplier,setSupplier]=useState(""); const [file,setFile]=useState(null);
  const [consent,setConsent]=useState(false); const [uploading,setUploading]=useState(false); const [progress,setProgress]=useState(0); const [err,setErr]=useState("");
  const needConsent = type==="passport";
  const ok = type && supplier.trim() && file && (!needConsent||consent);
  const upload = async () => {
    if (!ok) return; setUploading(true); setErr(""); setProgress(5);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const q = new URLSearchParams({
        doc_type: type,
        supplier_name: supplier.trim(),
        has_pd_consent: needConsent && consent ? "true" : "false",
      });
      const r = await fetch(`${API_BASE}/purchase-docs/product/${productId}?${q}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка загрузки");
      setProgress(100);
      onDone(data);
      onClose();
    } catch (e) {
      setErr(e.message || "Ошибка");
    }
    setUploading(false);
  };
  return (
    <div className="mo" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="md">
        <div className="mt">📄 Добавить документ закупки</div>
        <div className="ms">К товару: <strong>{product.model} {product.storage}</strong> · {product.store_name}</div>
        <div className="ms" style={{fontSize:11,color:"var(--muted)",marginTop:4}}>Файлы хранятся только на сервере (папка по IMEI), наружу не выгружаются.</div>
        <div className="field">
          <label>Тип документа</label>
          <select value={type} onChange={e=>{setType(e.target.value);setConsent(false);}}>
            <option value="">— выберите —</option>
            <option value="receipt">🧾 Чек об оплате</option>
            <option value="contract">📝 Договор купли-продажи</option>
            <option value="passport">🪪 Паспорт / удостоверение личности</option>
            <option value="other">📎 Другой документ</option>
          </select>
        </div>
        {needConsent && (
          <div className="cb">
            <input type="checkbox" id="con" checked={consent} onChange={e=>setConsent(e.target.checked)}/>
            <label htmlFor="con">Письменное согласие клиента на обработку персональных данных получено (152-ФЗ).</label>
          </div>
        )}
        <div className="field"><label>Поставщик / клиент</label><input placeholder="Иванов Иван или ИП Смирнов" value={supplier} onChange={e=>setSupplier(e.target.value)}/></div>
        {err && <div className="err" style={{marginBottom:10}}>{err}</div>}
        <UploadZone icon="📎" text="Нажмите или перетащите документ" hint="PDF, JPG, PNG, WEBP · максимум 20 МБ" onFile={setFile} file={file} uploading={uploading} progress={progress}/>
        <div className="mf">
          <button className="btn btn-outline btn-sm" onClick={onClose}>Отмена</button>
          <button className="btn btn-primary btn-sm" onClick={upload} disabled={!ok||uploading} style={{opacity:!ok||uploading?.5:1}}>
            {uploading?<><span className="spinner"/> Сохранение...</>:"Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}

function defaultAvitoTitle(p) {
  const model = p.model || "";
  const brand = p.brand || "";
  const name = brand && model.toLowerCase().startsWith(brand.toLowerCase()) ? model : [brand, model].filter(Boolean).join(" ");
  const parts = [name, p.storage ? p.storage.replace(/[Gg][Bb]/, "ГБ") : ""];
  if (p.condition) parts.push("б/у");
  return parts.filter(Boolean).join(" ").trim().slice(0, 50);
}

function defaultAvitoDescription(p, store) {
  const name = [p.brand, p.model, p.storage ? p.storage.replace(/[Gg][Bb]/, "ГБ") : ""].filter(Boolean).join(" ");
  const title = [name, "б/у"].filter(Boolean).join(" ");
  const parts = [];
  if (p.battery_pct) parts.push(`аккумулятор ${p.battery_pct}`);
  const headline = [title, parts.length ? "— " + parts.join(", ") : ""].filter(Boolean).join(" ");

  const lines = [headline, ""];

  lines.push("Состояние:", "");
  if (p.battery_pct) lines.push(`Аккумулятор: ${p.battery_pct} ёмкости.`);
  if (p.condition) lines.push(`Общее состояние: ${p.condition}.`);
  if (p.color) lines.push(`Цвет: ${p.color}.`);
  if (p.in_repair) {
    lines.push("Внимание: товар находится в ремонте.");
  } else {
    lines.push("Все функции работают исправно.");
  }
  lines.push("Гарантия: 30 дней на проверку работоспособности.");

  lines.push("", "Преимущества покупки:", "");
  lines.push("Проверка перед покупкой.");
  lines.push("Помощь в настройке и переносе данных.");
  lines.push("Возможность Trade-in.");
  lines.push("Рассрочка платежа.");

  if (store && store.avito_address) {
    const sName = store.name || p.store_name || "Магазин";
    lines.push("", `Где найти: Магазин «${sName}», ${store.avito_address}. Часы работы: 9:00–19:00 (консультации до 21:00).`);
  }
  lines.push("", "Доставка: по всей России, быстрая обработка заказа.");
  lines.push("", "Примечание: устройство б/у, возможны незначительные следы эксплуатации, не влияющие на работу.");

  return lines.join("\n").slice(0, 7500);
}

// ─── PRODUCT CARD ─────────────────────────────────────────────────────────────
function DocPreviewModal({ doc, token, onClose }) {
  const [url, setUrl] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [accessLog, setAccessLog] = useState([]);
  const [showLog, setShowLog] = useState(false);
  const iframeRef = useRef(null);

  const ext = (doc.filename || "").split(".").pop().toLowerCase();
  const isImage = ["jpg","jpeg","png","webp","gif","bmp"].includes(ext);
  const isPdf = ext === "pdf";

  const logAction = (action) => {
    apiFetch(`/purchase-docs/${doc.id}/log?action=${action}`, { token, method: "POST" }).catch(() => {});
  };

  const loadLog = async () => {
    try {
      const data = await apiFetch(`/purchase-docs/${doc.id}/log`, { token });
      setAccessLog(data);
    } catch {}
  };

  useEffect(() => {
    let revoke = null;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/purchase-docs/${doc.id}/file`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) throw new Error("Не удалось загрузить документ");
        const blob = await r.blob();
        const u = URL.createObjectURL(blob);
        revoke = u;
        setUrl(u);
        logAction("view");
      } catch (e) {
        setErr(e.message);
      }
      setLoading(false);
    })();
    return () => { if (revoke) URL.revokeObjectURL(revoke); };
  }, [doc.id, token]);

  const handlePrint = () => {
    logAction("print");
    if (isImage && url) {
      const w = window.open("", "_blank");
      if (!w) return;
      w.document.write(`<html><head><title>${doc.filename}</title><style>
        body{margin:0;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#fff}
        img{max-width:100%;max-height:100vh;object-fit:contain}
        @media print{body{display:block}img{max-width:100%;height:auto}}
      </style></head><body><img src="${url}" onload="window.print();"/></body></html>`);
      w.document.close();
    } else if (isPdf && iframeRef.current) {
      try { iframeRef.current.contentWindow.print(); }
      catch { window.open(url, "_blank"); }
    }
  };

  const toggleLog = () => {
    if (!showLog) loadLog();
    setShowLog(v => !v);
  };

  const fmtDate = (iso) => {
    try {
      const d = new Date(iso);
      return d.toLocaleString("ru", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch { return iso; }
  };

  return (
    <div className="mo" onClick={onClose}>
      <div style={{background:"var(--bg2)",border:"1px solid var(--border)",borderRadius:16,width:"90vw",maxWidth:900,maxHeight:"90vh",display:"flex",flexDirection:"column",animation:"fadeUp .25s ease",overflow:"hidden"}} onClick={e=>e.stopPropagation()}>
        <div style={{padding:"14px 20px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:10,flexShrink:0}}>
          <span style={{flex:1,fontSize:14,fontWeight:700,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{doc.doc_type_label || "Документ"} — {doc.filename}</span>
          <button type="button" className="btn btn-outline btn-sm" onClick={toggleLog}>{showLog ? "Документ" : "История"}</button>
          <button type="button" className="btn btn-outline btn-sm" onClick={handlePrint} disabled={loading || !!err}>Печать</button>
          <button type="button" className="btn-ghost" style={{fontSize:18}} onClick={onClose}>✕</button>
        </div>

        {!showLog ? (
          <div style={{flex:1,overflow:"auto",display:"flex",alignItems:"center",justifyContent:"center",minHeight:300,background:"var(--bg)"}}>
            {loading && <span className="spinner" style={{width:24,height:24}}/>}
            {err && <div className="err" style={{margin:20}}>{err}</div>}
            {!loading && !err && isImage && (
              <img src={url} alt={doc.filename} style={{maxWidth:"100%",maxHeight:"80vh",objectFit:"contain",display:"block"}}/>
            )}
            {!loading && !err && isPdf && (
              <iframe ref={iframeRef} src={url} title={doc.filename} style={{width:"100%",height:"80vh",border:"none"}}/>
            )}
            {!loading && !err && !isImage && !isPdf && (
              <div style={{padding:40,textAlign:"center",color:"var(--muted)"}}>
                <div style={{fontSize:48,marginBottom:12}}>📄</div>
                <div style={{fontSize:14}}>Предпросмотр недоступен для формата .{ext}</div>
                <div style={{fontSize:12,marginTop:6}}>Используйте кнопку «Скачать»</div>
              </div>
            )}
          </div>
        ) : (
          <div style={{flex:1,overflow:"auto",padding:16,minHeight:300}}>
            <div style={{fontSize:12,fontWeight:700,color:"var(--muted)",textTransform:"uppercase",letterSpacing:".5px",marginBottom:12}}>Журнал доступа</div>
            {accessLog.length === 0 ? (
              <div style={{color:"var(--muted)",fontSize:13}}>Нет записей</div>
            ) : (
              <div style={{display:"flex",flexDirection:"column",gap:4}}>
                {accessLog.map((r, i) => (
                  <div key={i} style={{display:"flex",gap:10,padding:"7px 10px",background:"var(--bg3)",borderRadius:8,border:"1px solid var(--border)",fontSize:12,alignItems:"center"}}>
                    <span style={{fontFamily:"var(--mono)",fontSize:11,color:"var(--muted)",whiteSpace:"nowrap",minWidth:110}}>{fmtDate(r.at)}</span>
                    <span style={{fontWeight:600,minWidth:70}}>{r.user}</span>
                    <span style={{color: r.action === "Печать" ? "var(--warn)" : r.action === "Скачивание" ? "var(--success)" : "var(--accent2)"}}>{r.action}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ProductCard({ productId, token, user, onBack }) {
  const [product, setProduct] = useState(null);
  const [loadErr, setLoadErr] = useState("");
  const [modal, setModal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [avitoErr, setAvitoErr] = useState("");
  const [avitoTitle, setAvitoTitle] = useState("");
  const [avitoDesc, setAvitoDesc] = useState("");
  const [batteryPct, setBatteryPct] = useState("");
  const [previewDoc, setPreviewDoc] = useState(null);
  const [storeInfo, setStoreInfo] = useState(null);
  const [lightboxIdx, setLightboxIdx] = useState(null);

  const load = async () => {
    setLoadErr("");
    try {
      const [d, storesData] = await Promise.all([
        apiFetch(`/products/${productId}`, { token }),
        apiFetch("/stores/", { token }),
      ]);
      setProduct(d);
      const si = (storesData.items || []).find(s => s.id === d.store_id);
      setStoreInfo(si || null);
      setAvitoTitle(d.avito_title || defaultAvitoTitle(d));
      setAvitoDesc(d.avito_description || defaultAvitoDescription(d, si));
      setBatteryPct(d.battery_pct || "");
    } catch (e) {
      setLoadErr(e.message || "Ошибка загрузки");
    }
  };

  useEffect(() => { load(); }, [productId, token]);

  if (loadErr && !product) {
    return (
      <div>
        <button className="btn btn-outline btn-sm" style={{marginBottom:12}} onClick={onBack}>← Назад</button>
        <div className="err">{loadErr}</div>
      </div>
    );
  }
  if (!product) {
    return (
      <div>
        <button className="btn btn-outline btn-sm" style={{marginBottom:12}} onClick={onBack}>← Назад</button>
        <span className="spinner"/> Загрузка…
      </div>
    );
  }

  const row = { ...product, retail: product.price_retail, cost: product.price_cost, repair: product.in_repair, sold: product.is_sold, photos: product.photos_count, docs: product.docs_count, avito: product.avito_published };
  const isSold = !!product.is_sold;
  const canEdit = Access.canEdit(user, row) && !isSold;
  const canManageDocs = Access.canManagePurchaseDocs(user, row) && !isSold;
  const seeCost = Access.canSeeCost(user, row);
  const docs = product.docs || [];
  const profit = product.price_retail != null && product.price_cost != null ? product.price_retail - product.price_cost : null;

  const onPhotoAdded = async (data) => {
    await load();
  };

  const delPhoto = async (id) => {
    if (!canEdit || busy) return;
    setBusy(true);
    try {
      await apiFetch(`/photos/${id}`, { token, method: "DELETE" });
      await load();
    } catch (e) {
      setLoadErr(e.message);
    }
    setBusy(false);
  };

  const downloadDoc = async (docId, fname) => {
    try {
      const r = await fetch(`${API_BASE}/purchase-docs/${docId}/file`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось скачать");
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fname || "document";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setLoadErr(e.message || "Ошибка");
    }
  };

  const delDoc = async (docId) => {
    if (!canManageDocs || busy) return;
    if (!window.confirm("Удалить документ закупки с сервера?")) return;
    setBusy(true);
    try {
      await apiFetch(`/purchase-docs/${docId}`, { token, method: "DELETE" });
      await load();
    } catch (e) {
      setLoadErr(e.message);
    }
    setBusy(false);
  };

  const saveAvitoText = async () => {
    setAvitoErr("");
    try {
      await apiFetch(`/products/${productId}`, {
        token,
        method: "PATCH",
        json: { avito_title: avitoTitle || null, avito_description: avitoDesc || null },
      });
      await load();
    } catch (e) {
      setAvitoErr(e.message);
    }
  };

  const toggleAvito = async () => {
    if (!canEdit || busy) return;
    setBusy(true);
    setAvitoErr("");
    const next = !product.avito_published;
    try {
      await apiFetch(`/products/${productId}`, {
        token,
        method: "PATCH",
        json: { avito_published: next, avito_title: avitoTitle || null, avito_description: avitoDesc || null },
      });
      await load();
    } catch (e) {
      setAvitoErr(e.message);
    }
    setBusy(false);
  };

  const photos = product.photos || [];

  return (
    <div>
      <button className="btn btn-outline btn-sm" style={{marginBottom:12}} onClick={onBack}>← Назад</button>
      {loadErr && <div className="err" style={{marginBottom:10}}>{loadErr}</div>}

      {isSold && (
        <div className="sold-banner">
          <span>🏷️</span>
          <span><strong>Товар продан</strong>{product.sold_at ? ` (${fmtDt(product.sold_at)})` : ""} — редактирование запрещено.</span>
        </div>
      )}

      {!canEdit && !isSold && (
        <div className="banner ban-ro" style={{marginBottom:12}}>
          <span>👁</span>
          <span>
            {Access.isInfo(user)
              ? <>Роль <strong>Инфо</strong> — просмотр по всем магазинам без учётной и прибыли, без фото и документов закупки.</>
              : <>Товар магазина <strong>{product.store_name}</strong> — только просмотр.</>}
          </span>
        </div>
      )}

      <div className="cg2">
        <div>
          <div className="panel">
            <div className="ph"><span className="pt2">Основные данные</span><Chip condition={product.condition} repair={product.in_repair} sold={isSold}/></div>
            <div className="pb2">
              <div className="ir"><span className="ik">Модель</span><span className="iv" style={{fontFamily:"var(--sans)"}}>{product.model}</span></div>
              {product.storage && <div className="ir"><span className="ik">Память</span><span className="iv">{product.storage}</span></div>}
              {product.color && <div className="ir"><span className="ik">Цвет</span><span className="iv">{product.color}</span></div>}
              {product.battery_pct && <div className="ir"><span className="ik">АКБ</span><span className="iv" style={{color:"var(--warn)"}}>{product.battery_pct}</span></div>}
              <div className="ir"><span className="ik">IMEI / S/N</span><span className="iv" style={{color:"var(--accent)",fontSize:11,cursor:"pointer"}} title="Нажмите, чтобы скопировать" onClick={()=>copyText(product.imei)}>{product.imei}</span></div>
              <div className="ir"><span className="ik">Магазин</span>
                <span style={{display:"flex",alignItems:"center",fontSize:12,fontFamily:"var(--sans)",fontWeight:500}}>
                  <span className="sdot" style={{background:STORE_COLORS[product.store_name]||"#64748b"}}/>{product.store_name}
                </span>
              </div>
              {product.purchased_at && <div className="ir"><span className="ik">Дата покупки</span><span className="iv">{new Date(product.purchased_at).toLocaleDateString("ru-RU")}</span></div>}
              <div className="ir"><span className="ik">Розница</span><span className="iv" style={{color:"var(--success)"}}>{fmt(product.price_retail)}</span></div>
              {seeCost ? <>
                <div className="ir"><span className="ik">Учётная</span><span className="iv" style={{color:"var(--muted)"}}>{fmt(product.price_cost)}</span></div>
                <div className="ir"><span className="ik">Прибыль</span><span className="iv" style={{color:profit>=0?"var(--success)":"var(--danger)"}}>{profit!=null?(profit>=0?"+":"")+profit.toLocaleString("ru")+" ₽":"—"}</span></div>
              </> : (
                <div className="ir"><span className="ik">Учётная / прибыль</span><span style={{fontSize:18,color:"var(--border2)"}} title="Недоступно">🔒</span></div>
              )}
            </div>
          </div>

          {!Access.isInfo(user) && (
          <div className="panel">
            <div className="ph">
              <span className="pt2">📷 Фотографии</span>
              <span style={{fontSize:11,color:"var(--muted)",marginRight:6}}>{photos.length}/10</span>
              {canEdit
                ? <button className="btn btn-sm btn-photo" onClick={()=>setModal("photo")}>+ Добавить</button>
                : <span style={{fontSize:11,color:"var(--muted)"}}>🔒</span>}
            </div>
            <div className="pb2">
              <div className="pgrid">
                {photos.map((ph,i)=>(
                  <div key={ph.id} className="pthumb" title="Нажмите для увеличения" style={{cursor:"zoom-in"}} onClick={()=>setLightboxIdx(i)}>
                    <img src={ph.url} alt="" style={{width:"100%",height:"100%",objectFit:"cover"}}/>
                    {ph.is_main && <div className="pmb">ГЛАВНОЕ</div>}
                    {canEdit && <button type="button" className="pdel" onClick={e=>{e.stopPropagation();delPhoto(ph.id)}}>✕</button>}
                  </div>
                ))}
                {Array.from({length:Math.max(0,6-photos.length)}).map((_,i)=>(
                  <div key={"e"+i} className="pthumb empty" style={{opacity:Math.max(.1,.28-i*.06)}}><span style={{fontSize:18,color:"var(--muted)"}}>+</span></div>
                ))}
              </div>
              <div style={{fontSize:11,color:"var(--muted)"}}>
                {canEdit ? "JPG, PNG, WEBP до 10 МБ." : `Загрузка фото — только сотрудникам ${product.store_name}`}
              </div>
            </div>
          </div>
          )}
        </div>

        <div>
          <div className="panel">
            <div className="ph">
              <span className="pt2">▲ Авито</span>
              <span style={{fontSize:11,color:product.avito_published?"var(--success)":"var(--muted)"}}>{product.avito_published?"Опубликован":"Не опубликован"}</span>
            </div>
            <div className="pb2">
              {canEdit && (
                <>
                  <div className="field" style={{marginBottom:10}}>
                    <label>Заголовок (до 50 символов)</label>
                    <input maxLength={50} value={avitoTitle} onChange={e=>setAvitoTitle(e.target.value)} placeholder={defaultAvitoTitle(product)}/>
                  </div>
                  <div className="field" style={{marginBottom:10}}>
                    <label>Описание для Авито</label>
                    <textarea rows={6} style={{width:"100%",resize:"vertical",padding:9,background:"var(--bg3)",border:"1px solid var(--border)",borderRadius:8,color:"var(--text)",fontFamily:"var(--sans)",fontSize:12}} maxLength={7500} value={avitoDesc} onChange={e=>setAvitoDesc(e.target.value)} placeholder={defaultAvitoDescription(product, storeInfo)}/>
                  </div>
                  <button type="button" className="btn btn-outline btn-sm" style={{marginBottom:10}} onClick={saveAvitoText} disabled={busy}>Сохранить текст</button>
                </>
              )}
              {avitoErr && <div className="err" style={{marginBottom:8}}>{avitoErr}</div>}
              {canEdit && photos.length > 0 ? (
                <button type="button" className={`btn btn-sm ${product.avito_published?"btn-avito-off":"btn-avito"}`} onClick={toggleAvito} disabled={busy} style={{width:"100%",justifyContent:"center"}}>
                  {product.avito_published ? "Снять с Авито" : "Опубликовать на Авито"}
                </button>
              ) : canEdit && photos.length === 0 ? (
                <div style={{fontSize:12,color:"var(--muted)"}}>Добавьте хотя бы одно фото для публикации на Авито</div>
              ) : (
                <div style={{fontSize:12,color:"var(--muted)"}}>Публикация доступна только сотрудникам {product.store_name}</div>
              )}
              {product.avito_published && (
                <div style={{fontSize:11,color:"var(--muted)",marginTop:8}}>
                  Фид: <code style={{fontSize:10,cursor:"pointer",color:"var(--accent)"}} onClick={()=>copyText(location.origin+"/api/avito/feed/"+product.store_id+".xml")} title="Нажмите, чтобы скопировать">{location.origin}/api/avito/feed/{product.store_id}.xml</code>
                </div>
              )}
            </div>
          </div>

          {!Access.isInfo(user) && !product.is_new && (
          <div className="panel">
            <div className="ph">
              <span className="pt2">📄 Документы закупки</span>
              <span style={{fontSize:11,color:"var(--muted)",marginRight:6}}>{docs.length}</span>
              {canManageDocs
                ? <button type="button" className="btn btn-sm btn-doc" onClick={()=>setModal("doc")}>+ Добавить</button>
                : <span style={{fontSize:11,color:"var(--muted)"}} title="Только админ и сотрудник своего магазина">🔒</span>}
            </div>
            <div className="pb2">
              <div style={{fontSize:11,color:"var(--muted)",marginBottom:10,lineHeight:1.45}}>
                Фото/PDF подтверждения покупки у клиента. Хранятся в папке по IMEI на сервере, в объявления не попадают.
                Просматривают все сотрудники с доступом к товару; загружают и удаляют — администраторы и сотрудники своего магазина.
              </div>
              {docs.length === 0 ? (
                <div style={{fontSize:12,color:"var(--muted)"}}>Документов пока нет</div>
              ) : (
                <div style={{display:"flex",flexDirection:"column",gap:8}}>
                  {docs.map((d)=>(
                    <div key={d.id} style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:8,flexWrap:"wrap",padding:"8px 10px",background:"var(--bg3)",borderRadius:8,border:"1px solid var(--border)"}}>
                      <div style={{minWidth:0}}>
                        <div style={{fontSize:12,fontWeight:600}}>{d.doc_type_label}</div>
                        <div style={{fontSize:11,color:"var(--muted)"}}>{d.supplier_name || "—"} · {d.filename}</div>
                      </div>
                      <div style={{display:"flex",gap:6,alignItems:"center"}}>
                        <button type="button" className="btn btn-outline btn-sm" onClick={()=>setPreviewDoc(d)}>Просмотр</button>
                        <button type="button" className="btn btn-outline btn-sm" onClick={()=>downloadDoc(d.id, d.filename)}>Скачать</button>
                        {canManageDocs && <button type="button" className="btn-ghost" style={{fontSize:16}} title="Удалить" onClick={()=>delDoc(d.id)}>🗑</button>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          )}
        </div>
      </div>

      {modal==="photo" && (
        <PhotoModal
          product={{ model: product.model, storage: product.storage }}
          productId={productId}
          token={token}
          onClose={()=>setModal(null)}
          onDone={onPhotoAdded}
        />
      )}
      {modal==="doc" && (
        <DocModal
          product={product}
          productId={productId}
          token={token}
          onClose={()=>setModal(null)}
          onDone={()=>load()}
        />
      )}
      {previewDoc && (
        <DocPreviewModal
          doc={previewDoc}
          token={token}
          onClose={()=>setPreviewDoc(null)}
        />
      )}
      {lightboxIdx != null && photos[lightboxIdx] && (
        <ProductPhotoLightbox
          photos={photos}
          index={lightboxIdx}
          onChangeIndex={setLightboxIdx}
          onClose={()=>setLightboxIdx(null)}
        />
      )}
    </div>
  );
}

function ProductPhotoLightbox({ photos, index, onChangeIndex, onClose }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") onChangeIndex((index - 1 + photos.length) % photos.length);
      else if (e.key === "ArrowRight") onChangeIndex((index + 1) % photos.length);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, photos.length, onClose, onChangeIndex]);

  const navBtn = {
    position: "absolute", top: "50%", transform: "translateY(-50%)",
    background: "rgba(0,0,0,.55)", border: "none", color: "#fff",
    width: 44, height: 44, borderRadius: "50%", fontSize: 22,
    cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
    backdropFilter: "blur(4px)", transition: "background .15s",
  };

  return (
    <div
      role="presentation"
      style={{
        position: "fixed", inset: 0, zIndex: 300,
        background: "rgba(0,0,0,.92)", display: "flex",
        alignItems: "center", justifyContent: "center", padding: 16,
      }}
      onClick={onClose}
    >
      <img
        src={photos[index].url}
        alt=""
        style={{ maxWidth: "90%", maxHeight: "90%", objectFit: "contain", borderRadius: 8 }}
        onClick={(e) => e.stopPropagation()}
      />
      {photos.length > 1 && (
        <>
          <button
            type="button"
            style={{ ...navBtn, left: 16 }}
            onClick={(e) => { e.stopPropagation(); onChangeIndex((index - 1 + photos.length) % photos.length); }}
            title="Предыдущее фото"
          >‹</button>
          <button
            type="button"
            style={{ ...navBtn, right: 16 }}
            onClick={(e) => { e.stopPropagation(); onChangeIndex((index + 1) % photos.length); }}
            title="Следующее фото"
          >›</button>
        </>
      )}
      <button
        type="button"
        style={{
          position: "absolute", top: 16, right: 16,
          background: "rgba(0,0,0,.55)", border: "none", color: "#fff",
          width: 36, height: 36, borderRadius: "50%", fontSize: 18,
          cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
          backdropFilter: "blur(4px)",
        }}
        onClick={(e) => { e.stopPropagation(); onClose(); }}
        title="Закрыть"
      >✕</button>
      {photos.length > 1 && (
        <div style={{
          position: "absolute", bottom: 20, left: "50%", transform: "translateX(-50%)",
          background: "rgba(0,0,0,.6)", borderRadius: 12, padding: "4px 12px",
          fontSize: 13, color: "#fff", backdropFilter: "blur(4px)",
        }}>
          {index + 1} / {photos.length}
        </div>
      )}
    </div>
  );
}

// ─── PRODUCTS PAGE ────────────────────────────────────────────────────────────
/** Максимальный размер одной порции (бэкенд le=20000). */
const CATALOG_BATCH = 20000;

function mapProductRow(p) {
  return {
    ...p,
    retail: p.price_retail,
    cost: p.price_cost,
    repair: p.in_repair,
    sold: p.is_sold,
    photos: p.photos_count,
    docs: p.docs_count,
    avito: p.avito_published,
  };
}

function ProductsPage({ user, token, activeStore, onOpen, onActiveStoreChange, isNew, soldOnly = false }) {
  const [q,setQ]=useState(""); const [debouncedQ,setDebouncedQ]=useState(""); const [submittedQ,setSubmittedQ]=useState("");
  const [soldFrom,setSoldFrom]=useState(""); const [soldTo,setSoldTo]=useState("");
  const [brand,setBrand]=useState(""); const [cond,setCond]=useState("");
  const [storeFilter,setStoreFilter]=useState(() => (Access.seesAllStores(user) ? (activeStore || "") : ""));
  const showSold = soldOnly;
  const [avitoFilter,setAvitoFilter]=useState("");
  const [costColorFilter,setCostColorFilter]=useState("");
  const [sortCol,setSortCol]=useState("model");
  const [sortDir,setSortDir]=useState("asc");
  const [visibleCount,setVisibleCount]=useState(80);
  const [items,setItems]=useState([]);
  const [total,setTotal]=useState(0);
  const [loading,setLoading]=useState(true);
  const [listErr,setListErr]=useState("");
  const [avitoListErr,setAvitoListErr]=useState("");
  const [brands,setBrands]=useState([]);
  const [photoGalleryId, setPhotoGalleryId] = useState(null);
  const [docsModalId, setDocsModalId] = useState(null);
  const [priceStats, setPriceStats] = useState({});
  const [expandedNew, setExpandedNew] = useState({});

  const isAdm  = Access.isAdmin(user);
  const isInfo = Access.isInfo(user);
  const showPurchaseDocsBtn = !isInfo;

  useEffect(() => {
    if (Access.seesAllStores(user)) setStoreFilter(activeStore || "");
  }, [activeStore, user]);

  // Загрузка аналитики цен
  useEffect(() => {
    let c = true;
    (async () => {
      try {
        if (isNew) { setPriceStats({}); return; }
        const params = new URLSearchParams({ limit: "500", min_units: "1" });
        if (showSold) params.set("include_sold", "true");
        params.set("is_new", "false");
        const data = await apiFetch(`/analytics/price-aggregates?${params}`, { token });
        if (!c) return;
        const map = {};
        for (const r of (data.items || [])) {
          const key = [r.brand||"", r.model||"", r.storage||"", r.condition||""].join("|").toLowerCase();
          const comp_price = r.competitor?.price_excellent || r.competitor?.price_good || 0;
          const avg_cost = r.avg_cost || 0;
          let market_avg = 0;
          if (avg_cost && comp_price) market_avg = (avg_cost + comp_price) / 2;
          else if (comp_price) market_avg = comp_price;
          else if (avg_cost) market_avg = avg_cost;
          map[key] = { avg: r.avg_retail, min: r.min_retail, max: r.max_retail, market_avg };
        }
        setPriceStats(map);
      } catch {}
    })();
    return () => { c = false; };
  }, [token, showSold, isNew]);

  useEffect(()=>{
    const t = setTimeout(()=>setDebouncedQ(q), 300);
    return ()=>clearTimeout(t);
  }, [q]);
  const submitSearch = () => setSubmittedQ(q);

  useEffect(()=>{
    let cancelled = false;
    (async () => {
      setLoading(true); setListErr("");
      try {
        const base = new URLSearchParams();
        if (submittedQ.trim()) base.set("q", submittedQ.trim());
        if (brand) base.set("brand", brand);
        if (cond) base.set("condition", cond);
        if (soldOnly) base.set("sold_only", "true");
        else if (showSold) base.set("include_sold", "true");
        if (soldOnly && soldFrom) base.set("sold_from", soldFrom);
        if (soldOnly && soldTo) base.set("sold_to", soldTo);
        if (storeFilter) base.set("store", storeFilter);
        if (isNew === true) base.set("is_new", "true");
        if (isNew === false) base.set("is_new", "false");
        base.set("size", String(CATALOG_BATCH));
        base.set("page", "1");

        const data = await apiFetch(`/products/?${base.toString()}`, { token });
        if (cancelled) return;
        const all = data.items || [];
        const reportedTotal = data.total ?? all.length;
        setItems(all);
        setTotal(reportedTotal);
        setVisibleCount(80);
        const br = [...new Set(all.map((x) => x.brand).filter(Boolean))].sort();
        setBrands(br);
      } catch (e) {
        if (!cancelled) setListErr(e.message || "Ошибка списка");
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [token, submittedQ, brand, cond, showSold, storeFilter, isNew, soldFrom, soldTo]);

  const [sentinelEl, setSentinelEl] = useState(null);
  useEffect(() => {
    if (!sentinelEl) return;
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) setVisibleCount(v => v + 80);
    }, { rootMargin: "200px" });
    obs.observe(sentinelEl);
    return () => obs.disconnect();
  }, [sentinelEl]);
  useEffect(() => { setVisibleCount(80); }, [sortCol, sortDir, avitoFilter, costColorFilter]);

  function priceColor(p) {
    if (!p.price_retail) return "var(--muted)";
    const key = [p.brand||"", p.model||"", p.storage||"", p.condition||""].join("|").toLowerCase();
    const st = priceStats[key];
    if (!st || !st.avg) return "var(--text)";
    const diff = (p.price_retail - st.avg) / st.avg;
    if (diff <= -0.05) return "var(--success)";   // ниже средней на 5%+ — зелёный (выгодно)
    if (diff >= 0.05) return "var(--danger)";      // выше средней на 5%+ — красный (дорого)
    return "var(--warn)";                           // около средней — жёлтый
  }

  function costColor(p) {
    if (!p.price_cost) return "var(--muted)";
    const key = [p.brand||"", p.model||"", p.storage||"", p.condition||""].join("|").toLowerCase();
    const st = priceStats[key];
    if (!st || !st.market_avg) return "var(--muted)";
    const diff = (p.price_cost - st.market_avg) / st.market_avg;
    if (diff < -0.03)             return "var(--success)";  // дешевле рынка >3% — зелёный
    if (diff <= 0.03)             return "var(--cyan)";     // ±3% — голубой
    if (diff <= 0.06)             return "var(--warn)";     // +3%..+6% — жёлтый
    return "var(--danger)";                                  // >+6% — красный
  }

  function priceTitle(p) {
    if (!p.price_retail) return "";
    const key = [p.brand||"", p.model||"", p.storage||"", p.condition||""].join("|").toLowerCase();
    const st = priceStats[key];
    if (!st) return "";
    return `Средняя: ${Math.round(st.avg).toLocaleString("ru")} ₽ · Мин: ${Math.round(st.min).toLocaleString("ru")} ₽ · Макс: ${Math.round(st.max).toLocaleString("ru")} ₽`;
  }

  const filtered = items.filter(p => {
    if (avitoFilter === "yes" && !p.avito_published) return false;
    if (avitoFilter === "no" && p.avito_published) return false;
    if (costColorFilter) {
      const c = costColor(p);
      if (costColorFilter === "green"  && c !== "var(--success)") return false;
      if (costColorFilter === "cyan"   && c !== "var(--cyan)")    return false;
      if (costColorFilter === "yellow" && c !== "var(--warn)")    return false;
      if (costColorFilter === "red"    && c !== "var(--danger)")  return false;
      if (costColorFilter === "none"   && c !== "var(--muted)")   return false;
    }
    return true;
  });
  const toggleSort = (col) => {
    if (sortCol === col) { setSortDir(d => d === "asc" ? "desc" : "asc"); }
    else { setSortCol(col); setSortDir("asc"); }
  };
  const sortArrow = (col) => sortCol === col ? (sortDir === "asc" ? " ▲" : " ▼") : "";
  const thStyle = {cursor:"pointer",userSelect:"none"};
  const slice = (() => {
    if (!sortCol) return filtered;
    const arr = [...filtered];
    const dir = sortDir === "asc" ? 1 : -1;
    arr.sort((a,b) => {
      let va, vb;
      switch (sortCol) {
        case "store":    va = a.store_name||""; vb = b.store_name||""; break;
        case "model":    va = (a.model||"")+" "+(a.storage||""); vb = (b.model||"")+" "+(b.storage||""); break;
        case "condition":va = a.condition||""; vb = b.condition||""; break;
        case "sold_at":  va = a.sold_at||""; vb = b.sold_at||""; break;
        case "imei":     va = a.imei||""; vb = b.imei||""; break;
        case "quantity": return dir * ((a.quantity||0) - (b.quantity||0));
        case "retail":   return dir * ((a.price_retail||0) - (b.price_retail||0));
        case "cost":     return dir * ((a.price_cost||0) - (b.price_cost||0));
        case "profit":   return dir * (((a.price_retail||0)-(a.price_cost||0)) - ((b.price_retail||0)-(b.price_cost||0)));
        default: return 0;
      }
      return dir * va.localeCompare(vb, "ru");
    });
    return arr;
  })();
  const totalR    = filtered.reduce((s,p)=>s+(p.price_retail||0),0);
  const myProfit  = filtered.filter(p=>Access.canSeeCost(user, mapProductRow(p))).reduce((s,p)=>s+(p.price_retail||0)-(p.price_cost||0),0);
  const inRepair  = filtered.filter(p=>p.in_repair).length;

  const toggleSiteRow = async (p, next) => {
    const prev = p.site_published;
    setItems((xs) => xs.map((x) => (x.id === p.id ? { ...x, site_published: next } : x)));
    try {
      await apiFetch(`/products/${p.id}`, { token, method: "PATCH", json: { site_published: next } });
    } catch (e) {
      setItems((xs) => xs.map((x) => (x.id === p.id ? { ...x, site_published: prev } : x)));
    }
  };

  const toggleAvitoRow = async (p, next) => {
    setAvitoListErr("");
    const prev = p.avito_published;
    setItems((xs) => xs.map((x) => (x.id === p.id ? { ...x, avito_published: next } : x)));
    try {
      await apiFetch(`/products/${p.id}`, { token, method: "PATCH", json: { avito_published: next } });
    } catch (e) {
      setItems((xs) => xs.map((x) => (x.id === p.id ? { ...x, avito_published: prev } : x)));
      setAvitoListErr(e.message || "Не удалось изменить Авито");
    }
  };

  return (
    <>
      <div className={`banner ${isAdm?"ban-admin":"ban-staff"}`}>
        <span style={{flexShrink:0}}>{isAdm?"⚙️":isInfo?"ℹ️":"🟢"}</span>
        <span>
          {isAdm
            ? <><strong>Администратор</strong> — полный доступ ко всем магазинам, учётным ценам и прибыли.</>
            : isInfo
              ? <><strong>Инфо</strong> — каталог <strong>всех магазинов</strong> (фильтр «Магазин» сужает список). Без учётной цены и прибыли, без превью и документов закупки, без редактирования.</>
            : <><strong>Сотрудник</strong> — каталог <strong>всех магазинов</strong> (фильтр «Магазин» сужает список). Колонки «Учётная цена» и «Прибыль» — только для <strong>{user.store_name}</strong>; для остальных — скрыто. Загрузка фото и документов, карточка для Авито — только у строк <strong>{user.store_name}</strong>.</>}
        </span>
      </div>

      {listErr && <div className="err" style={{marginBottom:10}}>{listErr}</div>}
      {avitoListErr && <div className="err" style={{marginBottom:10}}>{avitoListErr}</div>}
      {loading && <div style={{marginBottom:10,color:"var(--muted)"}}><span className="spinner"/> Загрузка…</div>}
      <div className="stats-bar">
        <div className="sc"><div className="sc-label">Товаров</div><div className="sc-val" style={{color:"var(--accent)"}}>{total}</div></div>
        <div className="sc"><div className="sc-label">Розница</div><div className="sc-val" style={{color:"var(--success)"}}>{(totalR/1000).toFixed(0)}К ₽</div></div>
        {!isInfo && (
        <div className="sc"><div className="sc-label">Прибыль{!isAdm&&` (${user.store_name})`}</div><div className="sc-val" style={{color:myProfit>=0?"var(--success)":"var(--danger)"}}>{myProfit>=0?"+":""}{(myProfit/1000).toFixed(0)}К ₽</div></div>
        )}
        <div className="sc"><div className="sc-label">В ремонте</div><div className="sc-val" style={{color:inRepair?"var(--danger)":"var(--muted)"}}>{inRepair}</div></div>
      </div>


      <div className="filters">
        <div style={{position:"relative",display:"flex",alignItems:"center",flex:1,minWidth:200}}>
          <input className="fi" placeholder="Поиск по модели, IMEI, цвету..." value={q} onChange={e=>setQ(e.target.value)} onKeyDown={e=>e.key==="Enter"&&submitSearch()} style={{paddingRight: q ? 28 : undefined}}/>
          {q && <button onClick={()=>{setQ("");setSubmittedQ("");}} style={{position:"absolute",right:8,background:"none",border:"none",color:"var(--text)",cursor:"pointer",fontSize:18,lineHeight:1,padding:"0 2px",zIndex:1,opacity:.6}} title="Очистить">×</button>}
        </div>
        <select className="fs" value={brand} onChange={e=>setBrand(e.target.value)}>
          <option value="">Все бренды</option>
          {brands.map(b=><option key={b}>{b}</option>)}
        </select>
        {!isNew && (
        <select className="fs" value={cond} onChange={e=>setCond(e.target.value)}>
          <option value="">Любое состояние</option>
          {["Отличное","Как новый","Хорошее","Среднее","Плохое"].map(c=><option key={c}>{c}</option>)}
        </select>
        )}
        {(Access.seesAllStores(user) || !isInfo) && (
        <select
          className="fs"
          value={storeFilter}
          onChange={(e) => {
            const v = e.target.value;
            setStoreFilter(v);
            if (Access.seesAllStores(user) && typeof onActiveStoreChange === "function") onActiveStoreChange(v);
          }}
          title="Фильтр по магазину"
        >
          <option value="">Все магазины</option>
          {STORES.map((s)=><option key={s}>{s}</option>)}
        </select>
        )}
        {!isNew && (
        <select className="fs" value={avitoFilter} onChange={e=>setAvitoFilter(e.target.value)}>
          <option value="">Авито: все</option>
          <option value="yes">На Авито</option>
          <option value="no">Не на Авито</option>
        </select>
        )}
        {showSold && (
          <>
            <input className="fi" type="date" style={{maxWidth:140}} value={soldFrom} onChange={e=>setSoldFrom(e.target.value)} title="Продано с"/>
            <input className="fi" type="date" style={{maxWidth:140}} value={soldTo} onChange={e=>setSoldTo(e.target.value)} title="Продано по"/>
          </>
        )}
        {!isNew && !showSold && (
        <select className="fs" value={costColorFilter} onChange={e=>setCostColorFilter(e.target.value)}>
          <option value="">Учётная: все</option>
          <option value="green">🟢 Ниже рынка (&lt;-3%)</option>
          <option value="cyan">🔵 Около рынка (±3%)</option>
          <option value="yellow">🟡 Немного выше (+3..6%)</option>
          <option value="red">🔴 Выше рынка (&gt;+6%)</option>
          <option value="none">⚪ Нет данных</option>
        </select>
        )}
        <span className="fc">{filtered.length} шт.</span>
      </div>

      {isNew ? (() => {
        const groupMap = {};
        for (const p of slice) {
          const key = `${p.model||""}|${p.storage||""}|${p.color||""}`;
          if (!groupMap[key]) groupMap[key] = { model: p.model, storage: p.storage, color: p.color, brand: p.brand, items: [], totalQty: 0, minPrice: Infinity, maxPrice: -Infinity, sumPrice: 0, count: 0 };
          const g = groupMap[key];
          g.items.push(p);
          g.totalQty += (p.quantity || 1);
          if (p.price_retail) { g.sumPrice += p.price_retail; g.count++; if (p.price_retail < g.minPrice) g.minPrice = p.price_retail; if (p.price_retail > g.maxPrice) g.maxPrice = p.price_retail; }
        }
        const groups = Object.entries(groupMap).map(([key, g]) => ({ key, ...g, avgPrice: g.count ? g.sumPrice / g.count : 0 }));
        groups.sort((a, b) => (a.model || "").localeCompare(b.model || "", "ru") || (a.storage || "").localeCompare(b.storage || "", "ru"));
        return (
          <div className="tw">
            <table className="pt">
              <thead><tr>
                <th>Модель</th><th>Память</th><th>Цвет</th>
                <th style={{textAlign:"center"}}>Кол-во</th>
                <th style={{textAlign:"right"}}>Розница</th>
                {!isInfo && <th style={{textAlign:"right"}}>Учётная</th>}
                <th/>
              </tr></thead>
              <tbody>
                {groups.map(g => {
                  const isOpen = expandedNew[g.key];
                  return (
                    <React.Fragment key={g.key}>
                      <tr style={{cursor:"pointer"}} onClick={() => setExpandedNew(prev => ({...prev, [g.key]: !prev[g.key]}))}>
                        <td style={{fontWeight:600}}><span style={{marginRight:6,fontSize:10,color:"var(--muted)"}}>{isOpen?"▼":"▶"}</span>{g.model}</td>
                        <td className="mono">{g.storage || "—"}</td>
                        <td style={{fontSize:11,color:"var(--muted)"}}>{g.color || "—"}</td>
                        <td style={{textAlign:"center",fontFamily:"var(--mono)"}}>{g.totalQty}</td>
                        <td/>{!isInfo && <td/>}<td/>
                      </tr>
                      {isOpen && g.items.map(p => {
                        const row = mapProductRow(p);
                        const canOpenCard = Access.canOpenProductCard(user, row);
                        return (
                          <tr key={p.id} style={{background:"rgba(255,255,255,.04)",fontSize:12}}>
                            <td style={{paddingLeft:28}}><span style={{display:"inline-block",padding:"3px 10px",borderRadius:6,fontSize:10,fontWeight:600,color:"#fff",background:STORE_GRADIENTS[p.store_name]||"var(--bg4)",boxShadow:STORE_GRADIENTS[p.store_name]?`0 2px 8px ${STORE_COLORS[p.store_name]||"transparent"}40`:""}}>{p.store_name||"—"}</span></td>
                            <td className="mono" style={{cursor:"pointer",color:"var(--text)"}} title="Скопировать" onClick={()=>copyText(p.imei)}>{p.imei || "—"}{p.sim_type ? <span style={{color:"var(--accent2)",marginLeft:6,fontFamily:"var(--sans)",fontSize:10}}>{p.sim_type}</span> : ""}</td>
                            <td/>
                            <td style={{textAlign:"center",color:"var(--text)"}}>{p.quantity || 1}</td>
                            <td className="tr" style={{color:"var(--success)"}}>{fmt(p.price_retail)}</td>
                            {!isInfo && <td className="tr" style={{color:"var(--text)"}}>{fmt(p.price_cost)}</td>}
                            <td style={{textAlign:"right"}}>{canOpenCard && <button type="button" className="act" onClick={()=>onOpen(p.id)}>Карточка</button>}</td>
                          </tr>
                        );
                      })}
                    </React.Fragment>
                  );
                })}
                {!loading && groups.length === 0 && <tr><td colSpan={!isInfo?7:6} style={{textAlign:"center",padding:"32px",color:"var(--muted)"}}>Товары не найдены</td></tr>}
              </tbody>
            </table>
          </div>
        );
      })() : (
      <>
      <div className="tw">
        <table className="pt">
          <thead><tr>
            {showSold && <th style={thStyle} onClick={()=>toggleSort("sold_at")}>Продано{sortArrow("sold_at")}</th>}
            <th style={thStyle} onClick={()=>toggleSort("store")}>Магазин{sortArrow("store")}</th>
            <th style={thStyle} onClick={()=>toggleSort("model")}>Модель{sortArrow("model")}</th>
            <th style={thStyle} onClick={()=>toggleSort("condition")}>Состояние{sortArrow("condition")}</th>
            <th style={thStyle} onClick={()=>toggleSort("imei")}>IMEI{sortArrow("imei")}</th>
            <th style={{...thStyle,textAlign:"center"}} onClick={()=>toggleSort("quantity")}>Кол-во{sortArrow("quantity")}</th>
            {!isInfo && <th>Медиа</th>}<th style={{textAlign:"center"}}>Сайт</th><th style={{textAlign:"center"}}>Авито</th>
            <th style={{...thStyle,textAlign:"right"}} onClick={()=>toggleSort("retail")}>Розница{sortArrow("retail")}</th>
            {!isInfo && <><th className={isAdm?"":"thl"} style={{...thStyle,textAlign:"right"}} onClick={()=>toggleSort("cost")}>Учётная{sortArrow("cost")}</th>
            <th className={isAdm?"":"thl"} style={{...thStyle,textAlign:"right"}} onClick={()=>toggleSort("profit")}>Прибыль{sortArrow("profit")}</th></>}
            <th style={{textAlign:"center"}}>Действия</th>
          </tr></thead>
          <tbody>
            {slice.slice(0, visibleCount).map(p=>{
              const row = mapProductRow(p);
              const own     = Access.canEdit(user, row) && !p.is_sold;
              const seeCost = Access.canSeeCost(user, row);
              const canOpenCard = Access.canOpenProductCard(user, row);
              const profit  = (p.price_retail??0)-(p.price_cost??0);
              const rowCls  = [p.is_sold?"sold":"", p.in_repair&&!p.is_sold?"rep":"", own?"own":""].filter(Boolean).join(" ");
              const storeColor = STORE_COLORS[p.store_name];
              return (
                <tr key={p.id} className={rowCls} style={storeColor&&!p.in_repair?{background:`${storeColor}12`}:undefined}>
                  {showSold && <td style={{fontSize:12,fontFamily:"var(--mono)",color:"var(--text)",whiteSpace:"nowrap"}}>{p.sold_at?fmtDt(p.sold_at):"—"}</td>}
                  <td><span style={{display:"inline-block",padding:"3px 10px",borderRadius:6,fontSize:10,fontWeight:600,color:"#fff",background:STORE_GRADIENTS[p.store_name]||"var(--bg4)",boxShadow:STORE_GRADIENTS[p.store_name]?`0 2px 8px ${STORE_COLORS[p.store_name]||"transparent"}40`:""}}>{p.store_name||"—"}</span></td>
                  <td>
                    <div
                      className={`tm${canOpenCard ? "" : " tm-disabled"}`}
                      title={canOpenCard ? "" : "Карточка доступна только для товаров вашего магазина"}
                      onClick={() => canOpenCard && onOpen(p.id)}
                    >{p.model}{p.storage?" "+p.storage:""}</div>
                    {p.color&&<div className="ts">{p.color}{p.battery_pct?" · АКБ "+p.battery_pct:""}</div>}
                  </td>
                  <td>
                    <Chip condition={p.condition} repair={p.in_repair} sold={p.is_sold}/>
                  </td>
                  <td><span className="imei-btn" title="Нажмите, чтобы скопировать" onClick={()=>copyText(p.imei)}>{p.imei||"—"}</span></td>
                  <td style={{textAlign:"center"}}>{p.quantity || ""}</td>
                  {!isInfo && <td><span className="mc" style={{display:"inline-flex",alignItems:"center",gap:6}}><span style={{display:"inline-flex",alignItems:"center",gap:2}}><Icon.camera/>{p.photos_count}</span><span style={{display:"inline-flex",alignItems:"center",gap:2}}><Icon.file/>{p.docs_count}</span></span></td>}
                  <td style={{textAlign:"center"}}>
                    <label className="toggle-sw" title={own && !p.is_sold ? "Показать на сайте" : "Сайт"}>
                      <input type="checkbox" checked={!!p.site_published} disabled={!own || p.is_sold} onChange={(e) => { e.stopPropagation(); toggleSiteRow(p, e.target.checked); }}/>
                      <span className="sw-track"/>
                    </label>
                  </td>
                  <td style={{textAlign:"center"}}>
                    <label className="toggle-sw" title={own && !p.is_sold ? "Опубликовать на Авито" : "Авито"}>
                      <input type="checkbox" checked={!!p.avito_published} disabled={!own || p.is_sold} onChange={(e) => { e.stopPropagation(); toggleAvitoRow(p, e.target.checked); }}/>
                      <span className="sw-track"/>
                    </label>
                  </td>
                  <td className="tr" style={{color:"var(--success)"}}>{fmt(p.price_retail)}</td>
                  {!isInfo && (seeCost
                    ? <><td className="trm" style={{color:costColor(p)}} title={(()=>{const st=priceStats[[p.brand||"",p.model||"",p.storage||"",p.condition||""].join("|").toLowerCase()];return st?.market_avg?`Средняя рынок: ${Math.round(st.market_avg).toLocaleString("ru")} ₽`:""})()}>{fmt(p.price_cost)}</td>
                    <td className="tr"><span className={profit>=0?"pp":"pn"}>{profit>=0?"+":""}{profit.toLocaleString("ru")} ₽</span></td></>
                    : <><td className="tlk" title="Скрыто">🔒</td><td className="tlk" title="Скрыто">🔒</td></>)}
                  <td style={{textAlign:"center",whiteSpace:"nowrap"}}>
                    {p.is_sold
                      ? <span className="sold-badge">Продан</span>
                      : own
                        ? <>
                            <button type="button" className="act" style={{marginRight:3,display:"inline-flex",alignItems:"center",gap:4}} onClick={()=>setPhotoGalleryId(p.id)}><Icon.camera/> Фото</button>
                            {showPurchaseDocsBtn && (
                              <button type="button" className="act" style={{marginRight:3,display:"inline-flex",alignItems:"center",gap:4}} onClick={()=>setDocsModalId(p.id)} title="Документы закупки"><Icon.clip/> Документы</button>
                            )}
                            <button type="button" className="act" style={{display:"inline-flex",alignItems:"center",gap:4}} onClick={()=>onOpen(p.id)}><Icon.card/> Карточка</button>
                          </>
                        : isInfo
                          ? <button type="button" className="act" onClick={()=>onOpen(p.id)}>Карточка</button>
                        : <span style={{fontSize:11,color:"var(--muted)"}} title="Действия только для товаров вашего магазина">—</span>}
                  </td>
                </tr>
              );
            })}
            {!loading&&slice.length===0&&<tr><td colSpan={20} style={{textAlign:"center",padding:"32px",color:"var(--muted)"}}>Товары не найдены</td></tr>}
          </tbody>
        </table>
      </div>
      {visibleCount < slice.length && <div ref={setSentinelEl} style={{height:1}}/>}
      </>
      )}

      {photoGalleryId && (
        <PhotoGalleryModal
          productId={photoGalleryId}
          token={token}
          user={user}
          onClose={() => setPhotoGalleryId(null)}
          onOpenCard={(id) => {
            setPhotoGalleryId(null);
            onOpen(id);
          }}
        />
      )}
      {docsModalId && (
        <PurchaseDocsListModal
          productId={docsModalId}
          token={token}
          user={user}
          onClose={() => setDocsModalId(null)}
          onOpenCard={(id) => {
            setDocsModalId(null);
            onOpen(id);
          }}
        />
      )}
    </>
  );
}

// ─── STORE SETTINGS (admin) ───────────────────────────────────────────────────
function StoreSettingsPage({ token, activeStore }) {
  const [stores, setStores] = useState([]);
  const [f, setF] = useState({ phone: "", address: "", avitoContact: "", websiteUrl: "", websiteFeedEnabled: false });
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");
  // Avito API credentials
  const [apiId, setApiId] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [apiSaving, setApiSaving] = useState(false);
  const [apiMsg, setApiMsg] = useState("");
  // Avito subsections
  const [statsBusy, setStatsBusy] = useState(false);
  const [statsMsg, setStatsMsg] = useState("");
  const [feedBusy, setFeedBusy] = useState(false);
  const [feedMsg, setFeedMsg] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importMsg, setImportMsg] = useState("");
  // Webhook
  const [webhookBusy, setWebhookBusy] = useState(false);
  const [webhookMsg, setWebhookMsg] = useState("");
  // 1С integration
  const [onecUrl, setOnecUrl] = useState("");
  const [onecNewUrl, setOnecNewUrl] = useState("");
  const [onecSaved, setOnecSaved] = useState(false);
  const [onecErr, setOnecErr] = useState("");
  const [onecSyncBusy, setOnecSyncBusy] = useState(false);
  const [onecSyncMsg, setOnecSyncMsg] = useState("");

  const selName = activeStore || (stores[0]?.name ?? "");
  const current = stores.find((s) => s.name === selName) || stores[0];

  const reloadStores = async () => {
    const data = await apiFetch("/stores/?include_inactive=true", { token });
    setStores(data.items || []);
  };

  useEffect(() => {
    (async () => {
      try {
        const data = await apiFetch("/settings/", { token });
        setOnecUrl(data.import_1c_url || "");
        setOnecNewUrl(data.import_1c_new_url || "");
      } catch {}
    })();
  }, [token]);

  useEffect(() => {
    let c = true;
    (async () => {
      try {
        const data = await apiFetch("/stores/?include_inactive=true", { token });
        if (!c) return;
        setStores(data.items || []);
      } catch (e) {
        if (c) setErr(e.message);
      }
    })();
    return () => { c = false; };
  }, [token]);

  useEffect(() => {
    const s = stores.find((x) => x.name === selName);
    if (!s) {
      setF({ phone: "", address: "", avitoContact: "", websiteUrl: "", websiteFeedEnabled: false });
      return;
    }
    setF({
      phone: s.avito_phone || "",
      address: s.avito_address || "",
      avitoContact: s.avito_manager_name || "",
      websiteUrl: s.website_url || "",
      websiteFeedEnabled: !!s.website_feed_enabled,
    });
    setApiId("");
    setApiSecret("");
    setApiMsg("");
    setSaved(false);
  }, [selName, stores]);

  const save = async () => {
    const s = stores.find((x) => x.name === selName) || current;
    if (!s) return;
    setErr("");
    try {
      await apiFetch(`/stores/${s.id}`, {
        token,
        method: "PATCH",
        json: {
          avito_phone: f.phone || null,
          avito_address: f.address || null,
          avito_manager_name: f.avitoContact || null,
          website_url: f.websiteUrl || null,
          website_feed_enabled: f.websiteFeedEnabled,
        },
      });
      await reloadStores();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e.message);
    }
  };

  const saveApiCredentials = async () => {
    if (!current?.id || !apiId.trim() || !apiSecret.trim()) return;
    setApiSaving(true);
    setApiMsg("");
    try {
      const res = await apiFetch(`/avito/credentials/${current.id}`, {
        token,
        method: "POST",
        json: { client_id: apiId.trim(), client_secret: apiSecret.trim() },
      });
      setApiMsg("ok");
      setApiId("");
      setApiSecret("");
      // Автозаполнение контактных данных из профиля Авито
      if (res.store) {
        setF(x => ({
          ...x,
          phone: res.store.avito_phone || x.phone,
          avitoContact: res.store.avito_manager_name || x.avitoContact,
          address: res.store.avito_address || x.address,
        }));
      }
      await reloadStores();
    } catch (e) {
      setApiMsg(e.message || "Ошибка подключения");
    } finally {
      setApiSaving(false);
    }
  };

  const saveOnec = async () => {
    setOnecErr(""); setOnecSaved(false);
    try {
      await apiFetch("/settings/", { token, method: "PATCH", json: { import_1c_url: onecUrl || null, import_1c_new_url: onecNewUrl || null } });
      setOnecSaved(true);
      setTimeout(() => setOnecSaved(false), 2000);
    } catch (e) { setOnecErr(e.message || "Ошибка"); }
  };

  const syncOnec = async () => {
    setOnecSyncBusy(true); setOnecSyncMsg("");
    try {
      const results = [];
      if (onecUrl) {
        const r = await apiFetch("/imports/from-configured-url", { token, method: "POST" });
        results.push(`Б/У: +${r.items_created} создано, ${r.items_updated} обновлено, ${r.items_sold} продано`);
      }
      if (onecNewUrl) {
        const r = await apiFetch("/imports/from-configured-url-new", { token, method: "POST" });
        results.push(`Новые: +${r.items_created} создано, ${r.items_updated} обновлено`);
      }
      if (!results.length) setOnecSyncMsg("Сначала сохраните ссылки");
      else setOnecSyncMsg(results.join(" · "));
    } catch (e) { setOnecSyncMsg(e.message || "Ошибка синхронизации"); }
    setOnecSyncBusy(false);
  };

  const fetchStats = async () => {
    if (!current?.id) return;
    setStatsBusy(true); setStatsMsg("");
    try {
      const r = await apiFetch(`/avito/fetch-stats/${current.id}`, { token, method: "POST" });
      setStatsMsg(`Собрано записей: ${r.collected ?? r.total ?? "—"}`);
    } catch (e) { setStatsMsg(e.message || "Ошибка"); }
    setStatsBusy(false);
  };

  const checkFeed = async () => {
    if (!current?.id) return;
    setFeedBusy(true); setFeedMsg("");
    try {
      const r = await apiFetch(`/avito/check-feed/${current.id}`, { token, method: "POST" });
      setFeedMsg(`Активных: ${r.active ?? "—"}, ошибок: ${r.errors ?? 0}, сопоставлено: ${r.mapped ?? "—"}`);
    } catch (e) { setFeedMsg(e.message || "Ошибка"); }
    setFeedBusy(false);
  };

  const importItems = async () => {
    if (!current?.id) return;
    setImportBusy(true); setImportMsg("");
    try {
      const r = await apiFetch(`/avito/import-items/${current.id}`, { token, method: "POST" });
      setImportMsg(`Всего: ${r.total_avito ?? "—"}, привязано: ${r.linked ?? 0}, не найдено: ${r.unmatched ?? 0}`);
    } catch (e) { setImportMsg(e.message || "Ошибка"); }
    setImportBusy(false);
  };

  const registerWebhook = async () => {
    if (!current?.id) return;
    setWebhookBusy(true); setWebhookMsg("");
    try {
      const r = await apiFetch(`/avito/register-webhook/${current.id}`, { token, method: "POST" });
      setWebhookMsg(`Вебхук зарегистрирован: ${r.webhook_url}`);
    } catch (e) { setWebhookMsg(e.message || "Ошибка"); }
    setWebhookBusy(false);
  };

  const [openSections, setOpenSections] = useState(new Set(["1c"]));
  const toggleSection = (k) => setOpenSections(prev => {
    const next = new Set(prev);
    next.has(k) ? next.delete(k) : next.add(k);
    return next;
  });
  const SectionHead = ({ id, title }) => (
    <div className="ph" style={{cursor:"pointer",userSelect:"none",display:"flex",alignItems:"center",justifyContent:"space-between"}} onClick={()=>toggleSection(id)}>
      <span className="pt2">{title}</span>
      <span style={{fontSize:11,color:"var(--muted)",transition:"transform .2s",display:"inline-block",transform: openSections.has(id)?"rotate(180deg)":"rotate(0deg)"}}>▼</span>
    </div>
  );

  return (
    <>
      <div>
        {err && <div className="err" style={{marginBottom:10}}>{err}</div>}

        {/* ── Интеграция 1С ────────────────────────────── */}
        <div className="panel" style={{marginBottom:16}}>
          <SectionHead id="1c" title="Интеграция 1С"/>
          {openSections.has("1c") && <div className="pb2">
            <div style={{fontSize:12,color:"var(--muted)",marginBottom:14,lineHeight:1.6}}>
              Ссылки на выгрузку товаров из 1С. Поддерживаются Google Диск, Яндекс.Диск и прямые URL. Импорт запускается автоматически после входа и по расписанию.<br/>
              Откройте файл → «Поделиться» → скопируйте публичную ссылку.
            </div>
            <div className="field" style={{marginBottom:12}}>
              <label>Ссылка на выгрузку Б/У товаров</label>
              <input
                placeholder="Google Drive или Яндекс.Диск — публичная ссылка на HTML-файл"
                value={onecUrl}
                onChange={e => setOnecUrl(e.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="field" style={{marginBottom:12}}>
              <label>Ссылка на выгрузку Новых товаров</label>
              <input
                placeholder="Google Drive или Яндекс.Диск — публичная ссылка на HTML-файл"
                value={onecNewUrl}
                onChange={e => setOnecNewUrl(e.target.value)}
                autoComplete="off"
              />
            </div>
            {onecErr && <div className="err" style={{marginBottom:8}}>{onecErr}</div>}
            <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
              <button type="button" className="btn btn-primary" onClick={saveOnec}>
                {onecSaved ? "Сохранено" : "Сохранить"}
              </button>
              <button type="button" className="btn btn-sm btn-avito" onClick={syncOnec} disabled={onecSyncBusy || (!onecUrl && !onecNewUrl)}>
                {onecSyncBusy ? <><span className="spinner"/> Синхронизация…</> : "Синхронизировать"}
              </button>
            </div>
            {onecSyncMsg && <div style={{marginTop:8,fontSize:12,color: onecSyncMsg.includes("Ошибка") || onecSyncMsg.includes("Сначала") ? "var(--danger)" : "var(--accent)"}}>{onecSyncMsg}</div>}
          </div>}
        </div>

        {/* ── Avito API ─────────────────────────────────── */}
        <div className="panel">
          <SectionHead id="avito-api" title={`Avito API — ${selName || "—"}`}/>
          {openSections.has("avito-api") && <div className="pb2">
            <div style={{fontSize:12,color:"var(--muted)",marginBottom:14,lineHeight:1.6}}>
              Подключение к Avito REST API для управления объявлениями, статистики и мессенджера.<br/>
              Получите client_id и client_secret в <a href="https://developers.avito.ru" target="_blank" rel="noopener" style={{color:"var(--accent)"}}>developers.avito.ru</a> → Настройки → API ключи.
            </div>

            {/* Статус подключения */}
            <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:16,padding:"10px 14px",background:"var(--bg3)",borderRadius:8,border:"1px solid var(--border)"}}>
              <span style={{
                width:8, height:8, borderRadius:"50%", flexShrink:0,
                background: current?.avito_configured ? "#4ade80" : "#f87171",
              }}/>
              <span style={{fontSize:13}}>
                {current?.avito_configured
                  ? "API подключён"
                  : "API не подключён"}
              </span>
            </div>

            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"12px 20px"}}>
              <div className="field">
                <label>Client ID</label>
                <input
                  placeholder={current?.avito_configured ? "Введите новый для замены" : "Из личного кабинета Авито"}
                  value={apiId}
                  onChange={e => setApiId(e.target.value)}
                  autoComplete="off"
                />
              </div>
              <div className="field">
                <label>Client Secret</label>
                <input
                  type="password"
                  placeholder={current?.avito_configured ? "Введите новый для замены" : "Из личного кабинета Авито"}
                  value={apiSecret}
                  onChange={e => setApiSecret(e.target.value)}
                  autoComplete="off"
                />
              </div>
            </div>

            {apiMsg && apiMsg !== "ok" && (
              <div className="err" style={{marginTop:8}}>{apiMsg}</div>
            )}
            {apiMsg === "ok" && (
              <div style={{marginTop:8,fontSize:12,color:"#4ade80"}}>API подключён и проверен</div>
            )}

            <button
              type="button" className="btn btn-primary" style={{marginTop:10}}
              onClick={saveApiCredentials}
              disabled={!current || !apiId.trim() || !apiSecret.trim() || apiSaving}
            >
              {apiSaving ? "Проверка..." : "Подключить API"}
            </button>
          </div>}
        </div>

        {/* ── Контактные данные ──────────────────────────── */}
        <div className="panel" style={{marginTop:16}}>
          <SectionHead id="contacts" title={`Контактные данные — ${selName || "—"}`}/>
          {openSections.has("contacts") && <div className="pb2">
            <div style={{fontSize:12,color:"var(--muted)",marginBottom:14,lineHeight:1.6}}>
              Контактная информация для объявлений на Авито. Выберите магазин в верхней панели.
            </div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"12px 20px"}}>
              <div className="field">
                <label>Контактный телефон</label>
                <input placeholder="+7 999 123-45-67" value={f.phone} onChange={e=>setF((x)=>({ ...x, phone: e.target.value }))}/>
              </div>
              <div className="field">
                <label>Контактное лицо</label>
                <input placeholder="Как в объявлении (Avito)" value={f.avitoContact} onChange={e=>setF((x)=>({ ...x, avitoContact: e.target.value }))}/>
              </div>
              <div className="field" style={{gridColumn:"1/-1"}}>
                <label>Адрес магазина (для объявлений)</label>
                <input placeholder="г. Москва, ул. Примерная, 1" value={f.address} onChange={e=>setF((x)=>({ ...x, address: e.target.value }))}/>
              </div>
            </div>
            <button type="button" className="btn btn-primary" onClick={save} style={{marginTop:10}} disabled={!current}>
              {saved ? "Сохранено" : "Сохранить"}
            </button>
          </div>}
        </div>

        {/* ── Выгрузка на сайт ──────────────────────────── */}
        <div className="panel" style={{marginTop:16}}>
          <SectionHead id="website" title={`Выгрузка на сайт — ${selName || "—"}`}/>
          {openSections.has("website") && <div className="pb2">
            <div style={{fontSize:12,color:"var(--muted)",marginBottom:14,lineHeight:1.6}}>
              JSON-фид с б/у товарами для сайта магазина. Содержит модель, цену, состояние, фото. Обновляется автоматически.
            </div>
            <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:14}}>
              <label style={{display:"flex",alignItems:"center",gap:6,fontSize:12,cursor:"pointer"}}>
                <input type="checkbox" checked={f.websiteFeedEnabled} onChange={e=>setF(x=>({...x, websiteFeedEnabled: e.target.checked}))} style={{accentColor:"var(--accent)"}}/>
                Фид включён
              </label>
            </div>
            <div className="field" style={{marginBottom:14}}>
              <label>URL сайта магазина</label>
              <input placeholder="https://mobilaks.ru" value={f.websiteUrl} onChange={e=>setF(x=>({...x, websiteUrl: e.target.value}))}/>
            </div>
            {current?.id && f.websiteFeedEnabled && (
              <div style={{display:"flex",alignItems:"center",gap:10,padding:"10px 14px",background:"var(--bg3)",borderRadius:8,border:"1px solid var(--border)",marginBottom:14}}>
                <span style={{fontSize:12,color:"var(--muted)",flexShrink:0}}>Фид:</span>
                <code style={{fontSize:11,color:"var(--accent)",flex:1,wordBreak:"break-all",cursor:"pointer"}} onClick={()=>copyText(location.origin+"/api/avito/website-feed/"+current.id+".json")} title="Нажмите, чтобы скопировать">{location.origin}/api/avito/website-feed/{current.id}.json</code>
                <a href={"/api/avito/website-feed/"+current.id+".json"} target="_blank" rel="noopener" className="btn btn-sm btn-outline" style={{flexShrink:0}}>Открыть</a>
              </div>
            )}
            {!f.websiteFeedEnabled && current?.id && (
              <div style={{fontSize:12,color:"var(--muted)"}}> Включите фид и сохраните, чтобы получить ссылку.</div>
            )}
            <button type="button" className="btn btn-primary" onClick={save} style={{marginTop:4}} disabled={!current}>
              {saved ? "Сохранено" : "Сохранить"}
            </button>
          </div>}
        </div>

        {/* ── Статистика ────────────────────────────────── */}
        <div className="panel" style={{marginTop:16}}>
          <SectionHead id="stats" title={`Статистика — ${selName || "—"}`}/>
          {openSections.has("stats") && <div className="pb2">
            <div style={{fontSize:12,color:"var(--muted)",marginBottom:14,lineHeight:1.6}}>
              Сбор просмотров, контактов и избранного по объявлениям с Авито. Запускается автоматически каждые 60 минут (AVITO_STATS_INTERVAL_MINUTES).
              Данные отображаются в карточке товара.
            </div>
            <button type="button" className="btn btn-sm btn-avito" onClick={fetchStats} disabled={statsBusy || !current?.avito_configured}>
              {statsBusy ? <><span className="spinner"/> Сбор…</> : "Собрать статистику сейчас"}
            </button>
            {statsMsg && <div style={{marginTop:8,fontSize:12,color: statsMsg.startsWith("Ошибка") || statsMsg.startsWith("Не") ? "var(--danger)" : "var(--accent)"}}>{statsMsg}</div>}
            {!current?.avito_configured && <div style={{marginTop:8,fontSize:11,color:"var(--muted)"}}>Требуется подключение Avito API</div>}
          </div>}
        </div>

        {/* ── Сообщения ─────────────────────────────────── */}
        <div className="panel" style={{marginTop:16}}>
          <SectionHead id="messages" title={`Сообщения — ${selName || "—"}`}/>
          {openSections.has("messages") && <div className="pb2">
            <div style={{fontSize:12,color:"var(--muted)",marginBottom:14,lineHeight:1.6}}>
              Загрузка входящих и исходящих сообщений из мессенджера Авито. При подключённом вебхуке — мгновенно, иначе каждые 5 минут (AVITO_MESSENGER_INTERVAL_MINUTES).
            </div>
            <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"center"}}>
              <button type="button" className="btn btn-sm btn-avito" onClick={checkFeed} disabled={feedBusy || !current?.avito_configured}>
                {feedBusy ? <><span className="spinner"/> Проверка…</> : "Проверить автозагрузку"}
              </button>
              <button type="button" className="btn btn-sm btn-primary" onClick={registerWebhook} disabled={webhookBusy || !current?.avito_configured}>
                {webhookBusy ? <><span className="spinner"/> Регистрация…</> : "Зарегистрировать вебхук"}
              </button>
            </div>
            {feedMsg && <div style={{marginTop:8,fontSize:12,color: feedMsg.startsWith("Ошибка") || feedMsg.startsWith("Не") ? "var(--danger)" : "var(--accent)"}}>{feedMsg}</div>}
            {webhookMsg && <div style={{marginTop:8,fontSize:12,color: webhookMsg.startsWith("Ошибка") || webhookMsg.startsWith("Не") ? "var(--danger)" : "var(--accent)",wordBreak:"break-all"}}>{webhookMsg}</div>}
            {!current?.avito_configured && <div style={{marginTop:8,fontSize:11,color:"var(--muted)"}}>Требуется подключение Avito API</div>}
          </div>}
        </div>

        {/* ── Автозагрузка ──────────────────────────────── */}
        <div className="panel" style={{marginTop:16}}>
          <SectionHead id="autoload" title={`Автозагрузка — ${selName || "—"}`}/>
          {openSections.has("autoload") && <div className="pb2">
            <div style={{fontSize:12,color:"var(--muted)",marginBottom:14,lineHeight:1.6}}>
              Проверка отчётов автозагрузки фида и сопоставление avito_item_id к товарам по IMEI или модели + памяти + состоянию.
              Запускается автоматически каждые 120 минут (AVITO_FEED_CHECK_INTERVAL_MINUTES).
            </div>
            <button type="button" className="btn btn-sm btn-avito" onClick={importItems} disabled={importBusy || !current?.avito_configured}>
              {importBusy ? <><span className="spinner"/> Импорт…</> : "Импортировать объявления"}
            </button>
            {importMsg && <div style={{marginTop:8,fontSize:12,color: importMsg.startsWith("Ошибка") || importMsg.startsWith("Не") ? "var(--danger)" : "var(--accent)"}}>{importMsg}</div>}
            {!current?.avito_configured && <div style={{marginTop:8,fontSize:11,color:"var(--muted)"}}>Требуется подключение Avito API</div>}
          </div>}
        </div>

      </div>
    </>
  );
}

// ─── АВИТО (мои объявления) ───────────────────────────────────────────────────
function AvitoPage({ user, token, activeStore, onOpenProduct }) {
  const [items, setItems] = useState([]);
  const [stores, setStores] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const isAdm = Access.isAdmin(user);
  const isInfo = Access.isInfo(user);
  const forcedStore = isAdm ? "" : (user.store_name || "");
  const [storeF, setStoreF] = useState(isAdm ? (activeStore || "") : forcedStore);
  // Messages section
  const [msgs, setMsgs] = useState([]);
  const [msgsLoading, setMsgsLoading] = useState(false);
  const [msgsErr, setMsgsErr] = useState("");
  const [msgsStoreId, setMsgsStoreId] = useState("");

  useEffect(() => { if (isAdm) setStoreF(activeStore || ""); }, [activeStore, isAdm]);

  // Load stores for feed URLs
  useEffect(() => {
    (async () => {
      try {
        const data = await apiFetch("/stores/", { token });
        setStores(data.items || []);
      } catch {}
    })();
  }, [token]);

  const reload = async () => {
    setLoading(true); setErr("");
    try {
      const store = isAdm ? storeF : forcedStore;
      const params = new URLSearchParams({ size: "20000", avito_published: "true", is_new: "false" });
      if (store) params.set("store", store);
      const data = await apiFetch(`/products/?${params}`, { token });
      setItems(data.items || []);
    } catch (e) {
      setErr(e.message || "Ошибка загрузки");
    }
    setLoading(false);
  };

  useEffect(() => { reload(); }, [token, storeF, forcedStore, isAdm]);

  const toggleAvito = async (p) => {
    setErr("");
    setItems(xs => xs.filter(x => x.id !== p.id));
    try {
      await apiFetch(`/products/${p.id}`, { token, method: "PATCH", json: { avito_published: false } });
    } catch (e) {
      setItems(xs => [...xs, p]);
      setErr(e.message || "Не удалось снять с Авито");
    }
  };

  const bulkPublish = async () => {
    setBulkBusy(true); setErr("");
    try {
      const store = isAdm ? storeF : forcedStore;
      const params = new URLSearchParams();
      if (store) params.set("store", store);
      const data = await apiFetch(`/products/bulk-avito-publish?${params}`, { token, method: "POST" });
      if (data.published > 0) await reload();
      else setErr("Нет подходящих товаров для публикации (нужны: б/у, с фото, с ценой, не проданные)");
    } catch (e) {
      setErr(e.message || "Ошибка массовой публикации");
    }
    setBulkBusy(false);
  };

  const storeMap = {};
  stores.forEach(s => { storeMap[s.name] = s; });

  // Keep msgsStoreId in sync with store filter
  useEffect(() => {
    if (storeF) {
      const s = storeMap[storeF];
      if (s?.avito_configured) setMsgsStoreId(s.id);
    } else {
      // default to first avito-configured store
      const first = stores.find(s => s.avito_configured);
      if (first && !msgsStoreId) setMsgsStoreId(first.id);
    }
  }, [storeF, stores]);

  useEffect(() => {
    if (!msgsStoreId) return;
    let c = true;
    setMsgsLoading(true); setMsgsErr("");
    apiFetch(`/avito/messages/${msgsStoreId}?limit=100`, { token })
      .then(d => { if (c) setMsgs(d.items || []); })
      .catch(e => { if (c) setMsgsErr(e.message || "Ошибка загрузки сообщений"); })
      .finally(() => { if (c) setMsgsLoading(false); });
    return () => { c = false; };
  }, [msgsStoreId, token]);

  const byStore = {};
  items.forEach(p => {
    const s = p.store_name || "—";
    if (!byStore[s]) byStore[s] = [];
    byStore[s].push(p);
  });

  const noPhoto = items.filter(p => !p.photos_count).length;
  const noPrice = items.filter(p => !p.price_retail).length;

  return (
    <>
      <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:12,flexWrap:"wrap"}}>
        {isAdm && (
          <>
            <label style={{fontSize:11,color:"var(--muted)"}}>Магазин</label>
            <select className="store-sel" style={{maxWidth:240}} value={storeF} onChange={e=>setStoreF(e.target.value)}>
              <option value="">Все магазины</option>
              {STORES.map(s=><option key={s}>{s}</option>)}
            </select>
          </>
        )}
        {!isInfo && (
          <button type="button" className="btn btn-sm btn-avito" onClick={bulkPublish} disabled={bulkBusy}>
            {bulkBusy ? "Публикация…" : "Опубликовать все с фото"}
          </button>
        )}
      </div>
      <div className="stats-bar">
        <div className="sc"><div className="sc-label">На Авито</div><div className="sc-val" style={{color:"var(--accent)"}}>{items.length}</div></div>
        <div className="sc"><div className="sc-label">Магазинов</div><div className="sc-val">{Object.keys(byStore).length}</div></div>
        <div className="sc"><div className="sc-label">Сумма розница</div><div className="sc-val" style={{color:"var(--success)"}}>{(items.reduce((s,p)=>s+(p.price_retail||0),0)/1000).toFixed(0)}К ₽</div></div>
        {noPhoto > 0 && <div className="sc"><div className="sc-label">Без фото</div><div className="sc-val" style={{color:"var(--danger)"}}>{noPhoto}</div></div>}
        {noPrice > 0 && <div className="sc"><div className="sc-label">Без цены</div><div className="sc-val" style={{color:"var(--danger)"}}>{noPrice}</div></div>}
      </div>
      {err && <div className="err" style={{marginBottom:10}}>{err}</div>}
      {loading && <div style={{marginBottom:10,color:"var(--muted)"}}><span className="spinner"/> Загрузка…</div>}
      {!loading && items.length === 0 && <div style={{color:"var(--muted)",fontSize:13,padding:"20px 0"}}>Нет активных объявлений на Авито. Нажмите «Опубликовать все с фото» или включите товары вручную в каталоге.</div>}
      {Object.entries(byStore).map(([store, prods]) => {
        const si = storeMap[store];
        const feedUrl = si ? `/api/avito/feed/${si.id}.xml` : null;
        return (
        <div key={store} style={{marginBottom:20}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:8,flexWrap:"wrap"}}>
            <span className="sdot" style={{background:STORE_COLORS[store]||"#64748b"}}/>
            <span style={{fontWeight:600,fontSize:14}}>{store}</span>
            <span style={{fontSize:11,color:"var(--muted)"}}>— {prods.length} шт.</span>
            {feedUrl && <code style={{fontSize:10,color:"var(--accent)",marginLeft:"auto",cursor:"pointer"}} onClick={()=>{copyText(location.origin+feedUrl)}} title="Нажмите, чтобы скопировать ссылку на фид">{location.origin}{feedUrl}</code>}
          </div>
          {si && !si.avito_address && <div style={{fontSize:11,color:"var(--danger)",marginBottom:6}}>Не заполнен адрес магазина в настройках — объявления будут без адреса</div>}
          <div className="tw">
            <table className="pt">
              <thead><tr>
                {!isInfo && <th className="pt-thumb-cell">Фото</th>}
                <th>Модель</th><th>Заголовок Авито</th><th>Состояние</th><th>IMEI</th>
                <th style={{textAlign:"right"}}>Цена</th>
                <th style={{textAlign:"center"}}>Фото</th>
                <th style={{textAlign:"center"}}>Действия</th>
              </tr></thead>
              <tbody>
                {prods.map(p => {
                  const row = { ...p, retail: p.price_retail, cost: p.price_cost, repair: p.in_repair, sold: p.is_sold };
                  const own = Access.canEdit(user, row);
                  return (
                    <tr key={p.id} className={[p.in_repair?"rep":"", !p.photos_count?"":"", !p.price_retail?"":""].filter(Boolean).join(" ")}>
                      {!isInfo && (
                      <td className="pt-thumb-cell">
                        {p.thumbnail_url ? <img className="pt-thumb" src={p.thumbnail_url} alt="" loading="lazy"/> : <span style={{display:"block",width:40,height:40}}/>}
                      </td>
                      )}
                      <td>
                        <div className="tm" onClick={()=>onOpenProduct(p.id)}>{p.model}{p.storage?" "+p.storage:""}</div>
                        {p.color && <div className="ts">{p.color}{p.battery_pct?" · АКБ "+p.battery_pct:""}</div>}
                      </td>
                      <td style={{fontSize:12,maxWidth:200,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{p.avito_title || defaultAvitoTitle(p)}</td>
                      <td><Chip condition={p.condition} repair={p.in_repair} sold={p.is_sold}/></td>
                      <td><span className="mono" style={{cursor:"pointer"}} title="Нажмите, чтобы скопировать" onClick={()=>copyText(p.imei)}>{p.imei}</span></td>
                      <td className="tr">{fmt(p.price_retail)}{!p.price_retail && <span style={{color:"var(--danger)"}} title="Нет цены — объявление будет без цены"> ⚠</span>}</td>
                      <td style={{textAlign:"center"}}>{p.photos_count > 0 ? <span style={{color:"var(--success)"}}>📷 {p.photos_count}</span> : <span style={{color:"var(--danger)"}} title="Нет фото — объявление не попадёт в фид">⚠ 0</span>}</td>
                      <td style={{textAlign:"center",whiteSpace:"nowrap"}}>
                        <button type="button" className="btn btn-sm btn-outline" onClick={()=>onOpenProduct(p.id)}>Карточка</button>
                        {own && <>{" "}<button type="button" className="btn btn-sm btn-avito-off" onClick={()=>toggleAvito(p)}>Снять</button></>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        );
      })}

      {/* ── Сообщения Авито ─────────────────────────────── */}
      <div style={{marginTop:28}}>
        <div style={{fontWeight:600,fontSize:14,marginBottom:12,color:"var(--text)"}}>Сообщения Авито</div>

        {/* Store picker shown only when no store filter or no avito-configured store auto-selected */}
        {isAdm && stores.filter(s=>s.avito_configured).length > 1 && (
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:12}}>
            <label style={{fontSize:11,color:"var(--muted)"}}>Магазин</label>
            <select className="store-sel" style={{maxWidth:220}} value={msgsStoreId} onChange={e=>setMsgsStoreId(e.target.value)}>
              <option value="">— выберите —</option>
              {stores.filter(s=>s.avito_configured).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
        )}

        {!msgsStoreId && <div style={{fontSize:12,color:"var(--muted)"}}>Нет магазина с подключённым Avito API</div>}
        {msgsLoading && <div style={{fontSize:12,color:"var(--muted)"}}><span className="spinner"/> Загрузка сообщений…</div>}
        {msgsErr && <div className="err" style={{marginBottom:8}}>{msgsErr}</div>}

        {!msgsLoading && msgsStoreId && msgs.length === 0 && !msgsErr && (
          <div style={{fontSize:12,color:"var(--muted)"}}>Нет сообщений</div>
        )}

        {msgs.length > 0 && (
          <div style={{display:"flex",flexDirection:"column",gap:6,maxHeight:480,overflowY:"auto",padding:"4px 0"}}>
            {[...msgs].reverse().map(m => {
              const out = m.direction === "outgoing";
              const dt = new Date(m.created_at);
              const dtStr = dt.toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
              return (
                <div key={m.id} style={{display:"flex",justifyContent: out ? "flex-end" : "flex-start"}}>
                  <div style={{
                    maxWidth:"70%",padding:"8px 12px",borderRadius: out ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
                    background: out ? "rgba(16,185,129,.15)" : "var(--bg3)",
                    border: `1px solid ${out ? "rgba(16,185,129,.25)" : "var(--border)"}`,
                  }}>
                    <div style={{fontSize:13,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{m.content || <span style={{color:"var(--muted)",fontStyle:"italic"}}>медиа</span>}</div>
                    <div style={{fontSize:10,color:"var(--muted)",marginTop:4,textAlign: out ? "right" : "left"}}>{dtStr} · {out ? "исх." : "вх."}</div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

// ─── ДОКУМЕНТЫ ЗАКУПКИ (реестр) ───────────────────────────────────────────────
function PurchaseDocsPage({ token, user, activeStore, onOpenProduct }) {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [pg, setPg] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [storeF, setStoreF] = useState(activeStore || "");
  const isAdm = Access.isAdmin(user);
  const PS = 30;

  useEffect(() => { setStoreF(activeStore || ""); setPg(0); }, [activeStore]);

  useEffect(() => {
    let c = true;
    (async () => {
      setLoading(true); setErr("");
      try {
        const params = new URLSearchParams({ page: String(pg + 1), size: String(PS) });
        if (isAdm && storeF) params.set("store", storeF);
        const data = await apiFetch(`/purchase-docs/registry?${params}`, { token });
        if (!c) return;
        setItems(data.items || []);
        setTotal(data.total ?? 0);
      } catch (e) {
        if (c) setErr(e.message);
      }
      if (c) setLoading(false);
    })();
    return () => { c = false; };
  }, [token, pg, storeF, isAdm]);

  const downloadDoc = async (docId, fname) => {
    try {
      const r = await fetch(`${API_BASE}/purchase-docs/${docId}/file`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка скачивания");
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fname || "document";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e.message || "Ошибка");
    }
  };

  const pages = Math.max(1, Math.ceil(total / PS));

  return (
    <>
      {isAdm && (
        <div style={{marginBottom:12}}>
          <label style={{fontSize:11,color:"var(--muted)",marginRight:8}}>Магазин</label>
          <select className="store-sel" style={{maxWidth:240}} value={storeF} onChange={(e)=>{ setStoreF(e.target.value); setPg(0); }}>
            <option value="">Все магазины</option>
            {STORES.map((s)=><option key={s}>{s}</option>)}
          </select>
        </div>
      )}
      <div className="banner ban-ro" style={{marginBottom:12}}>
        <span>📄</span>
        <span>Реестр документов закупки (чеки, договоры, сканы). Файлы хранятся только на сервере, в папках по IMEI/S/N; в объявления не передаются.</span>
      </div>
      {err && <div className="err" style={{marginBottom:10}}>{err}</div>}
      {loading && <div style={{marginBottom:10,color:"var(--muted)"}}><span className="spinner"/> Загрузка…</div>}
      <div className="tw">
        <table className="pt">
          <thead><tr>
            <th>Магазин</th><th>IMEI / S/N</th><th>Модель</th><th>Тип</th><th>Клиент</th><th>Дата</th><th style={{textAlign:"center"}}>Действия</th>
          </tr></thead>
          <tbody>
            {items.map((x)=>(
              <tr key={x.id}>
                <td>{x.store_name}</td>
                <td style={{fontFamily:"var(--mono)",fontSize:11}}>{x.imei}</td>
                <td>{x.model}</td>
                <td>{x.doc_type_label}</td>
                <td>{x.supplier_name || "—"}</td>
                <td style={{fontSize:11,color:"var(--muted)"}}>{(x.created_at || "").slice(0, 10)}</td>
                <td style={{textAlign:"center",whiteSpace:"nowrap"}}>
                  <button type="button" className="btn btn-outline btn-sm" onClick={()=>downloadDoc(x.id, x.filename)}>Скачать</button>
                  {" "}
                  <button type="button" className="btn btn-outline btn-sm" onClick={()=>onOpenProduct(x.product_id)}>Товар</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total === 0 && !loading && <div style={{color:"var(--muted)",fontSize:13}}>Нет записей</div>}
      {total > PS && (
        <div style={{display:"flex",gap:8,marginTop:12,alignItems:"center"}}>
          <button className="pb-btn" disabled={pg<=0} onClick={()=>setPg((p)=>p-1)}>←</button>
          <span style={{fontSize:12,color:"var(--muted)"}}>Стр. {pg+1} / {pages}</span>
          <button className="pb-btn" disabled={pg>=pages-1} onClick={()=>setPg((p)=>p+1)}>→</button>
        </div>
      )}
    </>
  );
}

// ─── ANALYTICS (свои данные BaseStock; без парсинга Авито) ─────────────────────
function AnalyticsTable({ items, loading, anSortCol, anSortDir, setAnSortCol, setAnSortDir, token, includeSold, user, onOpenProduct }) {
  const [expanded, setExpanded] = useState({});
  const [details, setDetails] = useState({});
  const [detailLoading, setDetailLoading] = useState({});

  const toggle = async (key, model, storage, brand) => {
    const isOpen = expanded[key];
    setExpanded(prev => ({ ...prev, [key]: !isOpen }));
    if (!isOpen && !details[key]) {
      setDetailLoading(prev => ({ ...prev, [key]: true }));
      try {
        const params = new URLSearchParams();
        params.set("q", model);
        if (storage) params.set("storage", storage);
        if (!includeSold) params.set("in_stock", "true");
        params.set("is_new", "false");
        params.set("limit", "200");
        const data = await apiFetch(`/products/?${params.toString()}`, { token });
        const excluded = ["Новый","Требуется ремонт","Ремонт","Залог"];
        const filtered = (data.items || []).filter(p =>
          p.model === model && (p.storage || "") === (storage || "")
          && !p.in_repair
          && !excluded.includes(p.condition)
        );
        setDetails(prev => ({ ...prev, [key]: filtered }));
      } catch { setDetails(prev => ({ ...prev, [key]: [] })); }
      setDetailLoading(prev => ({ ...prev, [key]: false }));
    }
  };

  const grouped = useMemo(() => {
    const map = {};
    for (const row of items) {
      const key = `${row.brand||""}|${row.model}|${row.storage||""}`;
      if (!map[key]) map[key] = { brand: row.brand, model: row.model, storage: row.storage, totalCount: 0, sumRetail: 0, sumCost: 0, costCount: 0, competitor: null };
      const g = map[key];
      g.totalCount += row.count;
      g.sumRetail += (row.avg_retail || 0) * row.count;
      if (row.avg_cost) { g.sumCost += row.avg_cost * row.count; g.costCount += row.count; }
      if (row.competitor && !g.competitor) g.competitor = row.competitor;
    }
    return Object.entries(map).map(([key, g]) => {
      const avg_retail = g.totalCount ? g.sumRetail / g.totalCount : 0;
      const avg_cost = g.costCount ? g.sumCost / g.costCount : 0;
      const comp_price = g.competitor?.price_excellent || 0;
      // Средняя рынок: среднее между нашей закупкой и ценой конкурента
      let market_avg = 0;
      if (avg_cost && comp_price) market_avg = (avg_cost + comp_price) / 2;
      else if (comp_price) market_avg = comp_price;
      else if (avg_cost) market_avg = avg_cost;
      return {
        key, brand: g.brand, model: g.model, storage: g.storage,
        avg_retail, avg_cost, count: g.totalCount,
        competitor: g.competitor, comp_price, market_avg,
      };
    });
  }, [items]);

  const sorted = useMemo(() => {
    const d = anSortDir === "asc" ? 1 : -1;
    return [...grouped].sort((a, b) => {
      switch (anSortCol) {
        case "brand": return d * (a.brand || "").localeCompare(b.brand || "", "ru");
        case "model": return d * (a.model || "").localeCompare(b.model || "", "ru");
        case "storage": return d * (a.storage || "").localeCompare(b.storage || "", "ru");
        case "avg": return d * (a.avg_retail - b.avg_retail);
        case "cost": return d * ((a.avg_cost||0) - (b.avg_cost||0));
        case "comp": return d * ((a.comp_price||0) - (b.comp_price||0));
        case "market": return d * ((a.market_avg||0) - (b.market_avg||0));
        case "count": return d * (a.count - b.count);
        default: return 0;
      }
    });
  }, [grouped, anSortCol, anSortDir]);

  const toggleSort = (col) => { anSortCol === col ? setAnSortDir(d => d === "asc" ? "desc" : "asc") : (setAnSortCol(col), setAnSortDir("asc")); };
  const arrow = (col) => anSortCol === col ? (anSortDir === "asc" ? " ▲" : " ▼") : "";
  const thS = { cursor: "pointer", userSelect: "none" };
  const subStyle = { background: "rgba(255,255,255,.02)", fontSize: 11 };
  const cols = 8;

  return (
    <div className="tw">
      <table className="pt">
        <thead><tr>
          {[["brand","Бренд"],["model","Модель"],["storage","Память"]].map(([k,l])=>(
            <th key={k} style={thS} onClick={()=>toggleSort(k)}>{l}{arrow(k)}</th>
          ))}
          <th style={{...thS,textAlign:"right"}} onClick={()=>toggleSort("avg")}>Наша розница{arrow("avg")}</th>
          <th style={{...thS,textAlign:"right"}} onClick={()=>toggleSort("cost")}>Наша закупка{arrow("cost")}</th>
          <th style={{...thS,textAlign:"right"}} onClick={()=>toggleSort("comp")}>Конкурент{arrow("comp")}</th>
          <th style={{...thS,textAlign:"right"}} onClick={()=>toggleSort("market")}>Средняя рынок{arrow("market")}</th>
          <th style={{...thS,textAlign:"center"}} onClick={()=>toggleSort("count")}>Шт.{arrow("count")}</th>
        </tr></thead>
        <tbody>
          {sorted.map(g => {
            const isOpen = expanded[g.key];
            const rows = details[g.key] || [];
            const isLoading = detailLoading[g.key];
            const cp = g.competitor;
            const compPrice = cp?.price_excellent;
            return (
              <React.Fragment key={g.key}>
                <tr style={{cursor:"pointer"}} onClick={()=>toggle(g.key, g.model, g.storage, g.brand)}>
                  <td style={{fontSize:11,color:"var(--muted)"}}>{g.brand || "—"}</td>
                  <td style={{fontWeight:600}}><span style={{marginRight:6,fontSize:10,color:"var(--muted)"}}>{isOpen?"▼":"▶"}</span>{g.model}</td>
                  <td className="mono">{g.storage || "—"}</td>
                  <td style={{textAlign:"right",fontFamily:"var(--mono)",color:"var(--success)"}}>{fmt(Math.round(g.avg_retail))}</td>
                  <td style={{textAlign:"right",fontFamily:"var(--mono)",color:g.avg_cost?"var(--warn)":"var(--muted)"}}>{g.avg_cost?fmt(Math.round(g.avg_cost)):"—"}</td>
                  <td style={{textAlign:"right",fontFamily:"var(--mono)",color:compPrice?"var(--cyan)":"var(--muted)"}}>{compPrice?fmt(compPrice):"—"}</td>
                  <td style={{textAlign:"right",fontFamily:"var(--mono)",fontWeight:600,color:g.market_avg?"var(--accent2)":"var(--muted)"}}>{g.market_avg?fmt(Math.round(g.market_avg)):"—"}</td>
                  <td style={{textAlign:"center",fontFamily:"var(--mono)"}}>{g.count}</td>
                </tr>
                {isOpen && isLoading && (
                  <tr style={subStyle}><td colSpan={cols} style={{paddingLeft:32,color:"var(--muted)"}}><span className="spinner" style={{width:12,height:12}}/> Загрузка…</td></tr>
                )}
                {isOpen && !isLoading && rows.map((p, ci) => {
                  const cond = (p.condition||"").toLowerCase();
                  const compForCond = cp
                    ? (cond.includes("отличн") || cond.includes("новый") ? cp.price_excellent
                      : cond.includes("хорош") ? cp.price_good
                      : cond.includes("плох") || cond.includes("удовл") ? cp.price_poor
                      : cp.price_good)
                    : null;
                  return (
                  <tr key={ci} style={{...subStyle,background:`${STORE_COLORS[p.store_name]||"transparent"}08`}}>
                    <td style={{paddingLeft:24}}><span style={{display:"inline-block",padding:"3px 10px",borderRadius:6,fontSize:10,fontWeight:600,color:"#fff",background:STORE_GRADIENTS[p.store_name]||"var(--bg4)",boxShadow:STORE_GRADIENTS[p.store_name]?`0 2px 8px ${STORE_COLORS[p.store_name]||"transparent"}40`:""}}>{p.store_name||"—"}</span></td>
                    <td style={{color:"var(--muted)"}}>{p.condition || "—"}</td>
                    <td className="mono">{Access.isAdmin(user) && p.id && onOpenProduct ? <button className="imei-btn" onClick={e=>{e.stopPropagation();onOpenProduct(p.id);}}>{p.imei||"—"}</button> : (p.imei||"—")}</td>
                    <td style={{textAlign:"right",fontFamily:"var(--mono)",color:"var(--success)"}}>{fmt(p.price_retail)}</td>
                    <td style={{textAlign:"right",fontFamily:"var(--mono)",color:"var(--warn)"}}>{fmt(p.price_cost)}</td>
                    <td style={{textAlign:"right",fontFamily:"var(--mono)",color:compForCond?"var(--cyan)":"var(--muted)"}}>{compForCond?fmt(compForCond):"—"}</td>
                    <td style={{textAlign:"right",fontFamily:"var(--mono)",color:compForCond&&p.price_cost?"var(--accent2)":"var(--muted)"}}>{compForCond&&p.price_cost?fmt(Math.round((p.price_cost+compForCond)/2)):"—"}</td>
                    <td/>
                  </tr>
                  );
                })}
                {isOpen && !isLoading && rows.length === 0 && !cp && (
                  <tr style={subStyle}><td colSpan={cols} style={{paddingLeft:32,color:"var(--muted)"}}>Нет товаров</td></tr>
                )}
              </React.Fragment>
            );
          })}
          {!loading && sorted.length === 0 && (
            <tr><td colSpan={cols} style={{textAlign:"center",padding:"28px",color:"var(--muted)"}}>Нет данных для выбранных фильтров</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function AnalyticsPage({ user, token, activeStore, onOpenProduct }) {
  const [q,setQ]=useState(""); const [submittedQ,setSubmittedQ]=useState("");
  const submitSearch = () => setSubmittedQ(q);
  const [brand,setBrand]=useState("");
  const [cond,setCond]=useState("");
  const [includeSold,setIncludeSold]=useState(false);
  const [soldFrom,setSoldFrom]=useState("");
  const [soldTo,setSoldTo]=useState("");
  const [minUnits,setMinUnits]=useState(1);
  const [items,setItems]=useState([]);
  const [loading,setLoading]=useState(true);
  const [err,setErr]=useState("");
  const [anSortCol,setAnSortCol]=useState("model");
  const [anSortDir,setAnSortDir]=useState("asc");
  const storeF = Access.seesAllStores(user) ? activeStore : user.store_name;

  useEffect(()=>{
    let c = true;
    (async ()=>{
      setLoading(true); setErr("");
      try {
        const params = new URLSearchParams();
        if (submittedQ.trim()) params.set("q", submittedQ.trim());
        if (brand.trim()) params.set("brand", brand.trim());
        if (cond) params.set("condition", cond);
        if (includeSold) params.set("include_sold", "true");
        if (includeSold && soldFrom) params.set("sold_from", soldFrom);
        if (includeSold && soldTo) params.set("sold_to", soldTo);
        if (minUnits > 1) params.set("min_units", String(minUnits));
        if (storeF) params.set("store", storeF);
        params.set("is_new", "false");
        const data = await apiFetch(`/analytics/price-aggregates?${params.toString()}`, { token });
        if (!c) return;
        setItems(data.items || []);
      } catch (e) {
        if (c) setErr(e.message || "Ошибка загрузки");
      }
      if (c) setLoading(false);
    })();
    return ()=>{ c = false; };
  }, [token, submittedQ, brand, cond, includeSold, soldFrom, soldTo, minUnits, storeF, user.role]);

  return (
    <>
      {err && <div className="err" style={{marginBottom:10}}>{err}</div>}
      {loading && <div style={{marginBottom:10,color:"var(--muted)"}}><span className="spinner"/> Загрузка…</div>}

      <div className="filters">
        <div style={{position:"relative",display:"flex",alignItems:"center",flex:1,minWidth:200}}>
          <input className="fi" placeholder="Поиск по модели…" value={q} onChange={e=>setQ(e.target.value)} onKeyDown={e=>e.key==="Enter"&&submitSearch()} style={{paddingRight: q ? 28 : undefined}}/>
          {q && <button onClick={()=>{setQ("");setSubmittedQ("");}} style={{position:"absolute",right:8,background:"none",border:"none",color:"var(--text)",cursor:"pointer",fontSize:18,lineHeight:1,padding:"0 2px",zIndex:1,opacity:.6}} title="Очистить">×</button>}
        </div>
        <input className="fi" style={{maxWidth:160}} placeholder="Бренд (точно)" value={brand} onChange={e=>setBrand(e.target.value)}/>
        <select className="fs" value={cond} onChange={e=>setCond(e.target.value)}>
          <option value="">Любое состояние</option>
          {["Отличное","Как новый","Хорошее","Среднее","Плохое"].map(c=><option key={c}>{c}</option>)}
        </select>
        <label style={{display:"flex",alignItems:"center",gap:5,fontSize:11,color:"var(--muted)",cursor:"pointer",whiteSpace:"nowrap"}}>
          <input type="checkbox" checked={includeSold} onChange={e=>{setIncludeSold(e.target.checked);if(!e.target.checked){setSoldFrom("");setSoldTo("");}}} style={{accentColor:"var(--accent)"}}/>
          С проданными
        </label>
        {includeSold && <>
          <label style={{display:"flex",alignItems:"center",gap:4,fontSize:11,color:"var(--muted)",whiteSpace:"nowrap"}}>
            с <input type="date" className="fi" style={{maxWidth:140,fontSize:11,padding:"6px 8px"}} value={soldFrom} onChange={e=>setSoldFrom(e.target.value)}/>
          </label>
          <label style={{display:"flex",alignItems:"center",gap:4,fontSize:11,color:"var(--muted)",whiteSpace:"nowrap"}}>
            по <input type="date" className="fi" style={{maxWidth:140,fontSize:11,padding:"6px 8px"}} value={soldTo} onChange={e=>setSoldTo(e.target.value)}/>
          </label>
        </>}
        <label style={{display:"flex",alignItems:"center",gap:6,fontSize:11,color:"var(--muted)",whiteSpace:"nowrap"}}>
          Мин. шт. в группе
          <input type="number" min={1} max={100} value={minUnits} onChange={e=>setMinUnits(Math.min(100, Math.max(1, parseInt(e.target.value,10)||1)))} style={{width:52,padding:4,background:"var(--bg3)",border:"1px solid var(--border)",borderRadius:6,color:"var(--text)",fontSize:11}}/>
        </label>
        <span className="fc">{items.length} групп</span>
      </div>

      <AnalyticsTable items={items} loading={loading} anSortCol={anSortCol} anSortDir={anSortDir} setAnSortCol={setAnSortCol} setAnSortDir={setAnSortDir} token={token} includeSold={includeSold} user={user} onOpenProduct={onOpenProduct}/>
    </>
  );
}

const ROLE_OPTIONS = [
  { v: "admin", label: "Администратор" },
  { v: "staff", label: "Сотрудник" },
  { v: "info", label: "Инфо (витрина по всем магазинам, без учёта и медиа)" },
];

function storeOptionLabel(s) {
  if (!s?.name) return "";
  return s.is_active === false ? `${s.name} (неактивен)` : s.name;
}

function UsersPage({ token, currentUserId }) {
  const [items, setItems] = useState([]);
  const [stores, setStores] = useState([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({
    username: "",
    password: "",
    full_name: "",
    role: "staff",
    store_id: "",
  });
  const [pwdForm, setPwdForm] = useState({ new_password: "" });

  const load = async () => {
    setLoading(true);
    setErr("");
    try {
      const [u, s] = await Promise.all([
        apiFetch("/users/", { token }),
        apiFetch("/stores/?include_inactive=true", { token }),
      ]);
      setItems(u.items || []);
      setStores(s.items || []);
    } catch (e) {
      setErr(e.message || "Ошибка загрузки");
    }
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, [token]);

  /** Пока список магазинов не подгрузился, в форме был пустой store_id — подставляем первый магазин. */
  useEffect(() => {
    if (modal !== "create" || !stores.length) return;
    setForm((f) => {
      if (f.role !== "staff") return f;
      if (f.store_id) return f;
      return { ...f, store_id: stores[0].id };
    });
  }, [modal, stores]);

  const openCreate = () => {
    setForm({
      username: "",
      password: "",
      full_name: "",
      role: "staff",
      store_id: stores[0]?.id || "",
    });
    setModal("create");
  };

  const submitCreate = async (e) => {
    e.preventDefault();
    setErr("");
    try {
      const body = {
        username: form.username.trim(),
        password: form.password,
        full_name: form.full_name.trim() || null,
        role: form.role,
        store_id: form.role === "admin" || form.role === "info" ? null : form.store_id || null,
      };
      if (form.role === "staff" && !body.store_id) {
        setErr("Выберите магазин");
        return;
      }
      await apiFetch("/users/", { token, method: "POST", json: body });
      setModal(null);
      await load();
    } catch (e) {
      setErr(e.message || "Ошибка");
    }
  };

  const patchUser = async (id, patch) => {
    setErr("");
    try {
      await apiFetch(`/users/${id}`, { token, method: "PATCH", json: patch });
      await load();
    } catch (e) {
      setErr(e.message || "Ошибка");
    }
  };

  const submitPwd = async (e, userId) => {
    e.preventDefault();
    if (pwdForm.new_password.length < 8) {
      setErr("Пароль не менее 8 символов");
      return;
    }
    setErr("");
    try {
      await apiFetch(`/users/${userId}/password`, {
        token,
        method: "POST",
        json: { new_password: pwdForm.new_password },
      });
      setModal(null);
      setPwdForm({ new_password: "" });
      await load();
    } catch (e) {
      setErr(e.message || "Ошибка");
    }
  };

  const removeUser = async (id, username) => {
    if (!window.confirm(`Удалить пользователя «${username}»?`)) return;
    setErr("");
    try {
      await apiFetch(`/users/${id}`, { token, method: "DELETE" });
      await load();
    } catch (e) {
      setErr(e.message || "Ошибка");
    }
  };

  return (
    <>
      {err && <div className="err" style={{ marginBottom: 10 }}>{err}</div>}
      <div className="banner ban-admin" style={{ marginBottom: 14 }}>
        <span>👥</span>
        <span>
          <strong>Пользователи</strong> — создание, роли, привязка к магазину (только сотрудники), сброс пароля. Роль «Инфо»: без привязки к точке, просмотр каталога по всем магазинам без учётной и медиа.
        </span>
      </div>
      <div style={{ marginBottom: 12 }}>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={loading}
          title={loading ? "Загрузка списка магазинов…" : undefined}
          onClick={openCreate}
        >
          + Новый пользователь
        </button>
      </div>
      {loading && (
        <div style={{ color: "var(--muted)" }}>
          <span className="spinner" /> Загрузка…
        </div>
      )}
      <div className="tw">
        <table className="pt">
          <thead>
            <tr>
              <th>Логин</th>
              <th>Имя</th>
              <th>Роль</th>
              <th>Магазин</th>
              <th style={{ textAlign: "center" }}>Активен</th>
              <th style={{ textAlign: "center" }}>Действия</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr
                key={row.id}
                style={
                  currentUserId && row.id === currentUserId
                    ? { background: "rgba(79,142,247,.1)", boxShadow: "inset 0 0 0 1px rgba(79,142,247,.35)" }
                    : undefined
                }
              >
                <td className="mono" style={{ fontSize: 12 }}>
                  <input
                    className="fi"
                    style={{ width: 120, padding: "2px 6px", fontSize: 12, fontFamily: "var(--mono)" }}
                    defaultValue={row.username}
                    onBlur={(e) => {
                      const v = e.target.value.trim();
                      if (v && v !== row.username) patchUser(row.id, { username: v });
                      else e.target.value = row.username;
                    }}
                    onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
                  />
                  {currentUserId && row.id === currentUserId && (
                    <span style={{ marginLeft: 6, fontSize: 10, color: "var(--accent)" }}>(вы)</span>
                  )}
                  {row.must_change_password && (
                    <span style={{ marginLeft: 6, fontSize: 10, color: "var(--warn)" }}>смена пароля</span>
                  )}
                </td>
                <td>
                  <input
                    className="fi"
                    style={{ width: 140, padding: "2px 6px", fontSize: 12 }}
                    defaultValue={row.full_name || ""}
                    placeholder="Имя"
                    onBlur={(e) => {
                      const v = e.target.value.trim();
                      if (v !== (row.full_name || "")) patchUser(row.id, { full_name: v || null });
                      else e.target.value = row.full_name || "";
                    }}
                    onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
                  />
                </td>
                <td>
                  <select
                    className="fs"
                    style={{ minWidth: 140 }}
                    value={row.role}
                    onChange={(e) => {
                      const newRole = e.target.value;
                      if (newRole === "admin" || newRole === "info") {
                        patchUser(row.id, { role: newRole });
                      } else {
                        const sid = row.store_id || stores[0]?.id || null;
                        if (!sid) {
                          setErr("Нет магазинов — выберите магазин после создания.");
                          return;
                        }
                        patchUser(row.id, { role: newRole, store_id: sid });
                      }
                    }}
                  >
                    {ROLE_OPTIONS.map((o) => (
                      <option key={o.v} value={o.v}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {row.role === "admin" || row.role === "info" ? (
                    <span style={{ color: "var(--muted)" }}>—</span>
                  ) : (
                    <select
                      className="fs"
                      style={{ minWidth: 120 }}
                      value={row.store_id || ""}
                      onChange={(e) => patchUser(row.id, { store_id: e.target.value || null })}
                    >
                      <option value="">—</option>
                      {stores.map((s) => (
                        <option key={s.id} value={s.id}>
                          {storeOptionLabel(s)}
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                <td style={{ textAlign: "center" }}>
                  <input
                    type="checkbox"
                    checked={row.is_active}
                    onChange={(e) => patchUser(row.id, { is_active: e.target.checked })}
                  />
                </td>
                <td style={{ textAlign: "center", whiteSpace: "nowrap" }}>
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => {
                      setPwdForm({ new_password: "" });
                      setModal({ pwd: row });
                    }}
                  >
                    Пароль
                  </button>{" "}
                  <button type="button" className="btn-ghost" style={{ color: "var(--danger)" }} onClick={() => removeUser(row.id, row.username)}>
                    Удалить
                  </button>
                </td>
              </tr>
            ))}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", padding: 24, color: "var(--muted)" }}>
                  Нет пользователей
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {modal === "create" && (
        <div className="cpww" style={{ zIndex: 50 }}>
          <div className="cpwb" style={{ maxWidth: 420 }}>
            <div className="ltitle" style={{ marginBottom: 12 }}>
              Новый пользователь
            </div>
            <form onSubmit={submitCreate}>
              <div className="field">
                <label>Логин</label>
                <input
                  value={form.username}
                  onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                  required
                />
              </div>
              <div className="field">
                <label>Пароль (мин. 8)</label>
                <input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                  required
                  minLength={8}
                />
              </div>
              <div className="field">
                <label>Имя</label>
                <input
                  value={form.full_name}
                  onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))}
                />
              </div>
              <div className="field">
                <label>Роль</label>
                <select
                  className="fs"
                  value={form.role}
                  onChange={(e) => {
                    const role = e.target.value;
                    setForm((f) => ({
                      ...f,
                      role,
                      store_id: role === "admin" || role === "info" ? "" : f.store_id || stores[0]?.id || "",
                    }));
                  }}
                >
                  {ROLE_OPTIONS.map((o) => (
                    <option key={o.v} value={o.v}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>Магазин {form.role === "staff" ? "(обязательно)" : "(не используется)"}</label>
                {form.role === "admin" || form.role === "info" ? (
                  <div style={{ fontSize: 13, color: "var(--muted)", padding: "8px 0" }}>
                    {form.role === "admin"
                      ? "Администратор не привязан к магазину и видит все данные."
                      : "Инфо — без привязки к магазину: просмотр каталога по всем точкам без учётной цены и медиа."}
                  </div>
                ) : stores.length === 0 ? (
                  <div style={{ fontSize: 13, color: "var(--warn)" }}>
                    В базе нет ни одного магазина. Добавьте точки через выгрузку 1С на странице «Настройки» или проверьте таблицу <code style={{ fontSize: 11 }}>business.stores</code>.
                  </div>
                ) : (
                  <select
                    className="fs"
                    value={form.store_id}
                    onChange={(e) => setForm((f) => ({ ...f, store_id: e.target.value }))}
                    required
                  >
                    <option value="">Выберите магазин</option>
                    {stores.map((s) => (
                      <option key={s.id} value={s.id}>
                        {storeOptionLabel(s)}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                <button type="submit" className="btn btn-primary">
                  Создать
                </button>
                <button type="button" className="btn btn-outline" onClick={() => setModal(null)}>
                  Отмена
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {modal?.pwd && (
        <div className="cpww" style={{ zIndex: 50 }}>
          <div className="cpwb" style={{ maxWidth: 380 }}>
            <div className="ltitle" style={{ marginBottom: 12 }}>
              Новый пароль: {modal.pwd.username}
            </div>
            <form onSubmit={(e) => submitPwd(e, modal.pwd.id)}>
              <div className="field">
                <label>Новый пароль</label>
                <input
                  type="password"
                  value={pwdForm.new_password}
                  onChange={(e) => setPwdForm({ new_password: e.target.value })}
                  minLength={8}
                  required
                />
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                <button type="submit" className="btn btn-primary">
                  Сохранить
                </button>
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => {
                    setModal(null);
                    setPwdForm({ new_password: "" });
                  }}
                >
                  Отмена
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

// ─── LOGS ─────────────────────────────────────────────────────────────────────

function LogsPage({ token }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    let c = true;
    (async () => {
      setLoading(true);
      try {
        const data = await apiFetch(`/logs/activity?limit=200&log_type=${filter}`, { token });
        if (c) setItems(data.items || []);
      } catch {}
      if (c) setLoading(false);
    })();
    return () => { c = false; };
  }, [token, filter]);

  const typeLabel = { import: "Импорт", doc_access: "Документы", avito: "Авито" };
  const typeColor = { import: "var(--accent2)", doc_access: "var(--cyan)", avito: "var(--warn)" };
  const statusColor = (s) => s === "success" || s === "ok" || s === "published" ? "var(--success)" : s === "error" ? "var(--danger)" : "var(--muted)";

  const fmtDate = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("ru") + " " + d.toLocaleTimeString("ru", { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <>
      <div className="filters" style={{marginBottom:14}}>
        {[["all","Все"],["import","Импорт"],["avito","Авито"],["docs","Документы"]].map(([v,l]) => (
          <button key={v} className={`btn btn-sm ${filter===v?"btn-primary":"btn-outline"}`} onClick={()=>setFilter(v)}>{l}</button>
        ))}
        <span className="fc">{items.length} записей</span>
      </div>

      {loading && <div style={{color:"var(--muted)"}}><span className="spinner"/> Загрузка…</div>}

      <div className="tw">
        <table className="pt">
          <thead><tr>
            <th>Дата</th>
            <th>Тип</th>
            <th>Пользователь</th>
            <th>Магазин</th>
            <th>Статус</th>
            <th>Детали</th>
          </tr></thead>
          <tbody>
            {items.map((log, i) => (
              <tr key={log.id + "-" + i}>
                <td style={{whiteSpace:"nowrap",fontFamily:"var(--mono)",fontSize:11,color:"var(--muted)"}}>{fmtDate(log.timestamp)}</td>
                <td><span className="chip" style={{background:"rgba(255,255,255,.05)",color:typeColor[log.type]||"var(--muted)",border:"1px solid " + (typeColor[log.type]||"var(--border)")}}>{typeLabel[log.type] || log.type}</span></td>
                <td style={{fontSize:12}}>{log.user_name || log.user}</td>
                <td style={{fontSize:11,color:"var(--muted)"}}>{log.store || "—"}</td>
                <td><span style={{color:statusColor(log.status),fontSize:11,fontWeight:600}}>{log.status}</span></td>
                <td style={{fontSize:11,maxWidth:400}}>
                  {log.details}
                  {log.error && <div style={{color:"var(--danger)",marginTop:3,fontSize:10}}>⚠ {log.error}</div>}
                </td>
              </tr>
            ))}
            {!loading && items.length === 0 && (
              <tr><td colSpan="6" style={{textAlign:"center",padding:"28px",color:"var(--muted)"}}>Нет записей</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ─── COMPETITOR PRICES ────────────────────────────────────────────────────────
function CompetitorPricesPage({ user, token }) {
  const [items,setItems]=useState([]);
  const [brands,setBrands]=useState([]);
  const [sources,setSources]=useState([]);
  const [lastParsed,setLastParsed]=useState(null);
  const [total,setTotal]=useState(0);
  const [loading,setLoading]=useState(true);
  const [err,setErr]=useState("");
  const [q,setQ]=useState(""); const [submittedQ,setSubmittedQ]=useState("");
  const [brand,setBrand]=useState("");
  const [source,setSource]=useState("");
  const [parsing,setParsing]=useState(false);
  const submitSearch = () => setSubmittedQ(q);
  const [sortCol,setSortCol]=useState("brand");
  const [sortDir,setSortDir]=useState("asc");
  const isAdm=Access.isAdmin(user);

  const load = async () => {
    setLoading(true); setErr("");
    try {
      const params = new URLSearchParams();
      if (submittedQ.trim()) params.set("q", submittedQ.trim());
      if (brand) params.set("brand", brand);
      if (source) params.set("source", source);
      params.set("limit","2000");
      const data = await apiFetch(`/competitor-prices/?${params}`,{token});
      setItems(data.items||[]);
      setBrands(data.brands||[]);
      setSources(data.sources||[]);
      setLastParsed(data.last_parsed);
      setTotal(data.total||0);
    } catch(e){ setErr(e.message||"Ошибка"); }
    setLoading(false);
  };

  useEffect(()=>{ load(); },[token,submittedQ,brand,source]);

  const startParse = async (src) => {
    setParsing(true);
    try {
      await apiFetch(`/competitor-prices/parse?source=${src}`,{token,method:"POST"});
      setTimeout(()=>{ load(); setParsing(false); }, 3000);
    } catch(e){ setErr(e.message); setParsing(false); }
  };

  const sorted = useMemo(()=>{
    const arr=[...items];
    const d = sortDir === "asc" ? 1 : -1;
    arr.sort((a,b)=>{
      const va=a[sortCol], vb=b[sortCol];
      if(typeof va==="number"&&typeof vb==="number") return d*(va-vb);
      return d*String(va||"").localeCompare(String(vb||""),"ru");
    });
    return arr;
  },[items,sortCol,sortDir]);

  const toggleSort=(col)=>{
    if(sortCol===col) setSortDir(d=>d==="asc"?"desc":"asc");
    else { setSortCol(col); setSortDir("asc"); }
  };
  const arrow=(col)=>sortCol===col?(sortDir==="asc"?" ▲":" ▼"):"";
  const thS={cursor:"pointer",userSelect:"none"};

  const sourceLabel = (s) => ({ goodcom: "GoodCom" }[s] || s);
  const sourceFull = (s) => ({ goodcom: "GoodCom (Хорошая Связь)" }[s] || s);

  const stats = useMemo(()=>{
    if(!items.length) return null;
    const bs = {};
    items.forEach(r=>{ bs[r.brand]=(bs[r.brand]||0)+1; });
    return { brands: Object.keys(bs).length, total: items.length };
  },[items]);

  return (
    <>
      {err && <div className="err" style={{marginBottom:10}}>{err}</div>}

      {stats && (
        <div style={{display:"flex",gap:12,marginBottom:14,flexWrap:"wrap"}}>
          <div className="sc"><div className="sc-label">Позиций</div><div className="sc-val" style={{color:"var(--accent)"}}>{stats.total}</div></div>
          <div className="sc"><div className="sc-label">Брендов</div><div className="sc-val">{stats.brands}</div></div>
          <div className="sc"><div className="sc-label">Источников</div><div className="sc-val">{sources.length}</div></div>
          {lastParsed && <div className="sc"><div className="sc-label">Обновлено</div><div className="sc-val" style={{fontSize:12}}>{new Date(lastParsed).toLocaleDateString("ru",{day:"numeric",month:"short",year:"numeric"})}</div></div>}
        </div>
      )}

      <div className="filters">
        <div style={{position:"relative",display:"flex",alignItems:"center",flex:1,minWidth:200}}>
          <input className="fi" placeholder="Поиск по модели…" value={q} onChange={e=>setQ(e.target.value)} onKeyDown={e=>e.key==="Enter"&&submitSearch()} style={{paddingRight: q ? 28 : undefined}}/>
          {q && <button onClick={()=>{setQ("");setSubmittedQ("");}} style={{position:"absolute",right:8,background:"none",border:"none",color:"var(--text)",cursor:"pointer",fontSize:18,lineHeight:1,padding:"0 2px",zIndex:1,opacity:.6}} title="Очистить">×</button>}
        </div>
        <select className="fs" value={brand} onChange={e=>setBrand(e.target.value)}>
          <option value="">Все бренды</option>
          {brands.map(b=><option key={b} value={b}>{b}</option>)}
        </select>
        <select className="fs" value={source} onChange={e=>setSource(e.target.value)}>
          <option value="">Все источники</option>
          {sources.map(s=><option key={s} value={s}>{sourceFull(s)}</option>)}
        </select>
        {isAdm && (
          <button className="pb-btn" disabled={parsing} onClick={()=>startParse("goodcom")} style={{marginLeft:"auto",whiteSpace:"nowrap"}}>
            {parsing ? <><span className="spinner" style={{width:12,height:12,marginRight:6}}/>Парсинг…</> : "⟳ Обновить GoodCom"}
          </button>
        )}
      </div>

      {loading && <div style={{color:"var(--muted)",marginBottom:10}}><span className="spinner"/> Загрузка…</div>}

      <div className="tw" style={{overflowX:"auto"}}>
        <table className="pt">
          <thead>
            <tr>
              <th style={{...thS,width:90}} onClick={()=>toggleSort("source")}>Источник{arrow("source")}</th>
              <th style={{...thS,width:90}} onClick={()=>toggleSort("brand")}>Бренд{arrow("brand")}</th>
              <th style={thS} onClick={()=>toggleSort("model")}>Модель{arrow("model")}</th>
              <th style={{width:80}}>Память</th>
              <th style={{...thS,textAlign:"right",width:100}} onClick={()=>toggleSort("price_excellent")}>Отличное{arrow("price_excellent")}</th>
              <th style={{...thS,textAlign:"right",width:100}} onClick={()=>toggleSort("price_good")}>Хорошее{arrow("price_good")}</th>
              <th style={{...thS,textAlign:"right",width:100}} onClick={()=>toggleSort("price_poor")}>Плохое{arrow("price_poor")}</th>
              <th style={{...thS,textAlign:"right",width:100}} onClick={()=>toggleSort("price_repair")}>Ремонт{arrow("price_repair")}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(r=>(
              <tr key={r.id}>
                <td><span style={{display:"inline-block",padding:"2px 8px",borderRadius:4,fontSize:9,fontWeight:600,letterSpacing:".3px",textTransform:"uppercase",background:"rgba(16,185,129,.1)",color:"var(--accent2)",border:"1px solid rgba(16,185,129,.2)"}}>{sourceLabel(r.source)}</span></td>
                <td style={{fontWeight:600,whiteSpace:"nowrap"}}>{r.brand}</td>
                <td>{r.model}</td>
                <td style={{fontFamily:"var(--mono)",fontSize:11,color:"var(--muted)"}}>{r.memory||"—"}</td>
                <td style={{textAlign:"right",fontFamily:"var(--mono)",color:"var(--success)",fontWeight:600}}>{r.price_excellent?fmt(r.price_excellent):"—"}</td>
                <td style={{textAlign:"right",fontFamily:"var(--mono)",color:"var(--accent2)"}}>{r.price_good?fmt(r.price_good):"—"}</td>
                <td style={{textAlign:"right",fontFamily:"var(--mono)",color:"var(--warn)"}}>{r.price_poor?fmt(r.price_poor):"—"}</td>
                <td style={{textAlign:"right",fontFamily:"var(--mono)",color:"var(--muted)"}}>{r.price_repair?fmt(r.price_repair):"—"}</td>
              </tr>
            ))}
            {!loading && items.length===0 && (
              <tr><td colSpan="8" style={{textAlign:"center",padding:28,color:"var(--muted)"}}>
                Нет данных.{isAdm ? " Нажмите «Обновить GoodCom» для первого парсинга." : ""}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ─── SHELL ────────────────────────────────────────────────────────────────────
function Shell({ user, token, onLogout, onRefreshUser }) {
  const [page,setPage]=useState(()=>sessionStorage.getItem("pb_page")||"products");
  const [activeStore,setActiveStore]=useState(()=>user.role==="staff"?(user.store_name||""):"");
  const [openCard,setOpenCard]=useState(null);
  const [accountOpen,setAccountOpen]=useState(false);
  const [sidebarOpen,setSidebarOpen]=useState(false);
  const [sidebarCollapsed,setSidebarCollapsed]=useState(()=>localStorage.getItem("pb_sidebar_collapsed")==="1");
  const isAdm = Access.isAdmin(user);
  const seesAll = Access.seesAllStores(user);
  const storeLabel = seesAll ? (activeStore || "Все магазины") : user.store_name;
  const roleShort =
    user.role === "admin"
      ? "Администратор"
      : user.role === "info"
        ? "Инфо"
        : "Сотрудник";
  const openProduct = (id) => { setPage("products"); setOpenCard(id); };
  const openNewProduct = (id) => { setPage("new-products"); setOpenCard(id); };
  const nav = [
    { id: "products", icon: <Icon.box/>, label: "Б/У Товары" },
    { id: "new-products", icon: <Icon.plus/>, label: "Новые товары" },
    ...(isAdm ? [{ id: "sold", icon: <Icon.check/>, label: "Продано" }] : []),
    { divider: true },
    ...(!Access.isInfo(user) ? [{ id: "avito", icon: <Icon.mega/>, label: "Авито" }] : []),
    { id: "analytics", icon: <Icon.chart/>, label: "Аналитика цен" },
    { id: "competitor-prices", icon: <Icon.competitors/>, label: "Цены конкурентов" },
    ...(isAdm
      ? [
          { divider: true },
          { id: "users", icon: <Icon.users/>, label: "Пользователи" },
          { id: "logs", icon: <Icon.logs/>, label: "Логи" },
          { id: "store-settings", icon: <Icon.gear/>, label: "Настройки" },
        ]
      : []),
  ];
  const titles = {
    products: openCard ? "Карточка товара" : "Б/У Товары",
    "new-products": openCard ? "Карточка товара" : "Новые товары",
    sold: openCard ? "Карточка товара" : "Продано",
    avito: "Авито — мои объявления",
    analytics: "Аналитика цен",
    "competitor-prices": "Цены конкурентов",
    users: "Пользователи",
    logs: "Логи",
    "store-settings": "Настройки магазина",
  };
  const goNav=(id)=>{setPage(id);sessionStorage.setItem("pb_page",id);setOpenCard(null);setSidebarOpen(false);};
  return (
    <div className="shell">
      <div className={`sidebar-overlay${sidebarOpen?" open":""}`} onClick={()=>setSidebarOpen(false)}/>
      <div className={`sidebar${sidebarOpen?" open":""}${sidebarCollapsed?" collapsed":""}`}>
        <div className="sb-logo">
          <div className="logo-icon" style={{width:44,height:44,borderRadius:14}}><Icon.logo/></div>
          <LogoWordmark compact />
        </div>
        <div className="sb-nav">
          <div className="sb-sec"><span/><button className="sb-collapse-btn" onClick={()=>setSidebarCollapsed(v=>{localStorage.setItem("pb_sidebar_collapsed",v?"0":"1");return !v;})} title={sidebarCollapsed?"Развернуть":"Свернуть"}>{sidebarCollapsed?"▶":"◀"}</button></div>
          {nav.map((n,i)=>n.divider?<div key={"d"+i} className="nav-divider"/>:<button key={n.id} className={`nav-item${page===n.id?" active":""}`} onClick={()=>goNav(n.id)} title={n.label}><span className="nav-icon">{n.icon}</span><span className="nav-label">{n.label}</span></button>)}
        </div>
        <div className="sb-user">
          <div className={`av ${isAdm?"av-admin":"av-staff"}`} style={{cursor:"pointer"}} title="Личный кабинет" onClick={() => setAccountOpen(true)}>{ini(user.full_name)}</div>
          <div className="sb-info" style={{cursor:"pointer"}} title="Личный кабинет" onClick={() => setAccountOpen(true)}>
            <div className="sb-name">{user.full_name || user.username}</div>
            <div className="sb-role">{roleShort}{user.store_name?` · ${user.store_name}`:""}</div>
          </div>
          <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
            <button type="button" className="btn-ghost" title="Выйти" onClick={onLogout}>
              ⏏
            </button>
          </div>
        </div>
      </div>
      {accountOpen && (
        <AccountModal
          user={user}
          token={token}
          onClose={() => setAccountOpen(false)}
          onSaved={() => {
            onRefreshUser?.();
          }}
        />
      )}
      <div className="main">
        <div className="topbar">
          <button className="hamburger" onClick={()=>setSidebarOpen(o=>!o)}>☰</button>
          <div className="topbar-title">{titles[page]}</div>
          {seesAll ? (
            <select className="topbar-store-sel" value={activeStore} onChange={e=>{setActiveStore(e.target.value);setOpenCard(null);}}>
              <option value="">Все магазины</option>
              {STORES.map(s=><option key={s}>{s}</option>)}
            </select>
          ) : (
            <span className="badge b-store">{storeLabel}</span>
          )}
          <span className={`badge topbar-role-badge ${isAdm?"b-admin":"b-staff"}`} style={{cursor:"pointer"}} onClick={()=>setAccountOpen(true)} title="Личный кабинет">{roleShort}</span>
        </div>
        <div className="content">
          {page==="products"&&!openCard&&<ProductsPage user={user} token={token} activeStore={activeStore} onOpen={(id)=>setOpenCard(id)} onActiveStoreChange={seesAll ? setActiveStore : undefined} isNew={false}/>}
          {page==="products"&&openCard&&<ProductCard productId={openCard} token={token} user={user} onBack={()=>setOpenCard(null)}/>}
          {page==="new-products"&&!openCard&&<ProductsPage user={user} token={token} activeStore={activeStore} onOpen={(id)=>setOpenCard(id)} onActiveStoreChange={seesAll ? setActiveStore : undefined} isNew={true}/>}
          {page==="new-products"&&openCard&&<ProductCard productId={openCard} token={token} user={user} onBack={()=>setOpenCard(null)}/>}
          {page==="sold"&&!openCard&&<ProductsPage user={user} token={token} activeStore={activeStore} onOpen={(id)=>setOpenCard(id)} onActiveStoreChange={seesAll ? setActiveStore : undefined} isNew={false} soldOnly={true}/>}
          {page==="sold"&&openCard&&<ProductCard productId={openCard} token={token} user={user} onBack={()=>setOpenCard(null)}/>}
          {page==="avito"&&<AvitoPage user={user} token={token} activeStore={activeStore} onOpenProduct={openProduct}/>}
          {page==="analytics"&&<AnalyticsPage user={user} token={token} activeStore={activeStore} onOpenProduct={openProduct}/>}
          {page==="competitor-prices"&&<CompetitorPricesPage user={user} token={token}/>}
          {page==="users"&&isAdm&&<UsersPage token={token} currentUserId={user.id} />}
          {page==="logs"&&isAdm&&<LogsPage token={token}/>}
          {page==="store-settings"&&isAdm&&<StoreSettingsPage token={token} activeStore={activeStore}/>}
        </div>
      </div>
    </div>
  );
}

// ─── ROOT ─────────────────────────────────────────────────────────────────────
export default function App() {
  const [session,setSession]     = useState(Session.get);
  const [mustChange,setMustChange] = useState(false);

  // Роль и права в сессии берутся только из ответа логина; без этого после смены роли в БД
  // пункт «Пользователи» и др. не появляются, пока не выйти и не войти снова.
  useEffect(() => {
    if (!session?.token) return;
    let cancelled = false;
    (async () => {
      try {
        const me = await apiFetch("/auth/me", { token: session.token });
        if (cancelled) return;
        setSession((prev) => {
          if (!prev) return prev;
          const next = { ...prev, user: { ...prev.user, ...me } };
          Session.set(next);
          return next;
        });
      } catch (e) {
        if (e.status === 401 && !cancelled) {
          Session.clear();
          setSession(null);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [session?.token]);

  const login = (user, accessToken, refreshToken, mustChangePassword) => {
    const s = { user, token: accessToken, refresh_token: refreshToken };
    Session.set(s);
    setSession(s);
    setMustChange(!!mustChangePassword);
  };
  const logout = () => { Session.clear(); setSession(null); setMustChange(false); };
  return (
    <>
      <style>{CSS}</style>
      {!session&&<LoginScreen onLogin={login}/>}
      {session&&mustChange&&<ChangePasswordScreen user={session.user} token={session.token} onDone={()=>setMustChange(false)}/>}
      {session&&!mustChange&&(
        <Shell
          user={session.user}
          token={session.token}
          onLogout={logout}
          onRefreshUser={async () => {
            try {
              const me = await apiFetch("/auth/me", { token: session.token });
              setSession((prev) => {
                if (!prev) return prev;
                const next = { ...prev, user: { ...prev.user, ...me } };
                Session.set(next);
                return next;
              });
            } catch (_) {}
          }}
        />
      )}
    </>
  );
}
