#!/usr/bin/env bash
# ============================================================================
#  NEO//LINK — instalador standalone do agente local
# ============================================================================
#
#  Instala o LINK em ~/.neo-x1/link/ e cria o comando `neo-link`.
#  Só precisa de Python e bash. Não precisa de git, Docker, VS Code ou root.
#
#  Instalar:
#      curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.sh | bash -- --name MEU-NODE
#
#  Opções:
#      --name NOME      nome do node (ex.: FEEDBACKAI). É obrigatório.
#      --host HOST      endereço onde o LINK escuta (omissão: config)
#      --remote         prepara para acesso via Tailscale (0.0.0.0 + token)
#      --check          diagnostica a instalação
#      --update         actualiza o código (preserva a configuração)
#      --uninstall      remove a instalação
#      --yes            não faz perguntas
#
#  O LINK lê a máquina e responde ao SENTINEL. Por omissão escuta apenas em
#  localhost e não aceita comandos — o controlo é desactivado.
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
REPO_RAW="https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link"
NEO_DIR="${NEO_DIR:-$HOME/.neo-x1}"
INSTALL_DIR="$NEO_DIR/link"
VENV_DIR="$INSTALL_DIR/.venv"
CONFIG_FILE="$INSTALL_DIR/config.json"
CREDENTIALS_FILE="$INSTALL_DIR/credentials.json"

# Ficheiros de runtime do LINK. Só código — nunca config nem credenciais.
RUNTIME_FILES=(
    "neo_link.py"
    "requirements.txt"
    "config.example.json"
    "neolink/__init__.py"
    "neolink/config.py"
    "neolink/identity.py"
    "neolink/collectors.py"
    "neolink/gpu.py"
    "neolink/services.py"
    "neolink/server.py"
    "neolink/pairing.py"
    "neolink/pairing_server.py"
)

NODE_NAME=""
HOST_OVERRIDE=""
MODE="install"
ASSUME_YES=0

# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------
say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mOK\033[0m   %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    \033[33maviso\033[0m %s\n' "$*" >&2; }
die()  { printf '\n\033[31merro: %s\033[0m\n' "$*" >&2; exit 1; }

usage() { sed -n '2,22p' "$0"; exit 0; }

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --name)      NODE_NAME="${2:-}"; shift 2 ;;
        --host)      HOST_OVERRIDE="${2:-}"; shift 2 ;;
        --remote)    MODE="remote"; shift ;;
        --check)     MODE="check"; shift ;;
        --update)    MODE="update"; shift ;;
        --uninstall) MODE="uninstall"; shift ;;
        --yes|-y)    ASSUME_YES=1; shift ;;
        -h|--help)   usage ;;
        *) die "Opção desconhecida: $1 (use --help)" ;;
    esac
done

# ---------------------------------------------------------------------------
# Ferramentas
# ---------------------------------------------------------------------------
have_curl() { command -v curl >/dev/null 2>&1; }
have_wget() { command -v wget >/dev/null 2>&1; }

fetch() {
    local url="$1" dest="$2"
    if have_curl; then
        curl -fsSL "$url" -o "$dest"
    elif have_wget; then
        wget -qO "$dest" "$url"
    else
        die "É preciso curl ou wget. No Termux: pkg install curl"
    fi
}

find_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
                command -v "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

# O venv usa bin/ em POSIX e Scripts\ em Windows.
venv_python() {
    if [ -x "$VENV_DIR/bin/python" ]; then
        printf '%s' "$VENV_DIR/bin/python"
    elif [ -x "$VENV_DIR/Scripts/python.exe" ]; then
        printf '%s' "$VENV_DIR/Scripts/python.exe"
    else
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------
run_check() {
    step "Diagnóstico"
    local problems=0
    local vpy
    vpy="$(venv_python)" || vpy=""

    if [ -n "$vpy" ]; then
        ok "Python do venv: $($vpy -V 2>&1)"
    else
        warn "venv não encontrado em $VENV_DIR"
        problems=$((problems + 1))
    fi

    local file missing=0
    for file in "${RUNTIME_FILES[@]}"; do
        [ -f "$INSTALL_DIR/$file" ] || { warn "falta: $file"; missing=$((missing + 1)); }
    done
    if [ "$missing" = "0" ]; then
        ok "ficheiros de runtime completos (${#RUNTIME_FILES[@]})"
    else
        problems=$((problems + missing))
    fi

    if [ -n "$vpy" ] && "$vpy" -c 'import psutil' 2>/dev/null; then
        ok "dependências instaladas (psutil)"
    else
        warn "psutil não está instalado"
        problems=$((problems + 1))
    fi

    if [ -x "$HOME/.local/bin/neo-link" ]; then
        ok "comando neo-link instalado"
        case ":$PATH:" in
            *":$HOME/.local/bin:"*) ok "PATH contém ~/.local/bin" ;;
            *) warn "~/.local/bin não está no PATH — corra: source ~/.bashrc" ;;
        esac
    else
        warn "comando neo-link não encontrado"
        problems=$((problems + 1))
    fi

    if [ -f "$CONFIG_FILE" ]; then
        ok "config.json presente"
        if [ -n "$vpy" ]; then
            "$vpy" "$INSTALL_DIR/neo_link.py" --config "$CONFIG_FILE" --check 2>&1 \
                | sed 's/^/    /' || true
        fi
    else
        warn "sem config.json — reinstale com --name"
    fi

    if [ -f "$CREDENTIALS_FILE" ] && [ -n "$vpy" ]; then
        local n
        n="$("$vpy" -c "
import json,sys
try: print(len(json.load(open(sys.argv[1],encoding='utf-8'))['credentials']))
except Exception: print(0)
" "$CREDENTIALS_FILE" 2>/dev/null || echo 0)"
        ok "clientes pareados: $n"
    fi

    say ""
    if [ "$problems" = "0" ]; then
        printf '\033[32mDiagnóstico: tudo OK\033[0m\n'
        return 0
    fi
    printf '\033[33mDiagnóstico: %s problema(s)\033[0m\n' "$problems"
    return 1
}

# ---------------------------------------------------------------------------
# Desinstalação
# ---------------------------------------------------------------------------
do_uninstall() {
    step "Remover instalação"
    if [ ! -d "$INSTALL_DIR" ]; then
        ok "nada instalado em $INSTALL_DIR"
        return 0
    fi
    if [ "$ASSUME_YES" != "1" ]; then
        say "    Isto remove $INSTALL_DIR (inclui config.json e as credenciais pareadas)."
        printf '    Tem a certeza? [s/N] '
        read -r reply
        case "$reply" in
            s|S|sim|y|Y) ;;
            *) say "cancelado"; return 0 ;;
        esac
    fi
    rm -rf "$INSTALL_DIR"
    ok "removido $INSTALL_DIR"
    info "O comando ~/.local/bin/neo-link foi mantido (inócuo sem a instalação)."
}

# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------
main() {
    case "$MODE" in
        check)    run_check; exit $? ;;
        uninstall) do_uninstall; exit 0 ;;
    esac

    say "NEO//LINK — instalação standalone"
    say "destino: $INSTALL_DIR"

    # 1) Python
    step "A verificar pré-requisitos"
    if ! PY_BIN="$(find_python)"; then
        say "    Python 3.9+ não encontrado."
        say "    Ubuntu/Debian: sudo apt install python3 python3-venv"
        say "    Windows:        https://www.python.org/downloads/"
        die "instale o Python e volte a correr o instalador"
    fi
    ok "Python: $($PY_BIN -V 2>&1)"

    # 2) Descarregar o runtime
    step "A descarregar o NEO//LINK"
    mkdir -p "$INSTALL_DIR/neolink"
    local file url dest
    for file in "${RUNTIME_FILES[@]}"; do
        url="$REPO_RAW/$file"
        dest="$INSTALL_DIR/$file"
        if fetch "$url" "$dest"; then
            ok "$file"
        else
            if [ "$MODE" = "update" ] && [ -f "$dest" ]; then
                warn "$file não descarregado (mantém a versão anterior)"
            else
                rm -f "$dest"
                die "falha ao descarregar $file de $url"
            fi
        fi
    done

    # 3) Ambiente e dependências
    step "A preparar o ambiente Python"
    mkdir -p "$INSTALL_DIR"
    if ! VENV_PY="$(venv_python)"; then
        "$PY_BIN" -m venv "$VENV_DIR" 2>/dev/null \
            || die "não foi possível criar o venv"
        VENV_PY="$(venv_python)" || die "venv criado mas o interpretador não foi encontrado"
        ok "venv criado"
    else
        ok "venv já existe"
    fi
    say "    a instalar dependências (psutil)..."
    if ! "$VENV_PY" -m pip install -q -r "$INSTALL_DIR/requirements.txt" 2>/dev/null; then
        # PEP 668: Python "externally managed" recusa instalar sem flag.
        if ! "$VENV_PY" -m pip install -q --break-system-packages -r "$INSTALL_DIR/requirements.txt" 2>/dev/null; then
            die "falha ao instalar dependências (psutil)"
        fi
    fi
    "$VENV_PY" -c 'import psutil' || die "psutil não importável"
    ok "psutil instalado"

    # 4) config.json
    step "A configurar o node"
    if [ -f "$CONFIG_FILE" ]; then
        ok "config.json já existe — preservado (não é sobrescrito)"
        if [ -n "$NODE_NAME" ]; then
            info "Recebeu --name mas a configuração existe. Para a mudar, edite:"
            info "    nano $CONFIG_FILE"
        fi
    else
        if [ -z "$NODE_NAME" ]; then
            say ""
            say "    Falta o nome do node. Corra novamente com:"
            say "        bash install.sh -- --name MEU-NODE"
            say "    ou copie o exemplo e edite:"
            say "        cp $INSTALL_DIR/config.example.json $CONFIG_FILE"
            return 2
        fi
        "$PY_BIN" - "$CONFIG_FILE" "$NODE_NAME" "$HOST_OVERRIDE" <<'PYEOF'
import json, sys
from pathlib import Path

config_path, name, host = sys.argv[1:4]
data = {
    "node": {"name": name},
    "server": {
        "host": host or "127.0.0.1",
        "port": 8765,
        "allow_control": False,
        "token": None,
    },
    "services": [],
}
Path(config_path).write_text(
    json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
PYEOF
        ok "config.json criado para $NODE_NAME (escutando em ${HOST_OVERRIDE:-127.0.0.1})"
        info "Adicione os serviços dessa máquina em:  nano $CONFIG_FILE"
    fi

    # 5) Comando neo-link
    step "A criar o comando neo-link"
    local bin_dir="$HOME/.local/bin"
    mkdir -p "$bin_dir"
    cat > "$bin_dir/neo-link" <<EOF
#!/usr/bin/env bash
# NEO//LINK — gerado pelo instalador
exec "$VENV_PY" "$INSTALL_DIR/neo_link.py" "\$@"
EOF
    chmod +x "$bin_dir/neo-link"
    ok "criado em $bin_dir/neo-link"
    if ! grep -qs 'HOME/.local/bin' "$HOME/.bashrc" 2>/dev/null; then
        {
            echo ''
            echo '# NEO//LINK'
            echo 'export PATH="$HOME/.local/bin:$PATH"'
        } >> "$HOME/.bashrc"
        ok "PATH adicionado ao ~/.bashrc"
    fi
    [ -f "$HOME/.profile" ] && ! grep -qs 'HOME/.local/bin' "$HOME/.profile" 2>/dev/null \
        && echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.profile"

    run_check || true

    say ""
    say "Instalação concluída."
    say ""
    say "  1. Abra um terminal novo (para o PATH carregar)"
    say "  2. Configure os serviços:  nano $CONFIG_FILE"
    say "  3. Arrancar:              neo-link"
    say "  4. Emparelhar um SENTINEL:  neo-link --pair --host 0.0.0.0"
    say ""
    say "  O LINK escuta em localhost e não aceita comandos."
    say "  Para o usar a partir de outra máquina, exponha-o com --host 0.0.0.0"
    say "  (e considere definir um 'token' no config.json)."
}

main
