# main.py
# Banca Bonsiorium - single-file server
# Requisiti: fastapi, uvicorn, python-multipart
# pip install fastapi uvicorn python-multipart

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3, secrets, hashlib, time

# ---------- CONFIG ----------
DB = "cards.db"
SESSION_TTL = 60 * 5  # session cookie durata (secondi)
ADMIN_KEY = "bunald1"  # cambia questa
app = FastAPI()

# ---------- DB Init ----------
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
            bound_fingerprint TEXT,
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

def fingerprint_from_request(request: Request) -> str:
    """Fingerprint semplice: ip + user-agent + accept-language, hashata.
    Limitazione: non è infallibile (IP o UA possono cambiare), ma è utile per binding demo."""
    client_ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    lang = request.headers.get("accept-language", "")
    raw = f"{client_ip}|{ua}|{lang}"
    return hashlib.sha256(raw.encode()).hexdigest()

def create_site(name: str, pin: str, initial: float=0.0):
    token = secrets.token_urlsafe(16)
    conn = sqlite3.connect(DB)
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
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_fingerprint, token_used FROM cards WHERE token=?", (token,))
    r = c.fetchone()
    conn.close()
    if not r: return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_fingerprint": r[5], "token_used": r[6]}

def get_by_name(name: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, name, token, pin_hash, balance, bound_fingerprint, token_used FROM cards WHERE name=?", (name,))
    r = c.fetchone()
    conn.close()
    if not r: return None
    return {"id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4], "bound_fingerprint": r[5], "token_used": r[6]}

def mark_token_used(token: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE cards SET token_used=1 WHERE token=?", (token,))
    conn.commit()
    conn.close()

def bind_fingerprint(token: str, fingerprint: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE cards SET bound_fingerprint=? WHERE token=?", (fingerprint, token))
    conn.commit()
    conn.close()

def update_balance_by_token(token: str, newbal: float):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE cards SET balance=? WHERE token=?", (float(newbal), token))
    conn.commit()
    conn.close()

# ---------- SESSIONS ----------
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
        # delete expired
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

# ---------- ROUTES ----------

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse("""
    <h2>Server attivo — Banca Bonsiorium</h2>
    <p>Usa /admin?key=... per gestire le banche.</p>
    """)

# 1) LAUNCH: endpoint che scrivi nel tag NFC: /launch/<token>
#    - token è usa-una-volta. Quando viene chiamato: crea session (cookie) e reindirizza a /card
#    - poi **segna token_used=1** per impedire riuso
@app.get("/launch/{token}", response_class=HTMLResponse)
def launch(token: str, request: Request):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Tag non valido.</h3>", status_code=404)
    if site['token_used']:
        # token già usato -> blocca. (Questo impedisce di aprire URL di launch dopo il primo utilizzo)
        return HTMLResponse("<h3>Token già usato. Usa la carta NFC sul dispositivo registrato.</h3>", status_code=403)

    # crea sessione temporanea e imposta cookie via JS (max-age breve)
    sid = create_session_for_token(token)
    # segna token come usato ora (one-time) - il bind sarà fatto al momento dell'inserimento PIN
    mark_token_used(token)

    html = f"""
    <html><head><meta charset='utf-8'><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Accesso</title></head><body>
      <script>
        // imposta cookie session e reindirizza a /card (così il token scompare dalla barra)
        document.cookie = "session={sid}; path=/; max-age={SESSION_TTL}; samesite=lax";
        window.location.replace("/card");
      </script>
      <noscript>Abilita JavaScript o apri manualmente /card</noscript>
    </body></html>
    """
    return HTMLResponse(html)

# 2) CARD: pagina senza token; richiede cookie di sessione; se token -> mostra form PIN per sbloccare/bindare
@app.get("/card", response_class=HTMLResponse)
def card_from_session(request: Request):
    # estrai cookie 'session'
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

    # se esiste binding e non coincide con questo device -> blocco
    fp = fingerprint_from_request(request)
    if site['bound_fingerprint'] and site['bound_fingerprint'] != fp:
        return HTMLResponse("<h3>Accesso non autorizzato</h3><p>Questa carta è già associata ad un altro dispositivo.</p>", status_code=403)

    # Mostra form per inserire PIN: al submit /unlock (server verificherà PIN e binda il dispositivo)
    return f"""
    <html><head><meta charset='utf-8'><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{site['name']}</title>
    <style>
      body{{font-family:Arial;background:#f5f7fb;padding:20px}}
      .card{{max-width:420px;margin:20px auto;background:#fff;padding:18px;border-radius:12px;box-shadow:0 8px 20px rgba(0,0,0,0.08)}}
      input{{width:100%;padding:12px;margin-top:8px;border-radius:8px;border:1px solid #e6e9ef}}
      button{{width:100%;padding:12px;margin-top:12px;border-radius:8px;background:#0066ff;color:#fff;border:none}}
    </style>
    </head><body>
      <div class="card">
        <h2>{site['name']}</h2>
        <p>Inserisci il PIN per sbloccare questa carta su questo dispositivo. (Se è la prima volta il dispositivo verrà associato.)</p>
        <form method="post" action="/unlock">
          <input type="hidden" name="token" value="{site['token']}">
          <input type="password" name="pin" placeholder="PIN" required><br>
          <button type="submit">Accedi</button>
        </form>
      </div>
    </body></html>
    """

# 3) UNLOCK: verifica PIN, binda fingerprint se prima volta
@app.post("/unlock", response_class=HTMLResponse)
def unlock(request: Request, token: str = Form(...), pin: str = Form(...)):
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non valido.</h3>", status_code=404)

    if site['pin_hash'] != hash_pin(pin):
        return HTMLResponse("<h3>PIN errato.</h3><p><a href='/card'>Riprova</a></p>", status_code=403)

    fp = fingerprint_from_request(request)
    # se non è bindato -> binda il dispositivo corrente
    if not site['bound_fingerprint']:
        bind_fingerprint(token, fp)
        site = get_by_token(token)

    if site['bound_fingerprint'] != fp:
        return HTMLResponse("<h3>Accesso non autorizzato: dispositivo non registrato.</h3>", status_code=403)

    # PIN corretto e device associato -> mostra saldo + form trasferimento
    bal = float(site['balance'])
    return f"""
    <html><head><meta charset='utf-8'><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{site['name']}</title>
    <style>
      body{{font-family:Arial;background:#f5f7fb;padding:20px}}
      .card{{max-width:420px;margin:20px auto;background:#fff;padding:18px;border-radius:12px;box-shadow:0 8px 20px rgba(0,0,0,0.08)}}
      input{{width:100%;padding:12px;margin-top:8px;border-radius:8px;border:1px solid #e6e9ef}}
      button{{width:100%;padding:12px;margin-top:12px;border-radius:8px;background:#0066ff;color:#fff;border:none}}
      .small{{font-size:13px;color:#666;margin-top:10px}}
    </style>
    </head><body>
      <div class="card">
        <h2>{site['name']}</h2>
        <p><b>Saldo:</b> {bal:.2f} €</p>
        <h4>Invia denaro</h4>
        <form method="post" action="/transfer">
          <input type="hidden" name="from_token" value="{site['token']}">
          <input name="to_name" placeholder="Nome banca destinatario" required><br>
          <input name="amount" type="number" step="0.01" placeholder="Importo" required><br>
          <button type="submit">Invia</button>
        </form>
        <p class="small">Nota: questa carta è associata a questo dispositivo. Se vuoi spostarla usa l'admin per resettare l'associazione.</p>
      </div>
    </body></html>
    """

# 4) TRANSFER: richiede che il from_token sia associato a questo dispositivo (binding)
@app.post("/transfer", response_class=HTMLResponse)
def transfer(request: Request, from_token: str = Form(...), to_name: str = Form(...), amount: float = Form(...)):
    from_site = get_by_token(from_token)
    if not from_site:
        return HTMLResponse("<h3>Mittente non trovato.</h3>", status_code=404)

    # controllo fingerprint
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

    # prendi o crea destinatario (creiamo destinatari senza token)
    dest = get_by_name(to_name)
    if not dest:
        new_token = secrets.token_urlsafe(12)
        conn = sqlite3.connect(DB)
        c = conn.cursor()
        # destinario creato con pin "0000" e senza binding
        c.execute("INSERT INTO cards (name, token, pin_hash, balance) VALUES (?, ?, ?, ?)",
                  (to_name, new_token, hash_pin("0000"), 0.0))
        conn.commit()
        conn.close()
        dest = get_by_name(to_name)

    # aggiorna bilanci
    update_balance_by_token(from_site['token'], from_site['balance'] - amount)
    update_balance_by_token(dest['token'], dest['balance'] + amount)
    return HTMLResponse(f"<h3>Trasferimento di {amount:.2f}€ a {to_name} effettuato.</h3><p><a href='/card'>Torna</a></p>")

# ---------- ADMIN ----------
@app.get("/admin/list", response_class=HTMLResponse)
def admin_list(key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>❌ Accesso negato</h3>", status_code=403)
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT name, token, balance, bound_fingerprint, token_used FROM cards")
    rows = c.fetchall()
    conn.close()
    html = "<h2>Lista carte</h2><ul>"
    for r in rows:
        bound = (r[3][:8] + "...") if r[3] else "nessuno"
        used = "sì" if r[4] else "no"
        html += f"<li><b>{r[0]}</b> — saldo: {r[2]:.2f} — token_used: {used} — bound: {bound} — <a href='/admin/reset/{r[1]}?key={ADMIN_KEY}'>Reset binding</a> — <a href='/admin/delete/{r[1]}?key={ADMIN_KEY}'>Elimina</a></li>"
    html += "</ul>"
    return HTMLResponse(html)

@app.get("/admin/create", response_class=HTMLResponse)
def admin_create(name: str = "", pin: str = "", initial: float = 0.0, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>❌ Accesso negato</h3>", status_code=403)
    if not name or not pin:
        return HTMLResponse("<h3>Fornire name e pin</h3>", status_code=400)
    # limite 5
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM cards")
    count = c.fetchone()[0]
    if count >= 5:
        conn.close()
        return HTMLResponse("<h3>Hai già raggiunto il limite di 5 carte.</h3>", status_code=400)
    token = create_site(name, pin, initial)
    if not token:
        return HTMLResponse("<h3>Errore: nome già esistente.</h3>", status_code=400)
    full_url = f"/launch/{token}"
    return HTMLResponse(f"<h3>✅ Creata: {name}</h3><p>URL da scrivere sulla carta NFC (aggiungi dominio):<br><code>https://TUO-DOMINIO{full_url}</code></p>")

@app.get("/admin/reset/{token}", response_class=HTMLResponse)
def admin_reset(token: str, key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse("<h3>❌ Accesso negato</h3>", status_code=403)
    site = get_by_token(token)
    if not site:
        return HTMLResponse("<h3>Token non trovato.</h3>", status_code=404)
    bind_fingerprint(token, None)
    return HTMLResponse(f"<h3>Binding resettato per {site['name']}.</h3><p><a href='/admin/list?key={ADMIN_KEY}'>Torna</a></p>")

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
    return HTMLResponse(f"<h3>Eliminata {site['name']}.</h3><p><a href='/admin/list?key={ADMIN_KEY}'>Torna</a></p>")

# ---------- NOTES ----------
# Limiti reali:
# - Questo approccio migliora la sicurezza perché il token è one-time e viene invalidato subito,
#   e perché il dispositivo viene boundato tramite fingerprint. Tuttavia:
#   * Se qualcuno legge il contenuto NDEF del tag (app tipo NFC Tools) potrà copiare l'URL
#     PRIMA che tu lo usi — quindi NON è totalmente a prova di copia.
#   * Per sicurezza assoluta servirebbe un'app mobile che legga l'UID hardware del tag o
#     l'uso di firme crittografiche native sul tag (richiede sviluppo mobile / tag speciali).
# - Raccomando di:
#   * scegliere ADMIN_KEY forte,
#   * non pubblicare il dominio + token su canali pubblici,
#   * resettare binding via /admin/reset/<token> prima di spostare la carta su un altro telefono.
#
# Fine file.
