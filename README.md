# SteamSwap

Faz um jogo da Steam abrir outro programa/jogo no lugar, para herdar o Steam Input (controle), overlay etc.

    pip install -r requirements.txt
    python run.py
    python -m unittest discover -s tests

A lógica (`steam.py`, `swap.py`, `stub.py`, `compat.py`, `shortcuts.py`) usa só a biblioteca
padrão do Python, mais o `csc.exe` que já vem no Windows. A interface usa
[pywebview](https://pywebview.flowrl.com/) (HTML/CSS/JS num `WebView2`, com tema escuro no
estilo da Steam) — daí a única dependência externa do projeto. As páginas estão em
`steamswap/webui/`; a ponte Python↔JS é `steamswap/webapi.py`.

- A troca renomeia a pasta do jogo para `<pasta>.steamswap-bak` e coloca um launcher mínimo no lugar do exe que a Steam abre.
- O launcher espera o destino fechar (e, opcionalmente, qualquer processo da pasta do destino), para a Steam manter a sessão ativa.
- "Restaurar" apaga a pasta com o launcher e devolve o backup. Se houver arquivos que não são do SteamSwap ali, pergunta antes.
- Estado das trocas: `%APPDATA%\SteamSwap\swaps.json`.
- Se a Steam atualizar/verificar o jogo hospedeiro, a troca é desfeita; desative a atualização automática dele.

## Vários destinos por hospedeiro

Um mesmo hospedeiro pode ter vários destinos cadastrados ("+ Adicionar destino").
Cada um vira um **atalho de verdade (.lnk) na Área de Trabalho** (dá pra desligar
essa opção por destino). Clicar num atalho específico marca "abra este destino
agora" (arquivo em `%APPDATA%\SteamSwap\next_target\<appid>.txt`) e manda a
Steam abrir o hospedeiro; o launcher lê essa marca uma única vez.

Se você abrir o jogo direto pela Steam (sem passar por um atalho específico) e
houver mais de um destino, aparece uma telinha para escolher, com o destino
padrão pré-selecionado e um tempo limite — se ninguém decidir, ela mesma abre
o padrão, pra nunca travar esperando teclado/mouse no modo Big Picture.

"Remover" tira um destino (e apaga o atalho dele); precisa sobrar pelo menos
um — pra tirar o último, use "Restaurar original". "Definir padrão" muda qual
deles abre quando não há atalho específico envolvido.

## Nota técnica: atributos de `Api`

Tudo que `webapi.Api` guarda (janela, cache, lista de jogos...) tem nome começando
com `_`. Isso não é estilo — é necessário: o pywebview monta a lista de métodos
expostos ao JS varrendo com `dir()` os atributos *públicos* do objeto e, para
qualquer um que não seja um método, desce recursivamente dentro dele. Um atributo
público guardando a própria `Window` faz essa varredura cair em propriedades .NET
(`native.AccessibilityObject.Bounds.Empty...`) que devolvem um objeto novo a cada
acesso — e como a trava de recursão do pywebview é por `id()`, isso nunca detecta
o ciclo e estoura a pilha, travando a ponte JS↔Python inteira sem erro visível na
página. `tests/test_webapi.py::ApiExposureShapeTests` garante que isso não volta.

## Filtro de compatibilidade

A lista de jogos hospedeiros tem um filtro, ligado por padrão, que mostra só jogos
com **controle total** e **Remote Play Together** na Steam Store (categorias 28 e 44).
Isso não vem nos arquivos locais da Steam — na primeira abertura, o programa consulta
a Steam Store (`store.steampowered.com/api/appdetails`) uma vez por jogo, em segundo
plano, sem travar a interface. O resultado fica em `%APPDATA%\SteamSwap\compat_cache.json`
por 30 dias; "Verificar de novo" força uma nova consulta. Precisa de internet só para
essa checagem — sem ela, os jogos ainda não verificados ficam ocultos com o filtro ligado
(desligue o filtro para ver todos).

## Build e auto-atualização

    python build.py

Compila `dist\SteamSwap.exe` com o [Nuitka](https://nuitka.net/) (modo `--onefile`,
sem console, `webui/` embutido). A versão vem de `steamswap/version.py`, fonte
única lida também pelo atualizador.

O `.exe` compilado verifica sozinho, na abertura, se há uma versão mais nova
publicada nas [Releases do GitHub](https://github.com/viiniciusnapoleao-bot/SteamSwap/releases)
deste repositório (`steamswap/version.py:GITHUB_REPO`) e mostra um aviso na
interface se houver. Rodando via `python run.py` a partir do código-fonte essa
checagem não faz nada — não há `.exe` pra substituir.

Pra publicar uma versão:
1. Suba `APP_VERSION` em `steamswap/version.py`.
2. `python build.py`.
3. Crie uma Release no GitHub com tag `v<versão>` (ex.: `v1.1.0`) e anexe
   `dist\SteamSwap.exe` (o nome do arquivo importa — é ele que o atualizador procura).

Ao aceitar a atualização, o SteamSwap baixa o `.exe` novo, compila um pequeno
relançador em C# (mesmo mecanismo do launcher/seletor) que espera este processo
fechar, copia o arquivo novo por cima do antigo e reabre — o Windows não deixa
um programa sobrescrever a si mesmo em execução.
