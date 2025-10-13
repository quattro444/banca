# manage_sites.py
import sqlite3, secrets, hashlib, sys, os

DB = "cards.db"

def hash_pin(pin: str) -> str:
    import hashlib
    return hashlib.sha256(pin.encode()).hexdigest()

def count_sites():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM cards")
    n = c.fetchone()[0]
    conn.close()
    return n

def create_site(name, pin, initial=100):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    token = secrets.token_urlsafe(12)
    try:
        c.execute("INSERT INTO cards (name, token, pin_hash, balance) VALUES (?, ?, ?, ?)",
                  (name, token, hash_pin(pin), float(initial)))
        conn.commit()
    except sqlite3.IntegrityError as e:
        print("Errore: nome già esistente o altro:", e)
        conn.close()
        return None
    conn.close()
    return token

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python manage_sites.py NOME PIN [SALDO_INIZIALE]")
        sys.exit(1)
    name = sys.argv[1]
    pin = sys.argv[2]
    initial = float(sys.argv[3]) if len(sys.argv) > 3 else 100.0
    if count_sites() >= 5:
        print("Hai già raggiunto il limite di 5 siti.")
        sys.exit(1)
    token = create_site(name, pin, initial)
    if token:
        print("Creato sito:", name)
        print("Token (URL da scrivere sul tag):")
        print(f"/card/{token}")
