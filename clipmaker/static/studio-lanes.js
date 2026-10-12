(function(root,factory){const api=factory(root.StudioEditing||(typeof require==='function'?require('./studio-editing.js'):null));if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioLanes=api;})(typeof window==='undefined'?globalThis:window,E=>{
  'use strict';
  const kinds={video:'overlays',audio:'sounds',caption:'captions'},prefix={video:'video:',audio:'sound:',caption:'caption:'};
  const clone=E.clone,uid=()=>globalThis.crypto.randomUUID();
  function rows(r,kind){
    const saved=(r.editing?.lanes||[]).filter(t=>t.kind===kind),ids=new Set(saved.map(t=>t.id));
    const used=[...new Set((r[kinds[kind]]||[]).map(c=>c.lane||(kind==='video'?'Overlays':kind==='audio'?'Audio':'')))].filter(Boolean);
    return [...used.filter(id=>!ids.has(id)).map(id=>({id,name:id,kind})),...saved];
  }
  function key(kind,item){return prefix[kind]+(item.lane||(kind==='video'?'Overlays':kind==='audio'?'Audio':''));}
  function add(r,kind){if(!kinds[kind])throw new Error('Choose a video, audio or caption track.');if((r.editing?.lanes||[]).length>=32)throw new Error('This project already has 32 additional tracks.');const out=clone(r),n=rows(r,kind).length+2,track={id:uid(),name:(kind==='video'?'Video ':kind==='audio'?'Audio ':'Captions ')+n,kind};out.editing={...out.editing,lanes:[...(out.editing?.lanes||[]),track]};return {recipe:out,track};}
  function selected(r,selection,type,index){
    if(selection?.type===type&&Number(selection.index)===index)return true;
    if(selection?.single)return false;
    const current=r[selection?.type]?.[selection?.index],item=r[type]?.[index];return !!current?.link_id&&item?.link_id===current.link_id;
  }
  function members(r,selection){const item=r[selection?.type]?.[selection?.index];if(!item)return [];const result=[];for(const type of ['overlays','sounds','captions'])r[type].forEach((c,index)=>{if(type===selection.type&&index===selection.index||!selection.single&&item.link_id&&c.link_id===item.link_id)result.push({type,index,item:c});});return result;}
  function update(r,selection,field,value){const out=clone(r),all=members(out,selection);if(!all.length)throw new Error('Select a timed clip.');if(!Number.isFinite(value))throw new Error('Enter a finite clip value.');const current=out[selection.type][selection.index],delta=value-(current[field]||0);for(const member of all){const c=member.item;if(c===current)c[field]=value;else if(['time','source_start','duration'].includes(field))c[field]=(c[field]||0)+delta;}E.assertUnlocked(r,out);return out;}
  function remove(r,selection){const out=clone(r),all=members(r,selection);for(const type of ['overlays','sounds','captions']){const ids=new Set(all.filter(m=>m.type===type).map(m=>m.item.id));out[type]=out[type].filter(c=>!ids.has(c.id));}E.assertUnlocked(r,out);return out;}
  function unlink(r,selection){const out=clone(r);for(const m of members(out,{...selection,single:false}))delete m.item.link_id;E.assertUnlocked(r,out);return out;}
  function split(r,selection,time){const out=clone(r),all=members(out,selection).sort((a,b)=>b.index-a.index),group=uid();for(const {type,index,item:c}of all){const start=c.time??c.start,end=c.end??start+c.duration;if(time<=start+.04||time>=end-.04)throw new Error('Move the playhead inside every linked clip.');const right=clone(c);right.id=uid();if(c.link_id)right.link_id=group;if(type==='captions'){c.end=time;right.start=time;}else{c.duration=time-start;c.fade_out=0;right.time=time;right.duration=end-time;right.source_start=(c.source_start||0)+time-start;right.fade_in=0;}out[type].splice(index,1,c,right);}if(out.sounds.length>100||out.overlays.length>100)throw new Error('This edit has reached its 100-layer limit.');E.assertUnlocked(r,out);return out;}
  function copy(r,selection){const item=r[selection.type][selection.index];return {type:selection.type,item:clone(item),layers:{},partners:members(r,selection).filter(m=>m.item!==item).map(m=>({type:m.type,item:clone(m.item)}))};}
  function paste(r,clipboard,time,ripple){const result=E.paste(r,clipboard,time,ripple);if(!clipboard.partners?.length)delete result.recipe[clipboard.type][result.index].link_id;else{const group=uid(),current=result.recipe[clipboard.type][result.index];current.link_id=group;for(const p of clipboard.partners){const c=clone(p.item);c.id=uid();c.link_id=group;c.time=current.time+(p.item.time-clipboard.item.time);c.duration=Math.min(c.duration,E.total(result.recipe)-c.time);result.recipe[p.type].push(c);}}if(result.recipe.sounds.length>100||result.recipe.overlays.length>100)throw new Error('This edit has reached its 100-layer limit.');E.assertUnlocked(r,result.recipe);return result;}
  function order(r,item){return rows(r,'video').findIndex(t=>t.id===(item.lane||'Overlays'));}
  return {rows,key,add,selected,members,update,remove,unlink,split,copy,paste,order};
});
