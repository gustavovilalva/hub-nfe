"""
Gerenciador do banco de dados SQLite e organização dos XMLs em disco.
"""

import os
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml_parser import parse_nfe_xml


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS notas_fiscais (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chave_acesso    TEXT UNIQUE,
    numero          TEXT,
    serie           TEXT,
    data_emissao    TEXT,
    tipo            TEXT,          -- 'saida' ou 'entrada'
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
    sincronizado_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_chave    ON notas_fiscais(chave_acesso);
CREATE INDEX IF NOT EXISTS idx_numero   ON notas_fiscais(numero);
CREATE INDEX IF NOT EXISTS idx_data     ON notas_fiscais(data_emissao);
CREATE INDEX IF NOT EXISTS idx_cnpj_dest ON notas_fiscais(cnpj_dest);
CREATE INDEX IF NOT EXISTS idx_situacao ON notas_fiscais(situacao);
"""


class HubDatabase:
    def __init__(self, db_path: str, xml_dir: str):
        self.db_path = db_path
        self.xml_dir = Path(xml_dir)
        self.xml_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(DB_SCHEMA)

        self.conn.commit()

    def _xml_file_path(self, data_emissao: str, chave_acesso: str) -> Path:
        """
        Organiza XMLs em: xml_dir/YYYY/MM/chave_acesso.xml
        data_emissao no formato YYYY-MM-DD ou DD/MM/YYYY
        """
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

    def salvar_nota(self, xml_content: str, tiny_id: str = "") -> Optional[str]:
        """
        Recebe o XML bruto de uma NFe, extrai metadados, salva o arquivo
        em disco e indexa no banco. Retorna a chave de acesso ou None em caso de erro.
        """
        meta = parse_nfe_xml(xml_content)
        if not meta:
            return None

        chave = meta["chave_acesso"]
        xml_bytes = xml_content.encode("utf-8")
        xml_hash = hashlib.sha256(xml_bytes).hexdigest()

        # Verifica se já existe e se o conteúdo mudou
        cur = self.conn.execute(
            "SELECT xml_hash, xml_path FROM notas_fiscais WHERE chave_acesso = ?", (chave,)
        )
        row = cur.fetchone()
        if row and row["xml_hash"] == xml_hash:
            return chave  # Já existe e é idêntico

        # Salva arquivo XML em disco
        xml_path = self._xml_file_path(meta["data_emissao"], chave)
        xml_path.write_text(xml_content, encoding="utf-8")

        agora = datetime.now().isoformat(timespec="seconds")
        dados = (
            chave,
            meta.get("numero"),
            meta.get("serie"),
            meta.get("data_emissao"),
            meta.get("tipo", "saida"),
            meta.get("situacao", ""),
            meta.get("valor_total"),
            meta.get("cnpj_emitente"),
            meta.get("nome_emitente"),
            meta.get("cnpj_dest"),
            meta.get("nome_dest"),
            meta.get("municipio_dest"),
            meta.get("uf_dest"),
            meta.get("natureza_op"),
            str(xml_path),
            xml_hash,
            tiny_id,
            agora,
        )

        self.conn.execute(
            """
            INSERT INTO notas_fiscais
                (chave_acesso, numero, serie, data_emissao, tipo, situacao,
                 valor_total, cnpj_emitente, nome_emitente, cnpj_dest, nome_dest,
                 municipio_dest, uf_dest, natureza_op, xml_path, xml_hash,
                 tiny_id, sincronizado_em)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(chave_acesso) DO UPDATE SET
                situacao        = excluded.situacao,
                xml_path        = excluded.xml_path,
                xml_hash        = excluded.xml_hash,
                sincronizado_em = excluded.sincronizado_em
            """,
            dados,
        )
        self.conn.commit()
        return chave

    def ja_existe(self, chave_acesso: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM notas_fiscais WHERE chave_acesso = ?", (chave_acesso,)
        )
        return cur.fetchone() is not None

    def buscar(
        self,
        numero: str = "",
        cnpj_dest: str = "",
        nome_dest: str = "",
        data_inicio: str = "",
        data_fim: str = "",
        situacao: str = "",
        uf: str = "",
        valor_min: float = None,
        valor_max: float = None,
        chave: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        """Pesquisa no banco de dados com múltiplos filtros."""
        where = []
        params = []

        if chave:
            where.append("chave_acesso LIKE ?")
            params.append(f"%{chave}%")
        if numero:
            where.append("numero = ?")
            params.append(numero)
        if cnpj_dest:
            where.append("cnpj_dest LIKE ?")
            params.append(f"%{cnpj_dest}%")
        if nome_dest:
            where.append("nome_dest LIKE ?")
            params.append(f"%{nome_dest}%")
        if data_inicio:
            where.append("data_emissao >= ?")
            params.append(data_inicio)
        if data_fim:
            where.append("data_emissao <= ?")
            params.append(data_fim)
        if situacao:
            where.append("situacao = ?")
            params.append(situacao)
        if uf:
            where.append("uf_dest = ?")
            params.append(uf.upper())
        if valor_min is not None:
            where.append("valor_total >= ?")
            params.append(valor_min)
        if valor_max is not None:
            where.append("valor_total <= ?")
            params.append(valor_max)

        sql = "SELECT * FROM notas_fiscais"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY data_emissao DESC, numero DESC"
        sql += f" LIMIT {limit} OFFSET {offset}"

        cur = self.conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def estatisticas(self) -> dict:
        cur = self.conn.execute(
            """SELECT
                COUNT(*)               AS total,
                SUM(valor_total)       AS valor_total,
                MIN(data_emissao)      AS mais_antiga,
                MAX(data_emissao)      AS mais_recente,
                COUNT(DISTINCT SUBSTR(data_emissao,1,7)) AS meses
               FROM notas_fiscais"""
        )
        row = cur.fetchone()
        return dict(row) if row else {}

    def close(self):
        self.conn.close()
