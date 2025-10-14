# main.py
# Requisiti: fastapi, uvicorn, python-multipart
# pip install fastapi uvicorn python-multipart

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3, secrets, hashlib, time, html as html_lib

# ---------- CONFIG ----------
DB_FILE = "cards.db"
SESSION_TTL = 60 * 5   # 5 minuti sessione corta
SCAN_WINDOW = 30       # finestra in secondi entro cui /card è accessibile dopo /launch
DEVICE_COOKIE_NAME = "device_id"
SESSION_COOKIE_NAME = "session"
ADMIN_KEY = "bunald"   # CAMBIA questa stringa prima del deploy

app = FastAPI()

# ---------- DB INIT ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""        
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            token TEXT UNIQUE,
            pin_hash TEXT,
            balance REAL DEFAULT 0,
            bound_device_id TEXT,
            token_used INTEGER DEFAULT 0,
            description TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            sid TEXT PRIMARY KEY,
            token TEXT,
            expires INTEGER
        )
    """)
    # Migrazione: created_at su sessions
    try:
        cols = c.execute("PRAGMA table_info(sessions)").fetchall()
        names = {col[1] for col in cols}
        if "created_at" not in names:
            c.execute("ALTER TABLE sessions ADD COLUMN created_at INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migrazione: description su cards
    try:
        cols = c.execute("PRAGMA table_info(cards)").fetchall()
        names = {col[1] for col in cols}
        if "description" not in names:
            c.execute("ALTER TABLE cards ADD COLUMN description TEXT DEFAULT ''")
    except Exception:
        pass
    # Impostazioni aspetto
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id=1),
            bank_name TEXT,
            logo_url TEXT,
            gradient_from TEXT,
            gradient_to TEXT,
            font_name TEXT
        )
    """)
    # Inizializza riga settings
    row = c.execute("SELECT id FROM settings WHERE id = 1").fetchone()
    if not row:
        c.execute(
            "INSERT INTO settings (id, bank_name, logo_url, gradient_from, gradient_to, font_name) VALUES (1, ?, ?, ?, ?, ?)",
            ("Banca NFC", "", "#0ea5e9", "#8b5cf6", "Poppins")
        )
    conn.commit()
    conn.close()

init_db()

# ---------- HELPERS ----------
def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def create_site(name: str, pin: str, initial: float = 0.0, description: str = ""):
    token = secrets.token_urlsafe(16)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO cards (name, token, pin_hash, balance, description) VALUES (?, ?, ?, ?, ?)",
            (name, token, hash_pin(pin), float(initial), description.strip())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return None
    conn.close()
    return token

def get_by_token(token: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_device_id, token_used, description FROM cards WHERE token = ?", (token,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_device_id": r[5], "token_used": r[6], "description": r[7]}

def get_by_name(name: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_device_id, token_used, description FROM cards WHERE name = ?", (name,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_device_id": r[5], "token_used": r[6], "description": r[7]}

def mark_token_used(token: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET token_used = 1 WHERE token = ?", (token,))
    conn.commit()
    conn.close()

def bind_device_id(token: str, device_id: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET bound_device_id = ? WHERE token = ?", (device_id, token))
    conn.commit()
    conn.close()

def unbind_device_id(token: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET bound_device_id = NULL WHERE token = ?", (token,))
    conn.commit()
    conn.close()

def update_balance_by_token(token: str, newbal: float):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET balance = ? WHERE token = ?", (float(newbal), token))
    conn.commit()
    conn.close()

def adjust_balance(token: str, delta: float):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET balance = balance + ? WHERE token = ?", (float(delta), token))
    conn.commit()
    conn.close()

# Settings helpers
def get_settings():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    r = c.execute("SELECT bank_name, logo_url, gradient_from, gradient_to, font_name FROM settings WHERE id = 1").fetchone()
    conn.close()
    if not r:
        return {"bank_name": "Banca NFC", "logo_url": "", "gradient_from": "#0ea5e9", "gradient_to": "#8b5cf6", "font_name": "Poppins"}
    return {"bank_name": r[0] or "Banca NFC", "logo_url": r[1] or "", "gradient_from": r[2] or "#0ea5e9", "gradient_to": r[3] or "#8b5cf6", "font_name": r[4] or "Poppins"}

def update_settings(bank_name: str, logo_url: str, gradient_from: str, gradient_to: str, font_name: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "UPDATE settings SET bank_name = ?, logo_url = ?, gradient_from = ?, gradient_to = ?, font_name = ? WHERE id = 1",
        (bank_name.strip(), logo_url.strip(), gradient_from.strip(), gradient_to.strip(), font_name.strip())
    )
    conn.commit()
    conn.close()

# ---------- SESSIONS ----------
def create_session_for_token(token: str):
    sid = secrets.token_urlsafe(24)
    now = int(time.time())
    expires = now + SESSION_TTL
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO sessions (sid, token, expires, created_at) VALUES (?, ?, ?, ?)", (sid, token, expires, now))
    conn.commit()
    conn.close()
    return sid

def get_session_info(sid: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT token, expires, created_at FROM sessions WHERE sid = ?", (sid,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    token, expires, created_at = r
    now = int(time.time())
    if now > (expires or 0):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
        conn.commit()
        conn.close()
        return None
    return {"token": token, "expires": int(expires or 0), "created_at": int(created_at or 0)}

def delete_session(sid: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
    conn.commit()
    conn.close()

# ---------- COOKIE UTILS ----------
def is_https(request: Request) -> bool:
    xf = request.headers.get("x-forwarded-proto", "")
    return (request.url.scheme == "https") or ("https" in xf)

def set_cookie(resp, name, value, max_age=None, httponly=False, request: Request = None):
    secure = is_https(request) if request is not None else False
    resp.set_cookie(
        name,
        value,
        max_age=max_age,
        samesite="Lax",
        httponly=httponly,
        secure=secure,
        path="/",
    )

# ---------- PAGE RENDER ----------
def render_page(inner_html: str, title: str = "") -> HTMLResponse:
    s = get_settings()
    title_text = title or s["bank_name"]
    font = s["font_name"] or "Poppins"
    google_font = font.replace(" ", "+")
    logo_html = f"<img src='{html_lib.escape(s['logo_url'])}' alt='' style='height:28px;margin-right:10px;border-radius:6px'>" if s["logo_url"] else ""
    header_name = html_lib.escape(s["bank_name"])
    html = f"""
    <!doctype html>
    <html lang="it">
    <head>
      <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{html_lib.escape(title_text)}</title>
      <link href="https://fonts.googleapis.com/css2?family={google_font}:wght@400;600;700&display=swap" rel="stylesheet">
      <style>
        :root {{
          --grad-from: {html_lib.escape(s["gradient_from"])};
          --grad-to: {html_lib.escape(s["gradient_to"])};
        }}
        body {{
          margin:0; padding:0;
          font-family: '{font}', system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
          background: linear-gradient(135deg, var(--grad-from), var(--grad-to));
          min-height: 100vh; color: #0f172a;
        }}
        .wrap {{ max-width: 980px; margin: 0 auto; padding: 16px; }}
        .nav {{
          display:flex; align-items:center; justify-content:space-between;
          background: rgba(255,255,255,0.85); backdrop-filter: blur(6px);
          border:1px solid rgba(255,255,255,0.6); border-radius:14px; padding:10px 14px; margin: 16px 0;
        }}
        .brand {{ display:flex; align-items:center; font-weight:700; font-size:18px; }}
        .nav a, .nav button {{ text-decoration:none; color:#0f172a; font-weight:600; }}
        .content {{
          background: rgba(255,255,255,0.92); backdrop-filter: blur(6px);
          border:1px solid rgba(255,255,255,0.6); border-radius:16px; padding:18px; margin:16px 0;
        }}
        .btn {{ display:inline-block; padding:10px 14px; border-radius:10px; border:none; cursor:pointer; font-weight:600; }}
        .btn.primary {{ background:#0ea5e9; color:white; }}
        .btn.secondary {{ background:#64748b; color:white; }}
        .btn.danger {{ background:#ef4444; color:white; }}
        .btn.success {{ background:#10b981; color:white; }}
        .grid {{ display:grid; gap:12px; }}
        @media(min-width:720px){{ .grid.cols-2 {{ grid-template-columns:1fr 1fr; }} .grid.cols-3 {{ grid-template-columns:1fr 1fr 1fr; }} }}
        input, select {{ width:100%; padding:10px; border-radius:10px; border:1px solid #e2e8f0; box-sizing:border-box; }}
        table {{ width:100%; border-collapse:collapse; }}
        th, td {{ padding:10px; border-bottom:1px solid #e2e8f0; text-align:left; }}
        .muted {{ color:#475569; font-size:13px; }}
        .pill {{ display:inline-block; padding:2px 8px; border-radius:999px; background:#e2e8f0; color:#334155; font-size:12px; }}
      </style>
    </head>
    <body>
      <div class="wrap">
        <div class="nav">
          <div class="brand">{logo_html}<span>{header_name}</span></div>
          <div style="display:flex; gap:8px;">
            <a class="btn" href="/">Home</a>
          </div>
        </div>
        <div class="content">
          {inner_html}
        </div>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

def fmt_bonsaura(amount: float) -> str:
    try:
        return f"{float(amount):.2f} Bonsaura"
    except Exception:
        return f"{amount} Bonsaura"

# ---------- ROUTES ----------

# Home: pannello con tasti Admin e Lista (richiedono key)
@app.get("/", response_class=HTMLResponse)
def home():
    inner = """
      <h2>Benvenuto</h2>
      <p class="muted">Accedi al pannello amministrativo o visualizza la lista carte. Inserisci la key.</p>
      <form class="grid cols-3" method="get" action="/go">
        <div><input name="key" placeholder="Chiave admin" required></div>
        <div><button class="btn primary" type="submit" name="dest" value="admin">Admin</button></div>
        <div><button class="btn secondary" type="submit" name="dest" value="lista">Lista</button></div>
      </form>
      <p class="muted">Per usare una carta: avvicina il telefono al tag NFC (URL /launch/...). Le pagine delle carte si aprono solo subito dopo il tap.</p>
    """
    return render_page(inner, "Home")

# Dispatcher per tasti home
@app.get("/go", response_class=HTMLResponse)
def go(dest: str = "", key: str = ""):
    if dest == "admin":
        return RedirectResponse(url=f"/admin?key={key}", status_code=302)
    if dest == "lista":
        return RedirectResponse(url=f"/lista?key={key}", status_code=302)
    return render_page("<h3>Destinazione non valida</h3>", "Errore")

# Crea carta (rapido via link – opzionale)
@app.get("/create", response_class=HTMLResponse)
def create_via_link(name: str = "", code: str = "", initial: float = 0.0, desc: str = "", request: Request = None):
    if not name or not code:
        return render_page("<h3>Parametri mancanti. Usa ?name=...&code=...&initial=...&desc=...</h3>", "Errore")
    # limite 10 carte
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM cards")
    count = c.fetchone()[0]
    conn.close()
    if count >= 10:
        return render_page("<h3>Hai già raggiunto il limite di 10 carte.</h3>", "Limite")
    token = create_site(name, code, initial, desc)
    if not token:
        return render_page("<h3>Errore: esiste già una carta con quel nome.</h3>", "Errore")
    base = str(request.base_url).rstrip("/")
    url = f"{base}/launch/{token}"
    inner = f"""
      <h3>✅ Carta '{html_lib.escape(name)}' creata</h3>
      <p>Saldo iniziale: {fmt_bonsaura(initial)}</p>
      <p>URL da scrivere sul tag NFC:</p>
      <pre>{html_lib.escape(url)}</pre>
    """
    return render_page(inner, "Carta creata")

# LAUNCH: URL da scrivere sul tag: /launch/<token>
@app.get("/launch/{token}")
@app.head("/launch/{token}")
def launch(token: str, request: Request):
    site = get_by_token(token)
    if not site:
        return render_page("<h3>Tag non valido.</h3>", "Errore")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if site["bound_device_id"] and (not device_id or site["bound_device_id"] != device_id):
        return render_page("<h3>Accesso non autorizzato</h3><p>Questa carta è associata ad un altro dispositivo.</p>", "Bloccato")
    resp = RedirectResponse(url="/card", status_code=302)
    if not device_id:
        device_id = secrets.token_hex(16)
        set_cookie(resp, DEVICE_COOKIE_NAME, device_id, max_age=60*60*24*365, httponly=True, request=request)
    sid = create_session_for_token(token)
    set_cookie(resp, SESSION_COOKIE_NAME, sid, max_age=SESSION_TTL, httponly=True, request=request)
    return resp

# CARD: accessibile solo entro SCAN_WINDOW secondi dal /launch
@app.get("/card", response_class=HTMLResponse)
def card_from_session(request: Request):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        return render_page(f"<h3>Sessione mancante</h3><p>Avvicina la carta NFC per accedere (entro {SCAN_WINDOW}s).</p>", "Richiesto NFC")
    session = get_session_info(sid)
    if not session:
        return render_page(f"<h3>Sessione scaduta</h3><p>Riavvicina la carta NFC (entro {SCAN_WINDOW}s).</p>", "Scaduta")
    now = int(time.time())
    if now - (session["created_at"] or 0) > SCAN_WINDOW:
        return render_page(f"<h3>Sessione non valida</h3><p>Per accedere devi avvicinare ora la carta NFC (entro {SCAN_WINDOW}s).</p>", "Non valida")
    token = session["token"]
    site = get_by_token(token)
    if not site:
        return render_page("<h3>Tag non valido.</h3>", "Errore")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if site["bound_device_id"] and (not device_id or site["bound_device_id"] != device_id):
        return render_page("<h3>Accesso non autorizzato</h3><p>Questa carta è associata ad un altro dispositivo.</p>", "Bloccato")
    inner = f"""
      <h2>{html_lib.escape(site["name"])}</h2>
      <p><strong>Saldo:</strong> {fmt_bonsaura(site["balance"])}</p>
      <p class="muted">{html_lib.escape(site.get("description") or "")}</p>
      <h4>Invia denaro</h4>
      <form method="post" action="/unlock">
        <input type="hidden" name="token" value="{html_lib.escape(site['token'])}">
        <input name="pin" type="password" placeholder="PIN" required><br>
        <button class="btn primary" type="submit">Accedi</button>
      </form>
      <p class="muted">Completa l’accesso entro {SCAN_WINDOW}s dal tap NFC.</p>
    """
    return render_page(inner, site["name"])

# UNLOCK: verifica PIN; se primo accesso binda device_id; poi mostra saldo/transfer
@app.post("/unlock", response_class=HTMLResponse)
def unlock(request: Request, token: str = Form(...), pin: str = Form(...)):
    site = get_by_token(token)
    if not site:
        return render_page("<h3>Token non valido.</h3>", "Errore")
    if site["pin_hash"] != hash_pin(pin):
        return render_page("<h3>PIN errato.</h3><p><a href='/card'>Riprova</a></p>", "PIN errato")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_id:
        return render_page("<h3>Device cookie mancante. Riavvicina la carta NFC.</h3>", "Cookie mancante")
    if not site["bound_device_id"]:
        bind_device_id(token, device_id)
        mark_token_used(token)
        site = get_by_token(token)
    if site["bound_device_id"] != device_id:
        return render_page("<h3>Accesso non autorizzato: dispositivo non registrato.</h3>", "Bloccato")
    inner = f"""
      <h2>{html_lib.escape(site["name"])}</h2>
      <p><strong>Saldo:</strong> {fmt_bonsaura(site["balance"])}</p>
      <p class="muted">{html_lib.escape(site.get("description") or "")}</p>
      <h4>Invia denaro</h4>
      <form method="post" action="/transfer">
        <input type="hidden" name="from_token" value="{html_lib.escape(site['token'])}">
        <input name="to_name" placeholder="Nome carta destinatario" required><br>
        <input name="amount" type="number" step="0.01" placeholder="Importo in Bonsaura" required><br>
        <button class="btn primary" type="submit">Invia</button>
      </form>
      <p class="muted">Carta associata a questo dispositivo. Per spostarla usa il reset binding in Admin.</p>
    """
    return render_page(inner, site["name"])

# TRANSFER: solo se la carta mittente è bindata a questo device
@app.post("/transfer", response_class=HTMLResponse)
def transfer(request: Request, from_token: str = Form(...), to_name: str = Form(...), amount: str = Form(...)):
    from_site = get_by_token(from_token)
    if not from_site:
        return render_page("<h3>Mittente non trovato.</h3>", "Errore")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not from_site["bound_device_id"] or from_site["bound_device_id"] != device_id:
        return render_page("<h3>Accesso non autorizzato per trasferimento.</h3>", "Bloccato")
    to_name = to_name.strip()
    if to_name == "":
        return render_page("<h3>Nome destinatario non valido.</h3>", "Errore")
    try:
        amt = float(amount)
    except Exception:
        return render_page("<h3>Importo non valido.</h3>", "Errore")
    if amt <= 0:
        return render_page("<h3>Importo deve essere positivo.</h3>", "Errore")
    if from_site["balance"] < amt:
        return render_page(f"<h3>Saldo insufficiente. Hai {fmt_bonsaura(from_site['balance'])}</h3>", "Errore")
    dest = get_by_name(to_name)
    if not dest:
        new_token = secrets.token_urlsafe(12)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO cards (name, token, pin_hash, balance, description) VALUES (?, ?, ?, ?, ?)",
                  (to_name, new_token, hash_pin("0000"), 0.0, ""))
        conn.commit(); conn.close()
        dest = get_by_name(to_name)
    update_balance_by_token(from_site["token"], from_site["balance"] - amt)
    update_balance_by_token(dest["token"], dest["balance"] + amt)
    return render_page(f"<h3>Trasferimento di {fmt_bonsaura(amt)} a {html_lib.escape(to_name)} effettuato.</h3><p><a href='/card'>Torna</a></p>", "OK")

# ---------- ADMIN / LISTA ----------
def require_key(key: str) -> bool:
    return key == ADMIN_KEY

@app.get("/lista", response_class=HTMLResponse)
def lista(key: str = ""):
    if not require_key(key):
        return render_page("<h3>Accesso negato</h3>", "403")
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    rows = c.execute("SELECT name, balance, bound_device_id, token_used, description FROM cards ORDER BY id").fetchall()
    conn.close()
    body_rows = ""
    for name, balance, bound, used, desc in rows:
        bound_disp = "associata" if bound else "libera"
        used_disp = "sì" if used else "no"
        body_rows += f"""
          <tr>
            <td>{html_lib.escape(name)}</td>
            <td>{fmt_bonsaura(balance)}</td>
            <td><span class="pill">{bound_disp}</span></td>
            <td>{used_disp}</td>
            <td>{html_lib.escape(desc or '')}</td>
          </tr>
        """
    inner = f"""
      <h2>Lista carte</h2>
      <table>
        <thead><tr><th>Nome</th><th>Saldo</th><th>Binding</th><th>Usata</th><th>Descrizione</th></tr></thead>
        <tbody>{body_rows or '<tr><td colspan="5" class="muted">Nessuna carta</td></tr>'}</tbody>
      </table>
      <p class="muted">Questa vista non mostra i token.</p>
    """
    return render_page(inner, "Lista carte")

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, key: str = ""):
    if key != ADMIN_KEY:
        return render_page("<h3>Accesso negato</h3>", "403")
    s = get_settings()
    base = str(request.base_url).rstrip("/")

    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    rows = c.execute("SELECT name, token, balance, bound_device_id, description FROM cards ORDER BY id").fetchall()
    conn.close()

    cards_html = ""
    for name, token, balance, bound, desc in rows:
        bind_state = "associata" if bound else "libera"
        token_html = html_lib.escape(token)
        url_nfc = html_lib.escape(f"{base}/launch/{token}")
        cards_html += f"""
          <tr>
            <td>
              {html_lib.escape(name)}<br>
              <span class="muted">token:</span> <code>{token_html}</code><br>
              <div style="display:flex;gap:6px;margin-top:6px">
                <button class="btn" type="button" onclick="copyText('{token_html}')">Copia token</button>
                <button class="btn secondary" type="button" onclick="copyText('{url_nfc}')">Copia URL NFC</button>
              </div>
            </td>
            <td>{fmt_bonsaura(balance)}</td>
            <td><span class="pill">{bind_state}</span></td>
            <td>{html_lib.escape(desc or '')}</td>
            <td style="min-width:280px">
              <form style="display:inline-block" method="post" action="/admin/adjust">
                <input type="hidden" name="key" value="{html_lib.escape(key)}">
                <input type="hidden" name="token" value="{token_html}">
                <input name="delta" type="number" step="0.01" placeholder="+/- Bonsaura" required style="width:130px">
                <button class="btn success" type="submit">Applica</button>
              </form>
              <form style="display:inline-block" method="post" action="/admin/reset">
                <input type="hidden" name="key" value="{html_lib.escape(key)}">
                <input type="hidden" name="token" value="{token_html}">
                <button class="btn secondary" type="submit">Reset binding</button>
              </form>
              <form style="display:inline-block" method="post" action="/admin/delete">
                <input type="hidden" name="key" value="{html_lib.escape(key)}">
                <input type="hidden" name="token" value="{token_html}">
                <button class="btn danger" type="submit" onclick="return confirm('Eliminare la carta?')">Elimina</button>
              </form>
            </td>
          </tr>
        """

    inner = f"""
      <h2>Admin</h2>
      <div class="grid cols-2">
        <div>
          <h3>Crea nuova carta</h3>
          <form method="post" action="/admin/create">
            <input type="hidden" name="key" value="{html_lib.escape(key)}">
            <input name="name" placeholder="Nome carta" required>
            <input name="pin" placeholder="PIN" required>
            <input name="initial" type="number" step="0.01" placeholder="Saldo iniziale (Bonsaura)" value="0">
            <input name="desc" placeholder="Descrizione (opzionale)">
            <button class="btn primary" type="submit">Crea</button>
          </form>
        </div>
        <div>
          <h3>Personalizzazione sito</h3>
          <form method="post" action="/admin/settings">
            <input type="hidden" name="key" value="{html_lib.escape(key)}">
            <input name="bank_name" placeholder="Nome banca" value="{html_lib.escape(s['bank_name'])}">
            <input name="logo_url" placeholder="Logo URL (https://...)" value="{html_lib.escape(s['logo_url'])}">
            <input name="gradient_from" placeholder="Gradiente da (es. #0ea5e9)" value="{html_lib.escape(s['gradient_from'])}">
            <input name="gradient_to" placeholder="Gradiente a (es. #8b5cf6)" value="{html_lib.escape(s['gradient_to'])}">
            <input name="font_name" placeholder="Font (Google Fonts)" value="{html_lib.escape(s['font_name'])}">
            <button class="btn primary" type="submit">Salva stile</button>
          </form>
        </div>
      </div>

      <h3>Carte</h3>
      <table>
        <thead><tr><th>Nome/Token</th><th>Saldo</th><th>Binding</th><th>Descrizione</th><th>Azioni</th></tr></thead>
        <tbody>{cards_html or '<tr><td colspan="5" class="muted">Nessuna carta</td></tr>'}</tbody>
      </table>

      <script>
      function copyText(t) {{
        if (navigator.clipboard && navigator.clipboard.writeText) {{
          navigator.clipboard.writeText(t).then(() => {{ alert('Copiato negli appunti'); }})
            .catch(() => {{ window.prompt('Copia manualmente:', t); }});
        }} else {{
          window.prompt('Copia manualmente:', t);
        }}
      }}
      </script>
    """
    return render_page(inner, "Admin")

@app.post("/admin/create", response_class=HTMLResponse)
def admin_create(name: str = Form(""), pin: str = Form(""), initial: float = Form(0.0), desc: str = Form(""), key: str = Form("")):
    if not require_key(key):
        return render_page("<h3>Accesso negato</h3>", "403")
    if not name or not pin:
        return render_page("<h3>Fornisci nome e PIN</h3>", "Errore")
    # limite 10 carte
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM cards"); count = c.fetchone()[0]
    conn.close()
    if count >= 10:
        return render_page("<h3>Hai già raggiunto il limite di 10 carte.</h3>", "Limite")
    token = create_site(name, pin, initial, desc)
    if not token:
        return render_page("<h3>Errore: nome già esistente.</h3>", "Errore")
    return RedirectResponse(url=f"/admin?key={key}", status_code=302)

@app.post("/admin/adjust", response_class=HTMLResponse)
def admin_adjust(token: str = Form(""), delta: float = Form(0.0), key: str = Form("")):
    if not require_key(key):
        return render_page("<h3>Accesso negato</h3>", "403")
    site = get_by_token(token)
    if not site:
        return render_page("<h3>Carta non trovata</h3>", "Errore")
    try:
        d = float(delta)
    except Exception:
        return render_page("<h3>Delta non valido</h3>", "Errore")
    adjust_balance(token, d)
    return RedirectResponse(url=f"/admin?key={key}", status_code=302)

@app.post("/admin/reset", response_class=HTMLResponse)
def admin_reset(token: str = Form(""), key: str = Form("")):
    if not require_key(key):
        return render_page("<h3>Accesso negato</h3>", "403")
    site = get_by_token(token)
    if not site:
        return render_page("<h3>Token non trovato</h3>", "Errore")
    unbind_device_id(token)
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("UPDATE cards SET token_used = 0 WHERE token = ?", (token,))
    conn.commit(); conn.close()
    return RedirectResponse(url=f"/admin?key={key}", status_code=302)

@app.post("/admin/delete", response_class=HTMLResponse)
def admin_delete(token: str = Form(""), key: str = Form("")):
    if not require_key(key):
        return render_page("<h3>Accesso negato</h3>", "403")
    site = get_by_token(token)
    if not site:
        return render_page("<h3>Token non trovato</h3>", "Errore")
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("DELETE FROM cards WHERE token = ?", (token,))
    conn.commit(); conn.close()
    return RedirectResponse(url=f"/admin?key={key}", status_code=302)

@app.post("/admin/settings", response_class=HTMLResponse)
def admin_settings(bank_name: str = Form(""), logo_url: str = Form(""), gradient_from: str = Form(""), gradient_to: str = Form(""), font_name: str = Form(""), key: str = Form("")):
    if not require_key(key):
        return render_page("<h3>Accesso negato</h3>", "403")
    update_settings(bank_name or "Banca NFC", logo_url or "", gradient_from or "#0ea5e9", gradient_to or "#8b5cf6", font_name or "Poppins")
    return RedirectResponse(url=f"/admin?key={key}", status_code=302)

# Back-compat: vecchia lista admin
@app.get("/admin/list", response_class=HTMLResponse)
def admin_list(key: str = ""):
    return lista(key=key)

# ---------- NOTE ----------
# - Le pagine carta si aprono solo subito dopo un tap NFC: /card richiede una sessione creata da /launch negli ultimi SCAN_WINDOW secondi.
# - Valuta visualizzata come "Bonsaura".
# - Home con pannello Admin/Lista (entrambe protette da key).
# - Personalizzazione aspetto: logo, gradiente, font (Google Fonts).
