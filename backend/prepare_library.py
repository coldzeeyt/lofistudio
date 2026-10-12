"""Build-time loudness preparation for the bundled, licensed music only."""
import hashlib,json,subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path('library').resolve()
OUT=ROOT/'prepared';OUT.mkdir(exist_ok=True)

def prepare(track):
    source=(ROOT/track['file']).resolve()
    if not source.is_relative_to(ROOT): raise ValueError('Invalid library path')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if digest!=track['sha256']: raise ValueError('Library checksum mismatch')
    target=OUT/f'balanced-v1-{digest}.flac'
    if target.exists(): return
    temporary=target.with_suffix('.tmp.flac')
    try:
        subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-threads','2','-filter_threads','1','-i',str(source),'-vn','-af','loudnorm=I=-18:TP=-2:LRA=11','-ar','48000','-ac','2','-c:a','flac','-compression_level','0','-sample_fmt','s16',str(temporary)],check=True)
        temporary.replace(target)
    finally: temporary.unlink(missing_ok=True)

if __name__=='__main__':
    tracks=json.loads((ROOT/'manifest.json').read_text())
    with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(prepare,tracks))
    print(f'Prepared {len(tracks)} licensed tracks')
