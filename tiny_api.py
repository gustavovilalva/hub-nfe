"""
Conector com a API do Tiny ERP (v2)
Documentação: https://tiny.com.br/api-doc
"""

import time
import requests
from typing import Optional, Generator


TINY_API_BASE = "https://api.tiny.com.br/api2"
SITUACOES_NFE = {
    "1": "Pendente",
    "2": "Enviada",
    "3": "Cancelada",
    "4": "Rejeitada",
    "5": "Autorizada",
    "6": "Denegada",
    "7": "Aguardando recibo",
    "8": "Aguardando protocolo",
}


class TinyAPIError(Exception):
    pass


class TinyAPI:
    """Cliente para a API do Tiny ERP v2."""

    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/x-www-form-urlencoded"})
        # A API do Tiny tem limite de ~30 req/min — aguardamos entre chamadas
        self._last_request = 0.0
        self._min_interval = 2.1  # segundos entre requisições

    def _wait(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _post(self, endpoint: str, params: dict) -> dict:
        self._wait()
        params["token"] = self.token
        params["formato"] = "json"
        url = f"{TINY_API_BASE}/{endpoint}.php"
        response = self.session.post(url, data=params, timeout=30)
        self._last_request = time.time()
        response.raise_for_status()
        data = response.json()
        retorno = data.get("retorno", {})
        status = retorno.get("status", "")
        if status == "Erro":
            erros = retorno.get("erros", [{}])
            msg = erros[0].get("erro", "Erro desconhecido") if erros else "Erro desconhecido"
            raise TinyAPIError(f"Tiny API: {msg}")
        return retorno

    def listar_notas_fiscais(
        self,
        pagina: int = 1,
        situacao: str = "",
        data_inicial: str = "",
        data_final: str = "",
        numero: str = "",
        cpf_cnpj: str = "",
    ) -> dict:
        """
        Retorna lista paginada de NFes.
        situacao: '' = todas | '5' = autorizadas | '3' = canceladas etc.
        datas no formato DD/MM/YYYY
        """
        params = {"pagina": str(pagina)}
        if situacao:
            params["situacao"] = situacao
        if data_inicial:
            params["dataInicial"] = data_inicial
        if data_final:
            params["dataFinal"] = data_final
        if numero:
            params["numero"] = numero
        if cpf_cnpj:
            params["cpf_cnpj"] = cpf_cnpj
        return self._post("notas.fiscais.pesquisa", params)

    def obter_xml(self, id_nota: str) -> Optional[str]:
        """
        Retorna o XML completo de uma NFe pelo ID interno do Tiny.
        Retorna None se a nota não tiver XML disponível.
        """
        try:
            retorno = self._post("nota.fiscal.obter.xml", {"id": id_nota})
            xml_nota = retorno.get("xml_nota_fiscal", {})
            return xml_nota.get("xml", None)
        except TinyAPIError as e:
            if "não possui xml" in str(e).lower() or "não encontrado" in str(e).lower():
                return None
            raise

    def gerar_todas_notas(
        self,
        situacao: str = "5",
        data_inicial: str = "",
        data_final: str = "",
    ) -> Generator[dict, None, None]:
        """
        Gerador que itera por todas as páginas e retorna cada nota como dict.
        Por padrão busca apenas notas autorizadas (situacao='5').
        """
        pagina = 1
        while True:
            retorno = self.listar_notas_fiscais(
                pagina=pagina,
                situacao=situacao,
                data_inicial=data_inicial,
                data_final=data_final,
            )
            notas_raw = retorno.get("notas_fiscais", [])

            if not notas_raw:
                break

            for item in notas_raw:
                nota = item.get("nota_fiscal", {})
                if nota:
                    yield nota

            # Tiny retorna no máximo 100 por página; se retornou menos, acabou
            if len(notas_raw) < 100:
                break

            pagina += 1
