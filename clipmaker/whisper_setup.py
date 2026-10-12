"""Whisper configuration and bounded local model discovery."""
import os
import re
import shutil
from pathlib import Path


def executable(settings):
    raw = str(settings.whisper_cli).strip()
    path = Path(shutil.which(raw) or raw).expanduser()
    return path.resolve() if path.is_file() else None


def validate(body):
    result = {}
    for key in ['whisper_cli','whisper_model','whisper_prompt','language','whisper_device','whisper_quality']:
        if key in body:
            value = str(body[key]).strip()
            if len(value)>2048 or '\0' in value: raise ValueError('Invalid Whisper setting')
            result[key]=value
    if 'language' in result and not re.fullmatch('auto|[a-z]{2,3}',result['language']): raise ValueError('Use a language code such as en, ja or auto')
    if 'whisper_device' in result and result['whisper_device'] not in {'auto','cpu'}: raise ValueError('Choose automatic GPU/CPU or CPU')
    if 'whisper_quality' in result and result['whisper_quality'] not in {'fast','balanced','accurate'}: raise ValueError('Choose a Whisper quality preset')
    if 'whisper_threads' in body:
        value = int(body['whisper_threads'])
        if not 1<=value<=64: raise ValueError('Use 1–64 CPU threads')
        result['whisper_threads']=value
    if 'whisper_translate' in body: result['whisper_translate']=body['whisper_translate'] is True
    return result


def status(settings):
    cli = executable(settings);model = Path(settings.whisper_model).expanduser()
    found = model.is_file();size = model.stat().st_size if found else 0
    warnings=[]
    if not cli: warnings.append('Choose an installed whisper.cpp CLI executable.')
    if not found: warnings.append('Choose a downloaded ggml Whisper model file.')
    if found and '.en.' in model.name and (settings.language!='en' or settings.whisper_translate):
        warnings.append('This English-only model cannot recognize other languages. Choose a multilingual model.')
    return {'executable_found':bool(cli),'model_found':found,'model_bytes':size,
            'ready':bool(cli and found and size>0 and not warnings),'warnings':warnings,
            'message':'Files found. Run a transcription to verify model compatibility and the selected backend.' if cli and found else 'Whisper setup needs attention.'}


def models(settings):
    folders = [Path(settings.whisper_model).expanduser().parent,Path(settings.data_dir)/'models'/'whisper',Path.home()/'.claude-video-vision'/'models']
    found={}
    for folder in folders:
        if not folder.is_dir(): continue
        for path in folder.glob('ggml-*.bin'):
            if len(found)>=100:break
            if path.is_file():found[str(path.resolve())]={'path':str(path.resolve()),'name':path.name,'bytes':path.stat().st_size}
    return sorted(found.values(),key=lambda item:item['name'])


def command(settings,wav,output):
    cli=executable(settings);state=status(settings)
    if not state['ready']: raise ValueError(' '.join(state['warnings']) or 'Choose a nonempty Whisper model')
    options=validate({key:getattr(settings,key) for key in ['language','whisper_threads','whisper_device','whisper_quality','whisper_prompt','whisper_translate']})
    beam={'fast':1,'balanced':5,'accurate':8}[options['whisper_quality']]
    args=[str(cli),'-m',str(Path(settings.whisper_model).expanduser()),'-f',str(wav),'-l',options['language'],
          '-ojf','-of',str(output),'-t',str(options['whisper_threads']),'-bs',str(beam),'-bo',str(beam),'-pp']
    if options['whisper_device']=='cpu':args+=['-ng']
    if options['whisper_translate']:args+=['-tr']
    if options['whisper_prompt']:args+=['--prompt',options['whisper_prompt']]
    return args
