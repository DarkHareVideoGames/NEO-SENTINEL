"""Servidor temporário de pairing.

Serve UMA rota: POST /pair. É de curta duração (fecha ao emparelhar ou ao
expirar o código) e corre numa porta própria, para não precisar de parar o
LINK principal nem de o reiniciar.

Esta escolha mantém a superfície de ataque mínima: o servidor de pairing não
conhece hardware, gpu, discos nem serviços — só aceita um código e devolve
uma credencial.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import LinkConfig
from .identity import build_identity
from .pairing import CredentialStore, PairingError, PairingManager, new_credential

log = logging.getLogger("neolink.pairing")


class PairingServer:
    """Servidor efémero que aceita um único pareamento."""

    def __init__(self, config: LinkConfig, store: CredentialStore, host: str, port: int) -> None:
        self.config = config
        self.store = store
        self.identity = build_identity(config.name, store.node_id())
        self._host = host
        self._port = port
        self._manager = PairingManager()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.paired = False

    @property
    def manager(self) -> PairingManager:
        return self._manager

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "NEO//LINK-PAIRING"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
                # Nunca registar o código nem a credencial.
                log.debug("pairing: pedido recebido")

            def _send(self, payload: dict[str, Any], status: HTTPStatus) -> None:
                body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                # Só informa se o pairing está activo. Nunca diz qual é o código.
                self._send(
                    {
                        "agent": "NEO//LINK",
                        "pairing_open": outer.manager.active is not None,
                        "node": outer.identity.name,
                    },
                    HTTPStatus.OK,
                )

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                if length > 4096:
                    self._send({"error": "payload_too_large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                    return
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8", "replace") or "{}")
                except json.JSONDecodeError:
                    self._send({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(data, dict):
                    self._send({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
                    return

                # A chave não interessa — é o par LINK que autoriza, não o corpo.
                key = data.get("code") or data.get("key") or ""

                try:
                    outer.manager.validate(str(key))
                except PairingError as exc:
                    self._send({"error": "pairing_failed", "detail": str(exc)},
                               HTTPStatus(exc.status))
                    return

                credential = new_credential()
                outer.store.add(credential, outer.identity.node_id)
                outer.paired = True

                # O nome da estação é apenas um rótulo. A identidade técnica
                # continua a ser a do node_id, gerada aqui e persistente.
                station = str(data.get("name") or "").strip()[:64]
                node = outer.identity.to_dict()
                if station:
                    node["name"] = station

                # A credencial vai no corpo da resposta — nunca na URL.
                self._send(
                    {
                        "paired": True,
                        "credential": credential,
                        "node": node,
                        "access": "monitor",
                    },
                    HTTPStatus.OK,
                )

        return Handler

    def start(self) -> PairingCodeDisplay:
        self._httpd = ThreadingHTTPServer(
            (self._host, self._port), self._handler()
        )
        self._httpd.daemon_threads = True
        # Serve numa thread: o wait() seguinte precisa de responder a pedidos.
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        # Gera o código imediatamente: o operador passa-o ao SENTINEL.
        display = self._begin_pairing()
        log.info("pairing disponível em http://%s:%d/pair", self._host, self._port)
        return display

    def _begin_pairing(self) -> PairingCodeDisplay:
        code = self.manager.generate()
        log.info("código de pairing gerado (expira em %ds)", code.seconds_left())
        # Mostramos com o hífen; internamente é guardado normalizado.
        return PairingCodeDisplay(code.display_code(), code.seconds_left())

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def wait(self, timeout: float) -> bool:
        """Espera até emparelhar ou expirar. Devolve True se emparelhou."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.paired:
                return True
            if self.manager.active is None:
                return False
            time.sleep(0.2)
        return self.paired


@dataclass
class PairingCodeDisplay:
    """Code + tempo restante, para o CLI mostrar."""

    code: str
    seconds_left: int
