import argparse
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time

import uvicorn

from server.api import Service

TITLE = "CursorLab - 桌面代码助手"
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def data_directory():
    if override := os.environ.get("CURSORLAB_DATA"):
        return Path(override).resolve()
    parent = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return parent / "CursorLabData"


class DesktopBridge:
    def __init__(self):
        self.window = None

    def pick_workspace(self):
        import webview
        selected = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return str(selected[0]) if selected else ""


def make_config(service, port):
    return uvicorn.Config(service.app, host="127.0.0.1", port=port,
                          access_log=False, log_config=None, ws="websockets")


def desktop(service):
    import webview

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(make_config(service, port))
        background = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        background.start()
        for _ in range(200):
            if server.started:
                break
            if not background.is_alive():
                break
            time.sleep(0.025)
        if not server.started:
            raise RuntimeError("本地后端无法启动。")
        bridge = DesktopBridge()
        bridge.window = webview.create_window(
            TITLE, f"http://127.0.0.1:{port}/#{service.token}", js_api=bridge,
            width=1260, height=780, min_size=(920, 600))
        try:
            webview.start(gui="edgechromium", private_mode=True)
        finally:
            for job in list(service.jobs.values()):
                job.stop.set()
            server.should_exit = True
            background.join(timeout=6)


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--backend", action="store_true")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--mcp-config", type=Path)
    args = parser.parse_args()
    token = os.environ.get("CURSORLAB_TOKEN")
    if args.backend and not token:
        parser.error("请先设置 CURSORLAB_TOKEN。")
    service = Service(BASE, data_directory(), token or secrets.token_urlsafe(32), args.mcp_config)
    if args.backend:
        uvicorn.Server(make_config(service, args.port)).run()
    else:
        desktop(service)


if __name__ == "__main__":
    main()

