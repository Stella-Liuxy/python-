# Windows 安装包构建

构建环境为 Windows x64 和 Python 3.12 x64。运行应用需要 Edge WebView2 Runtime 和 .NET Framework 4.5 或更高版本。构建器使用系统 .NET Framework 的 C# 编译器。

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[build]"
.\.venv\Scripts\python.exe build_support\package_windows.py --out dist
~~~

PyInstaller 将应用、WebView 界面和示例收进目录。构建器附带一份 Python 标准库运行时供 bash 的 Python 命令及示例 MCP Server 使用。C# 安装器把目录打包为 `dist\CursorLab-Setup-1.0.0-x64.exe`。安装程序以当前用户运行，将文件写入 `%LOCALAPPDATA%\Programs\CursorLab\1.0.0`，创建开始菜单快捷方式并打开窗口。应用样例写在 `%LOCALAPPDATA%\CursorLabData`。

发布时将安装器作为 `v1.0.0` 的 Release 附件上传。双击安装后试跑修复样例、MCP 查询和中断。安装包未做代码签名。源代码仓库忽略安装器、构建目录及本机密钥文件。

卸载时先退出应用，再从上述安装目录移除相应版本并删除开始菜单快捷方式。练习数据与安装目录分开保存。
