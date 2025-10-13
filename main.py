from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3, os, secrets

app = FastAPI()

DB_FILE = "cards.db"
ADMIN_KEY = "bunald1"

# === DATABASE SETUP ===
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        token TEXT UNIQUE,
        saldo REAL DEFAULT 0,
        bound INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()

# === HOME PAGE ===
@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <html>
    <head>
        <title>Banca Bonsiorium</title>
        <style>
            body { font-family: Arial; background: #f0f4f8; text-align: center; margin-top: 80px; }
            h1 { color: #003366; }
            a.button { display: inline-block; padding: 12px 24px; background: #0078ff; color: white;
                       text-decoration: none; border-radius: 8px; font-weight: bold; }
            a.button:hover { background: #005fcc; }
        </style>
    </head>
    <body>
        <h1>🏦 Banca Bonsiorium</h1>
        <p>Benvenuto! Puoi creare o visualizzare le banche dal pannello qui sotto.</p>
        <a class="button" href="/create_form">➕ Crea una nuova banca</a>
        <br><br>
        <a class="button" href="/admin?key=bunald1">🔑 Vai al pannello admin</a>
    </body>
    </html>
    """

# === PAGINA FORM PER CREARE BANCA ===
@app.get("/create_form", response_class=HTMLResponse)
async def create_form():
    return """
    <html>
    <head>
        <title>Crea una nuova banca</title>
        <style>
            body { font-family: Arial; background: #eef2f3; text-align: center; margin-top: 80px; }
            form { background: white; display: inline-block; padding: 40px; border-radius: 15px;
                   box-shadow: 0 0 10px rgba(0,0,0,0.1); }
            input, button { margin: 10px; padding: 10px; width: 250px; border-radius: 8px; border: 1px solid #ccc; }
            button { background: #0078ff; color: white; font-weight: bold; cursor: pointer; }
            button:hover { background: #005fcc; }
        </style>
    </head>
    <body>
        <h2>➕ Crea una nuova banca</h2>
        <form action="/create_bank" method="post">
            <input name="name" placeholder="Nome banca" required><br>
            <input name="bound" placeholder="Bound (es. 10)" type="number" required><br>
            <input name="saldo" placeholder="Saldo iniziale (€)" type="number" required><br>
            <button type="submit">Crea banca</button>
        </form>
    </body>
    </html>
    """

# === CREA NUOVA BANCA (FORM POST) ===
@app.post("/create_bank", response_class=HTMLResponse)
async def create_bank(name: str = Form(...), bound: int = Form(...), saldo: float = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # controlla se esiste già una banca con lo stesso nome
    c.execute("SELECT id FROM cards WHERE name = ?", (name,))
    if c.fetchone():
        conn.close()
        return f"<p>⚠️ Una banca con il nome <b>{name}</b> esiste già! Torna indietro e scegline un altro.</p>"

    token = secrets.token_hex(8)
    c.execute("INSERT INTO cards (name, token, saldo, bound) VALUES (?, ?, ?, ?)",
              (name, token, saldo, bound))
    conn.commit()
    conn.close()

    site_url = f"/card/{name}?token={token}"
    return RedirectResponse(url=site_url, status_code=303)

# === VISUALIZZA BANCA ===
@app.get("/card/{name}", response_class=HTMLResponse)
async def view_card(name: str, token: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT saldo, bound FROM cards WHERE name = ? AND token = ?", (name, token))
    row = c.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Banca non trovata o token errato.")

    saldo, bound = row
    return f"""
    <html>
    <head>
        <title>{name}</title>
        <style>
            body {{ font-family: Arial; background-color: #f4f6f8; text-align: center; margin-top: 100px; }}
            .card {{ background: white; display: inline-block; padding: 30px; border-radius: 15px;
                     box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
            h2 {{ color: #003366; }}
            p {{ color: #333; font-size: 18px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>🏦 {name}</h2>
            <p><b>Saldo:</b> €{saldo}</p>
            <p><b>Bound:</b> {bound}</p>
        </div>
    </body>
    </html>
    """

# === ADMIN PANEL ===
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    key = request.query_params.get("key")
    if key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Accesso negato")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, token, saldo, bound FROM cards")
    cards = c.fetchall()
    conn.close()

    rows = "".join([
        f"<tr><td>{name}</td><td>{token}</td><td>€{saldo}</td><td>{bound}</td></tr>"
        for name, token, saldo, bound in cards
    ])

    return f"""
    <html>
    <head>
        <title>Pannello Admin</title>
        <style>
            body {{ font-family: Arial; background-color: #eef2f3; text-align: center; }}
            table {{ margin: auto; border-collapse: collapse; width: 80%; background: white;
                     box-shadow: 0 0 8px rgba(0,0,0,0.1); }}
            th, td {{ padding: 10px 20px; border-bottom: 1px solid #ddd; }}
            th {{ background-color: #0078ff; color: white; }}
            tr:hover {{ background-color: #f1f1f1; }}
        </style>
    </head>
    <body>
        <h1>🏛️ Pannello Admin</h1>
        <a href='/create_form'>➕ Crea nuova banca</a>
        <table>
            <tr><th>Nome</th><th>Token</th><th>Saldo</th><th>Bound</th></tr>
            {rows if rows else '<tr><td colspan=4>Nessuna banca creata</td></tr>'}
        </table>
    </body>
    </html>
    """

# === AVVIO LOCALE ===
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
