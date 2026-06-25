#!/usr/bin/env python3
"""
Hub NFe — Interface Web
Execute: python web.py
Acesse:  http://localhost:8080
"""

import os
import zipfile
import tempfile
import threading
import logging
from pathlib import Path

from flask import (Flask, render_template, request, jsonify,
                   send_file, abort, url_for, flash)
from dotenv import load_dotenv

from database import HubDatabase
from xml_parser import parse_nfe_xml
import supabase_storage as storage

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

UPLOAD_MAX_MB = int(os.getenv("UPLOAD_MAX_MB", "200"))
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_MAX_MB * 1024 * 1024


def get_db():
    xml_dir = os.getenv("XML_DIR", "./xmls")
    db_path = os.getenv("DB_PATH", "./hub_nfe.db")
    return HubDatabase(db_path, xml_dir)


# ─── Re-indexação automática no startup ──────────────────────────────────────

def _reindexar_se_necessario():
    """
    Se o banco estiver vazio e o Drive estiver configurado,
    baixa todos os XMLs do Drive e reconstrói o índice.
    Roda em thread separada para não travar o startup.
    """
    try:
        db = get_db()
        stats = db.estatisticas()
        total = stats.get("total", 0)
        db.close()

        if total == 0 and storage.configurado():
            logger.info("Banco vazio — iniciando re-indexação do Google Drive...")
            db = get_db()
            resultado = storage.reindexar_do_storage(db)
            db.close()
            logger.info(
                f"Re-indexação concluída: {resultado['importados']} importadas, "
                f"{resultado['ignorados']} ignoradas, {resultado['erros']} erros."
            )
    except Exception as e:
        logger.warning(f"Re-indexação falhou: {e}")


threading.Thread(target=_reindexar_se_necessario, daemon=True).start()


# ─── Estado do processamento de upload ───────────────────────────────────────

upload_state = {
    "rodando": False,
    "arquivo": "",
    "processados": 0,
    "total": 0,
    "importados": 0,
    "ignorados": 0,
    "erros": 0,
    "drive_uploads": 0,
    "log": [],
    "concluido": False,
    "drive_ativo": False,
}


def _processar_zip(zip_path: str, xml_dir: str, db_path: str):
    """Roda em thread: extrai XMLs do ZIP, indexa no banco e faz upload para Drive."""
    global upload_state
    drive_ativo = storage.configurado()
    upload_state.update({
        "rodando": True, "processados": 0, "total": 0,
        "importados": 0, "ignorados": 0, "erros": 0, "drive_uploads": 0,
        "log": [], "concluido": False, "drive_ativo": drive_ativo,
    })

    def log(msg, tipo="info"):
        upload_state["log"].append({"msg": msg, "tipo": tipo})

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            xml_files = [n for n in zf.namelist()
                         if n.lower().endswith(".xml") and not n.startswith("__MACOSX")]
            upload_state["total"] = len(xml_files)
            log(f"{len(xml_files)} arquivo(s) XML encontrado(s) no ZIP.")
            if drive_ativo:
                log("Google Drive configurado — XMLs serão enviados para o Drive.", "success")

            if not xml_files:
                log("Nenhum XML encontrado no arquivo ZIP.", "warn")
                return

            db = HubDatabase(db_path, xml_dir)

            for nome in xml_files:
                upload_state["processados"] += 1
                try:
                    conteudo = zf.read(nome).decode("utf-8", errors="replace")
                    meta = parse_nfe_xml(conteudo)

                    if not meta:
                        log(f"⚠ {Path(nome).name}: não é uma NFe válida — ignorado", "warn")
                        upload_state["ignorados"] += 1
                        continue

                    chave = meta["chave_acesso"]

                    if db.ja_existe(chave):
                        # Mesmo existindo no banco, garante upload no Drive
                        if drive_ativo:
                            storage.upload_xml(conteudo, chave, meta["data_emissao"])
                        upload_state["ignorados"] += 1
                        continue

                    # Salva no banco local
                    resultado = db.salvar_nota(conteudo)
                    if resultado:
                        upload_state["importados"] += 1

                        # Upload para Supabase Storage
                        if drive_ativo:
                            ok = storage.upload_xml(
                                conteudo, chave, meta["data_emissao"]
                            )
                            if ok:
                                upload_state["drive_uploads"] += 1

                        log(
                            f"✓ NF {meta['numero']} — "
                            f"{(meta['nome_dest'] or 'sem destinatário')[:30]} "
                            f"— R$ {meta['valor_total']:,.2f}",
                            "success",
                        )
                    else:
                        upload_state["erros"] += 1
                        log(f"✗ {Path(nome).name}: erro ao salvar", "error")

                except Exception as e:
                    upload_state["erros"] += 1
                    log(f"✗ {Path(nome).name}: {e}", "error")

            db.close()

        imp = upload_state["importados"]
        ign = upload_state["ignorados"]
        err = upload_state["erros"]
        drv = upload_state["drive_uploads"]
        resumo = f"Concluído: {imp} importadas, {ign} já existiam, {err} erros."
        if drive_ativo:
            resumo += f" {drv} enviadas ao Drive."
        log(resumo, "success")

    except zipfile.BadZipFile:
        log("O arquivo enviado não é um ZIP válido.", "error")
    except Exception as e:
        log(f"Erro inesperado: {e}", "error")
    finally:
        try:
            os.unlink(zip_path)
        except Exception:
            pass
        upload_state["rodando"] = False
        upload_state["concluido"] = True


# ─── Rotas ───────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    db = get_db()
    stats = db.estatisticas()
    ultimas = db.buscar(limit=8)

    cur = db.conn.execute(
        "SELECT situacao, COUNT(*) as qtd, SUM(valor_total) as total "
        "FROM notas_fiscais GROUP BY situacao ORDER BY qtd DESC"
    )
    por_situacao = [dict(r) for r in cur.fetchall()]

    cur = db.conn.execute(
        """SELECT SUBSTR(data_emissao,1,7) as mes,
                  COUNT(*) as qtd, SUM(valor_total) as total
           FROM notas_fiscais
           WHERE data_emissao >= DATE('now','-12 months')
           GROUP BY mes ORDER BY mes"""
    )
    por_mes = [dict(r) for r in cur.fetchall()]

    cur = db.conn.execute(
        """SELECT nome_dest, COUNT(*) as qtd, SUM(valor_total) as total
           FROM notas_fiscais WHERE nome_dest != ''
           GROUP BY nome_dest ORDER BY total DESC LIMIT 5"""
    )
    top_dest = [dict(r) for r in cur.fetchall()]
    db.close()

    drive_ok = storage.configurado()
    return render_template("dashboard.html",
        stats=stats, ultimas=ultimas,
        por_situacao=por_situacao, por_mes=por_mes, top_dest=top_dest,
        drive_ok=drive_ok,
    )


@app.route("/buscar")
def buscar():
    args = request.args
    db = get_db()
    resultados = []
    total = 0

    if any(v for v in args.values() if v):
        pagina = int(args.get("pagina", 1))
        offset = (pagina - 1) * 50
        resultados = db.buscar(
            numero=args.get("numero", ""),
            chave=args.get("chave", ""),
            cnpj_dest=args.get("cnpj", ""),
            nome_dest=args.get("nome", ""),
            data_inicio=args.get("data_inicio", ""),
            data_fim=args.get("data_fim", ""),
            uf=args.get("uf", ""),
            situacao=args.get("situacao", ""),
            valor_min=float(args["valor_min"]) if args.get("valor_min") else None,
            valor_max=float(args["valor_max"]) if args.get("valor_max") else None,
            limit=50, offset=offset,
        )
        total_rows = db.buscar(
            numero=args.get("numero", ""),
            chave=args.get("chave", ""),
            cnpj_dest=args.get("cnpj", ""),
            nome_dest=args.get("nome", ""),
            data_inicio=args.get("data_inicio", ""),
            data_fim=args.get("data_fim", ""),
            uf=args.get("uf", ""),
            situacao=args.get("situacao", ""),
            valor_min=float(args["valor_min"]) if args.get("valor_min") else None,
            valor_max=float(args["valor_max"]) if args.get("valor_max") else None,
            limit=9999,
        )
        total = len(total_rows)
    db.close()

    pagina = int(args.get("pagina", 1))
    total_paginas = max(1, (total + 49) // 50)
    return render_template("buscar.html",
        resultados=resultados, args=args,
        total=total, pagina=pagina, total_paginas=total_paginas,
    )


@app.route("/nota/<chave>")
def detalhe_nota(chave):
    db = get_db()
    resultados = db.buscar(chave=chave, limit=1)
    db.close()
    if not resultados:
        abort(404)
    nota = resultados[0]
    xml_content = None
    xml_path = nota.get("xml_path")
    if xml_path and Path(xml_path).exists():
        try:
            xml_content = Path(xml_path).read_text(encoding="utf-8")
        except Exception:
            pass
    # Se não tem localmente, tenta buscar do Drive
    if not xml_content and storage.configurado():
        arquivos = storage.listar_xmls()
        for arq in arquivos:
            if arq.get("name", "").replace(".xml", "") == chave:
                xml_content = storage.download_xml(arq["path"])
                break
    return render_template("nota.html", nota=nota, xml_content=xml_content)


@app.route("/nota/<chave>/download")
def download_xml_route(chave):
    db = get_db()
    resultados = db.buscar(chave=chave, limit=1)
    db.close()
    if not resultados:
        abort(404)
    nota = resultados[0]
    xml_path = nota.get("xml_path")
    numero = nota.get("numero", chave[:8])
    filename = f"NFe_{numero}_{chave[:8]}.xml"

    # Tenta arquivo local primeiro
    if xml_path and Path(xml_path).exists():
        return send_file(xml_path, as_attachment=True,
                         download_name=filename, mimetype="application/xml")

    # Fallback: busca do Drive
    if storage.configurado():
        arquivos = storage.listar_xmls()
        for arq in arquivos:
            if arq.get("name", "").replace(".xml", "") == chave:
                xml_content = storage.download_xml(arq["path"])
                if xml_content:
                    import io
                    buf = io.BytesIO(xml_content.encode("utf-8"))
                    return send_file(buf, as_attachment=True,
                                     download_name=filename, mimetype="application/xml")

    abort(404)


# ─── Upload de ZIP ────────────────────────────────────────────────────────────

@app.route("/importar", methods=["GET", "POST"])
def importar():
    if request.method == "POST":
        if upload_state["rodando"]:
            return jsonify({"erro": "Já há uma importação em andamento."}), 409

        arquivo = request.files.get("arquivo")
        if not arquivo or not arquivo.filename:
            return jsonify({"erro": "Nenhum arquivo enviado."}), 400
        if not arquivo.filename.lower().endswith(".zip"):
            return jsonify({"erro": "Envie um arquivo .zip contendo os XMLs."}), 400

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        arquivo.save(tmp.name)
        tmp.close()

        xml_dir = os.getenv("XML_DIR", "./xmls")
        db_path = os.getenv("DB_PATH", "./hub_nfe.db")
        upload_state["arquivo"] = arquivo.filename

        t = threading.Thread(
            target=_processar_zip,
            args=(tmp.name, xml_dir, db_path),
            daemon=True,
        )
        t.start()
        return jsonify({"ok": True})

    drive_ok = storage.configurado()
    return render_template("importar.html", max_mb=UPLOAD_MAX_MB, drive_ok=drive_ok)


@app.route("/importar/status")
def importar_status():
    return jsonify(upload_state)


# ─── API JSON ─────────────────────────────────────────────────────────────────

@app.route("/api/notas")
def api_notas():
    db = get_db()
    resultados = db.buscar(
        numero=request.args.get("numero", ""),
        nome_dest=request.args.get("nome", ""),
        cnpj_dest=request.args.get("cnpj", ""),
        data_inicio=request.args.get("data_inicio", ""),
        data_fim=request.args.get("data_fim", ""),
        limit=int(request.args.get("limit", 50)),
    )
    db.close()
    for r in resultados:
        r.pop("xml_path", None)
        r.pop("xml_hash", None)
    return jsonify(resultados)


@app.route("/api/stats")
def api_stats():
    db = get_db()
    stats = db.estatisticas()
    db.close()
    return jsonify(stats)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print()
    print("=" * 50)
    print("  Hub NFe — Interface Web")
    print(f"  Acesse: http://localhost:{port}")
    print(f"  Drive:  {'Ativo' if storage.configurado() else 'Não configurado'}")
    print("=" * 50)
    print()
    app.run(debug=False, host="0.0.0.0", port=port)
