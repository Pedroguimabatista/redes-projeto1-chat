# Protocolo chat/1

Uma sala, servidor central, mensagens JSON UTF-8 sobre HTTP/1.1 e WebSocket/TCP. IPv4/IPv6 dependem do enlace; execução recomendada com um processo e IPv4 na LAN. WebSocket permite envio bidirecional pelo navegador; TCP oferece fluxo confiável e ordenado enquanto a conexão funciona. SQLite persiste mensagens, não sessões. A escolha dispensa polling contínuo. O microteste UDP é separado, não substitui o serviço de chat.

## HTTP

- `GET /`: interface; `GET /static/*`: recursos locais; `GET /health`: estado e horário UTC.
- `POST /api/login`: `{"user":"ana","password":"senha do arquivo local"}`. Retorna `token`, `user`, `expires_in` (28800 segundos). Credenciais ficam em `config/users.json`, gerado localmente, ignorado pelo Git. Não usar credenciais reais.
- `GET /api/history?after=0&limit=200`: cabeçalho `Authorization: Bearer TOKEN`; retorna `{"messages":[...]}`. Ordem crescente do ID global; `after` exclusivo, limite 1 a 500. Repetir páginas para recuperar histórico inteiro.
- HTTP: 400 corpo inválido, 401 credenciais/sessão inválida, 413 login grande, 422 parâmetros de consulta inválidos, 429 excesso de tentativas. As respostas de erro usam `detail`.

## WebSocket

Conectar a `/ws`, autenticar em até 10 segundos com primeiro frame:

```json
{"v":1,"type":"auth","token":"TOKEN_OBTIDO_POR_HTTP"}
```

Servidor responde `{"v":1,"type":"auth_ok","user":"ana","connection_id":"..."}`. Identidade vem da sessão. São permitidas várias conexões por usuário; demonstrar dois usuários diferentes. IDs de mensagem são compartilhados por usuário, não por conexão.

Cada operação posterior exige `v=1` inteiro e `request_id` string de 1 a 64 caracteres em `[A-Za-z0-9_-]`. Usar UUID aleatório por operação nova. Frames devem ser texto JSON; limite de 100000 bytes no executor. Tipos:

| Cliente → servidor | Resposta/efeito |
| --- | --- |
| `{"v":1,"type":"chat","request_id":"m1","text":"URGENTE: reunião"}` | Persiste e distribui `message` a todos, inclusive remetente |
| `{"v":1,"type":"ping","request_id":"p1"}` | `{"v":1,"type":"pong","request_id":"p1"}`; ping da aplicação, diferente do frame de controle WS |
| `{"v":1,"type":"bench","request_id":"b1","payload":"xxxx"}` | `bench_ack` com `request_id` e `received_bytes`; não persiste nem distribui |

Texto de chat: 1 a 2000 caracteres após remover espaços externos. Somente campos `v,type,request_id,text`; remetente não pode ser escolhido no corpo. `payload` de bench: até 65536 bytes UTF-8. Limite de 200 operações/s/conexão; o experimento usa carga abaixo dele.

Exemplo de evento distribuído:

```json
{"v":1,"type":"message","message":{"id":1,"user":"ana","request_id":"m1","text":"URGENTE: reunião","timestamp":"2026-11-03T15:00:00.000+00:00","alert":true}}
```

O horário é exemplo de formato, não medição. A palavra inteira `URGENTE`, sem distinguir maiúsculas, ativa a regra. O alerta fica salvo junto da mensagem e é destacado nos clientes; não depende de aviso manual do servidor. `id` inteiro global define ordenação no histórico; `timestamp` UTC é informativo e não deve ser usado para subtrair relógios de máquinas diferentes.

Sequência: autenticar → validar → inserir/confirmar transação SQLite → distribuir. Receber o próprio evento confirma que o servidor persistiu a mensagem, mas não prova que todos os destinatários já a exibiram. Reenvio com mesmo usuário + `request_id` + texto retorna a mensagem original somente ao remetente, sem duplicar a sala; ID igual com outro texto gera erro.

Erro recuperável: `{"v":1,"type":"error","code":"INVALID","detail":"...","request_id":"m1"}`. `AUTH` exige novo login. JSON/versão/tipo/texto inválidos geram erro sem matar sessão já autenticada. Fechamentos: 1008 autenticação/timeout inicial/sessão expirada; 1013 cliente lento/fila cheia; 1011 banco indisponível; 1001 encerramento do servidor. Frame que ultrapassa o limite do executor pode fechar com 1009.

## Reconexão, ordem e limites

- Cliente tenta reconectar após 1, 2, 4, 8 e no máximo 10 s; autentica e sincroniza histórico HTTP antes de habilitar envio. Fila de mensagens pendentes só existe enquanto a aba estiver aberta. Após atualizar a página ou sair, é necessário novo login.
- Mensagens pendentes são reenviadas com o mesmo ID. Histórico usa cursor próprio e IDs para evitar duplicatas e ordenar eventos que cheguem durante a sincronização.
- Sessões expiram em oito horas e conexões ociosas também são encerradas no prazo. Após reiniciar o servidor, sessões em memória deixam de valer; a interface pede novo login. As mensagens SQLite permanecem. Fila por conexão: 128 eventos; receptor lento é desconectado e recupera histórico depois.
- Os logs UTC registram método/caminho/status/duração, conexões, IDs de mensagens e falhas; não registram texto, senha ou token.
- Arquitetura central tem ponto único de falha. Escritas SQLite são curtas e síncronas, potencial gargalo sob carga alta. Estado de conexões/sessões em memória exige **um worker**. Não há garantia de entrega exatamente uma vez a todos; há deduplicação da persistência e recuperação por histórico.
- HTTP/WS sem TLS é configuração para laboratório isolado com dados e credenciais descartáveis. Internet exigiria HTTPS/WSS, armazenamento seguro de senhas, proteção contra abuso mais robusta e gestão de sessões. `textContent` evita interpretar mensagens como HTML; SQL usa parâmetros. Não publicar credenciais ou capturas sem revisão.
