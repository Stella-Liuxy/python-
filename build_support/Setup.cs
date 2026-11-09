using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;

internal static class Setup
{
    [STAThread]
    private static int Main(string[] args)
    {
        bool silent = Array.IndexOf(args, "--quiet") >= 0;
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                   "Programs", "CursorLab");
        int target = Array.IndexOf(args, "--target");
        if (silent && target >= 0 && target + 1 < args.Length)
            root = Path.GetFullPath(args[target + 1]);
        string destination = Path.Combine(root, "1.0.0");
        string exe = Path.Combine(destination, "CursorLab.exe");
        string staging = Path.Combine(root, ".setup-" + Guid.NewGuid().ToString("N"));
        try
        {
            if (!silent && MessageBox.Show(
                    "安装 CursorLab 1.0.0 到当前用户目录？\n\n" + root,
                    "CursorLab 安装程序", MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK)
                return 0;
            Directory.CreateDirectory(root);
            if (!Directory.Exists(destination))
            {
                Directory.CreateDirectory(staging);
                using (Stream bytes = Assembly.GetExecutingAssembly().GetManifestResourceStream("CursorLab.payload"))
                using (ZipArchive archive = new ZipArchive(bytes))
                    archive.ExtractToDirectory(staging);
                if (!File.Exists(Path.Combine(staging, "CursorLab.exe")))
                    throw new InvalidDataException("安装内容缺少 CursorLab.exe。");
                Directory.Move(staging, destination);
            }
            if (!File.Exists(exe))
                throw new FileNotFoundException("现有安装目录不完整，请移除该版本目录后重试。", exe);
            if (!silent)
            {
                string shortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                                               "CursorLab.lnk");
                try
                {
                    dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
                    dynamic link = shell.CreateShortcut(shortcut);
                    link.TargetPath = exe;
                    link.WorkingDirectory = destination;
                    link.Description = "CursorLab 桌面代码助手";
                    link.Save();
                }
                catch (Exception)
                {
                    MessageBox.Show("已安装，开始菜单快捷方式创建失败。可从以下位置打开：\n" + exe, "CursorLab");
                }
                Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = destination });
            }
            return 0;
        }
        catch (Exception ex)
        {
            if (!silent)
                MessageBox.Show(ex.Message, "CursorLab 安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            if (Directory.Exists(staging))
                Directory.Delete(staging, true);
        }
    }
}

