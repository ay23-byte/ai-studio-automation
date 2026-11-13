"""Offline review dashboard for a shared review package (all three modes).

``build_dashboard(package_dir)`` writes two files next to the manifest:

* ``review_dashboard.html`` - a single self-contained page (no network, no
  build step) in which a human plays the rendered MP4, jumps to each shot,
  reads narration, source and licence evidence, records a decision for every
  shot, asset, claim and QA report, and exports ``review_decisions.json``;
* ``review_decisions_template.json`` - the same decisions with everything
  still pending.

The page never approves anything by itself: every control starts at
"pending", there is no "approve all", and the exported file carries the job
id, manifest fingerprint and MP4 SHA-256 it was made for.  The release
workflow re-checks every rule; the page's completeness list is a convenience.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import review_contract as contract

DASHBOARD_NAME = "review_dashboard.html"


def _qa_evidence(root: Path) -> dict:
    evidence = {}
    for name, relative in contract.QA_FILES.items():
        path = root / relative
        entry = {"status": "NOT_RUN", "details": [], "file": relative}
        if path.is_file():
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(report, dict):
                    entry["status"] = str(report.get("status") or "NOT_RUN").upper()
                    details = report.get("details") or report.get("issues") or report.get("warnings") or []
                    if isinstance(details, (str, dict)):
                        details = [details]
                    entry["details"] = [json.dumps(item, ensure_ascii=False)[:400] if not isinstance(item, str)
                                        else item[:400] for item in list(details)[:12]]
                else:
                    entry["status"] = "INVALID"
            except (OSError, UnicodeError, ValueError):
                entry["status"] = "INVALID"
        evidence[name] = entry
    return evidence


def dashboard_data(package_dir) -> dict:
    """Everything the page needs, as plain JSON."""
    root = Path(package_dir).resolve()
    manifest = contract.read_manifest(root)
    if manifest.get("schema_version") != contract.MANIFEST_SCHEMA_VERSION:
        raise contract.ContractError(
            "The review dashboard needs a schema v2 manifest; rebuild the package with the current workflow.")
    evidence = {}
    for scene in manifest.get("scenes") or []:
        for asset in scene.get("assets") or []:
            evidence[str(asset.get("asset_id"))] = contract.rights_evidence_problems(asset)
    return {
        "manifest": manifest,
        "qa": _qa_evidence(root),
        "rights_evidence_problems": evidence,
        "template": contract.decision_template(manifest),
        "rules": {"min_note_chars": contract.MIN_NOTE_CHARS,
                  "confirmations": list(contract.CONFIRMATIONS),
                  "human_resolvable_qa": list(contract.HUMAN_RESOLVABLE_QA),
                  "technical_qa": list(contract.TECHNICAL_QA),
                  "mode_labels": {key: value["label"] for key, value in contract.MODES.items()}},
    }


def build_dashboard(package_dir) -> Path:
    root = Path(package_dir).resolve()
    data = dashboard_data(root)
    contract.write_json(root / contract.TEMPLATE_NAME, data["template"])
    # "</" must not appear inside the JSON island, or a narration containing
    # "</script>" would end the element and inject markup into the page.
    island = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\u0021--")
    page = _PAGE.replace("/*__PACKAGE_DATA__*/", island)
    target = root / DASHBOARD_NAME
    target.write_text(page, encoding="utf-8")
    return target


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' file: data: blob:; media-src 'self' file: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>AI Studio review</title>
<style>
:root{--bg:#f4f5f7;--panel:#fff;--ink:#16202c;--muted:#5b6675;--line:#d5dae1;--accent:#1d4ed8;--ok:#14713d;--okbg:#e3f4ea;--bad:#a61b1b;--badbg:#fbe6e6;--warn:#8a5300;--warnbg:#fdf0d6}
@media (prefers-color-scheme:dark){:root{--bg:#10151c;--panel:#19212b;--ink:#e6ebf1;--muted:#9aa6b4;--line:#2f3a48;--accent:#7aa5ff;--ok:#6fd49a;--okbg:#143424;--bad:#ff8f8f;--badbg:#3d1a1a;--warn:#f2c069;--warnbg:#3b2c10}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:1rem 1rem 6rem}
h1{font-size:1.5rem;margin:.5rem 0}h2{font-size:1.2rem;margin:0 0 .6rem}h3{font-size:1rem;margin:0 0 .4rem}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:1rem;margin:1rem 0}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:.2rem .8rem;font-size:.92rem}
.meta dt{color:var(--muted)}.meta dd{margin:0;overflow-wrap:anywhere}
code{font:0.86em ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
video.main{width:100%;max-height:70vh;background:#000;border-radius:8px}
.shot{display:grid;grid-template-columns:minmax(0,280px) minmax(0,1fr);gap:1rem}
@media (max-width:720px){.shot{grid-template-columns:1fr}}
.shot img,.shot video{max-width:100%;max-height:300px;background:#000;border-radius:6px;display:block}
.tag{display:inline-block;padding:.05rem .5rem;border-radius:999px;font-size:.8rem;font-weight:600;border:1px solid transparent}
.tag.ok{color:var(--ok);background:var(--okbg)}.tag.bad{color:var(--bad);background:var(--badbg)}.tag.warn{color:var(--warn);background:var(--warnbg)}
.blocked{color:var(--bad);background:var(--badbg);border-radius:6px;padding:.5rem .7rem;margin:.4rem 0;font-size:.92rem}
.note{color:var(--muted);font-size:.9rem}
label{display:block;margin:.5rem 0 .15rem;font-weight:600;font-size:.92rem}
.choice{display:flex;gap:1rem;flex-wrap:wrap}.choice label{display:inline-flex;gap:.35rem;align-items:center;font-weight:400;margin:.2rem 0}
input[type=text],input[type=url],textarea{width:100%;padding:.5rem .6rem;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit}
textarea{min-height:4.2rem;resize:vertical}
button,.button{font:inherit;font-weight:600;padding:.55rem .9rem;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
button.secondary{background:transparent;color:var(--accent)}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
a{color:var(--accent);overflow-wrap:anywhere}
.count{font-size:.8rem;color:var(--muted)}.count.short{color:var(--bad)}
ul.todo{margin:.3rem 0;padding-left:1.2rem;max-height:16rem;overflow:auto}
#bar{position:fixed;left:0;right:0;bottom:0;background:var(--panel);border-top:1px solid var(--line);padding:.6rem 1rem;display:flex;gap:1rem;align-items:center;justify-content:space-between;flex-wrap:wrap}
#export-text{min-height:7rem;font:0.8rem ui-monospace,monospace}
table{border-collapse:collapse;width:100%;font-size:.92rem}td,th{border-bottom:1px solid var(--line);padding:.35rem .4rem;text-align:left;vertical-align:top}
</style>
</head>
<body>
<main>
<h1>Human review &mdash; <span id="mode-label"></span></h1>
<p class="note">Nothing on this page is approved until you approve it. Decisions are saved in this browser and exported as a file tied to this exact video.</p>

<section class="panel" aria-labelledby="h-job"><h2 id="h-job">Production job</h2><dl class="meta" id="job-meta"></dl></section>

<section class="panel" aria-labelledby="h-video"><h2 id="h-video">1. Watch the complete video</h2>
<video class="main" id="main-video" controls preload="metadata"></video>
<p><a id="captions-link" href="#">Open the captions file</a> <span id="thumb-link"></span></p>
<p class="note">Optional check: select the downloaded <code>video.mp4</code> to confirm it is the file this review is tied to.</p>
<input type="file" id="hash-file" accept="video/mp4" aria-label="Select video.mp4 to verify its SHA-256"> <span id="hash-result" role="status"></span>
</section>

<section class="panel" aria-labelledby="h-qa"><h2 id="h-qa">2. Automated QA reports</h2>
<p class="note">Automated results are evidence, not approval. A FAIL or a missing report cannot be resolved here; a REVIEW needs your written acceptance.</p>
<div id="qa"></div></section>

<section aria-labelledby="h-shots"><h2 id="h-shots" style="margin-top:1.5rem">3. Shots, sources and rights</h2><div id="shots"></div></section>

<section class="panel" id="research-panel" aria-labelledby="h-research" hidden><h2 id="h-research">4. Research claims and sources</h2><div id="research"></div></section>

<section class="panel" aria-labelledby="h-confirm"><h2 id="h-confirm">5. Whole-video confirmations</h2>
<p class="note">Six separate decisions. Approving one does not approve another.</p><div id="confirmations"></div></section>

<section class="panel" aria-labelledby="h-overall"><h2 id="h-overall">6. Overall decision</h2>
<label for="reviewer">Your name (the named human reviewer)</label><input type="text" id="reviewer" data-path="reviewer" autocomplete="name">
<div class="choice" role="radiogroup" aria-label="Overall decision" id="overall-choice"></div>
<label for="overall-notes">Decision notes</label><textarea id="overall-notes" data-path="overall.notes"></textarea><span class="count" data-count-for="overall.notes"></span>
</section>

<section class="panel" aria-labelledby="h-export"><h2 id="h-export">7. Export the decision</h2>
<div id="todo" role="status" aria-live="polite"></div>
<p><button id="export-download" type="button">Download review_decisions.json</button>
<button id="export-copy" type="button" class="secondary">Copy for the release workflow</button>
<span id="export-status" role="status"></span></p>
<label for="export-text">Value for the <code>review_decisions</code> input of the release workflow</label>
<textarea id="export-text" readonly></textarea>
<div id="release-values" class="note"></div>
</section>

<section class="panel" aria-labelledby="h-reopen"><h2 id="h-reopen">Reopen an earlier review</h2>
<p class="note">Load a previously exported decision file to continue or change it. Its history is kept. A file made for a different video is shown for reference only and every approval is reset.</p>
<input type="file" id="load-file" accept="application/json,.json" aria-label="Load an earlier review_decisions.json">
<button id="reset" type="button" class="secondary">Start again (keep history)</button>
<div id="history"></div></section>
</main>
<div id="bar"><span id="bar-status" role="status" aria-live="polite"></span><a class="button" href="#h-export">Go to export</a></div>

<script type="application/json" id="package-data">/*__PACKAGE_DATA__*/</script>
<script>
(function(){
"use strict";
var DATA=JSON.parse(document.getElementById("package-data").textContent);
var M=DATA.manifest,RULES=DATA.rules,MIN=RULES.min_note_chars;
var KEY="ai-studio-review:"+M.job_id+":"+M.video.sha256;
var state;
function clone(x){return JSON.parse(JSON.stringify(x));}
function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
function safeUrl(v){try{var u=new URL(String(v));return (u.protocol==="http:"||u.protocol==="https:")?u.href:"";}catch(e){return "";}}
function relPath(v){v=String(v||"");return (/^[a-z]+:/i.test(v)||v.indexOf("..")>=0||v.charAt(0)==="/")?"":v.split("/").map(encodeURIComponent).join("/");}
function get(path){return path.split(".").reduce(function(o,k){return o==null?undefined:o[k];},state);}
function set(path,value){var keys=path.split("."),o=state;for(var i=0;i<keys.length-1;i++){o=o[keys[i]];}o[keys[keys.length-1]]=value;}
function fresh(){var t=clone(DATA.template);t.history=[];return t;}
function bound(d){return d&&d.schema_version===DATA.template.schema_version&&d.job_id===M.job_id&&d.video_sha256===M.video.sha256&&d.manifest_fingerprint===M.fingerprint;}
function load(){try{var raw=localStorage.getItem(KEY);var d=raw?JSON.parse(raw):null;return bound(d)?merge(d):null;}catch(e){return null;}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(state));}catch(e){}}
// Keep only ids that exist in this package; anything else never becomes a decision.
function merge(d){var t=fresh();
 ["reviewer"].forEach(function(k){if(typeof d[k]==="string")t[k]=d[k];});
 if(d.overall){t.overall.decision=d.overall.decision||"pending";t.overall.notes=String(d.overall.notes||"");}
 RULES.confirmations.forEach(function(k){if(d.confirmations&&d.confirmations[k])t.confirmations[k]=d.confirmations[k];});
 function byId(list,key){var m={};(list||[]).forEach(function(x){if(x&&x[key]!=null)m[x[key]]=x;});return m;}
 var s=byId(d.scene_reviews,"scene_id");t.scene_reviews.forEach(function(x){var y=s[x.scene_id];if(y){x.decision=y.decision||"pending";x.notes=String(y.notes||"");}});
 var a=byId(d.asset_reviews,"asset_id");t.asset_reviews.forEach(function(x){var y=a[x.asset_id];if(y){x.source_verified=y.source_verified===true;x.rights_decision=y.rights_decision||"pending";x.license_url=String(y.license_url||"");x.attribution=String(y.attribution||"");x.notes=String(y.notes||"");}});
 var c=byId(d.claim_reviews,"claim_id");t.claim_reviews.forEach(function(x){var y=c[x.claim_id];if(y){x.decision=y.decision||"pending";x.notes=String(y.notes||"");}});
 var r=byId(d.source_reviews,"source_id");t.source_reviews.forEach(function(x){var y=r[x.source_id];if(y){x.decision=y.decision||"pending";x.notes=String(y.notes||"");}});
 RULES.human_resolvable_qa.forEach(function(k){var y=d.qa_reviews&&d.qa_reviews[k];if(y){t.qa_reviews[k].decision=y.decision||"pending";t.qa_reviews[k].notes=String(y.notes||"");}});
 t.history=Array.isArray(d.history)?d.history.slice(-50):[];
 return t;}
function radio(path,options,label){var cur=get(path),name="r-"+path.replace(/[^a-z0-9]/gi,"-");
 return '<div class="choice" role="radiogroup" aria-label="'+esc(label)+'">'+options.map(function(o){return '<label><input type="radio" name="'+name+'" data-path="'+esc(path)+'" value="'+esc(o[0])+'"'+(cur===o[0]?" checked":"")+'> '+esc(o[1])+'</label>';}).join("")+'</div>';}
function notes(path,label){var id="n-"+path.replace(/[^a-z0-9]/gi,"-");return '<label for="'+id+'">'+esc(label)+'</label><textarea id="'+id+'" data-path="'+esc(path)+'">'+esc(get(path))+'</textarea><span class="count" data-count-for="'+esc(path)+'"></span>';}
function text(path,label,type){var id="t-"+path.replace(/[^a-z0-9]/gi,"-");return '<label for="'+id+'">'+esc(label)+'</label><input type="'+(type||"text")+'" id="'+id+'" data-path="'+esc(path)+'" value="'+esc(get(path))+'">';}
function tag(status){var s=String(status||"").toUpperCase();var cls=(s==="PASS"||s==="MATCH")?"ok":(s==="REVIEW"||s==="UNCERTAIN")?"warn":"bad";return '<span class="tag '+cls+'">'+esc(s)+'</span>';}
function link(url,label){var u=safeUrl(url);return u?'<a href="'+esc(u)+'" target="_blank" rel="noopener noreferrer">'+esc(label||u)+'</a>':'<span class="tag bad">not recorded</span>';}
function fmt(t){t=Number(t);if(!isFinite(t))return "";var m=Math.floor(t/60),s=(t%60).toFixed(1);return m+":"+(s<10?"0":"")+s;}

function renderStatic(){
 document.title="Review: "+(M.title||M.topic);
 document.getElementById("mode-label").textContent=(RULES.mode_labels[M.mode]||M.mode)+": "+(M.title||M.topic);
 var src=M.source||{},v=M.video||{},nar=M.narration||{},scr=M.script||{};
 var rows=[["Job ID",'<code>'+esc(M.job_id)+'</code>'],["Production mode",esc(RULES.mode_labels[M.mode]||M.mode)],["Topic",esc(M.topic)],
  ["MP4 SHA-256",'<code>'+esc(v.sha256)+'</code>'],["Manifest fingerprint",'<code>'+esc(M.fingerprint)+'</code>'],
  ["Video",esc(v.width+"×"+v.height+", "+fmt(v.duration_seconds)+", "+v.fps+" fps, "+v.video_codec+" + "+v.audio_codec+" ("+v.audio_channels+" ch, "+v.audio_sample_rate+" Hz)")],
  ["Built",esc(M.created_at)+(src.workflow_run_id?" by workflow run <code>"+esc(src.workflow_run_id)+"</code>":"")+(src.commit?" at commit <code>"+esc(String(src.commit).slice(0,12))+"</code>":"")],
  ["Script",esc([scr.provider,scr.model].filter(Boolean).join(" / ")||"not recorded")+(scr.fallback_used?' <span class="tag warn">fallback model used</span>':"")],
  ["Narration",esc([nar.provider,nar.voice,nar.language].filter(Boolean).join(" / ")||"not recorded")+(nar.fallback_used?' <span class="tag bad">fallback voice used</span>':"")]];
 document.getElementById("job-meta").innerHTML=rows.map(function(r){return "<dt>"+r[0]+"</dt><dd>"+r[1]+"</dd>";}).join("");
 var mv=document.getElementById("main-video");mv.src=relPath(v.file||"video.mp4");
 var cap=relPath((M.captions||{}).file||M.captions_file);var cl=document.getElementById("captions-link");if(cap){cl.href=cap;}else{cl.remove();}
 if(M.thumbnail_file&&relPath(M.thumbnail_file)){document.getElementById("thumb-link").innerHTML=' · <a href="'+relPath(M.thumbnail_file)+'" target="_blank" rel="noopener">Open the thumbnail</a>';}
}
function renderQa(){var out="<table><thead><tr><th>Report</th><th>Status</th><th>Evidence</th></tr></thead><tbody>";
 RULES.technical_qa.concat(RULES.human_resolvable_qa).forEach(function(n){var q=DATA.qa[n]||{status:"NOT_RUN",details:[]};
  out+="<tr><td><b>"+esc(n)+"</b><br><a href='"+relPath(q.file)+"' target='_blank' rel='noopener'>open report</a></td><td>"+tag(q.status)+"</td><td>"+(q.details||[]).map(esc).join("<br>")+"</td></tr>";});
 out+="</tbody></table>";
 RULES.human_resolvable_qa.forEach(function(n){var q=DATA.qa[n]||{status:"NOT_RUN"};
  if(q.status==="REVIEW"){out+='<h3 style="margin-top:1rem">'+esc(n)+" is REVIEW</h3>"+radio("qa_reviews."+n+".decision",[["pending","Pending"],["accepted","I checked this and accept it"],["rejected","Reject"]],n+" QA decision")+notes("qa_reviews."+n+".notes","What you checked for "+n);}
  else if(q.status!=="PASS"){out+='<div class="blocked">'+esc(n)+" is "+esc(q.status)+". This blocks release and cannot be resolved by review; the video must be rebuilt.</div>";}});
 RULES.technical_qa.forEach(function(n){var q=DATA.qa[n]||{status:"NOT_RUN"};if(q.status!=="PASS"){out+='<div class="blocked">Technical QA is '+esc(q.status)+". This blocks release and cannot be overridden.</div>";}});
 document.getElementById("qa").innerHTML=out;}
function renderShots(){var html="",ai=0;
 (M.scenes||[]).forEach(function(sc,i){
  var timing=(sc.start_seconds!=null?'<button type="button" class="secondary" data-seek="'+esc(sc.start_seconds)+'">Play this shot in the video ('+fmt(sc.start_seconds)+')</button>':'<span class="note">No start time recorded for this shot.</span>');
  var screening=sc.screening||sc.semantic_screening;
  html+='<article class="panel" aria-labelledby="shot-'+i+'"><h3 id="shot-'+i+'">'+esc(sc.scene_id)+(sc.duration_seconds!=null?' <span class="note">'+Number(sc.duration_seconds).toFixed(1)+' s</span>':"")+'</h3>';
  html+='<p><b>Narration / caption:</b> '+esc(sc.narration)+'</p><p>'+timing+'</p>';
  if(screening){html+='<p><b>AI visual screening:</b> '+tag(screening.status||"not_run")+' '+esc(screening.reason||"")+' <span class="note">(a model opinion, not a decision)</span></p>';}
  (sc.assets||[]).forEach(function(a){
   var p=a.provenance||{},r=a.rights||{},path=relPath(a.path),k=state.asset_reviews.findIndex(function(x){return x.asset_id===a.asset_id;});
   var media=!path?"":(a.asset_type==="video"||/\.(mp4|webm|mov)$/i.test(path))?'<video controls preload="metadata" src="'+path+'"></video>':'<img loading="lazy" src="'+path+'" alt="Visual used in '+esc(sc.scene_id)+'">';
   var probs=DATA.rights_evidence_problems[a.asset_id]||[];
   html+='<div class="shot"><div>'+media+'<p class="note"><code>'+esc(a.path)+'</code></p></div><div>';
   html+='<dl class="meta"><dt>Origin</dt><dd>'+esc(p.origin)+(p.origin==="ai_generated"?' <span class="tag warn">AI-generated</span>':"")+'</dd><dt>Provider</dt><dd>'+esc(p.provider||"not recorded")+'</dd><dt>Original title</dt><dd>'+esc(p.title||"not recorded")+'</dd><dt>Creator</dt><dd>'+esc(p.creator||"not recorded")+'</dd><dt>Source page</dt><dd>'+(p.origin==="source"?link(p.source_url,"Open the original source page"):esc(p.source_url||"n/a"))+'</dd><dt>Licence recorded</dt><dd>'+esc(r.license||"none")+'</dd><dt>Licence URL</dt><dd>'+(r.license_url?link(r.license_url):"not recorded")+'</dd><dt>Attribution</dt><dd>'+esc(r.attribution||"not recorded")+'</dd>'+(p.prompt?'<dt>Prompt</dt><dd>'+esc(p.prompt)+'</dd>':"")+'</dl>';
   if(probs.length){html+='<div class="blocked"><b>Blocked by missing rights evidence.</b> Approval here cannot fix it; the asset must be replaced in a new build.<br>'+probs.map(esc).join("<br>")+'</div>';}
   if(k>=0){html+='<div class="choice"><label><input type="checkbox" data-path="asset_reviews.'+k+'.source_verified"'+(state.asset_reviews[k].source_verified?" checked":"")+'> I opened the original source and it is this item</label></div>';
    html+=radio("asset_reviews."+k+".rights_decision",[["pending","Pending"],["approved","Rights approved"],["rejected","Rights rejected"]],"Rights decision for "+a.asset_id);
    if(p.origin==="source"){html+=text("asset_reviews."+k+".license_url","Licence / terms URL"+(r.license_url?" (recorded; override only to correct)":""),"url")+text("asset_reviews."+k+".attribution","Attribution text"+(r.attribution?" (recorded; leave empty to keep)":""));}
    html+=notes("asset_reviews."+k+".notes","Source and rights notes for "+a.asset_id);}
   html+='</div></div>';if(p.origin==="ai_generated")ai++;});
  html+='<hr style="border:0;border-top:1px solid var(--line);margin:1rem 0">'+radio("scene_reviews."+i+".decision",[["pending","Pending"],["approved","Approve this shot"],["rejected","Reject this shot"]],"Decision for "+sc.scene_id)+notes("scene_reviews."+i+".notes","Inspection notes for "+sc.scene_id)+'</article>';});
 document.getElementById("shots").innerHTML=html;}
function renderResearch(){var R=M.research||{},claims=R.claims||[],sources=R.sources||[];if(!claims.length&&!sources.length)return;
 document.getElementById("research-panel").hidden=false;var html="";
 sources.forEach(function(s,i){html+='<article><h3>Source '+esc(s.source_id)+'</h3><p>'+esc(s.title||"untitled")+(s.publisher?" — "+esc(s.publisher):"")+'<br>'+link(s.url,"Open the source")+'</p>'+radio("source_reviews."+i+".decision",[["pending","Pending"],["reviewed","I read this source"],["rejected","Reject"]],"Decision for source "+s.source_id)+notes("source_reviews."+i+".notes","Notes")+'</article><hr style="border:0;border-top:1px solid var(--line)">';});
 claims.forEach(function(c,i){html+='<article><h3>Claim '+esc(c.claim_id)+'</h3><p>'+esc(c.text||c.claim||"")+'</p><p class="note">Evidence: '+esc(c.evidence||"none recorded")+'<br>Sources: '+esc((c.source_ids||c.valid_source_ids||[]).join(", ")||"none")+'</p>'+radio("claim_reviews."+i+".decision",[["pending","Pending"],["verified","Verified against the source"],["rejected","Reject"]],"Decision for claim "+c.claim_id)+notes("claim_reviews."+i+".notes","Notes")+'</article><hr style="border:0;border-top:1px solid var(--line)">';});
 document.getElementById("research").innerHTML=html;}
var CONFIRM_TEXT={editorial:"Editorial: structure, pacing and wording are fit to publish",factual:"Factual: the claims are accurate and supported",visual:"Visual: every visual suits its narration and none misleads",audio:"Audio: I listened to the whole narration and mix",rights:"Rights: every asset may be used on this channel",disclosure:"Disclosure: AI-generated content is declared as required"};
function renderConfirm(){document.getElementById("confirmations").innerHTML=RULES.confirmations.map(function(k){return "<h3>"+esc(CONFIRM_TEXT[k]||k)+"</h3>"+radio("confirmations."+k,[["pending","Pending"],["approved","Approved"],["rejected","Rejected"]],k+" confirmation");}).join("");
 document.getElementById("overall-choice").innerHTML=radio("overall.decision",[["pending","Pending"],["approved","Approve this exact video for PRIVATE upload"],["rejected","Reject this video"]],"Overall decision");
 document.getElementById("reviewer").value=state.reviewer||"";document.getElementById("overall-notes").value=state.overall.notes||"";}
function ok(n){return typeof n==="string"&&n.trim().length>=MIN;}
function todo(){var t=[];
 if(!state.reviewer||state.reviewer.trim().length<2)t.push("Enter your name.");
 RULES.technical_qa.concat(RULES.human_resolvable_qa).forEach(function(n){var s=(DATA.qa[n]||{}).status;if(s!=="PASS"&&s!=="REVIEW")t.push("BLOCKED: QA report "+n+" is "+s+".");});
 RULES.human_resolvable_qa.forEach(function(n){if((DATA.qa[n]||{}).status==="REVIEW"){var q=state.qa_reviews[n];if(q.decision!=="accepted"||!ok(q.notes))t.push("QA "+n+": accept with notes.");}});
 state.scene_reviews.forEach(function(s){if(s.decision==="rejected")t.push("REJECTED shot "+s.scene_id+".");else if(s.decision!=="approved"||!ok(s.notes))t.push("Shot "+s.scene_id+": decision and notes.");});
 state.asset_reviews.forEach(function(a){var p=DATA.rights_evidence_problems[a.asset_id]||[];if(p.length)t.push("BLOCKED: "+a.asset_id+" lacks rights evidence.");if(a.rights_decision==="rejected")t.push("REJECTED rights for "+a.asset_id+".");else if(a.source_verified!==true||a.rights_decision!=="approved"||!ok(a.notes))t.push("Asset "+a.asset_id+": source check, rights decision and notes.");});
 state.source_reviews.forEach(function(s){if(s.decision!=="reviewed"||String(s.notes||"").trim().length<5)t.push("Research source "+s.source_id+".");});
 state.claim_reviews.forEach(function(c){if(c.decision!=="verified"||String(c.notes||"").trim().length<5)t.push("Claim "+c.claim_id+".");});
 RULES.confirmations.forEach(function(k){if(state.confirmations[k]!=="approved")t.push("Confirmation: "+k+".");});
 if(state.overall.decision!=="approved"||!ok(state.overall.notes))t.push(state.overall.decision==="rejected"?"The video is REJECTED overall.":"Overall decision and notes.");
 return t;}
function refresh(){var t=todo();var blocked=t.filter(function(x){return /^BLOCKED|^REJECTED|REJECTED/.test(x);}).length;
 var msg=t.length?(t.length+" item"+(t.length===1?"":"s")+" outstanding"+(blocked?" ("+blocked+" blocking or rejected)":"")):"Every decision is recorded. Export it and run the release workflow.";
 document.getElementById("bar-status").textContent=msg;
 document.getElementById("todo").innerHTML="<p><b>"+esc(msg)+"</b></p>"+(t.length?"<ul class='todo'>"+t.slice(0,200).map(function(x){return "<li>"+esc(x)+"</li>";}).join("")+"</ul><p class='note'>You can export an incomplete or rejecting review; the release workflow will refuse it and say why.</p>":"");
 Array.prototype.forEach.call(document.querySelectorAll("[data-count-for]"),function(el){var p=el.getAttribute("data-count-for"),v=String(get(p)||"").trim().length;el.textContent=v+" / "+MIN+" characters minimum";el.className="count"+(v<MIN?" short":"");});
 var src=M.source||{};
 document.getElementById("release-values").innerHTML="<p>Release workflow inputs for this package:</p><ul><li>mode: <code>"+esc(M.mode)+"</code></li><li>source_run_id: <code>"+esc(src.workflow_run_id||"the run that built this package")+"</code></li><li>video_sha256: <code>"+esc(M.video.sha256)+"</code></li></ul>";
 renderHistory();}
function renderHistory(){var h=state.history||[];document.getElementById("history").innerHTML=h.length?"<h3>History</h3><table><thead><tr><th>When</th><th>Event</th><th>Reviewer</th><th>Overall</th><th>Notes</th></tr></thead><tbody>"+h.map(function(x){return "<tr><td>"+esc(x.at)+"</td><td>"+esc(x.event)+"</td><td>"+esc(x.reviewer)+"</td><td>"+esc(x.overall)+"</td><td>"+esc(x.notes)+"</td></tr>";}).join("")+"</tbody></table>":"<p class='note'>No earlier exports.</p>";}
function entry(event,d,extra){d=d||state;var rej=(d.scene_reviews||[]).filter(function(s){return s.decision==="rejected";}).map(function(s){return s.scene_id;});
 return {at:new Date().toISOString(),event:event,reviewer:String(d.reviewer||""),overall:(d.overall||{}).decision||"pending",video_sha256:d.video_sha256||null,rejected_scenes:rej,notes:String((extra||"")||((d.overall||{}).notes||"")).slice(0,300)};}
function snapshot(){var out=clone(state);out.reviewed_at=new Date().toISOString();out.history=(state.history||[]).concat([entry("exported")]).slice(-50);return out;}
function compact(out){var json=JSON.stringify(out);
 if(typeof CompressionStream==="undefined"||json.length<20000)return Promise.resolve(json);
 var stream=new Blob([json]).stream().pipeThrough(new CompressionStream("gzip"));
 return new Response(stream).arrayBuffer().then(function(buf){var b=new Uint8Array(buf),s="";for(var i=0;i<b.length;i+=0x8000){s+=String.fromCharCode.apply(null,b.subarray(i,i+0x8000));}return "gz64:"+btoa(s);});}
function exported(out){state.history=out.history;save();compact(out).then(function(t){document.getElementById("export-text").value=t;});refresh();}
document.getElementById("export-download").addEventListener("click",function(){var out=snapshot();var blob=new Blob([JSON.stringify(out,null,2)+"\n"],{type:"application/json"});var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="review_decisions_"+String(M.job_id).replace(/[^a-z0-9._-]/gi,"_")+".json";document.body.appendChild(a);a.click();a.remove();exported(out);document.getElementById("export-status").textContent="Downloaded.";});
document.getElementById("export-copy").addEventListener("click",function(){var out=snapshot();state.history=out.history;save();compact(out).then(function(t){document.getElementById("export-text").value=t;var done=function(m){document.getElementById("export-status").textContent=m;};if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(function(){done("Copied ("+t.length+" characters).");},function(){done("Select the text below and copy it.");});}else{done("Select the text below and copy it.");}});refresh();});
document.getElementById("load-file").addEventListener("change",function(ev){var f=ev.target.files[0];if(!f)return;f.text().then(function(t){var d;try{d=JSON.parse(t);}catch(e){alert("That file is not valid JSON.");return;}
 var kept=Array.isArray(d.history)?d.history.slice(-49):[];
 if(bound(d)){state=merge(d);state.history=kept.concat([entry("reopened",d)]);}
 else{state=fresh();state.history=kept.concat([entry("loaded for reference only: made for a different video or package; all approvals reset",d,"Not applied: job, manifest or MP4 hash differs.")]);alert("This decision file was made for a different video or package. Nothing was approved from it; its history was kept.");}
 save();renderAll();});});
document.getElementById("reset").addEventListener("click",function(){if(!confirm("Reset every decision on this page to pending? History is kept."))return;var h=(state.history||[]).concat([entry("reset to pending")]);state=fresh();state.history=h;save();renderAll();});
document.addEventListener("input",function(ev){var el=ev.target,p=el.getAttribute&&el.getAttribute("data-path");if(!p)return;set(p,el.type==="checkbox"?el.checked:el.value);save();refresh();});
document.addEventListener("click",function(ev){var b=ev.target.closest&&ev.target.closest("[data-seek]");if(!b)return;var v=document.getElementById("main-video");v.currentTime=Number(b.getAttribute("data-seek"))||0;v.play();v.scrollIntoView({behavior:"smooth",block:"center"});});
document.getElementById("hash-file").addEventListener("change",function(ev){var f=ev.target.files[0],out=document.getElementById("hash-result");if(!f)return;if(!(window.crypto&&crypto.subtle)){out.textContent="This browser cannot hash files here; use sha256sum instead.";return;}out.textContent="Hashing…";f.arrayBuffer().then(function(b){return crypto.subtle.digest("SHA-256",b);}).then(function(h){var hex=Array.prototype.map.call(new Uint8Array(h),function(x){return ("0"+x.toString(16)).slice(-2);}).join("");out.innerHTML=hex===M.video.sha256?'<span class="tag ok">MATCH</span> this is the reviewed file':'<span class="tag bad">DIFFERENT FILE</span> <code>'+esc(hex)+'</code>';},function(){out.textContent="Could not read the file.";});});
function renderAll(){renderStatic();renderQa();renderShots();renderResearch();renderConfirm();refresh();}
state=load()||fresh();renderAll();
})();
</script>
</body>
</html>
"""
