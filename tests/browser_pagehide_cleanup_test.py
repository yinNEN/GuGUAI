import json,time,urllib.parse,urllib.request,websockets.sync.client as wsclient,httpx
BASE='http://127.0.0.1:18080';CDP='http://127.0.0.1:9222';fail=[]
def check(ok,n,d=''):
 print(('PASS  ' if ok else 'FAIL  ')+n+('' if ok else f'  {d}')); 
 if not ok:fail.append(n)
t=json.load(urllib.request.urlopen(urllib.request.Request(f'{CDP}/json/new?{urllib.parse.quote(BASE+"/login",safe="")}',method='PUT')))
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
ev("document.getElementById('username').value='admin';document.getElementById('password').value='admin12345';document.getElementById('login-form').requestSubmit();");wait(2.2)
cmd('Page.navigate',{'url':BASE+'/'});wait(2.5)
admin=httpx.Client(base_url=BASE,timeout=30);admin.post('/api/auth/login',json={'username':'admin','password':'admin12345'})
before=admin.get('/api/admin/storage').json()['text']['files']
ev("(()=>{const e=document.querySelector('textarea');const s=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;s.call(e,'退出残留'+('测试'.repeat(1100)));e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,cancelable:true}));return true})()")
for _ in range(20):
 if ev("!!document.querySelector('[class*=attachments]')"):break
 wait(.3)
check(ev("!!document.querySelector('[class*=attachments]')"),'长文本附件已创建')
for _ in range(20):
 midcount=admin.get('/api/admin/storage').json()['text']['files']
 if midcount>before: break
 wait(.3)
check(midcount>before,'长文本附件已上传到文本区',f'{before}->{midcount}')
cmd('Page.navigate',{'url':BASE+'/login'});wait(2)
after=admin.get('/api/admin/storage').json()['text']['files']
check(after < midcount,'离开页面后未发送草稿文件已清理',f'{midcount}->{after}')
ws.close();print()
if fail:print('失败：',fail);raise SystemExit(1)
print('pagehide cleanup test passed')
