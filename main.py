from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import sqlite3, secrets

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# === DATABASE INIT ===
def init_db():
    conn = sqlite3.connect("banche.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE,
            saldo INTEGER,
            bound INTEGER,
            token TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# === HOME ===
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# === ADMIN PANEL ===
@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, key: str = None):
    if key != "bunaldi1":  # chiave di sicurezza
        return HTMLResponse("Accesso negato", status_code=403)

    conn = sqlite3.connect("banche.db")
    c = conn.cursor()
    c.execute("SELECT nome, saldo, bound FROM cards")
    banche = c.fetchall()
    conn.close()

    return templates.TemplateResponse("admin.html", {"request": request, "banche": banche})


# === FORM CREAZIONE BANCA ===
@app.get("/create_form", response_class=HTMLResponse)
async def create_form(request: Request):
    return templates.TemplateResponse("create_form.html", {"request": request})


@app.post("/create", response_class=RedirectResponse)
async def create_banca(nome: str = Form(...), saldo: int = Form(...), bound: int = Form(...)):
    token = secrets.token_hex(8)
    conn = sqlite3.connect("banche.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO cards (nome, saldo, bound, token) VALUES (?, ?, ?, ?)",
              (nome, saldo, bound, token))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin?key=bunaldi1", status_code=303)


# === PAGINA PUBBLICA BANCA (UTENTE) ===
@app.get("/banca/{nome}", response_class=HTMLResponse)
async def mostra_banca(request: Request, nome: str):
    conn = sqlite3.connect("banche.db")
    c = conn.cursor()
    c.execute("SELECT nome, saldo, bound FROM cards WHERE nome = ?", (nome,))
    banca = c.fetchone()
    conn.close()

    if not banca:
        return HTMLResponse("Banca non trovata", status_code=404)

    banca_data = {
        "nome": banca[0],
        "saldo": banca[1],
        "bound": banca[2],
    }

    return templates.TemplateResponse("banca.html", {"request": request, "banca": banca_data})
