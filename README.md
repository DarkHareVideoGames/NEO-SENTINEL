# NEO//SENTINEL

Cliente TUI de monitorização para a tailnet NEO X1.

Observa e apresenta. Não conhece o interior das máquinas — todos os dados
chegam pela API do **NEO//LINK**, o agente local que corre em cada node.

```
     NEO//SENTINEL   (Termux / Linux / Windows / macOS)
            │
         Tailscale
            │
            ▼
        NEO//LINK  ──►  hardware, RAM, GPU, discos, serviços
```

## Requisitos

- Python 3.9+
- `bash` e `curl` (ou `wget`)
- No Android: [Termux](https://f-droid.org/packages/com.termux/) da F-Droid
  (a versão da Play Store está desatualizada)
- A app Tailscale ligada, para alcançar o node

## Instalar

Um comando:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/install.sh | bash
```

Instala em `~/.neo-x1/sentinel/` e cria o comando `neo-sentinel`.
Não precisa de `git`, Docker, VS Code, root, nem do código do LINK.

Depois, **reabra o Termux** (para o `PATH` carregar) e confirme:

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

## Adicionar um node

### Opção 1 — pairing (recomendado)

Sem copiar tokens à mão. No node remoto:

```bash
python neo_link.py --pair
```

Mostra um código temporário (ex.: `7K4M-92PX`, válido 10 minutos). No
SENTINEL:

```bash
neo-sentinel --pair
```

O SENTINEL pede o URL e o código, valida, e guarda a credencial sozinho.

### Opção 2 — configuração manual

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
      "token": "a-credencial",
      "enabled": true
    }
  ]
}
```

- `name` — como o node aparece na TUI
- `url` — endereço do LINK. Prefira o **MagicDNS** do Tailscale
  (`http://master:8765`) a um IP, que pode mudar

## Comandos

```bash
neo-sentinel              # TUI
neo-sentinel --check      # diagnóstico + estado dos serviços
neo-sentinel --pair       # emparelhar um node novo
neo-sentinel --version
```

O instalador também aceita:

```bash
bash install.sh --check      # diagnostica a instalação
bash install.sh --update     # actualiza o código (preserva a config)
bash install.sh --uninstall  # remove
```

Actualizar sem voltar a executar o instalador completo:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/install.sh | bash -s -- --update
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

O `config.json` é **preservado** em todas as instalações e actualizações.

## Características

- Só depende de `textual` (mais a biblioteca standard)
- **Não** usa `psutil`, `nvidia-smi`, PowerShell nem APIs do Windows —
  por isso o mesmo código corre no telemóvel, num servidor Linux e num PC
- Todos os dados do node chegam pela API do LINK
- Config e credenciais com permissão `600`, nunca versionadas
- Sem port scanning e sem discovery automático

## Diagnóstico

```bash
neo-sentinel --check
```

Mostra a versão, os nodes configurados, e para cada node se o LINK responde
e qual o estado dos serviços.

| Sintoma | Solução |
|---|---|
| `neo-sentinel: command not found` | `source ~/.bashrc` ou reabra o Termux |
| Node aparece `OFFLINE` | app Tailscale ligada? LINK a correr? porta certa? |
| `HTTP 401` | a credencial não bate com a do LINK |
| A tela dorme | `termux-wake-lock` |

## Segurança

- O SENTINEL só **observa** por omissão; não controla nada.
- Tokens e credenciais nunca são impressos no terminal.
- Todo o tráfego vai pela Tailscale, que já vem cifrado (WireGuard).
- A credencial do pairing é guardada só como hash no LINK: ler o ficheiro
  não dá acesso.

## Desinstalar

```bash
bash install.sh --uninstall
rm -rf ~/.neo-x1
rm ~/.local/bin/neo-sentinel
```

## Sobre este repositório

Este repo contém **apenas o runtime do SENTINEL** — o que precisa de ser
instalado fora da máquina. O código completo de desenvolvimento (incluindo o
NEO//LINK) vive no repositório privado `DNAI-WORKSTATION-NEOX1`.

## Licença

MIT. Ver [LICENSE](LICENSE).
