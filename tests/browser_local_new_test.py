import json,time,urllib.parse,urllib.request
import httpx, websockets.sync.client as wsclient
BASE='http://127.0.0.1:18080';CDP='http://127.0.0.1:9222';fail=[]
def check(ok,n,d=''):
 print(('PASS  ' if ok else 'FAIL  ')+n+('' if ok else f'  {d}')); 
 if not ok: fail.append(n)
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
# offline and create local session
cmd('Network.emulateNetworkConditions',{'offline':True,'latency':0,'downloadThroughput':0,'uploadThroughput':0})
ev("(()=>{const e=document.querySelector('textarea');const s=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;s.call(e,'离线新建会话');e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,cancelable:true}));return true})()")
wait(2)
print('offline body', (ev('document.body.innerText') or '')[:120])
cmd('Network.emulateNetworkConditions',{'offline':False,'latency':0,'downloadThroughput':-1,'uploadThroughput':-1})
cmd('Page.reload');wait(4)
# wait for sync push
for _ in range(20):
 r=httpx.Client(base_url=BASE,timeout=10);r.post('/api/auth/login',json={'username':'admin','password':'admin12345'})
 if len(r.get('/api/sync/sessions').json()['sessions'])>=1:break
 wait(0.5)
count=ev("new Promise(r=>{const q=indexedDB.open('chat-demo-u1');q.onsuccess=()=>{const d=q.result;const t=d.transaction('sessionSummaries');const c=t.objectStore('sessionSummaries').count();c.onsuccess=()=>{r(c.result);d.close()}};q.onerror=()=>r(-1)})")
check(count>=1,'本地新建会话未被误删',str(count))
r=httpx.Client(base_url=BASE,timeout=10);r.post('/api/auth/login',json={'username':'admin','password':'admin12345'})
check(len(r.get('/api/sync/sessions').json()['sessions'])>=1,'本地新建会话最终同步到服务端')
ws.close()
print()
if fail: print(fail);raise SystemExit(1)
print('local new sync test passed')
