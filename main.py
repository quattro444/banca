# main.py
# Requisiti: fastapi, uvicorn, python-multipart, (opzionale) psycopg2-binary per Postgres
# Avvio: uvicorn main:app --host 0.0.0.0 --port 8000

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import os, sqlite3, secrets, hashlib, time, html as html_lib

# ---------- CONFIG ----------
DB_FILE = os.environ.get("DB_PATH", "cards.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
try:
    import psycopg2
except ImportError:
    psycopg2 = None
USE_PG = DATABASE_URL.startswith("postgres") and psycopg2 is not None
if DATABASE_URL.startswith("postgres") and not USE_PG:
    print("ATTENZIONE: psycopg2-binary non installato, fallback a SQLite.")

SESSION_TTL = 60 * 5
SCAN_WINDOW = 30
DEVICE_COOKIE_NAME = "device_id"
SESSION_COOKIE_NAME = "session"
WEEK_SECONDS = 7 * 24 * 60 * 60
ADMIN_KEY = os.environ.get("ADMIN_KEY", "bunald")

app = FastAPI()

# ---------- DB LAYER ----------
def get_conn():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(DB_FILE)

def adapt_sql(sql: str) -> str:
    return sql.replace("?", "%s") if USE_PG else sql

def exec_sql(sql: str, params=(), fetch=None):
    conn = get_conn(); c = conn.cursor()
    c.execute(adapt_sql(sql), params)
    data = None
    if fetch == "one":
        data = c.fetchone()
    elif fetch == "all":
        data = c.fetchall()
    conn.commit()
    conn.close()
    return data

def init_db():
    if not USE_PG:
        d = os.path.dirname(DB_FILE)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
    conn = get_conn(); c = conn.cursor()
    if USE_PG:
        c.execute("""CREATE TABLE IF NOT EXISTS cards(
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE,
            token TEXT UNIQUE,
            pin_hash TEXT,
            balance DOUBLE PRECISION DEFAULT 0,
            bound_device_id TEXT,
            token_used INTEGER DEFAULT 0,
            description TEXT DEFAULT ''
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions(
            sid TEXT PRIMARY KEY,
            token TEXT,
            expires BIGINT,
            created_at BIGINT DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS transactions(
            id SERIAL PRIMARY KEY,
            ts BIGINT NOT NULL,
            from_token TEXT,
            from_name TEXT,
            to_token TEXT,
            to_name TEXT,
            amount DOUBLE PRECISION NOT NULL,
            reason TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS purchases(
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL,
            item_code TEXT NOT NULL,
            item_name TEXT NOT NULL,
            weekly_deduction DOUBLE PRECISION NOT NULL,
            next_charge_at BIGINT NOT NULL,
            started_at BIGINT NOT NULL,
            active INTEGER DEFAULT 1
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings(
            id INTEGER PRIMARY KEY,
            bank_name TEXT,
            logo_url TEXT,
            gradient_from TEXT,
            gradient_to TEXT,
            font_name TEXT
        )""")
    else:
        c.execute("""CREATE TABLE IF NOT EXISTS cards(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            token TEXT UNIQUE,
            pin_hash TEXT,
            balance REAL DEFAULT 0,
            bound_device_id TEXT,
            token_used INTEGER DEFAULT 0,
            description TEXT DEFAULT ''
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions(
            sid TEXT PRIMARY KEY,
            token TEXT,
            expires INTEGER,
            created_at INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            from_token TEXT,
            from_name TEXT,
            to_token TEXT,
            to_name TEXT,
            amount REAL NOT NULL,
            reason TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS purchases(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            item_code TEXT NOT NULL,
            item_name TEXT NOT NULL,
            weekly_deduction REAL NOT NULL,
            next_charge_at INTEGER NOT NULL,
            started_at INTEGER NOT NULL,
            active INTEGER DEFAULT 1
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings(
            id INTEGER PRIMARY KEY CHECK(id=1),
            bank_name TEXT,
            logo_url TEXT,
            gradient_from TEXT,
            gradient_to TEXT,
            font_name TEXT
        )""")
    c.execute("SELECT id FROM settings WHERE id = 1")
    if not c.fetchone():
        c.execute(adapt_sql(
            "INSERT INTO settings (id, bank_name, logo_url, gradient_from, gradient_to, font_name) VALUES (1, ?, ?, ?, ?, ?)"
        ), ("Banca NFC", "", "#0ea5e9", "#8b5cf6", "Poppins"))
    conn.commit(); conn.close()

init_db()

# ---------- HELPERS CARD ----------
def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def create_site(name: str, pin: str, initial: float = 0.0, description: str = ""):
    token = secrets.token_urlsafe(16)
    try:
        exec_sql("INSERT INTO cards (name, token, pin_hash, balance, description) VALUES (?, ?, ?, ?, ?)",
                 (name, token, hash_pin(pin), float(initial), description.strip()))
        return token
    except Exception:
        return None

def get_by_token(token: str):
    r = exec_sql(
        "SELECT id,name,token,pin_hash,balance,bound_device_id,token_used,description FROM cards WHERE token=?",
        (token,), fetch="one")
    if not r: return None
    return {
        "id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4],
        "bound_device_id": r[5], "token_used": r[6], "description": r[7]
    }

def get_by_name(name: str):
    r = exec_sql(
        "SELECT id,name,token,pin_hash,balance,bound_device_id,token_used,description FROM cards WHERE name=?",
        (name,), fetch="one")
    if not r: return None
    return {
        "id": r[0], "name": r[1], "token": r[2], "pin_hash": r[3], "balance": r[4],
        "bound_device_id": r[5], "token_used": r[6], "description": r[7]
    }

def mark_token_used(token: str):
    exec_sql("UPDATE cards SET token_used=1 WHERE token=?", (token,))

def bind_device_id(token: str, device_id: str):
    exec_sql("UPDATE cards SET bound_device_id=? WHERE token=?", (device_id, token))

def unbind_device_id(token: str):
    exec_sql("UPDATE cards SET bound_device_id=NULL, token_used=0 WHERE token=?", (token,))

def update_balance_by_token(token: str, newbal: float):
    exec_sql("UPDATE cards SET balance=? WHERE token=?", (float(newbal), token))

def adjust_balance(token: str, delta: float):
    exec_sql("UPDATE cards SET balance = balance + ? WHERE token=?", (float(delta), token))

def log_transaction(from_token, from_name, to_token, to_name, amount, reason):
    exec_sql("INSERT INTO transactions (ts,from_token,from_name,to_token,to_name,amount,reason) VALUES (?,?,?,?,?,?,?)",
             (int(time.time()), from_token, from_name, to_token, to_name, float(amount), reason))

def get_recent_transactions(token: str, limit: int = 10):
    # embed limit (già sanificato come int)
    placeholder = "%s" if USE_PG else "?"
    conn = get_conn(); c = conn.cursor()
    c.execute(f"""
        SELECT ts, from_name, to_name, amount, reason
        FROM transactions
        WHERE from_token = {placeholder} OR to_token = {placeholder}
        ORDER BY ts DESC
        LIMIT {int(limit)}
    """, (token, token))
    rows = c.fetchall(); conn.close()
    return [{"ts": r[0], "from_name": r[1], "to_name": r[2], "amount": r[3], "reason": r[4]} for r in rows]

def fmt_ts(ts: int) -> str:
    try: return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except: return str(ts)

def get_settings():
    r = exec_sql("SELECT bank_name,logo_url,gradient_from,gradient_to,font_name FROM settings WHERE id=1", fetch="one")
    if not r:
        return {"bank_name":"Banca NFC","logo_url":"","gradient_from":"#0ea5e9","#gradient_to":"#8b5cf6","font_name":"Poppins"}
    return {"bank_name": r[0] or "Banca NFC", "logo_url": r[1] or "", "gradient_from": r[2] or "#0ea5e9",
            "gradient_to": r[3] or "#8b5cf6", "font_name": r[4] or "Poppins"}

def update_settings(bank_name, logo_url, gradient_from, gradient_to, font_name):
    exec_sql("UPDATE settings SET bank_name=?,logo_url=?,gradient_from=?,gradient_to=?,font_name=? WHERE id=1",
             (bank_name.strip(), logo_url.strip(), gradient_from.strip(), gradient_to.strip(), font_name.strip()))

# ---------- SESSIONS ----------
def create_session_for_token(token: str):
    sid = secrets.token_urlsafe(24)
    now = int(time.time())
    exec_sql("INSERT INTO sessions (sid, token, expires, created_at) VALUES (?, ?, ?, ?)",
             (sid, token, now + SESSION_TTL, now))
    return sid

def get_session_info(sid: str):
    r = exec_sql("SELECT token,expires,created_at FROM sessions WHERE sid=?", (sid,), fetch="one")
    if not r: return None
    token, expires, created_at = r
    now = int(time.time())
    if now > (expires or 0):
        exec_sql("DELETE FROM sessions WHERE sid=?", (sid,))
        return None
    return {"token": token, "expires": int(expires or 0), "created_at": int(created_at or 0)}

def delete_session(sid: str):
    exec_sql("DELETE FROM sessions WHERE sid=?", (sid,))

# ---------- COOKIE / RENDER ----------
def is_https(request: Request) -> bool:
    xf = request.headers.get("x-forwarded-proto", "")
    return (request.url.scheme == "https") or ("https" in xf)

def set_cookie(resp, name, value, max_age=None, httponly=False, request: Request = None):
    resp.set_cookie(name, value, max_age=max_age, samesite="Lax", httponly=httponly,
                    secure=is_https(request) if request else False, path="/")

def render_page(inner_html: str, title: str = "") -> HTMLResponse:
    s = get_settings()
    title_text = title or s["bank_name"]
    font = s["font_name"] or "Poppins"
    google_font = font.replace(" ", "+")
    logo_html = f"<img src='{html_lib.escape(s['logo_url'])}' alt='' style='height:28px;margin-right:10px;border-radius:6px'>" if s["logo_url"] else ""
    header_name = html_lib.escape(s["bank_name"])
    html = f"""<!doctype html><html lang="it"><head>
      <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>{html_lib.escape(title_text)}</title>
      <link href="https://fonts.googleapis.com/css2?family={google_font}:wght@400;600;700&display=swap" rel="stylesheet">
      <style>
        :root {{ --grad-from:{html_lib.escape(s["gradient_from"])}; --grad-to:{html_lib.escape(s["gradient_to"])}; }}
        body {{ margin:0;font-family:'{font}',system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;
          background:linear-gradient(135deg,var(--grad-from),var(--grad-to));min-height:100vh;color:#0f172a; }}
        .wrap {{ max-width:980px;margin:0 auto;padding:16px; }}
        .nav {{ display:flex;justify-content:space-between;align-items:center;background:rgba(255,255,255,0.85);
          backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,0.6);border-radius:14px;padding:10px 14px;margin:16px 0; }}
        .brand {{ display:flex;align-items:center;font-weight:700;font-size:18px; }}
        .content {{ background:rgba(255,255,255,0.92);border:1px solid rgba(255,255,255,0.6);border-radius:16px;padding:18px;margin:16px 0; }}
        .btn {{ display:inline-block;padding:10px 14px;border:none;border-radius:10px;cursor:pointer;font-weight:600;
          text-decoration:none;color:#0f172a;background:#e2e8f0; }}
        .btn.primary {{ background:#0ea5e9;color:#fff; }} .btn.secondary {{ background:#64748b;color:#fff; }}
        .btn.danger {{ background:#ef4444;color:#fff; }} .btn.success {{ background:#10b981;color:#fff; }}
        input,textarea {{ width:100%;padding:10px;border:1px solid #e2e8f0;border-radius:10px;box-sizing:border-box;margin-top:6px;margin-bottom:8px; }}
        table {{ width:100%;border-collapse:collapse; }} th,td {{ padding:10px;border-bottom:1px solid #e2e8f0;text-align:left; }}
        .muted {{ color:#475569;font-size:13px; }} .pill {{ display:inline-block;padding:2px 8px;border-radius:999px;background:#e2e8f0;font-size:12px; }}
        .mono {{ font-family:ui-monospace,Consolas,monospace; }} .grid {{ display:grid;gap:12px; }}
        @media(min-width:720px) {{ .grid.cols-2 {{ grid-template-columns:1fr 1fr; }} .grid.cols-3 {{ grid-template-columns:1fr 1fr 1fr; }} }}
      </style></head><body>
      <div class="wrap">
        <div class="nav">
          <div class="brand">{logo_html}<span>{header_name}</span></div>
          <div style="display:flex;gap:8px"><a class="btn" href="/">Home</a></div>
        </div>
        <div class="content">{inner_html}</div>
      </div></body></html>"""
    return HTMLResponse(html)

def fmt_bonsaura(a: float) -> str:
    try: return f"{float(a):.2f} Bonsaura"
    except: return f"{a} Bonsaura"

# ---------- RECURRING CHARGES ----------
def apply_recurring_charges(token: str):
    now = int(time.time())
    r = exec_sql("SELECT name FROM cards WHERE token=?", (token,), fetch="one")
    from_name = r[0] if r else ""
    conn = get_conn(); c = conn.cursor()
    c.execute(adapt_sql("SELECT id,item_name,weekly_deduction,next_charge_at FROM purchases WHERE token=? AND active=1"), (token,))
    rows = c.fetchall()
    for pid, item_name, weekly, next_ts in rows:
        ts = int(next_ts or 0)
        charges = 0
        while ts and ts <= now:
            charges += 1
            ts += WEEK_SECONDS
        if charges > 0:
            amount = float(weekly) * charges
            c.execute(adapt_sql("UPDATE cards SET balance = balance - ? WHERE token=?"), (amount, token))
            c.execute(adapt_sql("UPDATE purchases SET next_charge_at=? WHERE id=?"), (ts, pid))
            c.execute(adapt_sql(
                "INSERT INTO transactions (ts,from_token,from_name,to_token,to_name,amount,reason) VALUES (?,?,?,?,?,?,?)"),
                (now, token, from_name, None, "Negozio", -amount,
                 f"Addebito {item_name} (-{weekly:.0f}/settimana) x{charges}"))
    conn.commit(); conn.close()

# ---------- ROUTES ----------
@app.get("/", response_class=HTMLResponse)
def home():
    inner = f"""
      <h2>Benvenuto</h2>
      <p class="muted">Accedi al pannello amministrativo o visualizza la lista carte. Inserisci la key.</p>
      <form class="grid cols-3" method="get" action="/go">
        <div><input name="key" placeholder="Chiave admin" required></div>
        <div><button class="btn primary" type="submit" name="dest" value="admin">Admin</button></div>
        <div><button class="btn secondary" type="submit" name="dest" value="lista">Lista</button></div>
      </form>
      <p class="muted">Per usare una carta: tap NFC su /launch/&lt;token&gt; (valido per {SCAN_WINDOW}s).</p>
    """
    return render_page(inner, "Home")

@app.get("/go")
def go(dest: str = "", key: str = ""):
    if dest == "admin": return RedirectResponse(f"/admin?key={key}", 302)
    if dest == "lista": return RedirectResponse(f"/lista?key={key}", 302)
    return render_page("<h3>Destinazione non valida</h3>", "Errore")

@app.get("/create", response_class=HTMLResponse)
def create_via_link(request: Request, name: str = "", code: str = "", initial: float = 0.0, desc: str = ""):
    if not name or not code:
        return render_page("<h3>Parametri mancanti (?name=&code=)</h3>", "Errore")
    r = exec_sql("SELECT COUNT(*) FROM cards", fetch="one")
    if (r[0] if r else 0) >= 10:
        return render_page("<h3>Limite 10 carte raggiunto</h3>", "Limite")
    token = create_site(name, code, initial, desc)
    if not token:
        return render_page("<h3>Nome già esistente</h3>", "Errore")
    base = str(request.base_url).rstrip("/")
    url = f"{base}/launch/{token}"
    inner = f"""
      <h3>✅ Carta '{html_lib.escape(name)}' creata</h3>
      <p>Saldo iniziale: {fmt_bonsaura(initial)}</p>
      <p>URL NFC:</p><pre class="mono">{html_lib.escape(url)}</pre>
    """
    return render_page(inner, "Carta creata")

@app.get("/launch/{token}")
@app.head("/launch/{token}")
def launch(token: str, request: Request):
    site = get_by_token(token)
    if not site:
        return render_page("<h3>Tag non valido</h3>", "Errore")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if site["bound_device_id"] and device_id != site["bound_device_id"]:
        return render_page("<h3>Accesso non autorizzato</h3>", "Bloccato")
    resp = RedirectResponse("/card", 302)
    if not device_id:
        device_id = secrets.token_hex(16)
        set_cookie(resp, DEVICE_COOKIE_NAME, device_id, max_age=60*60*24*365, httponly=True, request=request)
    sid = create_session_for_token(token)
    set_cookie(resp, SESSION_COOKIE_NAME, sid, max_age=SESSION_TTL, httponly=True, request=request)
    return resp

@app.get("/card", response_class=HTMLResponse)
def card_from_session(request: Request):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        return render_page(f"<h3>Sessione mancante</h3><p>Tap NFC (entro {SCAN_WINDOW}s).</p>", "Richiesto NFC")
    session = get_session_info(sid)
    if not session:
        return render_page("<h3>Sessione scaduta</h3>", "Scaduta")
    if int(time.time()) - session["created_at"] > SCAN_WINDOW:
        return render_page("<h3>Sessione non valida (scaduto timeout NFC)</h3>", "Non valida")
    site = get_by_token(session["token"])
    if not site:
        return render_page("<h3>Tag non valido</h3>", "Errore")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if site["bound_device_id"] and site["bound_device_id"] != device_id:
        return render_page("<h3>Accesso non autorizzato</h3>", "Bloccato")
    inner = f"""
      <h2>{html_lib.escape(site['name'])}</h2>
      <p><strong>Saldo:</strong> {fmt_bonsaura(site['balance'])}</p>
      <p class="muted">{html_lib.escape(site.get('description') or '')}</p>
      <h4>Accedi</h4>
      <form method="post" action="/unlock">
        <input type="hidden" name="token" value="{html_lib.escape(site['token'])}">
        <input name="pin" type="password" placeholder="PIN" required>
        <button class="btn primary" type="submit">Accedi</button>
      </form>
      <p class="muted">Completa entro {SCAN_WINDOW}s dal tap.</p>
    """
    return render_page(inner, site["name"])

@app.post("/unlock", response_class=HTMLResponse)
def unlock(request: Request, token: str = Form(...), pin: str = Form(...)):
    site = get_by_token(token)
    if not site: return render_page("<h3>Token non valido</h3>", "Errore")
    if site["pin_hash"] != hash_pin(pin):
        return render_page("<h3>PIN errato</h3><p><a href='/card'>Riprova</a></p>", "Errore")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_id:
        return render_page("<h3>Cookie dispositivo mancante (rifai tap)</h3>", "Errore")
    if not site["bound_device_id"]:
        bind_device_id(token, device_id)
        mark_token_used(token)
        site = get_by_token(token)
    if site["bound_device_id"] != device_id:
        return render_page("<h3>Dispositivo non autorizzato</h3>", "Bloccato")
    apply_recurring_charges(site["token"])
    site = get_by_token(token)
    can_shop = site["balance"] >= 30.0
    shop_btn = '<a class="btn" href="/shop">Negozio</a>' if can_shop else '<button class="btn" disabled>Negozio (saldo &lt; 30)</button>'
    inner = f"""
      <h2>Benvenuto, {html_lib.escape(site['name'])}</h2>
      <p class="muted">Saldo: {fmt_bonsaura(site['balance'])}</p>
      <div class="grid cols-3">
        <div><a class="btn secondary" href="/leaderboard">Classifica</a></div>
        <div><a class="btn primary" href="/bank">Banca</a></div>
        <div>{shop_btn}</div>
      </div>
      <p class="muted">Oggetto disponibile: “Moccolone pencs” (+35 subito, -3/settimana).</p>
    """
    return render_page(inner, "Menu")

@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid: return render_page("<h3>Sessione mancante</h3>", "Richiesto")
    session = get_session_info(sid)
    if not session: return render_page("<h3>Sessione scaduta</h3>", "Scaduta")
    if int(time.time()) - session["created_at"] > SCAN_WINDOW:
        return render_page("<h3>Sessione non valida</h3>", "Errore")
    apply_recurring_charges(session["token"])
    rows = exec_sql("SELECT name,balance,token FROM cards ORDER BY balance DESC, id ASC", fetch="all")
    palette = ["#ef4444","#f97316","#f59e0b","#eab308","#84cc16","#22c55e","#06b6d4","#3b82f6","#8b5cf6","#db2777"]
    body = []
    for idx, r in enumerate(rows or [], start=1):
        name, balance, token = r
        me = token == session["token"]
        color = palette[(idx-1)%len(palette)]
        mark = " — tu" if me else ""
        body.append(f"""
          <tr style="border-left:8px solid {html_lib.escape(color)};{'font-weight:700;background:rgba(0,0,0,0.03);' if me else ''}">
            <td>#{idx}</td><td>{html_lib.escape(name)}{mark}</td><td>{fmt_bonsaura(balance)}</td>
          </tr>
        """)
    inner = f"""
      <h2>Classifica Bonsaura</h2>
      <table><thead><tr><th>Pos</th><th>Banca</th><th>Punti</th></tr></thead>
      <tbody>{''.join(body) or '<tr><td colspan=3 class=muted>Nessuna carta</td></tr>'}</tbody></table>
      <div class="grid cols-2" style="margin-top:12px">
        <div><a class="btn secondary" href="/bank">Banca</a></div>
        <div><a class="btn" href="/">Home</a></div>
      </div>
    """
    return render_page(inner, "Classifica")

@app.get("/bank", response_class=HTMLResponse)
def bank(request: Request):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid: return render_page("<h3>Sessione mancante</h3>", "Richiesto")
    session = get_session_info(sid)
    if not session: return render_page("<h3>Sessione scaduta</h3>", "Scaduta")
    if int(time.time()) - session["created_at"] > SCAN_WINDOW:
        return render_page("<h3>Sessione non valida</h3>", "Errore")
    apply_recurring_charges(session["token"])
    site = get_by_token(session["token"])
    if not site: return render_page("<h3>Tag non valido</h3>", "Errore")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if site["bound_device_id"] and site["bound_device_id"] != device_id:
        return render_page("<h3>Accesso non autorizzato</h3>", "Bloccato")
    recent = get_recent_transactions(site["token"], limit=10)
    rows_html = "".join(
        f"<tr><td>{fmt_ts(t['ts'])}</td><td>{html_lib.escape(t['from_name'] or '-')}</td>"
        f"<td>{html_lib.escape(t['to_name'] or '-')}</td><td>{fmt_bonsaura(t['amount'])}</td>"
        f"<td>{html_lib.escape(t['reason'] or '')}</td></tr>"
        for t in recent
    ) or '<tr><td colspan="5" class="muted">Nessuna transazione</td></tr>'
    inner = f"""
      <h2>{html_lib.escape(site['name'])}</h2>
      <p><strong>Saldo:</strong> {fmt_bonsaura(site['balance'])}</p>
      <p class="muted">{html_lib.escape(site.get('description') or '')}</p>
      <h4>Invia denaro</h4>
      <form method="post" action="/transfer">
        <input type="hidden" name="from_token" value="{html_lib.escape(site['token'])}">
        <input name="to_name" placeholder="Nome carta destinatario" required>
        <input name="amount" type="number" step="0.01" placeholder="Importo" required>
        <textarea name="reason" rows="2" placeholder="Motivazione (obbligatoria)" required></textarea>
        <button class="btn primary" type="submit">Invia</button>
      </form>
      <h4 style="margin-top:16px">Ultime operazioni</h4>
      <table>
        <thead><tr><th>Data</th><th>Da</th><th>A</th><th>Importo</th><th>Motivazione</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    """
    return render_page(inner, site["name"])

@app.post("/transfer", response_class=HTMLResponse)
def transfer(request: Request,
             from_token: str = Form(...),
             to_name: str = Form(...),
             amount: str = Form(...),
             reason: str = Form(...)):
    from_site = get_by_token(from_token)
    if not from_site: return render_page("<h3>Mittente non trovato</h3>", "Errore")
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not from_site["bound_device_id"] or from_site["bound_device_id"] != device_id:
        return render_page("<h3>Accesso non autorizzato</h3>", "Bloccato")
    to_name = to_name.strip()
    if not to_name: return render_page("<h3>Nome destinatario non valido</h3>", "Errore")
    reason = (reason or "").strip()
    if not reason: return render_page("<h3>Motivazione obbligatoria</h3>", "Errore")
    if len(reason) > 300: return render_page("<h3>Motivazione troppo lunga</h3>", "Errore")
    try: amt = float(amount)
    except: return render_page("<h3>Importo non valido</h3>", "Errore")
    if amt <= 0: return render_page("<h3>Importo deve essere positivo</h3>", "Errore")
    if from_site["balance"] < amt:
        return render_page(f"<h3>Saldo insufficiente ({fmt_bonsaura(from_site['balance'])})</h3>", "Errore")
    dest = get_by_name(to_name)
    if not dest:
        new_token = secrets.token_urlsafe(12)
        create_site(to_name, "0000", 0.0, "")
        dest = get_by_name(to_name)
    update_balance_by_token(from_site["token"], from_site["balance"] - amt)
    update_balance_by_token(dest["token"], dest["balance"] + amt)
    log_transaction(from_site["token"], from_site["name"], dest["token"], dest["name"], amt, reason)
    return render_page(
        f"<h3>Trasferimento di {fmt_bonsaura(amt)} a {html_lib.escape(to_name)} eseguito.</h3>"
        f"<p>Motivazione: {html_lib.escape(reason)}</p><p><a href='/card'>Torna</a></p>", "OK")

# ---------- ADMIN ----------
def require_key(key: str) -> bool:
    return key == ADMIN_KEY

@app.get("/lista", response_class=HTMLResponse)
def lista(key: str = ""):
    if not require_key(key):
        return render_page("<h3>Accesso negato</h3>", "403")
    rows = exec_sql("SELECT name,balance,bound_device_id,token_used,description FROM cards ORDER BY id", fetch="all")
    body = ""
    for name, balance, bound, used, desc in rows or []:
        body += f"""
          <tr>
            <td>{html_lib.escape(name)}</td>
            <td>{fmt_bonsaura(balance)}</td>
            <td><span class="pill">{'associata' if bound else 'libera'}</span></td>
            <td>{'sì' if used else 'no'}</td>
            <td>{html_lib.escape(desc or '')}</td>
          </tr>
        """
    inner = f"""
      <h2>Lista carte</h2>
      <table>
        <thead><tr><th>Nome</th><th>Saldo</th><th>Binding</th><th>Usata</th><th>Descrizione</th></tr></thead>
        <tbody>{body or '<tr><td colspan=5 class=muted>Nessuna carta</td></tr>'}</tbody>
      </table>
      <p class="muted">I token non sono mostrati.</p>
    """
    return render_page(inner, "Lista carte")

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, key: str = ""):
    if not require_key(key):
        return render_page("<h3>Accesso negato</h3>", "403")
    s = get_settings()
    base = str(request.base_url).rstrip("/")
    rows = exec_sql("SELECT name,token,balance,bound_device_id,description FROM cards ORDER BY id", fetch="all")
    cards_html = ""
    for name, token, balance, bound, desc in rows or []:
        token_e = html_lib.escape(token)
        url_nfc = html_lib.escape(f"{base}/launch/{token}")
        cards_html += f"""
          <tr>
            <td>{html_lib.escape(name)}<br>
              <span class="muted">token:</span> <code class="mono">{token_e}</code><br>
              <div style="display:flex;gap:6px;margin-top:6px">
                <button class="btn" type="button" onclick="copyText('{token_e}')">Copia token</button>
                <button class="btn secondary" type="button" onclick="copyText('{url_nfc}')">Copia URL NFC</button>
              </div>
            </td>
            <td>{fmt_bonsaura(balance)}</td>
            <td><span class="pill">{'associata' if bound else 'libera'}</span></td>
            <td>{html_lib.escape(desc or '')}</td>
            <td style="min-width:260px">
              <form style="display:inline-block" method="post" action="/admin/adjust">
                <input type="hidden" name="key" value="{html_lib.escape(key)}">
                <input type="hidden" name="token" value="{token_e}">
                <input name="delta" type="number" step="0.01" placeholder="+/-" required style="width:110px">
                <button class="btn success" type="submit">Applica</button>
              </form>
              <form style="display:inline-block" method="post" action="/admin/reset">
                <input type="hidden" name="key" value="{html_lib.escape(key)}">
                <input type="hidden" name="token" value="{token_e}">
                <button class="btn secondary" type="submit">Reset bind</button>
              </form>
              <form style="display:inline-block" method="post" action="/admin/delete">
                <input type="hidden" name="key" value="{html_lib.escape(key)}">
                <input type="hidden" name="token" value="{token_e}">
                <button class="btn danger" type="submit" onclick="return confirm('Eliminare?')">Elimina</button>
              </form>
            </td>
          </tr>
        """
    inner = f"""
      <h2>Admin</h2>
      <div class="grid cols-2">
        <div>
          <h3>Crea carta</h3>
          <form method="post" action="/admin/create">
            <input type="hidden" name="key" value="{html_lib.escape(key)}">
            <input name="name" placeholder="Nome" required>
            <input name="pin" placeholder="PIN" required>
            <input name="initial" type="number" step="0.01" placeholder="Saldo iniziale" value="0">
            <input name="desc" placeholder="Descrizione">
            <button class="btn primary" type="submit">Crea</button>
          </form>
        </div>
        <div>
          <h3>Personalizzazione</h3>
            <form method="post" action="/admin/settings">
              <input type="hidden" name="key" value="{html_lib.escape(key)}">
              <input name="bank_name" placeholder="Nome banca" value="{html_lib.escape(s['bank_name'])}">
              <input name="logo_url" placeholder="Logo URL" value="{html_lib.escape(s['logo_url'])}">
              <input name="gradient_from" placeholder="Gradiente da" value="{html_lib.escape(s['gradient_from'])}">
              <input name="gradient_to" placeholder="Gradiente a" value="{html_lib.escape(s['gradient_to'])}">
              <input name="font_name" placeholder="Font Google" value="{html_lib.escape(s['font_name'])}">
              <button class="btn primary" type="submit">Salva stile</button>
            </form>
        </div>
      </div>
      <h3>Carte</h3>
      <table>
        <thead><tr><th>Nome/Token</th><th>Saldo</th><th>Binding</th><th>Descrizione</th><th>Azioni</th></tr></thead>
        <tbody>{cards_html or '<tr><td colspan=5 class=muted>Nessuna carta</td></tr>'}</tbody>
      </table>
      <script>
        function copyText(t) {{
          if (navigator.clipboard) {{
            navigator.clipboard.writeText(t).then(()=>alert('Copiato')).catch(()=>window.prompt('Copia:', t));
          }} else window.prompt('Copia:', t);
        }}
      </script>
    """
    return render_page(inner, "Admin")

@app.post("/admin/create", response_class=HTMLResponse)
def admin_create(name: str = Form(""), pin: str = Form(""), initial: float = Form(0.0),
                 desc: str = Form(""), key: str = Form("")):
    if not require_key(key): return render_page("<h3>Accesso negato</h3>", "403")
    if not name or not pin: return render_page("<h3>Nome e PIN richiesti</h3>", "Errore")
    cnt = exec_sql("SELECT COUNT(*) FROM cards", fetch="one")[0]
    if cnt >= 10: return render_page("<h3>Limite 10 carte raggiunto</h3>", "Limite")
    token = create_site(name, pin, initial, desc)
    if not token: return render_page("<h3>Nome già esistente</h3>", "Errore")
    return RedirectResponse(f"/admin?key={key}", 302)

@app.post("/admin/adjust", response_class=HTMLResponse)
def admin_adjust(token: str = Form(""), delta: float = Form(0.0), key: str = Form("")):
    if not require_key(key): return render_page("<h3>Accesso negato</h3>", "403")
    site = get_by_token(token)
    if not site: return render_page("<h3>Carta non trovata</h3>", "Errore")
    try: d = float(delta)
    except: return render_page("<h3>Delta non valido</h3>", "Errore")
    adjust_balance(token, d)
    return RedirectResponse(f"/admin?key={key}", 302)

@app.post("/admin/reset", response_class=HTMLResponse)
def admin_reset(token: str = Form(""), key: str = Form("")):
    if not require_key(key): return render_page("<h3>Accesso negato</h3>", "403")
    if not get_by_token(token): return render_page("<h3>Token non trovato</h3>", "Errore")
    unbind_device_id(token)
    return RedirectResponse(f"/admin?key={key}", 302)

@app.post("/admin/delete", response_class=HTMLResponse)
def admin_delete(token: str = Form(""), key: str = Form("")):
    if not require_key(key): return render_page("<h3>Accesso negato</h3>", "403")
    if not get_by_token(token): return render_page("<h3>Token non trovato</h3>", "Errore")
    exec_sql("DELETE FROM cards WHERE token=?", (token,))
    return RedirectResponse(f"/admin?key={key}", 302)

@app.post("/admin/settings", response_class=HTMLResponse)
def admin_settings(bank_name: str = Form(""), logo_url: str = Form(""),
                   gradient_from: str = Form(""), gradient_to: str = Form(""),
                   font_name: str = Form(""), key: str = Form("")):
    if not require_key(key): return render_page("<h3>Accesso negato</h3>", "403")
    update_settings(bank_name or "Banca NFC", logo_url or "", gradient_from or "#0ea5e9",
                    gradient_to or "#8b5cf6", font_name or "Poppins")
    return RedirectResponse(f"/admin?key={key}", 302)

# ---------- SHOP ----------
@app.get("/shop", response_class=HTMLResponse)
def shop(request: Request):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid: return render_page("<h3>Sessione mancante</h3>", "Richiesto")
    session = get_session_info(sid)
    if not session: return render_page("<h3>Sessione scaduta</h3>", "Scaduta")
    if int(time.time()) - session["created_at"] > SCAN_WINDOW:
        return render_page("<h3>Sessione non valida</h3>", "Errore")
    apply_recurring_charges(session["token"])
    site = get_by_token(session["token"])
    if not site: return render_page("<h3>Tag non valido</h3>", "Errore")
    if site["balance"] < 30.0:
        return render_page("<h3>Negozio bloccato</h3><p>Saldo minimo 30.</p>", "Negozio")
    r = exec_sql("SELECT next_charge_at FROM purchases WHERE token=? AND item_code='moccolone' AND active=1",
                 (site["token"],), fetch="one")
    status_html = ""
    action_html = ""
    if r:
        nxt = time.strftime("%Y-%m-%d", time.localtime(int(r[0] or 0)))
        status_html = f"<p class='muted'>Moccolone attivo. Prossimo addebito: {html_lib.escape(nxt)} (-3/settimana)</p>"
    else:
        action_html = """
          <form method="post" action="/buy">
            <input type="hidden" name="item_code" value="moccolone">
            <button class="btn success" type="submit">Compra "Moccolone pencs" (+35, poi -3/settimana)</button>
          </form>
        """
    inner = f"""
      <h2>Negozio</h2>
      <div class="content" style="margin:0">
        <h3>Moccolone pencs</h3>
        <p>Bonus immediato +35, costo ricorrente -3 Bonsaura / settimana.</p>
        {status_html}{action_html}
      </div>
      <p class="muted">Saldo attuale: {fmt_bonsaura(site['balance'])}</p>
      <div class="grid cols-3">
        <a class="btn secondary" href="/leaderboard">Classifica</a>
        <a class="btn primary" href="/bank">Banca</a>
        <a class="btn" href="/">Home</a>
      </div>
    """
    return render_page(inner, "Negozio")

@app.post("/buy", response_class=HTMLResponse)
def buy(request: Request, item_code: str = Form(...)):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid: return render_page("<h3>Sessione mancante</h3>", "Richiesto")
    session = get_session_info(sid)
    if not session: return render_page("<h3>Sessione scaduta</h3>", "Scaduta")
    if int(time.time()) - session["created_at"] > SCAN_WINDOW:
        return render_page("<h3>Sessione non valida</h3>", "Errore")
    apply_recurring_charges(session["token"])
    site = get_by_token(session["token"])
    if not site: return render_page("<h3>Tag non valido</h3>", "Errore")
    if site["balance"] < 30.0:
        return render_page("<h3>Negozio bloccato (saldo < 30)</h3>", "Negozio")
    if item_code != "moccolone":
        return render_page("<h3>Articolo non valido</h3>", "Errore")
    r = exec_sql("SELECT 1 FROM purchases WHERE token=? AND item_code='moccolone' AND active=1",
                 (site["token"],), fetch="one")
    if r:
        return render_page("<h3>Già possiedi Moccolone</h3><p><a href='/shop'>Indietro</a></p>", "Negozio")
    now = int(time.time())
    exec_sql("UPDATE cards SET balance = balance + ? WHERE token=?", (35.0, site["token"]))
    exec_sql("INSERT INTO purchases (token,item_code,item_name,weekly_deduction,next_charge_at,started_at,active) VALUES (?,?,?,?,?,?,1)",
             (site["token"], "moccolone", "Moccolone pencs", 3.0, now + WEEK_SECONDS, now))
    log_transaction(None, "Negozio", site["token"], site["name"], 35.0,
                    "Acquisto Moccolone: bonus iniziale +35; addebito -3/settimana")
    return RedirectResponse("/shop", 302)
