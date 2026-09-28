# Instalar o NEO//SENTINEL no Termux

Distribuição standalone. **Não precisa de clonar o repositório, nem de git,
Docker, VS Code, root, ou do código do NEO//LINK.**

## Requisitos

- Android com [Termux](https://f-droid.org/packages/com.termux/) (use a versão
  da F-Droid, não a da Play Store — a da loja está desatualizada)
- App Tailscale ligada (para aceder ao MASTER)

## Instalar

Primeiro, o Python:

```bash
pkg install python curl
```

Depois, o SENTINEL — um comando:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/DNAI-WORKSTATION-NEOX1/development/monitor/install_standalone.sh | bash
```

Ou, se preferir configurar o MASTER já durante a instalação:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/DNAI-WORKSTATION-NEOX1/development/monitor/install_standalone.sh | bash -s -- \
  --url http://100.69.16.82:8765 \
  --name MASTER \
  --token O_SEU_TOKEN
```

> O `--token` é opcional. Se o seu LINK não tiver token, omita-o.

Feche e abra o Termux (para o `PATH` carregar) e confirme:

```bash
neo-sentinel --check
```

## Executar

```bash
neo-sentinel
```

| Tecla | Acção |
|---|---|
| `↑` / `↓` | node anterior / seguinte |
| `r` | refresh |
| `q` | sair |

## Configurar o MASTER remoto

O SENTINEL lê `~/.neo-x1/sentinel/config.json`. Para o editar:

```bash
nano ~/.neo-x1/sentinel/config.json
```

```json
{
  "refresh_seconds": 2.0,
  "timeout_seconds": 8.0,
  "nodes": [
    {
      "name": "MASTER",
      "url": "http://100.69.16.82:8765",
      "token": "o-token-do-link",
      "enabled": true
    }
  ]
}
```

- `name` — como o node aparece na TUI (pode ser diferente do hostname)
- `url` — endereço do LINK. Prefira o **MagicDNS** do Tailscale
  (`http://master:8765`) a um IP, que pode mudar
- `token` — o segredo partilhado com o LINK, se tiver

Depois de editar, confirme:

```bash
neo-sentinel --check
```

```
NEO//SENTINEL 0.1.0
  MASTER       http://100.69.16.82:8765                    (config)
    MASTER: ONLINE (DefunctumNoctis · NEO//LINK 0.1.0)
      ComfyUI      RUNNING / API ONLINE
      Ollama       RUNNING / API ONLINE
```

## Comandos

```bash
neo-sentinel              # abre a TUI
neo-sentinel --check      # diagnostico: node, LINK e serviços
neo-sentinel --version    # versão
```

Do lado do instalador:

```bash
# actualizar o codigo (preserva a configuração)
curl -fsSL https://raw.githubusercontent.com/.../install_standalone.sh | bash -s -- --update

# diagnosticar a instalação
bash install_standalone.sh --check

# remover
bash install_standalone.sh --uninstall
```

## Onde fica instalado

```
~/.neo-x1/sentinel/
├── .venv/              # ambiente Python isolado
├── neo_sentinel.py
├── sentinel/           # código do SENTINEL
├── config.json         # a SUA configuração (nunca é sobrescrita)
└── config.example.json

~/.local/bin/neo-sentinel   # o comando
```

O `config.json` é **preservado** em todas as instalações e actualizações. Pode
apagar com segurança se quiser recomeçar:

```bash
rm ~/.neo-x1/sentinel/config.json
```

## Como funciona

```
GitHub (raw)  →  installer  →  ~/.neo-x1/sentinel/  →  neo-sentinel
                                                        │
                                                   Tailscale
                                                        ▼
                                              NEO//LINK no MASTER
```

O SENTINEL **só fala com o LINK**. Não usa `psutil`, `nvidia-smi`, PowerShell,
WMI nem APIs do Windows — por isso o mesmo código corre no telemóvel, num
servidor Linux e num PC Windows.

Todos os dados do MASTER (CPU, RAM, GPU, discos, serviços) chegam pela API do
LINK.

## Diagnóstico

Se algo não funcionar:

```bash
bash ~/.neo-x1/sentinel/../install_standalone.sh --check
# ou re-descarregue o instalador e corra --check
```

Verifica: Python, dependências, ficheiros, comando no `PATH`, configuração e
conectividade com o LINK.

### Problemas comuns

| Sintoma | Solução |
|---|---|
| `neo-sentinel: command not found` | `source ~/.bashrc` ou reabra o Termux |
| `Permission denied` a escrever | o Termux tem de estar instalado em `/data/data/com.termux` |
| Node aparece `OFFLINE` | app Tailscale ligada? o LINK está a correr? porta certa? |
| `HTTP 401` | o `token` no `config.json` não bate com o do LINK |
| A tela dorme | `termux-wake-lock` (o installer já tenta) |

## Segurança

- O SENTINEL **não controla** nada por omissão; só apresenta o estado.
- Tokens nunca são impressos no output (o diagnóstico mostra só os 4 primeiros
  caracteres).
- Todo o tráfego vai pela Tailscale, que já vem cifrado (WireGuard).
- Se o LINK expuser controlo (`allow_control`), o SENTINEL passa a poder
  arrancar/parar serviços nesse node.

## Desinstalar

```bash
rm -rf ~/.neo-x1
rm ~/.local/bin/neo-sentinel
# remover a linha do PATH em ~/.bashrc, se quiser
```
