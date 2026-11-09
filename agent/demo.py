import json

from .tools import check_stop


class DemoModel:
    """Deterministic classroom rehearsal. Tools and external MCP run for real."""
    def __init__(self, mode):
        if mode not in {"mean", "mcp", "stop"}:
            raise ValueError("未知演示场景。")
        self.mode = mode
        self.turn = 0

    def respond(self, history, specs, stop):
        check_stop(stop)
        calls = {
            "mean": [
                ("read", {"path": "stats.py"}),
                ("edit", {"path": "stats.py", "old": "        return 0",
                          "new": '        raise ValueError("mean requires at least one number")'}),
                ("mcp__classroom__lookup_reference", {"topic": "empty-mean"}),
                ("edit", {"path": "stats.py",
                          "old": "def mean(numbers):\n    if not numbers:\n        return 0",
                          "new": 'def mean(numbers):\n    if not numbers:\n        raise ValueError("mean requires at least one number")'}),
                ("bash", {"command": "python -m unittest -v", "timeout": 10}),
            ],
            "mcp": [("mcp__classroom__lookup_reference", {"topic": "mcp"})],
            "stop": [("bash", {"command": 'python -c "import time; time.sleep(20)"', "timeout": 25})],
        }[self.mode]
        if self.turn < len(calls):
            name, args = calls[self.turn]
            self.turn += 1
            return {"content": None, "tool_calls": [{
                "id": f"demo-{self.turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }]}
        result = json.loads(history[-1]["content"])
        if self.mode == "mean":
            summary = ("stats.py 的 mean([]) 现在抛出 ValueError。第一次 edit 因匹配不唯一失败，"
                       "补充函数上下文后替换成功。测试通过。" if result.get("exit_code") == 0
                       else "本轮测试未通过，请查看 bash 结果。")
        elif self.mode == "mcp":
            summary = (result.get("content") or [{"text": "MCP 调用失败。"}])[0]["text"]
        else:
            summary = "等待命令已执行完成。"
        return {"content": summary}

