"""Sequential intermediate renders keep hour-long, multi-source edits bounded."""
import json
import math
from pathlib import Path

from .motion_captions import append_motion_ass
from .studio import _run, clip_duration


def fades(item,duration,prefix=''):
    result=''
    left=min(float(item.get(prefix+'fade_in') or 0),duration/2)
    right=min(float(item.get(prefix+'fade_out') or 0),duration/2)
    if left:result+=f',afade=t=in:st=0:d={left}'
    if right:result+=f',afade=t=out:st={max(0,duration-right)}:d={right}'
    return result


def export_recipe(settings, data, destination, work, progress):
    from .studio_tracks import export_view,state
    recipe = export_view(data["recipe"])
    media = {a["id"]:a for a in data["assets"]}
    w,h = data["frame"]
    total = sum(clip_duration(c) for c in recipe["clips"])
    done = 0
    fragments = []
    for index,c in enumerate(recipe["clips"]):
        source = media[c["asset_id"]]
        duration = clip_duration(c)
        raw = c["end"]-c["start"]
        fragment = work/f"part-{index:04d}.mp4"
        fragments.append(fragment)
        args = ["-ss",str(c["start"]),"-i",source["path"]]
        enabled = [a for a in c["audio"] if a["enabled"]]
        vf = f"trim=duration={raw},setpts=(PTS-STARTPTS)/{c['speed']},fps=30,"
        if c["fit"]=="fill": vf += f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
        else: vf += f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        if c["freeze"]: vf += f",tpad=stop_mode=clone:stop_duration={c['freeze']}"
        if c["transition"]=="fade": vf += f",fade=t=in:st=0:d={min(.3,duration/3)},fade=t=out:st={max(0,duration-.3)}:d=.3"
        if state(recipe,"video").get("hidden") or c.get("disabled"): vf += ",drawbox=color=black:t=fill"
        graph = [f"[0:v]{vf}[v]"]
        if enabled:
            atempo = c["speed"]
            tempos = []
            while atempo>2: tempos.append("atempo=2");atempo/=2
            while atempo<.5: tempos.append("atempo=.5");atempo/=.5
            tempos.append(f"atempo={atempo}")
            voices=[]
            for j,a in enumerate(enabled):
                graph.append(f"[0:a:{a['ordinal']}]atrim=duration={raw},asetpts=PTS-STARTPTS,aresample=48000,"
                    f"volume={10**(a['gain_db']/20)},"+",".join(tempos)+f",apad,atrim=duration={duration}"+fades(c,duration,'audio_')+f"[track{j}]")
                if a.get('dialogue'):
                    graph.append(f'[track{j}]asplit=2[a{j}][voice{j}]');voices.append(f'[voice{j}]')
                else:graph.append(f'[track{j}]anull[a{j}]')
            graph.append("".join(f"[a{j}]" for j in range(len(enabled)))+
                         f"amix=inputs={len(enabled)}:normalize=0:duration=longest,alimiter=limit=.95[a]")
            if voices:graph.append(''.join(voices)+f'amix=inputs={len(voices)}:normalize=0,alimiter=limit=.95[voice]')
            else:graph.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={duration}[voice]')
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration}[a]")
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration}[voice]")
        args += ["-filter_complex",";".join(graph),"-map","[v]","-map","[a]","-map","[voice]",
                 "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p",
                 "-c:a","aac","-ar","48000","-ac","2","-t",str(duration),str(fragment)]
        _run(settings,data["id"],args,work/"export.log",duration,progress,done/total*65,duration/total*.65)
        done += duration
    concat = work/"parts.txt"
    # Fragments use generated filenames in a generated work directory.
    concat.write_text("".join(f"file '{p.name}'\n" for p in fragments),encoding="utf-8")
    captions = []
    for c in recipe["captions"]:
        item = dict(c)
        if not item.get("motion"):
            item["motion"] = {"enabled":True,"x":50,"y":85,"color":"#ffffff","size":1,"effect":"none"}
        captions.append(item)
    ass = work/"captions.ass"
    has_ass = append_motion_ass(ass,captions,[{"source_start":0,"source_end":total}],frame=(w,h))
    args = ["-f","concat","-safe","1","-i",str(concat)]
    graph = []
    video = "[0:v]"
    for i,item in enumerate(recipe["overlays"],start=1):
        a = media[item["asset_id"]]
        args += (["-loop","1"] if a["kind"]=="image" else ["-stream_loop","-1","-ss",str(item.get('source_start',0))])+["-i",a["path"]]
        graph.append(f"[{i}:v]scale={max(2,round(w*item['size'])//2*2)}:-2,trim=duration={item['duration']},"
                     f"setpts=PTS-STARTPTS+{item['time']}/TB[ov{i}]")
        graph.append(f"{video}[ov{i}]overlay=x='W*{item['x']/100}-w/2':y='H*{item['y']/100}-h/2':"
                     f"enable='between(t,{item['time']},{item['time']+item['duration']})':eof_action=pass[v{i}]")
        video = f"[v{i}]"
    audio = ["[0:a:0]"]
    ducked=[j for j,item in enumerate(recipe['sounds'],1) if item.get('duck') and item.get('role')!='dialogue']
    controls=['[0:a:1]'] if ducked else []
    for j,item in enumerate(recipe["sounds"],start=1):
        i = len(recipe["overlays"])+j
        a = media[item["asset_id"]]
        args += ["-i",a["path"]]
        graph.append(f"[{i}:a:{item.get('audio_ordinal',0)}]atrim=start={item.get('source_start',0)}:duration={item['duration']},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"volume={10**(item['gain_db']/20)}"+fades(item,item['duration'])+
                     f",adelay={round(item['time']*1000)}:all=1,apad,atrim=duration={total}[raw{j}]")
        if item.get('role')=='dialogue' and ducked:
            graph.append(f'[raw{j}]asplit=2[snd{j}][extraVoice{j}]');controls.append(f'[extraVoice{j}]')
        elif j not in ducked:graph.append(f'[raw{j}]anull[snd{j}]')
        audio.append(f"[snd{j}]")
    if ducked:
        graph.append(''.join(controls)+f'amix=inputs={len(controls)}:normalize=0:duration=longest,alimiter=limit=.95[voiceControl]')
        if len(ducked)>1:graph.append('[voiceControl]asplit='+str(len(ducked))+''.join(f'[control{j}]' for j in ducked))
        else:graph.append(f'[voiceControl]anull[control{ducked[0]}]')
        for j in ducked:
            item=recipe['sounds'][j-1]
            graph.append(f"[raw{j}][control{j}]sidechaincompress=threshold=.03:ratio={item.get('duck_ratio',6)}:attack=20:release=350:makeup=1:knee=2:detection=rms[snd{j}]")
    graph.append("".join(audio)+f"amix=inputs={len(audio)}:duration=first:normalize=0,alimiter=limit=.95[outa]")
    if has_ass:
        # Escape FFmpeg filter syntax (not the subprocess argument list).
        escaped = ass.as_posix().replace("\\", "\\\\").replace(":",r"\:").replace("'",r"\'")
        graph.append(f"{video}subtitles=filename='{escaped}'[outv]")
    else: graph.append(f"{video}null[outv]")
    quality = recipe["quality"]
    args += ["-filter_complex",";".join(graph),"-map","[outv]","-map","[outa]",
        "-c:v","libx264","-preset",{"fast":"ultrafast","balanced":"veryfast","high":"medium"}[quality],
        "-crf",{"fast":"25","balanced":"20","high":"17"}[quality],"-pix_fmt","yuv420p","-r","30",
        "-c:a","aac","-b:a","192k","-t",str(total),"-movflags","+faststart",str(destination)]
    _run(settings,data["id"],args,work/"export.log",total,progress,65,.35)
