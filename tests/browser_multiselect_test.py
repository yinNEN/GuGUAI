import base64,json,time,urllib.parse,urllib.request,pathlib
import websockets.sync.client as wsclient
BASE='http://127.0.0.1:18080'; CDP='http://127.0.0.1:9222'; ART=pathlib.Path('tests/artifacts')
fail=[]
def check(ok,name,detail=''):
 print(('PASS  ' if ok else 'FAIL  ')+name+('' if ok else f'  {detail}')); 
 if not ok: fail.append(name)
t=json.load(urllib.request.urlopen(urllib.request.Request(f"{CDP}/json/new?{urllib.parse.quote(BASE+'/login',safe='')}",method='PUT')))
ws=wsclient.connect(t['webSocketDebuggerUrl'],max_size=None); mid=0
def cmd(m,p=None):
 global mid; mid+=1; ws.send(json.dumps({'id':mid,'method':m,'params':p or {}}))
 while True:
  msg=json.loads(ws.recv(timeout=30))
  if msg.get('id')==mid: return msg
def ev(x):
 r=cmd('Runtime.evaluate',{'expression':x,'returnByValue':True,'awaitPromise':True}); return r.get('result',{}).get('result',{}).get('value')
def wait(s): time.sleep(s)
def shot(n):
 d=cmd('Page.captureScreenshot',{'format':'png','fromSurface':True})['result']['data']; (ART/n).write_bytes(base64.b64decode(d)); print('screenshot',n)
cmd('Runtime.enable');cmd('Page.enable');cmd('Network.enable');cmd('Network.clearBrowserCookies');cmd('Page.navigate',{'url':BASE+'/login'});wait(1)
ev("document.getElementById('username').value='admin';document.getElementById('password').value='admin12345';document.getElementById('login-form').requestSubmit();");wait(2.5)
cmd('Page.navigate',{'url':BASE+'/'});wait(3)
for _ in range(20):
 if ev("!!document.querySelector('button[aria-label=\"选择对话\"]')"): break
 wait(0.4)
check(ev("!!document.querySelector('button[aria-label=\"选择对话\"]')"),'聊天多选按钮存在')
ev("document.querySelector('button[aria-label=\"选择对话\"]').click()");wait(0.4)
check(ev("(document.body.innerText||'').includes('选择对话')"),'进入多选模式')
check(ev("!!document.querySelector('[class*=multiDelete]')"),'底部显示删除按钮')
check(ev("!document.querySelector('[class*=settingsLabel]')"),'多选时隐藏设置按钮')
boxes=ev("document.querySelectorAll('[role=checkbox]').length")
check(boxes>=3,'会话显示圆形复选框',str(boxes))
ev("(()=>{const b=[...document.querySelectorAll('[role=checkbox]')]; b[0].click(); b[1].click(); return true})()");wait(0.3)
check('已选择 2 个会话' in (ev("document.body.innerText") or ''),'已选择两个会话')
check(ev("!document.querySelector('[class*=multiDelete]').disabled"),'删除按钮已启用')
shot('chat-multiselect.png')
ev("document.querySelector('[class*=multiDelete]').click()");wait(0.4)
check(ev("(document.body.innerText||'').includes('删除选中的会话')"),'批量删除确认弹窗')
ev("(()=>{const d=document.querySelector('[role=dialog]'); const b=d&&[...d.querySelectorAll('button')].find(x=>x.innerText.trim()==='删除'); if(b)b.click(); return !!b})()");wait(2)
check(ev("!document.querySelector('[role=checkbox]')"),'删除后退出多选')
check(ev("!!document.querySelector('[class*=settingsLabel]')"),'退出多选后恢复设置按钮')
# Admin tables
cmd('Page.navigate',{'url':BASE+'/admin'});wait(1.5)
ev("window.confirm=()=>true")
ev("(()=>{const b=[...document.querySelectorAll('.nav-item')].find(x=>(x.innerText||'').includes('会话记录')); if(b)b.click(); return !!b})()");wait(1)
check(ev("!!document.querySelector('#convs-select-all')"),'会话记录表有全选复选框')
check(ev("document.querySelectorAll('[data-conv-check]').length")>=3,'会话记录行有复选框')
ev("document.querySelectorAll('[data-conv-check]')[0].click(); document.querySelectorAll('[data-conv-check]')[1].click()");wait(0.2)
check('已选择 2 个会话' in (ev('document.body.innerText') or ''),'后台会话多选计数')
var_convs_before = ev("document.querySelectorAll('[data-conv-check]').length")
ev("document.querySelector('#btn-delete-selected-convs').click()");wait(2)
var_convs_after = ev("document.querySelectorAll('[data-conv-check]').length")
check(var_convs_after <= var_convs_before - 2, '后台批量删除会话生效', f'{var_convs_before}->{var_convs_after}')
# storage
ev("(()=>{const b=[...document.querySelectorAll('.nav-item')].find(x=>(x.innerText||'').includes('存储管理')); if(b)b.click(); return !!b})()");wait(1)
check(ev("!!document.querySelector('#files-select-all')"),'存储管理表有全选复选框')
check(ev("document.querySelectorAll('[data-file-check]').length")>=3,'存储管理行有复选框')
ev("document.querySelector('#files-select-all').click()");wait(0.2)
check('已选择' in (ev('document.body.innerText') or ''),'文件全选计数')
shot('admin-multiselect.png')
ev("document.querySelector('#btn-delete-selected-files').click()");wait(2)
check(ev("document.querySelectorAll('[data-file-check]').length") == 0, '后台批量删除文件生效', str(ev("document.querySelectorAll('[data-file-check]').length")))
# users self no delete + admin role unavailable
ev("(()=>{const b=[...document.querySelectorAll('.nav-item')].find(x=>(x.innerText||'').includes('用户管理')); if(b)b.click(); return !!b})()");wait(1)
self_row_buttons=ev("(()=>{const row=[...document.querySelectorAll('#users-body tr')].find(r=>r.innerText.includes('admin')); return row?Array.from(row.querySelectorAll('button')).map(b=>b.innerText):[]})()")
check('删除' not in (self_row_buttons or []),'管理员自己行没有删除按钮',str(self_row_buttons))
ev("document.getElementById('btn-new-user').click()");wait(0.3)
check(not ev("!!document.querySelector('#f-role')"),'新建用户已移除角色设置')
ws.close()
print(); 
if fail: print('失败：',fail); raise SystemExit(1)
print('全部多选/管理员规则 UI 测试通过')
