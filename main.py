# main.py
# Requisiti: fastapi, uvicorn, python-multipart
# pip install fastapi uvicorn python-multipart

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3, secrets, hashlib, time, html as html_lib

# ---------- CONFIG ----------
DB_FILE = "cards.db"
SESSION_TTL = 60 * 5  # 5 minuti sessione corta
SCAN_WINDOW = 30      # finestra in secondi entro cui /card è accessibile dopo /launch
DEVICE_COOKIE_NAME = "device_id"
SESSION_COOKIE_NAME = "session"
ADMIN_KEY = "bunald"  # CAMBIA questa stringa prima del deploy

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
            token_used INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            sid TEXT PRIMARY KEY,
            token TEXT,
            expires INTEGER
        )
    """)
    # Migrazione: aggiungi created_at a sessions se manca
    try:
        cols = c.execute("PRAGMA table_info(sessions)").fetchall()
        names = {col[1] for col in cols}
        if "created_at" not in names:
            c.execute("ALTER TABLE sessions ADD COLUMN created_at INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass
    conn.commit()
    conn.close()

init_db()

# ---------- HELPERS ----------
def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def create_site(name: str, pin: str, initial: float = 0.0):
    token = secrets.token_urlsafe(16)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO cards (name, token, pin_hash, balance) VALUES (?, ?, ?, ?)",
            (name, token, hash_pin(pin), float(initial))
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
    c.execute("SELECT id, name, token, pin_hash, balance, bound_device_id, token_used FROM cards WHERE token = ?", (token,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_device_id": r[5], "token_used": r[6]}

def get_by_name(name: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_device_id, token_used FROM cards WHERE name = ?", (name,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_device_id": r[5], "token_used": r[6]}

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

# ---------- SESSIONS ----------
def create_session_for_token(token: str):
    sid = secrets.token_urlsafe(24)
    now = int(time.time())
    expires = now + SESSION_TTL
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # created_at usato per enforcement “solo dopo NFC”
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
        # scaduta: cleanup
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

# ---------- ROUTES ----------

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse("""
    <h3>Scanner NFC richiesto</h3>
    <p>Avvicina la carta NFC per accedere. Se non hai una carta, contatta l'admin.</p>
    """, status_code=403)

# ---- Creation via link: /create?name=&code=&initial=
@app.get("/create", response_class=HTMLResponse)
def create_via_link(name: str = "", code: str = "", initial: float = 0.0, key: str = "", request: Request = None):
    if not name or not code:
        return HTMLResponse("<h3>Parametri mancanti. Usa ?name=...&code=...&initial=...</h3>", status_code=400)

    # limit: max 5
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM cards")
    count = c.fetchone()[0]
    conn.close()
    if count >= 5:
        return HTMLResponse("<h3>Hai già raggiunto il limite di 5 banche.</h3>", status_code=400)

    token = create_site(name, code, initial)
    if not token:
        return HTMLResponse("<h3>Errore: esiste già una banca con quel nome.</h3>", status_code=400)

    base = str(request.base_url).rstrip("/")
    url = f"{base}/launch/{token}"
    return HTMLResponse(f"""
    <h3>✅ Banca '{html_lib.escape(name)}' creata con successo!</h3>
    <p>Saldo iniziale: {initial:.2f} €</p>
    <p>Scrivi sul tag NFC questo URL:</p>
    <pre>{html_lib.escape(url)}</pre>
    <p>Reset binding: /admin/reset/&lt;token&gt;?key={html_lib.escape(ADMIN_KEY)}</p>
    """)

# LAUNCH: URL da scrivere sul tag: /launch/<token>
# - Se carta già bindata e device non coincide => blocco
# - Crea sessione breve e redirige a /card
@app.get("/launch/{token}")
@app.head("/launch/{token}")
def launch(token: str, request: Request):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Tag non valido.</h3>", status_code=404)

    device_id = request.cookies.get(DEVICE_COOKIE_NAME)

    if site["bound_device_id"] and (not device_id or site["bound_device_id"] != device_id):
        return HTMLResponse(
            "<h3>Accesso non autorizzato</h3><p>Questa carta è associata ad un altro dispositivo.</p>",
            status_code=403
        )

    resp = RedirectResponse(url="/card", status_code=302)

    if not device_id:
        device_id = secrets.token_hex(16)
        set_cookie(resp, DEVICE_COOKIE_NAME, device_id, max_age=60*60*24*365, httponly=True, request=request)

    sid = create_session_for_token(token)
    set_cookie(resp, SESSION_COOKIE_NAME, sid, max_age=SESSION_TTL, httponly=True, request=request)

    return resp

# CARD: accessibile solo entro SCAN_WINDOW secondi dal /launch (effetto "solo via NFC")
@app.get("/card", response_class=HTMLResponse)
def card_from_session(request: Request):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        # Non hai uno scan recente -> blocco
        return HTMLResponse(f"<h3>Sessione mancante</h3><p>Avvicina la carta NFC per accedere (entro {SCAN_WINDOW}s).</p>", status_code=403)

    session = get_session_info(sid)
    if not session:
        return HTMLResponse(f"<h3>Sessione scaduta</h3><p>Riavvicina la carta NFC (entro {SCAN_WINDOW}s).</p>", status_code=403)

    now = int(time.time())
    if now - (session["created_at"] or 0) > SCAN_WINDOW:
        return HTMLResponse(f"<h3>Sessione non valida</h3><p>Per accedere devi avvicinare ora la carta NFC (entro {SCAN_WINDOW}s).</p>", status_code=403)

    token = session["token"]
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Tag non valido.</h3>", status_code=404)

    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if site["bound_device_id"] and (not device_id or site["bound_device_id"] != device_id):
        return HTMLResponse("<h3>Accesso non autorizzato</h3><p>Questa carta è associata ad un altro dispositivo.</p>", status_code=403)

    html = """
    <!doctype html>
    <html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>{name}</title>
    <style>
      body{{font-family:Arial;background:#f5f7fb;padding:20px}}
      .card{{max-width:420px;margin:18px auto;background:#fff;padding:18px;border-radius:12px;box-shadow:0 8px 20px rgba(0,0,0,0.08)}}
      input{{width:100%;padding:12px;margin-top:8px;border-radius:8px;border:1px solid #e6e9ef;box-sizing:border-box}}
      button{{width:100%;padding:12px;margin-top:12px;border-radius:8px;background:#0066ff;color:#fff;border:none}}
    </style>
    </head><body>
      <div class="card">
        <h2>{name}</h2>
        <p>Inserisci il PIN per sbloccare questa carta su questo dispositivo.</p>
        <form method="post" action="/unlock">
          <input type="hidden" name="token" value="{token}">
          <input name="pin" type="password" placeholder="PIN" required><br>
          <button type="submit">Accedi</button>
        </form>
        <p style="color:#666;font-size:12px">Consiglio: tieni il telefono sulla carta e completa l'accesso subito (finestra {scan}s).</p>
      </div>
    </body></html>
    """.format(name=html_lib.escape(site["name"]), token=html_lib.escape(site["token"]), scan=SCAN_WINDOW)

    return HTMLResponse(html)

# UNLOCK: verifica PIN; se primo accesso binda device_id; poi mostra saldo/transfer
@app.post("/unlock", response_class=HTMLResponse)
def unlock(request: Request, token: str = Form(...), pin: str = Form(...)):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non valido.</h3>", status_code=404)

    if site["pin_hash"] != hash_pin(pin):
        return HTMLResponse("<h3>PIN errato.</h3><p><a href='/card'>Riprova</a></p>", status_code=403)

    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_id:
        return HTMLResponse("<h3>Device cookie mancante. Riavvicina la carta NFC.</h3>", status_code=403)

    if not site["bound_device_id"]:
        bind_device_id(token, device_id)
        mark_token_used(token)
        site = get_by_token(token)

    if site["bound_device_id"] != device_id:
        return HTMLResponse("<h3>Accesso non autorizzato: dispositivo non registrato.</h3>", status_code=403)

    bal = float(site["balance"])
    html = """
    <!doctype html>
    <html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>{name}</title>
    <style>
      body{{font-family:Arial;background:#f5f7fb;padding:20px}}
      .card{{max-width:420px;margin:18px auto;background:#fff;padding:18px;border-radius:12px;box-shadow:0 8px 20px rgba(0,0,0,0.08)}}
      input{{width:100%;padding:12px;margin-top:8px;border-radius:8px;border:1px solid #e6e9ef;box-sizing:border-box}}
      button{{width:100%;padding:12px;margin-top:12px;border-radius:8px;background:#0066ff;color:#fff;border:none}}
      .small{{font-size:13px;color:#666;margin-top:10px}}
    </style>
    </head><body>
      <div class="card">
        <h2>{name}</h2>
        <p><strong>Saldo:</strong> {balance:.2f} €</p>
        <h4>Invia denaro</h4>
        <form method="post" action="/transfer">
          <input type="hidden" name="from_token" value="{token}">
          <input name="to_name" placeholder="Nome banca destinatario" required><br>
          <input name="amount" type="number" step="0.01" placeholder="Importo" required><br>
          <button type="submit">Invia</button>
        </form>
        <p class="small">Carta associata a questo dispositivo. Per spostarla usa l'admin per resettare l'associazione.</p>
      </div>
    </body></html>
    """.format(name=html_lib.escape(site["name"]), balance=bal, token=html_lib.escape(site["token"]))

    return HTMLResponse(html)

# TRANSFER: solo se la carta mittente è bindata a questo device
@app.post("/transfer", response_class=HTMLResponse)
def transfer(request: Request, from_token: str = Form(...), to_name: str = Form(...), amount: str = Form(...)):
    from_site = get_by_token(from_token)
    if not from_site:
        return HTMLResponse("<h3>Mittente non trovato.</h3>", status_code=404)

    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not from_site["bound_device_id"] or from_site["bound_device_id"] != device_id:
        return HTMLResponse("<h3>Accesso non autorizzato per trasferimento.</h3>", status_code=403)

    to_name = to_name.strip()
    if to_name == "":
        return HTMLResponse("<h3>Nome destinatario non valido.</h3>", status_code=400)

    try:
        amt = float(amount)
    except Exception:
        return HTMLResponse("<h3>Importo non valido.</h3>", status_code=400)

    if amt <= 0:
        return HTMLResponse("<h3>Importo deve essere positivo.</h3>", status_code=400)

    if from_site["balance"] < amt:
        return HTMLResponse(f"<h3>Saldo insufficiente. Hai {from_site['balance']:.2f} €</h3>", status_code=400)

    dest = get_by_name(to_name)
    if not dest:
        new_token = secrets.token_urlsafe(12)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO cards (name, token, pin_hash, balance) VALUES (?, ?, ?, ?)", (to_name, new_token, hash_pin("0000"), 0.0))
        conn.commit()
        conn.close()
        dest = get_by_name(to_name)

    update_balance_by_token(from_site["token"], from_site["balance"] - amt)
    update_balance_by_token(dest["token"], dest["balance"] + amt)

    return HTMLResponse(f"<h3>Trasferimento di {amt:.2f} € a {html_lib.escape(to_name)} effettuato.</h3><p><a href='/card'>Torna</a></p>")

# ---------- ADMIN ----------
@app.get("/admin/list", response_class=HTMLResponse)
def admin_list(key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>Accesso negato</h3>", status_code=403)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, token, balance, bound_device_id, token_used FROM cards")
    rows = c.fetchall()
    conn.close()
    html = "<h2>Lista carte</h2><ul>"
    for name, token, balance, bound, used in rows:
        bound_display = ((bound[:8] + "...") if bound else "nessuno")
        used_display = "sì" if used else "no"
        html += (
            "<li><b>{}</b> — saldo: {:.2f}€ — used: {} — bound: {} "
            "- <a href='/admin/reset/{}?key={}' >Reset binding</a> "
            "- <a href='/admin/delete/{}?key={}' >Elimina</a></li>"
        ).format(
            html_lib.escape(name), balance, used_display, html_lib.escape(bound_display),
            html_lib.escape(token), html_lib.escape(ADMIN_KEY),
            html_lib.escape(token), html_lib.escape(ADMIN_KEY)
        )
    html += "</ul>"
    return HTMLResponse(html)

@app.get("/admin/create", response_class=HTMLResponse)
def admin_create(name: str = "", pin: str = "", initial: float = 0.0, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>Accesso negato</h3>", status_code=403)
    if not name or not pin:
        return HTMLResponse("<h3>Fornisci name e pin</h3>", status_code=400)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM cards")
    count = c.fetchone()[0]
    conn.close()
    if count >= 5:
        return HTMLResponse("<h3>Hai già raggiunto il limite di 5 carte.</h3>", status_code=400)

    token = create_site(name, pin, initial)
    if not token:
        return HTMLResponse("<h3>Errore: nome già esistente.</h3>", status_code=400)

    return HTMLResponse(
        "<h3>✅ Creata: {}</h3><p>URL da scrivere sul tag NFC:<br>"
        "<code>/launch/{}</code> (aggiungi il tuo dominio davanti)</p>".format(html_lib.escape(name), html_lib.escape(token))
    )

@app.get("/admin/reset/{token}", response_class=HTMLResponse)
def admin_reset(token: str, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>Accesso negato</h3>", status_code=403)
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non trovato</h3>", status_code=404)
    unbind_device_id(token)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET token_used = 0 WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return HTMLResponse("<h3>Binding resettato per {}</h3><p><a href='/admin/list?key={}' >Torna</a></p>".format(html_lib.escape(site["name"]), html_lib.escape(ADMIN_KEY)))

@app.get("/admin/delete/{token}", response_class=HTMLResponse)
def admin_delete(token: str, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>Accesso negato</h3>", status_code=403)
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non trovato</h3>", status_code=404)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM cards WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return HTMLResponse("<h3>Eliminata {}</h3><p><a href='/admin/list?key={}' >Torna</a></p>".format(html_lib.escape(site["name"]), html_lib.escape(ADMIN_KEY)))

# ---------- NOTE ----------
# - Non è tecnicamente possibile “provare” lato server che l’URL provenga da un tap NFC.
# - Con SCAN_WINDOW imponiamo di aver scansionato la carta pochi secondi prima: di fatto
#   non puoi aprire /card senza tap immediato della carta (link copiati o vecchi ⇒ 403).
# - Mantieni ADMIN_KEY segreta e usa HTTPS (Render fornisce TLS). 
