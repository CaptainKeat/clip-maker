"""Small route layer shared by the desktop app and its browser workspace."""
import dataclasses
import json
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from . import __version__, studio, updater, llm, whisper_setup, studio_transcribe, studio_waveforms, studio_presets, studio_speakers
from .settings import save_settings

UPDATE = {"status":"idle","current_version":__version__,"message":"Check for a new release when you are ready."}
STAGED = None
UPDATE_LOCK = threading.Lock()


def update_state():
    with UPDATE_LOCK:
        state = dict(UPDATE)
    state["installed"] = bool(getattr(sys,"frozen",False))
    state["receipt"] = updater.read_update_receipt()
    return state


def update_check():
    global UPDATE
    with UPDATE_LOCK:
        if UPDATE["status"] in {"checking","downloading"}: return
        UPDATE.update(status="checking",message="Checking GitHub releases…")
    try:
        release = updater.fetch_latest_release()
        new = updater.is_newer_version(release.version,__version__)
        with UPDATE_LOCK:
            UPDATE.update(status="available" if new else "current",message="An update is available." if new else "You have the latest release.",
                          release=dataclasses.asdict(release))
    except Exception as error:
        with UPDATE_LOCK: UPDATE.update(status="error",message=str(error))


def update_download():
    global STAGED
    with UPDATE_LOCK:
        if UPDATE["status"] != "available": return
        UPDATE.update(status="downloading",message="Downloading and verifying the Windows package…",progress=0)
        release_data = dict(UPDATE["release"])
    try:
        release_data["asset"] = updater.ReleaseAsset(**release_data["asset"])
        release = updater.ReleaseInfo(**release_data)
        def progress(received,total):
            with UPDATE_LOCK: UPDATE["progress"] = round(received/max(1,total)*100)
        STAGED = updater.stage_update(release,updater.application_install_dir(),progress)
        with UPDATE_LOCK: UPDATE.update(status="ready",message="Verified and ready. Save your edit before installing.",progress=100)
    except Exception as error:
        with UPDATE_LOCK: UPDATE.update(status="error",message=str(error))


def get(handler, parsed):
    path = parsed.path
    settings = handler.settings
    if path == "/studio/settings":
        handler._json({"version":__version__,"ai_enabled":settings.ai_enabled,"source_root":settings.source_root,"output_dir":settings.output_dir,
            "desktop":bool(getattr(sys,"frozen",False)),"updates_url":updater.GITHUB_RELEASES_URL,
            "whisper_cli":settings.whisper_cli,"whisper_model":settings.whisper_model,
            "language":settings.language,"whisper_threads":settings.whisper_threads,"whisper_device":settings.whisper_device,
            "whisper_quality":settings.whisper_quality,"whisper_prompt":settings.whisper_prompt,"whisper_translate":settings.whisper_translate,
            "lmstudio_api_url":settings.lmstudio_api_url,"lmstudio_model":settings.lmstudio_model})
    elif path == "/studio/assets": handler._json(studio.assets(settings))
    elif path == '/studio/speakers':handler._json(studio_speakers.status())
    elif (m:=re.fullmatch(r'/studio/speakers/([a-f0-9]{32})',path)):handler._json(studio_speakers.result(settings,m[1]))
    elif path == '/studio/caption-presets':handler._json(studio_presets.list_presets(settings))
    elif (m:=re.fullmatch(r'/studio/waveforms/(\d+)',path)):
        ordinal=int(urllib.parse.parse_qs(parsed.query).get('ordinal',[0])[0]);handler._json(studio_waveforms.read(settings,int(m[1]),ordinal))
    elif (m:=re.fullmatch(r'/studio/scrub-frame/(\d+)',path)):
        query=urllib.parse.parse_qs(parsed.query);time=float(query.get('time',[0])[0]);quality=int(query.get('quality',[360])[0]);handler._serve_file(studio_waveforms.frame(settings,int(m[1]),time,quality))
    elif path == "/studio/llm": handler._json({"profiles":llm.profiles(settings),"active":settings.llm_active_profile or "legacy"})
    elif path == "/studio/whisper": handler._json(whisper_setup.status(settings))
    elif path == "/studio/whisper/models": handler._json(whisper_setup.models(settings))
    elif (m:=re.fullmatch(r"/studio/transcripts/([a-f0-9]{32})",path)): handler._json(studio_transcribe.result(settings,m[1]))
    elif path == "/studio/projects": handler._json(studio.projects(settings))
    elif (m:=re.fullmatch(r"/studio/projects/([a-f0-9]{32})",path)): handler._json(studio.project(settings,m[1]))
    elif path == "/studio/exports": handler._json(studio.list_exports(settings))
    elif path == "/studio/updates": handler._json(update_state())
    elif (m:=re.fullmatch(r"/studio/media/(\d+)",path)): handler._serve_file(Path(studio.asset(settings,m[1])["path"]))
    elif (m:=re.fullmatch(r"/studio/thumbnail/(\d+)",path)): handler._serve_file(studio.thumbnail(settings,m[1]))
    elif path.startswith("/studio/proxy-media/"):
        name = path.removeprefix("/studio/proxy-media/")
        if not re.fullmatch(r"\d+-\d+-\d+-[a-f0-9]{10}\.(mp4|webm)",name): raise ValueError("Invalid preview copy")
        handler._serve_file(Path(settings.data_dir)/"studio-proxies"/name)
    elif (m:=re.fullmatch(r"/studio/sound-preview/(\d+)",path)):
        start=float(urllib.parse.parse_qs(parsed.query).get("start",[0])[0])
        ordinal=int(urllib.parse.parse_qs(parsed.query).get('ordinal',[0])[0])
        handler._serve_file(studio.sound_preview(settings,m[1],start,ordinal))
    elif (m:=re.fullmatch(r'/studio/dialogue-preview/(\d+)',path)):
        query=urllib.parse.parse_qs(parsed.query);start=float(query.get('start',[0])[0]);mix=json.loads(query.get('mix',['[]'])[0])
        handler._serve_file(studio.dialogue_preview(settings,int(m[1]),start,mix))
    elif (m:=re.fullmatch(r"/studio/overlay-preview/(\d+)",path)):
        start=float(urllib.parse.parse_qs(parsed.query).get("start",[0])[0])
        result=studio.overlay_preview(settings,m[1],start)
        handler._serve_file(Path(settings.data_dir)/"studio-proxies"/Path(result["url"]).name)
    elif (m:=re.fullmatch(r"/studio/export-media/([a-f0-9]{32})",path)):
        rows = studio.list_exports(settings)
        row = next((r for r in rows if r["id"]==m[1] and r["status"]=="complete"),None)
        if not row: raise ValueError("Export is not ready")
        handler._serve_file(Path(row["path"]))
    else: return False
    return True


def post(handler, parsed, start_job, jobs, jobs_lock):
    path = parsed.path
    settings = handler.settings
    if not path.startswith("/studio/"): return False
    if path == "/studio/upload":
        from .studio_upload import receive
        name=urllib.parse.parse_qs(parsed.query).get('name',[''])[0]
        length=int(handler.headers.get('Content-Length','0'))
        handler._json(receive(settings,handler.rfile,name,length))
        return True
    body = handler._body()
    if (m:=re.fullmatch(r'/studio/projects/([a-f0-9]{32})/speakers',path)):
        import uuid
        from dataclasses import replace
        p=studio.project(settings,m[1]);clip_id=str(body.get('clip_id',''));ordinal=int(body.get('audio_ordinal',0));count=int(body.get('speakers',0))
        clip=next((c for c in p['recipe']['clips'] if c['id']==clip_id),None)
        if not clip or ordinal not in {a['ordinal'] for a in studio.asset(settings,clip['asset_id'])['metadata']['audio_streams']} or not 0<=count<=8:raise ValueError('Choose a clip, audio source and zero to eight speakers')
        if not studio_speakers.status()['available']:raise ValueError('Bundled speaker models are unavailable')
        identity=uuid.uuid4().hex;studio_speakers.reserve(m[1],identity)
        start_job('studio-speakers-'+identity,studio_speakers.scan,replace(settings),m[1],clip_id,ordinal,count,identity)
        handler._json({'id':identity,'job_id':'studio-speakers-'+identity})
    elif (m:=re.fullmatch(r'/studio/speakers/([a-f0-9]{32})/cancel',path)):
        studio_speakers.cancel(settings,m[1]);handler._json({'cancelled':True})
    elif path == '/studio/caption-presets':handler._json(studio_presets.save(settings,body))
    elif path == '/studio/waveforms/cancel':
        studio_waveforms.cancel(str(body.get('id','')));handler._json({'cancelled':True})
    elif path == '/studio/waveforms/cancel-all':studio_waveforms.cancel_all();handler._json({'cancelled':True})
    elif (m:=re.fullmatch(r'/studio/waveforms/(\d+)/build',path)):
        ordinal=int(body.get('ordinal',0));asset_id=int(m[1]);studio_waveforms.read(settings,asset_id,ordinal)
        identity,new=studio_waveforms.reserve(asset_id,ordinal)
        if new:start_job('studio-waveform-'+identity,studio_waveforms.build,settings,asset_id,ordinal,identity)
        handler._json({'id':identity,'job_id':'studio-waveform-'+identity})
    elif path == "/studio/llm/save":
        llm.save(settings,body);save_settings(settings);handler._json({"profiles":llm.profiles(settings),"active":settings.llm_active_profile or "legacy"})
    elif path == "/studio/llm/activate":
        profile=llm.selected(settings,str(body.get("id","")));settings.llm_active_profile=profile["id"];save_settings(settings);handler._json({"active":profile["id"]})
    elif path == "/studio/llm/models": handler._json(llm.discover(settings,str(body.get("id",""))))
    elif path == "/studio/llm/test":
        handler._json({"message":llm.chat(settings,"Reply with one short sentence confirming the connection works.",max_tokens=100,timeout=30,profile_id=str(body.get("id","")))})
    elif (m:=re.fullmatch(r"/studio/projects/([a-f0-9]{32})/transcribe",path)):
        import uuid
        from dataclasses import replace
        if not whisper_setup.status(settings)["ready"]: raise ValueError("Whisper setup needs attention. Check Settings > Speech.")
        p=studio.project(settings,m[1]);clip_id=str(body.get("clip_id",""));ordinal=int(body.get("audio_ordinal",0))
        clip=next((c for c in p["recipe"]["clips"] if c["id"]==clip_id),None)
        if not clip or ordinal not in {a['ordinal'] for a in studio.asset(settings,clip['asset_id'])['metadata']['audio_streams']}:raise ValueError('Choose a clip and an available audio track')
        identity=uuid.uuid4().hex;studio_transcribe.reserve(m[1],identity)
        started=start_job("studio-transcribe-"+identity,studio_transcribe.transcribe,replace(settings),m[1],clip_id,ordinal,identity)
        handler._json({"started":started,"id":identity,"job_id":"studio-transcribe-"+identity})
    elif (m:=re.fullmatch(r"/studio/transcripts/([a-f0-9]{32})/cancel",path)):
        studio_transcribe.cancel(m[1]);handler._json({"cancelled":True})
    elif path == "/studio/import":
        handler._json(studio.import_media(settings,body.get("path")))
    elif path == "/studio/projects": handler._json(studio.create_project(settings,body))
    elif (m:=re.fullmatch(r"/studio/projects/([a-f0-9]{32})/save",path)):
        handler._json(studio.save_project(settings,m[1],body))
    elif (m:=re.fullmatch(r"/studio/projects/([a-f0-9]{32})/export",path)):
        studio.project(settings,m[1])
        job_id = "project-export-"+m[1]
        started = start_job(job_id,studio.render_project,settings,m[1])
        handler._json({"started":started,"job_id":job_id})
    elif (m:=re.fullmatch(r"/studio/projects/([a-f0-9]{32})/cancel",path)):
        studio.cancel_export(m[1]);handler._json({"cancelled":True})
    elif path == "/studio/proxy":
        asset_id,start = int(body["asset_id"]),float(body.get("start",0))
        import hashlib
        mix=body.get("mix");webm=body.get("webm") is True;quality=body.get("quality",540)
        cached=studio.proxy(settings,asset_id,start,mix=mix,webm=webm,quality=quality,check=True)
        signature=hashlib.sha256(json.dumps([mix,webm,quality],sort_keys=True).encode()).hexdigest()[:10]
        job_id=f"studio-proxy-{asset_id}-{cached['offset']}-{signature}"
        if cached['ready']:handler._json(cached)
        else:
            def make_proxy(settings_,progress=None):
                return studio.proxy(settings_,asset_id,start,progress,mix,webm,quality)
            started=start_job(job_id,make_proxy,settings)
            handler._json({"started":started,"job_id":job_id})
    elif path == "/studio/settings":
        speech=whisper_setup.validate(body)
        if "ai_enabled" in body:
            settings.ai_enabled = body["ai_enabled"] is True
        for key in ("lmstudio_api_url","lmstudio_model"):
            if key in body:
                value = str(body[key]).strip()
                if not value or len(value)>2048: raise ValueError("Enter a valid "+key)
                if key=="lmstudio_api_url" and urllib.parse.urlparse(value).scheme not in {"http","https"}:
                    raise ValueError("Enter an HTTP model-server URL")
                setattr(settings,key,value)
        for key,value in speech.items(): setattr(settings,key,value)
        save_settings(settings)
        handler._json({"ai_enabled":settings.ai_enabled})
    elif path == "/studio/updates/check":
        threading.Thread(target=update_check,daemon=True).start();handler._json(update_state())
    elif path == "/studio/updates/download":
        if not getattr(sys,"frozen",False): raise ValueError("Install updates from the Windows app")
        threading.Thread(target=update_download,daemon=True).start();handler._json(update_state())
    elif path == "/studio/updates/install":
        global STAGED
        with jobs_lock:
            if any(j.get("status")=="running" for j in jobs.values()): raise ValueError("Wait for the current job to finish before updating")
        if not STAGED or not getattr(sys,"frozen",False): raise ValueError("Download and verify an update first")
        if not hasattr(handler,"update_ready"): raise ValueError("Update installation is available in the desktop app")
        backup = updater.launch_staged_update(STAGED,__version__)
        handler._json({"restarting":True,"backup":str(backup)})
        handler.update_ready()
    else: return False
    return True
