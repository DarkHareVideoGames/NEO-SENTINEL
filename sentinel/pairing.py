"""Pairing de um node remoto, do ponto de vista do SENTINEL.

Fluxo:
    LINK gera um código  ->  o utilizador introduz-o aqui  ->  o LINK valida
    ->  devolve uma credencial  ->  o SENTINEL guarda-a na config existente.

A credencial é guardada no campo `token` do node, que é o campo que o cliente
já usava — por isso, nada muda no resto do sistema.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class PairingError(Exception):
    """O pairing falhou. A mensagem é segura para mostrar ao utilizador."""


@dataclass
class PairResult:
    """Resultado de um pareamento bem-sucedido."""

    credential: str
    node: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.node.get("name") or "NODE")


def normalise_url(url: str) -> str:
    """Aceita 'host:8765' ou 'http://host:8765' e devolve sempre com esquema."""
    text = (url or "").strip().rstrip("/")
    if not text:
        raise PairingError("URL vazia")
    if not text.startswith(("http://", "https://")):
        text = f"http://{text}"
    return text


def pairing_url(url: str, port: int | None = None) -> str:
    """URL do servidor de pairing (a principal + 1, por omissão).

    Deriva a porta do URL dado, em vez de a fixar: se o LINK estiver numa porta
    não-padrão, o SENTINEL acompanha.
    """
    base = normalise_url(url)
    scheme, _, hostport = base.partition("://")
    host, sep, current = hostport.partition(":")
    if port:
        return f"{scheme}://{host}:{port}"
    if sep and current.isdigit():
        return f"{scheme}://{host}:{int(current) + 1}"
    return f"{scheme}://{hostport}:8766"


def _post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # O LINK explica o erro; mostramos a mensagem dele.
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")).get("detail")
        except Exception:  # noqa: BLE001
            detail = None
        raise PairingError(detail or f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise PairingError(f"não foi possível alcançar {url} ({exc.reason})") from exc
    except (TimeoutError, OSError) as exc:
        raise PairingError(str(exc)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PairingError("resposta inválida do LINK") from exc


def check_reachable(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """Confirma que o LINK responde, sem pairing. Usado no --pair."""
    target = normalise_url(url)
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise PairingError("o LINK pede credencial — emparelhe primeiro") from exc
        raise PairingError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise PairingError(f"não foi possível alcançar {target} ({exc.reason})") from exc
    except (TimeoutError, OSError) as exc:
        raise PairingError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise PairingError("resposta inválida") from exc


def pair(url: str, code: str, timeout: float = 10.0, port: int | None = None) -> PairResult:
    """Emparelha com o LINK e devolve a credencial permanente.

    O código viaja no corpo do pedido (nunca na URL, para não ficar em logs
    de servidor ou no histórico do browser).
    """
    if not code or not code.strip():
        raise PairingError("código vazio")
    endpoint = f"{pairing_url(url, port)}/pair"
    payload = _post(endpoint, {"code": code.strip()}, timeout)
    if not payload.get("paired"):
        raise PairingError(str(payload.get("detail") or "pairing recusado"))
    credential = payload.get("credential")
    if not credential:
        raise PairingError("o LINK não devolveu credencial")
    return PairResult(credential=credential, node=payload.get("node") or {})
