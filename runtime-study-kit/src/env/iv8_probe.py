"""Run Example original SDK offline in the existing iv8 runtime."""
from pathlib import Path
import argparse, hashlib, json, sys
import iv8
ROOT=Path(__file__).resolve().parents[2]
CAP=ROOT/'assets/vendor'
OUT=ROOT/'assets/iv8'
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--state',action='store_true');ap.add_argument('--all',action='store_true');ap.add_argument('--compat',action='store_true');ap.add_argument('--trace',action='store_true');ap.add_argument('--readable',action='store_true');ap.add_argument('--fixed',action='store_true');ap.add_argument('--settle',action='store_true');ap.add_argument('--fixtures',action='store_true');ap.add_argument('--checkpoints',action='store_true');ap.add_argument('--skip-jquery',action='store_true');ap.add_argument('--vm-trace',action='store_true');ap.add_argument('--sync-oracle',action='store_true');a=ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    env={'location':{'href':'https://www.example.invalid/index.html','origin':'https://www.example.invalid','protocol':'https:','hostname':'www.example.invalid','pathname':'/index.html','search':'','hash':''},'document':{'referrer':'','domain':'www.example.invalid'},'navigator':{'userAgent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36','platform':'Win32','language':'zh-CN','languages':['zh-CN','zh'],'hardwareConcurrency':8,'deviceMemory':4,'maxTouchPoints':10,'webdriver':False}}
    p=CAP/'browser-state.private.json';state=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    if state.get('environment'):env=state['environment']
    out={'iv8':iv8.__version__,'v8':iv8.v8_version,'mode':'offline-original-sdk','loads':[],'stateImported':bool(a.state and state)}
    with iv8.JSContext(mode='prod',time_mode='system',environment=env) as ctx:
        if a.fixed:ctx.eval("const __RealDate=Date;window.Date=class Date extends __RealDate{constructor(...a){super(...(a.length?a:[1791507032000]))}static now(){return 1791507032000}};let __seed=20261009;Math.random=()=>{__seed=(Math.imul(__seed,1664525)+1013904223)>>>0;return __seed/4294967296};",name='controlled_test_conditions.js')
        if a.compat:ctx.eval(Path(__file__).with_name('compat.js').read_text(encoding='utf-8'),name='iv8_message_channel_compat.js')
        ctx.eval(Path(__file__).with_name('offline.js').read_text(encoding='utf-8'),name='strict_offline_transport.js')
        if a.fixtures:
            fixtures=json.loads((ROOT/'assets/flow/fixtures.private.json').read_text(encoding='utf-8'));ctx.eval('window.__runtimeFixtures='+json.dumps(fixtures,ensure_ascii=False)+';',name='captured_response_fixtures.js')
        ctx.eval("window.__runtimeprobe={net:[]};const _xo=XMLHttpRequest.prototype.open,_xs=XMLHttpRequest.prototype.send;XMLHttpRequest.prototype.open=function(m,u){this.__runtimep={method:m,url:String(u)};return _xo.apply(this,arguments)};XMLHttpRequest.prototype.send=function(b){__runtimeprobe.net.push({...this.__runtimep,body:b==null?'':String(b)});return _xs.apply(this,arguments)};const _f=window.fetch;window.fetch=function(u,o){__runtimeprobe.net.push({kind:'fetch',url:String(u),body:String(o&&o.body||'')});return _f.apply(this,arguments)};",name='jd_recorder.js')
        if a.state and state:
            ctx.eval('const __runtimestate='+json.dumps(state,ensure_ascii=False)+';for(const [k,v] of Object.entries(__runtimestate.localStorage||{}))localStorage.setItem(k,v);for(const [k,v] of Object.entries(__runtimestate.sessionStorage||{}))sessionStorage.setItem(k,v);for(const c of __runtimestate.cookies||[])document.cookie=c.name+"="+c.value+"; path=/";',name='same_session_state.js')
        assets=['hash-helper.js','runtime.js']
        if a.all:assets=['jquery.js',*assets,'behavior-runtime.js','device-runtime.js','gatherInfo.js','pc-core-disposal.js','dra-browser.js','wl.js']
        if a.skip_jquery:assets=[f for f in assets if f not in ['jquery.js','wl.js']]
        for f in assets:
            p=(ROOT/'assets/readable'/f) if a.readable and (ROOT/'assets/readable'/f).exists() else CAP/f
            if a.vm_trace and f=='runtime.js':p=ROOT/'assets/vm/sdk-observed.js'
            if not p.exists():out['loads'].append({'file':f,'error':'asset missing'});continue
            try:
                print('LOADING '+f,flush=True)
                src=p.read_text(encoding='utf-8')
                if a.checkpoints and f=='runtime.js':
                    needle='window.ParamsSign=_$mx';assert src.count(needle)==1
                    src=src.replace(needle,'(function(){window.__runtimeEncodeInputs=[];const __runtimeOldEncode=_$PN.encode;_$PN.encode=function(v){let plain;try{plain=_$Py.stringify(v)}catch(e){plain=String(e)};let encoded=__runtimeOldEncode.apply(this,arguments);__runtimeEncodeInputs.push({plain,encoded});return encoded;};})(),'+needle)
                if a.checkpoints and f=='behavior-runtime.js':
                    needle='function _$g7('
                    src=src.replace(needle,'function __runtimeOriginalBehaviorEncode(',1)
                    src+='\n'
                    # Function declaration hoisting keeps every original call valid.
                    end=src.rfind('}();');assert end!=-1
                    src=src[:end]+"window.__runtimeBehaviorInputs=[];function _$g7(v,n){let encoded=__runtimeOriginalBehaviorEncode(v,n);__runtimeBehaviorInputs.push({plain:v,encoded});return encoded;}"+src[end:]
                ctx.eval(src,name=f);out['loads'].append({'file':f,'loaded':True,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
            except Exception as e:out['loads'].append({'file':f,'error':str(e).split(' -> ')[0][:1200]})
            print('LOADED '+f+': '+str(out['loads'][-1].get('error','ok')),flush=True)
        if a.trace:
            try:ctx.eval(Path(__file__).with_name('trace.js').read_text(encoding='utf-8'),name='trace_original_methods.js')
            except Exception as e:out['traceError']=str(e).split(' -> ')[0][:500]
        try:ctx.eval("window.PSign=new window.ParamsSign({appId:'local-app',preRequest:false});window.__runtimeout=[];window.__runtimestatus='pending';(async()=>{for(let page=1;page<=3;page++){const body={page,pagesize:25,area:'',source:'pc-home',kuangjia:1,notJsonp:true};let input={functionId:'feed_page',appid:'example-client',body:SHA256(JSON.stringify(body)),client:'pc',clientVersion:'1.0.0',t:Date.now()};let result=await PSign.sign(input);__runtimeout.push({input,result})}__runtimestatus='resolved'})().catch(e=>{__runtimestatus='error:'+e.stack})",name='original_sdk_entry.js')
        except Exception as e:out['entryError']=str(e).split(' -> ')[0][:1200]
        if a.sync_oracle:
            # Explicit oracle experiment: use a fresh SDK instance and the same
            # synchronous calls as browser_replay, before timer advancement.
            ctx.eval("window.__runtimeout=[];window.PSign=new ParamsSign({appId:'local-app',preRequest:false});for(let page=1;page<=3;page++){let body={page,pagesize:25,area:'',source:'pc-home',kuangjia:1,notJsonp:true};let input={functionId:'feed_page',appid:'example-client',body:SHA256(JSON.stringify(body)),client:'pc',clientVersion:'1.0.0',t:Date.now()};__runtimeout.push({input,result:PSign.signSync(input)})};__runtimestatus='resolved';")
        for t in [0,100,500,2000,8000,20000]:
            try:ctx.eval(f'window.__iv8__.eventLoop.tick({t});window.__iv8__.eventLoop.drain()')
            except Exception as e:out.setdefault('tickErrors',[]).append(str(e)[:400]);break
            if ctx.eval("window.__runtimestatus||'not-started'",to_py=True)!='pending':break
        out['status']=ctx.eval("window.__runtimestatus||'not-started'",to_py=True)
        if a.settle:
            try:ctx.eval("document.dispatchEvent(new Event('DOMContentLoaded'));window.dispatchEvent(new Event('load'));",name='page_lifecycle.js')
            except Exception as e:out['lifecycleError']=str(e).split(' -> ')[0][:300]
            for t in [500,2000,8000,20000]:
                try:ctx.eval(f'window.__iv8__.eventLoop.tick({t});window.__iv8__.eventLoop.drain()')
                except Exception as e:out.setdefault('settleErrors',[]).append(str(e).split(' -> ')[0][:350])
        out['globals']=ctx.eval("({ParamsSign:typeof ParamsSign,PSign:typeof PSign,getJsToken:typeof getJsToken})",to_py=True)
        outputs=ctx.eval('window.__runtimeout||[]',to_py=True)
        out['outputs']=[{'inputKeys':list(x.get('input',{})),'resultKeys':list(x.get('result',{})) if isinstance(x.get('result'),dict) else [],'signatureLength':len(x.get('result',{}).get('request_signature','')) if isinstance(x.get('result'),dict) else 0,'segments':len(x.get('result',{}).get('request_signature','').split(';')) if isinstance(x.get('result'),dict) else 0} for x in outputs]
        net=ctx.eval('window.__runtimeOffline.requests',to_py=True);out['networkShapes']=[{'method':x.get('method'),'kind':x.get('kind'),'url':x.get('url','').split('?')[0],'bodyLength':len(x.get('body',''))} for x in net];out['transportBoundary']='All XHR/fetch/beacon/WebSocket/EventSource/image/script paths replaced before SDK load; no native send delegation.'
        traces=ctx.eval('window.__runtimeTrace||[]',to_py=True)
        out['traceShapes']=[{'name':x['name'],'argTypes':[type(v).__name__ for v in x['args']],'resultType':type(x.get('result')).__name__,'resultLength':len(x['result']) if isinstance(x.get('result'),(str,list,dict)) else None}for x in traces]
        suffix=('stateful' if a.state else 'fresh')+('-readable' if a.readable else '')+('-fixed' if a.fixed else '')+('-trace' if a.trace else '')+('-all' if a.all else '')+('-settled' if a.settle else '')+('-fixtures'if a.fixtures else '')+('-checkpoints'if a.checkpoints else '')+('-skipjquery'if a.skip_jquery else '')
        if a.vm_trace:suffix+='-vm'
        if a.sync_oracle:suffix+='-sync'
        out['fixtures']=ctx.eval('({hits:__runtimeOffline.fixtureHits,misses:__runtimeOffline.fixtureMisses,listenerErrors:__runtimeOffline.listeners})',to_py=True)
        vmtrace=ctx.eval('window.__runtimeVm||[]',to_py=True);out['vmOpcodeRecords']=len(vmtrace)
        (OUT/(suffix+'.private.json')).write_text(json.dumps({'outputs':outputs,'network':net,'traces':traces,'vm':vmtrace,'checkpoints':ctx.eval('({signature:window.__runtimeEncodeInputs||[],behavior:window.__runtimeBehaviorInputs||[]})',to_py=True),'storage':ctx.eval('({local:Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)])),session:Object.fromEntries(Object.keys(sessionStorage).map(k=>[k,sessionStorage.getItem(k)]))})',to_py=True)},ensure_ascii=False,indent=2),encoding='utf-8')
    p=OUT/(suffix+'-result.json');p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
