"""
main.py - FastAPI 应用入口
==========================
对应 Spring 的 @SpringBootApplication + main 方法。

启动: uvicorn backend.app.main:app --reload --port 8001
或:   python -m backend.app.main
"""
import sys
import os
import logging

# 确保 solar_agent 根目录在 sys.path 中
_SOLAR_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SOLAR_AGENT_ROOT not in sys.path:
    sys.path.insert(0, _SOLAR_AGENT_ROOT)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from backend.app.config import settings
from backend.app.routers import chat, sessions, auth

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 清除所有大小写代理环境变量
proxy_env_keys = [
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy"
]
for key in proxy_env_keys:
    os.environ.pop(key, None)

# 配置NO_PROXY，强制阿里云域名、本地不走代理，兜底防护
os.environ["NO_PROXY"] = "dashscope.aliyuncs.com,bailian.cn-beijing.aliyuncs.com,127.0.0.1,localhost"
os.environ["no_proxy"] = "dashscope.aliyuncs.com,bailian.cn-beijing.aliyuncs.com,127.0.0.1,localhost"

def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    app = FastAPI(
        title="SolarAgent API",
        description="光伏发电分析助手 - Agent 对话接口",
        version="1.0.0",
    )

    # CORS 中间件（对应 Spring 的 CorsFilter）
    origins = [o.strip() for o in settings.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)

    # 健康检查
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "SolarAgent"}

    # 内置测试页面
    @app.get("/", response_class=HTMLResponse)
    async def test_page():
        return TEST_HTML

    return app


app = create_app()


# ============================================================
# 内置测试页面（方便快速验证 SSE + ask_user 交互）
# ============================================================
TEST_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SolarAgent 对话系统</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f2f5; height: 100vh; overflow: hidden; }

  /* === Login Page === */
  #login-page { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
  .login-card { background: white; border-radius: 16px; padding: 40px; width: 360px; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }
  .login-card h2 { text-align: center; margin-bottom: 8px; font-size: 22px; color: #333; }
  .login-card .subtitle { text-align: center; color: #999; font-size: 13px; margin-bottom: 28px; }
  .login-card .field { margin-bottom: 16px; }
  .login-card label { display: block; font-size: 13px; color: #666; margin-bottom: 6px; }
  .login-card input { width: 100%; padding: 10px 14px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; outline: none; transition: border 0.2s; }
  .login-card input:focus { border-color: #667eea; }
  .login-card .error-msg { color: #e74c3c; font-size: 13px; margin-top: 8px; display: none; }
  .login-card button { width: 100%; padding: 12px; background: #667eea; color: white; border: none; border-radius: 8px; font-size: 15px; cursor: pointer; margin-top: 8px; transition: background 0.2s; }
  .login-card button:hover { background: #5568d3; }
  .login-card button:disabled { background: #ccc; cursor: not-allowed; }
  .hint { text-align: center; font-size: 12px; color: #bbb; margin-top: 16px; }

  /* === Main Layout === */
  #main-page { display: none; height: 100vh; }
  #sidebar { width: 260px; background: #1a1a2e; color: #eee; display: flex; flex-direction: column; flex-shrink: 0; }
  #sidebar-header { padding: 16px 20px; border-bottom: 1px solid rgba(255,255,255,0.1); }
  #sidebar-header h1 { font-size: 16px; font-weight: 600; }
  #sidebar-header .user-info { font-size: 12px; color: #aaa; margin-top: 4px; }
  #new-session-btn { margin: 12px 16px; padding: 10px; background: rgba(255,255,255,0.1); color: #eee; border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; cursor: pointer; font-size: 13px; text-align: center; }
  #new-session-btn:hover { background: rgba(255,255,255,0.2); }
  #session-list { flex: 1; overflow-y: auto; padding: 8px 0; }
  .session-item { padding: 10px 20px; cursor: pointer; font-size: 13px; color: #ccc; border-left: 3px solid transparent; transition: all 0.15s; }
  .session-item:hover { background: rgba(255,255,255,0.05); }
  .session-item.active { background: rgba(102,126,234,0.2); border-left-color: #667eea; color: white; }
  .session-item .sid { font-family: monospace; font-size: 12px; }
  .session-item .time { font-size: 11px; color: #777; margin-top: 2px; }
  .session-item .del-btn { float: right; color: #555; font-size: 14px; display: none; }
  .session-item:hover .del-btn { display: inline; }
  .session-item .del-btn:hover { color: #e74c3c; }
  #sidebar-footer { padding: 12px 20px; border-top: 1px solid rgba(255,255,255,0.1); font-size: 12px; }
  #logout-btn { color: #aaa; cursor: pointer; }
  #logout-btn:hover { color: #e74c3c; }

  /* === Chat Area === */
  #chat-area { flex: 1; display: flex; flex-direction: column; height: 100vh; }
  #chat-header { background: white; padding: 12px 24px; border-bottom: 1px solid #e8e8e8; display: flex; justify-content: space-between; align-items: center; }
  #chat-header .title { font-size: 15px; font-weight: 500; color: #333; }
  #chat-header .status { font-size: 12px; color: #999; }
  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
  .status-dot.idle { background: #ccc; }
  .status-dot.busy { background: #faad14; animation: pulse 1s infinite; }
  .status-dot.online { background: #52c41a; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  #messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
  .msg { max-width: 72%; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: #667eea; color: white; }
  .msg.assistant { align-self: flex-start; background: white; border: 1px solid #e8e8e8; }
  .msg.assistant .content:empty::before { content: "思考中..."; color: #999; }
  .tool-event { align-self: flex-start; font-size: 12px; color: #666; background: #f5f5f5; padding: 6px 12px; border-radius: 8px; border-left: 3px solid #667eea; max-width: 72%; word-break: break-word; }
  #input-bar { padding: 16px 24px; background: white; border-top: 1px solid #e8e8e8; display: flex; gap: 12px; }
  #msg-input { flex: 1; padding: 10px 16px; border: 1px solid #d9d9d9; border-radius: 8px; font-size: 14px; outline: none; }
  #msg-input:focus { border-color: #667eea; }
  #send-btn { padding: 10px 24px; background: #667eea; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }
  #send-btn:hover { background: #5568d3; }
  #send-btn:disabled { background: #ccc; cursor: not-allowed; }

  /* === Ask Modal === */
  #ask-modal { display: none; position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: white; border: 2px solid #27D2BF; border-radius: 12px; padding: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); z-index: 100; width: 90%; max-width: 500px; }
  #ask-modal .label { font-size: 13px; color: #666; margin-bottom: 8px; }
  #ask-modal .question { font-size: 15px; font-weight: 500; margin-bottom: 12px; white-space: pre-wrap; }
  #ask-modal .input-row { display: flex; gap: 8px; }
  #ask-input { flex: 1; padding: 8px 12px; border: 1px solid #d9d9d9; border-radius: 6px; font-size: 14px; outline: none; }
  #ask-input:focus { border-color: #27D2BF; }
  #ask-reply-btn { padding: 8px 16px; background: #27D2BF; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }
  #ask-reply-btn:hover { background: #1fb5a3; }

  /* === Empty State === */
  #empty-state { flex: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; color: #999; }
  #empty-state .icon { font-size: 48px; margin-bottom: 16px; }
  #empty-state .text { font-size: 15px; }
</style>
</head>
<body>

<!-- Login Page -->
<div id="login-page">
  <div class="login-card">
    <h2>SolarAgent</h2>
    <p class="subtitle">光伏发电分析助手</p>
    <div class="field">
      <label>用户名</label>
      <input type="text" id="login-username" placeholder="输入用户名" value="testuser1"/>
    </div>
    <div class="field">
      <label>密码</label>
      <input type="password" id="login-password" placeholder="输入密码" value="test123456"/>
    </div>
    <div class="error-msg" id="login-error"></div>
    <button id="login-btn" onclick="doLogin()">登录</button>
    <p class="hint">测试账号: testuser1 / test123456</p>
  </div>
</div>

<!-- Main Page -->
<div id="main-page" style="display:none; flex-direction:row;">
  <!-- Sidebar -->
  <div id="sidebar">
    <div id="sidebar-header">
      <h1>SolarAgent</h1>
      <div class="user-info" id="user-info"></div>
    </div>
    <button id="new-session-btn" onclick="createSession()">+ 新建会话</button>
    <div id="session-list"></div>
    <div id="sidebar-footer">
      <span id="logout-btn" onclick="doLogout()">退出登录</span>
    </div>
  </div>

  <!-- Chat Area -->
  <div id="chat-area">
    <div id="chat-header">
      <span class="title" id="chat-title">选择或新建一个会话</span>
      <span class="status"><span class="status-dot idle" id="status-dot"></span><span id="status-text">未连接</span></span>
    </div>
    <div id="messages"></div>
    <div id="empty-state" style="display:flex;">
      <div class="icon">SolarAgent</div>
      <div class="text">选择左侧会话或点击「新建会话」开始对话</div>
    </div>
    <div id="ask-modal">
      <div class="label">Agent 需要你的回复:</div>
      <div class="question" id="ask-question"></div>
      <div class="input-row">
        <input type="text" id="ask-input" placeholder="输入回复..." onkeydown="if(event.key==='Enter')replyAskUser()"/>
        <button id="ask-reply-btn" onclick="replyAskUser()">回复</button>
      </div>
    </div>
    <div id="input-bar">
      <input type="text" id="msg-input" placeholder="输入消息，按 Enter 发送..." onkeydown="if(event.key==='Enter')sendMessage()" disabled/>
      <button id="send-btn" onclick="sendMessage()" disabled>发送</button>
    </div>
  </div>
</div>

<script>
let accessToken = "";
let userId = null;
let username = '';
let displayName = '';
let role = '';
let sessionId = null;
let isStreaming = false;

// === Login ===
async function doLogin() {
  const u = document.getElementById('login-username').value.trim();
  const p = document.getElementById('login-password').value.trim();
  const errEl = document.getElementById('login-error');
  const btn = document.getElementById('login-btn');
  if (!u || !p) { errEl.textContent = '请输入用户名和密码'; errEl.style.display = 'block'; return; }
  errEl.style.display = 'none';
  btn.disabled = true; btn.textContent = '登录中...';
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p })
    });
    const data = await res.json();
    if (!res.ok) { throw new Error(data.detail || '登录失败'); }
    accessToken = data.access_token;
    userId = data.user.user_id;
    username = data.user.username;
    displayName = data.user.display_name || data.user.username;
    role = data.user.role;
    showMainPage();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = '登录';
  }
}

function showMainPage() {
  document.getElementById('login-page').style.display = 'none';
  document.getElementById('main-page').style.display = 'flex';
  document.getElementById('user-info').textContent = displayName + ' (' + role + ')';
  loadSessions();
}

function doLogout() {
  accessToken = ""; userId = null; sessionId = null;
  document.getElementById('main-page').style.display = 'none';
  document.getElementById('login-page').style.display = 'flex';
  document.getElementById('session-list').innerHTML = '';
  document.getElementById('messages').innerHTML = '';
}

function apiFetch(url, options = {}) {
  options.headers = Object.assign({'Content-Type': 'application/json', 'Authorization': 'Bearer ' + accessToken}, options.headers || {});
  return fetch(url, options).then(res => {
    if (res.status === 401) { doLogout(); throw new Error('登录已过期'); }
    return res;
  });
}
// === Sessions ===
async function loadSessions() {
  try {
    const res = await apiFetch('/api/sessions');
    const data = await res.json();
    const list = document.getElementById('session-list');
    list.innerHTML = '';
    if (data.sessions.length === 0) {
      list.innerHTML = '<div style="padding:20px;text-align:center;color:#666;font-size:12px;">暂无会话</div>';
      return;
    }
    data.sessions.forEach(s => {
      const div = document.createElement('div');
      div.className = 'session-item';
      div.dataset.sid = s.session_id;
      if (s.session_id === sessionId) div.classList.add('active');
      const time = s.last_message_at ? s.last_message_at.substring(5, 16) : '';
      div.innerHTML = '<span class="sid">' + s.session_id + '</span><span class="del-btn" onclick="deleteSession(event,\\''+s.session_id+'\\')">&times;</span><div class="time">' + time + '</div>';
      div.onclick = function() { switchSession(s.session_id); };
      list.appendChild(div);
    });
  } catch (e) { console.error('加载会话列表失败:', e); }
}

async function createSession() {
  try {
    const res = await apiFetch('/api/sessions', { method: 'POST' });
    const data = await res.json();
    sessionId = data.session_id;
    document.getElementById('chat-title').textContent = '会话: ' + sessionId;
    document.getElementById('msg-input').disabled = false;
    document.getElementById('send-btn').disabled = false;
    setStatus('online');
    document.getElementById('messages').innerHTML = '';
    document.getElementById('empty-state').style.display = 'none';
    document.getElementById('messages').style.display = 'flex';
    addMessage('assistant', '会话已创建，请输入您的问题。');
    await loadSessions();
  } catch (e) { alert('创建会话失败: ' + e.message); }
}

async function switchSession(sid) {
  if (isStreaming) return;
  sessionId = sid;
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.dataset.sid === sid);
  });
  document.getElementById('chat-title').textContent = '会话: ' + sid;
  document.getElementById('msg-input').disabled = false;
  document.getElementById('send-btn').disabled = false;
  document.getElementById('empty-state').style.display = 'none';
  document.getElementById('messages').style.display = 'flex';
  setStatus('online');
  document.getElementById('messages').innerHTML = '';
  addMessage('assistant', '正在加载历史消息...');
  try {
    const res = await apiFetch('/api/sessions/' + sid + '/messages');
    const data = await res.json();
    document.getElementById('messages').innerHTML = '';
    if (data.messages.length === 0) {
      addMessage('assistant', '此会话暂无历史消息，请输入您的问题。');
    } else {
      data.messages.forEach(m => addMessage(m.role, m.content));
    }
  } catch (e) {
    document.getElementById('messages').innerHTML = '';
    addMessage('assistant', '加载历史消息失败: ' + e.message);
  }
}

async function deleteSession(e, sid) {
  e.stopPropagation();
  if (!confirm('确定删除此会话？')) return;
  try {
    await apiFetch('/api/sessions/' + sid, { method: 'DELETE' });
    if (sessionId === sid) {
      sessionId = null;
      document.getElementById('chat-title').textContent = '选择或新建一个会话';
      document.getElementById('msg-input').disabled = true;
      document.getElementById('send-btn').disabled = true;
      document.getElementById('messages').innerHTML = '';
      document.getElementById('messages').style.display = 'none';
      document.getElementById('empty-state').style.display = 'flex';
      setStatus('idle');
    }
    await loadSessions();
  } catch (e) { alert('删除失败: ' + e.message); }
}

// === Chat ===
function setStatus(state) {
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  dot.className = 'status-dot ' + state;
  if (state === 'idle') text.textContent = '未连接';
  else if (state === 'busy') text.textContent = '思考中';
  else if (state === 'online') text.textContent = '在线';
}

function addMessage(role, content) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  const contentDiv = document.createElement('div');
  contentDiv.className = 'content';
  contentDiv.textContent = content;
  div.appendChild(contentDiv);
  document.getElementById('messages').appendChild(div);
  scrollBottom();
  return div;
}

function addToolEvent(text) {
  const div = document.createElement('div');
  div.className = 'tool-event';
  div.textContent = text;
  document.getElementById('messages').appendChild(div);
  scrollBottom();
}

function scrollBottom() {
  const el = document.getElementById('messages');
  el.scrollTop = el.scrollHeight;
}

async function sendMessage() {
  if (!sessionId || isStreaming) return;
  const input = document.getElementById('msg-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  isStreaming = true;
  setStatus('busy');
  addMessage('user', msg);
  const assistantEl = addMessage('assistant', '');
  try {
    const response = await apiFetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: msg })
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            handleEvent(currentEvent, data, assistantEl);
          } catch(e) {}
        }
      }
    }
  } catch (e) {
    assistantEl.querySelector('.content').textContent = '连接错误: ' + e.message;
  } finally {
    isStreaming = false;
    setStatus('online');
    loadSessions();
  }
}

function handleEvent(event, data, el) {
  const contentEl = el.querySelector('.content');
  switch (event) {
    case 'token':
      contentEl.textContent += data.content;
      scrollBottom();
      break;
    case 'tool_start':
      addToolEvent(data.name + ' 执行中...');
      break;
    case 'tool_end':
      addToolEvent(data.name + ' 完成: ' + (data.result || '').substring(0, 120));
      break;
    case 'user_input_required':
      showAskModal(data.question);
      break;
    case 'done':
      if (!contentEl.textContent) { contentEl.textContent = data.output || '(无输出)'; }
      break;
    case 'error':
      contentEl.textContent = data.message;
      break;
  }
}

function showAskModal(question) {
  document.getElementById('ask-question').textContent = question || '请输入回复:';
  document.getElementById('ask-modal').style.display = 'block';
  document.getElementById('ask-input').focus();
}

async function replyAskUser() {
  const input = document.getElementById('ask-input');
  const answer = input.value.trim();
  if (!answer) return;
  input.value = '';
  document.getElementById('ask-modal').style.display = 'none';
  addToolEvent('用户回复: ' + answer);
  try {
    await apiFetch('/api/chat/' + sessionId + '/reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: answer })
    });
  } catch (e) { console.error('回复失败:', e); }
}

// Enter key on login
document.getElementById('login-password').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') doLogin();
});
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8001, reload=True)
