"""Browser UI backed by the same scanner and immutable SQLite log as the CLI."""
import csv
import io
import json
import threading
import os
import hmac
import time
import uuid
from collections import deque
from .storage.connect import hosted
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlparse
from .storage.db import Database, dumps
from .season import predict_events
from .__main__ import DEFAULT_DATA
from .providers.nse import NSEProvider
from .calendar.scanner import scan, date_range


def make_server(host='127.0.0.1',port=8765,db_path=None,refresh=False):
    force_refresh=refresh
    lock=threading.Lock()
    rate_lock=threading.Lock()
    requests=deque()
    jobs={}
    jobs_lock=threading.Lock()

    def prune_jobs():
        for job_id, job in list(jobs.items()):
            if job['created_at'] < time.monotonic()-1800:
                del jobs[job_id]

    def run_job(job_id, work):
        try:
            result=dict(status='done', **work())
        except (ValueError, TypeError, KeyError) as exc:
            result=dict(status='error', error=str(exc))
        except Exception:
            result=dict(status='error', error='Data unavailable',
                        coverage_gaps=['Provider or storage unavailable; retry after checking connectivity'])
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id].update(result)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass

        def respond(self,status,body,content_type='application/json; charset=utf-8'):
            raw=body.encode() if isinstance(body,str) else body
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers(); self.wfile.write(raw)

        def authorized(self):
            token=os.environ.get('RESULTS_PREDICTOR_WRITE_TOKEN','')
            if not token:
                return os.environ.get('RESULTS_PREDICTOR_PUBLIC_READ')!='0'
            supplied=self.headers.get('X-Write-Token','')
            bearer=self.headers.get('Authorization','')
            return hmac.compare_digest(supplied.encode(),token.encode()) or hmac.compare_digest(bearer.encode(),('Bearer '+token).encode())

        def do_GET(self):
            path=urlparse(self.path).path
            if path in ('/','/report'):
                page=HTML.replace('<!-- TOKEN_FIELD -->', '<label>Write token<input id="writeToken" type="password" autocomplete="off"></label>' if os.environ.get('RESULTS_PREDICTOR_WRITE_TOKEN') else '')
                return self.respond(200,page,'text/html; charset=utf-8')
            if path.startswith('/api/jobs/'):
                if not self.authorized():
                    return self.respond(401,dumps({'error':'Write token required'}))
                job_id=path.removeprefix('/api/jobs/')
                with jobs_lock:
                    prune_jobs()
                    job=jobs.get(job_id)
                    result={k:v for k,v in job.items() if k!='created_at'} if job else None
                return self.respond(200 if result else 404,dumps(result or {'error':'Job not found or expired; scan again'}))
            if path in ('/health','/api/health'):
                remote=bool(os.environ.get('TURSO_DATABASE_URL') and os.environ.get('TURSO_AUTH_TOKEN'))
                result=dict(ok=False,storage='turso' if remote or hosted() else 'local',hosted=hosted(),db_ok=False)
                try:
                    with lock:
                        db=Database(db_path)
                        try:
                            db.conn.execute('SELECT 1').fetchone()
                            result.update(ok=True,db_ok=True,storage=db.storage)
                        finally: db.close()
                except Exception:
                    result['detail']='Database unavailable; check storage configuration and connectivity'
                return self.respond(200 if result['ok'] else 503,dumps(result))
            if path not in ('/api/report','/api/export','/api/backup'):
                return self.respond(404,dumps({'error':'Not found'}))
            if os.environ.get('RESULTS_PREDICTOR_PUBLIC_READ')=='0' and not self.authorized():
                return self.respond(401,dumps({'error':'Write token required'}))
            try:
                with lock:
                    db=Database(db_path)
                    try: report=db.export_backup() if path=='/api/backup' else db.report()
                    finally: db.close()
            except Exception:
                return self.respond(503,dumps({'error':'Database unavailable'}))
            if path=='/api/export':
                out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=sorted({k for r in report['rows'] for k in r}) or ['symbol','metric','actual'])
                writer.writeheader()
                for row in report['rows']:
                    writer.writerow({k:dumps(v) if isinstance(v,(dict,list)) else v for k,v in row.items()})
                return self.respond(200,out.getvalue(),'text/csv; charset=utf-8')
            self.respond(200,json.dumps(report))

        def do_POST(self):
            path=urlparse(self.path).path
            if path not in ('/api/scan','/api/score','/api/backup/import'):
                return self.respond(404,dumps({'error':'Not found'}))
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=(16*1024*1024 if path=='/api/backup/import' else 16384): raise ValueError('Invalid request size')
                raw_body=self.rfile.read(length)
            except (ValueError, OSError):
                return self.respond(400,dumps({'error':'Invalid request size'}))
            if (os.environ.get('RESULTS_PREDICTOR_WRITE_TOKEN') or os.environ.get('RESULTS_PREDICTOR_PUBLIC_READ')=='0') and not self.authorized():
                return self.respond(401,dumps({'error':'Write token required'}))
            # The UI uses same-origin fetches; reject browser cross-origin writes.
            origin=self.headers.get('Origin')
            if origin and urlparse(origin).netloc!=self.headers.get('Host'):
                return self.respond(403,dumps({'error':'Cross-origin request rejected'}))
            if path in ('/api/scan','/api/score'):
                with rate_lock:
                    stamp=time.monotonic()
                    while requests and requests[0]<=stamp-60: requests.popleft()
                    limited=len(requests)>=10
                    if not limited: requests.append(stamp)
                if limited: return self.respond(429,dumps({'error':'Rate limit: 10 requests per 60 seconds'}))
            try:
                body=json.loads(raw_body)
                if not isinstance(body,dict): raise ValueError('Expected a JSON object')
                if path=='/api/backup/import':
                    with lock:
                        db=Database(db_path)
                        try: result=db.import_backup(body)
                        finally: db.close()
                    return self.respond(200,dumps(result))
                offline=body.get('offline',True)
                refresh=body.get('refresh',False)
                if not isinstance(offline,bool) or not isinstance(refresh,bool): raise ValueError('offline and refresh must be booleans')
                if path=='/api/scan':
                    start,end=date_range(str(body.get('start',''))+':'+str(body.get('end','')))
                    restricted=hosted() or bool(os.environ.get('RESULTS_PREDICTOR_WRITE_TOKEN'))
                    if restricted and (end-start).days+1>31: raise ValueError('Choose an inclusive date range of at most 31 days')
                    if not restricted and (end-start).days>366: raise ValueError('Choose a date range of at most one year')
                def work():
                    with lock:
                        db=Database(db_path)
                        try:
                            provider=NSEProvider(offline,refresh=force_refresh or refresh)
                            if path=='/api/score':
                                from .scoring.auto import score_auto
                                score_auto(db,provider)
                                result=dict(db.report(),coverage_gaps=provider.gaps)
                            else:
                                if offline: provider.gaps.append('OFFLINE DEMO — sample data; not current market coverage')
                                try: events=scan(provider,db,start,end)
                                except Exception as exc:
                                    if not offline: raise RuntimeError('Live calendar unavailable') from exc
                                    raise
                                options=body.get('options',True)
                                args=SimpleNamespace(offline=offline,data=DEFAULT_DATA,cutoff=None,screener=False,options_mode=('snapshot' if offline else 'public') if options else 'none',options_snapshot=None)
                                rows=predict_events(args,db,provider,events,provider.gaps)
                                result=dict(rows=rows,coverage_gaps=list(dict.fromkeys(provider.gaps)),events=[vars(e) for e in events],offline=offline)
                        finally: db.close()
                    return result
                asynchronous=body.get('async',False)
                if not isinstance(asynchronous,bool): raise ValueError('async must be a boolean')
                if path=='/api/scan' and asynchronous:
                    with jobs_lock:
                        prune_jobs()
                        if len(jobs)>=20:
                            completed=next((key for key,value in jobs.items() if value['status']!='running'),None)
                            if completed is None:
                                return self.respond(503,dumps({'error':'Scan queue is full; try again later'}))
                            del jobs[completed]
                        job_id=uuid.uuid4().hex
                        jobs[job_id]=dict(job_id=job_id,status='running',created_at=time.monotonic())
                    threading.Thread(target=run_job,args=(job_id,work),daemon=True).start()
                    return self.respond(202,dumps(dict(job_id=job_id,status='running')))
                result=work()
                self.respond(200,dumps(result))
            except (ValueError,TypeError,KeyError) as exc:
                self.respond(400,dumps({'error':str(exc)}))
            except Exception as exc:
                self.respond(503,dumps({'error':'Data unavailable','coverage_gaps':['Provider or storage unavailable; retry after checking connectivity']}))
    return ThreadingHTTPServer((host,port),Handler)


HTML=r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Results Radar · Earnings analysis</title>
<style>
:root{--ink:#162735;--muted:#667787;--line:#dde5e9;--green:#087f65;--bg:#f3f6f8}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}.banner{background:#163d43;color:#e3faf3;padding:10px 24px;text-align:center;font-size:12px;letter-spacing:.04em}header,main{max-width:1440px;margin:auto;padding:24px 32px}header{display:flex;justify-content:space-between;align-items:center}.brand{font-size:21px;font-weight:800;letter-spacing:-.8px}.brand span{color:var(--green)}nav{display:flex;gap:8px}button,.button{border:0;border-radius:8px;padding:11px 18px;font:inherit;cursor:pointer;background:var(--green);color:white;text-decoration:none}nav button{background:transparent;color:var(--muted)}nav button.active{background:#dceee8;color:#075e4c}h1{font-size:34px;line-height:1.2;letter-spacing:-1px;margin:8px 0}h2{font-size:18px;margin:0 0 16px}.eyebrow{text-transform:uppercase;color:var(--green);font-size:11px;font-weight:800;letter-spacing:2px}.subtitle{color:var(--muted);margin:12px 0 28px}.card{background:white;border:1px solid var(--line);border-radius:14px;padding:24px;margin:20px 0;box-shadow:0 4px 20px #102b3a04}.controls{display:flex;gap:20px;align-items:end;flex-wrap:wrap}label{display:flex;flex-direction:column;gap:6px;font-size:12px;font-weight:650;color:var(--muted)}label.check{flex-direction:row;align-items:center;align-self:center}input[type=date]{border:1px solid #cad6dd;border-radius:7px;background:white;padding:10px;color:var(--ink);font:inherit}input[type=checkbox]{accent-color:var(--green)}.toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px}.small{font-size:12px;color:var(--muted)}.scroll{overflow:auto;margin:16px -24px -24px;border-radius:0 0 14px 14px}table{border-collapse:collapse;width:100%;white-space:nowrap;text-align:left}th{background:#f7f9fa;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;padding:13px 18px;border-bottom:1px solid var(--line)}td{padding:17px 18px;border-bottom:1px solid #edf1f3;font-size:13px;vertical-align:top}td.detail{white-space:normal;min-width:300px;max-width:450px;font-size:11px;color:var(--muted)}.badge{display:inline-block;padding:4px 9px;border-radius:5px;font-size:11px;font-weight:750;background:#edf0f3;color:#627180}.BEAT{background:#ddf4e9;color:#087749}.MISS{background:#ffe6e5;color:#b93732}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.stat{margin:0}.stat strong{display:block;font-size:28px;letter-spacing:-1px}.gap{border-left:4px solid #d9a148;background:#fffcf4}.gap ul{padding-left:20px;color:#7b6948;font-size:13px}.empty{text-align:center;color:var(--muted);padding:44px 20px}.error{color:#b93732}.loading{display:none;color:var(--green);padding:12px 0}.loading.show{display:block}.spin{display:inline-block;width:12px;height:12px;border:2px solid #a2cdbc;border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite;margin-right:8px}@keyframes spin{to{transform:rotate(360deg)}}button:disabled{opacity:.5;cursor:wait}footer{padding:24px 0;color:var(--muted);font-size:12px}[hidden]{display:none!important}@media(max-width:700px){header,main{padding:18px}h1{font-size:28px}.card{padding:18px}.scroll{margin:16px -18px -18px}.controls{gap:12px}.stats{gap:8px}.stat strong{font-size:22px}.toolbar{align-items:start;flex-wrap:wrap}nav button{padding:8px}.banner{font-size:10px}}
.scan-actions{display:flex;align-items:end;gap:12px;flex-wrap:wrap;max-width:100%}.scan-actions label{flex:1;min-width:0}input[type=password]{width:100%;min-width:0;border:1px solid #cad6dd;border-radius:7px;padding:10px;font:16px system-ui}.action-error:empty{display:none}.action-error{background:#fff0ef;border-left:4px solid #b93732;padding:10px 12px;font-weight:650;overflow-wrap:anywhere}@media(max-width:700px){header{flex-wrap:wrap;gap:12px}.scan-actions{width:100%}.scan-actions label{flex-basis:100%}.scan-actions button{width:100%}.controls>label{max-width:100%;min-width:0}input[type=date]{max-width:100%;font-size:16px}}
</style></head><body><div class="banner">Analysis only · not a trade recommendation · no order placed</div>
<header><div class="brand">Results<span>Radar</span> <span class="small">/ NSE F&O</span></div><nav><button id="scanTab" class="active">Discover</button><button id="reportTab">Report card</button></nav></header>
<main><section id="discover"><div class="eyebrow">Results season workspace</div><h1>The quarter, in perspective.</h1><p class="subtitle">Discover upcoming reporters. Compare two models against each company's own history.</p>
<div class="card"><form id="form" class="controls"><label>From<input id="start" type="date" value="2026-11-09" required></label><label>Through<input id="end" type="date" value="2026-11-13" required></label><label class="check"><input id="offline" type="checkbox" checked>Offline / sample data</label><label class="check"><input id="options" type="checkbox" checked>Options analysis</label><label class="check"><input id="refresh" type="checkbox">Refresh sources</label><div class="scan-actions"><!-- TOKEN_FIELD --><button id="scanBtn">Scan reporters ↗</button></div></form><p id="scanError" class="error action-error" role="alert"></p><div class="loading" id="loading" role="status"><span class="spin"></span>Scanning… this can take 1–2 minutes on free hosting.</div><p class="small">Sample calendar: 9–13 Nov 2026. Turn off offline mode for a live scan. Live exchange access may be restricted.</p></div>
<div id="scanSummary" class="stats"></div><div class="card"><div class="toolbar"><h2>Reporter outlook</h2><button id="download" disabled>Download scan CSV ↓</button></div><div id="scanResults" class="empty">Choose a date range and scan to see reporter coverage.</div></div></section>
<section id="report" hidden><div class="eyebrow">Prediction ledger</div><h1>Let the results speak.</h1><p class="subtitle">Original forecasts, published actuals, and monthly hit rates. Missing actuals stay pending.</p><div class="toolbar"><div><button id="reload">Refresh report</button> <button id="score">Fetch actuals & score</button> <label class="check"><input id="scoreOffline" type="checkbox" checked>Score sample predictions only</label></div><a class="button" href="/api/export" download="predictions.csv">Download ledger CSV ↓</a></div><p id="scoreError" class="error action-error" role="alert"></p><div id="scoreLoading" class="loading" role="status">Fetching actuals & scoring… this can take 1–2 minutes.</div><div id="reportSummary" class="stats"></div><div class="card"><h2>Predicted vs actual</h2><div id="reportResults" class="empty">Loading ledger…</div></div><div class="card"><h2>Hit rates over time</h2><p class="small">By result month, metric and model. Only BEAT/MISS calls with actuals enter the hit-rate denominator. Old calls use the transparent proxy.</p><div id="scoreResults"></div></div></section>
<div class="loading" id="reportLoading" role="status"><span class="spin"></span>Loading report…</div><p id="error" class="error" role="alert"></p><aside class="card gap" id="gaps" hidden><h2>Coverage gaps</h2><div id="gapItems"></div></aside><footer>ANALYSIS ONLY — not a trade recommendation, no order placed.<br>Forecasts compare own-history expectations; they do not measure analyst consensus. All timestamps in the ledger preserve their source and offset.</footer></main>
<script>
const $=id=>document.getElementById(id);let scanRows=[];
function storedToken(){try{return sessionStorage.getItem('writeToken')||''}catch{return ''}}
if($('writeToken')){$('writeToken').value=storedToken();$('writeToken').oninput=()=>{try{sessionStorage.setItem('writeToken',$('writeToken').value)}catch{}}}
function token(){return $('writeToken')?$('writeToken').value:storedToken()}
function requireToken(action,errorId){if($('writeToken')&&!token().trim()){$(errorId).textContent=`Enter the write token to ${action}`;return false}return true}
const escape=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>v==null?'—':typeof v==='number'?v.toLocaleString(undefined,{maximumFractionDigits:2}):escape(v);
const badge=v=>`<span class="badge ${v==='BEAT'?'BEAT':v==='MISS'?'MISS':''}">${escape(v||'NO CALL')}</span>`;
function table(headers,rows){return rows.length?`<div class="scroll"><table><thead><tr>${headers.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map((c,i)=>`<td ${i===r.length-1?'class="detail"':''}>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<div class="empty">No observations yet. Scan reporters first; score after filings become available.</div>'}
function stats(id,items){$(id).innerHTML=items.map(([v,l])=>`<div class="card stat"><strong>${escape(v)}</strong><span class="small">${escape(l)}</span></div>`).join('')}
function gaps(items){$('gaps').hidden=!items?.length;$('gapItems').innerHTML='<ul>'+(items||[]).map(x=>`<li>${escape(x)}</li>`).join('')+'</ul>'}
async function api(path,body){
 const headers={'Content-Type':'application/json'};if(token())headers['X-Write-Token']=token();
 const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),180000);
 try{
  const r=await fetch(path,{method:body?'POST':'GET',headers,signal:controller.signal,...(body?{body:JSON.stringify(body)}:{})});
  let d;try{d=JSON.parse(await r.text())}catch(e){if(e.name==='AbortError')throw e;throw Error(`Server returned an invalid or empty response (HTTP ${r.status}). Please try again.`)}
  if(!r.ok){const action=path==='/api/score'?'score':'scan';throw Error((r.status===401||r.status===403)&&(!d?.error||d.error==='Write token required')?`Enter the write token to ${action}`:d?.error||`Request failed (HTTP ${r.status})`)}
  if(!d||typeof d!=='object')throw Error(`Server returned an invalid response (HTTP ${r.status}). Please try again.`);
  return d;
 }catch(e){if(e.name==='AbortError')throw Error('Request timed out after 3 minutes. Please try again.');if(e instanceof TypeError)throw Error('Cannot reach the server. Check your connection and try again.');throw e}finally{clearTimeout(timeout)}
}
async function scanRequest(body){
 let d=await api('/api/scan',{...body,async:true});
 const deadline=Date.now()+10*60*1000;
 while(d.status==='running'){
  if(Date.now()>deadline)throw Error('Scan is taking longer than expected. Please check the report or try again later.');
  const jobId=d.job_id;
  await new Promise(resolve=>setTimeout(resolve,1500));
  d=await api('/api/jobs/'+encodeURIComponent(jobId));
  d.job_id=jobId;
 }
 if(d.status==='error')throw Error(d.error||'Scan failed. Please try again.');
 return d;
}
function view(report){$('discover').hidden=report;$('report').hidden=!report;$('reportTab').classList.toggle('active',report);$('scanTab').classList.toggle('active',!report);$('error').textContent='';gaps([]);if(report)loadReport()}
$('scanTab').onclick=()=>{history.replaceState(null,'','/');view(false)};$('reportTab').onclick=()=>{history.replaceState(null,'','/report');view(true)};
$('form').onsubmit=async e=>{e.preventDefault();$('scanError').textContent='';if(!requireToken('scan','scanError'))return;$('scanBtn').disabled=true;$('loading').classList.add('show');try{const d=await scanRequest({start:$('start').value,end:$('end').value,offline:$('offline').checked,options:$('options').checked,refresh:$('refresh').checked});scanRows=d.rows;$('download').disabled=!scanRows.length;stats('scanSummary',[[new Set(d.events.map(r=>r.symbol)).size,'F&O reporters'],[scanRows.length,'Metric forecasts'],[d.offline?'Sample':'Live','Data mode']]);$('scanResults').className='';$('scanResults').innerHTML=table(['Company / result date','Metric / basis','v2 call / lean','Old E1 / proxy call','Options context','CE / PE analysis'],scanRows.map(r=>[`<b>${escape(r.symbol)}</b><br><span class="small">${escape(r.result_date)}</span>`,`${escape(r.metric)}<br><span class="small">${escape(r.basis)}</span>`,`${badge(r.v2_call)}<br><span class="small">${escape(r.v2_lean)} · ${escape(r.v2_confidence)}</span>`,`${num(r.E1)}<br>${badge(r.old_call)}`,r.options_context?`Implied move ${num(r.options_context.implied_move_pct)}%<br>IV ${num(r.options_context.atm_iv_pct)}% · PCR ${num(r.options_context.pcr_oi)}<br>${escape(r.options_context.liquidity)} · ${escape((r.options_context.flags||[]).join(', '))}`:'N/A — chain unavailable',`${escape(r.analysis)}<br><br>${escape(r.reason)}`]));gaps(d.coverage_gaps)}catch(e){$('scanError').textContent=e.message}finally{$('scanBtn').disabled=false;$('loading').classList.remove('show')}};
function renderReport(d){stats('reportSummary',[[d.rows.length,'Immutable forecasts'],[d.rows.filter(r=>r.actual!=null).length,'Filed metric actuals'],[d.rows.filter(r=>r.actual==null).length,'Awaiting actuals']]);$('reportResults').className='';$('reportResults').innerHTML=table(['Company / date','Metric','v2 / old','E1 / v2 forecast','Actual / reaction','Status & provenance'],d.rows.map(r=>[`${escape(r.symbol)}<br>${escape(r.result_date)}`,escape(r.metric),`${badge(r.v2_call)} / ${badge(r.old_call)}`,`${num(r.E1)} / ${num(r.v2_expectation)}`,`${num(r.actual)} / ${r.reaction_pct==null?'N/A':num(r.reaction_pct)+'%'}`,`${escape(r.state)}<br>#${escape(r.prediction_id)} · ${escape(r.created_at)}<br>${r.offline?'SAMPLE':'LIVE'} · run ${escape(r.run_id)}`]));$('scoreResults').innerHTML=table(['Month','Metric','Model','Scored / called','Hits','Hit rate'],d.scoreboard.map(r=>[escape(r.period),escape(r.metric),escape(r.model),`${r.observations} / ${r.called}`,num(r.hits),r.hit_rate==null?'—':(r.hit_rate*100).toFixed(1)+'%']));if(d.coverage_gaps)gaps(d.coverage_gaps)}
async function loadReport(){ $('reportLoading').classList.add('show');try{renderReport(await api('/api/report'))}catch(e){$('error').textContent=e.message}finally{$('reportLoading').classList.remove('show')}}
$('reload').onclick=loadReport;$('score').onclick=async()=>{$('scoreError').textContent='';if(!requireToken('score (enter it in Discover)','scoreError'))return;$('score').disabled=true;$('scoreLoading').classList.add('show');try{renderReport(await api('/api/score',{offline:$('scoreOffline').checked}))}catch(e){$('scoreError').textContent=e.message}finally{$('score').disabled=false;$('scoreLoading').classList.remove('show')}};
$('download').onclick=()=>{const keys=[...new Set(scanRows.flatMap(Object.keys))];const quote=v=>'"'+String(typeof v==='object'&&v!==null?JSON.stringify(v):v??'').replace(/"/g,'""')+'"';const csv=[keys,...scanRows.map(r=>keys.map(k=>r[k]))].map(r=>r.map(quote).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));const a=document.createElement('a');a.href=url;a.download='reporter-scan.csv';a.click();URL.revokeObjectURL(url)};
if(location.pathname==='/report')view(true);
</script></body></html>'''
