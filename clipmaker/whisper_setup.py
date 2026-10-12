"""Whisper configuration and bounded local model discovery."""
import os
import re
import shutil
import sys
import json
import hashlib
from functools import lru_cache
from pathlib import Path


def _custom_executable(settings):
    raw = str(settings.whisper_cli).strip()
    path = Path(shutil.which(raw) or raw).expanduser()
    return path.resolve() if path.is_file() else None


def bundle_root():
    if getattr(sys,'frozen',False):return Path(sys._MEIPASS)/'whisper-tools'
    return Path(__file__).resolve().parent.parent/'third_party/whisper/runtime'


@lru_cache(maxsize=8)
def _verify_bundle(root, stamps):
    path=Path(root);manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    for name,digest in manifest['files'].items():
        file=(path/name).resolve()
        if not file.is_relative_to(path.resolve()) or not file.is_file():raise ValueError('Bundled Whisper files are incomplete')
        with file.open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest()!=digest:raise ValueError('Bundled Whisper integrity check failed. Reinstall Clip Maker.')
    return True


def bundle():
    root=bundle_root();cli=root/'bin/whisper-cli.exe';model=root/'models/ggml-base-q5_1.bin'
    if not cli.is_file() or not model.is_file() or not (root/'manifest.json').is_file():return None
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    paths=[root/'manifest.json',*[root/name for name in manifest['files']]]
    stamps=tuple((str(path),path.stat().st_size,path.stat().st_mtime_ns) for path in paths)
    _verify_bundle(str(root),stamps)
    return {'cli':cli.resolve(),'model':model.resolve(),'version':manifest['version'],'label':'Built-in Whisper base · multilingual · CPU'}


def resolve(settings):
    if settings.whisper_cli!='bundled':
        cli=_custom_executable(settings);model=Path(settings.whisper_model).expanduser()
        if cli and model.is_file() and model.stat().st_size>0:return {'cli':cli,'model':model.resolve(),'source':'custom','label':'Custom Whisper setup'}
    bundled=bundle()
    if bundled:return {**bundled,'source':'bundled'}
    return {'cli':_custom_executable(settings),'model':Path(settings.whisper_model).expanduser(),'source':'custom','label':'Custom Whisper setup'}


def executable(settings):
    return resolve(settings)['cli']


def environment(settings):
    env=os.environ.copy()
    # The desktop already ships Microsoft's runtime for Qt/Python. Let the child
    # process find that same runtime without requiring a separate installation.
    if getattr(sys,'frozen',False):env['PATH']=str(Path(sys._MEIPASS))+os.pathsep+env.get('PATH','')
    return env


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
    try:resolved=resolve(settings)
    except (ValueError,OSError,KeyError) as error:return {'executable_found':False,'model_found':False,'model_bytes':0,'ready':False,'warnings':[str(error)],'message':'Built-in captions need repair.','source':'bundled','bundle_available':False}
    cli=resolved['cli'];model=resolved['model']
    found = model.is_file();size = model.stat().st_size if found else 0
    warnings=[]
    if not cli: warnings.append('Choose an installed whisper.cpp CLI executable.')
    if not found: warnings.append('Choose a downloaded ggml Whisper model file.')
    if found and re.search(r'\.en(?:[.-]|$)',model.name) and (settings.language!='en' or settings.whisper_translate):
        warnings.append('This English-only model cannot recognize other languages. Choose a multilingual model.')
    return {'executable_found':bool(cli),'model_found':found,'model_bytes':size,
            'ready':bool(cli and found and size>0 and not warnings),'warnings':warnings,
            'message':resolved['label']+' is ready for offline captions.' if resolved['source']=='bundled' else 'Files found. Run a transcription to verify model compatibility and the selected backend.' if cli and found else 'Whisper setup needs attention.',
            'source':resolved['source'],'bundle_available':bool(bundle_root().joinpath('manifest.json').is_file()),'selection':'bundled' if settings.whisper_cli=='bundled' else 'custom' if resolved['source']=='custom' and cli and found else 'auto'}


def models(settings):
    folders = [bundle_root()/'models',Path(settings.whisper_model).expanduser().parent,Path(settings.data_dir)/'models'/'whisper',Path.home()/'.claude-video-vision'/'models']
    found={}
    for folder in folders:
        if not folder.is_dir(): continue
        for path in folder.glob('ggml-*.bin'):
            if len(found)>=100:break
            if path.is_file():found[str(path.resolve())]={'path':str(path.resolve()),'name':path.name,'bytes':path.stat().st_size}
    return sorted(found.values(),key=lambda item:item['name'])


def command(settings,wav,output):
    state=status(settings);resolved=resolve(settings);cli=resolved['cli']
    if not state['ready']: raise ValueError(' '.join(state['warnings']) or 'Choose a nonempty Whisper model')
    options=validate({key:getattr(settings,key) for key in ['language','whisper_threads','whisper_device','whisper_quality','whisper_prompt','whisper_translate']})
    beam={'fast':1,'balanced':5,'accurate':8}[options['whisper_quality']]
    args=[str(cli),'-m',str(resolved['model']),'-f',str(wav),'-l',options['language'],
          '-ojf','-of',str(output),'-t',str(options['whisper_threads']),'-bs',str(beam),'-bo',str(beam),'-pp']
    if options['whisper_device']=='cpu' or resolved['source']=='bundled':args+=['-ng']
    if options['whisper_translate']:args+=['-tr']
    if options['whisper_prompt']:args+=['--prompt',options['whisper_prompt']]
    return args
