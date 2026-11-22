import json,time,urllib.parse,urllib.request,websockets.sync.client as wsclient
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
ev("document.getElementById('username').value='admin';document.getElementById('password').value='admin12345';document.getElementById('login-form').requestSubmit();");wait(2.5)
cmd('Page.navigate',{'url':BASE+'/'});wait(3)
for _ in range(20):
 if '提问跳转测试' in (ev('document.body.innerText') or ''):break
 wait(.4)
ev("(()=>{const n=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('提问跳转测试'));if(!n)return false;(n.closest('button')||n.parentElement).click();return true})()");wait(1.2)
print('rail labels', ev("Array.from(document.querySelectorAll('[class*=markButton]')).slice(0,3).map(b=>b.getAttribute('aria-label'))"))
initial=ev("document.querySelectorAll('[data-message-id]').length")
check(initial<=24,'首屏消息窗口受控',str(initial))
# click first rail button
clicked=ev("(()=>{const b=document.querySelector('[class*=markButton]');if(!b)return false;b.click();return true})()")
check(clicked,'点击第一个提问标记')
wait(.05)
check(ev("!!document.querySelector('[class*=revealOverlay]')"),'定位时显示等待提示')
wait(1)
check(ev("!!document.querySelector('[data-message-id=big-u0]')"),'目标旧消息已渲染')
pos=ev("(()=>{const n=document.querySelector('[data-message-id=big-u0]');const s=[...document.querySelectorAll('*')].find(e=>e.contains(n)&&e.scrollHeight>e.clientHeight+50);if(!n||!s)return null;const a=n.getBoundingClientRect(),b=s.getBoundingClientRect();return {top:a.top-b.top,st:s.scrollTop,sh:s.scrollHeight,ch:s.clientHeight}})()")
check(bool(pos) and -20<=pos['top']<=200,'目标消息位于可视区域',str(pos))
check(not ev("!!document.querySelector('[class*=revealOverlay]')"),'等待提示已结束')
count_after=ev("document.querySelectorAll('[data-message-id]').length")
check(count_after<=24,'跳转后渲染窗口仍受控',str(count_after))
# click a middle old mark, verify
labels=ev("Array.from(document.querySelectorAll('[class*=markButton]')).length")
if labels and labels>10:
 ev("document.querySelectorAll('[class*=markButton]')[10].click()");wait(1)
 check(ev("!!document.querySelector('[data-message-id=big-u10]')"),'中间旧消息跳转成功')
 pos2=ev("(()=>{const n=document.querySelector('[data-message-id=big-u10]');const s=[...document.querySelectorAll('*')].find(e=>e.contains(n)&&e.scrollHeight>e.clientHeight+50);if(!n||!s)return null;const a=n.getBoundingClientRect(),b=s.getBoundingClientRect();return {top:a.top-b.top}})()")
 check(bool(pos2) and -20<=pos2['top']<=200,'中间旧消息位于可视区域',str(pos2))
ws.close()
print()
if fail:print('失败：',fail);raise SystemExit(1)
print('提问跳转测试通过')
