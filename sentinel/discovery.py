"""Descoberta de nodes.

Estratégia deliberada: NÃO há port scanning nem brute force. O SENTINEL só
consulta nodes que conhece (config.json) ou que o Tailscale lhe reporta.

`StaticDiscovery`   — nodes declarados na configuração.
`TailscaleDiscovery` — nós da tailnet lidos da CLI do Tailscale, quando
                       disponível. Só o registo deKNOWN peers; nunca sondagem.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .config import NodeConfig

# Porta por omissão do LINK. Só é usada para construir o URL de um peer já
# conhecido — nunca para tentarligar a endereços desconhecidos.
DEFAULT_LINK_PORT = 8765


@dataclass
class DiscoveredNode:
    """Node encontrado, ainda por confirmar com /status."""

    name: str
    url: str
    source: str
    online: bool = False

    def to_node_config(self) -> NodeConfig:
        return NodeConfig(name=self.name, url=self.url)


class NodeDiscovery(ABC):
    """Camada de descoberta. Para implementar mais fontes, herdar daqui."""

    @abstractmethod
    def discover(self) -> list[DiscoveredNode]:
        """Devolve os nodes encontrados. Nunca levanta excepções."""


class StaticDiscovery(NodeDiscovery):
    """Nodes declarados em config.json. É a fonte por omissão."""

    def __init__(self, nodes: list[NodeConfig]) -> None:
        self._nodes = nodes

    def discover(self) -> list[DiscoveredNode]:
        return [
            DiscoveredNode(name=node.name, url=node.url, source="config")
            for node in self._nodes
            if node.enabled
        ]


class TailscaleDiscovery(NodeDiscovery):
    """Nodes da tailnet, lidos de `tailscale status --json`.

    Usa o mecanismo nativo do Tailscale (a lista de peers que a própria
    Tailnet já conhece). Não faz scanning de portas nem tenta adivinhar
    máquinas fora da tailnet.
    """

    def __init__(self, port: int = DEFAULT_LINK_PORT) -> None:
        self._port = port

    def discover(self) -> list[DiscoveredNode]:
        executable = shutil.which("tailscale")
        if executable is None:
            return []
        try:
            result = subprocess.run(
                [executable, "status", "--json"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        return self._parse(payload)

    def _parse(self, payload: dict[str, Any]) -> list[DiscoveredNode]:
        """Converte o estado do Tailscale em nodes candidatos.

        Só entram peers ligados à tailnet. O nome é o MagicDNS (o hostname),
        nunca o IP, para não depender de endereços fixos.

        `Peer` chega como dict (chave=nodekey) em algumas versões e como lista
        noutras — ambos são aceites.
        """
        found: list[DiscoveredNode] = []
        self_node = (payload.get("Self") or {}).get("HostName")
        for peer in self._iter_peers(payload.get("Peer")):
            if not isinstance(peer, dict) or not peer.get("Online"):
                continue
            hostname = peer.get("HostName") or peer.get("DNSName")
            if not hostname or hostname == self_node:
                continue
            # MagicDNS resolve pelo hostname, por isso o IP pode mudar.
            found.append(
                DiscoveredNode(
                    name=hostname,
                    url=f"http://{hostname}:{self._port}",
                    source="tailscale",
                )
            )
        return found

    @staticmethod
    def _iter_peers(peers: Any) -> list[Any]:
        """Normaliza `Peer` para uma lista, aceite dict ou lista."""
        if isinstance(peers, dict):
            return list(peers.values())
        if isinstance(peers, list):
            return peers
        return []


class CompositeDiscovery(NodeDiscovery):
    """Une várias fontes, sem duplicar por nome."""

    def __init__(self, sources: list[NodeDiscovery]) -> None:
        self._sources = sources

    def discover(self) -> list[DiscoveredNode]:
        merged: dict[str, DiscoveredNode] = {}
        for source in self._sources:
            for node in source.discover():
                # A config tem prioridade sobre a descoberta automática.
                merged.setdefault(node.name.lower(), node)
        return list(merged.values())
