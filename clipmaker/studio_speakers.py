"""Cancellable background speaker scan. Results are reviewed before editing."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from . import studio, speaker_audio
from .media import CREATE_NO_WINDOW

LOCK=threading.Lock();ACTIVE={};CANCELLED=set()


def folder(settings,identity):
    import re
    if not re.fullmatch('[a-f0-9]{32}',identity):raise ValueError('Invalid speaker scan identity')
    return Path(settings.data_dir)/'studio-speakers'/identity


def status():
    return {'available':all((speaker_audio.models()/name).is_file() for name in speaker_audio.HASHES),'model':'WeSpeaker ResNet34','local':True}


def result(settings,identity):
    path=folder(settings,identity)/'result.json'
    if not path.exists():raise ValueError('Speaker scan is not ready')
    return json.loads(path.read_text(encoding='utf-8'))


def reserve(project_id,identity):
    with LOCK:
        if project_id in ACTIVE:raise ValueError('This project already has a speaker scan running')
        ACTIVE[project_id]=identity


def cancel(settings,identity):
    with LOCK:
        if identity not in ACTIVE.values():return
        CANCELLED.add(identity)
    path=folder(settings,identity)
    if path.is_dir():(path/'cancel').touch()


def scan(settings,project_id,clip_id,ordinal,speakers,identity,progress=None):
    root=folder(settings,identity);process=None
    try:
        root.mkdir(parents=True,exist_ok=False);p=studio.project(settings,project_id)
        with LOCK:
            if identity in CANCELLED:(root/'cancel').touch()
        clip=next(c for c in p['recipe']['clips'] if c['id']==clip_id);media=studio.asset(settings,clip['asset_id'])
        cfg={'id':identity,'project_id':project_id,'revision':p['revision'],'clip':clip,'ordinal':ordinal,'speakers':speakers,
            'source':media['path'],'start':clip['start'],'end':clip['end'],'speed':clip['speed'],'ffmpeg':settings.ffmpeg}
        config=root/'config.json';config.write_text(json.dumps(cfg),encoding='utf-8')
        command=[sys.executable,'--speaker-worker',str(config)] if getattr(sys,'frozen',False) else [sys.executable,'-m','clipmaker.speaker_audio',str(config)]
        with (root/'worker.log').open('ab') as log:
            process=subprocess.Popen(command,stdout=log,stderr=log,creationflags=CREATE_NO_WINDOW)
            cancelling=None
            while process.poll() is None:
                if (root/'cancel').exists():
                    cancelling=cancelling or time.monotonic()
                    if time.monotonic()-cancelling>15:process.kill()
                path=root/'progress.txt'
                if progress:progress(path.read_text(encoding='utf-8') if path.exists() else 'Starting offline speaker scan…')
                time.sleep(.3)
        if (root/'cancel').exists():raise RuntimeError('Speaker scan cancelled; saved edit unchanged')
        if process.returncode:raise RuntimeError('Speaker scan failed. Local diagnostic: '+str(root/'worker.log'))
        data=result(settings,identity)
        for track in data['tracks']:track['asset']=studio.import_media(settings,track['path'])
        (root/'result.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
        return identity
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=5)
        with LOCK:ACTIVE.pop(project_id,None);CANCELLED.discard(identity)
