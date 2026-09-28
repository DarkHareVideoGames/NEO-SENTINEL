"""Carregamento e validação da configuração do LINK.

Toda a informação específica da máquina (paths, portas, comandos) vive no
config.json. O código nunca embute estes valores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# Tipos de serviço suportados. Adicionar um tipo novo = registar aqui + uma classe.
SERVICE_TYPES = ("comfyui", "ollama", "process")


@dataclass
class ServerConfig:
    """Onde e como o LINK escuta."""

    host: str = "127.0.0.1"
    port: int = 8765
    # Controlo (start/stop/restart) desligado por omissão: exige acção explícita.
    allow_control: bool = False
    # Token partilhado com o SENTINEL. Se definido, é exigido em todos os pedidos.
    token: str | None = None

    @property
    def bind_all_interfaces(self) -> bool:
        return self.host in ("0.0.0.0", "::")


@dataclass
class ServiceConfig:
    """Definição declarativa de um serviço. Genérica: não assume ComfyUI."""

    type: str
    name: str
    enabled: bool = True
    port: int | None = None
    path: str | None = None
    python: str | None = None
    args: list[str] = field(default_factory=list)
    api_path: str | None = None
    process_match: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ServiceConfig":
        known = set(cls.__dataclass_fields__)
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"serviço {raw.get('name', '?')!r}: chaves desconhecidas {sorted(unknown)}"
            )
        if raw.get("type") not in SERVICE_TYPES:
            raise ValueError(
                f"serviço {raw.get('name', '?')!r}: type {raw.get('type')!r} inválido "
                f"(esperado um de {SERVICE_TYPES})"
            )
        return cls(
            type=raw["type"],
            name=raw["name"],
            enabled=bool(raw.get("enabled", True)),
            port=raw.get("port"),
            path=raw.get("path"),
            python=raw.get("python"),
            args=list(raw.get("args", [])),
            api_path=raw.get("api_path"),
            process_match=list(raw.get("process_match", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "enabled": self.enabled,
            "port": self.port,
            "path": self.path,
            "python": self.python,
            "args": self.args,
            "api_path": self.api_path,
            "process_match": self.process_match,
        }


@dataclass
class LinkConfig:
    """Configuração completa do LINK."""

    name: str | None
    server: ServerConfig
    services: list[ServiceConfig]

    @classmethod
    def load(cls, path: Path | str | None = None) -> "LinkConfig":
        # None = usar o config.json ao lado do executável.
        config_path = Path(path) if path else DEFAULT_CONFIG_PATH
        if not config_path.is_file():
            raise FileNotFoundError(
                f"config.json não encontrado em {config_path}. "
                "Copie o exemplo e ajuste 'name' e os serviços."
            )
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LinkConfig":
        node = raw.get("node") or {}
        # O `name` é opcional: sem ele, o LINK usa o hostname real da máquina.
        name = str(node.get("name") or "").strip() or None

        server_raw = raw.get("server") or {}
        server = ServerConfig(
            host=server_raw.get("host", "127.0.0.1"),
            port=int(server_raw.get("port", 8765)),
            allow_control=bool(server_raw.get("allow_control", False)),
            token=server_raw.get("token") or None,
        )

        services = [ServiceConfig.from_dict(item) for item in raw.get("services", [])]
        seen: set[str] = set()
        for service in services:
            if service.name.lower() in seen:
                raise ValueError(f"nome de serviço duplicado: {service.name!r}")
            seen.add(service.name.lower())

        return cls(name=name, server=server, services=services)
