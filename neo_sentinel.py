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
        help="endereço do node para --pair (aceita IP Tailscale ou URL)",
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


def ask(prompt: str) -> str:
    """Lê uma linha do utilizador. Devolve '' em EOF/interrupção."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def run_pairing(config: SentinelConfig, args: argparse.Namespace) -> int:
    """Emparelha um node a partir de três respostas simples.

    O utilizador só fornece: IP Tailscale, código e nome da estação. As
    portas e o esquema HTTP são detalhes internos, resolvidos aqui e nunca
    mostrados.
    """
    from sentinel.pairing import (
        InvalidTailscaleIP,
        PairingError,
        check_pairing_open,
        link_endpoint,
        pair_by_ip,
        validate_tailscale_ip,
    )

    print()
    print(f"  {APP_NAME} — PAIR NEW NODE")
    print()

    # 1) IP Tailscale. Aceita o IP puro; recusa URLs com uma mensagem clara.
    legacy_url = ""
    if args.url:
        # Compatibilidade: --url já acceptava uma URL completa. Se for uma
        # URL, derivamos o IP e seguimos pelo mesmo caminho.
        text = args.url.strip()
        if text.startswith(("http://", "https://")):
            from urllib.parse import urlparse

            legacy_url = text
            text = urlparse(text).hostname or ""
        try:
            tailscale_ip = validate_tailscale_ip(text)
        except InvalidTailscaleIP:
            from sentinel.pairing import TAILSCALE_EXAMPLE

            print("  [erro] Invalid Tailscale IP.")
            print("         Enter only the Tailscale IP, for example:")
            print(f"         {TAILSCALE_EXAMPLE}")
            return 2
    else:
        while True:
            raw = ask("\n  IP Tailscale:\n  > ")
            if not raw:
                print("  Cancelado.")
                return 1
            try:
                tailscale_ip = validate_tailscale_ip(raw)
                break
            except InvalidTailscaleIP as exc:
                print(f"\n  [erro] {exc}\n")

    # 2) O node tem o pairing aberto?
    print()
    print("  Connecting to Tailscale node...")
    try:
        check_pairing_open(tailscale_ip, timeout=args.timeout)
    except PairingError as exc:
        print(f"  [erro] {exc}")
        print()
        print("  No node, start the pairing with:  neo-link --pair")
        return 1
    print("  [ok] LINK reachable")

    # 3) O código temporário, gerado no node.
    code = ask("\n  Código:\n  > ")
    if not code:
        print("  [erro] O código é obrigatório.")
        return 2

    # 4) O nome da estação. Opcional: vazio significa usar o hostname.
    station = ask("\n  Nome da estação:\n  > ")

    # 5) Trocar o código por uma credencial permanente.
    print()
    print("  Pairing...")
    try:
        if legacy_url:
            from sentinel.pairing import pair as legacy_pair

            result = legacy_pair(
                legacy_url, code, name=station or None, timeout=args.timeout
            )
        else:
            result = pair_by_ip(
                tailscale_ip, code, name=station or None, timeout=args.timeout
            )
    except PairingError as exc:
        print(f"  [erro] {exc}")
        return 1

    print("  [ok] Pairing accepted")
    print("  [ok] Station identity received")
    if not result.node_id:
        print("  [aviso] o LINK nao devolveu node_id")

    # 6) Guardar. A identidade vem do LINK; o nome e o que o utilizador escreveu.
    node = NodeConfig(
        name=result.name,
        url=legacy_url or link_endpoint(tailscale_ip),
        token=result.credential,
        enabled=True,
        node_id=result.node_id or None,
        hostname=result.hostname or None,
        tailscale_ip=tailscale_ip or None,
    )
    config.upsert_node(node)
    saved = config.save(args.config)
    print("  [ok] Credential stored")

    print()
    print(f"  Station:  {node.name}")
    if node.hostname:
        print(f"  Hostname: {node.hostname}")
    if node.node_id:
        print(f"  Node ID:  {node.node_id}")
    print(f"  config:   {saved}")
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
