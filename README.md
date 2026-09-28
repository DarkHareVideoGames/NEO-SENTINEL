# NEO//SENTINEL

Criei o NEO//SENTINEL para ter uma forma simples de acompanhar as máquinas
que fazem parte da minha infraestrutura, sem precisar de abrir uma página web
nem de estar sentado ao pé de cada máquina.

É um cliente de linha de comando, com interface de terminal. Corre num
telemóvel com Termux, num portátil com Linux, num Mac, ou num PC com Windows
(Git Bash ou WSL), e mostra o estado de cada node num ecrã só.

Este repositório tem as duas peças do sistema:

| Componente | Onde corre | O que faz |
|---|---|---|
| [NEO//SENTINEL](sentinel/) | onde observo | o cliente, com a interface |
| [NEO//LINK](link/) | em cada node | o agente que conhece a máquina |

## O problema que resolve

Cada máquina tem uma configuração diferente — caminhos, processos, portas. Não
queria que o cliente tivesse de conhecer esses detalhes, nem ter de o mudar de
cada vez que uma máquina muda.

Por isso separei as duas peças. O SENTINEL pergunta; o LINK responde com dados
estruturados. É isso que permite ao mesmo cliente mostrar um PC Windows com GPU
NVIDIA e um servidor Linux sem GPU nenhuma, sem qualquer alteração.

```
        NEO//SENTINEL
              │
           Tailscale
              │
      ┌───────┼───────┐
      ▼       ▼       ▼
     LINK    LINK    LINK
      │
   hardware, GPU,
   discos, serviços
```

Nada é inventado do lado do cliente: o que aparece no ecrã é o que o LINK
reportou. Se um dado não vier, aparece `N/A`.

## O que consigo monitorizar

Confirmado no código, o que a interface mostra hoje:

- **Identidade do node** — nome, hostname, sistema operativo, versão do agente
- **CPU** — percentagem de utilização e número de cores
- **RAM** — percentagem e total
- **GPU** — modelo, utilização, temperatura, VRAM
- **Discos** — percentagem usada em cada volume
- **Serviços** — estado de cada serviço configurado no node, e se a API
  responde

Os serviços são configurados no LINK, não no SENTINEL. É por isso que o
ComfyUI e o Ollama aparecem no meu setup, mas o sistema não assume que esses
são os serviços de ninguém.

## Instalar

### O SENTINEL (a máquina de onde observo)

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/install.sh | bash
```

Instala em `~/.neo-x1/sentinel/` e cria o comando `neo-sentinel`.

Precisa de **Python 3.9+**. Não precisa de `git`, de Docker, nem de root.

> O SENTINEL instala-se por shell. Num PC com Windows usa o
> [Git Bash](https://gitforwindows.org/) ou o WSL — a interface é uma TUI de
> terminal, por isso precisa de um terminal Unix para correr.

### O LINK (cada node a monitorizar)

O LINK tem instaladores nativos por plataforma.

**Windows 10/11 — PowerShell.** Não precisa de Bash, WSL, Git ou
Chocolatey:

```powershell
irm https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.ps1 -OutFile install.ps1
.\install.ps1 -Name MEU-NODE
```

**Linux, macOS e Termux — shell:**

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.sh | bash -s -- --name MEU-NODE
```

Instala em `~/.neo-x1/link` (ou `%USERPROFILE%\.neo-x1\link`) e cria o
comando `neo-link`. Depois é preciso descrever os serviços dessa máquina no
`config.json`.

Em qualquer plataforma podes também descarregar primeiro e correr depois —
é a forma recomendada, porque permite rever o script antes de o executar:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.sh -o install.sh
less install.sh
bash install.sh --name MEU-NODE
```

`--name` é opcional: sem ele, o node fica com o hostname da máquina.

### Onde corre o quê

| Plataforma | SENTINEL | LINK |
|---|---|---|
| Windows 10/11 | Git Bash ou WSL | **PowerShell nativo** |
| Linux | `install.sh` | `install.sh` |
| macOS | `install.sh` | `install.sh` |
| Termux / Android | `install.sh` | `install.sh` |

Os dois não precisam um do outro para instalar — o SENTINEL nunca vê o
código do LINK. Mais detalhe em [`link/README.md`](link/README.md).

Nenhum dos dois precisa de `git`, de Docker, ou de root.

## Primeiros passos

```bash
# no node que quero monitorizar
neo-link --pair --host 0.0.0.0     # mostra um código temporário

# na máquina de onde observo
neo-sentinel --pair                # peço o IP, o código e o nome
neo-sentinel --check               # confirmo que está tudo a responder
neo-sentinel                       # abro a interface
```

Na interface, `↑` e `↓` mudam de node, `r` força uma actualização, `q` sai.

## Pairing

O pairing pede três coisas, e nada mais:

```text
NEO//SENTINEL — PAIR NEW NODE

IP Tailscale:
> 100.69.16.82

Código:
> XXXX-XXXX

Nome da estação:
> AI-STATION
```

O IP Tailscale vem do `tailscale ip` no node. O código é o que o
`neo-link --pair` está a mostrar. O nome da estação é livre; se o deixar
vazio, o nome passa a ser o hostname real do node.

As portas e o esquema HTTP são detalhes internos do SENTINEL — não se escrevem,
não se veem e não se memorizam. O SENTINEL calcula o endpoint a partir do IP.

Cada node tem uma **identidade técnica** (`node_id`) gerada pelo LINK, que
sobrevive a reinícios e a renomeações. O `node_id` nunca é derivado do nome
nem do IP: mudar o nome da estação não cria um node novo, e a credencial
continua associada ao mesmo `node_id`.

Se colar uma URL onde se pede o IP, o SENTINEL recusa e explica o formato
esperado, em vez de tentar adivinhar o endereço.

Não gosto de pedir a alguém — nem a mim próprio — que copie tokens de um ecrã
para o outro. Por isso o pairing é a forma normal de adicionar um node.

1. Inicio o pairing no LINK.
2. O LINK mostra um código curto e temporário.
3. Introduzo esse código no SENTINEL.
4. O LINK valida o código e devolve uma credencial.
5. O SENTINEL guarda a credencial e o node passa a estar disponível.

O código tem uma validade curta e só serve uma vez. A credencial que fica
guardada é o que permite as consultas seguintes.

## Requisitos

- Python 3.9 ou superior
- Termux, no Android ([F-Droid](https://f-droid.org/packages/com.termux/))
- Acesso de rede aos nodes, tipicamente através de uma tailnet Tailscale

O SENTINEL só depende de `textual`. O LINK só depende de `psutil`. O resto vem
da biblioteca standard, por isso o mesmo código corre em qualquer lado.

## Segurança

Algumas notas, sem promessas que não possa cumprir:

- O SENTINEL é de **monitorização**. Por omissão não arranca nem para nada;
  isso depende de uma opção no LINK que está desligada.
- O LINK escuta apenas em `127.0.0.1` por omissão. `--host 0.0.0.0` deixa-o
  acessível em todas as interfaces, incluindo a rede local.
- O pairing estabelece uma credencial entre o cliente e o node. Quem
  configurou o LINK é que decide se aceita, e pode desligar o controlo a
  qualquer momento.
- O tráfego é pensado para funcionar dentro de uma tailnet, onde já vai
  cifrado.
- **Não publique tokens nem credenciais.** Vão para ficheiros locais que não
  devem ser versionados.

## Notas

Os instaladores aceitam `--check` (diagnóstico), `--update` (preserva a
configuração) e `--uninstall`. Em PowerShell são `-Check`, `-Update` e
`-Uninstall`. Mais detalhe em cada directório.

## Licença

MIT.
