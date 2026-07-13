"""
chat.py - 对话路由（SSE 流式 + ask_user 交互）
==============================================

对应 Spring 概念:
  - @RestController + @PostMapping("/api/chat/stream")
  - SseEmitter 替代方案：sse-starlette 的 EventSourceResponse
  - @PostMapping("/api/chat/{id}/reply") ≈ 处理用户交互回调

SSE 事件类型:
  token              — LLM 逐字输出
  tool_start         — 工具开始执行
  tool_end           — 工具执行完成
  user_input_required— ask_user 需要用户回复
  done               — 本轮对话结束
  error              — 出错
"""
import json
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.schemas.chat import ChatRequest, ReplyRequest
from app.services.agent_manager import agent_manager

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)


def _process_agent_event(ev: dict):
    """
    过滤 astream_events 的原始事件，提取前端需要的信息。
    返回 (kind, data) 或 None（忽略的事件）。
    """
    event = ev.get("event", "")
    name = ev.get("name", "")

    # LLM 逐 token 输出
    if event == "on_chat_model_stream":
        chunk = ev.get("data", {}).get("chunk")
        if chunk and hasattr(chunk, "content") and chunk.content:
            return ("token", {"content": chunk.content})

    # 工具开始
    elif event == "on_tool_start":
        return ("tool_start", {"name": name})

    # 工具结束
    elif event == "on_tool_end":
        output = ev.get("data", {}).get("output", "")
        output_str = str(output)
        if len(output_str) > 800:
            output_str = output_str[:800] + "..."
        return ("tool_end", {"name": name, "result": output_str})

    return None


# ============================================================
# SSE 流式对话（主链路）
# ============================================================
@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    SSE 流式对话。

    流程:
      1. 获取 AgentExecutor 和 Bridge
      2. 启动后台任务消费 astream_events
      3. 主循环从 asyncio.Queue 读取事件（agent 事件 + ask_user 问题）
      4. yield SSE 事件给前端
      5. 结束后异步触发摘要
    """
    executor = agent_manager.get_agent(req.session_id)
    bridge = agent_manager.get_or_create_bridge(req.session_id)

    async def event_generator():
        # 创建统一事件队列：agent 事件和 ask_user 问题都进这个队列
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        # 绑定 bridge 到当前事件循环
        bridge.attach(queue, loop)
        agent_manager.set_current_bridge(bridge)

        full_output = ""  # 收集所有 token 内容作为最终输出

        async def consume_agent():
            """后台任务：消费 astream_events，把处理后的事件推入队列。"""
            nonlocal full_output
            try:
                async for ev in executor.astream_events(
                    {"input": req.message}, version="v2"
                ):
                    processed = _process_agent_event(ev)
                    if processed:
                        kind, data = processed
                        if kind == "token":
                            full_output += data["content"]
                        await queue.put(processed)
            except Exception as e:
                logger.error(f"Agent 执行出错: {e}", exc_info=True)
                await queue.put(("error", {"message": str(e)}))
            finally:
                await queue.put(("done", {"output": full_output}))

        task = asyncio.create_task(consume_agent())

        try:
            while True:
                kind, data = await queue.get()

                if kind == "done":
                    yield {"event": "done", "data": json.dumps(data, ensure_ascii=False)}
                    break
                elif kind == "error":
                    yield {"event": "error", "data": json.dumps(data, ensure_ascii=False)}
                elif kind == "token":
                    yield {"event": "token", "data": json.dumps(data, ensure_ascii=False)}
                elif kind == "tool_start":
                    yield {"event": "tool_start", "data": json.dumps(data, ensure_ascii=False)}
                elif kind == "tool_end":
                    yield {"event": "tool_end", "data": json.dumps(data, ensure_ascii=False)}
                elif kind == "question":
                    yield {"event": "user_input_required",
                           "data": json.dumps(data, ensure_ascii=False)}
        finally:
            bridge.detach()
            agent_manager.set_current_bridge(None)
            if not task.done():
                task.cancel()
            # 异步触发摘要（不阻塞响应）
            try:
                await executor.memory.amaybe_summarize()
            except Exception as e:
                logger.warning(f"摘要生成失败(不影响对话): {e}")

    return EventSourceResponse(event_generator(), ping=15)


# ============================================================
# 非流式对话（兜底）
# ============================================================
@router.post("/chat")
async def chat(req: ChatRequest):
    """非流式对话。不支持 ask_user 交互（ask_user 会得到空回复）。"""
    executor = agent_manager.get_agent(req.session_id)

    result = await asyncio.to_thread(executor.invoke, {"input": req.message})
    output = result.get("output", str(result)) if isinstance(result, dict) else str(result)

    asyncio.create_task(executor.memory.amaybe_summarize())
    return {"output": output}


# ============================================================
# ask_user 回复
# ============================================================
@router.post("/chat/{session_id}/reply")
async def reply_to_question(session_id: str, req: ReplyRequest):
    """回复 ask_user 提问，解除 Agent 工具线程的阻塞。"""
    bridge = agent_manager.get_bridge(session_id)
    if bridge is None:
        raise HTTPException(404, detail="会话不存在")
    if not bridge.is_active:
        raise HTTPException(400, detail="当前无待回复的问题")

    bridge.reply(req.answer)
    return {"status": "ok", "message": "回复已发送"}
