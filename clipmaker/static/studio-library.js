/* Supported actions only. Property entries open controls; analysis tools review output. */
(() => {
  'use strict';
  const items=[
    {id:'speakers',group:'Audio analysis',name:'Split speakers',kind:'tool',target:'audio',keywords:'wespeaker scan splice diarization local'},
    {id:'transcribe',group:'Audio analysis',name:'Auto captions',kind:'tool',target:'primary',keywords:'whisper speech subtitles local transcribe'},
    {id:'volume',group:'Audio controls',name:'Volume & fades',kind:'controls',target:'audio',keywords:'gain fade mix'},
    {id:'duck',group:'Audio controls',name:'Duck under dialogue',kind:'controls',target:'sound',keywords:'music ducking ratio'},
    {id:'crossfade',group:'Audio controls',name:'Crossfade',kind:'controls',target:'sound',keywords:'transition blend next soundtrack'},
    {id:'frame',group:'Video controls',name:'Framing & speed',kind:'controls',target:'video',keywords:'fit fill position size transform'},
    {id:'freeze',group:'Video controls',name:'Freeze ending & fade',kind:'controls',target:'primary',keywords:'transition still freeze'},
    {id:'tracking',group:'Caption tools',name:'Track caption',kind:'controls',target:'caption',keywords:'target follow move'},
    {id:'style',group:'Caption tools',name:'Speaker style',kind:'controls',target:'caption',keywords:'presets font colour'},
    ...['pop','shake','grow','fall'].map(id=>({id,group:'Caption animation',name:id[0].toUpperCase()+id.slice(1),kind:'animation',target:'caption',keywords:'motion animate'}))
  ];
  function compatible(item,selection,media){
    if(!item||!selection)return false;
    const type=selection.type,audio=!!media?.metadata?.audio_streams?.length;
    return item.target==='audio'?audio&&['clips','sounds'].includes(type):item.target==='sound'?type==='sounds':item.target==='primary'?type==='clips'&&(item.id!=='transcribe'||audio):item.target==='caption'?type==='captions':item.target==='video'?type==='clips'||type==='overlays':false;
  }
  function search(term,scope='all'){const words=String(term||'').trim().toLowerCase().split(/\s+/);return items.filter(item=>(scope==='all'||item.group.toLowerCase().startsWith(scope))&&words.every(word=>(item.name+' '+item.group+' '+item.keywords).toLowerCase().includes(word)));}
  const api={items,compatible,search};if(typeof module!=='undefined')module.exports=api;else window.StudioLibrary=api;
})();
