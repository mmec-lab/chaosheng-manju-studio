"""Integration checks run on an isolated database; never touch the user's project."""
import copy,importlib.util,json,os,pathlib,threading,urllib.request,urllib.error,unittest,wave,struct,time
HERE=pathlib.Path(__file__).resolve().parent
os.environ['STUDIO_DB']=str(HERE/'data/test-state.json')
if pathlib.Path(os.environ['STUDIO_DB']).exists(): pathlib.Path(os.environ['STUDIO_DB']).unlink()
spec=importlib.util.spec_from_file_location('studio',HERE/'server.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
http=m.ThreadingHTTPServer(('127.0.0.1',0),m.Handler); port=http.server_address[1]; threading.Thread(target=http.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{port}'
def post(action,data,token=True):
 req=urllib.request.Request(base+'/api/'+action,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**({'X-Studio-Token':m.TOKEN} if token else {})})
 try:
  with urllib.request.urlopen(req) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
p=m.STATE['projects'][0]; ep=p['episodes'][2]; s=ep['shots'][0]
class Tests(unittest.TestCase):
 def test_01_import(self):
  self.assertEqual(len(p['episodes']),10); self.assertEqual(sum(len(e['shots']) for e in p['episodes']),240)
 def test_02_security(self):
  self.assertEqual(post('project',{'title':'bad'},False)[0],403)
  try: urllib.request.urlopen(base+'/media?path=../../../../etc/passwd')
  except urllib.error.HTTPError as e:self.assertEqual(e.code,400)
  else:self.fail('path traversal')
 def test_03_range(self):
  r=urllib.request.urlopen(urllib.request.Request(base+'/media?path='+urllib.parse.quote(s['video']),headers={'Range':'bytes=0-99'}))
  self.assertEqual(r.status,206);self.assertEqual(len(r.read()),100)
 def test_04_voice_lock(self):
  c=p['characters'][0]; self.assertTrue(c['locked'])
  self.assertEqual(post('character',{'project':p['id'],'id':c['id'],'character':{'identity':'changed'}})[0],400)
 def test_05_invalid_import_atomic(self):
  n=len(ep['shots'])
  code,_=post('shots',{'project':p['id'],'episode':ep['id'],'shots':[{'title':'good'},{'title':'bad','duration':-2}]})
  self.assertEqual(code,400);self.assertEqual(len(ep['shots']),n)
 def test_06_dialogue_change_invalidates_audio(self):
  self.assertTrue(s['audio'])
  code,_=post('shot',{'project':p['id'],'episode':ep['id'],'id':s['id'],'shot':{'dialogue':'这句台词已改。'}})
  self.assertEqual(code,200);self.assertEqual(s['audio'],'');self.assertEqual(s['review'],'pending')
 def test_07_queue_dedupe_and_edit_guard(self):
  d={'project':p['id'],'episode':ep['id'],'kind':'video','shots':[s['id']]}
  self.assertEqual(post('enqueue',d)[0],200);self.assertEqual(post('enqueue',d)[0],200)
  self.assertEqual(len(m.STATE['jobs']),1)
  self.assertEqual(post('shot',{'project':p['id'],'episode':ep['id'],'id':s['id'],'shot':{'title':'race'}})[0],400)
  j=m.STATE['jobs'][0]; post('job',{'id':j['id'],'op':'cancel'});self.assertEqual(j['status'],'cancelled')
 def test_08a_distinct_voice_samples(self):
  ids=['su_wanqing','tang_tang']
  for cid in ids:
   next(c for c in p['characters'] if c['id']==cid)['voice']['mode']='preset'
   self.assertEqual(post('enqueue',{'project':p['id'],'kind':'voice_sample','character':cid,'text':'你好。'})[0],200)
  queued=[j for j in m.STATE['jobs'] if j['kind']=='voice_sample' and j['status']=='queued']
  self.assertEqual(len(queued),2)
  for j in queued:post('job',{'id':j['id'],'op':'cancel'})
 def test_08_unreviewed_export_blocked(self):
  with self.assertRaisesRegex(ValueError,'审核'):m.exportjob({'id':'test_unreviewed'},p,ep)
 def test_09_real_export(self):
  # Two genuine existing clips, clean synthesized signal for music, existing actual TTS for voice.
  wav=HERE/'renders/test_music.wav'
  with wave.open(str(wav),'wb') as w:
   w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\x00\x00'*24000)
  shots=copy.deepcopy(p['episodes'][3]['shots'][:2])
  for sh in shots:sh['review']='approved'
  testep=dict(id='test_ep',number=99,title='合成测试',shots=shots,exports=[],bgm=m.asset(wav),bgm_db=-24,voice_db=0)
  job={'id':'test_export','kind':'export'}
  m.exportjob(job,p,testep)
  out=m.safe(testep['exports'][0]['file']);self.assertGreater(out.stat().st_size,10000)
  self.assertTrue(m.safe(testep['exports'][0]['subtitle']).is_file())
  # Decode the whole result: catches broken muxing and corrupt tracks.
  m.run([m.ffmpeg(),'-v','error','-i',out,'-f','null','-'])
 def test_10_long_audio_rejected(self):
  sh=copy.deepcopy(p['episodes'][3]['shots'][0]);sh.update(duration=.5,review='approved')
  ep2=dict(number=98,shots=[sh],exports=[])
  with self.assertRaisesRegex(ValueError,'配音长于画面'):m.exportjob({'id':'test_long'},p,ep2)
if __name__=='__main__':unittest.main(verbosity=2)
