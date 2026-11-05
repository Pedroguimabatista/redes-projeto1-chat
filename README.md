# Chat de Redes — Projeto 1

Aplicação Python com interface web, mensagens por WebSocket, histórico SQLite e alerta para a palavra URGENTE. Requer Python 3.12 e navegador. A instalação das dependências usa Internet.

## Instalar e iniciar

Extraia o ZIP. Abra o PowerShell na pasta que contém `server.py` e execute:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --ws-max-size 100000 --ws-per-message-deflate false
```

No Linux/macOS, crie o ambiente com `python3.12 -m venv .venv` e substitua `.\.venv\Scripts\python.exe` por `.venv/bin/python` nos comandos.

Mantenha o servidor aberto. Acesse http://127.0.0.1:8000/ no navegador. Na primeira execução, são criadas cinco contas em `config/users.json`. Consulte esse arquivo para entrar como `ana` e `bruno` em abas diferentes. Banco e logs são criados em `data/` e `logs/`.

Para usar dois computadores, conecte ambos à mesma rede. No computador servidor, consulte o IPv4 com `ipconfig`. No outro, abra `http://IP_DO_SERVIDOR:8000/` e entre com outra conta. Se o Windows solicitar, permita o Python na rede privada usada no teste. Caso a porta 8000 esteja ocupada, encerre a instância anterior antes de iniciar outra. Ctrl+C encerra o servidor.

## Testar o chat

1. Entre como `ana` em um cliente e como `bruno` no outro.
2. Envie `Teste 1` de Ana e `Recebido` de Bruno. Ambas devem aparecer nos dois clientes.
3. Envie `URGENTE: teste de alerta`. A mensagem deve ficar destacada.
4. Atualize a página e entre novamente. As mensagens devem reaparecer pelo histórico.
5. Encerre e reinicie o servidor pelo mesmo comando. Entre novamente: o histórico deve continuar salvo.

As contas são fictícias e suas senhas são geradas localmente. Não é necessário cadastrar usuários pela interface. HTTP/WS sem TLS destina-se ao laboratório; não use senhas pessoais. O protocolo está em `docs/PROTOCOLO.md`.

## Repetir A–C e histórico HTTP

Mantenha o chat ativo. Feche as abas do chat para não acrescentar conexões às do coletor. Use outro terminal na mesma pasta. O coletor usa as cinco contas geradas localmente e cada execução exige uma pasta de saída nova.

O histórico depende do estado do banco. Na coleta do artigo havia 100 mensagens por resposta HTTP. Para preparar essa condição em uma instalação nova, faça primeiro uma coleta preliminar, que também envia mensagens ao banco:

```powershell
.\.venv\Scripts\python.exe experiments/application.py --repeats 1 --out results/preparacao
```

Depois execute a coleta completa em outra pasta:

```powershell
.\.venv\Scripts\python.exe experiments/application.py --out results/nova-app
```

A coleta completa faz 50 pings JSON; três rodadas de 10 s para cada corpo de 256, 1024 e 16384 B; 50 consultas HTTP ao histórico; e três rodadas de 10 s com 1, 2 e 5 conexões. Leva alguns minutos e exporta amostras, resumos e metadados em CSV/JSON. A preparação não deve substituir os resultados da coleta completa.

Confira `http_history_messages` no novo `metadata.json`: deve ser 100 para comparar essa condição do artigo. Se ainda for menor, repita a preparação e a medição em outras pastas. Sem a preparação, um banco vazio produz uma série HTTP com zero mensagens. O conteúdo e o tamanho em bytes podem variar; os tempos também dependem do computador e do estado do banco.

## Repetir D — eco TCP/UDP

Em um terminal:

```powershell
.\.venv\Scripts\python.exe experiments/transport.py server
```

Em outro, na mesma pasta:

```powershell
.\.venv\Scripts\python.exe experiments/transport.py client --out results/novo-transporte
```

O endereço padrão é `127.0.0.1:9001`. São 50 tentativas por rodada, três rodadas, três tamanhos e dois transportes: 900 tentativas. O cliente grava CSV/JSON. Ctrl+C encerra o servidor de eco. Esse teste é independente do servidor do chat.

## Repetir E — resposta HTTP na LAN

1. Com o chat ativo, abra `http://IP_DO_SERVIDOR:8000/static/experimento-e.html` no Chrome do segundo computador.
2. Abra F12 → Network/Rede e mantenha o DevTools aberto. Selecione **No throttling/Sem limitação** e clique em **Coletar normal**.
3. Crie e selecione um perfil personalizado com latência de **100 ms**, Download/Upload vazios e sem perda configurada. Registre a configuração selecionada. Clique em **Coletar com atraso**.
4. Baixe o CSV e salve o JSON mostrado na página. Volte para **No throttling** ao terminar.

Cada condição faz cinco aquecimentos e 30 medidas de GET `/health`. A página mede até receber o corpo completo. A configuração é manual: 100 ms não significa somar exatamente 100 ms a cada resposta. O ensaio simula atraso HTTP no navegador, sem induzir perdas IP.

## Dados e Wireshark

As pastas abaixo são identificadas pelos experimentos. Os metadados registram as condições da coleta; o campo `out` conserva o nome da pasta na época da execução.

Os dados usados no relatório estão em `results/experimentos-A-B-C`, `results/experimento-D` e `results/experimento-E`. Cada pasta inclui amostras CSV, resumo, metadados, cenário e marcador `COMPLETE`. Use esses dados para conferir as tabelas; grave novas coletas em outras pastas. A–D foram medidos em loopback; E, pela LAN. Os gráficos e as três capturas comentadas já estão no PDF.

Para uma nova captura do chat, inicie o Wireshark antes de abrir as conexões. Na interface da LAN, use o filtro de captura `tcp port 8000`. Para A–D locais, selecione o adaptador de loopback; em D, use `tcp port 9001 or udp port 9001`. Registre data, interface, IPs, portas e ações. As numerações dos quadros de uma nova captura serão diferentes das do artigo.

`results/wireshark/correlacao-rtt.csv` contém o pareamento entre os pings e os quadros da captura original. As capturas brutas ficam locais porque podem conter credenciais e tokens. O arquivo `.gitignore` exclui contas, banco, logs e PCAPs. Para publicar o código, envie os arquivos extraídos preservando as subpastas e inclua também `.gitignore`.

O relatório PDF acompanha a entrega e contém o link do vídeo. O uso de OpenAI Codex está declarado no relatório.
