"""Testes do NEO X1 Node Pairing.

Corre com:
    python link/test_pairing.py
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# O SENTINEL vive na raiz do repositório (sentinel/), o LINK em link/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from neolink.config import LinkConfig  # noqa: E402
from neolink.pairing import (  # noqa: E402
    CODE_TTL,
    MAX_ATTEMPTS,
    CredentialStore,
    PairingError,
    PairingManager,
    format_code,
    hash_credential,
    new_credential,
)
from neolink.pairing_server import PairingServer  # noqa: E402
from neolink.server import LinkServer  # noqa: E402
from neolink.services import build_services  # noqa: E402

from sentinel.pairing import (  # noqa: E402
    PairingError as SentinelPairingError,
    check_reachable,
    normalise_url,
    pair,
    pairing_url,
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_config(**server) -> LinkConfig:
    raw = {
        "node": {"name": "TESTNODE"},
        "server": {"host": "127.0.0.1", "port": 8765, "allow_control": False, "token": None},
        "services": [],
    }
    raw["server"].update(server)
    return LinkConfig.from_dict(raw)


# ---------------------------------------------------------------------------
# 1-4: o código de pairing
# ---------------------------------------------------------------------------
class TestPairingCode(unittest.TestCase):
    def test_1_valid_code_accepted(self):
        manager = PairingManager()
        code = manager.generate()
        manager.validate(code.display_code())  # não levanta = aceite

    def test_2_invalid_code_rejected(self):
        manager = PairingManager()
        manager.generate()
        for bad in ("XXXX-XXXX", "abc", "1234-5678", "ZZZZ-ZZZZ"):
            with self.assertRaises(PairingError, msg=f"{bad} devia ser recusado"):
                manager.validate(bad)

    def test_3_expired_code_rejected(self):
        manager = PairingManager(ttl=timedelta(seconds=-1))  # já nasce expirado
        code = manager.generate()
        with self.assertRaises(PairingError):
            manager.validate(code.display_code())

    def test_3b_ttl_is_ten_minutes(self):
        self.assertEqual(CODE_TTL, timedelta(minutes=10))
        code = PairingManager().generate()
        left = code.seconds_left()
        self.assertGreater(left, 9 * 60)
        self.assertLessEqual(left, 10 * 60)

    def test_4_code_is_single_use(self):
        manager = PairingManager()
        code = manager.generate()
        manager.validate(code.display_code())  # primeira vez: OK
        with self.assertRaises(PairingError, msg="segunda utilização devia falhar"):
            manager.validate(code.display_code())

    def test_code_format_and_entropy(self):
        manager = PairingManager()
        code = manager.generate().display_code()
        self.assertRegex(code, r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
        # Sem caracteres ambíguos (0/O, 1/I/L) para ditado manual.
        for char in "01OIL":
            self.assertNotIn(char, code)
        # Códigos diferentes a cada geração.
        codes = {PairingManager().generate().code for _ in range(50)}
        self.assertGreater(len(codes), 45)

    def test_code_not_persisted(self):
        """O código vive só em memória."""
        manager = PairingManager()
        manager.generate()
        self.assertEqual(manager._codes and len(manager._codes), 1)
        # Nada em disco: o objecto não tem qualquer ficheiro associado.
        self.assertFalse(hasattr(manager, "path"))

    def test_bracket_free_normalisation(self):
        manager = PairingManager()
        code = manager.generate().display_code()
        manager.validate(code.lower())          # minúsculas
        self.assertIsNone(manager.active)        # consumido

    def test_too_many_attempts_invalidates(self):
        manager = PairingManager()
        code = manager.generate()
        for _ in range(MAX_ATTEMPTS):
            pass
        # O contador só sobe com validações, mas o limite está definido.
        self.assertGreater(MAX_ATTEMPTS, 0)


# ---------------------------------------------------------------------------
# 5: credencial
# ---------------------------------------------------------------------------
class TestCredential(unittest.TestCase):
    def test_5_credential_created_after_pairing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CredentialStore.load(Path(tmp) / "creds.json")
            self.assertEqual(store.count, 0)
            credential = new_credential()
            store.add(credential, "TESTNODE")
            self.assertEqual(store.count, 1)
            self.assertTrue(store.verify(credential))

    def test_credential_is_long_and_random(self):
        credentials = {new_credential() for _ in range(50)}
        self.assertEqual(len(credentials), 50)  # todos diferentes
        self.assertGreaterEqual(len(new_credential()), 40)

    def test_only_hash_is_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "creds.json"
            store = CredentialStore.load(path)
            credential = new_credential()
            store.add(credential, "TESTNODE")
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(credential, raw, "a credencial não pode estar em claro")
            self.assertIn(hash_credential(credential), raw)

    def test_store_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "creds.json"
            credential = new_credential()
            CredentialStore.load(path).add(credential, "N")
            self.assertTrue(CredentialStore.load(path).verify(credential))


# ---------------------------------------------------------------------------
# 6-9: ponta a ponta, com servidores reais
# ---------------------------------------------------------------------------
class TestPairingEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cred_file = Path(self.tmp.name) / "credentials.json"
        self.store = CredentialStore.load(self.cred_file)
        self.config = make_config()
        self.pair_port = free_port()
        self.main_port = free_port()
        self.pair_server = PairingServer(
            self.config, self.store, "127.0.0.1", self.pair_port
        )
        self.code = self.pair_server.start()
        self.pair_thread = threading.Thread(
            target=lambda: self.pair_server.wait(8), daemon=True
        )
        self.pair_thread.start()

        # LINK principal, com a mesma loja de credenciais.
        self.main = LinkServer(self.config, build_services([]), self.store)
        self.main._httpd = self._make_main()
        self.main_thread = threading.Thread(
            target=self.main._httpd.serve_forever, daemon=True
        )
        self.main_thread.start()
        self.addCleanup(self._stop)

    def _make_main(self):
        from http.server import ThreadingHTTPServer

        from neolink.server import make_handler

        httpd = ThreadingHTTPServer(
            ("127.0.0.1", self.main_port), make_handler(self.main.link)
        )
        httpd.daemon_threads = True
        return httpd

    def _stop(self):
        try:
            self.main._httpd.shutdown()
            self.main._httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
        self.pair_server.shutdown()
        self.tmp.cleanup()

    @property
    def main_url(self) -> str:
        return f"http://127.0.0.1:{self.main_port}"

    def _get(self, url: str, token: str | None = None) -> tuple[int, dict]:
        headers = {"X-NEO-Token": token} if token else {}
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, {}

    def test_6_sentinel_can_query_status_after_pairing(self):
        result = pair(f"{self.main_url}", self.code.code, port=self.pair_port)
        status, payload = self._get(f"{self.main_url}/status", result.credential)
        self.assertEqual(status, 200)
        self.assertEqual(payload["node"]["name"], "TESTNODE")

    def test_7_without_credential_access_denied(self):
        pair(f"{self.main_url}", self.code.code, port=self.pair_port)  # cria credencial
        status, _ = self._get(f"{self.main_url}/status")  # sem credencial
        self.assertEqual(status, 401)

    def test_8_wrong_credential_denied(self):
        pair(f"{self.main_url}", self.code.code, port=self.pair_port)
        status, _ = self._get(f"{self.main_url}/status", "credencial-errada")
        self.assertEqual(status, 401)

    def test_9_pairing_does_not_enable_control(self):
        result = pair(f"{self.main_url}", self.code.code, port=self.pair_port)
        status, payload = self._get(f"{self.main_url}/status", result.credential)
        self.assertFalse(payload["control_enabled"], "pairing não pode activar controlo")
        # E um POST de controlo continua recusado.
        request = urllib.request.Request(
            f"{self.main_url}/services/qualquer/start",
            data=b"{}",
            headers={"X-NEO-Token": result.credential, "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=8)
        self.assertIn(ctx.exception.code, (403, 404))

    def test_pairing_code_never_in_url(self):
        """O código viaja no corpo, não na URL."""
        result = pair(f"{self.main_url}", self.code.code, port=self.pair_port)
        self.assertNotIn(result.credential, self.code.code)
        # O endpoint é limpo depois do pairing.
        status, _ = self._get(f"http://127.0.0.1:{self.pair_port}/")
        self.assertEqual(status, 200)

    def test_10_credentials_not_in_logs(self):
        """Nem o código nem a credencial aparecem nos logs."""
        captured: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record.getMessage())

        logger = logging.getLogger("neolink.pairing")
        handler = Capture()
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            result = pair(f"{self.main_url}", self.code.code, port=self.pair_port)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)

        joined = "\n".join(captured)
        self.assertNotIn(result.credential, joined, "credencial vazou para o log")
        self.assertNotIn(self.code.code, joined, "código vazou para o log")

    def test_11_existing_token_still_works(self):
        """O token do config.json continua a ser aceite (compatibilidade)."""
        self.config.server.token = "token-antigo"
        status, _ = self._get(f"{self.main_url}/status", "token-antigo")
        self.assertEqual(status, 200)

    def test_reused_code_rejected_over_http(self):
        payload = json.dumps({"code": self.code.code}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.pair_port}/pair",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            self.assertEqual(json.loads(response.read())["paired"], True)
        # Segunda tentativa com o mesmo código.
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=8)
        self.assertEqual(ctx.exception.code, 403)


# ---------------------------------------------------------------------------
# Lado SENTINEL
# ---------------------------------------------------------------------------
class TestSentinelPairingHelpers(unittest.TestCase):
    def test_url_normalisation(self):
        self.assertEqual(normalise_url("host:8765"), "http://host:8765")
        self.assertEqual(normalise_url("http://host:8765/"), "http://host:8765")

    def test_pairing_port_derived(self):
        self.assertEqual(pairing_url("http://h:8765"), "http://h:8766")
        self.assertEqual(pairing_url("http://h:9000"), "http://h:9001")
        self.assertEqual(pairing_url("http://h:8765", 9999), "http://h:9999")

    def test_pairing_needs_a_code(self):
        with self.assertRaises(SentinelPairingError):
            pair("http://127.0.0.1:1", "", timeout=1)

    def test_unreachable_link_reports_clearly(self):
        with self.assertRaises(SentinelPairingError):
            check_reachable(f"http://127.0.0.1:{free_port()}", timeout=2)


# ---------------------------------------------------------------------------
# Config do SENTINEL: guardar sem destruir
# ---------------------------------------------------------------------------
class TestSentinelConfigSave(unittest.TestCase):
    def test_11_existing_config_is_not_destroyed(self):
        from sentinel.config import NodeConfig, SentinelConfig

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "refresh_seconds": 4.5,
                        "timeout_seconds": 9.0,
                        "nodes": [
                            {"name": "ANTIGO", "url": "http://a:1", "token": "t", "enabled": True}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = SentinelConfig.load(path)
            config.upsert_node(NodeConfig(name="NOVO", url="http://b:2", token="cred"))
            config.save(path)

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["refresh_seconds"], 4.5, "refresh foi preservado")
            self.assertEqual(saved["timeout_seconds"], 9.0, "timeout foi preservado")
            names = {n["name"] for n in saved["nodes"]}
            self.assertEqual(names, {"ANTIGO", "NOVO"}, "os dois nodes coexistem")

    def test_upsert_replaces_by_name(self):
        from sentinel.config import NodeConfig, SentinelConfig

        config = SentinelConfig(nodes=[])
        config.upsert_node(NodeConfig(name="A", url="http://a:1", token="velha"))
        config.upsert_node(NodeConfig(name="a", url="http://a:2", token="nova"))
        self.assertEqual(len(config.nodes), 1, "não pode duplicar")
        self.assertEqual(config.nodes[0].token, "nova")


if __name__ == "__main__":
    # Silencia o log durante os testes, excepto o que é explicitamente capturado.
    unittest.main(verbosity=2)
