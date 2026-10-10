# -*- coding: utf-8 -*-
"""Família CASCATA DE LIQUIDAÇÕES E EXAUSTÃO.

Hipótese: uma rajada de liquidações de longs no fundo (ou de shorts no topo), com queda de OI e volume
climático, marca o fim da cascata; o fluxo forçado acaba e o preço reverte nas horas seguintes.

Ordem obrigatória: (1) validar se as colunas de liquidações trazem informação própria além de |retorno|,
volume e taker da mesma vela; (2) estudo de eventos no IS, por decil de intensidade, com erros padrão
agrupados por hora (os activos caem juntos); (3) grelha PEQUENA e declarada, toda registada em
ensaios/cascata.csv; (4) escolha no IS pelo centro de um patamar; (5) OOS UMA vez; (6) robustez em
2020-2022 se a regra for só de preço e volume; (7) relatório em relatorios/familia_cascata.md.

Definições
----------
Vela de cascata (1 h, UTC, só velas completas e não imputadas quando a fonte é liquidações):
  fonte "liq":   |long_liq_usd| acima do percentil móvel P (90 dias, causal) numa vela de queda
                 (compra); short_liq_usd acima do percentil P numa vela de subida (venda).
                 Só BTC, ETH, BNB (SOL, XRP, DOGE têm séries modeladas; ver validar_colunas.py).
  fonte "subst": substitutos observáveis: |retorno de 1 h| acima do percentil P (365 dias, causal) E
                 volume_quote acima do percentil P (90 dias) E taker do lado do movimento
                 (desequilíbrio taker < 0 em queda, > 0 em subida). Seis activos.
Regra de entrada: depois do fecho da vela de cascata, a primeira vela de 15 m (nas W seguintes) que fecha
acima do nível de reconquista (compra; simétrico na venda). Nível: "minima" = mínima da vela de cascata;
"fecho" = fecho da vela de cascata. Stop = mínima da cascata menos 0,25 ATR(14) de 15 m. Alvo em R.
Saída por tempo ao fecho da 96.ª vela de 15 m (24 h). Custo 6,5 bps por lado.

Correr:  python familia_cascata.py            (IS, grelha, escolha, OOS uma vez, relatório)
"""
from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

FAMILIA = "cascata"
RESULTADOS = os.path.join(H.AQUI, "resultados")
RELATORIO = os.path.join(H.AQUI, "relatorios", "familia_cascata.md")
ACTIVOS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
ACTIVOS_LIQ = ["BTC", "ETH", "BNB"]          # únicos com liquidações usáveis (com reservas)
HORIZONTES_H = [1, 4, 12, 24]
JANELA_PCT_LIQ = 24 * 90                      # 90 dias em velas de 1 h
JANELA_PCT_RET = 24 * 365
JANELA_PCT_VOL = 24 * 90
MIN_PCT = 24 * 30

# Grelha declarada ANTES de correr, depois do estudo de eventos (36 configurações + 2 regras literais)
# O estudo de eventos e o diagnóstico de stop (a mínima da cascata é revisitada nas 24 h seguintes em 82 a
# 84 % dos casos) ditaram a grelha: a fonte é a substituta (as liquidações acrescentam pouco além de |ret| e
# volume e só existem em 3 activos), o stop mede-se em ATR(14) de 1 h abaixo da mínima da cascata, e o alvo
# inclui 6 R (na prática saída por tempo às 24 h).
GRELHA_FONTE = "subst"
GRELHA_LADOS = "compra"       # o estudo de eventos no IS nega o lado da venda (continuação, não reversão)
GRELHA_P = [0.90, 0.95, 0.98]
GRELHA_RECONQUISTA = ["minima", "fecho"]
GRELHA_STOP = [("atr1h", 1.0), ("atr1h", 2.0)]
GRELHA_ALVO = [2.0, 3.0, 6.0]
REGRAS_LITERAIS = [  # (fonte, P, reconquista, stop, alvo, lados): a regra tal como a tarefa a descreve, dos dois lados
    ("subst", 0.95, "minima", ("atr15", 0.25), 2.0, "ambos"),
    ("liq", 0.95, "minima", ("atr15", 0.25), 2.0, "ambos"),
    ("subst", 0.95, "minima", ("atr15", 0.25), 2.0, "compra"),
]
FIXOS = dict(W=8, n_max=96, atr_n=14)

HONESTIDADE = """
**Veredicto: a família NÃO passa.** O estudo de eventos no IS mostra um efeito real, acima de 2 erros padrão,
mas só do lado da compra e só no mercado em alta de 2023 a 2025-03; a regra converte pouco dessa deriva em R e
no OOS (avaliado uma vez) perde dinheiro: n 533, média -0,112 R (EP 0,043), PF 0,76, 1 de 6 activos positivo,
DD 92 R, as duas metades negativas. Nos 18 activos o OOS dá -0,109 R (EP 0,025), PF 0,76, 2 de 18 positivos.
Falha H2, H3, H4, H5 e H6; só passa H1.

O que pode estar errado ou é frágil:

1. **O lado da venda nega a hipótese no IS.** Depois de uma cascata de shorts (vela de 1 h extrema em subida,
   taker comprador) o preço CONTINUOU a subir: -48 a -92 bps a 24 h para quem vendesse (t -3 a -4,6). A
   hipótese de exaustão só vale para longs liquidados, o que é indistinguível de "comprar quedas num mercado
   em alta". A nula só de compras no IS dá -0,034 R com os mesmos stop e alvo (a deriva incondicional é +17 bps
   por dia); a regra dá +0,045 R: a vantagem sobre a deriva é 0,08 R, 2,6 EP, e desaparece no OOS.
2. **A mínima da cascata é revisitada em 82 % (BTC) a 84 % (SOL) dos casos nas 24 h seguintes**; a mínima menos
   1 ATR de 1 h em 55 %. A regra literal da tarefa (stop 0,25 ATR de 15 m abaixo da mínima, alvo 2 R, dois
   lados) dá no IS -0,167 R (n 2321, PF 0,77, 62 % stops) com a fonte substituta e -0,242 R (n 3351, PF 0,69)
   com liquidações; só compra -0,136 R. Os stops largos (2 ATR de 1 h, R de 4,3 % do preço) ainda são tocados
   em 31 % dos trades e deixam a média em +0,045 R no IS. Com R de 4,3 % o custo é 0,03 R: o problema não é o
   custo, é o sinal.
3. **O IS do próprio patamar já falhava H2, H3 e H5** (melhor bloco +0,048 R, PF 1,13, DD 36 R). Nenhuma das
   36 células passou de +0,076 R. Foi para o OOS por obrigação de método, não por expectativa.
4. **Regime, não vantagem.** Por ano, a regra só de compras dá +0,091 (2020), +0,118 (2021), -0,107 (2022),
   +0,076 (2023), +0,046 (2024), -0,092 (2025 Q1), -0,122 (2025 Q2 em diante), -0,100 (2026): segue o sinal
   do mercado. É beta ao regime com stop, não exaustão de fluxo forçado.
5. **Liquidações.** Nos 6 activos, a 1 h, o R2 em logs de |long_liq| sobre |ret|, volume e taker sell da mesma
   vela é 0,79 (BTC), 0,74 (ETH), 0,72 (BNB), 0,40 (SOL), 0,59 (XRP), 0,16 (DOGE); SOL, XRP e DOGE não
   distinguem direcção (assimetria queda/subida 1,1 a 1,5; p1/p50 0,53 a 0,89). A informação incremental é
   fraca: nas quedas extremas de BTC/ETH/BNB, resíduo > 0 dá +49 bps a 24 h (n 1266, EP 12) contra +1 bps
   (n 152, EP 30) com resíduo <= 0; a diferença de 48 bps tem EP 32 (1,5 EP). Decil 10 de liquidações sem
   preço/volume extremos: +26 bps (t 3,5), contra +79 do substituto. A fonte `liq` não foi para a grelha; a
   regra literal com `liq` fica registada (-0,242 R). A queda de OI não acrescenta nada (+81 contra +93 bps);
   o filtro taker exclui só 91 eventos em 2712 e é praticamente neutro.
6. **Fuga de informação a declarar.** (a) Antes de a grelha correr, um comando de diagnóstico imprimiu o estudo
   de eventos por lado também no OOS (lado da compra +1 a +13 bps a 24 h, EP 17 a 28). A grelha, o critério de
   escolha (centro do melhor bloco de 9) e o lado só de compra já estavam fixados no código antes dessa
   impressão e não mudaram depois; mesmo assim, o leitor deve saber. (b) Duas configurações foram corridas em
   ensaio seco antes da grelha definitiva (substituto P 0,95 mínima 2 R e liq P 0,95 fecho 2 R, ambas com stop
   0,25 ATR de 15 m): deram -0,167 e -0,198 R no IS; a primeira coincide com a regra literal registada, a
   segunda não está no CSV. (c) O diagnóstico de revisita da mínima foi feito no IS e ditou a escala do stop na
   grelha: é uso legítimo do IS, mas é uma escolha condicionada aos dados.
7. **Erros padrão.** Com espaçamento de 4 h entre eventos do mesmo activo, o t a 24 h do lado da compra é 5,2 a
   5,6; com 24 h (eventos não sobrepostos) cai para 3,4 a 3,9 e a média de 79 para 61 bps (P 0,90) e de 106
   para 76 (P 0,95). O EP agrupado por hora corrige a correlação entre activos, não a sobreposição temporal;
   os números com 24 h são os honestos.
8. **O que não se testou:** cascatas definidas a 4 h; saída a 48 ou 96 h (a deriva continuava até 96 h no IS,
   mas desapareceu no OOS segundo as taxas de base); filtro de regime (teria de ser uma família própria e,
   dado o rácio de variâncias de 2025-26 perto de 1, provavelmente ficaria fechado); funding como
   condicionante; posição no intervalo de dias ou zona estrutural; janela W diferente de 8 velas; o buffer do
   stop em ATR de 15 m com múltiplos maiores. Nenhuma destas variantes foi corrida, portanto M não as inclui.
9. **Binance não é a Hyperliquid:** preço e volume agregados são próximos; o livro, os takers e as liquidações
   não. Mesmo que uma variante passasse, teria de ser validada ao vivo.

Resumo em números: eventos IS lado compra P 0,95 espaçados 24 h, +76 bps a 24 h (EP 22, n 917); regra escolhida
IS +0,045 R (EP 0,030, n 1089, PF 1,12); OOS -0,112 R (EP 0,043, n 533, PF 0,76); 2020-22 +0,030 R (EP 0,037,
n 877); 18 activos OOS -0,109 R (EP 0,025, n 1503). M = 43 linhas em ensaios/cascata.csv: 39 configurações
no IS (3 regras literais + 36 da grelha), 1 linha de robustez nos 18 activos no IS, 1 em 2020-2022 e 2 no OOS
(6 e 18 activos, uma passagem).
"""

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 300)


# ---------------------------------------------------------------------------------------------
# Características na grelha de 1 h
# ---------------------------------------------------------------------------------------------

def caracteristicas_1h(df15: pd.DataFrame) -> pd.DataFrame:
    """Vela de 1 h com retorno, percentis móveis causais de |ret|, volume e liquidações, taker e OI."""
    h1 = H.agregar(df15, "1h")
    h1["ret"] = H.retorno_log(h1["close"])
    h1["abs_ret"] = h1["ret"].abs()
    h1["pct_ret"] = H.percentil_movel(h1["abs_ret"], JANELA_PCT_RET, MIN_PCT)
    h1["pct_vol"] = H.percentil_movel(h1["volume_quote"], JANELA_PCT_VOL, MIN_PCT)
    h1["taker"] = H.desequilibrio_taker(h1, 1)
    liq_l = H.mascarar_imputado(h1, "long_liq_usd").abs()
    liq_s = H.mascarar_imputado(h1, "short_liq_usd")
    h1["liq_long"] = liq_l
    h1["liq_short"] = liq_s
    h1["pct_liq_long"] = H.percentil_movel(liq_l, JANELA_PCT_LIQ, MIN_PCT)
    h1["pct_liq_short"] = H.percentil_movel(liq_s, JANELA_PCT_LIQ, MIN_PCT)
    h1["oi_chg"] = H.mascarar_imputado(h1, "oi_change_pct")
    h1["d_oi_usd"] = H.mascarar_imputado(h1, "open_interest_usd").pct_change()
    # intensidade substituta: o menor dos dois percentis (ambos têm de ser altos)
    h1["pct_subst"] = np.minimum(h1["pct_ret"], h1["pct_vol"])
    return h1


def eventos_cascata(h1: pd.DataFrame, fonte: str, p: float, lado: int, exigir_oi_neg: bool = False) -> pd.Series:
    """Series booleana das velas de 1 h que são cascata do lado dado (+1 longs liquidados em queda, -1 shorts em subida)."""
    if lado > 0:
        direccao = h1["ret"] < 0
        pct = h1["pct_liq_long"] if fonte == "liq" else h1["pct_subst"]
        taker_ok = h1["taker"] < 0
    else:
        direccao = h1["ret"] > 0
        pct = h1["pct_liq_short"] if fonte == "liq" else h1["pct_subst"]
        taker_ok = h1["taker"] > 0
    ev = direccao & (pct >= p)
    if fonte == "subst":
        ev &= taker_ok
    if exigir_oi_neg:
        ev &= h1["oi_chg"] < 0
    return ev.fillna(False)


# ---------------------------------------------------------------------------------------------
# Validação: as liquidações trazem informação além de |ret|, volume e taker?
# ---------------------------------------------------------------------------------------------

def _ols_r2(y: np.ndarray, X: np.ndarray) -> Tuple[float, np.ndarray]:
    X1 = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    res = y - X1 @ beta
    r2 = 1.0 - res.var() / y.var()
    return float(r2), res


def validar_liquidacoes(h1s: Dict[str, pd.DataFrame], periodo: str = "IS") -> pd.DataFrame:
    """Regressão em logs de |long_liq| sobre |ret|, volume e taker sell (1 h, IS); guarda o resíduo."""
    linhas = []
    for a, h1 in h1s.items():
        x = H.recortar(h1, periodo)
        x = x[~x["imputado"]]
        ok = (x["liq_long"] > 0) & (x["abs_ret"] > 0) & (x["volume_quote"] > 0)
        x = x[ok]
        y = np.log(x["liq_long"].to_numpy())
        X = np.column_stack([np.log(x["abs_ret"].to_numpy()), np.log(x["volume_quote"].to_numpy()),
                             np.log1p(x["taker_sell_vol_btc"].to_numpy() * x["close"].to_numpy())])
        r2, res = _ols_r2(y, X)
        ys = np.log(x["liq_short"].to_numpy())
        Xs = np.column_stack([np.log(x["abs_ret"].to_numpy()), np.log(x["volume_quote"].to_numpy()),
                              np.log1p(x["taker_buy_vol_btc"].to_numpy() * x["close"].to_numpy())])
        r2s, ress = _ols_r2(ys, Xs)
        h1.loc[x.index, "res_liq_long"] = res
        h1.loc[x.index, "res_liq_short"] = ress
        # razão queda/subida (num mercado real longs liquidam-se em quedas)
        q = x["liq_long"][x["ret"] < 0].mean() / x["liq_long"][x["ret"] > 0].mean()
        qs = x["liq_short"][x["ret"] < 0].mean() / x["liq_short"][x["ret"] > 0].mean()
        linhas.append({"activo": a, "n_1h": len(x), "R2_log_long": r2, "R2_log_short": r2s,
                       "assim_long_queda/subida": q, "assim_short_queda/subida": qs,
                       "p1/p50_long": x["liq_long"].quantile(0.01) / x["liq_long"].median()})
    return pd.DataFrame(linhas).set_index("activo")


# ---------------------------------------------------------------------------------------------
# Estudo de eventos agrupado (erro padrão agrupado por hora do evento)
# ---------------------------------------------------------------------------------------------

def retornos_apos(h1: pd.DataFrame, ev: pd.Series, lado: int, horizontes=HORIZONTES_H,
                  espacamento: int = 4) -> pd.DataFrame:
    """Retorno (bps, log) a favor do lado, da abertura da vela seguinte ao fecho de k+h, por evento."""
    pos = np.flatnonzero(ev.reindex(h1.index).fillna(False).to_numpy())
    if espacamento > 0 and len(pos):
        ret = [pos[0]]
        for p in pos[1:]:
            if p - ret[-1] >= espacamento:
                ret.append(p)
        pos = np.asarray(ret)
    c = np.log(h1["close"].to_numpy(dtype=float))
    o = np.log(h1["open"].to_numpy(dtype=float))
    n = len(h1)
    pos = pos[pos + max(horizontes) < n]
    out = pd.DataFrame({"hora": h1.index[pos], "activo": h1["activo"].iloc[pos].to_numpy()})
    for hz in horizontes:
        out[f"r{hz}"] = (c[pos + hz] - o[pos + 1]) * lado * 1e4
    return out


def resumo_agrupado(ret: pd.DataFrame, horizontes=HORIZONTES_H) -> pd.DataFrame:
    """Média, EP agrupado por hora (cluster), t, mediana e fracção positiva por horizonte."""
    linhas = []
    for hz in horizontes:
        r = ret[f"r{hz}"].to_numpy(dtype=float)
        m = len(r)
        if m == 0:
            linhas.append({"horizonte_h": hz, "n": 0, "n_horas": 0, "media_bps": np.nan, "ep_bps": np.nan,
                           "t": np.nan, "mediana_bps": np.nan, "frac_pos": np.nan})
            continue
        media = r.mean()
        d = r - media
        g = pd.Series(d).groupby(ret["hora"].to_numpy()).sum().to_numpy()
        ep = float(np.sqrt((g ** 2).sum()) / m)
        linhas.append({"horizonte_h": hz, "n": m, "n_horas": len(g), "media_bps": media, "ep_bps": ep,
                       "t": media / ep if ep > 0 else np.nan, "mediana_bps": float(np.median(r)),
                       "frac_pos": float((r > 0).mean())})
    return pd.DataFrame(linhas).set_index("horizonte_h")


def estudo_por_decil(h1s: Dict[str, pd.DataFrame], activos: List[str], coluna_pct: str, lado: int,
                     periodo: str, extra: Optional[str] = None) -> pd.DataFrame:
    """Retorno após velas de 1 h do lado dado, por decil do percentil móvel ``coluna_pct``, agrupando activos."""
    linhas = []
    partes = []
    for a in activos:
        x = H.recortar(h1s[a], periodo).copy()
        dirc = (x["ret"] < 0) if lado > 0 else (x["ret"] > 0)
        if extra == "taker":
            dirc &= (x["taker"] < 0) if lado > 0 else (x["taker"] > 0)
        if coluna_pct.startswith("pct_liq"):
            dirc &= ~x["imputado"]
        x["decil"] = np.minimum(np.floor(x[coluna_pct] * 10) + 1, 10)
        for d in range(1, 11):
            ev = dirc & (x["decil"] == d)
            r = retornos_apos(h1s[a], ev.reindex(h1s[a].index).fillna(False), lado)
            r["decil"] = d
            partes.append(r)
    tudo = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
    for d in range(1, 11):
        sub = tudo[tudo["decil"] == d]
        res = resumo_agrupado(sub)
        for hz, row in res.iterrows():
            linhas.append({"decil": d, "horizonte_h": hz, **row.to_dict()})
    return pd.DataFrame(linhas)


# ---------------------------------------------------------------------------------------------
# Regra: reconquista em 15 m após a vela de cascata
# ---------------------------------------------------------------------------------------------

def sinais_reconquista(df15: pd.DataFrame, h1: pd.DataFrame, ev_compra: pd.Series, ev_venda: pd.Series,
                       reconquista: str, W: int, stop: Tuple[str, float], atr_n: int) -> Tuple[pd.Series, pd.Series]:
    """Sinais (+1/-1) e stops (preço) na grelha de 15 m.

    Para cada cascata de 1 h (fechada em fecho_em), examina as W velas de 15 m seguintes e marca o sinal na
    primeira cujo fecho supera o nível de reconquista (compra: fecho > nível; venda: fecho < nível).
    Nível = mínima/máxima da vela de cascata ("minima") ou o seu fecho ("fecho"). Stop = extremo da
    cascata menos (mais) mult x ATR(14): de 15 m lido na vela do sinal ("atr15") ou de 1 h lido na vela
    de cascata ("atr1h"). Causal: só usa velas até k.
    """
    idx15 = df15.index.as_unit("ns").asi8
    fecho_h1 = h1["fecho_em"].to_numpy()
    fecho_h1 = pd.DatetimeIndex(fecho_h1).as_unit("ns").asi8
    c15 = df15["close"].to_numpy(dtype=float)
    atr15 = H.atr(df15, atr_n).to_numpy(dtype=float)
    atr1h = H.atr(h1, atr_n).to_numpy(dtype=float)
    stop_modo, mult = stop
    n = len(df15)
    sin = np.zeros(n, dtype=int)
    stop_px = np.full(n, np.nan)
    low_h, high_h, close_h = h1["low"].to_numpy(float), h1["high"].to_numpy(float), h1["close"].to_numpy(float)
    for lado, ev in ((1, ev_compra), (-1, ev_venda)):
        pos_ev = np.flatnonzero(ev.reindex(h1.index).fillna(False).to_numpy())
        # primeira vela de 15 m cuja abertura >= fecho da vela de cascata
        p0s = np.searchsorted(idx15, fecho_h1[pos_ev], side="left")
        for j, p0 in zip(pos_ev, p0s):
            if lado > 0:
                nivel = low_h[j] if reconquista == "minima" else close_h[j]
                extremo = low_h[j]
            else:
                nivel = high_h[j] if reconquista == "minima" else close_h[j]
                extremo = high_h[j]
            fim = min(p0 + W, n)
            for k in range(p0, fim):
                if (lado > 0 and c15[k] > nivel) or (lado < 0 and c15[k] < nivel):
                    escala = atr15[k] if stop_modo == "atr15" else atr1h[j]
                    if sin[k] == 0 and np.isfinite(escala):
                        sin[k] = lado
                        stop_px[k] = extremo - lado * mult * escala
                    break
    return pd.Series(sin, index=df15.index), pd.Series(stop_px, index=df15.index)


def correr_regra(dados: Dict[str, pd.DataFrame], h1s: Dict[str, pd.DataFrame], fonte: str, p: float,
                 reconquista: str, stop: Tuple[str, float], alvo: float, periodo: str,
                 activos: Optional[List[str]] = None, exigir_oi_neg: bool = False,
                 custo_bps: float = H.CUSTO_BPS, lados: str = "ambos") -> pd.DataFrame:
    activos = activos or (ACTIVOS_LIQ if fonte == "liq" else ACTIVOS)
    de, ate = H.PERIODOS[periodo]
    partes = []
    for a in activos:
        df15, h1 = dados[a], h1s[a]
        ev_c = eventos_cascata(h1, fonte, p, +1, exigir_oi_neg)
        ev_v = eventos_cascata(h1, fonte, p, -1, exigir_oi_neg)
        if lados == "compra":
            ev_v = ev_v & False
        # só cascatas cujo fecho cai no período
        dentro = (h1.index >= H._utc(de)) & (h1.index <= H._utc(ate))
        ev_c &= dentro
        ev_v &= dentro
        sin, stop_px = sinais_reconquista(df15, h1, ev_c, ev_v, reconquista, FIXOS["W"], stop, FIXOS["atr_n"])
        t = H.simular(df15, sin, stop_px, alvo, FIXOS["n_max"], custo_bps=custo_bps, alvo_em_R=True, activo=a)
        partes.append(t)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


def parametros(fonte, p, reconquista, stop, alvo, lados="ambos", **extra) -> dict:
    d = dict(fonte=fonte, p=p, reconquista=reconquista, stop=f"{stop[1]:g} x {stop[0]}", alvo_R=alvo, lados=lados, **FIXOS)
    d.update(extra)
    return d


# ---------------------------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------------------------

def _f(x, nd=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "nan"
    return f"{x:.{nd}f}"


def tabela_md(df: pd.DataFrame, nd: int = 2, indice: bool = True) -> str:
    d = df.copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda v: _f(v, nd))
    cols = ([d.index.name or ""] if indice else []) + list(d.columns)
    linhas = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for i, row in d.iterrows():
        vals = ([str(i)] if indice else []) + [str(v) for v in row.tolist()]
        linhas.append("| " + " | ".join(vals) + " |")
    return "\n".join(linhas)


def bloco_metricas(t: pd.DataFrame, de, ate, titulo: str) -> str:
    m = H.metricas(t, de, ate)
    g = m["global"]
    s = [f"**{titulo}**: n = {int(g['n'])}, média {_f(g['media'], 3)} R (EP {_f(g['erro_padrao'], 3)}), "
         f"mediana {_f(g['mediana'], 3)}, PF {_f(g['PF'])}, acerto {_f(g['taxa_acerto'] * 100, 1)} %, "
         f"DD máx {_f(g['dd_max'], 1)} R, R bruto médio {_f(g['R_bruto_medio'], 3)}, "
         f"stop {_f(g['frac_stop'] * 100, 0)} % / alvo {_f(g['frac_alvo'] * 100, 0)} %, duração média {_f(g['duracao_h_media'], 1)} h."]
    if len(t):
        cols = ["n", "media", "erro_padrao", "soma", "PF", "taxa_acerto", "dd_max"]
        s.append("\nPor activo:\n\n" + tabela_md(m["por_activo"][cols], 3))
        s.append("\nPor ano:\n\n" + tabela_md(m["por_ano"][cols], 3))
        s.append("\nPor metade:\n\n" + tabela_md(m["por_metade"][cols], 3))
    return "\n".join(s)


def main():
    t0 = time.time()
    os.makedirs(RESULTADOS, exist_ok=True)
    os.makedirs(os.path.dirname(RELATORIO), exist_ok=True)
    print("A carregar", ACTIVOS)
    dados = H.carregar_todos(ACTIVOS)
    h1s = {a: caracteristicas_1h(df) for a, df in dados.items()}
    R: Dict[str, object] = {}

    # ---- 1. validação das liquidações nos 6 activos (IS)
    val = validar_liquidacoes(h1s, "IS")
    print("\nValidação (IS, 1 h):\n", val)
    val.to_csv(os.path.join(RESULTADOS, "cascata_validacao.csv"))
    R["val"] = val

    # ---- 2. estudo de eventos no IS, por decil
    estudos = {}
    for nome, col, activos, extra in (
        ("liq_long", "pct_liq_long", ACTIVOS_LIQ, None),
        ("liq_short", "pct_liq_short", ACTIVOS_LIQ, None),
        ("subst_queda", "pct_subst", ACTIVOS, "taker"),
        ("subst_subida", "pct_subst", ACTIVOS, "taker"),
        ("liq_long_6", "pct_liq_long", ACTIVOS, None),
    ):
        lado = +1 if nome in ("liq_long", "subst_queda", "liq_long_6") else -1
        e = estudo_por_decil(h1s, activos, col, lado, "IS", extra)
        estudos[nome] = e
        e.to_csv(os.path.join(RESULTADOS, f"cascata_eventos_{nome}.csv"), index=False)
        piv = e.pivot(index="decil", columns="horizonte_h", values="media_bps")
        pivt = e.pivot(index="decil", columns="horizonte_h", values="t")
        pn = e.pivot(index="decil", columns="horizonte_h", values="n")[HORIZONTES_H[0]]
        print(f"\nEstudo de eventos IS: {nome} (média bps por decil x horizonte; t entre parênteses; n)")
        print(pd.concat([piv.round(0), pivt.round(1).add_prefix("t"), pn.rename("n")], axis=1))
    R["estudos"] = estudos

    # ---- 2a. resumo por lado, espaçamento 4 h e 24 h, com a deriva incondicional do período (IS)
    lados_linhas = []
    for p in (0.90, 0.95):
        for esp in (4, 24):
            for lado in (1, -1):
                partes, base = [], []
                for a in ACTIVOS:
                    h1 = h1s[a]
                    x = H.recortar(h1, "IS")
                    ev = eventos_cascata(h1, "subst", p, lado) & (h1.index >= x.index[0]) & (h1.index <= x.index[-1])
                    partes.append(retornos_apos(h1, ev, lado, espacamento=esp))
                    c, o, n = np.log(x["close"].to_numpy()), np.log(x["open"].to_numpy()), len(x)
                    base.append(float(((c[25:] - o[1:n - 24]) * lado * 1e4).mean()))
                r = pd.concat(partes, ignore_index=True)
                res = resumo_agrupado(r)
                linha = {"P": p, "espacamento_h": esp, "lado": "compra apos queda" if lado > 0 else "venda apos subida",
                         "n": len(r), "n_horas": int(res.loc[24, "n_horas"])}
                for hz in HORIZONTES_H:
                    linha[f"bps_{hz}h"] = res.loc[hz, "media_bps"]
                    linha[f"ep_{hz}h"] = res.loc[hz, "ep_bps"]
                linha["frac_pos_24h"] = res.loc[24, "frac_pos"]
                linha["deriva_24h_a_favor"] = float(np.mean(base))
                linha["excesso_24h"] = linha["bps_24h"] - linha["deriva_24h_a_favor"]
                lados_linhas.append(linha)
    lados_df = pd.DataFrame(lados_linhas)
    print("\nPor lado (IS):\n", lados_df.round(1))
    lados_df.to_csv(os.path.join(RESULTADOS, "cascata_eventos_por_lado_IS.csv"), index=False)
    R["lados"] = lados_df

    # ---- 2b. informação incremental das liquidações: no decil 10 de |ret| e volume, separar por resíduo
    incr_linhas = []
    for a in ACTIVOS_LIQ:
        x = H.recortar(h1s[a], "IS")
        base = (x["ret"] < 0) & (x["pct_subst"] >= 0.9) & (~x["imputado"]) & x["res_liq_long"].notna()
        for nome, cond in (("res>0 (mais liq do que o preço explica)", base & (x["res_liq_long"] > 0)),
                           ("res<=0", base & (x["res_liq_long"] <= 0))):
            r = retornos_apos(h1s[a], cond.reindex(h1s[a].index).fillna(False), +1)
            r["grupo"] = nome
            incr_linhas.append(r)
    incr = pd.concat(incr_linhas, ignore_index=True)
    incr_res = {g: resumo_agrupado(incr[incr["grupo"] == g]) for g in incr["grupo"].unique()}
    print("\nInformação incremental (IS, BTC/ETH/BNB, quedas no decil 10 de |ret| e volume):")
    for g, r in incr_res.items():
        print(g, "\n", r.round(2))
    R["incr"] = incr_res

    # ---- 2c. OI: a queda de OI acrescenta algo? (quedas no decil 10 substituto)
    oi_linhas = []
    for a in ACTIVOS:
        x = H.recortar(h1s[a], "IS")
        base = (x["ret"] < 0) & (x["taker"] < 0) & (x["pct_subst"] >= 0.9) & (~x["imputado"]) & x["oi_chg"].notna()
        for nome, cond in (("oi_chg<0", base & (x["oi_chg"] < 0)), ("oi_chg>=0", base & (x["oi_chg"] >= 0))):
            r = retornos_apos(h1s[a], cond.reindex(h1s[a].index).fillna(False), +1)
            r["grupo"] = nome
            oi_linhas.append(r)
    oi = pd.concat(oi_linhas, ignore_index=True)
    oi_res = {g: resumo_agrupado(oi[oi["grupo"] == g]) for g in oi["grupo"].unique()}
    print("\nOI (IS, 6 activos, quedas no decil 10 substituto):")
    for g, r in oi_res.items():
        print(g, "\n", r.round(2))
    R["oi"] = oi_res

    # ---- 3. grelha no IS (36 configurações + 2 regras literais, todas registadas)
    grelha = []
    print("\nGrelha IS:")

    def avaliar(fonte, p, rec, stop, alvo, lados, nota):
        t = correr_regra(dados, h1s, fonte, p, rec, stop, alvo, "IS", lados=lados)
        m = H.metricas(t, *H.PERIODOS["IS"])
        H.registar_ensaio(FAMILIA, parametros(fonte, p, rec, stop, alvo, lados), m, "IS", nota=nota)
        g = m["global"]
        n_pos = int((m["por_activo"]["soma"] > 0).sum()) if len(t) else 0
        linha = dict(fonte=fonte, p=p, reconquista=rec, stop=f"{stop[1]:g} x {stop[0]}", alvo_R=alvo, lados=lados, n=int(g["n"]),
                     media=g["media"], ep=g["erro_padrao"], PF=g["PF"], acerto=g["taxa_acerto"], dd=g["dd_max"],
                     n_pos=n_pos, R_bruto=g["R_bruto_medio"], R_pct=float((t["R_preco"] / t["entrada"]).median() * 100) if len(t) else np.nan,
                     frac_stop=g["frac_stop"], frac_alvo=g["frac_alvo"])
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in linha.items()})
        return linha, t

    for fonte, p, rec, stop, alvo, lados in REGRAS_LITERAIS:
        linha, _ = avaliar(fonte, p, rec, stop, alvo, lados, "regra literal da tarefa (stop 0,25 ATR15 abaixo da minima)")
        linha["grelha"] = "literal"
        grelha.append(linha)
    for p in GRELHA_P:
        for rec in GRELHA_RECONQUISTA:
            for stop in GRELHA_STOP:
                for alvo in GRELHA_ALVO:
                    linha, _ = avaliar(GRELHA_FONTE, p, rec, stop, alvo, GRELHA_LADOS, "grelha declarada")
                    linha["grelha"] = "grelha"
                    grelha.append(linha)
    grelha = pd.DataFrame(grelha)
    grelha.to_csv(os.path.join(RESULTADOS, "cascata_grelha_IS.csv"), index=False)
    R["grelha"] = grelha

    # ---- 4. escolha no IS: centro de um patamar
    # Bloco = (reconquista, stop) com 9 células (P x alvo). Escolhe-se o bloco com melhor média das médias
    # entre os blocos cujas células têm todas n >= 100, e dentro dele a célula CENTRAL (P = 0.95, alvo = 3 R),
    # nunca a célula máxima.
    g9 = grelha[grelha["grelha"] == "grelha"]
    blocos = g9.groupby(["reconquista", "stop"]).agg(media_bloco=("media", "mean"), n_total=("n", "sum"),
                                                      n_min=("n", "min"), n_pos_min=("n_pos", "min"),
                                                      min_media=("media", "min"), max_media=("media", "max"),
                                                      PF_medio=("PF", "mean")).reset_index()
    print("\nBlocos (média das 9 células):\n", blocos.round(3))
    R["blocos"] = blocos
    cand = blocos[blocos["n_min"] >= 100]
    melhor = (cand if len(cand) else blocos).sort_values("media_bloco", ascending=False).iloc[0]
    stop_esc = next(st for st in GRELHA_STOP if f"{st[1]:g} x {st[0]}" == melhor["stop"])
    escolha = dict(fonte=GRELHA_FONTE, reconquista=melhor["reconquista"], stop=stop_esc, p=0.95, alvo=3.0, lados=GRELHA_LADOS)
    cel = g9[(g9["reconquista"] == escolha["reconquista"]) & (g9["stop"] == melhor["stop"])
             & (g9["p"] == 0.95) & (g9["alvo_R"] == 3.0)].iloc[0]
    print("\nEscolha (centro do melhor bloco):", escolha, "\ncélula IS:", cel.to_dict())
    R["escolha"] = escolha
    R["celula"] = cel

    t_is = correr_regra(dados, h1s, escolha["fonte"], escolha["p"], escolha["reconquista"], escolha["stop"], escolha["alvo"], "IS", lados=escolha["lados"])
    R["t_is"] = t_is
    R["crit_is"] = H.criterios(t_is, *H.PERIODOS["IS"])
    print("\nCritérios IS:\n", R["crit_is"])

    # nula: sinais aleatórios com stop à mesma distância relativa mediana, mesmo alvo e n_max
    R_rel = (t_is["R_preco"] / t_is["entrada"]).median() if len(t_is) else np.nan
    nulas, nulas_c = [], []
    for a in ACTIVOS:
        df15 = dados[a]
        s = H.sinais_aleatorios(df15, 400, semente=7, de=H.PERIODOS["IS"][0], ate=H.PERIODOS["IS"][1])
        esc = df15["close"] * R_rel
        nulas.append(H.simular(df15, s, 1.0, escolha["alvo"], FIXOS["n_max"], escala=esc, alvo_em_R=True, activo=a))
        # nula só de compras (mede a deriva do IS com os mesmos stop e alvo)
        nulas_c.append(H.simular(df15, s.abs(), 1.0, escolha["alvo"], FIXOS["n_max"], escala=esc, alvo_em_R=True, activo=a))
    t_nula = pd.concat(nulas, ignore_index=True)
    R["nula"] = H.metricas(t_nula)["global"]
    R["nula_compra"] = H.metricas(pd.concat(nulas_c, ignore_index=True))["global"]
    R["R_rel"] = R_rel
    print("\nNula com R mediano relativo", round(R_rel * 100, 2), "%:\n", R["nula"].round(3))
    print("\nNula só compras:\n", R["nula_compra"].round(3))

    # ---- 5. OOS UMA vez (regra escolhida nos 6 activos; a mesma regra nos 18 fica declarada aqui como
    # robustez, avaliada na mesma passagem)
    par_esc = parametros(escolha["fonte"], escolha["p"], escolha["reconquista"], escolha["stop"], escolha["alvo"], escolha["lados"])
    t_oos = correr_regra(dados, h1s, escolha["fonte"], escolha["p"], escolha["reconquista"], escolha["stop"], escolha["alvo"], "OOS", lados=escolha["lados"])
    m_oos = H.metricas(t_oos, *H.PERIODOS["OOS"])
    H.registar_ensaio(FAMILIA, par_esc, m_oos, "OOS", nota="ESCOLHIDA: avaliação OOS única, 6 activos")
    R["t_oos"] = t_oos
    R["crit_oos"] = H.criterios(t_oos, *H.PERIODOS["OOS"])
    print("\nCritérios OOS:\n", R["crit_oos"])

    # ---- 6. robustez adicional (regra de preço, volume e taker): 2020-2022 nos 6 e 18 activos no IS e OOS
    dados18 = {a: (dados[a] if a in dados else H.carregar(a)) for a in H.SIMBOLOS}
    h1s18 = {a: (h1s[a] if a in h1s else caracteristicas_1h(df)) for a, df in dados18.items()}
    de0, ate0 = "2020-09-01", "2022-12-31 23:59:59"
    H.PERIODOS["PRE"] = (de0, ate0)
    t_pre = correr_regra(dados18, h1s18, escolha["fonte"], escolha["p"], escolha["reconquista"], escolha["stop"], escolha["alvo"], "PRE", activos=ACTIVOS, lados=escolha["lados"])
    H.registar_ensaio(FAMILIA, par_esc, H.metricas(t_pre, de0, ate0), "2020-2022", nota="robustez: regra escolhida, 6 activos, 2020-09 a 2022-12")
    R["t_2020"] = t_pre
    t18 = correr_regra(dados18, h1s18, escolha["fonte"], escolha["p"], escolha["reconquista"], escolha["stop"], escolha["alvo"], "IS", activos=H.SIMBOLOS, lados=escolha["lados"])
    H.registar_ensaio(FAMILIA, dict(par_esc, activos=18), H.metricas(t18, *H.PERIODOS["IS"]), "IS", nota="robustez: regra escolhida nos 18 activos")
    R["t_18_is"] = t18
    t18o = correr_regra(dados18, h1s18, escolha["fonte"], escolha["p"], escolha["reconquista"], escolha["stop"], escolha["alvo"], "OOS", activos=H.SIMBOLOS, lados=escolha["lados"])
    H.registar_ensaio(FAMILIA, dict(par_esc, activos=18), H.metricas(t18o, *H.PERIODOS["OOS"]), "OOS", nota="robustez: regra escolhida nos 18 activos (avaliação única, declarada antes)")
    R["t_18_oos"] = t18o
    print("\n2020-2022 (6):\n", H.metricas(t_pre, de0, ate0)["global"].round(3))
    print("\n18 activos IS:\n", H.metricas(t18, *H.PERIODOS["IS"])["global"].round(3))
    print("\n18 activos OOS:\n", H.metricas(t18o, *H.PERIODOS["OOS"])["global"].round(3))

    R["M"] = H.contar_ensaios(FAMILIA)
    R["tempo_s"] = time.time() - t0
    escrever_relatorio(R)
    print(f"\nRelatório em {RELATORIO}. M = {R['M']}. Tempo {R['tempo_s']:.0f} s.")


def escrever_relatorio(R: Dict[str, object]):
    val: pd.DataFrame = R["val"]
    grelha: pd.DataFrame = R["grelha"]
    esc = R["escolha"]
    cel = R["celula"]
    L: List[str] = []
    L.append("# Família CASCATA DE LIQUIDAÇÕES E EXAUSTÃO\n")
    L.append(f"Gerado por `pesquisa/familia_cascata.py` em {pd.Timestamp.now('UTC').strftime('%Y-%m-%d %H:%M UTC')}. "
             f"Configurações registadas em `ensaios/cascata.csv`: M = {R['M']}.\n")
    L.append("## 1. Hipótese\n")
    L.append("Uma cascata de liquidações é fluxo forçado: longs liquidados vendem a mercado sem olhar ao preço, "
             "o OI cai e o volume é climático. Quando o fluxo forçado acaba, o preço fica abaixo do que os "
             "participantes voluntários aceitam e reverte nas horas seguintes. A vantagem, se existir, vem de "
             "comprar a reconquista da mínima da vela de cascata (ou vender a reconquista da máxima após cascata "
             "de shorts), com o stop logo abaixo (acima) desse extremo e alvo em múltiplos de R. É uma regra de "
             "exaustão, não de reversão à média: só actua depois de um extremo de intensidade.\n")
    L.append("## 2. Validação das colunas de liquidações nos 6 activos (IS, velas de 1 h, logs)\n")
    L.append("Regressão de log|long_liq| sobre log|ret|, log volume e log taker sell (USD) da mesma vela; idem "
             "para short_liq com taker buy. Assimetria = média em velas de queda / média em velas de subida.\n")
    L.append(tabela_md(val, 3))
    L.append("\nLeitura: em BTC, ETH e BNB a série distingue direcção (longs liquidam-se em quedas, shorts em "
             "subidas) e tem cauda inferior fina; em SOL, XRP e DOGE a assimetria é fraca e o piso é alto "
             "(p1/p50 elevado): série modelada. A fonte `liq` usa só BTC, ETH e BNB; a fonte `subst` usa os 6.\n")
    L.append("### 2b. As liquidações trazem informação além do preço e do volume?\n")
    L.append("Quedas de 1 h no decil 10 de |ret| e volume (BTC/ETH/BNB, IS), separadas pelo sinal do resíduo da "
             "regressão (resíduo > 0 = mais liquidações do que o preço e o volume explicam). Retorno em bps a favor "
             "da compra, da abertura da vela seguinte ao fecho de k+h; EP agrupado por hora.\n")
    for g, r in R["incr"].items():
        L.append(f"\n{g}:\n\n" + tabela_md(r, 1))
    L.append("\n### 2c. A queda de OI acrescenta algo? (6 activos, quedas no decil 10 substituto, IS)\n")
    for g, r in R["oi"].items():
        L.append(f"\n{g}:\n\n" + tabela_md(r, 1))
    L.append("\n## 3. Estudo de eventos no IS (2023-01-01 a 2025-03-31)\n")
    L.append("### 3a. Por lado, com espaçamento mínimo entre eventos do mesmo activo e deriva incondicional\n")
    L.append("Fonte substituta (6 activos). Retorno em bps A FAVOR do lado (compra após cascata de queda; venda após "
             "cascata de subida), da abertura da vela de 1 h seguinte ao fecho de k+h; EP agrupado por hora. "
             "`deriva_24h_a_favor` = retorno médio a 24 h de TODAS as horas do IS, com o sinal do lado; `excesso_24h` = "
             "retorno após o evento menos essa deriva. Um valor negativo na venda significa que o preço CONTINUOU a subir.\n")
    lados_fmt = R["lados"].copy()
    lados_fmt["P"] = lados_fmt["P"].map(lambda v: f"{v:.2f}")
    lados_fmt["frac_pos_24h"] = lados_fmt["frac_pos_24h"].map(lambda v: f"{v:.2f}")
    L.append(tabela_md(lados_fmt, 1, indice=False))
    L.append("\n### 3b. Por decil de intensidade\n")
    L.append("Evento = vela de 1 h de queda (compra) ou de subida (venda) cuja intensidade cai no decil d do "
             "percentil móvel causal (liquidações: 90 dias; substituto = min(percentil de |ret| a 365 dias, "
             "percentil de volume a 90 dias) e taker do lado do movimento). Retorno em bps a favor do lado, da "
             "abertura da vela seguinte ao fecho de k+h; eventos do mesmo activo espaçados de 4 h; EP agrupado "
             "por hora porque os activos se movem juntos. O decil 10 é a cascata.\n")
    for nome, e in R["estudos"].items():
        piv = e.pivot(index="decil", columns="horizonte_h", values="media_bps")
        pivt = e.pivot(index="decil", columns="horizonte_h", values="t")
        pn = e.pivot(index="decil", columns="horizonte_h", values="n")[HORIZONTES_H[0]]
        tab = pd.concat([piv.add_prefix("bps_"), pivt.add_prefix("t_"), pn.rename("n")], axis=1)
        tab.index.name = "decil"
        L.append(f"\n**{nome}** (horizontes em horas):\n\n" + tabela_md(tab, 1))
    L.append("\n## 4. Grelha declarada (IS)\n")
    L.append(f"Fonte `{GRELHA_FONTE}` (6 activos), lado `{GRELHA_LADOS}` (o estudo de eventos no IS nega a venda), x percentil P {GRELHA_P} x reconquista {GRELHA_RECONQUISTA} x stop "
             f"{[f'{m:g} x ATR(14) {u}' for u, m in GRELHA_STOP]} abaixo da mínima (acima da máxima) da cascata x alvo "
             f"{GRELHA_ALVO} R = {len(grelha[grelha['grelha'] == 'grelha'])} configurações, mais {len(REGRAS_LITERAIS)} regras "
             f"literais da tarefa (stop 0,25 ATR de 15 m abaixo da mínima; fontes subst e liq dos dois lados, e subst só compra). Fixos: {FIXOS} (W velas de "
             "15 m para a reconquista, saída por tempo às 24 h, ATR de 14). Custo 6,5 bps por lado. Todas registadas em "
             "`ensaios/cascata.csv`. A grelha foi fixada DEPOIS do estudo de eventos e do diagnóstico de stop "
             "(a mínima da cascata é revisitada nas 24 h seguintes em 82 % dos casos em BTC e 84 % em SOL; a mínima "
             "menos 1 ATR de 1 h em 55 %), e ANTES de correr qualquer célula.\n")
    L.append(tabela_md(grelha.drop(columns=["grelha"]), 3, indice=False))
    L.append("\nBlocos de 9 células (reconquista x stop), média das médias:\n\n" + tabela_md(R["blocos"], 3, indice=False))
    L.append(f"\n## 5. Escolha no IS\n\nCentro do bloco com melhor média de bloco (entre blocos com n >= 100 em todas as células), "
             f"nunca a célula máxima: fonte `{esc['fonte']}`, reconquista `{esc['reconquista']}`, stop {esc['stop'][1]:g} x ATR(14) "
             f"{esc['stop'][0]}, P = {esc['p']}, alvo = {esc['alvo']} R, lado {esc['lados']}. Célula IS: n = {int(cel['n'])}, média {_f(cel['media'], 3)} R "
             f"(EP {_f(cel['ep'], 3)}), PF {_f(cel['PF'])}, DD {_f(cel['dd'], 1)} R, {int(cel['n_pos'])} activos positivos, "
             f"R mediano {_f(cel['R_pct'])} % do preço.\n")
    L.append(bloco_metricas(R["t_is"], *H.PERIODOS["IS"], "IS, regra escolhida"))
    L.append("\nCritérios H1 a H6 no IS (informativo):\n\n" + tabela_md(R["crit_is"], 3))
    nula = R["nula"]
    L.append(f"\nNula com stop à mesma distância relativa mediana ({_f(R['R_rel'] * 100, 2)} % do preço), alvo "
             f"{esc['alvo']} R, 96 velas, 400 sinais aleatórios por activo no IS (6 activos): média {_f(nula['media'], 3)} R "
             f"(EP {_f(nula['erro_padrao'], 3)}), PF {_f(nula['PF'])}, acerto {_f(nula['taxa_acerto'] * 100, 1)} %. "
             f"Nula SÓ DE COMPRAS nos mesmos instantes (mede a deriva do IS com os mesmos stop e alvo): média "
             f"{_f(R['nula_compra']['media'], 3)} R (EP {_f(R['nula_compra']['erro_padrao'], 3)}), PF {_f(R['nula_compra']['PF'])}.\n")
    L.append("\n## 6. OOS (2025-04-01 a 2026-09-09), avaliado UMA vez\n")
    L.append(bloco_metricas(R["t_oos"], *H.PERIODOS["OOS"], "OOS, regra escolhida, 6 activos"))
    L.append("\nCritérios H1 a H6 no OOS:\n\n" + tabela_md(R["crit_oos"], 3))
    L.append(f"\n**Passa tudo: {R['crit_oos'].attrs['passa_tudo']}.**\n")
    L.append("\n## 7. Robustez adicional (regra só de preço, volume e taker)\n")
    L.append(bloco_metricas(R["t_2020"], "2020-09-01", "2022-12-31 23:59:59", "2020-09 a 2022-12, 6 activos"))
    L.append("\n" + bloco_metricas(R["t_18_is"], *H.PERIODOS["IS"], "IS, 18 activos"))
    L.append("\n" + bloco_metricas(R["t_18_oos"], *H.PERIODOS["OOS"], "OOS, 18 activos (avaliação única, declarada antes)"))
    L.append("\nCritérios H1 a H6 no OOS, 18 activos:\n\n" + tabela_md(H.criterios(R["t_18_oos"], *H.PERIODOS["OOS"]), 3))
    L.append("\n## 8. Veredicto e honestidade\n")
    L.append(HONESTIDADE if HONESTIDADE else "(a escrever depois de ler os números)")
    with open(RELATORIO, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
