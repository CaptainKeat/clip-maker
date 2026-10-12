/* Workspace preferences never change the project's output recipe. */
(() => {
  'use strict';
  const names=['editing','effects','audio','captions'];
  const defaults={timeline:320,bin:250,inspector:300,effects:260};
  const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
  function normalize(value={}) {
    const layouts={};
    for(const name of names){layouts[name]={};for(const [key,fallback]of Object.entries(defaults)){const n=Number(value.layouts?.[name]?.[key]);layouts[name][key]=Number.isFinite(n)&&n>0?clamp(n,120,2400):fallback;}}
    return {selected:names.includes(value.selected)?value.selected:'editing',layouts};
  }
  function fit(preferred,width,height,handheld=false){
    const timeline=clamp(preferred.timeline,handheld?130:180,Math.max(handheld?130:180,handheld?Math.min(height*.42,height-248):height-210));
    const available=Math.max(320,width-250),left=clamp(preferred.inspector,180,available*.55),right=clamp(preferred.effects,180,Math.max(180,available-left));
    return {timeline,bin:clamp(preferred.bin,150,Math.max(150,available*.45)),inspector:left,effects:right};
  }
  const api={names,defaults,normalize,fit};
  if(typeof module!=='undefined')module.exports=api;else window.StudioLayout=api;
})();
