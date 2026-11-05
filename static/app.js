const $ = id => document.getElementById(id);
let token = null, me = null, ws = null, stopped = true, retry = 0, timer = null;
let generation = 0;
let lastId = 0, seen = new Set(), pending = new Map();
function status(text) { $('status').textContent = text; }
function error(text = '') { $('error').textContent = text; }
function pendingLabel() { $('pending').textContent = pending.size ? `${pending.size} mensagem(ns) aguardando confirmação.` : ''; }
function render(m) {
  if (seen.has(m.id)) return;
  seen.add(m.id);
  const el = document.createElement('article'); el.className = 'message' + (m.alert ? ' alert' : '');
  el.dataset.id = m.id;
  const meta = document.createElement('span'); meta.className = 'meta';
  meta.textContent = `${m.user} · ${new Date(m.timestamp).toLocaleString()} · #${m.id}${m.alert ? ' · ALERTA' : ''}`;
  const body = document.createElement('div'); body.className = 'body'; body.textContent = m.text;
  el.append(meta, body);
  const following = [...$('messages').children].find(child => Number(child.dataset.id) > m.id);
  $('messages').insertBefore(el, following || null);
  $('messages').scrollTop = $('messages').scrollHeight;
}
async function syncHistory(socket, session) {
  const current = () => session === generation && socket === ws && !stopped;
  const sessionToken = token, sessionUser = me;
  // Cursor HTTP separado: eventos ao vivo podem chegar durante a paginação.
  while (true) {
    const res = await fetch(`/api/history?after=${lastId}&limit=500`, {headers: {Authorization: `Bearer ${sessionToken}`}});
    if (!current()) return false;
    if (res.status === 401) { logout(); throw new Error('Sessão expirada. Entre novamente.'); }
    if (!res.ok) throw new Error('Não foi possível recuperar o histórico.');
    const {messages} = await res.json();
    if (!current()) return false;
    for (const m of messages) {
      render(m); lastId = Math.max(lastId, m.id);
      if (m.user === sessionUser) pending.delete(m.request_id);
    }
    pendingLabel();
    if (messages.length < 500) return true;
  }
}
function transmitPending() {
  for (const packet of pending.values()) ws.send(JSON.stringify(packet));
}
function connect() {
  if (stopped) return;
  status('Conectando…');
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  const socket = ws, session = generation;
  socket.onopen = () => socket.send(JSON.stringify({v:1,type:'auth',token}));
  socket.onmessage = async event => {
    if (socket !== ws || session !== generation || stopped) return;
    try {
      const p = JSON.parse(event.data);
      if (p.type === 'auth_ok') {
        status('Sincronizando histórico…');
        if (!await syncHistory(socket, session)) return;
        if (socket !== ws || stopped || socket.readyState !== WebSocket.OPEN) return;
        retry = 0; error(); status('Conectado');
        transmitPending(); $('send').disabled = false;
      } else if (p.type === 'message') {
        render(p.message);
        if (p.message.user === me) pending.delete(p.message.request_id);
        pendingLabel();
      } else if (p.type === 'error') {
        if (p.request_id) pending.delete(p.request_id);
        pendingLabel(); error(p.detail);
        if (p.code === 'AUTH') logout();
      }
    } catch (err) {
      if (socket !== ws || session !== generation || stopped) return;
      error(err.message); socket.close();
    }
  };
  socket.onclose = event => {
    if (socket !== ws) return;
    $('send').disabled = true;
    if (stopped) return;
    if (event.code === 1008) { logout(); error('Entre novamente para continuar.'); return; }
    const delay = Math.min(1000 * 2 ** retry++, 10000);
    status(`Reconectando em ${delay/1000}s…`); timer = setTimeout(connect, delay);
  };
  socket.onerror = () => {
    if (socket === ws && session === generation && !stopped)
      error('Servidor indisponível. Tentaremos reconectar automaticamente.');
  };
}
function logout() {
  generation++;
  stopped = true; clearTimeout(timer); if (ws) ws.close();
  ws = null; token = null; me = null; pending.clear(); pendingLabel();
  $('login').hidden = false; $('chat').hidden = true; $('send').disabled = true; status('Desconectado');
}
$('loginForm').onsubmit = async e => {
  e.preventDefault(); error();
  const attempt = ++generation;
  try {
    const res = await fetch('/api/login', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user:$('user').value.trim(),password:$('password').value})});
    const data = await res.json();
    if (attempt !== generation) return;
    if (!res.ok) throw new Error(data.detail || 'Falha no login');
    token = data.token; me = data.user; $('password').value = ''; stopped = false;
    lastId = 0; seen.clear(); pending.clear(); $('messages').replaceChildren();
    $('identity').textContent = `Conectado como ${me}`;
    $('login').hidden = true; $('chat').hidden = false; connect();
  } catch (err) { if (attempt === generation) error(err.message); }
};
$('sendForm').onsubmit = e => {
  e.preventDefault(); error();
  const text = $('text').value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN || $('send').disabled) return;
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const request_id = Array.from(bytes, b => b.toString(16).padStart(2,'0')).join('');
  const p = {v:1,type:'chat',request_id,text}; pending.set(request_id,p); pendingLabel();
  ws.send(JSON.stringify(p)); $('text').value = ''; $('text').focus();
};
$('logout').onclick = logout;
