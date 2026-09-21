"""Gera os executáveis do SteamSwap, compilados com o csc.exe do Windows:

- o launcher que fica no lugar do jogo hospedeiro, com um ou mais destinos;
- os pequenos seletores apontados pelos atalhos da área de trabalho, que
  marcam qual destino usar e pedem à Steam para abrir o hospedeiro.
"""
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import List

from .csc import cs_string as _cs_string
from .csc import compile_cs

DEFAULT_PICKER_TIMEOUT_MS = 12000

# Se houver mais de um destino e nenhum atalho específico tiver sido usado
# (arquivo-marcador ausente), mostra essa telinha para escolher, com um
# tempo limite que cai no destino padrão — nunca trava esperando alguém
# com um controle na mão que não vê teclado/mouse.
STUB_TEMPLATE = r'''
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class SteamSwapStub
{
    struct Tgt
    {
        public string Id, Name, Kind, ExePath, Args, WorkDir, SteamAppId;
        public bool FollowFolder;
    }

    static readonly Tgt[] Targets = new Tgt[] {
        @@TARGETS@@
    };
    const string DefaultId = @"@@DEFAULT_ID@@";
    const string MarkerPath = @"@@MARKER_PATH@@";
    const int PickerTimeoutMs = @@PICKER_TIMEOUT_MS@@;
    const int GraceSeconds = 15;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int MessageBox(IntPtr hWnd, string text, string caption, uint type);

    [STAThread]
    static int Main()
    {
        try
        {
            Tgt t = ChooseTarget();
            return t.Kind == "steam" ? RunSteam(t) : RunExe(t);
        }
        catch (Exception ex)
        {
            MessageBox(IntPtr.Zero, "Não foi possível iniciar o destino:\n\n" + ex.Message, "SteamSwap", 0x10);
            return 1;
        }
    }

    static Tgt ChooseTarget()
    {
        string wanted = ReadAndClearMarker();
        if (wanted.Length > 0)
            foreach (Tgt t in Targets)
                if (t.Id == wanted) return t;

        if (Targets.Length == 1) return Targets[0];

        Tgt picked;
        if (TryPick(out picked)) return picked;

        foreach (Tgt t in Targets)
            if (t.Id == DefaultId) return t;
        return Targets[0];
    }

    // Um atalho específico grava aqui, antes de pedir à Steam para abrir o
    // hospedeiro, qual destino usar desta vez. Consumido uma única vez.
    static string ReadAndClearMarker()
    {
        try
        {
            if (!File.Exists(MarkerPath)) return "";
            string id = File.ReadAllText(MarkerPath).Trim();
            try { File.Delete(MarkerPath); } catch { }
            return id;
        }
        catch { return ""; }
    }

    static bool TryPick(out Tgt picked)
    {
        picked = default(Tgt);
        using (Form f = new Form())
        using (ListBox lb = new ListBox())
        using (Label lbl = new Label())
        using (Button ok = new Button())
        using (System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer())
        {
            f.Text = "SteamSwap";
            f.FormBorderStyle = FormBorderStyle.FixedDialog;
            f.MaximizeBox = false;
            f.MinimizeBox = false;
            f.StartPosition = FormStartPosition.CenterScreen;
            f.ClientSize = new Size(320, 232);
            f.TopMost = true;

            int defaultIndex = 0;
            lb.Dock = DockStyle.Top;
            lb.Height = 160;
            for (int i = 0; i < Targets.Length; i++)
            {
                lb.Items.Add(Targets[i].Name);
                if (Targets[i].Id == DefaultId) defaultIndex = i;
            }
            lb.SelectedIndex = defaultIndex;
            lb.DoubleClick += delegate { f.DialogResult = DialogResult.OK; f.Close(); };

            lbl.Dock = DockStyle.Top;
            lbl.Height = 24;
            lbl.TextAlign = ContentAlignment.MiddleCenter;

            ok.Text = "Abrir";
            ok.Dock = DockStyle.Bottom;
            ok.Height = 32;
            ok.Click += delegate { f.DialogResult = DialogResult.OK; f.Close(); };
            f.AcceptButton = ok;

            int remainingMs = PickerTimeoutMs;
            Action updateLabel = delegate
            {
                lbl.Text = "Abre \"" + Targets[defaultIndex].Name + "\" em " + (remainingMs / 1000 + 1) + "s...";
            };
            updateLabel();
            timer.Interval = 500;
            timer.Tick += delegate
            {
                remainingMs -= timer.Interval;
                if (remainingMs <= 0) { f.DialogResult = DialogResult.Cancel; f.Close(); return; }
                updateLabel();
            };

            f.Controls.Add(lb);
            f.Controls.Add(lbl);
            f.Controls.Add(ok);
            timer.Start();
            DialogResult r = f.ShowDialog();
            timer.Stop();

            if (r == DialogResult.OK && lb.SelectedIndex >= 0)
            {
                picked = Targets[lb.SelectedIndex];
                return true;
            }
            return false;
        }
    }

    static int RunExe(Tgt t)
    {
        if (!File.Exists(t.ExePath)) throw new FileNotFoundException("Arquivo não encontrado: " + t.ExePath);
        string dir = Path.GetDirectoryName(t.ExePath);
        var psi = new ProcessStartInfo(t.ExePath, t.Args);
        psi.WorkingDirectory = t.WorkDir.Length > 0 ? t.WorkDir : dir;
        psi.UseShellExecute = true;
        using (Process p = Process.Start(psi)) { if (p != null) p.WaitForExit(); }
        if (t.FollowFolder) WaitForFolderProcesses(dir);
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

    static int RunSteam(Tgt t)
    {
        ProcessStartInfo psi = new ProcessStartInfo("steam://rungameid/" + t.SteamAppId);
        psi.UseShellExecute = true;
        Process.Start(psi);
        string key = @"HKEY_CURRENT_USER\Software\Valve\Steam\Apps\" + t.SteamAppId;
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

# O seletor não tem interface: só grava qual destino usar e manda a Steam
# abrir o hospedeiro. É ele que cada atalho da área de trabalho aponta.
SELECTOR_TEMPLATE = r'''
using System.Diagnostics;
using System.IO;

static class SteamSwapSelector
{
    const string MarkerPath = @"@@MARKER_PATH@@";
    const string TargetId = @"@@TARGET_ID@@";
    const string SteamUri = @"steam://rungameid/@@HOST_APPID@@";

    static int Main()
    {
        try
        {
            string dir = Path.GetDirectoryName(MarkerPath);
            if (dir.Length > 0) Directory.CreateDirectory(dir);
            File.WriteAllText(MarkerPath, TargetId);
            var psi = new ProcessStartInfo(SteamUri);
            psi.UseShellExecute = true;
            Process.Start(psi);
        }
        catch { }
        return 0;
    }
}
'''


@dataclass
class Target:
    id: str = ""                  # gerado ao adicionar; vazio só antes disso
    name: str = "Destino"
    kind: str = "exe"              # "exe" ou "steam"
    path: str = ""                 # exe/atalho a iniciar (kind="exe")
    args: str = ""
    workdir: str = ""
    steam_appid: int = 0           # kind="steam"
    follow_folder: bool = True     # esperar também processos filhos/launchers da pasta
    shortcut: str = ""             # caminho do .lnk na área de trabalho, se algum foi criado

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

    @staticmethod
    def from_dict(d: dict) -> "Target":
        known = Target.__dataclass_fields__.keys()
        return Target(**{k: v for k, v in d.items() if k in known})


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def prepared(target: Target, name: str) -> Target:
    """Cópia do destino já com id novo, nome e sem atalho (ainda não criado)."""
    return replace(target, id=new_id(), name=(name or "").strip() or "Destino", shortcut="")


def _target_literal(t: Target) -> str:
    return (
        'new Tgt { Id = @"%s", Name = @"%s", Kind = @"%s", ExePath = @"%s", '
        'Args = @"%s", WorkDir = @"%s", SteamAppId = @"%s", FollowFolder = %s }'
    ) % (
        _cs_string(t.id), _cs_string(t.name), _cs_string(t.kind), _cs_string(t.path),
        _cs_string(t.args), _cs_string(t.workdir), _cs_string(int(t.steam_appid)),
        "true" if t.follow_folder else "false",
    )


def render_source(targets: List[Target], default_id: str, marker_path,
                  picker_timeout_ms: int = DEFAULT_PICKER_TIMEOUT_MS) -> str:
    if not targets:
        raise ValueError("Precisa de ao menos um destino")
    literals = ",\n        ".join(_target_literal(t) for t in targets)
    src = STUB_TEMPLATE
    src = src.replace("@@TARGETS@@", literals)
    src = src.replace("@@DEFAULT_ID@@", _cs_string(default_id))
    src = src.replace("@@MARKER_PATH@@", _cs_string(marker_path))
    src = src.replace("@@PICKER_TIMEOUT_MS@@", str(int(picker_timeout_ms)))
    return src


def render_selector_source(host_appid: int, target_id: str, marker_path) -> str:
    src = SELECTOR_TEMPLATE
    src = src.replace("@@MARKER_PATH@@", _cs_string(marker_path))
    src = src.replace("@@TARGET_ID@@", _cs_string(target_id))
    src = src.replace("@@HOST_APPID@@", str(int(host_appid)))
    return src


def build_stub(targets: List[Target], default_id: str, marker_path, out_path,
               picker_timeout_ms: int = DEFAULT_PICKER_TIMEOUT_MS) -> Path:
    for t in targets:
        t.validate()
    src = render_source(targets, default_id, str(marker_path), picker_timeout_ms)
    return compile_cs(src, out_path, refs=("System.Windows.Forms.dll", "System.Drawing.dll"))


def build_selector(host_appid: int, target_id: str, marker_path, out_path) -> Path:
    src = render_selector_source(host_appid, target_id, str(marker_path))
    return compile_cs(src, out_path)
