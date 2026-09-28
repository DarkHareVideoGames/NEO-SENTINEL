"""Recolha de informação de GPU.

Não assume que a máquina tem uma GPU específica. Tenta o `nvidia-smi`; se não
existir, devolve uma lista vazia — o SENTINEL apresenta "N/A".

Cada GPU é descrita por campos independentes. Nunca se expõe a saída bruta.
"""

from __future__ import annotations

import csv
import math
import shutil
import subprocess
from typing import Any

# Campos pedidos ao nvidia-smi, pela ordem em que são lidos no CSV.
NVIDIA_FIELDS = (
    "name",
    "utilization.gpu",
    "temperature.gpu",
    "memory.used",
    "memory.total",
)
MB_PER_GB = 1024.0

# No Windows evita piscar uma janela de consola preta a cada consulta.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _parse_optional_float(raw: str | None) -> float | None:
    """'42' -> 42.0 ; '[N/A]' / '' / texto -> None. Nunca levanta excepção."""
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.startswith("["):  # nvidia-smi usa [N/A], [Not Supported]
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def _parse_optional_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.startswith("["):
        return None
    return text


def nvidia_smi_available(executable: str = "nvidia-smi") -> bool:
    """True se o nvidia-smi existir no PATH. Não assume que exista."""
    return shutil.which(executable) is not None


def _parse_nvidia_csv(raw: str) -> list[dict[str, Any]]:
    """Converte a saída CSV do nvidia-smi (uma linha por GPU) em campos."""
    gpus: list[dict[str, Any]] = []
    for index, row in enumerate(csv.reader(raw.splitlines())):
        if not row or not any(cell.strip() for cell in row):
            continue
        fields = [cell.strip() for cell in row]
        if len(fields) < len(NVIDIA_FIELDS):
            continue
        used_mb = _parse_optional_float(fields[3])
        total_mb = _parse_optional_float(fields[4])
        vram_percent = (
            used_mb / total_mb * 100.0
            if used_mb is not None and total_mb
            else None
        )
        gpus.append(
            {
                "index": index,
                "vendor": "nvidia",
                "model": _parse_optional_text(fields[0]),
                "usage_percent": _parse_optional_float(fields[1]),
                "temperature_c": _parse_optional_float(fields[2]),
                "vram_used_gb": round(used_mb / MB_PER_GB, 1) if used_mb is not None else None,
                "vram_total_gb": round(total_mb / MB_PER_GB, 1) if total_mb is not None else None,
                "vram_percent": round(vram_percent, 1) if vram_percent is not None else None,
            }
        )
    return gpus


def collect_nvidia(executable: str = "nvidia-smi", timeout: float = 5.0) -> list[dict[str, Any]]:
    """GPUs NVIDIA via nvidia-smi. Devolve [] se não houver ou falhar."""
    if not nvidia_smi_available(executable):
        return []
    command = [
        executable,
        f"--query-gpu={','.join(NVIDIA_FIELDS)}",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return _parse_nvidia_csv(result.stdout)


def collect_gpu() -> dict[str, Any]:
    """Todas as GPUs da máquina, com o vendor identificado.

    extensible: basta adicionar outro recollector e incluí-lo na lista.
    """
    gpus = collect_nvidia()
    return {
        "gpus": gpus,
        "count": len(gpus),
        "vendor": "nvidia" if gpus else None,
    }
