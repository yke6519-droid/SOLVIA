"""Conversation routes: SSE streaming and ask_user interaction."""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from backend.app.dependencies.auth import get_current_user
from backend.app.schemas.chat import ChatRequest, ReplyRequest
from backend.app.services.agent_manager import agent_manager
from backend.app.routers.sessions import _verify_session_ownership, ensure_session_title
from backend.app.services.chart_snapshot_store import save_chart_snapshot
from backend.app.services.summary_task_manager import summary_task_manager
from backend.app.charting.context import bind_chart_context, reset_chart_context

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
        if name == "create_chart_plan":
            result = _parse_structured_tool_output(raw_output)
            if isinstance(result, dict) and result.get("status") == "accepted":
                chart_spec = result.get("chart_spec")
                if isinstance(chart_spec, dict):
                    return "chart_spec", {
                        "name": name,
                        "result_type": "chart_spec",
                        "result": "图表已生成",
                        "chart_spec": chart_spec,
                    }
            return "tool_end", {
                "name": name,
                "result_type": "chart_error",
                "result": output_str[:800] + ("..." if len(output_str) > 800 else ""),
            }
        if name in {"get_power_chart_data", "get_power_chart_data_by_range"}:
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

    # 首条用户消息到达时创建/补齐会话名称；手动重命名不会被覆盖。
    ensure_session_title(req.session_id, user_id, req.message)

    executor = agent_manager.get_agent(req.session_id, user_id=user_id)
    bridge = agent_manager.get_or_create_bridge(req.session_id)

    # 构建一个异步生成器，逐条发送事件
    async def event_generator():
        # 一个异步队列，用于生产者/消费者之间传递事件
        queue = asyncio.Queue()
        # 让桥接器把 Agent 事件发到这个队列
        bridge.attach(queue, asyncio.get_running_loop())
        # 累积最终完整文本结果
        full_output = ""
        stream_completed = False
        # 收集工具生成的图表数据
        chart_specs = []

        # 接口的“后台执行线程”，负责调用 Agent 并把内部事件放到队列里
        async def consume_agent():
            nonlocal full_output
            # ContextVar 必须在 Agent 执行任务内部绑定，工具线程才能定位当前 session。
            bridge_token = agent_manager.bind_bridge(bridge)
            chart_context_token = bind_chart_context(req.session_id, user_id)
            try:
                # 异步读取 Agent 产生的事件流
                # astream_events 是一个流式事件接口，可能会产生 token、工具调用、问题、完成等事件
                async for ev in executor.astream_events(
                    {"input": req.message}, version="v2"
                ):
                    processed = _process_agent_event(ev)
                    if processed:
                        # 如果是工具结束事件，并且是图表数据，收集图表数据
                        if processed[0] == "tool_end" and processed[1].get("result_type") == "chart":
                            chart_data = processed[1].get("chart_data")
                            if isinstance(chart_data, dict):
                                chart_specs.append(chart_data)
                        if processed[0] == "chart_spec":
                            chart_spec = processed[1].get("chart_spec")
                            if isinstance(chart_spec, dict):
                                chart_specs.append(chart_spec)
                        # 如果是 token 事件，累积完整输出
                        if processed[0] == "token":
                            full_output += processed[1]["content"]
                        # 把处理后的事件放到队列里，供 SSE 生成器发送
                        await queue.put(processed)
                if chart_specs:
                    # 如果有图表数据，异步保存图表快照到持久化存储
                    await asyncio.to_thread(save_chart_snapshot, req.session_id, user_id, chart_specs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Agent 执行出错")
                await queue.put(("error", {"message": str(exc)}))
            finally:
                # 解绑 ContextVar，避免泄漏
                agent_manager.reset_bridge(bridge_token)
                reset_chart_context(chart_context_token)
                # 在队列里放一个“完成”事件，包含最终完整输出
                await queue.put(("done", {"output": full_output}))
        # 在锁内启动后台任务，消费 Agent 事件并发送 SSE
        async with lock:
            # 启动后台任务
            task = asyncio.create_task(consume_agent())
            try:
                while True:
                    kind, data = await queue.get()
                    if kind == "done":
                        stream_completed = True
                        # 如果是完成事件，发送最终输出并结束 SSE
                        yield {
                            "event": "done",
                            "data": json.dumps(data, ensure_ascii=False),
                        }
                        break
                    # 如果是错误事件，发送错误信息并结束 SSE
                    event_name = "user_input_required" if kind == "question" else kind
                    yield {
                        "event": event_name,
                        "data": json.dumps(data, ensure_ascii=False),
                    }
            finally:
                # 确保在退出时解绑桥接器和取消后台任务
                bridge.detach()
                if not task.done():
                    task.cancel()
                try:
                    if stream_completed:
                        summary_task_manager.schedule(req.session_id, executor.memory)
                except Exception:
                    logger.exception("摘要生成失败")
    # 返回一个 SSE 响应，ping 每 15 秒发送一次心跳
    return EventSourceResponse(event_generator(), ping=15)


@router.post("/chat")
async def chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    """Non-streaming compatibility endpoint."""
    user_id = current_user["user_id"]
    _verify_session_ownership(req.session_id, user_id)
    lock = agent_manager.get_lock(req.session_id)
    if lock.locked():
        raise HTTPException(409, detail="当前会话正在处理请求")

    # 首条用户消息到达时创建/补齐会话名称；手动重命名不会被覆盖。
    ensure_session_title(req.session_id, user_id, req.message)

    executor = agent_manager.get_agent(req.session_id, user_id=user_id)
    chart_context_token = bind_chart_context(req.session_id, user_id)
    try:
        async with lock:
            result = await asyncio.to_thread(executor.invoke, {"input": req.message})
    finally:
        reset_chart_context(chart_context_token)
    summary_task_manager.schedule(req.session_id, executor.memory)
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
