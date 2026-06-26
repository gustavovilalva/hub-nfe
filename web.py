#!/usr/bin/env python3
"""
Hub NFe — Interface Web com autenticação multi-usuário
Execute: python web.py
Acesse:  http://localhost:5000
"""

import os
import zipfile
import tempfile
import threading
from pathlib import Path

from flask import (Flask, render_template, request, jsonify,
                   send_file, abort, redirect, url_for, flash)
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from database import HubDatabase
from xml_parser import parse_nfe_xml

load_dotenv()

app = Flask(__name__, template_folder=".")
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))

UPLOAD_MAX_MB = int(os.getenv("UPLOAD_MAX_MB", "200"))
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_MAX_MB * 1024 * 1024

# ─── Flask-Login ──────────────────────────────────────────────────────────────

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para acessar esta página."
login_manager.login_message_category = "warning"


class User(UserMixin):
    def __init__(self, id, email, nome):
        self.id = id
        self.email = email
        self.nome = nome


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    u = db.buscar_usuario_por_id(int(user_id))
    db.close()
    if u:
        return User(u["id"], u["email"], u["nome"])
    return None


# ─── Banco de dados ───────────────────────────────────────────────────────────

def get_db():
    xml_dir = os.getenv("XML_DIR", "./xmls")
    db_path = os.getenv("DB_PATH", "./hub_nfe.db")
    return HubDatabase(db_path, xml_dir)


# ─── Estado do processamento de upload ───────────────────────────────────────

# Mapeia usuario_id → estado do upload
_upload_states: dict = {}
_upload_lock = threading.Lock()


def _get_state(usuario_id: int) -> dict:
    with _upload_lock:
        if usuario_id not in _upload_states:
            _upload_states[usuario_id] = {
                "rodando": False, "arquivo": "", "processados": 0,
                "total": 0, "importados": 0, "ignorados": 0,
                "erros": 0, "log": [], "concluido": False,
            }
        return _upload_states[usuario_id]


def _processar_zip(zip_path: str, xml_dir: str, db_path: str, usuario_id: int):
    """Roda em thread separada: extrai XMLs do ZIP e indexa no banco."""
    state = _get_state(usuario_id)
    state.update({
        "rodando": True, "processados": 0, "total": 0,
        "importados": 0, "ignorados": 0, "erros": 0,
        "log": [], "concluido": False,
    })

    def log(msg, tipo="info"):
        state["log"].append({"msg": msg, "tipo": tipo})

    try:
        import supabase_storage as ss

        with zipfile.ZipFile(zip_path, "r") as zf:
            xml_files = [n for n in zf.namelist()
                         if n.lower().endswith(".xml") and not n.startswith("__MACOSX")]
            state["total"] = len(xml_files)
            log(f"{len(xml_files)} arquivo(s) XML encontrado(s) no ZIP.")

            if not xml_files:
                log("Nenhum XML encontrado no arquivo ZIP.", "warn")
                return

            db = HubDatabase(db_path, xml_dir)

            for nome in xml_files:
                state["processados"] += 1
                try:
                    conteudo = zf.read(nome).decode("utf-8", errors="replace")

                    meta = parse_nfe_xml(conteudo)
                    if not meta:
                        log(f"⚠ {Path(nome).name}: não é uma NFe válida — ignorado", "warn")
                        state["ignorados"] += 1
                        continue

                    chave = meta["chave_acesso"]

                    if db.ja_existe(chave, usuario_id):
                        state["ignorados"] += 1
                        continue

                    resultado = db.salvar_nota(conteudo, usuario_id=usuario_id)
                    if resultado:
                        state["importados"] += 1
                        log(f"✓ NF {meta['numero']} — {meta['nome_dest'] or 'sem destinatário'} "
                            f"— R$ {meta['valor_total']:,.2f}", "success")

                        # Upload para Supabase Storage
                        ss.upload_xml(conteudo, chave, meta.get("data_emissao", ""), usuario_id)
                    else:
                        state["erros"] += 1
                        log(f"✗ {Path(nome).name}: erro ao salvar", "error")

                except Exception as e:
                    state["erros"] += 1
                    log(f"✗ {Path(nome).name}: {e}", "error")

            db.close()

        imp = state["importados"]
        ign = state["ignorados"]
        err = state["erros"]
        log(f"Concluído: {imp} importadas, {ign} já existiam/inválidas, {err} erros.", "success")

    except zipfile.BadZipFile:
        log("O arquivo enviado não é um ZIP válido.", "error")
    except Exception as e:
        log(f"Erro inesperado: {e}", "error")
    finally:
        try:
            os.unlink(zip_path)
        except Exception:
            pass
        state["rodando"] = False
        state["concluido"] = True


# ─── Autenticação ─────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        db = get_db()
        u = db.buscar_usuario_por_email(email)
        db.close()
        if u and check_password_hash(u["senha_hash"], senha):
            user = User(u["id"], u["email"], u["nome"])
            login_user(user, remember=True)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))
        flash("E-mail ou senha incorretos.", "danger")
    return render_template("login.html")


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        senha2 = request.form.get("senha2", "")

        if not nome or not email or not senha:
            flash("Preencha todos os campos.", "danger")
        elif senha != senha2:
            flash("As senhas não coincidem.", "danger")
        elif len(senha) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
        else:
            db = get_db()
            primeiro = db.total_usuarios() == 0
            user_id = db.criar_usuario(email, nome, generate_password_hash(senha))
            if user_id is None:
                db.close()
                flash("Este e-mail já está cadastrado.", "danger")
            else:
                # Primeiro usuário herda as notas sem dono (legacy)
                if primeiro:
                    db.conn.execute(
                        "UPDATE notas_fiscais SET usuario_id = ? WHERE usuario_id IS NULL",
                        (user_id,)
                    )
                    db.conn.commit()
                db.close()
                user = User(user_id, email, nome)
                login_user(user, remember=True)
                flash(f"Bem-vindo, {nome}! Conta criada com sucesso.", "success")
                return redirect(url_for("dashboard"))
    return render_template("cadastro.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ─── Rotas principais ─────────────────────────────────────────────────────────

@app.route("/alterar-senha", methods=["GET", "POST"])
@login_required
def alterar_senha():
    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        nova_senha  = request.form.get("nova_senha", "")
        confirma    = request.form.get("confirma", "")

        db = get_db()
        u  = db.buscar_usuario_por_id(current_user.id)

        if not check_password_hash(u["senha_hash"], senha_atual):
            db.close()
            flash("Senha atual incorreta.", "danger")
        elif len(nova_senha) < 6:
            db.close()
            flash("A nova senha deve ter pelo menos 6 caracteres.", "danger")
        elif nova_senha != confirma:
            db.close()
            flash("As senhas não coincidem.", "danger")
        else:
            db.atualizar_senha(current_user.id, generate_password_hash(nova_senha))
            db.close()
            flash("Senha alterada com sucesso!", "success")
            return redirect(url_for("dashboard"))

    return render_template("alterar_senha.html")


@app.route("/aliquota", methods=["POST"])
@login_required
def salvar_aliquota():
    try:
        aliquota = float(request.form.get("aliquota", 0))
        aliquota = max(0.0, min(100.0, aliquota))
    except (ValueError, TypeError):
        aliquota = 0.0
    db = get_db()
    db.atualizar_aliquota(current_user.id, aliquota)
    db.close()
    flash(f"Alíquota atualizada para {aliquota:.2f}%.", "success")
    return redirect(url_for("dashboard"))


@app.route("/")
@login_required
def dashboard():
    uid = current_user.id
    db = get_db()
    stats = db.estatisticas(uid)
    ultimas = db.buscar(limit=8, usuario_id=uid)

    # Alíquota do usuário
    u = db.buscar_usuario_por_id(uid)
    aliquota = float(u.get("aliquota") or 0) if u else 0.0

    cur = db.conn.execute(
        "SELECT situacao, COUNT(*) as qtd, SUM(valor_total) as total "
        "FROM notas_fiscais WHERE usuario_id = ? GROUP BY situacao ORDER BY qtd DESC",
        (uid,)
    )
    por_situacao = [dict(r) for r in cur.fetchall()]

    cur = db.conn.execute(
        """SELECT SUBSTR(data_emissao,1,7) as mes,
                  COUNT(*) as qtd, SUM(valor_total) as total
           FROM notas_fiscais
           WHERE usuario_id = ? AND data_emissao >= DATE('now','-12 months')
           GROUP BY mes ORDER BY mes""",
        (uid,)
    )
    por_mes = [dict(r) for r in cur.fetchall()]

    cur = db.conn.execute(
        """SELECT nome_dest, COUNT(*) as qtd, SUM(valor_total) as total
           FROM notas_fiscais WHERE usuario_id = ? AND nome_dest != ''
           GROUP BY nome_dest ORDER BY total DESC LIMIT 5""",
        (uid,)
    )
    top_dest = [dict(r) for r in cur.fetchall()]

    # Valor do mês atual para cálculo de imposto
    cur = db.conn.execute(
        """SELECT COALESCE(SUM(valor_total), 0) as total
           FROM notas_fiscais
           WHERE usuario_id = ? AND SUBSTR(data_emissao,1,7) = STRFTIME('%Y-%m', 'now')""",
        (uid,)
    )
    valor_mes_atual = cur.fetchone()["total"] or 0.0

    # Valor do ano atual
    cur = db.conn.execute(
        """SELECT COALESCE(SUM(valor_total), 0) as total
           FROM notas_fiscais
           WHERE usuario_id = ? AND SUBSTR(data_emissao,1,4) = STRFTIME('%Y', 'now')""",
        (uid,)
    )
    valor_ano_atual = cur.fetchone()["total"] or 0.0

    # Todos os meses disponíveis para o seletor de imposto
    cur = db.conn.execute(
        """SELECT SUBSTR(data_emissao,1,7) as mes, COALESCE(SUM(valor_total), 0) as total
           FROM notas_fiscais
           WHERE usuario_id = ? AND data_emissao IS NOT NULL AND data_emissao != ''
           GROUP BY mes ORDER BY mes DESC""",
        (uid,)
    )
    meses_imposto = [dict(r) for r in cur.fetchall()]

    db.close()

    return render_template("dashboard.html",
        stats=stats, ultimas=ultimas,
        por_situacao=por_situacao, por_mes=por_mes, top_dest=top_dest,
        aliquota=aliquota,
        valor_mes_atual=valor_mes_atual,
        valor_ano_atual=valor_ano_atual,
        meses_imposto=meses_imposto,
    )


@app.route("/buscar")
@login_required
def buscar():
    uid = current_user.id
    args = request.args
    db = get_db()
    resultados = []
    total = 0

    if any(v for v in args.values() if v):
        pagina = int(args.get("pagina", 1))
        offset = (pagina - 1) * 50
        filtros = dict(
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
            usuario_id=uid,
        )
        resultados = db.buscar(**filtros, limit=50, offset=offset)
        total = len(db.buscar(**filtros, limit=9999))
    db.close()

    pagina = int(args.get("pagina", 1))
    total_paginas = max(1, (total + 49) // 50)
    return render_template("buscar.html",
        resultados=resultados, args=args,
        total=total, pagina=pagina, total_paginas=total_paginas,
    )


@app.route("/nota/<chave>")
@login_required
def detalhe_nota(chave):
    uid = current_user.id
    db = get_db()
    resultados = db.buscar(chave=chave, usuario_id=uid, limit=1)
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
    return render_template("nota.html", nota=nota, xml_content=xml_content)


@app.route("/nota/<chave>/download")
@login_required
def download_xml_route(chave):
    uid = current_user.id
    db = get_db()
    resultados = db.buscar(chave=chave, usuario_id=uid, limit=1)
    db.close()
    if not resultados:
        abort(404)
    nota = resultados[0]
    xml_path = nota.get("xml_path")

    # Tenta arquivo local primeiro, depois Supabase Storage
    if xml_path and Path(xml_path).exists():
        numero = nota.get("numero", chave[:8])
        filename = f"NFe_{numero}_{chave[:8]}.xml"
        return send_file(xml_path, as_attachment=True, download_name=filename, mimetype="application/xml")

    # Fallback: busca no Supabase Storage
    try:
        import supabase_storage as ss
        if ss.configurado():
            from supabase_storage import _caminho
            path = _caminho(chave, nota.get("data_emissao", ""), uid)
            xml = ss.download_xml(path)
            if xml:
                import io
                numero = nota.get("numero", chave[:8])
                filename = f"NFe_{numero}_{chave[:8]}.xml"
                return send_file(
                    io.BytesIO(xml.encode("utf-8")),
                    as_attachment=True,
                    download_name=filename,
                    mimetype="application/xml",
                )
    except Exception:
        pass

    abort(404)


# ─── Upload de ZIP ────────────────────────────────────────────────────────────

@app.route("/importar", methods=["GET", "POST"])
@login_required
def importar():
    uid = current_user.id
    state = _get_state(uid)

    if request.method == "POST":
        if state["rodando"]:
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
        state["arquivo"] = arquivo.filename

        t = threading.Thread(
            target=_processar_zip,
            args=(tmp.name, xml_dir, db_path, uid),
            daemon=True,
        )
        t.start()
        return jsonify({"ok": True})

    return render_template("importar.html", max_mb=UPLOAD_MAX_MB)


@app.route("/importar/status")
@login_required
def importar_status():
    return jsonify(_get_state(current_user.id))


# ─── API JSON ─────────────────────────────────────────────────────────────────

@app.route("/api/notas")
@login_required
def api_notas():
    uid = current_user.id
    db = get_db()
    resultados = db.buscar(
        numero=request.args.get("numero", ""),
        nome_dest=request.args.get("nome", ""),
        cnpj_dest=request.args.get("cnpj", ""),
        data_inicio=request.args.get("data_inicio", ""),
        data_fim=request.args.get("data_fim", ""),
        usuario_id=uid,
        limit=int(request.args.get("limit", 50)),
    )
    db.close()
    for r in resultados:
        r.pop("xml_path", None)
        r.pop("xml_hash", None)
    return jsonify(resultados)


@app.route("/api/stats")
@login_required
def api_stats():
    db = get_db()
    stats = db.estatisticas(current_user.id)
    db.close()
    return jsonify(stats)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print()
    print("=" * 50)
    print(f"  Hub NFe — Interface Web")
    print(f"  Acesse: http://localhost:{port}")
    print("=" * 50)
    print()
    app.run(debug=False, host="0.0.0.0", port=port)
