# NEO//SENTINEL

O cliente. Corre onde observo, e apresenta o estado dos nodes.

Instala-se a partir da raiz do repositório:

```bash
curl -fsSL https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/install.sh | bash
```

Depois:

```bash
neo-sentinel --pair     # emparelhar um node
neo-sentinel --check    # diagnóstico
neo-sentinel            # interface
```

Cada node precisa do [NEO//LINK](../link/README.md) a correr. O SENTINEL não
conhece, nem precisa de conhecer, a configuração interna de cada máquina —
pergunta ao LINK de cada node e mostra o que recebe.

MIT.
