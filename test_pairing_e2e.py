"""Teste E2E do pairing com um LINK real, na máquina local.

Sobe um PairingServer a sério, corre o fluxo completo do SENTINEL (IP Tailscale
-> codigo -> nome) e verifica o que fica guardado. Nao abre portas publicas:
tudo em 127.0.0.1.

    python test_pairing_e2e.py
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "link"))

from link.neolink.config import LinkConfig  # noqa: E402
from link.neolink.identity import build_identity  # noqa: E402
from link.neolink.pairing import CredentialStore  # noqa: E402
from link.neolink.pairing_server import PairingServer  # noqa: E402
from sentinel import pairing as sentinel_pairing  # noqa: E402
from sentinel.config import NodeConfig, SentinelConfig  # noqa: E402


def free_port() -> int:
    """Uma porta livre na loopback (a de pairing, 8766, fica reservada)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestPairingEndToEnd(unittest.TestCase):
    """O fluxo real, com o LINK a correr."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.port = free_port()
        self.server = None
        self.threads = []

    def _start_link(self, node_name: str = ""):
        """Arranca um PairingServer real, como `neo-link --pair` faria.

        `node_name` é o `name` configurado no LINK. Vazio significa "não
        configurado", e o LINK usará o hostname real da máquina.
        """
        config = LinkConfig.from_dict({
            "node": {"name": node_name} if node_name else {},
            "server": {"host": "127.0.0.1", "port": 8765,
                       "allow_control": False, "token": None},
            "services": [],
        })
        store = CredentialStore.load(self.root / "credentials.json")
        self.server = PairingServer(config, store, "127.0.0.1", self.port)
        display = self.server.start()

        def run():
            self.server.wait(20)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.threads.append(thread)
        return display, store

    def _pair(self, code: str, name: str | None = None):
        """Corre o fluxo do SENTINEL contra o LINK a correr."""
        sentinel_pairing.PAIRING_PORT = self.port  # o teste n~ao assume 8766
        return sentinel_pairing.pair_by_ip("127.0.0.1", code, name=name, timeout=10)

    def test_full_pairing_with_custom_name(self):
        display, store = self._start_link("NOME-DO-LINK")
        original = sentinel_pairing.PAIRING_PORT
        self.addCleanup(setattr, sentinel_pairing, "PAIRING_PORT", original)

        result = self._pair(display.code, name="RENDER-01")

        self.assertTrue(result.credential)
        self.assertTrue(result.node_id.startswith("nx1-"))
        # O nome escolhido pelo utilizador prevalece sobre o do LINK.
        self.assertEqual(result.name, "RENDER-01")
        # O hostname continua a ser o da maquina, descoberto pelo LINK.
        import platform

        self.assertEqual(result.hostname, platform.node())
        # A credencial foi realmente registada no LINK.
        self.assertTrue(store.verify(result.credential))

    def test_full_pairing_without_name_uses_hostname(self):
        """Campo vazio: o nome passa a ser o hostname real do node."""
        display, store = self._start_link()  # LINK sem `name` configurado
        original = sentinel_pairing.PAIRING_PORT
        self.addCleanup(setattr, sentinel_pairing, "PAIRING_PORT", original)

        result = self._pair(display.code, name=None)

        # O LINK caiu no hostname da máquina, como manda a identidade canónica.
        import platform

        self.assertEqual(result.name, platform.node())
        self.assertEqual(result.hostname, platform.node())

    def test_node_id_stable_across_renames(self):
        """Emparelhar duas vezes com nomes diferentes: o node_id nao muda."""
        display, store = self._start_link("NOME-ANTIGO")
        original = sentinel_pairing.PAIRING_PORT
        self.addCleanup(setattr, sentinel_pairing, "PAIRING_PORT", original)

        first = self._pair(display.code, name="NOME-ANTIGO")
        node_id = first.node_id
        stored = store.node_id()

        # Segundo pairing, agora com outro nome.
        display2 = self.server._begin_pairing()
        second = self._pair(display2.code, name="NOME-NOVO")

        self.assertEqual(second.node_id, node_id, "o node_id mudou ao renomear")
        self.assertEqual(second.name, "NOME-NOVO")
        self.assertEqual(store.node_id(), stored)
        # Ambas as credenciais continuam validas no mesmo node.
        self.assertTrue(store.verify(first.credential))
        self.assertTrue(store.verify(second.credential))

    def test_credential_persisted_and_node_stored(self):
        """O que o SENTINEL guarda chega para voltar a falar com o node."""
        display, store = self._start_link("ESTACAO")
        original = sentinel_pairing.PAIRING_PORT
        self.addCleanup(setattr, sentinel_pairing, "PAIRING_PORT", original)

        result = self._pair(display.code, name="ESTACAO")

        config = SentinelConfig(nodes=[])
        config.upsert_node(NodeConfig(
            name=result.name,
            url=sentinel_pairing.link_endpoint("127.0.0.1"),
            token=result.credential,
            node_id=result.node_id,
            hostname=result.hostname,
            tailscale_ip="127.0.0.1",
        ))
        path = self.root / "config.json"
        config.save(path)

        reloaded = SentinelConfig.load(path)
        node = reloaded.nodes[0]
        self.assertEqual(node.node_id, result.node_id)
        self.assertEqual(node.hostname, result.hostname)
        self.assertEqual(node.tailscale_ip, "127.0.0.1")
        self.assertTrue(node.token)
        # O pairing code nunca e guardado.
        self.assertNotIn(display.code, path.read_text(encoding="utf-8"))

        # E a credencial ainda e aceite pelo LINK.
        self.assertTrue(store.verify(node.token))

    def test_expired_or_wrong_code_is_rejected(self):
        """Um codigo errado falha, e a mensagem e orientada ao utilizador."""
        self._start_link()
        original = sentinel_pairing.PAIRING_PORT
        self.addCleanup(setattr, sentinel_pairing, "PAIRING_PORT", original)

        with self.assertRaises(sentinel_pairing.PairingError) as ctx:
            self._pair("ZZZZ-ZZZZ")
        message = str(ctx.exception)
        self.assertNotIn("8766", message)
        self.assertNotIn("http://", message)

    def test_pairing_is_single_use(self):
        """O codigo nao serve para emparelhar duas vezes."""
        display, _ = self._start_link()
        original = sentinel_pairing.PAIRING_PORT
        self.addCleanup(setattr, sentinel_pairing, "PAIRING_PORT", original)

        self._pair(display.code, name="PRIMEIRO")
        time.sleep(0.3)
        with self.assertRaises(sentinel_pairing.PairingError):
            self._pair(display.code, name="SEGUNDO")

    def test_legacy_url_path_still_works(self):
        """Uma configuracao antiga (URL) continua a emparelhar."""
        display, store = self._start_link("VELHO")
        legacy = f"http://127.0.0.1:{self.port}"

        result = sentinel_pairing.pair(
            legacy, display.code, port=self.port, name="VELHO"
        )
        self.assertTrue(result.credential)
        self.assertTrue(store.verify(result.credential))

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
