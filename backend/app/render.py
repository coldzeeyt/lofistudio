"""Bounded-memory FFmpeg pipeline. The visualizer consumes the final music mix."""
import json, subprocess, time, queue, threading, wave
from pathlib import Path
from .store import r, update
from .config import DATA
class Cancelled(Exception): pass

def probe(path):
    p = subprocess.run(['ffprobe','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm,wav,mp3,ogg,flac,aac,png_pipe,jpeg_pipe,webp_pipe','-v','error','-show_format','-show_streams','-of','json',str(path)], capture_output=True, text=True, timeout=20)
    if p.returncode: raise ValueError('File is not readable media')
    return json.loads(p.stdout)

def run(args, job, stage, start, span, duration):
    safe_args=[]
    for arg in args:
        if arg=='-i': safe_args += ['-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm,wav,mp3,ogg,flac,aac,png_pipe,jpeg_pipe,webp_pipe']
        safe_args.append(arg)
    args=safe_args
    update(job, stage=stage, progress=start)
    log = DATA / job / 'ffmpeg.log'
    with log.open('w') as errors:
        p = subprocess.Popen(['ffmpeg','-hide_banner','-y','-nostdin','-filter_complex_threads','1','-threads','2',*args,'-progress','pipe:1','-nostats'], stdout=subprocess.PIPE, stderr=errors, text=True)
        deadline = time.monotonic() + max(300, duration*8)
        try:
            lines=queue.Queue()
            def read_progress():
                for line in p.stdout: lines.put(line)
                lines.put(None)
            threading.Thread(target=read_progress,daemon=True).start()
            while True:
                if r.exists(f'cancel:{job}'): raise Cancelled()
                if time.monotonic() > deadline: raise RuntimeError('Render time limit reached')
                try: line=lines.get(timeout=1)
                except queue.Empty: continue
                if line is None: break
                if line.startswith('out_time_us='):
                    try: seconds = int(line.split('=')[1])/1e6
                    except ValueError: continue
                    update(job, progress=min(start+span, start+span*seconds/max(duration, .1)))
            if p.wait() != 0: raise RuntimeError('FFmpeg failed: '+log.read_text()[-1500:])
        finally:
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired: p.kill(); p.wait()

def render(job):
    id = job['id']; directory = DATA/id; directory.mkdir(exist_ok=True)
    video = Path(job['video_path']); tracks = job['tracks']; seconds = job['seconds']
    w,h = (1280,720) if job['resolution']=='720p' else (1920,1080)
    source = probe(video); d = float(source['format']['duration']); fade = min(1.,d/4)
    # Tail/head dissolve followed by the middle: both joins match adjacent source frames.
    cycle = directory/'cycle.mp4'; period = d-fade
    vf = f"fps=24,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p,split=3[a][b][c];[a]trim=start={d-fade}:end={d},setpts=PTS-STARTPTS[tail];[b]trim=end={fade},setpts=PTS-STARTPTS[head];[tail][head]blend=all_expr='A*(1-T/{fade})+B*T/{fade}'[seam];[c]trim=start={fade}:end={d-fade},setpts=PTS-STARTPTS[mid];[seam][mid]concat=n=2:v=1:a=0,eq=saturation=0.85:brightness=-0.025:contrast=1.02[v]"
    run(['-i',str(video),'-filter_complex',vf,'-map','[v]','-an','-c:v','libx264','-threads:v','2','-preset','veryfast','-crf','20',str(cycle)],id,'Preparing seamless nature loop',0,10,period)
    normalized=[]
    for i,t in enumerate(tracks):
        out=directory/f'track-{i}.wav'; td=float(probe(t['path'])['format']['duration'])
        run(['-i',t['path'],'-vn','-af','loudnorm=I=-18:TP=-2:LRA=11','-ar','48000','-ac','2',str(out)],id,'Balancing music',10+5*i/len(tracks),5/len(tracks),td)
        normalized.append(out)
    # Crossfade all selected tracks, then turn playlist into a seamless repeating cycle.
    inputs=[]; graph=[]; label='0:a'; total=float(probe(normalized[0])['format']['duration'])
    for path in normalized: inputs+=['-i',str(path)]
    for i in range(1,len(normalized)):
        td=float(probe(normalized[i])['format']['duration']); f=min(2.,td/4,total/4)
        graph.append(f'[{label}][{i}:a]acrossfade=d={f}:c1=tri:c2=tri[a{i}]');label=f'a{i}';total+=td-f
    playlist=directory/'playlist.wav'
    args=inputs+(['-filter_complex',';'.join(graph),'-map',f'[{label}]'] if graph else ['-map','0:a'])+['-c:a','pcm_s16le',str(playlist)]
    run(args,id,'Creating playlist',15,5,total)
    with wave.open(str(playlist)) as wav:
        samples=wav.getnframes(); sample_rate=wav.getframerate()
    fade_samples=min(2*sample_rate,samples//4); af=fade_samples/sample_rate
    total=samples/sample_rate; music=directory/'music.wav'
    graph=f"[0:a]atrim=start_sample={samples-fade_samples}:end_sample={samples},asetpts=PTS-STARTPTS[tail];[1:a]atrim=end_sample={fade_samples},asetpts=PTS-STARTPTS[head];[tail][head]acrossfade=ns={fade_samples}:c1=tri:c2=tri[seam];[2:a]atrim=start_sample={fade_samples}:end_sample={samples-fade_samples},asetpts=PTS-STARTPTS[mid];[seam][mid]concat=n=2:v=0:a=1[a]"
    run(['-i',str(playlist),'-i',str(playlist),'-i',str(playlist),'-filter_complex',graph,'-map','[a]',str(music)],id,'Smoothing playlist loop',20,5,total-af)
    (directory/'channel.txt').write_text(job['channel'],encoding='utf-8')
    inputs=['-stream_loop','-1','-i',str(cycle),'-stream_loop','-1','-i',str(music)]
    graph=['[1:a]asplit=3[music][visual][waveaudio]',f'[visual]showfreqs=s={w//2}x100:mode=bar:ascale=log:fscale=log:colors={job["color"]}:rate=24,format=rgba,colorkey=black:0.08:0.1[viz]',f'[waveaudio]showwaves=s={w//2}x60:mode=line:rate=24:colors={job["color"]}:scale=sqrt,format=rgba,colorkey=black:0.08:0.1[wave]',f'[0:v][viz]overlay=(W-w)/2:H-h-125[spectrum]',f'[spectrum][wave]overlay=(W-w)/2:H-h-45[base]']
    if job['nature_volume']>0 and any(s['codec_type']=='audio' for s in source['streams']):
        inputs+=['-stream_loop','-1','-i',str(video)]
        graph += [f'[2:a]volume={job["nature_volume"]}[nature]','[music][nature]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[audio]']
    else: graph+=['[music]anull[audio]']
    font={'sans':'DejaVuSans.ttf','serif':'DejaVuSerif.ttf','mono':'DejaVuSansMono.ttf'}[job['font']]
    x={'left':'50','center':'(w-text_w)/2','right':'w-text_w-50'}[job['position']]
    graph += [f"[base]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/{font}:textfile={directory}/channel.txt:expansion=none:fontcolor={job['color']}:fontsize={h//22}:x={x}:y=50:shadowcolor=black@0.6:shadowx=2:shadowy=2[branded]"]
    if job.get('logo_path'):
        inputs+=['-loop','1','-i',job['logo_path']]
        # Input index determined by actual optional nature source.
        index=3 if job['nature_volume']>0 and any(s['codec_type']=='audio' for s in source['streams']) else 2
        graph += [f'[{index}:v]scale=100:100:force_original_aspect_ratio=decrease[logo]','[branded][logo]overlay=W-w-50:120[out]']
    else: graph+=['[branded]null[out]']
    output_limit=job.get('output_limit',seconds*(2_000_000 if h==1080 else 1_000_000))
    video_rate=max(400_000,int((output_limit*8/seconds-192_000)*0.9))
    run(inputs+['-filter_complex',';'.join(graph),'-map','[out]','-map','[audio]','-t',str(seconds),'-c:v','libx264','-threads:v','2','-preset','veryfast','-crf','20','-maxrate',str(video_rate),'-bufsize',str(video_rate*2),'-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart','-fs',str(output_limit),str(directory/'output.mp4')],id,'Rendering MP4',25,74,seconds)
    exported=probe(directory/'output.mp4')
    if abs(float(exported['format']['duration'])-seconds)>0.25: raise RuntimeError('Output was truncated by the size limit; choose shorter duration or lower resolution.')
    update(id,status='completed',stage='Ready to download',progress=100)
