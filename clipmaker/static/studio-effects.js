(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioEffects=api;})(typeof window==='undefined'?globalThis:window,()=>{
  'use strict';
  const copy=x=>JSON.parse(JSON.stringify(x));
  function target(recipe,selection,ordinal){
    const type=selection?.type,item=recipe?.[type]?.[selection?.index];
    if(!item||!['clips','sounds'].includes(type))throw new Error('Select an audio clip or linked source audio first.');
    if(type==='sounds')ordinal=item.audio_ordinal||0;
    else ordinal=ordinal??item.audio.find(a=>a.enabled)?.ordinal??0;
    const time=type==='sounds'?item.time:recipe.clips.slice(0,selection.index).reduce((n,c)=>n+(c.end-c.start)/c.speed+(c.freeze||0),0);
    return {type,id:item.id,item:copy(item),ordinal,time,key:(type==='sounds'?'sound:':'')+item.id+'/'+ordinal};
  }
  function apply(recipe,context,result,names,layout='turns',makeId=()=>crypto.randomUUID()){
    const original=recipe[context.type]?.find(c=>c.id===context.id);
    if(!original||JSON.stringify(original)!==JSON.stringify(context.item))throw new Error('The source clip changed after scanning. Scan it again before adding these tracks.');
    const r=copy(recipe),item=r[context.type].find(c=>c.id===context.id),cursor=context.type==='sounds'?item.time:r.clips.slice(0,r.clips.indexOf(item)).reduce((n,c)=>n+(c.end-c.start)/c.speed+(c.freeze||0),0);
    const mix=context.type==='clips'?item.audio.find(a=>a.ordinal===context.ordinal):item;
    const length=Math.min(result.duration,context.type==='sounds'?item.duration:(item.end-item.start)/item.speed);
    const existing=r.sounds.filter(s=>s.speaker_source!==context.key&&(context.type!=='sounds'||s.id!==item.id));
    const tracks=[];
    for(const [i,track]of result.tracks.entries()){
      const name=names[i]?.trim()||track.name,lane=name+' · '+context.id.slice(0,6);
      const regions=layout==='turns'?(result.regions||[]).filter(region=>region.speaker===track.name):[{start:0,end:length}];
      for(const region of regions){const start=Math.max(0,region.start),end=Math.min(length,region.end);if(end-start<.04)continue;
        tracks.push({id:makeId(),asset_id:track.asset.id,time:cursor+start,duration:end-start,source_start:start,gain_db:mix?.gain_db||0,audio_ordinal:0,role:track.name.startsWith('Speaker')?'dialogue':'other',name,lane,speaker_source:context.key,fade_in:start===0?(context.type==='sounds'?item.fade_in:item.audio_fade_in)||0:0,fade_out:end===length?(context.type==='sounds'?item.fade_out:item.audio_fade_out)||0:0});
      }
    }
    if(!tracks.length)throw new Error('No speaker regions are available. Choose aligned tracks or scan again.');
    if(existing.length+tracks.length>100)throw new Error('This edit would exceed 100 audio pieces. Choose aligned tracks instead.');
    r.sounds=[...existing,...tracks];
    if(context.type==='clips'&&mix)mix.enabled=false;
    return {recipe:r,index:existing.length,time:cursor,count:tracks.length};
  }
  return {target,apply};
});
