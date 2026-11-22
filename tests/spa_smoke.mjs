/**
 * 可选的前端冒烟测试（需要本机 Edge 与可用的图形/沙箱环境）。
 *
 * 启动：
 *   1. server 运行在 18080，已配置 API Key，Mock 上游运行在 18081
 *   2. Edge 启动：msedge --headless=new --remote-debugging-port=9222 --user-data-dir=<临时目录>
 *   3. node tests/spa_smoke.mjs
 *
 * 验证：登录 -> 打开聊天 -> 发送一条消息 -> 侧边栏出现会话 -> 服务端同步接口能读回。
 */
const BASE = process.env.BASE ?? 'http://127.0.0.1:18080'
const CDP = process.env.CDP ?? 'http://127.0.0.1:9222'
const USERNAME = process.env.SMOKE_USER ?? 'admin'
const PASSWORD = process.env.SMOKE_PASSWORD ?? 'admin12345'

const failures = []
const check = (ok, name, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  ${detail}`}`)
  if (!ok) failures.push(name)
}

const target = await (
  await fetch(`${CDP}/json/new?${encodeURIComponent(`${BASE}/login`)}`, { method: 'PUT' })
).json()
const ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve) => ws.addEventListener('open', resolve, { once: true }))

let nextId = 0
const pending = new Map()
const events = []
ws.addEventListener('message', (event) => {
  const message = JSON.parse(event.data)
  if (message.id !== undefined) {
    pending.get(message.id)?.(message)
    pending.delete(message.id)
  } else {
    events.push(message)
  }
})

const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++nextId
    pending.set(id, (message) => {
      if (message.error) reject(new Error(JSON.stringify(message.error)))
      else resolve(message.result)
    })
    ws.send(JSON.stringify({ id, method, params }))
  })

const evaluate = async (expression) => {
  const result = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text)
  }
  return result.result?.value
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const waitFor = async (expression, timeout = 15000) => {
  const started = Date.now()
  while (Date.now() - started < timeout) {
    if (await evaluate(expression)) return true
    await sleep(300)
  }
  return false
}

await send('Runtime.enable')
await send('Page.enable')
await sleep(1000)

// 登录
await evaluate(`
  document.getElementById('username').value = ${JSON.stringify(USERNAME)};
  document.getElementById('password').value = ${JSON.stringify(PASSWORD)};
  document.getElementById('login-form').requestSubmit();
`)
await sleep(2500)
check((await evaluate('location.pathname')) !== '/login', '登录成功')

// 打开聊天页
await send('Page.navigate', { url: `${BASE}/` })
const ready = await waitFor('!!document.querySelector("textarea")')
check(ready, '聊天界面加载完成')

// 发送一条消息
const typed = await evaluate(`
  (() => {
    const el = document.querySelector('textarea');
    if (!el) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(el, 'spa smoke 你好');
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  })()
`)
check(typed, '输入消息')
const clicked = await evaluate(`
  (() => {
    const labels = ['发送', 'Send'];
    const btn = Array.from(document.querySelectorAll('button')).find((b) =>
      labels.includes((b.getAttribute('aria-label') || '').trim())
    );
    if (!btn) return false;
    btn.click();
    return true;
  })()
`)
check(clicked, '点击发送')

const replied = await waitFor(
  `document.body.innerText.includes('[mock]') || document.body.innerText.includes('mock')`,
  20000
)
check(replied, '收到流式回复')

const sessions = await evaluate(
  `fetch('/api/sync/sessions').then(r => r.json()).then(d => d.sessions ?? [])`
)
check(Array.isArray(sessions) && sessions.length > 0, '服务端已同步会话', JSON.stringify(sessions))
if (sessions?.[0]) {
  const detail = await evaluate(
    `fetch('/api/sync/sessions/${sessions[0].id}').then(r => r.json()).then(d => d.session ?? null)`
  )
  const messages = detail?.messages ?? []
  check(messages.some((m) => m.role === 'user'), '服务端保存了用户消息')
  check(
    messages.some(
      (m) => m.role === 'assistant' && (m.blocks ?? []).some((b) => b.type === 'text' && b.text.includes('mock'))
    ),
    '服务端保存了助手回复'
  )
}

// 刷新后从服务端/缓存恢复会话
await send('Page.reload')
await waitFor('!!document.querySelector("textarea")')
const restored = await evaluate(
  `Array.from(document.querySelectorAll('button')).some((b) => (b.innerText || '').includes('spa smoke')) ||
   document.body.innerText.includes('spa smoke')`
)
check(restored, '刷新后会话仍可见')

const errors = events.filter(
  (event) =>
    event.method === 'Runtime.exceptionThrown' ||
    (event.method === 'Log.entryAdded' && event.params?.entry?.level === 'error')
)
check(errors.length === 0, '页面无 JS 异常', JSON.stringify(errors.slice(0, 3)).slice(0, 600))

ws.close()
if (failures.length) {
  console.log(`\n${failures.length} 项失败：${failures.join(', ')}`)
  process.exit(1)
}
console.log('\n全部通过')
