#!/usr/bin/env python3
"""Sinalizador de reversao para a Hyperliquid (Etapa 1, especificacao REVOU v2).

Processo ao vivo da estrategia de ESTRATEGIA-REVERSAO.md: liga-se por conta
propria a Hyperliquid (velas de 15 m e de 1 h, negocios e contexto do activo),
recalcula o contexto no fecho de cada vela de 1 h, avalia o gatilho no fecho de
cada vela de 15 m e, quando todas as condicoes G0 a G8 passam, escreve UMA
linha em dados/sinais.csv, que o medidor mede como mede qualquer sinal. Nunca
escreve nos ficheiros do medidor; os seus ficheiros sao proprios:

    dados/registo_sinalizador.csv    trades virtuais fechados (seccao 7.5)
    dados/estado_sinalizador.json    estado por activo e disjuntores (atomico)
    dados/velas/<ATIVO>_<int>_<dia>.csv  velas e colunas enriquecidas
    dados/funding/<ATIVO>.csv        fundingHistory por hora
    dados/baleias.csv                lista diaria (so prefixo e hash)
    dados/ensaios.csv                configuracoes experimentadas
    dados/sinalizador.log            registo com rotacao

Comandos:
    python3 sinalizador.py correr      corre o sinalizador (Ctrl+C para parar)
    python3 sinalizador.py verificar   confere as hipoteses H1 a H8 da API
    python3 sinalizador.py historico   descarrega velas e funding para dados/
    python3 sinalizador.py baleias     refaz a lista diaria de baleias
    python3 sinalizador.py relatorio   cruza o registo proprio com o do medidor
    python3 sinalizador.py ensaios     lista as configuracoes experimentadas
    python3 sinalizador.py reset       levanta os disjuntores

As formulas estao todas em reversao.py; este ficheiro trata do mundo exterior
(rede, ficheiros, relogio) e da ordem das coisas. Nenhum pedido de rede corre
entre o fecho da vela e a escrita da linha. A chave da CoinGlass nunca aparece
em logs, repr, notas nem ficheiros. Compativel com Python 3.9+; so a biblioteca
padrao e websockets.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import configparser
import csv
import io
import json
import logging
import logging.handlers
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

import medidor as md
import nucleo as nu
import reversao as rv
from medidor import (Config, Registo, URLS, agora_ms, base_da_liquidacao, chave_liquidacao, flt,
                     interpretar_hora, iso_utc, lado_liquidacao, num, obter_sz_decimals, pedir_info,
                     positivo, volume_liquidacao)

VERSAO = "1.0"
log = logging.getLogger("sinalizador")

NAN = float("nan")
MS_15M = 900_000
MS_1H = 3_600_000
MS_DIA = 86_400_000
INTERVALOS_MS = {"15m": MS_15M, "1h": MS_1H}
PESO_MINUTO_MAX = 400          # orcamento de peso por minuto do sinalizador (seccao 10)
PESO_CLEARINGHOUSE = 2
PESO_SNAPSHOT = 20             # mais 1 por 60 velas
PESO_FUNDING = 20              # fundingHistory: 20 mais 1 por 60 linhas (seccao 10)
VELAS_POR_PEDIDO = 5000
TIDS_VISTOS = 20000            # negocios recentes guardados por activo para nao ingerir o mesmo tid duas vezes
FAIXA_IMAN_BPS = 25.0          # (52)
CAB_SINAIS = md.Medidor.CAB_SINAIS            # hora,ativo,lado,preco,alvo_bps,tamanho_usd,nota
COLUNAS_VELAS = ["t", "T", "o", "h", "l", "c", "v", "n"]
COLUNAS_ENRIQUECIDAS = ["v_b", "v_a", "n_grandes", "flx", "rep", "abs", "hhi", "raj", "twap", "oi_usd",
                        "funding", "premium", "liq_long", "liq_short", "pos", "fuel", "iman", "atraso_ms", "corte"]
COLUNAS_FUNDING = ["time", "fundingRate", "premium"]
COLUNAS_REGISTO = ["id", "hora", "ativo", "lado", "preco", "A_0", "sigma_0", "phi_0", "H_0", "G", "L", "tmax_15",
                   "fase", "f_B", "hora_saida", "preco_saida", "motivo", "velas", "mae_bps", "mfe_bps",
                   "r_bruto_bps", "funding_bps", "r_liq_bps", "nota"]
COLUNAS_BALEIAS = ["prefixo", "hash", "valor_usd", "roi_semana", "roi_mes", "vlm_mes", "hora_utc"]
COLUNAS_ENSAIOS = ["data", "hash", "n", "expectancia_bps", "ep_bps", "sr", "parametros"]
SESSOES = ("asia", "europa", "eua", "fds")
MOTIVOS_SAIDA = ("alvo", "stop", "tempo", "invalidacao")


def _sim(txt: str) -> bool:
    return (txt or "").strip().lower() in ("sim", "s", "yes", "true", "1")


def _lista(txt: str) -> List[str]:
    return [p.strip() for p in (txt or "").replace(";", ",").split(",") if p.strip()]


def dia_utc(ms: int) -> str:
    """Dia UTC (AAAA-MM-DD) de um instante em ms."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def sem_chave(texto: Any, chave: str) -> str:
    """Texto com a chave da CoinGlass substituida por ***, para logs e mensagens."""
    t = str(texto)
    if chave:
        t = t.replace(chave, "***")
    return t


def escrever_json_atomico(caminho: str, doc: Dict[str, Any]) -> None:
    """Escreve um JSON por ficheiro temporario e os.replace, para nunca haver meio ficheiro."""
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, caminho)


def _limpo(v: Any) -> Any:
    """nan e inf viram None para o JSON (tambem dentro de dicionarios e listas); tuplos viram listas."""
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    if isinstance(v, dict):
        return {str(k): _limpo(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_limpo(x) for x in v]
    return v


def _ler_csv(caminho: str) -> List[Dict[str, str]]:
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ==========================================================================
# Configuracao: [sinalizador] por cima de md.Config (seccao 12)
# ==========================================================================
@dataclass(frozen=True)
class ParametrosSinalizador:
    """Chaves de [sinalizador] que nao pertencem as dataclasses de reversao.py."""
    h_a: float = 96.0
    # NOTA ESPEC: a tabela da seccao 12 chama a n_ajuste "janela de partida" que "cresce com o historico em
    # disco", mas o candleSnapshot ja da 5000 velas de 1 h no primeiro arranque e o backtest le o mesmo
    # ficheiro inteiro: a unica regra igual nos dois modulos e N = min(n_max, velas fechadas depois do
    # aquecimento da ancora), validada por n_min. n_ajuste fica como janela de referencia (entra na
    # variante e na sigma do nulo GBM do backtest) e nao altera N; config.ini di-lo.
    n_ajuste: int = 720
    n_min: int = 480
    n_max: int = 2160
    calibrar_nula: bool = True
    alpha_nula: float = 0.001
    replicas_nula: int = 1000
    semente_nula: int = 7
    t_nulo_max: float = -3.0
    q_vr: int = 8
    h_vc: float = 8.0
    h_vl: float = 96.0
    k_dia: float = 2.0
    z_in: float = 2.0
    z_stop: float = 3.0
    n_z: int = 720
    z_in_emp_max: float = 2.5
    k_h: float = 1.0
    k_max_tecto: int = 64
    n_h: float = 2.0
    tmax_h: float = 16.0
    folga_fecho_ms: int = 1500
    atraso_max_ms: int = 3000
    idade_ctx_max_min: float = 75.0
    rajada_dt_ms: int = 1000
    rajada_amp_bps: float = 2.0
    rajada_silencio_s: float = 60.0
    q_grande: float = 0.99
    n_grandes_max: int = 20000
    baleias_max: int = 100
    sonda_s: float = 900.0
    baleia_valor_min_usd: float = 1e6
    baleia_vlm_racio_max: float = 50.0
    baleia_direcc_min: float = 0.30
    baleia_liq_dias: int = 7
    capital_usd: float = 20000.0
    expo_max: float = 0.75
    g_min_x_custo: float = 3.0
    custo_taxa_entrada_bps: float = 4.5
    imp_defeito_bps: float = 2.0
    c_w_bps: float = 8.0
    c_l_bps: float = 13.0
    arrefecimento_velas: int = 2
    lado_por_tick: bool = False        # H2 falhada em verificar: lado do negocio pelo tick
    veto_sessao: Tuple[str, ...] = ()
    enderecos_excluidos: Tuple[str, ...] = ()
    pasta_velas: str = "dados/velas"
    url_leaderboard: str = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


class ConfigSinalizador(Config):
    """md.Config mais a seccao [sinalizador] em dataclasses de parametros (seccao 12).

    Le [geral], [orcamento], [sombra], [coinglass] e [avancado] pelo md.Config e a
    seccao [sinalizador] para p (ParametrosSinalizador), p_ctx, p_gat, p_twap, p_bal,
    p_tam e p_disj (dataclasses de reversao.py). Valida com SystemExit em portugues.
    Avisa no log se [sombra] horizontes_s nao incluir 900, 3600 e 14400. A chave da
    CoinGlass fica em cg_chave como no medidor e nunca entra no repr nem no log.
    """

    def __init__(self, caminho: str):
        super().__init__(caminho)
        cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cp.read(self.caminho, encoding="utf-8")

        def g(chave: str, defeito: str) -> str:
            return cp.get("sinalizador", chave, fallback=defeito).strip()

        def f(chave: str, defeito: float) -> float:
            txt = g(chave, repr(defeito))
            try:
                v = float(txt)
            except ValueError:
                raise SystemExit("config.ini: [sinalizador] %s tem de ser um numero (tem %r)" % (chave, txt))
            if v != v or v in (float("inf"), float("-inf")):
                raise SystemExit("config.ini: [sinalizador] %s tem de ser um numero finito" % chave)
            return v

        def i(chave: str, defeito: int) -> int:
            return int(round(f(chave, float(defeito))))

        sessoes = tuple(s.lower() for s in _lista(g("veto_sessao", "")))
        for s in sessoes:
            if s not in SESSOES:
                raise SystemExit("config.ini: [sinalizador] veto_sessao so aceita %s (tem %r)" % (", ".join(SESSOES), s))
        excluidos = tuple(e.lower() for e in _lista(g("enderecos_excluidos",
                                                    "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303;"
                                                    "0x2e3d94f0562703b25c83308a05046ddaf9a8dd14")))
        pasta_velas = g("pasta_velas", "dados/velas")
        self.p = ParametrosSinalizador(
            h_a=f("h_a", 96.0), n_ajuste=i("n_ajuste", 720), n_min=i("n_min", 480), n_max=i("n_max", 2160),
            calibrar_nula=_sim(g("calibrar_nula", "sim")), alpha_nula=f("alpha_nula", 0.001),
            replicas_nula=i("replicas_nula", 1000), semente_nula=i("semente_nula", 7),
            t_nulo_max=f("t_nulo_max", -3.0), q_vr=i("q_vr", 8), h_vc=f("h_vc", 8.0), h_vl=f("h_vl", 96.0),
            k_dia=f("k_dia", 2.0), z_in=f("z_in", 2.0), z_stop=f("z_stop", 3.0), n_z=i("n_z", 720),
            z_in_emp_max=f("z_in_emp_max", 2.5), k_h=f("k_h", 1.0), k_max_tecto=i("k_max_tecto", 64),
            n_h=f("n_h", 2.0), tmax_h=f("tmax_h", 16.0), folga_fecho_ms=i("folga_fecho_ms", 1500),
            atraso_max_ms=i("atraso_max_ms", 3000), idade_ctx_max_min=f("idade_ctx_max_min", 75.0),
            rajada_dt_ms=i("rajada_dt_ms", 1000), rajada_amp_bps=f("rajada_amp_bps", 2.0),
            rajada_silencio_s=f("rajada_silencio_s", 60.0), q_grande=f("q_grande", 0.99),
            n_grandes_max=i("n_grandes_max", 20000), baleias_max=i("baleias_max", 100), sonda_s=f("sonda_s", 900.0),
            baleia_valor_min_usd=f("baleia_valor_min_usd", 1e6), baleia_vlm_racio_max=f("baleia_vlm_racio_max", 50.0),
            baleia_direcc_min=f("baleia_direcc_min", 0.30), baleia_liq_dias=i("baleia_liq_dias", 7),
            capital_usd=f("capital_usd", 20000.0), expo_max=f("expo_max", 0.75),
            g_min_x_custo=f("g_min_x_custo", 3.0), custo_taxa_entrada_bps=f("custo_taxa_entrada_bps", 4.5),
            imp_defeito_bps=f("imp_defeito_bps", 2.0), c_w_bps=f("c_w_bps", 8.0), c_l_bps=f("c_l_bps", 13.0),
            arrefecimento_velas=i("arrefecimento_velas", 2), lado_por_tick=_sim(g("lado_por_tick", "nao")),
            veto_sessao=sessoes, enderecos_excluidos=excluidos,
            pasta_velas=pasta_velas, url_leaderboard=g("url_leaderboard", ParametrosSinalizador.url_leaderboard))
        self.p_ctx = rv.ParametrosContexto(
            t_amarelo=f("t_amarelo", -2.0), vr_max_verde=f("vr_max_verde", 1.0), vr_veto=f("vr_veto", 1.2),
            zvr_veto=f("zvr_veto", 2.0), h_min=f("h_min", 3.0), h_max=f("h_max", 24.0),
            vol_razao_max=f("vol_razao_max", 2.0), n_min=self.p.n_min, z_veto=f("z_veto", 4.0),
            desloc_alvo_max=f("desloc_alvo_max", 0.5))
        self.p_gat = rv.ParametrosGatilho(
            z_out=f("z_out", 0.5), z_min_resto=f("z_min_resto", 0.75), z_veto=self.p_ctx.z_veto,
            k_choque=f("k_choque", 4.0), vol_razao_max=self.p_ctx.vol_razao_max)
        self.p_twap = rv.ParametrosTwap(
            min_negocios=i("twap_min_negocios", 4), janela_s=f("twap_janela_s", 600.0),
            dt_min_s=f("twap_dt_min_s", 10.0), dt_max_s=f("twap_dt_max_s", 50.0), usar_hash=_sim(g("twap_usar_hash", "sim")))
        self.p_bal = rv.ParametrosBaleias(
            limiar_flx=f("limiar_flx", 0.3), limiar_rep=i("limiar_rep", 2), limiar_fz=f("limiar_fz", 1.0),
            limiar_liq=f("limiar_liq", 0.5), liq_alto=f("liq_alto", 0.8), limiar_abs=f("limiar_abs", 1.0),
            pct_fuel=f("pct_fuel", 0.02), pos_alto=f("pos_alto", 0.8), pos_baixo=f("pos_baixo", 0.1),
            b_veto=i("b_veto", -2), fb_baixo=f("fb_baixo", 0.5), fb_alto=f("fb_alto", 1.25), b_alto=i("b_alto", 3))
        self.p_tam = rv.ParametrosTamanho(
            k_kelly=f("k_kelly", 0.25), risco_por_trade=f("risco_por_trade", 0.005), vol_alvo_dia=f("vol_alvo_dia", 0.01),
            liq_fraccao=f("liq_fraccao", 0.01), tamanho_base=f("tamanho_base", 1000.0),
            tamanho_max_frac=f("tamanho_max_frac", 0.25), minimo_ordem_usd=self.minimo_ordem_usd,
            n_cal=i("n_cal", 30), m_min=f("m_min", 0.05), z=1.96)
        self.p_disj = rv.ParametrosDisjuntores(
            perdas_dia_x_g=f("perdas_dia_x_g", 3.0), perdas_seguidas=i("perdas_seguidas", 5),
            cvar_x_l=f("cvar_x_l", 2.0), cvar_fraccao=0.05, cvar_n=100)
        self._validar()

        self.pasta_velas = pasta_velas if os.path.isabs(pasta_velas) else os.path.join(self.base, pasta_velas)
        self.pasta_funding = os.path.join(self.pasta, "funding")
        self.reg_sinalizador = os.path.join(self.pasta, "registo_sinalizador.csv")
        self.estado_sinalizador_json = os.path.join(self.pasta, "estado_sinalizador.json")
        self.baleias_csv = os.path.join(self.pasta, "baleias.csv")
        self.ensaios_csv = os.path.join(self.pasta, "ensaios.csv")
        self.log_sinalizador = os.path.join(self.pasta, "sinalizador.log")
        self.lam_a = rv.lambda_ancora(self.p.h_a)
        self.excluidos_hash: Set[str] = {rv.hash_endereco(e) for e in self.p.enderecos_excluidos}
        self.liqsrc = "na"       # cg so quando o REST ou o websocket confirmarem liquidacoes da Hyperliquid (H7)
        self.variante = rv.hash_variante(self.parametros_variante())
        for h in (900, 3600, 14400):
            if h not in self.horizontes:
                log.warning("config.ini: [sombra] horizontes_s nao inclui %d s; o medidor nao vai medir o sinal "
                            "a escala do trade (recomendado: 5, 30, 60, 300, 900, 3600, 14400)", h)
        if self.p.h_a < 4.0 * self.p_ctx.h_max:
            log.warning("config.ini: h_a = %g e menor do que 4 x h_max = %g; o factor de captura rho pode "
                        "ficar abaixo de 0,75", self.p.h_a, 4.0 * self.p_ctx.h_max)

    def _validar(self) -> None:
        p, c, gt, t = self.p, self.p_ctx, self.p_gat, self.p_tam

        def exige(cond: bool, msg: str) -> None:
            if not cond:
                raise SystemExit("config.ini: [sinalizador] " + msg)

        exige(p.h_a > 0, "h_a tem de ser positivo")
        exige(3 <= p.n_min <= p.n_ajuste <= p.n_max, "e preciso 3 <= n_min <= n_ajuste <= n_max")
        exige(0.0 < p.alpha_nula < 1.0, "alpha_nula tem de estar entre 0 e 1")
        exige(p.replicas_nula >= 1, "replicas_nula tem de ser pelo menos 1")
        exige(p.q_vr >= 2, "q_vr tem de ser pelo menos 2")
        exige(0 < c.h_min <= c.h_max, "h_min tem de ser positivo e nao maior do que h_max")
        exige(0 < p.h_vc <= p.h_vl, "e preciso 0 < h_vc <= h_vl")
        exige(c.vol_razao_max > 0, "vol_razao_max tem de ser positivo")
        exige(gt.k_choque > 0, "k_choque tem de ser positivo")
        exige(p.k_dia >= 0, "k_dia nao pode ser negativo (0 desliga)")
        exige(0.0 <= gt.z_out < p.z_in < p.z_stop <= c.z_veto, "e preciso 0 <= z_out < z_in < z_stop <= z_veto")
        exige(gt.z_min_resto >= 0, "z_min_resto nao pode ser negativo")
        exige(p.n_z >= 10, "n_z tem de ser pelo menos 10")
        exige(p.z_in_emp_max >= p.z_in, "z_in_emp_max nao pode ser menor do que z_in")
        exige(p.k_h > 0 and p.k_max_tecto >= 1, "k_h tem de ser positivo e k_max_tecto pelo menos 1")
        exige(p.n_h > 0 and p.tmax_h > 0, "n_h e tmax_h tem de ser positivos")
        exige(p.folga_fecho_ms >= 0 and p.atraso_max_ms > 0 and p.idade_ctx_max_min > 0,
              "folga_fecho_ms >= 0, atraso_max_ms > 0 e idade_ctx_max_min > 0")
        exige(p.rajada_dt_ms > 0 and p.rajada_amp_bps >= 0 and p.rajada_silencio_s >= 0,
              "rajada_dt_ms > 0, rajada_amp_bps >= 0 e rajada_silencio_s >= 0")
        exige(0.0 < p.q_grande < 1.0 and p.n_grandes_max >= 100, "q_grande entre 0 e 1 e n_grandes_max >= 100")
        exige(self.p_twap.min_negocios >= 2 and 0 < self.p_twap.dt_min_s <= self.p_twap.dt_max_s
              and self.p_twap.janela_s > 0, "twap_min_negocios >= 2 e 0 < twap_dt_min_s <= twap_dt_max_s")
        exige(0.0 < self.p_bal.pos_alto <= 1.0 and 0.0 <= self.p_bal.pos_baixo < 1.0, "pos_alto em (0, 1] e pos_baixo em [0, 1)")
        exige(self.p_bal.fb_baixo > 0 and self.p_bal.fb_alto >= self.p_bal.fb_baixo, "fb_baixo > 0 e fb_alto >= fb_baixo")
        exige(0 <= self.p_bal.limiar_liq <= self.p_bal.liq_alto, "0 <= limiar_liq <= liq_alto")
        exige(p.baleias_max >= 0 and p.sonda_s > 0, "baleias_max >= 0 e sonda_s > 0")
        exige(p.baleia_valor_min_usd >= 0 and p.baleia_vlm_racio_max > 0 and 0 <= p.baleia_direcc_min <= 1
              and p.baleia_liq_dias >= 0, "filtros das baleias fora do intervalo")
        exige(p.capital_usd > 0, "capital_usd tem de ser positivo")
        exige(0.0 < t.k_kelly <= 1.0, "k_kelly tem de estar entre 0 e 1")
        exige(t.risco_por_trade > 0 and t.vol_alvo_dia > 0 and t.liq_fraccao > 0,
              "risco_por_trade, vol_alvo_dia e liq_fraccao tem de ser positivos")
        exige(t.tamanho_base > 0 and 0.0 < t.tamanho_max_frac <= 1.0, "tamanho_base > 0 e tamanho_max_frac em (0, 1]")
        exige(p.expo_max > 0, "expo_max tem de ser positivo")
        exige(t.n_cal >= 1 and t.m_min >= 0, "n_cal >= 1 e m_min >= 0")
        exige(p.g_min_x_custo >= 0, "g_min_x_custo nao pode ser negativo")
        exige(p.custo_taxa_entrada_bps >= 0 and p.imp_defeito_bps >= 0 and p.c_w_bps >= 0 and p.c_l_bps >= 0,
              "custos de defeito nao podem ser negativos")
        exige(self.p_disj.perdas_dia_x_g > 0 and self.p_disj.perdas_seguidas >= 1 and self.p_disj.cvar_x_l > 0,
              "perdas_dia_x_g > 0, perdas_seguidas >= 1 e cvar_x_l > 0")
        exige(p.arrefecimento_velas >= 0, "arrefecimento_velas nao pode ser negativo")
        for e in p.enderecos_excluidos:
            exige(e.startswith("0x") and len(e) == 42, "enderecos_excluidos: %r nao tem a forma 0x e 40 caracteres" % e)

    def parametros_variante(self) -> Dict[str, float]:
        """Parametros numericos em vigor, para hash_variante (var= na nota)."""
        out: Dict[str, float] = {}
        for nome, dc in (("", self.p), ("ctx_", self.p_ctx), ("gat_", self.p_gat), ("twap_", self.p_twap),
                         ("bal_", self.p_bal), ("tam_", self.p_tam), ("disj_", self.p_disj)):
            for campo in fields(dc):
                v = getattr(dc, campo.name)
                if isinstance(v, (bool, int, float)):
                    out[nome + campo.name] = float(v)
        return out

    def __repr__(self) -> str:
        return "ConfigSinalizador(ativos=%r, pasta=%r, variante=%r)" % (self.ativos, self.pasta, self.variante)


# ==========================================================================
# Velas: formato do canal candle (H1) e historico em memoria e em disco
# ==========================================================================
Vela = rv.Vela      # dataclass frozen (t, T, o, h, l, c, v, n, completa); a mesma de reversao.py


def interpretar_vela(msg: Dict[str, Any]) -> Vela:
    """Seccao 3.1 (H1): Vela a partir de {"channel":"candle","data":{t,T,s,i,o,c,h,l,v,n}} ou do proprio objecto; numeros ou texto.

    ValueError se faltar t, T, o, h, l ou c, se os numeros nao se lerem, ou se a vela
    for impossivel (l <= 0, h < l, o ou c fora de [l, h], T < t). v e n em falta valem 0.
    """
    if not isinstance(msg, dict):
        raise ValueError("vela tem de ser um dicionario")
    d = msg.get("data") if isinstance(msg.get("data"), dict) and "t" not in msg else msg
    if not isinstance(d, dict):
        raise ValueError("vela sem campo data")
    faltam = [k for k in ("t", "T", "o", "h", "l", "c") if d.get(k) is None]
    if faltam:
        raise ValueError("vela sem os campos %s" % ", ".join(faltam))
    try:
        t, T = int(float(d["t"])), int(float(d["T"]))
        o, h, l, c = (float(d["o"]), float(d["h"]), float(d["l"]), float(d["c"]))
        v = float(d.get("v") if d.get("v") not in (None, "") else 0.0)
        n = int(float(d.get("n") if d.get("n") not in (None, "") else 0))
    except (TypeError, ValueError) as e:
        raise ValueError("vela com numeros ilegiveis (%s)" % e)
    if any(x != x for x in (o, h, l, c, v)):
        raise ValueError("vela com nan")
    if l <= 0.0 or h < l or not (l <= o <= h) or not (l <= c <= h) or T < t:
        raise ValueError("vela impossivel: t=%d T=%d o=%g h=%g l=%g c=%g" % (t, T, o, h, l, c))
    return Vela(t, T, o, h, l, c, v, n, True)


def vela_de_linha(ln: Dict[str, str]) -> Optional[Vela]:
    """Vela a partir de uma linha dos CSV de dados/velas; None se a linha nao se ler."""
    try:
        return interpretar_vela({k: ln.get(k, "") for k in COLUNAS_VELAS})
    except ValueError:
        return None


class Historico:
    """Velas de um activo e intervalo em memoria e em dados/velas/<ATIVO>_<intervalo>_<dia>.csv (seccao 11.1).

    A memoria guarda as ultimas `maximo` velas ordenadas por t (uma por t; a mais recente
    substitui). Os ficheiros sao diarios (dia UTC do inicio da vela), so de acrescento, com
    as colunas comuns t,T,o,h,l,c,v,n e, so no 15 m, as colunas enriquecidas (vazias quando
    nao se sabem). Uma vela ja em disco nao se volta a escrever.
    """

    def __init__(self, ativo: str, intervalo: str, pasta: str, maximo: int = 12000):
        if intervalo not in INTERVALOS_MS:
            raise ValueError("intervalo tem de ser 15m ou 1h")
        self.ativo = ativo
        self.intervalo = intervalo
        self.pasta = pasta
        self.maximo = maximo
        self.ms = INTERVALOS_MS[intervalo]
        self.colunas = COLUNAS_VELAS + (COLUNAS_ENRIQUECIDAS if intervalo == "15m" else [])
        self._velas: Dict[int, Vela] = {}
        self._ts: List[int] = []
        self._em_disco: Set[int] = set()
        self._reg: Optional[Registo] = None
        self._reg_dia = ""

    def __len__(self) -> int:
        return len(self._ts)

    def ficheiro(self, dia: str) -> str:
        return os.path.join(self.pasta, "%s_%s_%s.csv" % (self.ativo.replace("/", "_"), self.intervalo, dia))

    def juntar(self, vela: Vela) -> None:
        """Guarda a vela em memoria (substitui a do mesmo t); corta as mais antigas alem de maximo."""
        if vela.t not in self._velas:
            bisect.insort(self._ts, vela.t)
        self._velas[vela.t] = vela
        while len(self._ts) > self.maximo:
            self._velas.pop(self._ts.pop(0), None)

    def ultimas(self, n: int) -> List[Vela]:
        if n <= 0:
            return []
        return [self._velas[t] for t in self._ts[-n:]]

    def ultima(self) -> Optional[Vela]:
        return self._velas[self._ts[-1]] if self._ts else None

    def tem(self, t: int) -> bool:
        return t in self._velas

    def juntar_lista(self, lista: List[Dict[str, Any]], ate_ms: Optional[int] = None) -> int:
        """Junta uma lista no formato de candleSnapshot; ignora velas ainda abertas (T > ate_ms) e ilegiveis."""
        limite = agora_ms() if ate_ms is None else ate_ms
        n = 0
        for item in lista or []:
            try:
                v = interpretar_vela(item)
            except ValueError as e:
                log.warning("%s %s: vela ignorada (%s)", self.ativo, self.intervalo, e)
                continue
            if v.T > limite:
                continue
            if not self.tem(v.t):
                n += 1
            self.juntar(v)
        return n

    def carregar_disco(self, dias: int = 400) -> int:
        """Le os CSV diarios da pasta (os ultimos `dias` ficheiros) para a memoria; devolve velas lidas."""
        if not os.path.isdir(self.pasta):
            return 0
        prefixo = "%s_%s_" % (self.ativo.replace("/", "_"), self.intervalo)
        nomes = sorted(f for f in os.listdir(self.pasta) if f.startswith(prefixo) and f.endswith(".csv"))
        n = 0
        for nome in nomes[-max(1, dias):]:
            try:
                for ln in _ler_csv(os.path.join(self.pasta, nome)):
                    v = vela_de_linha(ln)
                    if v is None:
                        continue
                    self._em_disco.add(v.t)
                    if not self.tem(v.t):
                        n += 1
                        self.juntar(v)
            except Exception as e:
                log.warning("Nao consegui ler %s (%s)", nome, e)
        return n

    def carregar_snapshot(self, cfg: Config, inicio_ms: Optional[int] = None, fim_ms: Optional[int] = None,
                          pedir: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                          orcamento: Optional["OrcamentoPeso"] = None) -> int:
        """candleSnapshot paginado (ate 5000 velas por pedido) de inicio_ms (defeito: 5000 velas atras) a fim_ms (agora).

        Usa `pedir` (defeito: medidor.pedir_info, sobreponivel nos testes) com o corpo
        {"type":"candleSnapshot","req":{coin, interval, startTime, endTime}}; aceita a
        resposta como lista de velas ou {"data": lista}. Devolve o numero de velas novas.
        Peso: 20 mais 1 por 60 velas por pedido, descontado em `orcamento` (OrcamentoPeso
        partilhado, 400 por minuto) depois de cada pagina; corre no arranque, nas religacoes e
        no comando historico.
        """
        pedir = pedir or pedir_info
        fim = agora_ms() if fim_ms is None else fim_ms
        inicio = fim - VELAS_POR_PEDIDO * self.ms if inicio_ms is None else inicio_ms
        total = 0
        for _ in range(40):
            if inicio >= fim:
                break
            corpo = {"type": "candleSnapshot", "req": {"coin": self.ativo, "interval": self.intervalo,
                                                        "startTime": int(inicio), "endTime": int(fim)}}
            resposta = pedir(cfg.url_info, corpo)
            lista = resposta.get("data") if isinstance(resposta, dict) else resposta
            if orcamento is not None:
                orcamento.usar(PESO_SNAPSHOT + int(math.ceil(len(lista) / 60.0)) if isinstance(lista, list) else PESO_SNAPSHOT)
            if not isinstance(lista, list) or not lista:
                break
            antes = len(self)
            self.juntar_lista(lista, fim)
            total += len(self) - antes
            ultimo_t = max(int(float(x.get("t", 0))) for x in lista if isinstance(x, dict)) if lista else inicio
            if len(lista) < 2 or ultimo_t + self.ms >= fim or ultimo_t < inicio:
                break
            inicio = ultimo_t + self.ms
        return total

    def guardar(self, vela: Vela, extra: Optional[Dict[str, Any]] = None) -> None:
        """Acrescenta a vela ao CSV diario com as colunas enriquecidas (so no 15 m); nao repete um t ja em disco."""
        if vela.t in self._em_disco:
            return
        dia = dia_utc(vela.t)
        if self._reg is None or dia != self._reg_dia:
            self._reg = Registo(self.ficheiro(dia), self.colunas)
            self._reg_dia = dia
        linha: Dict[str, Any] = {"t": vela.t, "T": vela.T, "o": num(vela.o, 8), "h": num(vela.h, 8),
                                 "l": num(vela.l, 8), "c": num(vela.c, 8), "v": num(vela.v, 6), "n": vela.n}
        if self.intervalo == "15m":
            for k in COLUNAS_ENRIQUECIDAS:
                v = (extra or {}).get(k)
                linha[k] = num(v, 6) if isinstance(v, float) else ("" if v is None else v)
        self._reg.escrever(linha)
        self._em_disco.add(vela.t)


def guardar_funding(cfg: ConfigSinalizador, ativo: str, linhas: List[Dict[str, Any]]) -> int:
    """Acrescenta linhas de fundingHistory (time, fundingRate, premium) a dados/funding/<ATIVO>.csv sem repetir time."""
    os.makedirs(cfg.pasta_funding, exist_ok=True)
    caminho = os.path.join(cfg.pasta_funding, "%s.csv" % ativo.replace("/", "_"))
    vistos = {ln.get("time", "") for ln in _ler_csv(caminho)}
    reg = Registo(caminho, COLUNAS_FUNDING)
    n = 0
    for ln in sorted(linhas, key=lambda x: int(flt(x.get("time")) if flt(x.get("time")) == flt(x.get("time")) else 0)):
        t = str(int(flt(ln.get("time")))) if flt(ln.get("time")) == flt(ln.get("time")) else ""
        if not t or t in vistos:
            continue
        reg.escrever({"time": t, "fundingRate": num(flt(ln.get("fundingRate")), 10),
                      "premium": num(flt(ln.get("premium")), 10)})
        vistos.add(t)
        n += 1
    return n


def carregar_funding(cfg: ConfigSinalizador, ativo: str) -> List[Tuple[int, float]]:
    """(time_ms, fundingRate) por hora de dados/funding/<ATIVO>.csv, por ordem de tempo."""
    caminho = os.path.join(cfg.pasta_funding, "%s.csv" % ativo.replace("/", "_"))
    out = []
    for ln in _ler_csv(caminho):
        t, fr = flt(ln.get("time")), flt(ln.get("fundingRate"))
        if t == t and fr == fr:
            out.append((int(t), fr))
    out.sort()
    return out


# ==========================================================================
# A linha escrita (seccao 9) e a convivencia com o medidor (seccao 10)
# ==========================================================================
def escrever_sinal(cfg: Config, sinal: rv.Sinal) -> int:
    """Seccao 9: espelho de cmd_sinal com alvo_bps e tamanho_usd; devolve o ms da escrita (0 se recusado).

    Ficheiro aberto em modo a, cabecalho so quando esta vazio, UMA chamada write com a
    linha terminada em \\n (csv.writer sobre memoria), fecho imediato. hora = iso_utc da
    decisao (sinal.hora_ms); lado em {compra, venda}; alvo_bps com 1 casa; tamanho_usd
    com 0 casas. Um activo fora de [geral] ativos e recusado com aviso. Regista a
    latencia decisao-escrita no log.
    """
    if sinal.ativo not in cfg.ativos:
        log.warning("Sinal recusado: activo %r nao esta em [geral] ativos (%s)", sinal.ativo, ", ".join(cfg.ativos))
        return 0
    if sinal.lado not in (1, -1):
        raise ValueError("lado tem de ser +1 ou -1")
    if not (sinal.preco == sinal.preco and sinal.preco > 0.0):
        raise ValueError("preco tem de ser positivo")
    nota = "".join(ch for ch in str(sinal.nota or "") if 32 <= ord(ch) < 127 and ch not in ",\"'")
    mem = io.StringIO()
    csv.writer(mem, lineterminator="\n").writerow([
        iso_utc(sinal.hora_ms), sinal.ativo, "compra" if sinal.lado > 0 else "venda", "%.8g" % sinal.preco,
        "%.1f" % sinal.alvo_bps, "%.0f" % sinal.tamanho_usd, nota])
    linha = mem.getvalue()
    if not linha.endswith("\n"):
        linha += "\n"
    os.makedirs(cfg.pasta, exist_ok=True)
    novo = not os.path.exists(cfg.sinais_csv) or os.path.getsize(cfg.sinais_csv) == 0
    with open(cfg.sinais_csv, "a", encoding="utf-8", newline="") as f:
        if novo:
            f.write(CAB_SINAIS + "\n")
        f.write(linha)
    t = agora_ms()
    log.info("Sinal escrito: %s %s %.8g alvo %.1f bps tamanho %.0f usd (latencia decisao-escrita %d ms)",
             "compra" if sinal.lado > 0 else "venda", sinal.ativo, sinal.preco, sinal.alvo_bps, sinal.tamanho_usd,
             t - sinal.hora_ms)
    return t


_AVISO_MEDIDOR = {"ms": 0}


def ler_estado_medidor(cfg: Config, agora_ms_: int) -> Optional[Dict[str, Any]]:
    """Seccao 10: estado.json do medidor com idade_ms; None (med=off) se ausente, com mais de sem_dados_s ou ligado = false.

    So leitura. O aviso no log repete-se no maximo de minuto a minuto.
    """
    motivo = ""
    doc: Optional[Dict[str, Any]] = None
    try:
        with open(cfg.estado_json, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        motivo = "estado.json nao existe ou nao se le"
    if doc is not None and not isinstance(doc, dict):
        doc, motivo = None, "estado.json nao e um objecto"
    if doc is not None:
        try:
            idade = agora_ms_ - interpretar_hora(str(doc.get("hora_utc") or ""), 0)
        except ValueError:
            idade = sys.maxsize
        if abs(idade) > cfg.sem_dados_ms:
            motivo = "estado.json tem %s" % (("%.0f s" % (idade / 1000.0)) if idade < sys.maxsize else "uma hora ilegivel")
            doc = None
        elif not doc.get("ligado"):
            motivo, doc = "o medidor esta sem ligacao a bolsa", None
        else:
            doc = dict(doc)
            doc["idade_ms"] = idade
    if doc is None and agora_ms_ - _AVISO_MEDIDOR["ms"] >= 60_000:
        _AVISO_MEDIDOR["ms"] = agora_ms_
        log.warning("Medidor: %s; os sinais escrevem-se na mesma mas nao vao ser medidos (med=off)", motivo)
    return doc


def custos_medidos(cfg: ConfigSinalizador, ativo: str) -> Tuple[float, float, int]:
    """(65) (c_W, c_L, n_aberturas) medidos no registo_fills.csv do medidor; defeitos de config.ini se n < 30.

    Por ordem de abertura do activo: custo = E + F + A_h + D (h = maior horizonte de fill),
    medias ponderadas pelo valor, separadas em taker e maker (as mesmas contas de
    medidor._ordens). c_W = custo taker (entrada) + custo maker (saida no alvo);
    c_L = 2 x custo taker. Sem ordens maker medidas, a saida maker vale a taxa maker.
    O funding esperado nao entra aqui: junta-se por sinal com (64).
    """
    p = cfg.p
    defeito = (p.c_w_bps, p.c_l_bps)
    fills = [x for x in _ler_csv(cfg.reg_fills) if x.get("ativo", "").upper() == ativo.upper()]
    if not fills:
        return defeito[0], defeito[1], 0
    ordens = [o for o in md._ordens(fills, cfg.horizontes_fill) if o["tipo"] == "abre"]
    n = len(ordens)
    if n < 30:
        return defeito[0], defeito[1], n
    h = max(cfg.horizontes_fill) if cfg.horizontes_fill else 0

    def custo(o: Dict[str, Any]) -> float:
        partes = [o["e"], o["f"], o["a"].get(h, NAN), o["d"]]
        return sum(x for x in partes if x == x)

    def media_pond(grupo: List[Dict[str, Any]]) -> float:
        pares = [(custo(o), o["valor"]) for o in grupo if o["valor"] == o["valor"] and o["valor"] > 0]
        tot = sum(v for _, v in pares)
        return sum(c * v for c, v in pares) / tot if tot > 0 else NAN
    taker = media_pond([o for o in ordens if o["taker"]])
    maker = media_pond([o for o in ordens if not o["taker"]])
    if taker != taker:
        taker = p.custo_taxa_entrada_bps + p.imp_defeito_bps
    if maker != maker:
        maker = cfg.taxa_maker
    return taker + maker, 2.0 * taker, n


# ==========================================================================
# Estado por activo (seccoes 4 a 8)
# ==========================================================================
def _sinal(x: float) -> int:
    if x != x:
        return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _hash_a_zeros(txt: Any) -> bool:
    """H5: hash com todos os digitos a zero (negocios de TWAP e de liquidacao)."""
    t = str(txt or "").strip().lower()
    if t.startswith("0x"):
        t = t[2:]
    return bool(t) and t.strip("0") == ""


class ActivoSinal:
    """Estado de um activo: ancora, ajuste, contexto, Parkinson, excursao, fluxo, baleias, trade virtual.

    fecho_1h recalcula o contexto so com velas de 1 h fechadas (seccao 4); fecho_15m
    actualiza a excursao e avalia G0 a G8 (seccao 5), devolvendo (Sinal, 'ok') ou (None,
    motivo da primeira condicao que falhou: 'disjuntor', 'sessao', 'G0'..'G8'); o detalhe
    fica em ultimo_detalhe. z_prev e o valor registado no fecho anterior (Excursao.z);
    A_ult e sigma_eq,ult sao os do ultimo fecho de 1 h. A vela corrente nunca entra na
    ancora, no ajuste nem no quantil empirico. Leituras omissas na especificacao, todas
    deterministas: N = min(n_max, velas de 1 h depois de 4 h_A de aquecimento da ancora);
    t_crit calibrado para N arredondado para baixo a multiplos de 24, para nao
    recalibrar a cada hora; sigma_eq, phi_c e H em vigor sao os do ultimo ajuste valido
    (o contexto fica VERMELHO entretanto); um atraso ainda nao medido nao falha G0;
    p_hat conta ganho = r_liq > 0 nos ultimos 200 trades fechados; o arrefecimento conta
    velas depois da vela do stop. O fim de uma vela e sempre t + 15 min (a Hyperliquid da
    T = t + intervalo - 1 ms): e por ele que se sabe se a vela fecha uma hora (funding) e a
    sessao da nota. Tudo o que se avalia no fecho k so usa o que chegou ate T_k: fluxo,
    liquidacoes e negocios grandes com time <= T_k, o ultimo activeAssetCtx recebido ate T_k
    (seccao 5.4). O impacto entra pelos custos c_W e c_L (defeito ou medidos), nunca pelo
    preco de saida do trade virtual (como no backtest): o stop preenche-se em P_stop. As
    colunas raj, twap e abs das velas sao independentes do sinal: lado da ultima rajada e do
    TWAP em {-1, 0, +1} e residuo (r_k - beta u_k)/s_e sem o factor lado; fuel e iman sao
    para lado_med = -sign(z_k), que numa vela de reentrada e o lado do sinal.
    """

    def __init__(self, nome: str, cfg: ConfigSinalizador, sz_decimals: int):
        p = cfg.p
        self.nome = nome
        self.cfg = cfg
        self.sz_decimals = sz_decimals
        self.lam_a = cfg.lam_a
        # 1 h: ancora, janela do ajuste, contexto
        self.ancora = rv.Ancora(p.h_a)
        self.aquecimento = int(math.ceil(4.0 * p.h_a))
        self.d_hist: Deque[float] = deque(maxlen=p.n_max)
        self.r_hist: Deque[float] = deque(maxlen=max(p.n_max, 2160))
        self.x_1h_ant = NAN
        self.x_abertura = NAN
        self.z_1h_hist: Deque[float] = deque(maxlen=p.n_z)
        self.aj = rv.ajustar_ar1([], self.lam_a)
        self.t_crit = p.t_nulo_max
        self._t_crit_cache: Dict[int, float] = {}
        self.ctx = rv.classificar_contexto(self.aj, NAN, NAN, NAN, False, self.t_crit, cfg.p_ctx)
        self.a_ult = NAN
        self.sigma_ult = NAN
        self.phi_ult = NAN
        self.h_ult = NAN
        self.sigma_r = NAN
        self.ctx_ms = 0
        self.z_in_ef = p.z_in
        self.q_z = NAN
        self.k_max = p.k_max_tecto
        self.vr_regime = 0
        # 15 m: volatilidade, excursao, absorcao
        self.vol = rv.VolParkinson(p.h_vc, p.h_vl)
        self.exc = rv.Excursao()
        self.x_15m_ant = NAN
        self.t_15m_ult = 0
        self.r15_hist: Deque[float] = deque(maxlen=96)
        self.u_hist: Deque[float] = deque(maxlen=96)
        self.ewma_vol = nu.Ewma(96)          # so com negocios reais (nunca no aquecimento, que nao os tem)
        self.pos_flx = nu.JanelaPercentil(672)
        self.n_exc = 0
        self.exc_entrada = -1
        self.p_min_exc = NAN
        self.p_max_exc = NAN
        self.liq_max_exc = NAN
        self.cvd = 0.0
        # negocios
        self.fluxo_15m = rv.FluxoAgressor(MS_15M)
        self.fluxo_1h = rv.FluxoAgressor(MS_1H)
        self.grandes = rv.NegociosGrandes(p.n_grandes_max, p.q_grande, MS_DIA)
        self.tids: Deque[int] = deque(maxlen=TIDS_VISTOS)
        self.tids_set: Set[int] = set()
        self.liquidados: Dict[str, int] = {}     # hash do agressor das rajadas -> T (partilhado pelo Sinalizador)
        self.atrasos: Deque[int] = deque(maxlen=200)
        self.exch_ms = 0
        self.ultimo_local_ms = 0
        self.preco_ult = NAN
        self.lado_por_tick = p.lado_por_tick    # H2 falhada: lado pelo tick (px >= ultimo preco = compra)
        self.negocios_24h: Dict[str, List[float]] = {}
        self.n_negocios = 0
        self.cortes: Deque[int] = deque(maxlen=500)
        # contexto do activo, funding, open interest
        self.ctx_act: Dict[str, float] = {}
        self.ctx_act_ms = 0
        self.ctx_hist: Deque[Tuple[int, Dict[str, float]]] = deque(maxlen=512)   # (ms local, ctx) para ler o ultimo ate T_k
        self.funding_hist: Deque[float] = deque(maxlen=720)
        self.funding_por_hora: Dict[int, float] = {}                             # fundingHistory: hora -> taxa
        self.funding_hora = -1
        self.max_leverage = NAN
        self.oi_hist: Deque[float] = deque(maxlen=8)
        self.pos_doi8 = nu.JanelaPercentil(2880)
        # liquidacoes (CoinGlass) e baleias
        self.liq_long = rv.JanelaSomaCausal(MS_15M)
        self.liq_short = rv.JanelaSomaCausal(MS_15M)
        self.posicoes_lista: List[rv.Posicao] = []
        self.posicoes_ms = 0
        self.pos_hist: Deque[float] = deque(maxlen=16)
        self.pos_dpos = nu.JanelaPercentil(672)
        # medidor, custos, trade virtual, registo
        self.medidor: Optional[Dict[str, Any]] = None
        self.custos: Tuple[float, float, int] = (p.c_w_bps, p.c_l_bps, 0)
        self.imp_bps = p.imp_defeito_bps
        self.trade: Optional[rv.TradeVirtual] = None
        self.trade_info: Dict[str, Any] = {}
        self.inval_motivo: Optional[str] = None
        self.arrefecimento = 0
        self.t_stop_ms = 0
        self.fechados: List[Dict[str, Any]] = []
        self.ids_fechados: Set[str] = set()
        self.stops_seguidos = 0
        self.registo: Optional[Registo] = None
        self.carteira: Dict[str, float] = {}
        self.n_sinal = 0
        self.parado: Optional[str] = None
        self.ultimo_motivo = ""
        self.ultimo_detalhe = ""
        self.ultima: Dict[str, Any] = {}
        self.ultima_nota: Dict[str, Any] = {}

    # ---- apoio ---------------------------------------------------------------
    def _t_crit_para(self, n: int) -> float:
        """(11) t_crit calibrado para rv.n_calibracao(N) (patamares de PASSO_T_CRIT, cache); t_nulo_max sem calibracao ou com N < n_min."""
        p = self.cfg.p
        n_cal = rv.n_calibracao(n, p.n_min, p.n_max)
        if not p.calibrar_nula or n_cal == 0:
            return p.t_nulo_max
        if n_cal not in self._t_crit_cache:
            self._t_crit_cache[n_cal] = rv.calibrar_t_crit(n_cal, self.lam_a, p.replicas_nula, p.alpha_nula,
                                                           p.semente_nula)
        return self._t_crit_cache[n_cal]

    def calibrar(self) -> float:
        """Calibra t_crit para a janela em vigor (chamado no arranque, fora do caminho critico)."""
        n = min(self.cfg.p.n_max, len(self.d_hist))
        self.t_crit = self._t_crit_para(n)
        return self.t_crit

    def oi_usd(self, ctx: Optional[Dict[str, float]] = None) -> float:
        """(33) OI em dolares do contexto dado (defeito: o ultimo recebido); nan sem openInterest ou markPx."""
        c = self.ctx_act if ctx is None else ctx
        oi, mark = c.get("openInterest", NAN), c.get("markPx", NAN)
        if oi == oi and mark == mark and oi >= 0.0 and mark > 0.0:
            return rv.oi_usd(oi, mark)
        return NAN

    def ctx_em(self, ms: int) -> Dict[str, float]:
        """Seccao 4.10: o ultimo activeAssetCtx recebido ate ms (relogio local); o mais antigo guardado se todos forem posteriores; vazio sem nenhum."""
        if not self.ctx_hist:
            return {}
        for t, c in reversed(self.ctx_hist):
            if t <= ms:
                return c
        return self.ctx_hist[0][1]

    def atraso_mediano(self) -> float:
        return nu.mediana(list(self.atrasos)) if self.atrasos else NAN

    def taxa_acerto(self) -> Tuple[float, int]:
        """(67) p_hat = ganhos / n nos ultimos 200 trades virtuais fechados (todos enquanto n < 200)."""
        ultimos = self.fechados[-200:]
        n = len(ultimos)
        if n == 0:
            return NAN, 0
        return sum(1 for f in ultimos if f["r_liq_bps"] > 0.0) / n, n

    def fase(self) -> str:
        return "cal" if len(self.fechados) < self.cfg.p_tam.n_cal else "op"

    def em_corte(self, agora_ms_: int, velas: int = 4) -> bool:
        limite = agora_ms_ - velas * MS_15M
        return any(c >= limite for c in self.cortes)

    # ---- entrada de dados --------------------------------------------------------
    def negocio(self, neg: Dict[str, Any], local_ms: int) -> None:
        """Um negocio do canal trades: fluxo (38), grandes (41), rajadas, TWAP, CVD (40), atraso (H8)."""
        px, sz, t = float(neg["px"]), float(neg["sz"]), int(neg["time"])
        if px <= 0.0 or sz <= 0.0:
            raise ValueError("negocio com preco ou tamanho nao positivo")
        tid = neg.get("tid")
        if tid is not None:                      # o lote reenviado ao religar repete negocios ja vistos
            try:
                tid = int(tid)
            except (TypeError, ValueError):
                tid = None
        if tid is not None:
            if tid in self.tids_set:
                return
            if len(self.tids) == self.tids.maxlen:
                self.tids_set.discard(self.tids[0])
            self.tids.append(tid)
            self.tids_set.add(tid)
        if self.lado_por_tick:
            s = 1 if (self.preco_ult != self.preco_ult or px >= self.preco_ult) else -1
        else:
            s = rv.lado_agressor(str(neg.get("side", "")))
        users = neg.get("users") or []
        agr, pas = rv.agressor(users, s)
        h_agr, h_pas = rv.hash_endereco(agr), rv.hash_endereco(pas)
        ntl = px * sz
        self.fluxo_15m.negocio(t, s, ntl)
        self.fluxo_1h.negocio(t, s, ntl)
        self.grandes.negocio(t, s, ntl, h_agr, h_pas, px, _hash_a_zeros(neg.get("hash")))
        self.cvd += s * ntl
        self.atrasos.append(local_ms - t)
        self.exch_ms = max(self.exch_ms, t)
        self.ultimo_local_ms = max(self.ultimo_local_ms, local_ms)
        self.preco_ult = px
        self.n_negocios += 1
        if h_agr:
            acc = self.negocios_24h.setdefault(h_agr, [0.0, 0.0])
            acc[0] += s * ntl
            acc[1] += ntl

    def contexto_activo(self, ctx: Dict[str, Any], agora_ms_: Optional[int] = None) -> None:
        """activeAssetCtx: funding, openInterest, premium, markPx, oraclePx, dayNtlVlm; funding por hora para (32)."""
        agora = agora_ms() if agora_ms_ is None else agora_ms_
        novo = {k: flt(ctx.get(k)) for k in ("funding", "openInterest", "premium", "dayNtlVlm")}
        for k in ("markPx", "oraclePx"):            # precos: so positivos e finitos contam
            v = positivo(ctx.get(k))
            novo[k] = v if v is not None else NAN
        self.ctx_act = novo
        self.ctx_act_ms = agora
        self.ctx_hist.append((agora, novo))
        self.ultimo_local_ms = max(self.ultimo_local_ms, agora)
        hora = agora // MS_1H
        if novo["funding"] == novo["funding"] and hora != self.funding_hora:
            self.funding_hora = hora
            self.funding_hist.append(novo["funding"])

    def funding_historico(self, linhas: List[Tuple[int, float]]) -> None:
        """Semeia o historico de funding por hora (fundingHistory) para a mediana e MAD de (32)."""
        for t, fr in sorted(linhas):
            self.funding_hist.append(fr)
            self.funding_hora = t // MS_1H
            self.funding_por_hora[t - t % MS_1H] = fr

    def liquidacao(self, it: Dict[str, Any], t_ms: int) -> None:
        """Uma liquidacao da CoinGlass (so com chave): soma por lado nos ultimos 15 min para (53)."""
        v = volume_liquidacao(it)
        if v != v:
            return
        lado = lado_liquidacao(it)
        if lado == 1:
            self.liq_long.juntar(t_ms, v)
        elif lado == 2:
            self.liq_short.juntar(t_ms, v)

    def posicoes(self, lista: Dict[str, List[rv.Posicao]], agora_ms_: Optional[int] = None) -> None:
        """Posicoes sondadas das baleias (por hash de endereco); guarda as deste activo para (50) a (52)."""
        self.posicoes_lista = [p for ps in lista.values() for p in ps if p.coin == self.nome]
        self.posicoes_ms = agora_ms() if agora_ms_ is None else agora_ms_

    def corte(self, t_ms: int) -> None:
        """Corte de ligacao ou silencio: conta para G0 nas 4 velas seguintes e reinicia o CVD."""
        self.cortes.append(int(t_ms))
        self.cvd = 0.0

    # ---- fecho de 1 h (seccao 4) ------------------------------------------------
    def fecho_1h(self, vela: Vela, completo: bool = True) -> rv.Contexto:
        """Contexto de 1 h so com velas fechadas: ancora (2), ajuste (6)-(14), VR (16)-(21), veto diario, (35).

        Com completo=False (aquecimento com o historico, excepto os ultimos fechos) salta o
        racio de variancias, o disjuntor de regime e a invalidacao, que so o fecho corrente usa.
        """
        p, cfg = self.cfg.p, self.cfg
        x = math.log(vela.c)
        if vela.t % MS_DIA == 0:
            self.x_abertura = math.log(vela.o)
        r = x - self.x_1h_ant if self.x_1h_ant == self.x_1h_ant else NAN
        self.x_1h_ant = x
        d = self.ancora.juntar(x)
        if self.ancora.n > self.aquecimento:
            self.d_hist.append(d)
        if r == r:
            self.r_hist.append(r)
        # NOTA ESPEC: "N = 720 de partida ... cresce ate 2160 com o historico em disco" nao diz como
        # cresce; com o candleSnapshot de 208 dias a janela ja tem n_max velas no arranque. Usa-se
        # N = min(n_max, velas fechadas depois do aquecimento da ancora), a mesma regra do backtest
        # (contexto_1h), e n_min valida o ajuste; n_ajuste nao altera N (ver ParametrosSinalizador).
        janela = list(self.d_hist)[-p.n_max:]
        n = len(janela)
        aj = rv.ajustar_ar1(janela, self.lam_a)
        n_cal = rv.n_calibracao(n, p.n_min, p.n_max)
        if not p.calibrar_nula or n_cal == 0:
            self.t_crit = p.t_nulo_max
        elif n_cal in self._t_crit_cache:        # calibrado por calibrar(); fora do fecho nunca se simula
            self.t_crit = self._t_crit_cache[n_cal]
        rs = list(self.r_hist)[-n:] if n else []
        self.sigma_r = rv.sigma_retorno(rs)
        if completo and len(rs) >= 2 * p.q_vr:
            vr, zvr = rv.racio_variancias(rs, p.q_vr)
        else:
            vr, zvr = NAN, NAN
        veto = rv.veto_dia(x, self.x_abertura, self.sigma_r, p.k_dia)
        ctx = rv.classificar_contexto(aj, vr, zvr, self.vol.vol_razao, veto, self.t_crit, cfg.p_ctx)
        a_antes = self.a_ult
        self.a_ult = self.ancora.valor
        if aj.valido:
            self.sigma_ult, self.phi_ult, self.h_ult = aj.sigma_eq, aj.phi_c, aj.meia_vida
            self.k_max = rv.k_maximo(aj.meia_vida, p.k_h, p.k_max_tecto)
        # (30)(31): quantil so com as velas anteriores a esta, e so com min(n_z, n_min) valores (regra partilhada)
        self.q_z = (rv.quantil_abs_z(list(self.z_1h_hist))
                    if len(self.z_1h_hist) >= rv.n_min_quantil_z(p.n_z, p.n_min) else NAN)
        self.z_in_ef = rv.z_in_efectivo(p.z_in, self.q_z, p.z_in_emp_max)
        z_t = rv.z_score(x, self.a_ult, self.sigma_ult)
        if z_t == z_t:
            self.z_1h_hist.append(z_t)
        self.aj, self.ctx, self.ctx_ms = aj, ctx, vela.T
        # disjuntor de regime: VR(8) sobre 2160 velas > 1 em 3 reestimacoes seguidas
        if completo and len(self.r_hist) >= 2160:
            vr_l, _ = rv.racio_variancias(list(self.r_hist)[-2160:], p.q_vr)
            self.vr_regime = self.vr_regime + 1 if vr_l == vr_l and vr_l > 1.0 else 0
            if self.vr_regime >= 3 and self.parado is None:
                self.parado = "regime"
                log.warning("%s: disjuntor de regime (VR sobre 2160 velas > 1 em 3 reestimacoes); activo desligado "
                            "ate 'sinalizador.py reset'", self.nome)
        if self.parado == "seguidas" and ctx.estado == rv.VERDE:
            self.parado, self.stops_seguidos = None, 0
            log.info("%s: fecho de 1 h VERDE levanta o disjuntor de stops seguidos", self.nome)
        # (60) invalidacao de um trade aberto, executada no fecho de 15 m seguinte
        if completo and self.trade is not None and self.inval_motivo is None:
            info = self.trade_info
            desloc = NAN
            a_0, sigma_0 = flt(info.get("A_0")), flt(info.get("sigma_0"))     # do estado em JSON, null vira None
            if a_antes == a_antes and a_0 == a_0 and sigma_0 == sigma_0 and sigma_0 > 0.0:
                # (60) o alvo e a banda z_out do mesmo lado (preco_alvo): a ancora a afastar-se do lado do
                # trade (a descer numa compra) encurta o destino; deslocacao contra = -lado (A_t - A_0)/sigma_0
                desloc = -self.trade.lado * (self.a_ult - a_0) / sigma_0
            self.inval_motivo = rv.invalidar(aj.t_nulo, vr, zvr, z_t, desloc, cfg.p_ctx)
        return ctx

    # ---- fecho de 15 m (seccoes 5 a 8) ------------------------------------------
    def fecho_15m(self, vela: Vela, agora_ms_: int, avaliar: bool = True) -> Tuple[Optional[rv.Sinal], str]:
        """Gatilho no fecho k: actualiza Parkinson, z_k (36), a excursao (5.2) e as medidas de fluxo; avalia G0 a G8.

        Devolve (Sinal, 'ok') ou (None, motivo). Com avaliar=False (aquecimento com o
        historico) so actualiza o estado e devolve (None, 'aquecimento'). Em fase sombra
        abre o trade virtual mas nao devolve sinal (motivo 'G8').
        """
        p, cfg = self.cfg.p, self.cfg
        T = vela.T
        x = math.log(vela.c)
        r_k = x - self.x_15m_ant if self.x_15m_ant == self.x_15m_ant else NAN
        self.x_15m_ant = x
        self.t_15m_ult = T
        self.vol.juntar(vela.h, vela.l)
        sigma_15, vol_razao = self.vol.sigma_15, self.vol.vol_razao
        z_prev, estado_prev = self.exc.z, self.exc.estado
        ctx_k = self.ctx_em(T)                        # 4.10: o ultimo activeAssetCtx recebido ate T_k
        oi = self.oi_usd(ctx_k)
        z_k = rv.z_score(x, self.a_ult, self.sigma_ult)
        self.exc.actualizar(z_k, self.z_in_ef, oi)
        if self.exc.estado == rv.FORA and estado_prev != rv.FORA:
            self.n_exc += 1
            self.p_min_exc, self.p_max_exc, self.liq_max_exc = vela.l, vela.h, 0.0
        elif self.exc.estado == rv.FORA:
            self.p_min_exc, self.p_max_exc = min(self.p_min_exc, vela.l), max(self.p_max_exc, vela.h)
        lado_cand = -_sinal(z_k)
        lado_med = lado_cand or 1
        # fluxo e medidas de baleias no fecho (seccao 6); as janelas terminam em T_k (5.4)
        v_b, v_a = self.fluxo_15m.v_b(T), self.fluxo_15m.v_a(T)
        den = self.ewma_vol.valor
        u_k = (v_b - v_a) / den if den == den and den > 0.0 else NAN
        flx = self.grandes.flx(T)
        pos_flx = self.pos_flx.posicao(abs(flx)) if flx == flx else NAN
        rep = self.grandes.repetidos(T, cfg.excluidos_hash)
        funding = ctx_k.get("funding", NAN)
        premio = ctx_k.get("premium", NAN)
        mark, oraculo = ctx_k.get("markPx", NAN), ctx_k.get("oraclePx", NAN)
        pm = rv.premio_bps(mark, oraculo) if mark == mark and oraculo == oraculo and oraculo > 0.0 else NAN   # (34)
        z_f = rv.z_robusto(funding, list(self.funding_hist))
        doi_exc = oi / self.exc.oi_inicio - 1.0 if oi == oi and self.exc.oi_inicio == self.exc.oi_inicio and self.exc.oi_inicio > 0 else NAN
        pos_doi8 = NAN
        if oi == oi and oi > 0.0:
            if len(self.oi_hist) >= 8:
                val = math.log(oi / max(self.oi_hist))
                pos_doi8 = self.pos_doi8.posicao(val) if len(self.pos_doi8) >= 960 else NAN
                self.pos_doi8.juntar(val)
            self.oi_hist.append(oi)
        if cfg.cg_chave:
            contra = self.liq_long.soma(T) if lado_med > 0 else self.liq_short.soma(T)
            if self.exc.estado == rv.FORA or self.exc.terminou:
                self.liq_max_exc = max(self.liq_max_exc if self.liq_max_exc == self.liq_max_exc else 0.0, contra)
            liq = rv.racio_liquidacoes(contra, self.liq_max_exc)
        else:
            liq = NAN
        if self.posicoes_lista:
            pos, fuel_oi, iman = rv.posicoes_baleias(self.posicoes_lista, vela.c, lado_med, cfg.p_bal.pct_fuel, oi,
                                                     FAIXA_IMAN_BPS, self.p_min_exc, self.p_max_exc)
        else:
            pos, fuel_oi, iman = NAN, NAN, 0
        dpos = pos - self.pos_hist[0] if pos == pos and len(self.pos_hist) == 16 else NAN
        pos_dpos = self.pos_dpos.posicao(abs(dpos) / oi) if dpos == dpos and oi == oi and oi > 0 else NAN
        if dpos == dpos and oi == oi and oi > 0:
            self.pos_dpos.juntar(abs(dpos) / oi)
        if pos == pos:
            self.pos_hist.append(pos)
        # colunas independentes do sinal: lado da ultima rajada e do TWAP, residuo sem o factor lado
        s_raj = self.grandes.lado_rajada(T, p.rajada_silencio_s, p.rajada_dt_ms, p.rajada_amp_bps)
        s_twap, twapsrc = self.grandes.twap(T, 1, cfg.p_twap)
        for raj_ms, _, h in self.grandes.rajadas(T, (T - vela.t) / 1000.0, p.rajada_dt_ms, p.rajada_amp_bps):
            if h and raj_ms > vela.t:             # 6.5: agressor de uma rajada = liquidado nos ultimos 7 dias
                self.liquidados[h] = T
        hhi = NAN
        if self.a_ult == self.a_ult and self.sigma_ult == self.sigma_ult and self.sigma_ult > 0:
            p_inf, p_sup = rv.niveis(self.a_ult, self.sigma_ult, self.z_in_ef)
            hhi = self.grandes.hhi_passivo(p_inf, p_sup, vela.t, T)
        _, _, a_abs = rv.absorcao_residual(list(self.r15_hist), list(self.u_hist), r_k, u_k, 1)
        atraso = self.atraso_mediano()
        corte_rec = self.em_corte(agora_ms_)
        self.ultima = {"v_b": v_b, "v_a": v_a, "n_grandes": self.grandes.n_grandes(vela.t, T), "flx": flx, "rep": rep,
                       "abs": a_abs, "hhi": hhi, "raj": s_raj, "twap": s_twap, "oi_usd": oi, "funding": funding,
                       "premium": premio,
                       "liq_long": self.liq_long.soma(T) if cfg.cg_chave else NAN,
                       "liq_short": self.liq_short.soma(T) if cfg.cg_chave else NAN,
                       "pos": pos, "fuel": fuel_oi, "iman": iman, "atraso_ms": atraso, "corte": int(corte_rec)}
        sinal: Optional[rv.Sinal] = None
        motivo, detalhe = "aquecimento", ""
        if avaliar:
            sinal, motivo, detalhe = self._avaliar(vela, agora_ms_, x, r_k, z_k, z_prev, estado_prev, sigma_15, vol_razao,
                                                   oi, u_k, flx, pos_flx, rep, z_f, doi_exc, pos_doi8, liq, pm, pos,
                                                   dpos, pos_dpos, fuel_oi, iman, hhi, atraso, corte_rec, ctx_k)
            self.ultimo_motivo, self.ultimo_detalhe = motivo, detalhe
        if self.arrefecimento > 0 and vela.t > self.t_stop_ms:    # conta velas depois da vela do stop
            self.arrefecimento -= 1
        # historicos para a vela seguinte (as 96 velas ANTERIORES)
        if r_k == r_k and u_k == u_k:
            self.r15_hist.append(r_k)
            self.u_hist.append(u_k)
        if avaliar:                                # (45) EWMA_96 do volume so com negocios reais: no aquecimento
            self.ewma_vol.juntar(v_b + v_a)        # nao ha negocios e um zero por vela saturava a correccao de arranque
        if flx == flx:
            self.pos_flx.juntar(abs(flx))
        return sinal, motivo

    def _avaliar(self, vela: Vela, agora_ms_: int, x: float, r_k: float, z_k: float, z_prev: float, estado_prev: str,
                 sigma_15: float, vol_razao: float, oi: float, u_k: float, flx: float, pos_flx: float, rep: int,
                 z_f: float, doi_exc: float, pos_doi8: float, liq: float, pm: float, pos: float, dpos: float,
                 pos_dpos: float, fuel_oi: float, iman: int, hhi: float, atraso: float,
                 corte_rec: bool, ctx_k: Optional[Dict[str, float]] = None) -> Tuple[Optional[rv.Sinal], str, str]:
        """G0 a G8 por ordem; devolve (sinal ou None, motivo, detalhe)."""
        p, cfg = self.cfg.p, self.cfg
        T = vela.T
        if ctx_k is None:
            ctx_k = self.ctx_em(T)
        ses, dow, hr = rv.sessao_utc(vela.t + MS_15M)           # fim da vela pela abertura (T e t + 15 min - 1)
        if self.parado:
            return None, "disjuntor", self.parado
        if ses in p.veto_sessao:
            return None, "sessao", ses
        if corte_rec:
            return None, "G0", "corte nas ultimas 4 velas"
        if self.ctx_ms == 0 or T - self.ctx_ms > p.idade_ctx_max_min * 60_000:
            return None, "G0", "contexto de 1 h com idade %.0f min" % ((T - self.ctx_ms) / 60_000.0)
        if atraso == atraso and atraso >= p.atraso_max_ms:
            return None, "G0", "atraso %.0f ms" % atraso
        if vela.n < 1:
            return None, "G0", "vela sem negocios"
        if self.ctx.estado != rv.VERDE:
            return None, "G1", "%s %s" % (self.ctx.estado, " ".join(self.ctx.motivos))
        lado, mot = rv.gatilho_reentrada(estado_prev, self.exc.lado_exc, self.exc.ext, self.exc.k_fora, self.k_max,
                                         z_prev, z_k, self.z_in_ef, r_k, sigma_15, vol_razao, cfg.p_gat)
        if lado == 0:
            return None, mot, "z=%.3f z_prev=%.3f ext=%.3f k_fora=%d" % (z_k, z_prev, self.exc.ext, self.exc.k_fora)
        # medidas que dependem do lado (seccao 6)
        raj = self.grandes.rajada_contra(T, lado, p.rajada_silencio_s, p.rajada_dt_ms, p.rajada_amp_bps)
        twap, twapsrc = self.grandes.twap(T, lado, cfg.p_twap)
        _, _, a_k = rv.absorcao_residual(list(self.r15_hist), list(self.u_hist), r_k, u_k, lado)
        if self.posicoes_lista:
            pos, fuel_oi, iman = rv.posicoes_baleias(self.posicoes_lista, vela.c, lado, cfg.p_bal.pct_fuel, oi,
                                                     FAIXA_IMAN_BPS, self.p_min_exc, self.p_max_exc)
        if cfg.cg_chave:
            contra = self.liq_long.soma(T) if lado > 0 else self.liq_short.soma(T)
            liq = rv.racio_liquidacoes(contra, self.liq_max_exc)
        b, termos = rv.pontuar_baleias(lado, flx, pos_flx, rep, z_f, doi_exc, liq, dpos, pos_dpos, twap, a_k, u_k,
                                       pos_doi8, cfg.p_bal)
        f_b = rv.factor_tamanho(b, fuel_oi, cfg.p_bal)
        if raj:
            return None, "G6", "rajada de liquidacao contra ha menos de %.0f s" % p.rajada_silencio_s
        if b <= cfg.p_bal.b_veto:
            return None, "G6", "B_k = %d" % b
        if self.trade is not None:
            return None, "G7", "trade virtual aberto"
        if self.arrefecimento > 0:
            return None, "G7", "arrefecimento (%d velas)" % self.arrefecimento
        if self.exc_entrada == self.n_exc:
            return None, "G7", "ja houve entrada nesta excursao"
        # geometria (seccao 7) e tamanho (seccao 8)
        if abs(z_k) >= p.z_stop or not (self.h_ult == self.h_ult and self.h_ult > 0):
            return None, "G8", "|z| >= z_stop ou meia-vida desconhecida"
        d0 = x - self.a_ult
        tau_max, tmax_15 = rv.tempo_maximo(self.h_ult, p.n_h, p.tmax_h)
        g = rv.alvo_bps(d0, self.sigma_ult, self.phi_ult, self.lam_a, tau_max, cfg.p_gat.z_out)
        l_bps = rv.stop_bps(z_k, self.sigma_ult, p.z_stop)
        funding = ctx_k.get("funding", NAN)
        f_esp = rv.funding_esperado_bps(lado, funding if funding == funding else 0.0, tau_max)
        c_w, c_l = self.custos[0] + f_esp, self.custos[1] + f_esp
        if g != g or l_bps <= 0.0 or l_bps + c_l <= 0.0:
            return None, "G8", "geometria impossivel"
        p_hat, n = self.taxa_acerto()
        qmax = NAN
        med = self.medidor
        if med and isinstance(med.get("ativos"), dict) and isinstance(med["ativos"].get(self.nome), dict):
            u = med["ativos"][self.nome]
            qmax = flt(u.get("qmax_usd_compra" if lado > 0 else "qmax_usd_venda"))
            imp_med = flt(u.get("imp_ref_bps"))
            if imp_med == imp_med and imp_med >= 0.0:
                self.imp_bps = imp_med
        tam = rv.dimensionar(p.capital_usd, g, l_bps, c_w, c_l, p_hat, n, self.sigma_r, f_b,
                             ctx_k.get("dayNtlVlm", NAN), qmax, cfg.p_tam)
        expo = sum(v for k, v in self.carteira.items() if k != self.nome)
        if tam.usd > 0.0 and expo + tam.usd > p.expo_max * p.capital_usd:
            return None, "G7", "exposicao %.0f + %.0f > %.0f usd" % (expo, tam.usd, p.expo_max * p.capital_usd)
        if g < p.g_min_x_custo * c_l:
            return None, "G8", "G = %.1f < %.1f bps" % (g, p.g_min_x_custo * c_l)
        try:
            p_teo = rv.prob_alvo_antes_stop(z_k, cfg.p_gat.z_out, p.z_stop)
        except ValueError:
            p_teo = NAN
        p_stop = rv.preco_stop(self.a_ult, self.sigma_ult, lado, p.z_stop)
        p_alvo = rv.preco_alvo(self.a_ult, self.sigma_ult, lado, cfg.p_gat.z_out)   # banda z_out do mesmo lado
        lev = (rv.alavancagem_maxima(l_bps, self.max_leverage)
               if self.max_leverage == self.max_leverage and self.max_leverage > 0.0 else NAN)   # seccao 8.6
        campos: Dict[str, Any] = {
            "var": cfg.variante, "fase": tam.fase, "z0": z_k, "zp": z_prev, "ext": self.exc.ext, "kf": self.exc.k_fora,
            "H": self.h_ult, "phi": self.phi_ult, "t": self.aj.t_nulo, "tc": self.t_crit, "vr8": self.ctx.vr,
            "zin": self.z_in_ef, "sig": self.sigma_ult, "G": g, "L": l_bps, "tmax": tmax_15, "lev": lev, "P": p_teo,
            "pst": p_stop,
            "ofi": self.fluxo_15m.ofi(T), "flx": flx, "rep": rep, "abs": a_k, "fz": z_f, "doi": doi_exc,
            "doi8": pos_doi8, "pm": pm, "liq": liq, "liqsrc": cfg.liqsrc if liq == liq else "na", "raj": int(raj),
            "twap": twap, "twapsrc": twapsrc, "pos": pos, "dpos": dpos, "fuel": fuel_oi, "iman": iman, "hhi": hhi,
            "bal": b, "fb": f_b, "cap": tam.cap, "ses": ses, "dow": dow, "hr": hr, "med": bool(med),
            "ctx": self.ctx.estado}
        nota = rv.nota_sinal(campos)
        self.ultima_nota = dict(campos, termos=termos)
        if tam.fase == "sombra" or tam.usd < cfg.minimo_ordem_usd:
            self._abrir_trade(vela, agora_ms_, lado, p_alvo, p_stop, tmax_15, g, l_bps, tam.fase, f_b, 0.0, nota, z_k)
            return None, "G8", "fase sombra" if tam.fase == "sombra" else "tamanho %.0f < minimo" % tam.usd
        sinal = rv.Sinal(agora_ms_, self.nome, lado, vela.c, g, tam.usd, nota)
        self._abrir_trade(vela, agora_ms_, lado, p_alvo, p_stop, tmax_15, g, l_bps, tam.fase, f_b, tam.usd, nota, z_k)
        return sinal, "ok", "B=%d f_B=%.2f %s" % (b, f_b, tam.cap)

    # ---- trade virtual (seccao 7.5) ----------------------------------------------
    def _abrir_trade(self, vela: Vela, agora_ms_: int, lado: int, p_alvo: float, p_stop: float, tmax_15: int,
                     g: float, l_bps: float, fase: str, f_b: float, tamanho: float, nota: str, z_0: float) -> None:
        self.n_sinal += 1
        ident = "%s-%s-%03d" % (self.nome, datetime.fromtimestamp(agora_ms_ / 1000.0, tz=timezone.utc).strftime("%y%m%d-%H%M%S"),
                                self.n_sinal % 1000)
        # o deslize do stop ja esta em c_L (taker + imp na saida, (65)): o trade virtual preenche em P_stop,
        # como o backtest, para o impacto nao contar duas vezes
        self.trade = rv.TradeVirtual(lado, vela.c, p_alvo, p_stop, tmax_15, 0.0)
        self.trade_info = {"id": ident, "hora": iso_utc(agora_ms_), "hora_ms": agora_ms_, "ativo": self.nome, "lado": lado,
                           "preco": vela.c, "A_0": self.a_ult, "sigma_0": self.sigma_ult, "phi_0": self.phi_ult,
                           "H_0": self.h_ult, "G": g, "L": l_bps, "tmax_15": tmax_15, "fase": fase, "f_B": f_b,
                           "nota": nota, "tamanho_usd": tamanho, "z_0": z_0, "p_alvo": p_alvo, "p_stop": p_stop}
        self.inval_motivo = None
        self.exc_entrada = self.n_exc
        self.carteira[self.nome] = tamanho

    def restaurar_trade(self, info: Dict[str, Any]) -> None:
        """Repoe um trade virtual aberto a partir do estado_sinalizador.json (rearranque); ValueError se o id ja esta no registo (fechado)."""
        if str(info.get("id", "")) in self.ids_fechados:
            raise ValueError("o trade %s ja esta fechado em registo_sinalizador.csv" % info.get("id"))
        tv = rv.TradeVirtual(int(info["lado"]), float(info["preco"]), float(info["p_alvo"]), float(info["p_stop"]),
                             int(info["tmax_15"]), 0.0)
        tv.velas = int(info.get("velas", 0))
        tv.funding_bps = float(info.get("funding_bps", 0.0))
        tv._mae = float(info.get("mae_bps", 0.0))
        tv._mfe = float(info.get("mfe_bps", 0.0))
        self.trade = tv
        self.trade_info = {k: v for k, v in info.items() if k not in ("velas", "funding_bps", "mae_bps", "mfe_bps", "imp_bps")}
        self.carteira[self.nome] = float(info.get("tamanho_usd", 0.0))
        self.exc_entrada = self.n_exc

    def avancar_trade_virtual(self, vela: Vela) -> Optional[Dict[str, Any]]:
        """Seccao 7.5: avanca o trade virtual no fecho de 15 m; devolve a linha do registo quando fecha."""
        tv = self.trade
        if tv is None:
            return None
        f_hora = 0.0
        fim = vela.t + MS_15M                       # fim pela abertura: T e t + 15 min - 1 na Hyperliquid
        if fim % MS_1H == 0:                         # esta vela fecha uma hora: funding a quem esta aberto
            f = self.ctx_em(fim).get("funding", NAN)
            if f != f:
                f = self.funding_por_hora.get(fim, NAN)   # replay no rearranque: fundingHistory em disco
            f_hora = f if f == f else 0.0
        if self.inval_motivo is not None:
            saida = tv.invalidar("invalidacao", vela.c, vela.T)
        else:
            saida = tv.avancar(vela, NAN, NAN, f_hora)
        if saida is None:
            return None
        return self._fechar_trade(saida, vela.t)

    def _fechar_trade(self, saida: rv.Saida, t_vela: int = 0) -> Dict[str, Any]:
        """Escreve a linha do registo e actualiza p_hat, stops seguidos e o arrefecimento (t_vela = inicio da vela da saida)."""
        info, tv = self.trade_info, self.trade
        assert tv is not None
        custo = self.custos[0] if saida.motivo == "alvo" else self.custos[1]
        r_bruto, r_liq = rv.resultado_trade(tv.lado, tv.p_entrada, saida.preco, saida.funding_bps, custo)
        linha = {"id": info.get("id", ""), "hora": info.get("hora", ""), "ativo": self.nome,
                 "lado": "compra" if tv.lado > 0 else "venda", "preco": num(tv.p_entrada, 8),
                 "A_0": num(info.get("A_0"), 8), "sigma_0": num(info.get("sigma_0"), 8), "phi_0": num(info.get("phi_0"), 6),
                 "H_0": num(info.get("H_0"), 3), "G": num(info.get("G"), 2), "L": num(info.get("L"), 2),
                 "tmax_15": info.get("tmax_15", ""), "fase": info.get("fase", ""), "f_B": num(info.get("f_B"), 2),
                 "hora_saida": iso_utc(saida.t_ms) if saida.t_ms else "", "preco_saida": num(saida.preco, 8),
                 "motivo": saida.motivo, "velas": saida.velas, "mae_bps": num(saida.mae_bps, 2),
                 "mfe_bps": num(saida.mfe_bps, 2), "r_bruto_bps": num(r_bruto, 2),
                 "funding_bps": num(saida.funding_bps, 3), "r_liq_bps": num(r_liq, 2), "nota": info.get("nota", "")}
        if self.registo is not None:
            self.registo.escrever(linha)
        self.fechados.append({"t_ms": saida.t_ms, "r_liq_bps": r_liq, "motivo": saida.motivo,
                              "G": flt(info.get("G")), "L": flt(info.get("L")), "fase": info.get("fase", ""),
                              "z_0": flt(info.get("z_0"))})
        self.ids_fechados.add(str(linha["id"]))
        if saida.motivo == "stop":
            self.stops_seguidos += 1
            self.arrefecimento = self.cfg.p.arrefecimento_velas
            self.t_stop_ms = t_vela if t_vela else saida.t_ms - (saida.t_ms % MS_15M or MS_15M)
        else:
            self.stops_seguidos = 0
        self.carteira.pop(self.nome, None)
        self.trade, self.trade_info, self.inval_motivo = None, {}, None
        log.info("%s: trade virtual %s fechado por %s: r_bruto %.1f bps, funding %.1f, r_liq %.1f (%d velas)",
                 self.nome, linha["id"], saida.motivo, r_bruto, saida.funding_bps, r_liq, saida.velas)
        return linha

    def carregar_fechados(self, linhas: List[Dict[str, str]]) -> None:
        """Reconstroi p_hat, stops seguidos e o historico de r_liq a partir do registo proprio."""
        for ln in linhas:
            if ln.get("ativo") != self.nome or ln.get("r_liq_bps", "") == "":
                continue
            try:
                t_ms = interpretar_hora(ln.get("hora_saida") or ln.get("hora") or "", 0)
            except ValueError:
                t_ms = 0
            self.fechados.append({"t_ms": t_ms, "r_liq_bps": flt(ln.get("r_liq_bps")), "motivo": ln.get("motivo", ""),
                                  "G": flt(ln.get("G")), "L": flt(ln.get("L")), "fase": ln.get("fase", ""), "z_0": NAN})
            if ln.get("id"):
                self.ids_fechados.add(str(ln["id"]))
        seguidos = 0
        for f in reversed(self.fechados):
            if f["motivo"] != "stop":
                break
            seguidos += 1
        self.stops_seguidos = seguidos

    def estado(self) -> Dict[str, Any]:
        """Resumo para estado_sinalizador.json."""
        aj, ctx = self.aj, self.ctx
        tv = self.trade
        trade: Optional[Dict[str, Any]] = None
        if tv is not None:
            trade = dict(self.trade_info)
            trade.update({"velas": tv.velas, "funding_bps": tv.funding_bps, "mae_bps": tv.mae_bps,
                          "mfe_bps": tv.mfe_bps, "imp_bps": tv.imp_bps})
        p_hat, n = self.taxa_acerto()
        doc = {"contexto": ctx.estado, "motivos": list(ctx.motivos), "t_nulo": aj.t_nulo, "t_crit": self.t_crit,
               "phi_c": aj.phi_c, "meia_vida": aj.meia_vida, "sigma_eq": aj.sigma_eq, "n_ajuste": aj.n,
               "vr": ctx.vr, "z_vr": ctx.z_vr, "vol_razao": ctx.vol_razao, "veto_dia": ctx.veto_dia,
               "sigma_r": self.sigma_r, "A": self.a_ult, "A_ult": self.a_ult, "sigma_ult": self.sigma_ult,
               "z": self.exc.z, "z_in_ef": self.z_in_ef, "q_z": self.q_z, "k_max": self.k_max,
               "excursao": self.exc.estado, "ext": self.exc.ext, "k_fora": self.exc.k_fora, "lado_exc": self.exc.lado_exc,
               "ctx_ms": self.ctx_ms, "t_15m_ult": self.t_15m_ult, "fase": self.fase(), "n_fechados": len(self.fechados),
               "p_hat": p_hat, "p_inf": rv.wilson_inferior(p_hat, n), "stops_seguidos": self.stops_seguidos,
               "arrefecimento": self.arrefecimento, "parado": self.parado, "ultimo_motivo": self.ultimo_motivo,
               "ultimo_detalhe": self.ultimo_detalhe, "atraso_ms": self.atraso_mediano(), "n_negocios": self.n_negocios,
               "funding": self.ctx_act.get("funding", NAN), "oi_usd": self.oi_usd(), "posicoes": len(self.posicoes_lista),
               "custos": list(self.custos), "imp_bps": self.imp_bps, "trade": trade,
               "ultima": dict(self.ultima), "nota": {k: v for k, v in self.ultima_nota.items() if k != "termos"}}
        return {k: _limpo(v) for k, v in doc.items()}


# ==========================================================================
# Baleias (seccao 6.5): lista diaria e sonda com orcamento de peso
# ==========================================================================
class OrcamentoPeso:
    """Peso gasto nos ultimos 60 s; usar(peso) espera (em thread) ate caber abaixo do maximo por minuto."""

    def __init__(self, maximo_por_minuto: int = PESO_MINUTO_MAX):
        self.maximo = maximo_por_minuto
        self._gasto: Deque[Tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def gasto(self) -> int:
        agora = time.monotonic()
        with self._lock:
            while self._gasto and agora - self._gasto[0][0] > 60.0:
                self._gasto.popleft()
            return sum(p for _, p in self._gasto)

    def usar(self, peso: int) -> None:
        while True:
            agora = time.monotonic()
            with self._lock:
                while self._gasto and agora - self._gasto[0][0] > 60.0:
                    self._gasto.popleft()
                total = sum(p for _, p in self._gasto)
                if total + peso <= self.maximo or not self._gasto:
                    self._gasto.append((agora, peso))
                    return
                espera = 60.0 - (agora - self._gasto[0][0]) + 0.05
            time.sleep(max(0.05, min(espera, 60.0)))


def _janela_leaderboard(linha: Dict[str, Any], nome: str) -> Dict[str, float]:
    """(roi, pnl, vlm) da janela `nome` de uma linha do leaderboard; aceita lista de pares, lista de dicionarios ou dicionario."""
    wp = linha.get("windowPerformances")
    item: Any = None
    if isinstance(wp, dict):
        item = wp.get(nome)
    elif isinstance(wp, list):
        for el in wp:
            if isinstance(el, (list, tuple)) and len(el) >= 2 and str(el[0]) == nome:
                item = el[1]
                break
            if isinstance(el, dict) and str(el.get("window") or el.get("name") or "") == nome:
                item = el
                break
    if not isinstance(item, dict):
        return {"roi": NAN, "pnl": NAN, "vlm": NAN}
    return {k: flt(item.get(k)) for k in ("roi", "pnl", "vlm")}


def carregar_baleias(caminho: str) -> List[str]:
    """Hashes (sha256[:10]) da lista diaria em dados/baleias.csv; lista vazia se nao existir. O ficheiro nunca tem o endereco inteiro."""
    return [ln.get("hash", "") for ln in _ler_csv(caminho) if ln.get("hash")]


def construir_baleias(cfg: ConfigSinalizador, leaderboard: List[Dict[str, Any]],
                      negocios_24h: Dict[str, Tuple[float, float]], liquidados_7d: Set[str],
                      excluidos: Set[str]) -> List[str]:
    """Seccao 6.5: enderecos (inteiros, so em memoria) que passam os filtros, por accountValue decrescente, ate baleias_max.

    Filtros: accountValue >= baleia_valor_min_usd; roi > 0 em week e month; vlm_month /
    accountValue <= baleia_vlm_racio_max; para os enderecos vistos nos negocios (chave =
    hash), |liquido| / bruto >= baleia_direcc_min; sem liquidacao nos ultimos 7 dias
    (liquidados_7d, hashes); fora de excluidos (enderecos ou hashes). As subcontas nao
    se agrupam: o leaderboard nao traz subAccounts e cada endereco conta por si.
    """
    # NOTA ESPEC: a seccao 6.5 pede "subcontas agrupadas por subAccounts", mas o leaderboard nao
    # traz esse campo e subAccounts e um pedido autenticado por utilizador; cada endereco conta por si.
    # NOTA ESPEC: liquidados_7d vem so das rajadas proprias detectadas (47), por hash do agressor; o
    # /api/hyperliquid/whale-alert da CoinGlass nao e pedido (seria mais um pedido REST com chave por
    # ciclo e o seu esquema nao esta confirmado): com chave a degradacao e a mesma de sem chave.
    p = cfg.p
    excl = {e.lower() for e in excluidos} | {rv.hash_endereco(e) for e in excluidos}
    cand: List[Tuple[float, str]] = []
    for ln in leaderboard or []:
        if not isinstance(ln, dict):
            continue
        end = str(ln.get("ethAddress") or ln.get("address") or ln.get("user") or "").strip().lower()
        if not (end.startswith("0x") and len(end) == 42):
            continue
        h = rv.hash_endereco(end)
        if end in excl or h in excl or h in liquidados_7d:
            continue
        valor = flt(ln.get("accountValue"))
        if not (valor == valor and valor >= p.baleia_valor_min_usd):
            continue
        semana, mes = _janela_leaderboard(ln, "week"), _janela_leaderboard(ln, "month")
        if not (semana["roi"] > 0.0 and mes["roi"] > 0.0):
            continue
        if mes["vlm"] == mes["vlm"] and valor > 0.0 and mes["vlm"] / valor > p.baleia_vlm_racio_max:
            continue
        visto = negocios_24h.get(h)
        if visto and visto[1] > 0.0 and abs(visto[0]) / visto[1] < p.baleia_direcc_min:
            continue
        cand.append((valor, end))
    cand.sort(key=lambda x: (-x[0], x[1]))
    return [e for _, e in cand[:max(0, p.baleias_max)]]


def guardar_baleias(caminho: str, leaderboard: List[Dict[str, Any]], enderecos: List[str], agora_ms_: int) -> None:
    """Escreve dados/baleias.csv com prefixo (10 caracteres) e hash de cada endereco escolhido; nunca o endereco inteiro."""
    por_end = {str(ln.get("ethAddress") or ln.get("address") or ln.get("user") or "").strip().lower(): ln
               for ln in (leaderboard or []) if isinstance(ln, dict)}
    linhas = []
    for e in enderecos:
        ln = por_end.get(e.lower(), {})
        semana, mes = _janela_leaderboard(ln, "week"), _janela_leaderboard(ln, "month")
        linhas.append({"prefixo": e[:10], "hash": rv.hash_endereco(e), "valor_usd": num(flt(ln.get("accountValue")), 0),
                       "roi_semana": num(semana["roi"], 4), "roi_mes": num(mes["roi"], 4), "vlm_mes": num(mes["vlm"], 0),
                       "hora_utc": iso_utc(agora_ms_)})
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_BALEIAS)
        w.writeheader()
        for ln in linhas:
            w.writerow(ln)
    os.replace(tmp, caminho)


def obter_leaderboard(cfg: ConfigSinalizador, timeout: float = 30.0) -> List[Dict[str, Any]]:
    """GET do leaderboard (H6, nao documentado); aceita lista ou {"leaderboardRows": lista}."""
    req = urllib.request.Request(cfg.p.url_leaderboard, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        corpo = json.loads(r.read().decode("utf-8"))
    if isinstance(corpo, dict):
        for chave in ("leaderboardRows", "rows", "data"):
            if isinstance(corpo.get(chave), list):
                return corpo[chave]
        return []
    return corpo if isinstance(corpo, list) else []


def interpretar_posicoes(doc: Dict[str, Any]) -> List[rv.Posicao]:
    """assetPositions[].position.{coin, szi, positionValue, liquidationPx} de um clearinghouseState."""
    out = []
    for ap in (doc or {}).get("assetPositions") or []:
        pos = ap.get("position") if isinstance(ap, dict) else None
        if not isinstance(pos, dict):
            continue
        szi, valor = flt(pos.get("szi")), flt(pos.get("positionValue"))
        if szi != szi or valor != valor or szi == 0.0:
            continue
        lq = flt(pos.get("liquidationPx"))
        out.append(rv.Posicao(str(pos.get("coin") or ""), szi, abs(valor), lq if lq == lq and lq > 0 else NAN))
    return out


def sondar_baleias(cfg: ConfigSinalizador, enderecos: List[str], orcamento: Optional[OrcamentoPeso] = None,
                   pedir: Optional[Callable[[str, Dict[str, Any]], Any]] = None) -> Dict[str, List[rv.Posicao]]:
    """clearinghouseState (peso 2) por endereco, em thread, abaixo de 400 de peso por minuto; chave = hash do endereco.

    Um endereco que falha fica de fora (aviso no log com o prefixo apenas).
    """
    pedir = pedir or pedir_info
    orc = orcamento or OrcamentoPeso(PESO_MINUTO_MAX)
    out: Dict[str, List[rv.Posicao]] = {}
    for e in enderecos[:max(0, cfg.p.baleias_max)]:
        orc.usar(PESO_CLEARINGHOUSE)
        try:
            doc = pedir(cfg.url_info, {"type": "clearinghouseState", "user": e})
            out[rv.hash_endereco(e)] = interpretar_posicoes(doc if isinstance(doc, dict) else {})
        except Exception as ex:
            # o texto do erro pode ecoar o pedido: o endereco inteiro nunca vai para o log
            log.warning("Sonda de %s...: %s", e[:10], sem_chave(str(ex).replace(e, e[:10] + "..."), cfg.cg_chave))
    return out


_AVISOS_CG: Set[str] = set()


def fundo_de_liquidacoes(cfg: ConfigSinalizador) -> Tuple[List[Dict[str, Any]], str]:
    """REST v4 da CoinGlass: ordens de liquidacao da Hyperliquid se existirem (liqsrc=cg), senao das outras bolsas (agg); so 401/403 levantam."""
    base = "https://open-api-v4.coinglass.com/api/futures/liquidation/order"
    itens: List[Dict[str, Any]] = []
    fonte = "agg"
    for bolsa in ["Hyperliquid"] + [b for b in cfg.cg_rest_ex if b.lower() != "hyperliquid"]:
        if fonte == "cg":
            break
        for coin in cfg.ativos:
            url = "%s?exchange=%s&symbol=%s&min_liquidation_amount=%d" % (
                base, urllib.request.quote(bolsa), urllib.request.quote(coin), int(cfg.cg_rest_min))
            req = urllib.request.Request(url, headers={"accept": "application/json", "CG-API-KEY": cfg.cg_chave})
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    corpo = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise
                continue
            except Exception:
                continue
            if not isinstance(corpo, dict) or str(corpo.get("code", "")) not in ("0", "200"):
                # uma bolsa ou parametro nao suportado nao e uma chave recusada: passa-se as outras bolsas (H7)
                if bolsa not in _AVISOS_CG:
                    _AVISOS_CG.add(bolsa)
                    log.warning("CoinGlass REST sem %s/%s: %s", bolsa, coin,
                                sem_chave((corpo or {}).get("msg") if isinstance(corpo, dict) else corpo, cfg.cg_chave))
                continue
            dados = [it for it in (corpo.get("data") or []) if isinstance(it, dict)]
            if dados and bolsa.lower() == "hyperliquid":
                fonte = "cg"
            for it in dados:
                it.setdefault("exchange", bolsa)
            itens.extend(dados)
    return itens, fonte


# ==========================================================================
# O sinalizador: ciclo asyncio (seccoes 5.1 e 10)
# ==========================================================================
class Sinalizador:
    """Ligacao propria (candle 15m e 1h, trades, activeAssetCtx), relogio de fechos, estado atomico, tarefas opcionais.

    O fecho de uma vela e declarado no primeiro de dois instantes: chegada de uma vela com
    t maior no canal candle, ou relogio_local - atraso >= T + folga_fecho_ms (tarefa_relogio).
    No fecho comum actualiza-se primeiro o contexto de 1 h e so depois o gatilho de 15 m.
    Nunca escreve nos ficheiros do medidor; le estado.json so para a nota e N_liq.
    """

    def __init__(self, cfg: ConfigSinalizador, sz_dec: Optional[Dict[str, int]] = None,
                 max_lev: Optional[Dict[str, float]] = None):
        self.cfg = cfg
        os.makedirs(cfg.pasta, exist_ok=True)
        os.makedirs(cfg.pasta_velas, exist_ok=True)
        os.makedirs(cfg.pasta_funding, exist_ok=True)
        sz = sz_dec or {n: 0 for n in cfg.ativos}
        self.ativos: Dict[str, ActivoSinal] = {n: ActivoSinal(n, cfg, int(sz.get(n, 0))) for n in cfg.ativos}
        self.carteira: Dict[str, float] = {}
        self.liquidados_7d: Dict[str, int] = {}
        self.registo = Registo(cfg.reg_sinalizador, COLUNAS_REGISTO)
        linhas = _ler_csv(cfg.reg_sinalizador)
        for at in self.ativos.values():
            at.carteira = self.carteira
            at.liquidados = self.liquidados_7d
            at.registo = self.registo
            at.carregar_fechados(linhas)
            lev = flt((max_lev or {}).get(at.nome))
            at.max_leverage = lev if lev == lev and lev > 0.0 else NAN
        self.hist: Dict[Tuple[str, str], Historico] = {(n, i): Historico(n, i, cfg.pasta_velas)
                                                       for n in cfg.ativos for i in ("15m", "1h")}
        self.pendentes: Dict[Tuple[str, str], Vela] = {}
        self.fechadas: Dict[Tuple[str, str], int] = {}
        self.recuperando: Set[str] = set()                                # activos a recuperar velas em falta
        self.em_espera: Dict[Tuple[str, str], List[Vela]] = {}           # velas fechadas pelo canal durante a recuperacao
        self.inicio_ms = agora_ms()
        self.ligado = False
        self.ligacao_ms = 0
        self.ultimo_dados = time.monotonic()
        self.religacoes = 0
        self.erros: Dict[str, int] = {}
        self.erros_msg = 0
        self.negocios_velhos = 0
        self.aquecido = False
        self.disjuntores: Dict[str, Any] = {"perdas_dia": "", "cvar": False}
        self.estado_medidor: Optional[Dict[str, Any]] = None
        self.baleias_hash: List[str] = carregar_baleias(cfg.baleias_csv)
        self.baleias_enderecos: List[str] = []
        self.baleias_ms = 0
        self.baleias_dia = ""
        self.cg_estado = "desligado" if not cfg.cg_chave else "a ligar"
        self.cg_vistos: Deque[Tuple[Any, ...]] = deque(maxlen=8000)
        self.cg_vistos_set: Set[Tuple[Any, ...]] = set()
        self.cg_rest_parado = False
        self.cg_total = 0
        self.peso = OrcamentoPeso(PESO_MINUTO_MAX)
        self.tarefas: List["asyncio.Future[Any]"] = []
        self.t_15m_estado: Dict[str, int] = {}
        self._carregar_estado()

    # ---- estado persistente ---------------------------------------------------------
    def _carregar_estado(self) -> None:
        try:
            with open(self.cfg.estado_sinalizador_json, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(doc, dict):
            return
        disj = doc.get("disjuntores") or {}
        self.disjuntores = {"perdas_dia": str(disj.get("perdas_dia") or ""), "cvar": bool(disj.get("cvar"))}
        limite = agora_ms() - self.cfg.p.baleia_liq_dias * MS_DIA
        for h, ms in (doc.get("liquidados_7d") or {}).items():
            if isinstance(h, str) and isinstance(ms, (int, float)) and ms >= limite:
                self.liquidados_7d[h] = int(ms)
        for nome, e in (doc.get("ativos") or {}).items():
            at = self.ativos.get(nome)
            if not at or not isinstance(e, dict):
                continue
            # NOTA ESPEC: (63) diz que perdas_dia dura ate a meia-noite UTC e cvar ate ao reset; os dois sao
            # globais e repoem-se abaixo a partir de "disjuntores". Por activo so se repoe o que e por
            # activo (seguidas, regime): um "perdas_dia" de ontem gravado no activo nao pode parar hoje.
            parado = e.get("parado") or None
            at.parado = parado if parado in ("seguidas", "regime") else None
            at.n_sinal = int(e.get("n_sinal") or 0)
            self.t_15m_estado[nome] = int(e.get("t_15m_ult") or 0)
            trade = e.get("trade")
            if isinstance(trade, dict):
                if str(trade.get("id", "")) in at.ids_fechados:      # fechou entre o registo e a ultima escrita do estado
                    log.info("%s: trade virtual %s ja esta fechado no registo; nao e reposto", nome, trade.get("id"))
                    continue
                try:
                    at.restaurar_trade(trade)
                    log.info("%s: trade virtual %s reposto do estado", nome, trade.get("id"))
                except (KeyError, TypeError, ValueError) as ex:
                    log.warning("%s: nao consegui repor o trade virtual (%s)", nome, ex)
        if self.disjuntores["cvar"]:
            self._parar_todos("cvar")
        elif self.disjuntores["perdas_dia"] == dia_utc(agora_ms()):
            self._parar_todos("perdas_dia")

    def _escrever_estado(self, t: int) -> None:
        doc = {"hora_utc": iso_utc(t), "ligado": self.ligado, "versao": VERSAO, "variante": self.cfg.variante,
               "aquecido": self.aquecido, "religacoes": self.religacoes, "negocios_velhos": self.negocios_velhos,
               "disjuntores": dict(self.disjuntores), "coinglass": self.cg_estado,
               "liquidados_7d": {h: ms for h, ms in self.liquidados_7d.items()
                                 if ms >= t - self.cfg.p.baleia_liq_dias * MS_DIA},
               "baleias": {"n": len(self.baleias_enderecos), "hora_utc": iso_utc(self.baleias_ms) if self.baleias_ms else ""},
               "medidor": "on" if self.estado_medidor else "off",
               "ativos": {n: dict(at.estado(), n_sinal=at.n_sinal) for n, at in self.ativos.items()}}
        escrever_json_atomico(self.cfg.estado_sinalizador_json, doc)

    # ---- disjuntores (seccao 7.6) -------------------------------------------------------
    def _parar_todos(self, motivo: str) -> None:
        for at in self.ativos.values():
            if at.parado is None or (motivo == "cvar" and at.parado != "regime"):
                at.parado = motivo

    def _levantar(self, motivo: str) -> None:
        for at in self.ativos.values():
            if at.parado == motivo:
                at.parado = None

    def _aplicar_disjuntores(self, t: int) -> None:
        # NOTA ESPEC: (63) nao diz se perdas_dia e cvar somam por activo ou no total; como param "a
        # emissao" e "tudo", contam-se os trades fechados de todos os activos; seguidas e por activo.
        dia = dia_utc(t)
        todos = sorted((f for at in self.ativos.values() for f in at.fechados), key=lambda f: f["t_ms"])
        r_dia = [f["r_liq_bps"] for f in todos if f["t_ms"] and dia_utc(f["t_ms"]) == dia]
        ultimos = [f["r_liq_bps"] for f in todos[-self.cfg.p_disj.cvar_n:]]
        g_med = nu.mediana([f["G"] for f in todos])
        l_med = nu.mediana([f["L"] for f in todos])
        for nome, at in self.ativos.items():
            motivo = rv.disjuntores(r_dia, at.stops_seguidos, ultimos, g_med, l_med, self.cfg.p_disj)
            if motivo == "cvar" and not self.disjuntores["cvar"]:
                self.disjuntores["cvar"] = True
                self._parar_todos("cvar")
                log.warning("Disjuntor CVaR: tudo parado ate 'sinalizador.py reset'")
            elif motivo == "perdas_dia" and self.disjuntores["perdas_dia"] != dia:
                self.disjuntores["perdas_dia"] = dia
                self._parar_todos("perdas_dia")
                log.warning("Disjuntor de perdas do dia: sem linhas ate a meia-noite UTC")
            elif motivo == "seguidas" and at.parado is None:
                at.parado = "seguidas"
                log.warning("%s: %d stops seguidos; activo parado ate ao proximo fecho de 1 h VERDE", nome, at.stops_seguidos)

    def reset(self) -> None:
        """Levanta os disjuntores (comando reset) e grava o estado."""
        self.disjuntores = {"perdas_dia": "", "cvar": False}
        for at in self.ativos.values():
            at.parado = None
            at.vr_regime = 0
            at.stops_seguidos = 0
        self._escrever_estado(agora_ms())

    # ---- aquecimento com o historico --------------------------------------------------------
    def aquecer(self, rede: bool = True) -> None:
        """Le dados/velas e dados/funding, completa com candleSnapshot e fundingHistory (se rede) e alimenta os activos.

        Os pedidos passam pelo OrcamentoPeso partilhado (400 por minuto). Um trade virtual
        reposto do estado avanca entrelacado com o replay: para cada vela de 15 m posterior a
        ultima que a instancia anterior viu (t_15m_ult do estado), primeiro os fechos de 1 h
        ate ela com o contexto completo (invalidacao (60) incluida), depois o trade, depois a
        vela; o funding das horas replicadas vem de dados/funding.
        """
        cfg = self.cfg
        for nome, at in self.ativos.items():
            h1, h15 = self.hist[(nome, "1h")], self.hist[(nome, "15m")]
            n_disco = h1.carregar_disco() + h15.carregar_disco()
            n_rede = 0
            if rede:
                for h in (h1, h15):
                    ult = h.ultima()
                    try:
                        n_rede += h.carregar_snapshot(cfg, ult.t + h.ms if ult else None, orcamento=self.peso)
                    except Exception as e:
                        log.warning("%s %s: candleSnapshot falhou (%s); sigo com o que ha em disco", nome, h.intervalo, e)
                try:
                    guardar_funding(cfg, nome, obter_funding_history(cfg, nome, agora_ms() - 30 * MS_DIA,
                                                                     orcamento=self.peso))
                except Exception as e:
                    log.warning("%s: fundingHistory falhou (%s)", nome, e)
            at.funding_historico(carregar_funding(cfg, nome))
            v1, v15 = h1.ultimas(len(h1)), h15.ultimas(len(h15))
            desde = sys.maxsize                           # sem trade reposto nao ha replay
            if at.trade is not None:                      # nunca antes da abertura do proprio trade
                aberto_ms = flt(at.trade_info.get("hora_ms"))
                desde = max(int(self.t_15m_estado.get(nome, 0)), int(aberto_ms) if aberto_ms == aberto_ms else 0)
            i = 0
            ultimos = max(0, len(v1) - 3)        # so os 3 ultimos fechos de 1 h levam o contexto completo
            for v in v15:
                while i < len(v1) and v1[i].T <= v.T:
                    at.fecho_1h(v1[i], completo=i >= ultimos or v1[i].T > desde)
                    i += 1
                if v.T > desde and at.trade is not None:          # replay do trade virtual reposto
                    linha = at.avancar_trade_virtual(v)
                    if linha:
                        self._aplicar_disjuntores(v.T)
                at.fecho_15m(v, v.T, avaliar=False)
            while i < len(v1):
                at.fecho_1h(v1[i], completo=i >= ultimos or v1[i].T > desde)
                i += 1
            if v1:
                self.fechadas[(nome, "1h")] = v1[-1].t
            if v15:
                self.fechadas[(nome, "15m")] = v15[-1].t
            at.calibrar()
            at.custos = custos_medidos(cfg, nome)
            for v in v15:
                h15.guardar(v, {})
            for v in v1:
                h1.guardar(v, {})
            log.info("%s: %d velas em disco, %d da rede; contexto %s (%s); t_crit %.2f; custos c_W %.1f c_L %.1f (n=%d)",
                     nome, n_disco, n_rede, at.ctx.estado, " ".join(at.ctx.motivos) or "ok", at.t_crit,
                     at.custos[0], at.custos[1], at.custos[2])
        self.aquecido = True

    # ---- ligacao a Hyperliquid ------------------------------------------------------------
    async def tarefa_hyperliquid(self) -> None:
        import websockets
        espera = 1.0
        while True:
            try:
                async with websockets.connect(self.cfg.url_ws, ping_interval=None, max_size=None, open_timeout=20) as ws:
                    for nome in self.cfg.ativos:
                        for intervalo in ("15m", "1h"):
                            await ws.send(json.dumps({"method": "subscribe",
                                                      "subscription": {"type": "candle", "coin": nome, "interval": intervalo}}))
                        for tipo in ("trades", "activeAssetCtx"):
                            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": tipo, "coin": nome}}))
                    self.ligado = True
                    self.ligacao_ms = agora_ms()
                    self.ultimo_dados = time.monotonic()
                    espera = 1.0
                    log.info("Hyperliquid: ligado (%d activos)", len(self.cfg.ativos))
                    vigia = asyncio.ensure_future(self._vigia(ws))
                    if self.aquecido and self.fechadas:          # velas fechadas durante o corte ou o aquecimento
                        asyncio.ensure_future(self._recuperar(list(self.cfg.ativos)))
                    try:
                        async for bruto in ws:
                            try:
                                self._tratar(json.loads(bruto))
                            except Exception as e:
                                self.erros_msg += 1
                                if self.erros_msg <= 20:
                                    log.warning("Mensagem ignorada (%s): %.200s", e, bruto)
                    finally:
                        vigia.cancel()
                log.warning("Hyperliquid: ligacao fechada pelo servidor")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Hyperliquid: ligacao falhou (%s)", e)
            self.ligado = False
            self.religacoes += 1
            t = agora_ms()
            for at in self.ativos.values():
                self._cortar(at, t)
            log.info("Nova tentativa dentro de %.0f s", espera)
            await asyncio.sleep(espera)
            espera = min(espera * 2, 30.0)

    async def _vigia(self, ws: Any) -> None:
        """Ping de manutencao; fecha a ligacao se deixarem de chegar dados de mercado (pongs nao contam)."""
        limite = self.cfg.vigia_s
        try:
            while True:
                await asyncio.sleep(min(15.0, limite / 2.0))
                if time.monotonic() - self.ultimo_dados > limite:
                    log.warning("Hyperliquid: %.0f s sem dados de mercado; a religar", limite)
                    await ws.close()
                    return
                await ws.send(json.dumps({"method": "ping"}))
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _tratar(self, msg: Dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        canal = msg.get("channel")
        d = msg.get("data")
        t = agora_ms()
        if canal in ("candle", "trades", "activeAssetCtx"):
            self.ultimo_dados = time.monotonic()
        if canal == "candle":
            if not isinstance(d, dict):
                return
            at = self.ativos.get(str(d.get("s", "")))
            intervalo = str(d.get("i", ""))
            if at is None or intervalo not in INTERVALOS_MS:
                return
            vela = interpretar_vela(d)
            at.ultimo_local_ms = max(at.ultimo_local_ms, t)
            chave = (at.nome, intervalo)
            pend = self.pendentes.get(chave)
            if pend is not None and vela.t > pend.t:
                if at.nome in self.recuperando:          # as velas em falta entram primeiro, por ordem
                    self.em_espera.setdefault(chave, []).append(pend)
                else:
                    self._fechar(chave, pend, t)
            if vela.t > self.fechadas.get(chave, -1):
                self.pendentes[chave] = vela
        elif canal == "trades":
            for tr in d or []:
                at = self.ativos.get(str(tr.get("coin", "")))
                if at is None:
                    continue
                ex = int(tr["time"])
                ref = at.exch_ms if at.exch_ms else self.ligacao_ms - 5000
                if ex < ref - 5000:          # lote de negocios antigos enviado ao subscrever
                    self.negocios_velhos += 1
                    continue
                self._passo("negocio " + at.nome, at.negocio, tr, t)
        elif canal == "activeAssetCtx":
            if isinstance(d, dict):
                at = self.ativos.get(str(d.get("coin", "")))
                if at is not None:
                    at.contexto_activo(d.get("ctx") or {}, t)
        elif canal == "error":
            log.warning("Hyperliquid devolveu erro: %s", d)

    # ---- fechos ----------------------------------------------------------------------------
    def _fechar(self, chave: Tuple[str, str], vela: Vela, agora: int, avaliar: bool = True) -> None:
        """Fecho de uma vela: 1 h recalcula o contexto; 15 m avanca o trade virtual, avalia o gatilho (se avaliar) e grava."""
        nome, intervalo = chave
        if vela.t <= self.fechadas.get(chave, -1):
            return
        if intervalo == "15m":                       # 1 h antes de 15 m no fecho comum
            ch1 = (nome, "1h")
            p1 = self.pendentes.get(ch1)
            if p1 is not None and p1.T <= vela.T:
                self._fechar(ch1, p1, agora, avaliar)
        self.fechadas[chave] = vela.t
        if self.pendentes.get(chave) is vela:
            self.pendentes.pop(chave, None)
        at, hist = self.ativos[nome], self.hist[chave]
        hist.juntar(vela)
        if intervalo == "1h":
            ctx = self._passo("fecho 1h " + nome, at.fecho_1h, vela)
            self._passo("guardar 1h " + nome, hist.guardar, vela, None)
            if ctx is not None:
                log.info("%s fecho 1h %s: %s %s | t_nulo %.2f (tc %.2f) phi %.4f H %.1f VR %.2f z %.2f",
                         nome, iso_utc(vela.T), ctx.estado, " ".join(ctx.motivos), ctx.aj.t_nulo, ctx.t_crit,
                         ctx.aj.phi_c, ctx.aj.meia_vida, ctx.vr, at.exc.z)
            return
        at.medidor = self.estado_medidor
        linha = self._passo("trade virtual " + nome, at.avancar_trade_virtual, vela)
        if linha:
            self._passo("disjuntores", self._aplicar_disjuntores, agora)
        res = self._passo("fecho 15m " + nome, at.fecho_15m, vela, agora, avaliar)
        sinal, motivo = res if res else (None, "erro")
        if sinal is not None:
            self._passo("escrita " + nome, escrever_sinal, self.cfg, sinal)
        self._passo("guardar 15m " + nome, hist.guardar, vela, at.ultima)
        log.info("%s fecho 15m %s: c %.6g z %.2f %s %s %s%s", nome, iso_utc(vela.T), vela.c,
                 at.exc.z if at.exc.z == at.exc.z else NAN, at.exc.estado, motivo, at.ultimo_detalhe,
                 " | trade virtual aberto" if at.trade is not None else "")

    async def tarefa_relogio(self) -> None:
        """Declara fechos pelo relogio corrigido (agora - atraso >= T + folga), 1 h antes de 15 m; detecta silencio."""
        cfg = self.cfg
        dia_ant = ""
        while True:
            await asyncio.sleep(0.25)
            agora = agora_ms()
            self._fechos_pelo_relogio(agora)
            if self.ligado:
                limite = int(cfg.vigia_s * 1000)
                for at in self.ativos.values():
                    if at.ultimo_local_ms and agora - at.ultimo_local_ms > limite and \
                            (not at.cortes or at.cortes[-1] < at.ultimo_local_ms):
                        self._cortar(at, agora)
                        log.warning("%s: %.0f s sem mensagens; conta como corte", at.nome, limite / 1000.0)
                        if at.nome not in self.recuperando:
                            asyncio.ensure_future(self._recuperar([at.nome]))
            dia = dia_utc(agora)
            if dia != dia_ant:
                if dia_ant:          # N cresceu: recalibra t_crit fora do caminho critico (cache por N)
                    for at in self.ativos.values():
                        asyncio.ensure_future(asyncio.to_thread(self._passo, "calibracao " + at.nome, at.calibrar))
                dia_ant = dia
                if self.disjuntores["perdas_dia"] and self.disjuntores["perdas_dia"] != dia:
                    self.disjuntores["perdas_dia"] = ""
                    self._levantar("perdas_dia")
                    log.info("Meia-noite UTC: disjuntor de perdas do dia levantado")

    def _fechos_pelo_relogio(self, agora: int) -> None:
        """Fecha pelo relogio corrigido (agora - atraso >= T + folga) as velas pendentes, 1 h antes de 15 m.

        Sem ligacao nao se fecha nada: a ultima actualizacao recebida de uma vela pode ser
        parcial e so a religacao (candleSnapshot) traz a vela final. Um activo em recuperacao
        espera que as velas em falta entrem pela ordem certa.
        """
        if not self.ligado:
            return
        cfg = self.cfg
        for chave in sorted(self.pendentes, key=lambda c: 0 if c[1] == "1h" else 1):
            vela = self.pendentes.get(chave)
            if vela is None or chave[0] in self.recuperando:
                continue
            at = self.ativos[chave[0]]
            atraso = at.atraso_mediano()
            atraso_ms = int(atraso) if atraso == atraso and atraso > 0 else 0
            if rv.vela_fechada(vela.t, vela.T, agora, atraso_ms, cfg.p.folga_fecho_ms):
                self._passo("fecho pelo relogio", self._fechar, chave, vela, agora)

    def _cortar(self, at: ActivoSinal, t: int) -> None:
        """Corte de ligacao ou silencio: regista-o no activo e descarta as velas pendentes (podem estar parciais)."""
        at.corte(t)
        for chave in [c for c in self.pendentes if c[0] == at.nome]:
            self.pendentes.pop(chave, None)

    def _recolher(self, nomes: List[str]) -> Dict[Tuple[str, str], List[Vela]]:
        """candleSnapshot (em thread, com orcamento) das velas fechadas depois da ultima fechada de cada activo e intervalo."""
        cfg = self.cfg
        agora = agora_ms()
        out: Dict[Tuple[str, str], List[Vela]] = {}
        for nome in nomes:
            for intervalo in ("1h", "15m"):
                chave = (nome, intervalo)
                ult = self.fechadas.get(chave)
                if ult is None:
                    continue
                h = Historico(nome, intervalo, cfg.pasta_velas)       # so memoria: quem grava e _fechar
                try:
                    h.carregar_snapshot(cfg, ult + h.ms, agora, orcamento=self.peso)
                except Exception as e:
                    log.warning("%s %s: candleSnapshot da religacao falhou (%s)", nome, intervalo, e)
                    continue
                out[chave] = [v for v in h.ultimas(len(h)) if v.t > ult]
        return out

    async def _recuperar(self, nomes: List[str]) -> None:
        """Religacao ou silencio: fecha, sem avaliar, as velas que faltam entre a ultima fechada e agora (1 h antes de 15 m).

        A vela corrente pendente e as que o canal fechar entretanto (em_espera) entram depois
        das recolhidas, pela ordem de tempo; as velas em falta ficam gravadas com a versao final
        e com corte = 1 quando houve corte nas 4 velas anteriores.
        """
        nomes = [n for n in nomes if n not in self.recuperando]
        if not nomes:
            return
        self.recuperando.update(nomes)
        try:
            recolhidas = await asyncio.to_thread(self._passo, "recuperacao", self._recolher, nomes) or {}
        except Exception as e:
            log.warning("Recuperacao de velas falhou (%s)", e)
            recolhidas = {}
        for nome in nomes:
            try:
                self._alimentar_em_falta(nome, recolhidas, agora_ms())
            finally:
                self.recuperando.discard(nome)

    def _alimentar_em_falta(self, nome: str, recolhidas: Dict[Tuple[str, str], List[Vela]], agora: int) -> None:
        por_intervalo: Dict[str, List[Vela]] = {}
        for intervalo in ("1h", "15m"):
            chave = (nome, intervalo)
            vistas: Dict[int, Vela] = {}
            for v in list(recolhidas.get(chave, [])) + self.em_espera.pop(chave, []):
                if v.t > self.fechadas.get(chave, -1) and v.t not in vistas:
                    vistas[v.t] = v
            por_intervalo[intervalo] = [vistas[t] for t in sorted(vistas)]
        v1, v15 = por_intervalo["1h"], por_intervalo["15m"]
        i = 0
        for v in v15:
            while i < len(v1) and v1[i].T <= v.T:
                self._fechar((nome, "1h"), v1[i], agora, avaliar=False)
                i += 1
            self._fechar((nome, "15m"), v, agora, avaliar=False)
        while i < len(v1):
            self._fechar((nome, "1h"), v1[i], agora, avaliar=False)
            i += 1
        if v1 or v15:
            log.info("%s: %d velas de 1 h e %d de 15 m recuperadas pelo candleSnapshot (sem avaliacao)", nome, len(v1), len(v15))

    async def tarefa_estado(self) -> None:
        """Le estado.json do medidor (so leitura) e escreve estado_sinalizador.json de forma atomica."""
        n = 0
        while True:
            await asyncio.sleep(1.0)
            t = agora_ms()
            self.estado_medidor = self._passo("estado do medidor", ler_estado_medidor, self.cfg, t)
            for at in self.ativos.values():
                at.medidor = self.estado_medidor
            n += 1
            if n % 5 == 0:
                self._passo("estado", self._escrever_estado, t)

    # ---- baleias (seccao 6.5) ----------------------------------------------------------------
    def _refazer_baleias(self, t: int) -> None:
        cfg = self.cfg
        negocios: Dict[str, Tuple[float, float]] = {}
        for at in self.ativos.values():
            for h, (liq, bruto) in list(at.negocios_24h.items()):     # copia: o ciclo de eventos escreve-o
                a, b = negocios.get(h, (0.0, 0.0))
                negocios[h] = (a + liq, b + bruto)
        limite = t - cfg.p.baleia_liq_dias * MS_DIA
        liquidados = {h for h, ms in self.liquidados_7d.items() if ms >= limite}
        # NOTA ESPEC: H6 diz "se o esquema mudar usa-se o ultimo dados/baleias.csv", mas o ficheiro so tem
        # prefixo e hash (privacidade) e a sonda precisa dos enderecos inteiros: a degradacao possivel e
        # continuar a sondar a lista em memoria (baleias_enderecos) e repetir o leaderboard em cada ciclo;
        # baleias_hash (do ficheiro) so serve para contar e para os filtros por hash depois de um rearranque.
        try:
            rows = obter_leaderboard(cfg)
        except Exception as e:
            log.warning("Leaderboard falhou (%s); fico com a lista em memoria (%d enderecos; %d hashes em disco)",
                        e, len(self.baleias_enderecos), len(self.baleias_hash))
            return
        enderecos = construir_baleias(cfg, rows, negocios, liquidados, set(cfg.p.enderecos_excluidos))
        if not enderecos:
            log.warning("Leaderboard sem linhas que passem os filtros (esquema mudou? H6); fico com a lista anterior")
            return
        guardar_baleias(cfg.baleias_csv, rows, enderecos, t)
        self.baleias_enderecos = enderecos
        self.baleias_hash = [rv.hash_endereco(e) for e in enderecos]
        self.baleias_ms = t
        for at in self.ativos.values():
            at.negocios_24h = {}
        log.info("Lista de baleias refeita: %d enderecos (so prefixo e hash em %s)", len(enderecos),
                 os.path.basename(cfg.baleias_csv))

    async def tarefa_baleias(self) -> None:
        """Lista diaria as 00:00 UTC (leaderboard) e sonda clearinghouseState de sonda_s em sonda_s, tudo em thread."""
        cfg = self.cfg
        if cfg.p.baleias_max <= 0:
            return
        await asyncio.sleep(3.0)
        while True:
            t = agora_ms()
            dia = dia_utc(t)
            if dia != self.baleias_dia or not self.baleias_enderecos:
                self.baleias_dia = dia
                await asyncio.to_thread(self._passo, "baleias", self._refazer_baleias, t)
            if self.baleias_enderecos:
                posicoes = await asyncio.to_thread(self._passo, "sonda", sondar_baleias, cfg, self.baleias_enderecos, self.peso)
                if posicoes:
                    t2 = agora_ms()
                    for at in self.ativos.values():
                        at.posicoes(posicoes, t2)
            await asyncio.sleep(max(60.0, cfg.p.sonda_s))

    # ---- CoinGlass (opcional, seccao 6.6) -----------------------------------------------------
    def _ingerir_liq(self, it: Dict[str, Any], t: int) -> None:
        chave = chave_liquidacao(it)
        if chave in self.cg_vistos_set:
            return
        if len(self.cg_vistos) == self.cg_vistos.maxlen:
            self.cg_vistos_set.discard(self.cg_vistos[0])
        self.cg_vistos.append(chave)
        self.cg_vistos_set.add(chave)
        self.cg_total += 1
        bolsa = str(it.get("exchange") or it.get("exchange_name") or it.get("exName") or "").lower()
        if bolsa == "hyperliquid" and self.cfg.liqsrc != "cg":
            self.cfg.liqsrc = "cg"                 # H7 confirmada pelo proprio fluxo
        base = base_da_liquidacao(it)
        at = next((a for n, a in self.ativos.items() if n.upper() == base), None)
        if at is not None:
            at.liquidacao(it, t)

    async def tarefa_coinglass(self) -> None:
        """Websocket de liquidacoes da CoinGlass, como no medidor; a chave nunca vai para o log."""
        import websockets
        cfg = self.cfg
        if not cfg.cg_chave:
            return
        espera = 2.0
        while True:
            try:
                async with websockets.connect(cfg.url_cg + cfg.cg_chave, ping_interval=None, open_timeout=20) as ws:
                    await ws.send(json.dumps({"method": "subscribe", "channels": cfg.cg_canais}))
                    self.cg_estado = "ligado"
                    espera = 2.0
                    bate = asyncio.ensure_future(self._bate_coinglass(ws))
                    try:
                        async for bruto in ws:
                            if isinstance(bruto, bytes):
                                bruto = bruto.decode("utf-8", "replace")
                            if bruto.strip().strip('"') == "pong":
                                continue
                            try:
                                msg = json.loads(bruto)
                            except ValueError:
                                continue
                            if not isinstance(msg, dict) or "liquidation" not in str(msg.get("channel", "")).lower():
                                continue
                            t = agora_ms()
                            for it in msg.get("data") or []:
                                if isinstance(it, dict):
                                    self._ingerir_liq(it, t)
                    finally:
                        bate.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("CoinGlass: ligacao falhou (%s)", sem_chave(e, cfg.cg_chave))
            self.cg_estado = "a religar"
            await asyncio.sleep(espera)
            espera = min(espera * 2, 60.0)

    async def _bate_coinglass(self, ws: Any) -> None:
        try:
            while True:
                await asyncio.sleep(20)
                await ws.send("ping")
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _poll_rest(self) -> None:
        """Ordens de liquidacao por REST v4 (plano Standard ou acima); define liqsrc (H7)."""
        if self.cg_rest_parado:
            return
        try:
            itens, fonte = fundo_de_liquidacoes(self.cfg)
        except Exception as e:
            self.cg_rest_parado = True
            log.warning("CoinGlass REST parado: %s", sem_chave(e, self.cfg.cg_chave))
            return
        self.cfg.liqsrc = fonte
        agora = agora_ms()
        for it in itens:
            ts = int(flt(it.get("time") or 0))
            if ts and agora - ts > 90_000:
                continue
            self._ingerir_liq(it, agora)

    async def tarefa_coinglass_rest(self) -> None:
        if not (self.cfg.cg_chave and self.cfg.cg_rest):
            return
        await asyncio.sleep(5)
        while True:
            await asyncio.to_thread(self._passo, "coinglass rest", self._poll_rest)
            await asyncio.sleep(max(5.0, self.cfg.cg_rest_s))

    # ---- ciclo -----------------------------------------------------------------------------
    def _passo(self, nome: str, fn: Callable[..., Any], *args: Any) -> Any:
        """Corre um passo; um erro num passo nao impede os outros (como no medidor)."""
        try:
            return fn(*args)
        except Exception:
            n = self.erros.get(nome, 0) + 1
            self.erros[nome] = n
            if n <= 3:
                log.exception("Erro em '%s' (o sinalizador continua)", nome)
            elif n % 600 == 0:
                log.error("Erro em '%s' repetido %d vezes", nome, n)
            return None

    async def arrancar(self) -> None:
        """Aquece com o historico, liga-se e corre as tarefas ate ser parado."""
        cfg = self.cfg
        await asyncio.to_thread(self._passo, "aquecimento", self.aquecer, True)
        self._escrever_estado(agora_ms())
        print("Sinalizador %s a correr. Activos: %s. Variante %s. Capital %.0f usd. Ctrl+C para parar." % (
            VERSAO, ", ".join(cfg.ativos), cfg.variante, cfg.p.capital_usd))
        print("Dados em: %s\n" % cfg.pasta)
        self.tarefas = [asyncio.ensure_future(self.tarefa_hyperliquid()), asyncio.ensure_future(self.tarefa_relogio()),
                        asyncio.ensure_future(self.tarefa_estado()), asyncio.ensure_future(self.tarefa_baleias())]
        if cfg.cg_chave:
            self.tarefas.append(asyncio.ensure_future(self.tarefa_coinglass()))
            self.tarefas.append(asyncio.ensure_future(self.tarefa_coinglass_rest()))
        try:
            await asyncio.gather(*self.tarefas)
        finally:
            self.parar()

    def parar(self) -> None:
        """Cancela as tarefas e grava o estado; os trades virtuais abertos ficam no estado para o rearranque."""
        for tk in self.tarefas:
            tk.cancel()
        self.tarefas = []
        self.ligado = False
        self._passo("estado final", self._escrever_estado, agora_ms())


# ==========================================================================
# Pedidos REST de arranque e do comando historico
# ==========================================================================
def obter_funding_history(cfg: Config, ativo: str, inicio_ms: int, fim_ms: Optional[int] = None,
                          pedir: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                          orcamento: Optional[OrcamentoPeso] = None) -> List[Dict[str, Any]]:
    """fundingHistory paginado de inicio_ms a fim_ms (agora): linhas {time, fundingRate, premium}; peso 20 + 1 por 60 linhas em `orcamento`."""
    pedir = pedir or pedir_info
    fim = agora_ms() if fim_ms is None else fim_ms
    inicio = int(inicio_ms)
    out: List[Dict[str, Any]] = []
    for _ in range(60):
        if inicio >= fim:
            break
        resposta = pedir(cfg.url_info, {"type": "fundingHistory", "coin": ativo, "startTime": inicio, "endTime": fim})
        lista = resposta.get("data") if isinstance(resposta, dict) else resposta
        if orcamento is not None:
            orcamento.usar(PESO_FUNDING + int(math.ceil(len(lista) / 60.0)) if isinstance(lista, list) else PESO_FUNDING)
        if not isinstance(lista, list) or not lista:
            break
        lista = [x for x in lista if isinstance(x, dict) and flt(x.get("time")) == flt(x.get("time"))]
        if not lista:
            break
        out.extend(lista)
        ultimo = max(int(flt(x.get("time"))) for x in lista)
        if ultimo + MS_1H >= fim or ultimo < inicio:
            break
        inicio = ultimo + 1
    return out


def obter_max_leverage(cfg: Config) -> Dict[str, float]:
    """maxLeverage por activo da resposta de meta (seccao 8.6, lev_max na nota); vazio se a resposta nao o trouxer."""
    meta = pedir_info(cfg.url_info, {"type": "meta"})
    out: Dict[str, float] = {}
    for u in (meta.get("universe") if isinstance(meta, dict) else None) or []:
        if isinstance(u, dict) and u.get("name") in cfg.ativos:
            ml = flt(u.get("maxLeverage"))
            if ml == ml and ml > 0.0:
                out[str(u["name"])] = ml
    return out


async def _correr(cfg: ConfigSinalizador) -> None:
    sz = None
    espera = 2.0
    while sz is None:
        try:
            sz = obter_sz_decimals(cfg)
        except SystemExit:
            raise
        except Exception as e:
            log.warning("Nao consegui falar com a Hyperliquid (%s). Nova tentativa em %.0f s", e, espera)
            await asyncio.sleep(espera)
            espera = min(espera * 2, 60.0)
    try:
        lev = obter_max_leverage(cfg)
    except Exception as e:
        log.warning("Nao li o maxLeverage da meta (%s); a nota fica com lev=na", e)
        lev = {}
    s = Sinalizador(cfg, sz, lev)
    await s.arrancar()


def cmd_correr(cfg: ConfigSinalizador) -> None:
    import importlib.util
    if importlib.util.find_spec("websockets") is None:
        raise SystemExit("Falta a biblioteca websockets. Corra:  python3 -m pip install websockets")
    try:
        asyncio.run(_correr(cfg))
    except KeyboardInterrupt:
        print("\nSinalizador parado. Os registos ficaram em %s" % cfg.pasta)


# ==========================================================================
# Comando: verificar (hipoteses H1 a H8 da seccao 3.2)
# ==========================================================================
async def _verificar(cfg: ConfigSinalizador) -> bool:
    ok_total = True

    def linha(ok: Optional[bool], txt: str) -> None:
        nonlocal ok_total
        marca = "OK    " if ok else ("AVISO " if ok is None else "FALHA ")
        if ok is False:
            ok_total = False
        print(marca + sem_chave(txt, cfg.cg_chave))

    rede = next((r for r, u in URLS.items() if u[1] == cfg.url_info), "url propria")
    print("Verificacao do sinalizador %s (variante %s, rede %s)\n" % (VERSAO, cfg.variante, rede))
    linha(sys.version_info >= (3, 9), "Python %d.%d.%d (minimo 3.9)" % sys.version_info[:3])
    try:
        import websockets
        linha(True, "biblioteca websockets %s" % getattr(websockets, "__version__", "?"))
    except ImportError:
        linha(False, "falta a biblioteca websockets: corra  python3 -m pip install websockets")
        return False
    try:
        sz = obter_sz_decimals(cfg)
        linha(True, "Hyperliquid reconhece os activos: " + ", ".join("%s (szDecimals %d)" % kv for kv in sz.items()))
    except SystemExit as e:
        linha(False, str(e))
        return False
    except Exception as e:
        linha(False, "sem resposta de %s (%s)" % (cfg.url_info, e))
        return False
    for h in (900, 3600, 14400):
        linha(True if h in cfg.horizontes else None, "[sombra] horizontes_s %s %d s" % ("inclui" if h in cfg.horizontes else "NAO inclui", h))
    nome = cfg.ativos[0]
    agora = agora_ms()

    # H1: candleSnapshot
    try:
        snap = pedir_info(cfg.url_info, {"type": "candleSnapshot", "req": {"coin": nome, "interval": "15m",
                                                                           "startTime": agora - 12 * MS_15M, "endTime": agora}})
        lista = snap.get("data") if isinstance(snap, dict) else snap
        velas = [interpretar_vela(x) for x in (lista or []) if isinstance(x, dict)]
        falta = [k for k in ("t", "T", "o", "h", "l", "c", "v", "n") if lista and k not in lista[0]]
        linha(bool(velas) and not falta, "H1 candleSnapshot de %s: %d velas de 15 m, campos %s" % (
            nome, len(velas), "completos" if not falta else "em falta: " + ", ".join(falta)))
        if velas:
            ultima = velas[-1]
            linha(ultima.v >= 0 and ultima.n >= 0, "H1 ultima vela %s c=%.6g v=%.4g (unidades do activo) n=%d" % (
                iso_utc(ultima.T), ultima.c, ultima.v, ultima.n))
            linha(ultima.T - ultima.t in (MS_15M, MS_15M - 1), "H1 T - t = %d ms (esperado 15 min menos 1 ms: o fim da vela "
                  "calcula-se pela abertura, t + 15 min)" % (ultima.T - ultima.t))
    except Exception as e:
        linha(False, "H1 candleSnapshot falhou: %s" % e)

    # canal candle, trades (H2, H5, H8), activeAssetCtx (H3, H4)
    visto: Dict[str, Any] = {}
    negocios: List[Dict[str, Any]] = []
    bbo: List[Tuple[int, float, float]] = []
    try:
        async with websockets.connect(cfg.url_ws, ping_interval=None, max_size=None, open_timeout=20) as ws:
            for intervalo in ("15m", "1h"):
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "candle", "coin": nome, "interval": intervalo}}))
            for tipo in ("trades", "activeAssetCtx", "bbo"):
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": tipo, "coin": nome}}))
            await ws.send(json.dumps({"method": "ping"}))
            fim = time.monotonic() + 25
            while time.monotonic() < fim:
                try:
                    bruto = await asyncio.wait_for(ws.recv(), timeout=max(0.1, fim - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                msg = json.loads(bruto)
                c = msg.get("channel")
                t = agora_ms()
                if c == "trades":
                    for tr in msg.get("data") or []:
                        tr = dict(tr)
                        tr["_local"] = t
                        negocios.append(tr)
                elif c == "bbo":
                    bb = (msg.get("data") or {}).get("bbo") or [None, None]
                    if bb[0] and bb[1]:
                        bbo.append((t, float(bb[0]["px"]), float(bb[1]["px"])))
                if c and c not in visto:
                    visto[c] = (msg.get("data"), t)
                if c == "error":
                    linha(False, "a Hyperliquid devolveu erro: %s" % msg.get("data"))
                if len(negocios) >= 300 and "candle" in visto and "activeAssetCtx" in visto:
                    break
    except Exception as e:
        linha(False, "nao consegui abrir o WebSocket %s (%s)" % (cfg.url_ws, e))
        return False
    linha(True, "WebSocket aberto em %s" % cfg.url_ws)
    linha("pong" in visto, "resposta ao ping de manutencao")
    if "candle" in visto:
        d, _ = visto["candle"]
        try:
            v = interpretar_vela(d)
            linha(True, "H1 canal candle de %s %s: t=%d T=%d c=%.6g n=%d" % (d.get("s"), d.get("i"), v.t, v.T, v.c, v.n))
        except ValueError as e:
            linha(False, "H1 canal candle com formato inesperado (%s): %.200s" % (e, d))
    else:
        linha(False, "H1 nenhuma vela chegou pelo canal candle em 25 s")
    if negocios:
        tr0 = negocios[0]
        linha(all(k in tr0 for k in ("px", "sz", "side", "time", "users")),
              "negocios: %d recebidos; campos px, sz, side, time, users %s; hash %s" % (
                  len(negocios), "presentes" if all(k in tr0 for k in ("px", "sz", "side", "time", "users")) else "em falta",
                  "presente" if "hash" in tr0 else "ausente"))
        # H8 atraso
        atrasos = [tr["_local"] - int(tr["time"]) for tr in negocios[-200:]]
        med_atraso = nu.mediana([float(a) for a in atrasos])
        linha(med_atraso < 3000 if med_atraso == med_atraso else None,
              "H8 atraso local menos bolsa: mediana %.0f ms em %d negocios (limite %d ms)%s" % (
                  med_atraso, len(atrasos), cfg.p.atraso_max_ms, "" if med_atraso < 3000 else " (acerte o relogio do computador)"))
        # H2 ordem de users: um agressor comprador (B) paga pelo menos a melhor venda; um vendedor (A)
        # recebe no maximo a melhor compra (tolerancia de um tick, pelo bbo imediatamente anterior)
        confere = viola = 0
        for tr in negocios:
            if not bbo or not tr.get("users"):
                continue
            anteriores = [b for b in bbo if b[0] <= tr["_local"]]
            if not anteriores:
                continue
            _, b, a = anteriores[-1]
            px = float(tr["px"])
            tick = nu.passo_preco(px, sz.get(nome, 0))
            if tr.get("side") == "B":
                confere += 1
                if px < a - tick:
                    viola += 1
            elif tr.get("side") == "A":
                confere += 1
                if px > b + tick:
                    viola += 1
        if confere:
            linha(True if viola <= 0.1 * confere else None,
                  "H2 lado dos negocios contra o bbo: %d de %d incoerentes%s" % (
                      viola, confere, "" if viola <= 0.1 * confere else " (o lado passa a derivar do tick: lado_por_tick)"))
        else:
            linha(None, "H2 sem negocios com users e bbo no mesmo instante; nao confirmada")
        # H5 hash a zeros
        zeros = sum(1 for tr in negocios if _hash_a_zeros(tr.get("hash")))
        frac = zeros / len(negocios)
        linha(True if 0.001 <= frac <= 0.20 else None,
              "H5 negocios com hash a zeros: %.1f %% (%d de %d; esperado 0,1 a 20 %%)%s" % (
                  100 * frac, zeros, len(negocios), "" if 0.001 <= frac <= 0.20 else " (TWAP so pela cadencia: twapsrc=cad)"))
    else:
        linha(None, "nenhum negocio de %s em 25 s: H2, H5 e H8 nao confirmadas (normal em activos calmos)" % nome)
    if "activeAssetCtx" in visto:
        ctx = (visto["activeAssetCtx"][0] or {}).get("ctx") or {}
        oi, mark, fund = flt(ctx.get("openInterest")), flt(ctx.get("markPx")), flt(ctx.get("funding"))
        usd = oi * mark if oi == oi and mark == mark else NAN
        lo, hi = (1e6, 1e11) if nome.upper() == "BTC" else (1e4, 1e12)
        linha(True if lo <= usd <= hi else None, "H3 openInterest x markPx = %.3g usd (openInterest %s em unidades do activo)" % (
            usd, ctx.get("openInterest")))
        try:
            fh = pedir_info(cfg.url_info, {"type": "fundingHistory", "coin": nome, "startTime": agora - 3 * MS_1H})
            ult = max((x for x in fh if isinstance(x, dict)), key=lambda x: flt(x.get("time")), default=None)
            f_hist = flt(ult.get("fundingRate")) if ult else NAN
            perto = fund == fund and f_hist == f_hist and (abs(fund - f_hist) <= 1e-5 or abs(fund - f_hist) <= 0.5 * abs(f_hist))
            linha(True if perto else None, "H4 funding do activeAssetCtx %s contra o ultimo fundingHistory %s (por hora)" % (
                ctx.get("funding"), ult.get("fundingRate") if ult else "?"))
        except Exception as e:
            linha(None, "H4 fundingHistory falhou (%s)" % e)
        linha("premium" in ctx and "oraclePx" in ctx and "dayNtlVlm" in ctx, "contexto: premium %s, oraclePx %s, dayNtlVlm %s" % (
            ctx.get("premium"), ctx.get("oraclePx"), ctx.get("dayNtlVlm")))
    else:
        linha(False, "nao chegou o contexto do activo (activeAssetCtx): H3 e H4 nao confirmadas")

    # H6 leaderboard
    try:
        rows = obter_leaderboard(cfg)
        r0 = rows[0] if rows else {}
        campos = all(k in r0 for k in ("ethAddress", "accountValue", "windowPerformances"))
        semana = _janela_leaderboard(r0, "week") if r0 else {"roi": NAN}
        linha(True if rows and campos and semana["roi"] == semana["roi"] else None,
              "H6 leaderboard: %d linhas; campos ethAddress, accountValue, windowPerformances %s%s" % (
                  len(rows), "presentes" if campos else "em falta",
                  "" if rows and campos else " (uso o ultimo dados/baleias.csv: %d hashes)" % len(carregar_baleias(cfg.baleias_csv))))
    except Exception as e:
        linha(None, "H6 leaderboard falhou (%s); uso o ultimo dados/baleias.csv (%d hashes)" % (e, len(carregar_baleias(cfg.baleias_csv))))
    for e in cfg.p.enderecos_excluidos:
        try:
            vd = pedir_info(cfg.url_info, {"type": "vaultDetails", "vaultAddress": e})
            linha(bool(vd) and isinstance(vd, dict) and bool(vd.get("name")), "cofre excluido %s...: %s" % (
                e[:10], (vd or {}).get("name") if isinstance(vd, dict) else "sem resposta"))
        except Exception as ex:
            linha(None, "vaultDetails de %s... falhou (%s)" % (e[:10], ex))

    # H7 CoinGlass
    if cfg.cg_chave:
        try:
            itens, fonte = await asyncio.to_thread(fundo_de_liquidacoes, cfg)
            linha(True if fonte == "cg" else None, "H7 CoinGlass: %d liquidacoes; %s" % (
                len(itens), "a Hyperliquid esta no fluxo (liqsrc=cg)" if fonte == "cg" else "sem Hyperliquid; liquidacoes agregadas (liqsrc=agg)"))
        except Exception as e:
            linha(None, "H7 CoinGlass REST falhou: %s" % sem_chave(e, cfg.cg_chave))
    else:
        linha(None, "H7 sem chave CoinGlass: T4 vale 0 e liq=na")
    return ok_total


def cmd_verificar(cfg: ConfigSinalizador) -> None:
    ok = asyncio.run(_verificar(cfg))
    print("\n" + ("Tudo certo: pode arrancar o sinalizador." if ok else "Ha falhas acima. Copie este texto todo para se poder corrigir."))
    sys.exit(0 if ok else 1)


# ==========================================================================
# Comandos: historico, baleias, ensaios, reset
# ==========================================================================
def cmd_historico(cfg: ConfigSinalizador, desde: str = "") -> None:
    """candleSnapshot (1 h e 15 m) e fundingHistory para dados/velas e dados/funding, so o que falta."""
    agora = agora_ms()
    inicio_pedido = interpretar_hora(desde, 0) if desde else 0
    orc = OrcamentoPeso(PESO_MINUTO_MAX)          # o arranque e o comando historico espalham-se por minutos
    for nome in cfg.ativos:
        for intervalo in ("1h", "15m"):
            h = Historico(nome, intervalo, cfg.pasta_velas)
            h.carregar_disco()
            ult = h.ultima()
            inicio = ult.t + h.ms if ult else agora - VELAS_POR_PEDIDO * h.ms
            if inicio_pedido:
                inicio = min(inicio, inicio_pedido)
            try:
                n = h.carregar_snapshot(cfg, inicio, orcamento=orc)
            except Exception as e:
                print("%s %s: candleSnapshot falhou (%s)" % (nome, intervalo, e))
                continue
            for v in h.ultimas(len(h)):
                h.guardar(v, {})
            print("%s %s: %d velas novas; %d em memoria; ultima %s" % (
                nome, intervalo, n, len(h), iso_utc(h.ultima().T) if h.ultima() else "-"))
        existente = carregar_funding(cfg, nome)
        inicio_f = existente[-1][0] + 1 if existente else agora - 30 * MS_DIA
        if inicio_pedido:
            inicio_f = min(inicio_f, inicio_pedido)
        try:
            n_f = guardar_funding(cfg, nome, obter_funding_history(cfg, nome, inicio_f, orcamento=orc))
            print("%s funding: %d horas novas" % (nome, n_f))
        except Exception as e:
            print("%s fundingHistory falhou (%s)" % (nome, e))


def cmd_baleias(cfg: ConfigSinalizador) -> None:
    """Refaz dados/baleias.csv a partir do leaderboard com os filtros da seccao 6.5 (sem a direccionalidade, que e ao vivo)."""
    try:
        rows = obter_leaderboard(cfg)
    except Exception as e:
        raise SystemExit("Leaderboard falhou (%s). A lista anterior tem %d hashes." % (e, len(carregar_baleias(cfg.baleias_csv))))
    enderecos = construir_baleias(cfg, rows, {}, set(), set(cfg.p.enderecos_excluidos))
    if not enderecos:
        raise SystemExit("Nenhuma linha do leaderboard passa os filtros (%d linhas lidas); fica a lista anterior." % len(rows))
    guardar_baleias(cfg.baleias_csv, rows, enderecos, agora_ms())
    print("Lista de baleias: %d enderecos de %d linhas; so prefixo e hash em %s" % (len(enderecos), len(rows), cfg.baleias_csv))


def registar_ensaio(cfg: ConfigSinalizador, n: int = 0, expectancia: float = NAN, ep: float = NAN, sr: float = NAN) -> bool:
    """Acrescenta a configuracao em vigor a dados/ensaios.csv se o seu hash ainda la nao estiver; devolve True se acrescentou."""
    hashes = {ln.get("hash", "") for ln in _ler_csv(cfg.ensaios_csv)}
    if cfg.variante in hashes:
        return False
    params = cfg.parametros_variante()
    reg = Registo(cfg.ensaios_csv, COLUNAS_ENSAIOS)
    reg.escrever({"data": iso_utc(agora_ms()), "hash": cfg.variante, "n": n, "expectancia_bps": num(expectancia, 2),
                  "ep_bps": num(ep, 2), "sr": num(sr, 4),
                  "parametros": ";".join("%s=%s" % (k, ("%.6g" % params[k])) for k in sorted(params))})
    return True


def cmd_ensaios(cfg: ConfigSinalizador) -> None:
    novo = registar_ensaio(cfg)
    linhas = _ler_csv(cfg.ensaios_csv)
    print("Ensaios registados: %d (M do DSR)%s" % (len(linhas), "; a configuracao em vigor foi acrescentada" if novo else ""))
    for ln in linhas:
        print("  %s  %s  n=%s  expectancia %s bps  EP %s  SR %s%s" % (
            ln.get("data", ""), ln.get("hash", ""), ln.get("n", ""), ln.get("expectancia_bps") or "-",
            ln.get("ep_bps") or "-", ln.get("sr") or "-", "  <- em vigor" if ln.get("hash") == cfg.variante else ""))


def cmd_reset(cfg: ConfigSinalizador) -> None:
    """Levanta os disjuntores em estado_sinalizador.json (escrita atomica); o sinalizador a correr le-o no arranque seguinte."""
    doc: Dict[str, Any] = {}
    try:
        with open(cfg.estado_sinalizador_json, "r", encoding="utf-8") as f:
            doc = json.load(f) or {}
    except (OSError, ValueError):
        doc = {}
    doc["disjuntores"] = {"perdas_dia": "", "cvar": False}
    for e in (doc.get("ativos") or {}).values():
        if isinstance(e, dict):
            e["parado"] = None
    doc["hora_utc"] = iso_utc(agora_ms())
    doc["reset"] = True
    escrever_json_atomico(cfg.estado_sinalizador_json, doc)
    print("Disjuntores levantados em %s. Se o sinalizador estiver a correr, pare-o e volte a arranca-lo." % cfg.estado_sinalizador_json)


# ==========================================================================
# Comando: relatorio (seccoes 11.5 e 14)
# ==========================================================================
def _ep(xs: List[float]) -> float:
    xs = [x for x in xs if x == x]
    n = len(xs)
    if n < 2:
        return NAN
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n)


def juntar_registos(trades: List[Dict[str, str]], sinais_medidor: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Liga cada trade virtual a linha do registo_sinais.csv do medidor com o mesmo activo e lado e hora a menos de 2 s (ou a mesma nota)."""
    por_ativo: Dict[str, List[Tuple[int, Dict[str, str]]]] = {}
    for s in sinais_medidor:
        try:
            ms = interpretar_hora(s.get("hora_utc", ""), 0)
        except ValueError:
            continue
        por_ativo.setdefault(s.get("ativo", ""), []).append((ms, s))
    out = []
    for tr in trades:
        try:
            ms = interpretar_hora(tr.get("hora", ""), 0)
        except ValueError:
            ms = 0
        lig = None
        for ms_s, s in por_ativo.get(tr.get("ativo", ""), []):
            if s.get("lado") == tr.get("lado") and (abs(ms_s - ms) <= 2000 or (tr.get("nota") and s.get("nota") == tr.get("nota"))):
                lig = s
                break
        out.append({"trade": tr, "medidor": lig, "campos": rv.interpretar_nota(tr.get("nota", ""))})
    return out


def _caixa(campo: str, valor: str) -> str:
    """Caixa de um campo da nota para agrupar: sinal de flx e fz; na/baixo/alto de liq; o proprio valor nos outros."""
    if campo in ("flx", "fz"):
        v = flt(valor)
        if v != v:
            return "na"
        return "pos" if v > 0 else ("neg" if v < 0 else "zero")
    if campo == "liq":
        v = flt(valor)
        return "na" if v != v else ("<=0.5" if v <= 0.5 else ">0.5")
    return valor or "na"


def cmd_relatorio(cfg: ConfigSinalizador) -> None:
    out: List[str] = []
    w = out.append

    def f2(v: float) -> str:
        return "%.2f" % v if v == v and abs(v) != float("inf") else "-"

    trades = _ler_csv(cfg.reg_sinalizador)
    fechados = [t for t in trades if t.get("r_liq_bps", "") != ""]
    med = _ler_csv(cfg.reg_sinais)
    ligados = juntar_registos(fechados, med)
    w("RELATORIO DO SINALIZADOR %s  (%s)  variante %s" % (VERSAO, datetime.now().strftime("%Y-%m-%d %H:%M"), cfg.variante))
    w("Trades virtuais fechados: %d (%d ligados a sinais medidos pelo medidor)" % (len(fechados), sum(1 for x in ligados if x["medidor"])))
    w("")

    def resumo(titulo: str, grupo: List[Dict[str, Any]], c_w: float, c_l: float, recuo: str = "   ") -> None:
        if not grupo:
            return
        r = [flt(x["trade"]["r_liq_bps"]) for x in grupo]
        n = len(r)
        p_hat = sum(1 for v in r if v > 0) / n
        g_med = nu.mediana([flt(x["trade"].get("G")) for x in grupo])
        l_med = nu.mediana([flt(x["trade"].get("L")) for x in grupo])
        p_est = nu.acerto_equilibrio(g_med, l_med, c_w, c_l)
        p_inf = rv.wilson_inferior(p_hat, n)
        m = rv.margem_decisao(p_hat, n, cfg.p_tam.m_min)
        ep = _ep(r)
        motivos = {}
        for x in grupo:
            k = x["trade"].get("motivo", "")
            motivos[k] = motivos.get(k, 0) + 1
        w("%s%-18s n=%-4d expectancia %s bps (EP %s) | p_hat %.3f p_inf %.3f | p* %s margem %s -> %s | mediana G %s L %s | %s" % (
            recuo, titulo, n, f2(nu.media(r)), f2(ep), p_hat, p_inf, f2(p_est), f2(m),
            "op" if p_inf == p_inf and p_est == p_est and p_inf > p_est + m else "sombra",
            f2(g_med), f2(l_med), " ".join("%s %d" % kv for kv in sorted(motivos.items()))))

    w("1. RESULTADOS POR ACTIVO  (r_liq em bps; p* com os custos medidos pelo medidor quando ha 30 aberturas)")
    if not fechados:
        w("   Ainda nao ha trades virtuais fechados.")
    custos = {n: custos_medidos(cfg, n) for n in cfg.ativos}
    for nome in cfg.ativos:
        c_w, c_l, n_ab = custos[nome]
        grupo = [x for x in ligados if x["trade"].get("ativo") == nome]
        if grupo:
            w("   %s: custos c_W %.1f c_L %.1f bps (%s)" % (nome, c_w, c_l, "medidos em %d aberturas" % n_ab if n_ab >= 30 else "defeitos"))
            resumo("  todos", grupo, c_w, c_l)
            for fase in ("cal", "op", "sombra"):
                resumo("  fase " + fase, [x for x in grupo if x["trade"].get("fase") == fase], c_w, c_l)
    w("")
    w("2. PELA NOTA  (var, ctx, bal, flx, fz, liq, ses)")
    c_w_m = nu.media([custos[n][0] for n in cfg.ativos])
    c_l_m = nu.media([custos[n][1] for n in cfg.ativos])
    for campo in ("var", "ctx", "bal", "flx", "fz", "liq", "ses"):
        grupos: Dict[str, List[Dict[str, Any]]] = {}
        for x in ligados:
            grupos.setdefault(_caixa(campo, x["campos"].get(campo, "")), []).append(x)
        if not grupos:
            continue
        w("   %s:" % campo)
        for chave in sorted(grupos):
            resumo("  %s=%s" % (campo, chave), grupos[chave], c_w_m, c_l_m, "     ")
    w("")
    w("3. CRUZAMENTO COM O MEDIDOR  (r_900, r_3600, r_14400 do registo_sinais.csv; regra passiva/agressiva nos ligados)")
    com_med = [x for x in ligados if x["medidor"]]
    if not com_med:
        w("   Ainda nao ha trades ligados a sinais medidos.")
    else:
        for h in (900, 3600, 14400):
            col = "r_%d" % h
            rs = [flt(x["medidor"].get(col)) for x in com_med if x["medidor"].get(col, "") != ""]
            if rs:
                w("   %-8s n=%-4d media %s bps (EP %s) | r_liq virtual nos mesmos %s bps" % (
                    col, len(rs), f2(nu.media(rs)), f2(_ep(rs)),
                    f2(nu.media([flt(x["trade"]["r_liq_bps"]) for x in com_med if x["medidor"].get(col, "") != ""]))))
        col = "r_%d" % cfg.h_regra
        regra = [x["medidor"] for x in com_med if x["medidor"].get("imp_viavel") == "1" and x["medidor"].get("imp_bps", "") != ""
                 and x["medidor"].get(col, "") != "" and x["medidor"].get("sombra_preenchida") in ("0", "1")]
        if regra:
            ench = [s for s in regra if s["sombra_preenchida"] == "1"]
            pi = len(ench) / len(regra)
            v_ag, v_pas, escolha = nu.regra_rotas(
                nu.media([flt(s[col]) for s in regra]), nu.media([flt(s["imp_bps"]) for s in regra]), pi,
                nu.media([flt(s[col]) for s in ench]) if ench else NAN,
                nu.media([flt(s["ganho_passiva_bps"]) for s in ench]) if ench else NAN, cfg.taxa_taker, cfg.taxa_maker)
            dif, erro = nu.margem_regra([flt(s[col]) for s in regra], [flt(s["imp_bps"]) for s in regra],
                                        [s["sombra_preenchida"] == "1" for s in regra],
                                        [flt(s["ganho_passiva_bps"]) for s in regra], cfg.taxa_taker, cfg.taxa_maker)
            w("   regra aos %d s (n=%d): V agressiva %s | V passiva %s -> %s; diferenca %s bps, EP %s: %s" % (
                cfg.h_regra, len(regra), f2(v_ag), f2(v_pas), escolha.upper(), f2(dif), f2(erro),
                "clara (2 EP)" if nu.margem_clara(dif, erro) else "dentro do ruido"))
    w("")
    w("4. CALIBRACAO DE P_teo POR CAIXAS DE z0  (alvo antes do stop; ECE e Brier)")
    caixas = [(0.0, 1.5), (1.5, 1.75), (1.75, 2.0), (2.0, 2.5), (2.5, 9.9)]
    soma_ece = 0.0
    brier = []
    n_tot = 0
    for lo, hi in caixas:
        grupo = [x for x in ligados if lo <= abs(flt(x["campos"].get("z0"))) < hi and flt(x["campos"].get("P")) == flt(x["campos"].get("P"))]
        if not grupo:
            continue
        p_teo = nu.media([flt(x["campos"].get("P")) for x in grupo])
        y = [1.0 if x["trade"].get("motivo") == "alvo" else 0.0 for x in grupo]
        p_emp = sum(y) / len(y)
        soma_ece += len(y) * abs(p_emp - p_teo)
        n_tot += len(y)
        brier.extend((flt(x["campos"].get("P")) - yy) ** 2 for x, yy in zip(grupo, y))
        w("   |z0| em [%.2f, %.2f): n=%-4d P_teo %.3f | acertos %.3f (Wilson inf %.3f)" % (
            lo, hi, len(y), p_teo, p_emp, rv.wilson_inferior(p_emp, len(y))))
    if n_tot:
        w("   ECE %.3f | Brier %.3f (n=%d)" % (soma_ece / n_tot, nu.media(brier), n_tot))
    else:
        w("   Sem trades com z0 e P na nota.")
    w("")
    w("Os limiares das baleias (bal, flx, fz, liq) so se promovem com expectancia positiva a 2 erros padrao; ate la ficam congelados.")
    texto = "\n".join(out)
    print(texto)
    os.makedirs(cfg.pasta, exist_ok=True)
    with open(os.path.join(cfg.pasta, "relatorio_sinalizador.txt"), "w", encoding="utf-8") as f:
        f.write(texto + "\n")


# ==========================================================================
# Entrada
# ==========================================================================
def main(argv: Optional[List[str]] = None) -> None:
    aqui = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Sinalizador de reversao para a Hyperliquid (escreve em sinais.csv; nao envia ordens).")
    ap.add_argument("--config", default=os.path.join(aqui, "config.ini"), help="caminho do config.ini")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("correr", help="corre o sinalizador (e o que acontece sem comando)")
    sub.add_parser("verificar", help="confere as hipoteses H1 a H8 da API")
    ph = sub.add_parser("historico", help="descarrega candleSnapshot e fundingHistory para dados/")
    ph.add_argument("--desde", default="", help="hora inicial (ISO ou epoch); por defeito so o que falta")
    sub.add_parser("baleias", help="refaz a lista diaria de baleias")
    sub.add_parser("relatorio", help="cruza o registo proprio com o do medidor")
    sub.add_parser("ensaios", help="lista as configuracoes experimentadas")
    sub.add_parser("reset", help="levanta os disjuntores")
    args = ap.parse_args(argv)
    cfg = ConfigSinalizador(args.config)
    os.makedirs(cfg.pasta, exist_ok=True)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.handlers.RotatingFileHandler(cfg.log_sinalizador, maxBytes=5_000_000,
                                                                       backupCount=3, encoding="utf-8")])
    if args.cmd == "verificar":
        cmd_verificar(cfg)
    elif args.cmd == "historico":
        cmd_historico(cfg, args.desde)
    elif args.cmd == "baleias":
        cmd_baleias(cfg)
    elif args.cmd == "relatorio":
        cmd_relatorio(cfg)
    elif args.cmd == "ensaios":
        cmd_ensaios(cfg)
    elif args.cmd == "reset":
        cmd_reset(cfg)
    else:
        cmd_correr(cfg)


if __name__ == "__main__":
    main()
