"""Conversation routes: SSE streaming and ask_user interaction."""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from backend.app.dependencies.auth import get_current_user
from backend.app.schemas.chat import ChatRequest, ReplyRequest
from backend.app.services.agent_manager import agent_manager
from backend.app.routers.sessions import _verify_session_ownership

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)


def _parse_structured_tool_output(raw_output):
    """Return a tool's structured JSON output when one is available."""
    if hasattr(raw_output, "content"):
        raw_output = raw_output.content
    if isinstance(raw_output, dict):
        return raw_output
    if isinstance(raw_output, str):
        try:
            return json.loads(raw_output)
        except json.JSONDecodeError:
            return None
    return None


def _process_agent_event(ev: dict):
    event = ev.get("event", "")
    name = ev.get("name", "")
    if event == "on_chat_model_stream":
        chunk = ev.get("data", {}).get("chunk")
        if chunk and hasattr(chunk, "content") and chunk.content:
            return "token", {"content": chunk.content}
    if event == "on_tool_start":
        return "tool_start", {"name": name}
    if event == "on_tool_end":
        raw_output = ev.get("data", {}).get("output", "")
        output_str = str(raw_output)
        if name == "get_power_chart_data":
            chart_data = _parse_structured_tool_output(raw_output)
            if chart_data is not None:
                return "tool_end", {
                    "name": name,
                    "result_type": "chart",
                    "result": "图表数据已生成",
                    "chart_data": chart_data,
                }
        return "tool_end", {
            "name": name,
            "result_type": "text",
            "result": output_str[:800] + ("..." if len(output_str) > 800 else ""),
        }
    return None


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    """Execute the current user's session with SSE streaming output."""
    user_id = current_user["user_id"]
    _verify_session_ownership(req.session_id, user_id)
    lock = agent_manager.get_lock(req.session_id)
    if lock.locked():
        raise HTTPException(409, detail="当前会话正在处理请求")

    executor = agent_manager.get_agent(req.session_id, user_id=user_id)
    bridge = agent_manager.get_or_create_bridge(req.session_id)

    async def event_generator():
        queue = asyncio.Queue()
        bridge.attach(queue, asyncio.get_running_loop())
        full_output = ""

        async def consume_agent():
            nonlocal full_output
            # ContextVar 必须在 Agent 执行任务内部绑定，工具线程才能定位当前 session。
            bridge_token = agent_manager.bind_bridge(bridge)
            try:
                async for ev in executor.astream_events(
                    {"input": req.message}, version="v2"
                ):
                    processed = _process_agent_event(ev)
                    if processed:
                        if processed[0] == "token":
                            full_output += processed[1]["content"]
                        await queue.put(processed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Agent 执行出错")
                await queue.put(("error", {"message": str(exc)}))
            finally:
                agent_manager.reset_bridge(bridge_token)
                await queue.put(("done", {"output": full_output}))

        async with lock:
            task = asyncio.create_task(consume_agent())
            try:
                while True:
                    kind, data = await queue.get()
                    if kind == "done":
                        yield {
                            "event": "done",
                            "data": json.dumps(data, ensure_ascii=False),
                        }
                        break
                    event_name = "user_input_required" if kind == "question" else kind
                    yield {
                        "event": event_name,
                        "data": json.dumps(data, ensure_ascii=False),
                    }
            finally:
                bridge.detach()
                if not task.done():
                    task.cancel()
                try:
                    await executor.memory.amaybe_summarize()
                except Exception:
                    logger.exception("摘要生成失败")

    return EventSourceResponse(event_generator(), ping=15)


@router.post("/chat")
async def chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    """Non-streaming compatibility endpoint."""
    user_id = current_user["user_id"]
    _verify_session_ownership(req.session_id, user_id)
    lock = agent_manager.get_lock(req.session_id)
    if lock.locked():
        raise HTTPException(409, detail="当前会话正在处理请求")

    executor = agent_manager.get_agent(req.session_id, user_id=user_id)
    async with lock:
        result = await asyncio.to_thread(executor.invoke, {"input": req.message})
    asyncio.create_task(executor.memory.amaybe_summarize())
    return {"output": result.get("output", str(result)) if isinstance(result, dict) else str(result)}


@router.post("/chat/{session_id}/reply")
async def reply_to_question(
    session_id: str,
    req: ReplyRequest,
    current_user: dict = Depends(get_current_user),
):
    """Reply to the pending ask_user question in the current session."""
    _verify_session_ownership(session_id, current_user["user_id"])
    bridge = agent_manager.get_bridge(session_id)
    if bridge is None or not bridge.is_active:
        raise HTTPException(404, detail="会话不存在")
    if not bridge.is_waiting:
        raise HTTPException(400, detail="当前没有等待回复的问题")
    bridge.reply(req.answer)
    return {"status": "ok", "message": "回复已发送"}
