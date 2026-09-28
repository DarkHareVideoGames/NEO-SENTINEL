"""Testes do NEO//LINK — configuração, parsing e protocolo.

Corre com:  python link/test_link.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Corre também quando executado a partir da raiz do repositório.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from neolink.config import LinkConfig, ServiceConfig
from neolink.gpu import _parse_nvidia_csv
from neolink.identity import build_identity, detect_platform
from neolink.server import LinkServer
from neolink.services import (
    COMFYUI_REQUIRED_ARGS,
    ComfyUIService,
    OllamaService,
    Service,
    build_services,
)


def write_config(raw: dict) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    handle.write(json.dumps(raw))
    handle.close()
    return Path(handle.name)


class TestConfig(unittest.TestCase):
    def test_loads_master_like_config(self):
        config = LinkConfig.from_dict(
            {
                "node": {"name": "MASTER"},
                "server": {"host": "127.0.0.1", "port": 8765},
                "services": [
                    {"type": "comfyui", "name": "ComfyUI", "port": 8188},
                    {"type": "ollama", "name": "Ollama", "port": 11434},
                ],
            }
        )
        self.assertEqual(config.name, "MASTER")
        self.assertEqual(len(config.services), 2)
        self.assertFalse(config.server.allow_control)  # seguro por omissão

    def test_name_is_required(self):
        with self.assertRaises(ValueError):
            LinkConfig.from_dict({"node": {}})

    def test_unknown_service_type_rejected(self):
        with self.assertRaises(ValueError):
            LinkConfig.from_dict(
                {"node": {"name": "X"}, "services": [{"type": "inexistente", "name": "A"}]}
            )

    def test_unknown_key_rejected(self):
        """Uma chave errada é quase sempre um erro de digitação — falhar cedo."""
        with self.assertRaises(ValueError):
            LinkConfig.from_dict(
                {
                    "node": {"name": "X"},
                    "services": [{"type": "ollama", "name": "A", "potr": 11434}],
                }
            )

    def test_duplicate_service_names_rejected(self):
        with self.assertRaises(ValueError):
            LinkConfig.from_dict(
                {
                    "node": {"name": "X"},
                    "services": [
                        {"type": "ollama", "name": "Dup"},
                        {"type": "comfyui", "name": "dup"},
                    ],
                }
            )

    def test_missing_file_raises_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            LinkConfig.load("nao-existe.json")

    def test_load_without_argument_uses_default(self):
        """`load()` sem argumento usa o config.json ao lado do pacote.

        Num repositório público esse ficheiro não existe — é estado local da
        máquina. O teste valida o caminho escolhido e aceita a ausência.
        """
        default = Path(__file__).resolve().parent / "config.json"
        with self.assertRaises(FileNotFoundError) as ctx:
            LinkConfig.load()
        self.assertIn(str(default), str(ctx.exception))

    def test_example_config_is_valid(self):
        """O config.example.json versionado tem de ser carregável."""
        example = Path(__file__).resolve().parent / "config.example.json"
        self.assertTrue(example.is_file(), "config.example.json em falta")
        config = LinkConfig.load(example)
        self.assertTrue(config.name)
        self.assertGreaterEqual(config.server.port, 1)


class TestIdentity(unittest.TestCase):
    def test_identity_uses_configured_name_not_ip(self):
        identity = build_identity("MASTER")
        self.assertEqual(identity.name, "MASTER")
        self.assertEqual(identity.agent, "NEO//LINK")
        self.assertTrue(identity.hostname)
        self.assertIn(identity.platform, ("windows", "linux", "macos", "android"))

    def test_identity_is_serialisable(self):
        payload = build_identity("FEEDBACKAI").to_dict()
        self.assertEqual(payload["name"], "FEEDBACKAI")
        json.dumps(payload)  # tem de ser serializável para a API

    def test_detect_platform_returns_known_value(self):
        self.assertIsInstance(detect_platform(), str)


class TestGpuParsing(unittest.TestCase):
    def test_parses_real_nvidia_row(self):
        gpus = _parse_nvidia_csv("NVIDIA GeForce RTX 4080 SUPER, 42, 58, 8601, 16376")
        self.assertEqual(len(gpus), 1)
        gpu = gpus[0]
        self.assertEqual(gpu["model"], "NVIDIA GeForce RTX 4080 SUPER")
        self.assertEqual(gpu["usage_percent"], 42.0)
        self.assertEqual(gpu["temperature_c"], 58.0)
        self.assertEqual(gpu["vram_total_gb"], 16.0)

    def test_vram_percent_computed(self):
        gpu = _parse_nvidia_csv("GPU, 0, 0, 8000, 16000")[0]
        self.assertAlmostEqual(gpu["vram_percent"], 50.0)

    def test_na_fields_become_none(self):
        gpu = _parse_nvidia_csv("Intel Arc, [N/A], [Not Supported], [N/A], 8192")[0]
        self.assertIsNone(gpu["usage_percent"])
        self.assertIsNone(gpu["temperature_c"])
        self.assertIsNone(gpu["vram_percent"])

    def test_no_gpu_returns_empty(self):
        self.assertEqual(_parse_nvidia_csv(""), [])
        self.assertEqual(_parse_nvidia_csv("\n\n"), [])

    def test_no_raw_line_exposed(self):
        """A resposta é sempre estruturada, nunca a linha crua."""
        gpu = _parse_nvidia_csv("NVIDIA GeForce RTX 4080 SUPER, 42, 58, 8601, 16376")[0]
        self.assertIsInstance(gpu, dict)
        for value in gpu.values():
            if isinstance(value, str):
                self.assertNotIn(",", value, "um valor não deve conter a linha CSV crua")


class TestServices(unittest.TestCase):
    def test_registry_builds_right_types(self):
        services = build_services(
            [
                ServiceConfig(type="comfyui", name="ComfyUI", port=8188),
                ServiceConfig(type="ollama", name="Ollama", port=11434),
                ServiceConfig(type="process", name="Outro", port=9000),
            ]
        )
        self.assertIsInstance(services[0], ComfyUIService)
        self.assertIsInstance(services[1], OllamaService)
        self.assertIsInstance(services[2], Service)

    def test_comfyui_command_keeps_required_flags(self):
        """--enable-manager e --listen 0.0.0.0 são obrigatórios."""
        service = ComfyUIService(
            ServiceConfig(
                type="comfyui",
                name="ComfyUI",
                path="C:\\ComfyUI",
                python="C:\\ComfyUI\\.venv\\Scripts\\python.exe",
                port=8188,
                args=["--enable-manager", "--listen", "0.0.0.0"],
            )
        )
        executable, args, cwd = service._build_command()
        self.assertEqual(executable, "C:\\ComfyUI\\.venv\\Scripts\\python.exe")
        self.assertEqual(args[0], "main.py")
        self.assertEqual(cwd, "C:\\ComfyUI")
        for flag in COMFYUI_REQUIRED_ARGS:
            self.assertIn(flag, args)

    def test_comfyui_completes_missing_flags(self):
        """Se a config perder as flags, o LINK volta a injectar."""
        service = ComfyUIService(
            ServiceConfig(
                type="comfyui",
                name="ComfyUI",
                path="C:\\ComfyUI",
                python="C:\\ComfyUI\\.venv\\Scripts\\python.exe",
                args=[],
            )
        )
        _, args, _ = service._build_command()
        for flag in COMFYUI_REQUIRED_ARGS:
            self.assertIn(flag, args)

    def test_api_url_uses_loopback(self):
        service = ComfyUIService(ServiceConfig(type="comfyui", name="ComfyUI", port=8188))
        self.assertEqual(service._api_url(), "http://127.0.0.1:8188/system_stats")

    def test_disabled_service_reports_disabled(self):
        service = Service(ServiceConfig(type="process", name="X", enabled=False))
        self.assertEqual(service.probe().state, "DISABLED")

    def test_status_never_leaks_paths(self):
        """O SENTINEL não deve receber paths nem comandos."""
        service = ComfyUIService(
            ServiceConfig(
                type="comfyui",
                name="ComfyUI",
                path="C:\\ComfyUI",
                python="C:\\ComfyUI\\.venv\\Scripts\\python.exe",
                port=8188,
            )
        )
        payload = json.dumps(service.probe().to_dict())
        self.assertNotIn("C:\\\\ComfyUI", payload)
        self.assertNotIn("python.exe", payload)


class TestProtocol(unittest.TestCase):
    """Testa os payloads do protocolo sem abrir sockets."""

    def setUp(self):
        self.config = LinkConfig.from_dict(
            {
                "node": {"name": "TESTNODE"},
                "services": [{"type": "ollama", "name": "Ollama", "port": 11434}],
            }
        )
        from neolink.server import LinkService

        self.link = LinkService(self.config, build_services(self.config.services))

    def test_status_payload_shape(self):
        payload = self.link.payload_status()
        self.assertEqual(payload["node"]["name"], "TESTNODE")
        self.assertEqual(payload["agent"], "NEO//LINK")
        json.dumps(payload)

    def test_hardware_payload_has_expected_keys(self):
        payload = self.link.payload_hardware()
        for key in ("cpu", "ram", "disks", "network"):
            self.assertIn(key, payload)
        self.assertIn("percent", payload["cpu"])
        self.assertIn("percent", payload["ram"])

    def test_gpu_payload_shape(self):
        payload = self.link.payload_gpu()
        self.assertIn("gpus", payload)
        self.assertEqual(payload["count"], len(payload["gpus"]))

    def test_services_payload_shape(self):
        payload = self.link.payload_services()
        self.assertEqual(len(payload["services"]), 1)
        service = payload["services"][0]
        for key in ("name", "state", "pid", "port", "api_online", "controllable"):
            self.assertIn(key, service)

    def test_find_service_case_insensitive(self):
        self.assertIsNotNone(self.link.find_service("ollama"))
        self.assertIsNotNone(self.link.find_service("OLLAMA"))
        self.assertIsNone(self.link.find_service("nao-existe"))

    def test_control_disabled_by_default(self):
        self.assertFalse(self.config.server.allow_control)


if __name__ == "__main__":
    unittest.main(verbosity=2)
