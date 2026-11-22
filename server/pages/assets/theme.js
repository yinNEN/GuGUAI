/* 登录页 / 管理后台 / 用户页共用的主题切换：默认跟随系统，点击后记住选择 */
(function () {
  var KEY = 'gg-theme'
  var root = document.documentElement
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null
  var modes = ['system', 'light', 'dark']
  var mode = 'system'
  try {
    var saved = localStorage.getItem(KEY)
    if (saved === 'light' || saved === 'dark' || saved === 'system') mode = saved
  } catch (e) {}

  function isDark() {
    return mode === 'dark' || (mode === 'system' && media && media.matches)
  }

  function render() {
    var dark = isDark()
    root.setAttribute('data-theme', dark ? 'dark' : 'light')
    var button = document.getElementById('gg-theme-toggle')
    if (button) {
      button.textContent = dark ? '☀' : '☾'
      button.title = dark ? '切换到明亮模式' : '切换到暗黑模式'
      button.setAttribute('aria-label', button.title)
    }
  }

  function toggle() {
    mode = isDark() ? 'light' : 'dark'
    try {
      localStorage.setItem(KEY, mode)
    } catch (e) {}
    render()
  }

  function mount() {
    if (document.getElementById('gg-theme-toggle')) return
    var button = document.createElement('button')
    button.id = 'gg-theme-toggle'
    button.type = 'button'
    button.className = 'theme-toggle'
    button.addEventListener('click', toggle)
    document.body.appendChild(button)
    render()
  }

  render()
  if (media && media.addEventListener) {
    media.addEventListener('change', function () {
      if (mode === 'system') render()
    })
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount)
  } else {
    mount()
  }
})()
