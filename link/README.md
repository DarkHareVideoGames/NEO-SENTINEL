# NEO//LINK

O agente local do sistema de monitorização. É esta peça que corre em cada
máquina que quero acompanhar.

## O que é

O LINK conhece a máquina onde está instalado. Sabe que processos correm, em
que portas escutam, quanto de CPU e RAM está a usar, como está a GPU, e se os
serviços configurados respondem.

Não é um servidor genérico nem um agente com muitas capacidades. Faz uma
coisa: recolhe o estado da máquina e responde ao
[NEO//SENTINEL](../README.md) quando este pergunta.

## Como se instala

O instalador é específico de cada plataforma. **Todos usam a mesma lógica**
(`bootstrap.py`), por isso o resultado é idêntico em qualquer sistema.

### Windows 10/11 — PowerShell

Forma recomendada: descarregar primeiro, para poder rever antes de executar.

```powershell
irm https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.ps1 -OutFile install.ps1
.\install.ps1 -Name MEU-NODE
```

Atalho para quem aceita executar directamente:

```powershell
irm https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.ps1 | iex
```

Precisa apenas de **Python 3.9+**. Não precisa de Bash, WSL, Git, Git Bash,
Cygwin, Chocolatey, Docker, nem de permissões de administrador.

Instala em `%USERPROFILE%\.neo-x1\link` e cria o comando `neo-link`.

### Linux, macOS e Termux — shell

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.sh -o install.sh
bash install.sh --name MEU-NODE
```

Ou, mais curto, se preferir executar directamente:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.sh | bash -s -- --name MEU-NODE
```

Funciona em Linux, macOS (Intel e Apple Silicon) e Termux. Precisa apenas de
**Python 3.9+** e de `curl` ou `wget`. Não precisa de `sudo`, de `systemd`, de
Homebrew, de Docker, nem de nenhuma distribuição específica.

Instala em `~/.neo-x1/link` e cria o comando `neo-link`.

### Termux (Android)

O Termux é suportado como plataforma própria, sem root, sem `systemd` e sem
`sudo` — a instalação fica dentro do `$PREFIX`, onde `$HOME` já aponta:

```bash
pkg install python
bash install.sh --name MEU-NODE
```

Para o Android não matar o processo em segundo plano:

```bash
termux-wake-lock
neo-link
```

O **NEO//SENTINEL** corre no Termux sem alterações. Já o **NEO//LINK** é
suportado, mas com as limitações do Android: sem serviços de sistema e sem
monitorização de outros processos que não sejam da própria app. Ver a tabela
de suporte abaixo.

### Opções (ambos os instaladores)

| Opção | PowerShell | Shell | Efeito |
|---|---|---|---|
| `-Name` | `--name` | | Nome lógico do node. Sem ele, usa o hostname. |
| `-HostAddress` | `--host` | | Endereço onde escuta. Por omissão, só localhost. |
| `-Port` | `--port` | | Porta. Por omissão, 8765. |
| `-InstallDir` | `--dir` | | Directório de instalação. |
| `-Check` | `--check` | | Diagnóstico, sem alterar nada. |
| `-Update` | `--update` | | Actualiza o runtime; preserva a configuração. |
| `-Uninstall` | `--uninstall` | | Remove. |
| `-Yes` | `--yes` | | Não faz perguntas. |

### Estado do suporte

Declarado honestamente, conforme o que foi verificado:

| Plataforma | Estado | Notas |
|---|---|---|
| Windows 10/11 | **SUPPORTED** | `install.ps1` nativo. Testes de sintaxe e de conteúdo executados; instalação real ainda não verificada. |
| Linux | **SUPPORTED** | `install.sh`. Testes de sintaxe e de conteúdo executados. |
| macOS (Intel / Apple Silicon) | **SUPPORTED** | `install.sh`. Usa o Python do sistema; Homebrew não é obrigatório. Não testado em hardware. |
| Termux / Android | **SUPPORTED** | `install.sh`, dentro do `$PREFIX`. Não testado em dispositivo. |

Nenhuma destas plataformas é declarada "testada" sem o ter sido. A lógica de
instalação é uma só, o que reduz a superfície a testar.

### Arranque automático (opcional)

Não é instalado por omissão. Se o quiser:

- **Windows** — Menu Início → Executar → `neo-link` (arranca no login).
- **Linux** — crie você um serviço de `systemd` de utilizador, se quiser.
- **macOS** — um `LaunchAgent` de utilizador em `~/Library/LaunchAgents/`.

Nenhum destes caminhos é necessary para o NEO//LINK funcionar, e nenhum
installer os configura sozinho.

### Depois de instalar

Editar a configuração da máquina:

```bash
nano ~/.neo-x1/link/config.json      # Windows: %USERPROFILE%\.neo-x1\link\config.json
```

Verificar o que foi instalado:

```bash
neo-link --check
```

Arrancar e emparelhar:

```bash
neo-link
neo-link --pair
```

Uma máquina sem serviços é perfeitamente válida — o SENTINEL mostra a mesma
hardware e os discos, e os serviços aparecem como `N/A`.

## O LINK não precisa de ser público

O NEO//SENTINEL e o NEO//LINK nunca precisam de ser expostos à Internet.
Ambos são feitos para uma rede privada, tipicamente **Tailscale**.

Por omissão o LINK escuta apenas em `127.0.0.1` e não aceita comandos
(`allow_control` é `false`). Para o usar a partir de outra máquina expõe-se
explicitamente:

```bash
neo-link --host 0.0.0.0
```

Mesmo assim:

- os instaladores **não** abrem portas no router, **não** fazem port
  forwarding e **não** mexem na firewall;
- `allow_control` fica `false` numa instalação nova;
- a autenticação (pairing) continua recomendada em qualquer rede;
- o `token`, se o definir em `config.json`, é um segredo da máquina e nunca
  vai para o Git (`config.json` está no `.gitignore`).

## O config

Tudo o que é específico da máquina vive em `config.json`, e só aí: o nome do
node, o endereço onde escuta e os serviços a vigiar. O código não embute
nenhum destes valores.

```json
{
  "node": { "name": "MEU-NODE" },
  "server": {
    "host": "127.0.0.1",
    "port": 8765,
    "allow_control": false,
    "token": null
  },
  "services": []
}
```

Uma máquina sem serviços é perfeitamente válida — o SENTINEL mostra a mesma
hardware e os discos, e os serviços aparecem como `N/A`.

### Serviços

Cada serviço descreve como existe naquela máquina:

| Campo | Para que serve |
|---|---|
| `type` | `comfyui`, `ollama` ou `process` |
| `name` | como aparece no SENTINEL |
| `path` / `python` | onde está instalado |
| `port` / `api_path` | como verificar se responde |
| `process_match` | como o identificar na lista de processos |
| `args` | comando de arranque (quando `allow_control` está activo) |

O SENTINEL nunca vê estes campos. Recebe só o estado.

### Expor na rede

Por omissão o LINK escuta em `127.0.0.1` e não aceita comandos. Para o usar
a partir de outra máquina:

```bash
neo-link --host 0.0.0.0
```

O `0.0.0.0` deixa-o acessível em todas as interfaces, incluindo a rede local.
Numa tailnet, o costume é limitar isso à interface do Tailscale e definir um
`token` no `config.json`.

## Pairing

Para não ter de passar tokens à mão, o LINK tem um modo de pairing:

```bash
neo-link --pair --host 0.0.0.0
```

Mostra um código curto e temporário. No SENTINEL, `neo-sentinel --pair`
valida-o e guarda a credencial. O código só serve uma vez.

Depois, o node fica disponível para monitorização.

## Comandos

```bash
neo-link              # serve a API
neo-link --check      # valida a configuração e mostra o estado dos serviços
neo-link --pair       # emparelha um SENTINEL
```

## Segurança

- Por omissão escuta apenas em `127.0.0.1` e `allow_control` é `false` — não
  arranca nem para nada.
- As credenciais resultantes do pairing são guardadas apenas como hash: ler o
  ficheiro não dá acesso.
- `config.json` e `credentials.json` são estado local e não devem ser
  versionados.

## Licença

MIT.
