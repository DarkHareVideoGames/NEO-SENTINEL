"""Bootstrap partilhado do NEO//LINK.

Toda a lógica comum de instalação vive aqui — é este ficheiro que tanto o
`install.sh` (Linux/macOS/Termux) como o `install.ps1` (Windows) executam. As
diferenças de plataforma ficam reduzidas ao launcher e ao mecanismo opcional
de arranque.

O que este módulo garante em todas as plataformas:

- Python 3.9+ e um venv isolado;
- apenas as dependências declaradas (psutil);
- config.json criado só se não existir, sempre com `allow_control: false`
  e a escutar apenas em localhost;
- o `node_id` gerado uma vez e preservado (nunca a partir de `name` ou
  `hostname`);
- o hostname sempre lido do sistema;
- nada de credenciais impressas, nada de portas abertas, nada de escrita
  fora do directório do utilizador.

Não altera nada do sistema: não usa sudo, não toca na firewall, não instala
serviços e não muda o router.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

MIN_PYTHON = (3, 9)

# Ficheiros de runtime. Só código — nunca config, credenciais ou logs.
RUNTIME_FILES = (
    "neo_link.py",
    "requirements.txt",
    "config.example.json",
    "neolink/__init__.py",
    "neolink/config.py",
    "neolink/identity.py",
    "neolink/collectors.py",
    "neolink/gpu.py",
    "neolink/services.py",
    "neolink/server.py",
    "neolink/pairing.py",
    "neolink/pairing_server.py",
)

DEFAULT_PORT = 8765
LOOPBACK = "127.0.0.1"


# ---------------------------------------------------------------------------
# Contexto
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """Onde tudo acontece. Derivado do HOME do utilizador, sem Assume-Yes."""

    home: Path
    install_dir: Path
    venv_dir: Path
    config_file: Path
    credentials_file: Path

    @classmethod
    def create(cls, root: Path | None = None) -> "Context":
        """Constrói o contexto a partir do directório do utilizador.

        Raiz por omissão: `~/.neo-x1/link` (Windows: `%USERPROFILE%\\.neo-x1\\link`;
        Termux: `$HOME/.neo-x1/link` dentro do prefixo do Termux).
        """
        base = root or (Path.home() / ".neo-x1" / "link")
        base = base.expanduser()
        return cls(
            home=base.parent.parent,
            install_dir=base,
            venv_dir=base / ".venv",
            config_file=base / "config.json",
            credentials_file=base / "credentials.json",
        )

    @property
    def is_termux(self) -> bool:
        return bool(os.environ.get("PREFIX", "").endswith("com.termux/app_packages")) or (
            Path("/data/data/com.termux").is_dir()
        )

    @property
    def venv_python(self) -> Path:
        """O interpretador do venv, com o nome certo para cada SO."""
        if os.name == "nt":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------


class Reporter:
    """Mensagens simples, iguais em todas as plataformas."""

    def __init__(self, assume_yes: bool = False) -> None:
        self.assume_yes = assume_yes

    def step(self, text: str) -> None:
        print(f"\n==> {text}")

    def ok(self, text: str) -> None:
        print(f"    OK   {text}")

    def info(self, text: str) -> None:
        print(f"    {text}")

    def warn(self, text: str) -> None:
        print(f"    aviso: {text}", file=sys.stderr)

    def die(self, text: str) -> "NoReturn":  # type: ignore[valid-type]
        print(f"\nerro: {text}", file=sys.stderr)
        raise SystemExit(1)

    def ask(self, prompt: str) -> str:
        if self.assume_yes:
            return ""
        try:
            return input(f"    {prompt} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1) from None


# ---------------------------------------------------------------------------
# Pré-requisitos
# ---------------------------------------------------------------------------


def check_python(ctx: Context, rep: Reporter) -> None:
    """Confirma Python 3.9+ no interpretador que vai correr o bootstrap."""
    if sys.version_info < MIN_PYTHON:
        rep.die(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ necessário "
            f"(esta a correr {platform.python_version()})"
        )
    rep.ok(f"Python {platform.python_version()} ({sys.executable})")


def find_system_python(rep: Reporter) -> Path | None:
    """Procura um Python utilizável no sistema, para criar o venv."""
    names = (["py", "python"] if os.name == "nt" else ["python3", "python"])
    for name in names:
        found = shutil.which(name)
        if not found:
            continue
        try:
            result = subprocess.run(
                [found, "-c", "import sys; print(1 if sys.version_info >= (3, 9) else 0)"],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.stdout.strip() == "1":
            return Path(found)
    return None


def ensure_venv(ctx: Context, rep: Reporter) -> Path:
    """Cria o venv se ainda não existir e devolve o interpretador."""
    if ctx.venv_python.is_file():
        rep.ok("ambiente já existe")
        return ctx.venv_python

    system = find_system_python(rep)
    if system is None:
        rep.die(
            "nenhum Python 3.9+ encontrado. Instale Python e volte a correr.\n"
            "       Windows: https://www.python.org/downloads/\n"
            "       macOS:   https://www.python.org/downloads/ (ou xcode-select --install)\n"
            "       Linux:   a distribuição, ou https://www.python.org/downloads/\n"
            "       Termux:  pkg install python"
        )
    rep.info(f"a criar o ambiente com {system}")
    try:
        venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(ctx.venv_dir)
    except Exception as exc:  # noqa: BLE001
        rep.die(f"não foi possível criar o ambiente: {exc}")

    if not ctx.venv_python.is_file():
        rep.die("ambiente criado mas o interpretador não foi encontrado")
    rep.ok("ambiente criado")
    return ctx.venv_python


def pip_install(python: Path, requirements: Path, rep: Reporter) -> None:
    """Instala as dependências, com recurso ao modo PEP 668 se for preciso."""
    base = [str(python), "-m", "pip", "install", "-q", "-r", str(requirements)]
    for extra in ([], ["--break-system-packages"]):
        try:
            subprocess.run(
                [*base, *extra],
                capture_output=True,
                text=True,
                timeout=900,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        probe = subprocess.run(
            [str(python), "-c", "import psutil"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if probe.returncode == 0:
            rep.ok("dependências instaladas")
            return
    rep.die("falha ao instalar as dependências")


# ---------------------------------------------------------------------------
# Ficheiros
# ---------------------------------------------------------------------------


def download(url: str, dest: Path, rep: Reporter) -> None:
    """Descarrega um ficheiro. Usa urllib (stdlib) em todas as plataformas."""
    import urllib.error
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            dest.write_bytes(response.read())
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        rep.die(f"não foi possível descarregar {url}: {exc}")


def install_runtime(ctx: Context, base_url: str, rep: Reporter) -> None:
    """Descarrega o runtime do LINK para o directório de instalação."""
    for relative in RUNTIME_FILES:
        dest = ctx.install_dir / relative
        download(f"{base_url.rstrip('/')}/{relative}", dest, rep)
    rep.ok(f"runtime instalado ({len(RUNTIME_FILES)} ficheiros)")


def remove_stale(ctx: Context, rep: Reporter) -> None:
    """Remove módulos que deixou de existir no upstream."""
    package = ctx.install_dir / "neolink"
    for module in package.glob("*.py"):
        relative = f"neolink/{module.name}"
        if relative not in RUNTIME_FILES:
            module.unlink()
            rep.info(f"removido obsoleto: {relative}")


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


def default_config(name: str | None, host: str | None, port: int) -> dict[str, Any]:
    """Configuração segura por omissão: localhost, sem controlo."""
    return {
        "node": {"name": name} if name else {},
        "server": {
            "host": host or LOOPBACK,
            "port": port,
            # O controlo remoto fica sempre desligado numa instalação nova.
            "allow_control": False,
            "token": None,
        },
        "services": [],
    }


def write_config(ctx: Context, config: dict[str, Any], rep: Reporter) -> None:
    ctx.config_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.config_file.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    try:  # só o dono lê a configuração
        ctx.config_file.chmod(0o600)
    except OSError:
        pass
    rep.ok("config.json criado")


def read_config(ctx: Context) -> dict[str, Any]:
    try:
        return json.loads(ctx.config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def ensure_config(ctx: Context, name: str | None, host: str | None,
                  port: int, rep: Reporter) -> dict[str, Any]:
    """Cria a configuração se não existir; nunca sobrescreve a existente."""
    if ctx.config_file.is_file():
        rep.ok("config.json já existe — preservado")
        return read_config(ctx)
    if not name:
        # Sem nome configurado o LINK usa o hostname real da máquina.
        name = rep.ask(
            f"Nome do node [{platform.node()}]: (enter para usar o hostname)"
        ) or None
    write_config(ctx, default_config(name, host, port), rep)
    return read_config(ctx)


def ensure_node_id(ctx: Context, rep: Reporter) -> str:
    """Garante o node_id, gerado uma vez e sempre preservado.

    Reutiliza a lógica já existente do LINK, para não haver duas identidades.
    """
    sys.path.insert(0, str(ctx.install_dir))
    from neolink.pairing import CredentialStore  # type: ignore[import-not-found]

    store = CredentialStore.load(ctx.credentials_file)
    node_id = store.node_id()
    if not store.count:
        rep.ok(f"node_id: {node_id}")
    else:
        rep.ok(f"node_id preservado: {node_id}")
    return node_id


# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------


def diagnose(ctx: Context, rep: Reporter) -> int:
    """Verifica a instalação sem alterar nada. Devolve nº de problemas."""
    problems = 0
    rep.step("Diagnóstico")

    if ctx.venv_python.is_file():
        rep.ok(f"Python do ambiente: {ctx.venv_python}")
    else:
        rep.warn("ambiente não encontrado")
        problems += 1

    missing = [
        relative for relative in RUNTIME_FILES
        if not (ctx.install_dir / relative).is_file()
    ]
    if missing:
        rep.warn(f"faltam {len(missing)} ficheiros: {', '.join(missing[:4])}")
        problems += 1
    else:
        rep.ok("runtime completo")

    if ctx.venv_python.is_file():
        probe = subprocess.run(
            [str(ctx.venv_python), "-c", "import psutil"],
            capture_output=True, text=True, timeout=60,
        )
        if probe.returncode == 0:
            rep.ok("dependências instaladas")
        else:
            rep.warn("psutil não está instalado")
            problems += 1

    if ctx.config_file.is_file():
        config = read_config(ctx)
        host = config.get("server", {}).get("host")
        control = config.get("server", {}).get("allow_control")
        rep.ok("config.json presente")
        rep.info(f"escuta em: {host or '?'}   allow_control: {control}")
        if control:
            rep.warn("allow_control está activo — o LINK aceita comandos")
        if host not in (None, LOOPBACK, "localhost"):
            rep.info("o LINK escuta fora do localhost; confirme a rede onde está")
    else:
        rep.warn("sem config.json")

    if ctx.credentials_file.is_file():
        try:
            data = json.loads(ctx.credentials_file.read_text(encoding="utf-8"))
            node_id = data.get("node_id")
            clients = len(data.get("credentials", []))
            rep.ok(f"identidade: {node_id or '(ainda não gerada)'} · {clients} pareado(s)")
        except (OSError, json.JSONDecodeError):
            rep.warn("credentials.json ilegível")
            problems += 1
    else:
        rep.info("ainda sem identidade persistente (cria-se ao primeiro arrancar)")

    print()
    if problems == 0:
        print("Diagnóstico: tudo OK")
    else:
        print(f"Diagnóstico: {problems} problema(s)")
    return problems


# ---------------------------------------------------------------------------
# Fluxos
# ---------------------------------------------------------------------------


def run_check(ctx: Context, rep: Reporter) -> int:
    return 1 if diagnose(ctx, rep) else 0


def run_uninstall(ctx: Context, rep: Reporter) -> int:
    import shutil as _shutil

    rep.step("Remover instalação")
    if not ctx.install_dir.is_dir():
        rep.ok(f"nada instalado em {ctx.install_dir}")
        return 0
    answer = rep.ask(
        f"Isto remove {ctx.install_dir}, incluindo a configuração e as "
        f"credenciais. Continuar? [s/N] "
    )
    if answer.lower() not in ("s", "sim", "y", "yes"):
        rep.info("cancelado")
        return 0
    _shutil.rmtree(ctx.install_dir)
    rep.ok(f"removido {ctx.install_dir}")
    return 0


def run_install(ctx: Context, base_url: str, name: str | None, host: str | None,
                port: int, mode: str, rep: Reporter) -> int:
    say = rep.step
    check_python(ctx, rep)

    if mode == "update":
        say("A actualizar o runtime (a configuração é preservada)")
    else:
        say("A instalar o NEO//LINK")

    install_runtime(ctx, base_url, rep)
    remove_stale(ctx, rep)

    say("A preparar o ambiente Python")
    python = ensure_venv(ctx, rep)
    say("A instalar dependências")
    pip_install(python, ctx.install_dir / "requirements.txt", rep)

    say("A configurar o node")
    ensure_config(ctx, name, host, port, rep)
    ensure_node_id(ctx, rep)

    say("Como arrancar")
    command = "neo-link" if os.name != "nt" else "python neo_link.py"
    rep.info(f"ver o estado:      neo-link --check" if os.name != "nt"
             else "  (use o atalho criado pelo instalador)")
    rep.info(f"arrancar:          {command}")
    rep.info(f"emparelhar:        {command} --pair")
    rep.info(f"serviços:          editar {ctx.config_file}")

    diagnose(ctx, rep)

    rep.step("Instalação concluída")
    rep.info(f"O LINK escuta em {LOOPBACK} e não aceita comandos.")
    rep.info("Para o usar a partir de outra máquina, exponha-o explicitamente:")
    rep.info(f"    {command} --host 0.0.0.0")
    if ctx.is_termux:
        rep.info("Termux: mantenha o ecrã ligado com `termux-wake-lock`.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="neo-link-bootstrap",
        description="Instalação partilhada do NEO//LINK (usada por install.sh e install.ps1)",
    )
    parser.add_argument(
        "--base-url",
        default="https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link",
        help="de onde descarregar o runtime",
    )
    parser.add_argument("--name", default=None, help="nome lógico do node")
    parser.add_argument("--host", default=None, help="endereço onde escuta")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", default=None, help="directório de instalação")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    rep = Reporter(assume_yes=args.yes)
    ctx = (
        Context.create(Path(args.root))
        if args.root
        else Context.create()
    )

    if args.check:
        return run_check(ctx, rep)
    if args.uninstall:
        return run_uninstall(ctx, rep)

    mode = "update" if args.update else "install"
    return run_install(ctx, args.base_url, args.name, args.host, args.port, mode, rep)


if __name__ == "__main__":
    raise SystemExit(main())
