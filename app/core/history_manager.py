import json
import os
import platform
import glob
from datetime import datetime
from typing import List, Dict
from dataclasses import dataclass, asdict
from .config import AppConfig, ensure_app_data_dir

@dataclass
class HistoryEntry:
    """Registro de um item cotado/comprado"""
    date: str
    product_query: str
    supplier: str
    supplier_email: str
    user: str
    pc_name: str

    # Metadata opcional
    price: float = 0.0
    status: str = "COTADO" # COTADO, COMPRADO
    obs: str = ""

class HistoryManager:
    """
    Gerenciador de Histórico Distribuído 🌍

    Arquitetura:
    - Escrita: Cada PC grava no seu próprio arquivo (history_{pc}.json) para evitar conflitos.
    - Leitura: O sistema lê TODOS os arquivos .json da pasta para montar o histórico global.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.pc_name = platform.node()
        self.user = os.getlogin()

        # Pasta de histórico no NAS (ou local se não tiver NAS)
        # Tenta usar o diretório do arquivo Master como base
        nas_dir = os.path.dirname(self.config.nas_master_path) if self.config.nas_master_path else None

        if nas_dir and os.path.exists(nas_dir):
            self.history_dir = os.path.join(nas_dir, "history_db")
        else:
            self.history_dir = os.path.join(str(ensure_app_data_dir()), "history_db")

        self._ensure_dir()
        self.my_file = os.path.join(self.history_dir, f"history_{self.pc_name}.json")

    def _ensure_dir(self):
        if not os.path.exists(self.history_dir):
            try:
                os.makedirs(self.history_dir)
            except Exception as e:
                print(f"Erro criando pasta de histórico: {e}")

    def add_entry(self, entry: HistoryEntry):
        """Adiciona um novo registro ao histórico deste PC"""
        data = self._load_my_file()
        data.append(asdict(entry))
        self._save_my_file(data)
        print(f"Histórico salvo: {entry.product_query} -> {entry.supplier}")

    def get_global_history(self, query: str = "") -> List[Dict]:
        """Lê histórico de TODOS os computadores"""
        all_entries = []

        # Padronizar query
        query = query.lower().strip()

        # Listar todos os JSONs
        pattern = os.path.join(self.history_dir, "history_*.json")
        files = glob.glob(pattern)

        for f in files:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    entries = json.load(file)
                    # Filtrar se tiver query
                    if query:
                        entries = [
                            e for e in entries
                            if query in e.get('product_query', '').lower()
                            or query in e.get('supplier', '').lower()
                        ]
                    all_entries.extend(entries)
            except Exception as e:
                print(f"Erro lendo histórico {f}: {e}")

        # Ordenar por data (mais recente primeiro)
        # Assumes date format YYYY-MM-DD required for string sort,
        # but our saved format might be generic. We'll ensure sorting works.
        all_entries.sort(key=lambda x: x.get('date', ''), reverse=True)

        return all_entries

    def _load_my_file(self) -> List[Dict]:
        if not os.path.exists(self.my_file):
            return []
        try:
            with open(self.my_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []

    def _save_my_file(self, data: List[Dict]):
        try:
            with open(self.my_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Erro salvando histórico pessoal: {e}")

    # Helpers
    def register_quote(self, product_query: str, suppliers: List[str]):
        """Helper rápido para registrar cotação enviada"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Suppliers pode ser lista de emails ou objetos.
        # Vamos assumir lista de strings (Nome ou Email)
        for supp in suppliers:
             entry = HistoryEntry(
                 date=now,
                 product_query=product_query,
                 supplier=str(supp),
                 supplier_email="", # Preencher se possível
                 user=self.user,
                 pc_name=self.pc_name,
                 status="COTADO"
             )
             self.add_entry(entry)
