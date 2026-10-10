# -*- coding: utf-8 -*-
"""Familia BANDAS COM ANCORA COMPATIVEL: a correccao da REVOU.

A REVOU media o desvio do preco a uma EWMA de 96 h e exigia uma meia-vida de reversao de 3 a 24 h; o
portao nunca abriu porque a meia-vida de um desvio a uma ancora de 96 h e, por construcao, da ordem
das dezenas de horas. Aqui a ancora e o horizonte sao coerentes: para cada ancora de J velas de 15 m,
o sigma e a volatilidade realizada (EWMA de retornos de 15 m com a mesma meia-vida) escalada ao
desvio tipico de um passeio aleatorio face a essa ancora, a saida por tempo e J velas e o alvo e a
propria ancora. Nao ha portao de meia-vida.

Ancoras (J em velas de 15 m):
  ewma24   EWMA do log-preco, meia-vida 48 velas (J = 96, 24 h)
  ewma48   EWMA, meia-vida 96 velas (J = 192, 48 h)
  ewma96   EWMA, meia-vida 192 velas (J = 384, 96 h)   <- a ancora da REVOU
  vwap96   VWAP movel de 384 velas (so no estudo de eventos)
  media96  media simples de 384 velas com desvio padrao movel de 384 velas (bandas sigma em torno da
           media de 4 dias sugeridas pelo Bot: Bollinger de 4 dias em log-preco)
Escala do z:
  EWMA  : sigma_15m x sqrt(lambda^2 / (1 - lambda^2)), lambda = 2^(-1/meia_vida); e o desvio padrao
          teorico de (x - EWMA) para um passeio aleatorio com inovacoes sigma_15m
  VWAP  : sigma_15m x sqrt(J / 3) (desvio tipico de um passeio aleatorio face a media de J pontos)
  media : desvio padrao movel do log-preco em J velas (a definicao classica das bandas)
Modos de entrada (sempre contra o desvio):
  toque      |z| cruza para fora de Z (primeiro fecho com |z| >= Z); lado = -sinal(z)
  reentrada  |z| volta para dentro de Z depois de ter estado fora; lado = -sinal(z anterior)  (REVOU)
Filtros de regime (todos observaveis no fecho k):
  nenhum
  vol    racio sigma_15m(meia-vida 16 velas = 4 h) / sigma_15m(meia-vida 384 velas = 96 h) < 1
  vr     racio de variancias VR(24) de retornos de 1 h nos ultimos 30 dias (720 velas de 1 h,
         actualizado de 4 em 4 h, projectado causalmente) < 1 (regime de reversao)
  tend   tendencia de 4 h a favor do trade: z do fecho de 4 h face a media de 42 velas de 4 h (7 dias)
         com o mesmo sinal do lado (comprar quedas em tendencia de subida)
Stop e alvo (fixos a priori, nao entram na grelha): stop a 1 sigma do fecho do sinal, no sentido
contrario ao trade (fica perto de |z| = 3); alvo = preco da ancora no fecho do sinal; saida por tempo
ao fecho da vela J (a entrada e a 1.a). Custo 6,5 bps por lado. Simulacao em harness.simular.

Correr: python familia_bandas.py eventos | natural | grelha | oos | tudo
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

FAMILIA = "bandas"
RESULTADOS = os.path.join(H.AQUI, "resultados")
RELATORIOS = os.path.join(H.AQUI, "relatorios")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)

PRINCIPAIS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]

# Ancoras: nome -> (tipo, J em velas de 15 m)
ANCORAS = {"ewma24": ("ewma", 96), "ewma48": ("ewma", 192), "ewma96": ("ewma", 384),
           "vwap96": ("vwap", 384), "media96": ("media", 384)}
ANCORAS_GRELHA = ["ewma24", "ewma48", "ewma96", "media96"]
MODOS = ["toque", "reentrada"]
FILTROS = ["nenhum", "vol", "vr", "tend"]
ZS_EVENTOS = [2.0, 2.5, 3.0]
HORIZONTES = [8, 16, 32, 96, 384]      # 2 h, 4 h, 8 h, 24 h, 96 h
MULT_STOP = 1.0                         # stop a 1 sigma do fecho do sinal (fixo)

# Parametros fixos dos filtros
HL_VOL_CURTA = 16        # 4 h
HL_VOL_LONGA = 384       # 96 h
VR_Q = 24                # 24 h em retornos de 1 h
VR_JANELA_1H = 720       # 30 dias
VR_PASSO_1H = 4          # actualiza de 4 em 4 h
TEND_N_4H = 42           # 7 dias de velas de 4 h

# REGRA NATURAL, fixada A PRIORI: e a REVOU sem portao, com escala coerente. E a unica configuracao
# avaliada no OOS se o estudo de eventos for negativo.
REGRA_NATURAL = {"ancora": "ewma96", "modo": "reentrada", "z": 2.0, "filtro": "nenhum"}

# Grelha declarada (48 configuracoes): 4 ancoras x 2 modos x 4 filtros com Z = 2 (32) mais
# 4 ancoras x 2 modos x Z em {2,5; 3,0} sem filtro (16).
GRELHA = ([{"ancora": a, "modo": m, "z": 2.0, "filtro": f}
           for a in ANCORAS_GRELHA for m in MODOS for f in FILTROS]
          + [{"ancora": a, "modo": m, "z": z, "filtro": "nenhum"}
             for a in ANCORAS_GRELHA for m in MODOS for z in (2.5, 3.0)])
assert len(GRELHA) == 48


# ---------------------------------------------------------------------------------------------
# Caracteristicas (todas causais: EWMA e janelas so para tras; velas superiores ja fechadas)
# ---------------------------------------------------------------------------------------------

def _factor_ewma(meia_vida: float) -> float:
    lam = 2.0 ** (-1.0 / meia_vida)
    return float(np.sqrt(lam ** 2 / (1.0 - lam ** 2)))


def caracteristicas(df15: pd.DataFrame) -> pd.DataFrame:
    """z-scores por ancora, sigma em fraccao do preco, ancora em preco, e filtros de regime."""
    lc = np.log(df15["close"].astype(float))
    ret = lc.diff()
    car = pd.DataFrame(index=df15.index)
    car["open_px"] = df15["open"].astype(float)
    car["high"] = df15["high"].astype(float)
    car["low"] = df15["low"].astype(float)
    car["close"] = df15["close"].astype(float)
    car["fecho_em"] = df15["fecho_em"]
    car["activo"] = df15["activo"]
    for nome, (tipo, J) in ANCORAS.items():
        hl = J / 2.0
        if tipo == "ewma":
            a = lc.ewm(halflife=hl, min_periods=J).mean()
            sig = H.vol_realizada_ewma(ret, hl) * _factor_ewma(hl)
        elif tipo == "vwap":
            a = np.log(H.vwap_movel(df15, J))
            sig = H.vol_realizada_ewma(ret, hl) * np.sqrt(J / 3.0)
        else:  # media simples + desvio padrao movel do log-preco (Bollinger)
            a = lc.rolling(J, min_periods=J).mean()
            sig = lc.rolling(J, min_periods=J).std()
        sig = sig.replace(0.0, np.nan)
        car[f"z_{nome}"] = (lc - a) / sig
        car[f"sig_{nome}"] = sig                 # em fraccao do preco (log)
        car[f"anc_{nome}"] = np.exp(a)           # ancora em preco
    # filtro vol: racio curta/longa
    car["racio_vol"] = H.vol_realizada_ewma(ret, HL_VOL_CURTA) / H.vol_realizada_ewma(ret, HL_VOL_LONGA)
    # filtro vr: VR(24) de retornos de 1 h em janela de 30 dias, projectado pela vela de 1 h fechada
    df1h = H.agregar(df15, "1h")
    r1h = H.retorno_log(df1h["close"])
    vrm = H.racio_variancias_movel(r1h, VR_Q, VR_JANELA_1H, passo=VR_PASSO_1H)
    vrm["fecho_em"] = df1h["fecho_em"].reindex(vrm.index)
    car["vr24"] = H.projectar_superior(df15, vrm, ["vr"])["vr"]
    # filtro tend: z do fecho de 4 h face a media de 42 velas de 4 h, escalado ao passeio aleatorio
    df4h = H.agregar(df15, "4h")
    lc4 = np.log(df4h["close"].astype(float))
    r4 = lc4.diff()
    m4 = lc4.rolling(TEND_N_4H, min_periods=TEND_N_4H).mean()
    s4 = H.vol_realizada_ewma(r4, TEND_N_4H / 2.0) * np.sqrt(TEND_N_4H / 3.0)
    df4h = df4h.assign(z_tend=(lc4 - m4) / s4.replace(0.0, np.nan))
    car["z_tend4h"] = H.projectar_superior(df15, df4h, ["z_tend"])["z_tend"]
    return car


def sinais_e_niveis(car: pd.DataFrame, ancora: str, modo: str, z_lim: float, filtro: str):
    """Series de lado (+1/-1/0) ao fecho k, stop em preco e alvo em preco (ancora)."""
    z = car[f"z_{ancora}"]
    zp = z.shift(1)
    if modo == "toque":
        ev = (z.abs() >= z_lim) & (zp.abs() < z_lim)
        lado = -np.sign(z).where(ev, 0.0)
    elif modo == "reentrada":
        ev = (z.abs() < z_lim) & (zp.abs() >= z_lim)
        lado = -np.sign(zp).where(ev, 0.0)
    else:
        raise ValueError(modo)
    lado = lado.fillna(0.0)
    if filtro == "vol":
        lado = lado.where(car["racio_vol"] < 1.0, 0.0)
    elif filtro == "vr":
        lado = lado.where(car["vr24"] < 1.0, 0.0)
    elif filtro == "tend":
        lado = lado.where(lado * np.sign(car["z_tend4h"]) > 0, 0.0)
    elif filtro != "nenhum":
        raise ValueError(filtro)
    sig = car[f"sig_{ancora}"]
    stop = car["close"] * np.exp(-lado * MULT_STOP * sig)
    alvo = car[f"anc_{ancora}"]
    return lado.astype(float), stop, alvo


# ---------------------------------------------------------------------------------------------
# Estudo de eventos (IS), agrupado por bloco de 4 h entre activos
# ---------------------------------------------------------------------------------------------

def _retornos_eventos(car: pd.DataFrame, lado: pd.Series, horizontes, de, ate, espacar=True) -> pd.DataFrame:
    """Uma linha por evento e horizonte: retorno (bps, a favor do lado) da abertura de k+1 ao fecho de
    k+h, e em unidades de sigma da ancora. Com ``espacar`` retem so eventos a >= h velas do anterior."""
    idx = car.index
    mask = (idx >= H._utc(de)) & (idx <= H._utc(ate))
    pos_all = np.flatnonzero((lado.to_numpy() != 0) & mask)
    c = np.log(car["close"].to_numpy(dtype=float))
    o = np.log(car["open_px"].to_numpy(dtype=float))
    ld = lado.to_numpy(dtype=float)
    n = len(car)
    linhas = []
    for hzt in horizontes:
        pos = pos_all
        if espacar and len(pos):
            ret_ = [pos[0]]
            for p in pos[1:]:
                if p - ret_[-1] >= hzt:
                    ret_.append(p)
            pos = np.asarray(ret_)
        ok = pos[pos + hzt < n]
        r = (c[ok + hzt] - o[ok + 1]) * ld[ok]
        linhas.append(pd.DataFrame({"h": hzt, "t": idx[ok], "lado": ld[ok], "r_bps": r * 1e4}))
    return pd.concat(linhas, ignore_index=True) if linhas else pd.DataFrame()


def resumo_agrupado(df: pd.DataFrame, col="r_bps") -> pd.Series:
    """Media, erro padrao ingenuo e agrupado (por bloco de 4 h), t agrupado, mediana e fraccao positiva."""
    r = df[col].to_numpy(dtype=float)
    n = len(r)
    if n == 0:
        return pd.Series({"n": 0, "n_blocos": 0, "media_bps": np.nan, "ep_bps": np.nan,
                          "ep_agrupado_bps": np.nan, "t_agrupado": np.nan, "mediana_bps": np.nan, "frac_pos": np.nan})
    m = r.mean()
    bloco = pd.DatetimeIndex(df["t"]).floor("4h")
    e = pd.Series(r - m).groupby(bloco.to_numpy()).sum()
    ep_cl = float(np.sqrt((e ** 2).sum()) / n)
    ep = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else np.nan
    return pd.Series({"n": n, "n_blocos": int(len(e)), "media_bps": m, "ep_bps": ep, "ep_agrupado_bps": ep_cl,
                      "t_agrupado": m / ep_cl if ep_cl > 0 else np.nan, "mediana_bps": float(np.median(r)),
                      "frac_pos": float((r > 0).mean())})


def carregar_caracteristicas(activos):
    cars = {}
    for a in activos:
        t = time.time()
        cars[a] = caracteristicas(H.carregar(a))
        print(f"  {a}: {len(cars[a])} velas, {time.time() - t:.1f} s", flush=True)
    return cars


def estudo_eventos(cars, periodo="IS", nome="eventos"):
    """Tabela: ancora x modo x Z x horizonte, retorno a favor do lado, agrupado por bloco de 4 h."""
    de, ate = H.PERIODOS[periodo]
    linhas = []
    for anc in ANCORAS:
        for modo in MODOS:
            for z in ZS_EVENTOS:
                partes = []
                for a, car in cars.items():
                    lado, _, _ = sinais_e_niveis(car, anc, modo, z, "nenhum")
                    d = _retornos_eventos(car, lado, HORIZONTES, de, ate)
                    if len(d):
                        d["activo"] = a
                        partes.append(d)
                if not partes:
                    continue
                d = pd.concat(partes, ignore_index=True)
                for h, g in d.groupby("h"):
                    r = resumo_agrupado(g)
                    r["ancora"], r["modo"], r["z"], r["h"] = anc, modo, z, h
                    linhas.append(r)
    out = pd.DataFrame(linhas)
    out.to_csv(os.path.join(RESULTADOS, f"bandas_{nome}_{periodo}.csv"), index=False)
    return out


def estudo_eventos_por_activo(cars, periodo="IS", anc="ewma96", modo="reentrada", z=2.0, h=96):
    de, ate = H.PERIODOS[periodo]
    linhas = []
    for a, car in cars.items():
        lado, _, _ = sinais_e_niveis(car, anc, modo, z, "nenhum")
        d = _retornos_eventos(car, lado, [h], de, ate)
        r = resumo_agrupado(d) if len(d) else resumo_agrupado(pd.DataFrame({"r_bps": [], "t": []}))
        r["activo"] = a
        linhas.append(r)
    return pd.DataFrame(linhas).set_index("activo")


def estudo_por_bucket_z(cars, periodo="IS", h=96, nome="buckets"):
    """Retorno bruto (bps, nao assinado) nas h velas seguintes por intervalo de z, por ancora.
    Mostra directamente se desvios grandes revertem (retorno de sinal contrario ao z)."""
    de, ate = H.PERIODOS[periodo]
    limites = [-np.inf, -3, -2, -1, 1, 2, 3, np.inf]
    rotulos = ["<-3", "-3..-2", "-2..-1", "-1..1", "1..2", "2..3", ">3"]
    linhas = []
    for anc in ANCORAS:
        partes = []
        for a, car in cars.items():
            idx = car.index
            mask = (idx >= H._utc(de)) & (idx <= H._utc(ate))
            z = car[f"z_{anc}"].to_numpy(dtype=float)
            c = np.log(car["close"].to_numpy(dtype=float))
            o = np.log(car["open_px"].to_numpy(dtype=float))
            n = len(car)
            pos = np.flatnonzero(mask & np.isfinite(z))
            pos = pos[(pos + h < n)]
            pos = pos[::h]      # amostragem sem sobreposicao
            r = (c[pos + h] - o[pos + 1]) * 1e4
            partes.append(pd.DataFrame({"t": idx[pos], "z": z[pos], "r_bps": r}))
        d = pd.concat(partes, ignore_index=True)
        d["bucket"] = pd.cut(d["z"], limites, labels=rotulos)
        for b, g in d.groupby("bucket", observed=True):
            r = resumo_agrupado(g)
            r["ancora"], r["bucket"], r["h"] = anc, str(b), h
            linhas.append(r)
    out = pd.DataFrame(linhas)
    out.to_csv(os.path.join(RESULTADOS, f"bandas_{nome}_{periodo}.csv"), index=False)
    return out


def estudo_filtros(cars, periodo="IS", z=2.0, horizontes=(16, 96), nome="filtros"):
    """Para cada ancora e modo com Z = 2: retorno a favor do lado dividido pelo estado de cada filtro."""
    de, ate = H.PERIODOS[periodo]
    linhas = []
    for anc in ANCORAS_GRELHA:
        for modo in MODOS:
            partes = []
            for a, car in cars.items():
                lado, _, _ = sinais_e_niveis(car, anc, modo, z, "nenhum")
                d = _retornos_eventos(car, lado, list(horizontes), de, ate)
                if not len(d):
                    continue
                pos = car.index.get_indexer(d["t"])
                d["vol_calma"] = car["racio_vol"].to_numpy()[pos] < 1.0
                d["vr_rev"] = car["vr24"].to_numpy()[pos] < 1.0
                d["tend_favor"] = d["lado"].to_numpy() * np.sign(car["z_tend4h"].to_numpy()[pos]) > 0
                d["vr_ok"] = np.isfinite(car["vr24"].to_numpy()[pos])
                d["tend_ok"] = np.isfinite(car["z_tend4h"].to_numpy()[pos])
                partes.append(d)
            d = pd.concat(partes, ignore_index=True)
            for h, g in d.groupby("h"):
                for filtro, col, ok in (("vol", "vol_calma", None), ("vr", "vr_rev", "vr_ok"), ("tend", "tend_favor", "tend_ok")):
                    gg = g if ok is None else g[g[ok]]
                    for estado in (True, False):
                        r = resumo_agrupado(gg[gg[col] == estado])
                        r["ancora"], r["modo"], r["h"], r["filtro"], r["estado"] = anc, modo, h, filtro, estado
                        linhas.append(r)
    out = pd.DataFrame(linhas)
    out.to_csv(os.path.join(RESULTADOS, f"bandas_{nome}_{periodo}.csv"), index=False)
    return out


# ---------------------------------------------------------------------------------------------
# Simulacao, grelha, avaliacao
# ---------------------------------------------------------------------------------------------

def simular_config(cars, cfg, de, ate):
    partes = []
    ign = 0
    J = ANCORAS[cfg["ancora"]][1]
    for a, car in cars.items():
        lado, stop, alvo = sinais_e_niveis(car, cfg["ancora"], cfg["modo"], cfg["z"], cfg["filtro"])
        rec = car[(car.index >= H._utc(de)) & (car.index <= H._utc(ate))]
        df = rec.rename(columns={"open_px": "open"})[["open", "high", "low", "close", "fecho_em", "activo"]]
        tr = H.simular(df, lado.reindex(rec.index), stop.reindex(rec.index), alvo.reindex(rec.index),
                       n_max=J, custo_bps=H.CUSTO_BPS, activo=a)
        ign += tr.attrs.get("ignorados", 0)
        partes.append(tr)
    out = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
    out.attrs["ignorados"] = ign
    return out


def avaliar(cars, cfg, periodo, nota, guardar=True, etiqueta=None):
    de, ate = H.PERIODOS[periodo]
    tr = simular_config(cars, cfg, de, ate)
    m = H.metricas(tr, de, ate)
    cr = H.criterios(tr, de, ate)
    params = dict(cfg, mult_stop=MULT_STOP, alvo="ancora", n_max=ANCORAS[cfg["ancora"]][1],
                  activos=len(cars))
    if guardar:
        H.registar_ensaio(FAMILIA, params, m, periodo=periodo, nota=nota)
        if etiqueta:
            tr.to_csv(os.path.join(RESULTADOS, f"bandas_trades_{etiqueta}.csv"), index=False)
    return tr, m, cr


def imprimir_avaliacao(nome, tr, m, cr):
    g = m["global"]
    print(f"\n== {nome}: n={int(g['n'])} media={g['media']:.4f} R (ep {g['erro_padrao']:.4f}) PF={g['PF']:.3f} "
          f"acerto={g['taxa_acerto']:.3f} DD={g['dd_max']:.1f} R bruto={g['R_bruto_medio']:.4f} "
          f"dur={g['duracao_h_media']:.1f} h stop={g['frac_stop']:.2f} alvo={g['frac_alvo']:.2f} "
          f"ignorados={tr.attrs.get('ignorados', 0)}")
    if len(tr):
        print("R em % do preco (mediana):", round(float((tr["R_preco"] / tr["entrada"]).median() * 100), 3))
        print(m["por_activo"][["n", "media", "erro_padrao", "soma", "PF", "taxa_acerto"]].round(4))
        print(m["por_ano"][["n", "media", "erro_padrao", "soma", "PF"]].round(4))
        print(m["por_metade"][["n", "media", "soma", "PF"]].round(4))
    print(cr[["descricao", "valor", "limite", "passa"]])


def grelha(cars, periodo="IS"):
    de, ate = H.PERIODOS[periodo]
    linhas = []
    for i, cfg in enumerate(GRELHA):
        t = time.time()
        tr = simular_config(cars, cfg, de, ate)
        m = H.metricas(tr, de, ate)
        H.registar_ensaio(FAMILIA, dict(cfg, mult_stop=MULT_STOP, alvo="ancora", n_max=ANCORAS[cfg["ancora"]][1],
                                        activos=len(cars)), m, periodo=periodo, nota="grelha")
        g = m["global"]
        n_pos = int((m["por_activo"]["soma"] > 0).sum()) if len(tr) else 0
        met = m["por_metade"]["soma"] if len(tr) else pd.Series(dtype=float)
        linhas.append(dict(cfg, n=int(g["n"]), media=g["media"], ep=g["erro_padrao"], PF=g["PF"],
                           acerto=g["taxa_acerto"], dd=g["dd_max"], n_pos=n_pos,
                           metades_pos=int((met > 0).sum()), R_pct=float((tr["R_preco"] / tr["entrada"]).median() * 100) if len(tr) else np.nan,
                           dur_h=g["duracao_h_media"], frac_stop=g["frac_stop"], frac_alvo=g["frac_alvo"]))
        print(f"  [{i + 1}/{len(GRELHA)}] {cfg} n={int(g['n'])} media={g['media']:.4f} PF={g['PF']:.3f} "
              f"pos={n_pos} ({time.time() - t:.1f} s)", flush=True)
    out = pd.DataFrame(linhas)
    out.to_csv(os.path.join(RESULTADOS, f"bandas_grelha_{periodo}.csv"), index=False)
    return out


def escolher(gr: pd.DataFrame):
    """Escolha no IS pelo centro de um patamar: configuracoes com media >= 0,15 R e PF >= 1,3;
    o grupo (ancora, modo) com mais membros; dentro dele a configuracao com a media MEDIANA (nunca o
    maximo). Devolve None se nao houver patamar (a regra natural vai ao OOS)."""
    ok = gr[(gr["media"] >= 0.15) & (gr["PF"] >= 1.3) & (gr["n"] >= 100)]
    if len(ok) < 3:
        return None
    grupo = ok.groupby(["ancora", "modo"]).size().sort_values(ascending=False)
    anc, modo = grupo.index[0]
    cand = ok[(ok["ancora"] == anc) & (ok["modo"] == modo)].sort_values("media")
    linha = cand.iloc[len(cand) // 2]
    return {"ancora": linha["ancora"], "modo": linha["modo"], "z": float(linha["z"]), "filtro": linha["filtro"]}


def main(etapa="tudo"):
    os.makedirs(RESULTADOS, exist_ok=True)
    os.makedirs(RELATORIOS, exist_ok=True)
    t0 = time.time()
    print("A carregar e a calcular caracteristicas dos 18 activos...")
    cars = carregar_caracteristicas(H.SIMBOLOS)
    cars6 = {a: cars[a] for a in PRINCIPAIS}
    print(f"caracteristicas prontas em {time.time() - t0:.0f} s")

    if etapa in ("eventos", "tudo"):
        print("\n### ESTUDO DE EVENTOS no IS, 6 principais (retorno a favor do lado, bps, agrupado por 4 h)")
        ev6 = estudo_eventos(cars6, "IS", "eventos6")
        cols = ["ancora", "modo", "z", "h", "n", "n_blocos", "media_bps", "ep_agrupado_bps", "t_agrupado", "mediana_bps", "frac_pos"]
        print(ev6[cols].round(2).to_string(index=False))
        print("\n### ESTUDO DE EVENTOS no IS, 18 activos")
        ev18 = estudo_eventos(cars, "IS", "eventos18")
        print(ev18[cols].round(2).to_string(index=False))
        print("\n### ESTUDO DE EVENTOS em 2020-09 a 2022-12, 18 activos (so preco)")
        H.PERIODOS["PRE"] = ("2020-09-01", "2022-12-31 23:59:59")
        evpre = estudo_eventos(cars, "PRE", "eventos18")
        print(evpre[cols].round(2).to_string(index=False))
        print("\n### RETORNO BRUTO a 24 h por intervalo de z (IS, 18 activos, amostragem sem sobreposicao)")
        bk = estudo_por_bucket_z(cars, "IS", 96)
        print(bk[["ancora", "bucket", "n", "media_bps", "ep_agrupado_bps", "t_agrupado", "frac_pos"]].round(2).to_string(index=False))
        print("\n### RETORNO BRUTO a 4 h por intervalo de z (IS, 18 activos)")
        bk16 = estudo_por_bucket_z(cars, "IS", 16, nome="buckets16")
        print(bk16[["ancora", "bucket", "n", "media_bps", "ep_agrupado_bps", "t_agrupado", "frac_pos"]].round(2).to_string(index=False))
        print("\n### FILTROS DE REGIME no IS (18 activos, Z = 2)")
        fl = estudo_filtros(cars, "IS")
        print(fl[["ancora", "modo", "h", "filtro", "estado", "n", "media_bps", "ep_agrupado_bps", "t_agrupado", "frac_pos"]].round(2).to_string(index=False))
        print("\n### POR ACTIVO: ewma96 reentrada Z=2, 24 h, IS")
        print(estudo_eventos_por_activo(cars, "IS").round(2))
        print("\n### POR ACTIVO: ewma24 toque Z=2, 4 h, IS")
        print(estudo_eventos_por_activo(cars, "IS", "ewma24", "toque", 2.0, 16).round(2))

    if etapa in ("natural", "tudo"):
        print("\n### REGRA NATURAL (a priori) no IS")
        tr, m, cr = avaliar(cars6, REGRA_NATURAL, "IS", "regra natural, 6 principais", etiqueta="natural_IS6")
        imprimir_avaliacao("natural IS 6 principais", tr, m, cr)
        tr, m, cr = avaliar(cars, REGRA_NATURAL, "IS", "regra natural, 18 activos", etiqueta="natural_IS18")
        imprimir_avaliacao("natural IS 18 activos", tr, m, cr)
        # nula com os mesmos stop e alvo em R (referencia): sinais ao acaso, mesmo n por activo
        print("\n### NULA com a mesma escala (ewma96: stop 1 sigma, alvo 2 R, 384 velas), IS, 18 activos")
        partes = []
        de, ate = H.PERIODOS["IS"]
        for a, car in cars.items():
            rec = car[(car.index >= H._utc(de)) & (car.index <= H._utc(ate))]
            df = rec.rename(columns={"open_px": "open"})[["open", "high", "low", "close", "fecho_em", "activo"]]
            s = H.sinais_aleatorios(df, 300, semente=7)
            esc = (rec["sig_ewma96"] * rec["close"]).reindex(df.index)
            partes.append(H.simular(df, s, MULT_STOP, 2.0, 384, escala=esc, alvo_em_R=True, activo=a))
        nula = pd.concat(partes, ignore_index=True)
        mn = H.metricas(nula, de, ate)
        print(mn["global"].round(4))
        nula.to_csv(os.path.join(RESULTADOS, "bandas_trades_nula_IS18.csv"), index=False)

    if etapa in ("grelha", "tudo"):
        print("\n### GRELHA no IS (48 configuracoes, 18 activos), todas registadas")
        gr = grelha(cars, "IS")
        print(gr.round(4).to_string(index=False))
        esc = escolher(gr)
        print("\nEscolha pelo centro do patamar:", esc)
        with open(os.path.join(RESULTADOS, "bandas_escolha.json"), "w", encoding="utf-8") as f:
            json.dump({"escolha": esc}, f)

    if etapa in ("oos", "tudo"):
        p = os.path.join(RESULTADOS, "bandas_escolha.json")
        esc = None
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                esc = json.load(f).get("escolha")
        cfg = esc if esc else REGRA_NATURAL
        print(f"\n### OOS (uma vez) com a configuracao: {cfg}  ({'patamar do IS' if esc else 'regra natural'})")
        tr, m, cr = avaliar(cars6, cfg, "OOS", "OOS, 6 principais, avaliado uma vez", etiqueta="OOS6")
        imprimir_avaliacao("OOS 6 principais", tr, m, cr)
        tr, m, cr = avaliar(cars, cfg, "OOS", "OOS, 18 activos, avaliado uma vez", etiqueta="OOS18")
        imprimir_avaliacao("OOS 18 activos", tr, m, cr)
        print("\n### ROBUSTEZ: 2020-09 a 2022-12 (so preco), 18 activos")
        H.PERIODOS["PRE"] = ("2020-09-01", "2022-12-31 23:59:59")
        tr, m, cr = avaliar(cars, cfg, "PRE", "2020-09 a 2022-12, 18 activos", etiqueta="PRE18")
        imprimir_avaliacao("2020-2022 18 activos", tr, m, cr)
    print(f"\nM (configuracoes registadas em ensaios/{FAMILIA}.csv): {H.contar_ensaios(FAMILIA)}")
    print(f"tempo total {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tudo")
