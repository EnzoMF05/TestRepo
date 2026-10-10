# -*- coding: utf-8 -*-
"""Familia TRANSVERSAL: desvio relativo de cada alt face ao BTC (retorno do alt menos beta x retorno
do BTC, beta estimado em janela movel de 30 dias) em 4 h e 24 h.

Hipotese: os alts sobre-reagem ao BTC. Quando um alt se afasta do que o seu beta ao BTC justifica
(cai mais do que o BTC numa queda, ou sobe menos; ou o contrario), parte do desvio e liquidez e
posicionamento forcado (stops, liquidacoes, market makers a cobrir) e nao informacao propria do alt,
e deveria reverter em 1 a 3 dias. O sinal e so no alt (compra ou venda do alt, sem perna no BTC,
porque o utilizador opera a mao); a variante com perna no BTC (venda/compra de beta x nocional em
BTC, fechada ao mesmo tempo) e avaliada a parte. O diferencial de funding alt menos BTC entra como
filtro de aglomeracao: nao se compra o alt quando a multidao ja esta longa no alt (diferencial
positivo e extremo) nem se vende quando ja esta curta.

Ordem de trabalho (obrigatoria): caracteristicas causais -> estudo de eventos por DECIL no IS, com
erros padrao agrupados por bloco temporal, ANTES de qualquer regra -> regra natural fixada a priori
-> grelha pequena e declarada (48 configuracoes), toda registada -> escolha no IS pelo centro de um
patamar (nunca o maximo) -> OOS avaliado UMA vez -> robustez em 2020-2022 (a parte de preco e so de
preco) -> relatorio.

Caracteristicas (todas na vela de 1 h FECHADA do alt, alinhada a vela de 1 h fechada do BTC, e
projectadas ao 15 m por harness.projectar_superior):
  beta    cov(r_alt, r_btc) / var(r_btc) em 720 velas de 1 h (30 dias, minimo 360), retornos log de 1 h
  e1      residuo de 1 h: r_alt - beta x r_btc
  sig_e   vol EWMA (meia-vida 168 h) do residuo de 1 h
  z_res4  (ret4h_alt - beta x ret4h_btc) / (sig_e x sqrt(4))
  z_res24 (ret24h_alt - beta x ret24h_btc) / (sig_e x sqrt(24))
  z_ret24 ret24h_alt / (sigma_1h_alt x sqrt(24)) (retorno proprio normalizado, para comparar: o
          residuo traz informacao alem do retorno bruto do alt?)
  z_fd    z robusto do diferencial de funding (alt menos BTC, taxa de 8 h em %) sobre 30 dias,
          escala minima 0,002 % (so em velas nao imputadas; BNB fica fora do filtro: funding nao fiavel)
  sigma   sigma diaria em preco do alt: vol EWMA (meia-vida 168 h) dos retornos de 1 h x sqrt(24) x close

Regras (sinal no fecho de 1 h; entrada na abertura do 15 m seguinte; so uma posicao por activo):
  z_res_H <= -Z -> compra do alt (caiu demais face ao BTC); z_res_H >= +Z -> venda.  H em {4, 24}; Z em {2, 3}
  filtro de aglomeracao (0/1): com filtro, a compra exige z_fd <= +1 e a venda exige z_fd >= -1
  (nao entrar com a multidao ja do nosso lado em funding). Stop: M x sigma diaria, M em {1,0, 1,5}.
  Sem alvo (so tempo e stop). Saida por tempo ao fecho de N_MAX velas de 15 m, N_MAX em {96, 192, 288}.
  Grelha: 2 x 2 x 2 x 3 x 2 = 48, nos 5 alts principais (ETH, SOL, BNB, XRP, DOGE) no IS 2023-01 a 2025-03.

Correr: python familia_transversal.py eventos | natural | grelha | oos | causal | tudo
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

FAMILIA = "transversal"
RESULTADOS = os.path.join(H.AQUI, "resultados")
RELATORIOS = os.path.join(H.AQUI, "relatorios")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)

REFERENCIA = "BTC"
ALTS_PRINCIPAIS = ["ETH", "SOL", "BNB", "XRP", "DOGE"]
ALTS_18 = [s for s in H.SIMBOLOS if s != REFERENCIA]          # 17 alts
SEM_FUNDING = {"BNB"}            # funding do BNB: 50 % zeros (validar_colunas): o filtro nao se aplica

# Parametros FIXOS, declarados antes de olhar para trades (nao entram na grelha)
JANELA_BETA_H = 720              # 30 dias de velas de 1 h
MIN_BETA_H = 360                 # 15 dias
MEIA_VIDA_VOL_H = 168            # 7 dias (sigma do residuo e sigma diaria)
JANELA_Z_FD = 720
MIN_Z_FD = 360
ESCALA_MIN_FD = 0.002            # % por 8 h
LIMIAR_FILTRO_FD = 1.0           # com filtro: compra exige z_fd <= +1; venda exige z_fd >= -1
HORIZONTES_H = [4, 24, 48, 72]   # horas, para o estudo de eventos
ALVO_R_NULO = 1000.0             # sem alvo: so stop e tempo
LIMIAR_EVENTOS = 2.0             # |z| >= 2 para o teste contrarian nos extremos

GRELHA_H = [4, 24]
GRELHA_Z = [2.0, 3.0]
GRELHA_STOP_SIGMA = [1.0, 1.5]
GRELHA_N_MAX = [96, 192, 288]
GRELHA_FILTRO = [0, 1]

# REGRA NATURAL, fixada A PRIORI (e a formulacao da hipotese: desvio de 24 h, 2 sigma, reversao em 2 dias,
# stop 1,5 sigma diaria, sem filtro). Se o estudo de eventos for negativo, e a UNICA configuracao
# avaliada no OOS. Conjunto de avaliacao OOS declarado a priori: (a) os 5 alts principais, (b) os 17 alts
# para a versao so de preco (sem filtro), (c) 2020-2022 so de preco. Variante com perna no BTC: registo.
REGRA_NATURAL = {"h": 24, "z": 2.0, "stop_sigma": 1.5, "n_max": 192, "filtro": 0}


# ---------------------------------------------------------------------------------------------
# Caracteristicas (causais: velas de 1 h fechadas, janelas so para tras, metricas sem imputados)
# ---------------------------------------------------------------------------------------------

def btc_1h(de=None) -> pd.DataFrame:
    """Velas de 1 h completas do BTC com retorno log de 1 h e funding mascarado."""
    b = H.agregar(H.carregar(REFERENCIA, de=de), "1h")
    out = pd.DataFrame(index=b.index)
    out["c_btc"] = b["close"].astype(float)
    out["o_btc"] = b["open"].astype(float)
    out["r_btc"] = H.retorno_log(out["c_btc"], 1)
    out["f_btc"] = H.mascarar_imputado(b, "funding_rate_pct")
    out["fecho_em"] = b["fecho_em"]
    return out


def caracteristicas_1h(df15: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    """Velas de 1 h completas do alt, alinhadas ao BTC (so horas em que ambas as velas estao fechadas)."""
    h1 = H.agregar(df15, "1h").copy()
    comum = h1.index.intersection(btc.index)
    h1 = h1.loc[comum]
    b = btc.loc[comum]
    c = h1["close"].astype(float)
    r = H.retorno_log(c, 1)
    rb = b["r_btc"]
    # beta movel de 30 dias (cov/var em janela fechada em k: so usa dados ate k)
    cov = r.rolling(JANELA_BETA_H, min_periods=MIN_BETA_H).cov(rb)
    var = rb.rolling(JANELA_BETA_H, min_periods=MIN_BETA_H).var()
    beta = cov / var.replace(0.0, np.nan)
    h1["beta"] = beta
    e1 = r - beta * rb
    h1["e1"] = e1
    sig_e = H.vol_realizada_ewma(e1, MEIA_VIDA_VOL_H)
    h1["sig_e"] = sig_e
    for hz in (4, 24):
        res = H.retorno_log(c, hz) - beta * H.retorno_log(b["c_btc"], hz)
        h1[f"res{hz}"] = res
        h1[f"z_res{hz}"] = res / (sig_e * np.sqrt(hz))
    sig1 = H.vol_realizada_ewma(r, MEIA_VIDA_VOL_H)
    h1["z_ret24"] = H.retorno_log(c, 24) / (sig1 * np.sqrt(24.0))
    h1["sigma"] = sig1 * np.sqrt(24.0) * c
    fd = H.mascarar_imputado(h1, "funding_rate_pct") - b["f_btc"]
    h1["fd"] = fd
    h1["z_fd"] = H.z_robusto(fd, JANELA_Z_FD, MIN_Z_FD, escala_min=ESCALA_MIN_FD)
    h1["open_px"] = h1["open"].astype(float)
    h1["c_btc"] = b["c_btc"]
    h1["o_btc"] = b["o_btc"]
    return h1


COLS_PROJ = ["beta", "sig_e", "z_res4", "z_res24", "z_ret24", "z_fd", "sigma"]


def projectar(df15: pd.DataFrame, h1: pd.DataFrame) -> pd.DataFrame:
    """Leva as caracteristicas de 1 h a grelha de 15 m (so velas de 1 h ja fechadas) e marca os fechos de hora."""
    car = H.projectar_superior(df15, h1, COLS_PROJ)
    car["fecho_hora"] = (pd.DatetimeIndex(df15["fecho_em"]).minute == 0)
    car["activo"] = df15["activo"].iloc[0]
    return car


def carregar_caracteristicas(activos, de="2020-09-01"):
    """{activo: (df15, car15, h1)} mais o BTC de 15 m em cars["_BTC15"] (para a variante com perna no BTC)."""
    t0 = time.time()
    btc = btc_1h(de=de)
    btc15 = H.carregar(REFERENCIA, de=de)
    out = {}
    for a in activos:
        df15 = H.carregar(a, de=de)
        h1 = caracteristicas_1h(df15, btc)
        out[a] = (df15, projectar(df15, h1), h1)
        print(f"  {a}: {len(df15)} velas de 15 m, {len(h1)} de 1 h alinhadas ao BTC, {time.time() - t0:.0f} s")
    out["_BTC15"] = btc15
    return out


def so_activos(cars):
    return {a: v for a, v in cars.items() if not a.startswith("_")}


# ---------------------------------------------------------------------------------------------
# Estudo de eventos por decil (no 1 h, alts em conjunto, erro padrao agrupado por bloco)
# ---------------------------------------------------------------------------------------------

def _retornos_frente(h1: pd.DataFrame, horizontes) -> pd.DataFrame:
    """Retorno log em bps da ABERTURA da vela de 1 h seguinte ate ao FECHO de k+h: do alt (r{h}) e do
    residuo a frente (rres{h} = alt menos beta_k x BTC, o que ganha a variante com perna no BTC)."""
    c = np.log(h1["close"].to_numpy(dtype=float))
    o = np.log(h1["open_px"].to_numpy(dtype=float))
    cb = np.log(h1["c_btc"].to_numpy(dtype=float))
    ob = np.log(h1["o_btc"].to_numpy(dtype=float))
    beta = h1["beta"].to_numpy(dtype=float)
    n = len(h1)
    out = pd.DataFrame(index=h1.index)
    for hz in horizontes:
        r = np.full(n, np.nan)
        rb = np.full(n, np.nan)
        if n > hz:
            r[: n - hz] = (c[hz:] - o[1 : n - hz + 1]) * 1e4
            rb[: n - hz] = (cb[hz:] - ob[1 : n - hz + 1]) * 1e4
        out[f"r{hz}"] = r
        out[f"rres{hz}"] = r - beta * rb
    return out


def _agrupado(r: np.ndarray, t: pd.DatetimeIndex, bloco_h: int):
    """Media, erro padrao ingenuo e agrupado por bloco de bloco_h horas (os alts movem-se juntos e os horizontes sobrepoem-se)."""
    n = len(r)
    if n < 2:
        return np.nan, np.nan, np.nan
    m = float(r.mean())
    ep = float(r.std(ddof=1) / np.sqrt(n))
    bl = t.floor(f"{bloco_h}h").to_numpy()
    e = pd.Series(r - m).groupby(bl).sum()
    ep_cl = float(np.sqrt((e ** 2).sum()) / n)
    return m, ep, ep_cl


def tabela_eventos(cars, periodo="IS"):
    de, ate = H.PERIODOS[periodo]
    partes = []
    for a, (_, _, h1) in so_activos(cars).items():
        rf = _retornos_frente(h1, HORIZONTES_H)
        d = h1[["beta", "z_res4", "z_res24", "z_ret24", "z_fd", "imputado"]].join(rf)
        d["activo"] = a
        d = d[(d.index >= H._utc(de)) & (d.index <= H._utc(ate))]
        partes.append(d)
    p = pd.concat(partes)
    p.index.name = "t"
    return p.reset_index()


def _decis(x: pd.Series) -> pd.Series:
    return pd.qcut(x.rank(method="first"), 10, labels=False) + 1


def estudo_decis(painel: pd.DataFrame, nome: str, coluna: str, alvo="r", excluir=()):
    """Retorno medio por decil da caracteristica, por horizonte; alvo "r" = retorno do alt, "rres" = residuo a frente."""
    d = painel[~painel["activo"].isin(excluir)].dropna(subset=[coluna]).copy()
    d["decil"] = _decis(d[coluna])
    linhas = []
    for hz in HORIZONTES_H:
        col = f"{alvo}{hz}"
        todo = d.dropna(subset=[col])
        media_global = float(todo[col].mean())
        for dec, g in todo.groupby("decil"):
            m, ep, ep_cl = _agrupado(g[col].to_numpy(dtype=float), pd.DatetimeIndex(g["t"]), hz)
            linhas.append({"caracteristica": nome, "alvo": alvo, "h": hz, "decil": int(dec), "n": len(g), "media_bps": m,
                           "excesso_bps": m - media_global, "ep_bps": ep, "ep_agrupado_bps": ep_cl,
                           "t_excesso": (m - media_global) / ep_cl if ep_cl > 0 else np.nan,
                           "frac_pos": float((g[col] > 0).mean()), "limite_inf": float(g[coluna].min()),
                           "limite_sup": float(g[coluna].max())})
    return pd.DataFrame(linhas)


def teste_contrarian(painel: pd.DataFrame, nome: str, coluna: str, limiar: float, alvo="r", filtro_fd=False, excluir=()):
    """Retorno A FAVOR do lado contrarian quando |z| >= limiar (z <= -limiar compra, z >= +limiar vende), com ep agrupado.
    O excesso subtrai a deriva do periodo multiplicada pelo lado."""
    d = painel[~painel["activo"].isin(excluir)].dropna(subset=[coluna]).copy()
    lado = np.where(d[coluna] <= -limiar, 1, np.where(d[coluna] >= limiar, -1, 0))
    if filtro_fd:
        zf = d["z_fd"].to_numpy(dtype=float)
        ok = np.where(lado > 0, zf <= LIMIAR_FILTRO_FD, np.where(lado < 0, zf >= -LIMIAR_FILTRO_FD, False))
        lado = np.where(ok & np.isfinite(zf), lado, 0)
    d["lado"] = lado
    linhas = []
    for hz in HORIZONTES_H:
        col = f"{alvo}{hz}"
        todo = d.dropna(subset=[col])
        deriva = float(todo[col].mean())
        ev = todo[todo["lado"] != 0]
        r = (ev[col] * ev["lado"]).to_numpy(dtype=float)
        m, ep, ep_cl = _agrupado(r, pd.DatetimeIndex(ev["t"]), hz)
        exc = float(((ev[col] - deriva) * ev["lado"]).mean()) if len(ev) else np.nan
        rc = (ev.loc[ev["lado"] > 0, col]).to_numpy(dtype=float)
        rv = (-ev.loc[ev["lado"] < 0, col]).to_numpy(dtype=float)
        linhas.append({"caracteristica": nome, "alvo": alvo, "limiar": limiar, "filtro_fd": int(filtro_fd), "h": hz,
                       "n": len(ev), "n_compras": int((ev["lado"] > 0).sum()), "n_vendas": int((ev["lado"] < 0).sum()),
                       "favor_bps": m, "ep_bps": ep, "ep_agrupado_bps": ep_cl,
                       "t_agrupado": m / ep_cl if ep_cl > 0 else np.nan, "excesso_deriva_bps": exc,
                       "t_excesso": exc / ep_cl if ep_cl > 0 else np.nan,
                       "favor_compras_bps": float(rc.mean()) if len(rc) else np.nan,
                       "favor_vendas_bps": float(rv.mean()) if len(rv) else np.nan,
                       "frac_pos": float((r > 0).mean()) if len(r) else np.nan})
    return pd.DataFrame(linhas)


def estudo_eventos(cars, periodo="IS", etiqueta=None):
    etiqueta = etiqueta or periodo
    painel = tabela_eventos(cars, periodo)
    os.makedirs(RESULTADOS, exist_ok=True)
    decis = pd.concat([
        estudo_decis(painel, "z_res4", "z_res4", "r"),
        estudo_decis(painel, "z_res24", "z_res24", "r"),
        estudo_decis(painel, "z_res4", "z_res4", "rres"),
        estudo_decis(painel, "z_res24", "z_res24", "rres"),
        estudo_decis(painel, "z_ret24", "z_ret24", "r"),
    ], ignore_index=True)
    decis.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_eventos_decis_{etiqueta}.csv"), index=False)
    contr = pd.concat([
        teste_contrarian(painel, "z_res4", "z_res4", 2.0, "r"),
        teste_contrarian(painel, "z_res4", "z_res4", 3.0, "r"),
        teste_contrarian(painel, "z_res24", "z_res24", 2.0, "r"),
        teste_contrarian(painel, "z_res24", "z_res24", 3.0, "r"),
        teste_contrarian(painel, "z_res4", "z_res4", 2.0, "rres"),
        teste_contrarian(painel, "z_res24", "z_res24", 2.0, "rres"),
        teste_contrarian(painel, "z_ret24", "z_ret24", 2.0, "r"),
        teste_contrarian(painel, "z_res24", "z_res24", 2.0, "r", filtro_fd=True, excluir=SEM_FUNDING),
        teste_contrarian(painel, "z_res24", "z_res24", 2.0, "r", filtro_fd=False, excluir=SEM_FUNDING),
        teste_contrarian(painel, "z_res4", "z_res4", 2.0, "r", filtro_fd=True, excluir=SEM_FUNDING),
        teste_contrarian(painel, "z_res4", "z_res4", 2.0, "r", filtro_fd=False, excluir=SEM_FUNDING),
    ], ignore_index=True)
    contr.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_eventos_contrarian_{etiqueta}.csv"), index=False)

    # por activo, z_res24 limiar 2, retorno do alt a 24 e 48 h
    por_activo = []
    for a, g in painel.groupby("activo"):
        for hz in (24, 48):
            for col_z in ("z_res4", "z_res24"):
                gg = g.dropna(subset=[col_z, f"r{hz}"])
                lado = np.where(gg[col_z] <= -2, 1, np.where(gg[col_z] >= 2, -1, 0))
                ev = gg[lado != 0]
                r = (ev[f"r{hz}"] * lado[lado != 0]).to_numpy(dtype=float)
                m, ep, ep_cl = _agrupado(r, pd.DatetimeIndex(ev["t"]), hz)
                por_activo.append({"activo": a, "z": col_z, "h": hz, "n": len(ev), "favor_bps": m, "ep_agrupado_bps": ep_cl,
                                   "t": m / ep_cl if ep_cl > 0 else np.nan, "beta_medio": float(gg["beta"].mean())})
    por_activo = pd.DataFrame(por_activo)
    por_activo.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_eventos_por_activo_{etiqueta}.csv"), index=False)

    deriva = pd.DataFrame({f"r{hz}": [painel[f"r{hz}"].mean(), painel[f"r{hz}"].median()] for hz in HORIZONTES_H},
                          index=["media_bps", "mediana_bps"])
    fmt = lambda x: f"{x:.1f}" if isinstance(x, float) else str(x)
    print(f"\nESTUDO DE EVENTOS {etiqueta}: painel de {len(painel)} horas x activo, "
          f"{painel['t'].nunique()} horas distintas, {painel['activo'].nunique()} activos")
    print("deriva do periodo (bps do alt, todas as horas):\n", deriva.to_string(float_format=fmt))
    print("beta medio por activo:\n", painel.groupby("activo")["beta"].describe()[["mean", "25%", "50%", "75%"]].round(2).to_string())
    for (nome, alvo), g in decis.groupby(["caracteristica", "alvo"], sort=False):
        print(f"\n{nome} -> {alvo}: bps por decil (excesso = decil menos media de todas as horas; ep agrupado por bloco de h horas)")
        piv = g.pivot(index="decil", columns="h", values="excesso_bps").round(1)
        piv_t = g.pivot(index="decil", columns="h", values="t_excesso").round(1)
        piv_n = g.pivot(index="decil", columns="h", values="n")
        lim = g[g["h"] == HORIZONTES_H[0]].set_index("decil")[["limite_inf", "limite_sup"]].round(2)
        piv.columns = [f"exc_{h}h" for h in piv.columns]
        piv_t.columns = [f"t_{h}h" for h in piv_t.columns]
        tab = piv.join(piv_t)
        tab["n"] = piv_n[HORIZONTES_H[0]]
        tab = tab.join(lim)
        print(tab.to_string())
    print("\nTESTE CONTRARIAN NOS EXTREMOS (z <= -limiar compra o alt, z >= +limiar vende; favor = bps a favor do lado):")
    print(contr.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print("\nPOR ACTIVO (|z| >= 2, retorno do alt a favor):")
    print(por_activo.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return painel, decis, contr


# ---------------------------------------------------------------------------------------------
# Regras, simulacao, grelha, escolha, OOS
# ---------------------------------------------------------------------------------------------

def sinais(car: pd.DataFrame, h: int, z: float, filtro: int) -> pd.Series:
    """Series +1/-1/0 ao fecho das velas de 15 m que coincidem com um fecho de 1 h."""
    zr = car[f"z_res{h}"]
    lado = pd.Series(0, index=car.index, dtype=int)
    lado[zr <= -z] = 1
    lado[zr >= z] = -1
    if filtro:
        zf = car["z_fd"]
        ok_c = (lado > 0) & (zf <= LIMIAR_FILTRO_FD)
        ok_v = (lado < 0) & (zf >= -LIMIAR_FILTRO_FD)
        lado = lado.where(ok_c | ok_v, 0)
    lado[~car["fecho_hora"]] = 0
    return lado


def simular_config(cars, h, z, stop_sigma, n_max, filtro, de, ate):
    trades = []
    ignorados = 0
    n_sinais = 0
    for a, (df15, car, _) in so_activos(cars).items():
        if filtro and a in SEM_FUNDING:
            continue
        mask = (car.index >= H._utc(de)) & (car.index <= H._utc(ate))
        s = sinais(car, h, z, filtro).where(mask, 0)
        n_sinais += int((s != 0).sum())
        if (s != 0).sum() == 0:
            continue
        t = H.simular(df15, s, stop_sigma, ALVO_R_NULO, n_max, escala=car["sigma"], alvo_em_R=True, activo=a)
        ignorados += int(t.attrs.get("ignorados", 0))
        trades.append(t)
    cols = ["activo", "sinal_em", "entrada_em", "saida_em", "lado", "entrada", "stop", "alvo", "saida", "motivo",
            "R_bruto", "R_liquido", "duracao", "duracao_h", "R_preco"]
    out = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(columns=cols)
    out.attrs["ignorados"] = ignorados
    out.attrs["n_sinais"] = n_sinais
    return out


def perna_btc(trades: pd.DataFrame, cars) -> pd.DataFrame:
    """Variante com perna no BTC: para cada trade no alt, posicao contraria em BTC de beta x nocional,
    aberta na mesma abertura e fechada ao FECHO da vela de 15 m em que o alt sai (stop ou tempo).
    Resultado em R do alt, liquido de 6,5 bps por lado sobre o nocional do BTC. Devolve o DataFrame com
    as colunas R_btc, R_liquido_so_alt e R_liquido (= alt + BTC)."""
    if len(trades) == 0:
        return trades.copy()
    btc15 = cars["_BTC15"]
    ob = btc15["open"].astype(float)
    cb = btc15["close"].astype(float)
    fecho_para_abertura = pd.Series(btc15.index, index=pd.DatetimeIndex(btc15["fecho_em"]))
    t = trades.copy()
    beta_sinal = []
    for a, g in t.groupby("activo"):
        car = cars[a][1]
        # beta lido na vela de 15 m do sinal (sinal_em e o fecho dessa vela; a abertura e 15 min antes)
        ab = pd.DatetimeIndex(pd.to_datetime(g["sinal_em"], utc=True)) - pd.Timedelta(minutes=15)
        beta_sinal.append(pd.Series(car["beta"].reindex(ab).to_numpy(), index=g.index))
    t["beta"] = pd.concat(beta_sinal).reindex(t.index)
    ent = pd.DatetimeIndex(pd.to_datetime(t["entrada_em"], utc=True))
    sai = pd.DatetimeIndex(pd.to_datetime(t["saida_em"], utc=True))
    p0 = ob.reindex(ent).to_numpy()
    ab_saida = fecho_para_abertura.reindex(sai).to_numpy()
    p1 = cb.reindex(pd.DatetimeIndex(ab_saida)).to_numpy()
    lado = t["lado"].to_numpy(dtype=float)
    beta = t["beta"].to_numpy(dtype=float)
    noc = beta * t["entrada"].to_numpy(dtype=float) / t["R_preco"].to_numpy(dtype=float)   # nocional BTC em R do alt
    ret_btc = p1 / p0 - 1.0
    custo = (2.0 * H.CUSTO_BPS / 1e4) * np.abs(noc)
    t["R_btc"] = -lado * noc * ret_btc - custo
    t["R_liquido_so_alt"] = t["R_liquido"]
    t["R_liquido"] = t["R_liquido_so_alt"] + t["R_btc"]
    t["R_bruto"] = t["R_bruto"] - lado * noc * ret_btc
    return t


def grelha(cars, periodo="IS"):
    de, ate = H.PERIODOS[periodo]
    linhas = []
    t0 = time.time()
    for h in GRELHA_H:
        for z in GRELHA_Z:
            for filtro in GRELHA_FILTRO:
                for stop_sigma in GRELHA_STOP_SIGMA:
                    for n_max in GRELHA_N_MAX:
                        tr = simular_config(cars, h, z, stop_sigma, n_max, filtro, de, ate)
                        m = H.metricas(tr, de, ate)
                        g = m["global"]
                        params = {"h": h, "z": z, "stop_sigma": stop_sigma, "n_max": n_max, "filtro": filtro}
                        H.registar_ensaio(FAMILIA, params, m, periodo=periodo,
                                          nota=f"grelha {periodo} {len(so_activos(cars))} alts" + (" (sem BNB: filtro de funding)" if filtro else ""))
                        pa = m["por_activo"]
                        linhas.append(dict(params, n=int(g["n"]), media=g["media"], ep=g["erro_padrao"], PF=g["PF"],
                                           acerto=g["taxa_acerto"], dd=g["dd_max"],
                                           R_pct=float((tr["R_preco"] / tr["entrada"]).mean() * 100) if len(tr) else np.nan,
                                           frac_stop=g["frac_stop"], n_pos=int((pa["soma"] > 0).sum()) if len(pa) else 0,
                                           n_compras=int((tr["lado"] > 0).sum()) if len(tr) else 0,
                                           media_compras=float(tr.loc[tr["lado"] > 0, "R_liquido"].mean()) if len(tr) else np.nan,
                                           media_vendas=float(tr.loc[tr["lado"] < 0, "R_liquido"].mean()) if len(tr) else np.nan))
                        print(f"  h {h} z {z} filtro {filtro} stop {stop_sigma} n_max {n_max}: n {int(g['n'])} media {g['media']:.3f} "
                              f"PF {g['PF']:.2f} n_pos {linhas[-1]['n_pos']} ({time.time() - t0:.0f} s)")
    tab = pd.DataFrame(linhas)
    os.makedirs(RESULTADOS, exist_ok=True)
    tab.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_grelha_{periodo}.csv"), index=False)
    return tab


def escolher(tab: pd.DataFrame):
    """Centro de um patamar: media alisada com os vizinhos em (z, stop, n_max) dentro do mesmo (h, filtro);
    escolhe a melhor media alisada desde que a propria media e a alisada sejam positivas e n >= 100.
    Nunca o maximo isolado."""
    chave = {(r.h, r.filtro, r.z, r.stop_sigma, r.n_max): r.media for r in tab.itertuples()}
    alis = []
    for r in tab.itertuples():
        viz = []
        for dz in (-1, 0, 1):
            for ds in (-1, 0, 1):
                for dn in (-1, 0, 1):
                    iz, is_, in_ = GRELHA_Z.index(r.z) + dz, GRELHA_STOP_SIGMA.index(r.stop_sigma) + ds, GRELHA_N_MAX.index(r.n_max) + dn
                    if 0 <= iz < len(GRELHA_Z) and 0 <= is_ < len(GRELHA_STOP_SIGMA) and 0 <= in_ < len(GRELHA_N_MAX):
                        v = chave.get((r.h, r.filtro, GRELHA_Z[iz], GRELHA_STOP_SIGMA[is_], GRELHA_N_MAX[in_]))
                        if v is not None and np.isfinite(v):
                            viz.append(v)
        alis.append(float(np.mean(viz)) if viz else np.nan)
    g = tab.copy()
    g["media_alisada"] = alis
    g = g.sort_values("media_alisada", ascending=False)
    melhor = g.iloc[0]
    ok = bool(melhor["media"] > 0 and melhor["media_alisada"] > 0 and melhor["n"] >= 100)
    cfg = dict(h=int(melhor["h"]), z=float(melhor["z"]), stop_sigma=float(melhor["stop_sigma"]),
               n_max=int(melhor["n_max"]), filtro=int(melhor["filtro"])) if ok else None
    return g, cfg


def avaliar(cars, cfg, periodo, nota, de=None, ate=None):
    if de is None:
        de, ate = H.PERIODOS[periodo]
    tr = simular_config(cars, cfg["h"], cfg["z"], cfg["stop_sigma"], cfg["n_max"], cfg["filtro"], de, ate)
    m = H.metricas(tr, de, ate)
    cr = H.criterios(tr, de, ate)
    H.registar_ensaio(FAMILIA, dict(cfg), m, periodo=periodo, nota=nota)
    os.makedirs(RESULTADOS, exist_ok=True)
    tr.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_trades_{nota.split()[0]}_{periodo}.csv"), index=False)
    return tr, m, cr


def avaliar_perna_btc(cars, tr, cfg, periodo, nota, de=None, ate=None):
    if de is None:
        de, ate = H.PERIODOS[periodo]
    th = perna_btc(tr, cars)
    m = H.metricas(th, de, ate)
    cr = H.criterios(th, de, ate)
    H.registar_ensaio(FAMILIA, dict(cfg, perna_btc=1), m, periodo=periodo, nota=nota)
    th.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_trades_{nota.split()[0]}_{periodo}.csv"), index=False)
    return th, m, cr


def nula(cars, cfg, periodo, n_por_activo=800, semente=0, de=None, ate=None):
    if de is None:
        de, ate = H.PERIODOS[periodo]
    trades = []
    for a, (df15, car, _) in so_activos(cars).items():
        s = H.sinais_aleatorios(df15, n_por_activo, semente, de, ate)
        trades.append(H.simular(df15, s, cfg["stop_sigma"], ALVO_R_NULO, cfg["n_max"], escala=car["sigma"], alvo_em_R=True, activo=a))
    tr = pd.concat(trades, ignore_index=True)
    return H.metricas(tr, de, ate)


def imprimir_avaliacao(nome, tr, m, cr):
    g = m["global"]
    print(f"\n== {nome} ==")
    if g["n"] == 0:
        print("sem trades")
        return
    print(f"n {int(g['n'])}  media {g['media']:.4f} R  ep {g['erro_padrao']:.4f}  mediana {g['mediana']:.3f}  PF {g['PF']:.3f}  "
          f"acerto {g['taxa_acerto']:.3f}  DD {g['dd_max']:.2f} R  bruto {g['R_bruto_medio']:.4f}  duracao {g['duracao_h_media']:.1f} h  "
          f"stop {g['frac_stop']:.2f}  R/preco {float((tr['R_preco'] / tr['entrada']).mean() * 100):.2f} %  "
          f"compras {int((tr['lado'] > 0).sum())} ({tr.loc[tr['lado'] > 0, 'R_liquido'].mean():.3f}) vendas {int((tr['lado'] < 0).sum())} ({tr.loc[tr['lado'] < 0, 'R_liquido'].mean():.3f})")
    print("por activo:\n", m["por_activo"][["n", "media", "erro_padrao", "soma", "PF", "taxa_acerto", "dd_max"]].round(3).to_string())
    print("por ano:\n", m["por_ano"][["n", "media", "soma", "PF"]].round(3).to_string())
    print("por metade:\n", m["por_metade"][["n", "media", "soma", "PF"]].round(3).to_string())
    print("criterios:\n", cr.to_string(), "\npassa tudo:", cr.attrs["passa_tudo"])


def _json_metr(m):
    g = m["global"]
    return {k: (None if not np.isfinite(float(v)) else round(float(v), 4)) for k, v in g.items()}


# ---------------------------------------------------------------------------------------------
# Causalidade: perturbar a cauda e confirmar que o passado nao muda
# ---------------------------------------------------------------------------------------------

def verificar_causalidade(activo="ETH", horas=200):
    btc = btc_1h(de="2024-01-01")
    df15 = H.carregar(activo, de="2024-01-01")
    h1_a = caracteristicas_1h(df15, btc)
    car_a = projectar(df15, h1_a)
    corte = df15.index[-horas * 4]
    df15_p = df15.copy()
    for c in ("open", "high", "low", "close", "funding_rate_pct"):
        df15_p.loc[df15_p.index >= corte, c] = df15_p.loc[df15_p.index >= corte, c] * 1.3
    btc_p = btc.copy()
    for c in ("c_btc", "o_btc", "r_btc", "f_btc"):
        btc_p.loc[btc_p.index >= corte, c] = btc_p.loc[btc_p.index >= corte, c] * 1.3
    h1_b = caracteristicas_1h(df15_p, btc_p)
    car_b = projectar(df15_p, h1_b)
    antes = h1_a.index < corte - pd.Timedelta(hours=1)
    dif_1h = max(float(np.nanmax(np.abs(h1_a.loc[antes, c].to_numpy(dtype=float) - h1_b.loc[antes, c].to_numpy(dtype=float)))) for c in COLS_PROJ)
    antes15 = car_a.index < corte - pd.Timedelta(hours=1)
    dif_15 = max(float(np.nanmax(np.abs(car_a.loc[antes15, c].to_numpy(dtype=float) - car_b.loc[antes15, c].to_numpy(dtype=float)))) for c in COLS_PROJ)
    print(f"causalidade {activo}: perturbar as ultimas {horas} h (preco, funding, BTC) muda o passado em no maximo {dif_1h:.3g} (1 h) e {dif_15:.3g} (15 m)")
    return dif_1h, dif_15


# ---------------------------------------------------------------------------------------------
# Fluxo completo
# ---------------------------------------------------------------------------------------------

def main(etapas):
    os.makedirs(RESULTADOS, exist_ok=True)
    resumo = {}
    if "causal" in etapas or "tudo" in etapas:
        resumo["causalidade"] = verificar_causalidade()

    cars5 = cars18 = None
    if any(e in etapas for e in ("eventos", "natural", "grelha", "oos", "tudo")):
        print("A carregar os 5 alts principais + BTC desde 2020-09 ...")
        cars5 = carregar_caracteristicas(ALTS_PRINCIPAIS)

    if "eventos" in etapas or "tudo" in etapas:
        _, decis, contr = estudo_eventos(cars5, "IS")
        resumo["eventos_IS_contrarian"] = contr.to_dict(orient="records")
        # robustez so de preco: 2020-09 a 2022-12 (o filtro de funding nao se usa aqui)
        H.PERIODOS["PRE"] = ("2020-09-01", "2022-12-31 23:59:59")
        _, _, contr_pre = estudo_eventos(cars5, "PRE")
        resumo["eventos_PRE_contrarian"] = contr_pre.to_dict(orient="records")

    if "natural" in etapas or "tudo" in etapas:
        tr, m, cr = avaliar(cars5, REGRA_NATURAL, "IS", "natural IS 5 alts (regra fixada a priori)")
        imprimir_avaliacao("REGRA NATURAL no IS (5 alts)", tr, m, cr)
        resumo["natural_IS"] = _json_metr(m)
        mn = nula(cars5, REGRA_NATURAL, "IS")
        print(f"nula com o mesmo stop e n_max no IS: media {mn['global']['media']:.4f} R +- {mn['global']['erro_padrao']:.4f}, PF {mn['global']['PF']:.3f}, n {int(mn['global']['n'])}")
        resumo["nula_IS"] = _json_metr(mn)
        th, mh, crh = avaliar_perna_btc(cars5, tr, REGRA_NATURAL, "IS", "naturalBTC IS 5 alts com perna no BTC (variante)")
        imprimir_avaliacao("REGRA NATURAL com perna no BTC no IS (5 alts)", th, mh, crh)
        resumo["natural_pernaBTC_IS"] = _json_metr(mh)

    cfg = None
    if "grelha" in etapas or "tudo" in etapas:
        tab = grelha(cars5, "IS")
        g, cfg = escolher(tab)
        print("\nGRELHA IS ordenada pela media alisada (centro de patamar):")
        print(g[["h", "z", "filtro", "stop_sigma", "n_max", "n", "media", "ep", "PF", "acerto", "dd", "R_pct", "frac_stop", "n_pos",
                 "n_compras", "media_compras", "media_vendas", "media_alisada"]].round(4).to_string(index=False))
        print("\nESCOLHA no IS:", cfg if cfg else "NENHUMA configuracao com media e media alisada positivas e n >= 100: a familia e negativa; "
                                                    "o OOS avalia so a regra natural para registo")
        resumo["grelha"] = {"M": len(tab), "n_positivas": int((tab["media"] > 0).sum()), "melhor_media": float(tab["media"].max()),
                            "pior_media": float(tab["media"].min()), "escolha": cfg}
        with open(os.path.join(RESULTADOS, f"{FAMILIA}_escolha.json"), "w", encoding="utf-8") as f:
            json.dump({"escolha": cfg, "natural": REGRA_NATURAL}, f, ensure_ascii=False, indent=1)

    if "oos" in etapas or "tudo" in etapas:
        if cfg is None:
            p = os.path.join(RESULTADOS, f"{FAMILIA}_escolha.json")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    cfg = json.load(f).get("escolha")
        final = cfg or REGRA_NATURAL
        nota = "escolhida" if cfg else "natural"
        print(f"\nOOS avaliado UMA vez com a configuracao {nota}: {final}")
        tr, m, cr = avaliar(cars5, final, "OOS", f"{nota} OOS 5 alts (avaliacao unica)")
        imprimir_avaliacao(f"OOS 5 alts, regra {nota}", tr, m, cr)
        resumo["OOS_5"] = {"cfg": final, "metricas": _json_metr(m), "criterios": cr["passa"].to_dict(), "passa": cr.attrs["passa_tudo"],
                           "por_activo": m["por_activo"]["media"].round(4).to_dict(), "por_ano": m["por_ano"]["media"].round(4).to_dict(),
                           "por_metade": m["por_metade"]["soma"].round(3).to_dict()}
        mn = nula(cars5, final, "OOS")
        print(f"nula com o mesmo stop e n_max no OOS: media {mn['global']['media']:.4f} R +- {mn['global']['erro_padrao']:.4f}, PF {mn['global']['PF']:.3f}")
        resumo["nula_OOS"] = _json_metr(mn)
        th, mh, crh = avaliar_perna_btc(cars5, tr, final, "OOS", f"{nota}BTC OOS 5 alts com perna no BTC (variante)")
        imprimir_avaliacao(f"OOS 5 alts com perna no BTC, regra {nota}", th, mh, crh)
        resumo["OOS_5_pernaBTC"] = _json_metr(mh)

        # versao so de preco (sem filtro) nos 17 alts: IS (registo), OOS (avaliacao unica declarada a priori) e 2020-2022
        preco = dict(final, filtro=0)
        print("\nA carregar os 17 alts + BTC desde 2020-09 (versao so de preco) ...")
        cars18 = carregar_caracteristicas(ALTS_18)
        tr18, m18, cr18 = avaliar(cars18, preco, "IS", f"{nota}18 IS 17 alts so preco (sem filtro)")
        imprimir_avaliacao("IS 17 alts, so preco", tr18, m18, cr18)
        resumo["IS_17"] = _json_metr(m18)
        tr18o, m18o, cr18o = avaliar(cars18, preco, "OOS", f"{nota}18 OOS 17 alts so preco (avaliacao unica)")
        imprimir_avaliacao("OOS 17 alts, so preco", tr18o, m18o, cr18o)
        resumo["OOS_17"] = {"metricas": _json_metr(m18o), "criterios": cr18o["passa"].to_dict(), "passa": cr18o.attrs["passa_tudo"],
                            "por_activo": m18o["por_activo"]["media"].round(4).to_dict()}
        H.PERIODOS["PRE"] = ("2020-09-01", "2022-12-31 23:59:59")
        trp, mp, crp = avaliar(cars18, preco, "PRE", f"{nota}18 PRE 2020-09 a 2022-12, 17 alts so preco")
        imprimir_avaliacao("2020-09 a 2022-12, 17 alts, so preco", trp, mp, crp)
        resumo["PRE_17"] = {"metricas": _json_metr(mp), "por_ano": mp["por_ano"]["media"].round(4).to_dict() if len(mp["por_ano"]) else {}}
        trp5, mp5, crp5 = avaliar(cars5, preco, "PRE", f"{nota} PRE 2020-09 a 2022-12, 5 alts so preco")
        imprimir_avaliacao("2020-09 a 2022-12, 5 alts, so preco", trp5, mp5, crp5)
        resumo["PRE_5"] = _json_metr(mp5)

    resumo["M"] = H.contar_ensaios(FAMILIA)
    with open(os.path.join(RESULTADOS, f"{FAMILIA}_resumo.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nM = {resumo['M']} configuracoes registadas em ensaios/{FAMILIA}.csv")


if __name__ == "__main__":
    main(sys.argv[1:] or ["tudo"])
