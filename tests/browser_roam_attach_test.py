import json,time,urllib.parse,urllib.request,websockets.sync.client as wsclient,httpx
BASE='http://127.0.0.1:18080';fail=[]
def check(ok,n,d=''):
 print(('PASS  ' if ok else 'FAIL  ')+n+('' if ok else f'  {d}')); 
 if not ok:fail.append(n)
def conn(port):
 t=json.load(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/json/new?{urllib.parse.quote(BASE+"/login",safe="")}',method='PUT')))
 ws=wsclient.connect(t['webSocketDebuggerUrl'],max_size=None);mid=0
 def cmd(m,p=None):
  nonlocal mid;mid+=1;ws.send(json.dumps({'id':mid,'method':m,'params':p or {}}))
  while True:
   x=json.loads(ws.recv(timeout=30))
   if x.get('id')==mid:return x
 def ev(x):
  r=cmd('Runtime.evaluate',{'expression':x,'returnByValue':True,'awaitPromise':True});return r.get('result',{}).get('result',{}).get('value')
 cmd('Runtime.enable');cmd('Page.enable');cmd('Network.enable');return ws,cmd,ev
def login(cmd,ev):
 cmd('Network.clearBrowserCookies');cmd('Page.navigate',{'url':BASE+'/login'});time.sleep(1)
 ev("document.getElementById('username').value='admin';document.getElementById('password').value='admin12345';document.getElementById('login-form').requestSubmit();");time.sleep(2.2)
 cmd('Page.navigate',{'url':BASE+'/'});time.sleep(2.5)
wa,ca,ea=conn(9222);wb,cb,eb=conn(9223);login(ca,ea);login(cb,eb)
for ev,label in [(ea,'A'),(eb,'B')]:
 for _ in range(20):
  if '附件漫游测试' in (ev('document.body.innerText') or ''):break
  time.sleep(.3)
 check('附件漫游测试' in (ev('document.body.innerText') or ''),f'设备{label}看到会话摘要')
 ev("(()=>{const n=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('附件漫游测试'));if(!n)return false;(n.closest('button')||n.parentElement).click();return true})()");time.sleep(1.5)
 body=ev('document.body.innerText') or ''
 check('roam.txt' in body,f'设备{label}看到附件卡片',body[:120])
 if label=='B':
  eb("(()=>{const n=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('roam.txt'));if(!n)return false;(n.closest('button')||n.parentElement).click();return true})()");time.sleep(1)
  check('漫游附件内容' in (eb('document.body.innerText') or ''),'设备B读取服务器附件内容')
wa.close();wb.close()
print()
if fail:print(fail);raise SystemExit(1)
print('attachment roaming test passed')
