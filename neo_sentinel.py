#!/usr/bin/env python3
"""NEO//SENTINEL — cliente TUI de monitorização e controlo.

Uso:
    python neo_sentinel.py                    # usa ./config.json
    python neo_sentinel.py --config outro.json
    python neo_sentinel.py --discover         # inclui descoberta Tailscale
    python neo_sentinel.py --check            # lista os nodes e sai

Para testar localmente, arranque primeiro o LINK na outra máquina (ou na mesma):
    python ..\\link\\neo_link.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sentinel import APP_NAME, __version__  # noqa: E402
from sentinel.config import NodeConfig, SentinelConfig  # noqa: E402
from sentinel.discovery import (  # noqa: E402
    CompositeDiscovery,
    NodeDiscovery,
    StaticDiscovery,
    TailscaleDiscovery,
)
from sentinel.ui import SentinelApp  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} — cliente TUI")
    parser.add_argument("--config", default=None, help="caminho para config.json")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="inclui descoberta automática via Tailscale",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="lista os nodes configurados e termina",
    )
    parser.add_argument(
        "--pair",
        action="store_true",
        help="emparelha um node remoto e guarda a credencial",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="URL do LINK, para --pair (ex.: http://host:8765)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="tempo limite de rede em segundos",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser.parse_args(argv)


def build_discovery(config: SentinelConfig, discover: bool) -> NodeDiscovery:
    sources: list[NodeDiscovery] = [StaticDiscovery(config.nodes)]
    if discover:
        # Só lê a lista de peers que o Tailscale já conhece. Sem port scanning.
        sources.append(TailscaleDiscovery())
    return CompositeDiscovery(sources)


def check_links(config: SentinelConfig, found: list) -> int:
    """Testa a conectividade com cada LINK e mostra o estado de cada serviço.

    Não imprime tokens. Devolve 0 se todos responderem, 1 caso contrário.
    """
    from sentinel.client import LinkClient

    by_name = {node.name: node for node in found}
    problems = 0
    for node_config in config.nodes:
        if node_config.name not in by_name:
            continue
        try:
            snapshot = LinkClient(node_config, config.timeout_seconds).poll()
        except Exception as exc:  # noqa: BLE001
            print(f"    {node_config.name}: OFFLINE ({exc})")
            problems += 1
            continue
        if not snapshot.online:
            print(f"    {node_config.name}: OFFLINE ({snapshot.error})")
            problems += 1
            continue
        print(
            f"    {node_config.name}: ONLINE "
            f"({snapshot.hostname or '?'} · {snapshot.agent or '?'} "
            f"{snapshot.agent_version or ''})".rstrip()
        )
        for service in snapshot.services:
            print(f"      {service['name']:<12} {service.get('state', '?')}")
    return 1 if problems else 0


def run_pairing(config: SentinelConfig, args: argparse.Namespace) -> int:
    """Emparelha um node e grava a credencial na configuração existente."""
    from sentinel.pairing import PairingError, check_reachable, normalise_url, pair

    print()
    print(f"  {APP_NAME}")
    print()
    print("  Pair new node")
    print()

    url = args.url
    if not url:
        try:
            url = input("  LINK URL (ex.: http://master:8765): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
    if not url:
        print("  URL em falta.")
        return 2
    url = normalise_url(url)

    # 1) O LINK responde?
    try:
        info = check_reachable(url, timeout=args.timeout)
        print(f"  [ok] LINK reachable ({info.get('agent', 'NEO//LINK')})")
    except PairingError as exc:
        print(f"  [erro] {exc}")
        return 1

    # 2) O codigo temporario, gerado no LINK.
    print()
    try:
        code = input("  Pairing code (ex.: 7K4M-92PX): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 1
    if not code:
        print("  Codigo em falta.")
        return 2

    # 3) Trocar o codigo por uma credencial permanente.
    print()
    print("  Connecting...")
    try:
        result = pair(url, code, timeout=args.timeout)
    except PairingError as exc:
        print(f"  [erro] {exc}")
        return 1

    print("  [ok] Pairing accepted")
    print(f"  [ok] Node identity received ({result.name})")

    # 4) Guardar na configuracao existente (formato actual: campo 'token').
    node = NodeConfig(
        name=result.name,
        url=url,
        token=result.credential,
        enabled=True,
    )
    config.upsert_node(node)
    saved = config.save(args.config)
    print("  [ok] Credential stored")
    print()
    print(f"  {result.name} [online]")
    print(f"  config: {saved}")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(message)s",
    )

    try:
        config = SentinelConfig.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        logging.error("configuração inválida: %s", exc)
        return 2

    discovery = build_discovery(config, args.discover)

    if args.pair:
        return run_pairing(config, args)

    if args.check:
        print(f"{APP_NAME} {__version__}")
        found = discovery.discover()
        if not found:
            print("nenhum node configurado (edite config.json)")
            return 0
        for node in found:
            print(f"  {node.name:<12} {node.url:<40} ({node.source})")
        # Verifica conectividade com cada LINK, usando o cliente normal.
        return check_links(config, found)

    SentinelApp(config=config, discovery=discovery).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
