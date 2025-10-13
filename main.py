from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3

app = FastAPI()

DB_FILE = "cards.db"

# --- DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS banks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            pin TEXT,
            balance REAL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- HTML BASE STYLE ---
def base_html(content: str, title="Banca Bonsiorium"):
    return f"""
    <html>
    <head>
        <title>{title}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #00416A, #E4E5E6);
                color: #222;
                margin: 0; padding: 0;
                text-align: center;
            }}
            .container {{
                max-width: 450px;
                background: white;
                margin: 60px auto;
                padding: 30px;
                border-radius: 16px;
                box-shadow: 0 6px 20px rgba(0,0,0,0.2);
            }}
            h1 {{ color: #00416A; }}
            input, button {{
                width: 90%;
                padding: 10px;
                margin: 8px 0;
                border-radius: 8px;
                border: 1px solid #ccc;
                font-size: 15px;
            }}
            button {{
                background: #00416A;
                color: white;
                border: none;
                font-weight: bold;
                transition: 0.2s;
            }}
            button:hover {{
                background: #0366a8;
                cursor: pointer;
            }}
            a {{
                color: #00416A;
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
            }}
            th, td {{
                border-bottom: 1px solid #ddd;
                padding: 8px;
            }}
            th {{
                background: #00416A;
                color: white;
            }}
        </style>
    </head>
    <body>
        <div class="container">{content}</div>
    </body>
    </html>
    """

# --- HOME ---
@app.get("/", response_class=HTMLResponse)
async def home():
    html = """
        <h1>🏦 Banca Bonsiorium</h1>
        <p>Benvenuto! Scegli un'azione:</p>
        <p><a href='/crea'><button>➕ Crea nuova banca</button></a></p>
        <p><a href='/lista'><button>📋 Lista banche</button></a></p>
    """
    return HTMLResponse(base_html(html))

# --- FORM CREAZIONE BANCA ---
@app.get("/crea", response_class=HTMLResponse)
async def crea_form():
    html = """
        <h2>➕ Crea una nuova banca</h2>
        <form action="/crea" method="post">
            <input type="text" name="name" placeholder="Nome banca" required><br>
            <input type="password" name="pin" placeholder="PIN segreto" required><br>
            <input type="number" name="initial_balance" value="0" step="0.01" placeholder="Saldo iniziale"><br>
            <button type="submit">Crea</button>
        </form>
        <br><a href="/">⬅ Torna alla home</a>
    """
    return HTMLResponse(base_html(html, "Crea banca"))

@app.post("/crea", response_class=HTMLResponse)
async def crea_banca(name: str = Form(...), pin: str = Form(...), initial_balance: float = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM banks")
    count = c.fetchone()[0]

    if count >= 5:
        conn.close()
        return HTMLResponse(base_html("<h2>❌ Hai già raggiunto il limite massimo di 5 banche.</h2><a href='/'>Torna</a>"))

    try:
        c.execute("INSERT INTO banks (name, pin, balance) VALUES (?, ?, ?)", (name, pin, initial_balance))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return HTMLResponse(base_html("<h2>⚠️ Nome banca già esistente!</h2><a href='/crea'>Riprova</a>"))
    
    conn.close()
    return RedirectResponse(f"/banca/{name}", status_code=303)

# --- LISTA BANCHE ---
@app.get("/lista", response_class=HTMLResponse)
async def lista_banche():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, balance FROM banks")
    banks = c.fetchall()
    conn.close()

    rows = "".join([f"<tr><td><a href='/banca/{b[0]}'>{b[0]}</a></td><td>{b[1]:.2f} €</td></tr>" for b in banks])
    html = f"""
        <h2>📋 Banche registrate</h2>
        <table>
            <tr><th>Nome</th><th>Saldo</th></tr>
            {rows}
        </table>
        <br><a href='/'>⬅ Torna</a>
    """
    return HTMLResponse(base_html(html, "Lista banche"))

# --- PAGINA BANCA ---
@app.get("/banca/{name}", response_class=HTMLResponse)
async def mostra_banca(name: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, balance FROM banks WHERE name=?", (name,))
    banca = c.fetchone()
    conn.close()

    if not banca:
        return HTMLResponse(base_html("<h2>❌ Banca non trovata.</h2><a href='/lista'>Torna</a>"))

    html = f"""
        <h1>{banca[0]}</h1>
        <h3>Saldo: {banca[1]:.2f} €</h3>

        <h4>💸 Invia denaro</h4>
        <form action="/invia" method="post">
            <input type="text" name="sender" value="{banca[0]}" readonly><br>
            <input type="text" name="recipient" placeholder="Destinatario" required><br>
            <input type="number" name="amount" step="0.01" placeholder="Importo" required><br>
            <input type="password" name="pin" placeholder="PIN segreto" required><br>
            <button type="submit">Invia</button>
        </form>

        <br><a href='/lista'>⬅ Torna alla lista</a>
    """
    return HTMLResponse(base_html(html, banca[0]))

# --- INVIA SOLDI ---
@app.post("/invia", response_class=HTMLResponse)
async def invia(sender: str = Form(...), recipient: str = Form(...), amount: float = Form(...), pin: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT balance, pin FROM banks WHERE name=?", (sender,))
    sender_data = c.fetchone()

    if not sender_data:
        conn.close()
        return HTMLResponse(base_html("<h2>❌ Mittente non trovato.</h2>"))

    balance, correct_pin = sender_data
    if pin != correct_pin:
        conn.close()
        return HTMLResponse(base_html("<h2>🔒 PIN errato!</h2>"))

    if balance < amount:
        conn.close()
        return HTMLResponse(base_html("<h2>⚠️ Saldo insufficiente!</h2>"))

    c.execute("SELECT balance FROM banks WHERE name=?", (recipient,))
    recipient_data = c.fetchone()

    if not recipient_data:
        conn.close()
        return HTMLResponse(base_html("<h2>❌ Banca destinataria non trovata.</h2>"))

    new_sender_balance = balance - amount
    new_recipient_balance = recipient_data[0] + amount

    c.execute("UPDATE banks SET balance=? WHERE name=?", (new_sender_balance, sender))
    c.execute("UPDATE banks SET balance=? WHERE name=?", (new_recipient_balance, recipient))
    conn.commit()
    conn.close()

    return RedirectResponse(f"/banca/{sender}", status_code=303)
