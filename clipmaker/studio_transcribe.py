"""Cancellable, local transcription produces reviewable cues without changing an edit."""
import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from . import studio, whisper_setup
from .captions import transcript_words
from .media import CREATE_NO_WINDOW

LOCK=threading.Lock()
RUNS={}
ACTIVE={}


def cue_groups(words,speed=1):
    cues=[];group=[]
    for word in words:
        if group and (len(group)>=6 or word['start']-group[0]['start']>3 or word['start']-group[-1]['end']>.6):
            cues.append({'start':group[0]['start']/speed,'end':group[-1]['end']/speed,'text':' '.join(w['text'] for w in group)});group=[]
        group.append(word)
    if group:cues.append({'start':group[0]['start']/speed,'end':group[-1]['end']/speed,'text':' '.join(w['text'] for w in group)})
    return cues


def folder(settings):
    path=Path(settings.data_dir)/'studio-transcripts';path.mkdir(parents=True,exist_ok=True);return path


def result(settings,identity):
    if not re.fullmatch('[a-f0-9]{32}',identity):raise ValueError('Invalid transcription identity')
    path=folder(settings)/identity/'result.json'
    if not path.exists():raise ValueError('Transcription is not ready')
    return json.loads(path.read_text(encoding='utf-8'))


def cancel(identity):
    with LOCK:
        run=RUNS.get(identity)
        if run:
            run['cancelled']=True
            if run.get('process') and run['process'].poll() is None:run['process'].terminate()


def _run(identity,command,log,progress,label,cwd=None):
    with log.open('ab') as output:
        with LOCK:
            if RUNS[identity]['cancelled']:raise RuntimeError('Transcription cancelled; saved edit unchanged')
            process=subprocess.Popen(command,stdout=output,stderr=output,cwd=cwd,creationflags=CREATE_NO_WINDOW)
            RUNS[identity]['process']=process
        started=time.monotonic()
        try:
            while process.poll() is None:
                if progress:progress(f'{label} · {int(time.monotonic()-started)}s elapsed')
                time.sleep(.3)
            with LOCK:
                if RUNS[identity]['cancelled']:raise RuntimeError('Transcription cancelled; saved edit unchanged')
            if process.returncode:raise RuntimeError(f'{label} failed (exit {process.returncode}). Local diagnostic log: {log.name}')
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()


def reserve(project_id,identity):
    with LOCK:
        if project_id in ACTIVE:raise ValueError('This project already has a transcription running')
        ACTIVE[project_id]=identity;RUNS[identity]={'cancelled':False}


def transcribe(settings,project_id,clip_id,ordinal,identity,progress=None):
    destination=Path(settings.data_dir)/'studio-transcripts'/identity
    record={'id':identity,'project_id':project_id,'clip_id':clip_id,'status':'running'}
    try:
        destination.mkdir(parents=True,exist_ok=False)
        project=studio.project(settings,project_id)
        clip=next((c for c in project['recipe']['clips'] if c['id']==clip_id),None)
        if not clip:raise ValueError('Select an existing video clip')
        media=studio.asset(settings,clip['asset_id']);available={a['ordinal'] for a in media['metadata']['audio_streams']}
        if ordinal not in available:raise ValueError('Choose an available audio track')
        record.update(revision=project['revision'],clip=clip,audio_ordinal=ordinal)
        wav=destination/'audio.wav';output=destination/'whisper'
        command=whisper_setup.command(settings,wav,output)
        _run(identity,[settings.ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(clip['start']),'-i',media['path'],'-t',str(clip['end']-clip['start']),
             '-map',f'0:a:{ordinal}','-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)],destination/'audio.log',progress,'Extracting selected audio track')
        _run(identity,command,destination/'whisper.log',progress,'Whisper transcription',cwd=Path(command[0]).parent)
        payload=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
        from .transcribe import gate_words_by_audio
        words=gate_words_by_audio(transcript_words(payload),wav)
        limit=clip['end']-clip['start']
        words=[{**w,'start':max(0,w['start']),'end':min(limit,w['end'])} for w in words if w['end']>0 and w['start']<limit and w['end']>w['start']]
        record.update(status='complete',cues=cue_groups(words,clip['speed']),text=' '.join(w['text'] for w in words))
        return identity
    except Exception as error:
        record.update(status='cancelled' if RUNS.get(identity,{}).get('cancelled') else 'error',error=str(error));raise
    finally:
        try:
            if destination.is_dir(): (destination/'result.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
        finally:
            with LOCK:ACTIVE.pop(project_id,None);RUNS.pop(identity,None)
