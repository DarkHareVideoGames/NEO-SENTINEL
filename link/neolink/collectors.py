"""Recolha de informação do sistema: CPU, RAM, discos e rede.

Usa psutil, que abstrai Windows/Linux/Android. Nenhum valor é hardcoded.
"""

from __future__ import annotations

import re
import socket
from typing import Any

import psutil

BYTES_PER_GB = 1024.0**3
_WINDOWS_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/]?$")


def _safe(func, default):
    """Executa uma recolha devolvendo `default` em caso de falha.

    Uma métrica indisponível nunca pode derrubar o LINK inteiro.
    """
    try:
        return func()
    except Exception:  # noqa: BLE001
        return default


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

# Estado do psutil para a utilização instantânea de CPU (devolve 0.0 na 1.ª vez).
_cpu_primed = False


def collect_cpu() -> dict[str, Any]:
    """Utilização de CPU. Faz um aquecimento inicial para não reportar 0.0."""
    global _cpu_primed

    def measure() -> float:
        global _cpu_primed
        if _cpu_primed:
            return psutil.cpu_percent(interval=None)
        _cpu_primed = True
        return psutil.cpu_percent(interval=0.3)

    freq = _safe(lambda: round(psutil.cpu_freq().current, 1) if psutil.cpu_freq() else None, None)
    return {
        "percent": _safe(measure, None),
        "logical_cores": _safe(psutil.cpu_count, None),
        "physical_cores": _safe(psutil.cpu_count, None),  # psutil só expõe lógico aqui
        "max_frequency_mhz": freq,
        "load_average": _safe(lambda: list(psutil.getloadavg()) or None, None),
    }


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------


def collect_ram() -> dict[str, Any]:
    """Utilização de memória RAM."""
    mem = _safe(psutil.virtual_memory, None)
    if mem is None:
        return {
            "percent": None,
            "total_gb": None,
            "used_gb": None,
            "available_gb": None,
        }
    swap = _safe(psutil.swap_memory, None)
    return {
        "percent": mem.percent,
        "total_gb": round(mem.total / BYTES_PER_GB, 1),
        "used_gb": round(mem.used / BYTES_PER_GB, 1),
        "available_gb": round(mem.available / BYTES_PER_GB, 1),
        "swap_percent": swap.percent if swap else None,
    }


# ---------------------------------------------------------------------------
# Discos
# ---------------------------------------------------------------------------


def _is_relevant(partition: Any) -> bool:
    """Ignora cdrom, removíveis e pseudo-sistemas de ficheiros."""
    opts = (getattr(partition, "opts", "") or "").lower()
    return "cdrom" not in opts and "removable" not in opts


def _format_device(partition: Any) -> str:
    """Normaliza o identificador do volume: 'C:\\' -> 'C:'."""
    device = (getattr(partition, "device", "") or "").strip()
    match = _WINDOWS_DRIVE_RE.match(device)
    if match:
        return f"{match.group(1).upper()}:"
    return device or (getattr(partition, "mountpoint", "") or "?")


def collect_disks() -> list[dict[str, Any]]:
    """Cada volume relevante, individualmente."""
    disks: list[dict[str, Any]] = []
    for partition in _safe(psutil.disk_partitions, []) or []:
        if not _is_relevant(partition):
            continue
        usage = _safe(lambda: psutil.disk_usage(partition.mountpoint), None)
        if usage is None or usage.total <= 0:
            continue
        disks.append(
            {
                "device": _format_device(partition),
                "mountpoint": partition.mountpoint,
                "percent": usage.percent,
                "total_gb": round(usage.total / BYTES_PER_GB, 1),
                "used_gb": round(usage.used / BYTES_PER_GB, 1),
                "free_gb": round(usage.free / BYTES_PER_GB, 1),
            }
        )
    return sorted(disks, key=lambda disk: disk["device"].upper())


# ---------------------------------------------------------------------------
# Rede
# ---------------------------------------------------------------------------


def collect_network() -> dict[str, Any]:
    """Interfaces de rede. Não faz scanning: apenas descreve o que já existe."""
    addresses = _safe(psutil.net_if_addrs, {}) or {}
    interfaces: list[dict[str, Any]] = []
    for name, entries in addresses.items():
        ipv4 = [a.address for a in entries if a.family == socket.AF_INET and a.address]
        if not ipv4:
            continue
        interfaces.append(
            {
                "name": name,
                "ipv4": ipv4,
                "is_loopback": ipv4[0].startswith("127."),
                "is_tailscale": "tailscale" in name.lower(),
            }
        )
    return {
        "hostname": socket.gethostname(),
        "interfaces": interfaces,
        # IP primário apenas como informação de contacto (não é identidade).
        "primary_ipv4": _safe(
            lambda: socket.gethostbyname(socket.gethostname()),
            None,
        ),
    }
