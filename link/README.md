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

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.sh | bash -- --name MEU-NODE
```

O script instala as dependências, cria o `config.json` a partir do exemplo com
o nome indicado, e valida. Depois é preciso configurar os serviços dessa
máquina:

```bash
nano ~/.neo-x1/link/config.json
```

Ou, para conferir o que foi instalado:

```bash
bash install.sh --check
```

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
