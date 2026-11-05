# A–C e histórico HTTP

Coleta de 04/11/2026 em 127.0.0.1:8000, com servidor e clientes automatizados no computador principal. Cinco contas distintas; sem conexões extras do navegador. A troca entre dois computadores foi demonstrada separadamente pela LAN.

Parâmetros: 50 pings após cinco aquecimentos; corpos de 256, 1024 e 16384 B, três rodadas de 10 s; 50 consultas HTTP com 100 mensagens por resposta; concorrência com 1, 2 e 5 conexões, três rodadas de 10 s e alvo de 10 mensagens/s por cliente. Os CSV registram durações e contagens efetivas. A–C e HTTP somam 20 rodadas, todas sem erro.

O coletor aguarda confirmação a cada envio. A vazão útil inclui a pausa de 10 ms e não representa capacidade máxima do enlace. Em C, o próprio evento recebido confirma a persistência pelo servidor. O banco cresce durante as rodadas e a ordem das condições é fixa. Esses resultados descrevem loopback, não o desempenho do Wi-Fi.
