(() => {
  'use strict';
  window.StudioWorkspace = ctx => {
    const $=s=>document.querySelector(s), E=window.StudioEditing;
    const esc=ctx.esc, clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
    let sizes={timeline:390,bin:250,inspector:260}, source=null, sourceTime=0, sourceOffset=0, sourcePlaying=false, sourceToken=0;
    try{sizes={...sizes,...JSON.parse(localStorage.getItem('clipmaker-layout-v2')||'{}')};}catch{}
    const editor=$('#studio-editor');
    function resize(){
      if(editor.hidden||!editor.clientHeight)return;const width=$('.editor-columns').clientWidth, height=editor.clientHeight;
      const handheld=document.body.classList.contains('handheld');
      sizes.timeline=clamp(Number(sizes.timeline)||390,180,Math.max(180,handheld?Math.floor(height*.42):height-160));
      sizes.bin=clamp(Number(sizes.bin)||250,150,Math.max(150,width-430));
      sizes.inspector=clamp(Number(sizes.inspector)||260,180,Math.max(180,width-sizes.bin-180));
      editor.style.setProperty('--timeline-size',sizes.timeline+'px');editor.style.setProperty('--bin-size',sizes.bin+'px');editor.style.setProperty('--inspector-size',sizes.inspector+'px');
      for(const [id,key]of [['studio-timeline-resize','timeline'],['studio-bin-resize','bin'],['studio-inspector-resize','inspector']]){const h=$('#'+id);h.setAttribute('aria-valuenow',Math.round(sizes[key]));h.setAttribute('aria-valuemin',key==='timeline'?180:150);h.setAttribute('aria-valuemax',Math.max(1000,Math.round(sizes[key])));}
      const audioTools=$('.audio-track-tools');if(audioTools)audioTools.style.width=Math.max(1,$('#studio-timeline-scroll').clientWidth)+'px';ctx.layout();
    }
    function remember(){localStorage.setItem('clipmaker-layout-v2',JSON.stringify(sizes));}
    for(const [id,key,axis,direction]of [['studio-timeline-resize','timeline','y',-1],['studio-bin-resize','bin','x',1],['studio-inspector-resize','inspector','x',-1]]){
      const handle=$('#'+id);let drag=null;
      handle.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();drag={point:axis==='x'?e.clientX:e.clientY,size:sizes[key],id:e.pointerId};handle.setPointerCapture(e.pointerId);};
      handle.onpointermove=e=>{if(!drag)return;sizes[key]=drag.size+direction*((axis==='x'?e.clientX:e.clientY)-drag.point);resize();};
      const end=()=>{if(!drag)return;drag=null;remember();};for(const event of ['pointerup','pointercancel','lostpointercapture'])handle.addEventListener(event,end);
      handle.onkeydown=e=>{const negative=axis==='x'?'ArrowLeft':'ArrowUp',positive=axis==='x'?'ArrowRight':'ArrowDown';if(![negative,positive].includes(e.key))return;e.preventDefault();e.stopPropagation();sizes[key]+=direction*(e.key===positive?20:-20);resize();remember();};
    }
    $('#studio-layout-reset').onclick=()=>{sizes={timeline:390,bin:250,inspector:260};resize();remember();};
    new ResizeObserver(resize).observe(editor);
    function tab(bookmarks){$('#studio-effects-library').hidden=true;$('#studio-effects-tab').setAttribute('aria-pressed','false');$('.media-bin').scrollTop=0;$('#studio-bookmark-list').hidden=!bookmarks;$('#studio-bin-list').hidden=bookmarks;$('#studio-source-monitor').hidden=bookmarks||!source;$('#studio-sources-tab').setAttribute('aria-pressed',!bookmarks);$('#studio-bookmarks-tab').setAttribute('aria-pressed',bookmarks);if(bookmarks){pause();refresh();}}
    $('#studio-sources-tab').onclick=()=>tab(false);$('#studio-bookmarks-tab').onclick=()=>tab(true);$('#studio-effects-tab').onclick=()=>{pause();$('#studio-effects-library').hidden=false;for(const id of ['studio-bin-list','studio-source-monitor','studio-bookmark-list'])$('#'+id).hidden=true;for(const id of ['studio-sources-tab','studio-bookmarks-tab'])$('#'+id).setAttribute('aria-pressed','false');$('#studio-effects-tab').setAttribute('aria-pressed','true');};
    function refresh(){
      const r=ctx.recipe();if(!r)return;const term=$('#studio-bookmark-search').value.toLowerCase();
      $('#studio-bookmark-results').innerHTML=r.markers.map((m,index)=>({...m,index})).filter(m=>(m.label+' '+ctx.fmt(m.time)).toLowerCase().includes(term)).sort((a,b)=>a.time-b.time).map(m=>'<button class="bookmark-result secondary" data-bookmark-jump="'+m.index+'"><time>'+ctx.fmt(m.time)+'</time><span>'+esc(m.label||'Bookmark')+'</span></button>').join('')||'<p class="muted small">No matching bookmarks.</p>';
    }
    $('#studio-bookmark-search').oninput=refresh;
    $('#studio-bookmark-results').onclick=e=>{const b=e.target.closest('[data-bookmark-jump]');if(b)ctx.jump(Number(b.dataset.bookmarkJump));};
    for(const [id,direction]of [['prev',-1],['next',1]])$('#studio-bookmark-'+id).onclick=()=>{const markers=(ctx.recipe()?.markers||[]).map((m,index)=>({...m,index})).sort((a,b)=>a.time-b.time);const candidates=markers.filter(m=>direction>0?m.time>ctx.time()+.04:m.time<ctx.time()-.04);const m=direction>0?candidates[0]:candidates.at(-1);if(m)ctx.jump(m.index);};
    const v=$('#studio-source-video');
    function pause(){sourcePlaying=false;v.pause();$('#studio-source-play').textContent='▶ Source';}
    function sourceClock(){if(!source)return;$('#studio-source-clock').textContent=ctx.fmt(sourceTime)+' / '+ctx.fmt(source.metadata.duration_sec);$('#studio-source-scrub').value=sourceTime;}
    function loadSource(url){return new Promise((resolve,reject)=>{const timer=setTimeout(()=>done(new Error('Source preview timed out. Try opening this source again.')),20000),ready=()=>done(),error=()=>done(new Error('Source preview could not play.'));function done(err){clearTimeout(timer);v.removeEventListener('loadedmetadata',ready);v.removeEventListener('error',error);err?reject(err):resolve();}v.addEventListener('loadedmetadata',ready,{once:true});v.addEventListener('error',error,{once:true});v.src=url;});}
    const sourceMix=media=>media.metadata.audio_streams.map(t=>({ordinal:t.ordinal,enabled:t.ordinal===Number($('#studio-source-track').value),gain_db:0}));
    async function seekSource(time){
      if(!source)return;sourceTime=clamp(Number(time)||0,0,Math.max(0,source.metadata.duration_sec-.04));sourceClock();const token=++sourceToken,media=source;
      if(media.kind==='audio'){const left=Math.floor(sourceTime/60)*60,url='/studio/sound-preview/'+media.id+'?start='+left+'&ordinal='+Number($('#studio-source-track').value);sourceOffset=left;if(v.dataset.url!==url){v.dataset.url=url;try{await loadSource(url);}catch(e){$('#studio-source-status').textContent=e.message;return;}}if(token!==sourceToken)return;v.currentTime=sourceTime-left;if(sourcePlaying)await v.play();return;}
      const left=Math.floor(sourceTime/15)*15;
      if(v.dataset.asset===String(media.id)&&sourceTime>=sourceOffset&&sourceTime<sourceOffset+v.duration-.04){v.currentTime=sourceTime-sourceOffset;if(sourcePlaying)await v.play();return;}
      v.pause();$('#studio-source-status').textContent='Preparing source preview…';
      try{let result=await ctx.api('/studio/proxy',{asset_id:media.id,start:sourceTime,mix:sourceMix(media),webm:true,quality:360});
        while(!result.ready){await new Promise(resolve=>setTimeout(resolve,500));if(token!==sourceToken)return;const jobs=await ctx.api('/api/jobs'),job=jobs[result.job_id];if(job?.status==='error')throw new Error(job.detail||job.error);if(job?.status==='complete')result=await ctx.api('/studio/proxy',{asset_id:media.id,start:left,mix:sourceMix(media),webm:true,quality:360});}
        if(token!==sourceToken||source!==media)return;sourceOffset=result.offset;v.dataset.asset=media.id;v.dataset.url=result.url;await loadSource(result.url);if(token!==sourceToken)return;v.currentTime=sourceTime-sourceOffset;$('#studio-source-status').textContent='Source times · Insert range at timeline playhead';if(sourcePlaying)await v.play();
      }catch(e){if(token===sourceToken)$('#studio-source-status').textContent=e.message;}
    }
    async function openSource(id){const a=ctx.asset(id);if(!a||a.kind==='image')return;ctx.pause();pause();sourceToken++;source=a;sourceTime=0;$('#studio-source-track').innerHTML=a.metadata.audio_streams.map(t=>'<option value="'+t.ordinal+'">'+esc(t.title||'Track '+(t.ordinal+1))+'</option>').join('')||'<option value="0">No audio</option>';$('#studio-source-track').disabled=!a.metadata.audio_streams.length;v.removeAttribute('data-asset');v.removeAttribute('data-url');$('#studio-source-name').textContent=a.name;$('#studio-source-in').value=0;$('#studio-source-out').value=a.metadata.duration_sec;$('#studio-source-scrub').max=a.metadata.duration_sec;tab(false);await seekSource(0);}
    $('#studio-bin-list').addEventListener('click',e=>{const b=e.target.closest('[data-source-preview]');if(b)openSource(Number(b.dataset.sourcePreview));});
    $('#studio-source-track').onchange=()=>{pause();v.removeAttribute('data-asset');v.removeAttribute('data-url');seekSource(sourceTime);};
    $('#studio-source-play').onclick=()=>{if(!source)return;sourcePlaying=!sourcePlaying;$('#studio-source-play').textContent=sourcePlaying?'Ⅱ Source':'▶ Source';if(sourcePlaying){ctx.pause();seekSource(sourceTime);}else v.pause();};
    $('#studio-source-scrub').oninput=e=>{pause();seekSource(e.target.value);};
    v.ontimeupdate=()=>{if(!sourcePlaying)return;sourceTime=sourceOffset+v.currentTime;sourceClock();};
    v.onended=()=>{if(sourcePlaying&&sourceTime<source.metadata.duration_sec-.1)seekSource(sourceTime+.04);else pause();};
    $('#studio-source-mark-in').onclick=()=>{$('#studio-source-in').value=sourceTime.toFixed(3);};$('#studio-source-mark-out').onclick=()=>{$('#studio-source-out').value=sourceTime.toFixed(3);};
    $('#studio-source-insert').onclick=()=>{if(!source)return;const start=Number($('#studio-source-in').value),end=Number($('#studio-source-out').value);if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>source.metadata.duration_sec+.001||end-start<.04)return ctx.message('Choose a source range of at least one frame.',true);try{ctx.insert(source,start,end,Number($('#studio-source-track').value));pause();}catch(e){ctx.message(e.message,true);}};
    function decorate(){
      const r=ctx.recipe();if(!r)return;
      for(const [selector,key]of [['#studio-video-track','video'],['#studio-caption-track','captions'],['#studio-overlay-track','overlays'],['#studio-source-audio-track','audio'],['#studio-marker-track','bookmarks']])decorateRow($(selector),key);
      document.querySelectorAll('.sound-lane').forEach(row=>decorateRow(row,row.dataset.trackKey));refresh();
    }
    function decorateRow(row,key){
      if(!row||!key)return;const state=E.track(ctx.recipe(),key),audio=key==='audio'||key.startsWith('sound:');row.style.height=(state.height||52)+'px';row.dataset.trackKey=key;row.classList.toggle('track-locked',!!state.locked);row.classList.toggle('track-disabled',audio?!E.audible(ctx.recipe(),key):!!state.hidden);
      if(row.hidden)return;const controls=document.createElement('span');controls.className='track-controls';
      const button=(flag,label,title)=>'<button data-track-key="'+esc(key)+'" data-track-flag="'+flag+'" aria-label="'+esc(title+' '+key)+'" aria-pressed="'+!!state[flag]+'" title="'+esc(title)+'">'+label+'</button>';
      controls.innerHTML=(audio?button('muted','M','Mute')+button('solo','S','Solo'):key!=='bookmarks'?button('hidden','V','Hide output'):'')+button('locked','L','Lock')+'<select aria-label="'+esc('Height '+key)+'" data-track-height="'+esc(key)+'">'+[38,52,80,120,180].map(n=>'<option value="'+n+'"'+((state.height||52)===n?' selected':'')+'>'+n+'</option>').join('')+'</select>';
      row.querySelector('.track-label')?.append(controls);row.querySelectorAll('.timeline-piece').forEach(piece=>{piece.style.height=((state.height||52)-8)+'px';});
    }
    $('#studio-timeline').addEventListener('click',e=>{const b=e.target.closest('[data-track-flag]');if(!b)return;e.stopPropagation();ctx.trackChange(b.dataset.trackKey,b.dataset.trackFlag);},true);
    $('#studio-timeline').addEventListener('change',e=>{if(e.target.dataset.trackHeight)ctx.trackChange(e.target.dataset.trackHeight,'height',Number(e.target.value));});
    $('#studio-timeline').addEventListener('pointerdown',e=>{if(e.target.closest('.track-controls'))e.stopPropagation();},true);
    return {decorate,refresh,resize,pause,openSource};
  };
})();
