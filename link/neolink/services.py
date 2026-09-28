"""Serviços: o LINK conhece e executa, o SENTINEL apenas observa.

Cada serviço sabe, na sua máquina, como existe: path, processo, porta, API e
comandos. Esta é a única camada com conhecimento específico do ambiente.

Adicionar um serviço novo = uma subclasse de `Service` + registo em `build_services`.
Não é preciso alterar o servidor, o protocolo nem o SENTINEL.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

import psutil

from .config import ServiceConfig

# Flags obrigatórias do ComfyUI quando exposto na rede.
# --enable-manager : mantém o gestor de modelos disponível
# --listen 0.0.0.0 : permite aceder via interface Tailscale
COMFYUI_REQUIRED_ARGS = ("--enable-manager", "--listen", "0.0.0.0")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# Helpers de rede/processo
# ---------------------------------------------------------------------------


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """True se algo escuta na porta. Verifica localhost apenas (nunca a rede)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_probe(url: str, timeout: float = 2.0, max_bytes: int = 4096) -> tuple[bool, str | None]:
    """Faz um GET e devolve (online, corpo). Erros não levantam excepção.

    O corpo só é truncado no fim (nunca a meio de um JSON) para que quem o
    processa downstream consiga fazer parse.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(max_bytes).decode("utf-8", errors="replace")
            return 200 <= response.status < 300, body
    except urllib.error.HTTPError as exc:
        # O servidor respondeu — está online, mesmo com código de erro.
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def find_process(match_tokens: list[str]) -> psutil.Process | None:
    """Procura um processo cujo cmdline contenha todos os tokens dados.

    O match é feito sobre a linha de comando real, não sobre o nome.
    """
    if not match_tokens:
        return None
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(process.info.get("cmdline") or [])
            if all(token.lower() in cmdline.lower() for token in match_tokens):
                return process
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


def process_on_port(port: int) -> psutil.Process | None:
    """Processo que escuta na porta local, se for possível determinar."""
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError, OSError):
        return None
    for connection in connections:
        if connection.laddr and connection.laddr.port == port and connection.pid:
            try:
                return psutil.Process(connection.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None
    return None


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


@dataclass
class ServiceStatus:
    """Estado de um serviço, tal como o SENTINEL o recebe.

    O SENTINEL nunca vê paths nem comandos: só este resultado.
    """

    name: str
    type: str
    enabled: bool
    state: str
    pid: int | None = None
    port: int | None = None
    api: str | None = None
    api_online: bool | None = None
    detail: str | None = None
    controllable: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Estados possíveis
RUNNING_API_ONLINE = "RUNNING / API ONLINE"
RUNNING_API_OFFLINE = "RUNNING / API OFFLINE"
STOPPED = "STOPPED"
UNKNOWN = "UNKNOWN"
DISABLED = "DISABLED"


# ---------------------------------------------------------------------------
# Serviço base
# ---------------------------------------------------------------------------


class Service:
    """Base genérica de um serviço. Subclasses implementam `probe` e `start`."""

    #: Tipo declarado no config.json e aceite pelo registo.
    type_name: ClassVar[str] = "process"

    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.name = config.name

    # -- leitura ------------------------------------------------------------

    def probe(self) -> ServiceStatus:
        """Estado actual. Implementação por omissão: verifica processo + API."""
        if not self.config.enabled:
            return ServiceStatus(
                name=self.name,
                type=self.type_name,
                enabled=False,
                state=DISABLED,
                port=self.config.port,
            )

        process = self._find_process()
        pid = process.pid if process else None
        api_online, body = self._check_api()
        # O corpo cru pode ser enorme: guarda-se um resumo, não o payload.
        detail = self._summarise(body) if api_online else body

        if process is None:
            state = STOPPED
        elif api_online is True:
            state = RUNNING_API_ONLINE
        else:
            state = RUNNING_API_OFFLINE

        return ServiceStatus(
            name=self.name,
            type=self.type_name,
            enabled=True,
            state=state,
            pid=pid,
            port=self.config.port,
            api=self._api_url(),
            api_online=api_online,
            detail=detail,
            controllable=True,
            # Ponto de extensão: cada serviço acrescenta os seus campos.
            extra=self._describe(body),
        )

    # -- controlo -----------------------------------------------------------

    def start(self) -> tuple[bool, str]:
        """Inicia o serviço. Devolve (sucesso, mensagem)."""
        if not self.config.enabled:
            return False, f"{self.name} está desactivado na configuração"
        if self._find_process() is not None:
            return False, f"{self.name} já está a correr"
        command = self._build_command()
        if command is None:
            return False, f"{self.name}: comando de arranque não configurado"
        executable, args, cwd = command
        try:
            subprocess.Popen(
                [executable, *args],
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{self.name}: falha ao iniciar — {exc}"
        return True, f"{self.name}: comando de arranque enviado"

    def stop(self) -> tuple[bool, str]:
        """Termina o processo do serviço."""
        process = self._find_process()
        if process is None:
            return False, f"{self.name} já está parado"
        try:
            process.terminate()
            process.wait(timeout=10)
        except psutil.TimeoutExpired:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
            return False, f"{self.name}: não foi possível terminar — {exc}"
        return True, f"{self.name}: processo terminado"

    def restart(self) -> tuple[bool, str]:
        stopped, message = self.stop()
        if not stopped and "já está parado" not in message:
            return False, message
        time.sleep(1.0)
        return self.start()

    # -- pontos de extensão -------------------------------------------------

    def _find_process(self) -> psutil.Process | None:
        tokens = self.config.process_match
        if not tokens and self.config.path:
            tokens = [self.config.path]
        return find_process(tokens) if tokens else None

    def _api_url(self) -> str | None:
        if not self.config.port or not self.config.api_path:
            return None
        return f"http://127.0.0.1:{self.config.port}{self.config.api_path}"

    def _check_api(self) -> tuple[bool | None, str | None]:
        url = self._api_url()
        if url is None:
            return None, None
        return http_probe(url)

    @staticmethod
    def _summarise(body: str | None) -> str | None:
        """Resumo curto do corpo da resposta, para não inchar o payload."""
        if not body:
            return None
        text = " ".join(body.split())
        return text if len(text) <= 120 else f"{text[:117]}..."

    def _describe(self, body: str | None = None) -> dict[str, Any]:
        """Campos extra específicos do serviço. Vazio por omissão.

        `body` é o corpo cru da resposta da API (None se a API estiver offline),
        para evitar uma segunda chamada HTTP.
        """
        return {}

    def _build_command(self) -> tuple[str, list[str], str | None] | None:
        """(executável, argumentos, working directory) para arrancar."""
        if self.config.python and self.config.path:
            return self.config.python, ["main.py", *self.config.args], self.config.path
        if self.config.python:
            return self.config.python, list(self.config.args), None
        if self.config.path:
            return self.config.path, list(self.config.args), None
        return None


# ---------------------------------------------------------------------------
# ComfyUI
# ---------------------------------------------------------------------------


class ComfyUIService(Service):
    """ComfyUI — servidor de geração de imagens.

    Requisitos ao arrancar: `--enable-manager` e `--listen 0.0.0.0` para que
    seja alcançável através da interface Tailscale.
    """

    type_name: ClassVar[str] = "comfyui"

    DEFAULT_API_PATH = "/system_stats"

    def _api_url(self) -> str | None:
        if not self.config.port:
            return None
        return f"http://127.0.0.1:{self.config.port}{self.config.api_path or self.DEFAULT_API_PATH}"

    def _build_command(self) -> tuple[str, list[str], str | None] | None:
        if not (self.config.python and self.config.path):
            return super()._build_command()
        args = ["main.py", *self.config.args]
        missing = [flag for flag in COMFYUI_REQUIRED_ARGS if flag not in args]
        if missing:
            # Não se removem as flags: completa-se a configuração.
            args.extend(missing)
        return self.config.python, args, self.config.path

    def _find_process(self) -> psutil.Process | None:
        # Preferência pelo match configurado; senão procura pelo path do ComfyUI.
        process = super()._find_process()
        if process is not None:
            return process
        if self.config.path:
            return find_process([self.config.path])
        return None

    def _describe(self, body: str | None = None) -> dict[str, Any]:
        """O Sentinel recebe o estado das flags, sem conhecer os paths."""
        return {
            "managed": "--enable-manager" in (self.config.args or []),
            "listens_tailscale": "0.0.0.0" in (self.config.args or []),
            "install_found": bool(self.config.path and os.path.isdir(self.config.path)),
        }


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


class OllamaService(Service):
    """Ollama — servidor local de modelos."""

    type_name: ClassVar[str] = "ollama"

    DEFAULT_API_PATH = "/api/tags"

    def _find_process(self) -> psutil.Process | None:
        process = super()._find_process()
        if process is not None:
            return process
        # O servidor Ollama chama-se tipicamente "ollama serve".
        return find_process(["ollama", "serve"]) or find_process(["ollama.exe"])

    def _describe(self, body: str | None = None) -> dict[str, Any]:
        """Lista de modelos, usando o corpo já obtido (sem 2.ª chamada HTTP)."""
        models: list[str] | None = None
        if body:
            try:
                payload = json.loads(body)
                models = [
                    model.get("name")
                    for model in payload.get("models", [])
                    if isinstance(model, dict)
                ]
            except (json.JSONDecodeError, AttributeError, TypeError):
                models = None
        return {
            "models": models,
            "model_count": len(models) if models else 0,
        }


# ---------------------------------------------------------------------------
# Registo
# ---------------------------------------------------------------------------

SERVICE_REGISTRY: dict[str, type[Service]] = {
    ComfyUIService.type_name: ComfyUIService,
    OllamaService.type_name: OllamaService,
    Service.type_name: Service,
}


def build_services(configs: list[ServiceConfig]) -> list[Service]:
    """Constrói os serviços a partir da configuração, via registo por tipo."""
    services: list[Service] = []
    for config in configs:
        service_class = SERVICE_REGISTRY.get(config.type)
        if service_class is None:  # pragma: no cover - validado no config
            raise ValueError(f"tipo de serviço desconhecido: {config.type!r}")
        services.append(service_class(config))
    return services


def collect_services(services: list[Service]) -> list[dict[str, Any]]:
    """Estado de todos os serviços. Um serviço que falha não derruba os outros."""
    results: list[dict[str, Any]] = []
    for service in services:
        try:
            results.append(service.probe().to_dict())
        except Exception as exc:  # noqa: BLE001
            results.append(
                ServiceStatus(
                    name=service.name,
                    type=service.type_name,
                    enabled=service.config.enabled,
                    state=UNKNOWN,
                    detail=f"erro ao consultar: {exc}",
                ).to_dict()
            )
    return results
