(() => {
  'use strict';
  const $=s=>document.querySelector(s);
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await r.json();if(!r.ok)throw new Error(result.error||r.statusText);return result;}
  let profiles=[],active='',editing='',dirty=false,busy=false,speechContext=null,transcription=null,polling=null,draft=null;
  const defaults={lmstudio:'http://127.0.0.1:1234/v1',ollama:'http://127.0.0.1:11434/v1',openai:'https://api.openai.com/v1',openrouter:'https://openrouter.ai/api/v1',anthropic:'https://api.anthropic.com/v1',gemini:'https://generativelanguage.googleapis.com/v1beta/openai',compatible:'http://127.0.0.1:8080/v1'};
  function form(){return {id:editing||undefined,name:$('#connection-name').value,location:$('#connection-location').value,kind:$('#connection-kind').value,url:$('#connection-url').value,model:$('#connection-model').value,allow_http:$('#connection-http').checked,api_key:$('#connection-key').value,clear_key:$('#connection-clear-key').checked};}
  function location(){ $('#connection-http-row').hidden=$('#connection-location').value!=='remote'; }
  function edit(profile){
    editing=profile?.id||'';dirty=false;$('#connection-form-title').textContent=editing?'Edit connection':'New connection';
    for(const key of ['name','location','kind','url','model'])$('#connection-'+key).value=profile?.[key]||({name:'',location:'local',kind:'lmstudio',url:defaults.lmstudio,model:''}[key]);
    $('#connection-key').value='';$('#connection-clear-key').checked=false;$('#connection-http').checked=profile?.allow_http===true;
    $('#connection-key-state').textContent=profile?.has_key?'API key saved for this Windows account.':'No API key saved.';
    $('#connection-models').innerHTML='';$('#connection-status').textContent='Save changes before checking or using this connection.';location();
  }
  function render(){
    $('#connection-profiles').innerHTML=profiles.map(p=>'<button class="connection-profile '+(p.id===active?'active':'')+'" data-connection="'+p.id+'"><strong>'+esc(p.name)+(p.id===active?' · Active':'')+'</strong><small>'+esc(p.location)+' · '+esc(p.kind)+'<br>'+esc(p.model||'Choose a model')+'</small></button>').join('');
    document.querySelectorAll('[data-connection]').forEach(b=>b.onclick=()=>{if(busy)return;edit(profiles.find(p=>p.id===b.dataset.connection));});
  }
  async function load(){const data=await api('/studio/llm');profiles=data.profiles;active=data.active;render();return data;}
  async function connectionAction(action){
    if(busy)return;busy=true;document.querySelectorAll('#settings-ai button').forEach(b=>b.disabled=true);
    try{await action();}catch(e){$('#connection-status').textContent=e.message;}
    finally{busy=false;document.querySelectorAll('#settings-ai button').forEach(b=>b.disabled=false);}
  }
  function saved(){if(!editing||dirty)throw new Error('Save this connection’s changes first.');return editing;}
  $('#connection-new').onclick=()=>edit(null);
  for(const key of ['name','url','model','key','clear-key','http'])$('#connection-'+key).addEventListener('input',()=>{dirty=true;});
  $('#connection-kind').onchange=()=>{dirty=true;const kind=$('#connection-kind').value;$('#connection-url').value=defaults[kind];$('#connection-key').value='';if(['openai','openrouter','anthropic','gemini'].includes(kind))$('#connection-location').value='api';location();};
  $('#connection-location').onchange=()=>{dirty=true;location();if($('#connection-location').value==='remote')$('#connection-url').value='https://192.168.1.50:1234/v1';};
  $('#connection-save').onclick=()=>connectionAction(async()=>{const body=form();const result=await api('/studio/llm/save',body);profiles=result.profiles;active=result.active;const profile=profiles.find(p=>p.id===editing)||profiles[profiles.length-1];render();edit(profile);$('#connection-status').textContent='Connection saved. Choose Use this connection to activate it.';});
  $('#connection-use').onclick=()=>connectionAction(async()=>{await api('/studio/llm/activate',{id:saved()});active=editing;render();$('#connection-status').textContent='Active connection selected. The model was not loaded or started.';});
  $('#connection-check').onclick=()=>connectionAction(async()=>{const result=await api('/studio/llm/models',{id:saved()});$('#connection-models').innerHTML=result.models.map(id=>'<option value="'+esc(id)+'"></option>').join('');$('#connection-status').textContent=result.message+' '+result.models.length+' model(s) listed.'+(!result.selected_listed&&$('#connection-model').value?' The selected identifier is not listed. Choose a listed model or verify its custom alias.':'');});
  $('#connection-test').onclick=()=>connectionAction(async()=>{const result=await api('/studio/llm/test',{id:saved()});$('#connection-status').textContent='Response received: '+result.message;});
  async function checkSpeech(){const status=await api('/studio/whisper');$('#speech-status').textContent=[status.message,...status.warnings].join(' ');$('#speech-engine-summary').textContent=status.message;$('#speech-source').value=status.selection||'auto';}
  $('#speech-source').onchange=()=>{$('#speech-advanced').open=$('#speech-source').value==='custom';};
  $('#speech-check').onclick=()=>checkSpeech().catch(e=>$('#speech-status').textContent=e.message);
  $('#speech-save').onclick=async()=>{try{await api('/studio/settings',{whisper_cli:$('#speech-source').value==='bundled'?'bundled':$('#studio-ai-whisper_cli').value,whisper_model:$('#speech-source').value==='bundled'?'bundled':$('#studio-ai-whisper_model').value,language:$('#speech-language').value,whisper_threads:Number($('#speech-threads').value),whisper_device:$('#speech-device').value,whisper_quality:$('#speech-quality').value,whisper_prompt:$('#speech-prompt').value,whisper_translate:$('#speech-translate').checked});await checkSpeech();}catch(e){$('#speech-status').textContent=e.message;}};
  $('#whisper-discover').onclick=async()=>{try{const models=await api('/studio/whisper/models');$('#whisper-models').innerHTML=models.map(m=>'<option value="'+esc(m.path)+'">'+esc(m.name)+' · '+Math.round(m.bytes/1024/1024)+' MB</option>').join('');$('#speech-status').textContent=models.length+' downloaded model(s) found. Choose one in Model file, then save.';}catch(e){$('#speech-status').textContent=e.message;}};
  for(const kind of ['cli','model'])$('#whisper-pick-'+kind).onclick=()=>{if(window.desktopBridge)window.desktopBridge.pickWhisper(kind);else $('#speech-status').textContent='Use the installed app to browse files, or enter a local file path here.';};
  function displayResult(result){
    draft=result;$('#speech-review').hidden=false;$('#speech-track').disabled=false;$('#speech-start').disabled=false;$('#speech-cancel').hidden=true;
    $('#speech-progress').textContent=result.cues.length?'Draft ready. Correct the text before adding it to your edit.':'No speech detected on this track. Try a different track or check the source audio.';
    $('#speech-apply').disabled=!result.cues.length;
    $('#speech-cues').innerHTML=result.cues.map((c,i)=>'<label class="speech-cue"><span>'+c.start.toFixed(2)+'–'+c.end.toFixed(2)+'s</span><textarea data-cue="'+i+'" aria-label="Caption cue '+(i+1)+'">'+esc(c.text)+'</textarea></label>').join('');
  }
  async function speechPoll(){
    if(!transcription)return;
    try{const jobs=await api('/api/jobs'),job=jobs['studio-transcribe-'+transcription];
      if(!job)return;
      $('#speech-progress').textContent=job.detail||job.stage;
      if(job.status==='complete'){const result=await api('/studio/transcripts/'+transcription);localStorage.setItem('clipmaker-transcript-'+speechContext.projectId,transcription);localStorage.removeItem('clipmaker-transcription-running-'+speechContext.projectId);clearInterval(polling);polling=null;transcription=null;displayResult(result);}
      else if(job.status==='error'){localStorage.removeItem('clipmaker-transcription-running-'+speechContext.projectId);clearInterval(polling);polling=null;transcription=null;$('#speech-progress').textContent=job.error||job.detail;$('#speech-start').disabled=false;$('#speech-track').disabled=false;$('#speech-cancel').hidden=true;}
    }catch(e){$('#speech-progress').textContent='Connection interrupted; the transcript and log remain local. '+e.message;}
  }
  async function openSpeech(context){
    if(transcription)throw new Error('A transcription is already running');
    if(!context.media.metadata.audio_streams.length)throw new Error('This clip has no audio track');
    await context.save();speechContext=context;draft=null;$('#speech-review').hidden=true;$('#speech-cues').innerHTML='';$('#speech-start').disabled=false;$('#speech-track').disabled=false;
    $('#speech-source').textContent=context.media.name+' · source '+context.clip.start.toFixed(2)+'–'+context.clip.end.toFixed(2)+'s';
    $('#speech-track').innerHTML=context.media.metadata.audio_streams.map(t=>'<option value="'+t.ordinal+'">Track '+(t.ordinal+1)+(t.title?' · '+esc(t.title):'')+'</option>').join('');
    const enabled=context.clip.audio.find(t=>t.enabled);if(enabled)$('#speech-track').value=enabled.ordinal;
    const engine=await api('/studio/whisper');$('#speech-progress').textContent=engine.message+' Only the selected audio track is transcribed. Review and apply when ready.';
    $('#speech-dialog').showModal();
    const pending=localStorage.getItem('clipmaker-transcription-running-'+context.projectId);
    if(pending){const jobs=await api('/api/jobs');if(jobs['studio-transcribe-'+pending]){transcription=pending;$('#speech-start').disabled=true;$('#speech-track').disabled=true;$('#speech-cancel').hidden=false;polling=setInterval(speechPoll,1000);await speechPoll();}else localStorage.removeItem('clipmaker-transcription-running-'+context.projectId);}
  }
  $('#speech-start').onclick=async()=>{
    if(transcription)return;
    $('#speech-start').disabled=true;$('#speech-track').disabled=true;$('#speech-review').hidden=true;
    try{const result=await api('/studio/projects/'+speechContext.projectId+'/transcribe',{clip_id:speechContext.clip.id,audio_ordinal:Number($('#speech-track').value)});transcription=result.id;localStorage.setItem('clipmaker-transcription-running-'+speechContext.projectId,transcription);$('#speech-cancel').hidden=false;$('#speech-progress').textContent='Starting local Whisper…';polling=setInterval(speechPoll,1000);}
    catch(e){$('#speech-progress').textContent=e.message;$('#speech-start').disabled=false;$('#speech-track').disabled=false;}
  };
  $('#speech-cancel').onclick=async()=>{if(transcription){await api('/studio/transcripts/'+transcription+'/cancel',{});$('#speech-progress').textContent='Cancelling transcription. Existing edits stay unchanged.';}};
  $('#speech-close').onclick=()=>{if(transcription){$('#speech-progress').textContent='Cancel or finish the transcription before closing this review.';return;}$('#speech-dialog').close();};
  $('#speech-dialog').addEventListener('cancel',e=>{if(transcription)e.preventDefault();});
  $('#speech-apply').onclick=async()=>{if($('#speech-apply').disabled)return;$('#speech-apply').disabled=true;try{const cues=draft.cues.map((cue,i)=>({...cue,text:document.querySelector('[data-cue="'+i+'"]').value}));await speechContext.apply({...draft,cues});$('#speech-dialog').close();}catch(e){$('#speech-progress').textContent=e.message;}finally{$('#speech-apply').disabled=false;}};
  window.ClipMakerConnections={
    async refresh(data){await load();if(!editing)edit(profiles.find(p=>p.id===active));for(const [id,key] of [['language','language'],['threads','whisper_threads'],['device','whisper_device'],['quality','whisper_quality'],['prompt','whisper_prompt']])$('#speech-'+id).value=data[key];$('#speech-translate').checked=data.whisper_translate;await checkSpeech();},
    isRunning:()=>!!transcription,
    transcribe:openSpeech,
    async review(context){const identity=localStorage.getItem('clipmaker-transcript-'+context.projectId);if(!identity)throw new Error('No previous transcript for this project. Transcribe a clip first.');const result=await api('/studio/transcripts/'+identity);if(result.status!=='complete')throw new Error(result.error||'The last transcription is not complete');await openSpeech(context);displayResult(result);}
  };
})();
