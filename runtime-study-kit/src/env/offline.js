// All SDK transports are replaced before any original SDK is loaded.
// These implementations never delegate to native send/fetch/beacon/WebSocket.
(() => {
  window.__runtimeOffline = {requests:[], fixtureHits:0, fixtureMisses:0, listeners:[]};
  function fixture(method,url,body) {
    function norm(u){try{return new URL(u,location.href)}catch(_){return {origin:'',pathname:u,searchParams:new URLSearchParams()}}}
    function fid(u,b){let n=norm(u);let f=n.searchParams.get('functionId');if(!f&&b){try{f=JSON.parse(b).functionId}catch(_){f=new URLSearchParams(b).get('functionId')}}return f||''}
    const target=norm(url),functionId=fid(url,body);
    const f=(window.__runtimeFixtures||[]).find(x=>{const n=norm(x.url);return x.method===method&&n.origin===target.origin&&n.pathname===target.pathname&&fid(x.url,x.body)===functionId;});
    if(f){__runtimeOffline.fixtureHits++;return f.response;}
    __runtimeOffline.fixtureMisses++;return {status:599,text:'{"offlineMissingFixture":true}',headers:{}};
  }
  const X=XMLHttpRequest.prototype;
  X.open=function(m,u){this.__runtimeRequest={method:String(m).toUpperCase(),url:String(u),headers:{}};};
  X.setRequestHeader=function(k,v){this.__runtimeRequest.headers[String(k)]=String(v);};
  X.addEventListener=function(type,fn){(this.__runtimeListeners||(this.__runtimeListeners={}))[type]=((this.__runtimeListeners||{})[type]||[]).concat([fn]);};
  X.send=function(body){const q={...this.__runtimeRequest,body:body==null?'':String(body)};__runtimeOffline.requests.push(q);const r=fixture(q.method,q.url,q.body);const self=this;setTimeout(()=>{
    for(const [k,v] of Object.entries({status:r.status,readyState:4,responseText:r.text,response:r.text,responseURL:q.url}))try{Object.defineProperty(self,k,{value:v,writable:true,configurable:true});}catch(_){}
    self.getAllResponseHeaders=()=>Object.entries(r.headers||{}).map(([k,v])=>k+': '+v).join('\r\n');
    self.getResponseHeader=k=>Object.entries(r.headers||{}).find(([n])=>n.toLowerCase()===String(k).toLowerCase())?.[1]||null;
    for(const type of ['readystatechange','load','loadend']){const e={type,target:self};try{if(typeof self['on'+type]==='function')self['on'+type](e);for(const fn of self.__runtimeListeners?.[type]||[])fn.call(self,e);}catch(err){__runtimeOffline.listeners.push(String(err));}}
  },0);};
  X.abort=function(){};
  window.fetch=function(input,options){const q={kind:'fetch',method:options?.method||'GET',url:String(input?.url||input),body:String(options?.body||'')};__runtimeOffline.requests.push(q);const r=fixture(q.method,q.url,q.body);return Promise.resolve(new Response(r.text,{status:r.status,headers:r.headers}));};
  navigator.sendBeacon=function(u,b){__runtimeOffline.requests.push({kind:'beacon',method:'POST',url:String(u),body:String(b||'')});return true;};
  window.WebSocket=function(){throw new Error('OfflineTransportBlocked: WebSocket');};
  window.EventSource=function(){throw new Error('OfflineTransportBlocked: EventSource');};
  for(const [proto,key] of [[HTMLImageElement.prototype,'src'],[HTMLScriptElement.prototype,'src']]){
    let values=new WeakMap();Object.defineProperty(proto,key,{configurable:true,get(){return values.get(this)||''},set(v){values.set(this,String(v));__runtimeOffline.requests.push({kind:key==='src'&&this.tagName==='SCRIPT'?'script':'image',method:'GET',url:String(v),body:''});}});
  }
})();
