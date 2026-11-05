import argparse
import asyncio
import csv
from datetime import datetime, timezone
import getpass
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import time
import uuid

import httpx
from websockets.asyncio.client import connect


def save_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prepare_output(path):
    out = Path(path)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(
            "A pasta de resultados não está vazia. Escolha outro --out."
        )
    out.mkdir(parents=True, exist_ok=True)
    return out


def get_accounts(args):
    if args.user:
        password = getpass.getpass(f"Senha de {args.user}: ")
        return [(args.user, password)] * 5
    users = json.loads(Path(args.users).read_text(encoding="utf-8"))
    if not isinstance(users, dict) or len(users) < 5:
        raise ValueError("São necessárias 5 contas no arquivo, ou informe --user")
    return list(users.items())[:5]


async def main(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    accounts = get_accounts(args)
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "sem commit"
    metadata = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "revision": revision,
        "parameters": vars(args),
        "identity_mode": "conexões independentes da mesma conta"
        if args.user
        else "cinco contas distintas",
        "connections": [],
        "network_note": "Preencher topologia, hosts, enlace e perturbações em cenario.md",
        "method": "Relógio perf_counter no cliente; conexões persistentes; aquecimento de 5 pings; "
        "throughput stop-and-wait com confirmação de bytes, sem compressão WS; "
        "concorrência com carga por cliente fixa e mensagens persistidas; "
        "percentil 95 por posto mais próximo.",
    }

    def save_metadata():
        (out / "metadata.json").write_text(
            json.dumps(
                metadata,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    save_metadata()
    rows, summaries = [], []
    url = args.url.rstrip("/")
    ws_url = url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/ws"
    async with httpx.AsyncClient(timeout=10, trust_env=False) as http:
        tokens = []
        for user, password in accounts:
            response = await http.post(
                url + "/api/login", json={"user": user, "password": password}
            )
            response.raise_for_status()
            tokens.append(response.json()["token"])

        async def opened(token, phase):
            ws = await connect(ws_url, compression=None, proxy=None, max_size=200_000)
            await ws.send(json.dumps({"v": 1, "type": "auth", "token": token}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if reply["type"] != "auth_ok":
                raise RuntimeError("Autenticação WebSocket falhou")
            metadata["connections"].append(
                {
                    "phase": phase,
                    "user": reply["user"],
                    "connection_id": reply["connection_id"],
                }
            )
            return ws

        async def exchange(ws, kind, **fields):
            request_id = uuid.uuid4().hex
            data = json.dumps(
                {"v": 1, "type": kind, "request_id": request_id, **fields}
            )
            started = time.perf_counter()
            deadline = started + 10
            await asyncio.wait_for(ws.send(data), timeout=10)
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError("Operação sem confirmação em 10 segundos")
                reply = json.loads(await asyncio.wait_for(ws.recv(), remaining))
                match = (
                    reply.get("request_id") == request_id
                    or reply.get("message", {}).get("request_id") == request_id
                )
                if match:
                    if reply["type"] == "error":
                        raise RuntimeError(reply["detail"])
                    return (time.perf_counter() - started) * 1000, reply

        def record(group, latencies, duration, useful, messages, errors=0):
            operation, clients, size, repetition = group
            sorted_values = sorted(latencies)
            summary = dict(
                operation=operation,
                clients=clients,
                payload_bytes=size,
                repeat=repetition,
                samples=len(latencies),
                errors=errors,
                mean_ms=statistics.mean(latencies),
                median_ms=statistics.median(latencies),
                stdev_ms=statistics.stdev(latencies) if len(latencies) > 1 else 0,
                p95_ms=sorted_values[max(0, math.ceil(0.95 * len(latencies)) - 1)],
                duration_s=duration,
                messages=messages,
                messages_s=messages / duration,
                useful_bytes=useful,
                goodput_mbit_s=useful * 8 / duration / 1e6,
            )
            summaries.append(summary)
            for index, latency in enumerate(latencies):
                rows.append(
                    dict(
                        operation=operation,
                        clients=clients,
                        payload_bytes=size,
                        repeat=repetition,
                        sample=index,
                        latency_ms=latency,
                    )
                )
            save_csv(out / "measurements.csv", rows)
            save_csv(out / "summary.csv", summaries)
            save_metadata()
            print(
                f"{operation}: clientes={clients}, bytes={size}, repetição={repetition}, "
                f"n={len(latencies)}, média={summary['mean_ms']:.3f} ms"
            )

        ws = await opened(tokens[0], "rtt-throughput")
        try:
            for _ in range(5):
                await exchange(ws, "ping")
                await asyncio.sleep(0.02)
            started = time.perf_counter()
            latencies = []
            for _ in range(args.samples):
                latency, _ = await exchange(ws, "ping")
                latencies.append(latency)
                await asyncio.sleep(0.02)
            record(
                ("rtt", 1, 0, 1),
                latencies,
                time.perf_counter() - started,
                0,
                len(latencies),
            )

            # O limite de envio faz parte da metodologia, não mede a capacidade física do enlace.
            for size in [256, 1024, 16384]:
                payload = "x" * size
                for repeat in range(1, args.repeats + 1):
                    started = time.perf_counter()
                    latencies = []
                    useful = 0
                    while time.perf_counter() - started < args.duration:
                        latency, reply = await exchange(ws, "bench", payload=payload)
                        latencies.append(latency)
                        useful += reply["received_bytes"]
                        await asyncio.sleep(0.01)
                    record(
                        ("throughput", 1, size, repeat),
                        latencies,
                        time.perf_counter() - started,
                        useful,
                        len(latencies),
                    )
        finally:
            await ws.close()

        # Recebimento completo da resposta HTTP, mesma máquina/relógio, cliente persistente.
        headers = {"Authorization": "Bearer " + tokens[0]}
        response = await http.get(url + "/api/history?limit=100", headers=headers)
        response.raise_for_status()
        metadata["http_history_messages"] = len(response.json()["messages"])
        latencies = []
        started = time.perf_counter()
        volume = 0
        for _ in range(args.samples):
            tick = time.perf_counter()
            response = await http.get(url + "/api/history?limit=100", headers=headers)
            response.raise_for_status()
            latencies.append((time.perf_counter() - tick) * 1000)
            volume += len(response.content)
            await asyncio.sleep(0.02)
        record(
            ("http", 1, len(response.content), 1),
            latencies,
            time.perf_counter() - started,
            volume,
            len(latencies),
        )

        for count in [1, 2, 5]:
            for repeat in range(1, args.repeats + 1):
                sockets = [
                    await opened(token, f"concurrency-{count}-repeat-{repeat}")
                    for token in tokens[:count]
                ]
                start = time.perf_counter()

                async def worker(ws, index):
                    values = []
                    while time.perf_counter() - start < args.duration:
                        tick = time.perf_counter()
                        latency, _ = await exchange(
                            ws, "chat", text=f"Experimento C cliente {index}"
                        )
                        values.append(latency)
                        await asyncio.sleep(
                            max(0, 1 / args.rate - (time.perf_counter() - tick))
                        )
                    return values

                try:
                    values = await asyncio.gather(
                        *(worker(sock, i) for i, sock in enumerate(sockets))
                    )
                    elapsed = time.perf_counter() - start
                    flat = [v for group in values for v in group]
                    record(
                        ("concurrency", count, 0, repeat), flat, elapsed, 0, len(flat)
                    )
                finally:
                    await asyncio.gather(*(sock.close() for sock in sockets))
    (out / "COMPLETE").write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8"
    )
    print(f"Resultados: {out.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", default="config/users.json")
    parser.add_argument(
        "--user", help="Conta usada em todas as conexões; solicita senha sem salvá-la"
    )
    parser.add_argument(
        "--out", default="results/local-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rate", type=float, default=10)
    args = parser.parse_args()
    if (
        args.samples < 30
        or not math.isfinite(args.duration)
        or args.duration <= 0
        or args.repeats < 1
        or not 0 < args.rate <= 50
    ):
        parser.error("samples>=30, duration>0, repeats>=1 e 0<rate<=50")
    out = prepare_output(args.out)
    try:
        asyncio.run(main(args))
    except Exception as exc:
        (out / "FAILED.txt").write_text(
            f"{type(exc).__name__}: {exc}", encoding="utf-8"
        )
        raise
