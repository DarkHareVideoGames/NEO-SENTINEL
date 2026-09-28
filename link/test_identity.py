"""Testes da identidade canónica do node: node_id, name e hostname.

Estes testes asseguram que a identidade é genérica e estável.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from neolink.config import LinkConfig  # noqa: E402
from neolink.identity import (  # noqa: E402
    build_identity,
    hostname,
    logical_name,
)
from neolink.pairing import CredentialStore  # noqa: E402
from neolink.server import LinkService  # noqa: E402


def make_config(name=None):
    node = {"name": name} if name is not None else {}
    return LinkConfig.from_dict(
        {"node": node, "server": {"port": 8765}, "services": []}
    )


class TestNodeId(unittest.TestCase):
    """O node_id é gerado, persistente e independente do hostname e do name."""

    def test_node_id_is_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CredentialStore.load(Path(tmp) / "credentials.json")
            node_id = store.node_id()
            self.assertTrue(node_id)
            self.assertTrue(node_id.startswith("nx1-"), node_id)

    def test_node_id_is_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.json"
            first = CredentialStore.load(path).node_id()
            # Simula um reinício do LINK: nova instância, mesmo ficheiro.
            second = CredentialStore.load(path).node_id()
            self.assertEqual(first, second, "o node_id mudou entre arranques")

    def test_node_id_does_not_depend_on_hostname(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CredentialStore.load(Path(tmp) / "credentials.json")
            node_id = store.node_id()
            with mock.patch("platform.node", return_value="OUTRA-MAQUINA"):
                identity = build_identity(None, node_id)
            self.assertEqual(identity.node_id, node_id)

    def test_node_id_is_persisted_in_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.json"
            node_id = CredentialStore.load(path).node_id()
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["node_id"], node_id)

    def test_two_nodes_get_different_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = CredentialStore.load(Path(tmp) / "a.json").node_id()
            b = CredentialStore.load(Path(tmp) / "b.json").node_id()
            self.assertNotEqual(a, b)


class TestLogicalName(unittest.TestCase):
    """O `name` é configurável; sem configuração usa o hostname."""

    def test_configured_name_is_used(self):
        with mock.patch("platform.node", return_value="REALHOST"):
            identity = build_identity("MEU-NOME", "nx1-abc")
        self.assertEqual(identity.name, "MEU-NOME")
        self.assertEqual(identity.hostname, "REALHOST")

    def test_without_name_falls_back_to_hostname(self):
        with mock.patch("platform.node", return_value="REALHOST"):
            self.assertEqual(logical_name(None), "REALHOST")
            self.assertEqual(logical_name(""), "REALHOST")
            self.assertEqual(logical_name("   "), "REALHOST")
            identity = build_identity(None, "nx1-abc")
        self.assertEqual(identity.name, "REALHOST")
        self.assertEqual(identity.hostname, "REALHOST")

    def test_hostname_comes_from_the_system(self):
        with mock.patch("platform.node", return_value="HOST-DO-SISTEMA"):
            self.assertEqual(hostname(), "HOST-DO-SISTEMA")

    def test_renaming_does_not_change_node_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CredentialStore.load(Path(tmp) / "credentials.json")
            node_id = store.node_id()
            with mock.patch("platform.node", return_value="HOST"):
                before = build_identity("ANTIGO", node_id)
                after = build_identity("NOVO", node_id)
            self.assertNotEqual(before.name, after.name, "o name devia mudar")
            self.assertEqual(before.node_id, after.node_id, "o node_id não pode mudar")
            self.assertEqual(before.hostname, after.hostname)


class TestConfigAcceptsMissingName(unittest.TestCase):
    def test_name_is_optional_in_config(self):
        config = make_config(None)
        self.assertIsNone(config.name)

    def test_name_is_read_when_present(self):
        self.assertEqual(make_config("CONFIGURADO").name, "CONFIGURADO")

    def test_empty_name_becomes_none(self):
        self.assertIsNone(make_config("").name)


class TestIdentityContract(unittest.TestCase):
    """Os três campos canónicos são expostos na API."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CredentialStore.load(Path(self.tmp.name) / "credentials.json")
        self.link = LinkService(make_config("UM-NOME"), [], self.store)

    def test_identity_has_the_three_canonical_fields(self):
        payload = self.link.identity.to_dict()
        for field in ("node_id", "name", "hostname"):
            self.assertIn(field, payload)
            self.assertTrue(payload[field], f"{field} vazio")

    def test_status_exposes_identity(self):
        payload = self.link.payload_status()["node"]
        self.assertEqual(payload["node_id"], self.store.node_id())
        self.assertEqual(payload["name"], "UM-NOME")
        self.assertTrue(payload["hostname"])

    def test_hardware_exposes_identity(self):
        payload = self.link.payload_hardware()["node"]
        self.assertEqual(payload["node_id"], self.store.node_id())

    def test_identity_is_serialisable(self):
        json.dumps(self.link.identity.to_dict())


class TestNoHardcodedNames(unittest.TestCase):
    """Nenhum nome de máquina pode estar embebido no código."""

    FORBIDDEN = ("MASTER", "FEEDBACKAI", "DEFUNCTUMNOCTIS", "DefunctumNoctis")

    def test_no_machine_names_in_runtime_code(self):
        root = Path(__file__).resolve().parent / "neolink"
        offenders = []
        for source in root.glob("*.py"):
            text = source.read_text(encoding="utf-8")
            for name in self.FORBIDDEN:
                if name in text:
                    offenders.append(f"{source.name}: {name}")
        self.assertEqual(offenders, [], f"nomes de maquina no codigo: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
