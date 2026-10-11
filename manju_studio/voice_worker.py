# Executed on the user's own GPU host via SSH. Returns a WAV, never credentials.
import os, sys, json, base64, pathlib, tempfile, io
import numpy as np
import torch, soundfile as sf
from qwen_tts import Qwen3TTSModel
payload=json.loads(base64.b64decode(sys.stdin.buffer.read()))
v=payload['voice']; root=pathlib.Path(payload['root'])
modelpath=root/'voice/models'/('Qwen3-TTS-Base' if v['mode']=='clone' else 'Qwen3-TTS-CustomVoice')
if not (modelpath/'model.safetensors').is_file(): raise RuntimeError('需要的配音模型尚未下载完成：'+str(modelpath))
if torch.cuda.mem_get_info()[0]<6*1024**3: raise RuntimeError('可用显存不足6GB，请先在ComfyUI卸载空闲模型后再试；工作台不会自动杀掉其他进程')
model=Qwen3TTSModel.from_pretrained(str(modelpath),device_map='cuda:0',dtype=torch.bfloat16,attn_implementation='sdpa')
if v['mode']=='clone':
    with tempfile.NamedTemporaryFile(suffix='.wav') as ref:
        ref.write(base64.b64decode(payload['reference'])); ref.flush()
        wav,sr=model.generate_voice_clone(text=payload['text'],language='Chinese',ref_audio=ref.name,ref_text=v['reference_text'],do_sample=False)
else:
    wav,sr=model.generate_custom_voice(text=payload['text'],language='Chinese',speaker=v['speaker'],do_sample=False)
b=io.BytesIO(); sf.write(b,np.asarray(wav[0],dtype=np.float32),sr,format='WAV'); print('STUDIO_AUDIO:'+base64.b64encode(b.getvalue()).decode(),flush=True)
