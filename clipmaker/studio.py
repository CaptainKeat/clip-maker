"""Manual projects. No model imports or inference calls belong in this module."""
from __future__ import annotations

import json
import math
import hashlib
import re
import subprocess
import threading
import uuid
from pathlib import Path

from .db import database
from .media import CREATE_NO_WINDOW, probe_video
from .motion_captions import normalize_motion

SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_assets(
 id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 kind TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS studio_projects(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, recipe_json TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS studio_exports(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL, status TEXT NOT NULL,
 error TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""
VIDEO = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv"}
AUDIO = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
LOCK = threading.Lock()
PROCESSES = {}
CANCELLED = set()


def init(settings):
    with database(settings.db_path) as db:
        db.executescript(SCHEMA)


def number(value, default=0, low=0, high=1_000_000):
    try:
        n = float(value)
    except (ValueError, TypeError):
        n = default
    if not math.isfinite(n):
        raise ValueError("Project numbers must be finite")
    return round(max(low, min(high, n)), 4)


def _asset(row):
    result = dict(row)
    result["metadata"] = json.loads(result.pop("metadata_json"))
    result["url"] = f"/studio/media/{result['id']}"
    result["thumbnail"] = f"/studio/thumbnail/{result['id']}"
    return result


def assets(settings):
    init(settings)
    with database(settings.db_path) as db:
        return [_asset(row) for row in db.execute("SELECT * FROM studio_assets ORDER BY id DESC")]


def asset(settings, asset_id):
    init(settings)
    with database(settings.db_path) as db:
        row = db.execute("SELECT * FROM studio_assets WHERE id=?", (int(asset_id),)).fetchone()
    if not row:
        raise ValueError("Media item no longer exists")
    return _asset(row)


def import_media(settings, raw_path):
    path = Path(str(raw_path)).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Choose a media file")
    suffix = path.suffix.lower()
    if suffix not in VIDEO | AUDIO | IMAGES:
        raise ValueError("Choose a video, audio file or image")
    kind = "video" if suffix in VIDEO else "audio" if suffix in AUDIO else "image"
    metadata = probe_video(path, settings.ffprobe)
    if kind != "image" and metadata["duration_sec"] <= 0:
        raise ValueError("This file has no playable duration")
    init(settings)
    with database(settings.db_path) as db:
        db.execute("INSERT INTO studio_assets(path,name,kind,metadata_json) VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET metadata_json=excluded.metadata_json",
                   (str(path), path.name, kind, json.dumps(metadata)))
        row = db.execute("SELECT * FROM studio_assets WHERE path=?", (str(path),)).fetchone()
    return _asset(row)


def _project(row):
    data = dict(row)
    data["recipe"] = json.loads(data.pop("recipe_json"))
    return data


def projects(settings):
    init(settings)
    with database(settings.db_path) as db:
        return [_project(row) for row in db.execute("SELECT * FROM studio_projects ORDER BY updated_at DESC")]


def project(settings, project_id):
    init(settings)
    with database(settings.db_path) as db:
        row = db.execute("SELECT * FROM studio_projects WHERE id=?", (str(project_id),)).fetchone()
    if not row:
        raise ValueError("Project not found")
    result = _project(row)
    result["assets"] = assets(settings)
    result["frame"] = frame_size(result["recipe"], result["assets"])
    return result


def create_project(settings, body):
    init(settings)
    name = str(body.get("name") or "Untitled edit").strip()[:120]
    recipe = {"schema": 1, "clips": [], "captions": [], "overlays": [], "sounds": [], "markers": [], "editing":{"ripple":True,"snap":True,"tracks":{},"track_groups":{}},
              "aspect": "16:9", "resolution": 1080, "quality": "balanced"}
    if body.get("asset_id"):
        media = asset(settings, body["asset_id"])
        if media["kind"] == "video":
            recipe["clips"] = [new_clip(media)]
            recipe["aspect"] = "source"
    project_id = uuid.uuid4().hex
    with database(settings.db_path) as db:
        db.execute("INSERT INTO studio_projects(id,name,recipe_json) VALUES(?,?,?)", (project_id,name,json.dumps(recipe)))
    return project(settings, project_id)


def new_clip(media):
    return {"id": uuid.uuid4().hex[:12], "asset_id": media["id"], "start": 0,
            "end": media["metadata"]["duration_sec"], "speed": 1, "freeze": 0, "transition": "none",
            "fit": "fit", "audio": [{"ordinal": a["ordinal"], "enabled": a["ordinal"] == 0, "gain_db": 0}
                                     for a in media["metadata"]["audio_streams"]]}


def normalize_recipe(raw, media_list):
    by_id = {a["id"]: a for a in media_list}
    clips = []
    for c in (raw.get("clips") or [])[:500]:
        media = by_id.get(int(c.get("asset_id", -1)))
        if not media or media["kind"] != "video":
            raise ValueError("A timeline clip references unavailable video media")
        start = number(c.get("start"), 0, 0, media["metadata"]["duration_sec"])
        end = number(c.get("end"), media["metadata"]["duration_sec"], start, media["metadata"]["duration_sec"])
        if end - start < .04:
            raise ValueError("Each video piece must contain at least one frame")
        available = {a["ordinal"] for a in media["metadata"]["audio_streams"]}
        mixes = [{"ordinal": int(a["ordinal"]), "enabled": bool(a.get("enabled")), "gain_db": number(a.get("gain_db"),0,-36,12),"dialogue":a.get("dialogue") is True}
                 for a in (c.get("audio") or []) if int(a.get("ordinal",-1)) in available]
        clips.append({"id": str(c.get("id") or uuid.uuid4().hex[:12])[:80],"asset_id":media["id"],"start":start,"end":end,
            "speed": number(c.get("speed"),1,.25,4), "freeze":number(c.get("freeze"),0,0,30),
            "transition":c.get("transition") if c.get("transition") in {"fade","none"} else "none",
            "fit":"fill" if c.get("fit")=="fill" else "fit","audio":mixes,
            "audio_fade_in":number(c.get("audio_fade_in"),0,0,(end-start)/max(.25,number(c.get('speed'),1,.25,4))/2),
            "audio_fade_out":number(c.get("audio_fade_out"),0,0,(end-start)/max(.25,number(c.get('speed'),1,.25,4))/2)})
    duration = sum(clip_duration(c) for c in clips)
    captions = []
    for c in (raw.get("captions") or [])[:5000]:
        start = number(c.get("start"),0,0,duration)
        end = number(c.get("end"),start+.5,start,duration)
        text = str(c.get("text") or "").strip()[:300]
        if text and end > start:
            captions.append({"id":str(c.get("id") or uuid.uuid4().hex[:12])[:80], "text":text,"start":start,"end":end,
                             "speaker":str(c.get("speaker") or "")[:60],"lane":str(c.get('lane') or '')[:80],"motion":normalize_motion(c.get("motion"),start,end)})
    def extras(kind):
        result = []
        for item in (raw.get(kind) or [])[:100]:
            media = by_id.get(int(item.get("asset_id",-1)))
            if not media or (kind == "sounds" and not media["metadata"]["audio_streams"]) or (kind == "overlays" and media["kind"] == "audio"):
                raise ValueError("A timeline layer references unavailable media")
            t = number(item.get("time"),0,0,duration)
            source_start=number(item.get('source_start'),0,0,max(0,media['metadata']['duration_sec']-.04)) if media['kind']!='image' else 0
            length = number(item.get("duration"),3,.04,max(.04,duration-t))
            if kind=='sounds':length=min(length,max(.04,media['metadata']['duration_sec']-source_start))
            available={a['ordinal'] for a in media['metadata']['audio_streams']}
            ordinal=int(item.get('audio_ordinal',0))
            if kind=='sounds' and ordinal not in available: raise ValueError('A soundtrack references an unavailable audio track')
            result.append({"id":str(item.get("id") or uuid.uuid4().hex[:12])[:80],"asset_id":media["id"],"time":t,"duration":length,
                "x":number(item.get("x"),50,0,100),"y":number(item.get("y"),50,0,100),"size":number(item.get("size"),.35,.05,2),
                "gain_db":number(item.get("gain_db"),0,-36,12),"source_start":source_start,
                "name":str(item.get('name') or '')[:80],"lane":str(item.get('lane') or '')[:80],
                "link_id":str(item.get('link_id') or '')[:80],
                "speaker_source":str(item.get('speaker_source') or '')[:160],
                "audio_ordinal":ordinal if kind=='sounds' else 0,
                "fade_in":number(item.get('fade_in'),0,0,length/2),"fade_out":number(item.get('fade_out'),0,0,length/2),
                "role":"dialogue" if item.get('role')=='dialogue' else 'other',
                "duck":item.get('duck') is True and item.get('role')!='dialogue',
                "duck_ratio":number(item.get('duck_ratio'),6,1,20)})
        return result
    result = {"schema":1,"clips":clips,"captions":captions,"overlays":extras("overlays"),"sounds":extras("sounds"),
        "markers":[{"time":number(m.get("time"),0,0,duration),"label":str(m.get("label") or "Bookmark")[:100]} for m in (raw.get("markers") or [])[:1000]],
        "aspect":raw.get("aspect") if raw.get("aspect") in {"source","16:9","9:16","1:1"} else "16:9",
        "resolution":int(raw.get("resolution")) if raw.get("resolution") in {720,1080,2160} else 1080,
        "quality":raw.get("quality") if raw.get("quality") in {"fast","balanced","high"} else "balanced",
        "editing":{"ripple":(raw.get('editing') or {}).get('ripple',True) is not False,"snap":(raw.get('editing') or {}).get('snap',True) is not False,
                   "lanes":[{'id':str(t.get('id') or '')[:80],'name':str(t.get('name') or 'Track')[:80],'kind':t['kind']} for t in (raw.get('editing') or {}).get('lanes',[])[:32] if isinstance(t,dict) and t.get('kind') in {'video','audio','caption'} and t.get('id')],
                   "tracks":{str(key)[:90]:{**{flag:value.get(flag) is True for flag in ("locked","hidden","muted","solo")},"height":number(value.get("height"),52,38,180)} for key,value in ((raw.get("editing") or {}).get("tracks") or {}).items() if isinstance(value,dict) and (key in ("video","audio","captions","overlays","bookmarks") or str(key).startswith(("sound:","video:","caption:")))},
                   "track_groups":{name:(raw.get('editing') or {}).get('track_groups',{}).get(name) is True for name in ('captions','effects','video','audio','bookmarks')}}}
    # Preserve optional flags without changing old clip dictionaries on a no-op save.
    for kind in ('clips','captions','overlays','sounds'):
        source={str(c.get('id') or ''):c for c in (raw.get(kind) or []) if isinstance(c,dict)}
        for item in result[kind]:
            original=source.get(item['id'],{})
            if 'disabled' in original:item['disabled']=original.get('disabled') is True
            if original.get('group_id'):item['group_id']=str(original['group_id'])[:80]
    from .studio_groups import normalize_groups
    result['editing']['groups']=normalize_groups((raw.get('editing') or {}).get('groups'),result)
    return result



def clip_duration(c):
    return (float(c["end"])-float(c["start"]))/float(c.get("speed") or 1)+float(c.get("freeze") or 0)


def frame_size(recipe, media_list):
    resolution = recipe.get("resolution",1080)
    if recipe["aspect"] == "source" and recipe["clips"]:
        media = next(a for a in media_list if a["id"]==recipe["clips"][0]["asset_id"])
        w,h = media["metadata"]["width"],media["metadata"]["height"]
        return [max(2,min(8192,w//2*2)),max(2,min(8192,h//2*2))]
    if recipe["aspect"] == "9:16": return [resolution,round(resolution*16/9)//2*2]
    if recipe["aspect"] == "1:1": return [resolution,resolution]
    return [round(resolution*16/9)//2*2,resolution]


def save_project(settings, project_id, body):
    old = project(settings,project_id)
    recipe = normalize_recipe(body.get("recipe") or {},old["assets"])
    revision = int(body.get("revision",0))
    with database(settings.db_path) as db:
        result = db.execute("UPDATE studio_projects SET name=?,recipe_json=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND revision=?",
            (str(body.get("name") or "Untitled edit").strip()[:120],json.dumps(recipe),project_id,revision))
        if result.rowcount != 1:
            raise ValueError("This project was saved in another window. Reopen it before saving again.")
    return project(settings,project_id)


def thumbnail(settings, asset_id):
    media = asset(settings,asset_id)
    cache = Path(settings.data_dir)/"studio-thumbnails"
    cache.mkdir(exist_ok=True)
    path = Path(media["path"])
    stamp = path.stat().st_mtime_ns
    destination = cache/f"{media['id']}-{stamp}.jpg"
    if not destination.exists() and media["kind"] != "audio":
        args = [settings.ffmpeg,"-hide_banner","-loglevel","error","-y"]
        if media["kind"] == "video": args += ["-ss",str(min(5,media["metadata"]["duration_sec"]*.1))]
        subprocess.run(args+["-i",media["path"],"-frames:v","1","-vf","scale=320:180:force_original_aspect_ratio=decrease",str(destination)],
                       check=True,capture_output=True,creationflags=CREATE_NO_WINDOW)
    return destination


def proxy(settings, asset_id, start, progress=None, mix=None, webm=False, quality=540, check=False):
    media = asset(settings,asset_id)
    if media["kind"] != "video": raise ValueError("Preview copies are for video files")
    left = int(number(start,0,0,max(0,media["metadata"]["duration_sec"]-.04))//15*15)
    cache = Path(settings.data_dir)/"studio-proxies"
    cache.mkdir(exist_ok=True)
    stamp = Path(media["path"]).stat().st_mtime_ns
    available = {a["ordinal"] for a in media["metadata"]["audio_streams"]}
    audio = ([{"ordinal":0,"enabled":True,"gain_db":0}] if mix is None and 0 in available else mix or [])
    audio = [{"ordinal":int(a["ordinal"]),"gain_db":number(a.get("gain_db"),0,-36,12)} for a in audio if a.get("enabled") and int(a.get("ordinal",-1)) in available]
    quality = int(quality) if quality in (360,540,720) else 540
    signature = hashlib.sha256(json.dumps([audio,quality,"seek-v3"],sort_keys=True).encode()).hexdigest()[:10]
    output = cache/f"{asset_id}-{stamp}-{left}-{signature}.{'webm' if webm else 'mp4'}"
    if check:
        return {"ready":output.exists(),"url":f"/studio/proxy-media/{output.name}","offset":left,"duration":20,"quality":quality}
    if not output.exists():
        partial = output.with_name(output.stem+"."+uuid.uuid4().hex+".part"+output.suffix)
        command = [settings.ffmpeg,"-hide_banner","-loglevel","error","-y","-ss",str(left),"-threads","2","-i",media["path"],"-t","20"]
        graph=[]
        for i,a in enumerate(audio): graph.append(f"[0:a:{a['ordinal']}]aresample=48000,volume={10**(a['gain_db']/20)}[a{i}]")
        if audio:
            graph.append("".join(f"[a{i}]" for i in range(len(audio)))+f"amix=inputs={len(audio)}:normalize=0,alimiter=limit=.95[a]")
            command += ["-filter_complex",";".join(graph),"-map","0:v","-map","[a]"]
        else: command += ["-map","0:v","-an"]
        command += ["-vf",f"scale=w='min(iw,{quality*16//9//2*2})':h='min(ih,{quality})':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30","-pix_fmt","yuv420p","-threads","2","-g","30","-keyint_min","30"]
        command += (["-c:v","libvpx-vp9","-deadline","realtime","-cpu-used","8","-row-mt","1","-crf","35","-b:v","0","-c:a","libopus"] if webm
                    else ["-c:v","libx264","-preset","veryfast","-crf","25","-c:a","aac","-movflags","+faststart"])
        subprocess.run(command+[str(partial)],check=True,capture_output=True,creationflags=CREATE_NO_WINDOW)
        partial.replace(output)
    return {"ready":True,"url":f"/studio/proxy-media/{output.name}","offset":left,"duration":20,"quality":quality}


def overlay_preview(settings,asset_id,start=0):
    media=asset(settings,asset_id);left=int(number(start,0,0,media['metadata']['duration_sec'])//60*60)
    root=Path(settings.data_dir)/'studio-proxies';root.mkdir(exist_ok=True)
    output=root/f"{asset_id}-{Path(media['path']).stat().st_mtime_ns}-{left}-0000000000.webm"
    if not output.exists():
        partial=output.with_name(output.stem+'.'+uuid.uuid4().hex+'.part.webm')
        subprocess.run([settings.ffmpeg,'-v','error','-y','-ss',str(left),'-threads','2','-i',media['path'],'-t','90','-an','-vf','scale=960:540:force_original_aspect_ratio=decrease:force_divisible_by=2','-c:v','libvpx-vp9','-deadline','realtime','-cpu-used','8','-row-mt','1','-threads','2','-g','30','-crf','35','-b:v','0',str(partial)],check=True,capture_output=True,creationflags=CREATE_NO_WINDOW)
        partial.replace(output)
    return {'url':f'/studio/proxy-media/{output.name}','offset':left}


def sound_preview(settings,asset_id,start=0,ordinal=0):
    media=asset(settings,asset_id)
    if ordinal not in {a['ordinal'] for a in media["metadata"]["audio_streams"]}: raise ValueError("Choose an available audio track")
    root=Path(settings.data_dir)/"studio-proxies";root.mkdir(exist_ok=True)
    left=int(max(0,float(start))//60*60)
    output=root/f"sound-{asset_id}-{Path(media['path']).stat().st_mtime_ns}-{left}-{ordinal}.webm"
    if not output.exists():
        partial=output.with_name(output.stem+"."+uuid.uuid4().hex+".part.webm")
        subprocess.run([settings.ffmpeg,"-hide_banner","-loglevel","error","-y","-ss",str(left),"-i",media["path"],"-t","90","-vn","-map",f"0:a:{ordinal}","-c:a","libopus",str(partial)],
                       check=True,capture_output=True,creationflags=CREATE_NO_WINDOW)
        partial.replace(output)
    return output


def dialogue_preview(settings,asset_id,start,mix):
    media=asset(settings,asset_id);available={a['ordinal'] for a in media['metadata']['audio_streams']}
    tracks=[{'ordinal':int(a['ordinal']),'gain_db':number(a.get('gain_db'),0,-36,12)} for a in mix if a.get('enabled') and a.get('dialogue') and int(a['ordinal']) in available]
    if not tracks:raise ValueError('Mark a dialogue track for ducking')
    root=Path(settings.data_dir)/'studio-proxies';root.mkdir(exist_ok=True);left=int(max(0,start)//60*60)
    key=hashlib.sha256(json.dumps(tracks,sort_keys=True).encode()).hexdigest()[:10]
    output=root/f'dialogue-{asset_id}-{Path(media["path"]).stat().st_mtime_ns}-{left}-{key}.webm'
    if not output.exists():
        partial=output.with_name(output.stem+'.'+uuid.uuid4().hex+'.part.webm')
        graph=[f"[0:a:{a['ordinal']}]aresample=48000,volume={10**(a['gain_db']/20)}[a{i}]" for i,a in enumerate(tracks)]
        graph.append(''.join(f'[a{i}]' for i in range(len(tracks)))+f'amix=inputs={len(tracks)}:normalize=0,alimiter=limit=.95[out]')
        subprocess.run([settings.ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(left),'-i',media['path'],'-t','90','-vn','-filter_complex',';'.join(graph),'-map','[out]','-c:a','libopus',str(partial)],check=True,capture_output=True,creationflags=CREATE_NO_WINDOW)
        partial.replace(output)
    return output


def list_exports(settings):
    init(settings)
    with database(settings.db_path) as db:
        return [dict(row) for row in db.execute("SELECT e.*,p.name FROM studio_exports e LEFT JOIN studio_projects p ON p.id=e.project_id ORDER BY e.created_at DESC")]


def cancel_export(project_id):
    with LOCK:
        CANCELLED.add(project_id)
        process = PROCESSES.get(project_id)
        if process and process.poll() is None: process.terminate()


def _run(settings, project_id, args, log, total, progress, baseline=0, fraction=1):
    command = [settings.ffmpeg,"-hide_banner","-loglevel","error","-y"]+args+["-progress","pipe:1","-nostats"]
    with log.open("ab") as error_log:
        with LOCK:
            if project_id in CANCELLED: raise RuntimeError("Export cancelled")
            process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=error_log,text=True,creationflags=CREATE_NO_WINDOW)
            PROCESSES[project_id] = process
        try:
            for line in process.stdout:
                if line.startswith("out_time_us=") and progress:
                    # FFmpeg reports N/A before some layered graphs produce frames.
                    try: value = max(0,float(line.partition("=")[2].strip()))/1_000_000
                    except ValueError: continue
                    progress(f"Project export {min(99,round(baseline+fraction*100*value/max(.1,total)))}%")
            process.wait()
            with LOCK:
                if project_id in CANCELLED: raise RuntimeError("Export cancelled")
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill();process.wait()
            process.stdout.close()
            with LOCK: PROCESSES.pop(project_id,None)
        if process.returncode:
            raise RuntimeError("Video export failed. "+log.read_text(encoding="utf-8",errors="replace")[-1200:])


def render_project(settings, project_id, progress=None):
    from .studio_render import export_recipe
    data = project(settings,project_id)
    if not data["recipe"]["clips"]: raise ValueError("Add a video to the timeline first")
    with LOCK: CANCELLED.discard(project_id)
    export_id = uuid.uuid4().hex
    name = re.sub(r"[^\w .-]","-",data["name"]).strip(" .")[:80] or "edit"
    directory = Path(settings.output_dir)/"Projects"/name
    directory.mkdir(parents=True,exist_ok=True)
    destination = directory/f"{name}-{export_id[:8]}.mp4"
    work = Path(settings.data_dir)/"studio-renders"/export_id
    work.mkdir(parents=True)
    with database(settings.db_path) as db:
        db.execute("INSERT INTO studio_exports(id,project_id,path,status) VALUES(?,?,?,'rendering')",(export_id,project_id,str(destination)))
    try:
        export_recipe(settings,data,destination,work,progress)
        with database(settings.db_path) as db:
            db.execute("UPDATE studio_exports SET status='complete' WHERE id=?",(export_id,))
        return str(destination)
    except Exception as error:
        with database(settings.db_path) as db:
            db.execute("UPDATE studio_exports SET status='failed',error=? WHERE id=?",(str(error),export_id))
        raise
    finally:
        with LOCK: CANCELLED.discard(project_id)
