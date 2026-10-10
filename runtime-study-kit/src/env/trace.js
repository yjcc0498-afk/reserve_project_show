(() => {
  window.__runtimeTrace=[];
  function snapshot(v,depth=0){if(typeof v==='function')return {type:'function',source:v.toString()};if(depth>4)return {type:typeof v};if(Array.isArray(v))return v.slice(0,100).map(x=>snapshot(x,depth+1));if(v&&typeof v==='object'){let o={};for(const [k,x] of Object.entries(v).slice(0,80))o[k]=snapshot(x,depth+1);return o;}return v;}
  const p=ParamsSign.prototype;
  for(const name of Object.getOwnPropertyNames(p)){
    if(!name.startsWith('_$')||typeof p[name]!=='function')continue;
    const original=p[name];
    p[name]=function(...args){let r=original.apply(this,args);if(__runtimeTrace.length<500)__runtimeTrace.push({name,args:snapshot(args),result:snapshot(r),state:{fingerprint:this._fingerprint,token:this._token,isNormal:this._isNormal,defaultToken:this._defaultToken}});return r;};
  }
})();
