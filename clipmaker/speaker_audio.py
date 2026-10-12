"""Offline diarization worker and aligned, exclusive speaker-turn audio stems.

Stems contain the original mix during attributed turns, not isolated voices.
Overlaps and non-speech get their own stems so audio is never doubled or lost.
"""
import hashlib
import json
import math
import os
import subprocess
import sys
import wave
from pathlib import Path

from .media import CREATE_NO_WINDOW

MODEL = 'wespeaker_en_voxceleb_resnet34.onnx'
HASHES = {MODEL: '5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94',
          'segmentation.int8.onnx': 'd582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d'}


def models():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'third_party/speaker'


def verify_models():
    for name, digest in HASHES.items():
        path = models()/name
        if not path.is_file() or hashlib.file_digest(path.open('rb'), 'sha256').hexdigest() != digest:
            raise ValueError('Bundled speaker model is missing or damaged: '+name)


def partition(segments, duration):
    """Non-overlapping full-coverage regions; overlap is routed only once."""
    events = {0.0: [], float(duration): []}
    for item in segments:
        a, b = max(0, float(item['start'])), min(duration, float(item['end']))
        if b <= a: continue
        label = str(item['speaker'])
        events.setdefault(a, []).append((label, 1)); events.setdefault(b, []).append((label, -1))
    active = {}; result = []; previous = 0.0
    for point in sorted(events):
        if point > previous:
            speakers = [s for s, count in active.items() if count > 0]
            label = speakers[0] if len(speakers) == 1 else 'Overlapping voices' if speakers else 'Background / unassigned'
            if result and result[-1]['speaker'] == label: result[-1]['end'] = point
            else: result.append({'start': previous, 'end': point, 'speaker': label})
        for label, delta in events[point]: active[label] = active.get(label, 0)+delta
        previous = point
    return result


def worker(config_path):
    import numpy as np
    import sherpa_onnx as so
    cfg = json.loads(Path(config_path).read_text(encoding='utf-8')); root = Path(config_path).parent
    def check():
        if (root/'cancel').exists(): raise RuntimeError('Speaker scan cancelled; edit unchanged')
    def progress(text):
        check(); tmp=root/'progress.part';tmp.write_text(text,encoding='utf-8')
        try: tmp.replace(root/'progress.txt')
        except PermissionError: pass  # Windows reader may briefly hold the old progress file.
    verify_models(); progress('Preparing local speaker models…')
    segmentation=so.OfflineSpeakerSegmentationModelConfig(
        pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(models()/'segmentation.int8.onnx'),window_shift_ratio=.1),num_threads=2,provider='cpu')
    embedding=so.SpeakerEmbeddingExtractorConfig(model=str(models()/MODEL),num_threads=2,provider='cpu')
    extractor=so.SpeakerEmbeddingExtractor(embedding)
    sd=so.OfflineSpeakerDiarization(so.OfflineSpeakerDiarizationConfig(segmentation=segmentation,embedding=embedding,
        clustering=so.FastClusteringConfig(num_clusters=cfg.get('speakers',0) or -1,threshold=.5),min_duration_on=.3,min_duration_off=.3))
    wav=root/'analysis.wav'; log=(root/'decode.log').open('ab')
    command=[cfg['ffmpeg'],'-v','error','-y','-ss',str(cfg['start']),'-t',str(cfg['end']-cfg['start']),'-i',cfg['source'],'-map',f"0:a:{cfg['ordinal']}",'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)]
    process=subprocess.Popen(command,stdout=log,stderr=log,creationflags=CREATE_NO_WINDOW)
    try:
        import time
        while process.poll() is None: check(); time.sleep(.15)
        if process.returncode: raise RuntimeError('Selected audio could not be decoded')
    finally:
        if process.poll() is None: process.terminate();process.wait(timeout=5)
        log.close()
    centers=[]; counts=[]; segments=[]
    with wave.open(str(wav),'rb') as audio:
        length=audio.getnframes()/16000; offset=0
        while offset < length:
            check(); samples=np.frombuffer(audio.readframes(16000*300),dtype='<i2').astype(np.float32)/32768
            def callback(done,total):progress(f'Scanning speakers · {round((offset+len(samples)/16000*done/max(1,total))/max(.1,length)*100)}%');return 0
            turns=sd.process(samples,callback=callback).sort_by_start_time()
            local={}
            for label in sorted({r.speaker for r in turns}):
                slices=[]; remaining=16000*8
                for r in turns:
                    if r.speaker!=label or remaining<=0:continue
                    part=samples[max(0,int(r.start*16000)):min(len(samples),int(r.end*16000))][:remaining];slices.append(part);remaining-=len(part)
                speech=np.concatenate(slices) if slices else np.zeros(0,dtype=np.float32)
                if len(speech)<8000:local[label]='Uncertain speech';continue
                stream=extractor.create_stream();stream.accept_waveform(16000,speech);stream.input_finished()
                if not extractor.is_ready(stream):local[label]='Uncertain speech';continue
                vector=np.asarray(extractor.compute(stream),dtype=np.float32);vector/=max(1e-9,np.linalg.norm(vector))
                scores=[float(vector@center) for center in centers];best=int(np.argmax(scores)) if scores else -1
                # Speakers within one chunk must remain distinct.
                used=set(local.values());name=f'Speaker {best+1}'
                if best<0 or scores[best]<.55 or name in used:
                    if len(centers)>=8:local[label]='Uncertain speech';continue
                    best=len(centers);centers.append(vector);counts.append(0)
                merged=centers[best]*counts[best]+vector;centers[best]=merged/max(1e-9,np.linalg.norm(merged));counts[best]+=1
                local[label]=f'Speaker {best+1}'
            segments.extend({'start':offset+float(r.start),'end':min(length,offset+float(r.end)),'speaker':local[r.speaker]} for r in turns)
            offset+=len(samples)/16000
    regions=partition(segments,length)
    speed=float(cfg['speed']);duration=length/speed
    for region in regions:region['start']/=speed;region['end']/=speed
    names=list(dict.fromkeys(r['speaker'] for r in regions));encoders=[];decoder=None
    try:
        for index,name in enumerate(names):
            import re
            slug=re.sub('[^a-z0-9]+','-',name.lower()).strip('-')
            output=root/f'{index+1:02d}-{slug}.flac'
            encoders.append((name,output,subprocess.Popen([cfg['ffmpeg'],'-v','error','-y','-f','s16le','-ar','48000','-ac','2','-i','pipe:0','-c:a','flac','-threads','1',str(output)],stdin=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)))
        tempo=speed;filters=[]
        while tempo>2:filters.append('atempo=2');tempo/=2
        while tempo<.5:filters.append('atempo=.5');tempo/=.5
        filters.append('anull' if speed==1 else f'atempo={tempo}')
        decoder=subprocess.Popen([cfg['ffmpeg'],'-v','error','-ss',str(cfg['start']),'-t',str(cfg['end']-cfg['start']),'-i',cfg['source'],'-map',f"0:a:{cfg['ordinal']}",'-vn','-af',','.join(filters),'-ac','2','-ar','48000','-f','s16le','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
        position=0;region_index=0
        while data:=decoder.stdout.read(4096*4):
            check(); pcm=np.frombuffer(data,dtype='<i2').reshape(-1,2);n=len(pcm);masks={name:np.zeros(n,dtype=bool) for name in names}
            last=(position+n)/48000
            while region_index<len(regions) and regions[region_index]['end']<=position/48000:region_index+=1
            for region in regions[region_index:]:
                if region['start']>=last:break
                a=max(0,round(region['start']*48000)-position);b=min(n,round(region['end']*48000)-position)
                masks[region['speaker']][a:b]=True
            # atempo can differ slightly from nominal duration; retain the tail.
            covered=np.logical_or.reduce(list(masks.values()));masks[names[-1]]|=~covered
            for name,_,encoder in encoders:encoder.stdin.write(np.where(masks[name][:,None],pcm,0).astype('<i2').tobytes())
            position+=n
            if position%(4096*30)==0:progress(f'Creating aligned audio tracks · {min(99,round(position/48000/max(.1,duration)*100))}%')
        if decoder.wait():raise RuntimeError('Audio track rendering failed')
        for _,_,encoder in encoders:
            encoder.stdin.close()
            if encoder.wait():raise RuntimeError('A speaker audio track could not be written')
    finally:
        for process in ([decoder] if decoder else [])+[e[2] for e in encoders]:
            if process.poll() is None:process.terminate();process.wait(timeout=5)
        if decoder and decoder.stdout:decoder.stdout.close()
        for _,_,encoder in encoders:
            if not encoder.stdin.closed:encoder.stdin.close()
    check()
    result={**cfg,'status':'complete','duration':position/48000,'regions':regions,'tracks':[{'name':name,'path':str(output)} for name,output,_ in encoders],
            'note':'Speaker turns retain the source mix. Overlapping voices share one track. Speaker names are local estimates.'}
    (root/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');progress('Speaker tracks ready to review')
    return result


if __name__ == '__main__':
    worker(sys.argv[1])
