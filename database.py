"""
Gerenciador do banco de dados SQLite e organização dos XMLs em disco.
"""

import os
import sqlite3
import hashlib
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml_parser import parse_nfe_xml

_db_init_lock = threading.Lock()

DB_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS usuarios (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        email       TEXT UNIQUE NOT NULL,
        nome        TEXT NOT NULL,
        senha_hash  TEXT NOT NULL,
        criado_em   TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS notas_fiscais (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id      INTEGER REFERENCES usuarios(id),
        chave_acesso    TEXT NOT NULL,
        numero          TEXT,
        serie           TEXT,
        data_emissao    TEXT,
        tipo            TEXT,
        situacao        TEXT,
        valor_total     REAL,
        cnpj_emitente   TEXT,
        nome_emitente   TEXT,
        cnpj_dest       TEXT,
        nome_dest       TEXT,
        municipio_dest  TEXT,
        uf_dest         TEXT,
        natureza_op     TEXT,
        xml_path        TEXT,
        xml_hash        TEXT,
        tiny_id         TEXT,
        sincronizado_em TEXT,
        UNIQUE(usuario_id, chave_acesso)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chave     ON notas_fiscais(chave_acesso)",
    "CREATE INDEX IF NOT EXISTS idx_numero    ON notas_fiscais(numero)",
    "CREATE INDEX IF NOT EXISTS idx_data      ON notas_fiscais(data_emissao)",
    "CREATE INDEX IF NOT EXISTS idx_cnpj_dest ON notas_fiscais(cnpj_dest)",
    "CREATE INDEX IF NOT EXISTS idx_situacao  ON notas_fiscais(situacao)",
    "CREATE INDEX IF NOT EXISTS idx_usuario   ON notas_fiscais(usuario_id)",
]


class HubDatabase:
    def __init__(self, db_path: str, xml_dir: str):
        self.db_path = db_path
        self.xml_dir = Path(xml_dir)
        self.xml_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with _db_init_lock:
            for stmt in DB_SCHEMA:
                try:
                    self.conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            # Migration: add usuario_id to existing notas_fiscais table
            try:
                self.conn.execute("ALTER TABLE notas_fiscais ADD COLUMN usuario_id INTEGER REFERENCES usuarios(id)")
            except sqlite3.OperationalError:
                pass
            # Migration: add aliquota to usuarios table
            try:
                self.conn.execute("ALTER TABLE usuarios ADD COLUMN aliquota REAL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            self.conn.commit()

    # ── Usuários ──────────────────────────────────────────────────────────────

    def criar_usuario(self, email: str, nome: str, senha_hash: str) -> Optional[int]:
        try:
            agora = datetime.now().isoformat(timespec="seconds")
            cur = self.conn.execute(
                "INSERT INTO usuarios (email, nome, senha_hash, criado_em) VALUES (?,?,?,?)",
                (email.lower().strip(), nome.strip(), senha_hash, agora),
            )
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def buscar_usuario_por_email(self, email: str) -> Optional[dict]:
        cur = self.conn.execute(
            "SELECT * FROM usuarios WHERE email = ?", (email.lower().strip(),)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def buscar_usuario_por_id(self, user_id: int) -> Optional[dict]:
        cur = self.conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def total_usuarios(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM usuarios")
        return cur.fetchone()[0]

    def atualizar_aliquota(self, user_id: int, aliquota: float) -> None:
        self.conn.execute(
            "UPDATE usuarios SET aliquota = ? WHERE id = ?",
            (aliquota, user_id)
        )
        self.conn.commit()

    def atualizar_senha(self, user_id: int, nova_senha_hash: str) -> None:
        self.conn.execute(
            "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
            (nova_senha_hash, user_id)
        )
        self.conn.commit()

    # ── XMLs ──────────────────────────────────────────────────────────────────

    def _xml_file_path(self, data_emissao: str, chave_acesso: str) -> Path:
        try:
            if "-" in data_emissao:
                dt = datetime.strptime(data_emissao[:10], "%Y-%m-%d")
            else:
                dt = datetime.strptime(data_emissao[:10], "%d/%m/%Y")
        except ValueError:
            dt = datetime.now()
        pasta = self.xml_dir / str(dt.year) / f"{dt.month:02d}"
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta / f"{chave_acesso}.xml"

    def salvar_nota(self, xml_content: str, tiny_id: str = "", usuario_id: int = None) -> Optional[str]:
        meta = parse_nfe_xml(xml_content)
        if not meta:
            return None

        chave = meta["chave_acesso"]
        xml_bytes = xml_content.encode("utf-8")
        xml_hash = hashlib.sha256(xml_bytes).hexdigest()

        cur = self.conn.execute(
            "SELECT xml_hash FROM notas_fiscais WHERE chave_acesso = ? AND usuario_id IS ?",
            (chave, usuario_id),
        )
        row = cur.fetchone()
        if row and row["xml_hash"] == xml_hash:
            return chave

        xml_path = self._xml_file_path(meta["data_emissao"], chave)
        xml_path.write_text(xml_content, encoding="utf-8")

        agora = datetime.now().isoformat(timespec="seconds")
        dados = (
            usuario_id, chave, meta.get("numero"), meta.get("serie"),
            meta.get("data_emissao"), meta.get("tipo", "saida"), meta.get("situacao", ""),
            meta.get("valor_total"), meta.get("cnpj_emitente"), meta.get("nome_emitente"),
            meta.get("cnpj_dest"), meta.get("nome_dest"), meta.get("municipio_dest"),
            meta.get("uf_dest"), meta.get("natureza_op"), str(xml_path), xml_hash,
            tiny_id, agora,
        )

        self.conn.execute(
            """INSERT INTO notas_fiscais
                (usuario_id, chave_acesso, numero, serie, data_emissao, tipo, situacao,
                 valor_total, cnpj_emitente, nome_emitente, cnpj_dest, nome_dest,
                 municipio_dest, uf_dest, natureza_op, xml_path, xml_hash, tiny_id, sincronizado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(usuario_id, chave_acesso) DO UPDATE SET
                 situacao=excluded.situacao, xml_path=excluded.xml_path,
                 xml_hash=excluded.xml_hash, sincronizado_em=excluded.sincronizado_em""",
            dados,
        )
        self.conn.commit()
        return chave

    def ja_existe(self, chave_acesso: str, usuario_id: int = None) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM notas_fiscais WHERE chave_acesso = ? AND usuario_id IS ?",
            (chave_acesso, usuario_id),
        )
        return cur.fetchone() is not None

    def buscar(self, numero="", cnpj_dest="", nome_dest="", data_inicio="",
               data_fim="", situacao="", uf="", valor_min=None, valor_max=None,
               chave="", usuario_id=None, limit=50, offset=0) -> list:
        where, params = [], []

        if usuario_id is not None:
            where.append("usuario_id = ?")
            params.append(usuario_id)

        if chave:
            where.append("chave_acesso LIKE ?"); params.append(f"%{chave}%")
        if numero:
            where.append("numero = ?"); params.append(numero)
        if cnpj_dest:
            where.append("cnpj_dest LIKE ?"); params.append(f"%{cnpj_dest}%")
        if nome_dest:
            where.append("nome_dest LIKE ?"); params.append(f"%{nome_dest}%")
        if data_inicio:
            where.append("data_emissao >= ?"); params.append(data_inicio)
        if data_fim:
            where.append("data_emissao <= ?"); params.append(data_fim)
        if situacao:
            where.append("situacao = ?"); params.append(situacao)
        if uf:
            where.append("uf_dest = ?"); params.append(uf.upper())
        if valor_min is not None:
            where.append("valor_total >= ?"); params.append(valor_min)
        if valor_max is not None:
            where.append("valor_total <= ?"); params.append(valor_max)

        sql = "SELECT * FROM notas_fiscais"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY data_emissao DESC, numero DESC LIMIT {limit} OFFSET {offset}"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def estatisticas(self, usuario_id=None) -> dict:
        where = "WHERE usuario_id = ?" if usuario_id is not None else ""
        params = (usuario_id,) if usuario_id is not None else ()
        cur = self.conn.execute(
            f"""SELECT COUNT(*) AS total, SUM(valor_total) AS valor_total,
                       MIN(data_emissao) AS mais_antiga, MAX(data_emissao) AS mais_recente,
                       COUNT(DISTINCT SUBSTR(data_emissao,1,7)) AS meses
                FROM notas_fiscais {where}""",
            params,
        )
        row = cur.fetchone()
        return dict(row) if row else {}

    def close(self):
        self.conn.close()
