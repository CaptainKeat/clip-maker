"""Bounded, cancellable audio peak caches and lightweight scrub frames."""
import array
import json
import math
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from . import studio
from .media import CREATE_NO_WINDOW

LOCK=threading.Lock()
ACTIVE={}
FRAMES=threading.Semaphore(2)


def _cache(settings,asset_id,ordinal):
    media=studio.asset(settings,asset_id)
    if ordinal not in {a['ordinal'] for a in media['metadata']['audio_streams']}:raise ValueError('Choose an available audio track')
    root=Path(settings.data_dir)/'studio-waveforms';root.mkdir(parents=True,exist_ok=True)
    stamp=Path(media['path']).stat().st_mtime_ns
    return media,root/f'{asset_id}-{stamp}-{ordinal}.json'


def read(settings,asset_id,ordinal):
    media,path=_cache(settings,asset_id,ordinal)
    if not path.exists():return {'ready':False,'duration':media['metadata']['duration_sec']}
    return {'ready':True,**json.loads(path.read_text(encoding='utf-8'))}


def reserve(asset_id,ordinal):
    key=(asset_id,ordinal)
    with LOCK:
        if key in ACTIVE:return ACTIVE[key]['id'],False
        identity=uuid.uuid4().hex;ACTIVE[key]={'id':identity,'cancelled':False,'process':None};return identity,True


def cancel_all():
    with LOCK:
        for run in ACTIVE.values():
            run['cancelled']=True
            if run['process'] and run['process'].poll() is None:run['process'].terminate()


def cancel(identity):
    with LOCK:
        for run in ACTIVE.values():
            if run['id']==identity:
                run['cancelled']=True
                if run['process'] and run['process'].poll() is None:run['process'].terminate()


def build(settings,asset_id,ordinal,identity,progress=None):
    key=(asset_id,ordinal);process=None;partial=None;values=[]
    try:
        media,path=_cache(settings,asset_id,ordinal)
        duration=media['metadata']['duration_sec'];bucket=max(.05,duration/12000);samples=max(1,round(bucket*8000));bucket=samples/8000
        partial=path.with_name(path.name+'.'+identity+'.part')
        with LOCK:
            if ACTIVE[key]['cancelled']:raise RuntimeError('Waveform cancelled')
            process=subprocess.Popen([settings.ffmpeg,'-hide_banner','-loglevel','error','-threads','1','-i',media['path'],'-vn',
                '-map',f'0:a:{ordinal}','-ac','2','-ar','8000','-f','s16le','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
            ACTIVE[key]['process']=process
        while data:=process.stdout.read(samples*4):
            data=data[:len(data)//2*2];pcm=array.array('h');pcm.frombytes(data)
            if sys.byteorder!='little':pcm.byteswap()
            values.append(round(max((abs(v) for v in pcm),default=0)/32768*1000))
            if progress and len(values)%20==0:progress(f'Waveform {min(99,round(len(values)*bucket/max(.1,duration)*100))}%')
            with LOCK:
                if ACTIVE[key]['cancelled']:raise RuntimeError('Waveform cancelled')
        process.wait()
        with LOCK:
            if ACTIVE[key]['cancelled']:raise RuntimeError('Waveform cancelled')
        if process.returncode:raise RuntimeError('Audio could not be decoded for this waveform')
        payload={'duration':duration,'bucket':bucket,'peaks':values,'audio_ordinal':ordinal}
        partial.write_text(json.dumps(payload),encoding='utf-8');partial.replace(path)
        return identity
    finally:
        if process:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            if process.stdout:process.stdout.close()
        if partial and values and not partial.exists():
            # Retain interrupted work without treating it as a complete cache.
            if process and process.returncode:
                try:partial.write_text(json.dumps({'partial':True,'peaks':values}),encoding='utf-8')
                except OSError:pass
        with LOCK:ACTIVE.pop(key,None)


def frame(settings,asset_id,time):
    media=studio.asset(settings,asset_id)
    if media['kind']!='video':raise ValueError('Scrub frames need video media')
    value=float(time)
    if not math.isfinite(value):raise ValueError('Choose a finite frame time')
    point=max(0,min(value,media['metadata']['duration_sec']-.04));point=min(round(point*2)/2,max(0,media['metadata']['duration_sec']-.04))
    root=Path(settings.data_dir)/'studio-scrub-frames';root.mkdir(parents=True,exist_ok=True)
    path=root/f"{asset_id}-{Path(media['path']).stat().st_mtime_ns}-{round(point*2)}.jpg"
    with FRAMES:
        if not path.exists():
            partial=path.with_name(path.stem+'.'+uuid.uuid4().hex+'.part.jpg')
            subprocess.run([settings.ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(point),'-i',media['path'],
                '-frames:v','1','-vf','scale=640:360:force_original_aspect_ratio=decrease',str(partial)],
                check=True,capture_output=True,timeout=20,creationflags=CREATE_NO_WINDOW)
            partial.replace(path)
    return path
