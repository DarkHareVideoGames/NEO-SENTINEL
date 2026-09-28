"""Servidor HTTP do LINK — a interface que o SENTINEL consulta.

Protocolo (JSON sobre HTTP):

    GET  /                      -> descrição das rotas
    GET  /status                -> identidade + resumo de serviços
    GET  /hardware              -> cpu, ram, disks, network, uptime
    GET  /gpu                   -> lista de GPUs
    GET  /disks                 -> lista de volumes
    GET  /services              -> estado de todos os serviços
    GET  /services/<nome>       -> estado de um serviço
    POST /services/<nome>/start | /stop | /restart   (se allow_control)

O LINK não expõe paths nem comandos: o SENTINEL recebe apenas o estado.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from . import AGENT_NAME, __version__
from .collectors import collect_cpu, collect_disks, collect_network, collect_ram
from .config import LinkConfig
from .gpu import collect_gpu
from .identity import build_identity, uptime_seconds
from .pairing import CredentialStore
from .services import Service, collect_services

log = logging.getLogger("neolink.server")

SERVICE_ACTION_RE = re.compile(r"^/services/(?P<name>[^/]+)/(?P<action>start|stop|restart)$")
SERVICE_ONE_RE = re.compile(r"^/services/(?P<name>[^/]+)$")


class LinkService:
    """Agrega a recolha de dados. Independente do transporte HTTP."""

    def __init__(
        self,
        config: LinkConfig,
        services: list[Service],
        store: "CredentialStore | None" = None,
    ) -> None:
        self.config = config
        self.services = services
        self.store = store
        # A identidade canónica: node_id persistente + name + hostname.
        node_id = store.node_id() if store is not None else ""
        self.identity = build_identity(config.name, node_id)

    def find_service(self, name: str) -> Service | None:
        """Serviço por nome (case-insensitive)."""
        wanted = name.strip().lower()
        for service in self.services:
            if service.name.lower() == wanted:
                return service
        return None

    # -- payloads -----------------------------------------------------------

    def payload_status(self) -> dict[str, Any]:
        return {
            "agent": AGENT_NAME,
            "version": __version__,
            "node": self.identity.to_dict(),
            "uptime_seconds": round(uptime_seconds(), 1),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "control_enabled": self.config.server.allow_control,
            "paired_clients": self.store.count if self.store else 0,
            "services": [
                {"name": item["name"], "state": item["state"]}
                for item in collect_services(self.services)
            ],
        }

    def payload_hardware(self) -> dict[str, Any]:
        return {
            "node": self.identity.to_dict(),
            "cpu": collect_cpu(),
            "ram": collect_ram(),
            "disks": collect_disks(),
            "network": collect_network(),
            "uptime_seconds": round(uptime_seconds(), 1),
        }

    def payload_gpu(self) -> dict[str, Any]:
        return collect_gpu()

    def payload_disks(self) -> dict[str, Any]:
        return {"disks": collect_disks()}

    def payload_services(self) -> dict[str, Any]:
        return {"services": collect_services(self.services)}


ROUTES: dict[str, Callable[[LinkService], dict[str, Any]]] = {
    "/status": LinkService.payload_status,
    "/hardware": LinkService.payload_hardware,
    "/gpu": LinkService.payload_gpu,
    "/disks": LinkService.payload_disks,
    "/services": LinkService.payload_services,
}


def make_handler(link: LinkService) -> type[BaseHTTPRequestHandler]:
    """Cria a classe de handler fechada sobre uma instância de LinkService."""

    class Handler(BaseHTTPRequestHandler):
        server_version = f"{AGENT_NAME}/{__version__}"
        protocol_version = "HTTP/1.1"

        # -- utilitários ---------------------------------------------------

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            """Aceita credencial de pairing OU token partilhado do config.

            Ambos dão exatamente o mesmo acesso: leitura da monitorização.
            Nenhum dos dois concede controlo — isso é decidido exclusivamente
            por `allow_control` no config.json.
            """
            supplied = self.headers.get("X-NEO-Token", "")
            token = link.config.server.token
            has_credentials = link.store is not None and link.store.count > 0

            # 1) Credencial obtida por pairing (caminho preferido).
            if link.store is not None and supplied and link.store.verify(supplied):
                return True

            # 2) Token partilhado do config.json (compatibilidade).
            if token and hmac.compare_digest(supplied, token):
                return True

            # 3) Sem token e sem credenciais: acesso livre (modo local).
            return not token and not has_credentials

        def _require_auth(self) -> bool:
            if self._authorised():
                return True
            self._send_json(
                {"error": "unauthorized", "detail": "credencial inválida ou em falta"},
                HTTPStatus.UNAUTHORIZED,
            )
            return False

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.debug("%s - %s", self.address_string(), format % args)

        # -- GET ------------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            path = urlparse(self.path).path.rstrip("/") or "/"

            if path == "/":
                self._send_json(
                    {
                        "agent": AGENT_NAME,
                        "version": __version__,
                        "node": link.identity.to_dict(),
                        "routes": sorted([*ROUTES, "/services/{name}", "/services/{name}/{action}"]),
                    }
                )
                return

            if path in ROUTES:
                self._send_json(ROUTES[path](link))
                return

            match = SERVICE_ONE_RE.match(path)
            if match:
                service = link.find_service(unquote(match.group("name")))
                if service is None:
                    self._send_json(
                        {"error": "unknown_service", "name": match.group("name")},
                        HTTPStatus.NOT_FOUND,
                    )
                    return
                try:
                    self._send_json({"service": service.probe().to_dict()})
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        {"error": "probe_failed", "detail": str(exc)},
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                return

            self._send_json({"error": "not_found", "path": path}, HTTPStatus.NOT_FOUND)

        # -- POST -----------------------------------------------------------

        def do_POST(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            path = urlparse(self.path).path.rstrip("/") or "/"

            if not link.config.server.allow_control:
                self._send_json(
                    {
                        "error": "control_disabled",
                        "detail": "active 'allow_control' no config.json do LINK para permitir controlo",
                    },
                    HTTPStatus.FORBIDDEN,
                )
                return

            match = SERVICE_ACTION_RE.match(path)
            if not match:
                self._send_json(
                    {"error": "not_found", "path": path},
                    HTTPStatus.NOT_FOUND,
                )
                return

            service = link.find_service(unquote(match.group("name")))
            if service is None:
                self._send_json(
                    {"error": "unknown_service", "name": match.group("name")},
                    HTTPStatus.NOT_FOUND,
                )
                return

            action = match.group("action")
            try:
                ok, message = getattr(service, action)()
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    {"error": "action_failed", "detail": str(exc)},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._send_json(
                {"service": service.name, "action": action, "ok": ok, "message": message},
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
            )

    return Handler


class LinkServer:
    """Servidor HTTP do LINK."""

    def __init__(
        self,
        config: LinkConfig,
        services: list[Service],
        store: CredentialStore | None = None,
    ) -> None:
        self.link = LinkService(config, services, store)
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self.link.config.server.port
        return self._httpd.server_address[1]

    def serve_forever(self) -> None:
        server_config = self.link.config.server
        self._httpd = ThreadingHTTPServer(
            (server_config.host, server_config.port),
            make_handler(self.link),
        )
        host = server_config.host
        log.info("%s %s a servir em http://%s:%d", AGENT_NAME, __version__, host, self.port)
        if server_config.bind_all_interfaces:
            log.warning(
                "Escutando em %s — o LINK fica acessível em todas as interfaces. "
                "Confie apenas na Tailnet e defina 'token'.",
                host,
            )
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            log.info("interrompido pelo utilizador")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
