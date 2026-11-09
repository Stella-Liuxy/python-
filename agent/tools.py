import base64
import difflib
import io
import mimetypes
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

MAX_FILE = 2 * 1024 * 1024
MAX_OUTPUT = 12000
READ_CHUNK = 4096
LINE_PREVIEW = 600
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class Cancelled(Exception):
    pass


def check_stop(stop):
    if stop.is_set():
        raise Cancelled()


def workspace_path(root, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("path 不能为空。")
    rel = Path(name)
    if rel.anchor:
        raise ValueError("请提供相对于工作区的路径。")
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("路径越出工作区。")
    return path


def load_bytes(path):
    if not path.is_file():
        raise ValueError(f"找不到文件：{path.name}。请先检查路径或列出目录。")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("文件超过 2 MiB。文本可用 read 分页查看。")
    return data


def read_file(root, stop, path, offset=1, limit=80):
    target = workspace_path(root, path)
    if type(offset) is not int or type(limit) is not int or offset < 1 or not 1 <= limit <= 200:
        raise ValueError("offset 从 1 开始，limit 范围为 1 到 200。")
    if target.suffix.lower() in IMAGE_TYPES:
        return {"path": path, "image": {
            "mime": mimetypes.guess_type(target)[0],
            "data": base64.b64encode(load_bytes(target)).decode("ascii"),
        }}
    if not target.is_file():
        raise ValueError(f"找不到文件：{path}。请先检查路径或列出目录。")
    items = []
    total = 0
    with target.open("r", encoding="utf-8-sig", newline=None) as stream:
        # 分块读取超长行，避免一行就耗尽内存。
        while chunk := stream.readline(READ_CHUNK):
            check_stop(stop)
            total += 1
            preview = chunk.removesuffix("\n")
            while len(chunk) == READ_CHUNK and not chunk.endswith("\n"):
                check_stop(stop)
                chunk = stream.readline(READ_CHUNK)
            if offset <= total < offset + limit:
                if len(preview) > LINE_PREVIEW:
                    preview = preview[:LINE_PREVIEW] + " …[长行已截断]"
                items.append(f"{total:>4}  {preview}")
    remaining = max(0, total - offset - len(items) + 1)
    return {"path": path, "text": "\n".join(items), "total_lines": total,
            "remaining": remaining, "next_offset": offset + len(items) if remaining else None}


def decode_file(target):
    data = load_bytes(target)
    bom = data.startswith(b"\xef\xbb\xbf")
    return data.decode("utf-8-sig"), bom


def change_summary(path, before, after):
    changes = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                   fromfile=f"a/{path}", tofile=f"b/{path}")
    diff = io.StringIO()
    for line in changes:
        diff.write(line if line.endswith("\n") else line + "\n")
        if diff.tell() > MAX_OUTPUT:
            return diff.getvalue()[:MAX_OUTPUT] + "\n…[diff 已截断]"
    return diff.getvalue()


def save(stop, path, target, before, after, bom=False):
    raw = after.encode("utf-8-sig" if bom else "utf-8")
    if len(raw) > MAX_FILE:
        raise ValueError("写入内容超过 2 MiB。")
    diff = change_summary(path, before, after)
    check_stop(stop)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cursorlab-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        check_stop(stop)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"path": path, "before_lines": len(before.splitlines()),
            "after_lines": len(after.splitlines()), "diff": diff}


def write_file(root, stop, path, content):
    target = workspace_path(root, path)
    before, bom = decode_file(target) if target.exists() else ("", False)
    return save(stop, path, target, before, content, bom)


def edit_file(root, stop, path, old, new):
    if not old:
        raise ValueError("old 不能为空。")
    target = workspace_path(root, path)
    before, bom = decode_file(target)
    # 读取与编辑的换行约定一致。对 CRLF 文件，允许复制界面中的 LF 片段。
    if "\r\n" in before and "\n" not in before.replace("\r\n", ""):
        old = old.replace("\r\n", "\n").replace("\n", "\r\n")
        new = new.replace("\r\n", "\n").replace("\n", "\r\n")
    first = before.find(old)
    if first < 0:
        raise ValueError("old 找不到。请先 read 并复制完整的原文。")
    if before.find(old, first + 1) >= 0:
        raise ValueError("old 出现多次。请补充上下文，让匹配唯一。")
    after = before[:first] + new + before[first + len(old):]
    return save(stop, path, target, before, after, bom)


def hidden_process():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, **hidden_process())
        if proc.poll() is None:
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def windows_job(proc):
    import ctypes as ct
    from ctypes import wintypes as wt

    class BasicLimits(ct.Structure):
        _fields_ = [
            ("process_time", ct.c_longlong), ("job_time", ct.c_longlong),
            ("flags", wt.DWORD), ("min_working_set", ct.c_size_t),
            ("max_working_set", ct.c_size_t), ("active_processes", wt.DWORD),
            ("affinity", ct.c_size_t), ("priority", wt.DWORD), ("scheduling", wt.DWORD),
        ]

    class ExtendedLimits(ct.Structure):
        _fields_ = [("basic", BasicLimits), ("io", ct.c_ulonglong * 6),
                    ("memory", ct.c_size_t * 4)]

    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ct.c_void_p, wt.LPCWSTR]
    kernel.CreateJobObjectW.restype = wt.HANDLE
    kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        return None
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (kernel.SetInformationJobObject(handle, 9, ct.byref(limits), ct.sizeof(limits))
            and kernel.AssignProcessToJobObject(handle, int(proc._handle))):
        return lambda: kernel.CloseHandle(handle)
    kernel.CloseHandle(handle)
    return None


def capture(stream, buffer):
    while data := stream.read(4096):
        remaining = MAX_OUTPUT + 1 - len(buffer)
        if remaining > 0:
            buffer.extend(data[:remaining])


def run_shell(root, stop, command, timeout=30):
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ValueError("timeout 必须是 1 到 120 秒的整数。")
    check_stop(stop)
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "CURSORLAB_TOKEN"):
        env.pop(key, None)
    env["PYTHONUTF8"] = "1"
    env["PATH"] = str(Path(os.sys.executable).parent) + os.pathsep + env.get("PATH", "")
    if getattr(os.sys, "frozen", False):
        runtime = Path(os.sys.executable).parent / "runtime"
        if runtime.is_dir():
            env["PATH"] = str(runtime) + os.pathsep + env["PATH"]
    opts = hidden_process() if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(command, shell=True, cwd=root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **opts)
    close_job = windows_job(proc) if os.name == "nt" else None
    buffers = [bytearray(), bytearray()]
    threads = [threading.Thread(target=capture, args=(stream, buffer), daemon=True)
               for stream, buffer in zip((proc.stdout, proc.stderr), buffers)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while proc.poll() is None or any(thread.is_alive() for thread in threads):
            check_stop(stop)
            if time.monotonic() >= deadline:
                timed_out = True
                break
            stop.wait(0.05)
    finally:
        if close_job:
            close_job()
        elif proc.poll() is None or any(thread.is_alive() for thread in threads):
            kill_tree(proc)
        proc.wait(timeout=6)
        for thread in threads:
            thread.join(0.5)
    def decode(buffer):
        data = bytes(buffer[:MAX_OUTPUT])
        try:
            result = data.decode("utf-8")
        except UnicodeDecodeError:
            result = data.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")
        return result + ("\n…[输出已截断]" if len(buffer) > MAX_OUTPUT else "")
    return {"stdout": decode(buffers[0]), "stderr": decode(buffers[1]),
            "exit_code": 124 if timed_out else proc.returncode, "timed_out": timed_out}


def schema(description, required, **properties):
    return {"description": description, "parameters": {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False}}


STR = {"type": "string"}
INT = {"type": "integer"}
TOOLS = {
    "read": (read_file, schema("读取工作区文本或图片。文本带行号并支持分页，offset 从 1 起。",
                              ["path"], path=STR, offset=INT, limit=INT)),
    "write": (write_file, schema("新建或完整覆盖文本。已有文件的局部修改用 edit。",
                                 ["path", "content"], path=STR, content=STR)),
    "edit": (edit_file, schema("精确替换唯一片段。先读文件，old 必须唯一且包含足够上下文。",
                               ["path", "old", "new"], path=STR, old=STR, new=STR)),
    "bash": (run_shell, schema("在工作区运行系统 shell。Windows 使用 cmd.exe。用于列目录、搜索及测试。timeout 为秒。",
                              ["command"], command=STR, timeout=INT)),
}


def execute(root, stop, name, args):
    function, spec = TOOLS[name]
    params = spec["parameters"]
    if set(args) - set(params["properties"]) or not set(params["required"]) <= set(args):
        raise ValueError(f"{name} 参数不匹配。必填项：{', '.join(params['required'])}。")
    for field, value in args.items():
        kind = str if params["properties"][field]["type"] == "string" else int
        if type(value) is not kind:
            raise ValueError(f"{field} 参数类型错误。")
    return function(root, stop, **args)

