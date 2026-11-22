import json,time,urllib.parse,urllib.request,urllib.parse as up,websockets.sync.client as wsclient,httpx
BASE='http://127.0.0.1:18080';CDP='http://127.0.0.1:9222';fail=[]
def check(ok,n,d=''):
 print(('PASS  ' if ok else 'FAIL  ')+n+('' if ok else f'  {d}'))
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
cmd('Runtime.enable');cmd('Page.enable');cmd('Network.enable');cmd('Network.clearBrowserCookies')
c=httpx.Client(base_url=BASE,timeout=30);c.post('/api/auth/login',json={'username':'admin','password':'admin12345'})
# malicious mark (4-char limit), text mode
r=c.put('/api/admin/branding',json={'title':'GuGuAI','subtitle':'Powered by Deepseek.','mark':'</te','color':'#4d6bfe','mark_mode':'text'});print('brand',r.status_code)
cmd('Page.navigate',{'url':BASE+'/login'});wait(1.2)
href=ev("document.querySelector('link[rel~=icon]')?.href||''")
svg=up.unquote(href.split(',',1)[1]) if href.startswith('data:image/svg') else ''
check(href.startswith('data:image/svg'),'malicious mark still uses SVG',href[:80])
check('<script' not in svg.lower() and '&lt;/te' in svg,'SVG text is XML-escaped',svg[:160])
# admin page favicon with image mode
r=c.put('/api/admin/branding',json={'title':'GuGuAI','subtitle':'Powered by Deepseek.','mark':'G','color':'#4d6bfe','mark_mode':'image'});print('image brand',r.status_code)
# login then admin
cmd('Page.navigate',{'url':BASE+'/login'});wait(1)
ev("document.getElementById('username').value='admin';document.getElementById('password').value='admin12345';document.getElementById('login-form').requestSubmit();");wait(2.5)
cmd('Page.navigate',{'url':BASE+'/admin'});wait(2)
href2=ev("document.querySelector('link[rel~=icon]')?.href||''")
check('/api/public/branding/icon' in href2,'admin page favicon follows image brand',href2[:100])
# icon endpoint headers
resp=c.get('/api/public/branding/icon');check(resp.status_code==200 and resp.headers.get('content-type','').startswith('image/') and resp.headers.get('x-content-type-options')=='nosniff','icon endpoint content type/nosniff')
ws.close();print()
if fail:print('失败：',fail);raise SystemExit(1)
print('favicon security test passed')
