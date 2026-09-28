"""Testes do pairing do SENTINEL: validação do IP, endpoints e identidade.

Nenhum teste abre uma porta real nem contacta a rede: o HTTP é substituído por
um duplo de teste.

    python test_pairing_ux.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sentinel import pairing  # noqa: E402
from sentinel.config import NodeConfig, SentinelConfig  # noqa: E402

GOOD_IP = "100.69.16.82"

# Nomes e IPs de máquina que nunca podem estar no código.
FORBIDDEN = ("MASTER", "FEEDBACKAI", "DEFUNCTUMNOCTIS")


class TestTailscaleIPValidation(unittest.TestCase):
    """1, 2, 3 — IP válido, inválido e URL onde se espera apenas IP."""

    def test_valid_tailscale_ip(self):
        self.assertEqual(pairing.validate_tailscale_ip(GOOD_IP), GOOD_IP)

    def test_valid_ip_is_stripped(self):
        self.assertEqual(pairing.validate_tailscale_ip(f"  {GOOD_IP}  "), GOOD_IP)

    def test_other_tailscale_ips(self):
        for ip in ("100.64.0.1", "100.127.255.254", "10.0.0.5"):
            self.assertEqual(pairing.validate_tailscale_ip(ip), ip)

    def test_url_where_ip_expected_is_rejected(self):
        """Uma URL colada no campo do IP tem de ser recusada, não convertida."""
        for bad in (
            f"http://{GOOD_IP}:8765",
            f"https://{GOOD_IP}",
            f"{GOOD_IP}:8765",
            f"http://{GOOD_IP}/",
        ):
            with self.assertRaises(pairing.InvalidTailscaleIP, msg=bad):
                pairing.validate_tailscale_ip(bad)

    def test_invalid_ip_is_rejected(self):
        for bad in ("", "   ", "abc", "999.1.1.1", "100.69.16", "100.69.16.82.5",
                    "not-an-ip", "100.69.16.82/pair"):
            with self.assertRaises(pairing.InvalidTailscaleIP, msg=bad):
                pairing.validate_tailscale_ip(bad)

    def test_error_message_is_user_friendly(self):
        """A mensagem explica o formato, sem detalhes técnicos."""
        with self.assertRaises(pairing.InvalidTailscaleIP) as ctx:
            pairing.validate_tailscale_ip(f"http://{GOOD_IP}:8765")
        message = str(ctx.exception)
        self.assertIn("Invalid Tailscale IP.", message)
        self.assertIn("Enter only the Tailscale IP", message)
        self.assertIn("100.", message)
        # Sem portas nem URLs técnicas na mensagem.
        self.assertNotIn("8765", message)
        self.assertNotIn("8766", message)

    def test_invalid_ip_error_is_a_pairing_error(self):
        """Continua a ser apanhado pelo mesmo `except PairingError`."""
        self.assertTrue(
            issubclass(pairing.InvalidTailscaleIP, pairing.PairingError)
        )


class TestEndpoints(unittest.TestCase):
    """4, 5 — as portas são calculadas, nunca pedidas ao utilizador."""

    def test_link_endpoint_uses_internal_port(self):
        self.assertEqual(pairing.link_endpoint(GOOD_IP), f"http://{GOOD_IP}:8765")

    def test_pairing_endpoint_uses_internal_port(self):
        self.assertEqual(
            pairing.pairing_endpoint(GOOD_IP), f"http://{GOOD_IP}:8766"
        )

    def test_endpoints_reject_invalid_ip(self):
        for bad in ("nope", f"http://{GOOD_IP}"):
            with self.assertRaises(pairing.InvalidTailscaleIP):
                pairing.link_endpoint(bad)
            with self.assertRaises(pairing.InvalidTailscaleIP):
                pairing.pairing_endpoint(bad)

    def test_ports_match_the_link_defaults(self):
        """As portas aqui devem coincidir com as do LINK (não mudámos)."""
        link_config = HERE / "link" / "neolink" / "config.py"
        text = link_config.read_text(encoding="utf-8")
        self.assertIn("8765", text, "o LINK deixou de usar a porta 8765")
        self.assertEqual(pairing.LINK_PORT, 8765)
        self.assertEqual(pairing.PAIRING_PORT, 8766)


class TestPairingSuccess(unittest.TestCase):
    """6, 7, 8, 12, 13 — pairing com nome, sem nome e sem código na URL."""

    def _fake_post(self, payload, captured):
        def _post(url, body, timeout):
            captured["url"] = url
            captured["body"] = body
            return {
                "paired": True,
                "credential": "cred-abc",
                "node": {
                    "node_id": "nx1-0123456789abcdef",
                    "name": "HOSTNAME-DO-NODE",
                    "hostname": "HOSTNAME-DO-NODE",
                },
            }
        return _post

    def test_successful_pairing(self):
        captured: dict = {}
        with mock.patch.object(
            pairing, "_post", side_effect=self._fake_post(None, captured)
        ):
            result = pairing.pair_by_ip(GOOD_IP, "ABCD-EFGH")

        self.assertEqual(result.credential, "cred-abc")
        self.assertEqual(result.node_id, "nx1-0123456789abcdef")
        self.assertEqual(result.hostname, "HOSTNAME-DO-NODE")

    def test_custom_station_name_is_used(self):
        captured: dict = {}
        with mock.patch.object(
            pairing, "_post", side_effect=self._fake_post(None, captured)
        ):
            result = pairing.pair_by_ip(GOOD_IP, "ABCD-EFGH", name="RENDER-01")
        self.assertEqual(result.name, "RENDER-01")
        self.assertEqual(captured["body"]["name"], "RENDER-01")

    def test_empty_name_falls_back_to_hostname(self):
        """Campo vazio = comportamento canónico: o nome passa a ser o hostname."""
        captured: dict = {}
        with mock.patch.object(
            pairing, "_post", side_effect=self._fake_post(None, captured)
        ):
            result = pairing.pair_by_ip(GOOD_IP, "ABCD-EFGH", name="")
        # O LINK decide: devolve o hostname, e o SENTINEL aceita-o.
        self.assertEqual(result.name, "HOSTNAME-DO-NODE")
        self.assertNotIn("name", captured["body"])

    def test_name_is_never_used_for_node_id(self):
        """O node_id vem sempre do LINK, nunca do que o utilizador escreveu."""
        captured: dict = {}
        with mock.patch.object(
            pairing, "_post", side_effect=self._fake_post(None, captured)
        ):
            result = pairing.pair_by_ip(GOOD_IP, "ABCD-EFGH", name="RENDER-01")
        self.assertEqual(result.node_id, "nx1-0123456789abcdef")
        self.assertNotIn(result.node_id, ("RENDER-01", GOOD_IP))

    def test_pairing_code_never_appears_in_url(self):
        """12 — o código viaja no corpo, nunca no endpoint."""
        captured: dict = {}
        with mock.patch.object(
            pairing, "_post", side_effect=self._fake_post(None, captured)
        ):
            pairing.pair_by_ip(GOOD_IP, "ABCD-EFGH")
        self.assertNotIn("ABCD-EFGH", captured["url"])
        self.assertNotIn("ABCD", captured["url"])
        self.assertEqual(captured["body"]["code"], "ABCD-EFGH")

    def test_empty_code_is_rejected(self):
        for empty in ("", "   "):
            with self.assertRaises(pairing.PairingError):
                pairing.pair_by_ip(GOOD_IP, empty)

    def test_no_internal_port_in_result(self):
        """13 — o resultado não transporta detalhes de rede."""
        captured: dict = {}
        with mock.patch.object(
            pairing, "_post", side_effect=self._fake_post(None, captured)
        ):
            result = pairing.pair_by_ip(GOOD_IP, "ABCD-EFGH", name="STATION")
        blob = json.dumps(result.node)
        self.assertNotIn("8765", blob)
        self.assertNotIn("8766", blob)
        self.assertNotIn("http://", blob)


class TestPairingErrors(unittest.TestCase):
    """Mensagens orientadas ao utilizador."""

    def test_unreachable_node_message(self):
        """O erro de rede traduz-se numa mensagem sem detalhes técnicos."""

        def boom(request, timeout=None):
            raise urllib.error.URLError("timed out")

        with mock.patch.object(pairing.urllib.request, "urlopen", side_effect=boom):
            with self.assertRaises(pairing.PairingError) as ctx:
                pairing.pair_by_ip(GOOD_IP, "ABCD-EFGH")
        self.assertEqual(str(ctx.exception), "Unable to reach Tailscale node")
        # Sem URL nem portas técnicas.
        self.assertNotIn("8765", str(ctx.exception))
        self.assertNotIn("8766", str(ctx.exception))

    def test_expired_code_message(self):
        """Um 403 do LINK chega como JSON; traduz-se para linguagem simples."""
        import io

        body = io.BytesIO(
            json.dumps({"error": "pairing_failed", "detail": "codigo expirado"}).encode()
        )
        error = urllib.error.HTTPError("http://x", 403, "Forbidden", {}, body)

        def boom(request, timeout=None):
            raise error

        with mock.patch.object(pairing.urllib.request, "urlopen", side_effect=boom):
            with self.assertRaises(pairing.PairingError) as ctx:
                pairing.pair_by_ip(GOOD_IP, "ABCD-EFGH")
        self.assertIn("expired", str(ctx.exception).lower())
        # Sem detalhes técnicos expostos.
        self.assertNotIn("403", str(ctx.exception))
        self.assertNotIn("8766", str(ctx.exception))

    def test_too_many_attempts_message(self):
        """A mensagem do LINK é traduzida para linguagem do utilizador."""
        message = pairing.friendly_pairing_error("demasiadas tentativas")
        self.assertIn("Too many attempts", message)

    def test_expired_or_invalid_code_is_friendly(self):
        for detail in ("código expirado", "código inválido", "expired"):
            message = pairing.friendly_pairing_error(detail)
            self.assertIn("rejected or expired", message)


class TestConfigPersistence(unittest.TestCase):
    """7, 8, 9, 10, 11 — identidade guardada e compatibilidade."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"

    def test_node_stores_canonical_identity(self):
        node = NodeConfig(
            name="STATION",
            url=pairing.link_endpoint(GOOD_IP),
            token="cred",
            node_id="nx1-0123456789abcdef",
            hostname="REAL-HOSTNAME",
            tailscale_ip=GOOD_IP,
        )
        data = node.to_dict()
        self.assertEqual(data["node_id"], "nx1-0123456789abcdef")
        self.assertEqual(data["hostname"], "REAL-HOSTNAME")
        self.assertEqual(data["tailscale_ip"], GOOD_IP)
        self.assertEqual(data["token"], "cred")

    def test_pairing_code_is_never_stored(self):
        node = NodeConfig(
            name="STATION", url=pairing.link_endpoint(GOOD_IP), token="cred"
        )
        blob = json.dumps(node.to_dict())
        self.assertNotIn("ABCD", blob)
        self.assertNotIn("code", blob.lower())

    def test_renaming_keeps_node_id_and_credential(self):
        """9 e 10 — mudar o nome não cria um node novo."""
        config = SentinelConfig(nodes=[])
        config.upsert_node(NodeConfig(
            name="NOME-ANTIGO", url=pairing.link_endpoint(GOOD_IP),
            token="cred-1", node_id="nx1-fixo", hostname="REAL",
            tailscale_ip=GOOD_IP,
        ))
        config.upsert_node(NodeConfig(
            name="NOME-NOVO", url=pairing.link_endpoint(GOOD_IP),
            token="cred-1", node_id="nx1-fixo", hostname="REAL",
            tailscale_ip=GOOD_IP,
        ))
        self.assertEqual(len(config.nodes), 1, "renomear criou um node duplicado")
        self.assertEqual(config.nodes[0].name, "NOME-NOVO")
        self.assertEqual(config.nodes[0].node_id, "nx1-fixo")
        self.assertEqual(config.nodes[0].token, "cred-1")

    def test_different_node_id_creates_new_node(self):
        config = SentinelConfig(nodes=[])
        config.upsert_node(NodeConfig(
            name="A", url=pairing.link_endpoint(GOOD_IP), node_id="nx1-um"))
        config.upsert_node(NodeConfig(
            name="B", url=pairing.link_endpoint("100.1.1.2"), node_id="nx1-dois"))
        self.assertEqual(len(config.nodes), 2)

    def test_old_config_with_url_still_loads(self):
        """11 — configurações antigas não se perdem."""
        self.path.write_text(json.dumps({
            "nodes": [{
                "name": "VELHO",
                "url": "http://100.69.16.82:8765",
                "token": "cred-antigo",
                "enabled": True,
            }],
            "refresh_seconds": 2.0,
        }), encoding="utf-8")
        config = SentinelConfig.load(self.path)
        self.assertEqual(len(config.nodes), 1)
        self.assertEqual(config.nodes[0].name, "VELHO")
        self.assertEqual(config.nodes[0].url, "http://100.69.16.82:8765")
        self.assertEqual(config.nodes[0].token, "cred-antigo")
        self.assertIsNone(config.nodes[0].node_id)

    def test_old_config_round_trips(self):
        self.path.write_text(json.dumps({
            "nodes": [{"name": "VELHO", "url": "http://1.2.3.4:8765", "token": "t"}],
        }), encoding="utf-8")
        config = SentinelConfig.load(self.path)
        config.save(self.path)
        again = SentinelConfig.load(self.path)
        self.assertEqual(again.nodes[0].url, "http://1.2.3.4:8765")
        self.assertEqual(again.nodes[0].token, "t")

    def test_identity_fields_are_optional_in_output(self):
        """Nodes antigos continuam a serializar só o essencial."""
        node = NodeConfig(name="VELHO", url="http://1.2.3.4:8765", token="t")
        data = node.to_dict()
        self.assertNotIn("node_id", data)
        self.assertNotIn("hostname", data)
        self.assertNotIn("tailscale_ip", data)

    def test_config_with_only_tailscale_ip_is_accepted(self):
        self.path.write_text(json.dumps({
            "nodes": [{"name": "NOVO", "tailscale_ip": GOOD_IP}],
        }), encoding="utf-8")
        config = SentinelConfig.load(self.path)
        self.assertEqual(config.nodes[0].url, f"http://{GOOD_IP}:8765")

    def test_node_without_url_or_ip_is_rejected(self):
        self.path.write_text(json.dumps({
            "nodes": [{"name": "SEM-ENDERECO"}],
        }), encoding="utf-8")
        with self.assertRaises(ValueError):
            SentinelConfig.load(self.path)


class TestNoHardcodedEnvironment(unittest.TestCase):
    """O pairing não embute nomes nem IPs de máquina."""

    def test_no_machine_names(self):
        for name in ("sentinel/pairing.py", "sentinel/config.py", "neo_sentinel.py"):
            text = (HERE / name).read_text(encoding="utf-8")
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, text,
                                 f"{name} não deve conter {forbidden!r}")

    def test_only_example_ip_is_present(self):
        """Só o IP de exemplo e a faixa de rede podem estar no código."""
        text = (HERE / "sentinel" / "pairing.py").read_text(encoding="utf-8")
        import re

        found = set(re.findall(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text))
        # 100.64.0.0 é a faixa do Tailscale, não a máquina de ninguém.
        allowed = {pairing.TAILSCALE_EXAMPLE, "100.64.0.0"}
        self.assertEqual(
            found, allowed,
            f"IPs inesperados no código: {found - allowed}",
        )

    def test_node_id_is_never_derived_from_ip_or_name(self):
        """O node_id só pode vir do LINK."""
        text = (HERE / "sentinel" / "pairing.py").read_text(encoding="utf-8")
        self.assertNotIn("nx1-", text, "o SENTINEL não deve gerar node_id")


class TestUserInterface(unittest.TestCase):
    """A UI normal não mostra portas nem URLs."""

    def test_no_internal_ports_in_cli_text(self):
        text = (HERE / "neo_sentinel.py").read_text(encoding="utf-8")
        # As portas podem existir como constante, mas nunca numa mensagem.
        for line in text.splitlines():
            if "8765" in line or "8766" in line:
                self.fail(f"porta interna exposta na UI: {line.strip()}")

    def test_prompts_are_user_facing(self):
        text = (HERE / "neo_sentinel.py").read_text(encoding="utf-8")
        self.assertIn("IP Tailscale:", text)
        self.assertIn("Código:", text)
        self.assertIn("Nome da estação:", text)
        self.assertNotIn("LINK URL", text)

    def test_link_cli_pairing_still_exists(self):
        """`neo-link --pair` continua disponível do lado do LINK."""
        entry = HERE / "link" / "neo_link.py"
        self.assertIn("--pair", entry.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
