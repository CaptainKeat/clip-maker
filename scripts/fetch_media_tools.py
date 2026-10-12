"""Fetch pinned media tools, verifying the archive and executables."""
import hashlib,json,urllib.request,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'third_party/ffmpeg'
manifest=json.loads((root/'manifest.json').read_text())
archive=root/'tools.zip.part'
urllib.request.urlretrieve(manifest['url'],archive)
if hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest['archive_sha256']:
    raise ValueError('FFmpeg archive failed SHA-256 validation; partial download retained')
with zipfile.ZipFile(archive) as z:
    for name,digest in manifest['files'].items():
        entry=next(p for p in z.namelist() if p.endswith('/bin/'+name))
        data=z.read(entry)
        if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Executable hash mismatch')
        (root/'bin').mkdir(exist_ok=True);(root/'bin'/name).write_bytes(data)
print('Verified FFmpeg and FFprobe are ready')
