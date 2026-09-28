"""Cliente HTTP para falar com um NEO//LINK.

Usa apenas a biblioteca standard (urllib) — sem requests, sem psutil, sem
nada específico do Windows. É o que permite correr no Termux/Android.

O cliente nunca executa nada localmente: apenas pede e apresenta. Todo o
controlo é delegado ao LINK da máquina remota.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import NodeConfig


class LinkError(Exception):
    """Erro ao comunicar com um LINK."""


@dataclass
class NodeSnapshot:
    """Tudo o que o SENTINEL sabe sobre um node num instante.

    Construído a partir das respostas do LINK. Campos em falta = None, e a UI
    mostra N/A. O SENTINEL não adivinha nem completa informação.
    """

    name: str
    online: bool = False
    error: str | None = None

    # Identidade canónica, fornecida pelo LINK. O SENTINEL não a descobre
    # remotamente de nenhuma outra forma.
    node_id: str | None = None
    hostname: str | None = None
    platform: str | None = None
    agent: str | None = None
    agent_version: str | None = None

    cpu_percent: float | None = None
    cpu_cores: int | None = None
    ram_percent: float | None = None
    ram_total_gb: float | None = None
    ram_used_gb: float | None = None

    gpus: list[dict[str, Any]] = field(default_factory=list)
    disks: list[dict[str, Any]] = field(default_factory=list)
    services: list[dict[str, Any]] = field(default_factory=list)

    uptime_seconds: float | None = None
    tailscale_ip: str | None = None

    @property
    def gpus_count(self) -> int:
        return len(self.gpus)

    @property
    def disks_count(self) -> int:
        return len(self.disks)


class LinkClient:
    """Cliente de um node. Uma instância por node."""

    def __init__(self, node: NodeConfig, timeout: float = 5.0) -> None:
        self.node = node
        self.timeout = timeout

    # -- transporte ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.node.token:
            headers["X-NEO-Token"] = self.node.token
        return headers

    def _request(self, path: str, method: str = "GET") -> dict[str, Any]:
        url = f"{self.node.url}{path}"
        request = urllib.request.Request(url, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise LinkError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise LinkError(str(exc.reason)) from exc
        except (TimeoutError, OSError) as exc:
            raise LinkError(str(exc)) from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LinkError("resposta não é JSON válido") from exc

    # -- endpoints de leitura ------------------------------------------------

    def status(self) -> dict[str, Any]:
        return self._request("/status")

    def hardware(self) -> dict[str, Any]:
        return self._request("/hardware")

    def gpu(self) -> dict[str, Any]:
        return self._request("/gpu")

    def disks(self) -> dict[str, Any]:
        return self._request("/disks")

    def services(self) -> dict[str, Any]:
        return self._request("/services")

    # -- controlo (delegado ao LINK) -----------------------------------------

    def service_action(self, name: str, action: str) -> dict[str, Any]:
        """Pede ao LINK para arrancar/parar/reiniciar um serviço.

        O SENTINEL nunca executa o comando: quem o executa é sempre o LINK
        da máquina, que conhece os paths e comandos correctos.
        """
        return self._request(f"/services/{name}/{action}", method="POST")

    # -- agregação -----------------------------------------------------------

    def poll(self) -> NodeSnapshot:
        """Consulta o node e devolve um snapshot consolidado.

        Uma falha parcial não invalida o resto: se /gpu falhar, mostramos N/A
        nessa secção mas o resto do node continua visível.
        """
        snapshot = NodeSnapshot(name=self.node.name)
        errors: list[str] = []

        try:
            status = self.status()
        except LinkError as exc:
            snapshot.online = False
            snapshot.error = str(exc)
            return snapshot

        snapshot.online = True
        node = status.get("node") or {}
        # Identidade canónica: o LINK é a fonte da verdade.
        snapshot.node_id = node.get("node_id")
        snapshot.name = str(node.get("name") or snapshot.name)
        snapshot.hostname = node.get("hostname")
        snapshot.platform = node.get("platform")
        snapshot.agent = status.get("agent") or node.get("agent")
        snapshot.agent_version = status.get("version") or node.get("version")
        snapshot.tailscale_ip = node.get("tailscale_ip")
        snapshot.uptime_seconds = status.get("uptime_seconds")

        # /hardware traz cpu + ram + disks de uma vez.
        try:
            hardware = self.hardware()
            cpu = hardware.get("cpu") or {}
            ram = hardware.get("ram") or {}
            snapshot.cpu_percent = cpu.get("percent")
            snapshot.cpu_cores = cpu.get("logical_cores")
            snapshot.ram_percent = ram.get("percent")
            snapshot.ram_total_gb = ram.get("total_gb")
            snapshot.ram_used_gb = ram.get("used_gb")
            snapshot.disks = hardware.get("disks") or []
        except LinkError as exc:
            errors.append(f"hardware: {exc}")

        try:
            snapshot.gpus = self.gpu().get("gpus") or []
        except LinkError as exc:
            errors.append(f"gpu: {exc}")

        try:
            snapshot.services = self.services().get("services") or []
        except LinkError as exc:
            errors.append(f"services: {exc}")

        if errors:
            snapshot.error = "; ".join(errors)
        return snapshot
