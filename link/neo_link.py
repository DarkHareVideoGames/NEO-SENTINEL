#!/usr/bin/env python3
"""NEO//LINK — agente local.

Uso:
    python neo_link.py                 # usa ./config.json
    python neo_link.py --config outro.json
    python neo_link.py --port 9000     # sobrepõe a porta
    python neo_link.py --check         # valida a config e não serve
    python neo_link.py --pair          # emparelha um SENTINEL (temporário)

O LINK é executável independentemente do SENTINEL.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from neolink import AGENT_NAME, __version__  # noqa: E402
from neolink.config import LinkConfig  # noqa: E402
from neolink.pairing import CODE_TTL, CredentialStore  # noqa: E402
from neolink.pairing_server import PairingServer  # noqa: E402
from neolink.server import LinkServer  # noqa: E402
from neolink.services import build_services  # noqa: E402

DEFAULT_CREDENTIALS_FILE = "credentials.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{AGENT_NAME} — agente local")
    parser.add_argument("--config", default=None, help="caminho para config.json")
    parser.add_argument("--host", default=None, help="sobrescreve server.host")
    parser.add_argument("--port", type=int, default=None, help="sobrescreve server.port")
    parser.add_argument(
        "--allow-control",
        action="store_true",
        help="activa start/stop/restart nesta execução",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="valida a configuração e termina",
    )
    parser.add_argument(
        "--pair",
        action="store_true",
        help="emparelha um SENTINEL e termina (servidor temporário)",
    )
    parser.add_argument(
        "--pair-port",
        type=int,
        default=None,
        help="porta do servidor de pairing (omissão: server.port + 1)",
    )
    parser.add_argument(
        "--credentials",
        default=None,
        help=f"ficheiro de credenciais (omissão: {DEFAULT_CREDENTIALS_FILE} ao lado do config)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    parser.add_argument("--version", action="version", version=f"{AGENT_NAME} {__version__}")
    return parser.parse_args(argv)


def credentials_path(args: argparse.Namespace, config_path: str | None) -> Path:
    """Onde ficam as credenciais pareadas (nunca versionadas)."""
    if args.credentials:
        return Path(args.credentials)
    base = Path(config_path) if config_path else Path(__file__).resolve().parent / "config.json"
    return base.parent / DEFAULT_CREDENTIALS_FILE


def run_pairing(config: LinkConfig, store: CredentialStore, args: argparse.Namespace) -> int:
    """Servidor de pairing temporário: gera o código e espera pelo SENTINEL."""
    # O pairing escuta sempre em todas as interfaces configuradas, para o
    # telemóvel alcançar a máquina. Se o LINK só escuta em localhost, o
    # operador tem de expor explicitamente com --host.
    host = args.host or config.server.host
    if host == "127.0.0.1":
        print()
        print("  Nota: o LINK está configurado para 127.0.0.1 (só local).")
        print("  Para emparelhar a partir de outro dispositivo, exponha a")
        print("  interface Tailscale, por exemplo:")
        print("      python neo_link.py --pair --host 0.0.0.0")
        print()
    port = args.pair_port or (config.server.port + 1)

    server = PairingServer(config, store, host, port)
    try:
        display = server.start()
    except OSError as exc:
        logging.error("não foi possível abrir a porta %d: %s", port, exc)
        print(f"  Porta {port} ocupada. Use --pair-port <outra>.", file=sys.stderr)
        return 3

    minutes = int(CODE_TTL.total_seconds() // 60)
    print()
    print("  NEO//LINK PAIRING")
    print()
    print(f"  Node: {config.name}")
    print()
    print("  Pairing code:")
    print(f"      {display.code}")
    print()
    print(f"  Expires in: {minutes} minutes")
    print(f"  Pairing URL: http://{host}:{port}")
    print()
    print("  Waiting for Sentinel...")
    print("  (Ctrl+C para cancelar)")
    print()

    try:
        paired = server.wait(float(CODE_TTL.total_seconds()) + 5)
    except KeyboardInterrupt:
        print()
        print("  Cancelado.")
        return 1
    finally:
        server.shutdown()

    if paired:
        print()
        print("  SENTINEL emparelhado. Credencial guardada (nunca é mostrada).")
        print(f"  Clientes pareados: {store.count}")
        print()
        return 0
    print()
    print("  Tempo esgotado sem pareamento. Gere um novo código quando quiser.")
    print()
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        config = LinkConfig.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        logging.error("configuração inválida: %s", exc)
        return 2

    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    if args.allow_control:
        config.server.allow_control = True

    services = build_services(config.services)
    store = CredentialStore.load(credentials_path(args, args.config))

    if args.pair:
        return run_pairing(config, store, args)

    logging.info("node: %s", config.name)
    logging.info("serviços: %s", ", ".join(s.name for s in services) or "(nenhum)")
    if store.count:
        logging.info("clientes pareados: %d", store.count)

    if args.check:
        for service in services:
            status = service.probe()
            logging.info("  %s -> %s", status.name, status.state)
        logging.info("configuração válida")
        return 0

    LinkServer(config, services, store).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
