# main.py
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
import sqlite3, secrets, hashlib, time

DB = "cards.db"
SESSION_TTL = 60 * 5  # durata sessione in secondi (5 minuti)
ADMIN_KEY = "bunald1"  # <-- CAMBIALA!

app = FastAPI()
# --- Endpoint per creare una banca da remoto (protetto da ADMIN_KEY) ---
@app.get("/admin/create", response_class=HTMLResponse)
def admin_create(name: str = "", pin: str = "", initial: float = 100.0, key: str = ""):
    # Controllo chiave admin
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>❌ Accesso negato (chiave errata)</h3>", status_code=403)

    # Controlli basilari
    name = name.strip()
    pin = str(pin).strip()
    try:
        initial = float(initial)
    except:
        initial = 100.0

    if not name or not pin:
        return HTMLResponse("<h3>Errore: fornire 'name' e 'pin' nella query string</h3>", status_code=400)

    # Limite di 5 siti (opzionale) - verifica quante banche ci sono già
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM cards")
    count = c.fetchone()[0]
    conn.close()
    if count >= 5:
        return HTMLResponse("<h3>Hai già raggiunto il limite di 5 siti.</h3>", status_code=400)

    # Crea la banca (usa la funzione già presente)
    token = create_site_in_db(name, pin, initial)
    if not token:
        return HTMLResponse("<h3>Errore: nome già esistente o problema creazione.</h3>", status_code=400)

    # Risposta con token completo (URL da scrivere nel tag NFC)
    full_url = f"/launch/{token}"
    html = f"""
    <html><body style='font-family:Arial;padding:20px;'>
      <h3>✅ Banca creata: {name}</h3>
      <p>Saldo iniziale: {initial:.2f}€</p>
      <p>Token: <code>{token}</code></p>
      <p>URL da scrivere sul tag NFC (aggiungi il dominio prima):</p>
      <pre>https://TUO-DOMINIO{full_url}</pre>
      <p><b>IMPORTANTE:</b> copia subito il token e usalo per scrivere il tag.</p>
    </body></html>
    """
    return HTMLResponse(html)

# ----------------- DB init -----------------
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            token TEXT UNIQUE,
            pin_hash TEXT,
            balance REAL DEFAULT 0,
            bound_fingerprint TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            sid TEXT PRIMARY KEY,
            token TEXT,
            expires INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ----------------- helper -----------------
def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def fingerprint_from_request(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    lang = request.headers.get("accept-language", "")
    raw = f"{client_ip}|{ua}|{lang}"
    return hashlib.sha256(raw.encode()).hexdigest()

def create_site_in_db(name: str, pin: str, initial: float=100.0):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    token = secrets.token_urlsafe(12)
    try:
        c.execute("INSERT INTO cards (name, token, pin_hash, balance) VALUES (?, ?, ?, ?)",
                  (name, token, hash_pin(pin), float(initial)))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return None
    conn.close()
    return token

def get_by_token(token: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_fingerprint FROM cards WHERE token=?", (token,))
    r = c.fetchone()
    conn.close()
    if not r: return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_fingerprint": r[5]}

def get_by_name(name: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_fingerprint FROM cards WHERE name=?", (name,))
    r = c.fetchone()
    conn.close()
    if not r: return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_fingerprint": r[5]}

def update_balance_by_token(token: str, newbal: float):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE cards SET balance=? WHERE token=?", (float(newbal), token))
    conn.commit()
    conn.close()

def bind_fingerprint(token: str, fingerprint: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE cards SET bound_fingerprint=? WHERE token=?", (fingerprint, token))
    conn.commit()
    conn.close()

def clear_binding(token: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE cards SET bound_fingerprint=NULL WHERE token=?", (token,))
    conn.commit()
    conn.close()

# ----------------- sessions helpers -----------------
def create_session_for_token(token: str):
    sid = secrets.token_urlsafe(24)
    expires = int(time.time()) + SESSION_TTL
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO sessions (sid, token, expires) VALUES (?, ?, ?)", (sid, token, expires))
    conn.commit()
    conn.close()
    return sid

def get_token_from_session(sid: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT token, expires FROM sessions WHERE sid=?", (sid,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    token, expires = r
    if int(time.time()) > expires:
        conn = sqlite3.connect(DB)
        c = conn.cursor()
        c.execute("DELETE FROM sessions WHERE sid=?", (sid,))
        conn.commit()
        conn.close()
        return None
    return token

def delete_session(sid: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE sid=?", (sid,))
    conn.commit()
    conn.close()

# ----------------- ROUTES -----------------

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse("<h3>Server attivo. Usa /admin?key=TUAPASSWORD per il pannello admin (sostituisci TUAPASSWORD)</h3>")

# launch endpoint: scrivi questo sull'NFC, imposta cookie e reindirizza a /card
@app.get("/launch/{token}", response_class=HTMLResponse)
def launch(token: str, request: Request):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Tag non valido.</h3>", status_code=404)

    sid = create_session_for_token(token)

    html = f"""
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Accesso {site['name']}</title>
      </head>
      <body>
        <script>
          document.cookie = "session={sid}; path=/; max-age={SESSION_TTL}; samesite=lax";
          window.location.replace("/card");
        </script>
        <noscript>Se non vieni reindirizzato, apri manualmente /card</noscript>
      </body>
    </html>
    """
    return HTMLResponse(html)

# card without token: legge cookie session e mostra pagina equivalente
@app.get("/card", response_class=HTMLResponse)
def card_from_session(request: Request):
    cookies = request.headers.get("cookie", "")
    sid = None
    for part in cookies.split(";"):
        p = part.strip()
        if p.startswith("session="):
            sid = p.split("=",1)[1]
            break
    if not sid:
        return HTMLResponse("<h3>Sessione non trovata. Usa la carta NFC per accedere.</h3>", status_code=403)

    token = get_token_from_session(sid)
    if not token:
        return HTMLResponse("<h3>Sessione scaduta o non valida. Riavvicina la carta NFC.</h3>", status_code=403)

    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non valido.</h3>", status_code=404)

    fp = fingerprint_from_request(request)
    if site['bound_fingerprint'] and site['bound_fingerprint'] != fp:
        return HTMLResponse("<h3>Accesso non autorizzato</h3><p>Questa carta è associata a un altro dispositivo.</p>", status_code=403)

    # show PIN form
    return f"""
    <html>
    <head>
      <meta charset='utf-8'>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{site['name']}</title>
      <style>
        body {{ font-family: Inter, Arial, sans-serif; margin:0; padding:20px; background:#f5f7fb; color:#222; }}
        .card {{ max-width:420px; margin: 18px auto; background:white; padding:18px; border-radius:12px; box-shadow:0 6px 18px rgba(20,30,50,0.08); }}
        h2 {{ font-size:20px; margin:0 0 8px 0; }}
        p {{ margin:6px 0; font-size:16px; }}
        input[type="password"] {{ width:100%; padding:12px; border-radius:10px; border:1px solid #e6e9ef; margin-top:8px; box-sizing:border-box; font-size:16px; }}
        button {{ width:100%; padding:12px; border-radius:10px; border:none; background:#0066ff; color:white; font-size:16px; margin-top:10px; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h2>{site['name']}</h2>
        <p>Inserisci PIN per accedere. Se è la prima volta il dispositivo verrà associato a questa carta.</p>
        <form method="post" action="/unlock">
          <input type="hidden" name="token" value="{site['token']}">
          <input name="pin" type="password" placeholder="PIN"><br>
          <button type="submit">Accedi</button>
        </form>
      </div>
    </body>
    </html>
    """

# legacy token endpoint (kept for compatibility)
@app.get("/card/{token}", response_class=HTMLResponse)
def card_get(token: str, request: Request):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Tag non valido.</h3>", status_code=404)

    fp = fingerprint_from_request(request)
    if site['bound_fingerprint'] and site['bound_fingerprint'] != fp:
        return HTMLResponse("<h3>Accesso non autorizzato</h3><p>Questa carta è associata a un altro dispositivo.</p>", status_code=403)

    return f"""
    <html>
    <head>
      <meta charset='utf-8'>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{site['name']}</title>
      <style>
        body {{ font-family: Inter, Arial, sans-serif; margin:0; padding:20px; background:#f5f7fb; color:#222; }}
        .card {{ max-width:420px; margin: 18px auto; background:white; padding:18px; border-radius:12px; box-shadow:0 6px 18px rgba(20,30,50,0.08); }}
        h2 {{ font-size:20px; margin:0 0 8px 0; }}
        p {{ margin:6px 0; font-size:16px; }}
        input[type="password"] {{ width:100%; padding:12px; border-radius:10px; border:1px solid #e6e9ef; margin-top:8px; box-sizing:border-box; font-size:16px; }}
        button {{ width:100%; padding:12px; border-radius:10px; border:none; background:#0066ff; color:white; font-size:16px; margin-top:10px; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h2>{site['name']}</h2>
        <p>Inserisci PIN per accedere. Se è la prima volta il dispositivo verrà associato a questa carta.</p>
        <form method="post" action="/unlock">
          <input type="hidden" name="token" value="{site['token']}">
          <input name="pin" type="password" placeholder="PIN"><br>
          <button type="submit">Accedi</button>
        </form>
      </div>
    </body>
    </html>
    """

# unlock: verify PIN and bind device on first use
@app.post("/unlock", response_class=HTMLResponse)
def unlock(request: Request, token: str = Form(...), pin: str = Form(...)):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non valido.</h3>", status_code=404)

    if site['pin_hash'] != hash_pin(pin):
        return HTMLResponse("<h3>PIN errato.</h3><p><a href='/card/{0}'>Riprova</a></p>".format(token), status_code=403)

    fp = fingerprint_from_request(request)
    if not site['bound_fingerprint']:
        bind_fingerprint(token, fp)
        site = get_by_token(token)

    if site['bound_fingerprint'] != fp:
        return HTMLResponse("<h3>Accesso non autorizzato: dispositivo non registrato.</h3>", status_code=403)

    bal = float(site['balance'])
    return f"""
    <html>
    <head>
      <meta charset='utf-8'>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{site['name']}</title>
      <style>
        body {{ font-family: Inter, Arial, sans-serif; margin:0; padding:20px; background:#f5f7fb; color:#222; }}
        .card {{ max-width:420px; margin: 18px auto; background:white; padding:18px; border-radius:12px; box-shadow:0 6px 18px rgba(20,30,50,0.08); }}
        h2 {{ font-size:20px; margin:0 0 8px 0; }}
        p {{ margin:6px 0; font-size:16px; }}
        input[type="text"], input[type="number"] {{ width:100%; padding:12px; border-radius:10px; border:1px solid #e6e9ef; margin-top:8px; box-sizing:border-box; font-size:16px; }}
        button {{ width:100%; padding:12px; border-radius:10px; border:none; background:#0066ff; color:white; font-size:16px; margin-top:10px; }}
        .small {{ font-size:13px; color:#6b7280; margin-top:10px; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h2>{site['name']}</h2>
        <p><b>Saldo:</b> {bal:.2f} €</p>
        <h4>Invia denaro</h4>
        <form method="post" action="/transfer">
          <input type="hidden" name="from_token" value="{site['token']}">
          <input name="to_name" placeholder="Nome banca destinatario"><br>
          <input name="amount" type="number" step="0.01" placeholder="Importo"><br>
          <button type="submit">Invia</button>
        </form>
        <p class="small">Nota: la carta è associata a questo dispositivo. Per spostarla usa l'admin per resettare l'associazione.</p>
      </div>
    </body>
    </html>
    """

# transfer endpoint
@app.post("/transfer", response_class=HTMLResponse)
def transfer(request: Request, from_token: str = Form(...), to_name: str = Form(...), amount: float = Form(...)):
    from_site = get_by_token(from_token)
    if not from_site:
        return HTMLResponse("<h3>Mittente non trovato.</h3>", status_code=404)

    fp = fingerprint_from_request(request)
    if not from_site['bound_fingerprint'] or from_site['bound_fingerprint'] != fp:
        return HTMLResponse("<h3>Accesso non autorizzato per trasferimento.</h3>", status_code=403)

    to_name = to_name.strip()
    if to_name == "":
        return HTMLResponse("<h3>Nome destinatario non valido.</h3>", status_code=400)
    if amount <= 0:
        return HTMLResponse("<h3>Importo non valido.</h3>", status_code=400)
    if from_site['balance'] < amount:
        return HTMLResponse(f"<h3>Saldo insufficiente. Hai {from_site['balance']:.2f} €</h3>", status_code=400)

    dest = get_by_name(to_name)
    if not dest:
        new_token = secrets.token_urlsafe(12)
        conn = sqlite3.connect(DB)
        c = conn.cursor()
        c.execute("INSERT INTO cards (name, token, pin_hash, balance) VALUES (?, ?, ?, ?)",
                  (to_name, new_token, hash_pin("0000"), 0.0))
        conn.commit()
        conn.close()
        dest = get_by_name(to_name)

    update_balance_by_token(from_site['token'], from_site['balance'] - amount)
    update_balance_by_token(dest['token'], dest['balance'] + amount)

    return HTMLResponse(f"<h3>Trasferimento di {amount:.2f}€ a {to_name} effettuato.</h3><p><a href='/card'>Torna</a></p>")

# admin endpoints (protected by ADMIN_KEY)
@app.get("/admin", response_class=HTMLResponse)
def admin_list(key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>❌ Accesso negato</h3>", status_code=403)
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT name, token, balance, bound_fingerprint FROM cards")
    rows = c.fetchall()
    conn.close()
    html = """
    <html>
    <head>
      <meta charset='utf-8'>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Admin - Banche</title>
      <style>
        body { font-family: Arial; padding:20px; background:#f4f6fb; }
        table { border-collapse: collapse; width:100%; background:white; border-radius:8px; overflow:hidden; box-shadow:0 8px 24px rgba(20,30,50,0.06); }
        th, td { border-bottom:1px solid #eee; padding:10px 12px; text-align:left; }
        th { background:#0b63ff; color:white; }
        a.button { display:inline-block; padding:6px 10px; background:#0b63ff; color:white; border-radius:6px; text-decoration:none; font-size:14px; }
      </style>
    </head>
    <body>
      <h2>🏦 Pannello Admin</h2>
      <table>
        <tr><th>Nome</th><th>Token</th><th>Saldo</th><th>Bound</th><th>Azioni</th></tr>
    """
    for name, token, balance, bound in rows:
        bound_disp = (bound[:8] + "...") if bound else "nessuno"
        html += f"<tr><td>{name}</td><td>{token}</td><td>{balance:.2f}€</td><td>{bound_disp}</td>"
        html += f"<td><a class='button' href='/admin/reset/{token}?key={ADMIN_KEY}'>Reset binding</a> "
        html += f"<a class='button' href='/admin/delete/{token}?key={ADMIN_KEY}'>Elimina</a></td></tr>"
    html += "</table></body></html>"
    return HTMLResponse(html)

@app.get("/admin/reset/{token}", response_class=HTMLResponse)
def admin_reset(token: str, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>❌ Accesso negato</h3>", status_code=403)
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non trovato.</h3>", status_code=404)
    clear_binding(token)
    return HTMLResponse(f"<h3>Binding resettato per {site['name']}.</h3><p><a href='/admin?key={ADMIN_KEY}'>Torna alla lista</a></p>")

@app.get("/admin/delete/{token}", response_class=HTMLResponse)
def admin_delete(token: str, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>❌ Accesso negato</h3>", status_code=403)
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non trovato.</h3>", status_code=404)
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM cards WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return HTMLResponse(f"<h3>Eliminata {site['name']}.</h3><p><a href='/admin?key={ADMIN_KEY}'>Torna alla lista</a></p>")
