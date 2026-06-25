"""
Parser de XML de NFe (NF-e modelo 55).
Extrai os campos principais para indexação no banco de dados.
"""

from typing import Optional
from lxml import etree


# Namespaces da NFe
NS = {
    "nfe": "http://www.portalfiscal.inf.br/nfe",
}


def _text(element, xpath: str) -> str:
    """Retorna o texto de um nó XPath ou string vazia."""
    nodes = element.xpath(xpath, namespaces=NS)
    if nodes:
        return (nodes[0].text or "").strip()
    return ""


def parse_nfe_xml(xml_content: str) -> Optional[dict]:
    """
    Faz o parse do XML de uma NFe e retorna um dicionário com os campos principais.
    Retorna None se o XML for inválido ou não for uma NFe.
    """
    try:
        # Remove BOM se presente
        xml_bytes = xml_content.strip().encode("utf-8")
        if xml_bytes.startswith(b"\xef\xbb\xbf"):
            xml_bytes = xml_bytes[3:]

        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None

    # Aceita tanto <nfeProc> (com protocolo) quanto <NFe> direta
    nfe_node = (
        root.xpath("//nfe:NFe", namespaces=NS)
        or root.xpath("//NFe")
    )
    if not nfe_node:
        return None
    nfe = nfe_node[0]

    infnfe = (
        nfe.xpath("nfe:infNFe", namespaces=NS)
        or nfe.xpath("infNFe")
    )
    if not infnfe:
        return None
    inf = infnfe[0]

    # Chave de acesso (sem dígitos de controle externos — 44 dígitos no Id)
    chave_attr = inf.get("Id", "")
    chave = chave_attr.replace("NFe", "") if chave_attr.startswith("NFe") else chave_attr

    # Identificação
    ide = inf.xpath("nfe:ide", namespaces=NS) or inf.xpath("ide")
    ide = ide[0] if ide else inf

    numero  = _text(ide, "nfe:nNF")  or _text(ide, "nNF")
    serie   = _text(ide, "nfe:serie") or _text(ide, "serie")
    nat_op  = _text(ide, "nfe:natOp") or _text(ide, "natOp")
    dh_emi  = _text(ide, "nfe:dhEmi") or _text(ide, "dhEmi")
    tpnf    = _text(ide, "nfe:tpNF")  or _text(ide, "tpNF")   # 0=entrada 1=saída

    # Normaliza data: pega apenas YYYY-MM-DD
    data_emissao = dh_emi[:10] if dh_emi else ""

    tipo = "saida" if tpnf == "1" else "entrada"

    # Emitente
    emit = inf.xpath("nfe:emit", namespaces=NS) or inf.xpath("emit")
    emit = emit[0] if emit else None
    cnpj_emitente = _text(emit, "nfe:CNPJ") or _text(emit, "CNPJ") if emit else ""
    nome_emitente = _text(emit, "nfe:xNome") or _text(emit, "xNome") if emit else ""

    # Destinatário
    dest = inf.xpath("nfe:dest", namespaces=NS) or inf.xpath("dest")
    dest = dest[0] if dest else None
    cnpj_dest = (
        (_text(dest, "nfe:CNPJ") or _text(dest, "CNPJ") or
         _text(dest, "nfe:CPF")  or _text(dest, "CPF"))
        if dest else ""
    )
    nome_dest = _text(dest, "nfe:xNome") or _text(dest, "xNome") if dest else ""

    end_dest = (
        (dest.xpath("nfe:enderDest", namespaces=NS) or dest.xpath("enderDest"))
        if dest else []
    )
    end_dest = end_dest[0] if end_dest else None
    municipio_dest = _text(end_dest, "nfe:xMun") or _text(end_dest, "xMun") if end_dest else ""
    uf_dest        = _text(end_dest, "nfe:UF")   or _text(end_dest, "UF")   if end_dest else ""

    # Total
    total = inf.xpath("nfe:total/nfe:ICMSTot", namespaces=NS) or inf.xpath("total/ICMSTot")
    total = total[0] if total else None
    valor_total_str = _text(total, "nfe:vNF") or _text(total, "vNF") if total else "0"
    try:
        valor_total = float(valor_total_str)
    except ValueError:
        valor_total = 0.0

    # Situação via protocolo (se disponível)
    prot = root.xpath("//nfe:protNFe/nfe:infProt/nfe:cStat", namespaces=NS)
    if prot:
        cstat = (prot[0].text or "").strip()
        situacao = "Autorizada" if cstat == "100" else f"cStat:{cstat}"
    else:
        situacao = "Sem protocolo"

    return {
        "chave_acesso":  chave,
        "numero":        numero,
        "serie":         serie,
        "data_emissao":  data_emissao,
        "tipo":          tipo,
        "natureza_op":   nat_op,
        "cnpj_emitente": cnpj_emitente,
        "nome_emitente": nome_emitente,
        "cnpj_dest":     cnpj_dest,
        "nome_dest":     nome_dest,
        "municipio_dest":municipio_dest,
        "uf_dest":       uf_dest,
        "valor_total":   valor_total,
        "situacao":      situacao,
    }
