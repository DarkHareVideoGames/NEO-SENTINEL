"""Identidade canónica do node: quem é esta máquina.

Três campos com papéis distintos:

  node_id   identidade técnica, única e estável. Gerada uma vez com `secrets`
            e persistida pelo LINK. Não depende do hostname, não muda quando o
            hostname muda e não muda quando o `name` é alterado.

  name      nome lógico e amigável. Configurável pelo utilizador. Sem nome
            configurado, usa o hostname real da máquina.

  hostname  hostname real, sempre obtido do sistema operativo.

O IP é apenas informação de contacto — nunca identidade.
"""

from __future__ import annotations

import getpass
import os
import platform
import socket
from dataclasses import asdict, dataclass
from datetime import datetime

from . import AGENT_NAME, __version__


@dataclass(frozen=True)
class NodeIdentity:
    """Identidade canónica do node, tal como o SENTINEL a vê.

    Três campos distintos, com papéis diferentes:

    - `node_id`  identidade técnica, estável e persistente. Gerada uma vez e
                 guardada; não depende do hostname nem do `name`, e não muda
                 quando o utilizador renomeia o node.
    - `name`     nome lógico/amigável. Configurável; sem configuração usa o
                 hostname real da máquina.
    - `hostname` hostname real da máquina, sempre descoberto pelo sistema.
    """

    node_id: str
    name: str
    hostname: str
    platform: str
    agent: str
    version: str
    username: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    architecture: str | None = None
    tailscale_ip: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_platform() -> str:
    """Nome normalizado da plataforma: 'windows' | 'linux' | 'android' | ..."""
    if os.name == "nt":
        return "windows"
    if "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ:
        return "android"
    if sys_platform() == "darwin":
        return "macos"
    return "linux"


def sys_platform() -> str:
    return platform.system().lower()


def _safe_username() -> str | None:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - em contentores o getuser pode falhar
        return os.environ.get("USERNAME") or os.environ.get("USER")


def tailscale_ip() -> str | None:
    """IP da interface Tailscale, se existir. Não é identidade — é contacto."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for name, addresses in psutil.net_if_addrs().items():
            if "tailscale" not in name.lower() and "ts" != name.lower():
                continue
            for address in addresses:
                if address.family == socket.AF_INET and address.address:
                    return address.address
    except Exception:  # noqa: BLE001
        return None
    return None


def hostname() -> str:
    """Hostname real da máquina, sempre descoberto pelo sistema.

    'desconhecido' apenas em sistemas onde o hostname não é legível.
    """
    return platform.node() or "desconhecido"


def logical_name(configured: str | None) -> str:
    """Nome lógico do node.

    Usa o nome configurado; sem ele, o hostname real da máquina. Assim uma
    instalação nova mostra logo o hostname, sem exigir configuração.
    """
    if configured and configured.strip():
        return configured.strip()
    return hostname()


def build_identity(name: str | None, node_id: str) -> NodeIdentity:
    """Constrói a identidade canónica.

    `name` pode ser None: nesse caso o hostname é usado. `node_id` vem de
    fora (ver `CredentialStore.node_id()`) para ser persistente entre
    reinícios do LINK.
    """
    return NodeIdentity(
        node_id=node_id,
        name=logical_name(name),
        hostname=hostname(),
        platform=detect_platform(),
        agent=AGENT_NAME,
        version=__version__,
        username=_safe_username(),
        os_name=platform.system() or None,
        os_version=platform.release() or None,
        architecture=platform.machine() or None,
        tailscale_ip=tailscale_ip(),
    )


def uptime_seconds() -> float:
    """Tempo desde o arranque do sistema."""
    try:
        import psutil

        boot = datetime.fromtimestamp(psutil.boot_time())
        return max(0.0, (datetime.now() - boot).total_seconds())
    except Exception:  # noqa: BLE001
        return 0.0
