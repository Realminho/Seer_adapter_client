"""서버렌더 HTML — f-string + 단일 escape 헬퍼. 외부 자원/JS 없음."""
from __future__ import annotations

import html
import re
import string

from core import configio

# Design system ported from wcs-ui/amr-adaptor-dashboard-redesign.css. Inlined
# (the WebUi serves no external assets) and extended with .err/.ok flash colors
# plus base button/form/pre rules so the existing server-rendered markup adopts
# the same theme without rewriting every form builder.
_CSS = """
:root{
  --bg:#f3f4f6;--surface:#fff;--fg:#1d2433;--muted-surface:#f8fafc;--muted:#64748b;
  --border:#e2e8f0;--accent:#2563eb;--secondary:#475569;--success:#16a34a;
  --warning:#d97706;--danger:#ef4444;--destructive:#dc2626;--neutral:#64748b;
  --accent-soft:#eef4ff;--success-soft:#e9f7ef;--warning-soft:#fdf3e6;
  --danger-soft:#fdecec;--neutral-soft:#eef2f6;--fg-soft:#f1f3f6;
  --hairline:#cbd5e1;
  --font-display:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,sans-serif;
  --font-body:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --radius-sm:4px;--radius-md:6px;--radius-lg:8px;--radius-pill:9999px;--page:24px;
}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;min-width:320px;background:var(--bg);color:var(--fg);font-family:var(--font-body);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
button,input,select,textarea{font:inherit}
button{cursor:pointer}
button:disabled{cursor:not-allowed;opacity:.48}
h1,h2,h3,h4,p{margin:0}
h1,h2,h3,h4{font-family:var(--font-display);color:var(--fg)}
h1{font-size:24px;line-height:1.2;font-weight:600}
h2{font-size:16px;line-height:1.3;font-weight:600}
h3{font-size:14px;line-height:1.3;font-weight:600;margin-top:4px}
h4{font-size:12.5px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-top:6px}
.skip-link{position:absolute;left:12px;top:8px;z-index:20;transform:translateY(-160%);border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);padding:8px 10px}
.skip-link:focus{transform:translateY(0)}
.shell{min-height:100vh;display:flex;flex-direction:column;min-width:0}
.topbar{position:sticky;top:0;z-index:10;border-bottom:1px solid var(--border);background:rgba(243,244,246,.88);backdrop-filter:blur(12px);padding:12px var(--page);display:flex;align-items:center;justify-content:space-between;gap:12px 16px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;min-width:0}
.brand-logo{height:28px;width:auto;display:block;border-radius:var(--radius-sm)}
.brand strong{font-size:14px;white-space:nowrap}
.model-title{font-size:22px;line-height:1.1;font-weight:700;white-space:nowrap;color:var(--fg)}
.topnav{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.nav-link{min-height:32px;border-radius:var(--radius-lg);padding:6px 10px;color:var(--muted);display:inline-flex;align-items:center;gap:6px;font-weight:500}
.nav-link:hover,.nav-link[aria-current=page]{background:var(--surface);color:var(--fg)}
.page{padding:var(--page);display:grid;gap:16px}
.page-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.subtitle{color:var(--muted);margin-top:4px;max-width:76ch}
.head-actions,.footer-links{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.panel,.metric,.command-group,.status-strip,.footerbar{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg)}
.status-strip{padding:10px;display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.metric{min-width:0;padding:10px 12px;display:grid;gap:6px;align-content:start}
.metric-label{display:flex;align-items:center;justify-content:space-between;gap:8px;color:var(--muted);font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase}
.metric-value{min-width:0;font-size:20px;line-height:1.1;font-weight:600;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.metric-value small{color:var(--muted);font-size:13px;font-weight:500}
.metric-foot{color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:8px;min-width:0}
.metric-foot strong{color:var(--fg);font-family:var(--font-mono);font-size:12px}
.workspace{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(360px,.8fr);gap:16px;align-items:start}
.stack,.command-stack{display:grid;gap:16px;min-width:0}
.panel,.command-group{overflow:hidden}
.panel-head,.command-head{padding:12px 14px;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.panel-desc{color:var(--muted);font-size:12.5px;margin-top:2px}
.panel-body,.command-body{padding:14px;display:grid;gap:10px}
.bar{height:7px;border-radius:var(--radius-pill);background:var(--fg-soft);overflow:hidden}
.bar span{display:block;height:100%;width:var(--value);border-radius:inherit;background:var(--success)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:middle}
th{color:var(--muted);font-size:12px;font-weight:500;text-transform:uppercase;background:var(--surface)}
td:first-child{color:var(--muted);white-space:nowrap}
tbody tr:hover,table tr:hover{background:var(--fg-soft)}
.num{font-family:var(--font-mono);font-variant-numeric:tabular-nums}
.status{display:inline-flex;align-items:center;gap:6px;min-height:22px;border-radius:var(--radius-pill);border:1px solid var(--border);padding:2px 8px;background:var(--surface);color:var(--fg);font-size:11.5px;font-weight:600;white-space:nowrap}
.status.success{background:var(--success-soft)}.status.warning{background:var(--warning-soft)}
.status.danger{background:var(--danger-soft)}.status.neutral{background:var(--neutral-soft)}.status.accent{background:var(--accent-soft)}
.status.io-on{background:var(--success);border-color:var(--success);color:#fff;font-weight:800;box-shadow:0 0 0 1px rgba(22,163,74,.22)}
.dot{width:8px;height:8px;border-radius:var(--radius-pill);background:var(--neutral);flex:0 0 auto}
.dot.success{background:var(--success)}.dot.warning{background:var(--warning)}.dot.danger{background:var(--danger)}.dot.accent{background:var(--accent)}.dot.neutral{background:var(--neutral)}
.btn,button{min-height:32px;border-radius:var(--radius-lg);border:1px solid var(--border);padding:0 12px;background:var(--surface);color:var(--fg);display:inline-flex;align-items:center;justify-content:center;gap:6px;font-weight:500;white-space:nowrap;transition:background .16s ease,border-color .16s ease,color .16s ease,transform .08s ease}
.btn:hover,button:hover:not(:disabled){border-color:var(--hairline)}
.btn:active,button:active:not(:disabled){transform:translateY(1px)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.success{background:var(--success);border-color:var(--success);color:#fff}
.btn.warning{background:var(--warning);border-color:var(--warning);color:#fff}
.btn.danger,.btn.ghost{color:var(--muted)}
.btn.danger{background:var(--destructive);border-color:var(--destructive);color:#fff}
.btn.ghost{background:transparent;border-color:transparent}
form.btn{display:inline-flex;align-items:center;gap:6px;margin:2px 4px 2px 0;vertical-align:top;flex-wrap:wrap;min-height:0;border:0;padding:0;background:none;border-radius:0}
form.btn input[type=text]{height:32px;padding:0 10px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);color:var(--fg)}
.footerbar{padding:12px 14px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:12.5px}
/* /config per-field editor */
.cfg-group{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:4px 14px 12px;margin:0 0 14px}
.cfg-group>h3{font-family:var(--font-mono);font-size:13px;color:var(--accent);margin:12px 0 6px}
.cfg-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0;padding:7px 0;border:0;border-top:1px solid var(--fg-soft);border-radius:0;background:none;min-height:0}
.cfg-row+.cfg-row{}
.cfg-group .cfg-row:first-of-type{border-top:0}
.cfg-label{flex:0 0 280px;max-width:100%;color:var(--fg);font-size:13px;display:grid;gap:2px}
.cfg-label-main{display:inline-flex;align-items:baseline;gap:6px;min-width:0}
.cfg-label .muted{font-family:var(--font-mono);font-size:11px}
.cfg-help{color:var(--muted);font-size:12px;line-height:1.35}
.cfg-row input[type=text],.cfg-row input[type=number],.cfg-row select{flex:1 1 180px;min-width:140px;height:32px;padding:0 10px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);color:var(--fg)}
.cfg-row button{flex:0 0 auto}
.cfg-group details{margin:8px 0 2px}
.cfg-group summary{cursor:pointer;padding:4px 0;font-size:12.5px}
.cfg-group details table{width:100%;margin-top:4px}
.cfg-group details code{font-family:var(--font-mono);font-size:12px;color:var(--muted);word-break:break-all}
.cfg-meta{display:flex;align-items:center;gap:6px;flex-wrap:wrap;color:var(--muted);font-size:11.5px;line-height:1.35}
.cfg-source{font-family:var(--font-mono);word-break:break-all}
.run-wrap{display:grid;gap:10px}.run-card{border:1px solid var(--border);border-radius:var(--radius-md);padding:10px 12px;display:grid;gap:8px;background:var(--surface)}.rc-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.rc-head code{font-weight:600}.rc-meta{color:var(--muted);font-size:12px}.ap-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:6px}.ap-row{display:grid;gap:2px;min-width:0}.ap-key{color:var(--muted);font-family:var(--font-mono);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ap-hint{font-family:var(--font-body);font-size:11px}input.ap-key{flex:1 1 0;min-width:0}.ap-val{flex:1 1 0;min-width:0}.run-card button{justify-self:start}.run-card.acted{border-color:var(--primary);box-shadow:0 0 0 2px var(--primary-soft)}.ok,.error-notice{position:sticky;top:56px;z-index:5}.cfg-badge.motion{background:var(--warning-soft);color:var(--warning)}.cfg-badge{display:inline-flex;align-items:center;min-height:20px;border:1px solid var(--border);border-radius:var(--radius-pill);padding:0 7px;background:var(--neutral-soft);color:var(--secondary);font-size:11px;font-weight:600;white-space:nowrap}
.cfg-badge.read-only{background:var(--warning-soft);color:var(--warning)}
.cfg-badge.robot-override{background:var(--accent-soft);color:var(--accent)}
.cfg-badge.jibot-overlay{background:var(--success-soft);color:var(--success)}
.cfg-badge.advanced{background:var(--danger-soft);color:var(--danger)}
.err{color:var(--danger);font-weight:600}
.error-notice{border:1px solid var(--danger);border-radius:var(--radius-md);background:var(--danger-soft);padding:8px 10px;display:grid;gap:2px}
.error-notice .error-title{font-size:12.5px;font-weight:700;color:var(--fg)}
.error-notice .error-description{font-size:12px;font-weight:500;line-height:1.4;color:var(--secondary);overflow-wrap:anywhere}
.ok{color:var(--success);font-weight:600}
pre{background:var(--muted-surface);border:1px solid var(--border);border-radius:var(--radius-md);padding:12px;overflow:auto;font-family:var(--font-mono);font-size:12.5px;line-height:1.5;margin:.4rem 0}
/* live telemetry cards + pose map */
.telemetry-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(122px,1fr));gap:7px}
.telemetry-item{min-width:0;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--muted-surface);padding:6px 8px;display:grid;gap:3px}
.telemetry-item.span2{grid-column:span 2}
.telemetry-item .label{color:var(--muted);font-size:10px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.telemetry-item .value{min-width:0;font-family:var(--font-mono);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.telemetry-item .error-list{display:grid;gap:7px}
.telemetry-item .error-entry{display:grid;gap:2px;min-width:0}
.telemetry-item .error-title{font-family:var(--font-mono);font-size:12px;font-weight:700;color:var(--fg);overflow-wrap:anywhere}
.telemetry-item .error-description{font-size:12px;font-weight:500;line-height:1.4;color:var(--secondary);overflow-wrap:anywhere}
.telemetry-item.warn{background:var(--warning-soft);border-color:var(--warning)}
.telemetry-item.bad{background:var(--danger-soft);border-color:var(--danger)}
.telemetry-item.good{background:var(--success-soft)}
.map-card{border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--surface);min-height:200px;padding:14px;display:grid;grid-template-columns:minmax(0,1fr) 220px;gap:14px}
.pose-map{min-height:180px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--muted-surface);position:relative;overflow:hidden}
.amr-dot{position:absolute;width:18px;height:18px;border-radius:var(--radius-sm);border:2px solid var(--surface);background:var(--accent);transform:translate(-50%,-50%)}
.map-axis{position:absolute;color:var(--muted);font-family:var(--font-mono);font-size:10px}
.map-axis.x{left:8px;bottom:7px}.map-axis.y{right:8px;top:7px}
.pose-detail{display:grid;align-content:start;gap:9px}
.detail-row{display:grid;grid-template-columns:64px minmax(0,1fr);gap:8px;align-items:center;font-size:12.5px}
.detail-row span:first-child{color:var(--muted)}
.detail-row strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--font-mono);font-size:12.5px}
details.extras{border:1px solid var(--border);border-radius:var(--radius-md);padding:0 10px;background:var(--muted-surface)}
details.extras>summary{cursor:pointer;padding:9px 0;color:var(--muted);font-size:12.5px;font-weight:600}
details.extras table{margin-bottom:8px}
/* command forms */
.command-form{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--muted-surface);padding:8px;margin:0}
.command-form.has-fields{grid-template-columns:minmax(120px,.8fr) minmax(150px,1fr) auto}
.command-info{display:grid;gap:2px;min-width:0}
.command-info strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;color:var(--fg)}
.command-info span{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.confirm{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:12px;margin-right:6px;white-space:nowrap}
.confirm input{margin:0}
.command-actions{display:inline-flex;align-items:center;gap:6px;justify-content:flex-end;white-space:nowrap}
.command-fields{display:grid;gap:4px;min-width:0}
.cf-row{display:grid;gap:2px;min-width:0}
.command-fields input{width:100%;height:32px;padding:0 10px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);color:var(--fg)}
/* command items that wrap (Service control / Vehicle actions / emergency) */
.cmd-wrap{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch}
.cmd-wrap form.btn{margin:0}
.cmd-wrap form.btn .btn{min-height:30px;padding:0 10px;font-size:12.5px}
/* label + description + button card, wrapping so many fit per row */
.action-card{display:flex;flex-direction:column;gap:6px;flex:1 1 170px;min-width:150px;max-width:260px;margin:0;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--muted-surface);padding:9px}
.action-card .ac-info{display:grid;gap:3px;min-width:0}
.action-card .ac-info strong{font-size:12.5px;font-weight:600;color:var(--fg)}
.action-card .ac-info span{color:var(--muted);font-size:11.5px;line-height:1.35}
.action-card>.btn{width:100%;margin-top:auto}
/* input cards (Drive move / Sound / Goto / Motion rule / Host): title + desc +
   optional field(s) + button, laid out like the Service/Vehicle/Clamp cards */
.action-card .command-fields{margin:2px 0}
.command-fields select,.command-fields .ap-val{width:100%;min-width:0}
.action-card .ac-actions{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:auto}
.action-card .ac-actions .btn{flex:1 1 auto}
.action-card .ac-actions .confirm{margin:0;flex:0 0 auto}
.action-card.wide{flex:1 1 100%;max-width:none}
.action-card .chip-row{gap:6px}
.motor-card{max-width:none;flex:1 1 240px}
.motor-card .ac-state{font-size:11.5px;color:var(--muted)}
.motor-card .cmd-wrap{margin-top:auto}
.motor-card .cmd-wrap form.btn{flex:1 1 0}
.motor-card .cmd-wrap .btn{width:100%}
.jog-card{flex:1 1 240px;max-width:none}
.jog-head{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.jog-pad{display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:repeat(3,56px);gap:8px;margin:10px 0}
.jog-pad .btn{width:100%;height:100%;min-height:0;font-size:20px;padding:0}
.jog-up{grid-area:1/2/2/3}
.jog-left{grid-area:2/1/3/2}
.jog-stop{grid-area:2/2/3/3;font-size:13px;font-weight:700}
.jog-right{grid-area:2/3/3/4}
.jog-down{grid-area:3/2/4/3}
.jog-btn{touch-action:none;user-select:none;-webkit-user-select:none}
/* emergency / quick-control group: oversized red STOP + adjacent actions */
.command-group.emergency{border-color:var(--danger)}
.command-group.emergency .command-head{background:var(--danger-soft)}
form.estop{margin:0 0 8px;display:block}
.btn.estop{width:100%;min-height:60px;background:var(--destructive);border-color:var(--destructive);color:#fff;font-size:22px;font-weight:800;letter-spacing:.12em;border-radius:var(--radius-md)}
.btn.estop:hover:not(:disabled){border-color:var(--destructive);filter:brightness(.95)}
/* pressed/selected state for sound volume presets (closest to current volume) */
.btn.is-active{background:var(--accent);border-color:var(--accent);color:#fff;box-shadow:inset 0 2px 4px rgba(0,0,0,.25)}
.btn.danger.is-active{background:var(--destructive);border-color:var(--destructive)}
/* compact Sound card: title+transport row, preset chips, inline set-volume row */
.sound-card{gap:8px}
.sound-row{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.sound-transport{display:flex;gap:6px;flex:0 0 auto}
.sound-transport form.btn{margin:0}
.sound-transport .btn{min-height:30px;padding:0 16px;font-size:12.5px}
.sound-card .chip-row{gap:6px}
.sound-set{display:flex;align-items:center;gap:6px;margin:0;border:0;padding:0;background:none;min-height:0}
.sound-set input[type=number]{flex:1 1 auto;min-width:0;height:32px;padding:0 10px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);color:var(--fg)}
.sound-set .btn{flex:0 0 auto}
/* test queue table + filters */
.toolbar{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--muted-surface)}
.chip-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.chip{min-height:28px;border:1px solid var(--border);border-radius:var(--radius-pill);padding:0 10px;background:var(--surface);color:var(--fg);display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:500}
.table-wrap{overflow:auto}
.table-wrap th{position:sticky;top:0}
/* 진행 화면: 오더 액션 체크리스트 + 남은 경로 */
.checklist{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.checklist li{display:grid;grid-template-columns:2.2em 1.3em minmax(0,1fr) auto;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);min-width:0}
.checklist li.is-done{background:var(--muted-surface);border-color:var(--fg-soft)}
.checklist li.is-done .ck-name strong{color:var(--muted);text-decoration:line-through}
.checklist li.is-active{border-color:var(--accent);box-shadow:inset 3px 0 0 var(--accent)}
.checklist li.is-failed{border-color:var(--danger)}
.ck-idx{color:var(--muted);font-family:var(--font-mono);font-size:11.5px;text-align:right}
.ck-mark{font-size:14px;line-height:1;text-align:center}
.ck-mark.done{color:var(--success)}.ck-mark.failed{color:var(--danger)}.ck-mark.active{color:var(--accent)}.ck-mark.wait{color:var(--muted)}
.ck-name{min-width:0;display:grid;gap:2px}
.ck-name strong{font-size:13.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ck-name small{color:var(--muted);font-size:11.5px;font-family:var(--font-mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ck-empty{color:var(--muted);font-size:13px;padding:6px 2px}
button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid rgba(37,99,235,.45);outline-offset:2px}
@media (max-width:1280px){.workspace{grid-template-columns:1fr}}
@media (max-width:920px){:root{--page:14px}.topbar{align-items:flex-start;flex-direction:column}.head-actions{width:100%}}
@media (max-width:560px){.footerbar{display:grid}}
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _csrf_field(csrf: str) -> str:
    return f'<input type="hidden" name="csrf_token" value="{esc(csrf)}">'


def action_anchor(action_type: str) -> str:
    """DOM id for one action's form.

    POST -> 302 -> GET hands the browser a new document, so it scrolls to the
    top and the operator loses their place. Pointing return_to at this id makes
    the browser land back on the control they clicked. The redirect already
    preserves fragments (see _post_action); nothing used them until now.
    """
    return "act-" + re.sub(r"[^A-Za-z0-9_-]", "-", action_type)


def _anchored(return_to: str, action_type: str) -> str:
    """Append this action's anchor to return_to, keeping any existing query."""
    if not return_to or "#" in return_to:
        return return_to
    return f"{return_to}#{action_anchor(action_type)}"


def _return_to_field(return_to: str = "") -> str:
    if not return_to:
        return ""
    return f'<input type="hidden" name="return_to" value="{esc(return_to)}">'


def _nav(current: str = "") -> str:
    links = (
        ("/", "Adapters"),
        ("/actions", "Actions"),
        ("/camera", "Camera"),
        ("/config", "Config"),
        ("/source/recipes.hcl", "Recipes"),
        ("/source/extensions.hcl", "Extensions"),
        ("/robots", "Robots"),
        ("/factsheet", "Factsheet"),
    )
    out = []
    for href, label in links:
        cur = ' aria-current="page"' if href == current else ""
        out.append(f'<a class="nav-link" href="{href}"{cur}>{label}</a>')
    return "".join(out)


def _flash(q: dict) -> str:
    if "err" in q:
        return _error_notice("Error", q["err"])
    if "msg" in q:
        return f'<p class="ok">{esc(q.get("msg") or "ok")}</p>'
    return ""


_VERBS = ("start", "stop", "restart", "enable", "disable")

#: 확인 체크박스를 반드시 거치는 액션. 폼(_run_form)과 서버(_post_action)가 같은
#: 목록을 본다 — 폼에만 두면 POST 를 직접 보내는 것으로 우회된다. pioPing /
#: pioWriteOut / clampMoveTo 는 예전에 각 모듈 panel.html 의 ``required`` 속성으로만
#: 막혀 있었다(클라이언트 전용). 패널이 어댑터 페이지에서 빠지면서 여기로 옮겼다.
CONFIRM_REQUIRED_ACTIONS = (
    "gotoNearestNode",   # 가장 가까운 노드까지 자율 주행
    "localize",          # 현재 위치를 강제로 재설정
    "pioPing",           # 출력 1~8 을 차례로 on/off — 실제 설비 출력이 움직인다
    "pioWriteOut",       # 지정 출력 핀 구동
    "clampMoveTo",       # 엔코더 절대 위치로 클램프 이동
)


def page(
    title: str,
    body: str,
    refresh: int | None = None,
    current: str = "",
    brand_title: str = "AMR Adaptor",
    brand_status: str = "",
    head_actions: str = "",
) -> str:
    """Shell wrapper. brand_title/brand_status/head_actions let a page hoist its
    own title (e.g. the adapter serial), live pills, and action buttons into the
    top AppShell header; they default to the plain global brand on other pages."""
    meta = f'<meta http-equiv="refresh" content="{esc(refresh)}">' if refresh else ""
    actions_html = f'<div class="head-actions">{head_actions}</div>' if head_actions else ""
    return (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">{meta}'
        f"<title>AMR Adaptor — {esc(title)}</title><style>{_CSS}</style></head><body>"
        '<a class="skip-link" href="#content">본문으로 이동</a>'
        '<div class="shell">'
        '<header class="topbar"><div class="brand">'
        '<img class="brand-logo" src="/assets/mw-logo-blue-small.png" alt="MW" '
        'width="59" height="28">'
        f'<strong>{esc(brand_title)}</strong>{brand_status}</div>'
        f'<nav class="topnav" aria-label="adapter navigation">{_nav(current)}</nav>'
        f'{actions_html}</header>'
        f'<main class="page" id="content">{body}</main>'
        "</div></body></html>"
    )


def _refresh_secs(q, default: int = 5) -> int:
    """Poll interval (seconds) from the ?refresh query; default 5, 0 = paused."""
    try:
        return int((q or {}).get("refresh", str(default)))
    except (TypeError, ValueError):
        return default


def _poll_script(secs: int) -> str:
    """Reusable in-place poller for every view page: every N seconds fetch the
    current URL and replace ONLY #content (no full-page reload, no scroll jump,
    no chrome flicker). Registered once on window and survives the innerHTML swap.

    ``window.__amrSetPoll(s)`` changes the interval live (used by the seconds
    input) and syncs ?refresh into the URL so the swapped-in input keeps its
    value. The tick skips swapping while an INPUT/SELECT/TEXTAREA is focused, so
    a poll never wipes in-progress typing. ``secs <= 0`` starts paused (the
    input can re-enable it).
    """
    return (
        "<script>(function(){if(window.__amrPoll)return;window.__amrPoll=true;"
        "window.__amrPollTick=function(){var a=document.activeElement;"
        "if(a&&/^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName))return;"
        "fetch(window.location.href,{credentials:'same-origin'})"
        ".then(function(r){return r.ok?r.text():Promise.reject()})"
        ".then(function(t){var d=new DOMParser().parseFromString(t,'text/html');"
        "var f=d.getElementById('content'),c=document.getElementById('content');"
        "if(f&&c)c.innerHTML=f.innerHTML;}).catch(function(){});};"
        "window.__amrSetPoll=function(s){s=parseInt(s,10);if(isNaN(s)||s<0)s=0;"
        "window.__amrPollSecs=s;"
        "try{var u=new URL(window.location.href);u.searchParams.set('refresh',s);"
        "window.history.replaceState(null,'',u);}catch(e){}"
        "if(window.__amrPollTimer)clearInterval(window.__amrPollTimer);"
        "if(s>0)window.__amrPollTimer=setInterval(window.__amrPollTick,s*1000);};"
        f"window.__amrSetPoll({int(secs)});}})();</script>"
    )


def _poll_controls(secs: int) -> str:
    """Seconds input that sets the live poll interval (0 disables). Lives inside
    #content so it re-renders with the server value after each swap; onchange
    updates the interval live without a reload."""
    return (
        '<div class="poll-controls"><label>자동 갱신 '
        f'<input type="number" min="0" step="1" value="{esc(secs)}" '
        'style="width:4.5em" '
        'onchange="window.__amrSetPoll&amp;&amp;window.__amrSetPoll(this.value)">'
        " 초</label> <small>(0 = 끔)</small></div>"
    )


def _with_poll(body: str, secs: int) -> str:
    """Wrap a view-page body with the interval input (top) + poll script (bottom)
    so the page updates data in place instead of meta-refresh reloading."""
    return _poll_controls(secs) + body + _poll_script(secs)


def adapter_list_page(rows, camera_metrics=None, csrf: str = "", q: dict | None = None,
                      startup_error: str = "") -> str:
    # rows: iterable of (spec, metrics, snapshot|None)
    trs = []
    for spec, m, snap in rows:
        live = "-"
        if snap is not None:
            errc = len(snap.errors or ())
            live = (
                f"{esc(snap.connection_state)} / {esc(snap.operating_mode)} / "
                f"{_num_or_dash(snap.battery_soc)}% / pos ({esc(snap.x)}, {esc(snap.y)}, {esc(snap.theta)}) / err {errc}"
            )
            if _runs_urobot(spec):
                live += f" / {_motor_title(snap)} {esc(_motor_label(snap))}"
        trs.append(
            f"<tr><td><a href=\"/adapter/{esc(spec.key)}\">{esc(spec.display_name)}</a></td>"
            f"<td>{esc(m.active_state)}</td><td>{live}</td></tr>"
        )
    if camera_metrics is not None:
        trs.append(_camera_list_row(camera_metrics))
    table = (
        "<table><tr><th>adapter</th><th>service</th><th>live state</th></tr>"
        + "".join(trs)
        + "</table>"
    )
    # surface the camera service controls on the dashboard too (not just /camera);
    # posting with return_to=dashboard sends the operator back here, not to /camera.
    camera_ctl = ""
    if camera_metrics is not None:
        camera_ctl = _command_group(
            "Camera service control", _camera_verbs(csrf, return_to="dashboard")
        )
    startup_notice = (
        _error_notice("어댑터 기동 오류", startup_error)
        if startup_error
        else ""
    )
    # extension 하드웨어(EZIO/PIO) 상태는 여기 없다. extension 화면(/actions)이
    # discover 된 모듈을 따라 낸다 — 대시보드는 어댑터 목록만 책임진다.
    body = f"{_flash(q or {})}{startup_notice}{table}{camera_ctl}"
    # Live update: poll and swap ONLY #content in place, so the screen does not do
    # a full-page reload (?refresh=N seconds, default 5, 0 pauses).
    return page("adapters", _with_poll(body, _refresh_secs(q)))


def _camera_list_row(m) -> str:
    """Dashboard row for the camera service (web_video_server).

    The camera has no MQTT telemetry, so the live-state column carries the
    systemctl-derived state instead (enabled / uptime / memory / restarts).
    The name links to the existing /camera page.
    """
    live = (
        f"{esc(m.enabled_state)} / up {esc(m.uptime_sec)}s / "
        f"mem {esc(m.memory_bytes)} / restarts {esc(m.restarts)}"
    )
    return (
        '<tr><td><a href="/camera">Camera</a></td>'
        f"<td>{_camera_status(m)}</td><td>{live}</td></tr>"
    )


def _camera_status(metrics) -> str:
    running = str(getattr(metrics, "active_state", "")) == "active"
    return _pill("실행 중" if running else "중지됨", "success" if running else "danger")


def _runs_urobot(spec) -> bool:
    """JIBOT spec(key 'jibot' 또는 'jibot:<robot>')만 vendor urobot 스택을 구동한다."""
    key = getattr(spec, "key", "") or ""
    return key == "jibot" or key.startswith("jibot:")


def _model_header_title(spec) -> str:
    if not _runs_urobot(spec):
        return ""
    serial = str(getattr(spec, "serial", "") or "").strip()
    return f"JIBOT Adaptor - {serial}" if serial else "JIBOT Adaptor"


def _motor_state_known(snapshot) -> bool:
    """어댑터가 발행한 권위 있는 모터 플래그(jibotMotorState)가 있는가."""
    return getattr(snapshot, "motor_state", None) in ("running", "stopped")


def _motor_title(snapshot) -> str:
    """권위 값이 있으면 '모터'(실값), 없으면 '모터(추정)'(eStop 추론)."""
    return "모터" if _motor_state_known(snapshot) else "모터(추정)"


def _motor_label(snapshot) -> str:
    """모터 전원 상태. 어댑터가 실제로 가져온 jibotMotorState(권위)를 우선 쓰고,
    없으면 eStop에서 추론한다.

    JIBOT 어댑터는 모터 전원 off(_motor_flag==0)일 때만 비-NONE eStop을 발행한다
    (v2: AUTOACK, v3: MANUAL). 그래서 NONE만 '정상', 그 외 비-NONE은 모두 '정지'로 본다.
    """
    state = getattr(snapshot, "motor_state", None)
    if state == "stopped":
        return "정지 (비활성)"
    if state == "running":
        return "정상 (활성)"
    raw = getattr(snapshot, "active_emergency_stop", None)
    if raw is None or str(raw) == "":
        return "알 수 없음"
    if str(raw).upper() == "NONE":
        return "정상 (활성)"
    return "정지 (비활성)"


def _tele(label: str, value, cls: str = "") -> str:
    box = "telemetry-item" + (f" {esc(cls)}" if cls else "")
    text = esc(value)
    return (
        f'<div class="{box}">'
        f'<span class="label">{esc(label)}</span>'
        f'<strong class="value" title="{text}">{text}</strong></div>'
    )


def _working_state_kind(ws) -> str:
    """telemetry-item / status colour for an AMR workingState token."""
    w = str(ws or "").upper()
    if w == "ERROR":
        return "bad"
    if w in ("BLOCKED", "PAUSED"):
        return "warn"
    return ""


def _error_list(errors) -> str:
    """Render a VDA5050 error title before its existing description."""
    entries = []
    for error in errors or ():
        if isinstance(error, dict):
            title = error.get("errorType") or "Error"
            description = error.get("errorDescription") or ""
        else:
            title = "Error"
            description = str(error)
        description_html = (
            f'<span class="error-description">{esc(description)}</span>'
            if description
            else ""
        )
        entries.append(
            '<span class="error-entry">'
            f'<strong class="error-title">{esc(title)}</strong>'
            f"{description_html}</span>"
        )
    return '<span class="error-list">' + "".join(entries) + "</span>"


def _error_notice(title, description) -> str:
    """Render non-VDA errors with a visible title before their description."""
    return (
        '<div class="err error-notice" role="alert">'
        f'<strong class="error-title">{esc(title)}</strong>'
        f'<span class="error-description">{esc(description)}</span></div>'
    )


def _mqtt_live_table(spec, snapshot) -> str:
    if spec.monitor_kind == "none" or snapshot is None:
        return ""
    s = snapshot
    err_n = 0 if not s.errors else len(s.errors)
    node_count = len(getattr(s, "node_states", ()) or ())
    edge_count = len(getattr(s, "edge_states", ()) or ())
    action_count = len(getattr(s, "action_states", ()) or ())
    order_val = f"{esc(s.order_id) or '—'} (u{esc(s.order_update_id)})"

    estop_raw = getattr(s, "active_emergency_stop", None)
    estop_bad = str(estop_raw or "").upper() not in ("", "NONE")
    working_state = getattr(s, "working_state", None)
    charging = getattr(s, "charging", None)
    field_violation = getattr(s, "field_violation", None)

    # Every live value is a small card (the old collapsible "전체 telemetry" table
    # is gone). Colour highlights what an operator scans for: working state,
    # emergency stop, field violation, charging, errors.
    cards = [
        _tele("connection", s.connection_state, _conn_kind(s.connection_state) == "danger" and "bad" or ""),
        _tele("mode", s.operating_mode),
        _tele("working state", working_state or "—", _working_state_kind(working_state)),
        _tele("emergency stop", estop_raw if estop_raw not in (None, "") else "NONE",
              "bad" if estop_bad else "good"),
    ]
    if _runs_urobot(spec):
        cards.append(_tele(_motor_title(s), _motor_label(s),
                           "warn" if _motor_state_known(s) and getattr(s, "motor_state", None) == "stopped" else ""))
    cards += [
        _tele("driving", getattr(s, "driving", None)),
        _tele("paused", getattr(s, "paused", None)),
        _tele("field violation", field_violation, "bad" if field_violation else ""),
        _tele("battery", f"{_num_or_dash(s.battery_soc)}%"),
        _tele("charging", charging, "good" if charging else ""),
        _tele("localization", s.localization_score),
        _tele("last node", getattr(s, "last_node_id", None) or "—"),
        _tele("order", order_val),
        _tele("map", getattr(s, "map_id", None) or "—"),
        _tele("pose x", s.x),
        _tele("pose y", s.y),
        _tele("theta", s.theta),
        _tele("nodes/edges/actions", f"{node_count} / {edge_count} / {action_count}"),
        _tele("errors", err_n, "bad" if err_n else "good"),
        _tele("header id", getattr(s, "header_id", None)),
    ]
    if hasattr(s, "adapter_online"):
        online = getattr(s, "adapter_online", False)
        cards.append(_tele("adapter", "online" if online else "offline/stale",
                           "" if online else "bad"))
    if hasattr(s, "acs_broker_connected"):
        acs = getattr(s, "acs_broker_connected", False)
        cards.append(_tele("ACS broker", "connected" if acs else "disconnected",
                           "" if acs else "warn"))
    if hasattr(s, "state_age_sec"):
        age = getattr(s, "state_age_sec", None)
        cards.append(_tele("state age", "-" if age is None else f"{age:.1f}s"))
    if getattr(s, "state_last_error", None):
        cards.append(_tele("state error", getattr(s, "state_last_error"), "bad"))
    if err_n:
        cards.append(
            '<div class="telemetry-item bad span2"><span class="label">errors</span>'
            f"{_error_list(s.errors)}</div>"
        )
    cards.append(_instant_action_cards(s))
    grid = '<h4>live state</h4><div class="telemetry-grid">' + "".join(cards) + "</div>"

    map_card = (
        '<div class="map-card">'
        '<div class="pose-map" role="img" aria-label="AMR pose preview">'
        '<span class="amr-dot" style="left:52%;top:46%"></span>'
        f'<span class="map-axis x">x {esc(s.x)}</span>'
        f'<span class="map-axis y">y {esc(s.y)}</span></div>'
        '<div class="pose-detail"><h3>Pose detail</h3>'
        f'<div class="detail-row"><span>좌표</span><strong>{esc(s.x)}, {esc(s.y)}</strong></div>'
        f'<div class="detail-row"><span>방향</span><strong>{esc(s.theta)} deg</strong></div>'
        f'<div class="detail-row"><span>배터리</span><strong>{_num_or_dash(s.battery_soc)}%</strong></div>'
        f'<div class="detail-row"><span>오류</span><strong>{esc(err_n)}</strong></div>'
        f'<div class="detail-row"><span>노드</span><strong>{esc(getattr(s, "last_node_id", None))}</strong></div>'
        "</div></div>"
    )
    return grid + map_card


#: Most recent instant-action outcome cards to draw in the telemetry panel.
MAX_INSTANT_ACTION_CARDS = 10


def _instant_action_cards(s) -> str:
    """Cards for recent instant-action outcomes (e.g. 'action clamp · FAILED: busy').

    instant action results live in state.instantActionStates (parsed by
    extract_state); the delivered-level command ack does not carry them.
    Terminal states stay there until an order replaces them, so show only the
    most recent ones — the full retained history would bury the telemetry.
    """
    states = getattr(s, "instant_action_states", None) or ()
    cards = ""
    for st in tuple(states)[-MAX_INSTANT_ACTION_CARDS:]:
        if not isinstance(st, dict):
            continue
        label = st.get("actionType", "?")
        status = st.get("actionStatus", "?")
        desc = st.get("resultDescription", "") or ""
        text = f"{status}: {desc}" if desc else status
        cls = "bad" if str(status).upper() in ("FAILED", "FAIL") else ""
        cards += _tele(f"action {label}", text, cls)
    return cards


def _manual_test_block_reason(snapshot) -> str:
    if getattr(snapshot, "adapter_online", True) is False:
        return "requires live adapter state"
    if getattr(snapshot, "state_fresh", True) is False:
        return "requires fresh adapter state"
    # Emergency-stop interlock removed: eStop detection is unreliable in the
    # field and was blocking manual diagnostics. Manual hardware tests now only
    # require a live adapter.
    return ""


def _service_metrics_table(metrics) -> str:
    m = metrics
    return (
        "<table>"
        f"<tr><td>service</td><td>{esc(m.active_state)} ({esc(m.enabled_state)})</td></tr>"
        f"<tr><td>uptime</td><td>{esc(m.uptime_sec)}s</td></tr>"
        f"<tr><td>cpu</td><td>{esc(m.cpu_percent)}%</td></tr>"
        f"<tr><td>mem</td><td>{esc(m.memory_bytes)}</td></tr>"
        f"<tr><td>restarts</td><td>{esc(m.restarts)}</td></tr>"
        "</table>"
    )


def _endpoint(host, port) -> str:
    if host in (None, ""):
        return ""
    if port in (None, "", 0):
        return str(host)
    return f"{host}:{port}"


def _adapter_info_table(spec) -> str:
    return (
        "<h3>adapter info</h3><table>"
        f"<tr><td>unit</td><td>{esc(getattr(spec, 'unit', ''))}</td></tr>"
        f"<tr><td>runtime</td><td>{esc(getattr(spec, 'runtime_kind', ''))}</td></tr>"
        f"<tr><td>exec</td><td>{esc(getattr(spec, 'exec_script', ''))}</td></tr>"
        f"<tr><td>manufacturer</td><td>{esc(getattr(spec, 'manufacturer', ''))}</td></tr>"
        f"<tr><td>serial</td><td>{esc(getattr(spec, 'serial', ''))}</td></tr>"
        f"<tr><td>VDA version</td><td>{esc(getattr(spec, 'vda_full_version', ''))}</td></tr>"
        f"<tr><td>vehicle endpoint</td><td>{esc(_endpoint(getattr(spec, 'vehicle_host', ''), getattr(spec, 'vehicle_port', 0)))}</td></tr>"
        f"<tr><td>MQTT broker</td><td>{esc(_endpoint(getattr(spec, 'mqtt_host', ''), getattr(spec, 'mqtt_port', 0)))}</td></tr>"
        f"<tr><td>MQTT prefix</td><td>{esc(getattr(spec, 'topic_prefix', ''))}</td></tr>"
        "</table>"
    )


def _pill(text, kind: str = "neutral", dot: bool = True) -> str:
    d = f'<span class="dot {esc(kind)}"></span>' if dot else ""
    return f'<span class="status {esc(kind)}">{d}{esc(text)}</span>'


def _conn_kind(state) -> str:
    s = str(state or "").upper()
    if s in ("ONLINE", "CONNECTED"):
        return "success"
    if s in ("", "UNKNOWN"):
        return "neutral"
    return "danger"


def _estop_kind(raw) -> str:
    return "success" if str(raw or "").upper() in ("", "NONE") else "danger"


def _metric(label_html: str, value_html: str, *, foot: str = "", bar=None) -> str:
    if bar is not None:
        extra = f'<div class="bar"><span style="--value: {esc(bar)}%"></span></div>'
    elif foot:
        extra = f'<div class="metric-foot">{foot}</div>'
    else:
        extra = ""
    return (
        '<article class="metric">'
        f'<div class="metric-label">{label_html}</div>'
        f'<div class="metric-value">{value_html}</div>'
        f"{extra}</article>"
    )


def _num_or_dash(v) -> str:
    """Service metrics fields are Optional[int|float] — None when the unit is
    inactive or systemd does not report them. Never feed None to arithmetic."""
    return "—" if v is None else esc(v)


def _mb(v) -> str:
    return esc(round(v / 1048576, 1)) if isinstance(v, (int, float)) else "—"


def _status_strip(spec, metrics, snapshot) -> str:
    m = metrics
    active_kind = "success" if str(getattr(m, "active_state", "")) == "active" else "danger"
    mem = getattr(m, "memory_bytes", None)
    cards = [
        _metric(
            f'Service {_pill(m.active_state, active_kind)}',
            esc(m.enabled_state),
            foot=f'<span>restarts</span><strong class="num">{_num_or_dash(getattr(m, "restarts", None))}</strong>',
        ),
        _metric(
            "Uptime",
            f'<span class="num">{_num_or_dash(getattr(m, "uptime_sec", None))}<small>s</small></span>',
            foot=f'<span>cpu</span><strong class="num">{_num_or_dash(getattr(m, "cpu_percent", None))}%</strong>',
        ),
        _metric(
            "Memory",
            f'<span class="num">{_mb(mem)}<small> MB</small></span>',
            foot=f'<span>raw</span><strong class="num">{_num_or_dash(mem)}</strong>',
        ),
    ]
    if snapshot is not None:
        soc = getattr(snapshot, "battery_soc", None)
        cards.append(
            _metric(
                "Battery",
                f'<span class="num">{_num_or_dash(soc)}<small>%</small></span>',
                bar=soc if isinstance(soc, (int, float)) else 0,
            )
        )
        raw = getattr(snapshot, "active_emergency_stop", None)
        err_list = getattr(snapshot, "errors", None) or ()
        errc = "(none)" if not err_list else str(len(err_list))
        cards.append(
            _metric(
                f'Safety {_pill(raw or "NONE", _estop_kind(raw))}',
                esc(raw) if raw not in (None, "") else "—",
                foot=f'<span>errors</span><strong>{esc(errc)}</strong>',
            )
        )
        ws = getattr(snapshot, "working_state", None)
        ws_kind = {"bad": "danger", "warn": "warning"}.get(
            _working_state_kind(ws), "neutral" if not ws else "accent"
        )
        cards.append(
            _metric(
                f'Working {_pill(ws or "—", ws_kind)}',
                esc(ws) if ws else "—",
                foot=f'<span>order</span><strong class="num">{esc(getattr(snapshot, "order_id", None) or "—")}</strong>',
            )
        )
        last_node = getattr(snapshot, "last_node_id", None)
        cards.append(
            _metric(
                "Last node",
                f'<span class="num">{esc(last_node) if last_node else "—"}</span>',
                foot=f'<span>localization</span><strong class="num">{esc(getattr(snapshot, "localization_score", None))}</strong>',
            )
        )
    return f'<section class="status-strip" aria-label="adapter summary">{"".join(cards)}</section>'


def _panel(title: str, body_html: str, *, desc: str = "", head_extra: str = "") -> str:
    desc_html = f'<p class="panel-desc">{esc(desc)}</p>' if desc else ""
    return (
        '<section class="panel">'
        f'<div class="panel-head"><div><h2>{esc(title)}</h2>{desc_html}</div>{head_extra}</div>'
        f'<div class="panel-body">{body_html}</div>'
        "</section>"
    )


def adapter_detail_page(
    spec,
    metrics,
    snapshot,
    q: dict,
    csrf: str = "",
    runner=None,
    video_url: str = "",
    host_reboot_enabled: bool = False,
    urobot_restart_enabled: bool = False,
    page_default_refresh_sec: int = 5,
    test_output_poll_sec: int = 2,
    sound_state=None,
    drive_trans: float = 200.0,
    drive_rot: float = 30.0,
    drive_speed: float = 200.0,
) -> str:
    try:
        r = int(q.get("refresh", str(page_default_refresh_sec)))
    except (TypeError, ValueError):
        r = page_default_refresh_sec
    svc = _service_metrics_table(metrics)
    info = _adapter_info_table(spec)
    live = _mqtt_live_table(spec, snapshot)
    tests_output, tests_refresh = _tests_output(spec.key, runner, csrf, return_to="dashboard", poll_sec=test_output_poll_sec)

    # AppShell header content: the serial title is the brand, live pills sit
    # beside it, and the page menu (manual / tests / camera / logs / refresh)
    # are the head actions — all hoisted into the top .topbar by page().
    brand_pills = []
    if snapshot is not None:
        brand_pills.append(_pill(getattr(snapshot, "connection_state", "?"),
                                 _conn_kind(getattr(snapshot, "connection_state", None))))
        brand_pills.append(_pill(getattr(snapshot, "operating_mode", "?"), "accent"))
    head_buttons = []
    head_buttons.append(
        f'<a class="btn ghost" href="/adapter/{esc(spec.key)}/actions">actions</a>'
    )
    head_buttons.append(
        f'<a class="btn ghost" href="/adapter/{esc(spec.key)}/progress">progress</a>'
    )
    head_buttons.append(f'<a class="btn ghost" href="/adapter/{esc(spec.key)}/tests">tests</a>')
    if video_url:
        head_buttons.append('<a class="btn ghost" href="/camera">camera</a>')
    head_buttons.append(f'<a class="btn ghost" href="/adapter/{esc(spec.key)}/logs">logs</a>')
    title = _model_header_title(spec)
    if title:
        head_buttons.append(f'<div class="model-title">{esc(title)}</div>')
    head_buttons.append(f'<a class="btn" href="/adapter/{esc(spec.key)}?refresh={esc(r)}">Refresh {esc(r)}s</a>')

    # left stack: live snapshot + adapter info + test queue
    stack = []
    if live:
        stack.append(_panel("MQTT live snapshot", live,
                            desc="최근 adapter state payload 기준 운영 판단 값.",
                            head_extra=_pill("live", "success")))
    stack.append(_panel("Test queue",
                        f"{_tests_listing(spec, csrf, snapshot, return_to='dashboard')}{tests_output}",
                        desc="실행 가능 테스트와 emergency stop 조건 테스트."))
    stack.append(_panel("Adapter", f"{svc}{info}"))

    # right command stack: emergency / service / vehicle / clamp / drive(jog) /
    # sound / goto / host
    controls = _control_forms(
        spec, csrf, host_reboot_enabled,
        return_to="dashboard", urobot_restart_enabled=urobot_restart_enabled,
        sound_state=sound_state, snapshot=snapshot,
        drive_trans=drive_trans, drive_rot=drive_rot, drive_speed=drive_speed,
    )
    workspace = (
        '<div class="workspace">'
        f'<div class="stack">{"".join(stack)}</div>'
        f'<aside class="command-stack" aria-label="adapter controls">{controls}</aside>'
        "</div>"
    )

    # The page menu moved to the top-right (head-actions); a slim footer keeps
    # only the adapter-path / polling hint (not a menu).
    footer = (
        '<footer class="footerbar"><span></span>'
        f'<span>auto refresh {esc(r)}s · 0 disables polling · adapter path /adapter/{esc(spec.key)}</span></footer>'
    )

    # The jog script runs once on load and binds DOCUMENT-level (delegation)
    # listeners, so it keeps working after the #content poll-swap re-creates the
    # pad. Included only when the embedded jog (manualDrive) is present.
    jog_js = _JOG_JS if _manual_enabled(spec) else ""
    body = f"{_flash(q)}{_status_strip(spec, metrics, snapshot)}{workspace}{footer}{jog_js}"
    # Update data in place (swap #content), not a full-page meta refresh.
    return page(
        spec.display_name, _with_poll(body, r), current="/",
        brand_title=spec.display_name,
        brand_status="".join(brand_pills),
        head_actions="".join(head_buttons),
    )


#: ActionStatus → pill 색. 체크리스트와 상단 요약이 같은 표를 본다.
_ACTION_STATUS_KIND = {
    "WAITING": "neutral",
    "INITIALIZING": "accent",
    "INITIALIZATION": "accent",
    "RUNNING": "accent",
    "PAUSED": "warning",
    "FINISHED": "success",
    "FAILED": "danger",
}


# 체크리스트 등급. INITIALIZATION 은 v2 호환 철자라 같이 받는다
# (adapter_jibot.py _active_action_types 가 같은 두 철자를 함께 본다).
_ACTION_ACTIVE_STATUSES = ("RUNNING", "INITIALIZING", "INITIALIZATION", "PAUSED")


def _seq_of(item) -> int:
    if not isinstance(item, dict):
        return 0
    try:
        return int(item.get("sequenceId") or 0)
    except (TypeError, ValueError):
        return 0


def _checklist_row(index, *, kind: str, title: str, sub: str, right: str) -> str:
    """체크리스트 한 줄. kind 는 done/failed/active/wait 중 하나."""
    marks = {"done": "&#10003;", "failed": "&#10007;", "active": "&#9679;", "wait": "&#9675;"}
    li_cls = {"done": " is-done", "failed": " is-failed", "active": " is-active"}.get(kind, "")
    sub_html = f"<small>{esc(sub)}</small>" if sub else ""
    return (
        f'<li class="ck{li_cls}">'
        f'<span class="ck-idx">{esc(index)}</span>'
        f'<span class="ck-mark {esc(kind)}">{marks.get(kind, marks["wait"])}</span>'
        f'<span class="ck-name"><strong>{esc(title)}</strong>{sub_html}</span>'
        f"{right}</li>"
    )


def _action_checklist(action_states) -> str:
    """actionStates 를 배열 순서(= 오더의 sequence 순) 그대로 체크리스트로 만든다.

    어댑터는 오더 접수 시 전 액션을 WAITING 으로 만들어 두고 끝나도 배열에서 빼지
    않으므로(adapter_jibot.py build_action_states / _clear_cancelled_order_state),
    이 배열 하나로 "완료된 것 + 남은 것"이 다 보인다.
    """
    rows = []
    for i, a in enumerate(tuple(action_states or ()), 1):
        if not isinstance(a, dict):
            continue
        status = str(a.get("actionStatus") or "").upper()
        if status == "FINISHED":
            kind = "done"
        elif status == "FAILED":
            kind = "failed"
        elif status in _ACTION_ACTIVE_STATUSES:
            kind = "active"
        else:
            kind = "wait"
        result = a.get("actionResult") or a.get("resultDescription") or ""
        desc = a.get("actionDescriptor") or a.get("actionDescription") or ""
        sub = " · ".join(x for x in (a.get("actionId") or "", desc, result) if x)
        rows.append(
            _checklist_row(
                i,
                kind=kind,
                title=str(a.get("actionType") or a.get("actionId") or "?"),
                sub=sub,
                right=_pill(status or "?", _ACTION_STATUS_KIND.get(status, "neutral")),
            )
        )
    if not rows:
        return '<p class="ck-empty">진행 중인 오더 액션이 없음.</p>'
    return f'<ul class="checklist">{"".join(rows)}</ul>'


def _path_checklist(snapshot) -> str:
    """남은 노드/엣지를 sequenceId 순으로 합쳐 보여준다.

    어댑터가 통과한 노드를 nodeStates 에서 지우므로(_prune_v3_order_state_before_last_node)
    여기 남는 건 "앞으로 갈 곳"뿐이다. 지나온 경로는 표시할 수 없고, 목록이 위에서부터
    줄어드는 것이 곧 진행 표시다.
    """
    items = []
    for node in getattr(snapshot, "node_states", None) or ():
        if isinstance(node, dict):
            items.append(("node", node))
    for edge in getattr(snapshot, "edge_states", None) or ():
        if isinstance(edge, dict):
            items.append(("edge", edge))
    items.sort(key=lambda kv: _seq_of(kv[1]))

    last_node_id = getattr(snapshot, "last_node_id", None)
    rows = []
    first_pending = True
    for i, (kind_name, item) in enumerate(items, 1):
        is_node = kind_name == "node"
        ident = str((item.get("nodeId") if is_node else item.get("edgeId")) or "?")
        desc = str(
            (item.get("nodeDescriptor") if is_node else item.get("edgeDescriptor")) or ""
        )
        pos = item.get("nodePosition") if is_node else None
        if isinstance(pos, dict):
            coords = f'x={pos.get("x")} y={pos.get("y")} θ={pos.get("theta")}'
        else:
            coords = ""
        sub = " · ".join(
            x for x in (f'seq {_seq_of(item)}', kind_name, desc, coords) if x
        )
        if is_node and last_node_id and ident == last_node_id:
            kind = "done"  # 이미 도착한 시작 노드(액션이 남아 목록에 유지된 경우)
        elif first_pending:
            kind = "active"
            first_pending = False
        else:
            kind = "wait"
        released = bool(item.get("released"))
        rows.append(
            _checklist_row(
                i,
                kind=kind,
                title=ident,
                sub=sub,
                right=_pill("released" if released else "horizon",
                            "accent" if released else "neutral"),
            )
        )
    if not rows:
        return '<p class="ck-empty">남은 경로가 없음(오더 없음 또는 주행 완료).</p>'
    return f'<ul class="checklist">{"".join(rows)}</ul>'


def _progress_status_strip(snapshot) -> str:
    s = snapshot
    actions = [a for a in (getattr(s, "action_states", None) or ()) if isinstance(a, dict)]
    total = len(actions)
    done = sum(
        1 for a in actions
        if str(a.get("actionStatus") or "").upper() in ("FINISHED", "FAILED")
    )
    pct = int(done * 100 / total) if total else 0
    ws = getattr(s, "working_state", None)
    ws_kind = {"bad": "danger", "warn": "warning"}.get(
        _working_state_kind(ws), "neutral" if not ws else "accent"
    )
    detail = getattr(s, "working_state_detail", None)
    active_action = getattr(s, "active_action_type", None)
    active_step = getattr(s, "active_step_action_type", None)
    node_n = len([n for n in (getattr(s, "node_states", None) or ())])
    cards = [
        _metric(
            "Actions cleared",
            f'<span class="num">{esc(done)}<small> / {esc(total)}</small></span>',
            bar=pct,
        ),
        _metric(
            f'Stage {_pill(ws or "—", ws_kind)}',
            esc(detail) if detail else (esc(ws) if ws else "—"),
            foot=f'<span>order</span><strong class="num">{esc(getattr(s, "order_id", None) or "—")}</strong>',
        ),
        _metric(
            "Action now",
            f'<span class="num">{esc(active_action) if active_action else "—"}</span>',
            foot=f'<span>step</span><strong class="num">{esc(active_step) if active_step else "—"}</strong>',
        ),
        _metric(
            "Last node",
            f'<span class="num">{esc(getattr(s, "last_node_id", None) or "—")}</span>',
            foot=f'<span>remaining nodes</span><strong class="num">{esc(node_n)}</strong>',
        ),
        _metric(
            "Position",
            f'<span class="num">{_num_or_dash(getattr(s, "x", None))}, {_num_or_dash(getattr(s, "y", None))}</span>',
            foot=f'<span>θ / map</span><strong class="num">{_num_or_dash(getattr(s, "theta", None))} / {esc(getattr(s, "map_id", None) or "—")}</strong>',
        ),
    ]
    return f'<section class="status-strip" aria-label="order progress summary">{"".join(cards)}</section>'


def order_progress_page(
    spec,
    snapshot,
    q: dict,
    page_default_refresh_sec: int = 5,
) -> str:
    """오더 진행 화면 — 위치 / 단계 / 액션을 리스트로 두고 하나씩 클리어되는 것을 본다.

    데이터는 전부 어댑터가 이미 발행하는 VDA5050 state(= state.json)에서 온다.
    """
    try:
        r = int(q.get("refresh", str(page_default_refresh_sec)))
    except (TypeError, ValueError):
        r = page_default_refresh_sec

    if snapshot is None:
        body = (
            f"{_flash(q)}"
            '<p class="ck-empty">이 어댑터는 라이브 상태를 제공하지 않음.</p>'
            f'<p><a href="/adapter/{esc(spec.key)}">&larr; dashboard</a></p>'
        )
        return page(f"{spec.display_name} progress", body, current="/")

    instant = [
        a for a in (getattr(snapshot, "instant_action_states", None) or ())
        if isinstance(a, dict)
    ]
    stack = [
        _panel(
            "오더 액션",
            _action_checklist(getattr(snapshot, "action_states", None)),
            desc="오더의 전체 액션. 끝난 항목은 취소선으로 남고 진행 중 항목은 강조된다.",
            head_extra=_pill("live", "success"),
        ),
        _panel(
            "남은 경로",
            _path_checklist(snapshot),
            desc="nodeStates/edgeStates 는 남은 구간만 담는다 — 지나온 노드는 어댑터가 지운다.",
        ),
    ]
    if instant:
        # 체크리스트로 만들지 않는다: instantActionStates 는 오더와 무관하게 최근
        # 결과가 쌓이는 retain 로그(_prune_retained_instant_action_states, 상한 50)라
        # 번호를 붙이면 "클리어되지 않는 네 번째 목록"으로 읽힌다.
        stack.append(
            _panel(
                "Instant action",
                '<div class="telemetry-grid">'
                + _instant_action_cards(snapshot)
                + "</div>",
                desc="최근 instant action 결과(오더 밖에서 실행된 것). 진행 순서가 아니라 이력이다.",
            )
        )

    head_buttons = [
        f'<a class="btn ghost" href="/adapter/{esc(spec.key)}">dashboard</a>',
        f'<a class="btn ghost" href="/adapter/{esc(spec.key)}/actions">actions</a>',
        f'<a class="btn" href="/adapter/{esc(spec.key)}/progress?refresh={esc(r)}">Refresh {esc(r)}s</a>',
    ]
    brand_pills = _pill(
        getattr(snapshot, "connection_state", "?"),
        _conn_kind(getattr(snapshot, "connection_state", None)),
    )
    footer = (
        '<footer class="footerbar"><span></span>'
        f'<span>auto refresh {esc(r)}s · 0 disables polling · /adapter/{esc(spec.key)}/progress</span></footer>'
    )
    body = (
        f"{_flash(q)}{_progress_status_strip(snapshot)}"
        f'<div class="stack">{"".join(stack)}</div>{footer}'
    )
    return page(
        f"{spec.display_name} progress",
        _with_poll(body, r),
        current="/",
        brand_title=f"{spec.display_name} — progress",
        brand_status=brand_pills,
        head_actions="".join(head_buttons),
    )


def logs_page(spec, lines, q: dict, page_default_refresh_sec: int = 5) -> str:
    try:
        r = int(q.get("refresh", str(page_default_refresh_sec)))
    except (TypeError, ValueError):
        r = page_default_refresh_sec
    text = "\n".join(esc(line) for line in lines)
    body = (
        f"{_flash(q)}<h3>{esc(spec.display_name)} — logs</h3>"
        f"<pre>{text}</pre>"
        f'<p><a href="/adapter/{esc(spec.key)}">&larr; dashboard</a></p>'
    )
    # Update the <pre> in place (swap #content), not a full-page meta refresh.
    return page(f"{spec.display_name} logs", _with_poll(body, r))


def _camera_verbs(csrf: str, return_to: str = "") -> str:
    """Service-control verb buttons (start/stop/…) wired to /camera/control.

    The camera reuses the generic adapter verb form; only the POST target differs
    (web_video_server is controlled via /camera/control, not /adapter/<key>/control).
    """
    return "".join(
        _verb_form("camera", v, csrf, return_to=return_to).replace(
            "/adapter/camera/control", "/camera/control"
        )
        for v in _VERBS
    )


def camera_page(metrics, rows, csrf: str, q: dict, enabled: bool = True) -> str:
    verbs = _camera_verbs(csrf)
    svc = (
        "<table>"
        f"<tr><td>service</td><td>{esc(metrics.active_state)} ({esc(metrics.enabled_state)})</td></tr>"
        f"<tr><td>uptime</td><td>{esc(metrics.uptime_sec)}s</td></tr>"
        f"<tr><td>restarts</td><td>{esc(metrics.restarts)}</td></tr>"
        "</table>"
    )
    if not enabled:
        streams = "<p>camera disabled</p>"
    elif not rows:
        streams = "<p>no configured camera topics</p>"
    else:
        body_rows = []
        for topic, stream_url, snapshot_url in rows:
            body_rows.append(
                "<tr>"
                f"<td>{esc(topic)}</td>"
                f'<td><a href="{esc(stream_url)}" target="_blank" rel="noopener">stream</a></td>'
                f'<td><a href="{esc(snapshot_url)}" target="_blank" rel="noopener">snapshot</a></td>'
                "</tr>"
            )
        streams = (
            "<table><tr><th>topic</th><th>stream</th><th>snapshot</th></tr>"
            + "".join(body_rows)
            + "</table>"
        )
    body = f"{_flash(q)}<h3>amr-camera {_camera_status(metrics)}</h3>{svc}<h4>service</h4>{verbs}<h4>cameras</h4>{streams}"
    return page("camera", _with_poll(body, _refresh_secs(q)))


# title / description / button-colour / button-label per service verb & action.
_VERB_META = {
    "start": ("Start service", "disabled 상태에서 adapter process 시작", "success", "Start"),
    "stop": ("Stop service", "실시간 adapter 처리 중지", "danger", "Stop"),
    "restart": ("Restart service", "process 재시작 후 MQTT state 재수신", "warning", "Restart"),
    "enable": ("Enable adapter", "부팅/운영 대상에 adapter 포함", "", "Enable"),
    "disable": ("Disable adapter", "자동 시작·운영 대상에서 제외", "danger", "Disable"),
}
_ACTION_META = {
    "stateRequest": ("즉시 state snapshot 요청", "primary", "Request"),
    "factsheetRequest": ("vehicle capability metadata 요청", "", "Request"),
    "startPause": ("startPause action 발행", "warning", "Pause"),
    "stopPause": ("stopPause action 발행", "success", "Resume"),
    "startCharging": ("충전(UmDock) action 전달", "warning", "Charge"),
    "stopCharging": ("충전 중지 — 활성 충전 모드 종료(UmStop/relay 해제)", "warning", "Stop charge"),
    "cancelOrder": ("현재 order 취소 action 발행", "danger", "Cancel"),
    "clearErrors": ("누적된 sticky 오류 제거 — state.errors 비움", "warning", "Clear errors"),
    "localize": ("현재 위치를 지정 노드/pose로 재설정", "warning", "Localize"),
    "manualStop": ("즉시 정지 (UmStop) — 항상 동작", "danger", "STOP"),
    "enableMotor": ("모터 전원 ON — 이동 전 필요(서보 OFF면 이동 무시)", "warning", "Motor ON"),
    "testSound": ("사운드 테스트 재생", "", "Play"),
    "stopSound": ("사운드 정지", "", "Stop"),
}
#: 여기에는 어댑터가 내장한 액션만 넣는다. extension 액션의 설명·버튼 라벨은
#: 그 모듈의 ActionSpec(label)과 panel.html 이 갖는다 — 코어가 특정 extension 의
#: 문구를 알면 모듈을 추가할 때마다 이 표를 고쳐야 한다.


def _command_form(action_url: str, csrf: str, return_to: str, hidden: str, *,
                  title: str, desc: str, label: str, variant: str = "",
                  confirm: bool = False, fields: str = "", anchor: str = "") -> str:
    confirm_html = (
        '<label class="confirm"><input type="checkbox" name="confirm"> confirm</label>'
        if confirm else ""
    )
    btn_cls = ("btn " + variant).strip()
    cls = "command-form has-fields" if fields else "command-form"
    anchor_attr = f' id="{esc(anchor)}"' if anchor else ""
    return (
        f'<form{anchor_attr} class="{cls}" method="post" action="{action_url}">'
        f"{_csrf_field(csrf)}{_return_to_field(return_to)}{hidden}"
        f'<span class="command-info"><strong>{esc(title)}</strong><span>{esc(desc)}</span></span>'
        f"{fields}"
        f'<span class="command-actions">{confirm_html}'
        f'<button class="{btn_cls}">{esc(label)}</button></span></form>'
    )


def _input_card(action_url: str, csrf: str, return_to: str, hidden: str, *,
                title: str, desc: str, label: str, variant: str = "",
                confirm: bool = False, fields: str = "",
                extra_class: str = "") -> str:
    """Card flavour of _command_form: title + description + optional input
    field(s) + button laid out as a vertical .action-card, so the Drive / Sound /
    Goto / Motion-rule / Host controls match the Service/Vehicle/Clamp card groups
    and flow inside a .cmd-wrap. confirm/fields behave exactly like _command_form."""
    confirm_html = (
        '<label class="confirm"><input type="checkbox" name="confirm"> confirm</label>'
        if confirm else ""
    )
    btn_cls = ("btn " + variant).strip()
    cls = ("action-card " + extra_class).strip()
    return (
        f'<form class="{cls}" method="post" action="{action_url}">'
        f"{_csrf_field(csrf)}{_return_to_field(return_to)}{hidden}"
        f'<span class="ac-info"><strong>{esc(title)}</strong>'
        f'<span>{esc(desc)}</span></span>'
        f"{fields}"
        f'<div class="ac-actions">{confirm_html}'
        f'<button class="{btn_cls}">{esc(label)}</button></div></form>'
    )


def _command_group(title: str, body: str, head_extra: str = "") -> str:
    return (
        '<section class="command-group">'
        f'<div class="command-head"><h2>{esc(title)}</h2>{head_extra}</div>'
        f'<div class="command-body">{body}</div></section>'
    )


def _io_bits(label: str, bits, updated_at) -> str:
    """비트 리스트를 핀 인덱스별 ON/OFF dot 행으로."""
    if not bits:
        return ""
    cells = "".join(
        f'<span class="status {"success io-on" if int(b) else "neutral"}">'
        f'{i}:{"ON" if int(b) else "OFF"}</span> '
        for i, b in enumerate(bits)
    )
    age = f" <small>({esc(updated_at)})</small>" if updated_at is not None else ""
    return f"<div><strong>{esc(label)}</strong>{age}<br>{cells}</div>"


_PIO_PINS = ("1", "2", "3", "4", "5", "6", "7", "8")


def _pio_circles(label: str, states, updated_at) -> str:
    """PIO 8핀(in 또는 out)을 인덱스별 원형 인디케이터 행으로.

    ● = ON(초록 success), ○ = OFF(회색 neutral), ◌ = 아직 모름(neutral).
    states: dict 핀("1".."8")->'on'/'off'. 없는 핀은 unknown(아직 읽기 전이거나
    핀 맵에 없는 번호). in/out 모두 EZI IO 레지스터를 주기적으로 되읽은 값이다.
    """
    states = states or {}
    cells = []
    for pin in _PIO_PINS:
        v = states.get(pin)
        if v == "on":
            cls, dot, txt = "success io-on", "●", "on"
        elif v == "off":
            cls, dot, txt = "neutral", "○", "off"
        else:
            cls, dot, txt = "neutral", "◌", "—"
        cells.append(
            f'<span class="status {cls}" title="{esc(label)} {esc(pin)}: {esc(txt)}">'
            f"{esc(pin)} {dot}</span> "
        )
    age = f" <small>({esc(updated_at)})</small>" if updated_at is not None else ""
    return f'<div><strong>{esc(label)}</strong>{age}<br>{"".join(cells)}</div>'


def _io_out_button(spec_key: str, csrf: str, return_to: str, board: str, index, state) -> str:
    """클릭 가능한 out 배지: 현재의 반대 상태를 /io/out 로 POST(토글).
    state: 'on' | 'off' | None(모름). 모름/off → 클릭 시 on, on → 클릭 시 off.
    실제 장비 출력 신호가 나가므로 confirm 없이 즉시 전송한다."""
    if state == "on":
        cls, dot, cur, target = "success io-on", "●", "on", "off"
    elif state == "off":
        cls, dot, cur, target = "neutral", "○", "off", "on"
    else:
        cls, dot, cur, target = "neutral", "◌", "—", "on"
    return (
        f'<form class="io-out" method="post" action="/adapter/{esc(spec_key)}/io/out"'
        ' style="display:inline">'
        f'<input type="hidden" name="csrf_token" value="{esc(csrf)}">'
        f'<input type="hidden" name="return_to" value="{esc(return_to)}">'
        f'<input type="hidden" name="board" value="{esc(board)}">'
        f'<input type="hidden" name="index" value="{esc(index)}">'
        f'<input type="hidden" name="state" value="{esc(target)}">'
        f'<button type="submit" class="status {cls} io-out-btn" '
        f'title="out {esc(index)}: {esc(cur)}">'
        f"{esc(index)} {dot}</button></form> "
    )


def _pio_out_row(outputs, updated_at, spec_key: str, csrf: str, return_to: str) -> str:
    """PIO out 8핀. spec_key가 있으면 클릭 토글 버튼, 없으면 표시 전용 동그라미."""
    if not spec_key:
        return _pio_circles("out", outputs, updated_at)
    states = outputs or {}
    cells = "".join(
        _io_out_button(spec_key, csrf, return_to, "pio", pin, states.get(pin))
        for pin in _PIO_PINS
    )
    age = f" <small>({esc(updated_at)})</small>" if updated_at is not None else ""
    return f"<div><strong>out</strong>{age}<br>{cells}</div>"


def _ezio_out_row(outputs, updated_at, spec_key: str, csrf: str, return_to: str) -> str:
    """EZIO out 핀(0-인덱스). spec_key가 있으면 클릭 토글 버튼, 없으면 표시 전용."""
    if not spec_key or not outputs:
        return _io_bits("outputs", outputs, updated_at)
    cells = "".join(
        _io_out_button(spec_key, csrf, return_to, "ezio", i, "on" if int(b) else "off")
        for i, b in enumerate(outputs)
    )
    age = f" <small>({esc(updated_at)})</small>" if updated_at is not None else ""
    return f"<div><strong>outputs</strong>{age}<br>{cells}</div>"


def _io_conn_pill(connected) -> str:
    """Connection status pill for an IO board.

    Disconnected is NOT an error: pioScenario finally-disconnects after reading
    inputs, so a disconnected board with last inputs is normal. The pill is
    neutral ('idle') for both EZIO and PIO when disconnected; error colour is
    reserved for an actual `error` field (rendered separately in the body).
    """
    conn = "success" if connected else "neutral"
    conn_label = "connected" if connected else "idle"
    return f'<span class="status {conn}">{conn_label}</span>'


_IO_NO_DATA = '<p class="muted">데이터 없음 — io.json 미수신 (어댑터가 아직 기록 안 함)</p>'
_IO_NO_DATA_PILL = '<span class="status neutral">데이터 없음</span>'


def _ezio_section(display_name: str, ez, spec_key: str = "", csrf: str = "",
                  return_to: str = "") -> str:
    """EZIO 섹션. 미구성/무데이터여도 헤더는 항상 렌더('데이터 없음').
    out 비트는 spec_key가 있으면 클릭 토글 버튼(ezioWriteOut)."""
    if ez is not None and getattr(ez, "configured", False):
        err = (
            _error_notice("EZIO Error", ez.error)
            if getattr(ez, "error", "")
            else ""
        )
        inp = _io_bits("inputs", ez.inputs, ez.inputs_updated_at)
        outp = _ezio_out_row(ez.outputs, ez.outputs_updated_at, spec_key, csrf, return_to)
        body = (
            f'<p>IP {esc(ez.ip)}:{esc(ez.port)} · {esc(ez.board)}</p>'
            f"{err}{inp}{outp}"
        )
        pill = _io_conn_pill(getattr(ez, "connected", False))
    else:
        body = _IO_NO_DATA
        pill = _IO_NO_DATA_PILL
    return _command_group(f"EZIO — {display_name}", body, head_extra=pill)


def _pio_section(display_name: str, pio, spec_key: str = "", csrf: str = "",
                 return_to: str = "") -> str:
    """PIO 섹션. io.json이 없거나 미구성이어도 in8/out8 동그라미 골격을 항상 렌더
    (값은 ◌=모름). out은 spec_key가 있으면 클릭 토글 버튼(pioWriteOut)."""
    # in 8 / out 8 은 데이터 유무와 무관하게 항상 8핀(unknown은 ◌)으로 렌더.
    in_row = _pio_circles(
        "in",
        getattr(pio, "inputs", None) if pio is not None else None,
        getattr(pio, "inputs_updated_at", None) if pio is not None else None,
    )
    out_row = _pio_out_row(
        getattr(pio, "outputs", None) if pio is not None else None,
        getattr(pio, "outputs_updated_at", None) if pio is not None else None,
        spec_key, csrf, return_to,
    )
    if pio is not None and getattr(pio, "configured", False):
        # connected=false + inputs present is NORMAL (pioScenario finally-disconnect)
        err = (
            _error_notice("PIO Error", pio.error)
            if getattr(pio, "error", "")
            else ""
        )
        head = f"<p>port {esc(pio.port)} @ {esc(pio.baudrate)}</p>{err}"
        pill = _io_conn_pill(getattr(pio, "connected", False))
    else:
        head = _IO_NO_DATA
        pill = _IO_NO_DATA_PILL
    return _command_group(
        f"PIO — {display_name}", f"{head}{in_row}{out_row}", head_extra=pill
    )


#: IO 보드 상태를 그리는 모듈. 키는 모듈 이름의 끝 조각(``extensions.pio`` → ``pio``)
#: 이고, 값은 (io 스냅샷 속성, 섹션 렌더러, out 쓰기 액션)이다. 두 보드는 프로토콜이
#: 실제로 달라(EZIO는 0-베이스 비트열, PIO는 1-베이스 8핀 + 미상) 렌더러를 공유하지
#: 않는다. 여기 없는 모듈은 상태 패널 없이 액션 카드만 나가므로, 상태 표시가 필요
#: 없는 extension 은 아무것도 등록하지 않으면 된다.
_IO_STATUS_SECTIONS = {
    "ezio": ("ezio", lambda *a: _ezio_section(*a), "ezioWriteOut"),
    "pio": ("pio", lambda *a: _pio_section(*a), "pioWriteOut"),
}


#: 모듈 panel.html 안에서 그 액션이 이미 다뤄졌는지 알아내는 표식. 액션 폼은 반드시
#: 이 hidden 입력을 갖는다(그래야 /action 엔드포인트가 받는다).
_PANEL_ACTION_RE = re.compile(r'name="action_type"\s+value="([^"]+)"')


def _module_panel(spec_key: str, module_view, csrf: str, return_to: str,
                  remembered=None) -> str:
    """모듈이 패키지에 넣어 둔 ``panel.html``. 없거나 깨졌으면 빈 문자열.

    UI 를 모듈이 소유하게 하는 통로다 — 코어는 마크업에 관여하지 않고 값만 채운다.

    채워 주는 것:

    - ``$adapter_key`` / ``$csrf_token`` / ``$return_to``
    - ``$field_<actionType>_<parameterName>`` — 그 액션이 선언한 파라미터의 입력
      요소와 이름표. 선택지가 설정에서 오는 파라미터(``pioWriteOut`` 의
      ``signal``, ``pioPing`` 의 ``stationId``)를 패널에 박지 않고 쓰기 위한
      것이다. 패널이 직접 ``<input>`` 을 써도 되지만, 그러면 ``extensions.hcl`` 을
      고쳐도 선택지가 따라오지 않는다. 이름표까지 여기서 붙이므로 패널은 칸
      순서만 정하고, 어느 칸이 무엇인지는 스키마가 정한다.

    렌더가 실패해도 화면을 죽이지 않고 빈 문자열을 돌려주며, 호출부가 선언된
    액션을 생성 폼으로 대신 낸다.
    """
    template = getattr(module_view, "panel_template", None)
    if not template or not template.strip():
        return ""
    remembered = remembered or {}
    mapping = {
        "adapter_key": esc(spec_key),
        "csrf_token": esc(csrf),
        "return_to": esc(return_to),
    }
    for action in getattr(module_view, "actions", ()):
        action_type = getattr(action, "action_type", "")
        values = remembered.get(action_type) or {}
        for parameter in getattr(action, "parameters", ()) or ():
            name = getattr(parameter, "name", "")
            mapping[f"field_{action_type}_{name}"] = _panel_field(
                parameter, values.get(name), action_type
            )
    try:
        return string.Template(template).substitute(mapping)
    except (KeyError, ValueError) as exc:
        print(f"[ACTION MODULE PANEL RENDER FALLBACK] {module_view.module}: {exc}")
        return ""


def _panel_field(parameter, current=None, scope: str = "") -> str:
    """패널 카드 안의 파라미터 칸 — 입력 요소에 이름표를 달아서 준다.

    패널은 카드가 좁아 안내를 placeholder 하나에 맡겼는데, select 에는
    placeholder 가 없고 값을 채우면 placeholder 도 사라진다. 그래서 pioPing 첫
    칸이 ``media`` 인지 ``stationId`` 인지 화면만 보고는 알 수 없었다.
    """
    return (
        '<label class="cf-row">'
        f"{_param_key(parameter)}"
        f"{_param_field(parameter, current, scope)}"
        "</label>"
    )


def _module_status_panel(spec_key: str, display_name: str, module_view, io,
                         csrf: str, return_to: str) -> str:
    """discover 된 모듈 하나의 라이브 IO 상태 패널.

    표시 여부는 extension 설정을 그대로 따른다 — 모듈이 discovery 에서 빠지면
    호출 자체가 없고, 상태 소스가 없는 모듈이면 빈 문자열이다. io.json 이 아직
    없을 때는 골격만("데이터 없음") 낸다. 모듈이 out 쓰기 액션을 노출하지 않으면
    (``[[actions]]`` 로 껐거나 애초에 없거나) out 배지를 표시 전용으로 그린다 —
    누를 수는 있는데 서버가 거절하는 상태를 만들지 않기 위해서다.
    """
    name = (getattr(module_view, "module", "") or "").rsplit(".", 1)[-1]
    entry = _IO_STATUS_SECTIONS.get(name)
    if entry is None:
        return ""
    attr, section, write_action = entry
    state = getattr(io, attr, None) if io is not None else None
    writable = any(
        getattr(action, "action_type", "") == write_action
        for action in getattr(module_view, "actions", ())
    )
    return section(display_name, state, spec_key if writable else "", csrf, return_to)


def _verb_form(spec_key: str, verb: str, csrf: str, return_to: str = "") -> str:
    title, desc, variant, label = _VERB_META.get(verb, (verb, "", "", verb))
    return _command_form(
        f"/adapter/{esc(spec_key)}/control", csrf, return_to,
        f'<input type="hidden" name="verb" value="{esc(verb)}">',
        title=title, desc=desc, label=label, variant=variant,
        confirm=False,  # service-control (and camera) verbs fire on a single click
    )


def _action_form(spec_key: str, action, csrf: str, return_to: str = "") -> str:
    desc, variant, label = _ACTION_META.get(
        action.action_type, ("instant action 발행", "", action.label)
    )
    return _command_form(
        f"/adapter/{esc(spec_key)}/action", csrf,
        _anchored(return_to, action.action_type),
        f'<input type="hidden" name="action_type" value="{esc(action.action_type)}">',
        title=action.label, desc=desc, label=label, variant=variant,
        anchor=action_anchor(action.action_type),
        confirm=False,  # motion vehicle actions fire on a single click (host/urobot keep confirm)
    )


def _card_verb_form(spec_key: str, verb: str, csrf: str, return_to: str = "") -> str:
    """Service-control verb as a compact CARD: label + description + button.
    Wrapped in .cmd-wrap so many cards flow and wrap densely."""
    title, desc, variant, label = _VERB_META.get(verb, (verb, "", "", verb))
    btn_cls = ("btn " + variant).strip()
    return (
        f'<form class="action-card" method="post" action="/adapter/{esc(spec_key)}/control">'
        f"{_csrf_field(csrf)}{_return_to_field(return_to)}"
        f'<input type="hidden" name="verb" value="{esc(verb)}">'
        f'<span class="ac-info"><strong>{esc(title)}</strong><span>{esc(desc)}</span></span>'
        f'<button class="{btn_cls}">{esc(label)}</button></form>'
    )


def _card_action_form(spec_key: str, action, csrf: str, return_to: str = "") -> str:
    """Instant action as a compact CARD: action label + description + button.
    Wrapped in .cmd-wrap (Vehicle actions) so many cards flow and wrap."""
    desc, variant, label = _ACTION_META.get(
        action.action_type, ("instant action 발행", "", action.label)
    )
    btn_cls = ("btn " + variant).strip()
    anchor = action_anchor(action.action_type)
    return (
        f'<form id="{anchor}" class="action-card" method="post" '
        f'action="/adapter/{esc(spec_key)}/action">'
        f"{_csrf_field(csrf)}{_return_to_field(_anchored(return_to, action.action_type))}"
        f'<input type="hidden" name="action_type" value="{esc(action.action_type)}">'
        f'<span class="ac-info"><strong>{esc(action.label)}</strong><span>{esc(desc)}</span></span>'
        f'<button class="{btn_cls}">{esc(label)}</button></form>'
    )


def _compact_action_form(spec_key: str, action, csrf: str, return_to: str = "") -> str:
    """Instant action as a compact button (label only; title carries the full
    label + description). Used for the emergency quick buttons next to STOP."""
    desc, variant, label = _ACTION_META.get(
        action.action_type, ("instant action 발행", "", action.label)
    )
    btn_cls = ("btn " + variant).strip()
    return (
        f'<form class="btn" method="post" action="/adapter/{esc(spec_key)}/action" '
        f'title="{esc(action.label)} — {esc(desc)}">'
        f"{_csrf_field(csrf)}{_return_to_field(return_to)}"
        f'<input type="hidden" name="action_type" value="{esc(action.action_type)}">'
        f'<button class="{btn_cls}">{esc(label)}</button></form>'
    )


# Actions that live ONLY in the prominent emergency/quick group (not duplicated
# in the generic Vehicle-actions wrap): the always-available stop, end-charging,
# and motor-enable controls an operator reaches for first.
_EMERGENCY_ACTION_TYPES = ("manualStop", "stopCharging", "enableMotor", "disableMotor")

# Operator recovery actions get their own "주문 / 복구" group near the top (right
# after the emergency block): cancel the active order, clear sticky errors. Both
# are pulled out of the generic Vehicle-actions wrap so they read as a dedicated
# recovery pair rather than two bare buttons buried among the vehicle actions.
_RECOVERY_ACTION_TYPES = ("cancelOrder", "clearErrors")


def _motor_power_btn(spec_key: str, action_type: str, label: str, variant: str,
                     csrf: str, return_to: str = "") -> str:
    """One motor-power button (enable/disable) as a form, for the motor card."""
    return (
        f'<form class="btn" method="post" action="/adapter/{esc(spec_key)}/action">'
        f"{_csrf_field(csrf)}{_return_to_field(return_to)}"
        f'<input type="hidden" name="action_type" value="{esc(action_type)}">'
        f'<button class="btn {esc(variant)}">{esc(label)}</button></form>'
    )


def _motor_control_card(spec, by_type, csrf: str, return_to: str = "", snapshot=None) -> str:
    """Motor power as a title/description/button card (not a bare quick button):
    current state + Motor ON (enableMotor) / Motor OFF (disableMotor)."""
    has_on = "enableMotor" in by_type
    has_off = "disableMotor" in by_type
    if not (has_on or has_off):
        return ""
    state = _motor_label(snapshot) if snapshot is not None else ""
    state_html = (
        f'<span class="ac-state">현재: {esc(state)}</span>' if state else ""
    )
    btns = []
    if has_on:
        btns.append(_motor_power_btn(spec.key, "enableMotor", "Motor ON", "success", csrf, return_to))
    if has_off:
        btns.append(_motor_power_btn(spec.key, "disableMotor", "Motor OFF", "warning", csrf, return_to))
    return (
        '<div class="action-card motor-card">'
        '<span class="ac-info"><strong>모터 전원</strong>'
        '<span>이동 전 ON 필요 · OFF는 테스트용 재수동화</span></span>'
        f'{state_html}'
        f'<div class="cmd-wrap">{"".join(btns)}</div>'
        '</div>'
    )


def _emergency_forms(spec, csrf: str, return_to: str = "", snapshot=None) -> str:
    """Emergency / quick-control block: an oversized red STOP (manualStop=UmStop),
    a compact Stop-charge button, and a motor-power card (ON/OFF) when the spec
    exposes the corresponding actions."""
    by_type = {getattr(a, "action_type", ""): a for a in spec.instant_actions}
    parts = []
    if "manualStop" in by_type:
        parts.append(
            f'<form class="estop" method="post" action="/adapter/{esc(spec.key)}/action">'
            f"{_csrf_field(csrf)}{_return_to_field(return_to)}"
            '<input type="hidden" name="action_type" value="manualStop">'
            '<button class="btn estop" title="즉시 정지 (UmStop) — 항상 동작">STOP</button></form>'
        )
    quick = [
        _compact_action_form(spec.key, by_type[at], csrf, return_to=return_to)
        for at in ("stopCharging",)
        if at in by_type
    ]
    if quick:
        parts.append('<div class="cmd-wrap">' + "".join(quick) + "</div>")
    motor = _motor_control_card(spec, by_type, csrf, return_to=return_to, snapshot=snapshot)
    if motor:
        parts.append(motor)
    return "".join(parts)


def _goto_form(spec_key: str, csrf: str, return_to: str = "") -> str:
    """A text input + button to send a JIBOT UmGoto to a named goal/node.

    Sends actionType=jibotCommand with command=UmGoto and the typed goal; the
    adapter normalises target='goal'. Fires on a single click (no confirm).
    """
    fields = (
        '<span class="command-fields">'
        '<input type="text" name="goal" placeholder="goal node e.g. F1_60" required></span>'
    )
    return _input_card(
        f"/adapter/{esc(spec_key)}/goto", csrf, return_to, "",
        title="Move to goal (UmGoto)", desc="지정 노드로 이동 — 로봇이 실제로 움직임",
        label="Send goto", variant="primary", confirm=False, fields=fields,
    )


def _goto_nearest_form(spec_key: str, csrf: str, return_to: str = "") -> str:
    """Button: drive to the nearest map node and set lastNodeId on arrival.

    Posts action_type=gotoNearestNode to /action; the adapter resolves the
    nearest node from the live pose (no node id is sent). Requires confirm
    (the robot moves); the server enforces the confirm via _CONFIRM_REQUIRED_ACTIONS.
    """
    return _input_card(
        f"/adapter/{esc(spec_key)}/action", csrf, return_to,
        '<input type="hidden" name="action_type" value="gotoNearestNode">',
        title="가장 가까운 노드로 이동",
        desc="현재 위치에서 가장 가까운 노드로 이동 후 lastNodeId 설정 — 로봇이 실제로 움직임",
        label="Go to nearest node", variant="primary", confirm=True,
    )


def _localize_form(spec_key: str, csrf: str, return_to: str = "") -> str:
    """Localize to a named map node without physically driving there."""
    fields = (
        '<span class="command-fields">'
        '<input type="text" name="goal" placeholder="map node e.g. p2" required></span>'
    )
    return _input_card(
        f"/adapter/{esc(spec_key)}/action", csrf, return_to,
        '<input type="hidden" name="action_type" value="localize">'
        '<input type="hidden" name="target" value="goal">',
        title="Localize",
        desc="현재 pose를 지정 노드로 재설정하고 lastNodeId를 다시 잡음 — 실제 이동 없음",
        label="Localize", variant="warning", confirm=True, fields=fields,
    )


# Sound actions get a dedicated panel (Play/Stop + volume), so they are kept out
# of the generic "Vehicle actions" group to avoid bare duplicate buttons.
_SOUND_ACTION_TYPES = ("testSound", "stopSound", "setSoundVolume")
_VOLUME_PRESETS = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)

_DRIVE_ACTION_TYPES = ("manualDrive", "manualMove")


def _sound_mini_form(spec_key: str, csrf: str, return_to: str, hidden: str,
                     label: str, variant: str = "") -> str:
    """Compact inline form (one button) posting setSoundVolume with one param."""
    btn_cls = ("btn " + variant).strip()
    return (
        f'<form class="btn" method="post" action="/adapter/{esc(spec_key)}/action">'
        f"{_csrf_field(csrf)}{_return_to_field(return_to)}"
        '<input type="hidden" name="action_type" value="setSoundVolume">'
        f"{hidden}"
        f'<button class="{btn_cls}">{esc(label)}</button></form>'
    )


def _sound_forms(spec, csrf: str, return_to: str = "", sound_state=None) -> str:
    """Sound panel as ONE compact card: a Play/Stop transport button row, volume
    presets + mute/unmute, and an inline free-entry volume input — all inside a
    single .action-card.wide so the panel stays short instead of spreading across
    four tall cards.

    Presets and the numeric input live in SEPARATE forms on purpose: the WebUI
    form parser keeps the first value per key (parse_qs -> v[0]), so a preset
    button and a same-named text input in one form would collide. The handler
    reads `volume` (0-100) or `mute`.

    ``sound_state`` is the WebUi's last-commanded {volume, muted}; the preset
    nearest the current volume (or Mute, when muted) is shown pressed (is-active)
    since the OS sink has no reliable volume read-back.
    """
    by_type = {getattr(a, "action_type", ""): a for a in spec.instant_actions}
    if not (set(by_type) & set(_SOUND_ACTION_TYPES)):
        return ""
    # Play / Stop as compact transport buttons (single click; label-only).
    transport = "".join(
        _compact_action_form(spec.key, by_type[at], csrf, return_to=return_to)
        for at in ("testSound", "stopSound")
        if at in by_type
    )
    transport_html = f'<div class="sound-transport">{transport}</div>' if transport else ""

    cur_label = "사운드 재생 / 정지"
    body = ""
    if "setSoundVolume" in by_type:
        st = sound_state or {}
        cur_vol = st.get("volume")
        muted = bool(st.get("muted"))
        active_preset = None
        if cur_vol is not None and not muted:
            active_preset = min(_VOLUME_PRESETS, key=lambda p: abs(p - cur_vol))
        presets = "".join(
            _sound_mini_form(
                spec.key, csrf, return_to,
                f'<input type="hidden" name="volume" value="{p}">', f"{p}%",
                "is-active" if p == active_preset else "",
            )
            for p in _VOLUME_PRESETS
        )
        mute = _sound_mini_form(
            spec.key, csrf, return_to,
            '<input type="hidden" name="mute" value="1">', "Mute",
            "danger is-active" if muted else "danger",
        )
        unmute = _sound_mini_form(
            spec.key, csrf, return_to,
            '<input type="hidden" name="mute" value="0">', "Unmute",
        )
        if muted:
            cur_label = "음소거됨"
        elif cur_vol is not None:
            cur_label = f"현재 ~{esc(cur_vol)}% (가까운 값 강조)"
        else:
            cur_label = "OS sink 볼륨 프리셋 / 음소거"
        # Free-entry volume as a compact inline row (number + Set), not a tall card.
        custom = (
            f'<form class="sound-set" method="post" action="/adapter/{esc(spec.key)}/action">'
            f"{_csrf_field(csrf)}{_return_to_field(return_to)}"
            '<input type="hidden" name="action_type" value="setSoundVolume">'
            '<input type="number" name="volume" min="0" max="100" step="1" '
            'placeholder="0-100 직접 입력 (pactl set-sink-volume)">'
            '<button class="btn primary">Set</button></form>'
        )
        body = f'<div class="chip-row">{presets}{mute}{unmute}</div>{custom}'

    return (
        '<div class="action-card wide sound-card">'
        '<div class="sound-row">'
        f'<span class="ac-info"><strong>Sound</strong><span>{esc(cur_label)}</span></span>'
        f"{transport_html}</div>{body}</div>"
    )


def _host_reboot_form(spec_key: str, csrf: str, return_to: str = "") -> str:
    return _input_card(
        f"/adapter/{esc(spec_key)}/host/reboot", csrf, return_to, "",
        title="Reboot OS", desc="host reboot OS — adapter·stream·MQTT 연결 모두 끊김",
        label="reboot OS", variant="danger", confirm=True,
    )


def _urobot_restart_form(spec_key: str, csrf: str, return_to: str = "") -> str:
    return _input_card(
        f"/adapter/{esc(spec_key)}/urobot/restart", csrf, return_to, "",
        title="Restart urobot", desc="nav/perception 스택 재기동 — 로봇 정지 시에만",
        label="restart urobot", variant="warning", confirm=True,
    )


def _motion_rule_form(spec_key: str, csrf: str, return_to: str = "") -> str:
    """`to` (+ optional `from`) input + button to run the configured dock/move
    motion_rule for the `to` node — reuses the order-worker orchestrators so the
    dock-approach and move-segment flows are testable without an ACS order.
    Posts actionType=jibotMotionRule with to/from params to /action.
    """
    fields = (
        '<span class="command-fields">'
        '<input type="text" name="to" placeholder="to node e.g. F2_90_S2CH" required>'
        '<input type="text" name="from" placeholder="from (optional)"></span>'
    )
    return _input_card(
        f"/adapter/{esc(spec_key)}/action", csrf, return_to,
        '<input type="hidden" name="action_type" value="jibotMotionRule">',
        title="Motion rule (dock/move)",
        desc="설정된 dock/move motion_rule 실행 — to 도착 시 완료 (로봇이 실제로 움직임)",
        label="Run", variant="primary", confirm=True, fields=fields,
    )


def _control_forms(
    spec,
    csrf: str,
    host_reboot_enabled: bool = False,
    return_to: str = "",
    urobot_restart_enabled: bool = False,
    sound_state=None,
    snapshot=None,
    drive_trans: float = 200.0,
    drive_rot: float = 30.0,
    drive_speed: float = 200.0,
) -> str:
    groups = []
    # extension 모듈 액션과 recipe 는 이 페이지에 오지 않는다 — 둘 다 /actions 가
    # 파라미터 스키마/변수 칸까지 갖춰 렌더한다. spec.instant_actions 에는 셋이
    # 섞여 들어오므로(registry._jibot_spec) 여기서 걸러 낸다. recipe 를 빼지 않으면
    # 변수 칸 없는 맨 버튼이 되어, 누르면 missing recipe parameter 로 실패한다.
    extension_action_types = {
        action.action_type
        for module in getattr(spec, "action_modules", ())
        for action in module.actions
    } | {
        recipe.action_type for recipe in getattr(spec, "recipes", ())
    }
    # Emergency / quick control first: oversized STOP + stop-charge + motor card.
    emergency = _emergency_forms(spec, csrf, return_to=return_to, snapshot=snapshot)
    if emergency:
        groups.append(_command_group(
            "긴급 · 즉시 제어", emergency, head_extra=_pill("priority", "danger")
        ))
    # Order / recovery: cancel order + clear errors, kept together near the top so
    # an operator reaches them right after the emergency stop.
    recovery = "".join(
        _card_action_form(spec.key, a, csrf, return_to=return_to)
        for a in spec.instant_actions
        if a.action_type in _RECOVERY_ACTION_TYPES
    )
    if recovery:
        groups.append(_command_group("주문 / 복구", f'<div class="cmd-wrap">{recovery}</div>'))
    # Service control + Vehicle actions render as compact CARDS (label +
    # description + button) that wrap, so many controls fit in the column.
    verbs = "".join(_card_verb_form(spec.key, v, csrf, return_to=return_to) for v in _VERBS)
    groups.append(_command_group("Service control", f'<div class="cmd-wrap">{verbs}</div>'))
    actions = "".join(
        _card_action_form(spec.key, a, csrf, return_to=return_to)
        for a in spec.instant_actions
        if a.action_type not in _SOUND_ACTION_TYPES
        and a.action_type not in extension_action_types
        and a.action_type not in _DRIVE_ACTION_TYPES  # → dedicated Drive group
        and a.action_type not in _RECOVERY_ACTION_TYPES  # → dedicated 주문/복구 group
        and a.action_type not in _EMERGENCY_ACTION_TYPES  # live in the emergency group
        and a.action_type != "jibotMotionRule"  # dedicated to/from form below
        and a.action_type != "gotoNearestNode"  # dedicated button in the Goto group
        and a.action_type != "localize"  # dedicated node input in the Goto group
    )
    if actions:
        groups.append(_command_group("Vehicle actions", f'<div class="cmd-wrap">{actions}</div>'))
    # Drive: embedded manual jog D-pad + move-distance (JIBOT manualDrive).
    if _manual_enabled(spec):
        groups.append(_command_group(
            "Drive (수동)",
            _jog_card(spec, snapshot=snapshot, csrf=csrf, return_to=return_to,
                      drive_trans=drive_trans, drive_rot=drive_rot, drive_speed=drive_speed),
        ))
    sound = _sound_forms(spec, csrf, return_to=return_to, sound_state=sound_state)
    if sound:
        groups.append(_command_group("Sound", f'<div class="cmd-wrap">{sound}</div>'))
    if _runs_urobot(spec):
        goto_html = (
            _goto_form(spec.key, csrf, return_to=return_to)
            + _goto_nearest_form(spec.key, csrf, return_to=return_to)
        )
        if any(getattr(a, "action_type", "") == "localize" for a in spec.instant_actions):
            goto_html += _localize_form(spec.key, csrf, return_to=return_to)
        groups.append(_command_group("Goto (UmGoto)", f'<div class="cmd-wrap">{goto_html}</div>'))
        if any(getattr(a, "action_type", "") == "jibotMotionRule" for a in spec.instant_actions):
            groups.append(_command_group(
                "Motion rule (dock/move)",
                f'<div class="cmd-wrap">{_motion_rule_form(spec.key, csrf, return_to=return_to)}</div>',
            ))
    host_items = ""
    if host_reboot_enabled:
        host_items += _host_reboot_form(spec.key, csrf, return_to=return_to)
    if urobot_restart_enabled and _runs_urobot(spec):
        host_items += _urobot_restart_form(spec.key, csrf, return_to=return_to)
    if host_items:
        groups.append(_command_group("Host / robot", f'<div class="cmd-wrap">{host_items}</div>'))
    return "".join(groups)


def control_page(spec, metrics, csrf: str, q: dict, snapshot=None, host_reboot_enabled: bool = False, urobot_restart_enabled: bool = False) -> str:
    # The standalone /control page was merged into adapter_detail_page; the route
    # now 302-redirects there. This builder is retained only as a focused render
    # harness for _control_forms (see test_web_render control_* tests). The manual
    # jog is embedded in the Drive group of _control_forms (its own page is gone).
    jog_js = _JOG_JS if _manual_enabled(spec) else ""
    body = (
        f"{_flash(q)}<h3>{esc(spec.display_name)} — control</h3>"
        f"<p>service: {esc(metrics.active_state)}</p>"
        f"{_mqtt_live_table(spec, snapshot)}"
        f"{_control_forms(spec, csrf, host_reboot_enabled, urobot_restart_enabled=urobot_restart_enabled, snapshot=snapshot)}"
        f'<p><a href="/adapter/{esc(spec.key)}">&larr; dashboard</a> | '
        f'<a href="/adapter/{esc(spec.key)}/tests">tests</a></p>'
        f"{jog_js}"
    )
    return page(f"{spec.display_name} control", _with_poll(body, _refresh_secs(q)))


def _manual_enabled(spec) -> bool:
    """True iff spec exposes a manualDrive instant action (JIBOT only)."""
    return any(getattr(a, "action_type", "") == "manualDrive" for a in spec.instant_actions)


_JOG_JS = """
<script>
(function(){
  if(window.__amrJog) return; window.__amrJog=true;
  // Event-DELEGATION jog so it survives the dashboard's periodic #content swap
  // (per-element listeners attached on load would be wiped on every poll). The
  // .manual element + #mc-csrf/#mc-arm are re-queried at send time.
  var beat=null, dir=null;
  function root(){ return document.querySelector('.manual'); }
  function armed(){ var a=document.getElementById('mc-arm'); return !!(a&&a.checked); }
  function send(type, extra){
    var r=root(); if(!r) return;
    var key=r.getAttribute('data-key');
    var c=document.getElementById('mc-csrf'); var csrf=c?c.value:'';
    var b=new URLSearchParams();
    b.set('csrf_token',csrf); b.set('action_type',type); b.set('confirm','on');
    if(armed()) b.set('armed','on');
    if(extra){ for(var k in extra) b.set(k, extra[k]); }
    return fetch('/adapter/'+encodeURIComponent(key)+'/manual',
      {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:b.toString()});
  }
  function startDir(d){
    if(!armed()||dir===d) return;
    var r=root(); if(!r) return;
    var TRANS=parseFloat(r.getAttribute('data-trans'))||200;
    var ROT=parseFloat(r.getAttribute('data-rot'))||30;
    var DEFAULT_SPEED=parseFloat(r.getAttribute('data-speed'))||200;
    var speedEl=r.querySelector('input[name="speed"]');
    var SPEED=parseFloat(speedEl&&speedEl.value)||DEFAULT_SPEED;
    var m={up:{trans:TRANS,rot:0},down:{trans:-TRANS,rot:0},
           left:{trans:0,rot:ROT},right:{trans:0,rot:-ROT}}[d]||{trans:0,rot:0};
    dir=d; var payload={trans:m.trans, rot:m.rot, speed:SPEED};
    send('manualDrive', payload);
    if(beat) clearInterval(beat);
    beat=setInterval(function(){ if(armed()&&dir) send('manualDrive', payload); }, 300);
  }
  function stop(){ if(dir===null&&!beat) return; dir=null; if(beat){clearInterval(beat); beat=null;} send('manualStop'); }
  function dirOf(t){ var el=t&&t.closest?t.closest('.jog-btn[data-dir]'):null; return el?el.getAttribute('data-dir'):null; }
  document.addEventListener('mousedown', function(e){ var d=dirOf(e.target); if(d){e.preventDefault(); startDir(d);} });
  document.addEventListener('touchstart', function(e){ var d=dirOf(e.target); if(d){e.preventDefault(); startDir(d);} }, {passive:false});
  document.addEventListener('mouseup', function(){ stop(); });
  document.addEventListener('touchend', function(){ stop(); });
  document.addEventListener('click', function(e){ if(e.target.closest&&e.target.closest('.jog-stop')){ e.preventDefault(); stop(); } });
  var keys={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right'};
  document.addEventListener('keydown', function(e){ if(keys[e.key]&&root()){e.preventDefault(); startDir(keys[e.key]);} });
  document.addEventListener('keyup', function(e){ if(keys[e.key]){e.preventDefault(); stop();} });
  window.addEventListener('blur', stop);
  document.addEventListener('visibilitychange', function(){ if(document.hidden) stop(); });
})();
</script>
"""


def _jog_card(spec, snapshot=None, csrf: str = "", return_to: str = "dashboard",
              drive_trans: float = 200.0, drive_rot: float = 30.0,
              drive_speed: float = 200.0) -> str:
    """Embedded manual jog control (JIBOT only) as a Drive-group card: a D-pad
    (▲ / ◀ STOP ▶ / ▼) plus a Move-distance form. The .manual element carries the
    jog magnitudes; the delegation-based _JOG_JS drives it and survives the
    dashboard #content poll-swap. Each arrow sends BOTH axes explicitly (unused
    axis = 0) so the adapter does not fill the other from its config default."""
    key = esc(spec.key)
    action_url = f"/adapter/{key}/action"
    motor_state = _motor_label(snapshot) if snapshot is not None else "모터 상태 미상"
    arm = (
        '<label class="confirm jog-arm"><input type="checkbox" id="mc-arm" name="armed"> '
        'Arm (수동모드 무장)</label>'
    )
    pad = (
        '<div class="jog-pad">'
        '<button type="button" class="btn jog-btn jog-up" data-dir="up" title="전진">&#9650;</button>'
        '<button type="button" class="btn jog-btn jog-left" data-dir="left" title="좌회전">&#9668;</button>'
        '<button type="button" class="btn danger jog-stop" title="정지 (UmStop)">STOP</button>'
        '<button type="button" class="btn jog-btn jog-right" data-dir="right" title="우회전">&#9658;</button>'
        '<button type="button" class="btn jog-btn jog-down" data-dir="down" title="후진">&#9660;</button>'
        '</div>'
    )
    move = _input_card(
        action_url, csrf, return_to,
        '<input type="hidden" name="action_type" value="manualMove">'
        '<input type="hidden" name="armed" value="on">',
        title="Move distance", desc="상대 거리 이동 (mm, 음수=후진)", label="Go",
        variant="primary", confirm=True,
        fields=(
            '<span class="command-fields">'
            '<input type="number" name="distance" placeholder="distance mm" required>'
            '<input type="number" name="speed" placeholder="speed" required>'
            '<input type="number" name="flag" value="1" placeholder="flag">'
            '<input type="number" name="io" value="1" placeholder="io">'
            '<input type="number" name="obs_avoid_dist" value="1000" placeholder="obs avoid">'
            '<input type="number" name="side_avoid_dist" value="50" placeholder="side avoid">'
            '<select name="use_io" title="use_io">'
            '<option value="false" selected>use_io false</option>'
            '<option value="true">use_io true</option>'
            '</select>'
            '<select name="run_mode" title="run_mode">'
            '<option value="scheduler" selected>UmSchedulerThis</option>'
            '<option value="set_routes">UmSetRoutes + UmRoutes</option>'
            '</select>'
            '<input type="number" name="note" value="1" placeholder="note"></span>'
        ),
    )
    jog = (
        f'<div class="manual jog-card action-card" data-key="{key}" '
        f'data-trans="{float(drive_trans)}" data-rot="{float(drive_rot)}" '
        f'data-speed="{float(drive_speed)}">'
        f'<input type="hidden" id="mc-csrf" value="{esc(csrf)}">'
        f'<div class="jog-head">{arm}'
        f'<span class="ac-state">현재: {esc(motor_state)}</span></div>'
        f'{pad}'
        '<label class="mini-field">speed '
        f'<input type="number" name="speed" value="{float(drive_speed)}" step="any" min="0"></label>'
        '<p class="muted">누르는 동안 주행, 떼면 정지 · 키보드 화살표키도 동작 · Arm 필요</p>'
        '<p class="muted">범위 참고: 전진/후진 0-500, 회전 0-90. '
        'UmDrive speed 기본 200, 별도 최대값 미확인.</p>'
        '</div>'
    )
    return f'<div class="cmd-wrap">{jog}{move}</div>'


def _row_attr(row, name: str, default=None):
    return getattr(row, name, default)


def _row_value(row, attr: str, index: int):
    if hasattr(row, attr):
        return getattr(row, attr)
    return row[index]


def _badge_class(label: str) -> str:
    return label.replace(" ", "-")


def _config_row_meta(section: str, key: str, row=None) -> str:
    location = _row_attr(row, "location")
    badges = _row_attr(row, "badges", ()) or ()
    key_path = getattr(location, "key_path", f"[{section}].{key}")
    source = ""
    if location is not None:
        suffix = f":{location.line}" if getattr(location, "line", 0) else ""
        source = f'<span class="cfg-source">{esc(location.path)}{esc(suffix)}</span>'
    badge_html = "".join(
        f'<span class="cfg-badge {_badge_class(str(badge))}">{esc(badge)}</span>'
        for badge in badges
    )
    return (
        '<span class="cfg-meta">'
        f'<span class="cfg-source">{esc(key_path)}</span>'
        f'{source}'
        f'{badge_html}'
        '</span>'
    )


def _config_field_form(section: str, key: str, kind: str, value, csrf: str, row=None) -> str:
    """One scalar config value rendered as its OWN form, so each value is saved
    independently (clicking 저장 posts only this field)."""
    if kind == "bool":
        cur = str(value).lower()
        opts = "".join(
            f'<option value="{v}"{" selected" if cur == v else ""}>{v}</option>'
            for v in ("true", "false")
        )
        inp = f'<select name="value">{opts}</select>'
    else:
        itype = "number" if kind in ("int", "num") else "text"
        step = ' step="any"' if kind == "num" else ""
        inp = f'<input type="{itype}"{step} name="value" value="{esc(value)}">'
    desc = configio.field_description(section, key)
    return (
        '<form class="cfg-row" method="post" action="/config">'
        f"{_csrf_field(csrf)}"
        f'<input type="hidden" name="section" value="{esc(section)}">'
        f'<input type="hidden" name="key" value="{esc(key)}">'
        f'<input type="hidden" name="kind" value="{esc(kind)}">'
        '<label class="cfg-label">'
        f'<span class="cfg-label-main">{esc(key)} <span class="muted">({esc(kind)})</span></span>'
        f'{_config_row_meta(section, key, row)}'
        f'<span class="cfg-help">{esc(desc)}</span>'
        '</label>'
        f'{inp}<button type="submit">저장</button>'
        "</form>"
    )


def config_page(sections, csrf: str, q: dict) -> str:
    """Full config.toml editor — every scalar value gets its own save button,
    grouped by [section]. Lists/arrays are read-only (edit in config.toml).

    ``sections``: iterable of ``(section, scalars, readonly)`` where
    ``scalars`` is ``[(key, kind, value), ...]`` and ``readonly`` is
    ``[(key, value), ...]`` (see ``configio.iter_config_sections``).
    """
    blocks = []
    for section, scalars, readonly in sections:
        if not scalars and not readonly:
            continue
        rows = "".join(
            _config_field_form(
                section,
                _row_value(row, "key", 0),
                _row_value(row, "kind", 1),
                _row_value(row, "value", 2),
                csrf,
                row=row,
            )
            for row in scalars
        )
        ro = ""
        if readonly:
            items = "".join(
                "<tr>"
                f"<td>{esc(_row_value(row, 'key', 0))}</td>"
                f"<td>{_config_row_meta(section, _row_value(row, 'key', 0), row)}</td>"
                f"<td><code>{esc(str(_row_value(row, 'value', 1)))}</code></td>"
                "</tr>"
                for row in readonly
            )
            ro = (
                '<details><summary class="muted">읽기전용 (리스트/배열 — '
                f"config.toml에서 편집)</summary><table>{items}</table></details>"
            )
        blocks.append(
            f'<section class="cfg-group"><h3>[{esc(section)}]</h3>{rows}{ro}</section>'
        )
    body = (
        f"{_flash(q)}"
        '<p class="muted">이 페이지는 현재 로드된 config.toml의 scalar 값을 편집합니다. '
        '각 행에는 실제 파일 경로와 TOML 키 위치가 표시됩니다. robots.hcl은 실제 '
        '로봇 identity/connectivity를 덮어쓸 수 있습니다. <strong>robot override</strong> '
        '배지가 붙은 값은 <a href="/robots">Robots 설정</a>의 대응 값이 있으면 '
        '그 값으로 대체됩니다. jibot-config.toml은 JIBOT '
        '하드웨어 특성 overlay로 병합될 수 있습니다. 저장 후 적용하려면 어댑터 '
        '서비스 재시작이 필요합니다. 리스트/배열은 읽기전용입니다.</p>'
        '<p class="muted">이 폼은 <strong>값 하나씩만</strong> 고칩니다. recipe·extension '
        '<strong>블록 추가·삭제</strong>는 '
        '<a href="/source/recipes.hcl">Recipes</a> · '
        '<a href="/source/extensions.hcl">Extensions</a> 원문 편집에서 합니다.</p>'
        f"{''.join(blocks)}"
    )
    return page("config", body, current="/config")


def source_page(source: str, text: str, source_path, csrf: str, q: dict, blocked: str = "") -> str:
    """원문 편집기 — MW 의 EPR 원문 편집과 같은 파일을 로봇에서도 고칠 수 있게 한다.

    값 단위 폼(/config)으로는 recipe/extension **블록을 추가·삭제할 수 없다**. MW 에서만
    되고 로봇에서는 안 되는 상태를 두면, 어댑터가 못 뜨거나 MW 가 닿지 않을 때 고칠 창구가
    없어진다. 저장 경로는 MW 와 같은 ``configio.write_config`` + 부팅 로더 재검증이다.

    :param source: 대상 파일명(``recipes.hcl`` 등)
    :param text: 현재 파일 원문
    :param source_path: 화면에 표시할 실제 경로
    :param csrf: CSRF 토큰
    :param q: 쿼리스트링(플래시 메시지)
    :param blocked: 편집을 막아야 하는 사유. 비어 있지 않으면 읽기전용으로 렌더한다
    :returns: HTML 문자열
    """
    current = f"/source/{source}"
    if blocked:
        form = (
            f'<p class="cfg-source">{esc(str(source_path))}</p>'
            f'<p class="muted"><strong>원문 편집이 막혀 있습니다.</strong> {esc(blocked)}</p>'
            '<textarea rows="32" readonly style="width:100%;font-family:monospace">'
            f"{esc(text)}</textarea>"
        )
    else:
        form = (
            f'<p class="cfg-source">{esc(str(source_path))}</p>'
            f'<form method="post" action="{current}">'
            f"{_csrf_field(csrf)}"
            '<textarea name="text" rows="32" style="width:100%;font-family:monospace">'
            f"{esc(text)}</textarea>"
            f'<p><button type="submit">{esc(source)} 저장</button></p>'
            "</form>"
        )
    body = (
        f"{_flash(q)}"
        f'<p class="muted"><code>{esc(source)}</code> 원문을 그대로 편집합니다. '
        'recipe·extension <strong>블록 추가·삭제와 순서 변경</strong>은 여기서만 가능합니다'
        '(<a href="/config">Config</a> 폼은 값 하나씩만 고칩니다). 저장하면 부팅 로더로 '
        '재검증하고, 실패하면 원래 내용으로 되돌립니다. 적용하려면 어댑터 서비스를 '
        '재시작해야 합니다.</p>'
        f"{form}"
    )
    return page(source, body, current=current)


def robots_page(text: str, source_path, csrf: str, q: dict) -> str:
    """Comment-preserving raw editor for the HCL fleet file."""
    body = (
        f"{_flash(q)}"
        '<p class="muted"><code>robots.hcl</code>은 로봇별 identity와 연결 설정의 '
        'override 파일입니다. <code>mqtt_host</code>/<code>mqtt_port</code> 등이 '
        '있으면 <a href="/config">config.toml 기본값</a>보다 우선합니다. 공통값을 '
        '사용하려면 해당 로봇의 override 줄을 제거하세요. 저장 후 어댑터 서비스를 '
        '재시작해야 적용됩니다.</p>'
        f'<p class="cfg-source">{esc(source_path)}</p>'
        '<form method="post" action="/robots">'
        f"{_csrf_field(csrf)}"
        '<textarea name="text" rows="32" style="width:100%;font-family:monospace">'
        f"{esc(text)}</textarea>"
        '<p><button type="submit">robots.hcl 저장</button></p>'
        "</form>"
    )
    return page("robots", body, current="/robots")


def _param_rows(parameters, values=None, scope: str = "") -> str:
    """Action parameter inputs.

    Both extensions and recipes carry an explicit schema, so an action shows
    only the fields it accepts and every field has a visible parameter name.

    A parameter that declares ``choices`` renders as a select unless it also
    declares ``editable``. Free text for a two-value parameter makes a typo the
    operator's problem — pioWriteOut's state only accepts on/off, and the
    hand-written PIO panel has always given a dropdown for it.

    ``values`` pre-fills from what was last submitted for this action. The
    action POST redirects, so without this every field comes back empty and the
    operator retypes the same pins to run it again.

    ``scope`` (액션 타입)는 datalist id 를 액션마다 다르게 만든다. 한 화면에
    같은 이름의 파라미터가 여러 액션에 있으므로(stationId) id 가 겹치면 브라우저가
    첫 번째 것만 쓴다.
    """
    values = values or {}
    rows = []
    for parameter in parameters:
        name = getattr(parameter, "name", str(parameter))
        rows.append(
            '<label class="ap-row">'
            f"{_param_key(parameter)}"
            f"{_param_field(parameter, values.get(name), scope)}"
            "</label>"
        )
    return f'<div class="ap-grid">{"".join(rows)}</div>' if rows else ""


def _param_key(parameter) -> str:
    """칸 이름표. 전송되는 키(파라미터 이름)를 앞에 두고 설명을 뒤에 붙인다.

    이름만으로는 무엇을 넣는 칸인지 알 수 없는 것이 많다(``media``, ``port``).
    설명은 ``label`` 이며, 없으면 이름만 나온다. 카드가 좁아 잘릴 수 있으므로
    전체 문구는 ``title`` 로도 남긴다.
    """
    name = getattr(parameter, "name", str(parameter))
    label = str(getattr(parameter, "label", "") or "").strip()
    title = f"{name} · {label}" if label else name
    hint = f' <span class="ap-hint">· {esc(label)}</span>' if label else ""
    return f'<span class="ap-key" title="{esc(title)}">{esc(name)}{hint}</span>'


def _param_field(parameter, current=None, scope: str = "") -> str:
    """액션 파라미터 하나의 입력 요소.

    ``choices`` 를 선언하면 select 다. 선택지는 설정에서 온다 —
    ``pioWriteOut`` 의 ``signal`` 은 ``extensions.hcl`` 의 ``output_signals`` 이고,
    ``pioPing`` 의 ``stationId`` 는 ``extension "airshower"`` 와 elevator motion
    rule 이다. 그래서 이 목록을 손으로 쓴 ``panel.html`` 에 박으면 설정을 바꿔도
    따라오지 않는다. 패널은 대신
    ``$field_<actionType>_<name>`` 으로 이 함수의 결과를 끌어다 쓴다.

    ``editable`` 까지 선언하면 목록은 힌트일 뿐이라는 뜻이라 datalist 를 단 입력이
    된다 — 설정이 아는 값은 골라 넣고, 아직 설정에 없는 설비도 손으로 칠 수 있다.
    설정이 비어 선택지가 하나도 없을 때 required select 가 아무것도 고를 수 없는
    칸이 되는 것도 이 쪽이 막아 준다.
    """
    name = getattr(parameter, "name", str(parameter))
    input_type = getattr(parameter, "input_type", "text")
    label = str(getattr(parameter, "label", "") or "").strip()
    placeholder = getattr(parameter, "placeholder", "") or label or "value"
    required = " required" if getattr(parameter, "required", False) else ""
    title = f' title="{esc(label)}"' if label else ""
    choices = tuple(getattr(parameter, "choices", ()) or ())
    if choices and not getattr(parameter, "editable", False):
        options = "".join(
            f'<option value="{esc(choice)}"'
            f'{" selected" if str(current) == str(choice) else ""}'
            f">{esc(choice)}</option>"
            for choice in choices
        )
        return (
            f'<select class="ap-val" name="{esc(name)}"{required}'
            f' aria-label="{esc(name)}"{title}>{options}</select>'
        )
    list_attr, datalist = "", ""
    if choices:
        list_id = _datalist_id(scope, name)
        options = "".join(
            f'<option value="{esc(choice)}"></option>'
            for choice in choices if str(choice) != ""
        )
        list_attr = f' list="{list_id}"'
        datalist = f'<datalist id="{list_id}">{options}</datalist>'
    value_attr = f' value="{esc(str(current))}"' if current not in (None, "") else ""
    return (
        f'<input class="ap-val" type="{esc(input_type)}" name="{esc(name)}" '
        f'placeholder="{esc(placeholder)}"{value_attr}{required}{list_attr} '
        f'aria-label="{esc(name)}"{title}>{datalist}'
    )


def _datalist_id(scope: str, name: str) -> str:
    """datalist 의 DOM id. 액션 타입으로 묶어 한 화면 안에서 겹치지 않게 한다."""
    return "dl-" + re.sub(r"[^A-Za-z0-9_-]", "-", f"{scope}-{name}").strip("-")


def _run_form(spec_key: str, action_type: str, label: str, motion: bool,
              csrf: str, meta: str, parameters=(), acted: bool = False,
              values=None) -> str:
    # .cfg-badge is the styled badge in this stylesheet; a bare .badge has no
    # CSS and renders glued to the action name ("clampmotion").
    badge = '<span class="cfg-badge motion">motion</span>' if motion else ""
    anchor = action_anchor(action_type)
    cls = "run-card acted" if acted else "run-card"
    # 설비 출력을 실제로 움직이는 액션은 체크박스를 거친다. 서버도 같은 목록으로
    # 거절하므로(web.server._CONFIRM_REQUIRED_ACTIONS) 폼을 우회해도 막힌다.
    confirm = (
        '<label class="confirm"><input type="checkbox" name="confirm" required>'
        " confirm</label>"
        if action_type in CONFIRM_REQUIRED_ACTIONS else ""
    )
    return (
        f'<form id="{anchor}" class="{cls}" method="post" '
        f'action="/adapter/{esc(spec_key)}/action">'
        f"{_csrf_field(csrf)}"
        f'{_return_to_field(f"/adapter/{spec_key}/actions#{anchor}")}'
        f'<input type="hidden" name="action_type" value="{esc(action_type)}">'
        f'<div class="rc-head"><code>{esc(action_type)}</code> {badge}</div>'
        f'<div class="rc-meta">{esc(label)} — {esc(meta)}</div>'
        f"{_param_rows(parameters, values, action_type)}"
        f'{confirm}<button class="btn">실행</button>'
        "</form>"
    )


def actions_page(spec, csrf: str, q: dict, remembered=None, io=None) -> str:
    """Every extension and recipe this robot can run, with a run form each.

    이 화면이 extension 의 유일한 창구다 — 어댑터 페이지는 로봇 자체 제어만 담고,
    여기 나오는 것은 전부 discover 된 모듈과 ``recipes.hcl`` 이 정한다. 그래서 새
    extension 이 붙어도 이 함수를 고칠 일이 없다.
    """
    remembered = remembered or {}
    return_to = f"/adapter/{spec.key}/actions"
    blocks = []

    for module in spec.action_modules:
        status = _module_status_panel(
            spec.key, spec.display_name, module, io, csrf, return_to
        )
        # 모듈이 panel.html 을 주면 그게 이 모듈의 화면이다. 다만 패널은 손으로 쓴
        # 파일이라 모듈보다 뒤처질 수 있으므로(액션을 추가하고 패널을 안 고친 경우)
        # 패널이 다루지 않는 액션만 스키마 생성 폼으로 보완한다 — 그래야 액션이
        # 조용히 사라지지 않는다.
        panel = _module_panel(spec.key, module, csrf, return_to, remembered)
        covered = set(_PANEL_ACTION_RE.findall(panel))
        cards = "".join(
            _run_form(
                spec.key, action.action_type, action.label, action.motion,
                csrf, module.title,
                parameters=getattr(action, "parameters", ()),
                acted=q.get("act") == action.action_type,
                values=remembered.get(action.action_type),
            )
            for action in module.actions
            if action.action_type not in covered
        )
        leftover = (
            _command_group(
                f"{module.title} — 그 외" if panel else module.title,
                f'<div class="run-wrap">{cards}</div>',
            )
            if cards else ""
        )
        if panel or leftover:
            blocks.append(status + panel + leftover)

    extensions_html = "".join(blocks) or (
        '<p class="muted">등록된 extension 액션이 없습니다.</p>'
    )

    if spec.recipes:
        cards = "".join(
            _run_form(
                spec.key, view.action_type, view.label, view.motion, csrf,
                f"step {view.step_count} · cleanup {view.cleanup_count}",
                parameters=view.variables,
                acted=q.get("act") == view.action_type,
                values=remembered.get(view.action_type),
            )
            for view in spec.recipes
        )
        recipes_html = f'<div class="run-wrap">{cards}</div>'
    else:
        recipes_html = (
            '<p class="muted">정의된 recipe가 없습니다. '
            '<code>config/recipes.hcl.example</code>을 '
            '<code>config/recipes.hcl</code>로 복사한 뒤 어댑터를 재시작하세요.</p>'
        )

    body = (
        f"{_flash(q)}"
        '<p class="muted">이 로봇이 실행할 수 있는 액션입니다. 전송 경로는 '
        '상세 화면의 액션 버튼과 같습니다 — VDA5050 instant action으로 발행됩니다. '
        '<strong>extension</strong>은 액션 스키마에 선언된 파라미터만, '
        '<strong>recipe</strong>는 정의에서 뽑은 변수 칸만 표시됩니다. '
        '값을 비우면 그 파라미터는 전송되지 않습니다.</p>'
        f"{_command_group('Extensions', extensions_html)}"
        f"{_command_group('Recipes', recipes_html)}"
    )
    return page(f"{spec.display_name} · actions", body, current="/actions")


def actions_index_page(specs, q: dict) -> str:
    """Adapter picker for the global /actions nav link.

    The nav is robot-agnostic but every actions page is robot-scoped, so with
    more than one adapter the operator picks one here. A single adapter never
    reaches this page — the route redirects straight to it."""
    rows = "".join(
        f'<tr><td><a href="/adapter/{esc(spec.key)}/actions">'
        f"{esc(spec.display_name)}</a></td><td><code>{esc(spec.key)}</code></td></tr>"
        for spec in specs
    )
    if rows:
        body = (
            f"{_flash(q)}"
            '<p class="muted">액션은 로봇별로 다릅니다. 실행할 로봇을 고르세요.</p>'
            f"<table><tr><th>adapter</th><th>key</th></tr>{rows}</table>"
        )
    else:
        body = f'{_flash(q)}<p class="muted">등록된 어댑터가 없습니다.</p>'
    return page("actions", body, current="/actions")


def factsheet_page(preview_json: str, q: dict) -> str:
    """Read-only preview of the rendered VDA5050 factsheet.

    Editing factsheet values lives on /config (the ``[factsheet]`` section);
    this page only shows what will actually be published, including computed
    fields (agvActions, videoStreams, derived speeds) that do not appear in the
    flat config form.
    """
    body = (
        f"{_flash(q)}<h3>Rendered factsheet (read-only)</h3>"
        '<p class="muted"><code>[factsheet]</code> 값 편집은 '
        '<a href="/config">/config</a> 에서 합니다. 아래는 발행될 factsheet JSON '
        "미리보기입니다 (agvActions·videoStreams 등 파생값 포함).</p>"
        f"<pre>{esc(preview_json)}</pre>"
    )
    return page("factsheet", body, current="/factsheet")


def tests_page(spec, runner, csrf: str, q: dict, snapshot=None, test_output_poll_sec: int = 2) -> str:
    listing = _tests_listing(spec, csrf, snapshot)
    output, refresh = _tests_output(spec.key, runner, csrf, poll_sec=test_output_poll_sec)
    body = (
        f"{_flash(q)}<h3>{esc(spec.display_name)} — tests</h3>"
        f"{_mqtt_live_table(spec, snapshot)}{listing}{output}"
        f'<p><a href="/adapter/{esc(spec.key)}">&larr; dashboard</a></p>'
    )
    # Update test output/listing in place (swap #content), not a meta refresh.
    return page(f"{spec.display_name} tests", _with_poll(body, _refresh_secs(q)))


def _tests_listing(spec, csrf: str, snapshot=None, return_to: str = "") -> str:
    runnable = tuple(spec.runnable)
    if not runnable:
        return "(none)"
    manual_block_reason = _manual_test_block_reason(snapshot)
    rows = []
    blocked = 0
    for i, d in enumerate(runnable):
        manual_required = bool(getattr(d, "requires_manual", False))
        disabled = manual_required and bool(manual_block_reason)
        scope = esc(getattr(d, "note", "")) or "—"
        if disabled:
            blocked += 1
            status = (
                f'{_pill("blocked", "danger")} '
                f'<span class="err">{esc(manual_block_reason)}</span>'
            )
            run = '<button disabled class="btn">Locked</button>'
        else:
            status = _pill("ready", "success")
            run = (
                f'<form class="btn" method="post" action="/adapter/{esc(spec.key)}/tests/run">'
                f'{_csrf_field(csrf)}{_return_to_field(return_to)}'
                f'<input type="hidden" name="index" value="{esc(i)}">'
                '<button class="btn primary">Run</button></form>'
            )
        rows.append(
            f'<tr><td class="num">{esc(i)}</td><td>{esc(d.label)}</td>'
            f"<td>{scope}</td><td>{status}</td><td>{run}</td></tr>"
        )
    total = len(runnable)
    chips = (
        '<div class="toolbar"><div class="chip-row">'
        f'<span class="chip"><span class="dot accent"></span>All {total}</span>'
        f'<span class="chip"><span class="dot success"></span>Runnable {total - blocked}</span>'
        f'<span class="chip"><span class="dot danger"></span>Blocked {blocked}</span>'
        "</div></div>"
    )
    return (
        f"{chips}"
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Index</th><th>Test</th><th>Scope</th><th>Status</th><th>Run</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _tests_output(spec_key: str, runner, csrf: str, return_to: str = "", poll_sec: int = 2) -> tuple[str, int | None]:
    out_lines = runner.lines() if runner is not None else []
    output = ""
    refresh = None
    if runner is not None and (runner.label or out_lines):
        if runner.running:
            status = "running"
            refresh = poll_sec
            stop = (
                f'<form class="btn" method="post" action="/adapter/{esc(spec_key)}/tests/stop">'
                f"{_csrf_field(csrf)}{_return_to_field(return_to)}<button>stop</button></form>"
            )
        else:
            status = f"exit {runner.returncode}" if runner.returncode is not None else "idle"
            stop = ""
        text = "\n".join(esc(line) for line in out_lines)
        output = f"<h4>{esc(runner.label)} [{esc(status)}]</h4>{stop}<pre>{text}</pre>"
    return output, refresh
