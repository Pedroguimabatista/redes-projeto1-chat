# D — eco TCP/UDP

Coleta de 04/11/2026 em 127.0.0.1:9001, com servidor e cliente em processos distintos no computador principal. Corpos de 128, 512 e 1024 B; 50 tentativas, três repetições e dois transportes: 900 ecos recebidos dentro do prazo de 1 s. Os contadores não registraram duplicatas ou respostas atrasadas.

O método envia uma operação por vez, com pausa de 1/50 s. O corpo inclui sequência de 8 B. TCP acrescenta prefixo de 4 B e usa conexão persistente; seu estabelecimento fica fora do RTT. UDP não tem retransmissão de aplicação. O servidor TCP cria trabalhador por conexão; o UDP, por datagrama, diferença que também afeta os tempos. A ausência de ecos perdidos nesta execução não torna as garantias dos protocolos equivalentes.

A captura de apoio 04-tcp-udp-loopback.pcapng permanece local. As amostras estão em transport.csv; os resumos em transport-summary.csv; os parâmetros em transport-metadata.json.
