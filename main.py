# main.py
# Requisiti: fastapi, uvicorn, python-multipart
# pip install fastapi uvicorn python-multipart

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3
import secrets
import hashlib
import time
import html as html_lib

# ---------- CONFIG ----------
DB_FILE = "cards.db"
SESSION_TTL = 60 * 5  # 5 minuti di sessione (modifica se vuoi)
ADMIN_KEY = "sostituisci_con_chiave_admin_sicura"  # -> CAMBIA questa stringa prima del deploy

app = FastAPI()


# ---------- DB INIT ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # cards: ogni "banca" o carta virtuale
    c.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            token TEXT UNIQUE,
            pin_hash TEXT,
            balance REAL DEFAULT 0,
            bound_fingerprint TEXT,
            token_used INTEGER DEFAULT 0
        )
    """)
    # sessions: cookie session che lega token a session id temporaneo
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

def fingerprint_from_request(request: Request) -> str:
    """
    Fingerprint semplice: ip + user-agent + accept-language, hashata.
    Non è perfetta ma è sufficiente per binding dimostrativo.
    """
    client_ip = "unknown"
    try:
        client_ip = request.client.host or "unknown"
    except Exception:
        client_ip = "unknown"
    ua = request.headers.get("user-agent", "")
    lang = request.headers.get("accept-language", "")
    raw = f"{client_ip}|{ua}|{lang}"
    return hashlib.sha256(raw.encode()).hexdigest()

def create_site(name: str, pin: str, initial: float = 0.0):
    token = secrets.token_urlsafe(16)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
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
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_fingerprint, token_used FROM cards WHERE token=?", (token,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_fingerprint": r[5], "token_used": r[6]}

def get_by_name(name: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_fingerprint, token_used FROM cards WHERE name=?", (name,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_fingerprint": r[5], "token_used": r[6]}

def mark_token_used(token: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET token_used=1 WHERE token=?", (token,))
    conn.commit()
    conn.close()

def bind_fingerprint(token: str, fingerprint: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET bound_fingerprint=? WHERE token=?", (fingerprint, token))
    conn.commit()
    conn.close()

def update_balance_by_token(token: str, newbal: float):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET balance=? WHERE token=?", (float(newbal), token))
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
    c.execute("SELECT token, expires FROM sessions WHERE sid=?", (sid,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    token, expires = r
    if int(time.time()) > expires:
        # cancella sessione scaduta
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM sessions WHERE sid=?", (sid,))
        conn.commit()
        conn.close()
        return None
    return token

def delete_session(sid: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE sid=?", (sid,))
    conn.commit()
    conn.close()


# ---------- ROUTES ----------

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse("<h3>Server attivo — usa /admin/list?key=LA_TUA_CHIAVE per gestire</h3>")


# LAUNCH: scrivi questo URL sulla carta NFC: /launch/<token>
# - quando viene chiamato, se token non usato: crea sessione, segna token usato e redirect a /card
# - l'URL /launch/<token> sarà quindi single-use (one-time).
@app.get("/launch/{token}", response_class=HTMLResponse)
def launch(token: str, request: Request):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Tag non valido.</h3>", status_code=404)

    if site["token_used"]:
        return HTMLResponse("<h3>QR/Tag già usato. Per usare questa carta, avvicinala al dispositivo registrato (o resetta binding dall'admin).</h3>", status_code=403)

    # crea session e poi marca token come usato
    sid = create_session_for_token(token)
    # marca token usato ora — non permettiamo riuso del launch URL
    mark_token_used(token)

    # Impostiamo cookie via JS e reindirizziamo a /card (così il token non rimane nella barra URL)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Accesso</title></head><body>"
        "<script>"
        f"document.cookie = 'session={sid}; path=/; max-age={SESSION_TTL}; samesite=lax';"
        "window.location.replace('/card');"
        "</script>"
        "<noscript>Abilita JavaScript o apri /card manualmente</noscript>"
        "</body></html>"
    )
    return HTMLResponse(html)


# CARD: pagina che non espone il token; legge cookie session -> mostra form PIN -> unlock
@app.get("/card", response_class=HTMLResponse)
def card_from_session(request: Request):
    # Legge cookie 'session'
    cookies = request.headers.get("cookie", "")
    sid = None
    for part in cookies.split(";"):
        p = part.strip()
        if p.startswith("session="):
            sid = p.split("=", 1)[1]
            break
    if not sid:
        return HTMLResponse("<h3>Sessione non trovata. Avvicina la carta NFC.</h3>", status_code=403)

    token = get_token_from_session(sid)
    if not token:
        return HTMLResponse("<h3>Sessione scaduta o non valida. Riavvicina la carta NFC.</h3>", status_code=403)

    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non valido.</h3>", status_code=404)

    # controllo binding: se è già bindato a fingerprint diversa -> blocco
    fp = fingerprint_from_request(request)
    if site["bound_fingerprint"] and site["bound_fingerprint"] != fp:
        return HTMLResponse("<h3>Accesso non autorizzato.</h3><p>Questa carta è associata a un altro dispositivo.</p>", status_code=403)

    # Mostra form PIN (non mostriamo token)
    # Usiamo .format e doppi {} per il CSS per evitare problemi di formattazione
    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset='utf-8'>
      <meta name='viewport' content='width=device-width,initial-scale=1'>
      <title>{name}</title>
      <style>
        body {{ font-family: Arial, sans-serif; background: #f5f7fb; padding: 20px; }}
        .card {{ max-width:420px; margin:20px auto; background:#fff; padding:18px; border-radius:12px; box-shadow:0 8px 20px rgba(0,0,0,0.08); }}
        input {{ width:100%; padding:12px; margin-top:8px; border-radius:8px; border:1px solid #e6e9ef; box-sizing:border-box; }}
        button {{ width:100%; padding:12px; margin-top:12px; border-radius:8px; background:#0066ff; color:#fff; border:none; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h2>{name}</h2>
        <p>Inserisci PIN per sbloccare questa carta su questo dispositivo. Se è la prima volta il dispositivo verrà associato.</p>
        <form method="post" action="/unlock">
          <input type="hidden" name="token" value="{token}">
          <input name="pin" type="password" placeholder="PIN" required><br>
          <button type="submit">Accedi</button>
        </form>
      </div>
    </body>
    </html>
    """.format(name=html_lib.escape(site["name"]), token=html_lib.escape(site["token"]))

    return HTMLResponse(html)


# UNLOCK: verifica PIN, se primo utilizzo bind fingerprint, altrimenti controlla fingerprint
@app.post("/unlock", response_class=HTMLResponse)
def unlock(request: Request, token: str = Form(...), pin: str = Form(...)):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non valido.</h3>", status_code=404)

    if site["pin_hash"] != hash_pin(pin):
        return HTMLResponse("<h3>PIN errato.</h3><p><a href='/card'>Riprova</a></p>", status_code=403)

    fp = fingerprint_from_request(request)
    if not site["bound_fingerprint"]:
        # primo utilizzo: bind al device corrente
        bind_fingerprint(token, fp)
        site = get_by_token(token)  # rilegge con bound aggiornato

    if site["bound_fingerprint"] != fp:
        return HTMLResponse("<h3>Accesso non autorizzato: dispositivo non registrato.</h3>", status_code=403)

    # PIN corretto e device associato: mostra saldo e modulo trasferimento
    bal = float(site["balance"])
    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset='utf-8'>
      <meta name='viewport' content='width=device-width,initial-scale=1'>
      <title>{name}</title>
      <style>
        body {{ font-family: Arial, sans-serif; background: #f5f7fb; padding: 20px; }}
        .card {{ max-width:420px; margin:20px auto; background:#fff; padding:18px; border-radius:12px; box-shadow:0 8px 20px rgba(0,0,0,0.08); }}
        input {{ width:100%; padding:12px; margin-top:8px; border-radius:8px; border:1px solid #e6e9ef; box-sizing:border-box; }}
        button {{ width:100%; padding:12px; margin-top:12px; border-radius:8px; background:#0066ff; color:#fff; border:none; }}
        .small {{ font-size:13px; color:#666; margin-top:10px; }}
      </style>
    </head>
    <body>
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
    </body>
    </html>
    """.format(name=html_lib.escape(site["name"]), balance=bal, token=html_lib.escape(site["token"]))

    return HTMLResponse(html)


# TRANSFER: trasferisce tra banche; controlla che mittente sia bindato a questo device
@app.post("/transfer", response_class=HTMLResponse)
def transfer(request: Request, from_token: str = Form(...), to_name: str = Form(...), amount: float = Form(...)):
    from_site = get_by_token(from_token)
    if not from_site:
        return HTMLResponse("<h3>Mittente non trovato.</h3>", status_code=404)

    fp = fingerprint_from_request(request)
    if not from_site["bound_fingerprint"] or from_site["bound_fingerprint"] != fp:
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
        # crea destinatario senza token usabile (solo per ricevere)
        new_token = secrets.token_urlsafe(12)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO cards (name, token, pin_hash, balance) VALUES (?, ?, ?, ?)",
                  (to_name, new_token, hash_pin("0000"), 0.0))
        conn.commit()
        conn.close()
        dest = get_by_name(to_name)

    # aggiorna saldi
    update_balance_by_token(from_site["token"], from_site["balance"] - amt)
    update_balance_by_token(dest["token"], dest["balance"] + amt)

    return HTMLResponse(f"<h3>Trasferimento di {amt:.2f} € a {html_lib.escape(to_name)} effettuato.</h3><p><a href='/card'>Torna</a></p>")


# ---------- ADMIN ENDPOINTS ----------
@app.get("/admin/list", response_class=HTMLResponse)
def admin_list(key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>Accesso negato</h3>", status_code=403)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, token, balance, bound_fingerprint, token_used FROM cards")
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

    # limite 5 carte
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

    # restituiamo l'URL da scrivere fisicamente nel tag NFC (con dominio)
    return HTMLResponse(
        "<h3>✅ Creata: {}</h3><p>URL da scrivere sul tag NFC (metti il tuo dominio prima):<br>"
        "<code>https://TUO-DOMINIO/launch/{} </code></p>".format(html_lib.escape(name), html_lib.escape(token))
    )

@app.get("/admin/reset/{token}", response_class=HTMLResponse)
def admin_reset(token: str, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>Accesso negato</h3>", status_code=403)
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non trovato</h3>", status_code=404)
    bind_fingerprint(token, None)
    # riabilitiamo token_used a 0 così puoi riscrivere il tag e riusarlo (opzionale)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE cards SET token_used=0 WHERE token=?", (token,))
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
    c.execute("DELETE FROM cards WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return HTMLResponse("<h3>Eliminata {}</h3><p><a href='/admin/list?key={}' >Torna</a></p>".format(html_lib.escape(site["name"]), html_lib.escape(ADMIN_KEY)))

# ---------- NOTES ----------
# - Prima di fare il deploy: cambia ADMIN_KEY con una stringa sicura e non pubblicarla.
# - Il token scritto sulla carta NFC è ONE-TIME: quando lo usi la prima volta il server lo invalida,
#   crea una sessione temporanea e richiede il PIN; la carta viene poi associata (bind) al device
#   (fingerprint ip+agent+lang). Solo quel device potrà usare la carta.
# - Limiti e caveat reali:
#   * Se qualcuno legge e copia l'NDEF del tag PRIMA che lo usi, potrà usare l'URL: evita di condividere i tag.
#   * Fingerprint non è infallibile (IP/UA possono cambiare). Per robustezza si potrebbero usare
#     tecniche native (UID del tag sul mondo mobile) oppure autenticazione lato client.
# - Per test: crea carte tramite /admin/create?name=Nome&pin=1234&initial=50&key=LA_TUA_CHIAVE
#   poi scrivi il link completo sulla carta: https://TUO-DOMINIO/launch/<token>
