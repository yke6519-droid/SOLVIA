"""Conversation routes: SSE streaming and ask_user interaction."""
import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends
from langchain_core.tools import ToolException
from sse_starlette.sse import EventSourceResponse

from backend.app.dependencies.auth import get_current_user
from backend.app.schemas.chat import ChatRequest, ReplyRequest
from backend.app.services.agent_manager import agent_manager
from backend.app.routers.sessions import _verify_session_ownership, ensure_session_title
from backend.app.services.chart_snapshot_store import save_chart_snapshot
from backend.app.services.summary_task_manager import summary_task_manager
from backend.app.services.session_context_service import (
    load_context as load_session_context,
    save_active_station,
    save_active_chart,
    save_active_dataset,
)
from backend.app.services.dataset_artifact_service import (
    bind_dataset_context,
    get_dataset_context,
    dataset_reference,
    load_dataset_artifact,
    reset_dataset_context,
)
from backend.app.services.attachment_context import (
    bind_attachment_context,
    reset_attachment_context,
)
from backend.app.services.attachment_service import (
    build_attachment_context,
    load_owned_attachments,
)
from backend.app.services.file_artifact_service import (
    attach_files_to_latest_assistant_message,
    attach_token_usage_to_latest_assistant_message,
    sanitize_generated_file_text,
)
from backend.app.charting.context import bind_chart_context, reset_chart_context
from backend.app.runtime import (
    RuntimeObserver,
    RuntimeInteractionError,
    bind_runtime_context,
    get_runtime_context,
    reset_runtime_context,
)
from backend.app.services.station_resolver import (
    bind_station_resolution_context,
    get_station_resolution_context,
    reset_station_resolution_context,
)
from backend.app.errors import AppError, ErrorCode, ToolError
from backend.Agent.memory import bind_raw_user_message, reset_raw_user_message

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)


def _requires_all_station_tool(message: str) -> bool:
    """判断本轮是否明确要求查询系统中的全部站点。

    这里只做很窄的事实查询识别，不承担完整意图理解；复杂任务仍交给
    Agent。带有“宁波地区/衢州市”等明确地域条件的请求交给地区工具。
    """
    text = re.sub(r"\s+", "", str(message or ""))
    all_station_terms = (
        "全部站点",
        "所有站点",
        "全量站点",
        "站点列表",
        "站点清单",
        "已接入哪些站点",
        "接入了哪些站点",
        "有哪些站点",
    )
    if not any(term in text for term in all_station_terms):
        return False

    # “宁波地区有哪些站点”属于地区查询，不应触发全量站点保护。
    has_region = re.search(r"(地区|省份|省|市|县|区)", text) is not None
    has_system_scope = re.search(r"(系统|全量)", text) is not None
    return not has_region or has_system_scope


def _raise_fact_tool_required() -> None:
    """阻止没有真实站点工具调用的“已查询”伪成功结果。"""
    raise ToolError(
        ErrorCode.CHAT_FACT_TOOL_REQUIRED,
        "查询全部接入站点必须先调用 list_all_stations 工具，未检测到真实工具调用。",
        details={"required_tool": "list_all_stations"},
        retryable=True,
    )


def _load_request_attachments(req: ChatRequest, persisted_context: dict, user_id: int):
    """解析当前消息或会话上下文中的附件，并执行归属校验。"""
    attachment_ids = [item.attachment_id for item in req.attachments]
    if not attachment_ids:
        active_attachment = persisted_context.get("active_attachment")
        if isinstance(active_attachment, dict) and active_attachment.get("attachment_id"):
            attachment_ids = [str(active_attachment["attachment_id"])]
    if not attachment_ids:
        return []
    return load_owned_attachments(
        attachment_ids,
        user_id=user_id,
        session_id=req.session_id,
    )


def _build_agent_input(message: str, attachments: list[dict]) -> str:
    """将安全附件元数据注入本轮 Agent 输入，不暴露真实路径。"""
    attachment_context = build_attachment_context(attachments)
    if not attachment_context:
        return message
    return f"{attachment_context}\n\n用户原始任务：{message}"


def _build_agent_input_with_context(
    message: str,
    attachments: list[dict],
    persisted_context: dict | None = None,
) -> str:
    """注入附件、数据制品和图表的轻量引用，不把原始数据数组放进 Prompt。"""
    parts = []
    attachment_context = build_attachment_context(attachments)
    if attachment_context:
        parts.append(attachment_context)

    active_dataset = (persisted_context or {}).get("active_dataset")
    if isinstance(active_dataset, dict) and active_dataset.get("artifact_id"):
        dataset_reference_payload = {
            key: active_dataset.get(key)
            for key in (
                "artifact_id",
                "artifact_type",
                "source_tool",
                "data_type",
                "stations",
                "period_start",
                "period_end",
                "granularity",
                "row_count",
            )
            if active_dataset.get(key) not in (None, "", [])
        }
        parts.append(
            "当前会话最近一次数据制品，可在用户要求导出刚才数据时复用："
            + json.dumps(dataset_reference_payload, ensure_ascii=False)
        )

    active_chart = (persisted_context or {}).get("active_chart")
    if isinstance(active_chart, dict):
        reference = {
            key: active_chart.get(key)
            for key in ("chart_id", "title", "chart_type")
            if active_chart.get(key)
        }
        if reference:
            parts.append(
                "当前会话最近一次已生成图表，仅用于恢复图表展示，不作为表格导出数据源："
                + json.dumps(reference, ensure_ascii=False)
            )

    if not parts:
        return message
    return "\n\n".join(parts) + f"\n\n用户原始任务：{message}"


def _agent_error_event(exc: Exception) -> dict:
    """Convert an Agent/tool exception into the stable SSE error payload."""
    if isinstance(exc, RuntimeInteractionError):
        return {
            "code": exc.code,
            "message": exc.message,
            "status": 409,
            "retryable": False,
            "details": exc.details,
        }
    if isinstance(exc, ToolError):
        return {
            "code": exc.code,
            "message": exc.message,
            "status": 422,
            "retryable": exc.retryable,
            "details": exc.details,
        }
    if isinstance(exc, ToolException):
        return {
            "code": "CHAT_AGENT_FAILED",
            "message": str(exc) or "任务执行失败",
            "status": 422,
            "retryable": False,
            "details": {},
        }
    if isinstance(exc, asyncio.TimeoutError):
        return {
            "code": ErrorCode.CHAT_AGENT_TIMEOUT.value,
            "message": "任务执行超时，请稍后重试",
            "status": 504,
            "retryable": True,
            "details": {},
        }
    logger.exception("Agent execution failed")
    return {
        "code": ErrorCode.CHAT_AGENT_FAILED.value,
        "message": "任务执行失败，请稍后重试",
        "status": 500,
        "retryable": True,
        "details": {},
    }


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


def _normalize_token_usage(value) -> dict[str, int] | None:
    """兼容 LangChain/OpenAI 两套字段名，统一为三字段 token 用量。"""
    if not isinstance(value, dict):
        return None
    input_value = value.get("input_tokens", value.get("prompt_tokens"))
    output_value = value.get("output_tokens", value.get("completion_tokens"))
    total_value = value.get("total_tokens")
    if input_value is None and output_value is None and total_value is None:
        return None
    try:
        input_tokens = int(input_value or 0)
        output_tokens = int(output_value or 0)
        total_tokens = int(total_value if total_value is not None else input_tokens + output_tokens)
    except (TypeError, ValueError):
        return None
    return {
        "input_tokens": max(input_tokens, 0),
        "output_tokens": max(output_tokens, 0),
        "total_tokens": max(total_tokens, 0),
    }


def _extract_token_usage(value, depth: int = 0) -> dict[str, int] | None:
    """从 AIMessage、LLMResult 或普通 dict 中提取 usage，避免绑定具体模型供应商。"""
    if value is None or depth > 4:
        return None
    if isinstance(value, dict):
        direct = _normalize_token_usage(value)
        if direct:
            return direct
        for key in (
            "usage_metadata",
            "token_usage",
            "usage",
            "response_metadata",
            "llm_output",
            "output",
            "generations",
        ):
            nested = _extract_token_usage(value.get(key), depth + 1)
            if nested:
                return nested
        return None
    for attribute in ("usage_metadata", "response_metadata", "llm_output", "token_usage"):
        nested = _extract_token_usage(getattr(value, attribute, None), depth + 1)
        if nested:
            return nested
    return None


def _merge_token_usage(usage_by_run: dict[str, dict[str, int]]) -> dict[str, int] | None:
    """按模型运行聚合用量；同一个 run 的结束事件只覆盖，不会重复累加。"""
    if not usage_by_run:
        return None
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for usage in usage_by_run.values():
        for key in totals:
            totals[key] += int(usage.get(key, 0) or 0)
    return totals


def _process_agent_event(ev: dict):
    event = ev.get("event", "")
    name = ev.get("name", "")
    if event == "on_chat_model_stream":
        chunk = ev.get("data", {}).get("chunk")
        if chunk and hasattr(chunk, "content") and chunk.content:
            payload = {"content": chunk.content}
            usage = _extract_token_usage(chunk)
            if usage:
                payload.update({"usage": usage, "run_id": ev.get("run_id")})
            return "token", payload
        usage = _extract_token_usage(chunk)
        if usage:
            return "usage", {"usage": usage, "run_id": ev.get("run_id")}
    if event in {"on_chat_model_end", "on_llm_end"}:
        data = ev.get("data", {})
        usage = _extract_token_usage(data.get("output"))
        if usage:
            return "usage", {"usage": usage, "run_id": ev.get("run_id")}
    if event == "on_tool_start":
        return "tool_start", {"name": name}
    if event == "on_tool_end":
        raw_output = ev.get("data", {}).get("output", "")
        output_str = str(raw_output)
        if name == "create_power_chart":
            result = _parse_structured_tool_output(raw_output)
            result_data = result.get("data") if isinstance(result, dict) else None
            if isinstance(result, dict) and result.get("status") == "success":
                chart_spec = result_data.get("chart_spec") if isinstance(result_data, dict) else None
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
                "code": result.get("code") if isinstance(result, dict) else "CHART_TOOL_INVALID",
                "message": result.get("message") if isinstance(result, dict) else None,
                "details": result_data if isinstance(result_data, dict) else None,
                "result": output_str[:800] + ("..." if len(output_str) > 800 else ""),
            }
        result = _parse_structured_tool_output(raw_output)
        result_data = result.get("data") if isinstance(result, dict) else None
        if isinstance(result_data, dict) and result_data.get("result_type") == "file":
            raw_file = result_data.get("file")
            # SSE 只允许传递卡片所需字段，防止工具结果把路径或下载链接
            # 重新暴露给 Agent/前端。真正的下载地址由前端 file_id 组装并通过
            # Axios 请求，用户不能从 Agent 文本中点击服务器链接。
            safe_file = None
            if isinstance(raw_file, dict) and raw_file.get("file_id"):
                safe_file = {
                    key: raw_file[key]
                    for key in ("file_id", "filename", "content_type", "size_bytes", "status")
                    if key in raw_file
                }
            return "tool_end", {
                "name": name,
                "result_type": "file",
                "result": result.get("message", "文件已生成，可以下载"),
                "file": safe_file,
            }
        return "tool_end", {
            "name": name,
            "result_type": "text",
            "result": output_str[:800] + ("..." if len(output_str) > 800 else ""),
        }
    return None


def _log_runtime_event(event) -> None:
    """把 Runtime 旁路事件写入结构化日志，不改变外部 SSE 协议。"""

    logger.info(
        "runtime_event=%s",
        event.model_dump(mode="json"),
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    """Execute the current user's session with SSE streaming output."""
    user_id = current_user["user_id"]
    _verify_session_ownership(req.session_id, user_id)


    lock = agent_manager.get_lock(req.session_id)
    if lock.locked():
        raise AppError(ErrorCode.SESSION_BUSY, "当前会话正在处理请求", status_code=409, retryable=True)

    # 首条用户消息到达时创建/补齐会话名称；手动重命名不会被覆盖。
    ensure_session_title(req.session_id, user_id, req.message)

    executor = agent_manager.get_agent(req.session_id, user_id=user_id)
    bridge = agent_manager.get_or_create_bridge(req.session_id)
    persisted_context = load_session_context(req.session_id, user_id)
    attachments = _load_request_attachments(req, persisted_context, user_id)
    agent_input = _build_agent_input_with_context(req.message, attachments, persisted_context)
    active_station = persisted_context.get("active_station")
    if not isinstance(active_station, dict):
        active_station = None

    # 构建一个异步生成器，逐条发送事件
    async def event_generator():
        # 一个异步队列，用于生产者/消费者之间传递事件
        queue = asyncio.Queue()
        # 让桥接器把 Agent 事件发到这个队列
        bridge.attach(queue, asyncio.get_running_loop())
        # 累积最终完整文本结果
        visible_output = ""
        stream_completed = False
        # 收集工具生成的图表数据
        chart_specs = []
        # 收集本轮文件 ID，Agent 文本不携带下载 URL，完成后绑定助手消息。
        generated_file_ids = []
        # 对“查询全部站点”这类事实任务记录真实工具调用，防止模型只生成文字。
        required_all_station_query = _requires_all_station_tool(req.message)
        called_tool_names: set[str] = set()
        pending_fact_tokens: list[tuple[str, dict]] = []
        # 按模型 run 聚合 usage，避免 stream/end 两类事件重复计算。
        token_usage_by_run: dict[str, dict[str, int]] = {}
        usage_event_index = 0
        # SSE 的 done/error 事件补充 Runtime run_id，方便前端和排障日志
        # 将一轮用户任务与旁路 Runtime 事件关联起来。
        runtime_run_id: str | None = None
        runtime_state: str | None = None

        # 接口的“后台执行线程”，负责调用 Agent 并把内部事件放到队列里
        async def consume_agent():
            nonlocal visible_output, usage_event_index, runtime_run_id, runtime_state
            # ContextVar 必须在 Agent 执行任务内部绑定，工具线程才能定位当前 session。
            bridge_token = agent_manager.bind_bridge(bridge)

            runtime_context_token = bind_runtime_context(req.session_id, user_id)
            runtime_observer = RuntimeObserver(
                get_runtime_context(),
                event_sink=_log_runtime_event,
            )
            runtime_run_id = get_runtime_context().run_id
            runtime_observer.start()

            chart_context_token = bind_chart_context(req.session_id, user_id)
            active_dataset = persisted_context.get("active_dataset")
            active_dataset_id = (
                str(active_dataset.get("artifact_id"))
                if isinstance(active_dataset, dict) and active_dataset.get("artifact_id")
                else None
            )
            dataset_context_token = bind_dataset_context(
                req.session_id,
                user_id,
                active_dataset_artifact_id=active_dataset_id,
            )
            station_context_token = bind_station_resolution_context(
                active_station=active_station,
            )
            attachment_context_token = bind_attachment_context(
                req.session_id,
                user_id,
                attachments,
            )
            raw_message_token = bind_raw_user_message(req.message)
            task_succeeded = False
            try:
                # 异步读取 Agent 产生的事件流
                # astream_events 是一个流式事件接口，可能会产生 token、工具调用、问题、完成等事件
                async for ev in executor.astream_events(
                    {"input": agent_input}, version="v2"
                ):
                    # Runtime 在现有 SSE 适配之前旁路观察原始 LangChain 事件；
                    # 观察失败由 RuntimeObserver 自己隔离，不影响原业务事件。
                    runtime_observer.observe(ev)
                    
                    processed = _process_agent_event(ev)
                    if processed:
                        if processed[0] == "tool_start":
                            tool_name = str(processed[1].get("name", ""))
                            called_tool_names.add(tool_name)
                            if required_all_station_query and tool_name == "list_all_stations":
                                # 工具调用已经被真实检测到，放行之前暂存的文本。
                                for pending_kind, pending_data in pending_fact_tokens:
                                    await queue.put((pending_kind, pending_data))
                                pending_fact_tokens.clear()
                        usage = processed[1].get("usage")
                        if isinstance(usage, dict):
                            usage_event_index += 1
                            run_id = processed[1].get("run_id") or f"usage-{usage_event_index}"
                            token_usage_by_run[str(run_id)] = usage
                            # 前端收到的是截至当前时刻的累计值，而不是某一个模型调用的局部值。
                            processed[1]["usage"] = _merge_token_usage(token_usage_by_run)
                        if processed[0] == "chart_spec":
                            chart_spec = processed[1].get("chart_spec")
                            if isinstance(chart_spec, dict):
                                chart_specs.append(chart_spec)
                        if processed[0] == "tool_end" and processed[1].get("result_type") == "file":
                            file_info = processed[1].get("file")
                            if isinstance(file_info, dict) and file_info.get("file_id"):
                                generated_file_ids.append(file_info["file_id"])
                        # 如果是 token 事件，累积完整输出
                        if processed[0] == "token":
                            visible_output += processed[1]["content"]
                            if required_all_station_query and "list_all_stations" not in called_tool_names:
                                # 在事实工具完成前不把模型可能幻想的结论发给前端。
                                pending_fact_tokens.append(processed)
                                continue
                        # 把处理后的事件放到队列里，供 SSE 生成器发送
                        await queue.put(processed)
                if required_all_station_query and "list_all_stations" not in called_tool_names:
                    # 不允许把模型自称的“已查询”当作真实数据库结果。
                    visible_output = ""
                    _raise_fact_tool_required()

                if chart_specs:
                    # 如果有图表数据，异步保存图表快照到持久化存储
                    await asyncio.to_thread(save_chart_snapshot, req.session_id, user_id, chart_specs)
                    # 会话只保存最近图表的短引用，具体横轴和序列仍从快照读取。
                    try:
                        await asyncio.to_thread(
                            save_active_chart,
                            req.session_id,
                            user_id,
                            chart_specs[-1],
                        )
                    except Exception:
                        logger.exception("保存会话最近图表引用失败")
                if generated_file_ids:
                    await asyncio.to_thread(
                        attach_files_to_latest_assistant_message,
                        req.session_id,
                        user_id,
                        generated_file_ids,
                    )
                final_token_usage = _merge_token_usage(token_usage_by_run)
                if final_token_usage:
                    await asyncio.to_thread(
                        attach_token_usage_to_latest_assistant_message,
                        req.session_id,
                        user_id,
                        final_token_usage,
                    )
                task_succeeded = True
                finished_event = runtime_observer.finish(success=True)
                runtime_state = (
                    finished_event.payload.get("state")
                    if finished_event is not None
                    else get_runtime_context().state.value
                )
            except asyncio.CancelledError:
                finished_event = runtime_observer.finish(cancelled=True)
                runtime_state = (
                    finished_event.payload.get("state")
                    if finished_event is not None
                    else get_runtime_context().state.value
                )
                raise
            except Exception as exc:
                finished_event = runtime_observer.finish(error=exc)
                runtime_state = (
                    finished_event.payload.get("state")
                    if finished_event is not None
                    else get_runtime_context().state.value
                )
                error_payload = _agent_error_event(exc)
                error_payload["run_id"] = runtime_run_id
                error_payload["runtime_state"] = runtime_state
                await queue.put(("error", error_payload))
            finally:
                # 解绑 ContextVar，避免泄漏
                if task_succeeded:
                    dataset_context = get_dataset_context(required=False)
                    if dataset_context and dataset_context.active_dataset_artifact_id:
                        try:
                            dataset = load_dataset_artifact(
                                dataset_context.active_dataset_artifact_id
                            )
                            await asyncio.to_thread(
                                save_active_dataset,
                                req.session_id,
                                user_id,
                                dataset_reference(dataset),
                            )
                        except Exception:
                            logger.exception("保存会话最近数据制品引用失败")
                    context = get_station_resolution_context()
                    selected_station = (
                        context.last_user_selected_station
                        if context is not None
                        else None
                    )
                    if selected_station:
                        try:
                            await asyncio.to_thread(
                                save_active_station,
                                req.session_id,
                                user_id,
                                selected_station,
                            )
                        except Exception:
                            # 会话上下文是辅助记忆，保存失败不应覆盖已完成的任务。
                            logger.exception("保存会话级站点上下文失败")
                agent_manager.reset_bridge(bridge_token)
                reset_chart_context(chart_context_token)
                reset_dataset_context(dataset_context_token)
                reset_station_resolution_context(station_context_token)
                reset_attachment_context(attachment_context_token)
                reset_raw_user_message(raw_message_token)
                reset_runtime_context(runtime_context_token)
                # 在队列里放一个“完成”事件，包含最终完整输出
                await queue.put(("done", {
                    "output": sanitize_generated_file_text(visible_output),
                    "usage": _merge_token_usage(token_usage_by_run),
                    "run_id": runtime_run_id,
                    "runtime_state": runtime_state,
                }))
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
        raise AppError(ErrorCode.CHAT_REPLY_NOT_WAITING, "当前会话没有等待回复的问题", status_code=409)
    if not bridge.is_waiting:
        raise AppError(ErrorCode.CHAT_REPLY_NOT_WAITING, "当前没有等待回复的问题", status_code=409)
    bridge.reply(req.answer)
    return {"status": "ok", "message": "回复已发送"}
