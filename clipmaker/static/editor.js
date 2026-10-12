(() => {
  const root=document.querySelector("#clip-editor"); if(!root)return;
  const $=(s)=>root.querySelector(s);
  const st={data:null,history:[],future:[],busy:false,zoom:1,pps:4,drag:null,scrubbing:false,cutMark:null,selected:new Set(),marquee:null,subtitleDrag:null,tool:"pointer",recorder:null,recordChunks:[]};
  let motionUI=null;
  const copy=(v)=>JSON.parse(JSON.stringify(v));
  const esc=(v)=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt=(v)=>{v=Math.max(0,Number(v)||0);const h=Math.floor(v/3600),m=Math.floor(v%3600/60),s=Math.floor(v%60);return(h?h+":":"")+String(m).padStart(2,"0")+":"+String(s).padStart(2,"0");};
  async function api(path,options={}){const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});const payload=await response.json();if(!response.ok)throw new Error(payload.error||response.statusText);return payload;}
  function status(message,error=false){$("#editor-status").textContent=message;$("#editor-status").classList.toggle("error",error);}
  function snapshot(){if(!st.data||st.busy)return;st.history.push(copy(st.data));if(st.history.length>30)st.history.shift();st.future=[];undoState();}
  function undoState(){$("#editor-undo").disabled=!st.history.length;$("#editor-redo").disabled=!st.future.length;}
  function clampBounds(){
    const d=st.data;
    d.source_start_sec=Math.max(d.edit_min_sec,Math.min(Number(d.source_start_sec),Number(d.source_end_sec)-3));
    d.source_end_sec=Math.min(d.edit_max_sec,Math.max(Number(d.source_end_sec),Number(d.source_start_sec)+3));
  }
  function px(time){return(Math.max(st.data.edit_min_sec,Math.min(st.data.edit_max_sec,Number(time)))-st.data.edit_min_sec)*st.pps;}
  function timelineWidth(){return Math.max(900,(st.data.edit_max_sec-st.data.edit_min_sec)*st.pps);}
  function cutIntersects(start,end){return(st.data.cuts||[]).some(c=>Number(start)<Number(c.end)&&Number(end)>Number(c.start));}
  function outputDuration(){if((st.data.segments||[]).length)return st.data.segments.reduce((sum,s)=>sum+(Number(s.source_end)-Number(s.source_start))/Number(s.speed||1)+Number(s.freeze||0),0);const removed=(st.data.cuts||[]).reduce((sum,c)=>sum+Math.max(0,Number(c.end)-Number(c.start)),0);return Math.max(0,st.data.source_end_sec-st.data.source_start_sec-removed);}
  function normalizeCuts(){
    const cuts=(st.data.cuts||[]).map(c=>({start:Math.max(st.data.source_start_sec,Number(c.start)),end:Math.min(st.data.source_end_sec,Number(c.end))})).filter(c=>c.end-c.start>=.1).sort((a,b)=>a.start-b.start);
    const merged=[];for(const cut of cuts){if(merged.length&&cut.start<=merged[merged.length-1].end+.02)merged[merged.length-1].end=Math.max(merged[merged.length-1].end,cut.end);else merged.push(cut);}st.data.cuts=merged;
  }
  function renderCuts(){
    normalizeCuts();$("#editor-cut-overlays").innerHTML=(st.data.cuts||[]).map((cut,index)=>'<button class="cut-block timeline-selectable'+(st.selected.has("cut:"+index)?" is-selected":"")+'" data-select-key="cut:'+index+'" data-index="'+index+'" style="left:'+px(cut.start)+'px;width:'+Math.max(5,px(cut.end)-px(cut.start))+'px" title="Removed '+(cut.end-cut.start).toFixed(1)+'s">CUT</button>').join("");
    $("#editor-clear-cuts").disabled=!(st.data.cuts||[]).length;
  }
  function updatePendingCut(){
    const preview=$("#editor-cut-preview");if(!st.data||st.cutMark===null){preview.hidden=true;return;}
    const current=Math.max(st.data.source_start_sec,Math.min(st.data.source_end_sec,Number($("#editor-video").currentTime)||st.cutMark)),start=Math.min(st.cutMark,current),end=Math.max(st.cutMark,current),duration=end-start;
    preview.style.left=px(start)+"px";preview.style.width=Math.max(3,px(end)-px(start))+"px";preview.classList.toggle("reverse",current<st.cutMark);preview.querySelector("span").textContent="PENDING CUT · "+duration.toFixed(1)+"s";preview.hidden=false;
    $("#editor-cut-status").textContent="Previewing "+fmt(start)+" → "+fmt(end)+" · "+duration.toFixed(1)+" seconds will be removed.";
  }  function readStageControls(){
    const crop=st.data.crop||(st.data.crop={});
    crop.mode=$("#editor-frame-mode").value;crop.zoom=Number($("#editor-frame-zoom").value);crop.x=Number($("#editor-frame-x").value);crop.y=Number($("#editor-frame-y").value);
    crop.caption_scale=Number($("#editor-caption-scale").value);crop.caption_y=Number($("#editor-caption-y").value);crop.fade_in=Number($("#editor-fade-in").value);crop.fade_out=Number($("#editor-fade-out").value);
    st.data.color={brightness:Number($("#editor-color-brightness").value),contrast:Number($("#editor-color-contrast").value),saturation:Number($("#editor-color-saturation").value),temperature:Number($("#editor-color-temperature").value)};
  }
  function updateStagePreview(){
    const crop=st.data.crop||{};const video=$("#editor-video");video.style.objectFit=crop.mode==="fill"?"cover":"contain";video.style.transform='translate('+(Number(crop.x||0)*.18)+'%,'+(Number(crop.y||0)*.18)+'%) scale('+Number(crop.zoom||1)+')';
    $("#editor-frame-zoom-value").textContent=Number(crop.zoom||1).toFixed(2)+"×";$("#editor-caption-scale-value").textContent=Math.round(Number(crop.caption_scale||1)*100)+"%";
    $("#editor-fade-in-value").textContent=Number(crop.fade_in||0).toFixed(2)+"s";$("#editor-fade-out-value").textContent=Number(crop.fade_out||0).toFixed(2)+"s";const color=st.data.color||{};video.style.filter="brightness("+(1+Number(color.brightness||0)*.5)+") contrast("+Number(color.contrast??1)+") saturate("+Number(color.saturation??1)+") sepia("+Math.abs(Number(color.temperature||0))*.12+")";["brightness","contrast","saturation","temperature"].forEach(key=>$("#editor-color-"+key+"-value").textContent=Number(color[key]??(key==="contrast"||key==="saturation"?1:0)).toFixed(2));
    updateCaptionPreview();if(motionUI)motionUI.draw();
  }
  function updateCaptionPreview(){
    if(!st.data)return;const overlay=$("#editor-caption-preview"),video=$("#editor-video"),time=Number.isFinite(video.currentTime)?video.currentTime:st.data.source_start_sec;
    const captions=(st.data.captions||[]).filter(c=>!c.motion?.enabled),active=captions.filter(c=>time>=Number(c.start)-.04&&time<=Number(c.end)+.08&&!cutIntersects(c.start,c.end));
    if(!active.length){overlay.hidden=true;overlay.innerHTML="";return;}
    const primary=active.sort((a,b)=>Number(b.start)-Number(a.start))[0];
    const sameVoice=captions.filter(c=>{
      const voice=primary.track_ordinal!==null&&primary.track_ordinal!==undefined?c.track_ordinal===primary.track_ordinal:(c.speaker||"")==(primary.speaker||"");
      return voice&&Number(c.end)>=Number(primary.start)-1.05&&Number(c.start)<=Number(primary.end)+1.05&&!cutIntersects(c.start,c.end);
    }).sort((a,b)=>Number(a.start)-Number(b.start));
    let words=[primary];if(!String(primary.text||"").trim().includes(" ")){const index=Math.max(0,sameVoice.indexOf(primary));let left=index,right=index;while(left>0&&right-left<4&&Number(sameVoice[left].start)-Number(sameVoice[left-1].end)<=.32&&!/[.!?]$/.test(String(sameVoice[left-1].text||"").trim()))left--;while(right<sameVoice.length-1&&right-left<4&&Number(sameVoice[right+1].start)-Number(sameVoice[right].end)<=.32&&!/[.!?]$/.test(String(sameVoice[right].text||"").trim()))right++;words=sameVoice.slice(left,right+1);}
    const preset=$("#editor-caption-preset").value||st.data.caption_preset||"punch",crop=st.data.crop||{},height=Math.max(80,Math.min(1600,({punch:500,clean:455,chaos:520}[preset]||500)+Number(crop.caption_y||0)))/1920*100;
    overlay.style.fontSize=((({punch:108,clean:82,chaos:116}[preset]||108)*Number(crop.caption_scale||1))*$(".editor-video-wrap").clientWidth/1080)+"px";overlay.className="editor-caption-preview preset-"+preset;overlay.style.bottom=height+"%";overlay.style.setProperty("--preview-caption-scale",Number(crop.caption_scale||1));overlay.innerHTML=words.map(word=>'<span class="'+(time>=Number(word.start)-.04&&time<=Number(word.end)+.08?"active":"")+'">'+esc(word.text)+'</span>').join(" ");overlay.hidden=false;
  }  function updateSelection(){
    root.querySelectorAll(".timeline-selectable").forEach(item=>item.classList.toggle("is-selected",st.selected.has(item.dataset.selectKey)));
    root.querySelectorAll(".editor-caption-row").forEach(row=>row.classList.toggle("is-selected",st.selected.has("caption:"+row.dataset.index)));
    const count=st.selected.size;$("#editor-clear-selection").disabled=!count;
    $("#editor-caption-summary").textContent=count?count+" timeline item"+(count===1?"":"s")+" selected. Shift-click or drag to add more.":"Drag across the timeline to select clips, audio, cuts, or captions.";updateSegmentInspector();if(motionUI)motionUI.sync();
  }
  function selectKey(key,add=false){if(!add)st.selected.clear();if(add&&st.selected.has(key))st.selected.delete(key);else st.selected.add(key);updateSelection();}
  function startMarquee(event,lane){if(event.button!==0)return;event.preventDefault();st.marquee={x:event.clientX,y:event.clientY,lane,add:event.shiftKey||event.ctrlKey||event.metaKey,base:new Set(st.selected),moved:false};}
  function moveMarquee(event){
    if(!st.marquee)return;const m=st.marquee,dx=event.clientX-m.x,dy=event.clientY-m.y;if(!m.moved&&Math.hypot(dx,dy)<5)return;m.moved=true;
    const timeline=$("#editor-timeline").getBoundingClientRect(),left=Math.min(m.x,event.clientX),right=Math.max(m.x,event.clientX),top=Math.min(m.y,event.clientY),bottom=Math.max(m.y,event.clientY),box=$("#editor-marquee");
    box.hidden=false;box.style.left=(left-timeline.left)+"px";box.style.top=(top-timeline.top)+"px";box.style.width=(right-left)+"px";box.style.height=(bottom-top)+"px";
    st.selected=new Set(m.add?m.base:[]);root.querySelectorAll(".timeline-selectable").forEach(item=>{const r=item.getBoundingClientRect();if(r.right>=left&&r.left<=right&&r.bottom>=top&&r.top<=bottom)st.selected.add(item.dataset.selectKey);});updateSelection();
  }
  function endMarquee(event){if(!st.marquee)return;const m=st.marquee;st.marquee=null;$("#editor-marquee").hidden=true;if(!m.moved)seekFromPointer(event.clientX,m.lane);else updateSelection();}
  function showJobModal(){const modal=$("#editor-job-modal");modal.hidden=false;$("#editor-job-close").hidden=true;$("#editor-job-title").textContent="Preparing subtitles";$("#editor-job-stage").textContent="Starting...";$("#editor-job-percent").textContent="0%";$("#editor-job-fill").style.width="0%";$("#editor-job-detail").textContent="Preparing the audio.";$("#editor-job-eta").textContent="Calculating time remaining...";}
  function etaLabel(seconds){if(!Number.isFinite(seconds)||seconds<1)return"Almost done";if(seconds<60)return"About "+Math.ceil(seconds)+" sec left";return"About "+Math.ceil(seconds/60)+" min left";}
  function updateJobModal(job,startedAt){const progress=Math.max(0,Math.min(100,Number(job.progress)||0)),elapsed=(Date.now()-startedAt)/1000;$("#editor-job-title").textContent=job.title||"Generating subtitles";$("#editor-job-stage").textContent=job.stage||"Working";$("#editor-job-percent").textContent=Math.round(progress)+"%";$("#editor-job-fill").style.width=progress+"%";$("#editor-job-detail").textContent=job.detail||"Listening and timing each subtitle.";$("#editor-job-eta").textContent=progress>=100?"Subtitles are ready":progress>=3?etaLabel(elapsed*(100-progress)/progress):"Calculating time remaining...";}
  function showJobError(message){$("#editor-job-modal").hidden=false;$("#editor-job-title").textContent="Subtitles failed";$("#editor-job-stage").textContent="Nothing was replaced";$("#editor-job-detail").textContent=message;$("#editor-job-eta").textContent="Close this box and try again when the PC is free.";$("#editor-job-close").hidden=false;}  function showSubtitlePanel(){const panel=$("#editor-subtitle-panel");panel.hidden=false;panel.classList.remove("is-minimized");$("#editor-subtitle-minimize").textContent="—";$("#editor-subtitle-window").classList.add("is-active");}
  function hideSubtitlePanel(){$("#editor-subtitle-panel").hidden=true;$("#editor-subtitle-window").classList.remove("is-active");}
  function startSubtitlePanelDrag(event){if(event.button!==0||event.target.closest("button"))return;event.preventDefault();const panel=$("#editor-subtitle-panel"),rootRect=root.getBoundingClientRect(),rect=panel.getBoundingClientRect();panel.style.right="auto";panel.style.left=(rect.left-rootRect.left)+"px";panel.style.top=(rect.top-rootRect.top)+"px";st.subtitleDrag={x:event.clientX,y:event.clientY,left:rect.left-rootRect.left,top:rect.top-rootRect.top};document.body.classList.add("subtitle-window-dragging");}
  function moveSubtitlePanel(event){if(!st.subtitleDrag)return;const panel=$("#editor-subtitle-panel"),rootRect=root.getBoundingClientRect(),rect=panel.getBoundingClientRect(),left=Math.max(8,Math.min(rootRect.width-rect.width-8,st.subtitleDrag.left+event.clientX-st.subtitleDrag.x)),top=Math.max(58,Math.min(rootRect.height-rect.height-8,st.subtitleDrag.top+event.clientY-st.subtitleDrag.y));panel.style.left=left+"px";panel.style.top=top+"px";}
  function endSubtitlePanelDrag(){if(!st.subtitleDrag)return;st.subtitleDrag=null;document.body.classList.remove("subtitle-window-dragging");}  function ruler(){
    const width=timelineWidth(),span=st.data.edit_max_sec-st.data.edit_min_sec;
    const targetTicks=Math.max(6,Math.floor(width/110));
    const candidates=[1,2,5,10,15,30,60,120,300];
    const step=candidates.find(value=>span/value<=targetTicks)||300;
    const marks=[];
    let time=Math.ceil(st.data.edit_min_sec/step)*step;
    for(;time<=st.data.edit_max_sec;time+=step)marks.push('<span style="left:'+px(time)+'px"><i></i>'+fmt(time)+'</span>');
    $("#editor-ruler").innerHTML=marks.join("");
  }
  function waveform(track){
    const all=track.waveform||[],base=Number(track.waveform_start||st.data.edit_min_sec);
    const from=Math.max(0,Math.floor(st.data.source_start_sec-base)),to=Math.min(all.length,Math.ceil(st.data.source_end_sec-base)+1);
    const values=all.slice(from,to);if(!values.length)return'<em>No waveform</em>';
    const stride=Math.max(1,Math.ceil(values.length/180)),bars=[];
    for(let i=0;i<values.length;i+=stride){const h=Math.max(7,Math.min(100,(Number(values[i])+60)/60*100));bars.push('<i style="height:'+h.toFixed(0)+'%"></i>');}
    return bars.join("");
  }
  function captionBlocks(){
    const blocks=st.data.captions.map((caption,index)=>({caption,index})).filter(x=>Number(x.caption.end)>=st.data.edit_min_sec&&Number(x.caption.start)<=st.data.edit_max_sec&&!cutIntersects(x.caption.start,x.caption.end)).map(({caption,index})=>{
      const left=px(caption.start),width=Math.max(5,px(caption.end)-left);
      return'<button class="caption-block timeline-selectable'+(st.selected.has("caption:"+index)?" is-selected":"")+'" data-select-key="caption:'+index+'" data-index="'+index+'" style="left:'+left+'px;width:'+width+'px" title="'+esc(caption.text)+'">'+esc(caption.text)+'</button>';
    });
    $("#editor-caption-track").innerHTML=blocks.join("");
  }


  function selectedSegment(){
    for(const key of st.selected){if(key.startsWith("segment:"))return st.data.segments[Number(key.split(":")[1])]||null;}
    const time=Number($("#editor-video").currentTime||0);
    return(st.data.segments||[]).find(segment=>time>=Number(segment.source_start)&&time<=Number(segment.source_end))||st.data.segments?.[0]||null;
  }
  function updateSegmentInspector(){
    const segment=selectedSegment();
    ["#editor-segment-speed","#editor-segment-freeze","#editor-segment-transition"].forEach(selector=>$(selector).disabled=!segment);
    if(segment){$("#editor-segment-speed").value=segment.speed||1;$("#editor-segment-freeze").value=segment.freeze||0;$("#editor-segment-transition").value=segment.transition||"none";$("#editor-segment-speed-value").textContent=Number(segment.speed||1).toFixed(2)+"×";$("#editor-segment-freeze-value").textContent=Number(segment.freeze||0).toFixed(2)+"s";}
  }
  function segmentBlocks(){
    $("#editor-segment-clips").innerHTML=(st.data.segments||[]).map((segment,index)=>{const left=px(segment.source_start),width=Math.max(8,px(segment.source_end)-left),selected=st.selected.has("segment:"+index)?" is-selected":"",rate=Number(segment.speed||1)!==1?" · "+Number(segment.speed).toFixed(2)+"×":"",freeze=Number(segment.freeze||0)>0?" · freeze "+Number(segment.freeze).toFixed(1)+"s":"";return'<div class="nle-clip video-clip segment-clip timeline-selectable'+selected+'" data-select-key="segment:'+index+'" data-index="'+index+'" style="left:'+left+'px;width:'+width+'px"><button class="clip-handle left" aria-label="Trim clip start"></button><span>Clip '+(index+1)+rate+freeze+'</span><button class="clip-handle right" aria-label="Trim clip end"></button></div>';}).join("");
  }
  function sourceToOutput(time){let cursor=0;for(const segment of(st.data.segments||[])){const left=Number(segment.source_start),right=Number(segment.source_end),speed=Number(segment.speed||1);if(time>=left&&time<=right)return cursor+(time-left)/speed;cursor+=(right-left)/speed+Number(segment.freeze||0);}return Math.max(0,Math.min(outputDuration(),time-st.data.source_start_sec));}
  function assetTracks(){
    const base=st.data.edit_min_sec;
    $("#editor-overlay-track").innerHTML=(st.data.overlays||[]).map((item,index)=>'<div class="nle-clip overlay-clip timeline-selectable'+(st.selected.has("overlay:"+index)?" is-selected":"")+'" data-select-key="overlay:'+index+'" data-index="'+index+'" style="left:'+px(base+Number(item.start))+'px;width:'+Math.max(10,Number(item.duration)*st.pps)+'px"><span>'+esc(item.name||"Overlay")+'</span></div>').join("");
    $("#editor-sfx-track").innerHTML=(st.data.sfx||[]).map((item,index)=>'<div class="nle-clip sfx-clip timeline-selectable'+(st.selected.has("sfx:"+index)?" is-selected":"")+'" data-select-key="sfx:'+index+'" data-index="'+index+'" style="left:'+px(base+Number(item.start))+'px;width:'+Math.max(10,Number(item.duration)*st.pps)+'px"><span>'+esc(item.name||"Sound")+'</span></div>').join("");
  }
  function setTool(tool){st.tool=tool;root.dataset.tool=tool;root.querySelectorAll(".tool-button").forEach(button=>button.classList.toggle("active",button.dataset.tool===tool));status(tool==="blade"?"Blade tool · click a video piece to split it.":tool==="track"?"Track select · click an item to select that whole track.":"Selection tool · drag, trim, or select timeline items.");}
  function splitAt(time){const index=(st.data.segments||[]).findIndex(segment=>time>Number(segment.source_start)+.08&&time<Number(segment.source_end)-.08);if(index<0){status("Move the playhead inside a video piece before splitting.",true);return;}snapshot();const original=st.data.segments[index],left={...original,id:original.id+"-a",source_end:Number(time.toFixed(3)),transition:"none"},right={...original,id:original.id+"-b",source_start:Number(time.toFixed(3))};st.data.segments.splice(index,1,left,right);st.selected.clear();st.selected.add("segment:"+(index+1));updateGeometry(false);updateSelection();updateSegmentInspector();}
  function segmentsFromCuts(){let cursor=st.data.source_start_sec,index=0,result=[];for(const cut of[...(st.data.cuts||[])].sort((a,b)=>a.start-b.start)){if(Number(cut.start)>cursor+.08)result.push({id:"segment-cut-"+(++index),source_start:cursor,source_end:Number(cut.start),speed:1,freeze:0,transition:"none"});cursor=Math.max(cursor,Number(cut.end));}if(st.data.source_end_sec>cursor+.08)result.push({id:"segment-cut-"+(++index),source_start:cursor,source_end:st.data.source_end_sec,speed:1,freeze:0,transition:"none"});return result;}
  function removeSourceRange(start,end){const next=[];for(const segment of(st.data.segments||[])){const left=Number(segment.source_start),right=Number(segment.source_end);if(end<=left||start>=right){next.push(segment);continue;}if(start>left+.08)next.push({...segment,id:segment.id+"-left",source_end:Number(start.toFixed(3)),transition:"none"});if(end<right-.08)next.push({...segment,id:segment.id+"-right",source_start:Number(end.toFixed(3))});}if(!next.length){status("That removal would delete the entire reel.",true);return false;}st.data.segments=next;return true;}
  function deleteSelected(){if(!st.selected.size)return;snapshot();const groups={segment:[],overlay:[],sfx:[],caption:[]};for(const key of st.selected){const[type,index]=key.split(":");if(groups[type])groups[type].push(Number(index));}if(groups.segment.length&&(st.data.segments.length-groups.segment.length)<1){status("Keep at least one video piece.",true);return;}for(const[type,list]of Object.entries(groups))for(const index of list.sort((a,b)=>b-a)){if(type==="segment")st.data.segments.splice(index,1);if(type==="overlay")st.data.overlays.splice(index,1);if(type==="sfx")st.data.sfx.splice(index,1);if(type==="caption")st.data.captions.splice(index,1);}st.selected.clear();updateGeometry();captionRows();assetTracks();updateSelection();updateSegmentInspector();}
  function audioTracks(){
    const clipLeft=px(st.data.source_start_sec),clipWidth=Math.max(20,px(st.data.source_end_sec)-clipLeft);
    $("#editor-audio-tracks").innerHTML=st.data.audio_tracks.map((track,index)=>{
      const dots=(track.keyframes||[]).map((key,keyIndex)=>'<button class="audio-keyframe timeline-selectable'+(st.selected.has("keyframe:"+track.ordinal+":"+keyIndex)?" is-selected":"")+'" data-select-key="keyframe:'+track.ordinal+':'+keyIndex+'" data-key-index="'+keyIndex+'" style="left:'+Math.max(0,px(key.time)-clipLeft)+'px;bottom:'+Math.max(4,Math.min(42,8+(Number(key.gain_db)+36)))+'px" title="'+Number(key.gain_db).toFixed(1)+' dB at '+fmt(key.time)+'">◆</button>').join("");
      return'<div class="nle-track audio-track" data-ordinal="'+track.ordinal+'">'+
        '<div class="nle-track-label"><label><input class="audio-enabled" type="checkbox" '+(track.enabled?"checked":"")+'><span>A'+(index+1)+' <small>'+esc(track.label)+'</small></span></label><input class="audio-gain" type="range" min="-24" max="12" step="0.5" value="'+track.gain_db+'" title="Track gain"><output>'+Number(track.gain_db).toFixed(1)+' dB</output></div>'+
        '<div class="nle-track-lane"><div class="nle-clip audio-clip timeline-selectable'+(st.selected.has("audio:"+track.ordinal)?" is-selected":"")+'" data-select-key="audio:'+track.ordinal+'" style="left:'+clipLeft+'px;width:'+clipWidth+'px"><div class="clip-waveform">'+waveform(track)+'</div>'+dots+'</div></div></div>';
    }).join("");
  }
  function updateGeometry(rebuildAudio=true){
    clampBounds();
    segmentBlocks();assetTracks();updateSegmentInspector();
    $("#editor-start-number").value=st.data.source_start_sec.toFixed(2);$("#editor-end-number").value=st.data.source_end_sec.toFixed(2);
    $("#editor-duration").textContent=outputDuration().toFixed(1)+"s";
    if(rebuildAudio)audioTracks();renderCuts();captionBlocks();assetTracks();updatePlayhead();
  }
  function updatePlayhead(){
    if(!st.data)return;const video=$("#editor-video"),time=Number.isFinite(video.currentTime)?video.currentTime:st.data.source_start_sec;
    $("#editor-playhead").style.left=(140+px(time))+"px";
    $("#editor-time-readout").textContent=fmt(time)+" / "+fmt(st.data.source_end_sec);updateCaptionPreview();updatePendingCut();
  }
  function captionRows(){
    const d=st.data;
    const rows=d.captions.map((caption,index)=>({caption,index})).filter(x=>Number(x.caption.end)>=d.source_start_sec-1&&Number(x.caption.start)<=d.source_end_sec+1);
    $("#editor-caption-summary").textContent=rows.length+" subtitle"+(rows.length===1?"":"s")+" in this edit · drag-select timeline items or click a subtitle to focus it.";
    $("#editor-caption-list").innerHTML=rows.length?rows.map(({caption,index},position)=>{
      const duration=Math.max(0,Number(caption.end)-Number(caption.start));
      return'<article class="editor-caption-row'+(st.selected.has("caption:"+index)?" is-selected":"")+'" data-index="'+index+'">'+
        '<button class="caption-jump secondary" type="button" title="Jump to subtitle"><span>▶</span><b>#'+String(position+1).padStart(2,"0")+'</b></button>'+
        '<div class="caption-edit-main"><input class="caption-text" value="'+esc(caption.text)+'" aria-label="Subtitle text" placeholder="Type subtitle text"><div class="caption-row-meta"><span class="speaker-chip">'+esc(caption.speaker||"Speaker")+'</span><span>'+duration.toFixed(2)+'s</span></div></div>'+
        '<div class="caption-timing"><label><span>IN</span><input class="caption-start" type="number" step="0.01" value="'+Number(caption.start).toFixed(2)+'"></label><label><span>OUT</span><input class="caption-end" type="number" step="0.01" value="'+Number(caption.end).toFixed(2)+'"></label></div>'+
        '<button class="caption-delete ghost-danger" type="button" aria-label="Delete subtitle">×</button></article>';
    }).join(""):'<div class="empty subtitle-empty"><strong>No subtitles in this clip</strong><span>Add one manually or run Auto Subtitles when you are not gaming.</span></div>';
    updateSelection();
  }
  function render(){
    const d=st.data;st.pps=4*st.zoom;
    $("#editor-title").textContent="Edit · "+d.video_name;
    $("#editor-start-number").min=d.edit_min_sec;$("#editor-start-number").max=d.edit_max_sec;
    $("#editor-end-number").min=d.edit_min_sec;$("#editor-end-number").max=d.edit_max_sec;
    $("#editor-caption-preset").value=d.caption_preset;$("#editor-audio-preset").value=d.audio_preset;
    const crop=d.crop||{};$("#editor-frame-mode").value=crop.mode||"fit";$("#editor-frame-zoom").value=crop.zoom||1;$("#editor-frame-x").value=crop.x||0;$("#editor-frame-y").value=crop.y||0;$("#editor-caption-scale").value=crop.caption_scale||1;$("#editor-caption-y").value=crop.caption_y||0;$("#editor-fade-in").value=crop.fade_in||0;$("#editor-fade-out").value=crop.fade_out||0;updateStagePreview();const color=d.color||{};$("#editor-color-brightness").value=color.brightness??0;$("#editor-color-contrast").value=color.contrast??1;$("#editor-color-saturation").value=color.saturation??1;$("#editor-color-temperature").value=color.temperature??0;["brightness","contrast","saturation","temperature"].forEach(key=>$("#editor-color-"+key+"-value").textContent=Number($("#editor-color-"+key).value).toFixed(2));
    const width=timelineWidth();$("#editor-timeline").style.width=(140+width)+"px";
    root.querySelectorAll(".nle-track-lane").forEach(lane=>lane.style.width=width+"px");
    $("#editor-ruler").style.width=width+"px";
    ruler();updateGeometry();captionRows();updateSelection();undoState();
  }
  function seekFromPointer(clientX,lane){
    const rect=lane.getBoundingClientRect();const time=st.data.edit_min_sec+(clientX-rect.left)/st.pps;
    $("#editor-video").currentTime=Math.max(st.data.edit_min_sec,Math.min(st.data.edit_max_sec,time));updatePlayhead();
  }
  function startPlayheadDrag(event){event.preventDefault();event.stopPropagation();st.scrubbing=true;document.body.classList.add("playhead-dragging");seekFromPointer(event.clientX,$("#editor-ruler"));}
  function movePlayhead(event){if(st.scrubbing)seekFromPointer(event.clientX,$("#editor-ruler"));}
  function endPlayheadDrag(){if(!st.scrubbing)return;st.scrubbing=false;document.body.classList.remove("playhead-dragging");}

  function startClipDrag(event){
    const clip=event.target.closest(".segment-clip");if(!clip||event.button!==0)return;
    if(st.tool==="blade"){event.stopPropagation();seekFromPointer(event.clientX,clip.parentElement);splitAt($("#editor-video").currentTime);return;}
    event.preventDefault();const index=Number(clip.dataset.index),segment=st.data.segments[index],target=event.target;
    const mode=target.classList.contains("left")?"left":target.classList.contains("right")?"right":"move";
    st.drag={kind:"segment",index,mode,x:event.clientX,start:Number(segment.source_start),end:Number(segment.source_end),moved:false,lane:clip.parentElement};
  }
  function moveClip(event){
    if(!st.drag||st.drag.kind!=="segment")return;
    const pixelDelta=event.clientX-st.drag.x;if(!st.drag.moved&&Math.abs(pixelDelta)<3)return;
    if(!st.drag.moved){snapshot();st.drag.moved=true;document.body.classList.add("timeline-dragging");}
    const delta=pixelDelta/st.pps,duration=st.drag.end-st.drag.start,segment=st.data.segments[st.drag.index];
    if(st.drag.mode==="left")segment.source_start=Math.max(st.data.edit_min_sec,Math.min(st.drag.end-.08,st.drag.start+delta));
    if(st.drag.mode==="right")segment.source_end=Math.min(st.data.edit_max_sec,Math.max(st.drag.start+.08,st.drag.end+delta));
    if(st.drag.mode==="move"){const start=Math.max(st.data.edit_min_sec,Math.min(st.data.edit_max_sec-duration,st.drag.start+delta));segment.source_start=start;segment.source_end=start+duration;}
    segmentBlocks();updateSegmentInspector();
  }
  function endClipDrag(event){
    if(!st.drag||st.drag.kind!=="segment")return;
    const drag=st.drag;st.drag=null;document.body.classList.remove("timeline-dragging");
    if(!drag.moved){selectKey("segment:"+drag.index,event.shiftKey||event.ctrlKey||event.metaKey);seekFromPointer(event.clientX,drag.lane);}
    else{segmentBlocks();captionRows();}
    updateSegmentInspector();
  }
  function startAssetDrag(event){
    const clip=event.target.closest(".overlay-clip,.sfx-clip");if(!clip||event.button!==0||st.tool!=="pointer")return;
    event.preventDefault();event.stopPropagation();const kind=clip.classList.contains("overlay-clip")?"overlay":"sfx",index=Number(clip.dataset.index),item=(kind==="overlay"?st.data.overlays:st.data.sfx)[index];
    selectKey(kind+":"+index,event.shiftKey||event.ctrlKey||event.metaKey);st.drag={kind:"asset",assetKind:kind,index,x:event.clientX,start:Number(item.start),moved:false};
  }
  function moveAsset(event){
    if(!st.drag||st.drag.kind!=="asset")return;const delta=(event.clientX-st.drag.x)/st.pps;if(!st.drag.moved&&Math.abs(delta*st.pps)<3)return;if(!st.drag.moved){snapshot();st.drag.moved=true;}
    const list=st.drag.assetKind==="overlay"?st.data.overlays:st.data.sfx,item=list[st.drag.index];item.start=Math.max(0,Math.min(outputDuration()-Number(item.duration),st.drag.start+delta));assetTracks();
  }
  function endAssetDrag(){if(st.drag&&st.drag.kind==="asset"){st.drag=null;status("Timeline asset moved.");}}
  function body(){readStageControls();st.data.caption_preset=$("#editor-caption-preset").value;st.data.audio_preset=$("#editor-audio-preset").value;return{base_variant_id:st.data.base_variant_id,source_start_sec:st.data.source_start_sec,source_end_sec:st.data.source_end_sec,caption_preset:st.data.caption_preset,audio_preset:st.data.audio_preset,captions:st.data.captions,audio_mix:st.data.audio_tracks.map(t=>({ordinal:t.ordinal,enabled:t.enabled,gain_db:t.gain_db,keyframes:t.keyframes||[]})),crop:st.data.crop||{},cuts:st.data.cuts||[],segments:st.data.segments||[],overlays:st.data.overlays||[],sfx:st.data.sfx||[],color:st.data.color||{}};}
  async function save(quiet=false){st.data=await api("/api/candidates/"+st.data.candidate_id+"/editor/save",{method:"POST",body:JSON.stringify(body())});if(!quiet){$("#editor-save-state").textContent="Draft saved · previous reel untouched.";status("Draft saved.");}}
  async function waitJob(id,label,showModal=false){const startedAt=Date.now();if(showModal)showJobModal();for(;;){const job=(await api("/api/jobs"))[id];if(!job)throw new Error("Editor job disappeared");status(label+" · "+Number(job.progress||0)+"% · "+(job.stage||"Working"));if(showModal)updateJobModal(job,startedAt);if(job.status==="complete")return job;if(job.status==="error")throw new Error(job.detail||job.error||"Editor job failed");await new Promise(r=>setTimeout(r,1000));}}

  function fileAsDataUrl(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(reader.error);reader.readAsDataURL(file);});}
  async function addAssetFile(file,kind){
    if(!file)return;if(file.size>80*1024*1024){status("That file is over the 80 MB editor-asset limit.",true);return;}
    status("Adding "+file.name+"...");
    const uploaded=await api("/api/candidates/"+st.data.candidate_id+"/editor/asset",{method:"POST",body:JSON.stringify({name:file.name,kind,data:await fileAsDataUrl(file)})});
    snapshot();const start=sourceToOutput(Number($("#editor-video").currentTime||st.data.source_start_sec));
    if(kind==="overlay")st.data.overlays.push({...uploaded,id:"overlay-"+Date.now(),start,duration:3,x:0,y:0,scale:.55});
    else st.data.sfx.push({...uploaded,id:"sfx-"+Date.now(),start,duration:Math.min(8,Math.max(1,outputDuration()-start)),gain_db:0});
    assetTracks();status(file.name+" added at "+fmt(start)+".");
  }
  function addAudioKeyframe(){
    const time=Number($("#editor-video").currentTime||st.data.source_start_sec);
    let track=null;for(const key of st.selected){if(key.startsWith("audio:"))track=st.data.audio_tracks.find(item=>item.ordinal===Number(key.split(":")[1]));}
    track=track||st.data.audio_tracks.find(item=>item.enabled)||st.data.audio_tracks[0];if(!track)return;
    const raw=window.prompt("Gain change at "+fmt(time)+" (dB, -36 to +12)",String(track.gain_db||0));if(raw===null)return;
    const gain=Math.max(-36,Math.min(12,Number(raw)));if(!Number.isFinite(gain))return;
    snapshot();track.keyframes=track.keyframes||[];const existing=track.keyframes.find(item=>Math.abs(Number(item.time)-time)<.08);
    if(existing)existing.gain_db=gain;else track.keyframes.push({time:Number(time.toFixed(3)),gain_db:Number(gain.toFixed(1))});
    track.keyframes.sort((a,b)=>a.time-b.time);audioTracks();status("Audio keyframe added to "+track.label+".");
  }
  async function toggleRecording(){
    const button=$("#editor-record-audio");
    if(st.recorder&&st.recorder.state==="recording"){st.recorder.stop();button.textContent="● Record audio";button.classList.remove("recording");return;}
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});st.recordChunks=[];st.recorder=new MediaRecorder(stream);
      st.recorder.ondataavailable=event=>{if(event.data.size)st.recordChunks.push(event.data);};
      st.recorder.onstop=async()=>{stream.getTracks().forEach(track=>track.stop());const blob=new Blob(st.recordChunks,{type:st.recorder.mimeType||"audio/webm"});const file=new File([blob],"voiceover-"+Date.now()+".webm",{type:blob.type});try{await addAssetFile(file,"sfx");}catch(error){status(error.message,true);}};
      st.recorder.start();button.textContent="■ Stop recording";button.classList.add("recording");status("Recording microphone into the SFX track...");
    }catch(error){status("Microphone recording failed: "+error.message,true);}
  }
  async function open(id){root.hidden=false;document.body.classList.add("editor-open");status("Loading source VOD and edit recipe...");try{st.data=await api("/api/candidates/"+id+"/editor");st.history=[];st.future=[];st.zoom=1;st.cutMark=null;st.selected.clear();showSubtitlePanel();$("#editor-timeline-zoom").value="1";render();const video=$("#editor-video");video.src=st.data.source_url;video.load();video.addEventListener("loadedmetadata",()=>{video.currentTime=st.data.source_start_sec;status(st.data.has_draft?"Saved draft loaded. Drag the clip or either edge to edit.":"AI cut loaded. Drag the clip or either edge to edit.");updatePlayhead();},{once:true});video.addEventListener("error",()=>status("This source format needs an editor proxy before browser preview; timing and rendering still work.",true),{once:true});}catch(error){status(error.message,true);}}
  function close(){if(motionUI)motionUI.cancel();const video=$("#editor-video");video.pause();video.removeAttribute("src");$("#editor-job-modal").hidden=true;root.hidden=true;document.body.classList.remove("editor-open");}
  motionUI=window.MotionCaptions.create({root,getData:()=>st.data,getSelected:()=>st.selected,snapshot,status,isBusy:()=>st.busy,refresh:()=>{captionRows();captionBlocks();updateSelection();updateCaptionPreview();}});
  $("#editor-motion-window").addEventListener("click",()=>{hideSubtitlePanel();$("#editor-motion-panel").open=true;$(".editor-inspector").scrollTop=0;motionUI.sync();status("Select a caption, make a short phrase, then place or track it above a character.");});
  $("#editor-close").addEventListener("click",close);$("#editor-job-close").addEventListener("click",()=>{$("#editor-job-modal").hidden=true;});
  $("#editor-subtitle-window").addEventListener("click",showSubtitlePanel);$("#editor-subtitle-close").addEventListener("click",hideSubtitlePanel);$("#editor-subtitle-minimize").addEventListener("click",()=>{const panel=$("#editor-subtitle-panel"),minimized=panel.classList.toggle("is-minimized");$("#editor-subtitle-minimize").textContent=minimized?"□":"—";});$("#editor-subtitle-drag-handle").addEventListener("pointerdown",startSubtitlePanelDrag);
  $("#editor-segment-clips").addEventListener("pointerdown",startClipDrag);
  $("#editor-playhead").addEventListener("pointerdown",startPlayheadDrag);
  $("#editor-overlay-track").addEventListener("pointerdown",startAssetDrag);$("#editor-sfx-track").addEventListener("pointerdown",startAssetDrag);
  document.addEventListener("pointermove",moveClip);document.addEventListener("pointermove",moveAsset);document.addEventListener("pointermove",movePlayhead);document.addEventListener("pointermove",moveMarquee);document.addEventListener("pointermove",moveSubtitlePanel);document.addEventListener("pointerup",endClipDrag);document.addEventListener("pointerup",endAssetDrag);document.addEventListener("pointerup",endPlayheadDrag);document.addEventListener("pointerup",endMarquee);document.addEventListener("pointerup",endSubtitlePanelDrag);
  $("#editor-timeline").addEventListener("pointerdown",event=>{if(event.target.closest(".segment-clip")||event.target.closest(".nle-track-label")||event.target.closest("#editor-playhead"))return;const ruler=event.target.closest("#editor-ruler");const lane=event.target.closest(".nle-track-lane");const item=event.target.closest(".timeline-selectable");if(ruler)seekFromPointer(event.clientX,ruler);else if(item){if(st.tool==="track"){const type=item.dataset.selectKey.split(":")[0];st.selected.clear();root.querySelectorAll("[data-select-key^=\""+type+":\"]").forEach(node=>st.selected.add(node.dataset.selectKey));updateSelection();}else selectKey(item.dataset.selectKey,event.shiftKey||event.ctrlKey||event.metaKey);if(lane)seekFromPointer(event.clientX,lane);updateSegmentInspector();}else if(lane)startMarquee(event,lane);});
  $("#editor-caption-track").addEventListener("click",event=>{const block=event.target.closest(".caption-block");if(!block)return;selectKey("caption:"+block.dataset.index,event.shiftKey||event.ctrlKey||event.metaKey);const caption=st.data.captions[Number(block.dataset.index)];$("#editor-video").currentTime=caption.start;updatePlayhead();const row=$("#editor-caption-list").querySelector('[data-index="'+block.dataset.index+'"]');if(row)row.scrollIntoView({block:"nearest"});});
  $("#editor-timeline-zoom").addEventListener("input",event=>{st.zoom=Number(event.target.value);render();});$("#editor-clear-selection").addEventListener("click",()=>{st.selected.clear();updateSelection();});
  $("#editor-caption-preset").addEventListener("input",updateCaptionPreview);
  ["#editor-frame-mode","#editor-frame-zoom","#editor-frame-x","#editor-frame-y","#editor-caption-scale","#editor-caption-y","#editor-fade-in","#editor-fade-out","#editor-color-brightness","#editor-color-contrast","#editor-color-saturation","#editor-color-temperature"].forEach(selector=>$(selector).addEventListener("input",()=>{readStageControls();updateStagePreview();}));

  root.querySelectorAll(".tool-button").forEach(button=>button.addEventListener("click",()=>setTool(button.dataset.tool)));
  $("#editor-split").addEventListener("click",()=>splitAt(Number($("#editor-video").currentTime)));
  $("#editor-delete-selected").addEventListener("click",deleteSelected);
  $("#editor-add-keyframe").addEventListener("click",addAudioKeyframe);
  $("#editor-add-overlay").addEventListener("click",()=>$("#editor-overlay-input").click());
  $("#editor-add-sfx").addEventListener("click",()=>$("#editor-sfx-input").click());
  $("#editor-overlay-input").addEventListener("change",async event=>{try{await addAssetFile(event.target.files[0],"overlay");}catch(error){status(error.message,true);}event.target.value="";});
  $("#editor-sfx-input").addEventListener("change",async event=>{try{await addAssetFile(event.target.files[0],"sfx");}catch(error){status(error.message,true);}event.target.value="";});
  $("#editor-record-audio").addEventListener("click",toggleRecording);
  ["#editor-segment-speed","#editor-segment-freeze","#editor-segment-transition"].forEach(selector=>$(selector).addEventListener("input",event=>{const segment=selectedSegment();if(!segment)return;snapshot();if(selector.endsWith("speed"))segment.speed=Number(event.target.value);else if(selector.endsWith("freeze"))segment.freeze=Number(event.target.value);else segment.transition=event.target.value;updateSegmentInspector();segmentBlocks();$("#editor-duration").textContent=outputDuration().toFixed(1)+"s";}));
  $("#editor-cut-start").addEventListener("click",()=>{st.cutMark=Math.max(st.data.source_start_sec,Math.min(st.data.source_end_sec,$("#editor-video").currentTime||st.data.source_start_sec));$("#editor-cut-finish").disabled=false;updatePendingCut();});
  $("#editor-cut-finish").addEventListener("click",()=>{if(st.cutMark===null)return;const time=Math.max(st.data.source_start_sec,Math.min(st.data.source_end_sec,$("#editor-video").currentTime));const start=Math.min(st.cutMark,time),end=Math.max(st.cutMark,time);if(end-start<.1){status("Move the playhead before finishing the cut.",true);return;}if(outputDuration()-(end-start)<3){status("That cut would leave less than 3 seconds.",true);return;}snapshot();if(!removeSourceRange(start,end))return;st.data.cuts.push({start,end});st.cutMark=null;$("#editor-cut-finish").disabled=true;$("#editor-cut-status").textContent="Section removed. Render a new attempt when ready.";updateGeometry();captionRows();});
  $("#editor-clear-cuts").addEventListener("click",()=>{if(!(st.data.cuts||[]).length)return;snapshot();st.data.cuts=[];st.data.segments=[{id:"segment-reset",source_start:st.data.source_start_sec,source_end:st.data.source_end_sec,speed:1,freeze:0,transition:"none"}];st.cutMark=null;$("#editor-cut-finish").disabled=true;$("#editor-cut-status").textContent="Cuts cleared.";updateGeometry();captionRows();});
  $("#editor-cut-overlays").addEventListener("click",event=>{const block=event.target.closest(".cut-block");if(!block)return;event.stopPropagation();snapshot();st.selected.delete("cut:"+block.dataset.index);st.data.cuts.splice(Number(block.dataset.index),1);st.data.segments=segmentsFromCuts();$("#editor-cut-status").textContent="Cut restored.";updateGeometry();captionRows();});
  ["#editor-start-number","#editor-end-number"].forEach(s=>$(s).addEventListener("change",()=>{snapshot();st.data.source_start_sec=Number($("#editor-start-number").value);st.data.source_end_sec=Number($("#editor-end-number").value);updateGeometry();captionRows();}));
  $("#editor-play-selection").addEventListener("click",async()=>{const v=$("#editor-video");v.currentTime=st.data.source_start_sec;await v.play();});
  $("#editor-pause").addEventListener("click",()=>$("#editor-video").pause());
  $("#editor-preview-size").addEventListener("click",()=>{const expanded=root.classList.toggle("preview-expanded");$("#editor-preview-size").textContent=expanded?"Show timeline":"Expand preview";});
  $("#editor-step-back").addEventListener("click",()=>{$("#editor-video").currentTime=Math.max(st.data.edit_min_sec,$("#editor-video").currentTime-1/30);updatePlayhead();});
  $("#editor-step-forward").addEventListener("click",()=>{$("#editor-video").currentTime=Math.min(st.data.edit_max_sec,$("#editor-video").currentTime+1/30);updatePlayhead();});
  $("#editor-video").addEventListener("timeupdate",()=>{const v=$("#editor-video");if(st.data){const segments=st.data.segments||[],index=segments.findIndex(item=>v.currentTime>=Number(item.source_start)&&v.currentTime<Number(item.source_end)-.02);if(index>=0&&v.currentTime>=Number(segments[index].source_end)-.06){const next=segments[index+1];if(next)v.currentTime=Number(next.source_start);else v.pause();}else if(index<0){const next=segments.find(item=>Number(item.source_start)>v.currentTime);if(next)v.currentTime=Number(next.source_start);else if(v.currentTime>=st.data.source_end_sec)v.pause();}}updatePlayhead();});
  $("#editor-audio-tracks").addEventListener("change",event=>{const row=event.target.closest(".audio-track");if(!row)return;snapshot();const track=st.data.audio_tracks.find(t=>t.ordinal===Number(row.dataset.ordinal));if(event.target.classList.contains("audio-enabled"))track.enabled=event.target.checked;if(event.target.classList.contains("audio-gain")){track.gain_db=Number(event.target.value);row.querySelector("output").textContent=track.gain_db.toFixed(1)+" dB";}});
  $("#editor-caption-list").addEventListener("input",event=>{const row=event.target.closest(".editor-caption-row");if(!row)return;snapshot();const caption=st.data.captions[Number(row.dataset.index)];if(event.target.classList.contains("caption-text"))caption.text=event.target.value;if(event.target.classList.contains("caption-start"))caption.start=Number(event.target.value);if(event.target.classList.contains("caption-end"))caption.end=Number(event.target.value);captionBlocks();updateCaptionPreview();motionUI.sync();motionUI.draw();});
  $("#editor-caption-list").addEventListener("click",event=>{const row=event.target.closest(".editor-caption-row");if(!row)return;const index=Number(row.dataset.index),caption=st.data.captions[index];if(event.target.closest(".caption-jump")){$("#editor-video").currentTime=caption.start;selectKey("caption:"+index,event.shiftKey||event.ctrlKey||event.metaKey);updatePlayhead();return;}if(event.target.closest(".caption-delete")){snapshot();st.selected.delete("caption:"+index);st.data.captions.splice(index,1);st.selected.clear();captionRows();captionBlocks();updateSelection();motionUI.draw();return;}if(!event.target.matches("input"))selectKey("caption:"+index,event.shiftKey||event.ctrlKey||event.metaKey);});
  $("#editor-caption-list").addEventListener("focusin",event=>{const row=event.target.closest(".editor-caption-row");if(row&&!st.selected.has("caption:"+row.dataset.index))selectKey("caption:"+row.dataset.index);});
  $("#editor-add-caption").addEventListener("click",()=>{snapshot();const time=Math.max(st.data.source_start_sec,$("#editor-video").currentTime||st.data.source_start_sec);st.data.captions.push({text:"New subtitle",start:time,end:Math.min(st.data.source_end_sec,time+1),speaker:"Me",track_ordinal:null});st.data.captions.sort((a,b)=>a.start-b.start);captionRows();captionBlocks();});
  $("#editor-save").addEventListener("click",async()=>{try{await save();render();}catch(error){status(error.message,true);}});
  $("#editor-auto-subtitles").addEventListener("click",async()=>{if(st.busy)return;st.busy=true;try{await save(true);const started=await api("/api/candidates/"+st.data.candidate_id+"/editor/subtitles",{method:"POST",body:"{}"});await waitJob(started.job_id,"Generating new subtitles",true);st.data=await api("/api/candidates/"+st.data.candidate_id+"/editor");st.history=[];st.future=[];render();status("New subtitles ready. Review, then save or render.");setTimeout(()=>{$("#editor-job-modal").hidden=true;},700);}catch(error){status(error.message,true);showJobError(error.message);}finally{st.busy=false;}});
  $("#editor-render").addEventListener("click",async()=>{if(st.busy)return;st.busy=true;try{await save(true);const started=await api("/api/candidates/"+st.data.candidate_id+"/editor/render",{method:"POST",body:"{}"});await waitJob(started.job_id,"Rendering manual edit");status("New edited attempt is ready in Review.");$("#editor-save-state").textContent="Rendered as a new attempt · original preserved.";if(typeof loadCandidates==="function")await loadCandidates();}catch(error){status(error.message,true);}finally{st.busy=false;}});
  $("#editor-reset").textContent="Reload saved";$("#editor-reset").addEventListener("click",async()=>{st.data=await api("/api/candidates/"+st.data.candidate_id+"/editor");st.history=[];st.future=[];render();status("Reloaded last saved draft.");});
  $("#editor-undo").addEventListener("click",()=>{if(!st.history.length)return;st.future.push(copy(st.data));st.data=st.history.pop();render();});
  $("#editor-redo").addEventListener("click",()=>{if(!st.future.length)return;st.history.push(copy(st.data));st.data=st.future.pop();render();});

  document.addEventListener("keydown",event=>{
    if(root.hidden)return;
    if(motionUI.isTracking()){if(event.key==="Escape")motionUI.cancel();event.preventDefault();return;}
    const editable=event.target.matches("input,textarea,select");
    const key=event.key.toLowerCase();
    if(event.key==="Escape"){if(st.tool!=="pointer")setTool("pointer");else close();return;}
    if((event.ctrlKey||event.metaKey)&&key==="s"){event.preventDefault();$("#editor-save").click();return;}
    if((event.ctrlKey||event.metaKey)&&key==="z"){event.preventDefault();$("#editor-undo").click();return;}
    if((event.ctrlKey||event.metaKey)&&key==="y"){event.preventDefault();$("#editor-redo").click();return;}
    if(editable)return;
    if(event.code==="Space"){event.preventDefault();const v=$("#editor-video");v.paused?v.play():v.pause();return;}
    if((event.ctrlKey||event.metaKey)&&key==="k"){event.preventDefault();splitAt(Number($("#editor-video").currentTime));return;}
    if(key==="v"){setTool("pointer");return;}if(key==="c"){setTool("blade");return;}if(key==="a"){setTool("track");return;}
    if(event.key==="Delete"||event.key==="Backspace"){event.preventDefault();deleteSelected();return;}
    if(key==="i"){$("#editor-cut-start").click();return;}if(key==="o"){$("#editor-cut-finish").click();return;}
    if(key==="j"){$("#editor-video").pause();$("#editor-video").currentTime=Math.max(st.data.edit_min_sec,$("#editor-video").currentTime-1);updatePlayhead();return;}
    if(key==="k"&&!event.shiftKey){$("#editor-video").pause();return;}if(key==="l"){$("#editor-video").play();return;}
    if(event.shiftKey&&key==="k"){addAudioKeyframe();return;}
    if(key==="r"){const segment=selectedSegment();if(!segment)return;const value=window.prompt("Clip speed (0.25× to 4×)",String(segment.speed||1));if(value!==null){snapshot();segment.speed=Math.max(.25,Math.min(4,Number(value)||1));updateGeometry(false);}return;}
    const segment=selectedSegment();if(segment&&event.key==="["){snapshot();segment.source_start=Math.min(segment.source_end-.08,Number($("#editor-video").currentTime));updateGeometry(false);}if(segment&&event.key==="]"){snapshot();segment.source_end=Math.max(segment.source_start+.08,Number($("#editor-video").currentTime));updateGeometry(false);}
  });
  window.clipEditor={open,close};
})();
