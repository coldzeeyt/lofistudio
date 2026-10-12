"""Private, bounded cache of reusable media preparation; one render worker."""
import hashlib, os
from pathlib import Path
from .config import DATA
ROOT=DATA/'media-cache'
LIMIT=int(os.getenv('PREPARATION_CACHE_MB','256'))*1024**2

def location(path, profile, digest=None):
    source=Path(path).resolve()
    if digest is None:
        with source.open('rb') as file: digest=hashlib.file_digest(file,'sha256').hexdigest()
    identity=digest
    key=hashlib.sha256(f'{identity}:{profile}'.encode()).hexdigest()
    ROOT.mkdir(exist_ok=True)
    return ROOT/key

def touch(path):
    os.utime(path,None)

def prune(keep=()):
    if not ROOT.exists(): return
    protected=set(keep)
    files=[p for p in ROOT.iterdir() if p.is_file() and '.tmp.' not in p.name]
    total=sum(p.stat().st_size for p in files)
    for path in sorted(files,key=lambda p:p.stat().st_mtime):
        if total<=LIMIT: break
        if path in protected: continue
        size=path.stat().st_size;path.unlink(missing_ok=True);total-=size
