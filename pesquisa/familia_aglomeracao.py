# -*- coding: utf-8 -*-
"""Familia AGLOMERACAO EM PERPETUOS: funding extremo, open interest a crescer com o preco parado ou a
cair, ls_ratio_top extremo, basis extremo.

Hipotese: posicionamento concentrado de um lado paga-se em funding (ou ve-se no OI, no racio
long/short dos grandes e no basis) e reverte em 4 a 48 h quando a multidao e espremida. Horizontes
mais longos do que as outras familias: sinal no fecho de 1 h, saida por tempo de 1 a 3 dias, stop em
multiplos de sigma diaria.

Ordem de trabalho (obrigatoria): caracteristicas causais -> estudo de eventos por DECIL no IS, com
erros padrao agrupados por bloco temporal -> grelha pequena e declarada (48 configuracoes), toda
registada -> escolha no IS pelo centro de um patamar (nunca o maximo) -> OOS avaliado UMA vez ->
relatorio. A familia depende de metricas (funding, OI, ls_ratio, basis), por isso NAO corre em
2020-2022 (87 % imputado em 2022; alts imputados em 2020-2021) e corre so nos 6 activos principais.

Caracteristicas (todas na vela de 1 h FECHADA, projectadas ao 15 m por harness.projectar_superior):
  z_f    z robusto do funding (8 h, %) sobre 30 dias (720 velas de 1 h), escala minima 0,002 %
  p_f    percentil do funding em 90 dias (2160 velas de 1 h)
  z_b    z robusto do basis (perp - spot, em bps do preco) sobre 30 dias
  z_oi   z robusto da variacao do OI em 24 h (soma de oi_change_pct, em contratos) sobre 30 dias
  ret24  retorno log de 24 h (para saber se o OI cresce com o preco a cair ou a subir)
  z_l    z robusto do ls_ratio_top sobre 30 dias (nunca o nivel, que nao e estacionario)
  sigma  sigma diaria em preco: vol EWMA (meia-vida 7 dias) dos retornos de 1 h x sqrt(24) x close
Metricas so em velas nao imputadas (harness.mascarar_imputado). O funding do BNB nao e fiavel (54 %
de zeros), por isso o BNB fica fora das regras F e P.

Regras (sinal no fecho de 1 h; entrada na abertura do 15 m seguinte; so uma posicao por activo):
  "F"  z_f >= Z -> venda (multidao longa paga funding); z_f <= -Z -> compra.          Z em {2, 3}
  "P"  p_f >= P -> venda; p_f <= 1 - P -> compra.                                     P em {0,95, 0,98}
  "O"  z_oi >= Z e ret24 <= 0 -> compra (posicoes novas contra a queda = shorts a aglomerar, squeeze);
       z_oi >= Z e ret24 > 0 -> venda (longs novos a perseguir).                       Z em {1,5, 2,5}
  "L"  z_l >= Z -> venda (grandes contas muito longas); z_l <= -Z -> compra.           Z em {2, 3}
Stop: M x sigma diaria, M em {1,0, 1,5}. Sem alvo (so tempo e stop). Saida por tempo ao fecho de
N_MAX velas de 15 m, N_MAX em {96, 192, 288} = 1, 2, 3 dias. Grelha: 4 x 2 x 2 x 3 = 48.

Correr: python familia_aglomeracao.py eventos | natural | grelha | oos | tudo
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

FAMILIA = "aglomeracao"
RESULTADOS = os.path.join(H.AQUI, "resultados")
RELATORIOS = os.path.join(H.AQUI, "relatorios")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)

ACTIVOS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
SEM_FUNDING = {"BNB"}           # funding do BNB: 54 % zeros, 1,7 mudancas por dia (validar_colunas)

# Parametros FIXOS, declarados antes de olhar para trades (nao entram na grelha)
JANELA_Z_1H = 720               # 30 dias de velas de 1 h
JANELA_PCT_1H = 2160            # 90 dias
MIN_PERIODOS_Z = 360            # 15 dias
MIN_PERIODOS_PCT = 1080         # 45 dias
ESCALA_MIN_FUNDING = 0.002      # % por 8 h (o funding fica preso em 0,01 %: MAD ~ 0)
ESCALA_MIN_BASIS_BPS = 1.0      # bps
ESCALA_MIN_LS = 0.01
ESCALA_MIN_OI = 0.05            # pontos percentuais de variacao de OI em 24 h
JANELA_OI_H = 24
MEIA_VIDA_VOL_H = 168           # 7 dias
HORIZONTES_H = [4, 24, 48, 72]  # horas, para o estudo de eventos
ALVO_R_NULO = 1000.0            # sem alvo: so stop e tempo

REGRAS = ["F", "P", "O", "L"]
LIMIARES = {"F": [2.0, 3.0], "P": [0.95, 0.98], "O": [1.5, 2.5], "L": [2.0, 3.0]}
GRELHA_STOP_SIGMA = [1.0, 1.5]
GRELHA_N_MAX = [96, 192, 288]

# REGRA NATURAL, fixada A PRIORI: funding extremo (a caracteristica que da nome a hipotese), z >= 2
# sobre 30 dias, stop 1,5 sigma diaria, saida por tempo a 48 h. E o centro da grelha da regra F. Se o
# estudo de eventos for negativo, e a UNICA configuracao avaliada no OOS.
REGRA_NATURAL = {"regra": "F", "limiar": 2.0, "stop_sigma": 1.5, "n_max": 192}


# ---------------------------------------------------------------------------------------------
# Caracteristicas (causais: velas de 1 h fechadas, janelas so para tras, metricas sem imputados)
# ---------------------------------------------------------------------------------------------

def caracteristicas_1h(df15: pd.DataFrame) -> pd.DataFrame:
    """Velas de 1 h completas com as caracteristicas da familia (indice = abertura da vela de 1 h)."""
    h1 = H.agregar(df15, "1h").copy()
    c = h1["close"].astype(float)
    f = H.mascarar_imputado(h1, "funding_rate_pct")
    h1["z_f"] = H.z_robusto(f, JANELA_Z_1H, MIN_PERIODOS_Z, escala_min=ESCALA_MIN_FUNDING)
    h1["p_f"] = H.percentil_movel(f, JANELA_PCT_1H, MIN_PERIODOS_PCT)
    basis_bps = H.mascarar_imputado(h1, "basis_usd") / c * 1e4
    h1["z_b"] = H.z_robusto(basis_bps, JANELA_Z_1H, MIN_PERIODOS_Z, escala_min=ESCALA_MIN_BASIS_BPS)
    oi = H.mascarar_imputado(h1, "oi_change_pct")
    oi24 = oi.rolling(JANELA_OI_H, min_periods=JANELA_OI_H).sum()
    h1["oi24"] = oi24
    h1["z_oi"] = H.z_robusto(oi24, JANELA_Z_1H, MIN_PERIODOS_Z, escala_min=ESCALA_MIN_OI)
    h1["ret24"] = H.retorno_log(c, JANELA_OI_H)
    ls = H.mascarar_imputado(h1, "ls_ratio_top")
    h1["z_l"] = H.z_robusto(ls, JANELA_Z_1H, MIN_PERIODOS_Z, escala_min=ESCALA_MIN_LS)
    r1 = H.retorno_log(c, 1)
    h1["sigma"] = H.vol_realizada_ewma(r1, MEIA_VIDA_VOL_H) * np.sqrt(24.0) * c
    h1["open_px"] = h1["open"].astype(float)
    return h1


def projectar(df15: pd.DataFrame, h1: pd.DataFrame) -> pd.DataFrame:
    """Leva as caracteristicas de 1 h a grelha de 15 m (so velas de 1 h ja fechadas) e marca os fechos de hora."""
    cols = ["z_f", "p_f", "z_b", "z_oi", "ret24", "z_l", "sigma"]
    car = H.projectar_superior(df15, h1, cols)
    car["fecho_hora"] = (pd.DatetimeIndex(df15["fecho_em"]).minute == 0)
    car["activo"] = df15["activo"].iloc[0]
    return car


def carregar_caracteristicas(activos, de="2022-10-01"):
    """{activo: (df15, car15, h1)}; comeca em 2022-10 para aquecer as janelas (tudo imputado ate Dez-2022)."""
    t0 = time.time()
    out = {}
    for a in activos:
        df15 = H.carregar(a, de=de)
        h1 = caracteristicas_1h(df15)
        out[a] = (df15, projectar(df15, h1), h1)
        print(f"  {a}: {len(df15)} velas de 15 m, {len(h1)} de 1 h, {time.time() - t0:.0f} s")
    return out


# ---------------------------------------------------------------------------------------------
# Estudo de eventos por decil (no 1 h, 6 activos em conjunto, erro padrao agrupado por bloco)
# ---------------------------------------------------------------------------------------------

def _retornos_frente(h1: pd.DataFrame, horizontes) -> pd.DataFrame:
    """Retorno log em bps da ABERTURA da vela de 1 h seguinte ate ao FECHO de k+h, para cada h."""
    c = np.log(h1["close"].to_numpy(dtype=float))
    o = np.log(h1["open_px"].to_numpy(dtype=float))
    n = len(h1)
    out = pd.DataFrame(index=h1.index)
    for hz in horizontes:
        r = np.full(n, np.nan)
        if n > hz:
            r[: n - hz] = (c[hz:] - o[1 : n - hz + 1]) * 1e4
        out[f"r{hz}"] = r
    return out


def _agrupado(r: np.ndarray, t: pd.DatetimeIndex, bloco_h: int):
    """Media, erro padrao ingenuo e agrupado por bloco de bloco_h horas (activos movem-se juntos; horizontes sobrepostos)."""
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
    """Painel de 1 h dos 6 activos no periodo, com caracteristicas e retornos a frente."""
    de, ate = H.PERIODOS[periodo]
    partes = []
    for a, (_, _, h1) in cars.items():
        rf = _retornos_frente(h1, HORIZONTES_H)
        d = h1[["z_f", "p_f", "z_b", "z_oi", "ret24", "z_l", "imputado"]].join(rf)
        d["activo"] = a
        d = d[(d.index >= H._utc(de)) & (d.index <= H._utc(ate))]
        partes.append(d)
    p = pd.concat(partes)
    p.index.name = "t"
    return p.reset_index()


def _decis(x: pd.Series) -> pd.Series:
    """Decis 1..10 com desempate por ordem (o z do funding tem milhares de zeros exactos: funding preso em
    0,01 %; os empates ficam nos decis centrais e nao afectam os extremos)."""
    return pd.qcut(x.rank(method="first"), 10, labels=False) + 1


def estudo_decis(painel: pd.DataFrame, nome: str, coluna: str, filtro=None, excluir=()):
    """Retorno medio por decil da caracteristica (decis no painel inteiro do periodo), por horizonte."""
    d = painel[~painel["activo"].isin(excluir)].dropna(subset=[coluna]).copy()
    d["decil"] = _decis(d[coluna])
    if filtro is not None:
        d = d[filtro(d)]
    linhas = []
    for hz in HORIZONTES_H:
        col = f"r{hz}"
        todo = d.dropna(subset=[col])
        media_global = float(todo[col].mean())
        for dec, g in todo.groupby("decil"):
            m, ep, ep_cl = _agrupado(g[col].to_numpy(dtype=float), pd.DatetimeIndex(g["t"]), hz)
            linhas.append({"caracteristica": nome, "h": hz, "decil": int(dec), "n": len(g), "media_bps": m,
                           "excesso_bps": m - media_global, "ep_bps": ep, "ep_agrupado_bps": ep_cl,
                           "t_excesso": (m - media_global) / ep_cl if ep_cl > 0 else np.nan,
                           "frac_pos": float((g[col] > 0).mean()), "limite_inf": float(g[coluna].min()),
                           "limite_sup": float(g[coluna].max())})
    return pd.DataFrame(linhas)


def teste_contrarian(painel: pd.DataFrame, nome: str, coluna: str, lado_fn, excluir=()):
    """Retorno A FAVOR do lado contrarian nos extremos (decil 1 e 10), por horizonte, com ep agrupado.

    lado_fn(d) devolve +1/-1/0 por linha (0 = fora dos extremos). O excesso subtrai a deriva do
    periodo multiplicada pelo lado (uma regra que so vende perde a deriva do bull market).
    """
    d = painel[~painel["activo"].isin(excluir)].dropna(subset=[coluna]).copy()
    d["decil"] = _decis(d[coluna])
    d["lado"] = lado_fn(d)
    linhas = []
    for hz in HORIZONTES_H:
        col = f"r{hz}"
        todo = d.dropna(subset=[col])
        deriva = float(todo[col].mean())
        ev = todo[todo["lado"] != 0]
        r = (ev[col] * ev["lado"]).to_numpy(dtype=float)
        m, ep, ep_cl = _agrupado(r, pd.DatetimeIndex(ev["t"]), hz)
        exc = float(((ev[col] - deriva) * ev["lado"]).mean()) if len(ev) else np.nan
        linhas.append({"caracteristica": nome, "h": hz, "n": len(ev), "n_compras": int((ev["lado"] > 0).sum()),
                       "n_vendas": int((ev["lado"] < 0).sum()), "favor_bps": m, "ep_bps": ep, "ep_agrupado_bps": ep_cl,
                       "t_agrupado": m / ep_cl if ep_cl > 0 else np.nan, "excesso_deriva_bps": exc,
                       "t_excesso": exc / ep_cl if ep_cl > 0 else np.nan,
                       "frac_pos": float((r > 0).mean()) if len(r) else np.nan})
    return pd.DataFrame(linhas)


def estudo_eventos(cars, periodo="IS"):
    painel = tabela_eventos(cars, periodo)
    os.makedirs(RESULTADOS, exist_ok=True)
    decis = pd.concat([
        estudo_decis(painel, "funding_z30d", "z_f", excluir=SEM_FUNDING),
        estudo_decis(painel, "funding_pct90d", "p_f", excluir=SEM_FUNDING),
        estudo_decis(painel, "basis_z30d", "z_b"),
        estudo_decis(painel, "ls_top_z30d", "z_l"),
        estudo_decis(painel, "oi24_z30d_preco_cai", "z_oi", filtro=lambda d: d["ret24"] <= 0),
        estudo_decis(painel, "oi24_z30d_preco_sobe", "z_oi", filtro=lambda d: d["ret24"] > 0),
    ], ignore_index=True)
    decis.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_eventos_decis_{periodo}.csv"), index=False)

    def extremos(d):
        return np.where(d["decil"] == 10, -1, np.where(d["decil"] == 1, 1, 0))

    def oi_lado(d):
        topo = d["decil"] == 10
        return np.where(topo & (d["ret24"] <= 0), 1, np.where(topo & (d["ret24"] > 0), -1, 0))

    contr = pd.concat([
        teste_contrarian(painel, "funding_z30d", "z_f", extremos, excluir=SEM_FUNDING),
        teste_contrarian(painel, "funding_pct90d", "p_f", extremos, excluir=SEM_FUNDING),
        teste_contrarian(painel, "basis_z30d", "z_b", extremos),
        teste_contrarian(painel, "ls_top_z30d", "z_l", extremos),
        teste_contrarian(painel, "oi24_z30d", "z_oi", oi_lado),
    ], ignore_index=True)
    contr.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_eventos_contrarian_{periodo}.csv"), index=False)

    # deriva do periodo por horizonte (todas as horas), para contexto
    deriva = pd.DataFrame({f"r{hz}": [painel[f"r{hz}"].mean(), painel[f"r{hz}"].median()] for hz in HORIZONTES_H},
                          index=["media_bps", "mediana_bps"])
    fmt = lambda x: f"{x:.1f}" if isinstance(x, float) else str(x)
    print(f"\nESTUDO DE EVENTOS {periodo}: painel de {len(painel)} horas x activo, "
          f"{painel['t'].nunique()} horas distintas, {painel['activo'].nunique()} activos")
    print("deriva do periodo (bps, todas as horas):\n", deriva.to_string(float_format=fmt))
    for nome, g in decis.groupby("caracteristica", sort=False):
        print(f"\n{nome}: retorno bruto em bps por decil (excesso = decil menos media de todas as horas; ep agrupado por bloco de h horas)")
        piv = g.pivot(index="decil", columns="h", values="excesso_bps").round(1)
        piv_t = g.pivot(index="decil", columns="h", values="t_excesso").round(1)
        piv_n = g.pivot(index="decil", columns="h", values="n")
        lim = g[g["h"] == HORIZONTES_H[0]].set_index("decil")[["limite_inf", "limite_sup"]].round(3)
        piv.columns = [f"exc_{h}h" for h in piv.columns]
        piv_t.columns = [f"t_{h}h" for h in piv_t.columns]
        tab = piv.join(piv_t)
        tab["n"] = piv_n[HORIZONTES_H[0]]
        tab = tab.join(lim)
        print(tab.to_string())
    print("\nTESTE CONTRARIAN NOS EXTREMOS (decil 1 compra, decil 10 vende; OI: decil 10 com preco a cair compra, a subir vende):")
    print(contr.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return painel, decis, contr


# ---------------------------------------------------------------------------------------------
# Regras, simulacao, grelha, escolha, OOS
# ---------------------------------------------------------------------------------------------

def sinais(car: pd.DataFrame, regra: str, limiar: float) -> pd.Series:
    """Series +1/-1/0 ao fecho das velas de 15 m que coincidem com um fecho de 1 h."""
    lado = pd.Series(0, index=car.index, dtype=int)
    if regra == "F":
        lado[car["z_f"] >= limiar] = -1
        lado[car["z_f"] <= -limiar] = 1
    elif regra == "P":
        lado[car["p_f"] >= limiar] = -1
        lado[car["p_f"] <= 1.0 - limiar] = 1
    elif regra == "O":
        forte = car["z_oi"] >= limiar
        lado[forte & (car["ret24"] <= 0)] = 1
        lado[forte & (car["ret24"] > 0)] = -1
    elif regra == "L":
        lado[car["z_l"] >= limiar] = -1
        lado[car["z_l"] <= -limiar] = 1
    elif regra in ("F_inv", "O_inv"):
        # sinal INVERTIDO (seguir a multidao / ressalto apos desalavancagem): so para registo no IS, depois
        # de o estudo de eventos ter mostrado o sinal ao contrario; NUNCA candidato ao OOS nesta familia
        lado = -sinais(car, regra[0], limiar)
        return lado
    else:
        raise ValueError(regra)
    lado[~car["fecho_hora"]] = 0
    return lado


def simular_config(cars, regra, limiar, stop_sigma, n_max, de, ate):
    trades = []
    ignorados = 0
    n_sinais = 0
    for a, (df15, car, _) in cars.items():
        if regra[0] in ("F", "P") and a in SEM_FUNDING:
            continue
        mask = (car.index >= H._utc(de)) & (car.index <= H._utc(ate))
        s = sinais(car, regra, limiar).where(mask, 0)
        n_sinais += int((s != 0).sum())
        if (s != 0).sum() == 0:
            continue
        t = H.simular(df15, s, stop_sigma, ALVO_R_NULO, n_max, escala=car["sigma"], alvo_em_R=True, activo=a)
        ignorados += int(t.attrs.get("ignorados", 0))
        trades.append(t)
    if not trades:
        out = pd.DataFrame(columns=["activo", "sinal_em", "entrada_em", "saida_em", "lado", "entrada", "stop", "alvo",
                                    "saida", "motivo", "R_bruto", "R_liquido", "duracao", "duracao_h", "R_preco"])
    else:
        out = pd.concat(trades, ignore_index=True)
    out.attrs["ignorados"] = ignorados
    out.attrs["n_sinais"] = n_sinais
    return out


def grelha(cars, periodo="IS"):
    de, ate = H.PERIODOS[periodo]
    linhas = []
    t0 = time.time()
    for regra in REGRAS:
        for limiar in LIMIARES[regra]:
            for stop_sigma in GRELHA_STOP_SIGMA:
                for n_max in GRELHA_N_MAX:
                    tr = simular_config(cars, regra, limiar, stop_sigma, n_max, de, ate)
                    m = H.metricas(tr, de, ate)
                    g = m["global"]
                    params = {"regra": regra, "limiar": limiar, "stop_sigma": stop_sigma, "n_max": n_max}
                    H.registar_ensaio(FAMILIA, params, m, periodo=periodo, nota=f"grelha {periodo} {len(cars)} activos")
                    pa = m["por_activo"]
                    linhas.append(dict(params, n=int(g["n"]), media=g["media"], ep=g["erro_padrao"], PF=g["PF"],
                                       acerto=g["taxa_acerto"], dd=g["dd_max"], R_pct=float((tr["R_preco"] / tr["entrada"]).mean() * 100) if len(tr) else np.nan,
                                       frac_stop=g["frac_stop"], n_pos=int((pa["soma"] > 0).sum()) if len(pa) else 0,
                                       n_compras=int((tr["lado"] > 0).sum()) if len(tr) else 0,
                                       media_compras=float(tr.loc[tr["lado"] > 0, "R_liquido"].mean()) if len(tr) else np.nan,
                                       media_vendas=float(tr.loc[tr["lado"] < 0, "R_liquido"].mean()) if len(tr) else np.nan))
                    print(f"  {regra} {limiar} stop {stop_sigma} n_max {n_max}: n {int(g['n'])} media {g['media']:.3f} "
                          f"PF {g['PF']:.2f} n_pos {linhas[-1]['n_pos']} ({time.time() - t0:.0f} s)")
    tab = pd.DataFrame(linhas)
    os.makedirs(RESULTADOS, exist_ok=True)
    tab.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_grelha_{periodo}.csv"), index=False)
    return tab


def escolher(tab: pd.DataFrame, regra: str):
    """Centro de um patamar: para cada configuracao da regra, a media alisada com os vizinhos (limiar, stop,
    n_max adjacentes); escolhe a que tem melhor media alisada, desde que a propria media e a alisada
    sejam positivas. Nunca o maximo isolado."""
    g = tab[tab["regra"] == regra].copy()
    lims, stops, ns = LIMIARES[regra], GRELHA_STOP_SIGMA, GRELHA_N_MAX
    chave = {(r.limiar, r.stop_sigma, r.n_max): r.media for r in g.itertuples()}
    alis = []
    for r in g.itertuples():
        viz = []
        for dl in (-1, 0, 1):
            for ds in (-1, 0, 1):
                for dn in (-1, 0, 1):
                    il, is_, in_ = lims.index(r.limiar) + dl, stops.index(r.stop_sigma) + ds, ns.index(r.n_max) + dn
                    if 0 <= il < len(lims) and 0 <= is_ < len(stops) and 0 <= in_ < len(ns):
                        v = chave.get((lims[il], stops[is_], ns[in_]))
                        if v is not None and np.isfinite(v):
                            viz.append(v)
        alis.append(float(np.mean(viz)) if viz else np.nan)
    g["media_alisada"] = alis
    g = g.sort_values("media_alisada", ascending=False)
    melhor = g.iloc[0]
    ok = bool(melhor["media"] > 0 and melhor["media_alisada"] > 0)
    return g, (dict(regra=regra, limiar=float(melhor["limiar"]), stop_sigma=float(melhor["stop_sigma"]), n_max=int(melhor["n_max"])) if ok else None)


def avaliar(cars, cfg, periodo, nota):
    de, ate = H.PERIODOS[periodo]
    tr = simular_config(cars, cfg["regra"], cfg["limiar"], cfg["stop_sigma"], cfg["n_max"], de, ate)
    m = H.metricas(tr, de, ate)
    cr = H.criterios(tr, de, ate)
    H.registar_ensaio(FAMILIA, dict(cfg), m, periodo=periodo, nota=nota)
    os.makedirs(RESULTADOS, exist_ok=True)
    tr.to_csv(os.path.join(RESULTADOS, f"{FAMILIA}_trades_{nota.split()[0]}_{periodo}.csv"), index=False)
    return tr, m, cr


def nula(cars, cfg, periodo, n_por_activo=800, semente=0):
    """Sinais aleatorios com o mesmo stop e n_max: a esperanca de uma regra sem vantagem."""
    de, ate = H.PERIODOS[periodo]
    trades = []
    for a, (df15, car, _) in cars.items():
        s = H.sinais_aleatorios(df15, n_por_activo, semente, de, ate)
        trades.append(H.simular(df15, s, cfg["stop_sigma"], ALVO_R_NULO, cfg["n_max"], escala=car["sigma"], alvo_em_R=True, activo=a))
    tr = pd.concat(trades, ignore_index=True)
    return H.metricas(tr, de, ate)


def imprimir_avaliacao(nome, tr, m, cr):
    g = m["global"]
    print(f"\n== {nome} ==")
    print(f"n {int(g['n'])}  media {g['media']:.4f} R  ep {g['erro_padrao']:.4f}  mediana {g['mediana']:.3f}  PF {g['PF']:.3f}  "
          f"acerto {g['taxa_acerto']:.3f}  DD {g['dd_max']:.2f} R  bruto {g['R_bruto_medio']:.4f}  duracao {g['duracao_h_media']:.1f} h  "
          f"stop {g['frac_stop']:.2f}")
    if len(tr):
        print(f"R medio em % do preco: {(tr['R_preco'] / tr['entrada']).mean() * 100:.2f};  compras {int((tr['lado'] > 0).sum())} "
              f"(media {tr.loc[tr['lado'] > 0, 'R_liquido'].mean():.3f}), vendas {int((tr['lado'] < 0).sum())} "
              f"(media {tr.loc[tr['lado'] < 0, 'R_liquido'].mean():.3f}); sinais horarios {tr.attrs.get('n_sinais', 'n/d')}, ignorados por posicao aberta {tr.attrs.get('ignorados', 'n/d')}")
        print("por activo:\n", m["por_activo"][["n", "media", "erro_padrao", "soma", "PF", "taxa_acerto", "dd_max"]].to_string(float_format=lambda x: f"{x:.3f}"))
        print("por metade:\n", m["por_metade"][["n", "media", "soma", "PF"]].to_string(float_format=lambda x: f"{x:.3f}"))
        print("por ano:\n", m["por_ano"][["n", "media", "soma", "PF"]].to_string(float_format=lambda x: f"{x:.3f}"))
    print("criterios:\n", cr.to_string(float_format=lambda x: f"{x:.3f}"), "\npassa tudo:", cr.attrs["passa_tudo"])


def main(etapa="tudo"):
    print("A carregar caracteristicas (6 activos)...")
    cars = carregar_caracteristicas(ACTIVOS)
    estado = {}
    if etapa in ("eventos", "tudo"):
        painel, decis, contr = estudo_eventos(cars, "IS")
        estado["contr"] = contr
    if etapa in ("natural", "tudo"):
        tr, m, cr = avaliar(cars, REGRA_NATURAL, "IS", "natural IS (fixada a priori)")
        imprimir_avaliacao("REGRA NATURAL no IS", tr, m, cr)
        mn = nula(cars, REGRA_NATURAL, "IS")
        gn = mn["global"]
        print(f"NULA com o mesmo stop e n_max (IS, 6 x 800 sinais ao acaso): media {gn['media']:.4f} R  ep {gn['erro_padrao']:.4f}  "
              f"PF {gn['PF']:.3f}  acerto {gn['taxa_acerto']:.3f}  stop {gn['frac_stop']:.2f}")
    if etapa in ("grelha", "tudo"):
        tab = grelha(cars, "IS")
        # Diagnostico IS-only, FORA da grelha e fora da escolha: as duas regras com o sinal invertido
        # (o estudo de eventos mostrou funding alto seguido de subida e OI a cair seguido de subida).
        # Registam-se para que o M conte tudo o que se olhou; nao vao ao OOS.
        for cfg_inv in ({"regra": "F_inv", "limiar": 2.0, "stop_sigma": 1.5, "n_max": 192},
                        {"regra": "O_inv", "limiar": 1.5, "stop_sigma": 1.5, "n_max": 192}):
            tr_i, m_i, cr_i = avaliar(cars, cfg_inv, "IS", f"{cfg_inv['regra']} diagnostico IS (sinal invertido; nao candidato ao OOS)")
            imprimir_avaliacao(f"DIAGNOSTICO IS sinal invertido {cfg_inv}", tr_i, m_i, cr_i)
        print("\nGRELHA IS (48 configuracoes):\n", tab.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
        for regra in REGRAS:
            g, esc = escolher(tab, regra)
            print(f"\nregra {regra}: patamar (media alisada com vizinhos)\n",
                  g[["limiar", "stop_sigma", "n_max", "n", "media", "PF", "n_pos", "media_alisada"]].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
            print("  escolha pelo centro do patamar:", esc)
        estado["tab"] = tab
    if etapa in ("oos", "tudo"):
        # Decisao a priori: se o estudo de eventos nao mostrou t agrupado > 2 a favor em pelo menos dois
        # horizontes consecutivos para a mesma caracteristica, a familia e negativa e vai ao OOS SO a
        # regra natural. Caso contrario vai a escolha do patamar da regra correspondente (uma so).
        cfg = dict(REGRA_NATURAL)
        razao = "estudo de eventos negativo: so a regra natural"
        if "contr" in estado and "tab" in estado:
            contr = estado["contr"]
            mapa = {"funding_z30d": "F", "funding_pct90d": "P", "oi24_z30d": "O", "ls_top_z30d": "L"}
            candidatas = []
            for nome, g in contr.groupby("caracteristica"):
                g = g.sort_values("h")
                t = g["t_agrupado"].to_numpy()
                consec = any(t[i] > 2 and t[i + 1] > 2 for i in range(len(t) - 1))
                if consec and nome in mapa:
                    candidatas.append((float(np.nanmax(t)), mapa[nome]))
            if candidatas:
                candidatas.sort(reverse=True)
                _, esc = escolher(estado["tab"], candidatas[0][1])
                if esc is not None:
                    cfg = esc
                    razao = f"estudo de eventos positivo na regra {esc['regra']}: centro do patamar"
        print(f"\nCONFIGURACAO PARA O OOS ({razao}): {cfg}")
        tr, m, cr = avaliar(cars, cfg, "OOS", "oos (avaliado uma vez)")
        imprimir_avaliacao("OOS 2025-04-01 a 2026-09-09 (UMA vez)", tr, m, cr)
        with open(os.path.join(RESULTADOS, f"{FAMILIA}_escolha.json"), "w", encoding="utf-8") as f:
            json.dump({"configuracao": cfg, "razao": razao}, f, ensure_ascii=False, indent=1)
    print(f"\nM (configuracoes registadas em ensaios/{FAMILIA}.csv): {H.contar_ensaios(FAMILIA)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tudo")
