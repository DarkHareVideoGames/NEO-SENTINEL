#!/usr/bin/env bash
# ============================================================================
#  NEO//LINK — instalador para Linux, macOS e Termux
# ============================================================================
#
#  Instala o agente NEO//LINK em ~/.neo-x1/link/ e cria o comando `neo-link`.
#  Toda a lógica de instalação vive em bootstrap.py, partilhada com o Windows.
#
#  Instalar (recomendado — descarrega primeiro, para poder rever):
#      curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.sh -o install.sh
#      bash install.sh --name MEU-NODE
#
#  Opções:
#      --name NOME      nome lógico do node. Sem ele usa-se o hostname.
#      --host HOST      endereço onde escuta (omissão: config)
#      --port PORTA     porta (omissão: 8765)
#      --dir CAMINHO    directório de instalação
#      --check          diagnostica
#      --update         actualiza o runtime (preserva a configuração)
#      --uninstall      remove
#      --yes            não faz perguntas
#
#  Compatibilidade:
#      Linux     TESTED
#      macOS     SUPPORTED   (Intel e Apple Silicon; sem Homebrew)
#      Termux     SUPPORTED   (sem root, sem systemd, sem sudo)
#      Windows   NAO — use link/install.ps1, em PowerShell nativo. No Windows
#                não é preciso instalar Bash, WSL ou Git.
#
#  Segurança: instala só na pasta do utilizador. Não usa sudo, não altera a
#  firewall, não abre portas no router e não activa o controlo remoto.
# ============================================================================

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link"

NEO_DIR="${NEO_DIR:-${HOME}/.neo-x1}"
LINK_DIR="${LINK_DIR:-$NEO_DIR/link}"

ARGS=()
CHECK_ONLY=0

die()  { printf '\nerro: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }

usage() { sed -n '2,30p' "$0"; exit 0; }

# ---------------------------------------------------------------------------
# Plataforma (só para mensagens; a lógica é comum)
# ---------------------------------------------------------------------------
detect_platform() {
    if [ -n "${PREFIX:-}" ] && [ -d "/data/data/com.termux" ]; then
        echo "termux"
    elif [ "$(uname -s 2>/dev/null || echo unknown)" = "Darwin" ]; then
        echo "macos"
    elif [ "$(uname -s 2>/dev/null || echo unknown)" = "Linux" ]; then
        echo "linux"
    else
        echo "posix"
    fi
}

PLATFORM="$(detect_platform)"

# ---------------------------------------------------------------------------
# Python 3.9+ com venv
# ---------------------------------------------------------------------------
find_python() {
    local candidate
    for candidate in python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if "$candidate" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

install_hint() {
    case "$PLATFORM" in
        termux) printf '    Termux:  pkg install python\n' ;;
        macos)  printf '    macOS:   https://www.python.org/downloads/ ou xcode-select --install\n' ;;
        *)      printf '    Linux:   a sua distribuicao, ou https://www.python.org/downloads/\n' ;;
    esac
}

# ---------------------------------------------------------------------------
# Opções
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --name)      ARGS+=(--name "${2:-}"); shift 2 ;;
        --host)      ARGS+=(--host "${2:-}"); shift 2 ;;
        --port)      ARGS+=(--port "${2:-}"); shift 2 ;;
        --dir)       LINK_DIR="${2:-}"; shift 2 ;;
        --check)     ARGS+=(--check); CHECK_ONLY=1; shift ;;
        --update)    ARGS+=(--update); shift ;;
        --uninstall) ARGS+=(--uninstall); CHECK_ONLY=1; shift ;;
        --yes|-y)    ARGS+=(--yes); shift ;;
        -h|--help)   usage ;;
        *) die "opcao desconhecida: $1 (use --help)" ;;
    esac
done

BOOTSTRAP="$LINK_DIR/bootstrap.py"

# ---------------------------------------------------------------------------
# Descarregar o bootstrap partilhado
# ---------------------------------------------------------------------------
fetch() {
    local url="$1" dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$dest"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$dest" "$url"
    else
        printf '\nerro: e preciso curl ou wget para descarregar.\n' >&2
        install_hint
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
info "NEO//LINK — instalacao ($PLATFORM)"
info "destino: $LINK_DIR"

PY_BIN="$(find_python)" || {
    info ""
    info "Python 3.9+ (com venv) nao encontrado."
    install_hint
    die "instale o Python e volte a correr"
}
info "Python: $($PY_BIN -V 2>&1)"

mkdir -p "$LINK_DIR"
fetch "$REPO_RAW/bootstrap.py" "$BOOTSTRAP" \
    || die "nao foi possivel obter o bootstrap de $REPO_RAW"

set +e
"$PY_BIN" "$BOOTSTRAP" \
    --base-url "$REPO_RAW" \
    --root "$LINK_DIR" \
    ${ARGS[@]+"${ARGS[@]}"}
STATUS=$?
set -e

# ---------------------------------------------------------------------------
# Comando neo-link
# ---------------------------------------------------------------------------
if [ "$STATUS" -eq 0 ] && [ "$CHECK_ONLY" -eq 0 ]; then
    VENV_PY="$LINK_DIR/.venv/bin/python"
    if [ -x "$VENV_PY" ]; then
        BIN_DIR="$HOME/.local/bin"
        mkdir -p "$BIN_DIR"

        cat > "$BIN_DIR/neo-link" <<EOF
#!/usr/bin/env bash
# NEO//LINK — gerado pelo instalador
exec "$VENV_PY" "$LINK_DIR/neo_link.py" "\$@"
EOF
        chmod +x "$BIN_DIR/neo-link"
        info ""
        info "comando criado em $BIN_DIR/neo-link"

        for RC in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
            [ -f "$RC" ] || continue
            if ! grep -qs 'HOME/.local/bin' "$RC"; then
                {
                    printf '\n# NEO//LINK\n'
                    printf 'export PATH="$HOME/.local/bin:$PATH"\n'
                } >> "$RC"
            fi
        done
        info "PATH actualizado (bash/zsh)"
    fi
fi

exit "$STATUS"
