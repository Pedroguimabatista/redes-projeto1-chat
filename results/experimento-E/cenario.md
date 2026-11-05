# E — resposta HTTP na LAN

Coleta de 04/11/2026 pelo Chrome 156 em Windows no segundo computador, acessando http://192.168.1.103:8000/static/experimento-e.html. A página fez GET /health com cache desativado e identificador exclusivo. Foram cinco aquecimentos e 30 medidas por condição, com pausa de 50 ms fora da medição. As 70 respostas tiveram status HTTP 200 e corpo de 83 B.

Primeiro foi usado No throttling; depois, um perfil manual de latência de 100 ms no Chrome DevTools, sem limite de download/upload e sem perda configurada. O coletor não verifica automaticamente o perfil. performance.now mediu do envio até a leitura completa do corpo no mesmo cliente. A média passou de 75,877 para 111,630 ms; o parâmetro não representa acréscimo exato de 100 ms.

Houve uma série por condição, em ordem fixa e com intervalo para ajustar o perfil. O método simula atraso HTTP no navegador, sem alterar o enlace físico ou induzir perdas IP. Os relógios absolutos dos dois computadores não foram subtraídos. measurements.csv preserva a coleta original, incluindo os aquecimentos; summary.csv usa apenas as 30 medidas por condição.

A captura 05-e-lan.pcapng é preparatória e não foi usada como evidência dos tempos medidos de E. Ferramenta: https://developer.chrome.com/docs/devtools/settings/throttling .
