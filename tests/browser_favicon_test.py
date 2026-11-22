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
def nav(url,w=2):
 cmd('Page.navigate',{'url':url});time.sleep(w)
cmd('Runtime.enable');cmd('Page.enable');cmd('Network.enable');cmd('Network.clearBrowserCookies')
nav(BASE+'/login')
for _ in range(20):
 href=ev("document.querySelector('link[rel~=icon]')?.href||''")
 if '/api/public/branding/icon' in href: break
 time.sleep(.3)
check('/api/public/branding/icon' in href,'图片品牌 favicon 使用上传图片',href)
# text mode
c=httpx.Client(base_url=BASE,timeout=30);c.post('/api/auth/login',json={'username':'admin','password':'admin12345'})
print(c.put('/api/admin/branding',json={'title':'GuGuAI','subtitle':'Powered by Deepseek.','mark':'G','color':'#4d6bfe','mark_mode':'text'}).status_code)
nav(BASE+'/login')
for _ in range(20):
 href=ev("document.querySelector('link[rel~=icon]')?.href||''")
 if href.startswith('data:image/svg'): break
 time.sleep(.3)
check(href.startswith('data:image/svg'),'文字品牌生成 SVG favicon',href[:60])
# none mode
c.put('/api/admin/branding',json={'title':'GuGuAI','subtitle':'Powered by Deepseek.','mark':'G','color':'#4d6bfe','mark_mode':'none'})
nav(BASE+'/login')
time.sleep(.8)
check(not ev("!!document.querySelector('link[rel~=icon]')"),'关闭品牌标识后移除 favicon')
ws.close()
print()
if fail:print(fail);raise SystemExit(1)
print('favicon test passed')
