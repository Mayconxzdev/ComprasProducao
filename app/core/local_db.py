"""
Local database overlay for supplier corrections and additions
Allows modifying supplier data without changing master XLSX
"""
import sqlite3
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class SupplierOverride:
    """Override for supplier fields"""
    supplier_id: str
    email: Optional[str] = None
    telefone: Optional[str] = None
    contato: Optional[str] = None
    updated_at: Optional[datetime] = None

@dataclass
class LocalSupplier:
    """Locally added supplier"""
    local_supplier_id: str
    empresa: str
    email: str
    contato: str = ""
    telefone: str = ""
    cidade: str = ""
    uf: str = ""
    created_at: Optional[datetime] = None

class LocalDB:
    """Manages local overlay database"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            # Default to %APPDATA%/ComprasApp/local.db
            import os
            appdata = os.getenv('APPDATA') or os.path.expanduser('~')
            app_dir = Path(appdata) / 'ComprasApp'
            app_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(app_dir / 'local.db')

        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        return conn

    def _init_db(self):
        """Initialize database schema"""
        conn = self._connect()
        cursor = conn.cursor()

        # Supplier overrides table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS supplier_overrides (
                supplier_id TEXT PRIMARY KEY,
                email TEXT,
                telefone TEXT,
                contato TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Local suppliers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS local_suppliers (
                local_supplier_id TEXT PRIMARY KEY,
                empresa TEXT NOT NULL,
                email TEXT NOT NULL,
                contato TEXT,
                telefone TEXT,
                cidade TEXT,
                uf TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Local supplier-item links
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS local_supplier_items (
                local_link_id TEXT PRIMARY KEY,
                local_supplier_id TEXT,
                item_id TEXT,
                serv_corte BOOLEAN DEFAULT 0,
                serv_dobra BOOLEAN DEFAULT 0,
                serv_entrega BOOLEAN DEFAULT 0,
                serv_retira BOOLEAN DEFAULT 0,
                FOREIGN KEY (local_supplier_id) REFERENCES local_suppliers(local_supplier_id)
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_local_suppliers_email ON local_suppliers(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_local_suppliers_empresa ON local_suppliers(empresa)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_local_items_supplier ON local_supplier_items(local_supplier_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_local_items_item ON local_supplier_items(item_id)")

        conn.commit()
        conn.close()
        logger.info(f"Local DB initialized: {self.db_path}")

    def save_override(self, supplier_id: str, email: str = None, telefone: str = None, contato: str = None):
        """Save or update supplier override"""
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO supplier_overrides (supplier_id, email, telefone, contato, updated_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (supplier_id, email, telefone, contato, datetime.now()))

        conn.commit()
        conn.close()
        logger.info(f"Saved override for supplier_id={supplier_id}")

    def get_override(self, supplier_id: str) -> Optional[SupplierOverride]:
        """Get override for supplier"""
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM supplier_overrides WHERE supplier_id = ?', (supplier_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return SupplierOverride(
                supplier_id=row[0],
                email=row[1],
                telefone=row[2],
                contato=row[3],
                updated_at=datetime.fromisoformat(row[4]) if row[4] else None
            )
        return None

    def get_all_overrides(self) -> Dict[str, SupplierOverride]:
        """Get all overrides"""
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM supplier_overrides')
        rows = cursor.fetchall()
        conn.close()

        return {
            row[0]: SupplierOverride(
                supplier_id=row[0],
                email=row[1],
                telefone=row[2],
                contato=row[3],
                updated_at=datetime.fromisoformat(row[4]) if row[4] else None
            )
            for row in rows
        }

    def add_local_supplier(self, empresa: str, email: str, contato: str = "", telefone: str = "",
                          cidade: str = "", uf: str = "") -> str:
        """Add new local supplier, returns local_supplier_id"""
        import uuid
        local_id = f"LOCAL_{uuid.uuid4().hex[:8].upper()}"

        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO local_suppliers (local_supplier_id, empresa, email, contato, telefone, cidade, uf, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (local_id, empresa, email, contato, telefone, cidade, uf, datetime.now()))

        conn.commit()
        conn.close()
        logger.info(f"Added local supplier: {local_id} - {empresa}")
        return local_id

    def get_all_local_suppliers(self) -> List[LocalSupplier]:
        """Get all locally added suppliers"""
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM local_suppliers')
        rows = cursor.fetchall()
        conn.close()

        return [
            LocalSupplier(
                local_supplier_id=row[0],
                empresa=row[1],
                email=row[2],
                contato=row[3] or "",
                telefone=row[4] or "",
                cidade=row[5] or "",
                uf=row[6] or "",
                created_at=datetime.fromisoformat(row[7]) if row[7] else None
            )
            for row in rows
        ]

    def add_local_supplier_item(self, local_supplier_id: str, item_id: str,
                               serv_corte: bool = False, serv_dobra: bool = False,
                               serv_entrega: bool = False, serv_retira: bool = False):
        """Link local supplier with item"""
        import uuid
        link_id = f"LINK_{uuid.uuid4().hex[:8].upper()}"

        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO local_supplier_items
            (local_link_id, local_supplier_id, item_id, serv_corte, serv_dobra, serv_entrega, serv_retira)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (link_id, local_supplier_id, item_id, serv_corte, serv_dobra, serv_entrega, serv_retira))

        conn.commit()
        conn.close()

# Global instance
_local_db = None

def get_local_db() -> LocalDB:
    """Get global LocalDB instance"""
    global _local_db
    if _local_db is None:
        _local_db = LocalDB()
    return _local_db
