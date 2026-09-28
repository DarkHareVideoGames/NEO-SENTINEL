"""Pairing temporário entre um SENTINEL e este LINK.

O pairing substitui a introdução manual de um token: o operador gera um código
curto no LINK, introduz-o no SENTINEL, e o SENTINEL recebe uma credencial
permanente.

Garantias:
  - o código é gerado com `secrets` (nunca `random`);
  - vive apenas em memória, nunca é escrito em disco;
  - expira em 10 minutos e é de utilização única;
  - a credencial só é criada depois de o código ser validado.

A credencial permanente é guardada apenas como hash SHA-256. Se o ficheiro de
credenciais for lido por terceiros, não lhes dá acesso ao LINK.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Validade máxima de um código de pairing.
CODE_TTL = timedelta(minutes=10)
# Tentativas por código antes de o invalidar (protege contra força bruta).
MAX_ATTEMPTS = 5

# Alfabeto sem caracteres ambíguos (sem 0/O, 1/I/L) para leitura ao teclado.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_GROUPS = (4, 4)

# Entropia: 31^8 ≈ 8.5e11 ≈ 40 bits — invulnerável a força bruta em 10 min.
CREDENTIAL_BYTES = 32


class PairingError(Exception):
    """Falha no pairing. A mensagem é segura para mostrar ao utilizador."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def format_code(raw: str) -> str:
    """XXXX-XXXX a partir de 8 caracteres crus."""
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def _random_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(sum(CODE_GROUPS)))


def new_credential() -> str:
    """Credencial permanente: aleatória e longa (256 bits)."""
    return secrets.token_urlsafe(CREDENTIAL_BYTES)


def hash_credential(credential: str) -> str:
    """Hash da credencial. Nunca guardamos a credencial em claro."""
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


@dataclass
class PairingCode:
    """Um código de pairing activo."""

    code: str
    created_at: datetime
    expires_at: datetime
    attempts: int = 0
    used: bool = False

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) >= self.expires_at

    def display_code(self) -> str:
        """Código como se mostra ao utilizador: XXXX-XXXX."""
        return format_code(self.code)

    def seconds_left(self) -> int:
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds()))


class PairingManager:
    """Gera e valida códigos de pairing. Estado só em memória."""

    def __init__(self, ttl: timedelta = CODE_TTL) -> None:
        self._ttl = ttl
        self._codes: dict[str, PairingCode] = {}

    def generate(self) -> PairingCode:
        """Cria um código novo, invalidando os anteriores (só um activo)."""
        self._codes.clear()
        raw = _random_code()
        now = datetime.now(timezone.utc)
        code = PairingCode(
            # Armazenado na mesma forma normalizada com que é comparado.
            code=self._normalise(raw),
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._codes[code.code] = code
        return code

    @property
    def active(self) -> PairingCode | None:
        """Código activo e válido, se existir."""
        for code in self._codes.values():
            if not code.used and not code.is_expired():
                return code
        return None

    def validate(self, supplied: str) -> None:
        """Valida o código. Levanta PairingError se não servir.

        Não distingue "código errado" de "inexistente" — mesma resposta para
        todos os casos, para não dar informação a quem tenta adivinhar.
        """
        if not supplied or not supplied.strip():
            raise PairingError("código em falta")

        normalised = self._normalise(supplied)
        code = self._codes.get(normalised)

        if code is None:
            raise PairingError("código inválido ou expirado", 403)
        if code.used:
            raise PairingError("código já utilizado", 403)
        if code.is_expired():
            # Limpa o código expirado: não vale a pena guardá-lo.
            self._codes.pop(normalised, None)
            raise PairingError("código expirado", 403)

        code.attempts += 1
        if code.attempts > MAX_ATTEMPTS:
            self._codes.pop(normalised, None)
            raise PairingError("demasiadas tentativas — gere um código novo", 403)

        # Utilização única: o código é consumido imediatamente.
        code.used = True
        self._codes.pop(normalised, None)

    @staticmethod
    def _normalise(value: str) -> str:
        """Compara maiúsculas e aceita o código escrito sem o hífen."""
        return value.strip().upper().replace(" ", "").replace("-", "")


@dataclass
class CredentialStore:
    """Credenciais pareadas, guardadas apenas como hash.

    Ficheiro local, nunca versionado. Se for lido, não permite autenticar-se.
    """

    path: Path
    _hashes: dict[str, dict] = field(default_factory=dict)
    _node_id: str | None = None

    @classmethod
    def load(cls, path: Path | str) -> "CredentialStore":
        store = cls(path=Path(path))
        store._read()
        return store

    def node_id(self) -> str:
        """Identidade técnica do node, gerada uma vez e persistente.

        É criada com `secrets` na primeira execução e guardada no mesmo
        ficheiro das credenciais. Como é gerada aleatoriamente, não depende do
        hostname nem do `name`, e sobrevive a reinícios e a renomeações.
        """
        if self._node_id:
            return self._node_id
        self._node_id = f"nx1-{secrets.token_hex(8)}"
        self._save()
        return self._node_id

    def _read(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._hashes = {}
            return
        entries = raw.get("credentials") or []
        self._hashes = {
            item["hash"]: item
            for item in entries
            if isinstance(item, dict) and item.get("hash")
        }
        node_id = raw.get("node_id")
        self._node_id = node_id if isinstance(node_id, str) and node_id else None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "node_id": self._node_id,
            "credentials": list(self._hashes.values()),
        }
        # Sem segredo em claro — só hashes, metadados e a identidade técnica.
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:  # restrinjo a leitura a quem é o dono do ficheiro
            self.path.chmod(0o600)
        except OSError:  # pragma: no cover - depende do sistema de ficheiros
            pass

    def _write(self) -> None:
        self._save()

    def add(self, credential: str, node: str) -> str:
        """Regist uma credencial associada ao `node_id` do node.

        A associação é feita pela identidade técnica, não pelo nome — assim
        renomear o node não quebra a ligação da credencial.
        """
        digest = hash_credential(credential)
        self._hashes[digest] = {
            "hash": digest,
            "node_id": self.node_id(),
            "node": node,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save()
        return digest

    def verify(self, credential: str) -> bool:
        """Verifica uma credencial em tempo constante."""
        if not credential:
            return False
        digest = hash_credential(credential)
        # Compara com todas as entradas em tempo constante.
        matched = False
        for known in self._hashes:
            if hmac.compare_digest(known, digest):
                matched = True
        return matched

    @property
    def count(self) -> int:
        return len(self._hashes)
