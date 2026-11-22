import json,time,urllib.parse,urllib.request
import httpx
import websockets.sync.client as wsclient
BASE='http://127.0.0.1:18080'; CDP='http://127.0.0.1:9222'
fail=[]
def check(ok,name,detail=''):
 print(('PASS  ' if ok else 'FAIL  ')+name+('' if ok else f'  {detail}'))
 if not ok: fail.append(name)
t=json.load(urllib.request.urlopen(urllib.request.Request(f"{CDP}/json/new?{urllib.parse.quote(BASE+'/login',safe='')}",method='PUT')))
ws=wsclient.connect(t['webSocketDebuggerUrl'],max_size=None); mid=0
def cmd(m,p=None):
 global mid; mid+=1; ws.send(json.dumps({'id':mid,'method':m,'params':p or {}}))
 while True:
  msg=json.loads(ws.recv(timeout=30))
  if msg.get('id')==mid:return msg
def ev(x):
 r=cmd('Runtime.evaluate',{'expression':x,'returnByValue':True,'awaitPromise':True}); return r.get('result',{}).get('result',{}).get('value')
def wait(s):time.sleep(s)
cmd('Runtime.enable');cmd('Page.enable');cmd('Network.enable');cmd('Network.clearBrowserCookies');cmd('Page.navigate',{'url':BASE+'/login'});wait(1)
ev("document.getElementById('username').value='admin';document.getElementById('password').value='admin12345';document.getElementById('login-form').requestSubmit();");wait(2.5)
cmd('Page.navigate',{'url':BASE+'/'});wait(3)
for _ in range(20):
 if ev("(document.body.innerText||'').includes('STALE_TEST')"):break
 wait(0.4)
check('STALE_TEST' in (ev('document.body.innerText') or ''),'会话首次同步可见')
ev("(()=>{const n=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('STALE_TEST')); if(!n)return false; (n.closest('button')||n.parentElement).click(); return true})()")
wait(1)
check('stale' in (ev('document.body.innerText') or ''),'会话详情已缓存到本地')
# 服务端删除（模拟另一台设备）
a=httpx.Client(base_url=BASE,timeout=30);a.post('/api/auth/login',json={'username':'admin','password':'admin12345'})
check(a.delete('/api/sync/sessions/stale-test').status_code==200,'服务端删除会话')
# 重新加载：本地应清理
cmd('Page.reload');wait(3)
for _ in range(20):
 if ev("!!document.querySelector('textarea')"):break
 wait(0.4)
check('STALE_TEST' not in (ev('document.body.innerText') or ''),'重新登录后本地删除服务端不存在的会话')
count=ev("new Promise(r=>{const q=indexedDB.open('chat-demo-u1');q.onsuccess=()=>{const d=q.result;if(!d.objectStoreNames.contains('sessionSummaries')){d.close();r(0);return}const t=d.transaction('sessionSummaries');const c=t.objectStore('sessionSummaries').count();c.onsuccess=()=>{r(c.result);d.close()}};q.onerror=()=>r(-1)})")
check(count==0,'本地会话缓存已清空',str(count))
ws.close()
print()
if fail: print('失败：',fail); raise SystemExit(1)
print('stale deletion test passed')
