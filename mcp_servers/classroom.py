"""Independent stdio MCP server for looking up a Python classroom reference."""
import json
import sys

VERSION = "2025-11-25"
TOPICS = {
    "empty-mean": "空列表的算术平均值未定义。本练习要求抛出 ValueError，并在测试中断言该异常。",
    "mcp": "stdio MCP 使用逐行 JSON-RPC。stdout 只传协议，日志走 stderr。",
}
TOOL = {
    "name": "lookup_reference",
    "description": "按主题查课堂参考。遇到空列表平均值要求或 MCP 协议说明时使用。",
    "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}},
                    "required": ["topic"], "additionalProperties": False},
}


def dispatch(method, params):
    if method == "initialize":
        return {"protocolVersion": VERSION, "capabilities": {"tools": {}},
                "serverInfo": {"name": "classroom-reference", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        topic = params.get("arguments", {}).get("topic")
        text = TOPICS.get(topic) if isinstance(topic, str) else None
        ok = params.get("name") == TOOL["name"] and bool(text)
        return {"content": [{"type": "text", "text": text if ok else "可用主题：empty-mean、mcp。"}],
                "isError": not ok}
    raise KeyError(method)


def main():
    for line in sys.stdin:
        response = {"jsonrpc": "2.0", "id": None}
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            response["id"] = request["id"]
            response["result"] = dispatch(request["method"], request.get("params", {}))
        except KeyError:
            response["error"] = {"code": -32601, "message": "Method not found"}
        except (ValueError, TypeError, AttributeError):
            response["error"] = {"code": -32600, "message": "Invalid request"}
        print(json.dumps(response, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()

