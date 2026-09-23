#!/usr/bin/env python3
"""Small read-only JSON API and regression dashboard for imported perf runs."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.service import TrendService  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402

PAGE = """<!doctype html><html lang='zh'><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>香山性能回归</title><style>
body{font:15px/1.5 system-ui,sans-serif;max-width:1200px;margin:28px auto;padding:0 18px;color:#1c2939;background:#f6f8fb}
h1{font-size:1.6em}select,button{padding:8px;margin:5px 10px 5px 0}button{cursor:pointer}
section{background:white;border:1px solid #dce3ec;border-radius:10px;padding:18px;margin:16px 0;overflow:auto}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #e5ebf2;padding:7px;text-align:left}
th{background:#f0f4f8} .bad{color:#aa2727}.good{color:#137d54}.muted{color:#607080}
</style><h1>香山性能回归</h1><section><label>基线 <select id='a'></select></label>
<label>目标 <select id='b'></select></label><label>Workload <select id='w'></select></label>
<button id='go'>分析</button><p id='info' class='muted'></p></section>
<section><h2>A/B 汇总</h2><div id='summary'></div></section>
<section><h2>切片加权 CPI 贡献</h2><div id='slices'></div></section>
<section><h2>证据路径</h2><div id='evidence'></div></section>
<script>
const $=id=>document.getElementById(id);let runs=[];
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmt(x){return x===null||x===undefined?'N/A':Number(x).toFixed(5);}
function options(id,items){$(id).innerHTML=items.map(x=>'<option value="'+esc(x.value)+'">'+esc(x.label)+'</option>').join('');}
async function init(){runs=await (await fetch('/api/runs')).json();
 const label=r=>r.short_sha+' · '+r.config+' · '+r.status+' · '+(r.branch_label||'');
 options('a',runs.map(r=>({value:r.run_id,label:label(r)})));
 options('b',runs.map(r=>({value:r.run_id,label:label(r)})));
 if(runs.length>1){$('a').selectedIndex=1;$('b').selectedIndex=0;}
 await workloads();}
async function workloads(){let r=runs.find(x=>x.run_id===$('b').value); if(!r)return;
 let names=await(await fetch('/api/workloads?run='+encodeURIComponent(r.run_id))).json();
 options('w',names.map(x=>({value:x,label:x})));}
$('b').onchange=workloads;
$('go').onclick=async()=>{let p=new URLSearchParams({a:$('a').value,b:$('b').value,workload:$('w').value});
try{let r=await(await fetch('/api/compare?'+p)).json();
 $('info').textContent=r.status==='comparable'?'比较条件一致 · '+r.mode:'无法进行严格比较：'+(r.reasons||[r.error]).join(', ');
 if(r.status!=='comparable'){$('summary').textContent='';$('slices').textContent='';$('evidence').textContent='';return;}
 $('summary').innerHTML='<p>覆盖率 '+fmt(r.coverage_weight*100)+'%　CPI A '+fmt(r.weighted_cpi_a)+' → B '+fmt(r.weighted_cpi_b)+'　Δ '+fmt(r.weighted_cpi_delta)+'　退化 '+fmt(r.cpi_degradation_percent)+'%</p>';
 $('slices').innerHTML='<table><tr><th>切片</th><th>权重</th><th>CPI A</th><th>CPI B</th><th>权重 × ΔCPI</th></tr>'+r.slices.map(s=>'<tr><td>'+esc(s.slice)+'</td><td>'+fmt(s.weight)+'</td><td>'+fmt(s.cpi_a)+'</td><td>'+fmt(s.cpi_b)+'</td><td class="'+(s.weighted_cpi_contribution>0?'bad':'good')+'">'+fmt(s.weighted_cpi_contribution)+'</td></tr>').join('')+'</table>';
 $('evidence').innerHTML='<table><tr><th>切片</th><th>A 原始日志</th><th>B 原始日志</th></tr>'+r.slices.map(s=>'<tr><td>'+esc(s.slice)+'</td><td>'+esc(s.source_out_uri_a)+'</td><td>'+esc(s.source_out_uri_b)+'</td></tr>').join('')+'</table>';
}catch(e){$('info').textContent=String(e);}};
init().catch(e=>$('info').textContent=String(e));</script></html>"""


def handler_for(db: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            q = parse_qs(parsed.query)
            try:
                if parsed.path == "/":
                    body, content_type = PAGE.encode(), "text/html; charset=utf-8"
                else:
                    with Store(db, read_only=True) as store:
                        service = TrendService(store)
                        if parsed.path == "/api/runs":
                            value = [dict(row) for row in store.connection.execute(
                                """SELECT r.run_id, r.commit_sha, c.short_sha, r.branch_label,
                                          r.config, r.status, r.comparison_key, r.source_uri, r.snapshot_at
                                     FROM runs r JOIN commits c USING (commit_sha)
                                    ORDER BY r.snapshot_at DESC LIMIT 500""")]
                        elif parsed.path == "/api/workloads":
                            value = [row[0] for row in store.connection.execute(
                                """SELECT DISTINCT s.workload FROM slices s
                                     JOIN slice_results sr USING (slice_id)
                                    WHERE sr.run_id = ? ORDER BY s.workload""",
                                (q["run"][0],))]
                        elif parsed.path == "/api/compare":
                            value = service.compare_points(q["a"][0], q["b"][0], object_id=q["workload"][0])
                        elif parsed.path == "/api/trend":
                            value = service.get_trend(q["level"][0], q["object"], q["metric"][0],
                                                      comparison_key=q.get("comparison_key", [None])[0])
                        elif parsed.path == "/api/artifacts":
                            value = service.get_artifacts(q["run"][0], q.get("slice", [None])[0])
                        else:
                            self.send_error(404)
                            return
                    body, content_type = json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (KeyError, ValueError) as exc:
                self.send_error(400, str(exc))

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    with Store(args.db, read_only=True) as store:
        store.validate_schema()
    ThreadingHTTPServer((args.host, args.port), handler_for(args.db)).serve_forever()


if __name__ == "__main__":
    main()
