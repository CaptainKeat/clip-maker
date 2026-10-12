"""Small route layer shared by the desktop app and its browser workspace."""
import dataclasses
import json
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from . import __version__, studio, updater
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
            "lmstudio_api_url":settings.lmstudio_api_url,"lmstudio_model":settings.lmstudio_model})
    elif path == "/studio/assets": handler._json(studio.assets(settings))
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
        handler._serve_file(studio.sound_preview(settings,m[1],start))
    elif (m:=re.fullmatch(r"/studio/overlay-preview/(\d+)",path)):
        start=float(urllib.parse.parse_qs(parsed.query).get("start",[0])[0])
        result=studio.proxy(settings,m[1],start,mix=[],webm=True)
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
    if path == "/studio/import":
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
        # Decoding is explicit and limited to a 90-second window near the cursor.
        asset_id,start = int(body["asset_id"]),float(body.get("start",0))
        import hashlib
        mix=body.get("mix")
        webm=body.get("webm") is True
        signature=hashlib.sha256(json.dumps([mix,webm],sort_keys=True).encode()).hexdigest()[:10]
        job_id = f"studio-proxy-{asset_id}-{int(start//60)}-{signature}"
        with jobs_lock: previous = jobs.get(job_id,{})
        if previous.get("status")=="complete":
            handler._json({"ready":True,**studio.proxy(settings,asset_id,start,mix=mix,webm=webm)})
        else:
            def make_proxy(settings_,progress=None):
                return studio.proxy(settings_,asset_id,start,progress,mix,webm)
            started = start_job(job_id,make_proxy,settings)
            handler._json({"started":started,"job_id":job_id})
    elif path == "/studio/settings":
        if "ai_enabled" in body:
            settings.ai_enabled = body["ai_enabled"] is True
        for key in ("whisper_cli","whisper_model","lmstudio_api_url","lmstudio_model"):
            if key in body:
                value = str(body[key]).strip()
                if not value or len(value)>2048: raise ValueError("Enter a valid "+key)
                if key=="lmstudio_api_url" and urllib.parse.urlparse(value).scheme not in {"http","https"}:
                    raise ValueError("Enter an HTTP model-server URL")
                setattr(settings,key,value)
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
