# SteamSwap

Faz um jogo da Steam abrir outro programa/jogo no lugar, para herdar o Steam Input (controle), overlay etc.

    python run.py
    python -m unittest discover -s tests

Sem dependências além do Python (Tkinter) e do `csc.exe` que já vem no Windows.

- A troca renomeia a pasta do jogo para `<pasta>.steamswap-bak` e coloca um launcher mínimo no lugar do exe que a Steam abre.
- O launcher espera o destino fechar (e, opcionalmente, qualquer processo da pasta do destino), para a Steam manter a sessão ativa.
- "Restaurar" apaga a pasta com o launcher e devolve o backup. Se houver arquivos que não são do SteamSwap ali, pergunta antes.
- Estado das trocas: `%APPDATA%\SteamSwap\swaps.json`.
- Se a Steam atualizar/verificar o jogo hospedeiro, a troca é desfeita; desative a atualização automática dele.

## Filtro de compatibilidade

A lista de jogos hospedeiros tem um filtro, ligado por padrão, que mostra só jogos
com **controle total** e **Remote Play Together** na Steam Store (categorias 28 e 44).
Isso não vem nos arquivos locais da Steam — na primeira abertura, o programa consulta
a Steam Store (`store.steampowered.com/api/appdetails`) uma vez por jogo, em segundo
plano, sem travar a interface. O resultado fica em `%APPDATA%\SteamSwap\compat_cache.json`
por 30 dias; "Verificar de novo" força uma nova consulta. Precisa de internet só para
essa checagem — sem ela, os jogos ainda não verificados ficam ocultos com o filtro ligado
(desligue o filtro para ver todos).
