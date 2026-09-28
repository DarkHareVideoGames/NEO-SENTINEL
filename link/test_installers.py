"""Testes de conformidade multiplataforma dos installers do NEO//LINK.

NÃO instala nada no sistema: só analisa os ficheiros e exercita o bootstrap
num directório temporário.

    python link/test_installers.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FORBIDDEN_NAMES = ("MASTER", "FEEDBACKAI", "DEFUNCTUMNOCTIS", "DefunctumNoctis")
FORBIDDEN_PATHS = (
    r"C:\\+ComfyUI",
    r"C:/ComfyUI",
    r"/home/[a-z]+/",
    r"C:/DNAI",
    r"C:\\+DNAI",
)

RUNTIME_SOURCES = ["neo_link.py", "bootstrap.py"] + [
    f"neolink/{n}.py"
    for n in ("__init__", "config", "identity", "collectors", "gpu",
              "services", "server", "pairing", "pairing_server")
]
INSTALLERS = ["install.sh", "install.ps1"]


def read(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    """Remove comentários e docstrings, para os testes verem só o que corre.

    Sem isto, um installer que diz "não precisa de Bash" seria acusado de
    depender de Bash. As docstrings também saem, porque descrevem o que o
    código NÃO faz.
    """
    import io
    import tokenize

    # Blocos <# ... #> (comentários do PowerShell) saem antes de tokenizar.
    text = re.sub(r"<#.*?#>", " ", text, flags=re.S)

    kept: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    except (tokenize.TokenError, IndentationError):
        return "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith(("#", "//", "*"))
        )
    return " ".join(kept)


def read_code(name: str) -> str:
    """Só o código, sem comentários."""
    return strip_comments(read(name))


def find_bash() -> str | None:
    for path in (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        try:
            subprocess.run([path, "--version"], capture_output=True, timeout=20)
            return path
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def find_pwsh() -> str | None:
    for name in ("pwsh", "powershell"):
        try:
            result = subprocess.run(
                [name, "-NoProfile", "-Command", "$PSVersionTable.PSVersion"],
                capture_output=True, timeout=30,
            )
            if result.returncode == 0:
                return name
        except (OSError, subprocess.SubprocessError):
            continue
    return None


class TestSyntax(unittest.TestCase):
    """Cada installer tem de ser sintaticamente válido na sua plataforma."""

    def test_bash_syntax(self):
        bash = find_bash()
        if not bash:
            self.skipTest("bash indisponível neste ambiente")
        result = subprocess.run(
            [bash, "-n", str(HERE / "install.sh")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_powershell_syntax(self):
        pwsh = find_pwsh()
        if not pwsh:
            self.skipTest("PowerShell indisponível neste ambiente")
        script = str(HERE / "install.ps1").replace("\\", "/")
        command = (
            "$errors=$null; "
            f"[void][System.Management.Automation.Language.Parser]::"
            f"ParseFile('{script}',[ref]$null,[ref]$errors); "
            "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            [pwsh, "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bootstrap_is_valid_python(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(HERE / "bootstrap.py")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestNoPlatformAssumptions(unittest.TestCase):
    """Nenhum installer pode depender da ferramenta de outra plataforma."""

    def test_windows_installer_does_not_need_bash(self):
        code = read_code("install.ps1").lower()
        for forbidden in ("bash", "wsl", "cygwin", "choco", "chocolatey",
                          "git clone", "apt-get", "yum install", "brew install"):
            self.assertNotIn(forbidden, code,
                             f"install.ps1 não deve depender de {forbidden!r}")

    def test_windows_installer_uses_only_powershell(self):
        text = read("install.ps1")
        for expected in ("Invoke-WebRequest", "Get-Command", "$HOME",
                         "SetEnvironmentVariable"):
            self.assertIn(expected, text, f"falta {expected!r} no install.ps1")

    def test_posix_installer_does_not_need_powershell(self):
        code = read_code("install.sh").lower()
        for forbidden in ("powershell", "pwsh", ".ps1"):
            self.assertNotIn(forbidden, code,
                             f"install.sh não deve depender de {forbidden!r}")

    def test_installers_share_the_same_bootstrap(self):
        """A lógica comum vive num sítio só."""
        self.assertIn("bootstrap.py", read("install.sh"))
        self.assertIn("bootstrap.py", read("install.ps1"))
        self.assertTrue((HERE / "bootstrap.py").is_file())

    def test_no_system_modification(self):
        """Nenhum installer mexe no sistema."""
        code = "".join(read_code(n) for n in INSTALLERS + ["bootstrap.py"])
        for forbidden in ("sudo ", "chown /", "chmod 777", "iptables",
                          "netsh advfirewall", "new-netfirewallrule",
                          "launchctl load", "systemctl enable",
                          "set-executionpolicy", "reg add"):
            self.assertNotIn(forbidden, code.lower(),
                             f"installer não deve usar {forbidden!r}")

    def test_termux_is_not_assumed(self):
        """O Termux não tem root, systemd nem sudo — nada disso é assumido."""
        code = "".join(read_code(n) for n in ("install.sh", "bootstrap.py"))
        for forbidden in ("systemctl", "systemd", "sudo"):
            self.assertNotIn(forbidden, code,
                             f"Termux não tem {forbidden!r}")
        self.assertIn("PREFIX", read_code("install.sh"),
                      "deve detectar o ambiente Termux")

    def test_macos_does_not_need_homebrew(self):
        code = (read_code("install.sh") + read_code("bootstrap.py")).lower()
        for forbidden in ("brew install", "port install"):
            self.assertNotIn(forbidden, code,
                             f"macOS não deve depender de {forbidden!r}")


class TestNoHardcodedEnvironment(unittest.TestCase):
    """Nenhum nome ou caminho da máquina de desenvolvimento."""

    def test_no_machine_names_in_installer_or_bootstrap(self):
        for name in INSTALLERS + ["bootstrap.py"]:
            text = read(name)
            for forbidden in FORBIDDEN_NAMES:
                self.assertNotIn(forbidden, text,
                                 f"{name} não deve conter {forbidden!r}")

    def test_no_machine_names_in_runtime(self):
        for name in RUNTIME_SOURCES:
            text = read(name)
            for forbidden in FORBIDDEN_NAMES:
                self.assertNotIn(forbidden, text,
                                 f"{name} não deve conter {forbidden!r}")

    def test_no_personal_paths(self):
        for name in INSTALLERS + ["bootstrap.py"] + RUNTIME_SOURCES:
            text = read(name)
            for pattern in FORBIDDEN_PATHS:
                self.assertIsNone(
                    re.search(pattern, text),
                    f"{name} pode conter um caminho pessoal ({pattern!r})",
                )

    def test_example_config_has_no_windows_specific_path(self):
        """O exemplo não deve impor paths de uma máquina Windows."""
        example = json.loads(read("config.example.json"))
        service = example["services"][0]
        blob = str(service.get("path", "")) + str(service.get("python", ""))
        self.assertNotIn("ComfyUI", blob)


class TestSecurityDefaults(unittest.TestCase):
    """O comportamento por omissão continua seguro."""

    def test_example_config_disables_control(self):
        example = json.loads(read("config.example.json"))
        self.assertFalse(example["server"]["allow_control"])
        self.assertEqual(example["server"]["host"], "127.0.0.1")

    def test_bootstrap_generates_safe_config(self):
        import bootstrap

        config = bootstrap.default_config(name="N", host=None, port=8765)
        self.assertFalse(config["server"]["allow_control"])
        self.assertEqual(config["server"]["host"], "127.0.0.1")
        self.assertIsNone(config["server"]["token"])

    def test_bootstrap_opens_no_ports(self):
        """Não há código de firewall, port forwarding ou router."""
        code = "".join(read_code(n) for n in INSTALLERS + ["bootstrap.py"]).lower()
        for forbidden in ("port forward", "upnp", "iptables", "firewall",
                          "set-netfirewallrule", "netsh "):
            self.assertNotIn(forbidden, code)

    def test_no_hardcoded_credentials(self):
        for name in INSTALLERS + ["bootstrap.py"] + RUNTIME_SOURCES:
            text = read(name)
            for pattern in (r"ghp_", r"gho_", r"github_pat_", r"sk-[A-Za-z0-9]{20}"):
                self.assertIsNone(re.search(pattern, text),
                                  f"{name} pode conter um token")


class TestBootstrapLogic(unittest.TestCase):
    """Exercita o bootstrap num directório temporário. Não instala no sistema."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "link"

    def _stage_runtime(self):
        """Copia o runtime mínimo para o CredentialStore funcionar."""
        import bootstrap

        for relative in bootstrap.RUNTIME_FILES:
            source = HERE / relative
            if source.is_file():
                dest = self.root / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "neolink" / "__init__.py").write_text(
            (HERE / "neolink" / "__init__.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def test_context_paths_are_platform_correct(self):
        import bootstrap

        ctx = bootstrap.Context.create(self.root)
        self.assertEqual(ctx.install_dir, self.root)
        expected = "python.exe" if sys.platform == "win32" else "python"
        self.assertTrue(str(ctx.venv_python).endswith(expected))

    def test_node_id_is_persistent(self):
        import bootstrap

        self._stage_runtime()
        ctx = bootstrap.Context.create(self.root)
        rep = bootstrap.Reporter(assume_yes=True)
        first = bootstrap.ensure_node_id(ctx, rep)
        second = bootstrap.ensure_node_id(ctx, rep)
        self.assertEqual(first, second, "o node_id mudou entre chamadas")
        self.assertTrue(first)

    def test_node_id_is_not_derived_from_name_or_hostname(self):
        import bootstrap

        self._stage_runtime()
        ctx = bootstrap.Context.create(self.root)
        node_id = bootstrap.ensure_node_id(ctx, bootstrap.Reporter(assume_yes=True))
        self.assertNotIn(node_id, (ctx.install_dir.name,))
        self.assertRegex(node_id, r"^nx1-[0-9a-f]{16}$")

    def test_config_is_not_overwritten(self):
        import bootstrap

        ctx = bootstrap.Context.create(self.root)
        ctx.install_dir.mkdir(parents=True, exist_ok=True)
        ctx.config_file.write_text(json.dumps({
            "node": {"name": "JA-EXISTE"},
            "server": {"host": "0.0.0.0", "port": 9999,
                       "allow_control": False, "token": None},
            "services": [],
        }), encoding="utf-8")

        bootstrap.ensure_config(ctx, "OUTRO", "10.0.0.1", 1234,
                                bootstrap.Reporter(assume_yes=True))
        saved = json.loads(ctx.config_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["node"]["name"], "JA-EXISTE")
        self.assertEqual(saved["server"]["port"], 9999)

    def test_new_config_without_name_defers_to_hostname(self):
        import bootstrap

        ctx = bootstrap.Context.create(self.root)
        ctx.install_dir.mkdir(parents=True, exist_ok=True)
        config = bootstrap.ensure_config(ctx, None, None, 8765,
                                         bootstrap.Reporter(assume_yes=True))
        # Sem `name`, o LINK usa o hostname real da máquina.
        self.assertIsNone(config.get("node", {}).get("name", None))
        self.assertFalse(config["server"]["allow_control"])

    def test_runtime_file_list_matches_the_repo(self):
        """A lista do bootstrap tem de corresponder ao que existe."""
        import bootstrap

        for relative in bootstrap.RUNTIME_FILES:
            self.assertTrue((HERE / relative).is_file(),
                            f"{relative} listado no bootstrap mas não existe")
        for forbidden in ("config.json", "credentials.json"):
            self.assertNotIn(forbidden, bootstrap.RUNTIME_FILES)

    def test_diagnose_reports_missing_install(self):
        import bootstrap

        ctx = bootstrap.Context.create(self.root)
        problems = bootstrap.diagnose(ctx, bootstrap.Reporter(assume_yes=True))
        self.assertGreater(problems, 0, "deveria reportar problemas")


if __name__ == "__main__":
    unittest.main(verbosity=2)
