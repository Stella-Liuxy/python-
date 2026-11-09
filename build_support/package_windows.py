"""Build a self-contained Windows x64 installation executable."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import PyInstaller.__main__


def copy_runtime(destination):
    origin = Path(sys.base_prefix)
    destination.mkdir(exist_ok=True)
    for pattern in ("python.exe", "python*.dll", "vcruntime*.dll", "LICENSE.txt"):
        for file in origin.glob(pattern):
            shutil.copy2(file, destination)
    for folder in ("DLLs", "Lib"):
        shutil.copytree(origin / folder, destination / folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc",
                                                     "idlelib", "tkinter", "turtledemo", "test"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("dist"))
    parser.add_argument("--skip-freeze", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    out = args.out.resolve()
    work = out / "freeze-work"
    bundle = out / "CursorLab"
    compiler = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    out.mkdir(parents=True, exist_ok=True)
    if not args.skip_freeze:
        PyInstaller.__main__.run([
            str(repo / "app.py"), "--name=CursorLab", "--windowed", "--onedir", "--noconfirm",
            "--distpath", str(out), "--workpath", str(work), "--specpath", str(work),
            "--hidden-import=uvicorn.logging", "--hidden-import=uvicorn.loops.auto",
            "--hidden-import=uvicorn.protocols.http.auto", "--hidden-import=uvicorn.protocols.http.h11_impl",
            "--hidden-import=uvicorn.protocols.websockets.websockets_impl",
            "--hidden-import=uvicorn.lifespan.on", "--hidden-import=anyio._backends._asyncio",
            "--hidden-import=webview.platforms.winforms", "--collect-all=webview",
            "--collect-all=clr_loader", "--collect-all=pythonnet",
            *[f"--add-data={repo / name}{';' if sys.platform == 'win32' else ':'}{target}"
              for name, target in [("ui", "ui"), ("examples", "examples"), ("mcp.json", "."),
                                   ("mcp_servers", "mcp_servers")]],
        ])
        copy_runtime(bundle / "runtime")
    if not (bundle / "CursorLab.exe").is_file():
        raise SystemExit("No frozen app in the requested output directory.")
    for example_cache in bundle.glob("_internal/examples/__pycache__/*.pyc"):
        example_cache.unlink()
    payload = out / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(bundle))
    installer = out / "CursorLab-Setup-1.0.0-x64.exe"
    subprocess.run([str(compiler), "/nologo", "/codepage:65001", "/target:winexe", "/platform:x64",
                    "/reference:System.IO.Compression.dll",
                    "/reference:System.IO.Compression.FileSystem.dll", "/reference:System.Windows.Forms.dll",
                    "/reference:Microsoft.CSharp.dll", f"/resource:{payload},CursorLab.payload",
                    f"/out:{installer}", str(repo / "build_support" / "Setup.cs")], check=True)
    print(installer)


if __name__ == "__main__":
    main()

