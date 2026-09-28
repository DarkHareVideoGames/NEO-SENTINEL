"""TUI do NEO//SENTINEL.

Princípio: o SENTINEL só apresenta. Todos os dados vêm dos LINKs.

Layout: lista de NODES em cima; abaixo, o detalhe do node seleccionado.
As barras de utilização são compactas (meio bloco) e nunca fundem duas linhas
consecutivas num bloco sólido.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Static

from . import APP_NAME, __version__
from .client import LinkClient, NodeSnapshot
from .config import SentinelConfig
from .discovery import CompositeDiscovery, NodeDiscovery, StaticDiscovery

NA = "N/A"
_REFRESH_CLOCK_SECONDS = 1.0  # actualiza a data/hora a cada segundo
_CARD_HEIGHT_RULE = "─"

# Larguras das colunas (label | valor | barra)
_LABEL_WIDTH = 13
_VALUE_WIDTH = 34
_BAR_WIDTH = 10
_RULE_WIDTH = _LABEL_WIDTH + _VALUE_WIDTH + _BAR_WIDTH + 4

# Meio bloco: preenche só metade da célula, pelo que barras consecutivas
# ficam separadas sem precisar de linhas em branco.
_BAR_FILLED = "▄"
_BAR_EMPTY = "─"


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------


def format_percent(value: float | None, digits: int = 0) -> str:
    if value is None:
        return NA
    return f"{value:.{digits}f}%"


def format_gb(value: float | None) -> str:
    if value is None:
        return NA
    return f"{value:.1f} GB"


def load_style(percent: float | None) -> str:
    """Cor orientada à carga."""
    if percent is None:
        return "dim"
    if percent >= 90:
        return "bold red"
    if percent >= 70:
        return "bold yellow"
    return "bold green"


def temperature_style(celsius: float | None) -> str:
    if celsius is None:
        return "dim"
    if celsius >= 85:
        return "bold red"
    if celsius >= 70:
        return "bold yellow"
    return "bold green"


def usage_bar(percent: float | None, width: int = _BAR_WIDTH) -> Text:
    """Barra compacta de utilização (meio bloco, uma linha de altura)."""
    if percent is None:
        return Text(_BAR_EMPTY * width, style="dim")
    filled = max(0, min(width, round(width * percent / 100)))
    return Text(_BAR_FILLED * filled, style=load_style(percent)) + Text(
        _BAR_EMPTY * (width - filled), style="dim"
    )


def state_symbol(online: bool) -> str:
    return "●" if online else "○"


def state_style(online: bool) -> str:
    return "bold green" if online else "bold red"


# ---------------------------------------------------------------------------
# Tabelas
# ---------------------------------------------------------------------------


def _table() -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(width=_LABEL_WIDTH, no_wrap=True)
    table.add_column(width=_VALUE_WIDTH, no_wrap=True, overflow="ellipsis")
    table.add_column(width=_BAR_WIDTH, no_wrap=True)
    return table


def _field(table: Table, label: str, value: str, style: str = "") -> None:
    table.add_row(Text(label, style="dim cyan"), Text(value, style=style), Text(""))


def _usage(table: Table, label: str, percent: float | None) -> None:
    """Linha com percentagem textual + barra (a percentagem nunca é perdida)."""
    table.add_row(
        Text(label, style="dim cyan"),
        Text(format_percent(percent), style=load_style(percent)),
        usage_bar(percent),
    )


def _title(text: str) -> Text:
    return Text(text.ljust(_RULE_WIDTH, _CARD_HEIGHT_RULE), style="bold white")


def render_system(snapshot: NodeSnapshot) -> RenderableType:
    """SYSTEM: CPU e RAM, percentagem + barra, com os totais em baixo."""
    table = _table()
    _usage(table, "CPU", snapshot.cpu_percent)
    _usage(table, "RAM", snapshot.ram_percent)
    # Totais só se existirem; nunca substituem a percentagem.
    if snapshot.ram_used_gb is not None or snapshot.ram_total_gb is not None:
        _field(
            table,
            "RAM",
            f"{format_gb(snapshot.ram_used_gb)} / {format_gb(snapshot.ram_total_gb)}",
            "dim",
        )
    return Group(_title("SYSTEM"), Text(""), table)


def render_gpu(snapshot: NodeSnapshot) -> RenderableType:
    """GPU: um bloco por GPU, com campos independentes."""
    if not snapshot.gpus:
        table = _table()
        _field(table, "GPU", f"{NA} (sem GPU reportada)", "dim")
        return Group(_title("GPU"), Text(""), table)

    blocks: list[RenderableType] = []
    for index, gpu in enumerate(snapshot.gpus):
        if index:
            blocks.append(Text(""))
        table = _table()
        _field(table, "Model", str(gpu.get("model") or NA))
        _usage(table, "Usage", gpu.get("usage_percent"))
        temperature = gpu.get("temperature_c")
        _field(
            table,
            "Temperature",
            NA if temperature is None else f"{temperature:.0f}°C",
            temperature_style(temperature),
        )
        _usage(table, "VRAM", gpu.get("vram_percent"))
        vram_used = gpu.get("vram_used_gb")
        vram_total = gpu.get("vram_total_gb")
        if vram_used is not None or vram_total is not None:
            _field(table, "VRAM", f"{format_gb(vram_used)} / {format_gb(vram_total)}", "dim")
        blocks.append(table)
    return Group(_title("GPU"), Text(""), *blocks)


def render_disks(snapshot: NodeSnapshot) -> RenderableType:
    """DISKS: uma linha por volume."""
    if not snapshot.disks:
        table = _table()
        _field(table, "Disks", NA, "dim")
        return Group(_title("DISKS"), Text(""), table)

    table = _table()
    for disk in snapshot.disks:
        _usage(table, str(disk.get("device") or NA), disk.get("percent"))
    return Group(_title("DISKS"), Text(""), table)


def render_services(snapshot: NodeSnapshot) -> RenderableType:
    """SERVICES: estado reportado pelo LINK. O SENTINEL não conhece comandos."""
    if not snapshot.services:
        table = _table()
        _field(table, "Services", f"{NA} (nenhum serviço configurado)", "dim")
        return Group(_title("SERVICES"), Text(""), table)

    table = Table.grid(padding=(0, 1))
    table.add_column(width=_LABEL_WIDTH + 4, no_wrap=True)
    table.add_column(width=24, no_wrap=True)
    table.add_column(no_wrap=True)
    for service in snapshot.services:
        online = service.get("api_online") is True or service.get("pid") is not None
        state = str(service.get("state") or NA)
        pid = service.get("pid")
        port = service.get("port")
        detail = NA if port is None else f"pid {pid or '-'} · porta {port}"
        table.add_row(
            Text(str(service.get("name") or NA), style="dim cyan"),
            Text(f"{state_symbol(online)} {state}", style=state_style(online)),
            Text(detail, style="dim"),
        )
    return Group(_title("SERVICES"), Text(""), table)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class NodeListWidget(Static):
    """Lista NODES com o node seleccionado destacado."""

    def show(self, snapshots: dict[str, NodeSnapshot], selected: str | None) -> None:
        if not snapshots:
            self.update(Text("NODES\n\n  nenhum node configurado", style="dim"))
            return

        table = Table.grid(padding=(0, 1))
        for name, snapshot in snapshots.items():
            is_selected = name == selected
            marker = "›" if is_selected else " "
            style = "bold white" if is_selected else ""
            table.add_row(
                Text(f"{marker} {state_symbol(snapshot.online)}", style=state_style(snapshot.online)),
                Text(name, style=style),
            )
        self.update(Group(_title("NODES"), Text(""), table))


class NodeDetailWidget(Static):
    """Detalhe completo do node seleccionado."""

    def show(self, snapshot: NodeSnapshot | None) -> None:
        if snapshot is None:
            self.update(Text("a carregar…", style="dim"))
            return

        header = Table.grid(padding=(0, 1))
        header.add_row(
            Text(state_symbol(snapshot.online), style=state_style(snapshot.online)),
            Text(snapshot.name, style="bold white"),
            Text(
                f"({snapshot.hostname})" if snapshot.hostname else "",
                style="dim",
            ),
        )
        meta = Table.grid(padding=(0, 1))
        if snapshot.platform:
            _field(meta, "Platform", snapshot.platform, "dim")
        if snapshot.agent:
            _field(meta, "Agent", f"{snapshot.agent} {snapshot.agent_version or ''}".strip(), "dim")
        if snapshot.tailscale_ip:
            _field(meta, "Tailscale", snapshot.tailscale_ip, "dim")
        if snapshot.error:
            _field(meta, "Aviso", snapshot.error, "yellow")

        self.update(
            Group(
                header,
                Text(""),
                render_system(snapshot),
                Text(""),
                render_gpu(snapshot),
                Text(""),
                render_disks(snapshot),
                Text(""),
                render_services(snapshot),
            )
        )


# ---------------------------------------------------------------------------
# Aplicação
# ---------------------------------------------------------------------------


class SentinelApp(App[None]):
    """TUI do SENTINEL."""

    TITLE = APP_NAME
    SUB_TITLE = f"v{__version__}"

    CSS = """
    Screen { background: $surface; }

    Header {
        background: $panel;
    }

    #body {
        height: 1fr;
        padding: 0 2;
    }

    #nodes-list {
        height: auto;
        border: round $accent;
        padding: 0 1;
        margin-bottom: 1;
    }

    #node-detail {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("q", "quit", "Sair"),
        ("r", "refresh", "Refresh"),
        ("up", "select_previous", "Node anterior"),
        ("down", "select_next", "Node seguinte"),
    ]

    def __init__(
        self,
        config: SentinelConfig | None = None,
        discovery: NodeDiscovery | None = None,
    ) -> None:
        super().__init__()
        self.config = config or SentinelConfig(nodes=[])
        self.discovery = discovery or StaticDiscovery(self.config.nodes)
        self.clients: dict[str, LinkClient] = {
            node.name: LinkClient(node, self.config.timeout_seconds)
            for node in self.config.nodes
            if node.enabled
        }
        self.snapshots: dict[str, NodeSnapshot] = {}
        self.selected: str | None = None
        # NÃO usar `_nodes`: colide com o atributo interno do Textual (App._nodes).
        self._known_nodes: list[str] = []

    def compose(self) -> ComposeResult:
        # show_clock=False: o relógio é o nosso, em DD/MM/YYYY HH:MM:SS.
        yield Header(show_clock=False)
        with Vertical(id="body"):
            yield NodeListWidget(id="nodes-list")
            with VerticalScroll(id="node-detail"):
                yield NodeDetailWidget(id="node-detail-widget")
        yield Footer()

    def on_mount(self) -> None:
        self._sync_nodes()
        # Relógio a cada segundo; dados a cada N segundos.
        self.set_interval(_REFRESH_CLOCK_SECONDS, self._tick_clock)
        self.set_interval(self.config.refresh_seconds, self.poll_all)
        self.poll_all()

    def action_refresh(self) -> None:
        self.poll_all()

    def action_select_next(self) -> None:
        self._move_selection(1)

    def action_select_previous(self) -> None:
        self._move_selection(-1)

    def _move_selection(self, delta: int) -> None:
        if not self._known_nodes:
            return
        index = (
            self._known_nodes.index(self.selected)
            if self.selected in self._known_nodes
            else 0
        )
        self.selected = self._known_nodes[(index + delta) % len(self._known_nodes)]
        self._render()

    def _sync_nodes(self) -> None:
        """Mantém a lista de nodes em dia com o que a descoberta devolveu."""
        for node in self.discovery.discover():
            if node.name in self.clients:
                continue
            from .config import NodeConfig  # import local: evita dependência circular

            self.clients[node.name] = LinkClient(
                NodeConfig(name=node.name, url=node.url),
                self.config.timeout_seconds,
            )
        self._known_nodes = list(self.clients)
        if self.selected not in self._known_nodes:
            self.selected = self._known_nodes[0] if self._known_nodes else None

    def _tick_clock(self) -> None:
        """Actualiza só o cabeçalho (data/hora), a cada segundo."""
        if hasattr(self, "title"):
            self.sub_title = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    @work(exclusive=True, group="poll", thread=True)
    def poll_all(self) -> None:
        """Consulta todos os nodes numa thread; nunca bloqueia a UI."""
        for name, client in list(self.clients.items()):
            snapshot = client.poll()
            self.app.call_from_thread(self._apply, name, snapshot)

    def _apply(self, name: str, snapshot: NodeSnapshot) -> None:
        self.snapshots[name] = snapshot
        self._render()

    def _render(self) -> None:
        self.query_one("#nodes-list", NodeListWidget).show(self.snapshots, self.selected)
        self.query_one("#node-detail-widget", NodeDetailWidget).show(
            self.snapshots.get(self.selected) if self.selected else None
        )


def build_default_app() -> SentinelApp:
    """SENTINEL com os nodes da configuração, mais descoberta estática."""
    config = SentinelConfig.load()
    discovery: NodeDiscovery = CompositeDiscovery([StaticDiscovery(config.nodes)])
    return SentinelApp(config=config, discovery=discovery)
