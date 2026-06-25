"""
Integração com Supabase Storage para armazenamento permanente dos XMLs.

Usa a API REST do Supabase — sem SDK extra, só requests.
XMLs são organizados em: YYYY/MM/chave_acesso.xml

Configuração (variáveis de ambiente):
  SUPABASE_URL  → URL do projeto, ex: https://xyzxyz.supabase.co
  SUPABASE_KEY  → service_role key (em Settings > API > service_role)
  SUPABASE_BUCKET → nome do bucket (padrão: hub-nfe)
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)

BUCKET = os.getenv("SUPABASE_BUCKET", "hub-nfe")


def _base() -> str:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    return f"{url}/storage/v1"


def _headers() -> dict:
    key = os.getenv("SUPABASE_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
    }


def configurado() -> bool:
    return bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))


def _caminho(chave_acesso: str, data_emissao: str) -> str:
    """Gera o caminho do arquivo no bucket: YYYY/MM/chave.xml"""
    ano = data_emissao[:4] if data_emissao else "0000"
    mes = data_emissao[5:7] if len(data_emissao) >= 7 else "00"
    return f"{ano}/{mes}/{chave_acesso}.xml"


def upload_xml(xml_content: str, chave_acesso: str, data_emissao: str) -> bool:
    """
    Faz upload de um XML para o Supabase Storage.
    Retorna True em caso de sucesso.
    """
    if not configurado():
        return False
    try:
        path = _caminho(chave_acesso, data_emissao)
        url = f"{_base()}/object/{BUCKET}/{path}"
        headers = _headers()
        headers["Content-Type"] = "application/xml"
        headers["x-upsert"] = "false"  # não sobrescreve se já existe

        resp = requests.post(
            url,
            headers=headers,
            data=xml_content.encode("utf-8"),
            timeout=30,
        )
        if resp.status_code in (200, 409):  # 409 = já existe, tudo bem
            return True
        logger.warning(f"Supabase upload {chave_acesso}: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Supabase upload falhou para {chave_acesso}: {e}")
        return False


def download_xml(path: str) -> str | None:
    """
    Baixa um XML do Supabase Storage pelo caminho completo.
    Retorna o conteúdo como string ou None em caso de erro.
    """
    if not configurado():
        return None
    try:
        url = f"{_base()}/object/{BUCKET}/{path}"
        resp = requests.get(url, headers=_headers(), timeout=30)
        if resp.status_code == 200:
            return resp.text
        logger.warning(f"Supabase download {path}: {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"Supabase download falhou para {path}: {e}")
        return None


def listar_xmls() -> list:
    """
    Lista todos os arquivos XML no bucket, percorrendo as pastas YYYY/MM.
    Retorna lista de dicts com: name, path
    """
    if not configurado():
        return []

    todos = []
    try:
        # Lista as pastas de ano
        anos = _listar_pasta("")
        for ano in anos:
            if not ano.get("id"):  # é uma pasta
                nome_ano = ano["name"]
                meses = _listar_pasta(nome_ano)
                for mes in meses:
                    if not mes.get("id"):
                        nome_mes = mes["name"]
                        arquivos = _listar_pasta(f"{nome_ano}/{nome_mes}")
                        for arq in arquivos:
                            if arq.get("name", "").endswith(".xml"):
                                todos.append({
                                    "name": arq["name"],
                                    "path": f"{nome_ano}/{nome_mes}/{arq['name']}",
                                })
    except Exception as e:
        logger.warning(f"Supabase listar falhou: {e}")

    return todos


def _listar_pasta(prefix: str) -> list:
    """Lista conteúdo de uma pasta no bucket."""
    url = f"{_base()}/object/list/{BUCKET}"
    headers = _headers()
    headers["Content-Type"] = "application/json"
    body = {"prefix": prefix, "limit": 1000, "offset": 0}
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return []


def reindexar_do_storage(db) -> dict:
    """
    Baixa todos os XMLs do Supabase e os indexa no banco local.
    Chamado automaticamente no startup quando o banco está vazio.
    """
    if not configurado():
        return {"importados": 0, "ignorados": 0, "erros": 0}

    arquivos = listar_xmls()
    importados = ignorados = erros = 0

    for arq in arquivos:
        chave = arq["name"].replace(".xml", "")

        if db.ja_existe(chave):
            ignorados += 1
            continue

        xml = download_xml(arq["path"])
        if not xml:
            erros += 1
            continue

        resultado = db.salvar_nota(xml)
        if resultado:
            importados += 1
        else:
            erros += 1

    return {"importados": importados, "ignorados": ignorados, "erros": erros}
