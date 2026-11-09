import asyncio
from collections import deque
import hmac
import json
from pathlib import Path
import shutil
import tempfile
from threading import Event, Lock, Thread
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.demo import DemoModel
from agent.loop import run_agent
from agent.model import ChatModel
from agent.tools import Cancelled, read_file


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=6000)
    workspace: str = ""
    demo: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    key: str = ""
    mcp: bool = True
    continued: bool = False


class WorkspaceRequest(BaseModel):
    workspace: str


class Job:
    def __init__(self):
        self.events = []
        self.stop = Event()
        self.done = Event()
        self.lock = Lock()
        self.approval = Event()
        self.allowed = False
        self.waiting = False

    def emit(self, kind, data):
        with self.lock:
            self.events.append({"type": kind, **data})
            if kind == "done":
                self.done.set()

    def snapshot(self, offset):
        with self.lock:
            return self.events[offset:], self.done.is_set()

    def approve(self, command):
        self.approval.clear()
        self.waiting = True
        self.allowed = False
        self.emit("approval", {"command": command})
        for _ in range(900):
            if self.stop.is_set():
                raise Cancelled()
            if self.approval.wait(0.1):
                return self.allowed
        self.waiting = False
        return False

    def decide(self, allow):
        if self.waiting:
            self.waiting = False
            self.allowed = allow
            self.approval.set()


class Service:
    def __init__(self, base, data, token, config=None):
        self.base = Path(base)
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.mcp_config = Path(config) if config else self.base / "mcp.json"
        self.token = token
        self.workspace = self.new_demo()
        self.jobs = {}
        self.history = None
        self.recent = deque(maxlen=8)
        self.lock = Lock()
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.routes()

    def new_demo(self):
        destination = Path(tempfile.mkdtemp(prefix="practice-", dir=self.data))
        shutil.copytree(self.base / "examples", destination, dirs_exist_ok=True)
        return destination

    def auth(self, supplied):
        return hmac.compare_digest(self.token, supplied or "")

    def require(self, supplied):
        if not self.auth(supplied):
            raise HTTPException(403, "本地会话令牌无效。")

    def find(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在。")
        return job

    def busy(self):
        return any(not job.done.is_set() for job in self.jobs.values())

    def routes(self):
        app = self.app

        @app.get("/api/state")
        def state(x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            return {"workspace": str(self.workspace)}

        @app.post("/api/reset")
        def reset(x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            with self.lock:
                if self.busy():
                    raise HTTPException(409, "请先结束当前任务。")
                self.workspace = self.new_demo()
                self.history = None
                return {"workspace": str(self.workspace)}

        @app.post("/api/workspace")
        def select_workspace(request: WorkspaceRequest, x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            with self.lock:
                if self.busy():
                    raise HTTPException(409, "请先结束当前任务。")
                try:
                    path = Path(request.workspace).resolve(strict=True)
                except OSError as exc:
                    raise HTTPException(400, "工作区不存在。") from exc
                if not path.is_dir():
                    raise HTTPException(400, "工作区必须是一个目录。")
                self.workspace = path
                self.history = None
                return {"workspace": str(path)}

        @app.get("/api/files")
        def files(x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            root = self.workspace
            output = []
            hidden = {".git", "__pycache__", "node_modules", ".venv", "dist", "build"}

            def walk(directory, depth):
                if depth > 8 or len(output) >= 350:
                    return
                try:
                    children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
                except OSError:
                    return
                for item in children:
                    if item.name in hidden or item.name.startswith(".env") or item.is_symlink():
                        continue
                    if not item.resolve().is_relative_to(root.resolve()):
                        continue
                    if item.is_dir():
                        walk(item, depth + 1)
                    elif item.is_file():
                        output.append(item.relative_to(root).as_posix())
                    if len(output) >= 350:
                        break

            walk(root, 0)
            return {"workspace": str(root), "files": output}

        @app.get("/api/preview")
        def preview(path: str, offset: int = 1, x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            try:
                result = read_file(self.workspace, Event(), path, offset=offset, limit=120)
                if "image" in result:
                    return {"path": path, "text": "图片文件。可让模型用 read 读取。"}
                return result
            except (OSError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from exc

        @app.post("/api/tasks")
        def start(request: TaskRequest, x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            with self.lock:
                if self.busy():
                    raise HTTPException(409, "同一时间只能运行一个任务。")
                try:
                    if request.demo:
                        model = DemoModel(request.demo)
                        self.workspace = self.new_demo()
                        self.history = None
                    else:
                        model = ChatModel(request.base_url, request.key, request.model)
                        if request.workspace:
                            path = Path(request.workspace).resolve(strict=True)
                            if not path.is_dir():
                                raise ValueError("工作区必须是一个目录。")
                            if path != self.workspace:
                                self.history = None
                            self.workspace = path
                except (OSError, ValueError) as exc:
                    raise HTTPException(400, str(exc)) from exc
                job_id = uuid4().hex
                if len(self.recent) == self.recent.maxlen:
                    self.jobs.pop(self.recent[0], None)
                self.recent.append(job_id)
                job = Job()
                self.jobs[job_id] = job
                root = self.workspace

                def worker():
                    completion = {"state": "error"}

                    def emit(kind, data):
                        if kind == "done":
                            completion.update(data)
                        else:
                            job.emit(kind, data)

                    try:
                        history = run_agent(
                            root, request.prompt, model, job.stop, emit,
                            self.mcp_config if request.mcp else None,
                            None if request.demo else job.approve,
                            self.history if request.continued else None)
                        self.history = history
                    except Exception as exc:
                        completion["state"] = "error"
                        job.emit("error", {"message": f"任务异常结束（{type(exc).__name__}）。"})
                    finally:
                        job.emit("done", completion)

                Thread(target=worker, daemon=True).start()
            return {"id": job_id, "workspace": str(root)}

        @app.post("/api/tasks/{job_id}/stop")
        def stop(job_id: str, x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            self.find(job_id).stop.set()
            return {"accepted": True}

        @app.post("/api/tasks/{job_id}/approve")
        def approve(job_id: str, allow: bool, x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            self.find(job_id).decide(allow)
            return {"accepted": True}

        @app.get("/api/tasks/{job_id}/events")
        async def sse(job_id: str, x_cursorlab_token: str = Header(default="")):
            self.require(x_cursorlab_token)
            job = self.find(job_id)

            async def stream():
                offset = 0
                while True:
                    events, done = job.snapshot(offset)
                    offset += len(events)
                    for event in events:
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if done:
                        break
                    await asyncio.sleep(0.08)

            return StreamingResponse(stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})

        @app.websocket("/ws/{job_id}")
        async def ws(socket: WebSocket, job_id: str, token: str = ""):
            job = self.jobs.get(job_id)
            if not job or not self.auth(token):
                await socket.close(code=1008)
                return
            await socket.accept()
            offset = 0
            try:
                while True:
                    events, done = job.snapshot(offset)
                    offset += len(events)
                    for event in events:
                        await socket.send_json(event)
                    if done:
                        await socket.close()
                        return
                    await asyncio.sleep(0.08)
            except (WebSocketDisconnect, RuntimeError, OSError):
                return

        app.mount("/", StaticFiles(directory=self.base / "ui", html=True), name="ui")

