import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("CHAT_DB", str(ROOT / "data" / "chat.sqlite3")))
USERS_FILE = Path(os.environ.get("CHAT_USERS", str(ROOT / "config" / "users.json")))
MAX_FRAME = 100_000
SESSION_SECONDS = 8 * 3600
log = logging.getLogger("chat")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_users():
    """Credenciais descartáveis do laboratório, nunca incluídas no Git."""
    if not USERS_FILE.exists():
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        USERS_FILE.write_text(
            json.dumps(
                {
                    name: secrets.token_urlsafe(12)
                    for name in ["ana", "bruno", "carla", "diego", "elisa"]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    if (
        not isinstance(users, dict)
        or not users
        or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in users.items()
        )
    ):
        raise RuntimeError("Arquivo de usuários inválido")
    return users


@contextmanager
def connection():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def history(after=0, limit=200):
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM messages WHERE id > ? ORDER BY id LIMIT ?", (after, limit)
        ).fetchall()
    return [dict(row) | {"alert": bool(row["alert"])} for row in rows]


def persist(user, request_id, text):
    with connection() as db:
        old = db.execute(
            "SELECT * FROM messages WHERE user=? AND request_id=?", (user, request_id)
        ).fetchone()
        if old:
            if old["text"] != text:
                raise ValueError("request_id já utilizado com outro conteúdo")
            return dict(old) | {"alert": bool(old["alert"])}, False
        alert = bool(re.search(r"\burgente\b", text, re.IGNORECASE))
        cursor = db.execute(
            "INSERT INTO messages(user,request_id,text,timestamp,alert) VALUES(?,?,?,?,?)",
            (user, request_id, text, now(), int(alert)),
        )
        row = db.execute(
            "SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row) | {"alert": bool(row["alert"])}, True


@dataclass
class Peer:
    ws: WebSocket
    user: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))


async def close_socket(ws, code, reason=""):
    try:
        await ws.close(code=code, reason=reason)
    except (RuntimeError, WebSocketDisconnect):
        pass  # A outra ponta pode já ter encerrado a conexão.


class Hub:
    def __init__(self):
        self.peers = {}

    def broadcast(self, packet):
        # Um receptor lento não bloqueia a sala. Ele deve recuperar o histórico.
        for key, peer in list(self.peers.items()):
            try:
                peer.queue.put_nowait(packet)
            except asyncio.QueueFull:
                self.peers.pop(key, None)
                asyncio.create_task(
                    close_socket(peer.ws, 1013, "Cliente lento; reconecte")
                )
                log.warning("slow_client user=%s connection=%s", peer.user, key)


@asynccontextmanager
async def lifespan(app):
    DB.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        ROOT / "logs" / "server.log",
        maxBytes=2_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    app.state.users = load_users()
    app.state.sessions = {}
    app.state.hub = Hub()
    app.state.login_attempts = {}
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL,
            request_id TEXT NOT NULL, text TEXT NOT NULL, timestamp TEXT NOT NULL,
            alert INTEGER NOT NULL, UNIQUE(user,request_id))""")
    log.info("startup database=%s", DB.name)
    yield
    for peer in list(app.state.hub.peers.values()):
        await peer.ws.close(code=1001)
    log.info("shutdown")
    log.removeHandler(handler)
    handler.close()


app = FastAPI(title="Chat de Redes", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def access_log(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    log.info(
        "http method=%s path=%s status=%d duration_ms=%.3f",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
def health():
    return {"status": "online", "protocol": "chat/1", "timestamp": now()}


@app.post("/api/login")
async def login(request: Request):
    # Limite de corpo e de tentativas para esta demonstração em LAN.
    raw = await request.body()
    if len(raw) > 2048:
        raise HTTPException(413, "Requisição muito grande")
    try:
        data = json.loads(raw)
        user, password = data["user"], data["password"]
        if not isinstance(user, str) or not isinstance(password, str):
            raise ValueError()
        user.encode("utf-8")
        password.encode("utf-8")
    except (ValueError, KeyError, TypeError, RecursionError):
        raise HTTPException(400, "Informe user e password como strings")
    host = request.client.host if request.client else "unknown"
    attempts = app.state.login_attempts
    cutoff = time.monotonic() - 60
    attempts[host] = [t for t in attempts.get(host, []) if t > cutoff]
    if len(attempts[host]) >= 30:
        raise HTTPException(429, "Aguarde um minuto para tentar novamente")
    expected = app.state.users.get(user, "")
    if not expected or not hmac.compare_digest(password.encode(), expected.encode()):
        attempts[host].append(time.monotonic())
        raise HTTPException(401, "Credenciais inválidas")
    sessions = app.state.sessions
    for key, (_, deadline) in list(sessions.items()):
        if deadline < time.monotonic():
            sessions.pop(key, None)
    token = secrets.token_urlsafe(32)
    sessions[token] = (user, time.monotonic() + SESSION_SECONDS)
    log.info("login user=%s", user)
    return {"token": token, "user": user, "expires_in": SESSION_SECONDS}


def session_user(token):
    item = app.state.sessions.get(token) if isinstance(token, str) else None
    if not item or item[1] < time.monotonic():
        return None
    return item[0]


def authorized(request: Request):
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    user = session_user(token)
    if not user:
        raise HTTPException(401, "Entre novamente")
    return user


@app.get("/api/history")
def get_history(
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    user: str = Depends(authorized),
):
    return {"messages": history(after, limit)}


def packet(kind, **fields):
    return {"v": 1, "type": kind, **fields}


async def read_packet(ws):
    frame = await ws.receive()
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    raw = frame.get("text")
    if raw is None:
        raise ValueError("Use frames WebSocket de texto JSON, não binários")
    if len(raw.encode("utf-8")) > MAX_FRAME:
        raise ValueError("Mensagem excede 100000 bytes")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("JSON inválido")
    if (
        not isinstance(value, dict)
        or type(value.get("v")) is not int
        or value["v"] != 1
    ):
        raise ValueError("Informe um objeto JSON com v=1")
    return value


async def writer(peer):
    while True:
        await asyncio.wait_for(peer.ws.send_json(await peer.queue.get()), timeout=5)


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    peer, task, expiry, key = None, None, None, uuid.uuid4().hex
    try:
        first = await asyncio.wait_for(read_packet(ws), timeout=10)
        user = session_user(first.get("token")) if first.get("type") == "auth" else None
        if not user:
            await ws.send_json(
                packet("error", code="AUTH", detail="Autenticação necessária")
            )
            await ws.close(code=1008)
            return
        peer = Peer(ws, user)
        await ws.send_json(packet("auth_ok", user=user, connection_id=key))
        app.state.hub.peers[key] = peer
        task = asyncio.create_task(writer(peer))
        log.info("connect user=%s connection=%s", user, key)
        # Se o escritor falhar, encerra também a leitura para não manter cliente fantasma.
        task.add_done_callback(
            lambda _: (
                asyncio.create_task(close_socket(ws, 1013))
                if not task.cancelled()
                else None
            )
        )
        remaining = app.state.sessions[first["token"]][1] - time.monotonic()
        expiry = asyncio.get_running_loop().call_later(
            max(0, remaining),
            lambda: asyncio.create_task(close_socket(ws, 1008, "Sessão expirada")),
        )
        recent = []
        while True:
            request_id = None
            try:
                data = await read_packet(ws)
                request_id = data.get("request_id")
                if not isinstance(request_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,64}", request_id
                ):
                    raise ValueError(
                        "request_id deve ter 1 a 64 caracteres alfanuméricos, _ ou -"
                    )
                if not session_user(first.get("token")):
                    await ws.close(code=1008, reason="Sessão expirada")
                    return
                tick = time.monotonic()
                recent = [t for t in recent if tick - t < 1]
                if len(recent) >= 200:
                    raise ValueError("Limite de 200 operações por segundo por conexão")
                recent.append(tick)
                kind = data.get("type")
                if kind == "ping":
                    peer.queue.put_nowait(packet("pong", request_id=request_id))
                elif kind == "bench":
                    payload = data.get("payload")
                    if (
                        not isinstance(payload, str)
                        or len(payload.encode("utf-8")) > 65536
                    ):
                        raise ValueError("payload deve ser string de até 65536 bytes")
                    peer.queue.put_nowait(
                        packet(
                            "bench_ack",
                            request_id=request_id,
                            received_bytes=len(payload.encode("utf-8")),
                        )
                    )
                elif kind == "chat":
                    text = data.get("text")
                    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
                        raise ValueError("Texto deve conter de 1 a 2000 caracteres")
                    try:
                        text.encode("utf-8")
                    except UnicodeError:
                        raise ValueError(
                            "Texto deve conter Unicode UTF-8 válido"
                        ) from None
                    if set(data) - {"v", "type", "request_id", "text"}:
                        raise ValueError(
                            "Campos extras não permitidos em chat; identidade vem da sessão"
                        )
                    message, created = persist(user, request_id, text.strip())
                    event = packet("message", message=message)
                    if created:
                        app.state.hub.broadcast(event)
                    else:
                        peer.queue.put_nowait(event)
                    log.info(
                        "message user=%s id=%d request_id=%s alert=%s duplicate=%s",
                        user,
                        message["id"],
                        request_id,
                        message["alert"],
                        not created,
                    )
                else:
                    raise ValueError("Tipo desconhecido: use chat, ping ou bench")
            except asyncio.QueueFull:
                await ws.close(code=1013, reason="Fila cheia; reconecte")
                return
            except (ValueError, TypeError) as exc:
                log.warning("invalid_message user=%s connection=%s", user, key)
                try:
                    peer.queue.put_nowait(
                        packet(
                            "error",
                            code="INVALID",
                            detail=str(exc),
                            request_id=request_id
                            if isinstance(request_id, str)
                            else None,
                        )
                    )
                except asyncio.QueueFull:
                    await close_socket(ws, 1013, "Fila cheia; reconecte")
                    return
    except (WebSocketDisconnect, RuntimeError):
        pass
    except (ValueError, asyncio.TimeoutError) as exc:
        log.warning("handshake_error connection=%s reason=%s", key, type(exc).__name__)
        try:
            await ws.close(code=1008)
        except RuntimeError:
            pass
    except sqlite3.Error:
        log.exception("database_error connection=%s", key)
        await ws.close(code=1011, reason="Armazenamento indisponível")
    finally:
        app.state.hub.peers.pop(key, None)
        if expiry:
            expiry.cancel()
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        log.info("disconnect connection=%s", key)
