from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import List, Tuple
from .config import ensure_app_data_dir
from .models import Quote, QuoteItem, QuoteRecipient

# Quote status constants
STATUS_GENERATED = "generated"  # Quando abre tela de email
STATUS_SMTP_OK = "sent_smtp_ok"  # Envio SMTP bem-sucedido
STATUS_SMTP_FAIL = "sent_smtp_fail"  # Envio SMTP falhou
STATUS_THUNDERBIRD = "opened_thunderbird"  # Fallback Thunderbird

def db_path() -> Path:
    return ensure_app_data_dir() / "history.db"

def connect() -> sqlite3.Connection:
    p = db_path()
    conn = sqlite3.connect(str(p), timeout=15)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = -8000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        product_query TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        user_pc TEXT NOT NULL,
        status TEXT NOT NULL
    );""")
    # Migration: Add sent_at and send_error_message columns
    try:
        conn.execute("ALTER TABLE quotes ADD COLUMN sent_at TEXT;")
    except sqlite3.OperationalError as e:
        if "duplicate column name: sent_at" not in str(e):
            raise
    try:
        conn.execute("ALTER TABLE quotes ADD COLUMN send_error_message TEXT;")
    except sqlite3.OperationalError as e:
        if "duplicate column name: send_error_message" not in str(e):
            raise

    conn.execute("""CREATE TABLE IF NOT EXISTS quote_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id INTEGER NOT NULL,
        line_text TEXT NOT NULL,
        FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
    );""")
    conn.execute("""CREATE TABLE IF NOT EXISTS quote_recipients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id INTEGER NOT NULL,
        empresa TEXT NOT NULL,
        contato_nome TEXT NOT NULL,
        email TEXT NOT NULL,
        telefone TEXT NOT NULL,
        material_produto TEXT NOT NULL,
        source_file TEXT NOT NULL,
        source_row INTEGER NOT NULL,
        FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
    );""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quotes_created_at ON quotes(created_at DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quotes_status ON quotes(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_items_quote ON quote_items(quote_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_recipients_quote ON quote_recipients(quote_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_recipients_email ON quote_recipients(email);")
    conn.commit()

def insert_quote(conn: sqlite3.Connection, q: Quote) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO quotes(created_at, product_query, subject, body, user_pc, status) VALUES (?,?,?,?,?,?)",
        (q.created_at, q.product_query, q.subject, q.body, q.user_pc, q.status),
    )
    quote_id = int(cur.lastrowid)
    for item in q.items:
        cur.execute(
            "INSERT INTO quote_items(quote_id, line_text) VALUES (?,?)",
            (quote_id, item.line_text),
        )
    for r in q.recipients:
        cur.execute(
            """INSERT INTO quote_recipients(
                quote_id, empresa, contato_nome, email, telefone, material_produto, source_file, source_row
            ) VALUES (?,?,?,?,?,?,?,?)""",
            (quote_id, r.empresa, r.contato_nome, r.email, r.telefone, r.material_produto, r.source_file, r.source_row),
        )
    conn.commit()
    return quote_id

def update_quote_status(conn: sqlite3.Connection, quote_id: int, status: str) -> None:
    conn.execute("UPDATE quotes SET status=? WHERE id=?", (status, quote_id))
    conn.commit()

def list_quotes(conn: sqlite3.Connection, limit: int = 200) -> List[Tuple[int, str, str, int, str]]:
    """Returns list of (id, created_at, product_query, recipient_count, status)."""
    cur = conn.cursor()
    cur.execute(
        """SELECT q.id, q.created_at, q.product_query,
                  (SELECT COUNT(*) FROM quote_recipients r WHERE r.quote_id=q.id) as rc,
                  q.status
           FROM quotes q
           ORDER BY q.id DESC
           LIMIT ?""",
        (limit,),
    )
    return [(int(r[0]), str(r[1]), str(r[2]), int(r[3]), str(r[4])) for r in cur.fetchall()]

def get_quote(conn: sqlite3.Connection, quote_id: int) -> Quote:
    cur = conn.cursor()
    cur.execute("SELECT id, created_at, product_query, subject, body, user_pc, status FROM quotes WHERE id=?", (quote_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError("Cotação não encontrada")
    q = Quote(
        id=int(row[0]),
        created_at=str(row[1]),
        product_query=str(row[2]),
        subject=str(row[3]),
        body=str(row[4]),
        user_pc=str(row[5]),
        status=str(row[6]),
    )
    cur.execute("SELECT line_text FROM quote_items WHERE quote_id=? ORDER BY id ASC", (quote_id,))
    q.items = [QuoteItem(line_text=str(r[0])) for r in cur.fetchall()]
    cur.execute("""SELECT empresa, contato_nome, email, telefone, material_produto, source_file, source_row
                    FROM quote_recipients WHERE quote_id=? ORDER BY id ASC""", (quote_id,))
    q.recipients = [
        QuoteRecipient(
            empresa=str(r[0]),
            contato_nome=str(r[1]),
            email=str(r[2]),
            telefone=str(r[3]),
            material_produto=str(r[4]),
            source_file=str(r[5]),
            source_row=int(r[6]),
        )
        for r in cur.fetchall()
    ]
    return q
