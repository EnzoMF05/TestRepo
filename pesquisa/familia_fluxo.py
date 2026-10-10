# -*- coding: utf-8 -*-
"""Familia FLUXO TAKER: desequilibrio taker, CVD, divergencia preco/CVD, absorcao e volume climatico.

Hipotese: no extremo de um movimento o fluxo agressor (taker) esgota-se ou e absorvido pelo lado
passivo e o preco reverte. Quatro leituras do mesmo fenomeno, todas no fecho de uma vela de 15 m e
todas causais (so dados ate fecho_em[k]):

  div    DIVERGENCIA preco/CVD: a maxima da vela k fura o maximo das N velas anteriores mas o CVD
         acumulado (taker buy - taker sell, em unidades do activo) fica ABAIXO do seu maximo das N
         velas anteriores (o novo maximo nao foi feito por compradores agressores); simetrico para
         minimos. N em velas de 15 m: 16 (4 h), 32 (8 h), 96 (24 h). Lado = contra o extremo.
  abs    ABSORCAO: desequilibrio taker de 1 h (4 velas) extremo em z robusto de 30 dias (|z| >= Z)
         mas o preco quase nao andou na mesma hora (|retorno 1 h| < 0,5 sigma de 1 h): o agressor
         bateu em ordens passivas. Lado = contra o agressor.
  clim   VOLUME CLIMATICO: volume quote de 1 h no percentil P de 30 dias e movimento de 1 h de pelo
         menos 1 sigma. Lado = contra o movimento.
  des4h  DESEQUILIBRIO de 4 h (16 velas) extremo em z robusto de 30 dias (|z| >= Z). Lado = contra
         o fluxo (esgotamento do agressor).

Confirmacao de preco (a mesma em todas as regras): a vela de 15 m do sinal fecha contra o movimento
(fecho < abertura para vender, fecho > abertura para comprar). O estudo de eventos mede as versoes
com e sem confirmacao; as regras usam sempre a confirmada.

Stop e alvo em ATR(14) de 1 h (Wilder), projectado causalmente ao 15 m a partir de velas de 1 h ja
fechadas; saida por tempo ao fecho da vela 96 (24 h); custo 6,5 bps por lado; uma posicao por activo.

Grelha declarada A PRIORI (48 configuracoes): 4 regras x 3 limiares x 2 stops {1,5; 3,0 ATR} x 2 alvos
{1,5; 3,0 ATR}, n_max = 96 fixo. Limiares: div N em {16, 32, 96}; abs Z em {1,5; 2,0; 2,5}; clim P em
{0,98; 0,99; 0,995}; des4h Z em {1,5; 2,0; 2,5}.
Regra natural fixada A PRIORI (vai ao OOS se nao houver patamar): div N = 16, stop 1,5 ATR, alvo
3,0 ATR, n_max 96.
Escolha no IS pelo centro de um patamar (media >= 0,15 R, PF >= 1,3, n >= 100; grupo da regra com
mais membros; dentro dele a mediana), nunca o maximo. OOS avaliado UMA vez.

Activos: os 6 principais (BTC, ETH, SOL, BNB, XRP, DOGE) sao a amostra primaria; os 18 e 2020-2022
entram como robustez porque o fluxo taker e um campo das klines (nao e metrica imputada; taker buy +
taker sell = volume_base em 100 % das velas).

Correr: python familia_fluxo.py eventos | natural | grelha | oos | tudo
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

FAMILIA = "fluxo"
RESULTADOS = os.path.join(H.AQUI, "resultados")
RELATORIOS = os.path.join(H.AQUI, "relatorios")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 500)

PRINCIPAIS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
HORIZONTES = [4, 16, 32, 96]              # 1 h, 4 h, 8 h, 24 h em velas de 15 m
N_MAX = 96                                # saida por tempo: 24 h
JANELA_Z = 2880                           # 30 dias de velas de 15 m
MIN_Z = 1440                              # 15 dias
HL_SIGMA = 96                             # meia-vida da vol de 15 m: 24 h
ESCALA_MIN_Z = 0.01                       # desequilibrio em [-1, 1]; MAD nunca abaixo de 0,01
FRAC_ABS = 0.5                            # absorcao: |ret 1 h| < 0,5 sigma 1 h
MIN_CLIM = 1.0                            # climax: |ret 1 h| >= 1 sigma 1 h

LIMIARES = {"div": [16, 32, 96], "abs": [1.5, 2.0, 2.5], "clim": [0.98, 0.99, 0.995], "des4h": [1.5, 2.0, 2.5]}
STOPS = [1.5, 3.0]
ALVOS = [1.5, 3.0]
GRELHA = [{"regra": r, "limiar": lim, "stop_atr": s, "alvo_atr": a}
          for r in LIMIARES for lim in LIMIARES[r] for s in STOPS for a in ALVOS]
assert len(GRELHA) == 48
REGRA_NATURAL = {"regra": "div", "limiar": 16, "stop_atr": 1.5, "alvo_atr": 3.0}


# ---------------------------------------------------------------------------------------------
# Caracteristicas (todas causais)
# ---------------------------------------------------------------------------------------------

def caracteristicas(df15: pd.DataFrame) -> pd.DataFrame:
    """Fluxo taker, CVD, extremos de preco e CVD, sigma e ATR de 1 h, tudo no fecho da vela de 15 m."""
    car = pd.DataFrame(index=df15.index)
    car["open_px"] = df15["open"].astype(float)
    car["high"] = df15["high"].astype(float)
    car["low"] = df15["low"].astype(float)
    car["close"] = df15["close"].astype(float)
    car["fecho_em"] = df15["fecho_em"]
    car["activo"] = df15["activo"]
    c = car["close"]
    ret = np.log(c).diff()
    sigma15 = H.vol_realizada_ewma(ret, HL_SIGMA)
    car["sigma1h"] = sigma15 * 2.0                      # sqrt(4) velas
    car["ret1h"] = H.retorno_log(c, 4)
    car["ret4h"] = H.retorno_log(c, 16)
    # fluxo taker
    car["d1h"] = H.desequilibrio_taker(df15, 4)
    car["d4h"] = H.desequilibrio_taker(df15, 16)
    car["z_d1h"] = H.z_robusto(car["d1h"], JANELA_Z, MIN_Z, escala_min=ESCALA_MIN_Z)
    car["z_d4h"] = H.z_robusto(car["d4h"], JANELA_Z, MIN_Z, escala_min=ESCALA_MIN_Z)
    car["cvd"] = H.cvd(df15)
    # volume de 1 h e percentil de 30 dias
    vol1h = df15["volume_quote"].astype(float).rolling(4, min_periods=4).sum()
    car["vol1h_pct"] = H.percentil_movel(vol1h, JANELA_Z, MIN_Z)
    # extremos de preco e de CVD das N velas anteriores (excluem a actual)
    for N in LIMIARES["div"]:
        ex = H.extremos_moveis(df15, N)
        car[f"hmax{N}"] = ex["max_anterior"]
        car[f"lmin{N}"] = ex["min_anterior"]
        car[f"cvdmax{N}"] = car["cvd"].rolling(N, min_periods=N).max().shift(1)
        car[f"cvdmin{N}"] = car["cvd"].rolling(N, min_periods=N).min().shift(1)
    # ATR(14) de 1 h, Wilder, so velas de 1 h fechadas
    h1 = H.agregar(df15, "1h")
    h1["atr1h"] = H.atr(h1, 14, "ewma")
    car["atr1h"] = H.projectar_superior(df15, h1, ["atr1h"])["atr1h"]
    return car


def carregar_caracteristicas(activos, de=None):
    cars = {}
    for a in activos:
        df = H.carregar(a, de=de)
        cars[a] = caracteristicas(df)
    return cars


# ---------------------------------------------------------------------------------------------
# Sinais (lado +1/-1/0 ao fecho de k)
# ---------------------------------------------------------------------------------------------

def lado_bruto(car: pd.DataFrame, regra: str, limiar) -> pd.Series:
    """Lado do evento SEM confirmacao de preco."""
    hi, lo = car["high"], car["low"]
    s = pd.Series(0, index=car.index, dtype=int)
    if regra == "div":
        N = int(limiar)
        baixa = (hi > car[f"hmax{N}"]) & (car["cvd"] < car[f"cvdmax{N}"])
        alta = (lo < car[f"lmin{N}"]) & (car["cvd"] > car[f"cvdmin{N}"])
        s[baixa] = -1
        s[alta & ~baixa] = 1
    elif regra == "abs":
        forte = car["z_d1h"].abs() >= float(limiar)
        parado = car["ret1h"].abs() < FRAC_ABS * car["sigma1h"]
        ev = forte & parado
        s[ev] = -np.sign(car["d1h"][ev]).astype(int)
    elif regra == "clim":
        ev = (car["vol1h_pct"] >= float(limiar)) & (car["ret1h"].abs() >= MIN_CLIM * car["sigma1h"])
        s[ev] = -np.sign(car["ret1h"][ev]).astype(int)
    elif regra == "des4h":
        ev = car["z_d4h"].abs() >= float(limiar)
        s[ev] = -np.sign(car["d4h"][ev]).astype(int)
    else:
        raise ValueError(regra)
    return s


def confirmar(car: pd.DataFrame, lado: pd.Series) -> pd.Series:
    """Confirmacao de preco: a vela do sinal fecha no sentido do trade (fecho > abertura para comprar)."""
    corpo = np.sign(car["close"] - car["open_px"])
    ok = corpo == lado
    return lado.where(ok, 0).astype(int)


def sinais(car: pd.DataFrame, regra: str, limiar, confirmado: bool = True) -> pd.Series:
    s = lado_bruto(car, regra, limiar)
    return confirmar(car, s) if confirmado else s


# ---------------------------------------------------------------------------------------------
# Estudo de eventos (antes de qualquer regra): retorno a favor do lado, erro padrao agrupado
# ---------------------------------------------------------------------------------------------

def _retornos_eventos(car: pd.DataFrame, lado: pd.Series, horizontes, de, ate, espacar=True) -> pd.DataFrame:
    """Uma linha por evento e horizonte: retorno log em bps da ABERTURA de k+1 ao FECHO de k+h, a favor
    do lado. Com ``espacar`` retem so eventos a >= h velas do anterior retido (reduz sobreposicao)."""
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
        linhas.append(pd.DataFrame({"h": hzt, "t": idx[ok], "lado": ld[ok], "r_bps": r * 1e4,
                                    "activo": car["activo"].iloc[0]}))
    return pd.concat(linhas, ignore_index=True) if linhas else pd.DataFrame()


def resumo_agrupado(df: pd.DataFrame, col="r_bps", bloco_h=None) -> pd.Series:
    """Media, erro padrao ingenuo e agrupado por bloco temporal (activos movem-se juntos e os horizontes
    sobrepoem-se), t agrupado, mediana e fraccao positiva. Bloco = max(4 h, horizonte)."""
    r = df[col].to_numpy(dtype=float)
    n = len(r)
    if n == 0:
        return pd.Series({"n": 0, "n_blocos": 0, "media_bps": np.nan, "ep_bps": np.nan,
                          "ep_agrupado_bps": np.nan, "t_agrupado": np.nan, "mediana_bps": np.nan, "frac_pos": np.nan})
    if bloco_h is None:
        hz = int(df["h"].iloc[0]) if "h" in df else 16
        bloco_h = max(4, hz // 4)
    m = r.mean()
    bloco = pd.DatetimeIndex(df["t"]).floor(f"{bloco_h}h")
    e = pd.Series(r - m).groupby(bloco.to_numpy()).sum()
    ep_cl = float(np.sqrt((e ** 2).sum()) / n)
    ep = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else np.nan
    return pd.Series({"n": n, "n_blocos": int(len(e)), "media_bps": m, "ep_bps": ep, "ep_agrupado_bps": ep_cl,
                      "t_agrupado": m / ep_cl if ep_cl > 0 else np.nan, "mediana_bps": float(np.median(r)),
                      "frac_pos": float((r > 0).mean())})


def estudo_eventos(cars, periodo="IS", nome="eventos"):
    """Para cada regra, limiar e confirmacao: retorno a favor por horizonte, total e por lado."""
    de, ate = H.PERIODOS[periodo]
    linhas = []
    for regra, lims in LIMIARES.items():
        for lim in lims:
            for conf in (False, True):
                partes = [_retornos_eventos(car, sinais(car, regra, lim, conf), HORIZONTES, de, ate)
                          for car in cars.values()]
                ev = pd.concat([p for p in partes if len(p)], ignore_index=True) if any(len(p) for p in partes) else pd.DataFrame()
                for hz in HORIZONTES:
                    sub = ev[ev["h"] == hz] if len(ev) else ev
                    base = {"regra": regra, "limiar": lim, "confirmado": conf, "h": hz}
                    res = resumo_agrupado(sub)
                    linhas.append({**base, "lado": "ambos", **res.to_dict()})
                    for ld, nome_ld in ((1, "compra"), (-1, "venda")):
                        res = resumo_agrupado(sub[sub["lado"] == ld] if len(sub) else sub)
                        linhas.append({**base, "lado": nome_ld, **res.to_dict()})
    out = pd.DataFrame(linhas)
    out.to_csv(os.path.join(RESULTADOS, f"fluxo_{nome}_{periodo}.csv"), index=False)
    return out


def estudo_decis(cars, periodo="IS", coluna="d4h", horizontes=(16, 96), nome="decis"):
    """Retorno bruto a frente por decil da caracteristica (amostragem de 4 em 4 velas para reduzir sobreposicao).
    Testa a relacao monotona: contrarian = decil 10 com retorno negativo e decil 1 positivo."""
    de, ate = H.PERIODOS[periodo]
    partes = []
    for a, car in cars.items():
        rec = car[(car.index >= H._utc(de)) & (car.index <= H._utc(ate))]
        c = np.log(rec["close"].to_numpy(dtype=float))
        o = np.log(rec["open_px"].to_numpy(dtype=float))
        n = len(rec)
        d = pd.DataFrame({"x": rec[coluna].to_numpy(dtype=float), "t": rec.index, "activo": a})
        for hz in horizontes:
            r = np.full(n, np.nan)
            if n > hz:
                r[: n - hz] = (c[hz:] - o[1: n - hz + 1]) * 1e4
            d[f"r{hz}"] = r
        partes.append(d.iloc[::4])
    painel = pd.concat(partes, ignore_index=True).dropna(subset=["x"])
    painel["decil"] = pd.qcut(painel["x"].rank(method="first"), 10, labels=False) + 1
    linhas = []
    for hz in horizontes:
        col = f"r{hz}"
        geral = painel[col].mean()
        for dc, g in painel.groupby("decil"):
            g = g.dropna(subset=[col])
            res = resumo_agrupado(g.rename(columns={col: "r_bps"}), bloco_h=max(4, hz // 4))
            linhas.append({"coluna": coluna, "h": hz, "decil": int(dc), "x_min": float(g["x"].min()),
                           "x_max": float(g["x"].max()), "n": int(res["n"]), "media_bps": res["media_bps"],
                           "excesso_bps": res["media_bps"] - geral, "ep_agrupado_bps": res["ep_agrupado_bps"],
                           "t_excesso": (res["media_bps"] - geral) / res["ep_agrupado_bps"] if res["ep_agrupado_bps"] > 0 else np.nan,
                           "frac_pos": res["frac_pos"]})
    out = pd.DataFrame(linhas)
    out.to_csv(os.path.join(RESULTADOS, f"fluxo_{nome}_{coluna}_{periodo}.csv"), index=False)
    return out


def estudo_por_activo(cars, periodo, regra, limiar, h, confirmado=True):
    de, ate = H.PERIODOS[periodo]
    linhas = []
    for a, car in cars.items():
        ev = _retornos_eventos(car, sinais(car, regra, limiar, confirmado), [h], de, ate)
        res = resumo_agrupado(ev) if len(ev) else resumo_agrupado(pd.DataFrame({"r_bps": [], "t": [], "h": []}))
        linhas.append({"activo": a, **res.to_dict()})
    return pd.DataFrame(linhas).set_index("activo")


# ---------------------------------------------------------------------------------------------
# Simulacao, grelha, escolha
# ---------------------------------------------------------------------------------------------

def _df_sim(rec: pd.DataFrame) -> pd.DataFrame:
    return rec.rename(columns={"open_px": "open"})[["open", "high", "low", "close", "fecho_em", "activo"]]


def simular_config(cars, cfg, de, ate):
    partes = []
    ign = 0
    for a, car in cars.items():
        lado = sinais(car, cfg["regra"], cfg["limiar"], True)
        rec = car[(car.index >= H._utc(de)) & (car.index <= H._utc(ate))]
        tr = H.simular(_df_sim(rec), lado.reindex(rec.index), cfg["stop_atr"], cfg["alvo_atr"], n_max=N_MAX,
                       custo_bps=H.CUSTO_BPS, escala=rec["atr1h"], alvo_em_R=False, activo=a)
        ign += tr.attrs.get("ignorados", 0)
        partes.append(tr)
    out = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
    out.attrs["ignorados"] = ign
    return out


def _params(cfg, n_activos):
    return dict(cfg, n_max=N_MAX, confirmado=True, activos=n_activos)


def avaliar(cars, cfg, periodo, nota, guardar=True, etiqueta=None, de=None, ate=None):
    if de is None:
        de, ate = H.PERIODOS[periodo]
    tr = simular_config(cars, cfg, de, ate)
    m = H.metricas(tr, de, ate)
    cr = H.criterios(tr, de, ate)
    if guardar:
        H.registar_ensaio(FAMILIA, _params(cfg, len(cars)), m, periodo=periodo, nota=nota)
        if etiqueta:
            tr.to_csv(os.path.join(RESULTADOS, f"fluxo_trades_{etiqueta}.csv"), index=False)
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


def nula(cars, cfg, periodo, n_por_activo=800, semente=0):
    """Sinais ao acaso com o mesmo stop, alvo e saida por tempo: a referencia sem vantagem."""
    de, ate = H.PERIODOS[periodo]
    partes = []
    for a, car in cars.items():
        rec = car[(car.index >= H._utc(de)) & (car.index <= H._utc(ate))]
        df = _df_sim(rec)
        s = H.sinais_aleatorios(df, n_por_activo, semente=semente)
        partes.append(H.simular(df, s, cfg["stop_atr"], cfg["alvo_atr"], N_MAX, escala=rec["atr1h"], activo=a))
    tr = pd.concat(partes, ignore_index=True)
    return tr, H.metricas(tr, de, ate)


def diagnostico_invertido(cars, cfg, periodo="IS"):
    """So no IS e so para registo: a regra natural com o sinal INVERTIDO (segue o falso extremo). Nao e candidata."""
    de, ate = H.PERIODOS[periodo]
    partes = []
    for a, car in cars.items():
        lado = -sinais(car, cfg["regra"], cfg["limiar"], True)
        rec = car[(car.index >= H._utc(de)) & (car.index <= H._utc(ate))]
        partes.append(H.simular(_df_sim(rec), lado.reindex(rec.index), cfg["stop_atr"], cfg["alvo_atr"], n_max=N_MAX,
                                custo_bps=H.CUSTO_BPS, escala=rec["atr1h"], activo=a))
    tr = pd.concat(partes, ignore_index=True)
    m = H.metricas(tr, de, ate)
    cr = H.criterios(tr, de, ate)
    H.registar_ensaio(FAMILIA, dict(_params(cfg, len(cars)), sinal="invertido"), m, periodo=periodo,
                      nota="diagnostico IS: regra natural com sinal invertido (nao e candidata)")
    tr.to_csv(os.path.join(RESULTADOS, "fluxo_trades_natural_invertido_IS6.csv"), index=False)
    return tr, m, cr


def grelha(cars, periodo="IS"):
    de, ate = H.PERIODOS[periodo]
    linhas = []
    for i, cfg in enumerate(GRELHA):
        t = time.time()
        tr = simular_config(cars, cfg, de, ate)
        m = H.metricas(tr, de, ate)
        H.registar_ensaio(FAMILIA, _params(cfg, len(cars)), m, periodo=periodo, nota="grelha")
        g = m["global"]
        n_pos = int((m["por_activo"]["soma"] > 0).sum()) if len(tr) else 0
        met = m["por_metade"]["soma"] if len(tr) else pd.Series(dtype=float)
        linhas.append(dict(cfg, n=int(g["n"]), media=g["media"], ep=g["erro_padrao"], PF=g["PF"],
                           acerto=g["taxa_acerto"], dd=g["dd_max"], n_pos=n_pos,
                           metades_pos=int((met > 0).sum()),
                           R_pct=float((tr["R_preco"] / tr["entrada"]).median() * 100) if len(tr) else np.nan,
                           R_bruto=g["R_bruto_medio"], dur_h=g["duracao_h_media"],
                           frac_stop=g["frac_stop"], frac_alvo=g["frac_alvo"]))
        print(f"  [{i + 1}/{len(GRELHA)}] {cfg} n={int(g['n'])} media={g['media']:.4f} (ep {g['erro_padrao']:.4f}) "
              f"PF={g['PF']:.3f} pos={n_pos} ({time.time() - t:.1f} s)", flush=True)
    out = pd.DataFrame(linhas)
    out.to_csv(os.path.join(RESULTADOS, f"fluxo_grelha_{periodo}.csv"), index=False)
    return out


def escolher(gr: pd.DataFrame):
    """Centro de um patamar: media >= 0,15 R, PF >= 1,3, n >= 100; a regra com mais membros; dentro dela a
    configuracao com a media MEDIANA (nunca o maximo). None se houver menos de 3 configuracoes no patamar."""
    ok = gr[(gr["media"] >= 0.15) & (gr["PF"] >= 1.3) & (gr["n"] >= 100)]
    if len(ok) < 3:
        return None
    grupo = ok.groupby("regra").size().sort_values(ascending=False)
    regra = grupo.index[0]
    cand = ok[ok["regra"] == regra].sort_values("media")
    linha = cand.iloc[len(cand) // 2]
    return {"regra": linha["regra"], "limiar": float(linha["limiar"]), "stop_atr": float(linha["stop_atr"]),
            "alvo_atr": float(linha["alvo_atr"])}


# ---------------------------------------------------------------------------------------------
# Verificacao de causalidade: perturbar a cauda e confirmar que o passado nao muda
# ---------------------------------------------------------------------------------------------

def verificar_causalidade(activo="ETH", cauda=200):
    df = H.carregar(activo, de="2024-01-01", ate="2024-06-30")
    base = caracteristicas(df)
    pert = df.copy()
    cols = ["open", "high", "low", "close", "volume_base", "volume_quote", "taker_buy_vol_btc", "taker_sell_vol_btc"]
    pert.iloc[-cauda:, [pert.columns.get_loc(c) for c in cols]] *= 1.3
    alt = caracteristicas(pert)
    num = [c for c in base.columns if c not in ("fecho_em", "activo")]
    dif = (base[num].iloc[:-cauda] - alt[num].iloc[:-cauda]).abs()
    pior = dif.max().max()
    sin_b = pd.concat([sinais(base, r, l) for r in LIMIARES for l in LIMIARES[r]], axis=1).iloc[:-cauda]
    sin_a = pd.concat([sinais(alt, r, l) for r in LIMIARES for l in LIMIARES[r]], axis=1).iloc[:-cauda]
    dif_sin = int((sin_b.to_numpy() != sin_a.to_numpy()).sum())
    return float(np.nan_to_num(pior)), dif_sin


# ---------------------------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------------------------

def main(etapa="tudo"):
    os.makedirs(RESULTADOS, exist_ok=True)
    os.makedirs(RELATORIOS, exist_ok=True)
    t0 = time.time()
    pior, dif_sin = verificar_causalidade()
    print(f"Causalidade (ETH, cauda de 200 velas x 1,3): diferenca maxima no passado {pior:.3g}; sinais alterados {dif_sin}")
    print("A carregar e a calcular caracteristicas dos 18 activos...")
    cars = carregar_caracteristicas(H.SIMBOLOS)
    cars6 = {a: cars[a] for a in PRINCIPAIS}
    print(f"caracteristicas prontas em {time.time() - t0:.0f} s")
    cols = ["regra", "limiar", "confirmado", "h", "lado", "n", "n_blocos", "media_bps", "ep_agrupado_bps", "t_agrupado",
            "mediana_bps", "frac_pos"]

    if etapa in ("eventos", "tudo"):
        print("\n### ESTUDO DE EVENTOS no IS, 6 principais (bps a favor do lado, da abertura de k+1 ao fecho de k+h; ep agrupado)")
        ev6 = estudo_eventos(cars6, "IS", "eventos6")
        print(ev6[ev6["lado"] == "ambos"][cols].round(2).to_string(index=False))
        print("\n--- por lado (confirmado)")
        print(ev6[(ev6["lado"] != "ambos") & (ev6["confirmado"])][cols].round(2).to_string(index=False))
        print("\n### ESTUDO DE EVENTOS no IS, 18 activos (so ambos os lados)")
        ev18 = estudo_eventos(cars, "IS", "eventos18")
        print(ev18[ev18["lado"] == "ambos"][cols].round(2).to_string(index=False))
        print("\n### ESTUDO DE EVENTOS em 2020-09 a 2022-12, 18 activos (confirmado)")
        H.PERIODOS["PRE"] = ("2020-09-01", "2022-12-31 23:59:59")
        evpre = estudo_eventos(cars, "PRE", "eventos18")
        print(evpre[(evpre["lado"] == "ambos") & (evpre["confirmado"])][cols].round(2).to_string(index=False))
        print("\n### DECIS do desequilibrio de 4 h (IS, 6 principais): retorno bruto a 4 h e 24 h")
        print(estudo_decis(cars6, "IS", "d4h").round(2).to_string(index=False))
        print("\n### DECIS do desequilibrio de 1 h (IS, 6 principais)")
        print(estudo_decis(cars6, "IS", "d1h").round(2).to_string(index=False))
        print("\n### POR ACTIVO: div N=16 confirmado, 24 h, IS (18 activos)")
        print(estudo_por_activo(cars, "IS", "div", 16, 96).round(2))
        print("\n### POR ACTIVO: des4h Z=2 confirmado, 4 h, IS (18 activos)")
        print(estudo_por_activo(cars, "IS", "des4h", 2.0, 16).round(2))

    if etapa in ("natural", "tudo"):
        print("\n### REGRA NATURAL (a priori) no IS")
        tr, m, cr = avaliar(cars6, REGRA_NATURAL, "IS", "regra natural, 6 principais", etiqueta="natural_IS6")
        imprimir_avaliacao("natural IS 6 principais", tr, m, cr)
        tr, m, cr = avaliar(cars, REGRA_NATURAL, "IS", "regra natural, 18 activos", etiqueta="natural_IS18")
        imprimir_avaliacao("natural IS 18 activos", tr, m, cr)
        print("\n### NULA com a mesma escala (stop 1,5 ATR, alvo 3 ATR, 96 velas), IS, 6 principais x 800")
        trn, mn = nula(cars6, REGRA_NATURAL, "IS")
        print(mn["global"].round(4))
        trn.to_csv(os.path.join(RESULTADOS, "fluxo_trades_nula_IS6.csv"), index=False)
        print("\n### DIAGNOSTICO (so IS, nao candidata): regra natural com o sinal invertido, 6 principais")
        tr, m, cr = diagnostico_invertido(cars6, REGRA_NATURAL, "IS")
        imprimir_avaliacao("natural INVERTIDA IS 6 principais", tr, m, cr)

    if etapa in ("grelha", "tudo"):
        print("\n### GRELHA no IS (48 configuracoes, 6 principais), todas registadas")
        gr = grelha(cars6, "IS")
        print(gr.round(4).to_string(index=False))
        esc = escolher(gr)
        print("\nEscolha pelo centro de um patamar:", esc if esc else "NENHUM patamar: vai a regra natural ao OOS")
        with open(os.path.join(RESULTADOS, "fluxo_escolha.json"), "w", encoding="utf-8") as f:
            json.dump({"escolha": esc, "regra_natural": REGRA_NATURAL, "n_patamar": int(((gr["media"] >= 0.15) & (gr["PF"] >= 1.3) & (gr["n"] >= 100)).sum())}, f, indent=1)

    if etapa in ("oos", "tudo"):
        p = os.path.join(RESULTADOS, "fluxo_escolha.json")
        esc = json.load(open(p, encoding="utf-8"))["escolha"] if os.path.exists(p) else None
        cfg = esc if esc else REGRA_NATURAL
        if esc and isinstance(cfg.get("limiar"), float) and cfg["regra"] == "div":
            cfg["limiar"] = int(cfg["limiar"])
        print(f"\n### OOS (UMA vez) com {cfg}")
        tr, m, cr = avaliar(cars6, cfg, "OOS", "OOS, avaliado uma vez, 6 principais", etiqueta="OOS6")
        imprimir_avaliacao("OOS 6 principais", tr, m, cr)
        tr, m, cr = avaliar(cars, cfg, "OOS", "OOS, mesma passagem, 18 activos (robustez)", etiqueta="OOS18")
        imprimir_avaliacao("OOS 18 activos (robustez)", tr, m, cr)
        print("\n### 2020-09 a 2022-12, 18 activos (robustez adicional; o fluxo taker e campo das klines)")
        tr, m, cr = avaliar(cars, cfg, "PRE", "2020-2022, 18 activos, robustez", etiqueta="PRE18",
                            de="2020-09-01", ate="2022-12-31 23:59:59")
        imprimir_avaliacao("2020-2022 18 activos", tr, m, cr)
        print(f"\nM = {H.contar_ensaios(FAMILIA)} configuracoes registadas em ensaios/{FAMILIA}.csv")
    print(f"\ntempo total {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tudo")
