"""Progressive UX overlay for the generated Momentum Chimpan dashboard.

The overlay improves navigation, mobile exploration and repeat use without
changing the workbook payload or any governed research logic.  It is applied to
an already generated static site and then re-seals the site manifest.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from site_builder import canonical_hash, sha256_file

EXPERIENCE_VERSION = "2026-07-14-daily-decision-ux-v2"
HEAD_MARKER = '<link rel="stylesheet" href="assets/experience.css">'
BODY_MARKER = '<script src="assets/experience.js"></script>'

EXPERIENCE_CSS = r'''
:root{--ux-danger:#991b1b;--ux-warn:#9a3412;--ux-safe:#166534}
:focus-visible{outline:3px solid #60a5fa;outline-offset:3px}
.ux-health{max-width:1180px;margin:18px auto 0;padding:0 24px}.ux-health-inner{display:flex;gap:12px;align-items:flex-start;border-radius:16px;padding:13px 15px;border:1px solid #bfdbfe;background:#eff6ff;color:#1e3a8a;font-size:13px}.ux-health-inner.warn{border-color:#fed7aa;background:#fff7ed;color:var(--ux-warn)}.ux-health-inner.danger{border-color:#fecaca;background:#fef2f2;color:var(--ux-danger)}.ux-health-title{font-weight:950;white-space:nowrap}.ux-health-copy{line-height:1.6}
.ux-brief{max-width:1180px;margin:22px auto 0;padding:0 24px}.ux-brief-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}.ux-brief-card{background:#fff;border:1px solid var(--line);border-radius:20px;padding:18px;box-shadow:0 6px 24px rgba(15,23,42,.04)}.ux-brief-card h2{font-size:18px;margin:2px 0 8px}.ux-brief-card p{font-size:13px;color:#475569;margin:0;line-height:1.7}.ux-change-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.ux-change-chip{font-size:11px;font-weight:900;border-radius:999px;padding:5px 9px;background:#e8eef7}.ux-caution{border-color:#fed7aa;background:#fff7ed}.ux-caution h2{color:#9a3412}
.ux-toolbar{position:fixed;right:18px;bottom:18px;z-index:80;display:flex;gap:8px;align-items:center;background:#0f172a;color:#fff;border-radius:999px;padding:8px;box-shadow:0 18px 50px rgba(15,23,42,.28)}.ux-toolbar button{border:0;border-radius:999px;background:#1e293b;color:#fff;padding:9px 12px;font-weight:850;cursor:pointer}.ux-toolbar button:hover{background:#334155}.ux-count{display:inline-grid;place-items:center;min-width:20px;height:20px;margin-left:5px;border-radius:999px;background:#2563eb;font-size:10px}
.ux-row-actions{display:flex;gap:5px;margin-top:7px}.ux-mini-button{border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:9px;padding:5px 8px;font-size:10px;font-weight:850;cursor:pointer}.ux-mini-button.active{background:#0f172a;color:#fff;border-color:#0f172a}.ux-mini-button.compare-active{background:#dbeafe;color:#1d4ed8;border-color:#93c5fd}
.ux-mobile-ranking{display:none}.ux-mobile-card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;margin-top:10px}.ux-mobile-top{display:flex;justify-content:space-between;gap:12px}.ux-mobile-name{font-weight:950}.ux-mobile-code{font-size:10px;color:#64748b}.ux-mobile-rank{font-size:20px;font-weight:950;color:#2563eb}.ux-mobile-kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}.ux-mobile-kpi{background:#f8fafc;border-radius:10px;padding:8px}.ux-mobile-kpi span{display:block;font-size:9px;color:#64748b}.ux-mobile-kpi b{font-size:13px}.ux-mobile-tags{margin-top:8px;font-size:10px;color:#475569}
.ux-filter-summary{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0 0}.ux-filter-pill{border:1px solid #cbd5e1;background:#fff;border-radius:999px;padding:5px 9px;font-size:10px;font-weight:800}.ux-filter-pill button{border:0;background:transparent;margin-left:4px;cursor:pointer;color:#64748b}
.ux-panel-backdrop{position:fixed;inset:0;z-index:100;background:rgba(15,23,42,.52);display:none;align-items:flex-end;justify-content:center;padding:18px}.ux-panel-backdrop.open{display:flex}.ux-panel{width:min(960px,100%);max-height:86vh;overflow:auto;background:#f8fafc;border-radius:24px;padding:20px;box-shadow:0 30px 80px rgba(15,23,42,.35)}.ux-panel-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.ux-panel-head h2{margin:0}.ux-close{border:0;background:#e2e8f0;border-radius:999px;width:38px;height:38px;font-size:20px;cursor:pointer}.ux-compare-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}.ux-compare-card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:15px}.ux-compare-card h3{margin:0 0 10px}.ux-compare-list{display:grid;gap:7px}.ux-compare-row{display:flex;justify-content:space-between;gap:12px;font-size:12px;border-top:1px solid #edf2f7;padding-top:7px}.ux-watch-list{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:16px}
.ux-detail-actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}.ux-sector-link{cursor:pointer;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}.ux-sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
@media(max-width:760px){.ux-health,.ux-brief{padding:0 16px}.ux-brief-grid{grid-template-columns:1fr}.table-wrap{display:none}.ux-mobile-ranking{display:block}.ux-toolbar{right:10px;bottom:10px}.ux-compare-grid,.ux-watch-list{grid-template-columns:1fr}.ux-panel-backdrop{padding:8px}.ux-panel{border-radius:20px}.ux-mobile-kpis{grid-template-columns:repeat(3,1fr)}}
'''.strip()

EXPERIENCE_JS = r'''
(() => {
"use strict";
const d=window.MOMENTUM_DASHBOARD||{};const s=d.summary||{};
const $=id=>document.getElementById(id);const num=(v,f=0)=>Number.isFinite(Number(v))?Number(v):f;
const bool=v=>v===true||String(v).toLowerCase()==="true"||v===1;
const esc=v=>String(v??"—").replace(/[&<>\'\"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const pct=v=>Number.isFinite(Number(v))?`${(Number(v)*100).toFixed(1)}%`:"—";
const money=v=>{const n=Number(v);if(!Number.isFinite(n))return"—";if(Math.abs(n)>=1e8)return`${(n/1e8).toFixed(1)}億円`;return`${Math.round(n/1e4).toLocaleString()}万円`};
const storage={watch:"momentum-watchlist-v2",compare:"momentum-compare-v2"};
const read=key=>{try{return JSON.parse(localStorage.getItem(key)||"[]")}catch{return[]}};
const write=(key,value)=>{localStorage.setItem(key,JSON.stringify(value));updateToolbar()};
const codeOf=v=>String(v??"").split(".")[0].padStart(4,"0");
const stockByCode=code=>(d.top100||[]).find(r=>codeOf(r.code)===codeOf(code));
function announce(message){let live=$("uxLive");if(!live){live=document.createElement("div");live.id="uxLive";live.className="ux-sr-only";live.setAttribute("aria-live","polite");document.body.appendChild(live)}live.textContent=message}
function warningState(){const report=String(s["実行日"]||""),price=String(s["株価データ日"]||s["最新株価日"]||"");const fresh=String(s["市場データ鮮度"]||"FRESH").toUpperCase();const p0=num(s["運用P0アラート"]),p1=num(s["運用P1アラート"]);if((report&&price&&report!==price)||!["FRESH","PASS"].includes(fresh)||String(s["状態更新実行"]||"").toUpperCase()==="NO")return{level:"danger",title:"データ要確認",copy:`株価データ日 ${price||"—"}。最新性を確認するまで銘柄評価より運用状態を優先してください。`};if(p0>0)return{level:"danger",title:"P0アラート",copy:`重大な運用警告が${p0}件あります。詳細確認までランキング利用を保留してください。`};if(p1>0)return{level:"warn",title:"P1アラート",copy:`運用警告が${p1}件あります。候補を見る前に運用品質を確認してください。`};return{level:"safe",title:"データ正常",copy:`株価データ日 ${price||"—"}・Run Health ${s["Run Health"]||"UNKNOWN"}。表示は売買推奨ではなく調査順です。`}}
function primaryCaution(){const a=(d.actions||[]).filter(r=>bool(r.daily_action_list));for(const r of a){const risk=String(r.risk_summary||r.caution_reasons||"").trim();if(risk&&!/特記事項なし|過熱注意なし/.test(risk))return risk}const c=num(s["Data Quality C"])+num(s["Data Quality D"]);if(c)return`品質C/Dが${c}件あります。候補ごとの警告理由を確認してください。`;const heat=num(s["Top100 過熱銘柄数"]);if(heat)return`Top100内の過熱判定は${heat}件です。上昇率だけで追わないでください。`;return"最新開示・チャート・流動性を確認し、スコアだけで判断しないでください。"}
function renderHealth(){const state=warningState();const wrap=document.createElement("section");wrap.className="ux-health";wrap.setAttribute("aria-label","データと運用状態");wrap.innerHTML=`<div class="ux-health-inner ${state.level}"><div class="ux-health-title">${esc(state.title)}</div><div class="ux-health-copy">${esc(state.copy)}</div></div>`;document.querySelector("header.topbar")?.insertAdjacentElement("afterend",wrap)}
function renderBrief(){const changes=d.priority_changes||[];const count=status=>changes.filter(r=>r.status===status).length;const selected=(d.actions||[]).filter(r=>bool(r.daily_action_list)).length;const section=document.createElement("section");section.className="ux-brief";section.innerHTML=`<div class="ux-brief-grid"><article class="ux-brief-card"><div class="eyebrow">TODAY IN 3 MINUTES</div><h2>今日の見方</h2><p>${esc((s["Market Regime"]||"判定待ち")+" "+num(s["Market Regime Score"])+"点。調査候補"+selected+"件から、理由・変化・注意の順に確認してください。")}</p><div class="ux-change-row"><span class="ux-change-chip">新規 ${count("新規")}</span><span class="ux-change-chip">継続 ${count("継続")}</span><span class="ux-change-chip">脱落 ${count("脱落")}</span><span class="ux-change-chip">急上昇 ${num(s["急上昇"])}</span></div></article><article class="ux-brief-card ux-caution"><div class="eyebrow" style="color:#9a3412">PRIMARY CAUTION</div><h2>最大の注意</h2><p>${esc(primaryCaution())}</p></article></div>`;document.querySelector("#summaryMetrics")?.insertAdjacentElement("afterend",section)}
function createToolbar(){const bar=document.createElement("div");bar.className="ux-toolbar";bar.innerHTML=`<button type="button" id="uxWatchButton">保存 <span id="uxWatchCount" class="ux-count">0</span></button><button type="button" id="uxCompareButton">比較 <span id="uxCompareCount" class="ux-count">0</span></button>`;document.body.appendChild(bar);const backdrop=document.createElement("div");backdrop.id="uxPanelBackdrop";backdrop.className="ux-panel-backdrop";backdrop.innerHTML=`<section class="ux-panel" role="dialog" aria-modal="true" aria-labelledby="uxPanelTitle"><div class="ux-panel-head"><h2 id="uxPanelTitle">保存銘柄</h2><button class="ux-close" id="uxClosePanel" aria-label="閉じる">×</button></div><div id="uxPanelBody"></div></section>`;document.body.appendChild(backdrop);$("uxClosePanel").onclick=closePanel;backdrop.addEventListener("click",e=>{if(e.target===backdrop)closePanel()});$("uxWatchButton").onclick=()=>openPanel("watch");$("uxCompareButton").onclick=()=>openPanel("compare");updateToolbar()}
function closePanel(){$("uxPanelBackdrop")?.classList.remove("open")}
function updateToolbar(){$("uxWatchCount")&&($("uxWatchCount").textContent=read(storage.watch).length);$("uxCompareCount")&&($("uxCompareCount").textContent=read(storage.compare).length);decorateAll()}
function toggle(key,code,max=100){let values=read(key);code=codeOf(code);if(values.includes(code))values=values.filter(x=>x!==code);else{if(values.length>=max)values.shift();values.push(code)}write(key,values);announce(`${code}を${key===storage.watch?"保存":"比較"}リスト${values.includes(code)?"に追加":"から削除"}しました`)}
function rowButtons(code){const watch=read(storage.watch).includes(codeOf(code)),compare=read(storage.compare).includes(codeOf(code));return `<div class="ux-row-actions"><button type="button" class="ux-mini-button ux-watch ${watch?"active":""}" data-code="${esc(codeOf(code))}">${watch?"保存済":"保存"}</button><button type="button" class="ux-mini-button ux-compare ${compare?"compare-active":""}" data-code="${esc(codeOf(code))}">${compare?"比較中":"比較"}</button></div>`}
function bindButtons(root=document){root.querySelectorAll(".ux-watch").forEach(b=>{if(b.dataset.bound)return;b.dataset.bound="1";b.onclick=e=>{e.stopPropagation();toggle(storage.watch,b.dataset.code)}});root.querySelectorAll(".ux-compare").forEach(b=>{if(b.dataset.bound)return;b.dataset.bound="1";b.onclick=e=>{e.stopPropagation();toggle(storage.compare,b.dataset.code,3)}})}
function decorateActions(){document.querySelectorAll("#actionCards .action-card").forEach((card,i)=>{if(card.querySelector(".ux-row-actions"))return;const selected=(d.actions||[]).filter(r=>bool(r.daily_action_list)).sort((a,b)=>num(a.daily_action_rank,999)-num(b.daily_action_rank,999));const code=selected[i]?.code;if(code)card.insertAdjacentHTML("beforeend",rowButtons(code))});bindButtons(document.querySelector("#actionCards")||document)}
function decorateRows(){document.querySelectorAll("#rankingBody tr[data-code]").forEach(tr=>{const cell=tr.querySelector("td:nth-child(2)");if(cell&&!cell.querySelector(".ux-row-actions"))cell.insertAdjacentHTML("beforeend",rowButtons(tr.dataset.code))});bindButtons($("rankingBody")||document)}
function decorateDetail(){const detail=$("stockDetail");if(!detail||detail.classList.contains("empty-state"))return;const heading=detail.querySelector("h3");if(!heading||detail.querySelector(".ux-detail-actions"))return;const code=codeOf(heading.textContent.trim().split(/\s+/)[0]);detail.insertAdjacentHTML("beforeend",`<div class="ux-detail-actions">${rowButtons(code)}<button type="button" class="ux-mini-button" id="uxCopyLink">リンクをコピー</button></div>`);bindButtons(detail);$("uxCopyLink").onclick=async()=>{const url=new URL(location.href);url.searchParams.set("code",code);url.hash="ranking";try{await navigator.clipboard.writeText(url.toString());announce("銘柄リンクをコピーしました")}catch{prompt("銘柄リンク",url.toString())}}}
function decorateAll(){decorateActions();decorateRows();decorateDetail();renderMobileRanking();renderFilterSummary()}
function renderMobileRanking(){let host=$("uxMobileRanking");if(!host){host=document.createElement("div");host.id="uxMobileRanking";host.className="ux-mobile-ranking";document.querySelector(".table-wrap")?.insertAdjacentElement("afterend",host)}const visible=[...document.querySelectorAll("#rankingBody tr[data-code]")].map(tr=>stockByCode(tr.dataset.code)).filter(Boolean);host.innerHTML=visible.map(r=>`<article class="ux-mobile-card" data-code="${esc(codeOf(r.code))}"><div class="ux-mobile-top"><div><div class="ux-mobile-name">${esc(r.name)}</div><div class="ux-mobile-code">${esc(codeOf(r.code))} / ${esc(r.sector33)}</div></div><div class="ux-mobile-rank">#${num(r.rank)}</div></div><div class="ux-mobile-kpis"><div class="ux-mobile-kpi"><span>Score</span><b>${num(r.score)}</b></div><div class="ux-mobile-kpi"><span>20日</span><b>${pct(r.return_20d)}</b></div><div class="ux-mobile-kpi"><span>出来高</span><b>${num(r.volume_ratio).toFixed(1)}倍</b></div></div><div class="ux-mobile-tags">相対 ${esc(r.relative_strength_grade||"—")} / 品質 ${esc(r.data_quality_grade||"—")}</div>${rowButtons(r.code)}</article>`).join("");host.querySelectorAll(".ux-mobile-card").forEach(card=>card.addEventListener("click",e=>{if(e.target.closest("button"))return;document.querySelector(`#rankingBody tr[data-code="${CSS.escape(card.dataset.code)}"]`)?.click()}));bindButtons(host)}
function activeFilters(){const mapping=[["searchInput","検索"],["sectorFilter","業種"],["qualityFilter","品質"],["lifecycleFilter","局面"]];const values=[];for(const [id,label] of mapping){const el=$(id);if(el?.value)values.push({id,label,value:el.value})}if($("newOnly")?.checked)values.push({id:"newOnly",label:"新規",value:"のみ"});if($("risingOnly")?.checked)values.push({id:"risingOnly",label:"急上昇",value:"のみ"});return values}
function renderFilterSummary(){let host=$("uxFilterSummary");if(!host){host=document.createElement("div");host.id="uxFilterSummary";host.className="ux-filter-summary";document.querySelector(".filter-panel")?.insertAdjacentElement("afterend",host)}const filters=activeFilters();host.innerHTML=filters.map(f=>`<span class="ux-filter-pill">${esc(f.label)}: ${esc(f.value)} <button type="button" data-filter="${esc(f.id)}" aria-label="${esc(f.label)}を解除">×</button></span>`).join("");host.querySelectorAll("button").forEach(b=>b.onclick=()=>{const el=$(b.dataset.filter);if(!el)return;if(el.type==="checkbox")el.checked=false;else el.value="";el.dispatchEvent(new Event(el.id==="searchInput"?"input":"change",{bubbles:true}))})}
function openPanel(mode){const codes=read(mode==="watch"?storage.watch:storage.compare),rows=codes.map(stockByCode).filter(Boolean);$("uxPanelTitle").textContent=mode==="watch"?"保存銘柄":"3銘柄比較";if(!rows.length){$("uxPanelBody").innerHTML='<div class="empty-state">まだ銘柄がありません。</div>'}else if(mode==="watch"){$("uxPanelBody").innerHTML=`<div class="ux-watch-list">${rows.map(r=>`<article class="ux-compare-card"><h3>${esc(codeOf(r.code))} ${esc(r.name)}</h3><div class="ux-compare-row"><span>順位</span><b>#${num(r.rank)}</b></div><div class="ux-compare-row"><span>Score</span><b>${num(r.score)}</b></div><div class="ux-compare-row"><span>20日</span><b>${pct(r.return_20d)}</b></div><div class="ux-compare-row"><span>相対強度</span><b>${esc(r.relative_strength_grade||"—")} ${num(r.relative_strength_score).toFixed(1)}</b></div>${rowButtons(r.code)}</article>`).join("")}</div>`}else{$("uxPanelBody").innerHTML=`<div class="ux-compare-grid">${rows.map(r=>`<article class="ux-compare-card"><h3>${esc(codeOf(r.code))} ${esc(r.name)}</h3><div class="ux-compare-list"><div class="ux-compare-row"><span>順位</span><b>#${num(r.rank)}</b></div><div class="ux-compare-row"><span>Momentum</span><b>${num(r.score)}点</b></div><div class="ux-compare-row"><span>5日 / 20日</span><b>${pct(r.return_5d)} / ${pct(r.return_20d)}</b></div><div class="ux-compare-row"><span>出来高</span><b>${num(r.volume_ratio).toFixed(1)}倍</b></div><div class="ux-compare-row"><span>売買代金</span><b>${money(r.trading_value)}</b></div><div class="ux-compare-row"><span>相対強度</span><b>${esc(r.relative_strength_grade||"—")} ${num(r.relative_strength_score).toFixed(1)}</b></div><div class="ux-compare-row"><span>品質</span><b>${esc(r.data_quality_grade||"—")}</b></div></div>${rowButtons(r.code)}</article>`).join("")}</div>`}bindButtons($("uxPanelBody"));$("uxPanelBackdrop").classList.add("open")}
function makeSectorsInteractive(){document.querySelectorAll("#sectorList .bar-row").forEach((row,i)=>{const sector=(d.sectors||[])[i]?.sector33;if(!sector)return;row.classList.add("ux-sector-link");row.setAttribute("role","button");row.tabIndex=0;const activate=()=>{const filter=$("sectorFilter");if(filter){filter.value=sector;filter.dispatchEvent(new Event("change",{bubbles:true}));document.querySelector("#ranking")?.scrollIntoView({behavior:"smooth"});announce(`${sector}で絞り込みました`)}};row.onclick=activate;row.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();activate()}}})}
function deepLink(){const code=new URLSearchParams(location.search).get("code");if(!code)return;let attempts=0;const timer=setInterval(()=>{attempts++;const row=document.querySelector(`#rankingBody tr[data-code="${CSS.escape(codeOf(code))}"]`);if(row){clearInterval(timer);row.click();row.scrollIntoView({behavior:"smooth",block:"center"});announce(`${codeOf(code)}の詳細を開きました`)}else if(attempts>30)clearInterval(timer)},120)}
function shortcuts(){document.addEventListener("keydown",e=>{if(e.key==="/"&&!/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName)){e.preventDefault();$("searchInput")?.focus()}if(e.key==="Escape")closePanel()})}
function observe(){const observer=new MutationObserver(()=>decorateAll());["actionCards","rankingBody","stockDetail"].forEach(id=>{const node=$(id);if(node)observer.observe(node,{childList:true,subtree:true})});["searchInput","sectorFilter","qualityFilter","lifecycleFilter","newOnly","risingOnly"].forEach(id=>$(id)?.addEventListener(id==="searchInput"?"input":"change",()=>setTimeout(decorateAll,0)))}
function init(){renderHealth();renderBrief();createToolbar();shortcuts();observe();decorateAll();makeSectorsInteractive();deepLink()}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);else init();
})();
'''.strip()


def inject_once(text: str, marker: str, insertion: str, before: str) -> str:
    if marker in text:
        return text
    if before not in text:
        raise ValueError(f"injection target not found: {before}")
    return text.replace(before, f"{insertion}\n{before}", 1)


def reseal_manifest(output: Path) -> dict:
    manifest_path = output / "site_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    for path in sorted(p for p in output.rglob("*") if p.is_file() and p.name != "site_manifest.json"):
        files.append({
            "path": path.relative_to(output).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    core = {
        "site_version": f"{manifest.get('site_version', '')}+{EXPERIENCE_VERSION}",
        "report_date": manifest.get("report_date", ""),
        "workbook_sha256": manifest.get("workbook_sha256", ""),
        "payload_sha256": manifest.get("payload_sha256", ""),
        "file_count": len(files),
        "files": files,
        "research_only": True,
        "production_state_mutations": [],
    }
    result = {
        **core,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "manifest_fingerprint": canonical_hash(core),
    }
    result["status_sha256"] = canonical_hash(result)
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def apply(output_dir: str | Path) -> dict:
    output = Path(output_dir)
    for name in ("index.html", "404.html", "site_manifest.json"):
        if not (output / name).is_file():
            raise FileNotFoundError(output / name)
    (output / "assets").mkdir(parents=True, exist_ok=True)
    (output / "assets" / "experience.css").write_text(EXPERIENCE_CSS + "\n", encoding="utf-8")
    (output / "assets" / "experience.js").write_text(EXPERIENCE_JS + "\n", encoding="utf-8")
    for name in ("index.html", "404.html"):
        path = output / name
        text = path.read_text(encoding="utf-8")
        text = inject_once(text, HEAD_MARKER, HEAD_MARKER, "</head>")
        text = inject_once(text, BODY_MARKER, BODY_MARKER, "</body>")
        path.write_text(text, encoding="utf-8")
    manifest = reseal_manifest(output)
    validation = validate(output)
    if not validation["passed"]:
        raise ValueError("invalid experience overlay: " + "; ".join(validation["issues"]))
    return {"manifest": manifest, "validation": validation}


def validate(output_dir: str | Path) -> dict:
    output = Path(output_dir)
    issues = []
    for relative in ("index.html", "404.html", "assets/experience.css", "assets/experience.js", "site_manifest.json"):
        if not (output / relative).is_file():
            issues.append(f"missing: {relative}")
    for name in ("index.html", "404.html"):
        path = output / name
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if text.count(HEAD_MARKER) != 1:
                issues.append(f"{name}: experience stylesheet must appear once")
            if text.count(BODY_MARKER) != 1:
                issues.append(f"{name}: experience script must appear once")
    script = output / "assets" / "experience.js"
    if script.is_file():
        text = script.read_text(encoding="utf-8")
        for required in ("momentum-watchlist-v2", "momentum-compare-v2", "URLSearchParams", "ux-mobile-ranking", "PRIMARY CAUTION"):
            if required not in text:
                issues.append(f"experience.js missing {required}")
        for forbidden in ("EMAIL_APP_PASSWORD", "EMAIL_FROM", "EMAIL_TO", "smtp.gmail.com", "@icloud.com", "@gmail.com"):
            if forbidden in text:
                issues.append(f"experience.js contains private marker {forbidden}")
    return {"passed": not issues, "issues": sorted(set(issues))}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply or validate dashboard UX overlay")
    commands = parser.add_subparsers(dest="command", required=True)
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--output-dir", default="output/site")
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--output-dir", default="output/site")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = apply(args.output_dir) if args.command == "apply" else validate(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("validation", result).get("passed", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
