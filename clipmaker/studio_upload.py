"""Browser file imports stream into private local storage, never into memory."""
import shutil,uuid
from pathlib import Path,PureWindowsPath,PurePosixPath
from . import studio
from .db import database

MAX_IMPORT_BYTES=512*1024**3


def receive(settings,stream,name,length):
    name=str(name or '')
    if not name or name!=PureWindowsPath(name).name or name!=PurePosixPath(name).name or any(c in name for c in '\x00<>:"/\\|?*'):
        raise ValueError('Choose a media file with a valid filename')
    suffix=Path(name).suffix.lower()
    if suffix not in studio.VIDEO|studio.AUDIO|studio.IMAGES:
        raise ValueError('This file type is not supported. Drop videos, audio or images.')
    if length<=0 or length>MAX_IMPORT_BYTES:raise ValueError('The media file has an invalid size')
    folder=Path(settings.data_dir)/'studio-imports';folder.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(folder).free<length+64*1024**2:raise ValueError('There is not enough free space for this browser import. The desktop app can link the original instead.')
    safe_stem=Path(name).stem.strip(' .')[:100] or 'media'
    target=folder/(uuid.uuid4().hex+'-'+safe_stem+suffix)
    partial=target.with_name(target.name+'.part')
    remaining=length
    with partial.open('xb') as output:
        while remaining:
            chunk=stream.read(min(1024*1024,remaining))
            if not chunk:raise ValueError('Import stopped before the whole file arrived. The partial copy was kept locally.')
            output.write(chunk);remaining-=len(chunk)
    partial.replace(target)
    asset=studio.import_media(settings,target)
    with database(settings.db_path) as db:db.execute('UPDATE studio_assets SET name=? WHERE id=?',(name,asset['id']))
    return studio.asset(settings,asset['id'])
