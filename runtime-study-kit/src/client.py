"""Example feed client: original SDK in iv8, then one HTTP GET per requested page.

The SDK has no network access. Imported capture state is an explicit dependency.
No automatic business retries, challenge solving or synthetic behavior upload.
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, math, os, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlsplit
from progress_log import PREFIX, ProgressLog, signature_info, value_info

ROOT=Path(__file__).resolve().parents[1]
CAP=ROOT/'assets/vendor'
STATE=CAP/'browser-state.private.json'
EXPECTED=('hash-helper.js', 'runtime.js')
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def save(p,o):p.write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def boot_sign(page,out,state_path=STATE,logger=None):
    log=logger or ProgressLog(child=True)
    log.event('SIGNER_PROCESS', '启动独立签名进程', page=page, interpreter=sys.executable)
    import iv8
    state=read(state_path);environment=state['environment']
    log.event('ENV_LOAD', '加载本地浏览器环境画像', state_file=str(state_path),
              origin=environment.get('location',{}).get('origin'),
              platform=environment.get('navigator',{}).get('platform'),
              hardware_concurrency=environment.get('navigator',{}).get('hardwareConcurrency'),
              viewport=environment.get('window',{}),
              iv8=iv8.__version__, v8=iv8.v8_version,
              state_origin='已保存的浏览器会话，当前进程不启动浏览器')
    with iv8.JSContext(mode='prod',time_mode='system',environment=environment) as ctx:
        log.event('ENV_START', 'iv8 上下文已创建', mode='prod', time_mode='system', clock='当前时钟；没有固定测试时间')
        ctx.eval((ROOT/'src/env/compat.js').read_text(encoding='utf-8'),name='message_channel_compat.js')
        ctx.eval((ROOT/'src/env/offline.js').read_text(encoding='utf-8'),name='sdk_network_disabled.js')
        log.event('ENV_PATCH', '兼容补丁与 SDK 离线出口已安装',
                  message_channel_fixed=ctx.eval('!!window.__runtimeMessageChannelFix',to_py=True),
                  sdk_network='XHR/fetch/beacon 等只在本地记录；签名 SDK 不自行发送请求')
        # Keep cookies from the captured document only, never other-domain cookies.
        ctx.eval('window.__capture='+json.dumps({'local':state.get('localStorage',{}),'session':state.get('sessionStorage',{}),'cookie':state.get('cookie','')})+';for(const[k,v]of Object.entries(__capture.local))localStorage.setItem(k,v);for(const[k,v]of Object.entries(__capture.session))sessionStorage.setItem(k,v);for(const x of __capture.cookie.split(";").map(x=>x.trim()))if(x)document.cookie=x+"; path=/";',name='captured_session.js')
        log.event('STATE_IMPORT', '导入 Cookie 和存储状态',
                  cookie_names=[c.split('=',1)[0].strip() for c in state.get('cookie','').split(';') if '=' in c],
                  local_storage_keys=sorted(state.get('localStorage',{})),
                  session_storage_keys=sorted(state.get('sessionStorage',{})),
                  algorithm_cache=value_info(state.get('localStorage',{}).get('runtime_algorithm_cache','')),
                  device_ticket_source=state.get('requestDefaults',{}).get('source','设备票据默认空值'))
        cache_entries=[]
        try:
            cache=json.loads(state.get('localStorage',{}).get('runtime_algorithm_cache','{}'))
            for fp, apps in cache.items():
                for app_id, cached in apps.items():
                    if app_id!='local-app':continue
                    issued=cached.get('t');ttl=cached.get('e')
                    expires=issued+ttl*1000 if isinstance(issued,(int,float))and isinstance(ttl,(int,float))else None
                    cache_entries.append({'sdk_app_id':app_id,'fingerprint':value_info(fp),
                                          'issued_at_ms':issued,'ttl_seconds':ttl,'expires_at_ms':expires,
                                          'expired_by_host_clock':expires is not None and time.time()*1000>=expires,
                                          'encoded_payload':value_info(cached.get('v',''))})
        except (ValueError,TypeError,AttributeError):
            log.event('CACHE_READ', '缓存元信息无法解析，具体分支由原 SDK 决定', key='runtime_algorithm_cache')
        else:
            log.event('CACHE_READ', '检查当前 appId 的算法缓存元信息', key='runtime_algorithm_cache',
                      entries=cache_entries, consumer='原 SDK 解析缓存中的 tk/algo；日志不输出原值')
        if state.get('dom'):
            ctx.eval('window.__domSnapshot='+json.dumps(state['dom'])+';')
            ctx.eval((ROOT/'src/env/dom_snapshot.js').read_text(encoding='utf-8'),name='observed_dom_shape.js')
            log.event('DOM_INIT', '按已采集页面结构初始化 DOM',
                      observed_head_children=state['dom'].get('headChildren'),
                      observed_body_children=state['dom'].get('bodyChildren'),
                      actual=ctx.eval('({headChildren:document.head.childElementCount,bodyChildren:document.body.childElementCount})',to_py=True))
        expected_hashes=read(CAP/'manifest.private.json')
        for f in EXPECTED:
            expected=expected_hashes[f]
            if digest(CAP/f)!=expected:raise RuntimeError('SDK asset version changed: '+f)
            log.event('SDK_LOAD', '校验并加载原版脚本', asset=f, sha256=expected, bytes=(CAP/f).stat().st_size)
            ctx.eval((CAP/f).read_text(encoding='utf-8'),name=f)
        ctx.eval("window.PSign=new ParamsSign({appId:'local-app',preRequest:false});",name='original_signer_init.js')
        log.event('SIGNER_INIT', 'ParamsSign 实例已初始化', sdk_app_id='local-app',
                  pre_request=False, algorithm_source='由原 SDK 读取缓存或选择降级分支；不是复用历史 request_signature')
        ctx.eval('window.__page='+str(page)+';window.__defaults='+json.dumps(state.get('requestDefaults',{}))+';')
        prepared=ctx.eval("(()=>{const body={page:__page,pagesize:25,area:'',source:'pc-home',kuangjia:1,notJsonp:true,...(__defaults.body||{}),page:__page};const t=Date.now();window.__body=body;window.__input={functionId:'feed_page',appid:'example-client',body:SHA256(JSON.stringify(body)),client:'pc',clientVersion:'1.0.0',t};return {body,input:__input}})()",to_py=True)
        log.event('SIGN_INPUT', '组装业务参数并计算正文摘要', page=page, body=prepared['body'],
                  body_sha256=prepared['input']['body'], timestamp=prepared['input']['t'],
                  signed_field_order=sorted(prepared['input']),
                  transform='SHA256(JSON.stringify(body)) → PSign.signSync(input)')
        started=time.monotonic()
        result=ctx.eval("(()=>{const signed=PSign.signSync(__input);if(!signed.request_signature)throw Error('SDK returned no request_signature');const params={appid:'example-client',client:'pc',clientVersion:'1.0.0',t:__input.t,uuid:(__capture.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('__session_metrics='))||'').slice(6),body:JSON.stringify(__body),functionId:'feed_page',request_signature:signed.request_signature,'x-api-eid-token':__defaults.eid||'',loginType:3};return {params,sdk:{normal:PSign._isNormal,version:PSign._version,fingerprint:PSign._fingerprint,token:PSign._token,defaultToken:PSign._defaultToken},bodyHash:__input.body,iv8Requests:__runtimeOffline.requests.length}})()",to_py=True)
        log.event('SIGN_RESULT', '原版 SDK 已生成本次签名', duration_ms=round((time.monotonic()-started)*1000,2),
                  version=result['sdk']['version'], normal_algorithm=result['sdk']['normal'],
                  fingerprint=value_info(result['sdk']['fingerprint']),
                  algorithm_token=value_info(result['sdk']['token']),
                  default_token=value_info(result['sdk']['defaultToken']),
                  request_signature=value_info(result['params']['request_signature']), segments=signature_info(result['params']['request_signature']),
                  device_eid=value_info(result['params']['x-api-eid-token']), uuid=value_info(result['params']['uuid']))
        pending=ctx.eval('__runtimeOffline.requests.map(x=>({method:x.method,kind:x.kind,endpoint:String(x.url).split("?")[0],bodyLength:String(x.body||"").length}))',to_py=True)
        log.event('SDK_OUTBOX', '核对 SDK 本地网络记录', count=len(pending), requests=pending,
                  forwarded=False, note='这些记录未发送；也不代表已完成前置上报或算法刷新')
    result['runtime']={'iv8':iv8.__version__,'v8':iv8.v8_version};save(out,result)
    log.event('SIGNER_DONE', '签名结果已保存，上下文已关闭', private_file=str(out))

def sign_child(args, log):
    """Relay only structured child events; iv8 native output stays off user logs."""
    import queue, threading
    child=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           text=True,encoding='utf-8',errors='replace')
    lines=queue.Queue()
    def collect():
        for line in child.stdout:lines.put(line)
        lines.put(None)
    thread=threading.Thread(target=collect,daemon=True);thread.start()
    deadline=time.monotonic()+15
    try:
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('iv8 signer exceeded 15 seconds')
            try:line=lines.get(timeout=min(.2,remaining))
            except queue.Empty:continue
            if line is None:break
            if line.startswith(PREFIX):
                log.receive(json.loads(line[len(PREFIX):]))
        code=child.wait(timeout=max(.01,deadline-time.monotonic()))
        if code:raise RuntimeError('iv8 signing failed; child code '+str(code))
    finally:
        if child.poll()is None:child.kill();child.wait()
        child.stdout.close();thread.join(timeout=1)

def cookie_match(c,host):
    domain=c.get('domain','').lstrip('.');return host==domain or c.get('domain','').startswith('.') and host.endswith('.'+domain)
def cookies_list(session):return list(getattr(session.cookies,'jar',session.cookies))
def set_cookie(session,name,value,domain,path='/',expires=None):
    from requests.cookies import create_cookie
    jar=getattr(session.cookies,'jar',session.cookies);jar.set_cookie(create_cookie(name,value,domain=domain,path=path,expires=expires))
def request_page(page,run,session,state,captured_headers=False,captured_cookie=False,log=None,dry_run=False):
    log=log or ProgressLog()
    log.event('PAGE_START', '开始处理商品页', page=page)
    signed=run/f'page-{page}-signed.private.json'
    sign_child([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),'--sign-child','--page',str(page),'--output',str(signed),'--state',str(state['_statePath'])],log)
    sign=read(signed);headers={'User-Agent':state['environment']['navigator']['userAgent'],'Accept':'application/json, text/plain, */*','Referer':'https://www.example.invalid/','Origin':'https://www.example.invalid','Accept-Language':'zh-CN,zh;q=0.9','x-referer-page':'https://www.example.invalid/','x-rp-client':'h5_2.2.0'}
    if captured_headers:
        entries=read(state.get('_harPath',ROOT/'assets/flow/session.private.har'))['log']['entries'];entry=next(e for e in entries if e['request']['method']=='GET'and any(p['name']=='functionId'and p['value']=='feed_page'for p in e['request'].get('queryString',[])))
        headers={h['name']:h['value']for h in entry['request']['headers']if not h['name'].startswith(':')and h['name'].lower()not in ['cookie','host','content-length','connection','accept-encoding']}
        if captured_cookie:
            cookie=next((h['value']for h in entry['request']['headers']if h['name'].lower()=='cookie'),None)
            if cookie:headers['Cookie']=cookie
    log.event('REQUEST_PREPARE', '准备 HTTP 请求', page=page, method='GET', endpoint='https://api.m.example.invalid/',
              function_id=sign['params']['functionId'], query_keys=list(sign['params']),
              header_names=sorted(headers), cookie_source='当前成功抓包的 Cookie 请求头'if captured_cookie else'HTTP session Cookie jar',
              cookie_names=[x.split('=',1)[0].strip()for x in headers.get('Cookie','').split(';')if '='in x]if captured_cookie else[c.name for c in cookies_list(session)],
              body_sha256=sign['bodyHash'], request_signature=value_info(sign['params']['request_signature']),
              automatic_retry=False)
    if dry_run:
        summary={'page':page,'dryRun':True,'requestSent':False,'request_signatureLength':len(sign['params']['request_signature']),
                 'signVersion':sign['sdk']['version'],'runtime':sign['runtime'],'evidence':str(run.relative_to(ROOT))}
        save(run/f'page-{page}-summary.json',summary)
        log.event('DRY_RUN', '初始化和签名已完成，HTTP 请求未发送', page=page)
        return summary
    log.event('HTTP_SEND', '发送商品数据请求', page=page, endpoint='https://api.m.example.invalid/', timeout_seconds=[10,20])
    start=time.monotonic();response=session.get('https://api.m.example.invalid/',params=sign['params'],headers=headers,timeout=(10,20),allow_redirects=False)
    elapsed=time.monotonic()-start;(run/f'page-{page}-response.private.bin').write_bytes(response.content)
    record={'startedDateTime':datetime.datetime.now(datetime.timezone.utc).isoformat(),'page':page,'url':response.request.url,'requestHeaders':dict(response.request.headers),'responseStatus':response.status_code,'responseHeaders':dict(response.headers),'responseBodySha256':hashlib.sha256(response.content).hexdigest(),'elapsedSeconds':elapsed,'source':'live requests.Session HTTP response; request_signature generated in current child iv8; no fixture or browser request'}
    save(run/f'page-{page}-http.private.json',record)
    log.event('HTTP_RESPONSE', '收到服务器响应', page=page, http_status=response.status_code,
              duration_ms=round(elapsed*1000,2), bytes=len(response.content),
              content_type=response.headers.get('content-type',''), header_names=list(response.headers),
              response_sha256=record['responseBodySha256'])
    directive=response.headers.get('x-rp-sdtoken','');parts=directive.split(';',2)
    if len(parts)==3 and parts[0]=='set' and parts[1].lstrip('-').isdigit():
        set_cookie(session,'sdtoken',parts[2],domain='.example.invalid',path='/',expires=int(time.time())+int(parts[1]))
        log.event('COOKIE_UPDATE', '处理服务器 sdtoken 更新指令', action='set', lifetime_seconds=int(parts[1]), token=value_info(parts[2]))
    elif directive.startswith('del;'):
        for c in cookies_list(session):
            if c.name=='sdtoken':getattr(session.cookies,'jar',session.cookies).clear(c.domain,c.path,c.name)
        log.event('COOKIE_UPDATE', '处理服务器 sdtoken 删除指令', action='delete')
    save(run/'http-session.private.json',{'cookies':[{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path,'expires':c.expires}for c in cookies_list(session)]})
    try:obj=response.json()
    except ValueError:obj={}
    data=obj.get('data',obj)if isinstance(obj,dict)else{};feed=data.get('feedList',[])if isinstance(data,dict)else[];products=[x for x in feed if isinstance(x,dict)and x.get('sku')]
    accepted=response.status_code==200 and isinstance(data,dict)and data.get('success')is True and len(products)>0
    fields=('sku','bn','t','name','title','jp','price','img','source','type')
    output=[{k:x[k]for k in fields if k in x}for x in products]
    prices={str(x.get('id','')).removeprefix('J_'):x for x in (data.get('priceList',[])if isinstance(data,dict)else[])if isinstance(x,dict)}
    for x in output:
        p=prices.get(str(x['sku']),{});x['price']=p.get('p',x.get('price',x.get('jp')));x['title']=x.get('t',x.get('title',x.get('name','')));x['url']='https://item.example.invalid/'+str(x['sku'])+'.html'
        if x.get('img')and not x['img'].startswith(('http://','https://')):x['imageUrl']='https://m.media.example.invalid/media/'+x['img']
    save(run/f'page-{page}-products.json',output)
    summary={'page':page,'accepted':accepted,'httpStatus':response.status_code,'outerCode':obj.get('code')if isinstance(obj,dict)else None,'success':data.get('success')if isinstance(data,dict)else None,'feedCount':len(feed),'productCount':len(products),'priceCount':len(data.get('priceList',[]))if isinstance(data,dict)else 0,'responseKeys':list(obj)if isinstance(obj,dict)else[],'dataKeys':list(data)if isinstance(data,dict)else[],'signNormal':sign['sdk']['normal'],'request_signatureLength':len(sign['params']['request_signature']),'signVersion':sign['sdk']['version'],'runtime':sign['runtime'],'elapsedSeconds':round(elapsed,3),'evidence':str(run.relative_to(ROOT)),'firstProducts':output[:3]}
    save(run/f'page-{page}-summary.json',summary)
    log.event('PRODUCT_RESULT', '商品响应验收通过'if accepted else'商品响应未通过验收',
              page=page, accepted=accepted, outer_code=summary['outerCode'], success=summary['success'],
              feed_count=summary['feedCount'], product_count=summary['productCount'],
              price_count=summary['priceCount'], products_file=str(run/f'page-{page}-products.json'),
              summary_file=str(run/f'page-{page}-summary.json'))
    summary['productsFile']=str(run/f'page-{page}-products.json')
    save(run/f'page-{page}-summary.json',summary)
    return summary
def main():
    try: default_max=int(os.environ.get('RUNTIME_MAX_PAGES','3'))
    except ValueError: default_max=3
    p=argparse.ArgumentParser();p.add_argument('--page',type=int,default=1);p.add_argument('--pages',type=int,default=1);p.add_argument('--max-pages',type=int,default=default_max,help='单次页数上限，默认 3，最大 40');p.add_argument('--delay',type=float,default=1.5,help='页间等待秒数，最小 1.0；顺序请求');p.add_argument('--concurrency',type=int,default=1,help='并发数固定为 1；不支持并发请求');p.add_argument('--sign-child',action='store_true');p.add_argument('--output',type=Path);p.add_argument('--session',type=Path);p.add_argument('--state',type=Path,default=STATE);p.add_argument('--har',type=Path);p.add_argument('--captured-headers',action='store_true');p.add_argument('--captured-cookie',action='store_true');p.add_argument('--transport',choices=['requests','curl'],default='requests');display=p.add_mutually_exclusive_group();display.add_argument('--quiet',action='store_true',help='Only print result JSON; progress.jsonl is still saved');display.add_argument('--verbose',action='store_true',help='Show every stage and detailed redacted fields');p.add_argument('--json',dest='json_output',action='store_true',help='Also print result JSON to stdout');p.add_argument('--dry-run',action='store_true',help='Initialize iv8 and sign; do not send HTTP requests');a=p.parse_args()
    if a.sign_child:boot_sign(a.page,a.output,a.state);return
    if not 1<=a.max_pages<=40:raise SystemExit('--max-pages must be 1–40')
    if a.concurrency!=1:raise SystemExit('为控制请求压力，并发数固定为 1；请使用 --delay 调整页间等待')
    if not math.isfinite(a.delay) or not 1.0<=a.delay<=60.0:raise SystemExit('--delay 必须是 1.0–60.0 秒的有限数值')
    if not 1<=a.pages<=a.max_pages or not 1<=a.page<=40 or a.page+a.pages-1>40:raise SystemExit(f'页数必须为 1–{a.max_pages}，且页码范围为 1–40')
    import requests
    run=ROOT/'assets/live'/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');run.mkdir(parents=True,exist_ok=False)
    log=ProgressLog(run/'progress.jsonl',quiet=a.quiet,verbose=a.verbose)
    log.event('START', '启动商品请求客户端', page=a.page, pages=a.pages, max_pages=a.max_pages,
              delay_seconds=a.delay, concurrency=1, dry_run=a.dry_run,
              output_directory=str(run), progress_file=str(run/'progress.jsonl'), browser_started=False)
    state=read(a.state);state['_statePath']=str(a.state.resolve());state['_harPath']=str(a.har.resolve())if a.har else str(ROOT/'assets/flow/session.private.har')
    if a.transport=='curl':
        from curl_cffi import requests as curl_requests
        session=curl_requests.Session(impersonate='chrome142')
    else:session=requests.Session()
    log.event('HTTP_INIT', 'HTTP 客户端已初始化', transport=a.transport,
              profile='Chromium 142'if a.transport=='curl'else'requests 默认传输',
              sdk_network_separate=True, state_file=state['_statePath'])
    for c in state.get('cookies',[]):
        if cookie_match(c,'api.m.example.invalid'):set_cookie(session,c['name'],c['value'],domain=c['domain'],path=c.get('path','/'))
    if a.session:
        for c in read(a.session)['cookies']:
            if cookie_match(c,'api.m.example.invalid'):set_cookie(session,c['name'],c['value'],domain=c['domain'],path=c.get('path','/'),expires=c.get('expires'))
    log.event('HTTP_SESSION', '加载匹配接口域名的 Cookie', count=len(cookies_list(session)),
              cookie_names=[c.name for c in cookies_list(session)], state_refresh='不自动刷新，使用已保存会话')
    results=[]
    try:
        for page in range(a.page,a.page+a.pages):
            result=request_page(page,run,session,state,a.captured_headers,a.captured_cookie,log,a.dry_run);results.append(result)
            if a.json_output or a.quiet:print(json.dumps(result,ensure_ascii=False),flush=True)
            if not a.dry_run and not result['accepted']:break
            if page+1<a.page+a.pages and not a.dry_run:
                log.event('PAGE_WAIT', '顺序请求，等待后继续下一页', wait_seconds=a.delay, concurrency=1)
                time.sleep(a.delay)
    except Exception as e:
        import traceback
        err={'errorType':type(e).__name__,'stage':'live client','detail':'request/signing did not complete; no retry performed','frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}for f in traceback.extract_tb(e.__traceback__)]};save(run/'error.json',err);log.event('ERROR','请求流程异常，已停止',error_type=err['errorType'],frames=err['frames'])
        if a.json_output or a.quiet:print(json.dumps(err))
        raise SystemExit(2)
    finally:
        session.close();save(run/'run-summary.json',{'results':results,'browserUsed':False,'transport':a.transport,'dryRun':a.dry_run,'requestPolicy':{'startPage':a.page,'requestedPages':a.pages,'maxPages':a.max_pages,'delaySeconds':a.delay,'concurrency':1,'pageWindow':40},'progressLog':'progress.jsonl','sessionDependency':'captured browser state; SDK code unchanged; no live report/algo refresh in baseline'})
        log.event('FINISH', '请求任务结束，HTTP session 已关闭', pages_processed=len(results),
                  accepted_pages=sum(bool(x.get('accepted'))for x in results), dry_run=a.dry_run,
                  run_summary=str(run/'run-summary.json'))
    if not results or not a.dry_run and not all(x['accepted']for x in results):raise SystemExit(1)
if __name__=='__main__':main()
