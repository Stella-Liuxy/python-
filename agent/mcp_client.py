import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import subprocess
import sys
import threading
import time

from .tools import check_stop, hidden_process, kill_tree

VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}


class McpClient:
    def __init__(self, config_path, stop):
        self.stop = stop
        self.config_path = Path(config_path).resolve()
        self.processes = {}
        self.tools = {}

    def __enter__(self):
        return self

    def discover(self, notify):
        config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        for name, entry in config.get("mcpServers", {}).items():
            check_stop(self.stop)
            try:
                connection = Connection(self.config_path.parent, entry, self.stop)
                self.processes[name] = connection
                connection.initialize()
                tool_count = 0
                for tool in connection.list_tools():
                    alias = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{name}__{tool['name']}")[:64]
                    if alias in self.tools:
                        continue
                    self.tools[alias] = (name, tool)
                    tool_count += 1
                notify("mcp", {"message": f"已连接 {name}，发现 {tool_count} 个工具。"})
            except (OSError, ValueError, KeyError, TimeoutError) as exc:
                notify("mcp", {"message": f"{name} 连接失败：{exc}", "ok": False})

    def specs(self):
        return [{"name": alias, "description": tool.get("description", tool["name"]),
                 "parameters": tool["inputSchema"]} for alias, (_, tool) in self.tools.items()]

    def call(self, alias, args):
        name, tool = self.tools[alias]
        return self.processes[name].request("tools/call", {"name": tool["name"], "arguments": args})

    def __exit__(self, *_):
        for connection in self.processes.values():
            connection.close()


class Connection:
    def __init__(self, directory, entry, stop):
        command = entry["command"]
        args = entry.get("args", [])
        if command == "$python":
            command = sys.executable
            if getattr(sys, "frozen", False):
                command = str(Path(sys.executable).parent / "runtime" / "python.exe")
        self.stop = stop
        self.next_id = 0
        self.lines = Queue(maxsize=32)
        env = dict(os.environ)
        for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "CURSORLAB_TOKEN"):
            env.pop(key, None)
        self.proc = subprocess.Popen([command, *args], cwd=directory, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, **hidden_process())
        threading.Thread(target=self.collect, daemon=True).start()

    def collect(self):
        while line := self.proc.stdout.readline(4 * 1024 * 1024 + 1):
            try:
                self.lines.put(line, timeout=0.2)
            except Full:
                return

    def send(self, message):
        check_stop(self.stop)
        payload = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=True).encode()
        self.proc.stdin.write(payload + b"\n")
        self.proc.stdin.flush()

    def request(self, method, params):
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            check_stop(self.stop)
            try:
                line = self.lines.get(timeout=0.1)
            except Empty:
                if self.proc.poll() is not None:
                    raise ValueError("MCP server 提前退出。")
                continue
            message = json.loads(line)
            if message.get("id") != self.next_id:
                continue
            if "error" in message:
                raise ValueError(message["error"].get("message", "MCP 请求失败。"))
            return message["result"]
        raise TimeoutError(f"MCP {method} 超时。")

    def initialize(self):
        result = self.request("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "CursorLab", "version": "1.0.0"},
        })
        if result.get("protocolVersion") not in VERSIONS:
            raise ValueError("不支持对端的协议版本。")
        self.send({"method": "notifications/initialized"})

    def list_tools(self):
        params = {}
        for _ in range(5):
            result = self.request("tools/list", params)
            yield from result["tools"]
            cursor = result.get("nextCursor")
            if not cursor:
                return
            params = {"cursor": cursor}

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=0.3)
        except (OSError, subprocess.TimeoutExpired):
            if self.proc.poll() is None:
                if os.name == "nt":
                    kill_tree(self.proc)
                else:
                    self.proc.kill()
                self.proc.wait(timeout=5)
        finally:
            self.proc.stdout.close()

