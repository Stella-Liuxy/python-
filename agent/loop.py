from contextlib import nullcontext
import json
import time

from .mcp_client import McpClient
from .model import SYSTEM
from .tools import Cancelled, TOOLS, check_stop, execute

MAX_STEPS = 12
MAX_CALLS = 16
MAX_HISTORY = 160000


def visible_result(result):
    if "image" in result:
        return {"path": result["path"], "image_mime": result["image"]["mime"],
                "message": "图片以独立多模态内容块传给模型。"}
    if "content" in result:
        return {**result, "content": [
            {key: value for key, value in part.items() if key != "data"}
            for part in result["content"]]}
    return result


def model_result(result):
    if "image" not in result:
        return visible_result(result), []
    item = result["image"]
    image = {"type": "image_url", "image_url": {"url": f"data:{item['mime']};base64,{item['data']}"}}
    return visible_result(result), [image]


def call_summary(name, arguments):
    detail = dict(arguments)
    if name in {"write", "edit"}:
        for key in ("content", "old", "new"):
            if key in detail and isinstance(detail[key], str):
                detail[key] = detail[key][:260]
    return detail


def run_agent(root, prompt, model, stop, emit, mcp_config=None, approve=None, history=None):
    messages = list(history or [{"role": "system", "content": SYSTEM}])
    messages.append({"role": "user", "content": prompt})
    emit("status", {"message": "开始处理任务。"})
    previous = None
    repeats = 0
    total_calls = 0
    started = time.monotonic()
    client = McpClient(mcp_config, stop) if mcp_config else nullcontext()
    try:
        with client as mcp:
            if mcp:
                try:
                    mcp.discover(emit)
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    emit("mcp", {"message": f"读取 MCP 配置失败：{exc}", "ok": False})
            specs = [{"name": name, **spec} for name, (_, spec) in TOOLS.items()]
            if mcp:
                specs.extend(mcp.specs())
            for _ in range(MAX_STEPS):
                check_stop(stop)
                if time.monotonic() - started > 180:
                    raise ValueError("达到任务时间上限。")
                budget = sum(len(message.get("content") or "") if isinstance(message.get("content"), str)
                             else 6000 for message in messages)
                if budget > MAX_HISTORY:
                    raise ValueError("达到上下文上限。请开启新会话或缩小任务范围。")
                reply = model.respond(messages, specs, stop)
                check_stop(stop)
                calls = reply.get("tool_calls") or []
                if not calls:
                    answer = reply.get("content") or "模型没有返回文本。"
                    emit("answer", {"message": answer})
                    messages.append({"role": "assistant", "content": answer})
                    emit("done", {"state": "completed"})
                    return messages
                total_calls += len(calls)
                if total_calls > MAX_CALLS:
                    raise ValueError("达到工具调用次数上限。")
                normalized = [{"id": call["id"], "type": "function", "function": call["function"]}
                              for call in calls]
                messages.append({"role": "assistant", "content": reply.get("content"), "tool_calls": normalized})
                images = []
                for call in normalized:
                    check_stop(stop)
                    fn = call["function"]
                    name = fn["name"]
                    args = {}
                    try:
                        args = json.loads(fn["arguments"])
                        if not isinstance(args, dict):
                            raise ValueError("工具参数必须是 JSON 对象。")
                    except (ValueError, TypeError):
                        result = {"error": "工具参数不是有效的 JSON 对象。"}
                    else:
                        signature = (name, json.dumps(args, sort_keys=True))
                        repeats = repeats + 1 if signature == previous else 1
                        previous = signature
                        if repeats >= 3:
                            raise ValueError("同一调用连续重复三次，已停止循环。")
                        emit("tool_call", {"name": name, "arguments": call_summary(name, args)})
                        try:
                            if name == "bash" and approve and not approve(args.get("command", "")):
                                result = {"error": "用户拒绝运行这条命令。"}
                            elif name in TOOLS:
                                result = execute(root, stop, name, args)
                            elif mcp and name in mcp.tools:
                                result = mcp.call(name, args)
                            else:
                                result = {"error": f"未注册的工具：{name}"}
                        except (ValueError, OSError, TimeoutError, KeyError, TypeError) as exc:
                            result = {"error": str(exc) or type(exc).__name__}
                    ok = not (result.get("error") or result.get("isError") or result.get("exit_code", 0))
                    emit("tool_result", {"name": name, "ok": ok, "result": visible_result(result)})
                    content, more_images = model_result(result)
                    images.extend(more_images)
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(content, ensure_ascii=False)})
                if images:
                    messages.append({"role": "user", "content": [
                        {"type": "text", "text": "以下是刚才 read 工具返回的图片。"}, *images]})
            raise ValueError("达到模型循环步数上限。")
    except Cancelled:
        emit("done", {"state": "cancelled", "message": "已中断。已经完成的文件修改会保留。"})
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit("error", {"message": str(exc)})
        emit("done", {"state": "error"})
    return history

