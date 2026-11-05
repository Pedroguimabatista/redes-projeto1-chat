import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import socket
import socketserver
import statistics
import struct
import threading
import time


def exact(sock, size, deadline=None):
    data = bytearray()
    while len(data) < size:
        if deadline is not None:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise socket.timeout()
            sock.settimeout(remaining)
        part = sock.recv(size - len(data))
        if not part:
            raise ConnectionError("TCP encerrado antes do frame completo")
        data.extend(part)
    return bytes(data)


def prepare_output(path):
    out = Path(path)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(
            "A pasta de resultados não está vazia. Escolha outro --out."
        )
    out.mkdir(parents=True, exist_ok=True)
    return out


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(10)
            while True:
                header = exact(self.request, 4)
                size = struct.unpack("!I", header)[0]
                if not 8 <= size <= 1200:
                    return
                self.request.sendall(header + exact(self.request, size))
        except (OSError, ConnectionError):
            return


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        if 8 <= len(data) <= 1200:
            sock.sendto(data, self.client_address)


class TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(args):
    with (
        TCPServer((args.host, args.port), TCP) as tcp,
        socketserver.ThreadingUDPServer((args.host, args.port), UDP) as udp,
    ):
        udp.daemon_threads = True
        threading.Thread(target=tcp.serve_forever, daemon=True).start()
        print(
            f"Eco TCP e UDP em {args.host}:{args.port}. Ctrl+C para parar.", flush=True
        )
        try:
            udp.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            tcp.shutdown()


def measure(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "transport-metadata.json").write_text(
        json.dumps(
            {
                "utc": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
                "parameters": vars(args),
                "method": "Eco stop-and-wait; TCP com conexão persistente e frame de comprimento; "
                "UDP sem retransmissão da aplicação; timeout por amostra; ausentes podem incluir ida/volta e buffers. "
                "Tamanhos incluem 8 bytes do identificador; TCP acrescenta prefixo de 4 bytes. Verificar MTU.",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = []
    summaries = []
    for transport in ["tcp", "udp"]:
        for size in [128, 512, 1024]:
            for repeat in range(1, args.repeats + 1):
                kind = socket.SOCK_STREAM if transport == "tcp" else socket.SOCK_DGRAM
                with socket.socket(socket.AF_INET, kind) as sock:
                    sock.settimeout(args.timeout)
                    sock.connect((args.host, args.port))
                    seen = set()
                    latencies = []
                    duplicates = 0
                    late = 0
                    started = time.perf_counter()
                    successful_bytes = 0
                    for seq in range(args.samples):
                        data = struct.pack("!Q", seq) + b"x" * (size - 8)
                        tick = time.perf_counter()
                        status = "timeout"
                        latency = ""
                        try:
                            if transport == "tcp":
                                deadline = tick + args.timeout
                                sock.settimeout(args.timeout)
                                sock.sendall(struct.pack("!I", size) + data)
                                length = struct.unpack("!I", exact(sock, 4, deadline))[
                                    0
                                ]
                                if (
                                    length != size
                                    or exact(sock, length, deadline) != data
                                ):
                                    raise ValueError("Eco TCP divergente")
                            else:
                                sock.send(data)
                                while True:
                                    remaining = args.timeout - (
                                        time.perf_counter() - tick
                                    )
                                    if remaining <= 0:
                                        raise socket.timeout()
                                    sock.settimeout(remaining)
                                    reply = sock.recv(65535)
                                    if len(reply) < 8:
                                        continue
                                    received = struct.unpack("!Q", reply[:8])[0]
                                    if received in seen:
                                        duplicates += 1
                                        continue
                                    seen.add(received)
                                    if received != seq:
                                        late += 1
                                        continue
                                    if reply != data:
                                        raise ValueError("Eco UDP divergente")
                                    break
                            latency = (time.perf_counter() - tick) * 1000
                            latencies.append(latency)
                            successful_bytes += size
                            status = "ok"
                        except socket.timeout:
                            # Em TCP, após timeout a fronteira da resposta fica ambígua.
                            if transport == "tcp":
                                rows.append(
                                    dict(
                                        transport=transport,
                                        payload_bytes=size,
                                        repeat=repeat,
                                        seq=seq,
                                        status="timeout_abort",
                                        rtt_ms="",
                                    )
                                )
                                break
                        finally:
                            sock.settimeout(args.timeout)
                        rows.append(
                            dict(
                                transport=transport,
                                payload_bytes=size,
                                repeat=repeat,
                                seq=seq,
                                status=status,
                                rtt_ms=latency,
                            )
                        )
                        time.sleep(1 / args.rate)
                    elapsed = time.perf_counter() - started
                    attempted = sum(
                        r["transport"] == transport
                        and r["payload_bytes"] == size
                        and r["repeat"] == repeat
                        for r in rows
                    )
                    summaries.append(
                        dict(
                            transport=transport,
                            payload_bytes=size,
                            repeat=repeat,
                            attempted=attempted,
                            received_in_time=len(latencies),
                            missing_or_late=attempted - len(latencies),
                            duplicates=duplicates,
                            late_or_out_of_order=late,
                            mean_ms=statistics.mean(latencies) if latencies else "",
                            median_ms=statistics.median(latencies) if latencies else "",
                            stdev_ms=statistics.stdev(latencies)
                            if len(latencies) > 1
                            else 0,
                            duration_s=elapsed,
                            useful_bytes=successful_bytes,
                            goodput_mbit_s=successful_bytes * 8 / elapsed / 1e6,
                        )
                    )
                    for name, data_rows in [
                        ("transport.csv", rows),
                        ("transport-summary.csv", summaries),
                    ]:
                        with (out / name).open(
                            "w", newline="", encoding="utf-8"
                        ) as file:
                            writer = csv.DictWriter(file, fieldnames=list(data_rows[0]))
                            writer.writeheader()
                            writer.writerows(data_rows)
                    print(
                        transport,
                        size,
                        repeat,
                        f"{len(latencies)}/{attempted} ecos no prazo",
                    )
    (out / "COMPLETE").write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["server", "client"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=1)
    parser.add_argument("--rate", type=float, default=50)
    parser.add_argument(
        "--out",
        default="results/local-transport-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    args = parser.parse_args()
    if (
        args.samples < 30
        or args.repeats < 1
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
        or not math.isfinite(args.rate)
        or args.rate <= 0
    ):
        parser.error("samples>=30, repeats>=1, timeout>0 e rate>0")
    if args.mode == "server":
        serve(args)
    else:
        out = prepare_output(args.out)
        try:
            measure(args)
        except Exception as exc:
            (out / "FAILED.txt").write_text(
                f"{type(exc).__name__}: {exc}", encoding="utf-8"
            )
            raise
