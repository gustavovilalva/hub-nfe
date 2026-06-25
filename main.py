#!/usr/bin/env python3
"""
Hub NFe — Central de XMLs de Notas Fiscais
Sincroniza, armazena e pesquisa NFes do Tiny ERP.
"""

import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from tiny_api import TinyAPI, TinyAPIError
from database import HubDatabase

load_dotenv()


def get_config():
    token  = os.getenv("TINY_TOKEN", "")
    xml_dir = os.getenv("XML_DIR", "./xmls")
    db_path = os.getenv("DB_PATH", "./hub_nfe.db")
    return token, xml_dir, db_path


def linha(char="─", width=60):
    click.echo(char * width)


# ─────────────────────────────────────────────
# CLI principal
# ─────────────────────────────────────────────

@click.group()
def cli():
    """Hub NFe — Central de XMLs de Notas Fiscais do Tiny ERP."""
    pass


# ─────────────────────────────────────────────
# Comando: sincronizar
# ─────────────────────────────────────────────

@cli.command("sincronizar")
@click.option("--data-inicio", "-di", default="", help="Data inicial DD/MM/YYYY")
@click.option("--data-fim",    "-df", default="", help="Data final DD/MM/YYYY")
@click.option("--situacao",    "-s",  default="5", show_default=True,
              help="Situação (5=Autorizada, 3=Cancelada, vazio=todas)")
@click.option("--reprocessar", is_flag=True, default=False,
              help="Re-baixa XMLs já existentes no banco")
def sincronizar(data_inicio, data_fim, situacao, reprocessar):
    """Sincroniza NFes do Tiny ERP para o hub local."""
    token, xml_dir, db_path = get_config()
    if not token:
        click.secho("Erro: TINY_TOKEN não configurado. Copie .env.example para .env e preencha.", fg="red")
        sys.exit(1)

    api = TinyAPI(token)
    db  = HubDatabase(db_path, xml_dir)

    linha()
    click.secho(" Sincronizando NFes do Tiny ERP", bold=True)
    linha()
    if data_inicio or data_fim:
        click.echo(f"  Período : {data_inicio or '(início)'} até {data_fim or '(hoje)'}")
    click.echo(f"  Situação: {situacao or 'todas'}")
    click.echo()

    click.echo("  Carregando lista de notas do Tiny...")
    notas = list(api.gerar_todas_notas(
        situacao=situacao,
        data_inicial=data_inicio,
        data_final=data_fim,
    ))
    click.secho(f"  {len(notas)} notas encontradas.", fg="green")

    if not notas:
        click.secho("Nenhuma nota encontrada com os filtros informados.", fg="yellow")
        db.close()
        return

    novas = atualizadas = sem_xml = erros = 0
    total = len(notas)

    for i, nota in enumerate(notas, 1):
        tiny_id = str(nota.get("id", ""))
        numero  = nota.get("numero", "?")
        chave   = nota.get("chave_acesso", "")

        prefix = f"  [{i:>4}/{total}] NF {numero:<8}"

        if chave and db.ja_existe(chave) and not reprocessar:
            if i % 50 == 0:
                click.echo(f"{prefix} já existe — pulando...")
            continue

        try:
            xml = api.obter_xml(tiny_id)
            if not xml:
                click.echo(f"{prefix} sem XML disponível")
                sem_xml += 1
                continue

            resultado = db.salvar_nota(xml, tiny_id=tiny_id)
            if resultado:
                click.secho(f"{prefix} OK", fg="green")
                novas += 1
            else:
                click.secho(f"{prefix} erro ao salvar", fg="red")
                erros += 1
        except TinyAPIError as e:
            click.secho(f"{prefix} ERRO API: {e}", fg="red")
            erros += 1
        except Exception as e:
            click.secho(f"{prefix} ERRO: {e}", fg="red")
            erros += 1

    click.echo()
    linha()
    click.secho(" Sincronização concluída!", bold=True)
    linha()
    click.secho(f"  Novas/atualizadas : {novas}", fg="green")
    click.secho(f"  Sem XML disponível: {sem_xml}", fg="yellow")
    if erros:
        click.secho(f"  Erros             : {erros}", fg="red")
    db.close()


# ─────────────────────────────────────────────
# Comando: buscar
# ─────────────────────────────────────────────

@cli.command("buscar")
@click.option("--numero",      "-n",  default="", help="Número da nota")
@click.option("--chave",       "-c",  default="", help="Chave de acesso (parcial)")
@click.option("--cnpj",        "-j",  default="", help="CNPJ/CPF do destinatário (parcial)")
@click.option("--nome",        "-m",  default="", help="Nome do destinatário (parcial)")
@click.option("--data-inicio", "-di", default="", help="Data inicial YYYY-MM-DD")
@click.option("--data-fim",    "-df", default="", help="Data final YYYY-MM-DD")
@click.option("--uf",                 default="", help="UF do destinatário (ex: SP)")
@click.option("--situacao",    "-s",  default="", help="Situação (Autorizada, Cancelada...)")
@click.option("--valor-min",          default=None, type=float, help="Valor mínimo (R$)")
@click.option("--valor-max",          default=None, type=float, help="Valor máximo (R$)")
@click.option("--limite",      "-l",  default=25, show_default=True, help="Máximo de resultados")
@click.option("--xml",                is_flag=True, default=False, help="Mostra o caminho do XML")
def buscar(numero, chave, cnpj, nome, data_inicio, data_fim, uf, situacao,
           valor_min, valor_max, limite, xml):
    """Pesquisa notas fiscais no banco local."""
    _, xml_dir, db_path = get_config()
    db = HubDatabase(db_path, xml_dir)

    resultados = db.buscar(
        numero=numero, chave=chave, cnpj_dest=cnpj, nome_dest=nome,
        data_inicio=data_inicio, data_fim=data_fim, uf=uf, situacao=situacao,
        valor_min=valor_min, valor_max=valor_max, limit=limite,
    )
    db.close()

    if not resultados:
        click.secho("Nenhuma nota encontrada com os filtros informados.", fg="yellow")
        return

    linha()
    click.secho(f" {len(resultados)} resultado(s) encontrado(s)", bold=True)
    linha()

    header = f"{'Nº':<8} {'Série':<5} {'Data':<12} {'Destinatário':<35} {'UF':<4} {'Valor (R$)':>14} {'Situação'}"
    click.echo(header)
    click.echo("─" * len(header))

    for r in resultados:
        sit = r.get("situacao", "")
        valor = f"{r.get('valor_total', 0):>14,.2f}"
        nome_d = (r.get("nome_dest") or "")[:34]
        row = (
            f"{r.get('numero',''):<8} "
            f"{r.get('serie',''):<5} "
            f"{(r.get('data_emissao') or '')[:10]:<12} "
            f"{nome_d:<35} "
            f"{r.get('uf_dest',''):<4} "
            f"{valor}  "
            f"{sit}"
        )
        color = "green" if "Autorizada" in sit else ("red" if "Cancelada" in sit else None)
        click.secho(row, fg=color)
        if xml and r.get("xml_path"):
            click.echo(f"         XML: {r['xml_path']}")


# ─────────────────────────────────────────────
# Comando: status
# ─────────────────────────────────────────────

@cli.command("status")
def status():
    """Exibe estatísticas do hub."""
    _, xml_dir, db_path = get_config()

    if not Path(db_path).exists():
        click.secho("Banco de dados não encontrado. Execute 'sincronizar' primeiro.", fg="yellow")
        return

    db = HubDatabase(db_path, xml_dir)
    stats = db.estatisticas()
    db.close()

    linha()
    click.secho(" Status do Hub NFe", bold=True)
    linha()
    click.echo(f"  Banco de dados : {db_path}")
    click.echo(f"  Diretório XMLs : {xml_dir}")
    click.echo()
    click.secho(f"  Total de notas   : {stats.get('total', 0):,}", fg="green", bold=True)
    click.secho(f"  Valor acumulado  : R$ {(stats.get('valor_total') or 0):,.2f}", fg="green", bold=True)
    click.echo(f"  Nota mais antiga : {stats.get('mais_antiga', '-')}")
    click.echo(f"  Nota mais recente: {stats.get('mais_recente', '-')}")
    click.echo(f"  Meses cobertos   : {stats.get('meses', 0)}")


# ─────────────────────────────────────────────
# Comando: obter-xml
# ─────────────────────────────────────────────

@cli.command("obter-xml")
@click.argument("numero_ou_chave")
def obter_xml(numero_ou_chave):
    """
    Retorna o caminho do XML pelo número ou chave de acesso.

    Exemplos:\n
      python main.py obter-xml 1234\n
      python main.py obter-xml 35240100000000000000550010000000001000000000
    """
    _, xml_dir, db_path = get_config()
    db = HubDatabase(db_path, xml_dir)

    if len(numero_ou_chave) >= 44:
        resultados = db.buscar(chave=numero_ou_chave, limit=1)
    else:
        resultados = db.buscar(numero=numero_ou_chave, limit=5)
    db.close()

    if not resultados:
        click.secho(f"Nota não encontrada: {numero_ou_chave}", fg="yellow")
        return

    for r in resultados:
        click.secho(f"NF {r['numero']}-{r['serie']}  {r['data_emissao']}  "
                    f"{r['nome_dest']}  R$ {r['valor_total']:,.2f}", bold=True)
        click.echo(f"  Chave  : {r['chave_acesso']}")
        click.secho(f"  Arquivo: {r['xml_path']}", fg="cyan")
        click.echo()


if __name__ == "__main__":
    cli()
