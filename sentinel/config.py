"""Configuração do SENTINEL.

O SENTINEL conhece apenas: nome do node e endpoint. Nunca paths, processos
nem comandos — isso é responsabilidade do LINK de cada máquina.

Um node guarda a identidade canónica que o LINK lhe devolveu no pairing:

    {
      "node_id": "nx1-...",     # identidade técnica, estável
      "name": "...",            # nome escolhido pelo utilizador
      "hostname": "...",        # hostname real, descoberto pelo LINK
      "tailscale_ip": "100...." # como voltar a falar com o node
    }

O `url` continua a ser guardado, para compatibilidade com configurações
antigas e com o resto do código. É derivado do IP, mas continua a ser um
campo válido — nenhuma configuração existente se perde.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class NodeConfig:
    """Um node conhecido. Só identidade e endpoint."""

    name: str
    url: str
    token: str | None = None
    enabled: bool = True
    # Identidade canónica, devolvida pelo LINK no pairing. Opcional para
    #configs escritas antes de a identidade existir.
    node_id: str | None = None
    hostname: str | None = None
    tailscale_ip: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NodeConfig":
        name = str(raw.get("name") or "").strip()
        url = str(raw.get("url") or "").strip().rstrip("/")
        if not name:
            raise ValueError("cada node precisa de 'name'")
        if not url:
            # Config antigo sem 'url' mas com IP: derivamos o endpoint.
            tailscale_ip = str(raw.get("tailscale_ip") or "").strip()
            if tailscale_ip:
                from .pairing import link_endpoint

                url = link_endpoint(tailscale_ip)
            else:
                raise ValueError(
                    f"node {name!r} precisa de 'url' (ex.: http://host:8765)"
                )
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"node {name!r}: url deve começar por http:// ou https://")
        return cls(
            name=name,
            url=url,
            token=raw.get("token") or None,
            enabled=bool(raw.get("enabled", True)),
            node_id=raw.get("node_id") or None,
            hostname=raw.get("hostname") or None,
            tailscale_ip=raw.get("tailscale_ip") or None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializa o node.

        A `url` continua presente — é o que o resto do sistema consome. Os
        campos da identidade só são escritos quando existem, para não sujar
        as configs.
        """
        data: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
            "token": self.token,
            "enabled": self.enabled,
        }
        if self.node_id:
            data["node_id"] = self.node_id
        if self.hostname:
            data["hostname"] = self.hostname
        if self.tailscale_ip:
            data["tailscale_ip"] = self.tailscale_ip
        return data


@dataclass
class SentinelConfig:
    """Configuração do SENTINEL."""

    nodes: list[NodeConfig]
    refresh_seconds: float = 2.0
    timeout_seconds: float = 5.0

    @classmethod
    def load(cls, path: Path | str | None = None) -> "SentinelConfig":
        config_path = Path(path) if path else DEFAULT_CONFIG_PATH
        if not config_path.is_file():
            # Configuração vazia é válida: o utilizador pode adicionar nodes depois.
            return cls(nodes=[])
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        nodes = [NodeConfig.from_dict(item) for item in raw.get("nodes", [])]
        seen: set[str] = set()
        for node in nodes:
            if node.name.lower() in seen:
                raise ValueError(f"nome de node duplicado: {node.name!r}")
            seen.add(node.name.lower())
        return cls(
            nodes=nodes,
            refresh_seconds=float(raw.get("refresh_seconds", 2.0)),
            timeout_seconds=float(raw.get("timeout_seconds", 5.0)),
        )

    def save(self, path: Path | str | None = None) -> Path:
        """Grava a configuração, preservando refresh/timeout já existentes.

        Só os nodes são reescritos — nada mais no ficheiro é tocado.
        """
        config_path = Path(path) if path else DEFAULT_CONFIG_PATH
        payload = {
            "refresh_seconds": self.refresh_seconds,
            "timeout_seconds": self.timeout_seconds,
            "nodes": [node.to_dict() for node in self.nodes],
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:  # só o dono lê a configuração (contém credenciais)
            config_path.chmod(0o600)
        except OSError:  # pragma: no cover - depende do sistema de ficheiros
            pass
        return config_path

    def upsert_node(self, node: NodeConfig) -> None:
        """Adiciona ou substitui um node.

        Preferimos a identidade técnica (`node_id`) quando existe, para
        renomear uma estação não criar um node novo. O nome serve apenas de
        recurso para nodes antigos, que ainda não têm `node_id`.
        """
        for index, existing in enumerate(self.nodes):
            if existing.node_id and node.node_id:
                if existing.node_id == node.node_id:
                    # Mesmo node: a credencial e o IP são actualizados, o
                    # node_id permanece igual e o nome pode mudar.
                    self.nodes[index] = node
                    return
            elif existing.name.lower() == node.name.lower():
                self.nodes[index] = node
                return
        self.nodes.append(node)
