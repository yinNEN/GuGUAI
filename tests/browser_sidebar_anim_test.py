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
 if '侧栏动画测试' in (ev('document.body.innerText') or ''):break
 wait(.4)
ev("(()=>{const n=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('侧栏动画测试'));if(!n)return false;(n.closest('button')||n.parentElement).click();return true})()");wait(1.2)
# jump to first old message to force an incomplete window
ev("document.querySelector('[class*=markButton]').click()");wait(.6)
count_before=ev("document.querySelectorAll('[data-message-id]').length")
check(count_before<=24,'跳转后处于未完全加载状态',str(count_before))
# collapse sidebar
collapsed=ev("(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='收起边栏');if(!b)return false;b.click();return true})()")
check(collapsed,'点击收起边栏')
wait(.45)
count_collapsed=ev("document.querySelectorAll('[data-message-id]').length")
check(count_collapsed==count_before,'收起动画期间没有连续自动加载',f'{count_before}->{count_collapsed}')
# expand
ev("(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='打开边栏');if(!b)return false;b.click();return true})()")
wait(.45)
count_expanded=ev("document.querySelectorAll('[data-message-id]').length")
check(count_expanded==count_before,'展开动画期间没有连续自动加载',f'{count_before}->{count_expanded}')
# user wheel at bottom loads later
ev("(()=>{const n=document.querySelector('[data-message-id]');const s=[...document.querySelectorAll('*')].find(e=>e.contains(n)&&e.scrollHeight>e.clientHeight+50);if(!s)return false;s.dispatchEvent(new WheelEvent('wheel',{deltaY:200,bubbles:true}));s.scrollTop=s.scrollHeight;return true})()")
wait(.5)
count_load=ev("document.querySelectorAll('[data-message-id]').length")
check(count_load>count_before,'用户主动滚动后动态加载仍有效',f'{count_before}->{count_load}')
ws.close()
print()
if fail:print('失败：',fail);raise SystemExit(1)
print('侧栏动画/动态加载测试通过')
