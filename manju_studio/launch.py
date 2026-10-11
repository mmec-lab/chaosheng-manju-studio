#!/usr/bin/env python3
import pathlib, subprocess, sys, time, urllib.request, webbrowser
root=pathlib.Path(__file__).resolve().parent
host='0.0.0.0' if '--share' in sys.argv else '127.0.0.1'
url='http://127.0.0.1:8765'
try:
    urllib.request.urlopen(url+'/api/state',timeout=2)
except Exception:
    with (root/'data/server.log').open('ab') as log:
        proc=subprocess.Popen([sys.executable,str(root/'server.py'),'--host',host],cwd=str(root),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        (root/'data/server.pid').write_text(str(proc.pid))
    for _ in range(40):
        try: urllib.request.urlopen(url+'/api/state',timeout=1); break
        except Exception: time.sleep(.25)
    else: raise SystemExit('启动失败，请查看 data/server.log')
webbrowser.open(url)
print('潮生漫剧工作台已打开：'+url+'。可以关闭这个启动窗口，后台服务会继续运行。')
