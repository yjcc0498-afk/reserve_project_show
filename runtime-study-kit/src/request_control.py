"""One captured-success HTTP control. No signer conclusion drawn from replay."""
import json,hashlib,datetime
from pathlib import Path
from curl_cffi import requests
from urllib.parse import parse_qsl,urlsplit
R=Path(__file__).resolve().parents[1];B=R/'assets/local-session';H=json.loads((B/'session.private.har').read_text(encoding='utf-8'));e=next(e for e in H['log']['entries']if e['request']['method']=='GET'and'functionId=feed_page'in e['request']['url']);q=e['request'];headers={x['name']:x['value']for x in q['headers']if not x['name'].startswith(':')and x['name'].lower()not in ['host','content-length','connection','accept-encoding']}
O=R/'assets/live'/('control-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));O.mkdir()
with requests.Session(impersonate='chrome142')as s:
    r=s.get(q['url'],headers=headers,timeout=20,allow_redirects=False);(O/'response.private.bin').write_bytes(r.content)
    try:o=r.json();d=o.get('data',{})
    except Exception:o={};d={}
    summary={'status':r.status_code,'bytes':len(r.content),'success':d.get('success')if isinstance(d,dict)else None,'feedCount':len(d.get('feedList',[]))if isinstance(d,dict)else 0,'source':'single original captured URL and Cookie header, no iv8-generated params','headerNames':list(r.headers)}
    (O/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False))
