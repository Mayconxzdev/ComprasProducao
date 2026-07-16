"""
Local Supplier Database Management
Handles local supplier registration and master data overrides
"""
from __future__ import annotations
import json
import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class LocalSupplier:
    """Local supplier record"""
    local_supplier_id: Optional[int]
    empresa: str
    contato: Optional[str]
    email: str
    telefone: Optional[str]
    cidade: Optional[str]
    uf: Optional[str]
    endereco: Optional[str]
    obs: Optional[str]
    created_at: str

@dataclass
class LocalSupplierItem:
    """Link between local supplier and item"""
    id: Optional[int]
    local_supplier_id: int
    item_id: str
    serv_corte: bool = False
    serv_dobra: bool = False
    serv_entrega: bool = False
    serv_retira: bool = False
    obs_link: Optional[str] = None
    prioridade: Optional[int] = None
    prazo_entrega_dias: Optional[int] = None

@dataclass
class SupplierOverride:
    """Override for master supplier data"""
    id: Optional[int]
    supplier_id: str  # SUPPLIER_ID from master XLSX
    email_override: Optional[str] = None
    contato_override: Optional[str] = None
    telefone_override: Optional[str] = None
    endereco_override: Optional[str] = None
    cidade_override: Optional[str] = None
    uf_override: Optional[str] = None
    obs_override: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class SupplierUiOverride:
    """UI-level overrides used by the Qt suppliers table."""
    supplier_key: str
    empresa_override: Optional[str] = None
    contato_override: Optional[str] = None
    email_override: Optional[str] = None
    telefone_override: Optional[str] = None
    produtos_override: Optional[str] = None
    updated_at: Optional[str] = None


def get_db_path() -> Path:
    """Get path to local.db in %APPDATA%/ComprasApp"""
    import os
    appdata = os.environ.get('APPDATA', '')
    if not appdata:
        # Fallback to current directory
        return Path('local.db')

    compras_dir = Path(appdata) / 'ComprasApp'
    compras_dir.mkdir(parents=True, exist_ok=True)
    return compras_dir / 'local.db'


def init_local_db(conn: sqlite3.Connection):
    """Initialize local database schema (migration-safe)"""
    cursor = conn.cursor()

    # Table: local_suppliers
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS local_suppliers (
            local_supplier_id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL,
            contato TEXT,
            email TEXT NOT NULL,
            telefone TEXT,
            cidade TEXT,
            uf TEXT,
            endereco TEXT,
            obs TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Table: local_supplier_items
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS local_supplier_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_supplier_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            serv_corte INTEGER DEFAULT 0,
            serv_dobra INTEGER DEFAULT 0,
            serv_entrega INTEGER DEFAULT 0,
            serv_retira INTEGER DEFAULT 0,
            obs_link TEXT,
            prioridade INTEGER,
            prazo_entrega_dias INTEGER,
            FOREIGN KEY(local_supplier_id) REFERENCES local_suppliers(local_supplier_id)
        )
    """)

    # Table: supplier_overrides
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS supplier_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id TEXT NOT NULL UNIQUE,
            email_override TEXT,
            contato_override TEXT,
            telefone_override TEXT,
            endereco_override TEXT,
            cidade_override TEXT,
            uf_override TEXT,
            obs_override TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    # Table: supplier_ui_overrides (Qt inline edit overlays)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS supplier_ui_overrides (
            supplier_key TEXT PRIMARY KEY,
            empresa_override TEXT,
            contato_override TEXT,
            email_override TEXT,
            telefone_override TEXT,
            produtos_override TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    # Table: pending_master_sync (best-effort queue for NAS master writes)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_master_sync (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Indexes for performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_local_supplier_email ON local_suppliers(email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_local_items_supplier ON local_supplier_items(local_supplier_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_local_items_item ON local_supplier_items(item_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_overrides_supplier ON supplier_overrides(supplier_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ui_overrides_supplier ON supplier_ui_overrides(supplier_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_master_sync_key ON pending_master_sync(sync_key)")

    conn.commit()
    logger.info("Local database initialized")


def connect_local_db() -> sqlite3.Connection:
    """Connect to local database"""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = -8000;")
    conn.row_factory = sqlite3.Row
    init_local_db(conn)
    return conn


# ============ LOCAL SUPPLIERS CRUD ============

def add_local_supplier(
    conn: sqlite3.Connection,
    empresa: str,
    email: str,
    contato: Optional[str] = None,
    telefone: Optional[str] = None,
    cidade: Optional[str] = None,
    uf: Optional[str] = None,
    endereco: Optional[str] = None,
    obs: Optional[str] = None
) -> int:
    """Add new local supplier, returns local_supplier_id"""
    email = email.strip().lower()
    if not email or '@' not in email:
        raise ValueError("Email inválido")

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO local_suppliers
        (empresa, contato, email, telefone, cidade, uf, endereco, obs, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (empresa, contato, email, telefone, cidade, uf, endereco, obs, created_at))

    conn.commit()
    supplier_id = cursor.lastrowid
    logger.info(f"Added local supplier: {empresa} (ID: {supplier_id})")
    return supplier_id


def find_local_supplier_by_email(conn: sqlite3.Connection, email: str) -> Optional[LocalSupplier]:
    email = (email or "").strip().lower()
    if not email:
        return None
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM local_suppliers WHERE lower(email) = ? LIMIT 1", (email,))
    row = cursor.fetchone()
    if not row:
        return None
    return LocalSupplier(
        local_supplier_id=row["local_supplier_id"],
        empresa=row["empresa"],
        contato=row["contato"],
        email=row["email"],
        telefone=row["telefone"],
        cidade=row["cidade"],
        uf=row["uf"],
        endereco=row["endereco"],
        obs=row["obs"],
        created_at=row["created_at"],
    )


def add_local_supplier_item(
    conn: sqlite3.Connection,
    local_supplier_id: int,
    item_id: str,
    serv_corte: bool = False,
    serv_dobra: bool = False,
    serv_entrega: bool = False,
    serv_retira: bool = False,
    obs_link: Optional[str] = None,
    prioridade: Optional[int] = None,
    prazo_entrega_dias: Optional[int] = None
) -> int:
    """Link local supplier to item"""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id FROM local_supplier_items
        WHERE local_supplier_id = ? AND item_id = ?
        LIMIT 1
        """,
        (local_supplier_id, item_id),
    )
    existing = cursor.fetchone()
    if existing:
        return int(existing["id"])
    cursor.execute("""
        INSERT INTO local_supplier_items
        (local_supplier_id, item_id, serv_corte, serv_dobra, serv_entrega, serv_retira,
         obs_link, prioridade, prazo_entrega_dias)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        local_supplier_id, item_id,
        1 if serv_corte else 0,
        1 if serv_dobra else 0,
        1 if serv_entrega else 0,
        1 if serv_retira else 0,
        obs_link, prioridade, prazo_entrega_dias
    ))

    conn.commit()
    link_id = cursor.lastrowid
    logger.info(f"Linked local supplier {local_supplier_id} to item {item_id}")
    return link_id


def get_all_local_suppliers(conn: sqlite3.Connection) -> List[LocalSupplier]:
    """Get all local suppliers"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM local_suppliers ORDER BY empresa")

    suppliers = []
    for row in cursor.fetchall():
        suppliers.append(LocalSupplier(
            local_supplier_id=row['local_supplier_id'],
            empresa=row['empresa'],
            contato=row['contato'],
            email=row['email'],
            telefone=row['telefone'],
            cidade=row['cidade'],
            uf=row['uf'],
            endereco=row['endereco'],
            obs=row['obs'],
            created_at=row['created_at']
        ))

    return suppliers


def get_local_supplier_items(conn: sqlite3.Connection, local_supplier_id: int) -> List[LocalSupplierItem]:
    """Get all items linked to a local supplier"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM local_supplier_items
        WHERE local_supplier_id = ?
    """, (local_supplier_id,))

    items = []
    for row in cursor.fetchall():
        items.append(LocalSupplierItem(
            id=row['id'],
            local_supplier_id=row['local_supplier_id'],
            item_id=row['item_id'],
            serv_corte=bool(row['serv_corte']),
            serv_dobra=bool(row['serv_dobra']),
            serv_entrega=bool(row['serv_entrega']),
            serv_retira=bool(row['serv_retira']),
            obs_link=row['obs_link'],
            prioridade=row['prioridade'],
            prazo_entrega_dias=row['prazo_entrega_dias']
        ))

    return items


def update_local_supplier(
    conn: sqlite3.Connection,
    *,
    local_supplier_id: int,
    empresa: Optional[str] = None,
    contato: Optional[str] = None,
    email: Optional[str] = None,
    telefone: Optional[str] = None,
    cidade: Optional[str] = None,
    uf: Optional[str] = None,
    endereco: Optional[str] = None,
    obs: Optional[str] = None,
) -> None:
    """Update local supplier fields atomically."""
    updates: list[str] = []
    params: list[object] = []

    if empresa is not None:
        updates.append("empresa = ?")
        params.append(empresa.strip())
    if contato is not None:
        updates.append("contato = ?")
        params.append(contato.strip())
    if email is not None:
        email_clean = email.strip().lower()
        if not email_clean or '@' not in email_clean:
            raise ValueError("Email invalido")
        updates.append("email = ?")
        params.append(email_clean)
    if telefone is not None:
        updates.append("telefone = ?")
        params.append(telefone.strip())
    if cidade is not None:
        updates.append("cidade = ?")
        params.append(cidade.strip())
    if uf is not None:
        updates.append("uf = ?")
        params.append(uf.strip())
    if endereco is not None:
        updates.append("endereco = ?")
        params.append(endereco.strip())
    if obs is not None:
        updates.append("obs = ?")
        params.append(obs.strip())

    if not updates:
        return

    params.append(local_supplier_id)
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE local_suppliers SET {', '.join(updates)} WHERE local_supplier_id = ?",
        params,
    )
    conn.commit()


# ============ SUPPLIER OVERRIDES CRUD ============

def save_supplier_override(
    conn: sqlite3.Connection,
    supplier_id: str,
    email_override: Optional[str] = None,
    contato_override: Optional[str] = None,
    telefone_override: Optional[str] = None,
    endereco_override: Optional[str] = None,
    cidade_override: Optional[str] = None,
    uf_override: Optional[str] = None,
    obs_override: Optional[str] = None
) -> int:
    """Save or update override for master supplier"""
    if email_override:
        email_override = email_override.strip().lower()
        if '@' not in email_override:
            raise ValueError("Email inválido")

    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.cursor()

    # Check if override exists
    cursor.execute("SELECT id FROM supplier_overrides WHERE supplier_id = ?", (supplier_id,))
    existing = cursor.fetchone()

    if existing:
        # Update existing
        cursor.execute("""
            UPDATE supplier_overrides SET
                email_override = COALESCE(?, email_override),
                contato_override = COALESCE(?, contato_override),
                telefone_override = COALESCE(?, telefone_override),
                endereco_override = COALESCE(?, endereco_override),
                cidade_override = COALESCE(?, cidade_override),
                uf_override = COALESCE(?, uf_override),
                obs_override = COALESCE(?, obs_override),
                updated_at = ?
            WHERE supplier_id = ?
        """, (
            email_override, contato_override, telefone_override,
            endereco_override, cidade_override, uf_override, obs_override,
            updated_at, supplier_id
        ))
        override_id = existing['id']
    else:
        # Insert new
        cursor.execute("""
            INSERT INTO supplier_overrides
            (supplier_id, email_override, contato_override, telefone_override,
             endereco_override, cidade_override, uf_override, obs_override, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            supplier_id, email_override, contato_override, telefone_override,
            endereco_override, cidade_override, uf_override, obs_override, updated_at
        ))
        override_id = cursor.lastrowid

    conn.commit()
    logger.info(f"Saved override for supplier {supplier_id}")
    return override_id


def get_all_overrides(conn: sqlite3.Connection) -> Dict[str, SupplierOverride]:
    """Get all overrides indexed by supplier_id"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM supplier_overrides")

    overrides = {}
    for row in cursor.fetchall():
        override = SupplierOverride(
            id=row['id'],
            supplier_id=row['supplier_id'],
            email_override=row['email_override'],
            contato_override=row['contato_override'],
            telefone_override=row['telefone_override'],
            endereco_override=row['endereco_override'],
            cidade_override=row['cidade_override'],
            uf_override=row['uf_override'],
            obs_override=row['obs_override'],
            updated_at=row['updated_at']
        )
        overrides[row['supplier_id']] = override

    return overrides


def save_supplier_ui_override(
    conn: sqlite3.Connection,
    *,
    supplier_key: str,
    empresa_override: Optional[str] = None,
    contato_override: Optional[str] = None,
    email_override: Optional[str] = None,
    telefone_override: Optional[str] = None,
    produtos_override: Optional[str] = None,
) -> None:
    """Insert/update UI-level override row."""
    key = (supplier_key or "").strip()
    if not key:
        raise ValueError("supplier_key vazio")

    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO supplier_ui_overrides (
            supplier_key,
            empresa_override,
            contato_override,
            email_override,
            telefone_override,
            produtos_override,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(supplier_key) DO UPDATE SET
            empresa_override = COALESCE(excluded.empresa_override, supplier_ui_overrides.empresa_override),
            contato_override = COALESCE(excluded.contato_override, supplier_ui_overrides.contato_override),
            email_override = COALESCE(excluded.email_override, supplier_ui_overrides.email_override),
            telefone_override = COALESCE(excluded.telefone_override, supplier_ui_overrides.telefone_override),
            produtos_override = COALESCE(excluded.produtos_override, supplier_ui_overrides.produtos_override),
            updated_at = excluded.updated_at
        """,
        (
            key,
            empresa_override,
            contato_override,
            email_override,
            telefone_override,
            produtos_override,
            updated_at,
        ),
    )
    conn.commit()


def get_supplier_ui_overrides(conn: sqlite3.Connection) -> Dict[str, Dict[str, str]]:
    """Return UI overrides indexed by supplier key."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM supplier_ui_overrides")
    out: Dict[str, Dict[str, str]] = {}
    for row in cursor.fetchall():
        key = str(row["supplier_key"] or "").strip()
        if not key:
            continue
        out[key] = {
            "empresa_override": row["empresa_override"],
            "contato_override": row["contato_override"],
            "email_override": row["email_override"],
            "telefone_override": row["telefone_override"],
            "produtos_override": row["produtos_override"],
            "updated_at": row["updated_at"],
        }
    return out


def queue_pending_master_sync(
    conn: sqlite3.Connection,
    *,
    sync_key: str,
    payload: dict,
    error_message: str = "",
) -> None:
    key = (sync_key or "").strip()
    if not key:
        raise ValueError("sync_key vazio")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO pending_master_sync (sync_key, payload_json, attempts, last_error, created_at, updated_at)
        VALUES (?, ?, 0, ?, ?, ?)
        ON CONFLICT(sync_key) DO UPDATE SET
            payload_json = excluded.payload_json,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (key, payload_json, (error_message or "").strip(), now, now),
    )
    conn.commit()


def list_pending_master_sync(conn: sqlite3.Connection, *, limit: int = 100) -> List[Dict[str, object]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, sync_key, payload_json, attempts, last_error
        FROM pending_master_sync
        ORDER BY updated_at ASC, id ASC
        LIMIT ?
        """,
        (max(1, int(limit or 100)),),
    )
    out: List[Dict[str, object]] = []
    for row in cursor.fetchall():
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except Exception:
            payload = {}
        out.append(
            {
                "id": int(row["id"]),
                "sync_key": str(row["sync_key"] or ""),
                "payload": payload if isinstance(payload, dict) else {},
                "attempts": int(row["attempts"] or 0),
                "last_error": str(row["last_error"] or ""),
            }
        )
    return out


def mark_pending_master_sync_success(conn: sqlite3.Connection, *, row_id: int) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pending_master_sync WHERE id = ?", (int(row_id),))
    conn.commit()


def mark_pending_master_sync_failure(conn: sqlite3.Connection, *, row_id: int, error_message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE pending_master_sync
        SET attempts = attempts + 1,
            last_error = ?,
            updated_at = ?
        WHERE id = ?
        """,
        ((error_message or "").strip(), now, int(row_id)),
    )
    conn.commit()


def delete_local_supplier(conn: sqlite3.Connection, local_supplier_id: int):
    """Delete local supplier and all its item links"""
    cursor = conn.cursor()

    # Delete items first (foreign key)
    cursor.execute("DELETE FROM local_supplier_items WHERE local_supplier_id = ?", (local_supplier_id,))

    # Delete supplier
    cursor.execute("DELETE FROM local_suppliers WHERE local_supplier_id = ?", (local_supplier_id,))

    conn.commit()
    logger.info(f"Deleted local supplier {local_supplier_id}")
