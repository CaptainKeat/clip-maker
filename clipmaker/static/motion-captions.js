(function (scope) {
  "use strict";
  const clamp = (n, low, high) => Math.max(low, Math.min(high, n));
  const defaults = () => ({enabled:true,x:50,y:30,size:1,rotation:0,color:"#ffffff",effect:"none",font:"Arial",bold:true,keyframes:[]});
  function position(m, time) {
    const keys = m.keyframes || [];
    if (!keys.length) return {x:m.x,y:m.y};
    if (time <= keys[0].time) return {x:keys[0].x,y:keys[0].y};
    for (let i=1;i<keys.length;i++) {
      const a=keys[i-1], b=keys[i];
      if (time <= b.time) {const r=(time-a.time)/(b.time-a.time);return {x:a.x+(b.x-a.x)*r,y:a.y+(b.y-a.y)*r};}
    }
    return {x:keys.at(-1).x,y:keys.at(-1).y};
  }
  function pose(m, time, start, end) {
    const p=position(m,time), age=Math.max(0,time-start), duration=Math.max(.04,end-start);
    let size=m.size;
    if(m.effect==="pop")size*=.72+.28*Math.min(1,age/.12);
    if(m.effect==="shake"){p.x+=.65*Math.sin(age*52);p.y+=.35*Math.sin(age*71);}
    if(m.effect==="grow")size*=1+.5*Math.min(1,age/duration);
    if(m.effect==="fall")p.y+=35*Math.pow(Math.min(1,age/duration),2);
    return {...p,size,rotation:m.rotation};
  }
  // Geometry matches the FFmpeg fit/fill framing, in output-frame pixels.
  function geometry(vw, vh, crop, width=1080, height=1920) {
    const zoom=Number(crop.zoom||1), fill=crop.mode==="fill";
    const scale=(fill?Math.max(width/vw,height/vh):Math.min(width/vw,height/vh))*zoom;
    const w=vw*scale,h=vh*scale;
    return {w,h,x:fill?-(w-width)*(Number(crop.x||0)+100)/200:(width-w)/2+Number(crop.x||0)*4*width/1080,
      y:fill?-(h-height)*(Number(crop.y||0)+100)/200:(height-h)/2+Number(crop.y||0)*7*height/1920};
  }
  function grayscale(image) {
    const gray=new Float32Array(image.width*image.height);
    for(let i=0;i<gray.length;i++)gray[i]=.299*image.data[i*4]+.587*image.data[i*4+1]+.114*image.data[i*4+2];
    return gray;
  }
  function template(frame, width, box) {
    const values=[],coords=[];
    for(let y=0;y<box.h;y+=2)for(let x=0;x<box.w;x+=2){coords.push([x,y]);values.push(frame[(box.y+y)*width+box.x+x]);}
    const mean=values.reduce((sum,v)=>sum+v,0)/values.length;
    const centered=values.map(v=>v-mean), energy=centered.reduce((sum,v)=>sum+v*v,0);
    if(!values.length||energy/values.length<25)throw new Error("Choose a detailed area around the character, rather than flat sky or a solid colour.");
    return {coords,centered,energy,box:{...box}};
  }
  function match(frame, width, height, model, previous, radius=24) {
    const {coords,centered,energy,box}=model;
    let best={score:-1,x:previous.x,y:previous.y};
    const lowX=Math.max(0,previous.x-radius),highX=Math.min(width-box.w,previous.x+radius);
    const lowY=Math.max(0,previous.y-radius),highY=Math.min(height-box.h,previous.y+radius);
    for(let y=lowY;y<=highY;y++)for(let x=lowX;x<=highX;x++){
      let sum=0,squared=0,dot=0;
      for(let i=0;i<coords.length;i++){const [dx,dy]=coords[i],v=frame[(y+dy)*width+x+dx];sum+=v;squared+=v*v;dot+=v*centered[i];}
      const variance=squared-sum*sum/coords.length;
      const score=variance>1?dot/Math.sqrt(variance*energy):-1;
      if(score>best.score)best={score,x,y};
    }
    return best;
  }
  function create({root,getData,getSelected,snapshot,status,refresh,isBusy}) {
    const $=s=>root.querySelector(s), stage=$(".editor-video-wrap"), video=$("#editor-video");
    const panel=$("#editor-motion-panel"), layer=$("#editor-motion-layer"), target=$("#editor-motion-target");
    let picking=false,drag=null,tracking=false,cancelled=false,selectionBox=null,lastData=null,lastCue=null,lastCrop="";
    const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    function selectedIndices(){return [...getSelected()].filter(k=>k.startsWith("caption:")).map(k=>Number(k.split(":")[1])).filter(i=>getData()?.captions[i]);}
    function selected(){return getData()?.captions[selectedIndices()[0]]||null;}
    function motion(c){return {...defaults(),...(c?.motion||{})};}
    function time(){return Number(video.currentTime)||0;}
    function message(text){$("#editor-motion-hint").textContent=text;status(text);}
    function sync() {
      const data=getData();if(!data)return;
      if(data!==lastData){selectionBox=null;target.hidden=true;lastData=data;}
      const c=selected(), m=motion(c);
      if(c!==lastCue){selectionBox=null;target.hidden=true;lastCue=c;}
      $("#editor-motion-cue").textContent=c?c.text:"Select a subtitle on the timeline or in the subtitle window.";
      panel.querySelectorAll("[data-motion-control]").forEach(node=>node.disabled=!c||tracking||isBusy());
      $("#editor-motion-enabled").checked=!!c?.motion?.enabled;
      $("#editor-motion-speaker").value=c?.speaker||"";
      for(const field of ["color","size","rotation","effect"])$("#editor-motion-"+field).value=m[field];
      const p=position(m,time());$("#editor-motion-x").value=p.x.toFixed(1);$("#editor-motion-y").value=p.y.toFixed(1);
      $("#editor-motion-size-value").textContent=Number(m.size).toFixed(2)+"×";
      $("#editor-motion-keys").innerHTML=(m.keyframes||[]).map((k,i)=>'<span><button type="button" class="secondary" data-key-jump="'+i+'">'+Number(k.time).toFixed(2)+'s</button><button type="button" class="secondary" data-key-remove="'+i+'" aria-label="Remove position at '+Number(k.time).toFixed(2)+' seconds">×</button></span>').join("");
      $("#editor-motion-join").disabled=tracking||isBusy()||selectedIndices().length<2;
      $("#editor-motion-cancel").hidden=!tracking;
      $("#editor-motion-track").disabled=!c||!selectionBox||tracking||isBusy();
    }
    function framedVideo() {
      if(!video.videoWidth||!stage.clientWidth)return;
      const cropKey=JSON.stringify(getData()?.crop||{});
      if(cropKey!==lastCrop){selectionBox=null;target.hidden=true;lastCrop=cropKey;}
      const g=geometry(video.videoWidth,video.videoHeight,getData()?.crop||{},stage.clientWidth,stage.clientHeight);
      Object.assign(video.style,{position:"absolute",width:g.w+"px",height:g.h+"px",left:g.x+"px",top:g.y+"px",objectFit:"fill",transform:"none"});
    }
    function draw() {
      const data=getData();if(!data)return;
      framedVideo();
      const t=time(), crop=data.crop||{}, indices=selectedIndices();
      layer.innerHTML=data.captions.map((c,i)=>({c,i})).filter(({c})=>c.motion?.enabled&&t>=Number(c.start)&&t<Number(c.end)&&(data.segments||[]).some(s=>t>=s.source_start&&t<=s.source_end)).map(({c,i})=>{
        const m=motion(c),p=pose(m,t,Number(c.start),Number(c.end));
        const font=64*p.size*Number(crop.caption_scale||1)*stage.clientWidth/1080;
        return '<button type="button" class="motion-caption'+(indices.includes(i)?' is-selected':'')+'" data-motion-index="'+i+'" aria-label="Move caption: '+esc(c.text)+'" style="left:'+p.x+'%;top:'+p.y+'%;font-size:'+font+'px;color:'+m.color+';transform:translate(-50%,-50%) rotate('+p.rotation+'deg)">'+esc(c.text)+'</button>';
      }).join("");
    }
    function upsert(c,t,x,y) {
      c.motion=motion(c);c.motion.enabled=true;
      const keys=c.motion.keyframes||[];
      const at=clamp(t,Number(c.start),Number(c.end));
      const i=keys.findIndex(k=>Math.abs(k.time-at)<.025),key={time:Number(at.toFixed(3)),x:clamp(x,0,100),y:clamp(y,0,100)};
      if(i>=0)keys[i]=key;else keys.push(key);keys.sort((a,b)=>a.time-b.time);c.motion.keyframes=keys;
    }
    function commit(field,value) {
      const c=selected();if(!c||tracking||isBusy())return;snapshot();c.motion=motion(c);
      if(field==="enabled")c.motion.enabled=value;
      else{c.motion.enabled=true;if(field==="x"||field==="y"){
        const p=position(c.motion,time());p[field]=Number(value);
        if(c.motion.keyframes.length)upsert(c,time(),p.x,p.y);else{c.motion.x=p.x;c.motion.y=p.y;}
      }else c.motion[field]=value;}
      refresh();sync();draw();
    }
    function point(e){const r=stage.getBoundingClientRect();return {x:clamp((e.clientX-r.left)/r.width*100,0,100),y:clamp((e.clientY-r.top)/r.height*100,0,100)};}
    function cancel(){cancelled=true;picking=false;stage.classList.remove("motion-picking");drag=null;}
    panel.querySelectorAll("[data-motion-field]").forEach(node=>node.addEventListener("change",()=>{
      const field=node.dataset.motionField;commit(field,node.type==="checkbox"?node.checked:["size","rotation","x","y"].includes(field)?Number(node.value):node.value);
    }));
    $("#editor-motion-position").addEventListener("click",()=>{if(!selected())return;video.pause();picking=true;stage.classList.add("motion-picking");message("Click above the character to place your caption.");});
    $("#editor-motion-speaker").addEventListener("change",e=>{const c=selected();if(!c)return;snapshot();c.speaker=e.target.value.trim().slice(0,60);refresh();sync();});
    $("#editor-motion-speaker-style").addEventListener("click",()=>{
      const c=selected();if(!c)return;if(!c.speaker){message("Give this speaker a name before applying their look.");return;}
      snapshot();const m=motion(c),p=position(m,time());let count=0;
      for(const other of getData().captions){if(other.speaker!==c.speaker)continue;const own=other.motion;
        other.motion={...m,x:p.x,y:p.y,keyframes:own?.keyframes||[]};count++;}
      refresh();sync();draw();message("Applied colour, size, effect and placement to "+count+" captions for "+c.speaker+". Existing paths are preserved.");
    });
    $("#editor-motion-pick").addEventListener("click",()=>{const c=selected();if(!c)return;video.pause();video.currentTime=Number(c.start);picking="box";selectionBox=null;target.hidden=true;stage.classList.add("motion-picking");message("At the caption start, drag a small box around a visible head or another distinctive detail.");});
    $("#editor-motion-key").addEventListener("click",()=>{const c=selected();if(!c)return;snapshot();const p=position(motion(c),time());upsert(c,time(),p.x,p.y);sync();draw();message("Position saved. Scrub forward and drag the caption to add its next position.");});
    $("#editor-motion-clear").addEventListener("click",()=>{const c=selected();if(!c)return;snapshot();const p=position(motion(c),time());c.motion={...motion(c),x:p.x,y:p.y,keyframes:[]};sync();draw();message("Motion path cleared; caption stays at this position.");});
    $("#editor-motion-cancel").addEventListener("click",cancel);
    $("#editor-motion-keys").addEventListener("click",e=>{const c=selected();if(!c||tracking)return;const jump=e.target.dataset.keyJump,remove=e.target.dataset.keyRemove;if(jump!==undefined){video.currentTime=c.motion.keyframes[Number(jump)].time;draw();}if(remove!==undefined){snapshot();c.motion.keyframes.splice(Number(remove),1);sync();draw();}});
    $("#editor-motion-join").addEventListener("click",()=>{
      const data=getData(),indices=selectedIndices().sort((a,b)=>data.captions[a].start-data.captions[b].start),cues=indices.map(i=>data.captions[i]);
      if(cues.length<2)return;
      if(cues.some(c=>String(c.speaker||"")!==String(cues[0].speaker||"")||c.track_ordinal!==cues[0].track_ordinal)){message("Select words from the same speaker before joining them.");return;}
      snapshot();const combined={...cues[0],text:cues.map(c=>c.text).join(" "),start:Math.min(...cues.map(c=>c.start)),end:Math.max(...cues.map(c=>c.end))};
      for(const i of [...indices].sort((a,b)=>b-a))data.captions.splice(i,1);
      data.captions.push(combined);data.captions.sort((a,b)=>a.start-b.start);getSelected().clear();getSelected().add("caption:"+data.captions.indexOf(combined));
      video.currentTime=combined.start;refresh();sync();draw();message("Words joined into one caption. Choose an effect or place it above a character.");
    });
    $("#editor-motion-phrase").addEventListener("click",()=>{
      const data=getData(),c=selected();if(!c)return;
      const indices=[data.captions.indexOf(c)];let previous=c;
      for(let i=indices[0]+1;i<data.captions.length&&indices.length<4;i++){
        const next=data.captions[i];
        if(next.speaker!==c.speaker||next.track_ordinal!==c.track_ordinal||next.start-previous.end>.32||next.end-c.start>2.2||/[.!?]$/.test(previous.text))break;
        indices.push(i);previous=next;
      }
      if(indices.length<2){message("This is already a phrase or has no nearby words. Shift-select words to join them manually.");return;}
      getSelected().clear();indices.forEach(i=>getSelected().add("caption:"+i));sync();$("#editor-motion-join").click();
    });
    stage.addEventListener("pointerdown",e=>{
      if(tracking||isBusy()||e.button!==0)return;
      const node=e.target.closest("[data-motion-index]");if(!picking&&!node)return;
      e.preventDefault();video.pause();
      if(node){getSelected().clear();getSelected().add("caption:"+node.dataset.motionIndex);sync();}
      const c=selected();if(!c)return;
      const p=point(e);
      if(picking==="box"){drag={box:true,start:p};target.hidden=false;target.style.left=p.x+"%";target.style.top=p.y+"%";target.style.width="0";target.style.height="0";}
      else{
        snapshot();c.motion=motion(c);c.motion.enabled=true;
        const at=position(c.motion,time());drag={caption:c,start:p,original:at,placed:!!picking};
        if(picking){c.motion.x=p.x;c.motion.y=p.y;if(c.motion.keyframes.length)upsert(c,time(),p.x,p.y);drag.original=p;}
      }
      stage.setPointerCapture(e.pointerId);
    });
    stage.addEventListener("pointermove",e=>{
      if(!drag)return;const p=point(e);
      if(drag.box){selectionBox={x:Math.min(drag.start.x,p.x),y:Math.min(drag.start.y,p.y),w:Math.abs(p.x-drag.start.x),h:Math.abs(p.y-drag.start.y)};Object.assign(target.style,{left:selectionBox.x+"%",top:selectionBox.y+"%",width:selectionBox.w+"%",height:selectionBox.h+"%"});}
      else{const c=drag.caption,x=clamp(drag.original.x+p.x-drag.start.x,0,100),y=clamp(drag.original.y+p.y-drag.start.y,0,100);if(c.motion.keyframes.length)upsert(c,time(),x,y);else{c.motion.x=x;c.motion.y=y;}draw();}
    });
    stage.addEventListener("pointerup",()=>{
      if(!drag)return;
      if(drag.box){if(!selectionBox||selectionBox.w<2||selectionBox.h<2){selectionBox=null;target.hidden=true;message("Draw a slightly larger tracking box.");}else message("Target selected. Place the caption above it, then choose Track caption.");}
      drag=null;picking=false;stage.classList.remove("motion-picking");sync();draw();
    });
    stage.addEventListener("pointercancel",()=>{drag=null;cancel();sync();});
    async function seek(v,t) {
      if(Math.abs(v.currentTime-t)<.001&&v.readyState>=2)return;
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{cleanup();reject(new Error("The browser could not seek this source. Add position keyframes manually."));},10000);
        function cleanup(){clearTimeout(timer);v.removeEventListener("seeked",done);v.removeEventListener("error",failed);}
        function done(){cleanup();resolve();}function failed(){cleanup();reject(new Error("This source cannot be decoded in the browser. Use manual position keyframes."));}
        v.addEventListener("seeked",done,{once:true});v.addEventListener("error",failed,{once:true});v.currentTime=t;
      });
    }
    async function track() {
      const c=selected(),data=getData();if(!c||!selectionBox||tracking||isBusy())return;
      if(!video.videoWidth||video.readyState<2){message("Wait for the source video to load before tracking.");return;}
      const start=Number(c.start),end=Number(c.end);
      if(end-start>30){message("Track up to 30 seconds per caption. Split a longer caption into shorter phrases.");return;}
      const boxSelection={...selectionBox},original=motion(c),g=geometry(video.videoWidth,video.videoHeight,data.crop||{},270,480);
      const box={x:Math.round(boxSelection.x*2.7),y:Math.round(boxSelection.y*4.8),w:clamp(Math.round(boxSelection.w*2.7),8,64),h:clamp(Math.round(boxSelection.h*4.8),8,64)};
      if(box.x<Math.max(0,g.x)||box.y<Math.max(0,g.y)||box.x+box.w>Math.min(270,g.x+g.w)||box.y+box.h>Math.min(480,g.y+g.h)){message("Keep the whole tracking box inside the gameplay picture.");return;}
      const worker=document.createElement("video");worker.preload="auto";worker.muted=true;worker.src=video.currentSrc||video.src;
      const canvas=document.createElement("canvas");canvas.width=270;canvas.height=480;const ctx=canvas.getContext("2d",{willReadFrequently:true});
      let keys=[],stopped=false;
      cancelled=false;tracking=true;video.pause();root.classList.add("motion-tracking-busy");sync();
      function frame(){ctx.fillStyle="#000";ctx.fillRect(0,0,270,480);ctx.drawImage(worker,g.x,g.y,g.w,g.h);return grayscale(ctx.getImageData(0,0,270,480));}
      try {
        await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error("Source did not load for tracking.")),10000);worker.onloadeddata=()=>{clearTimeout(timer);resolve();};worker.onerror=()=>{clearTimeout(timer);reject(new Error("Browser cannot decode this source. Use manual keyframes."));};worker.load();});
        await seek(worker,start);if(cancelled)return;
        const model=template(frame(),270,box),anchor=position(original,start),offset={x:anchor.x-(box.x+box.w/2)/2.7,y:anchor.y-(box.y+box.h/2)/4.8};
        let previous={x:box.x,y:box.y};keys.push({time:start,x:anchor.x,y:anchor.y});
        for(let t=start+.125;t<end+.001;t+=.125){
          if(cancelled)break;
          if(!data.segments.some(s=>t>=s.source_start&&t<=s.source_end)){stopped=true;break;}
          await seek(worker,Math.min(t,end));if(cancelled)break;
          const best=match(frame(),270,480,model,previous);
          if(best.score<.68){stopped=true;break;}
          previous={x:best.x,y:best.y};keys.push({time:Number(t.toFixed(3)),x:clamp((best.x+box.w/2)/2.7+offset.x,0,100),y:clamp((best.y+box.h/2)/4.8+offset.y,0,100)});
          $("#editor-motion-hint").textContent="Tracking locally · "+Math.round((t-start)/(end-start)*100)+"%";
        }
        if(cancelled){message("Tracking cancelled. Your previous caption path is preserved.");return;}
        if(keys.length<2){message("Could not follow that detail. Try a clearer target or add manual position keyframes.");return;}
        snapshot();c.motion={...original,enabled:true,keyframes:keys};
        refresh();video.currentTime=start;
        message(stopped?"Tracking lost the target at "+keys.at(-1).time.toFixed(2)+"s. The caption holds its last position; correct the remaining path manually.":"Tracked "+keys.length+" positions. Play the caption to review, then Save draft.");
      }catch(error){message(error.message);}
      finally{worker.pause();worker.removeAttribute("src");worker.load();tracking=false;root.classList.remove("motion-tracking-busy");sync();draw();}
    }
    $("#editor-motion-track").addEventListener("click",track);
    video.addEventListener("loadedmetadata",()=>{framedVideo();draw();});
    video.addEventListener("seeked",()=>{draw();});
    new ResizeObserver(()=>{framedVideo();draw();}).observe(stage);
    function loop(){if(!root.hidden)draw();requestAnimationFrame(loop);}requestAnimationFrame(loop);
    return {sync,draw,cancel,isTracking:()=>tracking};
  }
  const api={defaults,position,pose,geometry,grayscale,template,match,create};
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  else scope.MotionCaptions=api;
})(typeof window!=="undefined"?window:globalThis);
