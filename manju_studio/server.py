#!/usr/bin/env python3
"""Local, single-user production studio. No external Python dependencies."""
import ast, copy, hashlib, json, mimetypes, os, pathlib, re, shutil, subprocess, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, urlencode
from urllib.request import Request, urlopen

APP=pathlib.Path(__file__).resolve().parent
ROOT=APP.parent
SERIES=ROOT/'raft_series'
DB=pathlib.Path(os.environ.get('STUDIO_DB',str(APP/'data/state.json')))
LOCK=threading.RLock()
WAKE=threading.Event()
TOKEN=uuid.uuid4().hex

def uid(): return uuid.uuid4().hex[:12]
def save():
    with LOCK:
        tmp=DB.with_suffix('.tmp'); tmp.write_text(json.dumps(STATE,ensure_ascii=False,indent=2)); tmp.replace(DB)
def asset(path):
    p=pathlib.Path(path).resolve()
    return str(p.relative_to(ROOT)) if p.exists() and p.is_relative_to(ROOT) else ''
def safe(path):
    p=(ROOT/str(path)).resolve()
    if not p.is_relative_to(ROOT) or not p.is_file(): raise ValueError('文件不存在或不在工作区内')
    if p.suffix.lower() not in {'.mp4','.mov','.wav','.mp3','.m4a','.png','.jpg','.jpeg','.webp','.json','.srt','.zip'}: raise ValueError('不支持的媒体类型')
    return p

def literals(path,name):
    tree=ast.parse(path.read_text())
    for n in tree.body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets): return ast.literal_eval(n.value)
    return {}
def signature(value): return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def active(pid,eid=None):
    return any(j['status'] in ('queued','running') and j['project']==pid and (eid is None or j['episode']==eid) for j in STATE['jobs'])
def validate_shot(s,p):
    if not isinstance(s.get('prompt',''),str) or not isinstance(s.get('dialogue',''),str): raise ValueError('描述和台词必须为文本')
    if not .5<=float(s.get('duration',5.166667))<=120: raise ValueError('镜头时长需在0.5至120秒')
    if not 0<=int(s.get('seed',0))<2**63: raise ValueError('随机种子超出范围')
    if s.get('review','pending') not in ('pending','approved','rejected'): raise ValueError('无效审核状态')
    ids={c['id'] for c in p['characters']}
    if not isinstance(s.get('characters',[]),list) or any(c not in ids for c in s.get('characters',[])): raise ValueError('出镜角色不存在')
    if s.get('dialogue') and s.get('character') not in ids: raise ValueError('请先选择存在的说话角色')
    for k in ('video','audio','continuity'):
        if s.get(k): safe(s[k])
def newshot(title='',prompt=''):
    return dict(id=uid(),title=title,prompt=prompt,dialogue='',character='lu_chen',duration=5.166667,seed=int(time.time())%1000000000,characters=['lu_chen'],continuity='',video='',audio='',review='pending',notes='',versions=[])
def bootstrap():
    chars=json.loads((SERIES/'characters.json').read_text())['characters']; manifest=json.loads((SERIES/'assets/characters/manifest.json').read_text())['characters']
    for c in chars:
        m=manifest.get(c['id'],{}); c['image']=asset(SERIES/'assets/characters'/m.get('identity_master','missing')); c['locked']=bool(m.get('approved')); c['voice']={'mode':'preset','reference':'','reference_text':'','speaker':'serena','locked':False}
    chars.append(dict(id='system',name='系统',age=0,identity='画外系统提示音',costume='',image='',locked=True,voice={'mode':'clone','reference':'','reference_text':'','speaker':'vivian','locked':False}))
    for c in chars:
        if c['id'] in ('lu_chen','system'):
            c['voice']['mode']='clone'
            c['voice']['reference']=asset(SERIES/'outputs/voice_references'/f"{c['id']}_ep1.m4a")
            c['voice']['reference_text']='这是哪儿？' if c['id']=='lu_chen' else '全球海域求生，现在开始。'
    p=dict(id=uid(),title='全民海域：我的木筏无限升级',description='从一块木筏，到海上城市。',bible=(SERIES/'series_bible.md').read_text(),style=json.loads((SERIES/'assets/characters/manifest.json').read_text())['visual_style'],characters=chars,episodes=[])
    beats=literals(SERIES/'generate_episodes03_10_h3.py','EPISODES')
    dialogues=json.loads((ROOT/'retake_voice_payload/dialogue.json').read_text()) if (ROOT/'retake_voice_payload/dialogue.json').exists() else []
    for n in range(1,11):
        d=SERIES/'outputs'/f'episode{n:02d}_full'; title=beats.get(n,{}).get('title',{1:'海上醒来',2:'第一次升级'}.get(n,''))
        ep=dict(id=uid(),number=n,title=title,script='',shots=[],exports=[],bgm='',bgm_db=-24,voice_db=0)
        files=sorted(d.glob('[0-9][0-9]_*.mp4'))
        if n==1: files=sorted((SERIES/'outputs/episode01_pilot').glob('[0-9][0-9]_*.mp4'))+files
        existing=dict(literals(SERIES/'generate_episode02_h3.py','SHOTS')) if n==2 else {}
        for i,f in enumerate(files):
            detail=existing.get(f.stem,'') or (beats[n]['beats'][min(i//3,7)] if n in beats else '')
            s=newshot(f.stem,detail); s['video']=asset(f)
            if n in beats: s['characters']+=['su_wanqing'] if n>=3 else []
            if n>=8: s['characters']+=['tang_tang']
            match=next((x for x in dialogues if x['id']==f'{n:02d}_{f.stem}'),None)
            if match:
                s['dialogue']=match['text']; s['character']=match['character']
                s['audio']=asset(SERIES/'outputs/retake_ep03_ep04/voices'/f"{match['id']}.wav")
                s['notes']='旧版台词定位与音色待复核，配音未锁定。'
            ep['shots'].append(s)
        ep['script']='\n\n'.join(beats.get(n,{}).get('beats',[]))
        for f in sorted(d.glob(f'episode{n:02d}_full_v*.mp4')): ep['exports'].append(dict(file=asset(f),created=f.stat().st_mtime,status='旧版 · 待审核'))
        p['episodes'].append(ep)
    return dict(version=1,projects=[p],settings={'comfy_url':'http://100.80.251.102:8188','ssh_host':'ubuntu@100.80.251.102','remote_root':'/home/ubuntu/manju','paused':False},jobs=[])

DB.parent.mkdir(exist_ok=True)
STATE=json.loads(DB.read_text()) if DB.exists() else bootstrap()
for k,v in {'text_url':'','text_model':''}.items(): STATE['settings'].setdefault(k,v)
for j in STATE['jobs']:
    if j['status']=='running': j['status']='queued' if j.get('prompt_id') else 'interrupted'; j['message']='应用重启，请重试或恢复已提交任务'
save()

def project(pid): return next(p for p in STATE['projects'] if p['id']==pid)
def episode(p,eid): return next(e for e in p['episodes'] if e['id']==eid)
def shot(e,sid): return next(s for s in e['shots'] if s['id']==sid)
def ffmpeg():
    candidates=list(ROOT.glob('work/reference_env/lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*'))
    candidates += list(ROOT.glob('manju/.venv/lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*'))
    path=shutil.which('ffmpeg') or (str(candidates[0]) if candidates else '')
    if not path: raise ValueError('未找到 FFmpeg，请安装后重试')
    return path

def run(args,timeout=600,input=None):
    r=subprocess.run([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,input=input)
    if r.returncode: raise RuntimeError(r.stderr.decode(errors='replace')[-2500:])
    return r.stdout

def remote(args,timeout=15,input=None):
    cfg=STATE['settings']; host=cfg['ssh_host']
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+',host) or host.startswith('-'): raise ValueError('SSH 地址格式错误')
    identity=pathlib.Path.home()/'.ssh/id_ed25519_manju_studio'
    keyargs=['-i',str(identity)] if identity.exists() else []
    return run(['ssh']+keyargs+['-o','BatchMode=yes','-o','ConnectTimeout=8',host]+args,timeout,input=input)

def comfy(path,data=None,timeout=12):
    base=STATE['settings']['comfy_url'].rstrip('/')
    req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=timeout) as r: return json.load(r)

def upload_comfy(file):
    f=safe(file); boundary='studio'+uid(); name=uid()+f.suffix
    data=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n').encode()+f.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    req=Request(STATE['settings']['comfy_url'].rstrip('/')+'/upload/image',data=data,headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    with urlopen(req,timeout=60) as r:
        result=json.load(r); return '/'.join(x for x in [result.get('subfolder'),result['name']] if x)

def upload_comfy_audio(file):
    """Upload a reference voice for MiniMax H3's native audio conditioning."""
    f=safe(file); boundary='studio'+uid(); name=uid()+f.suffix
    data=(f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="{name}"\r\nContent-Type: audio/*\r\n\r\n').encode()+f.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    req=Request(STATE['settings']['comfy_url'].rstrip('/')+'/upload/audio',data=data,headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    with urlopen(req,timeout=120) as r:
        result=json.load(r); return '/'.join(x for x in [result.get('subfolder'),result['name']] if x)

def graph(p,e,s,j):
    # Reuse the locally installed, previously executed workflow; never guess node schemas.
    import sys
    sys.path.insert(0,str(SERIES))
    import extend_episode01_h3 as engine
    refs=[]
    for cid in s['characters']:
        c=next((c for c in p['characters'] if c['id']==cid),None)
        if not c or not c['locked'] or not c['image']: raise ValueError('出镜角色必须有已锁定定妆图')
        refs.append((c,upload_comfy(c['image'])))
    if not refs: raise ValueError('请至少选择一位出镜角色')
    previous=s.get('continuity')
    idx=next(i for i,x in enumerate(e['shots']) if x['id']==s['id'])
    if not previous and idx>0:
        prev=e['shots'][idx-1]
        if not prev.get('video'): raise ValueError('上一镜头尚未生成，请先完成上一镜头或上传衔接图')
        dest=APP/'renders'/f"{j['id']}_tail.png"
        run([ffmpeg(),'-y','-sseof','-0.15','-i',safe(prev['video']),'-frames:v','1',dest])
        previous=asset(dest)
    continuity=upload_comfy(previous) if previous else refs[0][1]
    g=engine.workflow(j['id'],s['seed'],continuity,s['prompt'])
    g['5']['inputs']['image']=refs[0][1]
    desc=f"{p['style']}. Picture 1 is the exact identity of {refs[0][0]['name']}. Picture 2 is scene continuity. "
    for k,(c,img) in enumerate(refs[1:],start=2):
        key=str(15+k); g[key]={'class_type':'LoadImage','inputs':{'image':img}}; g['6']['inputs'][f'ref_images.ref_image_{k}']=[key,0]; desc+=f'Picture {k+1} is {c["name"]}. '
    desc+='Preserve reference identities, costume and props. Continuous natural action, stable camera. '
    speaker=next((c for c in p['characters'] if c['id']==s.get('character')),None)
    voice=speaker.get('voice',{}) if speaker else {}
    if s.get('dialogue'):
        desc+=f'The on-screen speaker is {speaker["name"] if speaker else "the character"}. Speak the following exact Mandarin dialogue with synchronized natural lip movement: "{s["dialogue"]}". '
        desc+='The voice must come from the speaking character, with no narrator, no repeated words, no extra dialogue, and no subtitles. '
        if voice.get('reference') and voice.get('mode')=='clone':
            ref_audio=upload_comfy_audio(voice['reference'])
            key='20'; g[key]={'class_type':'LoadAudio','inputs':{'audio':ref_audio}}
            g['6']['inputs']['ref_audios.ref_audio_0']=[key,0]
            desc+='Match the timbre, pitch and speaking style of Audio 1. '
    else:
        desc+='No speech, no narration, no music, no subtitles. '
    desc+='Generate only natural diegetic sound effects and atmosphere; no background music. '
    g['6']['inputs'].update(prompt=desc+s['prompt'],length=124,width=480,height=864)
    g['15']['inputs']['filename_prefix']='manju_studio/'+j['id']
    return g

def updatejob(j,**kw):
    with LOCK: j.update(kw); save()
def checkcancel(j):
    if j.get('cancel'): raise ValueError('任务已取消；远端已提交的计算可能仍在完成，未中断他人队列')

def genvideo(j,p,e,s):
    if not j.get('prompt_id'):
        updatejob(j,message='上传角色参考并检查衔接')
        result=comfy('/prompt',{'prompt':graph(p,e,s,j),'client_id':'manju-studio'},60)
        if result.get('node_errors') or not result.get('prompt_id'): raise ValueError(str(result))
        updatejob(j,prompt_id=result['prompt_id'],message='4090 已接收，等待镜头生成')
    deadline=time.time()+7200
    while time.time()<deadline:
        checkcancel(j)
        h=comfy('/history/'+j['prompt_id']).get(j['prompt_id'])
        if h:
            if h.get('status',{}).get('status_str')=='error': raise RuntimeError(str(h['status']))
            if h.get('status',{}).get('completed'):
                outs=h['outputs'].get('15',{}); items=outs.get('videos') or outs.get('images') or []
                if not items: raise ValueError('生成结束但未返回视频文件')
                out=APP/'renders'/f"{j['id']}.mp4"
                with urlopen(STATE['settings']['comfy_url'].rstrip('/')+'/view?'+urlencode(items[0]),timeout=120) as r: out.write_bytes(r.read())
                with LOCK:
                    if s.get('video'): s['versions'].append(s['video'])
                    s.update(video=asset(out),review='pending'); save()
                return
        time.sleep(4)
    raise TimeoutError('等待超过两小时；可重试以继续查询同一任务')

def voicejob(j,p,e,s):
    c=next(x for x in p['characters'] if x['id']==s['character']); v=c['voice']
    if not v['locked'] and j['kind']!='voice_sample': raise ValueError('请先在角色库试听并锁定音色配置')
    if not s['dialogue'].strip(): raise ValueError('对白为空')
    ref=None
    if v['mode']=='clone':
        if not v.get('reference') or not v.get('reference_text'): raise ValueError('克隆需要参考音频及准确原文')
        wav=APP/'renders'/f"{j['id']}_ref.wav"
        run([ffmpeg(),'-y','-i',safe(v['reference']),'-ac','1','-ar','24000',wav]); ref=wav.read_bytes()
    import base64, shlex
    root=STATE['settings']['remote_root']; code=(APP/'voice_worker.py').read_text()
    payload={'text':s['dialogue'],'voice':v,'root':root,'reference':base64.b64encode(ref).decode() if ref else None}
    encoded=base64.b64encode(json.dumps(payload).encode()).decode()
    program=code
    cmd=shlex.quote(root+'/voice/.venv/bin/python')+' -c '+shlex.quote(program)
    updatejob(j,message='本地4090配音中；模型缺失时会明确报错')
    raw=remote([cmd],timeout=900,input=encoded.encode()).decode(errors='replace')
    marker=raw.split('STUDIO_AUDIO:')[-1].strip()
    wav=APP/'renders'/f"{j['id']}.wav"; wav.write_bytes(base64.b64decode(marker,validate=True))
    with LOCK:
        if j['kind']=='voice_sample': c['voice']['sample']=asset(wav); c['voice']['sample_text']=s['dialogue']; j['result_audio']=asset(wav)
        else: s['audio']=asset(wav); s['audio_signature']=signature({'voice':v,'text':s['dialogue'],'character':c['id']}); s['review']='pending'
        save()

def exportjob(j,p,e,s=None):
    shots=e['shots']
    if not shots: raise ValueError('本集没有镜头')
    if any(x['review']!='approved' or not x.get('video') for x in shots): raise ValueError('请逐镜头审核通过后再导出')
    if any(x['dialogue'].strip() and not x.get('audio') for x in shots): raise ValueError('存在未生成配音的对白')
    outdir=APP/'renders'/j['id']; outdir.mkdir(exist_ok=True); parts=[]; captions=[]; cursor=0
    for i,x in enumerate(shots):
        checkcancel(j); updatejob(j,message=f'处理镜头 {i+1}/{len(shots)}')
        dest=outdir/f'{i:03d}.mp4'; duration=float(x['duration']); args=[ffmpeg(),'-y','-i',safe(x['video'])]
        if x.get('audio'):
            # Refuse truncation. The user must extend the shot or shorten dialogue explicitly.
            import wave
            a=outdir/f'{i:03d}_voice.wav'
            run([ffmpeg(),'-y','-i',safe(x['audio']),'-ac','1','-ar','48000','-c:a','pcm_s16le',a])
            with wave.open(str(a)) as w: ad=w.getnframes()/w.getframerate()
            if ad+.15>duration: raise ValueError(f'镜头{i+1}配音长于画面，请缩短台词或调整镜头时长，禁止截断对白')
            args+=['-i',a]
        else: args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        args+=['-map','0:v:0','-map','1:a:0','-vf','scale=480:864:force_original_aspect_ratio=decrease,pad=480:864:(ow-iw)/2:(oh-ih)/2,fps=24,tpad=stop_mode=clone:stop_duration=120','-af',f'adelay=150:all=1,apad,volume={float(e.get("voice_db",0))}dB','-t',str(duration),'-c:v','libx264','-preset','fast','-crf','20','-c:a','aac','-ar','48000','-ac','2',dest]
        run(args); parts.append(dest)
        if x['dialogue'].strip(): captions.append((cursor+.15,cursor+duration-.1,x['dialogue']))
        cursor+=duration
    listing=outdir/'concat.txt'; listing.write_text('\n'.join("file '"+x.name+"'" for x in parts))
    joined=outdir/'joined.mp4'; run([ffmpeg(),'-y','-f','concat','-safe','0','-i',listing,'-c','copy',joined])
    final=outdir/f'episode{e["number"]:02d}_studio.mp4'
    if e.get('bgm'):
        run([ffmpeg(),'-y','-i',joined,'-stream_loop','-1','-i',safe(e['bgm']),'-filter_complex',f'[0:a]asplit=2[v][sc];[1:a]volume={float(e.get("bgm_db",-24))}dB[m];[m][sc]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=350[d];[v][d]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]','-map','0:v','-map','[a]','-c:v','copy','-c:a','aac','-t',str(cursor),final])
    else: shutil.copyfile(joined,final)
    def stamp(t):
        ms=round(t*1000); return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'
    sub=final.with_suffix('.srt'); sub.write_text('\n\n'.join(f'{i+1}\n{stamp(a)} --> {stamp(b)}\n{t}' for i,(a,b,t) in enumerate(captions)),encoding='utf-8')
    with LOCK:
        e['exports'].append(dict(file=asset(final),subtitle=asset(sub),created=time.time(),status='已导出 · 独立配音',duration=cursor)); save()

def textjob(j,p,e,s=None):
    cfg=STATE['settings']; base=cfg.get('text_url','').rstrip('/'); model=cfg.get('text_model','')
    if not base or not model: raise ValueError('请先在设置中连接编剧模型，可使用本地或你自己的文本服务')
    if urlparse(base).scheme not in ('http','https'): raise ValueError('文本服务地址无效')
    bible=p['bible']; cast=[{'id':c['id'],'name':c['name'],'role':c.get('role',''),'age':c['age']} for c in p['characters']]
    previous=[{'number':x['number'],'title':x['title'],'script':x['script'][-3500:]} for x in p['episodes'] if x['number']<e['number']][-2:]
    context=json.dumps({'series':p['title'],'bible':bible,'characters':cast,'previous':previous,'episode':e['number'],'title':e['title'],'script':e['script'],'request':j.get('text','')},ensure_ascii=False)
    system='你是中文漫剧编剧。保持人物身份、资源、场景和剧情因果连续，避免重复台词。所有角色按给定年龄和人设创作。输出中文。'
    if j['kind']=='script':
        system+='根据资料写约两分钟单集剧本，包含开场钩子、目标、阻碍、反转和下集悬念。严格区分动作与准确对白。只输出剧本正文。'
    else:
        system+='把剧本改写为约24个连续镜头，每镜头约5.166667秒、一个主要动作，每条对白只安排一次。只输出JSON数组，字段title,prompt,dialogue,character,characters,duration。prompt为画面描述，不含配音指令；dialogue为准确中文台词，短到可在5秒说完；character和characters必须用资料中的角色id；无对白则dialogue为空。characters是出镜角色数组，不含system。'
    headers={'Content-Type':'application/json'}
    secret=APP/'data/text_service.key'
    if secret.exists() and secret.read_text().strip(): headers['Authorization']='Bearer '+secret.read_text().strip()
    req=Request(base+'/chat/completions',data=json.dumps({'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':context}],'temperature':.65}).encode(),headers=headers)
    updatejob(j,message='编剧模型处理中；结果只保存为草稿，不会自动生成视频')
    with urlopen(req,timeout=240) as r: response=json.load(r)
    result=response['choices'][0]['message']['content']; checkcancel(j)
    if j['kind']=='script':
        with LOCK:
            e.setdefault('script_versions',[]).append({'script':e['script'],'created':time.time()}); e['script']=result; save()
    else:
        stripped=re.sub(r'^```(?:json)?\s*|\s*```$','',result.strip())
        rows=json.loads(stripped)
        if not isinstance(rows,list) or not 1<=len(rows)<=100: raise ValueError('模型未返回1至100个镜头的JSON数组')
        prepared=[]
        for row in rows:
            sh=newshot(row.get('title','新镜头'),row.get('prompt',''))
            for k in ('dialogue','character','characters','duration'):
                if k in row: sh[k]=row[k]
            validate_shot(sh,p); prepared.append(sh)
        with LOCK:
            e['shots'].extend(prepared); save()

def worker():
    while True:
        with LOCK:
            j=next((x for x in STATE['jobs'] if x['status']=='queued'),None) if not STATE['settings']['paused'] else None
            if j: updatejob(j,status='running',started=time.time())
        if not j: WAKE.wait(2); WAKE.clear(); continue
        try:
            with LOCK:
                p=project(j['project']); e=episode(p,j['episode']) if j.get('episode') else None; s=shot(e,j['shot']) if j.get('shot') else None
                if j['kind']=='voice_sample': s={'character':j['character'],'dialogue':j['text']}
            checkcancel(j)
            {'video':genvideo,'voice':voicejob,'voice_sample':voicejob,'export':exportjob,'script':textjob,'storyboard':textjob}[j['kind']](j,p,e,s)
            checkcancel(j); updatejob(j,status='done',message='完成',finished=time.time())
        except Exception as exc: updatejob(j,status='cancelled' if j.get('cancel') else 'failed',message=str(exc)[:4000],finished=time.time())

def health():
    result={'checked':time.time(),'ffmpeg':False,'comfy':False,'ssh':False,'clone':False,'custom_voice':False,'messages':[]}
    try: result['ffmpeg']=bool(ffmpeg())
    except Exception as e: result['messages'].append(str(e))
    try:
        stats=comfy('/system_stats',timeout=5); result['comfy']=True; result['devices']=stats.get('devices',[])
        result['queue']=comfy('/queue',timeout=5)
    except Exception as e: result['messages'].append('视频服务连接失败：'+str(e))
    try:
        import shlex
        root=STATE['settings']['remote_root']
        cmd='test -s '+shlex.quote(root+'/voice/models/Qwen3-TTS-Base/model.safetensors')+' && echo CLONE; test -s '+shlex.quote(root+'/voice/models/Qwen3-TTS-CustomVoice/model.safetensors')+' && echo CUSTOM; echo SSH_OK'
        data=remote([cmd]).decode(); result.update(ssh='SSH_OK' in data,clone='CLONE' in data,custom_voice='CUSTOM' in data)
    except Exception as e: result['messages'].append('配音连接失败：需要 Mac 到服务器的 SSH 公钥登录。'+str(e))
    return result

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,obj,status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def allowed(self):
        return self.headers.get('Host','').split(':')[0] in ('127.0.0.1','localhost','100.80.251.102')
    def do_GET(self):
        if not self.allowed(): return self.respond({'error':'仅允许本机访问'},403)
        try:
            parsed=urlparse(self.path)
            if parsed.path=='/api/state':
                with LOCK: return self.respond({**STATE,'token':TOKEN})
            if parsed.path=='/api/health': return self.respond(health())
            if parsed.path=='/api/backup': return self.respond(STATE)
            if parsed.path=='/media': path=safe(parse_qs(parsed.query).get('path',[''])[0])
            else:
                path=(APP/'static'/('index.html' if parsed.path=='/' else parsed.path.lstrip('/'))).resolve()
                if not path.is_relative_to(APP/'static') or not path.is_file(): return self.respond({'error':'找不到页面'},404)
            size=path.stat().st_size; start=0; end=size-1; status=200
            r=self.headers.get('Range','')
            if r:
                m=re.fullmatch(r'bytes=(\d*)-(\d*)',r)
                if not m: return self.respond({'error':'无效范围'},416)
                start=int(m[1]) if m[1] else max(0,size-int(m[2])); end=min(size-1,int(m[2])) if m[1] and m[2] else size-1
                if start>end: return self.respond({'error':'超出范围'},416)
                status=206
            self.send_response(status); self.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream'); self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Length',str(end-start+1)); self.send_header('X-Content-Type-Options','nosniff')
            if status==206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            with path.open('rb') as f:
                f.seek(start); left=end-start+1
                while left:
                    block=f.read(min(left,1024*1024))
                    if not block: break
                    self.wfile.write(block); left-=len(block)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception as e: self.respond({'error':str(e)},400)
    def do_POST(self):
        if not self.allowed() or self.headers.get('X-Studio-Token')!=TOKEN: return self.respond({'error':'请刷新本机页面后重试'},403)
        try:
            size=int(self.headers.get('Content-Length',0))
            if size>100*1024*1024: raise ValueError('文件限100MB')
            raw=self.rfile.read(size); parsed=urlparse(self.path)
            if parsed.path=='/api/upload':
                name=parse_qs(parsed.query).get('name',['asset.png'])[0]; ext=pathlib.Path(name).suffix.lower()
                if ext not in {'.png','.jpg','.jpeg','.webp','.wav','.mp3','.m4a','.mp4'}: raise ValueError('不支持的素材格式')
                dest=APP/'uploads'/(uid()+ext); dest.write_bytes(raw); return self.respond({'path':asset(dest)})
            d=json.loads(raw); action=parsed.path.rsplit('/',1)[-1]
            with LOCK:
                if action=='settings':
                    cfg=d['settings'];
                    if any(j['status'] in ('running','queued') for j in STATE['jobs']) and any(k in cfg and cfg[k]!=STATE['settings'].get(k) for k in ('comfy_url','ssh_host','remote_root','text_url','text_model')): raise ValueError('有未完成任务时不可切换服务地址，可先暂停并等待任务结束')
                    url=urlparse(cfg['comfy_url'])
                    if url.scheme not in ('http','https') or not url.hostname: raise ValueError('视频服务地址无效')
                    STATE['settings'].update({k:cfg[k] for k in ('comfy_url','ssh_host','remote_root','paused','text_url','text_model') if k in cfg})
                    if 'text_key' in cfg and cfg['text_key']:
                        secret=APP/'data/text_service.key'; secret.write_text(cfg['text_key']); secret.chmod(0o600)
                elif action=='project':
                    if d.get('id'):
                        p=project(d['id']);
                        if active(p['id']): raise ValueError('项目有未完成任务，请等待结束后修改设定')
                        p.update({k:d[k] for k in ('title','description','bible','style') if k in d})
                    else:
                        p=dict(id=uid(),title=d.get('title') or '新的漫剧',description='',bible='',style='Cinematic semi-realistic Chinese animation',characters=[],episodes=[]); STATE['projects'].append(p)
                elif action=='character':
                    p=project(d['project']);
                    if active(p['id']): raise ValueError('项目有未完成任务，请等待结束后修改角色')
                    c=next((x for x in p['characters'] if x['id']==d.get('id')),None)
                    if not c:
                        c=dict(id=uid(),name='新角色',age=20,identity='',costume='',image='',locked=False,voice={'mode':'preset','reference':'','reference_text':'','speaker':'serena','locked':False}); p['characters'].append(c)
                    changes=d['character']
                    oldvoice=copy.deepcopy(c['voice'])
                    if 'voice' in changes:
                        v=changes['voice']
                        if v.get('mode') not in ('clone','preset'): raise ValueError('无效配音方式')
                        if v.get('reference'): safe(v['reference'])
                        if v.get('locked') and v['mode']=='clone' and (not v.get('reference') or not v.get('reference_text')): raise ValueError('锁定克隆音色需要参考声音和原文')
                        if all(v.get(k)==c['voice'].get(k) for k in ('mode','reference','reference_text','speaker')):
                            for k in ('sample','sample_text'):
                                if k in c['voice']: v[k]=c['voice'][k]
                    if changes.get('image'): safe(changes['image'])
                    if c['locked'] and any(k in changes and changes[k]!=c.get(k) for k in ('identity','costume','image','age')): raise ValueError('请先解锁角色，再修改定妆')
                    if c['voice'].get('locked') and 'voice' in changes and any(changes['voice'].get(k)!=c['voice'].get(k) for k in ('mode','reference','reference_text','speaker')): raise ValueError('请先解锁音色')
                    for k in ('name','age','identity','costume','image','locked','voice'):
                        if k in changes: c[k]=changes[k]
                    if any(c['voice'].get(k)!=oldvoice.get(k) for k in ('mode','reference','reference_text','speaker')):
                        for k in ('sample','sample_text','sample_created'):
                            c['voice'].pop(k, None)
                        for ep in p['episodes']:
                            for sh in ep['shots']:
                                if sh['character']==c['id'] and sh.get('audio'):
                                    sh['audio']=''; sh['review']='pending'; sh['notes']='音色配置已更改，需要重新配音。'
                elif action=='episode':
                    p=project(d['project'])
                    if d.get('id'):
                        e=episode(p,d['id'])
                        if active(p['id'],e['id']): raise ValueError('本集有未完成任务，请等待结束后修改')
                    else:
                        e=dict(id=uid(),number=len(p['episodes'])+1,title='新的一集',script='',shots=[],exports=[],bgm='',bgm_db=-24,voice_db=0); p['episodes'].append(e)
                    if d.get('bgm'): safe(d['bgm'])
                    if not -40<=float(d.get('bgm_db',-24))<=-6 or not -12<=float(d.get('voice_db',0))<=6: raise ValueError('混音音量超出范围')
                    for k in ('title','script','bgm','bgm_db','voice_db'):
                        if k in d: e[k]=d[k]
                elif action in ('shots','shot'):
                    p=project(d['project']); e=episode(p,d['episode'])
                    if any(j['status'] in ('running','queued') and j['episode']==e['id'] for j in STATE['jobs']): raise ValueError('本集仍有任务，请先取消或等待完成后编辑')
                    if action=='shots':
                        if not isinstance(d['shots'],list) or len(d['shots'])>500: raise ValueError('镜头导入限500条')
                        pending=[]
                        for row in d['shots']:
                            s=newshot(row.get('title','新镜头'),row.get('prompt',''))
                            for k in ('dialogue','character','characters','continuity','duration','seed'):
                                if k in row: s[k]=row[k]
                            if not p['characters']: s['characters']=[]; s['character']=''
                            elif s['character'] not in [c['id'] for c in p['characters']]: s['character']=p['characters'][0]['id']; s['characters']=[s['character']]
                            validate_shot(s,p); pending.append(s)
                        e['shots'].extend(pending)
                    else:
                        s=shot(e,d['id'])
                        if d.get('delete'): e['shots'].remove(s)
                        elif 'move' in d:
                            idx=e['shots'].index(s); target=max(0,min(len(e['shots'])-1,idx+int(d['move']))); e['shots'].pop(idx); e['shots'].insert(target,s)
                        else:
                            edits=d['shot']; validate_shot({**s,**edits},p);
                            if any(k in edits and edits[k]!=s.get(k) for k in ('dialogue','character')) and edits.get('audio',s.get('audio'))==s.get('audio'): edits['audio']=''; edits['review']='pending'
                            dur=float(edits.get('duration',s['duration']))
                            if not .5<=dur<=120: raise ValueError('时长需在0.5至120秒之间')
                            for k in ('video','audio','continuity'):
                                if edits.get(k): safe(edits[k])
                            for k in ('title','prompt','dialogue','character','characters','continuity','duration','seed','video','audio','review','notes'):
                                if k in edits: s[k]=edits[k]
                elif action=='enqueue':
                    p=project(d['project']); kind=d['kind']; e=episode(p,d['episode']) if d.get('episode') else None
                    if kind not in ('video','voice','voice_sample','export','script','storyboard'): raise ValueError('未知任务')
                    ids=d.get('shots',[]) if kind not in ('export','voice_sample','script','storyboard') else ['']
                    if kind=='voice_sample':
                        if not any(c['id']==d.get('character') for c in p['characters']): raise ValueError('角色不存在')
                        if not d.get('text','').strip(): raise ValueError('试听台词为空')
                        vc=next(c for c in p['characters'] if c['id']==d['character'])
                        if vc['voice']['mode']=='clone' and (not vc['voice'].get('reference') or not vc['voice'].get('reference_text')): raise ValueError('克隆模式需要参考录音和准确原文；没有录音请切换固定预设')
                    elif e is None: raise ValueError('请先选择分集')
                    if kind not in ('export','voice_sample','script','storyboard') and not ids: raise ValueError('请选择镜头')
                    if kind=='video': ids=sorted(ids,key=lambda sid:next((i for i,sh in enumerate(e['shots']) if sh['id']==sid),999999))
                    for sid in ids:
                        if sid:
                            sh=shot(e,sid)
                            if kind=='video' and (not sh['prompt'].strip() or not sh['characters']): raise ValueError('生成画面前请填写描述并选择出镜角色')
                            if kind=='voice':
                                vc=next((x for x in p['characters'] if x['id']==sh['character']),None)
                                if not sh['dialogue'].strip() or not vc or not vc['voice']['locked']: raise ValueError('生成配音前请填写台词、指定说话角色并锁定音色')
                        if any(j['status'] in ('queued','running') and j['episode']==(e['id'] if e else '') and j['shot']==sid and j['kind']==kind and (kind!='voice_sample' or j.get('character')==d.get('character')) for j in STATE['jobs']): continue
                        STATE['jobs'].append(dict(id=uid(),project=p['id'],episode=e['id'] if e else '',shot=sid,character=d.get('character'),text=d.get('text'),kind=kind,status='queued',message='等待执行',created=time.time()))
                    WAKE.set()
                elif action=='job':
                    j=next(x for x in STATE['jobs'] if x['id']==d['id'])
                    if d['op']=='retry' and j['status'] in ('failed','interrupted','cancelled'):
                        if j['status']=='failed' and 'status_str' in j['message']: j.pop('prompt_id',None)
                        j.update(status='queued',cancel=False,message='等待重试'); WAKE.set()
                    elif d['op']=='cancel': j['cancel']=True; j['status']='cancelled' if j['status']=='queued' else j['status']
                else: raise ValueError('未知操作')
                save()
            self.respond({'ok':True})
        except Exception as e: self.respond({'error':str(e)},400)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--host',default='127.0.0.1'); parser.add_argument('--port',type=int,default=8765); args=parser.parse_args()
    threading.Thread(target=worker,daemon=True).start()
    print(f'潮生漫剧工作台 http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()
