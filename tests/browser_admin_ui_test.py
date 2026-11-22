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
ev("document.getElementById('username').value='admin';document.getElementById('password').value='admin12345';document.getElementById('login-form').requestSubmit();");wait(2.2)
cmd('Page.navigate',{'url':BASE+'/admin'});wait(2)
ev("document.querySelector('[data-view=users]').click()");wait(.8)
headers=ev("Array.from(document.querySelectorAll('[data-panel=users] thead th')).map(x=>x.textContent.trim())")
check('角色' not in headers,'用户表已移除角色列',str(headers))
ev("document.querySelector('#btn-new-user').click()");wait(.3)
check(not ev("!!document.querySelector('#f-role')"),'新建用户无角色设置')
check(ev("!!document.querySelector('#f-password')"),'新建用户保留密码输入')
check(ev("!!document.querySelector('#f-must')"),'新建用户保留强制改密选项')
ev("document.querySelector('[data-close]').click()");wait(.2)
ev("document.querySelector('[data-edit]').click()");wait(.3)
check(not ev("!!document.querySelector('#f-role')"),'编辑用户无角色设置')
check(not ev("!!document.querySelector('#f-password')"),'编辑用户无重复密码输入')
check(ev("document.querySelector('#f-active').disabled"),'编辑自己时启用开关禁用')
ev("document.querySelector('[data-close]').click()");wait(.2)
check(ev("!!document.querySelector('[data-reset]')"),'保留单一重置密码入口')
ev("document.querySelector('[data-view=branding]').click()");wait(1)
check(ev("document.querySelector('#brand-preview-mark').textContent")=='' and 'url(' in ev("document.querySelector('#brand-preview-mark').style.backgroundImage"),'图片品牌预览不再叠加文字')
check(ev("document.querySelector('.sidebar .brand .logo-mark').textContent")=='' and 'url(' in ev("document.querySelector('.sidebar .brand .logo-mark').style.backgroundImage"),'后台侧栏图片品牌不叠加文字')
check(ev("(()=>{const e=document.querySelector('.sidebar .brand .logo-mark');const c=getComputedStyle(e);return c.alignItems==='center'&&c.justifyContent==='center'})()"),'文字品牌标识居中')
check(ev("getComputedStyle(document.querySelector('#brand-icon-file')).display")=='none','原生文件输入已隐藏')
check(not ev("document.querySelector('label[for=brand-icon-file]')?.classList.contains('btn')"),'旧的选择图片按钮已替换')
thumb=ev("(()=>({w:document.querySelector('#brand-icon-thumb')?.getBoundingClientRect().width,h:document.querySelector('#brand-icon-thumb')?.getBoundingClientRect().height,bg:getComputedStyle(document.querySelector('#brand-icon-thumb')).backgroundImage}))()")
check(bool(thumb) and thumb['w']<=48 and thumb['h']<=48 and 'url(' in thumb['bg'],'旧按钮位置显示小预览框',str(thumb))
styles=ev("(()=>({upload:document.querySelector('#btn-upload-brand-icon')?.className,del:document.querySelector('#btn-delete-brand-icon')?.className}))()")
check(bool(styles) and 'btn' in styles['upload'] and 'btn' in styles['del'],'上传/删除按钮样式统一',str(styles))
ws.close();print()
if fail:print('失败：',fail);raise SystemExit(1)
print('admin UI test passed')
