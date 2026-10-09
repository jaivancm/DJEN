"""
Servidor MCP remoto para consulta de comunicações no DJEN (CNJ).

Consulta a API pública https://comunicaapi.pje.jus.br/api/v1/comunicacao
e expõe ferramentas ao Claude por meio de um conector personalizado.

Variáveis de ambiente:
  DJEN_API_KEY   chave exigida no cabeçalho X-API-Key (obrigatória)
  PORT           porta HTTP (padrão 8000)
"""

import os
from datetime import date, timedelta

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
API_KEY = os.environ.get("DJEN_API_KEY", "")
PORT = int(os.environ.get("PORT", "8000"))

# Inscrições do titular. Ajuste se necessário.
INSCRICOES = [("10935", "PI"), ("29408", "MA")]

mcp = FastMCP(
    "DJEN",
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _consultar(params: dict, max_chars: int) -> dict:
    params = {k: v for k, v in params.items() if v not in (None, "")}
    try:
        r = httpx.get(
            BASE_URL,
            params=params,
            timeout=40,
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 djen-mcp"},
        )
    except httpx.HTTPError as e:
        return {"sucesso": False, "erro": f"Falha de rede: {e}"}

    if r.status_code >= 400:
        return {
            "sucesso": False,
            "erro": f"HTTP {r.status_code}",
            "corpo": r.text[:500],
            "parametros": params,
        }
    try:
        data = r.json()
    except ValueError:
        return {"sucesso": False, "erro": "Resposta não é JSON", "corpo": r.text[:500]}

    itens = data.get("items") if isinstance(data, dict) else data
    itens = itens or []
    if max_chars and max_chars > 0:
        for it in itens:
            if isinstance(it, dict) and isinstance(it.get("texto"), str) and len(it["texto"]) > max_chars:
                it["texto"] = it["texto"][:max_chars]
                it["texto_truncado"] = True
    total = data.get("count") if isinstance(data, dict) else len(itens)
    return {"sucesso": True, "total": total, "retornados": len(itens), "publicacoes": itens}


@mcp.tool()
def consultar_por_oab(
    numero_oab: str,
    uf_oab: str,
    data_inicio: str = "",
    data_fim: str = "",
    sigla_tribunal: str = "",
    pagina: int = 1,
    itens_por_pagina: int = 50,
    max_chars: int = 4000,
) -> dict:
    """Consulta comunicações do DJEN por número da OAB e UF.
    Datas no formato AAAA-MM-DD (data de disponibilização). Padrão: últimos 7 dias.
    sigla_tribunal opcional (ex.: TJPI, TRT22, TRF1). max_chars=0 devolve o teor integral."""
    data_fim = data_fim or date.today().isoformat()
    data_inicio = data_inicio or (date.today() - timedelta(days=7)).isoformat()
    return _consultar(
        {
            "numeroOab": numero_oab.replace(".", "").replace("-", ""),
            "ufOab": uf_oab.upper(),
            "dataDisponibilizacaoInicio": data_inicio,
            "dataDisponibilizacaoFim": data_fim,
            "siglaTribunal": sigla_tribunal,
            "pagina": pagina,
            "itensPorPagina": itens_por_pagina,
        },
        max_chars,
    )


@mcp.tool()
def consultar_por_processo(numero_processo: str, max_chars: int = 4000) -> dict:
    """Consulta todas as comunicações do DJEN de um processo (número CNJ, com ou sem pontuação)."""
    return _consultar({"numeroProcesso": numero_processo}, max_chars)


@mcp.tool()
def consultar_por_nome_advogado(
    nome: str,
    data_inicio: str = "",
    data_fim: str = "",
    sigla_tribunal: str = "",
    max_chars: int = 4000,
) -> dict:
    """Consulta comunicações do DJEN pelo nome do advogado. Datas AAAA-MM-DD. Padrão: últimos 7 dias."""
    data_fim = data_fim or date.today().isoformat()
    data_inicio = data_inicio or (date.today() - timedelta(days=7)).isoformat()
    return _consultar(
        {
            "nomeAdvogado": nome,
            "dataDisponibilizacaoInicio": data_inicio,
            "dataDisponibilizacaoFim": data_fim,
            "siglaTribunal": sigla_tribunal,
        },
        max_chars,
    )


@mcp.tool()
def minhas_publicacoes(dias: int = 7, max_chars: int = 4000) -> dict:
    """Consulta as publicações do titular em todas as inscrições cadastradas (OAB/PI e OAB/MA)
    nos últimos N dias, por data de disponibilização."""
    fim = date.today().isoformat()
    ini = (date.today() - timedelta(days=dias)).isoformat()
    saida = {}
    for numero, uf in INSCRICOES:
        saida[f"OAB/{uf} {numero}"] = _consultar(
            {
                "numeroOab": numero,
                "ufOab": uf,
                "dataDisponibilizacaoInicio": ini,
                "dataDisponibilizacaoFim": fim,
                "itensPorPagina": 100,
            },
            max_chars,
        )
    return saida


class ApiKeyMiddleware:
    """Exige X-API-Key em todas as rotas, exceto /health."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if not API_KEY or headers.get("x-api-key") != API_KEY:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"erro":"nao autorizado"}'})
                return
        if scope["type"] == "http" and scope["path"] == "/health":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        await self.app(scope, receive, send)


app = ApiKeyMiddleware(mcp.streamable_http_app())

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
