"""Check packages remain extractable by earlier Windows updaters."""
import sys,zipfile
from pathlib import Path
STAGING_PREFIX_BUDGET=110
def check(names):
    longest=max(names,key=len)
    if STAGING_PREFIX_BUDGET+1+len(longest)>=260:
        raise ValueError('Release path exceeds the legacy Windows updater budget: '+longest)
    return STAGING_PREFIX_BUDGET+1+len(longest)
if __name__=='__main__':
    path=Path(sys.argv[1])
    if path.is_dir():names=['Clip Maker/'+p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_file()]
    else:
        with zipfile.ZipFile(path) as archive:names=archive.namelist()
    print('Legacy Windows staging path budget passed; longest path:',check(names))
