"""Gera o launcher falso (um .exe C# minúsculo, compilado com o csc.exe do Windows)."""
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path

CS_TEMPLATE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32;

static class SteamSwapStub
{
    const string Mode = @"@@MODE@@";
    const string TargetPath = @"@@PATH@@";
    const string TargetArgs = @"@@ARGS@@";
    const string WorkDir = @"@@WORKDIR@@";
    const string SteamAppId = @"@@APPID@@";
    const bool FollowFolder = @@FOLLOW@@;
    const int GraceSeconds = 15;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int MessageBox(IntPtr hWnd, string text, string caption, uint type);

    static int Main()
    {
        try { return Mode == "steam" ? RunSteam() : RunExe(); }
        catch (Exception ex)
        {
            MessageBox(IntPtr.Zero, "Não foi possível iniciar o jogo de destino:\n\n" + ex.Message, "SteamSwap", 0x10);
            return 1;
        }
    }

    static int RunExe()
    {
        if (!File.Exists(TargetPath)) throw new FileNotFoundException("Arquivo não encontrado: " + TargetPath);
        string dir = Path.GetDirectoryName(TargetPath);
        var psi = new ProcessStartInfo(TargetPath, TargetArgs);
        psi.WorkingDirectory = WorkDir.Length > 0 ? WorkDir : dir;
        psi.UseShellExecute = true;
        using (Process p = Process.Start(psi)) { if (p != null) p.WaitForExit(); }
        if (FollowFolder) WaitForFolderProcesses(dir);
        return 0;
    }

    // Launchers costumam abrir o jogo de verdade e fechar: seguimos qualquer
    // processo vindo da pasta do destino até ficar 15 s sem nenhum.
    static void WaitForFolderProcesses(string dir)
    {
        string prefix = dir.TrimEnd('\\') + "\\";
        int self = Process.GetCurrentProcess().Id;
        DateTime quietSince = DateTime.UtcNow;
        while (true)
        {
            if (AnyUnder(prefix, self)) quietSince = DateTime.UtcNow;
            else if ((DateTime.UtcNow - quietSince).TotalSeconds > GraceSeconds) return;
            Thread.Sleep(2000);
        }
    }

    static bool AnyUnder(string prefix, int self)
    {
        foreach (Process p in Process.GetProcesses())
        {
            try
            {
                if (p.Id == self) continue;
                if (p.MainModule.FileName.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return true;
            }
            catch { }
            finally { p.Dispose(); }
        }
        return false;
    }

    static int RunSteam()
    {
        ProcessStartInfo psi = new ProcessStartInfo("steam://rungameid/" + SteamAppId);
        psi.UseShellExecute = true;
        Process.Start(psi);
        string key = @"HKEY_CURRENT_USER\Software\Valve\Steam\Apps\" + SteamAppId;
        DateTime start = DateTime.UtcNow;
        while (!IsRunning(key))
        {
            if ((DateTime.UtcNow - start).TotalSeconds > 90) return 0;
            Thread.Sleep(1000);
        }
        while (IsRunning(key)) Thread.Sleep(2000);
        return 0;
    }

    static bool IsRunning(string key)
    {
        object v = Registry.GetValue(key, "Running", 0);
        return v is int && (int)v == 1;
    }
}
'''


@dataclass
class Target:
    kind: str = "exe"          # "exe" ou "steam"
    path: str = ""             # exe/atalho a iniciar (kind="exe")
    args: str = ""
    workdir: str = ""
    steam_appid: int = 0       # kind="steam"
    follow_folder: bool = True  # esperar também processos filhos/launchers da pasta

    def validate(self):
        if self.kind == "exe":
            if not Path(self.path).is_file():
                raise ValueError(f"Arquivo de destino não existe: {self.path}")
        elif self.kind == "steam":
            if self.steam_appid <= 0:
                raise ValueError("AppID do jogo de destino inválido")
        else:
            raise ValueError(f"Tipo de destino desconhecido: {self.kind}")

    def to_dict(self) -> dict:
        return asdict(self)


def _cs_string(s: str) -> str:
    return s.replace('"', '""')


def find_csc() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for fw in ("Framework64", "Framework"):
        p = windir / "Microsoft.NET" / fw / "v4.0.30319" / "csc.exe"
        if p.is_file():
            return p
    raise RuntimeError("csc.exe (.NET Framework 4) não encontrado neste Windows")


def render_source(target: Target) -> str:
    repl = {
        "@@MODE@@": target.kind,
        "@@PATH@@": _cs_string(target.path),
        "@@ARGS@@": _cs_string(target.args),
        "@@WORKDIR@@": _cs_string(target.workdir),
        "@@APPID@@": str(int(target.steam_appid)),
        "@@FOLLOW@@": "true" if target.follow_folder else "false",
    }
    src = CS_TEMPLATE
    for k, v in repl.items():
        src = src.replace(k, v)
    return src


def build_stub(target: Target, out_path: Path) -> Path:
    target.validate()
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory(prefix="steamswap_") as tmp:
        cs = Path(tmp) / "stub.cs"
        exe = Path(tmp) / "stub.exe"
        cs.write_text(render_source(target), encoding="utf-8-sig")
        proc = subprocess.run(
            [str(find_csc()), "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu",
             f"/out:{exe}", str(cs)],
            capture_output=True, text=True, errors="replace")
        if proc.returncode != 0 or not exe.is_file():
            raise RuntimeError("Falha ao compilar o launcher:\n" + (proc.stdout + proc.stderr).strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe, out_path)
    return out_path
