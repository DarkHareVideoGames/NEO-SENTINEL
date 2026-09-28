#!/usr/bin/env bash
# ============================================================================
#  NEO//SENTINEL — instalador standalone
# ============================================================================
#
#  Instala APENAS o NEO//SENTINEL em ~/.neo-x1/sentinel/ e cria o comando
#  `neo-sentinel`. Não precisa de git, Docker, VS Code, root, nem do código do
#  NEO//LINK. Funciona em Termux/Android, Linux, macOS e Windows (Git Bash/WSL).
#
#  Instalar:
#      curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/install.sh | bash
#
#  Opções:
#      --url URL          endereço do LINK (ex.: http://master:8765)
#      --name NOME        nome do node no SENTINEL (ex.: MASTER)
#      --token SEGREDO    token partilhado com o LINK
#      --check            diagnostico da instalacao
#      --update           actualiza o codigo (preserva a configuracao)
#      --uninstall        remove a instalacao (pergunta antes)
#      --dir CAMINHO      directorio de instalacao (omissao: ~/.neo-x1/sentinel)
#      --yes              nao faz perguntas
#
#  O Sentinel e platform-independent: nao usa psutil, nvidia-smi, PowerShell
#  nem APIs do Windows. Todos os dados do node chegam pela API do NEO//LINK.
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
NEO_DIR="${NEO_DIR:-$HOME/.neo-x1}"
INSTALL_DIR="$NEO_DIR/sentinel"
VENV_DIR="$INSTALL_DIR/.venv"
CONFIG_FILE="$INSTALL_DIR/config.json"

REPO_RAW="https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main"

# Ficheiros de runtime do SENTINEL. Derivados da arvore do pacote sentinel/
# mais o entrypoint. Nada de LINK, ComfyUI, Ollama ou testes.
RUNTIME_FILES=(
    "neo_sentinel.py"
    "requirements.txt"
    "config.example.json"
    "sentinel/__init__.py"
    "sentinel/client.py"
    "sentinel/config.py"
    "sentinel/discovery.py"
    "sentinel/pairing.py"
    "sentinel/ui.py"
)

NODE_URL=""
NODE_NAME=""
NODE_TOKEN=""
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

usage() { sed -n '2,30p' "$0"; exit 0; }

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --url)       NODE_URL="${2:-}"; shift 2 ;;
        --name)      NODE_NAME="${2:-}"; shift 2 ;;
        --token)     NODE_TOKEN="${2:-}"; shift 2 ;;
        --dir)       INSTALL_DIR="${2:-}"; VENV_DIR="$INSTALL_DIR/.venv"; CONFIG_FILE="$INSTALL_DIR/config.json"; shift 2 ;;
        --check)     MODE="check"; shift ;;
        --update)    MODE="update"; shift ;;
        --uninstall) MODE="uninstall"; shift ;;
        --yes|-y)    ASSUME_YES=1; shift ;;
        -h|--help)   usage ;;
        *) die "Opção desconhecida: $1 (use --help)" ;;
    esac
done

# ---------------------------------------------------------------------------
# Deteção de ambiente
# ---------------------------------------------------------------------------
detect_termux() {
    if [ -n "${PREFIX:-}" ] && [ -d "/data/data/com.termux" ]; then
        echo "termux"
    elif [ -n "${TERMUX_VERSION:-}" ]; then
        echo "termux"
    else
        echo "generic"
    fi
}

PLATFORM="$(detect_termux)"

find_python() {
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

# O venv usa bin/ em POSIX e Scripts\ em Windows. Esta função devolve sempre
# o caminho certo, para o instalador funcionar nos dois.
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
# Ferramentas de download
# ---------------------------------------------------------------------------
have_curl() { command -v curl >/dev/null 2>&1; }
have_wget() { command -v wget >/dev/null 2>&1; }

fetch() {
    # fetch <url> <destino>
    local url="$1" dest="$2"
    if have_curl; then
        curl -fsSL "$url" -o "$dest"
    elif have_wget; then
        wget -qO "$dest" "$url"
    else
        die "É preciso curl ou wget para descarregar o SENTINEL. No Termux: pkg install curl"
    fi
}

# ---------------------------------------------------------------------------
# PASSO 1 — pré-requisitos
# ---------------------------------------------------------------------------
check_prerequisites() {
    step "A verificar pré-requisitos"

    if ! PY_BIN="$(find_python)"; then
        say "    Python 3.9+ não encontrado."
        case "$PLATFORM" in
            termux) say "    No Termux:  pkg install python" ;;
            *)       say "    Ubuntu/Debian: sudo apt install python3 python3-venv"
                     say "    Fedora/RHEL:  sudo dnf install python3" ;;
        esac
        die "instale o Python e volte a correr o instalador"
    fi
    ok "Python: $($PY_BIN -V 2>&1)"

    if [ "$PLATFORM" = "termux" ]; then
        ok "Plataforma: Termux"
        if command -v termux-wake-lock >/dev/null 2>&1; then
            if termux-wake-lock 2>/dev/null; then
                ok "ecrã mantido acordado (termux-wake-lock)"
            else
                warn "wake-lock negado; o Android pode suspender a app"
            fi
        else
            warn "termux-wake-lock indisponível (pkg install termux-api?)"
        fi
        if ! have_curl && ! have_wget; then
            say "    curl/wget não encontrado."
            die "no Termux: pkg install curl"
        fi
        ok "Ferramenta de download: $(have_curl && echo curl || echo wget)"
    else
        ok "Plataforma: sistema genérico"
    fi

    if [ -f "$INSTALL_DIR/.installed" ]; then
        ok "Instalação existente encontrada (vai ser actualizada)"
    fi
}

# ---------------------------------------------------------------------------
# PASSO 2 — descarregar o runtime
# ---------------------------------------------------------------------------
download_runtime() {
    step "A descarregar o NEO//SENTINEL"

    mkdir -p "$INSTALL_DIR/sentinel"

    local file url dest
    for file in "${RUNTIME_FILES[@]}"; do
        url="$REPO_RAW/$file"
        dest="$INSTALL_DIR/$file"
        if fetch "$url" "$dest"; then
            ok "$file"
        else
            # Em modo update, um ficheiro em falta não deve apagar o que existe.
            if [ "$MODE" = "update" ] && [ -f "$dest" ]; then
                warn "$file não descarregado (mantém a versão anterior)"
            else
                rm -f "$dest"
                die "falha ao descarregar $file de $url"
            fi
        fi
    done

    # Remove módulos que deixaram de existir no upstream.
    # Feito sem subshell: `local` dentro de um pipe falha e o `set -e` abortaria.
    # A comparação normaliza os separadores: no Windows o caminho vem com "\",
    # mas a lista RUNTIME_FILES usa sempre "/". Sem isto, todos os módulos
    # eram considerados obsoletos e apagados logo após o download.
    local stale rel keep
    for stale in "$INSTALL_DIR"/sentinel/*.py; do
        [ -f "$stale" ] || continue
        rel="${stale#"$INSTALL_DIR"/}"
        rel="${rel//\\//}"
        keep=0
        for file in "${RUNTIME_FILES[@]}"; do
            if [ "$file" = "$rel" ]; then
                keep=1
                break
            fi
        done
        if [ "$keep" = "0" ]; then
            rm -f "$stale"
            info "removido obsoleto: $rel"
        fi
    done

    echo "installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$INSTALL_DIR/.installed"
    return 0
}

# ---------------------------------------------------------------------------
# PASSO 3 — ambiente Python e dependências
# ---------------------------------------------------------------------------
setup_environment() {
    step "A preparar o ambiente Python"

    mkdir -p "$INSTALL_DIR"

    if ! VENV_PY="$(venv_python)"; then
        "$PY_BIN" -m venv "$VENV_DIR" 2>/dev/null \
            || die "não foi possível criar o venv. No Termux: pkg install python (inclui venv)"
        VENV_PY="$(venv_python)" \
            || die "venv criado mas o interpretador não foi encontrado em $VENV_DIR"
        ok "venv criado em $VENV_DIR"
    else
        ok "venv já existe"
    fi

    say "    a instalar dependências..."
    if ! "$VENV_PY" -m pip install -q --upgrade pip 2>/dev/null; then
        warn "não foi possível actualizar o pip (continua)"
    fi

    if ! "$VENV_PY" -m pip install -q -r "$INSTALL_DIR/requirements.txt" 2>/dev/null; then
        # PEP 668: Python "externally managed" recusa instalar sem flag.
        if ! "$VENV_PY" -m pip install -q --break-system-packages -r "$INSTALL_DIR/requirements.txt" 2>/dev/null; then
            die "falha ao instalar dependências (textual)"
        fi
    fi

    "$VENV_PY" -c 'import textual' >/dev/null 2>&1 || die "textual não importável"
    ok "textual instalado"
}

# ---------------------------------------------------------------------------
# PASSO 4 — comando neo-sentinel
# ---------------------------------------------------------------------------
create_command() {
    step "A criar o comando neo-sentinel"

    local bin_dir="$HOME/.local/bin"
    local target="$bin_dir/neo-sentinel"

    mkdir -p "$bin_dir"

    # Usa o interpretador resolvido agora, para funcionar em POSIX e Windows.
    local vpy
    vpy="$(venv_python)" || die "venv sem interpretador; corra a instalação de novo"

    cat > "$target" <<EOF
#!/usr/bin/env bash
# NEO//SENTINEL — gerado pelo instalador
exec "$vpy" "$INSTALL_DIR/neo_sentinel.py" "\$@"
EOF
    chmod +x "$target"
    ok "criado em $target"

    # O Termux nao tem $HOME/.local/bin no PATH por omissao.
    if [ "$PLATFORM" = "termux" ]; then
        if ! grep -qs 'HOME/.local/bin' "$HOME/.bashrc" 2>/dev/null; then
            {
                echo ''
                echo '# NEO//SENTINEL'
                echo 'export PATH="$HOME/.local/bin:$PATH"'
            } >> "$HOME/.bashrc"
            ok "PATH adicionado ao ~/.bashrc"
        else
            ok "PATH já contem ~/.local/bin"
        fi
        # ~/.profile para shells não interativos.
        if [ -f "$HOME/.profile" ] && ! grep -qs 'HOME/.local/bin' "$HOME/.profile" 2>/dev/null; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.profile"
        fi
        ok "reabra o Termux (ou: source ~/.bashrc) para o comando ficar disponível"
    elif ! grep -qs 'HOME/.local/bin' "$HOME/.bashrc" 2>/dev/null; then
        {
            echo ''
            echo '# NEO//SENTINEL'
            echo 'export PATH="$HOME/.local/bin:$PATH"'
        } >> "$HOME/.bashrc"
        ok "PATH adicionado ao ~/.bashrc"
    fi
}

# ---------------------------------------------------------------------------
# PASSO 5 — configuração (nunca destrói a existente)
# ---------------------------------------------------------------------------
write_config() {
    step "A configurar o node"

    if [ -f "$CONFIG_FILE" ]; then
        ok "config.json já existe — preservado (não é sobrescrito)"
        if [ -n "$NODE_URL" ]; then
            say ""
            info "Recebeu --url mas a configuração existe. Para a atualizar:"
            info "    nano $CONFIG_FILE"
            info "ou apague o ficheiro e corra o instalador de novo."
        fi
        return 0
    fi

    if [ -z "$NODE_URL" ]; then
        say ""
        say "    Nenhum node configurado. Configure depois com:"
        say "        nano $CONFIG_FILE"
        say "    ou copie o exemplo:"
        say "        cp $INSTALL_DIR/config.example.json $CONFIG_FILE"
        return 0
    fi

    "$PY_BIN" - "$CONFIG_FILE" "$NODE_URL" "$NODE_NAME" "$NODE_TOKEN" <<'PYEOF'
import json, sys
from pathlib import Path

config_path, url, name, token = sys.argv[1:5]
data = {
    "refresh_seconds": 2.0,
    "timeout_seconds": 8.0,
    "nodes": [{
        "name": name or "MASTER",
        "url": url,
        "token": token or None,
        "enabled": True,
    }],
}
Path(config_path).write_text(
    json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
PYEOF
    ok "config.json criado para $([ -n "$NODE_NAME" ] && echo "$NODE_NAME" || echo MASTER)"
    info "url:  $NODE_URL"
    # Nunca mostrar o token por inteiro.
    if [ -n "$NODE_TOKEN" ]; then
        info "token: ${NODE_TOKEN:0:4}… (guardado, não é mostrado)"
    fi
}

# ---------------------------------------------------------------------------
# PASSO 6 — diagnóstico
# ---------------------------------------------------------------------------
run_check() {
    step "Diagnóstico"

    local problems=0
    local vpy
    vpy="$(venv_python)" || vpy=""

    # Python
    if [ -n "$vpy" ]; then
        ok "Python do venv: $($vpy -V 2>&1)"
    else
        warn "venv não encontrado em $VENV_DIR"
        problems=$((problems + 1))
    fi

    # Ficheiros de runtime
    local file missing=0
    for file in "${RUNTIME_FILES[@]}"; do
        if [ -f "$INSTALL_DIR/$file" ]; then
            :
        else
            warn "falta: $file"
            missing=$((missing + 1))
        fi
    done
    [ "$missing" = "0" ] && ok "ficheiros de runtime completos (${#RUNTIME_FILES[@]})" \
                         || problems=$((problems + missing))

    # Dependências
    if [ -n "$vpy" ] && "$vpy" -c 'import textual' 2>/dev/null; then
        ok "dependências instaladas (textual)"
    else
        warn "textual não está instalado"
        problems=$((problems + 1))
    fi

    # Plataforma / imports proibidos
    if [ -f "$INSTALL_DIR/sentinel/client.py" ]; then
        if grep -qE '^[[:space:]]*(import|from)[[:space:]]+(psutil|winreg|ctypes|win32)' \
             "$INSTALL_DIR/sentinel/client.py" 2>/dev/null; then
            warn "client.py parece usar recolha local — revê"
            problems=$((problems + 1))
        else
            ok "SENTINEL não usa psutil/WMI/WinAPI (dados vêm do LINK)"
        fi
    fi

    # Comando
    if [ -x "$HOME/.local/bin/neo-sentinel" ]; then
        ok "comando neo-sentinel instalado"
        case ":$PATH:" in
            *":$HOME/.local/bin:"*) ok "PATH contém ~/.local/bin" ;;
            *) warn "~/.local/bin não está no PATH — corra: source ~/.bashrc" ;;
        esac
    else
        warn "comando neo-sentinel não encontrado"
        problems=$((problems + 1))
    fi

    # Configuração
    if [ -f "$CONFIG_FILE" ]; then
        ok "config.json presente"
        local node_count
        node_count="$([ -n "$vpy" ] && "$vpy" - "$CONFIG_FILE" 2>/dev/null <<'PYEOF' || echo 0
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(len([n for n in data.get("nodes", []) if n.get("enabled")]))
PYEOF
)"
        if [ "$node_count" -gt 0 ] 2>/dev/null; then
            ok "nodes configurados: $node_count"
        else
            warn "nenhum node configurado"
        fi
    else
        warn "sem config.json — o SENTINEL arranca vazio"
    fi

    # Conectividade com o LINK
    if [ -n "$vpy" ] && [ -f "$CONFIG_FILE" ]; then
        local reach
        reach="$("$vpy" - "$CONFIG_FILE" 2>/dev/null <<'PYEOF' || true
import json, sys, urllib.request
from urllib.parse import urlparse

data = json.load(open(sys.argv[1], encoding="utf-8"))
for node in data.get("nodes", []):
    if not node.get("enabled"):
        continue
    host = urlparse(node["url"]).hostname
    if not host:
        continue
    headers = {"X-NEO-Token": node["token"]} if node.get("token") else {}
    req = urllib.request.Request(
        node["url"].rstrip("/") + "/status", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        name = (payload.get("node") or {}).get("name", "?")
        print(f"ONLINE {name} ({host})")
    except Exception as exc:
        reason = getattr(exc, "reason", exc)
        print(f"OFFLINE {host}: {reason}")
PYEOF
)"
        if [ -n "$reach" ]; then
            while IFS= read -r line; do
                case "$line" in
                    ONLINE*)  ok "LINK: $line" ;;
                    OFFLINE*) warn "LINK: $line" ;;
                esac
            done <<< "$reach"
        fi
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
        say "    Isto remove $INSTALL_DIR (inclui config.json e .venv)."
        printf '    Tem a certeza? [s/N] '
        read -r reply
        case "$reply" in
            s|S|sim|y|Y) ;;
            *) say "cancelado"; return 0 ;;
        esac
    fi

    rm -rf "$INSTALL_DIR"
    ok "removido $INSTALL_DIR"
    info "O comando ~/.local/bin/neo-sentinel foi mantido (inócuo sem a instalação)."
    info "Apague-o manualmente se quiser: rm ~/.local/bin/neo-sentinel"
}

# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------
main() {
    case "$MODE" in
        check)
            run_check
            exit $?
            ;;
        uninstall)
            do_uninstall
            exit 0
            ;;
    esac

    say "NEO//SENTINEL — instalação standalone"
    say "destino: $INSTALL_DIR"

    check_prerequisites
    download_runtime
    setup_environment
    create_command
    write_config

    run_check || true

    say ""
    say "Instalação concluída."
    say ""
    if [ "$PLATFORM" = "termux" ]; then
        say "  1. Abra um novo terminal Termux (para o PATH ficar carregado)"
        say "  2. Verifique:      neo-sentinel --check"
        say "  3. Configure o MASTER em:  nano $CONFIG_FILE"
        say "  4. Arrancar:      neo-sentinel"
    else
        say "  1. source ~/.bashrc      (se ~/.local/bin ainda não estiver no PATH)"
        say "  2. neo-sentinel --check"
        say "  3. Configure o node em:  nano $CONFIG_FILE"
        say "  4. Arrancar:             neo-sentinel"
    fi
}

main
