(function(root){
  'use strict';
  const clone=x=>JSON.parse(JSON.stringify(x)),id=()=>globalThis.crypto.randomUUID();
  const duration=c=>(c.end-c.start)/(c.speed||1)+(c.freeze||0);
  const total=r=>r.clips.reduce((n,c)=>n+duration(c),0);
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  function position(m,t){const k=m?.keyframes||[];if(!k.length)return {x:m?.x??50,y:m?.y??85};if(t<=k[0].time)return k[0];for(let i=1;i<k.length;i++){if(t<=k[i].time){const a=k[i-1],b=k[i],f=(t-a.time)/(b.time-a.time);return {x:a.x+(b.x-a.x)*f,y:a.y+(b.y-a.y)*f};}}return k.at(-1);}
  function motion(m,map){if(!m)return m;const out=clone(m),keys=new Map();for(const k of out.keyframes||[])keys.set(map(k.time),{...k,time:map(k.time)});out.keyframes=[...keys.values()].sort((a,b)=>a.time-b.time);return out;}
  function sliceClip(c,left,right,newId=true){
    const out=clone(c),played=(c.end-c.start)/c.speed;out.start=c.start+Math.min(left,played)*c.speed;out.end=c.start+Math.min(right,played)*c.speed;out.freeze=Math.max(0,right-Math.max(left,played));
    if(out.end-out.start<.04){out.start=Math.max(c.start,c.end-.04);out.end=c.end;out.freeze=Math.max(0,right-left-(out.end-out.start)/c.speed);}
    if(newId)out.id=id();return out;
  }
  function cutTimed(r,a,b){
    const delta=b-a,map=t=>t<=a?t:t>=b?t-delta:a;
    r.captions=r.captions.filter(c=>!(c.start>=a&&c.end<=b)).map(c=>({...c,start:map(c.start),end:map(c.end),motion:motion(c.motion,map)})).filter(c=>c.end-c.start>.001);
    for(const type of ['sounds','overlays']){
      r[type]=r[type].flatMap(c=>{const start=c.time,end=start+c.duration,segments=[];
        if(start<a)segments.push([start,Math.min(a,end)]);if(end>b)segments.push([Math.max(start,b),end]);
        return segments.filter(([x,y])=>y-x>.001).map(([x,y],i)=>({...c,id:i?id():c.id,time:map(x),duration:y-x,source_start:(c.source_start||0)+x-start,fade_in:x===start?(c.fade_in||0):0,fade_out:y===end?(c.fade_out||0):0}));
      });
    }
    r.markers=r.markers.filter(m=>m.time<a||m.time>=b).map(m=>({...m,time:map(m.time)}));
  }
  function insertTimed(r,at,length){
    for(const type of ['captions','sounds','overlays'])r[type]=r[type].flatMap(c=>{
      const start=c.start??c.time,end=c.end??c.time+c.duration;
      if(start>=at)return [type==='captions'?{...c,start:start+length,end:end+length,motion:motion(c.motion,t=>t+length)}:{...c,time:start+length}];
      if(end<=at)return [c];
      if(type==='captions')return [{...c,end:at},{...clone(c),id:id(),start:at+length,end:end+length,motion:motion(c.motion,t=>t+length)}];
      return [{...c,duration:at-start,fade_out:0},{...clone(c),id:id(),time:at+length,duration:end-at,source_start:(c.source_start||0)+at-start,fade_in:0}];
    });
    r.markers=r.markers.map(m=>({...m,time:m.time>=at?m.time+length:m.time}));
  }
  function cutRange(recipe,a,b,ripple=true){
    const r=clone(recipe);a=clamp(a,0,total(r));b=clamp(b,a,total(r));if(b-a<1/30)throw new Error('Mark a range at least one frame long.');
    let cursor=0;const clips=[];for(const c of r.clips){const end=cursor+duration(c);if(end<=a||cursor>=b)clips.push(c);else{if(cursor<a)clips.push(sliceClip(c,0,a-cursor));if(end>b)clips.push(sliceClip(c,b-cursor,end-cursor));}cursor=end;}
    r.clips=clips;if(ripple)cutTimed(r,a,b);return r;
  }
  function trim(recipe,index,start,end,speed,freeze,ripple=true){
    let r=clone(recipe),c=r.clips[index],cursor=r.clips.slice(0,index).reduce((n,v)=>n+duration(v),0),before=duration(c);
    const oldStart=c.start,oldEnd=c.end,oldSpeed=c.speed;
    if(ripple&&start>oldStart)cutTimed(r,cursor,cursor+(start-oldStart)/oldSpeed);
    else if(ripple&&start<oldStart)insertTimed(r,cursor,(oldStart-start)/oldSpeed);
    const afterStart=(oldEnd-start)/oldSpeed+(c.freeze||0);
    if(ripple&&end<oldEnd)cutTimed(r,cursor+(end-start)/oldSpeed,cursor+(oldEnd-start)/oldSpeed);
    else if(ripple&&end>oldEnd)insertTimed(r,cursor+afterStart,(end-oldEnd)/oldSpeed);
    c=r.clips[index];c.start=start;c.end=end;
    const oldLength=duration(c),newLength=(end-start)/speed+freeze;
    if(ripple&&Math.abs(newLength-oldLength)>.0001){
      const map=t=>t<=cursor?t:t>=cursor+oldLength?t+newLength-oldLength:cursor+(t-cursor)*newLength/oldLength;
      r.captions=r.captions.map(v=>({...v,start:map(v.start),end:map(v.end),motion:motion(v.motion,map)}));
      for(const type of ['sounds','overlays'])r[type]=r[type].map(v=>({...v,time:map(v.time),duration:map(v.time+v.duration)-map(v.time)}));r.markers=r.markers.map(v=>({...v,time:map(v.time)}));
    }
    c.speed=speed;c.freeze=freeze;return r;
  }
  function copySelection(recipe,type,index,linked=true){
    const item=recipe[type]?.[index];if(!item)throw new Error('Select an item to copy.');const out={type,item:clone(item),layers:{}};
    if(type==='clips'&&linked){const start=recipe.clips.slice(0,index).reduce((n,c)=>n+duration(c),0),end=start+duration(item);
      for(const kind of ['captions','sounds','overlays'])out.layers[kind]=recipe[kind].filter(c=>(c.start??c.time)<end&&(c.end??c.time+c.duration)>start).map(c=>{
        const a=Math.max(start,c.start??c.time),b=Math.min(end,c.end??c.time+c.duration),v=clone(c);
        if(kind==='captions'){v.start=a-start;v.end=b-start;v.motion=motion(v.motion,t=>Math.max(0,t-start));if(v.motion?.keyframes?.length){const p=position(c.motion,a);v.motion.keyframes.unshift({time:v.start,x:p.x,y:p.y});}}
        else{v.time=a-start;v.duration=b-a;v.source_start=(c.source_start||0)+a-(c.time||0);}return v;
      });
    }return out;
  }
  function paste(recipe,clipboard,at,ripple=true){
    if(!clipboard)throw new Error('Copy an item in Clip Maker first.');const r=clone(recipe);at=clamp(at,0,total(r));
    const c=clone(clipboard.item);c.id=id();
    if(clipboard.type==='clips'){
      let cursor=0,index=r.clips.length;
      for(let i=0;i<r.clips.length;i++){const end=cursor+duration(r.clips[i]);if(at<end-.0001){index=i;if(at>cursor+.0001){const original=r.clips[i];r.clips.splice(i,1,sliceClip(original,0,at-cursor),sliceClip(original,at-cursor,duration(original)));index=i+1;}break;}cursor=end;}
      if(r.clips.length>=500)throw new Error('The project has reached its 500-piece limit.');
      if(ripple)insertTimed(r,at,duration(c));r.clips.splice(index,0,c);
      for(const [type,layers]of Object.entries(clipboard.layers||{}))for(const raw of layers){const v=clone(raw);v.id=id();if(type==='captions'){v.start+=at;v.end+=at;v.motion=motion(v.motion,t=>t+at);}else v.time+=at;r[type].push(v);}return {recipe:r,index};
    }
    if(at>=total(r)-.04)throw new Error('Move the playhead before the end to paste a layer.');
    if(clipboard.type==='captions'){const delta=at-c.start;c.end=Math.min(total(r),at+c.end-c.start);c.start=at;c.motion=motion(c.motion,t=>t+delta);}else{c.time=at;if(c.duration)c.duration=Math.min(c.duration,total(r)-at);}
    r[clipboard.type].push(c);return {recipe:r,index:r[clipboard.type].length-1};
  }
  function snap(time,points,zoom,enabled=true){if(!enabled)return {time,line:null};let best=null,distance=8/Math.max(.1,zoom);for(const p of points){const d=Math.abs(p-time);if(d<=distance){distance=d;best=p;}}return {time:best??time,line:best};}
  function envelope(item,age,length,prefix=''){const a=Math.min(item[prefix+'fade_in']||0,length/2),b=Math.min(item[prefix+'fade_out']||0,length/2);return clamp(Math.min(a?age/a:1,b?(length-age)/b:1),0,1);}
  function crossfade(recipe,index,seconds){const r=clone(recipe),current=r.sounds[index];if(!current)throw new Error('Select a soundtrack.');const next=r.sounds.filter(s=>s!==current&&s.time>=current.time).sort((a,b)=>a.time-b.time)[0];if(!next)throw new Error('Add a following soundtrack first.');const f=clamp(seconds,.033,Math.min(current.duration/2,next.duration/2));current.fade_out=f;next.fade_in=f;next.time=current.time+current.duration-f;return r;}
  function style(c,preset){const m=clone(c.motion||{enabled:true,x:50,y:85,keyframes:[]});for(const k of ['color','font','bold','size','rotation','effect'])m[k]=preset.motion[k];c.motion=m;c.speaker=preset.speaker||c.speaker||'';return c;}
  const track=(r,key)=>r.editing?.tracks?.[key]||{};
  const soundKey=s=>'sound:'+(s.lane||'Audio');
  function audible(r,key){const tracks=r.editing?.tracks||{},active=new Set(['audio',...r.sounds.map(soundKey)]),solo=Object.entries(tracks).some(([k,v])=>active.has(k)&&v.solo);return !track(r,key).muted&&(!solo||!!track(r,key).solo);}
  function assertUnlocked(before,after){
    const different=(a,b)=>JSON.stringify(a)!==JSON.stringify(b);
    for(const [key,state]of Object.entries(before.editing?.tracks||{})){
      if(!state.locked)continue;
      const items=r=>key==='video'?r.clips:key==='audio'?r.clips.map(c=>[c.id,c.start,c.end,c.speed,c.freeze||0,c.audio,c.audio_fade_in||0,c.audio_fade_out||0]):key.startsWith('sound:')?r.sounds.filter(c=>soundKey(c)===key):r[key]||[];
      if(different(items(before),items(after))){const error=new Error('Unlock '+(key.startsWith('sound:')?key.slice(6):key)+' before editing it. Ripple edits cannot move a locked track.');error.name='TrackLockedError';throw error;}
    }
  }
  const api={clone,duration,total,cutRange,trim,copySelection,paste,snap,envelope,crossfade,style,position,track,soundKey,audible,assertUnlocked};
  if(typeof module!=='undefined')module.exports=api;root.StudioEditing=api;
})(typeof window!=='undefined'?window:globalThis);
