"""
main.py - FastAPI 应用入口
==========================
对应 Spring 的 @SpringBootApplication + main 方法。

启动: uvicorn app.main:app --reload --port 8000
或:   python -m app.main
"""
import sys
import os
import logging

# 确保 solar_agent 根目录在 sys.path 中
_SOLAR_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SOLAR_AGENT_ROOT not in sys.path:
    sys.path.insert(0, _SOLAR_AGENT_ROOT)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import settings
from app.routers import chat, sessions

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


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
<title>SolarAgent 对话测试</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f2f5; height: 100vh; display: flex; flex-direction: column; }
  #header { background: #4B3FE3; color: white; padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; }
  #header h1 { font-size: 18px; font-weight: 600; }
  #session-bar { display: flex; gap: 12px; align-items: center; font-size: 13px; }
  #session-id { opacity: 0.8; }
  #messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
  .msg { max-width: 75%; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: #4B3FE3; color: white; }
  .msg.assistant { align-self: flex-start; background: white; border: 1px solid #e8e8e8; }
  .msg.assistant .content:empty::before { content: "思考中..."; color: #999; }
  .tool-event { align-self: flex-start; font-size: 12px; color: #666; background: #f5f5f5; padding: 6px 12px; border-radius: 8px; border-left: 3px solid #4B3FE3; max-width: 75%; word-break: break-word; }
  #input-bar { padding: 16px 24px; background: white; border-top: 1px solid #e8e8e8; display: flex; gap: 12px; }
  #msg-input { flex: 1; padding: 10px 16px; border: 1px solid #d9d9d9; border-radius: 8px; font-size: 14px; outline: none; }
  #msg-input:focus { border-color: #4B3FE3; }
  #send-btn { padding: 10px 24px; background: #4B3FE3; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }
  #send-btn:hover { background: #3a2fb3; }
  #send-btn:disabled { background: #ccc; cursor: not-allowed; }
  #new-session-btn { padding: 6px 16px; background: rgba(255,255,255,0.2); color: white; border: 1px solid rgba(255,255,255,0.3); border-radius: 6px; cursor: pointer; font-size: 13px; }
  #new-session-btn:hover { background: rgba(255,255,255,0.3); }
  #ask-modal { display: none; position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: white; border: 2px solid #27D2BF; border-radius: 12px; padding: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); z-index: 100; width: 90%; max-width: 500px; }
  #ask-modal .label { font-size: 13px; color: #666; margin-bottom: 8px; }
  #ask-modal .question { font-size: 15px; font-weight: 500; margin-bottom: 12px; white-space: pre-wrap; }
  #ask-modal .input-row { display: flex; gap: 8px; }
  #ask-input { flex: 1; padding: 8px 12px; border: 1px solid #d9d9d9; border-radius: 6px; font-size: 14px; outline: none; }
  #ask-input:focus { border-color: #27D2BF; }
  #ask-reply-btn { padding: 8px 16px; background: #27D2BF; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }
  #ask-reply-btn:hover { background: #1fb5a3; }
  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .status-dot.idle { background: #ccc; }
  .status-dot.busy { background: #faad14; animation: pulse 1s infinite; }
  .status-dot.online { background: #52c41a; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
</style>
</head>
<body>
<div id="header">
  <h1>☀️ SolarAgent 对话测试</h1>
  <div id="session-bar">
    <span><span class="status-dot idle" id="status-dot"></span><span id="status-text">未连接</span></span>
    <span id="session-id">无会话</span>
    <button id="new-session-btn" onclick="createSession()">新建会话</button>
  </div>
</div>
<div id="messages"></div>
<div id="ask-modal">
  <div class="label">🤔 Agent 需要你的回复:</div>
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

<script>
let sessionId = null;
let isStreaming = false;

function setStatus(state) {
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  dot.className = 'status-dot ' + state;
  if (state === 'idle') text.textContent = '未连接';
  else if (state === 'busy') text.textContent = '思考中';
  else if (state === 'online') text.textContent = '在线';
}

async function createSession() {
  try {
    const res = await fetch('/api/sessions', { method: 'POST' });
    const data = await res.json();
    sessionId = data.session_id;
    document.getElementById('session-id').textContent = '会话: ' + sessionId;
    document.getElementById('msg-input').disabled = false;
    document.getElementById('send-btn').disabled = false;
    setStatus('online');
    document.getElementById('messages').innerHTML = '';
    addMessage('assistant', '会话已创建，请输入您的问题。');
  } catch (e) {
    alert('创建会话失败: ' + e.message);
  }
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
    const response = await fetch('/api/chat/stream', {
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
          } catch(e) { /* ignore parse errors */ }
        }
      }
    }
  } catch (e) {
    assistantEl.querySelector('.content').textContent = '❌ 连接错误: ' + e.message;
  } finally {
    isStreaming = false;
    setStatus('online');
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
      addToolEvent('🔧 ' + data.name + ' 执行中...');
      break;
    case 'tool_end':
      addToolEvent('✅ ' + data.name + ' 完成: ' + (data.result || '').substring(0, 120));
      break;
    case 'user_input_required':
      showAskModal(data.question);
      break;
    case 'done':
      if (!contentEl.textContent) {
        contentEl.textContent = data.output || '(无输出)';
      }
      break;
    case 'error':
      contentEl.textContent = '❌ ' + data.message;
      break;
  }
}

function showAskModal(question) {
  document.getElementById('ask-question').textContent = question;
  document.getElementById('ask-modal').style.display = 'block';
  document.getElementById('ask-input').focus();
}

async function replyAskUser() {
  const input = document.getElementById('ask-input');
  const answer = input.value.trim();
  if (!answer) return;
  input.value = '';
  document.getElementById('ask-modal').style.display = 'none';
  addToolEvent('💬 用户回复: ' + answer);
  try {
    await fetch('/api/chat/' + sessionId + '/reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: answer })
    });
  } catch (e) {
    console.error('回复失败:', e);
  }
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
