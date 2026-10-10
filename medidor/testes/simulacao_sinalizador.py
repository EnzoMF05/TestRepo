"""Simulacao de ponta a ponta do sinalizador de reversao (sem internet).

Estende a bolsa falsa de testes/simulacao.py com o canal candle (15 m e 1 h,
construidos a partir dos proprios negocios gerados, com t e T correctos), um
servico info falso em HTTP (meta, candleSnapshot, fundingHistory,
clearinghouseState, vaultDetails), um leaderboard falso e uma CoinGlass falsa.
Corre o Sinalizador em asyncio contra eles, com um config.ini temporario, e
confere os registos com o que a bolsa falsa enviou.

O historico sintetico de BTC e um processo de Ornstein-Uhlenbeck em log-preco
com meia-vida de 8 h, gerado em velas de 15 m e agregado a 1 h; o de ETH e um
passeio aleatorio, para conferir que o portao fecha sob a nula. A parte ao vivo
de BTC segue um guiao em z (excursao, reentrada, alvo, segunda excursao com um
corte de ligacao na vela da reentrada).

Tempo acelerado: o sinalizador nao tem opcao de relogio nem de velas
encurtadas, por isso a simulacao substitui, so em memoria, a funcao agora_ms do
modulo sinalizador (e a ms da bolsa falsa) por um relogio virtual que anda
ACELERACAO vezes mais depressa do que o relogio real. Os ficheiros em disco nao
sao tocados. Com 240x, uma vela de 15 m demora 3,75 s reais.

Correr (a partir da pasta do medidor, com websockets instalado):
    python3 testes/simulacao_sinalizador.py
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import math
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

import websockets  # noqa: E402

import medidor as md  # noqa: E402
import nucleo as nu  # noqa: E402
import reversao as rv  # noqa: E402
import simulacao as sim  # noqa: E402
import sinalizador as si  # noqa: E402

MS_15M = 900_000
MS_1H = 3_600_000
ACELERACAO = 240                 # segundos virtuais por segundo real
DIAS_HISTORICO = 60              # 5760 velas de 15 m, 1440 de 1 h
MEIA_VIDA_OU_H = 8.0             # meia-vida do OU sintetico, em horas
SIGMA_EQ_OU = 0.01               # desvio padrao estacionario do desvio (log-preco)
CHAVE_COINGLASS = "CHAVE-FALSA-COINGLASS-7731-NUNCA-NO-LOG"
# arranque virtual: terca-feira as 01:00 UTC (sem fim-de-semana nem mudanca de dia durante a corrida)
T_FIM_HISTORICO = int(datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
# guiao da parte ao vivo de BTC, em z (multiplicado pelo lado da excursao):
#   k3-k4 FORA, k5 reentrada (sinal), k9 alvo, k12-k13 FORA, k14 reentrada com corte de ligacao
GUIAO_Z = [0.4, 0.9, 1.5, 2.15, 2.3, 1.65, 1.6, 0.8, -0.2, -0.7, 0.6, 1.6, 2.2, 2.3, 1.65, 1.0, 0.5, 0.2]
K_SINAL = 5
K_ALVO = 9
K_ATRASO = 10
K_CORTE = 14
ENDERECO_HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"


def endereco_falso(semente: str) -> str:
    """Endereco com a forma 0x e 40 hexadecimais, derivado de um texto (nunca um endereco real)."""
    return "0x" + hashlib.sha256(("falso:" + semente).encode("utf-8")).hexdigest()[:40]


UTILIZADORES = [endereco_falso("negociante %d" % i) for i in range(6)]
BALEIAS = [endereco_falso("baleia %d" % i) for i in range(5)]
BALEIA_POBRE = endereco_falso("baleia pobre")


# --------------------------------------------------------------------------
# Relogio virtual
# --------------------------------------------------------------------------
class FiltroConsola(logging.Filter):
    """Deixa passar para a consola so o primeiro aviso med=off (o ficheiro de log recebe todos)."""

    def __init__(self) -> None:
        super().__init__()
        self.med_off = 0

    def filter(self, registo: logging.LogRecord) -> bool:
        if "med=off" in registo.getMessage():
            self.med_off += 1
            return self.med_off == 1
        return True


class RelogioVirtual:
    """ms() = inicio virtual + (tempo real decorrido) x aceleracao; substitui sinalizador.agora_ms e simulacao.ms."""

    def __init__(self, inicio_virtual_ms: int, aceleracao: float):
        self.inicio_v = int(inicio_virtual_ms)
        self.acel = float(aceleracao)
        self.inicio_r = time.monotonic()

    def ms(self) -> int:
        return self.inicio_v + int((time.monotonic() - self.inicio_r) * 1000.0 * self.acel)


# --------------------------------------------------------------------------
# Historico sintetico: OU em log-preco (meia-vida 8 h) em velas de 15 m, agregadas a 1 h
# --------------------------------------------------------------------------
def fmt(x: float, casas: int) -> str:
    return "%.*f" % (casas, x)


def gerar_historico(nome: str, preco0: float, casas: int, dias: int, semente: int,
                    reversao: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(velas de 15 m, velas de 1 h) no formato de candleSnapshot (precos em texto), a acabar em T_FIM_HISTORICO.

    Com reversao: x_k = mu + phi (x_{k-1} - mu) + eps_k, phi = 2^(-1/(4 H)), eps com desvio
    sigma_eq sqrt(1 - phi^2), ou seja um OU com meia-vida H horas e desvio estacionario
    sigma_eq. Sem reversao: passeio aleatorio com desvio 0,0015 por vela de 15 m.
    """
    rng = random.Random(semente)
    n = dias * 96
    t0 = T_FIM_HISTORICO - n * MS_15M
    mu = math.log(preco0)
    phi = 2.0 ** (-1.0 / (4.0 * MEIA_VIDA_OU_H))
    eps_sd = SIGMA_EQ_OU * math.sqrt(1.0 - phi * phi) if reversao else 0.0015
    x = mu + (rng.gauss(0.0, 1.0) * SIGMA_EQ_OU if reversao else 0.0)
    velas15: List[Dict[str, Any]] = []
    c_ant = math.exp(x)
    for k in range(n):
        if reversao:
            x = mu + phi * (x - mu) + eps_sd * rng.gauss(0.0, 1.0)
        else:
            x += eps_sd * rng.gauss(0.0, 1.0)
        c = round(math.exp(x), casas)
        o = c_ant
        h = round(max(o, c) * (1.0 + rng.uniform(0.0, 0.004)), casas)
        l = round(min(o, c) * (1.0 - rng.uniform(0.0, 0.004)), casas)
        v = rng.uniform(20.0, 80.0)
        velas15.append({"t": t0 + k * MS_15M, "T": t0 + (k + 1) * MS_15M - 1, "s": nome, "i": "15m",
                        "o": fmt(o, casas), "c": fmt(c, casas), "h": fmt(h, casas), "l": fmt(l, casas),
                        "v": fmt(v, 4), "n": rng.randint(300, 900)})
        c_ant = c
    velas1h = [agregar_velas(velas15[i:i + 4], "1h", casas) for i in range(0, n, 4)]
    return velas15, velas1h


def agregar_velas(grupo: List[Dict[str, Any]], intervalo: str, casas: int) -> Dict[str, Any]:
    """Vela de intervalo maior a partir de velas contiguas: o = o_1, h = max, l = min, c = c_ultima, v e n somados."""
    return {"t": grupo[0]["t"], "T": grupo[-1]["T"], "s": grupo[0]["s"], "i": intervalo,
            "o": grupo[0]["o"], "c": grupo[-1]["c"],
            "h": fmt(max(float(g["h"]) for g in grupo), casas), "l": fmt(min(float(g["l"]) for g in grupo), casas),
            "v": fmt(sum(float(g["v"]) for g in grupo), 4), "n": sum(int(g["n"]) for g in grupo)}


def ancora_e_sigma(velas1h: List[Dict[str, Any]], h_a: float = 96.0) -> Tuple[rv.Ancora, float, float]:
    """A mesma conta do sinalizador: Ancora(h_a) sobre os fechos de 1 h, ajuste AR(1) sobre d depois de 4 h_a velas."""
    anc = rv.Ancora(h_a)
    aquecimento = int(math.ceil(4.0 * h_a))
    d_hist: List[float] = []
    for v in velas1h:
        d = anc.juntar(math.log(float(v["c"])))
        if anc.n > aquecimento:
            d_hist.append(d)
    aj = rv.ajustar_ar1(d_hist[-2160:], rv.lambda_ancora(h_a))
    z = rv.z_score(math.log(float(velas1h[-1]["c"])), anc.valor, aj.sigma_eq)
    return anc, aj.sigma_eq, z


# --------------------------------------------------------------------------
# Armazem partilhado entre a bolsa, o info falso e o leaderboard falso
# --------------------------------------------------------------------------
class Armazem:
    def __init__(self, relogio: RelogioVirtual):
        self.relogio = relogio
        self.velas: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self.preco: Dict[str, float] = {}
        self.sz_decimals = {"BTC": 5, "ETH": 4, "SOL": 2}
        self.pedidos: Dict[str, int] = {}


ARMAZEM: Optional[Armazem] = None


def servir_candle_snapshot(req: Dict[str, Any]) -> List[Dict[str, Any]]:
    """candleSnapshot: velas fechadas (T <= agora virtual) de coin/interval com t >= startTime e T <= endTime, ate 5000."""
    assert ARMAZEM is not None
    coin, intervalo = str(req.get("coin", "")), str(req.get("interval", ""))
    inicio, fim = int(req.get("startTime", 0)), int(req.get("endTime", ARMAZEM.relogio.ms()))
    agora = ARMAZEM.relogio.ms()
    lista = ARMAZEM.velas.get((coin, intervalo), [])
    out = [v for v in list(lista) if v["t"] >= inicio and v["T"] <= min(fim, agora)]
    return out[:5000]


def servir_funding(corpo: Dict[str, Any]) -> List[Dict[str, Any]]:
    """fundingHistory por hora inteira entre startTime e min(endTime, agora), taxa constante 0,0000125."""
    assert ARMAZEM is not None
    agora = ARMAZEM.relogio.ms()
    inicio = int(corpo.get("startTime", agora - 30 * 86_400_000))
    fim = min(int(corpo.get("endTime", agora)), agora)
    primeiro = (inicio // MS_1H + (1 if inicio % MS_1H else 0)) * MS_1H
    out = []
    t = primeiro
    while t <= fim and len(out) < 500:
        out.append({"coin": corpo.get("coin"), "fundingRate": "0.0000125", "premium": "0.0001", "time": t})
        t += MS_1H
    return out


def servir_posicoes(user: str) -> Dict[str, Any]:
    """clearinghouseState de uma baleia falsa: posicoes em BTC e ETH com liquidationPx perto do preco."""
    assert ARMAZEM is not None
    u = (user or "").lower()
    if u not in BALEIAS:
        return {"assetPositions": [], "marginSummary": {"accountValue": "0.0"}}
    i = BALEIAS.index(u)
    posicoes = []
    for coin in ("BTC", "ETH"):
        preco = ARMAZEM.preco.get(coin, 1.0)
        longa = (i + (0 if coin == "BTC" else 1)) % 2 == 0
        valor = 200_000.0 + 150_000.0 * i
        szi = (valor if longa else -valor) / preco
        liq = preco * (1.0 - 0.015) if longa else preco * (1.0 + 0.012)
        posicoes.append({"type": "oneWay", "position": {
            "coin": coin, "szi": "%.5f" % szi, "positionValue": "%.2f" % valor, "entryPx": "%.2f" % preco,
            "liquidationPx": "%.2f" % liq, "leverage": {"type": "cross", "value": 10}}})
    return {"assetPositions": posicoes, "marginSummary": {"accountValue": "%.2f" % (2_500_000.0 + 1e6 * i)}}


class InfoFalso(BaseHTTPRequestHandler):
    """POST /info da Hyperliquid: meta, candleSnapshot, fundingHistory, clearinghouseState, vaultDetails."""

    def do_POST(self) -> None:
        assert ARMAZEM is not None
        n = int(self.headers.get("Content-Length", 0))
        try:
            corpo = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            corpo = {}
        tipo = str(corpo.get("type", ""))
        ARMAZEM.pedidos[tipo] = ARMAZEM.pedidos.get(tipo, 0) + 1
        estado = 200
        if tipo == "meta":
            resp: Any = {"universe": [{"name": n_, "szDecimals": d, "maxLeverage": 40}
                                      for n_, d in ARMAZEM.sz_decimals.items()]}
        elif tipo == "candleSnapshot":
            resp = servir_candle_snapshot(corpo.get("req") or {})
        elif tipo == "fundingHistory":
            resp = servir_funding(corpo)
        elif tipo == "clearinghouseState":
            resp = servir_posicoes(str(corpo.get("user", "")))
        elif tipo == "vaultDetails":
            resp = {"name": "Cofre falso", "vaultAddress": corpo.get("vaultAddress")}
        else:
            resp, estado = {"erro": "tipo desconhecido"}, 422
        dados = json.dumps(resp).encode("utf-8")
        self.send_response(estado)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, *a: Any) -> None:
        pass


class LeaderboardFalso(BaseHTTPRequestHandler):
    """GET do leaderboard: 5 baleias que passam os filtros, uma pobre e o cofre HLP excluido."""

    def do_GET(self) -> None:
        linhas = []
        for i, e in enumerate(BALEIAS + [BALEIA_POBRE, ENDERECO_HLP]):
            valor = 500_000.0 if e == BALEIA_POBRE else 2_500_000.0 + 1e6 * i
            linhas.append({"ethAddress": e, "accountValue": "%.2f" % valor, "prize": 0, "displayName": None,
                           "windowPerformances": [
                               ["day", {"pnl": "1200.0", "roi": "0.004", "vlm": "800000.0"}],
                               ["week", {"pnl": "40000.0", "roi": "0.03", "vlm": "5000000.0"}],
                               ["month", {"pnl": "300000.0", "roi": "0.12", "vlm": "20000000.0"}],
                               ["allTime", {"pnl": "900000.0", "roi": "0.5", "vlm": "90000000.0"}]]})
        dados = json.dumps({"leaderboardRows": linhas}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, *a: Any) -> None:
        pass


# --------------------------------------------------------------------------
# Bolsa falsa com velas
# --------------------------------------------------------------------------
class EstadoVivo:
    """Vela de 15 m e de 1 h em construcao, alvo da vela corrente e ancora replicada, por activo."""

    def __init__(self, nome: str, casas: int, preco: float, t_ini: int, ancora: Optional[rv.Ancora],
                 sigma: float, lado: int, sz_min: float, sz_max: float):
        self.nome = nome
        self.casas = casas
        self.p_ini = preco
        self.alvo = preco
        self.ancora = ancora
        self.sigma = sigma
        self.lado = lado
        self.sz_min, self.sz_max = sz_min, sz_max
        self.k = 0
        self.z = 0.0
        self.wick = 0.0
        self.v15 = self.nova(t_ini, MS_15M)
        self.v1h = self.nova(t_ini, MS_1H)
        self.fechadas15: List[Dict[str, Any]] = []
        self.fechadas1h: List[Dict[str, Any]] = []
        self.negocios: List[Tuple[int, float, float]] = []

    @staticmethod
    def nova(t: int, ms: int) -> Dict[str, Any]:
        """Vela vazia [t, t + ms) com T = t + ms - 1 ms, como a Hyperliquid (fim inclusivo)."""
        return {"t": t, "T": t + ms - 1, "o": None, "h": None, "l": None, "c": None, "v": 0.0, "n": 0}

    def ancora_valor(self) -> float:
        return self.ancora.valor if self.ancora is not None else math.log(self.p_ini)


class BolsaComVelas(sim.Bolsa):
    """Bolsa falsa do medidor mais o canal candle; os negocios de cada activo seguem um alvo por vela.

    Em cada passo (10 ms reais) sai um negocio por activo ao preco do caminho da vela
    (linear em log-preco do fecho anterior ao alvo, com um pavio sinusoidal), e seguem
    as velas de 15 m e de 1 h acumuladas desses negocios. Na fronteira de uma vela
    envia-se a vela final e so depois a primeira actualizacao da seguinte. Os
    negocios grandes de BTC vao contra o desvio a ancora (absorcao pelos grandes).
    """

    def __init__(self, relogio: RelogioVirtual, armazem: Armazem, guiao_z: List[float]):
        super().__init__()
        self.relogio = relogio
        self.armazem = armazem
        self.guiao_z = guiao_z
        self.vivo: Dict[str, EstadoVivo] = {}
        self.rng = random.Random(11)
        self.cascata = False
        self.lado_exc = 1
        self.atrasar_proxima = False
        self.pausa_ate = 0.0
        self.T_atrasada = 0
        self.passo = 0
        self.erros_bolsa = 0

    def preparar(self, nome: str, casas: int, ancora: Optional[rv.Ancora], sigma: float, lado: int,
                 sz_min: float, sz_max: float) -> None:
        velas15 = self.armazem.velas[(nome, "15m")]
        preco = float(velas15[-1]["c"])
        self.moedas[nome].pb = preco
        self.armazem.preco[nome] = preco
        e = EstadoVivo(nome, casas, preco, T_FIM_HISTORICO, ancora, sigma, lado, sz_min, sz_max)
        self.vivo[nome] = e
        self._novo_alvo(e)

    def _novo_alvo(self, e: EstadoVivo) -> None:
        if e.ancora is not None:
            e.z = (self.guiao_z[e.k] if e.k < len(self.guiao_z) else 0.2) * e.lado
            e.alvo = math.exp(e.ancora_valor() + e.z * e.sigma)
            self.cascata = abs(e.z) >= 2.0
        else:
            e.alvo = e.p_ini * math.exp(self.rng.gauss(0.0, 0.0015))
        e.wick = self.rng.choice((-1.0, 1.0)) * 0.003

    def t_vela(self, k: int) -> Tuple[int, int]:
        """(t, T) da vela k ao vivo, com o T inclusivo da Hyperliquid."""
        return T_FIM_HISTORICO + k * MS_15M, T_FIM_HISTORICO + (k + 1) * MS_15M - 1

    def mensagem_vela(self, e: EstadoVivo, v: Dict[str, Any], intervalo: str) -> Dict[str, Any]:
        return {"t": v["t"], "T": v["T"], "s": e.nome, "i": intervalo, "o": fmt(v["o"], e.casas),
                "c": fmt(v["c"], e.casas), "h": fmt(v["h"], e.casas), "l": fmt(v["l"], e.casas),
                "v": fmt(v["v"], 4), "n": v["n"]}

    async def _fechar_velas(self, e: EstadoVivo, agora: int) -> None:
        v15 = e.v15
        if v15["o"] is None:                        # vela sem negocios: repete o fecho anterior
            v15["o"] = v15["h"] = v15["l"] = v15["c"] = e.p_ini
        final15 = self.mensagem_vela(e, v15, "15m")
        e.fechadas15.append(final15)
        self.armazem.velas[(e.nome, "15m")].append(final15)
        await self.difundir("candle", e.nome, "candle", final15)
        v1h = e.v1h
        if v15["T"] >= v1h["T"]:                    # a quarta vela de 15 m tem o mesmo T inclusivo da de 1 h
            if v1h["o"] is None:
                v1h["o"] = v1h["h"] = v1h["l"] = v1h["c"] = e.p_ini
            final1h = self.mensagem_vela(e, v1h, "1h")
            e.fechadas1h.append(final1h)
            self.armazem.velas[(e.nome, "1h")].append(final1h)
            await self.difundir("candle", e.nome, "candle", final1h)
            if e.ancora is not None:
                e.ancora.juntar(math.log(v1h["c"]))
            e.v1h = e.nova(v1h["t"] + MS_1H, MS_1H)
        e.p_ini = float(final15["c"])
        e.v15 = e.nova(v15["t"] + MS_15M, MS_15M)
        e.k += 1
        self._novo_alvo(e)
        if self.atrasar_proxima and e.nome == "BTC":
            self.atrasar_proxima = False
            self.T_atrasada = v15["T"]
            self.pausa_ate = time.monotonic() + 0.5

    def _negociar(self, e: EstadoVivo, agora: int) -> Dict[str, Any]:
        m = self.moedas[e.nome]
        frac = min(1.0, max(0.0, (agora - e.v15["t"]) / float(MS_15M)))
        lnp = math.log(e.p_ini) + frac * math.log(e.alvo / e.p_ini) + e.wick * math.sin(math.pi * frac)
        mid = math.exp(lnp)
        m.pb = round(math.floor(mid / m.tick) * m.tick, 8)
        m.spread = 1
        sz = round(self.rng.uniform(e.sz_min, e.sz_max), 4)
        grande = sz >= e.sz_min + 0.9 * (e.sz_max - e.sz_min)
        if grande and e.ancora is not None:
            compra = lnp < e.ancora_valor()           # absorcao: os grandes vao contra o desvio
        else:
            compra = self.rng.random() < 0.5
        px = m.pa if compra else m.pb
        self.tid += 1
        comprador = self.rng.choice(UTILIZADORES[:3])
        vendedor = self.rng.choice(UTILIZADORES[3:])
        for v in (e.v15, e.v1h):
            if v["o"] is None:
                v["o"] = v["h"] = v["l"] = px
            v["h"], v["l"], v["c"] = max(v["h"], px), min(v["l"], px), px
            v["v"] += sz
            v["n"] += 1
        e.negocios.append((agora, px, sz))
        m.neg_log.append((agora, px, sz))
        self.armazem.preco[e.nome] = px
        return {"coin": e.nome, "side": "B" if compra else "A", "px": repr(px), "sz": repr(sz), "time": agora,
                "hash": "0x1", "tid": self.tid, "users": [comprador, vendedor]}

    async def mercado(self) -> None:
        while True:
            await asyncio.sleep(0.01)
            if self.parado or time.monotonic() < self.pausa_ate:
                continue
            self.passo += 1
            agora = self.relogio.ms()
            try:
                for e in list(self.vivo.values()):
                    if agora > e.v15["T"]:
                        await self._fechar_velas(e, agora)
                    neg = self._negociar(e, agora)
                    await self.difundir("trades", e.nome, "trades", [neg])
                    await self.difundir("candle", e.nome, "candle", self.mensagem_vela(e, e.v15, "15m"))
                    await self.difundir("candle", e.nome, "candle", self.mensagem_vela(e, e.v1h, "1h"))
                    if self.passo % 25 == 0:
                        m = self.moedas[e.nome]
                        mid = (m.pb + m.pa) / 2.0
                        oi = "25000" if e.nome == "BTC" else "600000"
                        await self.difundir("activeAssetCtx", e.nome, "activeAssetCtx", {"coin": e.nome, "ctx": {
                            "funding": "0.0000125", "openInterest": oi, "prevDayPx": repr(round(e.p_ini, 2)),
                            "dayNtlVlm": "2500000000.0", "premium": "0.0001", "oraclePx": repr(round(mid, 6)),
                            "markPx": repr(round(mid * 1.0001, 6)), "midPx": repr(round(mid, 6))}})
            except Exception as ex:                   # a bolsa falsa nunca pode parar
                self.erros_bolsa += 1
                if self.erros_bolsa <= 3:
                    logging.getLogger("simulacao").exception("bolsa falsa: %s", ex)


# --------------------------------------------------------------------------
# CoinGlass falsa, medidor falso e vigia do estado
# --------------------------------------------------------------------------
def coinglass_falsa(bolsa: BolsaComVelas):
    """Websocket da CoinGlass: liquidacoes de BTC contra o lado do sinal, grandes durante a cascata e pequenas depois."""

    async def atender(ws: Any) -> None:
        async def empurrar() -> None:
            while True:
                await asyncio.sleep(0.3)
                vol = 250_000.0 if bolsa.cascata else 4_000.0
                side = 2 if bolsa.lado_exc > 0 else 1       # excursao para cima: shorts liquidados (compra forcada)
                await ws.send(json.dumps({"channel": "liquidation_orders", "data": [{
                    "base_asset": "BTC", "exchange": "Hyperliquid", "price": bolsa.armazem.preco.get("BTC", 0.0),
                    "side": side, "symbol": "BTCUSDT", "time": bolsa.relogio.ms(), "volume_usd": vol}]}))
        tarefa = asyncio.ensure_future(empurrar())
        try:
            async for bruto in ws:
                if bruto == "ping":
                    await ws.send("pong")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            tarefa.cancel()
    return atender


async def medidor_falso(cfg: md.Config, relogio: RelogioVirtual, armazem: Armazem, ligado: asyncio.Event) -> None:
    """Escreve dados/estado.json como o medidor (tmp + os.replace) enquanto `ligado` estiver activo."""
    while True:
        await asyncio.sleep(0.1)
        if not ligado.is_set():
            continue
        doc = {"hora_utc": md.iso_utc(relogio.ms()), "ligado": True, "versao": "2.5", "orcamento_bps": 4.5,
               "tamanho_ref_usd": 1000.0,
               "ativos": {n: {"estado": nu.VERDE, "motivos": [], "mid": armazem.preco.get(n), "qmax_usd_compra": 80000.0,
                              "qmax_usd_venda": 80000.0, "imp_ref_bps": 1.2, "liq_long_usd": 0.0, "liq_short_usd": 0.0}
                          for n in cfg.ativos}}
        tmp = cfg.estado_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, cfg.estado_json)


async def vigiar_estado(caminho: str, contagem: Dict[str, int]) -> None:
    """Le estado_sinalizador.json sem parar: cada leitura tem de ser JSON valido (nunca meio ficheiro)."""
    while True:
        await asyncio.sleep(0.02)
        if not os.path.exists(caminho):
            continue
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and "ativos" in doc and "hora_utc" in doc:
                contagem["ok"] += 1
            else:
                contagem["mau"] += 1
        except (OSError, ValueError):
            contagem["mau"] += 1


# --------------------------------------------------------------------------
# Conferencia
# --------------------------------------------------------------------------
FALHAS: List[str] = []
CONTA = [0]


def confere(cond: Any, texto: str) -> None:
    CONTA[0] += 1
    print(("  ok    " if cond else "  FALHA ") + texto, flush=True)
    if not cond:
        FALHAS.append(texto)


def ler(caminho: str) -> List[Dict[str, str]]:
    if not os.path.exists(caminho):
        return []
    with open(caminho, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def texto_do_ficheiro(caminho: str) -> str:
    try:
        with open(caminho, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def ficheiros_de(pasta: str) -> List[str]:
    out = []
    for raiz, _, nomes in os.walk(pasta):
        for n in nomes:
            out.append(os.path.join(raiz, n))
    return sorted(out)


async def esperar(cond: Any, limite_s: float, passo: float = 0.05) -> bool:
    """Espera (tempo real) ate cond() ser verdadeiro ou o limite passar."""
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if cond():
            return True
        await asyncio.sleep(passo)
    return bool(cond())


def linhas_sinais(cfg: md.Config) -> List[str]:
    txt = texto_do_ficheiro(cfg.sinais_csv)
    return [ln for ln in txt.split("\n")[1:] if ln.strip()]


# --------------------------------------------------------------------------
# Cenarios (todos sobre a mesma corrida, por ordem)
# --------------------------------------------------------------------------
async def cenario_historico(s: si.Sinalizador, bolsa: BolsaComVelas, sigma_btc: float) -> None:
    print("\n2. Historico e contexto")
    ok = await esperar(lambda: s.aquecido, 30.0)
    confere(ok, "o aquecimento com o historico terminou (%s)" % ("sim" if ok else "nao"))
    h1, h15 = s.hist[("BTC", "1h")], s.hist[("BTC", "15m")]
    # candleSnapshot devolve as velas com t >= startTime: a janela de 5000 x 15 m comeca um segundo
    # depois do t da vela mais antiga, que fica de fora, como na API verdadeira (4999 velas)
    confere(len(h1) >= 1400 and len(h15) >= 4990, "historico carregado: %d velas de 1 h e %d de 15 m de BTC" % (len(h1), len(h15)))
    ult15 = h15.ultima()
    confere(ult15 is not None and ult15.T - ult15.t == MS_15M - 1, "as velas tem o T inclusivo da Hyperliquid (T - t = %s ms)" % (ult15.T - ult15.t if ult15 else "?"))
    confere(ARMAZEM is not None and ARMAZEM.pedidos.get("candleSnapshot", 0) >= 4 and ARMAZEM.pedidos.get("fundingHistory", 0) >= 2,
            "candleSnapshot e fundingHistory pedidos ao info falso %s" % (ARMAZEM.pedidos if ARMAZEM else {}))
    at = s.ativos["BTC"]
    confere(at.ctx.estado == rv.VERDE, "BTC (OU com H = 8 h): contexto %s %s" % (at.ctx.estado, " ".join(at.ctx.motivos)))
    confere(at.aj.valido and 4.0 <= at.aj.meia_vida <= 14.0, "BTC: meia-vida estimada %.2f h (verdadeira 8 h)" % at.aj.meia_vida)
    confere(at.aj.t_nulo == at.aj.t_nulo and at.aj.t_nulo <= at.t_crit and -4.5 <= at.t_crit <= -2.2,
            "BTC: t_nulo %.2f <= t_crit %.2f (calibrado para N = %d)" % (at.aj.t_nulo, at.t_crit, at.aj.n))
    confere(at.sigma_ult == at.sigma_ult and abs(at.sigma_ult / sigma_btc - 1.0) < 1e-9,
            "BTC: sigma_eq do sinalizador %.6f igual ao replicado pela simulacao" % at.sigma_ult)
    confere(abs(at.a_ult - bolsa.vivo["BTC"].ancora_valor()) < 1e-9, "BTC: ancora igual a replicada (%.6f)" % at.a_ult)
    confere(at.ctx.vr == at.ctx.vr and at.ctx.vr < 1.0, "BTC: VR(8) = %.3f abaixo de 1" % at.ctx.vr)
    confere(at.vol.vol_razao == at.vol.vol_razao and 0.7 <= at.vol.vol_razao <= 1.3, "BTC: vol_razao_15 = %.2f" % at.vol.vol_razao)
    confere(len(at.funding_hist) >= 500, "BTC: historico de funding semeado (%d horas)" % len(at.funding_hist))
    eth = s.ativos["ETH"]
    confere(eth.ctx.estado != rv.VERDE and "nulo" in eth.ctx.motivos,
            "ETH (passeio aleatorio): o portao fecha, contexto %s (%s), t_nulo %.2f" % (eth.ctx.estado, " ".join(eth.ctx.motivos), eth.aj.t_nulo))
    dia = si.dia_utc(T_FIM_HISTORICO - MS_15M)
    confere(os.path.exists(os.path.join(s.cfg.pasta_velas, "BTC_15m_%s.csv" % dia)) and
            os.path.exists(os.path.join(s.cfg.pasta_velas, "BTC_1h_%s.csv" % dia)), "dados/velas/BTC_{15m,1h}_%s.csv escritos" % dia)
    fund = ler(os.path.join(s.cfg.pasta_funding, "BTC.csv"))
    confere(len(fund) >= 500 and list(fund[0].keys()) == ["time", "fundingRate", "premium"], "dados/funding/BTC.csv com %d horas" % len(fund))


async def cenario_fecho_normal(cfg: si.ConfigSinalizador, s: si.Sinalizador, bolsa: BolsaComVelas) -> Dict[str, Any]:
    print("\n3. Fecho normal: reentrada na vela k%d" % K_SINAL)
    at = s.ativos["BTC"]
    e = bolsa.vivo["BTC"]
    _, T_sinal = bolsa.t_vela(K_SINAL)
    ok = await esperar(lambda: at.t_15m_ult >= T_sinal, 60.0)
    confere(ok, "a vela k%d (T %s) foi fechada pelo sinalizador" % (K_SINAL, md.iso_utc(T_sinal)))
    confere(at.ultimo_motivo == "ok", "motivo da avaliacao em k%d: %s %s" % (K_SINAL, at.ultimo_motivo, at.ultimo_detalhe))
    linhas = linhas_sinais(cfg)
    confere(len(linhas) == 1, "sinais.csv tem exactamente uma linha de sinal (%d)" % len(linhas))
    txt = texto_do_ficheiro(cfg.sinais_csv)
    confere(txt.startswith(md.Medidor.CAB_SINAIS + "\n"), "cabecalho hora,ativo,lado,preco,alvo_bps,tamanho_usd,nota")
    confere(txt.endswith("\n") and "\r" not in txt, "cada linha termina em \\n")
    info: Dict[str, Any] = {}
    if linhas:
        partes = next(csv.reader([linhas[0]]))
        confere(len(partes) == 7, "linha com 7 campos: %s" % partes[:6])
        hora, ativo, lado, preco, alvo, tam, nota = (partes + [""] * 7)[:7]
        confere(hora.endswith("Z") and len(hora) == 24, "hora ISO com milissegundos e Z: %s" % hora)
        try:
            hora_ms = md.interpretar_hora(hora, 0)
        except ValueError:
            hora_ms = 0
        confere(0 <= hora_ms - T_sinal <= 10_000, "hora da decisao %d ms (virtuais) depois de T" % (hora_ms - T_sinal))
        confere(ativo == "BTC", "activo BTC")
        esperado = "venda" if e.lado > 0 else "compra"
        confere(lado == esperado, "lado %s (excursao para %s)" % (lado, "cima" if e.lado > 0 else "baixo"))
        vela = e.fechadas15[K_SINAL] if len(e.fechadas15) > K_SINAL else None
        confere(vela is not None and abs(float(preco) - float(vela["c"])) < 1e-6,
                "preco %s = fecho da vela de 15 m enviada pela bolsa (%s)" % (preco, vela["c"] if vela else "?"))
        confere(md.flt(alvo) == md.flt(alvo) and md.flt(alvo) > 0 and "." in alvo and len(alvo.split(".")[1]) == 1,
                "alvo_bps numerico com 1 casa: %s" % alvo)
        confere(md.flt(tam) == 1000.0 and "." not in tam, "tamanho_usd = 1000 (fase cal, 0 casas): %s" % tam)
        confere(nota.startswith("revou v=2 ") and "," not in nota and '"' not in nota, "nota comeca por revou: %.60s..." % nota)
        campos = rv.interpretar_nota(nota)
        z0 = md.flt(campos.get("z0"))
        confere(campos.get("fase") == "cal" and campos.get("ctx") == "VERDE", "nota: fase=%s ctx=%s" % (campos.get("fase"), campos.get("ctx")))
        confere(z0 == z0 and 1.3 <= abs(z0) <= 1.95 and (z0 > 0) == (e.lado > 0), "nota: z0=%s (guiao %.2f)" % (campos.get("z0"), GUIAO_Z[K_SINAL] * e.lado))
        confere(campos.get("med") == "on", "nota: med=%s (medidor falso a escrever estado.json)" % campos.get("med"))
        confere(md.flt(campos.get("pos")) == md.flt(campos.get("pos")), "nota: pos=%s (posicoes das baleias sondadas)" % campos.get("pos"))
        confere(campos.get("ses") == "asia" and campos.get("dow") == "1", "nota: ses=%s dow=%s hr=%s" % (campos.get("ses"), campos.get("dow"), campos.get("hr")))
        confere(md.flt(campos.get("G")) == md.flt(alvo) or abs(md.flt(campos.get("G")) - md.flt(alvo)) <= 0.051,
                "nota: G=%s coincide com alvo_bps" % campos.get("G"))
        info = {"hora": hora, "hora_ms": hora_ms, "lado": lado, "preco": preco, "nota": nota, "campos": campos}
    confere(at.trade is not None and at.trade_info.get("id"), "trade virtual aberto no activo: %s" % at.trade_info.get("id"))
    info["id"] = at.trade_info.get("id", "")
    confere(s.ativos["ETH"].ultimo_motivo in ("G1", "G0", "G2") and not any("ETH" in ln for ln in linhas),
            "ETH nunca escreve (motivo %s)" % s.ativos["ETH"].ultimo_motivo)
    return info


async def cenario_frescura(info: Dict[str, Any], bolsa: BolsaComVelas) -> None:
    print("\n4. Frescura")
    _, T_sinal = bolsa.t_vela(K_SINAL)
    atraso_v = info.get("hora_ms", 0) - T_sinal
    confere(0 <= atraso_v <= 10_000, "decisao %d ms virtuais (%.1f ms reais) depois do fim da vela" % (atraso_v, atraso_v / ACELERACAO))
    confere("latencia decisao-escrita" in texto_do_ficheiro(LOG_PATH[0]), "o log regista a latencia decisao-escrita")


async def cenario_coinglass_falsa(cfg: si.ConfigSinalizador, s: si.Sinalizador, info: Dict[str, Any]) -> None:
    print("\n5. CoinGlass falsa")
    campos = info.get("campos", {})
    confere(s.cg_estado == "ligado", "ligacao a CoinGlass falsa: %s" % s.cg_estado)
    liq = md.flt(campos.get("liq"))
    confere(liq == liq and 0.0 <= liq <= 0.5, "nota: liq=%s (cascata esgotada na reentrada)" % campos.get("liq"))
    confere(campos.get("liqsrc") == "cg", "nota: liqsrc=%s" % campos.get("liqsrc"))
    confere(s.cg_total >= 10, "%d liquidacoes recebidas" % s.cg_total)
    chave = cfg.cg_chave
    confere(chave == CHAVE_COINGLASS and chave not in repr(cfg) and chave not in (info.get("nota") or ""),
            "a chave nao esta no repr da configuracao nem na nota")


async def cenario_rearranque(cfg: si.ConfigSinalizador, s1: si.Sinalizador, tarefa1: "asyncio.Future[Any]",
                             bolsa: BolsaComVelas, info: Dict[str, Any]) -> Tuple[si.Sinalizador, "asyncio.Future[Any]"]:
    print("\n6. Rearranque com o trade virtual aberto")
    tarefa1.cancel()
    await asyncio.gather(tarefa1, return_exceptions=True)
    confere(not s1.erros and s1.erros_msg == 0, "primeira instancia sem erros nos passos %s" % (s1.erros or ""))
    with open(cfg.estado_sinalizador_json, encoding="utf-8") as f:
        est = json.load(f)
    tr = (est.get("ativos") or {}).get("BTC", {}).get("trade")
    confere(est.get("ligado") is False and isinstance(tr, dict) and tr.get("id") == info.get("id"),
            "estado final com ligado=false e o trade virtual %s" % (tr or {}).get("id"))
    s2 = si.Sinalizador(cfg, {"BTC": 5, "ETH": 4})
    at = s2.ativos["BTC"]
    confere(at.trade is not None and at.trade_info.get("id") == info.get("id") and at.n_sinal == 1,
            "segunda instancia repoe o trade virtual %s do estado (n_sinal=%d)" % (at.trade_info.get("id"), at.n_sinal))
    confere(len(s2.baleias_hash) == 5 and all(len(h) == 10 for h in s2.baleias_hash), "lista de baleias relida do disco: %d hashes" % len(s2.baleias_hash))
    tarefa2 = asyncio.ensure_future(s2.arrancar())
    ok = await esperar(lambda: s2.aquecido, 30.0)
    confere(ok and at.trade is not None, "segunda instancia aquecida com o trade ainda aberto")
    confere(len(linhas_sinais(cfg)) == 1, "o rearranque nao reemitiu o sinal (sinais.csv continua com 1 linha)")
    return s2, tarefa2


async def cenario_alvo(cfg: si.ConfigSinalizador, s: si.Sinalizador, bolsa: BolsaComVelas, info: Dict[str, Any]) -> None:
    print("\n7. Saida do trade virtual pelo alvo (k%d)" % K_ALVO)
    at = s.ativos["BTC"]
    _, T_alvo = bolsa.t_vela(K_ALVO)
    ok = await esperar(lambda: at.t_15m_ult >= T_alvo, 60.0)
    confere(ok, "a vela k%d foi fechada" % K_ALVO)
    reg = ler(cfg.reg_sinalizador)
    confere(len(reg) == 1, "registo_sinalizador.csv recebeu o trade virtual (%d linhas)" % len(reg))
    if reg:
        r = reg[0]
        confere(r.get("id") == info.get("id") and r.get("ativo") == "BTC" and r.get("lado") == info.get("lado"),
                "registo: id %s, %s %s" % (r.get("id"), r.get("ativo"), r.get("lado")))
        confere(r.get("motivo") == "alvo", "registo: motivo %s" % r.get("motivo"))
        confere(md.flt(r.get("r_bruto_bps")) > 0 and md.flt(r.get("r_liq_bps")) == md.flt(r.get("r_liq_bps")),
                "registo: r_bruto %s bps, funding %s, r_liq %s" % (r.get("r_bruto_bps"), r.get("funding_bps"), r.get("r_liq_bps")))
        confere(r.get("hora_saida", "").endswith("Z") and md.flt(r.get("preco_saida")) > 0, "registo: hora_saida %s preco_saida %s" % (r.get("hora_saida"), r.get("preco_saida")))
        confere(r.get("nota") == info.get("nota"), "registo: a nota e a mesma da linha de sinais.csv")
        confere(list(r.keys()) == si.COLUNAS_REGISTO, "registo: colunas da seccao 7.5")
    confere(at.trade is None and at.fase() == "cal", "trade fechado; fase %s" % at.fase())
    confere(len(linhas_sinais(cfg)) == 1, "a saida nao foi para sinais.csv")


async def cenario_medidor_parado(cfg: si.ConfigSinalizador, s: si.Sinalizador) -> None:
    print("\n8. Medidor parado")
    ok = await esperar(lambda: s.estado_medidor is None, 10.0)
    confere(ok, "com o estado.json velho o sinalizador passa a med=off")
    confere("med=off" in texto_do_ficheiro(LOG_PATH[0]), "o log avisa que os sinais nao vao ser medidos (med=off)")


async def cenario_fecho_com_atraso(s: si.Sinalizador, bolsa: BolsaComVelas) -> None:
    print("\n9. Fecho pelo relogio: a bolsa atrasa a vela seguinte a k%d" % K_ATRASO)
    at = s.ativos["BTC"]
    _, T_k = bolsa.t_vela(K_ATRASO)
    ok = await esperar(lambda: bolsa.vivo["BTC"].k >= K_ATRASO, 60.0)
    bolsa.atrasar_proxima = True
    ok = ok and await esperar(lambda: bolsa.T_atrasada == T_k, 60.0)
    fechada_antes = await esperar(lambda: at.t_15m_ult >= T_k, 0.45, 0.01)
    ainda_em_pausa = time.monotonic() < bolsa.pausa_ate
    confere(ok and fechada_antes and ainda_em_pausa,
            "k%d fechada pelo relogio corrigido (agora - atraso >= T + folga) antes de chegar a vela seguinte" % K_ATRASO)
    await esperar(lambda: time.monotonic() >= bolsa.pausa_ate, 2.0)


async def cenario_corte_na_vela(cfg: si.ConfigSinalizador, s: si.Sinalizador, bolsa: BolsaComVelas) -> None:
    print("\n10. Corte de ligacao na vela da reentrada (k%d)" % K_CORTE)
    at = s.ativos["BTC"]
    t_k, T_k = bolsa.t_vela(K_CORTE)
    ok = await esperar(lambda: bolsa.relogio.ms() >= t_k + 300_000, 60.0)
    religacoes_antes = s.religacoes
    await bolsa.cortar_ligacoes()
    ok2 = await esperar(lambda: s.religacoes > religacoes_antes, 10.0)
    confere(ok and ok2, "a bolsa cortou a ligacao a meio de k%d; o sinalizador registou o corte" % K_CORTE)
    ok3 = await esperar(lambda: s.ligado, 10.0)
    confere(ok3, "religou")
    ok4 = await esperar(lambda: at.t_15m_ult >= T_k, 20.0)
    confere(ok4, "a vela k%d foi fechada depois de religar" % K_CORTE)
    confere(at.ultimo_motivo == "G0" and "corte" in at.ultimo_detalhe,
            "sem sinal nessa vela: motivo %s (%s)" % (at.ultimo_motivo, at.ultimo_detalhe))
    confere(len(linhas_sinais(cfg)) == 1 and at.n_sinal == 1, "sinais.csv continua com 1 linha")
    e = bolsa.vivo["BTC"]
    z_prev = e.fechadas15[K_CORTE - 1] if len(e.fechadas15) > K_CORTE else None
    confere(z_prev is not None and abs(md.flt(at.exc.z_ant)) >= 2.0 and abs(md.flt(at.exc.z)) < 2.0,
            "era uma reentrada (z anterior %.2f, z %.2f): so o corte a travou" % (md.flt(at.exc.z_ant), md.flt(at.exc.z)))
    dia = si.dia_utc(t_k)
    linhas = ler(os.path.join(cfg.pasta_velas, "BTC_15m_%s.csv" % dia))
    row = next((ln for ln in linhas if ln.get("t") == str(t_k)), None)
    confere(row is not None and row.get("corte") == "1", "a vela k%d ficou marcada com corte=1 em dados/velas" % K_CORTE)


async def conferencias_finais(cfg: si.ConfigSinalizador, s: si.Sinalizador, bolsa: BolsaComVelas,
                              contagem_estado: Dict[str, int]) -> None:
    print("\n11. Ficheiros, coerencia das velas, privacidade")
    confere(not s.erros and s.erros_msg == 0, "segunda instancia sem erros nos passos %s" % (s.erros or ""))
    confere(bolsa.erros_bolsa == 0, "bolsa falsa sem erros")
    at = s.ativos["BTC"]
    atraso = at.atraso_mediano()
    confere(atraso == atraso and atraso < 3000, "atraso mediano local menos bolsa %.0f ms virtuais (%.1f ms reais)" % (atraso, atraso / ACELERACAO))
    confere(contagem_estado["ok"] >= 50 and contagem_estado["mau"] == 0,
            "estado_sinalizador.json: %d leituras validas, %d invalidas (escrita atomica)" % (contagem_estado["ok"], contagem_estado["mau"]))
    confere(not os.path.exists(cfg.estado_sinalizador_json + ".tmp"), "sem ficheiro temporario do estado")
    with open(cfg.estado_sinalizador_json, encoding="utf-8") as f:
        est = json.load(f)
    b = est["ativos"]["BTC"]
    confere(est.get("versao") == si.VERSAO and est.get("variante") == cfg.variante and b.get("contexto") == rv.VERDE
            and b.get("n_fechados") == 1 and b.get("ultimo_motivo") == "G0",
            "estado: versao %s variante %s contexto %s n_fechados %s ultimo_motivo %s" % (
                est.get("versao"), est.get("variante"), b.get("contexto"), b.get("n_fechados"), b.get("ultimo_motivo")))
    # velas ao vivo em disco iguais as da bolsa, 1 h = agregacao das 4 de 15 m, colunas enriquecidas
    e = bolsa.vivo["BTC"]
    dia = si.dia_utc(T_FIM_HISTORICO)
    rows15 = {ln["t"]: ln for ln in ler(os.path.join(cfg.pasta_velas, "BTC_15m_%s.csv" % dia))}
    rows1h = {ln["t"]: ln for ln in ler(os.path.join(cfg.pasta_velas, "BTC_1h_%s.csv" % dia))}
    iguais = 0
    faltam = 0
    for v in e.fechadas15[1:K_CORTE + 1]:
        ln = rows15.get(str(v["t"]))
        if ln is None:
            faltam += 1
            continue
        if (ln["T"] == str(v["T"]) and all(abs(md.flt(ln[c]) - float(v[c])) < 1e-6 for c in ("o", "h", "l", "c"))
                and abs(md.flt(ln["v"]) - float(v["v"])) < 1e-3 and ln["n"] == str(v["n"])):
            iguais += 1
    confere(iguais >= K_CORTE - 1 and faltam <= 1, "velas de 15 m ao vivo em disco iguais as enviadas pela bolsa (%d iguais, %d em falta)" % (iguais, faltam))
    v1 = e.fechadas1h[1] if len(e.fechadas1h) > 1 else None
    if v1:
        agreg = agregar_velas([x for x in e.fechadas15 if v1["t"] <= x["t"] < v1["T"]], "1h", 2)
        ln = rows1h.get(str(v1["t"]))
        confere(ln is not None and all(abs(md.flt(ln[c]) - float(agreg[c])) < 1e-6 for c in ("o", "h", "l", "c"))
                and ln["n"] == str(agreg["n"]) and abs(md.flt(ln["v"]) - float(agreg["v"])) < 1e-3,
                "vela de 1 h ao vivo = agregacao das 4 velas de 15 m (t %s)" % md.iso_utc(v1["t"]))
    t_sig, _ = bolsa.t_vela(K_SINAL)
    ln = rows15.get(str(t_sig), {})
    confere(all(ln.get(c, "") != "" for c in ("v_b", "v_a", "oi_usd", "funding", "liq_long", "liq_short", "atraso_ms", "corte"))
            and ln.get("corte") == "0" and ln.get("pos", "") != "",
            "colunas enriquecidas preenchidas na vela do sinal (v_b %s oi_usd %s liq_short %s pos %s)" % (
                ln.get("v_b"), ln.get("oi_usd"), ln.get("liq_short"), ln.get("pos")))
    # a vela da bolsa e coerente com os seus proprios negocios
    v = e.fechadas15[K_SINAL]
    negs = [n for n in e.negocios if v["t"] <= n[0] < v["T"]]
    confere(negs and abs(float(v["c"]) - negs[-1][1]) < 1e-9 and float(v["h"]) == max(n[1] for n in negs)
            and float(v["l"]) == min(n[1] for n in negs) and int(v["n"]) == len(negs),
            "a vela da bolsa vem dos negocios que enviou (%d negocios, fecho = ultimo preco)" % len(negs))
    # a chave da CoinGlass e os enderecos inteiros nunca vao para os ficheiros de dados nem para o log
    ficheiros = ficheiros_de(cfg.pasta)
    com_chave = [f for f in ficheiros if CHAVE_COINGLASS in texto_do_ficheiro(f)]
    confere(not com_chave and len(ficheiros) > 200, "a chave falsa da CoinGlass nao esta em nenhum dos %d ficheiros de dados nem no log %s" % (len(ficheiros), com_chave))
    inteiros = [f for f in ficheiros for e_ in BALEIAS + UTILIZADORES + [BALEIA_POBRE]
                if e_ in texto_do_ficheiro(f).lower()]
    confere(not inteiros, "nenhum endereco inteiro (baleias ou negociantes) em ficheiros de dados nem no log %s" % inteiros)
    bal = ler(cfg.baleias_csv)
    confere(len(bal) == 5 and list(bal[0].keys()) == si.COLUNAS_BALEIAS and all(len(x["hash"]) == 10 and len(x["prefixo"]) == 10 for x in bal)
            and all(x["hash"] in {rv.hash_endereco(b_) for b_ in BALEIAS} for x in bal),
            "baleias.csv: 5 baleias (pobre e HLP excluidas), so prefixo e hash")
    confere(not os.path.exists(cfg.reg_sinais) and not os.path.exists(cfg.reg_fills) and not os.path.isdir(cfg.pasta_metricas),
            "o sinalizador nao criou ficheiros do medidor (registo_sinais, registo_fills, metricas)")
    log_txt = texto_do_ficheiro(LOG_PATH[0])
    confere("Sinal escrito: " in log_txt and "trade virtual" in log_txt and "reposto do estado" in log_txt,
            "o log tem o sinal, o trade virtual e a reposicao no rearranque")


LOG_PATH = [""]


async def principal() -> bool:
    global ARMAZEM
    pasta = tempfile.mkdtemp(prefix="sinalizador_sim_")
    relogio = RelogioVirtual(T_FIM_HISTORICO + 1000, ACELERACAO)
    si.agora_ms = relogio.ms          # tempo acelerado: so em memoria, o ficheiro nao muda
    sim.ms = relogio.ms
    # o orcamento de peso (400 por minuto REAL) nao faz sentido com o tempo 240x acelerado: uma espera de
    # 60 s reais seriam 4 h virtuais; a simulacao levanta-o so em memoria (o orcamento tem teste proprio)
    si.PESO_MINUTO_MAX = 10 ** 9
    armazem = Armazem(relogio)
    ARMAZEM = armazem
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

    print("1. Historico sintetico, servicos falsos e configuracao")
    t0 = time.monotonic()
    btc15, btc1h = gerar_historico("BTC", 121_000.0, 2, DIAS_HISTORICO, 101, True)
    eth15, eth1h = gerar_historico("ETH", 4_300.0, 3, DIAS_HISTORICO, 202, False)
    armazem.velas = {("BTC", "15m"): btc15, ("BTC", "1h"): btc1h, ("ETH", "15m"): eth15, ("ETH", "1h"): eth1h}
    anc_btc, sigma_btc, z_btc = ancora_e_sigma(btc1h)
    confere(0.006 <= sigma_btc <= 0.014, "OU sintetico: sigma_eq replicado %.5f (alvo %.3f), z final %.2f em %.1f s" % (
        sigma_btc, SIGMA_EQ_OU, z_btc, time.monotonic() - t0))
    lado = 1 if z_btc >= 0 else -1

    info = ThreadingHTTPServer(("127.0.0.1", 0), InfoFalso)
    info.daemon_threads = True
    threading.Thread(target=info.serve_forever, daemon=True).start()
    lb = ThreadingHTTPServer(("127.0.0.1", 0), LeaderboardFalso)
    lb.daemon_threads = True
    threading.Thread(target=lb.serve_forever, daemon=True).start()
    bolsa = BolsaComVelas(relogio, armazem, GUIAO_Z)
    bolsa.lado_exc = lado
    bolsa.preparar("BTC", 2, anc_btc, sigma_btc, lado, 0.05, 1.5)
    bolsa.preparar("ETH", 3, None, 0.0, 1, 0.5, 20.0)
    srv = await websockets.serve(bolsa.atender, "127.0.0.1", 0)
    cg = await websockets.serve(coinglass_falsa(bolsa), "127.0.0.1", 0)
    porta = srv.sockets[0].getsockname()[1]
    porta_cg = cg.sockets[0].getsockname()[1]
    ini = os.path.join(pasta, "config.ini")
    with open(ini, "w", encoding="utf-8") as f:
        f.write("""
[geral]
ativos = BTC, ETH
endereco =
pasta_dados = dados
[orcamento]
alvo_bps = 30
tamanho_usd = 1000
[sombra]
horizontes_s = 5, 30, 60, 300, 900, 3600, 14400
horizonte_regra_s = 3600
[coinglass]
chave = %s
rest = nao
[avancado]
sem_dados_s = 120
vigia_s = 300
url_ws = ws://127.0.0.1:%d
url_info = http://127.0.0.1:%d
url_coinglass = ws://127.0.0.1:%d/?k=
[sinalizador]
emitir = sim
replicas_nula = 200
baleias_max = 5
sonda_s = 60
url_leaderboard = http://127.0.0.1:%d/leaderboard
""" % (CHAVE_COINGLASS, porta, info.server_address[1], porta_cg, lb.server_address[1]))
    cfg = si.ConfigSinalizador(ini)
    os.makedirs(cfg.pasta, exist_ok=True)
    sz = md.obter_sz_decimals(cfg)
    confere(sz == {"BTC": 5, "ETH": 4}, "meta do info falso: szDecimals %s" % sz)
    LOG_PATH[0] = cfg.log_sinalizador
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    fh = logging.FileHandler(cfg.log_sinalizador, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%H:%M:%S"))
    raiz.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("      %(levelname)s %(message)s"))
    filtro = FiltroConsola()
    ch.addFilter(filtro)
    raiz.addHandler(ch)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    confere(cfg.cg_chave == CHAVE_COINGLASS and CHAVE_COINGLASS not in repr(cfg), "config.ini temporario lido (variante %s)" % cfg.variante)

    mercado = asyncio.ensure_future(bolsa.mercado())
    medidor_ligado = asyncio.Event()
    medidor_ligado.set()
    med_falso = asyncio.ensure_future(medidor_falso(cfg, relogio, armazem, medidor_ligado))
    contagem_estado = {"ok": 0, "mau": 0}
    vigia = asyncio.ensure_future(vigiar_estado(cfg.estado_sinalizador_json, contagem_estado))
    inicio_real = time.monotonic()

    s1 = si.Sinalizador(cfg, sz)
    tarefa1 = asyncio.ensure_future(s1.arrancar())
    await cenario_historico(s1, bolsa, sigma_btc)
    info_sinal = await cenario_fecho_normal(cfg, s1, bolsa)
    await cenario_frescura(info_sinal, bolsa)
    await cenario_coinglass_falsa(cfg, s1, info_sinal)
    s2, tarefa2 = await cenario_rearranque(cfg, s1, tarefa1, bolsa, info_sinal)
    medidor_ligado.clear()
    await cenario_alvo(cfg, s2, bolsa, info_sinal)
    await cenario_medidor_parado(cfg, s2)
    await cenario_fecho_com_atraso(s2, bolsa)
    await cenario_corte_na_vela(cfg, s2, bolsa)
    tarefa2.cancel()
    await asyncio.gather(tarefa2, return_exceptions=True)
    await asyncio.sleep(0.1)
    vigia.cancel()
    await conferencias_finais(cfg, s2, bolsa, contagem_estado)

    print("\n12. Relatorio do sinalizador\n")
    si.cmd_relatorio(cfg)
    confere(os.path.exists(os.path.join(cfg.pasta, "relatorio_sinalizador.txt")), "relatorio escrito")
    duracao = time.monotonic() - inicio_real
    virtual_h = (relogio.ms() - T_FIM_HISTORICO) / 3_600_000.0
    print("\nCorrida: %.1f s reais = %.2f h virtuais (%dx); %d avisos med=off no log" % (duracao, virtual_h, ACELERACAO, filtro.med_off))

    for tk in (mercado, med_falso):
        tk.cancel()
    await asyncio.gather(mercado, med_falso, vigia, return_exceptions=True)
    srv.close()
    cg.close()
    info.shutdown()
    lb.shutdown()
    raiz.removeHandler(fh)
    fh.close()
    shutil.rmtree(pasta, ignore_errors=True)
    print("\n" + ("SIMULACAO PASSOU: %d conferencias" % CONTA[0] if not FALHAS
                  else "SIMULACAO FALHOU em %d de %d ponto(s):\n  - %s" % (len(FALHAS), CONTA[0], "\n  - ".join(FALHAS))))
    return not FALHAS


def main() -> int:
    return 0 if asyncio.run(principal()) else 1


if __name__ == "__main__":
    sys.exit(main())
