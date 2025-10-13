# main.py
# Requisiti: fastapi, uvicorn, python-multipart
# pip install fastapi uvicorn python-multipart

from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3, secrets, hashlib, time, html as html_lib

# ---------- CONFIG ----------
DB_FILE = "cards.db"
SESSION_TTL = 60 * 5  # 5 minuti sessione corta
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
    expires = int(time.time()) + SESSION_TTL
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO sessions (sid, token, expires) VALUES (?, ?, ?)", (sid, token, expires))
    conn.commit()
    conn.close()
    return sid

def get_token_from_session(sid: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT token, expires FROM sessions WHERE sid = ?", (sid,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    token, expires = r
    if int(time.time()) > expires:
        # delete expired
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
        conn.commit()
        conn.close()
        return None
    return token

def delete_session(sid: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
    conn.commit()
    conn.close()

# ---------- UTIL ----------
def ensure_device_cookie(response: Response, request: Request) -> str:
    """
    Return device_id from cookie if present; otherwise create one and set cookie on response.
    """
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_id:
        device_id = secrets.token_hex(16)
        # persist cookie long time (1 year)
        response.set_cookie(DEVICE_COOKIE_NAME, device_id, max_age=60*60*24*365, samesite="Lax")
    return device_id

# ---------- ROUTES ----------

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse("""
    <h3>Server attivo — usa /create?name=NomeBanca&code=PIN&initial=50 per creare una banca</h3>
    <p>Admin: /admin/list?key=LA_TUA_CHIAVE</p>
    """)

# ---- Creation via link: /create?name=&code=&initial=
# code = PIN (salvato hashed)
@app.get("/create", response_class=HTMLResponse)
def create_via_link(name: str = "", code: str = "", initial: float = 0.0, key: str = ""):
    # Optional: protect creation with admin key if you want; here we allow creation without key
    # If you want to require the admin key, uncomment the following:
    # if key != ADMIN_KEY:
    #     return HTMLResponse("<h3>Creazione bloccata: chiave admin richiesta</h3>", status_code=403)

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

    # Return the URL to write on the NFC tag (the token is shown only here for admin's convenience)
    url = f"/launch/{token}"
    return HTMLResponse(f"""
    <h3>✅ Banca '{html_lib.escape(name)}' creata con successo!</h3>
    <p>Saldo iniziale: {initial:.2f} €</p>
    <p>URL da scrivere sul tag NFC (aggiungi il tuo dominio prima):</p>
    <pre>https://TUO-DOMINIO{url}</pre>
    <p>Puoi resettare il binding dopo averlo scritto: /admin/reset/&lt;token&gt;?key={html_lib.escape(ADMIN_KEY)}</p>
    """)

# LAUNCH: write this URL on the NFC tag: /launch/<token>
# - one-time: marks token_used and creates a short session. also ensures device cookie exists.
@app.get("/launch/{token}", response_class=HTMLResponse)
def launch(token: str, request: Request, response: Response):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Tag non valido.</h3>", status_code=404)

    if site["token_used"]:
        return HTMLResponse("<h3>Tag già usato. Per usare la carta riporta il tag al dispositivo registrato o resetta il binding dall'admin.</h3>", status_code=403)

    # Ensure device cookie present (so device can be bound later reliably)
    device_id = ensure_device_cookie(response, request)

    # Create short session and mark token used (one-time)
    sid = create_session_for_token(token)
    mark_token_used(token)

    # set session cookie (httponly)
    response.set_cookie(SESSION_COOKIE_NAME, sid, max_age=SESSION_TTL, samesite="Lax", httponly=True)

    # redirect to /card (token no longer visible)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Accesso</title></head><body>"
        "<script>window.location.replace('/card');</script>"
        "<noscript>Apri /card manualmente</noscript>"
        "</body></html>"
    )
    return HTMLResponse(html)

# CARD: reads session cookie, shows PIN form. does not expose token.
@app.get("/card", response_class=HTMLResponse)
def card_from_session(request: Request, response: Response):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        # if session not present: ensure we still set a device cookie so future binds are stable
        # create device cookie and inform user to re-scan the tag
        dev = request.cookies.get(DEVICE_COOKIE_NAME)
        if not dev:
            new_dev = secrets.token_hex(16)
            response.set_cookie(DEVICE_COOKIE_NAME, new_dev, max_age=60*60*24*365, samesite="Lax")
            return HTMLResponse("<h3>Device cookie creato. Riprova avvicinando la carta NFC.</h3>", status_code=403)
        return HTMLResponse("<h3>Sessione non trovata. Avvicina la carta NFC.</h3>", status_code=403)

    token = get_token_from_session(sid)
    if not token:
        return HTMLResponse("<h3>Sessione scaduta o non valida. Riavvicina la carta NFC.</h3>", status_code=403)

    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Tag non valido.</h3>", status_code=404)

    # If already bound to a device and current device cookie differs => block
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
        <p>Inserisci il PIN per sbloccare questa carta su questo dispositivo. Se è la prima volta questo dispositivo verrà associato.</p>
        <form method="post" action="/unlock">
          <input type="hidden" name="token" value="{token}">
          <input name="pin" type="password" placeholder="PIN" required><br>
          <button type="submit">Accedi</button>
        </form>
      </div>
    </body></html>
    """.format(name=html_lib.escape(site["name"]), token=html_lib.escape(site["token"]))

    return HTMLResponse(html)

# UNLOCK: verify PIN; if first-time bind device_id; else enforce device match
@app.post("/unlock", response_class=HTMLResponse)
def unlock(request: Request, response: Response, token: str = Form(...), pin: str = Form(...)):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non valido.</h3>", status_code=404)

    if site["pin_hash"] != hash_pin(pin):
        return HTMLResponse("<h3>PIN errato.</h3><p><a href='/card'>Riprova</a></p>", status_code=403)

    # Ensure device cookie exists (should have been created on launch, but double-check)
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_id:
        # create one now and set cookie, then ask user to retry (rare case)
        new_device_id = secrets.token_hex(16)
        r = HTMLResponse("<h3>Device cookie creato, ricarica la pagina o riavvicina la carta</h3>")
        r.set_cookie(DEVICE_COOKIE_NAME, new_device_id, max_age=60*60*24*365, samesite="Lax")
        return r

    # If not yet bound -> bind now
    if not site["bound_device_id"]:
        bind_device_id(token, device_id)
        site = get_by_token(token)

    # If bound but doesn't match -> block
    if site["bound_device_id"] != device_id:
        return HTMLResponse("<h3>Accesso non autorizzato: dispositivo non registrato.</h3>", status_code=403)

    # OK -> show balance and transfer form
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
        <p class="small">Nota: carta associata a questo dispositivo. Per spostarla usa l'admin per resettare l'associazione.</p>
      </div>
    </body></html>
    """.format(name=html_lib.escape(site["name"]), balance=bal, token=html_lib.escape(site["token"]))

    return HTMLResponse(html)

# TRANSFER: checks that from_token is bound to this device (device cookie)
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
        # create recipient with dummy pin "0000"
        new_token = secrets.token_urlsafe(12)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO cards (name, token, pin_hash, balance) VALUES (?, ?, ?, ?)",
                  (to_name, new_token, hash_pin("0000"), 0.0))
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
        bound_display = (bound[:8] + "...") if bound else "nessuno"
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
        "<h3>✅ Creata: {}</h3><p>URL da scrivere sul tag NFC (metti il tuo dominio prima):<br>"
        "<code>https://TUO-DOMINIO/launch/{}</code></p>".format(html_lib.escape(name), html_lib.escape(token))
    )

@app.get("/admin/reset/{token}", response_class=HTMLResponse)
def admin_reset(token: str, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>Accesso negato</h3>", status_code=403)
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non trovato</h3>", status_code=404)
    unbind_device_id(token)
    # re-enable token for reuse if desired
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

# ---------- NOTES ----------
# - Cambia ADMIN_KEY prima del deploy.
# - Per creare carte via link (senza shell):
#   https://TUO-DOMINIO/create?name=NomeBanca&code=2127&initial=50
# - Scrivi l'URL mostrato sulla carta NFC: https://TUO-DOMINIO/launch/<token>
# - Procedura d'uso:
#   1) Avvicina la carta (launch) -> browser riceve session cookie + device cookie
#   2) /card -> inserisci PIN -> bind device (first time) e accesso
#   3) Dopo il primo uso solo il dispositivo con lo stesso cookie potrà usare la carta
# - Per resettare binding: /admin/reset/<token>?key=LA_TUA_CHIAVE
#
# Limiti e avvertenze:
# - Il device cookie può essere cancellato dall'utente o se si cambia browser; in quel caso bisogna resettare binding o ricreare la carta.
# - Se qualcuno copia l'NDEF PRIMA del primo utilizzo, potrà usare l'URL finché il token non viene usato; evita di condividere i tag.
# - Per maggiore sicurezza utilizzare bcrypt per PIN e HTTPS (Render fornisce TLS).
