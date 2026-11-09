import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import Event

from .demo import DemoModel
from .loop import run_agent
from .model import ChatModel


def main():
    parser = argparse.ArgumentParser(description="Run CursorLab without its desktop UI.")
    parser.add_argument("--demo", choices=["mean", "mcp", "stop"])
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("prompt", nargs="?", default="修复 mean([]) 并运行测试")
    args = parser.parse_args()
    base = Path(__file__).resolve().parents[1]

    def emit(kind, data):
        print(json.dumps({"type": kind, **data}, ensure_ascii=True), flush=True)

    with tempfile.TemporaryDirectory(prefix="cursorlab-") as temporary:
        workspace = args.workspace.resolve()
        if args.demo:
            workspace = Path(temporary) / "examples"
            shutil.copytree(base / "examples", workspace)
            model = DemoModel(args.demo)
            approve = None
        else:
            model = ChatModel(os.environ.get("CURSORLAB_BASE_URL", "https://api.openai.com/v1"),
                              os.environ.get("OPENAI_API_KEY", ""),
                              os.environ.get("CURSORLAB_MODEL", ""))
            approve = lambda command: input(f"允许执行 {command!r}？[y/N] ").strip().lower() == "y"
        run_agent(workspace, args.prompt, model, Event(), emit, base / "mcp.json", approve=approve)


if __name__ == "__main__":
    main()

