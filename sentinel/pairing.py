"""Emparelhamento de um node remoto, do ponto de vista do SENTINEL.

O que o utilizador introduce é apenas o IP Tailscale, o código e o nome da
estação. As portas e o esquema HTTP são detalhes internos: este módulo
traduz um IP num endpoint, sem mostrar esse raciocínio ao utilizador.

    IP Tailscale (100.69.16.82)
            │
            ├── :8765 → LINK          (leitura de estado, já autenticada)
            └── :8766 → pairing        (troca do código por uma credencial)

O mecanismo de segurança é o que já existia e não é simplificado: código
temporário, de utilização única, com expiração e limite de tentativas, criado
pelo LINK com `secrets`. O código viaja no corpo do pedido — nunca na URL, para
não ficar em logs de servidor nem no histórico.

A credencial permanente continua a ser guardada no campo `token` do node, que
é o campo que o cliente já usava.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import Any

try:  # pragma: no cover - o nome da excepção varia entre versões do Python
    from ipaddress import AddressValueError
except ImportError:  # Python < 3.9.5
    AddressValueError = ValueError  # type: ignore[assignment,misc]

# Portas do LINK. Detalhe interno: nunca são mostradas ao utilizador.
LINK_PORT = 8765
PAIRING_PORT = 8766

# O Tailscale atribui endereços em 100.64.0.0/10 por omissão. Aceitamos
# qualquer IPv4 válido para não prender o utilizador a essa faixa, mas esta é a
# que aparece em todo o lado.
TAILSCALE_EXAMPLE = "100.69.16.82"

# Limites de um nome de estação razoável. Protege a config de lixo digitado.
MAX_NAME_LENGTH = 64


class PairingError(Exception):
    """O pairing falhou. A mensagem é segura para mostrar ao utilizador."""


class InvalidTailscaleIP(PairingError):
    """O que foi introduzido não é um IP Tailscale válido."""


@dataclass
class PairResult:
    """Resultado de um pareamento bem-sucedido."""

    credential: str
    node: dict[str, Any] = field(default_factory=dict)

    @property
    def node_id(self) -> str:
        """Identidade técnica, estável. Vem do LINK — nunca é gerada aqui."""
        return str(self.node.get("node_id") or "")

    @property
    def hostname(self) -> str:
        """Hostname real, descoberto pelo LINK."""
        return str(self.node.get("hostname") or "")

    @property
    def name(self) -> str:
        """Nome da estação escolhido pelo utilizador.

        Se o utilizador não deu nome, vale o que o LINK conhece.
        """
        return str(self.node.get("name") or "NODE")


# ---------------------------------------------------------------------------
# Validação do IP Tailscale
# ---------------------------------------------------------------------------


def validate_tailscale_ip(raw: str) -> str:
    """Valida e devolve o IP Tailscale, ou levanta InvalidTailscaleIP.

    Aceita apenas um IPv4. URLs, portas, caminhos e hostnames são recusados
    com uma mensagem que explica o formato esperado — não são convertidos
    silenciosamente num endpoint.
    """
    text = (raw or "").strip()
    if not text:
        raise InvalidTailscaleIP(invalid_ip_message())
    # Qualquer esquema, porta ou caminho é sinal de que o utilizador colou
    # uma URL onde se pede um IP.
    if any(char in text for char in ("/", ":", " ", "@", "?")):
        raise InvalidTailscaleIP(invalid_ip_message())
    try:
        return str(IPv4Address(text))
    except (ValueError, AddressValueError):
        raise InvalidTailscaleIP(invalid_ip_message()) from None


def invalid_ip_message() -> str:
    """A mensagem mostrada quando o IP não é válido."""
    return (
        "Invalid Tailscale IP.\n"
        "Enter only the Tailscale IP, for example:\n"
        f"{TAILSCALE_EXAMPLE}"
    )


# ---------------------------------------------------------------------------
# IP → endpoints
# ---------------------------------------------------------------------------


def link_endpoint(tailscale_ip: str) -> str:
    """URL da API do LINK a partir de um IP Tailscale."""
    return f"http://{validate_tailscale_ip(tailscale_ip)}:{LINK_PORT}"


def pairing_endpoint(tailscale_ip: str) -> str:
    """URL do servidor de pairing a partir de um IP Tailscale."""
    return f"http://{validate_tailscale_ip(tailscale_ip)}:{PAIRING_PORT}"


# ---------------------------------------------------------------------------
# Compatibilidade com versões anteriores
# ---------------------------------------------------------------------------


def normalise_url(url: str) -> str:
    """Aceita 'host:8765' ou 'http://host:8765' e devolve sempre com esquema.

    Mantido para configs antigas e para o `--url` de linha de comando. O fluxo
    normal de pairing já não passa por aqui.
    """
    text = (url or "").strip().rstrip("/")
    if not text:
        raise PairingError("URL vazia")
    if not text.startswith(("http://", "https://")):
        text = f"http://{text}"
    return text


def pairing_url(url: str, port: int | None = None) -> str:
    """URL do servidor de pairing a partir de uma URL completa (legacy)."""
    base = normalise_url(url)
    scheme, _, hostport = base.partition("://")
    host, sep, current = hostport.partition(":")
    if port:
        return f"{scheme}://{host}:{port}"
    if sep and current.isdigit():
        return f"{scheme}://{host}:{int(current) + 1}"
    return f"{scheme}://{hostport}:{PAIRING_PORT}"


def clean_name(raw: str | None) -> str | None:
    """Normaliza o nome da estação.

    Vazio ou só espaços devolve None — o LINK usará então o hostname, que é o
    comportamento canónico. O nome nunca é usado para gerar o `node_id`.
    """
    if raw is None:
        return None
    text = " ".join(raw.split()).strip()
    if not text:
        return None
    if len(text) > MAX_NAME_LENGTH:
        raise PairingError(
            f"o nome da estação é demasiado longo (máximo {MAX_NAME_LENGTH} caracteres)"
        )
    return text


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST JSON. O corpo nunca vai na URL."""
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
        raise PairingError(
            friendly_pairing_error(str(detail or f"HTTP {exc.code}"))
        ) from exc
    except urllib.error.URLError as exc:
        raise PairingError("Unable to reach Tailscale node") from exc
    except (TimeoutError, OSError) as exc:
        raise PairingError("Unable to reach Tailscale node") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PairingError("resposta inválida do LINK") from exc


def friendly_pairing_error(detail: str) -> str:
    """Traduz o erro do LINK para uma mensagem orientada ao utilizador.

    As mensagens do LINK são já seguras para mostrar; só normalizamos os
    casos queixas para não vazar detalhes técnicos.
    """
    lowered = (detail or "").lower()
    if "expired" in lowered or "expirado" in lowered:
        return "Pairing code rejected or expired"
    if "invalid" in lowered or "inválido" in lowered or "incorrect" in lowered:
        return "Pairing code rejected or expired"
    if "attempt" in lowered or "tentativa" in lowered:
        return "Too many attempts. Generate a new pairing code on the node."
    return detail or "Pairing refused"


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
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PairingError("Unable to reach Tailscale node") from exc
    except json.JSONDecodeError as exc:
        raise PairingError("resposta inválida") from exc


def check_pairing_open(tailscale_ip: str, timeout: float = 5.0) -> dict[str, Any]:
    """Confirma que o servidor de pairing do node está a escutar.

    É este o teste certo antes de pedir o código: durante `neo-link --pair` é
    a porta de pairing que está aberta, e pode não ser a da API principal.
    """
    endpoint = pairing_endpoint(tailscale_ip)
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise PairingError(f"o node recusou o pedido (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PairingError("Unable to reach Tailscale node") from exc
    except json.JSONDecodeError as exc:
        raise PairingError("resposta inválida do node") from exc


def pair_by_ip(
    tailscale_ip: str,
    code: str,
    name: str | None = None,
    timeout: float = 10.0,
) -> PairResult:
    """Emparelha a partir de um IP Tailscale e devolve a credencial permanente.

    O nome da estação é opcional e apenas rotula o node no SENTINEL — não
   influencia o `node_id`, que é gerado e detido pelo LINK.
    """
    ip = validate_tailscale_ip(tailscale_ip)
    if not code or not code.strip():
        raise PairingError("Pairing code is required")

    payload: dict[str, Any] = {"code": code.strip()}
    station = clean_name(name)
    if station:
        payload["name"] = station

    # O código viaja no corpo; o endpoint é interno e nunca é mostrado.
    endpoint = f"{pairing_endpoint(ip)}/pair"
    body = _post(endpoint, payload, timeout)
    if not body.get("paired"):
        raise PairingError(friendly_pairing_error(str(body.get("detail") or "")))
    credential = body.get("credential")
    if not credential:
        raise PairingError("o LINK não devolveu credencial")

    node = dict(body.get("node") or {})
    # O nome escolhido pelo utilizador prevalece sobre o do LINK, para o que
    # ele vê no ecrã coincidir com o que escreveu. A identidade técnica é a
    # do LINK e nunca é tocada aqui.
    if station:
        node["name"] = station
    return PairResult(credential=credential, node=node)


def pair(
    url: str,
    code: str,
    timeout: float = 10.0,
    port: int | None = None,
    name: str | None = None,
) -> PairResult:
    """Emparelha a partir de uma URL completa.

    Mantido para compatibilidade com a opção `--url`. O fluxo normal usa
    `pair_by_ip`.
    """
    if not code or not code.strip():
        raise PairingError("código vazio")
    endpoint = f"{pairing_url(url, port)}/pair"
    payload: dict[str, Any] = {"code": code.strip()}
    station = clean_name(name)
    if station:
        payload["name"] = station
    body = _post(endpoint, payload, timeout)
    if not body.get("paired"):
        raise PairingError(friendly_pairing_error(str(body.get("detail") or "")))
    credential = body.get("credential")
    if not credential:
        raise PairingError("o LINK não devolveu credencial")
    node = dict(body.get("node") or {})
    if station:
        node["name"] = station
    return PairResult(credential=credential, node=node)
