import json,time,urllib.parse,urllib.request,base64,pathlib
import websockets.sync.client as wsclient
BASE='http://127.0.0.1:18080';CDP='http://127.0.0.1:9222';fail=[]
def check(ok,n,d=''):
 print(('PASS  ' if ok else 'FAIL  ')+n+('' if ok else f'  {d}')); 
 if not ok:fail.append(n)
t=json.load(urllib.request.urlopen(urllib.request.Request(f"{CDP}/json/new?{urllib.parse.quote(BASE+'/login',safe='')}",method='PUT')))
ws=wsclient.connect(t['webSocketDebuggerUrl'],max_size=None);mid=0
def cmd(m,p=None):
 global mid;mid+=1;ws.send(json.dumps({'id':mid,'method':m,'params':p or {}}))
 while True:
  x=json.loads(ws.recv(timeout=30))
  if x.get('id')==mid:return x
def ev(x):
 r=cmd('Runtime.evaluate',{'expression':x,'returnByValue':True,'awaitPromise':True});return r.get('result',{}).get('result',{}).get('value')
def wait(s):time.sleep(s)
cmd('Runtime.enable');cmd('Page.enable');cmd('Network.enable');cmd('Network.clearBrowserCookies');cmd('Page.navigate',{'url':BASE+'/login'});wait(1)
ev("document.getElementById('username').value='admin';document.getElementById('password').value='admin12345';document.getElementById('login-form').requestSubmit();");wait(2.5)
cmd('Page.navigate',{'url':BASE+'/'});wait(3)
for _ in range(20):
 if ev("!!document.querySelector('button[aria-label=\"选择对话\"]')"):break
 wait(0.4)
check(ev("!!document.querySelector('button[aria-label=\"选择对话\"]')"),'选择对话按钮存在')
check(ev("!document.querySelector('[class*=multiToggle]')"),'顶部旧选择按钮已移除')
pos=ev("(()=>{const s=document.querySelector('button[aria-label=\"选择对话\"]');const set=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='设置');if(!s||!set)return null;const a=s.getBoundingClientRect(),b=set.getBoundingClientRect();return {sx:a.x,sw:a.width,sh:a.height,setx:b.x,setw:b.width,seth:b.height,body:document.body.innerText}})()")
check(bool(pos) and pos['sx'] < pos['setx'] and pos['sh']>=40 and pos['seth']>=40,'选择在左、设置在右且点击区域足够',str(pos))
before=ev("document.querySelectorAll('[data-message-id]').length")
ev("document.querySelector('button[aria-label=\"选择对话\"]').click()");wait(0.4)
check(ev("!!document.querySelector('[class*=selectAll]')"),'选择模式有全选圆圈')
ev("document.querySelector('[class*=selectAll]').click()");wait(0.2)
body=ev('document.body.innerText') or ''
check('已选择' in body,'全选后显示已选择数量',body[-200:])
check(ev("!document.querySelector('[class*=multiDelete]').disabled"),'全选后删除可用')
shot=cmd('Page.captureScreenshot',{'format':'png','fromSurface':True})['result']['data']
pathlib.Path('tests/artifacts/chat-select-all.png').write_bytes(base64.b64decode(shot))
# exit
ev("document.querySelector('button[aria-label=\"退出多选\"]').click()");wait(0.3)
check(ev("!!document.querySelector('button[aria-label=\"选择对话\"]')") and ev("!!document.querySelector('button[aria-label=\"选择对话\"]')"),'退出后恢复选择/设置按钮')
ws.close()
print()
if fail:print('失败：',fail);raise SystemExit(1)
print('选择按钮位置/全选测试通过')
