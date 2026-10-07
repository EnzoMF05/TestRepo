#!/usr/bin/env python3
"""Medidor de execucao para a Hyperliquid (Etapa 2).

So le dados publicos e, se indicar o endereco da conta, os seus fills.
Nao tem chaves e nao envia ordens.

Comandos:
    python3 medidor.py verificar     testa as ligacoes e o formato dos dados
    python3 medidor.py               corre o medidor (Ctrl+C para parar)
    python3 medidor.py sinal BTC compra [preco]   regista um sinal agora
    python3 medidor.py relatorio     resume o que foi medido

Tudo o que mede fica na pasta de dados (por defeito "dados"), no seu computador.
"""
from __future__ import annotations

import argparse
import asyncio
import configparser
import csv
import json
import logging
import logging.handlers
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

import nucleo as nu

VERSAO = "2.4"
log = logging.getLogger("medidor")

URLS = {
    "mainnet": ("wss://api.hyperliquid.xyz/ws", "https://api.hyperliquid.xyz/info"),
    "testnet": ("wss://api.hyperliquid-testnet.xyz/ws", "https://api.hyperliquid-testnet.xyz/info"),
}
URL_COINGLASS = "wss://open-ws.coinglass.com/ws-api?cg-api-key="


# ==========================================================================
# Configuracao
# ==========================================================================
def _lista(txt: str) -> List[str]:
    return [p.strip() for p in (txt or "").replace(";", ",").split(",") if p.strip()]


class Config:
    def __init__(self, caminho: str):
        self.caminho = os.path.abspath(caminho)
        self.base = os.path.dirname(self.caminho)
        cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        if not cp.read(self.caminho, encoding="utf-8"):
            raise SystemExit("Nao encontrei o ficheiro de configuracao: %s" % self.caminho)

        def g(sec: str, chave: str, defeito: str) -> str:
            return cp.get(sec, chave, fallback=defeito).strip()

        self.ativos = _lista(g("geral", "ativos", "BTC, ETH, SOL"))
        if not self.ativos:
            raise SystemExit("config.ini: indique pelo menos um activo em [geral] ativos")
        self.endereco = g("geral", "endereco", "").lower()
        if self.endereco and not (self.endereco.startswith("0x") and len(self.endereco) == 42):
            raise SystemExit("config.ini: o endereco tem de ter a forma 0x seguido de 40 caracteres")
        pasta = g("geral", "pasta_dados", "dados")
        self.pasta = pasta if os.path.isabs(pasta) else os.path.join(self.base, pasta)
        rede = g("geral", "rede", "mainnet").lower()
        if rede not in URLS:
            raise SystemExit("config.ini: rede tem de ser mainnet ou testnet")
        self.url_ws, self.url_info = URLS[rede]

        self.alvo_bps = float(g("orcamento", "alvo_bps", "30"))
        self.fraccao = float(g("orcamento", "fraccao_alvo", "0.15"))
        self.theta = float(g("orcamento", "desconto_livro", "0.5"))
        self.tamanho_usd = float(g("orcamento", "tamanho_usd", "1000"))
        self.taxa_taker = float(g("orcamento", "taxa_taker_bps", "4.5"))
        self.taxa_maker = float(g("orcamento", "taxa_maker_bps", "1.5"))
        self.orcamento = nu.orcamento_bps(self.alvo_bps, self.fraccao)

        self.janela_h = float(g("detector", "janela_horas", "24"))
        self.aquecimento_min = float(g("detector", "aquecimento_min", "30"))
        self.p_amarelo = float(g("detector", "percentil_amarelo", "80")) / 100.0
        self.p_vermelho = float(g("detector", "percentil_vermelho", "95")) / 100.0
        self.piso_spread = float(g("detector", "piso_spread_bps", "1.0"))
        self.piso_prof_x = float(g("detector", "piso_profundidade_x", "20"))
        self.piso_vol = float(g("detector", "piso_vol_razao", "1.5"))
        self.piso_int = float(g("detector", "piso_intensidade_razao", "2.0"))
        self.piso_ofi = float(g("detector", "piso_ofi", "1.0"))
        self.piso_liq = float(g("detector", "piso_liquidacoes_usd", "100000"))
        self.piso_mo = float(g("detector", "piso_mark_oraculo_bps", "5.0"))
        # O fluxo de ordens tem lado: serve para escolher a rota, nao para medir stress.
        self.fluxo_no_estado = g("detector", "fluxo_no_estado", "nao").lower() in ("sim", "s", "yes", "true", "1")

        self.prazo_s = float(g("sombra", "prazo_s", "30"))
        self.horizontes = sorted({int(float(x)) for x in _lista(g("sombra", "horizontes_s", "5, 30, 60, 300"))})
        self.h_regra = int(float(g("sombra", "horizonte_regra_s", "300")))
        if self.h_regra not in self.horizontes:
            self.horizontes = sorted(set(self.horizontes) | {self.h_regra})
        self.janela_sinal_s = float(g("sombra", "janela_sinal_s", "900"))
        self.horizontes_fill = sorted({int(float(x)) for x in _lista(g("sombra", "horizontes_fill_s", "5, 30, 60"))})

        self.painel_s = float(g("painel", "intervalo_s", "5"))
        self.guardar_dias = int(float(g("geral", "guardar_dias", "30")))

        self.cg_chave = g("coinglass", "chave", "")
        self.cg_canal = g("coinglass", "canal", "liquidation_orders")
        # A documentacao escreve o canal das duas formas. Subscreve-se as duas;
        # a mesma liquidacao nao conta duas vezes.
        pedidos = _lista(g("coinglass", "canais", "")) or [self.cg_canal]
        alternativa = {"liquidation_orders": "liquidationOrders", "liquidationOrders": "liquidation_orders"}
        for nome in list(pedidos):
            outro = alternativa.get(nome)
            if outro and outro not in pedidos:
                pedidos.append(outro)
        self.cg_canais = pedidos
        self.cg_rest = g("coinglass", "rest", "sim").lower() in ("sim", "s", "yes", "true", "1")
        self.cg_rest_ex = _lista(g("coinglass", "rest_exchanges", "Binance, OKX, Bybit")) or ["Binance"]
        self.cg_rest_min = float(g("coinglass", "rest_min_usd", "10000"))
        self.cg_rest_s = float(g("coinglass", "rest_intervalo_s", "10"))

        # Parametros avancados: so se mexem em testes.
        self.amostra_s = float(g("avancado", "amostra_s", "1"))
        self.passo_hist = int(float(g("avancado", "passo_historico", "5")))
        self.url_ws = g("avancado", "url_ws", self.url_ws)
        self.url_info = g("avancado", "url_info", self.url_info)
        self.url_cg = g("avancado", "url_coinglass", URL_COINGLASS)
        self.sem_dados_ms = int(float(g("avancado", "sem_dados_s", "10")) * 1000)
        self.vigia_s = float(g("avancado", "vigia_s", "30"))

        self.n_janela = max(10, int(self.janela_h * 3600 / (self.amostra_s * self.passo_hist)))
        self.n_aquecimento = max(5, int(self.aquecimento_min * 60 / (self.amostra_s * self.passo_hist)))
        self.sinais_csv = os.path.join(self.pasta, "sinais.csv")
        self.reg_sinais = os.path.join(self.pasta, "registo_sinais.csv")
        self.reg_fills = os.path.join(self.pasta, "registo_fills.csv")
        self.pasta_metricas = os.path.join(self.pasta, "metricas")
        self.estado_json = os.path.join(self.pasta, "estado.json")


# ==========================================================================
# Utilitarios
# ==========================================================================
def agora_ms() -> int:
    return int(time.time() * 1000)


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ms % 1000)


_RE_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:[.,](\d+))?\s*(Z|z|[+-]\d{2}:?\d{2})?$")


def interpretar_hora(txt: str, defeito_ms: int) -> int:
    """Vazio = agora. Numero = epoch (s ou ms). Texto ISO sem fuso = hora local.

    Levanta ValueError se o texto nao for uma hora reconhecivel.
    """
    t = (txt or "").strip()
    if not t:
        return defeito_ms
    try:
        v: Optional[float] = float(t)
    except ValueError:
        v = None
    if v is not None:
        if not (0 < v < 1e14):
            raise ValueError("hora fora do intervalo: %r" % txt)
        return int(v if v > 1e11 else v * 1000)
    m = _RE_ISO.match(t)
    if not m:
        raise ValueError("hora nao reconhecida: %r" % txt)
    dia, hms, frac, fuso = m.groups()
    if len(hms) == 5:
        hms += ":00"
    dt = datetime.strptime(dia + " " + hms, "%Y-%m-%d %H:%M:%S")
    dt = dt.replace(microsecond=int((frac or "0")[:6].ljust(6, "0")))
    if fuso is None:
        dt = dt.astimezone()  # sem fuso: hora local do computador
    elif fuso in ("Z", "z"):
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        sinal = 1 if fuso[0] == "+" else -1
        dt = dt.replace(tzinfo=timezone(sinal * timedelta(hours=int(fuso[1:3]), minutes=int(fuso[-2:]))))
    return int(round(dt.timestamp() * 1000))


def num(x: Any, casas: int = 3) -> str:
    """Numero para CSV: vazio se nao existir."""
    if x is None:
        return ""
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return ""
        return ("%." + str(casas) + "f") % x
    return str(x)


def flt(txt: Any) -> float:
    try:
        return float(txt)
    except (TypeError, ValueError):
        return float("nan")


def base_da_liquidacao(it: Dict[str, Any]) -> str:
    """Activo de uma liquidacao da CoinGlass, em maiusculas.

    Usa o campo da base se existir; senao tira-o do simbolo (BTCUSDT, BTC-USD-PERP...).
    """
    base = str(it.get("base_asset") or it.get("baseAsset") or "").upper()
    if base:
        return base
    sim = re.sub(r"[-_/: ]", "", str(it.get("symbol") or "").upper())
    for _ in range(2):
        for suf in ("PERP", "SWAP", "USDT", "USDC", "USD"):
            if sim.endswith(suf) and len(sim) > len(suf):
                sim = sim[: -len(suf)]
    return sim


def volume_liquidacao(it: Dict[str, Any]) -> float:
    """Dolares de uma liquidacao.

    O websocket escreve volume_usd (canal liquidation_orders) ou volUsd (canal
    liquidationOrders); o REST v4 usa usd_value.
    """
    for chave in ("volume_usd", "volumeUsd", "volUsd", "vol_usd", "usd_value", "usdValue"):
        v = flt(it.get(chave))
        if v == v and 0 < v < float("inf"):
            return v
    return float("nan")


def lado_liquidacao(it: Dict[str, Any]) -> int:
    """1 = long liquidado (venda forcada). 2 = short liquidado (compra forcada)."""
    try:
        n = int(it.get("side"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return n if n in (1, 2) else 0


def chave_liquidacao(it: Dict[str, Any]) -> Tuple[Any, ...]:
    """Identidade de uma liquidacao, para o websocket e o REST nao a somarem duas vezes."""
    vol = volume_liquidacao(it)
    usd = int(round(vol / 10.0)) * 10 if vol == vol else 0
    t = int(flt(it.get("time") or 0) // 1000)
    px = flt(it.get("price") or 0)
    pxk = round(px, 2) if px >= 100 else round(px, 6)
    ex = str(it.get("exchange") or it.get("exchange_name") or it.get("exName") or "").upper()
    return (ex, base_da_liquidacao(it), t, pxk, lado_liquidacao(it), usd)


def positivo(txt: Any) -> Optional[float]:
    """Numero finito e maior do que zero, ou None."""
    v = flt(txt)
    return v if v == v and 0 < v < float("inf") else None


class Registo:
    """Ficheiro CSV so de acrescento.

    Abre e fecha em cada linha. Assim o ficheiro pode ser aberto, copiado ou
    substituido por outro programa sem o medidor ficar a escrever numa copia
    que ja ninguem ve.
    """

    def __init__(self, caminho: str, colunas: List[str]):
        self.caminho = caminho
        self.colunas = list(colunas)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        self._acertar_cabecalho()
        if not os.path.exists(caminho) or os.path.getsize(caminho) == 0:
            with open(caminho, "w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(self.colunas)

    def _cabecalho(self) -> Optional[List[str]]:
        try:
            with open(self.caminho, "r", encoding="utf-8", newline="") as f:
                primeira = f.readline()
        except OSError:
            return None
        if not primeira.strip():
            return None
        return next(csv.reader([primeira]), None)

    def _acertar_cabecalho(self) -> None:
        """Se as colunas mudaram (novos horizontes, por exemplo), migra sem perder linhas."""
        antigo = self._cabecalho()
        if antigo is None or antigo == self.colunas:
            return
        with open(self.caminho, "r", encoding="utf-8", newline="") as f:
            linhas = list(csv.DictReader(f))
        self.colunas = self.colunas + [c for c in antigo if c and c not in self.colunas]
        copia = self.caminho + ".anterior-%d" % int(time.time())
        os.replace(self.caminho, copia)
        with open(self.caminho, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.colunas, extrasaction="ignore")
            w.writeheader()
            for ln in linhas:
                ln.pop(None, None)
                w.writerow(ln)
        log.info("%s: colunas actualizadas, %d linhas mantidas (copia em %s)",
                 os.path.basename(self.caminho), len(linhas), os.path.basename(copia))

    def escrever(self, linha: Dict[str, Any]) -> None:
        novo = not os.path.exists(self.caminho) or os.path.getsize(self.caminho) == 0
        with open(self.caminho, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.colunas, extrasaction="ignore")
            if novo:
                w.writeheader()
            w.writerow(linha)

    def fechar(self) -> None:
        return None


COLUNAS_METRICAS = [
    "hora_utc", "local_ms", "mid", "bid", "ask", "qb", "qa", "spread_bps", "spread_ticks",
    "prof_util_usd", "imp_ref_bps", "sigma_curta", "sigma_longa", "vol_razao",
    "intens_razao", "ofi_norm", "liq_60s_usd", "mark_oraculo_bps", "desequilibrio",
    "estado", "motivos",
]


class RegistoDiario:
    """Um CSV de metricas por activo e por dia (UTC)."""

    def __init__(self, pasta: str, nome: str):
        self.pasta = pasta
        self.nome = nome.replace("/", "_").replace(":", "_")
        self._dia = ""
        self._reg: Optional[Registo] = None

    def escrever(self, ms: int, linha: Dict[str, Any]) -> None:
        dia = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        if dia != self._dia:
            if self._reg:
                self._reg.fechar()
            self._reg = Registo(os.path.join(self.pasta, "%s_%s.csv" % (self.nome, dia)), COLUNAS_METRICAS)
            self._dia = dia
        assert self._reg is not None
        self._reg.escrever(linha)

    def fechar(self) -> None:
        if self._reg:
            self._reg.fechar()


# ==========================================================================
# Estado de um activo
# ==========================================================================
class Ativo:
    HIST_BBO = 30000      # eventos de melhor preco guardados (cerca de 15 min ou mais)
    HIST_NEG = 60000      # negocios guardados
    AMOSTRAS = 7200       # amostras de 1 s guardadas para consultas (2 h)

    def __init__(self, nome: str, cfg: Config, sz_decimals: int):
        self.nome = nome
        self.cfg = cfg
        self.sz_decimals = sz_decimals
        self.b: Optional[float] = None
        self.a: Optional[float] = None
        self.qb = 0.0
        self.qa = 0.0
        self.exch_ms = 0            # hora da bolsa do ultimo bbo
        self.exch_visto_ms = 0      # maior hora da bolsa vista em qualquer mensagem
        self.ultimo_local_ms = 0    # hora local da ultima mensagem deste activo
        self.tol_ms = min(cfg.sem_dados_ms, 3000)
        self.bids: List[nu.Nivel] = []
        self.asks: List[nu.Nivel] = []
        self.livro_local_ms = 0
        self.mark: Optional[float] = None
        self.oraculo: Optional[float] = None
        # historicos curtos, um unico dono: o ciclo de eventos
        self.hist_bbo: Deque[Tuple[int, int, float, float, float, float]] = deque(maxlen=self.HIST_BBO)
        self.hist_neg: Deque[Tuple[int, int, float, float]] = deque(maxlen=self.HIST_NEG)
        self.amostras: Deque[Tuple[int, float, float, float, float, float, str]] = deque(maxlen=self.AMOSTRAS)
        self.atrasos: Deque[int] = deque(maxlen=200)
        # periodos sem dados: [inicio local, fim local, inicio bolsa, fim bolsa]; fim None = em curso
        self.cortes: Deque[List[Optional[int]]] = deque(maxlen=500)
        # acumuladores entre amostras
        self.notional = 0.0
        self.ofi = nu.JanelaSoma(10_000)
        self.liq = nu.JanelaSoma(60_000)
        self.liq_long = nu.JanelaSoma(60_000)    # side 1: longs liquidados
        self.liq_short = nu.JanelaSoma(60_000)   # side 2: shorts liquidados
        self.mid_ant: Optional[float] = None
        self.amostra_ant_ms = 0
        hl_c = max(1.0, 60.0 / cfg.amostra_s)
        hl_l = max(2.0, 1800.0 / cfg.amostra_s)
        self.var_c, self.var_l = nu.Ewma(hl_c), nu.Ewma(hl_l)
        self.int_c, self.int_l = nu.Ewma(max(1.0, 10.0 / cfg.amostra_s)), nu.Ewma(hl_l)
        self.topo_medio = nu.Ewma(hl_c)
        self.jan = {k: nu.JanelaPercentil(cfg.n_janela)
                    for k in ("spread", "prof", "vol", "int", "ofi", "liq", "mo")}
        self.conta = 0
        self.ultima: Dict[str, Any] = {"estado": nu.SEM_DADOS, "motivos": []}
        self.invertidos = 0

    # ---- entrada de dados -------------------------------------------------
    def _chegou(self, local_ms: int, exch_ms: int) -> None:
        """Regista a chegada de dados e fecha, ou descobre, um periodo sem eles."""
        u = self.ultimo_local_ms
        if self.cortes and self.cortes[-1][1] is None:      # fim de uma queda de ligacao
            self.cortes[-1][1] = local_ms
            self.cortes[-1][3] = exch_ms
        elif u and local_ms - u > self.cfg.sem_dados_ms:    # silencio sem a ligacao cair
            self.cortes.append([u + 1, local_ms, self.exch_visto_ms + 1, exch_ms])
            self.b = self.a = None
            self.mid_ant = None
        self.ultimo_local_ms = max(u, local_ms)
        self.exch_visto_ms = max(self.exch_visto_ms, exch_ms)

    def on_bbo(self, exch_ms: int, local_ms: int, b: float, qb: float, a: float, qa: float) -> None:
        self._chegou(local_ms, exch_ms)
        if b > a:  # ordem trocada na mensagem: corrige e conta
            b, qb, a, qa = a, qa, b, qb
            self.invertidos += 1
        if self.b is not None and self.a is not None:
            self.ofi.juntar(local_ms, nu.ofi_evento(self.b, self.qb, self.a, self.qa, b, qb, a, qa))
        self.b, self.qb, self.a, self.qa = b, qb, a, qa
        self.exch_ms = max(self.exch_ms, exch_ms)
        self.hist_bbo.append((exch_ms, local_ms, b, qb, a, qa))
        self.atrasos.append(local_ms - exch_ms)

    def on_livro(self, exch_ms: int, local_ms: int, lado0: List[nu.Nivel], lado1: List[nu.Nivel]) -> None:
        if not lado0 or not lado1:
            return
        self._chegou(local_ms, exch_ms)
        if max(p for p, _ in lado0) > max(p for p, _ in lado1):
            lado0, lado1 = lado1, lado0
        self.bids = sorted(lado0, key=lambda n: -n[0])
        self.asks = sorted(lado1, key=lambda n: n[0])
        self.livro_local_ms = local_ms
        (b, qb), (a, qa) = self.bids[0], self.asks[0]
        sem_bbo = self.b is None
        mudo = exch_ms > self.exch_ms + 5000 and (b != self.b or a != self.a)
        if sem_bbo or mudo:  # sem bbo, ou bbo calado ha 5 s com o topo ja diferente
            self.on_bbo(exch_ms, local_ms, b, qb, a, qa)

    def on_negocio(self, exch_ms: int, local_ms: int, px: float, sz: float) -> None:
        self.notional += px * sz
        self.hist_neg.append((exch_ms, local_ms, px, sz))

    def on_ctx(self, mark: Optional[float], oraculo: Optional[float]) -> None:
        if mark:
            self.mark = mark
        if oraculo:
            self.oraculo = oraculo

    def desligado(self, t_ms: int) -> None:
        """A ligacao caiu: o topo deixa de ser de confianca ate voltarem dados."""
        if self.ultimo_local_ms and not (self.cortes and self.cortes[-1][1] is None):
            self.cortes.append([min(t_ms, self.ultimo_local_ms + 1), None, self.exch_visto_ms + 1, None])
        self.b = self.a = None
        self.bids, self.asks = [], []
        self.mid_ant = None

    def em_corte(self, ini_ms: int, fim_ms: Optional[int] = None, bolsa: bool = False) -> bool:
        """Houve falta de dados em algum momento entre ini e fim?

        bolsa=True quando ini e fim estao na hora da bolsa e nao na hora local.
        """
        fim_ms = ini_ms if fim_ms is None else fim_ms
        i0, i1 = (2, 3) if bolsa else (0, 1)
        for c in self.cortes:
            if c[i0] <= fim_ms and (c[i1] is None or ini_ms < c[i1]):
                return True
        return False

    def coberto(self, alvo_ms: int, bolsa: bool = False) -> bool:
        """Ja chegaram dados posteriores ao instante alvo? So entao o preco desse instante e certo."""
        return (self.exch_visto_ms if bolsa else self.ultimo_local_ms) >= alvo_ms

    # ---- consultas ---------------------------------------------------------
    def niveis(self, lado: int) -> List[nu.Nivel]:
        """Lado do livro que uma ordem consome, acertado pelo melhor preco actual.

        A fotografia do livro pode ser mais velha do que o bbo. O primeiro nivel
        e sempre o melhor preco actual; da fotografia ficam so os niveis para la dele.
        """
        if self.b is None or self.a is None:
            return []
        if lado > 0:
            if not self.asks:
                return []
            return [(self.a, self.qa)] + [n for n in self.asks if n[0] > self.a]
        if not self.bids:
            return []
        return [(self.b, self.qb)] + [n for n in self.bids if n[0] < self.b]

    def topo_em_local(self, ts_ms: int) -> Optional[Tuple[float, float, float, float]]:
        """Melhor compra/venda em vigor no instante ts (relogio local).

        Devolve None se nao ha dados que cubram esse instante, ou se entre o
        ultimo dado conhecido e ts houve falta de dados: um preco velho nao
        serve de medida.
        """
        if ts_ms > self.ultimo_local_ms + self.tol_ms:
            return None
        for _, loc, b, qb, a, qa in reversed(self.hist_bbo):
            if loc <= ts_ms:
                return None if self.em_corte(loc + 1, ts_ms) else (b, qb, a, qa)
        for loc, _, b, qb, a, qa, _ in reversed(self.amostras):
            if loc <= ts_ms:
                return None if self.em_corte(loc + 1, ts_ms) else (b, qb, a, qa)
        return None

    def mid_em_local(self, ts_ms: int) -> Optional[float]:
        t = self.topo_em_local(ts_ms)
        return nu.mid(t[0], t[2]) if t else None

    def mid_exch(self, ts_ms: int, estrito: bool) -> Optional[float]:
        """Ultimo mid com hora da bolsa antes de ts (estrito) ou ate ts."""
        if ts_ms > self.exch_visto_ms + self.tol_ms:
            return None
        for ex, _, b, _, a, _ in reversed(self.hist_bbo):
            if ex < ts_ms or (not estrito and ex == ts_ms):
                return None if self.em_corte(ex + 1, ts_ms, bolsa=True) else nu.mid(b, a)
        return None

    def estado_em_local(self, ts_ms: int) -> str:
        limite = max(3000, 3 * int(self.cfg.amostra_s * 1000))
        for loc, _, _, _, _, _, est in reversed(self.amostras):
            if loc <= ts_ms:
                return est if ts_ms - loc <= limite else nu.SEM_DADOS
        return ""

    def atraso_mediano(self) -> float:
        return nu.mediana(list(self.atrasos)) if self.atrasos else float("nan")

    # ---- uma amostra -------------------------------------------------------
    def amostrar(self, t_ms: int) -> Dict[str, Any]:
        cfg = self.cfg
        if self.b is None or self.a is None or t_ms - self.ultimo_local_ms > cfg.sem_dados_ms:
            self.mid_ant = None
            self.ultima = {"estado": nu.SEM_DADOS, "motivos": []}
            return self.ultima
        b, a, qb, qa = self.b, self.a, self.qb, self.qa
        m = nu.mid(b, a)
        s_bps = nu.spread_bps(b, a)
        passo = nu.passo_preco(m, self.sz_decimals)
        s_ticks = float(max(0, round((a - b) / passo)))
        deseq = nu.desequilibrio(qb, qa)

        # profundidade util no teto ja arredondado: e o preco que a ordem pode usar
        teto_c = nu.arredondar_preco(nu.preco_teto(m, 1, cfg.orcamento), self.sz_decimals, True)
        teto_v = nu.arredondar_preco(nu.preco_teto(m, -1, cfg.orcamento), self.sz_decimals, False)
        asks, bids = self.niveis(1), self.niveis(-1)
        qc = nu.tamanho_max(asks, teto_c, 1, cfg.theta)
        qv = nu.tamanho_max(bids, teto_v, -1, cfg.theta)
        tem_livro = bool(asks and bids)
        prof = min(qc.usd, qv.usd) if tem_livro else float("nan")
        q_ref = cfg.tamanho_usd / m
        if tem_livro:
            ic = nu.impacto(asks, q_ref, m, 1, cfg.theta)
            iv = nu.impacto(bids, q_ref, m, -1, cfg.theta)
            imp_ref = max(ic.imp_bps, iv.imp_bps)
        else:
            ic = iv = None
            imp_ref = float("nan")

        # volatilidade curta e longa (bps por raiz de segundo)
        dt = (t_ms - self.amostra_ant_ms) / 1000.0 if self.amostra_ant_ms else cfg.amostra_s
        dt = min(max(dt, 1e-3), 10 * cfg.amostra_s)
        if self.mid_ant:
            r = nu.retorno_bps(m, self.mid_ant)
            self.var_c.juntar(r * r / dt)
            self.var_l.juntar(r * r / dt)
        self.mid_ant = m
        self.amostra_ant_ms = t_ms
        sig_c = math.sqrt(self.var_c.valor) if self.var_c.n else float("nan")
        sig_l = math.sqrt(self.var_l.valor) if self.var_l.n else float("nan")
        vol_r = sig_c / sig_l if sig_l == sig_l and sig_l > 0 else float("nan")

        # intensidade de negocios (dolares por segundo), curta sobre longa
        taxa = self.notional / dt
        self.notional = 0.0
        self.int_c.juntar(taxa)
        self.int_l.juntar(taxa)
        int_r = self.int_c.valor / self.int_l.valor if self.int_l.valor > 0 else float("nan")

        # fluxo de ordens dos ultimos 10 s, em unidades do topo medio do livro
        self.topo_medio.juntar((qb + qa) / 2.0)
        tm = self.topo_medio.valor
        ofi_n = self.ofi.soma(t_ms) / tm if tm and tm > 0 else float("nan")

        liq = self.liq.soma(t_ms) if cfg.cg_chave else float("nan")
        mo = (nu.BPS * abs(self.mark - self.oraculo) / self.oraculo
              if self.mark and self.oraculo else float("nan"))

        j = self.jan
        aquecido_longo = self.var_l.n >= max(30, int(300 / cfg.amostra_s))
        leituras = [
            nu.Leitura("spread", s_bps, j["spread"].posicao(s_ticks), s_bps >= cfg.piso_spread),
            nu.Leitura("profundidade", prof, j["prof"].posicao(prof),
                       prof == prof and prof < cfg.piso_prof_x * cfg.tamanho_usd, invertida=True),
            nu.Leitura("volatilidade", vol_r, j["vol"].posicao(vol_r),
                       aquecido_longo and vol_r == vol_r and vol_r >= cfg.piso_vol),
            nu.Leitura("intensidade", int_r, j["int"].posicao(int_r),
                       aquecido_longo and int_r == int_r and int_r >= cfg.piso_int),
            nu.Leitura("mark-oraculo", mo, j["mo"].posicao(mo), mo == mo and mo >= cfg.piso_mo),
        ]
        if cfg.fluxo_no_estado:  # por defeito nao: o fluxo regista-se por sinal, com o lado da ordem
            leituras.append(nu.Leitura("fluxo", abs(ofi_n), j["ofi"].posicao(abs(ofi_n)),
                                       ofi_n == ofi_n and abs(ofi_n) >= cfg.piso_ofi))
        if cfg.cg_chave:
            # so se compara com o historico quando ele ja tem amostras que cheguem; com meia duzia
            # de pontos qualquer liquidacao cairia no topo
            com_historico = liq > 0 and len(j["liq"]) >= cfg.n_aquecimento
            leituras.append(nu.Leitura("liquidacoes", liq, j["liq"].posicao(liq) if com_historico else float("nan"),
                                       liq >= cfg.piso_liq))
        if len(j["spread"]) < cfg.n_aquecimento:
            estado, motivos = nu.AQUECIMENTO, []
        else:
            estado, motivos = nu.classificar(leituras, cfg.p_amarelo, cfg.p_vermelho)

        self.conta += 1
        if self.conta % cfg.passo_hist == 0:  # o historico cresce a passo reduzido
            j["spread"].juntar(s_ticks)
            j["prof"].juntar(prof)
            j["vol"].juntar(vol_r)
            j["int"].juntar(int_r)
            j["ofi"].juntar(abs(ofi_n))
            j["mo"].juntar(mo)
            if liq == liq and liq > 0:
                j["liq"].juntar(liq)
        self.amostras.append((t_ms, m, b, qb, a, qa, estado))

        self.ultima = {
            "estado": estado, "motivos": motivos, "mid": m, "bid": b, "ask": a, "qb": qb, "qa": qa,
            "spread_bps": s_bps, "spread_ticks": s_ticks, "desequilibrio": deseq,
            "prof_util_usd": prof, "imp_ref_bps": imp_ref,
            "imp_compra_bps": ic.imp_bps if ic else float("nan"),
            "imp_venda_bps": iv.imp_bps if iv else float("nan"),
            "p_teto_compra": teto_c,
            "p_teto_venda": teto_v,
            "qmax_usd_compra": qc.usd if tem_livro else float("nan"),
            "qmax_usd_venda": qv.usd if tem_livro else float("nan"),
            "qmax_truncado": bool(tem_livro and (qc.truncado or qv.truncado)),
            "sz_decimals": self.sz_decimals,
            "niveis_compra": [[px, sz] for px, sz in asks],
            "niveis_venda": [[px, sz] for px, sz in bids],
            "sigma_curta": sig_c, "sigma_longa": sig_l, "vol_razao": vol_r,
            "intens_razao": int_r, "ofi_norm": ofi_n, "liq_60s_usd": liq,
            "liq_long_usd": self.liq_long.soma(t_ms) if cfg.cg_chave else float("nan"),
            "liq_short_usd": self.liq_short.soma(t_ms) if cfg.cg_chave else float("nan"),
            "mark_oraculo_bps": mo, "atraso_ms": self.atraso_mediano(),
            "mid_ponderado": nu.mid_ponderado(b, a, qb, qa),
            "historico": len(j["spread"]), "idade_livro_ms": t_ms - self.livro_local_ms,
        }
        return self.ultima


# ==========================================================================
# Sinais e fills em curso
# ==========================================================================
class SinalAberto:
    def __init__(self, ident: str, ts_ms: int, ativo: str, lado: int):
        self.id = ident
        self.ts_ms = ts_ms
        self.ativo = ativo
        self.lado = lado
        self.linha: Dict[str, Any] = {}
        self.m0: Optional[float] = None
        self.preco: Optional[float] = None     # preco que quem deu o sinal tinha em mente, se o escreveu
        self.sombra: Optional[nu.OrdemSombra] = None
        self.pendentes: List[int] = []
        self.oids: List[Any] = []              # ordens ja ligadas a este sinal, por ordem de chegada
        self.escrito = False


class FillAberto:
    def __init__(self, ativo: str, lado: int, exch_ms: int, local_ms: int, m1: Optional[float],
                 horizontes: List[int], abre: bool):
        self.ativo = ativo
        self.lado = lado
        self.exch_ms = exch_ms
        self.local_ms = local_ms
        self.m1 = m1
        self.abre = abre
        self.px = 0.0
        self.linha: Dict[str, Any] = {}
        self.pendentes: List[int] = list(horizontes)


# ==========================================================================
# O medidor
# ==========================================================================
class Medidor:
    def __init__(self, cfg: Config, sz_dec: Dict[str, int]):
        self.cfg = cfg
        self.ativos: Dict[str, Ativo] = {n: Ativo(n, cfg, sz_dec[n]) for n in cfg.ativos}
        self.inicio_ms = agora_ms()
        self.ultimo_dados = time.monotonic()   # ultima mensagem de mercado (pongs nao contam)
        self.ligado = False
        self.ligacao_ms = 0
        self.tick = 0
        self.erros: Dict[str, int] = {}
        self.dia_limpeza = -1
        self.cg_msgs = 0          # liquidacoes recebidas dos activos seguidos
        self.cg_total = 0         # liquidacoes recebidas, de qualquer activo
        self.cg_vistos: Deque[Tuple[Any, ...]] = deque(maxlen=8000)
        self.cg_vistos_set: set = set()
        self.cg_rest_parado = False
        self.moedas_avisadas: set = set()
        self.por_maiusculas = {n.upper(): a for n, a in self.ativos.items()}
        self.negocios_velhos = 0
        self.religacoes = 0
        self.erros_msg = 0
        self.sinais: List[SinalAberto] = []
        self.fills: List[FillAberto] = []
        self.tids: Deque[Any] = deque(maxlen=20000)
        self.tids_set: set = set()
        self.n_sinais = 0
        self.n_fills = 0
        self.cg_estado = "desligado" if not cfg.cg_chave else "a ligar"
        os.makedirs(cfg.pasta, exist_ok=True)
        os.makedirs(cfg.pasta_metricas, exist_ok=True)
        self.col_sinais = (["id", "hora_utc", "ativo", "lado", "preco_sinal", "desvio_preco_bps", "tamanho_usd", "alvo_bps",
                            "fresco", "estado", "motivos", "mid0", "spread_bps", "desequilibrio", "ofi_lado",
                            "liq_favor_usd", "liq_contra_usd",
                            "imp_bps", "imp_viavel", "orcamento_bps", "p_teto", "qmax_usd", "qmax_truncado",
                            "sombra_preco", "sombra_fila", "sombra_preenchida", "sombra_tempo_s",
                            "ganho_passiva_bps"]
                           + ["r_%d" % h for h in cfg.horizontes] + ["nota"])
        self.col_fills = (["hora_utc", "ativo", "lado", "px", "sz", "valor_usd", "taxa", "moeda_taxa",
                          "taker", "dir", "oid", "tid", "mid_antes", "e_bps", "f_bps"]
                         + ["a_%d" % h for h in cfg.horizontes_fill]
                         + ["estado", "sinal_id", "ordem_no_sinal", "d_bps", "atraso_s", "c_preco_bps"])
        self.reg_sinais = Registo(cfg.reg_sinais, self.col_sinais)
        self.reg_fills = Registo(cfg.reg_fills, self.col_fills)
        self.reg_met = {n: RegistoDiario(cfg.pasta_metricas, n) for n in cfg.ativos}
        self._preparar_sinais_csv()
        self._recarregar_historico()

    # ---- ficheiro de sinais -------------------------------------------------
    CAB_SINAIS = "hora,ativo,lado,preco,alvo_bps,tamanho_usd,nota"

    def _preparar_sinais_csv(self) -> None:
        p = self.cfg.sinais_csv
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(self.CAB_SINAIS + "\n")
        self._sinais_pos = os.path.getsize(p)  # sinais antigos nao se reprocessam

    def _ler_sinais_novos(self) -> List[Dict[str, str]]:
        p = self.cfg.sinais_csv
        try:
            tam = os.path.getsize(p)
        except OSError:
            return []
        if tam < self._sinais_pos:  # ficheiro foi encurtado ou substituido
            log.warning("sinais.csv ficou mais pequeno; continuo a partir do fim actual")
            self._sinais_pos = tam
            return []
        if tam == self._sinais_pos:
            return []
        with open(p, "rb") as f:
            f.seek(self._sinais_pos)
            bruto = f.read()
        fim = bruto.rfind(b"\n")
        if fim < 0:  # linha ainda a meio de ser escrita
            return []
        self._sinais_pos += fim + 1
        completo = bruto[: fim + 1].decode("utf-8", "replace")
        linhas = []
        campos = self.CAB_SINAIS.split(",")
        for texto in completo.splitlines():
            try:  # uma linha estragada nao pode levar as seguintes
                partes = next(csv.reader([texto.replace("\x00", "")]), None)
            except Exception as e:
                log.warning("Linha de sinais ilegivel (%s): %.120r", e, texto)
                continue
            if not partes or not "".join(partes).strip() or partes[0].strip().lower() == "hora":
                continue
            partes = [x.strip() for x in partes] + [""] * len(campos)
            linhas.append(dict(zip(campos, partes[: len(campos)])))
        return linhas

    # ---- historico em disco -------------------------------------------------
    def _recarregar_historico(self) -> None:
        """Reconstroi os percentis com as metricas das ultimas horas, se existirem."""
        limite = agora_ms() - int(self.cfg.janela_h * 3600 * 1000)
        for nome, at in self.ativos.items():
            n = 0
            for dias in (1, 0):
                dia = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d")
                p = os.path.join(self.cfg.pasta_metricas, "%s_%s.csv" % (nome, dia))
                if not os.path.exists(p):
                    continue
                try:
                    with open(p, "r", encoding="utf-8", newline="") as f:
                        for ln in csv.DictReader(f):
                            if flt(ln.get("local_ms")) < limite or ln.get("estado") == nu.SEM_DADOS:
                                continue
                            at.jan["spread"].juntar(flt(ln.get("spread_ticks")))
                            at.jan["prof"].juntar(flt(ln.get("prof_util_usd")))
                            at.jan["vol"].juntar(flt(ln.get("vol_razao")))
                            at.jan["int"].juntar(flt(ln.get("intens_razao")))
                            at.jan["ofi"].juntar(abs(flt(ln.get("ofi_norm"))))
                            at.jan["mo"].juntar(flt(ln.get("mark_oraculo_bps")))
                            lq = flt(ln.get("liq_60s_usd"))
                            if lq == lq and lq > 0:
                                at.jan["liq"].juntar(lq)
                            n += 1
                except Exception as e:  # ficheiro estragado nao impede o arranque
                    log.warning("Nao consegui reler %s (%s)", p, e)
            if n:
                log.info("%s: historico recarregado, %d amostras", nome, n)

    def _limpar_antigos(self) -> None:
        if self.cfg.guardar_dias <= 0:
            return
        limite = time.time() - self.cfg.guardar_dias * 86400
        try:
            for nome in os.listdir(self.cfg.pasta_metricas):
                p = os.path.join(self.cfg.pasta_metricas, nome)
                if nome.endswith(".csv") and os.path.getmtime(p) < limite:
                    os.remove(p)
        except OSError as e:
            log.warning("Limpeza de metricas antigas falhou (%s)", e)

    # ---- ligacao a Hyperliquid ----------------------------------------------
    async def tarefa_hyperliquid(self) -> None:
        import websockets
        espera = 1.0
        while True:
            try:
                async with websockets.connect(self.cfg.url_ws, ping_interval=None, max_size=None,
                                              open_timeout=20) as ws:
                    for nome in self.cfg.ativos:
                        for tipo in ("bbo", "l2Book", "trades", "activeAssetCtx"):
                            await ws.send(json.dumps({"method": "subscribe",
                                                      "subscription": {"type": tipo, "coin": nome}}))
                    if self.cfg.endereco:
                        await ws.send(json.dumps({"method": "subscribe",
                                                  "subscription": {"type": "userFills", "user": self.cfg.endereco}}))
                    self.ligado = True
                    self.ligacao_ms = agora_ms()
                    self.ultimo_dados = time.monotonic()
                    espera = 1.0
                    log.info("Hyperliquid: ligado (%d activos)", len(self.cfg.ativos))
                    vigia = asyncio.ensure_future(self._vigia(ws))
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
            for at in self.ativos.values():
                at.desligado(agora_ms())
            log.info("Nova tentativa dentro de %.0f s", espera)
            await asyncio.sleep(espera)
            espera = min(espera * 2, 30.0)

    async def _vigia(self, ws: Any) -> None:
        """Mantem a ligacao viva e fecha-a se deixarem de chegar dados de mercado.

        As respostas aos pings nao contam: uma ligacao que responde mas nao
        traz precos esta, para este efeito, morta.
        """
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
            return  # a ligacao ja caiu; o ciclo principal trata de religar

    def _tratar(self, msg: Dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        canal = msg.get("channel")
        d = msg.get("data")
        t = agora_ms()
        if canal in ("bbo", "l2Book", "trades", "activeAssetCtx"):
            self.ultimo_dados = time.monotonic()  # so dados de mercado mantem a ligacao por boa
        if canal == "bbo":
            at = self.ativos.get(d["coin"])
            bb = d.get("bbo") or [None, None]
            if at and bb[0] and bb[1]:
                at.on_bbo(int(d["time"]), t, float(bb[0]["px"]), float(bb[0]["sz"]),
                          float(bb[1]["px"]), float(bb[1]["sz"]))
        elif canal == "l2Book":
            at = self.ativos.get(d["coin"])
            if at:
                niv = d["levels"]
                at.on_livro(int(d["time"]), t,
                            [(float(x["px"]), float(x["sz"])) for x in niv[0]],
                            [(float(x["px"]), float(x["sz"])) for x in niv[1]])
        elif canal == "trades":
            for tr in d:
                at = self.ativos.get(tr["coin"])
                if not at:
                    continue
                px, sz, ex = float(tr["px"]), float(tr["sz"]), int(tr["time"])
                ref = at.exch_ms if at.exch_ms else self.ligacao_ms - 5000
                if ex < ref - 5000:  # lote de negocios antigos enviado ao subscrever
                    self.negocios_velhos += 1
                    continue
                at.on_negocio(ex, t, px, sz)
                for s in self.sinais:
                    if s.sombra and s.ativo == at.nome and not s.sombra.expirada(t):
                        s.sombra.negocio(t, px, sz)
        elif canal == "activeAssetCtx":
            at = self.ativos.get(d["coin"])
            if at:
                ctx = d.get("ctx", {})
                at.on_ctx(positivo(ctx.get("markPx")), positivo(ctx.get("oraclePx")))
        elif canal == "userFills":
            self._fills(d, t)
        elif canal == "error":
            log.warning("Hyperliquid devolveu erro: %s", d)

    # ---- sinais -------------------------------------------------------------
    def _novo_sinal(self, ln: Dict[str, str], t: int) -> None:
        cfg = self.cfg
        try:
            lado = nu.interpretar_lado(ln.get("lado", ""))
            ts = interpretar_hora(ln.get("hora", ""), t)
        except ValueError as e:
            log.warning("Sinal ignorado (%s): %s", e, ln)
            return
        nome = ln.get("ativo", "").strip()
        at = self.ativos.get(nome) or self.ativos.get(nome.upper())
        if not at:
            log.warning("Sinal ignorado: activo %r nao esta em config.ini", nome)
            return
        if ts > t + 2000:
            ts = t
        if t - ts > 2 * 3600 * 1000:
            log.warning("Sinal ignorado: tem mais de 2 horas (%s)", ln)
            return
        self.n_sinais += 1
        s = SinalAberto("%s-%03d" % (datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).strftime("%y%m%d-%H%M%S"),
                                     self.n_sinais % 1000), ts, at.nome, lado)
        fresco = (t - ts) <= 2000
        tam = flt(ln.get("tamanho_usd"))
        tam = tam if tam == tam and tam > 0 else cfg.tamanho_usd
        alvo = flt(ln.get("alvo_bps"))
        alvo = alvo if alvo == alvo and alvo > 0 else cfg.alvo_bps
        orc = nu.orcamento_bps(alvo, cfg.fraccao)
        s.linha = {"id": s.id, "hora_utc": iso_utc(ts), "ativo": at.nome,
                   "lado": "compra" if lado > 0 else "venda", "preco_sinal": ln.get("preco", ""),
                   "tamanho_usd": num(tam, 2), "alvo_bps": num(alvo, 2), "fresco": int(fresco),
                   "orcamento_bps": num(orc, 3), "nota": ln.get("nota", "")}
        topo = at.topo_em_local(ts)
        if not topo:
            s.linha["estado"] = "SEM_DADOS"
            self.reg_sinais.escrever(s.linha)
            log.warning("Sinal %s registado sem dados de mercado para esse instante", s.id)
            return
        b, qb, a, qa = topo
        m0 = nu.mid(b, a)
        s.m0 = m0
        s.preco = positivo(ln.get("preco"))
        if s.preco:  # quanto o mid ja estava para la do preco indicado, contra a ordem
            s.linha["desvio_preco_bps"] = num(nu.BPS * lado * (m0 - s.preco) / s.preco)
        q = tam / m0
        s.linha.update({"mid0": num(m0, 8), "spread_bps": num(nu.spread_bps(b, a)),
                        "desequilibrio": num(nu.desequilibrio(qb, qa)),
                        "estado": at.estado_em_local(ts) or at.ultima.get("estado", "")})
        if fresco:  # o livro em memoria e o do instante do sinal
            u = at.ultima
            s.linha["motivos"] = " | ".join(u.get("motivos", []))
            ofi = u.get("ofi_norm")
            if ofi is not None and ofi == ofi:
                s.linha["ofi_lado"] = num(lado * ofi)
            ll, ls = u.get("liq_long_usd"), u.get("liq_short_usd")
            if isinstance(ll, float) and isinstance(ls, float) and ll == ll and ls == ls:
                # compra: shorts liquidados empurram a favor; longs liquidados, contra
                favor, contra = (ls, ll) if lado > 0 else (ll, ls)
                s.linha["liq_favor_usd"] = num(favor, 0)
                s.linha["liq_contra_usd"] = num(contra, 0)
            niveis = at.niveis(lado)
            if niveis and at.b is not None and at.a is not None:
                m_agora = nu.mid(at.b, at.a)  # mid e livro do mesmo instante
                imp = nu.impacto(niveis, tam / m_agora, m_agora, lado, cfg.theta)
                teto = nu.arredondar_preco(nu.preco_teto(m_agora, lado, orc), at.sz_decimals, lado > 0)
                qm = nu.tamanho_max(niveis, teto, lado, cfg.theta)
                s.linha.update({"imp_bps": num(imp.imp_bps), "imp_viavel": int(imp.viavel),
                                "p_teto": num(teto, 8),
                                "qmax_usd": num(qm.usd, 0), "qmax_truncado": int(qm.truncado)})
        p_pas, fila = (b, qb) if lado > 0 else (a, qa)
        s.sombra = nu.OrdemSombra(lado, p_pas, fila, q, ts, int(cfg.prazo_s * 1000))
        s.linha.update({"sombra_preco": num(p_pas, 8), "sombra_fila": num(fila, 6),
                        "ganho_passiva_bps": num(nu.BPS * lado * (m0 - p_pas) / m0)})
        for _, loc, px, sz in at.hist_neg:  # sinal atrasado: repete os negocios ja vistos
            if loc >= ts:
                s.sombra.negocio(loc, px, sz)
        s.pendentes = list(cfg.horizontes)
        self.sinais.append(s)
        log.info("Sinal %s: %s %s, mid %.6g, estado %s", s.id, s.linha["lado"], at.nome, m0, s.linha["estado"])

    def _avancar_sinais(self, t: int) -> None:
        cfg = self.cfg
        for s in self.sinais:
            if s.escrito or s.m0 is None:
                continue
            at = self.ativos[s.ativo]
            for h in list(s.pendentes):
                alvo_ms = s.ts_ms + h * 1000
                if t < alvo_ms:
                    continue
                if at.coberto(alvo_ms):                    # ja ha dados posteriores: o preco e certo
                    mh = at.mid_em_local(alvo_ms)
                elif t >= alvo_ms + cfg.sem_dados_ms:      # os dados nao voltaram a tempo: em branco
                    mh = None
                else:
                    continue                               # espera por dados que cubram o instante
                s.linha["r_%d" % h] = num(nu.resultado_bps(s.lado, mh, s.m0)) if mh else ""
                s.pendentes.remove(h)
            sb = s.sombra
            if s.pendentes or sb is None:
                continue
            if not sb.preenchida and not at.coberto(sb.fim_ms) and t < sb.fim_ms + cfg.sem_dados_ms:
                continue                                   # o prazo da passiva ainda nao esta coberto
            self._escrever_sinal(s)
        guardar_ms = int((cfg.janela_sinal_s + max(cfg.horizontes_fill) + max(cfg.horizontes)
                          + cfg.prazo_s + 120) * 1000) + 2 * cfg.sem_dados_ms
        self.sinais = [s for s in self.sinais if not (s.escrito and s.ts_ms < t - guardar_ms)]

    def _escrever_sinal(self, s: SinalAberto) -> None:
        """Escreve a linha do sinal. O que nao se sabe fica em branco."""
        at = self.ativos[s.ativo]
        sb = s.sombra
        if sb is not None:
            if sb.preenchida:
                s.linha["sombra_preenchida"] = 1
                if not at.em_corte(sb.t0_ms, sb.t_fill_ms):
                    s.linha["sombra_tempo_s"] = num((sb.t_fill_ms - sb.t0_ms) / 1000.0, 2)
            elif at.coberto(sb.fim_ms) and not at.em_corte(sb.t0_ms, sb.fim_ms):
                s.linha["sombra_preenchida"] = 0
            else:  # faltaram negocios na janela, ou ela nao chegou ao fim: desconhecido
                s.linha["sombra_preenchida"] = ""
        self.reg_sinais.escrever(s.linha)
        s.escrito = True

    # ---- fills --------------------------------------------------------------
    def _fills(self, d: Dict[str, Any], t: int) -> None:
        if not isinstance(d, dict):
            return
        for f in d.get("fills", []) or []:
            try:
                self._um_fill(f, t)
            except Exception as e:  # um fill estranho nao impede os seguintes
                self.erros_msg += 1
                if self.erros_msg <= 20:
                    log.warning("Fill ignorado (%s): %.200r", e, f)

    def _um_fill(self, f: Dict[str, Any], t: int) -> None:
        tid = f.get("tid")
        if tid in self.tids_set:
            return
        if len(self.tids) == self.tids.maxlen:
            self.tids_set.discard(self.tids[0])
        self.tids.append(tid)
        self.tids_set.add(tid)
        ex = int(f["time"])
        if ex < self.inicio_ms:  # anterior ao arranque do medidor
            return
        at = self.ativos.get(f.get("coin", ""))
        if not at:
            return
        lado = 1 if f.get("side") == "B" else -1
        direc = str(f.get("dir", ""))
        esperado = {"Open Long": 1, "Close Short": 1, "Open Short": -1, "Close Long": -1}.get(direc)
        if esperado is not None and esperado != lado:
            log.warning("Fill %s: lado (%s) nao condiz com dir (%s); uso dir", tid, f.get("side"), direc)
            lado = esperado
        px, sz, fee = float(f["px"]), float(f["sz"]), flt(f.get("fee"))
        if px <= 0 or sz <= 0:
            return
        moeda = str(f.get("feeToken") or "")
        taxa_em_usd = moeda.upper() in ("", "USDC")
        if not taxa_em_usd and moeda not in self.moedas_avisadas:
            self.moedas_avisadas.add(moeda)
            log.warning("Taxa cobrada em %s e nao em USDC: F fica em branco nesses fills", moeda)
        m1 = at.mid_exch(ex, estrito=True)
        ini = flt(f.get("startPosition"))
        abre = direc.startswith("Open") or (not direc and ini == ini and abs(ini + lado * sz) > abs(ini))
        fa = FillAberto(at.nome, lado, ex, t, m1, self.cfg.horizontes_fill, abre)
        fa.linha = {"hora_utc": iso_utc(ex), "ativo": at.nome, "lado": "compra" if lado > 0 else "venda",
                    "px": num(px, 8), "sz": num(sz, 8), "valor_usd": num(px * sz, 2),
                    "taxa": num(fee, 6), "moeda_taxa": moeda,
                    "taker": int(bool(f.get("crossed"))), "dir": direc, "oid": f.get("oid", ""),
                    "tid": tid, "estado": at.estado_em_local(min(t, ex + 1000)),
                    "f_bps": num(nu.custo_taxa_bps(fee, px, sz)) if fee == fee and taxa_em_usd else ""}
        fa.px = px
        if m1:
            fa.linha["mid_antes"] = num(m1, 8)
            fa.linha["e_bps"] = num(nu.custo_execucao_bps(lado, px, m1))
        self.fills.append(fa)
        self.n_fills += 1
        log.info("Fill %s %s %s: E=%s bps, F=%s bps", at.nome, fa.linha["lado"],
                 "taker" if fa.linha["taker"] else "maker", fa.linha.get("e_bps", "?"), fa.linha.get("f_bps", "?"))

    def _avancar_fills(self, t: int) -> None:
        cfg = self.cfg
        resto = []
        for fa in self.fills:
            at = self.ativos[fa.ativo]
            for h in list(fa.pendentes):
                alvo_ms = fa.exch_ms + h * 1000
                if at.coberto(alvo_ms, bolsa=True):                   # ha dados da bolsa posteriores
                    mh = at.mid_exch(alvo_ms, estrito=False)
                elif t >= fa.local_ms + h * 1000 + cfg.sem_dados_ms:  # nao voltaram a tempo: em branco
                    mh = None
                else:
                    continue
                fa.linha["a_%d" % h] = (num(nu.seleccao_adversa_bps(fa.lado, mh, fa.m1))
                                        if mh and fa.m1 else "")
                fa.pendentes.remove(h)
            if fa.pendentes:
                resto.append(fa)
            else:
                self._escrever_fill(fa)
        self.fills = resto

    def _escrever_fill(self, fa: FillAberto) -> None:
        """Liga o fill ao sinal e escreve a linha.

        A ligacao faz-se aqui, e nao a chegada do fill, para que um fill mais
        rapido do que a leitura do ficheiro de sinais encontre o seu sinal.
        Usa a hora da bolsa do fill: um fill reenviado apos religar nao se liga
        a um sinal posterior a ele. Liga-se sempre ao sinal mais recente do
        mesmo activo e lado dentro da janela.
        """
        if fa.abre and fa.m1:
            janela = self.cfg.janela_sinal_s * 1000
            cand = [s for s in self.sinais
                    if s.ativo == fa.ativo and s.lado == fa.lado and s.m0
                    and s.ts_ms <= fa.exch_ms + 1000 and fa.exch_ms - s.ts_ms <= janela]
            if cand:
                s = max(cand, key=lambda z: z.ts_ms)
                oid = fa.linha.get("oid", "")
                if oid not in s.oids:
                    s.oids.append(oid)
                fa.linha.update({"sinal_id": s.id, "ordem_no_sinal": s.oids.index(oid) + 1,
                                 "d_bps": num(nu.custo_decisao_bps(fa.lado, fa.m1, s.m0)),
                                 "atraso_s": num(max(0.0, (fa.exch_ms - s.ts_ms) / 1000.0), 2)})
                if s.preco and fa.px:  # preco pago face ao preco indicado no sinal
                    fa.linha["c_preco_bps"] = num(nu.BPS * fa.lado * (fa.px - s.preco) / s.preco)
        self.reg_fills.escrever(fa.linha)

    def _ingerir_liq(self, it: Dict[str, Any], t: int) -> None:
        """Soma uma liquidacao, venha do websocket ou do REST. Repetidas ignoram-se."""
        chave = chave_liquidacao(it)
        if chave in self.cg_vistos_set:
            return
        if len(self.cg_vistos) == self.cg_vistos.maxlen:
            self.cg_vistos_set.discard(self.cg_vistos[0])
        self.cg_vistos.append(chave)
        self.cg_vistos_set.add(chave)
        self.cg_total += 1
        v = volume_liquidacao(it)
        at = self.por_maiusculas.get(chave[1])
        if not at or v != v:
            return
        at.liq.juntar(t, v)
        if chave[4] == 1:
            at.liq_long.juntar(t, v)
        elif chave[4] == 2:
            at.liq_short.juntar(t, v)
        self.cg_msgs += 1

    # ---- CoinGlass (opcional) -------------------------------------------------
    async def tarefa_coinglass(self) -> None:
        import websockets
        espera = 2.0
        while True:
            try:
                async with websockets.connect(self.cfg.url_cg + self.cfg.cg_chave, ping_interval=None,
                                              open_timeout=20) as ws:
                    await ws.send(json.dumps({"method": "subscribe", "channels": self.cfg.cg_canais}))
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
                log.warning("CoinGlass: ligacao falhou (%s)", e)
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
        """Ordens de liquidacao por REST v4. Plano Standard ou acima; a Professional inclui."""
        if self.cg_rest_parado:
            return
        base = "https://open-api-v4.coinglass.com/api/futures/liquidation/order"
        falha_plano = False
        avisou = False
        for bolsa in self.cfg.cg_rest_ex:
            if falha_plano:
                break
            for coin in self.cfg.ativos:
                url = "%s?exchange=%s&symbol=%s&min_liquidation_amount=%d" % (
                    base, urllib.request.quote(bolsa), urllib.request.quote(coin), int(self.cfg.cg_rest_min))
                req = urllib.request.Request(url, headers={
                    "accept": "application/json", "CG-API-KEY": self.cfg.cg_chave})
                try:
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        corpo = json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403):
                        falha_plano = True
                    if not avisou:
                        log.warning("CoinGlass REST %s %s: %s", bolsa, coin, e)
                        avisou = True
                    continue
                except Exception as e:
                    if not avisou:
                        log.warning("CoinGlass REST %s %s: %s", bolsa, coin, e)
                        avisou = True
                    continue
                if str(corpo.get("code", "")) not in ("0", "200"):
                    log.warning("CoinGlass REST recusou (%s). O websocket continua.", corpo.get("msg"))
                    falha_plano = True
                    break
                agora = agora_ms()
                itens = [it for it in (corpo.get("data") or []) if isinstance(it, dict)]
                itens.sort(key=lambda z: int(flt(z.get("time") or 0)), reverse=True)
                for it in itens[:400]:
                    ts = int(flt(it.get("time") or 0))
                    if ts and agora - ts > 90_000:
                        break
                    self._ingerir_liq(it, agora)
            if falha_plano:
                break
        if falha_plano:
            self.cg_rest_parado = True
            log.warning("CoinGlass REST parado: a chave nao autoriza o endpoint de ordens "
                        "(pede plano Standard ou acima; a Professional inclui).")

    async def tarefa_coinglass_rest(self) -> None:
        if not (self.cfg.cg_chave and self.cfg.cg_rest):
            return
        await asyncio.sleep(5)
        while True:
            await asyncio.to_thread(self._passo, "coinglass rest", self._poll_rest)
            await asyncio.sleep(max(5.0, self.cfg.cg_rest_s))

    # ---- ciclo de amostragem --------------------------------------------------
    def _passo(self, nome: str, fn: Any, *args: Any) -> Any:
        """Corre um passo do ciclo; um erro num passo nao impede os outros."""
        try:
            return fn(*args)
        except Exception:
            n = self.erros.get(nome, 0) + 1
            self.erros[nome] = n
            if n <= 3:
                log.exception("Erro em '%s' (o medidor continua)", nome)
            elif n % 600 == 0:
                log.error("Erro em '%s' repetido %d vezes", nome, n)
            return None

    def _amostrar(self, nome: str, at: Ativo, t: int) -> None:
        cfg = self.cfg
        u = at.amostrar(t)
        if self.tick % cfg.passo_hist:
            return
        if u.get("estado") == nu.SEM_DADOS:  # tambem se regista o tempo sem dados
            self.reg_met[nome].escrever(t, {"hora_utc": iso_utc(t), "local_ms": t, "estado": nu.SEM_DADOS})
            return
        self.reg_met[nome].escrever(t, {
            "hora_utc": iso_utc(t), "local_ms": t, "mid": num(u["mid"], 8),
            "bid": num(u["bid"], 8), "ask": num(u["ask"], 8), "qb": num(u["qb"], 6),
            "qa": num(u["qa"], 6), "spread_bps": num(u["spread_bps"], 4),
            "spread_ticks": num(u["spread_ticks"], 0), "prof_util_usd": num(u["prof_util_usd"], 0),
            "imp_ref_bps": num(u["imp_ref_bps"], 4), "sigma_curta": num(u["sigma_curta"], 4),
            "sigma_longa": num(u["sigma_longa"], 4), "vol_razao": num(u["vol_razao"], 4),
            "intens_razao": num(u["intens_razao"], 4), "ofi_norm": num(u["ofi_norm"], 4),
            "liq_60s_usd": num(u["liq_60s_usd"], 0), "mark_oraculo_bps": num(u["mark_oraculo_bps"], 4),
            "desequilibrio": num(u["desequilibrio"], 4), "estado": u["estado"],
            "motivos": " | ".join(u["motivos"])})

    async def tarefa_amostras(self) -> None:
        cfg = self.cfg
        passo = cfg.amostra_s
        seguinte = time.monotonic() + passo
        ultimo_painel = 0.0
        while True:
            await asyncio.sleep(max(0.0, seguinte - time.monotonic()))
            seguinte += passo
            if seguinte < time.monotonic() - 5 * passo:  # computador esteve suspenso
                seguinte = time.monotonic() + passo
            t = agora_ms()
            self.tick += 1
            for ln in self._passo("leitura de sinais", self._ler_sinais_novos) or []:
                self._passo("sinal", self._novo_sinal, ln, t)
            for nome, at in self.ativos.items():
                self._passo("amostra " + nome, self._amostrar, nome, at, t)
            self._passo("sinais", self._avancar_sinais, t)
            self._passo("fills", self._avancar_fills, t)
            self._passo("estado", self._escrever_estado, t)
            if time.monotonic() - ultimo_painel >= cfg.painel_s:
                ultimo_painel = time.monotonic()
                self._passo("painel", self._painel, t)
            dia = t // 86_400_000
            if dia != self.dia_limpeza:  # uma vez por dia
                self.dia_limpeza = dia
                self._passo("limpeza", self._limpar_antigos)

    def _escrever_estado(self, t: int) -> None:
        def limpo(v: Any) -> Any:
            if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                return None
            return v
        doc = {"hora_utc": iso_utc(t), "ligado": self.ligado, "versao": VERSAO,
               "orcamento_bps": self.cfg.orcamento, "tamanho_ref_usd": self.cfg.tamanho_usd,
               "ativos": {n: {k: limpo(v) for k, v in at.ultima.items()} for n, at in self.ativos.items()}}
        tmp = self.cfg.estado_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(tmp, self.cfg.estado_json)

    def _painel(self, t: int) -> None:
        def f(v: Any, fmt: str) -> str:
            return fmt % v if isinstance(v, (int, float)) and v == v and abs(v) != float("inf") else "-"

        def usd(v: Any) -> str:
            if not isinstance(v, (int, float)) or v != v:
                return "-"
            return "%.1fM" % (v / 1e6) if v >= 1e6 else ("%.0fk" % (v / 1e3) if v >= 1e3 else "%.0f" % v)

        hora = datetime.fromtimestamp(t / 1000.0).strftime("%H:%M:%S")
        atrasos = [a.atraso_mediano() for a in self.ativos.values() if a.atrasos]
        cab = "%s  %s  atraso dos dados %s ms  sinais %d  fills %d  CoinGlass %s" % (
            hora, "LIGADO" if self.ligado else "SEM LIGACAO", f(nu.mediana(atrasos), "%.0f"),
            self.n_sinais, self.n_fills,
            self.cg_estado + (" (%d liquidacoes, %d dos seus activos)" % (self.cg_total, self.cg_msgs)
                              if self.cfg.cg_chave else ""))
        linhas = [cab, "%-8s %-11s %12s %7s %9s %6s %6s %6s %6s %8s  %s" % (
            "ACTIVO", "ESTADO", "MID", "SPREAD", "PROF.UTIL", "VOL", "INTENS", "FLUXO", "MK-OR", "IMP.REF", "MOTIVO")]
        for n, at in self.ativos.items():
            u = at.ultima
            est = u.get("estado", "")
            if est == nu.SEM_DADOS:
                linhas.append("%-8s %-11s" % (n, est))
                continue
            motivo = " | ".join(u.get("motivos", []))
            if est == nu.AQUECIMENTO:
                motivo = "historico %d de %d" % (u.get("historico", 0), self.cfg.n_aquecimento)
            linhas.append("%-8s %-11s %12s %7s %9s %6s %6s %6s %6s %8s  %s" % (
                n, est, f(u.get("mid"), "%.6g"), f(u.get("spread_bps"), "%.2f"),
                usd(u.get("prof_util_usd")) + ("+" if u.get("qmax_truncado") else ""),
                f(u.get("vol_razao"), "%.2f"), f(u.get("intens_razao"), "%.2f"),
                f(u.get("ofi_norm"), "%+.2f"), f(u.get("mark_oraculo_bps"), "%.1f"),
                f(u.get("imp_ref_bps"), "%.2f"), motivo))
        print("\n".join(linhas) + "\n", flush=True)

    def fechar(self) -> None:
        """Ao parar, escreve o que estava a meio (com o que falta em branco)."""
        for s in self.sinais:
            if not s.escrito and s.m0 is not None:
                self._passo("fecho de sinal", self._escrever_sinal, s)
        for fa in self.fills:
            self._passo("fecho de fill", self._escrever_fill, fa)
        self.sinais, self.fills = [], []


# ==========================================================================
# Pedidos simples a Hyperliquid (sem chave)
# ==========================================================================
def pedir_info(url: str, corpo: Dict[str, Any], timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, data=json.dumps(corpo).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def obter_sz_decimals(cfg: Config) -> Dict[str, int]:
    meta = pedir_info(cfg.url_info, {"type": "meta"})
    mapa = {u["name"]: int(u["szDecimals"]) for u in meta.get("universe", [])}
    faltam = [n for n in cfg.ativos if n not in mapa]
    if faltam:
        parecidos = [k for k in mapa if any(k.upper() == x.upper() for x in faltam)]
        raise SystemExit("Activos que a Hyperliquid nao reconhece: %s. %s" % (
            ", ".join(faltam), ("Queria dizer %s?" % ", ".join(parecidos)) if parecidos else
            "Os nomes sao sensiveis a maiusculas (BTC, ETH, kPEPE)."))
    return {n: mapa[n] for n in cfg.ativos}


# ==========================================================================
# Comando: correr
# ==========================================================================
async def _correr(cfg: Config) -> None:
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
    med = Medidor(cfg, sz)
    print("Medidor %s a correr. Activos: %s. Orcamento de custo: %.2f bps. Ctrl+C para parar." % (
        VERSAO, ", ".join(cfg.ativos), cfg.orcamento))
    print("Dados em: %s\n" % cfg.pasta)
    tarefas = [asyncio.ensure_future(med.tarefa_hyperliquid()), asyncio.ensure_future(med.tarefa_amostras())]
    if cfg.cg_chave:
        tarefas.append(asyncio.ensure_future(med.tarefa_coinglass()))
        if cfg.cg_rest:
            tarefas.append(asyncio.ensure_future(med.tarefa_coinglass_rest()))
    try:
        await asyncio.gather(*tarefas)
    finally:
        for tk in tarefas:
            tk.cancel()
        med.fechar()


def cmd_correr(cfg: Config) -> None:
    try:
        import websockets  # noqa: F401
    except ImportError:
        raise SystemExit("Falta a biblioteca websockets. Corra:  python3 -m pip install websockets")
    try:
        asyncio.run(_correr(cfg))
    except KeyboardInterrupt:
        print("\nMedidor parado. Os registos ficaram em %s" % cfg.pasta)


# ==========================================================================
# Comando: sinal
# ==========================================================================
def cmd_sinal(cfg: Config, ativo: str, lado: str, preco: str, nota: str) -> None:
    nu.interpretar_lado(lado)  # valida
    os.makedirs(cfg.pasta, exist_ok=True)
    novo = not os.path.exists(cfg.sinais_csv) or os.path.getsize(cfg.sinais_csv) == 0
    with open(cfg.sinais_csv, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if novo:
            f.write(Medidor.CAB_SINAIS + "\n")
        w.writerow([iso_utc(agora_ms()), ativo, lado, preco, "", "", nota])
    print("Sinal registado: %s %s %s" % (ativo, lado, preco))


# ==========================================================================
# Comando: verificar
# ==========================================================================
async def _verificar(cfg: Config) -> bool:
    ok_total = True

    def linha(ok: Optional[bool], txt: str) -> None:
        nonlocal ok_total
        marca = "OK    " if ok else ("AVISO " if ok is None else "FALHA ")
        if ok is False:
            ok_total = False
        print(marca + txt)

    print("Verificacao do medidor %s\n" % VERSAO)
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

    nome = cfg.ativos[0]
    visto: Dict[str, Any] = {}
    try:
        async with websockets.connect(cfg.url_ws, ping_interval=None, max_size=None, open_timeout=20) as ws:
            for tipo in ("bbo", "l2Book", "trades", "activeAssetCtx"):
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": tipo, "coin": nome}}))
            if cfg.endereco:
                await ws.send(json.dumps({"method": "subscribe",
                                          "subscription": {"type": "userFills", "user": cfg.endereco}}))
            await ws.send(json.dumps({"method": "ping"}))
            fim = time.monotonic() + 20
            precisos = {"bbo", "l2Book", "activeAssetCtx", "pong"} | ({"userFills"} if cfg.endereco else set())
            while time.monotonic() < fim and not (precisos <= set(visto) and "trades" in visto):
                try:
                    bruto = await asyncio.wait_for(ws.recv(), timeout=max(0.1, fim - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                msg = json.loads(bruto)
                c = msg.get("channel")
                if c and c not in visto:
                    visto[c] = (msg.get("data"), agora_ms())
                if c == "error":
                    linha(False, "a Hyperliquid devolveu erro: %s" % msg.get("data"))
    except Exception as e:
        linha(False, "nao consegui abrir o WebSocket %s (%s)" % (cfg.url_ws, e))
        return False
    linha(True, "WebSocket aberto em %s" % cfg.url_ws)
    linha("pong" in visto, "resposta ao ping de manutencao")

    if "bbo" in visto:
        d, t = visto["bbo"]
        bb = d["bbo"]
        b, a = float(bb[0]["px"]), float(bb[1]["px"])
        linha(b < a, "bbo de %s: compra %.6g < venda %.6g (primeiro elemento = compra)" % (nome, b, a))
        atraso = t - int(d["time"])
        linha(None if abs(atraso) > 3000 else True,
              "atraso dos dados: %d ms%s" % (atraso, " (acerte o relogio do computador)" if abs(atraso) > 3000 else ""))
    else:
        linha(False, "nao chegou nenhum bbo de %s em 20 s" % nome)
    if "l2Book" in visto:
        d, _ = visto["l2Book"]
        nb, na = len(d["levels"][0]), len(d["levels"][1])
        b0, a0 = float(d["levels"][0][0]["px"]), float(d["levels"][1][0]["px"])
        desc = all(float(d["levels"][0][i]["px"]) > float(d["levels"][0][i + 1]["px"]) for i in range(nb - 1))
        asc = all(float(d["levels"][1][i]["px"]) < float(d["levels"][1][i + 1]["px"]) for i in range(na - 1))
        linha(b0 < a0 and desc and asc, "livro: %d niveis de compra e %d de venda, bem ordenados" % (nb, na))
    else:
        linha(False, "nao chegou o livro (l2Book)")
    if "trades" in visto:
        tr = visto["trades"][0][0]
        linha(all(k in tr for k in ("px", "sz", "time", "coin")), "negocios: campos px, sz, time presentes")
    else:
        linha(None, "nenhum negocio de %s em 20 s (normal em activos calmos)" % nome)
    if "activeAssetCtx" in visto:
        ctx = visto["activeAssetCtx"][0].get("ctx", {})
        linha("markPx" in ctx and "oraclePx" in ctx, "contexto: markPx %s, oraclePx %s" % (ctx.get("markPx"), ctx.get("oraclePx")))
    else:
        linha(False, "nao chegou o contexto do activo (mark e oracle)")
    if cfg.endereco:
        if "userFills" in visto:
            d = visto["userFills"][0]
            linha(True, "fills da conta: subscricao aceite (%d fills antigos recebidos, nao sao mostrados)"
                  % len(d.get("fills", []) or []))
        else:
            linha(False, "a subscricao de fills nao respondeu; confirme o endereco em config.ini")
    else:
        linha(None, "sem endereco em config.ini: os fills nao serao medidos")

    if cfg.cg_chave:
        try:
            async with websockets.connect(cfg.url_cg + cfg.cg_chave, ping_interval=None, open_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribe", "channels": cfg.cg_canais}))
                await ws.send("ping")
                recebido = []
                fim = time.monotonic() + 15
                while time.monotonic() < fim and len(recebido) < 3:
                    try:
                        recebido.append(str(await asyncio.wait_for(ws.recv(), timeout=fim - time.monotonic()))[:160])
                    except asyncio.TimeoutError:
                        break
            linha(None if not recebido else True,
                  "CoinGlass: ligacao aberta; primeiras respostas: %s" % (recebido or "nenhuma em 15 s"))
        except Exception as e:
            linha(False, "CoinGlass: ligacao falhou (%s)" % e)
    else:
        linha(None, "sem chave CoinGlass: a medida de liquidacoes fica desligada")

    return ok_total


def cmd_verificar(cfg: Config) -> None:
    ok = asyncio.run(_verificar(cfg))
    print("\n" + ("Tudo certo: pode arrancar o medidor." if ok
                  else "Ha falhas acima. Copie este texto todo para se poder corrigir."))
    sys.exit(0 if ok else 1)


# ==========================================================================
# Comando: relatorio
# ==========================================================================
def _ler_csv(caminho: str) -> List[Dict[str, str]]:
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _ordens(fills: List[Dict[str, str]], horizontes: List[int]) -> List[Dict[str, Any]]:
    """Junta os fills parciais da mesma ordem; cada medida e ponderada pelo valor negociado."""
    grupos: "OrderedDict[Tuple[str, str], List[Dict[str, str]]]" = OrderedDict()
    for x in fills:
        chave = (x.get("ativo", ""), x.get("oid") or "tid-%s" % x.get("tid", ""))
        grupos.setdefault(chave, []).append(x)
    ordens = []
    for (ativo, _), fs in grupos.items():
        def pond(col: str) -> float:
            pares = [(flt(x.get(col)), flt(x.get("valor_usd"))) for x in fs if x.get(col) not in ("", None)]
            pares = [(a, b) for a, b in pares if a == a and b == b and b > 0]
            tot = sum(b for _, b in pares)
            return sum(a * b for a, b in pares) / tot if tot > 0 else float("nan")

        valores = [(x, flt(x.get("valor_usd"))) for x in fs]
        valor = sum(v for _, v in valores if v == v)
        v_taker = sum(v for x, v in valores if v == v and x.get("taker") == "1")
        atrasos = [flt(x.get("atraso_s")) for x in fs if x.get("atraso_s")]
        direc = fs[0].get("dir", "")
        ordens.append({
            "ativo": ativo, "fills": len(fs), "valor": valor,
            "taker": (v_taker >= 0.5 * valor) if valor > 0 else fs[0].get("taker") == "1",
            "tipo": "abre" if direc.startswith("Open") else ("fecha" if direc.startswith("Close") else "outro"),
            "estado": fs[0].get("estado", ""), "e": pond("e_bps"), "f": pond("f_bps"),
            "a": {h: pond("a_%d" % h) for h in horizontes}, "d": pond("d_bps"),
            "c_preco": pond("c_preco_bps"), "sinal": fs[0].get("sinal_id", ""),
            "n_no_sinal": fs[0].get("ordem_no_sinal", ""),
            "atraso": min(atrasos) if atrasos else float("nan")})
    return ordens


def _pond_valor(ordens: List[Dict[str, Any]], campo: str) -> float:
    pares = [(o[campo], o["valor"]) for o in ordens if o[campo] == o[campo] and o["valor"] > 0]
    tot = sum(v for _, v in pares)
    return sum(a * v for a, v in pares) / tot if tot > 0 else float("nan")


def frase_do_horizonte(h: int) -> str:
    """Aos 300 s o R diagnostica a entrada. Acima disso, e o drift do sinal."""
    if h <= 300:
        return "R aos %d s mede a qualidade da ENTRADA, nao o resultado do trade." % h
    return ("R aos %d s e o drift do sinal a essa duracao. A regra compara as rotas "
            "nessa escala; a entrada mede-se nos horizontes curtos." % h)


def cmd_relatorio(cfg: Config) -> None:
    out: List[str] = []
    w = out.append

    def f2(v: float) -> str:
        return "%.2f" % v if v == v and abs(v) != float("inf") else "-"

    todos_sinais = _ler_csv(cfg.reg_sinais)
    sinais = [s for s in todos_sinais if s.get("mid0")]
    fills = _ler_csv(cfg.reg_fills)
    hs = cfg.horizontes_fill
    ordens = _ordens(fills, hs)
    abre = [o for o in ordens if o["tipo"] == "abre"]
    w("RELATORIO DO MEDIDOR %s  (%s)" % (VERSAO, datetime.now().strftime("%Y-%m-%d %H:%M")))
    w("Progresso da Etapa 2: %d de 100 sinais medidos, %d de 30 ordens de abertura (%d ordens, %d fills)"
      % (len(sinais), len(abre), len(ordens), len(fills)))
    w("")

    # taxas da regra: as medidas nos fills quando ha ordens que cheguem, senao as do config.ini
    def taxa(taker: bool, defeito: float) -> Tuple[float, str]:
        fs = [o["f"] for o in ordens if o["taker"] == taker and o["f"] == o["f"]]
        if len(fs) >= 5:
            return nu.media(fs), "medida em %d ordens" % len(fs)
        return defeito, "config.ini"

    f_t, origem_t = taxa(True, cfg.taxa_taker)
    f_m, origem_m = taxa(False, cfg.taxa_maker)

    # ---- sinais e regra passiva / agressiva (seccao 6.5)
    h = cfg.h_regra
    col = "r_%d" % h
    w("1. SINAIS E REGISTO SOMBRA  (horizonte %d s, prazo da passiva %.0f s)" % (h, cfg.prazo_s))
    w("   " + frase_do_horizonte(h))
    if not sinais:
        w("   Ainda nao ha sinais medidos.")
    else:
        completos = [s for s in sinais if s.get(col, "") != "" and s.get("sombra_preenchida") in ("0", "1")]
        w("   %d sinais registados | %d sem dados de mercado | %d com resultado e registo sombra completos"
          % (len(todos_sinais), len(todos_sinais) - len(sinais), len(completos)))
        w("   %d com impacto medido | %d em que a ordem nao cabia no livro visivel | %d chegaram com mais de 2 s"
          % (sum(1 for s in sinais if s.get("imp_viavel") == "1"), sum(1 for s in sinais if s.get("imp_viavel") == "0"),
             sum(1 for s in sinais if s.get("fresco") == "0")))
        if col not in (todos_sinais[0] if todos_sinais else {}):
            w("   Aviso: o horizonte da regra (%d s) nao existe nos registos antigos." % h)
        dp = [flt(s.get("desvio_preco_bps")) for s in sinais if s.get("desvio_preco_bps")]
        if dp:
            w("   Preco indicado no sinal: o mid ja estava em media %s bps para la dele (n=%d; positivo = pior)"
              % (f2(nu.media(dp)), len(dp)))
        w("   Taxas usadas na regra: taker %s bps (%s), maker %s bps (%s)" % (f2(f_t), origem_t, f2(f_m), origem_m))

        def linha_grupo(titulo: str, grupo: List[Dict[str, str]], com_regra: bool) -> None:
            if not grupo:
                return
            ench = [s for s in grupo if s["sombra_preenchida"] == "1"]
            w("   %-16s n=%-4d R medio %s bps | passiva preenche pelo menos %.0f %% | R nos preenchidos %s bps"
              % (titulo, len(grupo), f2(nu.media([flt(s[col]) for s in grupo])), 100.0 * len(ench) / len(grupo),
                 f2(nu.media([flt(s[col]) for s in ench]))))
            if not com_regra:
                return
            # a regra compara as duas rotas nos mesmos sinais: os que tem impacto medido e viavel
            regra = [s for s in grupo if s.get("imp_viavel") == "1" and s.get("imp_bps", "") != ""]
            if not regra:
                w("   %16s regra passiva/agressiva: sem sinais com impacto medido" % "")
                return
            ench_r = [s for s in regra if s["sombra_preenchida"] == "1"]
            imp = nu.media([flt(s["imp_bps"]) for s in regra])
            pi = len(ench_r) / len(regra)
            r_ench = nu.media([flt(s[col]) for s in ench_r]) if ench_r else float("nan")
            ganho = nu.media([flt(s["ganho_passiva_bps"]) for s in ench_r]) if ench_r else float("nan")
            v_ag, v_pas, escolha = nu.regra_rotas(
                nu.media([flt(s[col]) for s in regra]), imp, pi, r_ench, ganho, f_t, f_m)
            w("   %16s regra (n=%d): impacto %s bps | V agressiva %s | V passiva %s  ->  %s"
              % ("", len(regra), f2(imp), f2(v_ag), f2(v_pas), escolha.upper()))

        linha_grupo("TODOS", completos, True)
        if len(cfg.ativos) > 1:
            for nome in cfg.ativos:
                linha_grupo(nome, [s for s in completos if s["ativo"] == nome], True)
        w("   Pelo estado do livro no instante do sinal:")
        for est in (nu.VERDE, nu.AMARELO, nu.VERMELHO, nu.AQUECIMENTO):
            linha_grupo("  " + est.lower(), [s for s in completos if s.get("estado") == est], False)
        favor = [s for s in completos if s.get("ofi_lado") and flt(s["ofi_lado"]) > 0]
        contra = [s for s in completos if s.get("ofi_lado") and flt(s["ofi_lado"]) < 0]
        if favor or contra:
            w("   Pelo fluxo de ordens no instante do sinal:")
            linha_grupo("  fluxo a favor", favor, False)
            linha_grupo("  fluxo contra", contra, False)
        w("   A passiva simulada ignora cancelamentos a frente na fila: a percentagem real de preenchimento e maior.")
        if len(completos) < 100:
            w("   Nota: com menos de 100 sinais completos estes valores sao so indicativos.")
    w("")

    # ---- custo por ordem (seccao 6.7)
    w("2. CUSTO MEDIDO POR ORDEM  (bps; positivo = custo; fills parciais juntos e ponderados pelo valor)")
    if not ordens:
        w("   Ainda nao ha fills registados.")
    else:
        def bloco(titulo: str, grupo: List[Dict[str, Any]], com_d: bool = False) -> None:
            if not grupo:
                return
            adv = " / ".join("%ds %s" % (hh, f2(nu.media([o["a"][hh] for o in grupo]))) for hh in hs)
            txt = ("   %-18s n=%-4d E %s (mediana %s) | F %s | A %s"
                   % (titulo, len(grupo), f2(nu.media([o["e"] for o in grupo])),
                      f2(nu.mediana([o["e"] for o in grupo])), f2(nu.media([o["f"] for o in grupo])), adv))
            if com_d:  # D so na primeira ordem de cada sinal: as seguintes nao tem sinal proprio
                prim = [o for o in grupo if o["d"] == o["d"] and str(o["n_no_sinal"]) in ("1", "")]
                txt += " | D %s (n=%d) | atraso %s s" % (f2(nu.media([o["d"] for o in prim])), len(prim),
                                                        f2(nu.mediana([o["atraso"] for o in prim])))
            w(txt)

        def seccao(nome: str, grupo: List[Dict[str, Any]], com_d: bool) -> None:
            if not grupo:
                return
            w("   %s" % nome)
            bloco("  todas", grupo, com_d)
            bloco("  taker", [o for o in grupo if o["taker"]], com_d)
            bloco("  maker", [o for o in grupo if not o["taker"]], com_d)
            w("     ponderado pelo valor negociado: E %s | F %s" % (f2(_pond_valor(grupo, "e")), f2(_pond_valor(grupo, "f"))))

        seccao("ABERTURAS", abre, True)
        for est in (nu.VERDE, nu.AMARELO, nu.VERMELHO, nu.AQUECIMENTO, nu.SEM_DADOS):
            bloco("  estado " + est.lower(), [o for o in abre if o["estado"] == est], True)
        if len(cfg.ativos) > 1:
            for nome in cfg.ativos:
                bloco("  " + nome, [o for o in abre if o["ativo"] == nome], True)
        extra = [o for o in abre if str(o["n_no_sinal"]) not in ("", "1")]
        if extra:
            w("     %d aberturas adicionais no mesmo sinal: ficam fora de D, porque nao tem sinal proprio" % len(extra))
        cp = [o["c_preco"] for o in abre if o["c_preco"] == o["c_preco"]]
        if cp:
            w("     preco pago face ao preco indicado no sinal: %s bps em media (n=%d; positivo = pior)"
              % (f2(nu.media(cp)), len(cp)))
        seccao("FECHOS (alvos e stops)", [o for o in ordens if o["tipo"] == "fecha"], False)
        seccao("OUTROS (inversoes de posicao ou tipo desconhecido)", [o for o in ordens if o["tipo"] == "outro"], False)
        sem_f = sum(1 for x in fills if x.get("f_bps", "") == "")
        if sem_f:
            w("   Aviso: %d fills sem F, por a taxa nao ter sido cobrada em USDC." % sem_f)
        w("   E = execucao, F = taxa, A = seleccao adversa, D = decisao e latencia.")
        w("   Nas medias cada ordem conta uma vez; a linha 'ponderado pelo valor' da o custo por dolar.")
    w("")

    # ---- tempo em cada estado
    w("3. TEMPO EM CADA ESTADO  (dia inteiro, inclui o tempo sem dados; o estado nos SEUS sinais esta na seccao 1)")
    algum = False
    if os.path.isdir(cfg.pasta_metricas):
        for nome in cfg.ativos:
            cont: Dict[str, int] = {}
            mot: Dict[str, int] = {}
            prefixo = nome.replace("/", "_").replace(":", "_") + "_"
            for fich in sorted(os.listdir(cfg.pasta_metricas)):
                if not (fich.startswith(prefixo) and fich.endswith(".csv")):
                    continue
                for ln in _ler_csv(os.path.join(cfg.pasta_metricas, fich)):
                    est = ln.get("estado") or "?"
                    cont[est] = cont.get(est, 0) + 1
                    for m in (ln.get("motivos") or "").split(" | "):
                        if m:
                            chave = m.rsplit(" p", 1)[0]
                            mot[chave] = mot.get(chave, 0) + 1
            tot = sum(cont.values())
            if not tot:
                continue
            algum = True
            w("   %-6s %s" % (nome, "  ".join("%s %.1f %%" % (k.lower(), 100.0 * v / tot)
                                            for k, v in sorted(cont.items()))))
            if mot:
                w("          alarmes mais frequentes: " + ", ".join(
                    "%s (%d)" % kv for kv in sorted(mot.items(), key=lambda z: -z[1])[:4]))
    if not algum:
        w("   Ainda nao ha metricas guardadas.")
    w("")
    texto = "\n".join(out)
    print(texto)
    os.makedirs(cfg.pasta, exist_ok=True)
    with open(os.path.join(cfg.pasta, "relatorio.txt"), "w", encoding="utf-8") as f:
        f.write(texto + "\n")


def medida_do_activo(cfg: Config, ativo: str) -> Dict[str, Any]:
    """A regra 6.5 nos sinais completos deste activo. Nao muda nada por si."""
    vazio = {"n": 0, "completos": 0, "v_ag": float("nan"), "v_pas": float("nan"), "escolha": ""}
    if not os.path.exists(cfg.reg_sinais):
        return vazio
    col = "r_%d" % cfg.h_regra
    sinais = [s for s in _ler_csv(cfg.reg_sinais)
              if s.get("ativo", "").upper() == ativo.upper() and s.get("mid0")]
    completos = [s for s in sinais if s.get(col, "") != "" and s.get("sombra_preenchida") in ("0", "1")]
    regra = [s for s in completos if s.get("imp_viavel") == "1" and s.get("imp_bps", "") != ""]
    if not regra:
        vazio["completos"] = len(completos)
        return vazio
    ench = [s for s in regra if s["sombra_preenchida"] == "1"]
    fills = _ler_csv(cfg.reg_fills) if os.path.exists(cfg.reg_fills) else []
    ordens = _ordens(fills, cfg.horizontes_fill)

    def taxa(taker: bool, defeito: float) -> float:
        fs = [o["f"] for o in ordens if o["taker"] == taker and o["f"] == o["f"]]
        return nu.media(fs) if len(fs) >= 5 else defeito

    pi = len(ench) / len(regra)
    v_ag, v_pas, escolha = nu.regra_rotas(
        nu.media([flt(s[col]) for s in regra]),
        nu.media([flt(s["imp_bps"]) for s in regra]),
        pi,
        nu.media([flt(s[col]) for s in ench]) if ench else float("nan"),
        nu.media([flt(s["ganho_passiva_bps"]) for s in ench]) if ench else float("nan"),
        taxa(True, cfg.taxa_taker), taxa(False, cfg.taxa_maker))
    return {"n": len(regra), "completos": len(completos), "v_ag": v_ag, "v_pas": v_pas, "escolha": escolha}


def cmd_bilhete(cfg: Config, ativo: str, lado_txt: str, tamanho: str) -> None:
    """Etapa 3: imprime a ordem pronta a partir do estado.json. Nao a envia."""
    if not os.path.exists(cfg.estado_json):
        raise SystemExit("Nao ha estado.json. Arranque o medidor e deixe-o a medir.")
    with open(cfg.estado_json, encoding="utf-8") as f:
        doc = json.load(f)
    ativos = doc.get("ativos") or {}
    u = ativos.get(ativo) or ativos.get(ativo.upper())
    if not u:
        raise SystemExit("Activo %r nao esta no estado. A medir: %s" % (ativo, ", ".join(ativos)))
    lado = nu.interpretar_lado(lado_txt)
    tam = flt(tamanho) if tamanho else cfg.tamanho_usd
    if not (tam == tam and tam > 0):
        raise SystemExit("Tamanho invalido: %r" % tamanho)

    def num_ou_nan(v: Any) -> float:
        if v is None:
            return float("nan")
        x = flt(v)
        return x

    chave_niveis = "niveis_compra" if lado > 0 else "niveis_venda"
    brutos = u.get(chave_niveis) or []
    niveis = []
    for par in brutos:
        if isinstance(par, (list, tuple)) and len(par) >= 2:
            px, sz = flt(par[0]), flt(par[1])
            if px == px and sz == sz and px > 0 and sz > 0:
                niveis.append((px, sz))
    if niveis and u.get("sz_decimals") is not None:
        b = nu.decidir_rota(lado, tam, cfg.orcamento, str(u.get("estado") or ""),
                            num_ou_nan(u.get("bid")), num_ou_nan(u.get("ask")),
                            niveis, int(u["sz_decimals"]), cfg.theta)
    else:
        imp = num_ou_nan(u.get("imp_compra_bps") if lado > 0 else u.get("imp_venda_bps"))
        qmax = num_ou_nan(u.get("qmax_usd_compra") if lado > 0 else u.get("qmax_usd_venda"))
        teto = num_ou_nan(u.get("p_teto_compra") if lado > 0 else u.get("p_teto_venda"))
        b = nu.emitir_bilhete(lado, tam, cfg.orcamento, str(u.get("estado") or ""),
                              num_ou_nan(u.get("bid")), num_ou_nan(u.get("ask")), imp, qmax, teto)
    nome = next((k for k in ativos if k.upper() == ativo.upper()), ativo)
    med = medida_do_activo(cfg, nome)
    if nu.rota_com_medida(b.rota, med["n"], med["escolha"]) != b.rota:
        toque = num_ou_nan(u.get("bid") if lado > 0 else u.get("ask"))
        b = nu.Bilhete(
            "passiva", "ALO", toque, b.tamanho_usd,
            "o livro deixava atravessar; com %d sinais a medida prefere a passiva (V passiva %.2f, V agressiva %.2f)."
            % (med["n"], med["v_pas"], med["v_ag"]),
            b.qmax_usd, b.imp_bps)
    print("BILHETE  %s  %s %s  (medidor %s, livro de %s)" % (
        b.rota.upper(), "compra" if lado > 0 else "venda", ativo, doc.get("versao", VERSAO), doc.get("hora_utc", "")))
    print("Estado do livro: %s" % (u.get("estado") or "-"))
    if u.get("motivos"):
        print("Motivo: %s" % " | ".join(u.get("motivos") or []))
    if b.tipo:
        print("Tipo: %s    preco: %s    tamanho: %.2f usd    validade: %.0f s" % (
            b.tipo, ("%.8g" % b.preco) if b.preco == b.preco else "-", b.tamanho_usd, cfg.prazo_s))
    if b.qmax_usd == b.qmax_usd:
        print("Qmax no teto: %.0f usd    Imp(Q): %s" % (
            b.qmax_usd, ("%.2f bps" % b.imp_bps) if b.imp_bps == b.imp_bps else "-"))
    print(b.nota)
    if med["n"] == 0:
        print("Medida: ainda nao ha sinais deste activo com impacto. A rota e so a do livro.")
    else:
        print("Medida aos %d s, n=%d: V agressiva %.2f | V passiva %.2f -> %s." % (
            cfg.h_regra, med["n"], med["v_ag"], med["v_pas"], med["escolha"].upper()))
        if med["n"] < nu.MINIMO_REGRA:
            print("Com menos de %d sinais nao muda a rota. Faltam %d." % (nu.MINIMO_REGRA, nu.MINIMO_REGRA - med["n"]))
    ofi = u.get("ofi_norm")
    if isinstance(ofi, (int, float)) and ofi == ofi:
        lado_fluxo = lado * ofi
        print("Fluxo face a ordem: %+.2f (%s). Nao muda a rota." % (
            lado_fluxo, "a favor" if lado_fluxo > 0 else "contra" if lado_fluxo < 0 else "neutro"))
    print("Nao enviado. Quem envia e a pessoa. A Etapa 4 e que tem chave, e ainda nao.")


# ==========================================================================
# Entrada
# ==========================================================================
def main(argv: Optional[List[str]] = None) -> None:
    aqui = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Medidor de execucao para a Hyperliquid (so leitura).")
    ap.add_argument("--config", default=os.path.join(aqui, "config.ini"), help="caminho do config.ini")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("correr", help="corre o medidor (e o que acontece sem comando)")
    sub.add_parser("verificar", help="testa ligacoes e formato dos dados")
    sub.add_parser("relatorio", help="resume o que foi medido")
    ps = sub.add_parser("sinal", help="regista um sinal agora")
    ps.add_argument("ativo")
    ps.add_argument("lado", help="compra ou venda")
    ps.add_argument("preco", nargs="?", default="")
    ps.add_argument("--nota", default="")
    pb = sub.add_parser("bilhete", help="imprime a ordem pronta, sem a enviar")
    pb.add_argument("ativo")
    pb.add_argument("lado", help="compra ou venda")
    pb.add_argument("tamanho_usd", nargs="?", default="")
    args = ap.parse_args(argv)
    cfg = Config(args.config)
    os.makedirs(cfg.pasta, exist_ok=True)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.handlers.RotatingFileHandler(
                                      os.path.join(cfg.pasta, "medidor.log"), maxBytes=5_000_000,
                                      backupCount=3, encoding="utf-8")])
    if args.cmd == "verificar":
        cmd_verificar(cfg)
    elif args.cmd == "relatorio":
        cmd_relatorio(cfg)
    elif args.cmd == "sinal":
        cmd_sinal(cfg, args.ativo, args.lado, args.preco, args.nota)
    elif args.cmd == "bilhete":
        cmd_bilhete(cfg, args.ativo, args.lado, args.tamanho_usd)
    else:
        cmd_correr(cfg)


if __name__ == "__main__":
    main()
