import asyncio, json, os, re, secrets, shutil, threading, time, logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Depends
from fastapi import Request as HttpRequest
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import DATA, MAX_UPLOAD, MAX_STORAGE, TTL, MAX_ACTIVE, MAX_SECONDS, DAILY_SECONDS
from .store import r, get, put, update
from .render import probe, render, Cancelled

def owner(authorization: str = Header(default='')):
    token = authorization.removeprefix('Bearer ')
    if not re.fullmatch(r'[a-f0-9]{64}', token): raise HTTPException(401,'A studio session is required')
    return token

def owned(kind, id, token):
    item=get(kind,id)
    if not item or item['owner']!=token: raise HTTPException(404,'Not found or expired')
    return item

def cleanup():
    pinned=set()
    for key in r.scan_iter('job:*'):
        j=json.loads(r.get(key) or '{}')
        if j.get('status') in ('queued','rendering'):
            pinned.add(Path(j['video_path']).parent.name)
            pinned.update(Path(t['path']).parent.name for t in j['tracks'])
            if j.get('logo_path'): pinned.add(Path(j['logo_path']).parent.name)
    for directory in DATA.iterdir():
        if directory.name not in pinned and directory.is_dir() and time.time()-directory.stat().st_mtime>TTL:
            job=get('job',directory.name)
            if not job or job['status'] not in ('queued','rendering'):
                shutil.rmtree(directory,ignore_errors=True)

def worker():
    while True:
        try:
            cleanup()
            entry=r.blpop('queue',timeout=3)
            if not entry: continue
            id=entry[1]; job=get('job',id)
            if not job: continue
            if r.exists(f'cancel:{id}'):
                update(id,status='cancelled',stage='Cancelled'); continue
            update(id,status='rendering'); job=get('job',id)
            try: render(job)
            except Cancelled: update(id,status='cancelled',stage='Cancelled')
            except Exception as e: update(id,status='failed',stage='Render failed',error=str(e))
            finally:
                for path in (DATA/id).glob('*'):
                    if path.name not in ('output.mp4','ffmpeg.log'): path.unlink(missing_ok=True)
        except Exception:
            logging.exception("Background worker iteration failed")
            time.sleep(3)

@asynccontextmanager
async def lifespan(app):
    r.ping()
    # One service replica and one process: mark interrupted renders honestly on restart.
    for key in r.scan_iter('job:*'):
        item=json.loads(r.get(key) or '{}')
        if item.get('status')=='rendering': update(item['id'],status='failed',stage='Interrupted by server restart',error='Please generate again.')
    threading.Thread(target=worker,daemon=True).start()
    yield
app=FastAPI(title='Lofi Studio',lifespan=lifespan)

@app.get('/api/health')
def health():
    r.ping(); return {'status':'ok'}

@app.post('/api/session')
def session(): return {'token':secrets.token_hex(32)}

@app.get('/api/library')
def library():
    # Operator-reviewed library only. Import metadata remains private to each session.
    manifest=Path(os.getenv('LIBRARY_DIR','library'))/'manifest.json'
    return json.loads(manifest.read_text()) if manifest.exists() else []

@app.get('/api/library/{id}/audio')
def library_audio(id: str):
    track=next((t for t in library() if t['id']==id),None)
    root=Path(os.getenv('LIBRARY_DIR','library')).resolve()
    if not track: raise HTTPException(404,'Track not found')
    path=(root/track['file']).resolve()
    if not path.is_relative_to(root) or not path.is_file(): raise HTTPException(404,'Track unavailable')
    return FileResponse(path,media_type='audio/mpeg')

@app.post('/api/uploads')
async def upload(file: UploadFile=File(...), kind: str=Form(...), title: str=Form(''), artist: str=Form(''), license: str=Form(''), source: str=Form(''), permission: bool=Form(False), token: str=Depends(owner)):
    if kind not in ('video','music','logo'): raise HTTPException(400,'Unknown asset type')
    if kind=='music' and (not permission or not title.strip() or not artist.strip() or not license.strip()): raise HTTPException(400,'Music needs title, artist, license and permission confirmation')
    lock=r.lock('upload-lock',timeout=120,blocking_timeout=1)
    if not lock.acquire(blocking=True): raise HTTPException(429,'Another upload is being processed; try again')
    id=secrets.token_hex(16); directory=DATA/id; directory.mkdir(); path=directory/'source'
    try:
        active=[json.loads(r.get(k) or '{}') for k in r.scan_iter('job:*')]
        reserved=sum(j.get('storage_reservation',0) for j in active if j.get('status') in ('queued','rendering'))
        used=sum(p.stat().st_size for p in DATA.rglob('*') if p.is_file())
        if used+reserved>MAX_STORAGE-MAX_UPLOAD or shutil.disk_usage(DATA).free<reserved+MAX_UPLOAD: raise HTTPException(507,'Studio storage is full; try later')
        size=0
        with path.open('wb') as out:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>MAX_UPLOAD: raise HTTPException(413,'File exceeds upload limit')
                out.write(chunk)
        media=await asyncio.to_thread(probe,path)
        streams=media['streams']; duration=float(media['format'].get('duration',0))
        if kind=='video' and any(s.get('width',0)>4096 or s.get('height',0)>4096 for s in streams): raise HTTPException(400,'Source video must be at most 4096px on each side')
        if kind=='logo':
            if not any(s['codec_type']=='video' and s['codec_name'] in ('png','mjpeg','webp') and s.get('width',0)<=4096 and s.get('height',0)<=4096 for s in streams): raise HTTPException(400,'Use a PNG, JPEG or WebP logo under 4096px')
        elif not any(s['codec_type']==('video' if kind=='video' else 'audio') for s in streams) or not 2<=duration<=600:
            raise HTTPException(400,'Upload readable media between 2 seconds and 10 minutes')
        item={'id':id,'owner':token,'kind':kind,'path':str(path),'title':title[:200] or file.filename,'artist':artist[:200],'license':license[:500],'source':source[:1000],'duration':duration}
        put('asset',id,item)
        return {k:v for k,v in item.items() if k not in ('owner','path')}
    except HTTPException:
        shutil.rmtree(directory,ignore_errors=True); raise
    except Exception:
        shutil.rmtree(directory,ignore_errors=True); raise HTTPException(400,'Could not read this media file')
    finally:
        await file.close(); lock.release()

CHUNK_SIZE = 1024 * 1024
class UploadSessionRequest(BaseModel):
    filename: str = Field(max_length=250)
    size: int = Field(gt=0)
    kind: str
    title: str = ''
    artist: str = ''
    license: str = ''
    source: str = ''
    permission: bool = False

@app.post('/api/upload-sessions')
def start_upload(req: UploadSessionRequest, token: str=Depends(owner)):
    if req.kind not in ('video','music','logo'): raise HTTPException(400,'Unknown asset type')
    if req.size>MAX_UPLOAD: raise HTTPException(413,'File exceeds upload limit (250 MB)')
    id=secrets.token_hex(16);directory=DATA/id;directory.mkdir()
    (directory/'partial').touch()
    put('upload',id,{**req.model_dump(),'owner':token,'id':id})
    return {'id':id,'offset':0,'chunk_size':CHUNK_SIZE}

@app.get('/api/upload-sessions/{id}')
def upload_status(id: str, token: str=Depends(owner)):
    session=owned('upload',id,token)
    return {'id':id,'offset':session['size'] if session.get('asset') else (DATA/id/'partial').stat().st_size,'chunk_size':CHUNK_SIZE,'asset':session.get('asset')}

@app.put('/api/upload-sessions/{id}/{offset}')
async def upload_chunk(id: str, offset: int, request: HttpRequest, token: str=Depends(owner)):
    session=owned('upload',id,token)
    if offset<0 or offset%CHUNK_SIZE!=0 or offset>=session['size']: raise HTTPException(400,'Invalid chunk offset')
    chunk=bytearray()
    async for part in request.stream():
        chunk.extend(part)
        if len(chunk)>CHUNK_SIZE: raise HTTPException(413,'Chunk is too large')
    if len(chunk)!=min(CHUNK_SIZE,session['size']-offset): raise HTTPException(400,'Incomplete upload chunk; retry it')
    with r.lock('chunk:'+id,timeout=30,blocking_timeout=5):
        path=DATA/id/'partial';current=path.stat().st_size
        if offset<current:
            with path.open('rb') as file:
                file.seek(offset)
                if file.read(len(chunk))!=chunk: raise HTTPException(409,'Chunk differs from the stored upload')
            return {'offset':current}
        if offset!=current: raise HTTPException(409,'Upload offset changed; resume from its current position')
        active=[json.loads(r.get(k) or '{}') for k in r.scan_iter('job:*')]
        reserved=sum(j.get('storage_reservation',0) for j in active if j.get('status') in ('queued','rendering'))
        used=sum(p.stat().st_size for p in DATA.rglob('*') if p.is_file())
        if used+reserved+len(chunk)>MAX_STORAGE or shutil.disk_usage(DATA).free<reserved+len(chunk): raise HTTPException(507,'Studio storage is full; try later')
        with path.open('ab') as file: file.write(chunk)
        put('upload',id,session)
        return {'offset':path.stat().st_size}

@app.post('/api/upload-sessions/{id}/finish')
async def finish_upload(id: str, token: str=Depends(owner)):
    session=owned('upload',id,token)
    with r.lock('finish:'+id,timeout=120,blocking_timeout=5):
        session=owned('upload',id,token)
        if session.get('asset'): return session['asset']
        path=DATA/id/'partial'
        if path.stat().st_size!=session['size']: raise HTTPException(409,'Upload is incomplete')
        file=UploadFile(filename=session['filename'],file=path.open('rb'))
        asset=await upload(file=file,kind=session['kind'],title=session['title'],artist=session['artist'],license=session['license'],source=session['source'],permission=session['permission'],token=token)
        session['asset']=asset;put('upload',id,session)
        path.write_bytes(b'')
        return asset

class Request(BaseModel):
    video_id: str
    music_ids: list[str]=Field(default_factory=list,max_length=10)
    library_ids: list[str]=Field(default_factory=list,max_length=10)
    logo_id: str | None=None
    channel: str=Field(min_length=1,max_length=80)
    seconds: int=600
    resolution: str='720p'
    color: str='#b7c9a8'
    font: str='sans'
    position: str='left'
    nature_volume: float=Field(default=0,ge=0,le=1)

@app.post('/api/jobs',status_code=202)
def generate(req: Request, token: str=Depends(owner)):
    if req.seconds not in (600,1800,3600,10800) or req.seconds>MAX_SECONDS: raise HTTPException(400,'Duration is unavailable')
    if req.resolution not in ('720p','1080p') or req.font not in ('sans','serif','mono') or req.position not in ('left','center','right') or not re.fullmatch(r'#[0-9a-fA-F]{6}',req.color): raise HTTPException(400,'Invalid output settings')
    if any(ord(c)<32 for c in req.channel): raise HTTPException(400,'Channel name contains control characters')
    video=owned('asset',req.video_id,token)
    if video['kind']!='video': raise HTTPException(400,'Select a video')
    tracks=[owned('asset',id,token) for id in req.music_ids]
    if any(t['kind']!='music' for t in tracks): raise HTTPException(400,'Select music files')
    libroot=Path(os.getenv('LIBRARY_DIR','library')).resolve()
    for id in req.library_ids:
        track=next((t for t in library() if t['id']==id),None)
        if not track or not track.get('license') or not track.get('source'): raise HTTPException(400,'Library track is not licensed')
        path=(libroot/track['file']).resolve()
        if not path.is_relative_to(libroot) or not path.is_file(): raise HTTPException(400,'Library track unavailable')
        tracks.append({**track,'path':str(path)})
    if not tracks or len(tracks)>10: raise HTTPException(400,'Choose 1–10 licensed music tracks')
    logo=owned('asset',req.logo_id,token) if req.logo_id else None
    if logo and logo['kind']!='logo': raise HTTPException(400,'Select a logo image')
    with r.lock('admission',timeout=10,blocking_timeout=5):
        jobs=[json.loads(r.get(k) or '{}') for k in r.scan_iter('job:*')]
        active=[j for j in jobs if j.get('status') in ('queued','rendering')]
        if len(active)>=MAX_ACTIVE or any(j['owner']==token for j in active): raise HTTPException(429,'Render queue is full or you already have an active render')
        used=sum(p.stat().st_size for p in DATA.rglob('*') if p.is_file())
        scratch=sum(t.get('duration',600)*576_000 for t in tracks)+MAX_UPLOAD*2
        reserved=sum(j.get('storage_reservation',0) for j in active)
        available=min(MAX_STORAGE-used-reserved,shutil.disk_usage(DATA).free-reserved)
        output_limit=int(min(req.seconds*(2_000_000 if req.resolution=='1080p' else 1_000_000),available-scratch))
        minimum=req.seconds*(180_000 if req.resolution=='1080p' else 120_000)
        if output_limit<minimum: raise HTTPException(507,'Not enough render storage for this duration. Choose a shorter video or try later.')
        estimate=output_limit+scratch
        day='render-budget:'+time.strftime('%Y-%m-%d',time.gmtime())
        if int(r.get(day) or 0)+req.seconds>DAILY_SECONDS: raise HTTPException(429,'The studio has reached its daily rendering budget. Try again tomorrow.')
        r.incrby(day,req.seconds);r.expire(day,172800)
        id=secrets.token_hex(16)
        credits='\n'.join(f"• {t['title']} — {t['artist']} | {t['license']} | {t.get('source','')}" for t in tracks)
        title=f"{req.channel} • {req.seconds//60} minutes of nature & lo-fi beats"
        description=f"Relax, study and unwind with {req.channel}.\n\nMusic credits (playlist repeats):\n{credits}\n\nVisuals supplied by the channel."
        job={**req.model_dump(),'id':id,'owner':token,'video_path':video['path'],'tracks':tracks,'logo_path':logo['path'] if logo else None,'output_limit':output_limit,'storage_reservation':estimate,'status':'queued','stage':'Waiting for renderer','progress':0,'created_at':time.time(),'youtube':{'title':title,'description':description}}
        put('job',id,job); r.rpush('queue',id)
    return public(job)

def public(job):
    result={k:v for k,v in job.items() if k not in ('owner','video_path','tracks','logo_path')}
    if str(result.get('error','')).startswith('FFmpeg failed:'):
        result['error']='The renderer could not process this video. Use Retry render to try the updated renderer.'
    return result
@app.get('/api/jobs/{id}')
def status(id: str, token: str=Depends(owner)): return public(owned('job',id,token))
@app.post('/api/jobs/{id}/cancel')
def cancel(id: str, token: str=Depends(owner)):
    job=owned('job',id,token)
    if job['status'] in ('queued','rendering'):
        r.setex(f'cancel:{id}',TTL,'1')
        if job['status']=='queued':
            r.lrem('queue',0,id)
            update(id,status='cancelled',stage='Cancelled')
    return {'status':'cancellation requested'}
@app.post('/api/jobs/{id}/retry',status_code=202)
def retry_render(id: str, token: str=Depends(owner)):
    with r.lock('admission',timeout=10,blocking_timeout=5):
        job=owned('job',id,token)
        if job['status']!='failed' or job.get('progress',0)>=10: raise HTTPException(409,'Start a new render for this video')
        if job.get('retry_count',0)>=2: raise HTTPException(429,'Retry limit reached. Start a new render or choose another clip.')
        if not Path(job['video_path']).exists() or any(not Path(t['path']).exists() for t in job['tracks']): raise HTTPException(410,'Source files expired. Upload your video again.')
        active=[json.loads(r.get(k) or '{}') for k in r.scan_iter('job:*')]
        active=[j for j in active if j.get('status') in ('queued','rendering')]
        if len(active)>=MAX_ACTIVE or any(j.get('owner')==token for j in active): raise HTTPException(429,'You already have an active render or the queue is full')
        reserved=sum(j.get('storage_reservation',0) for j in active)
        used=sum(p.stat().st_size for p in DATA.rglob('*') if p.is_file())
        scratch=sum(t.get('duration',600)*576_000 for t in job['tracks'])+MAX_UPLOAD*2
        available=min(MAX_STORAGE-used-reserved,shutil.disk_usage(DATA).free-reserved)
        output_limit=int(min(job.get('output_limit',job.get('seconds',600)*1_000_000),available-scratch))
        minimum=job.get('seconds',600)*(180_000 if job.get('resolution')=='1080p' else 120_000)
        if output_limit<minimum: raise HTTPException(507,'Not enough storage. Try later.')
        # Preparation never reached the long render. Retry the prepaid job up to twice.
        update(id,status='queued',stage='Retrying video preparation',progress=0,error=None,retry_count=job.get('retry_count',0)+1,output_limit=output_limit,storage_reservation=output_limit+scratch)
        r.delete('cancel:'+id);r.rpush('queue',id)
        return public(get('job',id))

@app.get('/api/jobs/{id}/download')
def download(id: str, token: str=Depends(owner)):
    job=owned('job',id,token); path=DATA/id/'output.mp4'
    if job['status']!='completed' or not path.exists(): raise HTTPException(409,'Video is not ready or has expired')
    return FileResponse(path,media_type='video/mp4',filename='lofi-studio.mp4')
@app.post('/api/jobs/{id}/download-ticket')
def ticket(id: str, token: str=Depends(owner)):
    job=owned('job',id,token)
    if job['status']!='completed': raise HTTPException(409,'Not ready')
    ticket=secrets.token_hex(32); r.setex('download:'+ticket,120,id)
    return {'url':'/api/download/'+ticket}
@app.get('/api/download/{ticket}')
def redeem(ticket: str):
    id=r.get('download:'+ticket)
    if not id: raise HTTPException(404,'Download link expired')
    path=DATA/id/'output.mp4'
    if not path.exists(): raise HTTPException(404,'Video expired')
    return FileResponse(path,media_type='video/mp4',filename='lofi-studio.mp4')

static=Path('frontend/dist')
if static.exists():
    app.mount('/assets',StaticFiles(directory=static/'assets'),name='assets')
    @app.get('/{path:path}')
    def spa(path: str):
        target=(static/path).resolve()
        if target.is_relative_to(static.resolve()) and target.is_file(): return FileResponse(target)
        return FileResponse(static/'index.html')
