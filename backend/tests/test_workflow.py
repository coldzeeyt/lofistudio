"""Real FFmpeg/Redis integration checks. Uses isolated Redis DB 15 and temp media."""
import os, subprocess, json
from pathlib import Path
import pytest
os.environ['REDIS_URL']='redis://localhost:6379/15'
os.environ['DATA_DIR']=str(Path('/tmp/lofi-integration'))
from fastapi.testclient import TestClient
from backend.app.main import app, cleanup
from backend.app.store import r, put, get
from backend.app.render import render, probe, Cancelled
from backend.app.config import DATA

def ff(*args):
    subprocess.run(['ffmpeg','-v','error','-y',*map(str,args)],check=True)

@pytest.fixture(scope='module')
def media():
    r.flushdb(); DATA.mkdir(exist_ok=True)
    video=DATA/'nature.mp4'; music=DATA/'tone.wav'; music2=DATA/'tone2.wav'; logo=DATA/'logo.png'
    ff('-f','lavfi','-i','testsrc2=size=320x180:rate=24:duration=4','-f','lavfi','-i','sine=frequency=200:duration=4','-c:v','libx264','-pix_fmt','yuv420p',video)
    ff('-f','lavfi','-i','sine=frequency=440:duration=3',music)
    ff('-f','lavfi','-i','sine=frequency=880:duration=3',music2)
    ff('-f','lavfi','-i','color=c=green:s=100x100','-frames:v','1','-threads','1',logo)
    yield video,music,music2,logo
    r.flushdb()

def test_api_validation_and_ownership(media):
    client=TestClient(app) # Do not start background worker for this isolation test.
    token=client.post('/api/session').json()['token']; headers={'Authorization':'Bearer '+token}
    with media[0].open('rb') as file:
        response=client.post('/api/uploads',headers=headers,data={'kind':'video'},files={'file':('nature.mp4',file,'video/mp4')})
    assert response.status_code==200,response.text
    video=response.json()
    assert client.post('/api/jobs',headers=headers,json={'video_id':video['id'],'channel':'Test'}).status_code==400
    with media[1].open('rb') as file:
        response=client.post('/api/uploads',headers=headers,data={'kind':'music','title':'Tone','artist':'Test','license':'original','permission':'true'},files={'file':('tone.wav',file)})
    assert response.status_code==200,response.text
    request={'video_id':video['id'],'music_ids':[response.json()['id']],'channel':'Test','seconds':600}
    response=client.post('/api/jobs',headers=headers,json=request)
    assert response.status_code==202,response.text
    id=response.json()['id']
    assert client.get('/api/jobs/'+id,headers={'Authorization':'Bearer '+'a'*64}).status_code==404
    assert client.post('/api/jobs',headers=headers,json=request).status_code==429
    assert client.post('/api/jobs/'+id+'/cancel',headers=headers).status_code==200
    assert r.exists('cancel:'+id)
    assert client.get('/api/jobs/'+id,headers=headers).json()['status']=='cancelled'
    assert client.get('/api/jobs/'+id+'/download',headers=headers).status_code==409
    assert client.get('/api/jobs/'+id).status_code==401
    update_job=get('job',id);update_job['status']='cancelled';put('job',id,update_job)

@pytest.mark.parametrize('resolution',['720p','1080p'])
def test_real_export(media,resolution):
    id='export-'+resolution; directory=DATA/id;directory.mkdir(exist_ok=True)
    job={'id':id,'owner':'a'*64,'video_path':str(media[0]),'tracks':[{'path':str(p),'title':'Original test tone','artist':'Test','license':'Test fixture'} for p in media[1:3]],'seconds':7,'resolution':resolution,'channel':"Quiet Days: 100% & 'nature'",'color':'#b7c9a8','font':'serif','position':'center','nature_volume':.2,'logo_path':str(media[3]),'status':'rendering'}
    put('job',id,job);render(job)
    output=directory/'output.mp4'; info=probe(output)
    assert abs(float(info['format']['duration'])-7)<.2
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    assert (video['width'],video['height'])==((1280,720) if resolution=='720p' else (1920,1080))
    assert video['codec_name']=='h264'
    assert any(s['codec_name']=='aac' for s in info['streams'])
    assert get('job',id)['progress']==100
    # Decode the entire artifact, including seams and repeated music.
    subprocess.run(['ffmpeg','-v','error','-i',str(output),'-f','null','-'],check=True)

def test_cancellation(media):
    id='cancel-test';(DATA/id).mkdir(exist_ok=True)
    job={'id':id,'video_path':str(media[0]),'tracks':[{'path':str(media[1])}],'seconds':600,'resolution':'720p','status':'rendering'}
    put('job',id,job);r.setex('cancel:'+id,60,'1')
    with pytest.raises(Cancelled): render(job)
    job['status']='cancelled';put('job',id,job)

def test_reject_playlist_and_fake_media():
    client=TestClient(app);headers={'Authorization':'Bearer '+'b'*64}
    body=b'#EXTM3U\n#EXTINF:10,\nhttp://127.0.0.1/private.mp4\n'
    response=client.post('/api/uploads',headers=headers,data={'kind':'video'},files={'file':('nature.mp4',body,'video/mp4')})
    assert response.status_code==400
    response=client.post('/api/uploads',headers=headers,data={'kind':'music'},files={'file':('track.mp3',b'not media')})
    assert response.status_code==400

def test_cleanup_protects_active_sources(media):
    import time
    path=DATA/'retention-check';path.mkdir(exist_ok=True);source=path/'source';source.write_bytes(b'fixture')
    expired=time.time()-100000;os.utime(path,(expired,expired))
    job={'id':'retention-job','status':'queued','video_path':str(source),'tracks':[]}
    put('job','retention-job',job);cleanup();assert source.exists()
    job['status']='failed';put('job','retention-job',job);cleanup();assert not path.exists()

def test_bundled_library_integrity():
    import hashlib
    tracks=json.loads(Path('library/manifest.json').read_text())
    assert 20<=len(tracks)<=50
    assert len({t['id'] for t in tracks})==len(tracks)
    for track in tracks:
        path=Path('library')/track['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==track['sha256']
        assert track['license']=='CC0 1.0 Universal'
        assert track['source'].startswith('https://opengameart.org/content/')
        assert track['duration']>2
    client=TestClient(app)
    assert len(client.get('/api/library').json())==30
    response=client.get('/api/library/'+tracks[0]['id']+'/audio',headers={'Range':'bytes=0-1023'})
    assert response.status_code==206
    assert len(response.content)==1024

def test_real_library_music_export(media):
    tracks=json.loads(Path('library/manifest.json').read_text())
    selected=[{**t,'path':str((Path('library')/t['file']).resolve())} for t in (tracks[0],tracks[8],tracks[16])]
    id='real-library-export';(DATA/id).mkdir(exist_ok=True)
    job={'id':id,'owner':'a'*64,'video_path':str(media[0]),'tracks':selected,'seconds':7,'resolution':'720p','channel':'Forest Radio','color':'#b7c9a8','font':'sans','position':'left','nature_volume':0,'logo_path':None,'status':'rendering'}
    put('job',id,job);render(job)
    assert get('job',id)['status']=='completed'
    info=probe(DATA/id/'output.mp4');assert float(info['format']['duration'])==7
    # Repeat boundary: verify the crossfade was retained even with fractional sample durations.
    import wave, array
    with wave.open(str(DATA/id/'playlist.wav')) as playlist:
        expected=playlist.getnframes()-min(2*playlist.getframerate(),playlist.getnframes()//4)
    with wave.open(str(DATA/id/'music.wav')) as music:
        assert music.getnframes()==expected
        channels=music.getnchannels();samples=array.array('h',music.readframes(music.getnframes()))
    for channel in range(channels):
        signal=samples[channel::channels]
        local=[abs(a-b) for a,b in zip(signal[:10000],signal[1:10001])]
        p99=sorted(local)[int(len(local)*.99)]
        assert abs(signal[0]-signal[-1])<=max(100,5*p99), 'Audible discontinuity at music repeat boundary'

def test_long_render_storage_budget_and_daily_limit(media,monkeypatch):
    import time
    from backend.app import main
    monkeypatch.setattr(main,'MAX_STORAGE',4*1024**3)
    token='c'*64;headers={'Authorization':'Bearer '+token};client=TestClient(app)
    put('asset','budget-video',{'id':'budget-video','owner':token,'kind':'video','path':str(media[0])})
    put('asset','budget-music',{'id':'budget-music','owner':token,'kind':'music','path':str(media[1]),'title':'Original','artist':'Test','license':'original','duration':3})
    request={'video_id':'budget-video','music_ids':['budget-music'],'channel':'Budget check','seconds':10800,'resolution':'1080p'}
    response=client.post('/api/jobs',headers=headers,json=request)
    assert response.status_code==202,response.text
    job=get('job',response.json()['id'])
    assert job['storage_reservation']<=4*1024**3
    assert job['output_limit']<10800*2_000_000
    client.post('/api/jobs/'+job['id']+'/cancel',headers=headers)
    day='render-budget:'+time.strftime('%Y-%m-%d',time.gmtime())
    r.set(day,main.DAILY_SECONDS)
    assert client.post('/api/jobs',headers=headers,json=request).status_code==429

def test_chunked_upload_retry_and_finish(media):
    client=TestClient(app);token='e'*64;headers={'Authorization':'Bearer '+token}
    # Trailing padding creates several chunks while retaining a real readable MP4.
    data=media[0].read_bytes()+b'\0'*(2*1024*1024)
    response=client.post('/api/upload-sessions',headers=headers,json={'filename':'nature.mp4','size':len(data),'kind':'video'})
    assert response.status_code==200
    session=response.json();id=session['id'];chunk_size=session['chunk_size']
    assert client.get('/api/upload-sessions/'+id,headers={'Authorization':'Bearer '+'f'*64}).status_code==404
    assert client.put('/api/upload-sessions/'+id+'/0',headers=headers,content=data[:100]).status_code==400
    offset=0
    while offset<len(data):
        chunk=data[offset:offset+chunk_size]
        route=f'/api/upload-sessions/{id}/{offset}'
        response=client.put(route,headers=headers,content=chunk)
        assert response.status_code==200,response.text
        # Lost acknowledgements can retry the exact chunk without duplicating bytes.
        repeated=client.put(route,headers=headers,content=chunk)
        assert repeated.json()['offset']==response.json()['offset']
        offset=response.json()['offset']
    response=client.post('/api/upload-sessions/'+id+'/finish',headers=headers)
    assert response.status_code==200,response.text
    asset=response.json()
    assert client.post('/api/upload-sessions/'+id+'/finish',headers=headers).json()['id']==asset['id']
    assert Path(get('asset',asset['id'])['path']).read_bytes()==data
    assert client.get('/api/upload-sessions/'+id,headers=headers).json()['offset']==len(data)
