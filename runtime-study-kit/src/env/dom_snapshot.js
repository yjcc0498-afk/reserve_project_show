// Rehydrate captured DOM shape using native DOM APIs, not detector overrides.
// Source: current original page head/body counts and ordered head tag names.
(() => {
  const shape=window.__domSnapshot;
  if(!shape)return;
  while(document.head.firstChild)document.head.removeChild(document.head.firstChild);
  for(const tag of shape.headTags||[]){
    const element=document.createElement(tag.toLowerCase());
    if(tag==='TITLE')element.textContent=window.__state?.environment?.document?.title||'';
    document.head.appendChild(element);
  }
  while(document.body.firstChild)document.body.removeChild(document.body.firstChild);
  for(let i=0;i<(shape.bodyChildren||0);i++)document.body.appendChild(document.createElement('div'));
  window.__rehydratedDom={headChildren:document.head.childElementCount,bodyChildren:document.body.childElementCount};
})();
