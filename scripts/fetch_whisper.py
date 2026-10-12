"""Fetch the pinned offline caption runtime and model for a Windows build."""
import hashlib,json,urllib.request,zipfile
from pathlib import Path

folder=Path(__file__).resolve().parents[1]/'third_party/whisper'
manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
runtime=folder/'runtime';runtime.mkdir(exist_ok=True)
def download(url,path,expected):
    if path.is_file():
        with path.open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest()==expected:return
    partial=path.with_name(path.name+'.part')
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ClipMaker-build'}),timeout=60) as response,partial.open('wb') as output:
        while block:=response.read(1024*1024):output.write(block)
    with partial.open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=expected:raise ValueError('Whisper download failed SHA-256 validation; partial retained')
    partial.replace(path)
archive=folder/'runtime.zip.part';download(manifest['archive_url'],archive,manifest['archive_sha256'])
with zipfile.ZipFile(archive) as z:
    for name,digest in manifest['files'].items():
        if not name.startswith('bin/'):continue
        target=(runtime/name).resolve()
        if not target.is_relative_to(runtime.resolve()):raise ValueError('Invalid bundle path')
        entry='Release/'+Path(name).name;data=z.read(entry)
        if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Whisper runtime hash mismatch')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
model=runtime/'models/ggml-base-q5_1.bin';model.parent.mkdir(exist_ok=True)
download(manifest['model_url'],model,manifest['files']['models/ggml-base-q5_1.bin'])
(runtime/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
print('Verified offline Whisper runtime and multilingual base model are ready.')
