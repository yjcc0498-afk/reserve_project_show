// iv8 0.1.4 creates MessageChannel with null ports. This pair delivers queued
// messages through the existing iv8 timer loop; SDK code remains unchanged.
(() => {
  let broken = false;
  try { const c = new MessageChannel(); broken = !c.port1 || !c.port2; } catch (_) { broken = true; }
  if (!broken) return;
  function port() {
    return {onmessage:null,onmessageerror:null,_listeners:[],_closed:false,
      start(){},close(){this._closed=true;},
      addEventListener(type,fn){if(type==='message')this._listeners.push(fn);},
      removeEventListener(type,fn){this._listeners=this._listeners.filter(x=>x!==fn);}};
  }
  window.MessageChannel = function MessageChannel() {
    this.port1=port();this.port2=port();
    for(const [from,to] of [[this.port1,this.port2],[this.port2,this.port1]])
      from.postMessage=function(data){setTimeout(()=>{if(to._closed)return;const e={type:'message',data,target:to};if(typeof to.onmessage==='function')to.onmessage(e);for(const f of to._listeners)f(e);},0);};
  };
  window.__runtimeMessageChannelFix=true;
})();
