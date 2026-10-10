# -*- coding: utf-8 -*-
"""Familia ESTRUTURA: o 4 h autoriza (extremo varrido ou zona extrema do intervalo), o 1 h confirma
exaustao, o 15 m da a entrada na reconquista do nivel varrido.

Ordem de trabalho (obrigatoria): caracteristicas causais -> estudo de eventos no IS, com erros
padrao agrupados -> grelha pequena e declarada, toda registada -> escolha no IS pelo centro de um
patamar -> OOS avaliado UMA vez -> 2020-2022 como robustez extra (a regra e so de preco, volume e
taker) -> relatorio.

Modos de autorizacao (o nivel vem sempre de velas superiores JA FECHADAS):
  "4h_N"     varrimento na vela de 4 h: minima abaixo do minimo das N velas de 4 h anteriores e fecho
             de volta acima (harness.varrimento em 4 h). O 15 m entra no primeiro fecho acima do nivel
             varrido dentro da vela de 4 h seguinte (16 velas de 15 m).
  "1h_N"     o mesmo na vela de 1 h, janela de 4 velas de 15 m.
  "15m4h_N"  varrimento AO VIVO no 15 m do minimo das ultimas N velas de 4 h fechadas: uma minima de
             15 m fura o nivel e, dentro de 8 velas (2 h), um fecho de 15 m volta acima dele
             (fecho anterior abaixo ou furo e reconquista na mesma vela).
Filtros (acumulativos):
  "A" so varrimento e reconquista;
  "B" A + exaustao no 1 h: alguma das ultimas 4 velas de 1 h fechadas com amplitude E volume acima do
      percentil P (janela de 168 h) e fecho no terco oposto ao varrimento; e o desequilibrio taker da
      ultima vela de 1 h a favor do lado do trade face a media das 3 anteriores (pressao a secar);
  "C" B + posicao no intervalo das ultimas 120 velas de 4 h (20 dias) nos 20 % extremos.
Stop: extremo do varrimento menos (mais) FOLGA x ATR(14) de 1 h. Alvo em multiplos de R. Saida por
tempo ao fecho de N_MAX velas de 15 m. Tudo simetrico para vendas (varrimento de maximos).

Correr: python familia_estrutura.py eventos | natural | grelha | oos | tudo
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

FAMILIA = "estrutura"
RESULTADOS = os.path.join(H.AQUI, "resultados")
RELATORIOS = os.path.join(H.AQUI, "relatorios")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 300)

# Parametros FIXOS (declarados antes de olhar para os trades; nao entram na grelha)
P_EXAUSTAO = 0.80         # percentil de amplitude e de volume da vela de 1 h
JANELA_PCT_1H = 168       # 7 dias de velas de 1 h para o percentil movel
TERCO = 2.0 / 3.0         # fecho no terco oposto
N_EXAUSTAO_1H = 4         # alguma das ultimas 4 velas de 1 h fechadas
N_INTERVALO_4H = 120      # 20 dias de velas de 4 h para a posicao no intervalo
ZONA_INTERVALO = 0.20     # 20 % extremos
FOLGA_ATR = 0.5           # stop = extremo do varrimento -/+ 0,5 ATR(14) de 1 h
JANELA_15M4H = 8          # 2 h para a reconquista ao vivo
ATR_N = 14

MODOS_EVENTOS = ["4h_12", "4h_24", "1h_24", "1h_48", "15m4h_12", "15m4h_24"]
FILTROS = ["A", "B", "C"]
HORIZONTES_15M = [16, 48, 96, 192]      # 4 h, 12 h, 24 h, 48 h

# REGRA NATURAL, fixada A PRIORI (e a descricao do utilizador: o 4 h varre o minimo de 2 dias, o 1 h
# confirma exaustao, o 15 m reconquista; stop 0,5 ATR abaixo do extremo, alvo 2 R, 24 h). E a UNICA
# configuracao avaliada no OOS, porque o estudo de eventos foi negativo e nao ha patamar a escolher.
REGRA_NATURAL = {"modo": "4h_12", "filtro": "B", "alvo_R": 2.0, "n_max": 96}

# Grelha declarada (48 configuracoes): modos x filtros x alvo x n_max. Corre-se no IS APENAS como
# sensibilidade (quantas configuracoes ficam positivas, quantas passariam H2 em amostra); NAO serve
# para escolher a regra do OOS.
GRELHA_MODOS = ["4h_12", "4h_24", "15m4h_12", "15m4h_24"]
GRELHA_FILTROS = ["A", "B", "C"]
GRELHA_ALVO_R = [2.0, 3.0]
GRELHA_N_MAX = [48, 96]


# ---------------------------------------------------------------------------------------------
# Caracteristicas (todas causais: so velas superiores fechadas, janelas para tras)
# ---------------------------------------------------------------------------------------------

def caracteristicas(df15: pd.DataFrame) -> pd.DataFrame:
    """Devolve DataFrame alinhado a df15 com tudo o que os modos e filtros precisam."""
    h1 = H.agregar(df15, "1h")
    h4 = H.agregar(df15, "4h")
    out = pd.DataFrame(index=df15.index)
    out["close"] = df15["close"].astype(float)
    out["low"] = df15["low"].astype(float)
    out["high"] = df15["high"].astype(float)
    c15 = out["close"]

    # ATR(14) de 1 h, projectado
    h1 = h1.copy()
    h1["atr"] = H.atr(h1, ATR_N)
    # exaustao no 1 h
    amp = (h1["high"] - h1["low"]) / h1["close"]
    amp_pct = H.percentil_movel(amp, JANELA_PCT_1H, min_periodos=JANELA_PCT_1H // 2)
    vol_pct = H.percentil_movel(h1["volume_quote"], JANELA_PCT_1H, min_periodos=JANELA_PCT_1H // 2)
    pos_vela = (h1["close"] - h1["low"]) / (h1["high"] - h1["low"]).replace(0.0, np.nan)
    forte = (amp_pct >= P_EXAUSTAO) & (vol_pct >= P_EXAUSTAO)
    ex_long = (forte & (pos_vela >= TERCO)).astype(float).rolling(N_EXAUSTAO_1H, min_periods=1).max()
    ex_short = (forte & (pos_vela <= 1.0 - TERCO)).astype(float).rolling(N_EXAUSTAO_1H, min_periods=1).max()
    deseq = H.desequilibrio_taker(h1, 1)
    deseq_prev = deseq.shift(1).rolling(3, min_periods=3).mean()
    h1["ex_long"] = ex_long
    h1["ex_short"] = ex_short
    h1["taker_seca_long"] = (deseq > deseq_prev).astype(float)      # venda taker a secar
    h1["taker_seca_short"] = (deseq < deseq_prev).astype(float)     # compra taker a secar
    p1 = H.projectar_superior(df15, h1, ["atr", "ex_long", "ex_short", "taker_seca_long", "taker_seca_short"], "_1h")
    out = out.join(p1)

    # posicao no intervalo de 4 h (ultima vela fechada)
    h4 = h4.copy()
    h4["pos_int"] = H.posicao_no_intervalo(h4, N_INTERVALO_4H)
    h4["id"] = np.arange(len(h4), dtype=float)
    out = out.join(H.projectar_superior(df15, h4, ["pos_int"], "_4h"))

    # modos "4h_N" e "1h_N": varrimento na vela superior, projectado com o id da vela
    for sup, dfs, Ns in (("4h", h4, (12, 24)), ("1h", h1, (24, 48))):
        dfs = dfs.copy()
        dfs["id"] = np.arange(len(dfs), dtype=float)
        for N in Ns:
            v = H.varrimento(dfs, N)
            dfs["varr"] = v["varrimento"].astype(float)
            dfs["nivel"] = v["nivel"]
            dfs["ext"] = np.where(v["varrimento"] > 0, dfs["low"], np.where(v["varrimento"] < 0, dfs["high"], np.nan))
            p = H.projectar_superior(df15, dfs, ["varr", "nivel", "ext", "id"], f"_{sup}_{N}")
            out = out.join(p)

    # modos "15m4h_N": nivel = min/max das ultimas N velas de 4 h fechadas; furo e reconquista no 15 m
    for N in (12, 24):
        h4["minN"] = h4["low"].rolling(N, min_periods=N).min()
        h4["maxN"] = h4["high"].rolling(N, min_periods=N).max()
        p = H.projectar_superior(df15, h4, ["minN", "maxN"], f"_{N}")
        L, U = p[f"minN_{N}"], p[f"maxN_{N}"]
        W = JANELA_15M4H
        furo_l = out["low"] < L
        furo_s = out["high"] > U
        # nivel de referencia = nivel no instante do ultimo furo (dentro de W velas)
        ref_l = L.where(furo_l).ffill(limit=W - 1)
        ref_s = U.where(furo_s).ffill(limit=W - 1)
        em_furo_l = furo_l.astype(float).rolling(W, min_periods=1).max() > 0
        em_furo_s = furo_s.astype(float).rolling(W, min_periods=1).max() > 0
        rec_l = em_furo_l & (c15 > ref_l) & ((c15.shift(1) <= ref_l) | furo_l)
        rec_s = em_furo_s & (c15 < ref_s) & ((c15.shift(1) >= ref_s) | furo_s)
        ext_l = out["low"].rolling(W, min_periods=1).min()
        ext_s = out["high"].rolling(W, min_periods=1).max()
        out[f"varr_15m4h_{N}"] = np.where(rec_l, 1.0, np.where(rec_s & ~rec_l, -1.0, 0.0))
        out[f"nivel_15m4h_{N}"] = np.where(rec_l, ref_l, np.where(rec_s, ref_s, np.nan))
        out[f"ext_15m4h_{N}"] = np.where(rec_l, ext_l, np.where(rec_s, ext_s, np.nan))
    return out


def sinais_e_stop(car: pd.DataFrame, modo: str, filtro: str):
    """Series de lado (+1/-1/0) ao fecho de k e Series do stop em preco, para um modo e um filtro."""
    c = car["close"]
    if modo.startswith("15m4h_"):
        lado = pd.Series(car[f"varr_{modo}"].to_numpy(), index=car.index)
        ext = car[f"ext_{modo}"]
    else:
        sup, N = modo.split("_")
        varr = car[f"varr_{sup}_{N}"]
        nivel = car[f"nivel_{sup}_{N}"]
        ext = car[f"ext_{sup}_{N}"]
        ident = car[f"id_{sup}_{N}"]
        trig = ((varr > 0) & (c > nivel)) | ((varr < 0) & (c < nivel))
        # primeiro disparo por vela superior varrida
        primeiro = trig & (trig.astype(int).groupby(ident).cumsum() == 1)
        lado = pd.Series(np.where(primeiro, varr, 0.0), index=car.index)
    if filtro in ("B", "C"):
        ok_l = (car["ex_long_1h"] > 0) & (car["taker_seca_long_1h"] > 0)
        ok_s = (car["ex_short_1h"] > 0) & (car["taker_seca_short_1h"] > 0)
        lado = lado.where(((lado > 0) & ok_l) | ((lado < 0) & ok_s), 0.0)
    if filtro == "C":
        pos = car["pos_int_4h"]
        lado = lado.where(((lado > 0) & (pos <= ZONA_INTERVALO)) | ((lado < 0) & (pos >= 1.0 - ZONA_INTERVALO)), 0.0)
    stop = ext - np.sign(lado) * FOLGA_ATR * car["atr_1h"]
    stop = stop.where(lado != 0, np.nan)
    return lado, stop


# ---------------------------------------------------------------------------------------------
# Estudo de eventos (IS), agrupado por bloco de 4 h entre activos
# ---------------------------------------------------------------------------------------------

def _retornos_eventos(car: pd.DataFrame, lado: pd.Series, stop: pd.Series, horizontes, de, ate) -> pd.DataFrame:
    """Uma linha por evento e horizonte: retorno (bps, a favor do lado) da abertura de k+1 ao fecho
    de k+h, e o mesmo em unidades de R (distancia entrada-stop), sem simular o stop."""
    idx = car.index
    mask = (idx >= H._utc(de)) & (idx <= H._utc(ate))
    pos = np.flatnonzero((lado.to_numpy() != 0) & mask)
    c = np.log(car["close"].to_numpy(dtype=float))
    o_px = car["open_px"].to_numpy(dtype=float)
    o = np.log(o_px)
    s = stop.to_numpy(dtype=float)
    ld = lado.to_numpy(dtype=float)
    n = len(car)
    linhas = []
    for hzt in horizontes:
        ok = pos[pos + hzt < n]
        ent = o_px[ok + 1]
        R = ld[ok] * (ent - s[ok])
        r = (c[ok + hzt] - o[ok + 1]) * ld[ok]
        linhas.append(pd.DataFrame({"h": hzt, "t": idx[ok], "lado": ld[ok], "r_bps": r * 1e4,
                                    "r_R": r * ent / R, "R_pct": R / ent * 100.0}))
    return pd.concat(linhas, ignore_index=True) if linhas else pd.DataFrame()


def resumo_agrupado(df: pd.DataFrame) -> pd.Series:
    """Media, erro padrao ingenuo e agrupado (por bloco de 4 h), t agrupado, mediana e fraccao positiva."""
    r = df["r_bps"].to_numpy(dtype=float)
    n = len(r)
    if n == 0:
        return pd.Series({"n": 0, "n_blocos": 0, "media_bps": np.nan, "ep_bps": np.nan, "ep_agrupado_bps": np.nan,
                          "t_agrupado": np.nan, "mediana_bps": np.nan, "frac_pos": np.nan, "media_R": np.nan,
                          "R_pct_mediano": np.nan})
    m = r.mean()
    bloco = pd.DatetimeIndex(df["t"]).floor("4h")
    e = pd.Series(r - m).groupby(bloco.to_numpy()).sum()
    ep_cl = float(np.sqrt((e ** 2).sum()) / n)
    ep = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else np.nan
    return pd.Series({"n": n, "n_blocos": int(len(e)), "media_bps": m, "ep_bps": ep, "ep_agrupado_bps": ep_cl,
                      "t_agrupado": m / ep_cl if ep_cl > 0 else np.nan, "mediana_bps": float(np.median(r)),
                      "frac_pos": float((r > 0).mean()), "media_R": float(df["r_R"].mean()),
                      "R_pct_mediano": float(df["R_pct"].median())})


def carregar_caracteristicas(activos):
    t0 = time.time()
    cars = {}
    for a in activos:
        df = H.carregar(a)
        car = caracteristicas(df)
        car["open_px"] = df["open"].astype(float)
        car["fecho_em"] = df["fecho_em"]
        cars[a] = (df, car)
    print(f"caracteristicas de {len(activos)} activos em {time.time() - t0:.0f} s")
    return cars


def estudo_eventos(cars, periodo="IS", nome="eventos"):
    de, ate = H.PERIODOS[periodo]
    linhas, deriva = [], []
    # deriva incondicional (todas as velas) por horizonte, para comparar com a amostra condicionada
    for a, (df, car) in cars.items():
        idx = car.index
        mask = (idx >= H._utc(de)) & (idx <= H._utc(ate))
        c = np.log(car["close"].to_numpy(dtype=float))
        o = np.log(car["open_px"].to_numpy(dtype=float))
        n = len(car)
        for hzt in HORIZONTES_15M:
            ok = np.flatnonzero(mask)
            ok = ok[ok + hzt < n]
            deriva.append({"activo": a, "h": hzt, "deriva_bps": float(((c[ok + hzt] - o[ok + 1]) * 1e4).mean())})
    deriva = pd.DataFrame(deriva).groupby("h")["deriva_bps"].mean()
    for modo in MODOS_EVENTOS:
        for filtro in FILTROS:
            partes = []
            for a, (df, car) in cars.items():
                lado, stop = sinais_e_stop(car, modo, filtro)
                ev = _retornos_eventos(car, lado, stop, HORIZONTES_15M, de, ate)
                if len(ev):
                    ev["activo"] = a
                    partes.append(ev)
            if not partes:
                continue
            ev = pd.concat(partes, ignore_index=True)
            for lado_nome, sel in (("ambos", ev), ("compra", ev[ev["lado"] > 0]), ("venda", ev[ev["lado"] < 0])):
                for hzt, g in sel.groupby("h"):
                    r = resumo_agrupado(g)
                    r["deriva_bps"] = deriva[hzt] * (1 if lado_nome != "venda" else -1) if lado_nome != "ambos" else np.nan
                    linhas.append({"periodo": periodo, "modo": modo, "filtro": filtro, "lado": lado_nome, "h": hzt, **r.to_dict()})
    tab = pd.DataFrame(linhas)
    os.makedirs(RESULTADOS, exist_ok=True)
    tab.to_csv(os.path.join(RESULTADOS, f"estrutura_{nome}.csv"), index=False)
    return tab, deriva


# ---------------------------------------------------------------------------------------------
# Grelha, escolha e OOS
# ---------------------------------------------------------------------------------------------

def simular_config(cars, modo, filtro, alvo_R, n_max, de, ate):
    trades = []
    for a, (df, car) in cars.items():
        lado, stop = sinais_e_stop(car, modo, filtro)
        mask = (car.index >= H._utc(de)) & (car.index <= H._utc(ate))
        lado = lado.where(mask, 0.0)
        t = H.simular(df, lado, stop, alvo_R, n_max, alvo_em_R=True, activo=a)
        trades.append(t)
    return pd.concat(trades, ignore_index=True)


def grelha(cars, periodo="IS"):
    de, ate = H.PERIODOS[periodo]
    linhas = []
    t0 = time.time()
    for modo in GRELHA_MODOS:
        for filtro in GRELHA_FILTROS:
            for alvo in GRELHA_ALVO_R:
                for n_max in GRELHA_N_MAX:
                    params = {"modo": modo, "filtro": filtro, "alvo_R": alvo, "n_max": n_max, "folga_atr": FOLGA_ATR,
                              "p_exaustao": P_EXAUSTAO, "zona": ZONA_INTERVALO}
                    tr = simular_config(cars, modo, filtro, alvo, n_max, de, ate)
                    m = H.metricas(tr, de, ate)
                    H.registar_ensaio(FAMILIA, params, m, periodo=periodo, nota=f"grelha {periodo} {len(cars)} activos")
                    g = m["global"]
                    n_pos = int((m["por_activo"]["soma"] > 0).sum()) if len(tr) else 0
                    linhas.append({"modo": modo, "filtro": filtro, "alvo_R": alvo, "n_max": n_max, "n": int(g["n"]),
                                   "media": g["media"], "ep": g["erro_padrao"], "PF": g["PF"], "acerto": g["taxa_acerto"],
                                   "dd": g["dd_max"], "n_pos": n_pos, "R_pct": float(tr["R_preco"].div(tr["entrada"]).median() * 100) if len(tr) else np.nan,
                                   "frac_stop": g["frac_stop"], "dur_h": g["duracao_h_media"]})
                    print(f"{modo:9s} {filtro} alvo {alvo} n_max {n_max:3d}: n {int(g['n']):5d} media {g['media']:+.3f} "
                          f"ep {g['erro_padrao']:.3f} PF {g['PF']:.2f} pos {n_pos} ({time.time() - t0:.0f} s)")
    tab = pd.DataFrame(linhas)
    tab.to_csv(os.path.join(RESULTADOS, f"estrutura_grelha_{periodo}.csv"), index=False)
    return tab


def avaliar(cars, cfg, periodo, nota, guardar=True):
    de, ate = H.PERIODOS[periodo]
    tr = simular_config(cars, cfg["modo"], cfg["filtro"], cfg["alvo_R"], cfg["n_max"], de, ate)
    m = H.metricas(tr, de, ate)
    cr = H.criterios(tr, de, ate)
    params = {"modo": cfg["modo"], "filtro": cfg["filtro"], "alvo_R": cfg["alvo_R"], "n_max": cfg["n_max"],
              "folga_atr": FOLGA_ATR, "p_exaustao": P_EXAUSTAO, "zona": ZONA_INTERVALO}
    H.registar_ensaio(FAMILIA, params, m, periodo=periodo, nota=nota)
    if guardar:
        tr.to_csv(os.path.join(RESULTADOS, f"estrutura_trades_{periodo}.csv"), index=False)
    return tr, m, cr


def imprimir_avaliacao(nome, tr, m, cr):
    g = m["global"]
    print(f"\n=== {nome} ===")
    print(g.to_string())
    print("\npor activo:\n", m["por_activo"][["n", "media", "soma", "PF", "taxa_acerto"]].to_string())
    print("\npor ano:\n", m["por_ano"][["n", "media", "soma", "PF"]].to_string())
    print("\npor metade:\n", m["por_metade"][["n", "media", "soma", "PF"]].to_string())
    print("\ncriterios:\n", cr.to_string(), "\npassa tudo:", cr.attrs["passa_tudo"])
    if len(tr):
        print("por lado:\n", tr.groupby("lado")["R_liquido"].agg(["count", "mean", "sum"]).to_string())
        print("motivo:\n", tr["motivo"].value_counts().to_string())


def main(etapa="tudo"):
    os.makedirs(RESULTADOS, exist_ok=True)
    cars = carregar_caracteristicas(H.SIMBOLOS)
    if etapa in ("eventos", "tudo"):
        tab, deriva = estudo_eventos(cars, "IS")
        print("\nderiva incondicional IS (bps, media dos 18 activos):\n", deriva.to_string())
        cols = ["modo", "filtro", "lado", "h", "n", "n_blocos", "media_bps", "ep_agrupado_bps", "t_agrupado",
                "mediana_bps", "frac_pos", "media_R", "R_pct_mediano", "deriva_bps"]
        print("\nESTUDO DE EVENTOS IS (retorno a favor do lado, bps, da abertura de k+1 ao fecho de k+h):")
        print(tab[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    if etapa in ("natural", "tudo"):
        # regra natural e as suas duas ablacoes (so varrimento; varrimento + exaustao; + intervalo), no IS
        for filtro in ("A", "B", "C"):
            cfg = dict(REGRA_NATURAL, filtro=filtro)
            tr, m, cr = avaliar(cars, cfg, "IS", f"regra natural IS, filtro {filtro}", guardar=(filtro == "B"))
            imprimir_avaliacao(f"IS regra natural filtro {filtro}", tr, m, cr)
    if etapa in ("grelha", "tudo"):
        tab = grelha(cars, "IS")
        print("\nGRELHA IS (sensibilidade; nao escolhe nada):\n", tab.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
        print("configuracoes com media > 0:", int((tab["media"] > 0).sum()), "de", len(tab),
              "; com media >= 0,15 R:", int((tab["media"] >= 0.15).sum()), "; melhor media:", round(tab["media"].max(), 3),
              "+-", round(float(tab.loc[tab["media"].idxmax(), "ep"]), 3))
    if etapa in ("oos", "tudo"):
        cfg = dict(REGRA_NATURAL)
        tr, m, cr = avaliar(cars, cfg, "OOS", "OOS avaliado UMA vez: regra natural fixada a priori")
        imprimir_avaliacao("OOS regra natural", tr, m, cr)
        tr, m, cr = avaliar(cars, cfg, "IS_preco", "robustez 2020-09 a 2025-03 (so a parte anterior a 2023 e nova)")
        t2 = tr[pd.to_datetime(tr["sinal_em"], utc=True) < H._utc("2023-01-01")]
        m2 = H.metricas(t2, "2020-09-01", "2022-12-31")
        cr2 = H.criterios(t2, "2020-09-01", "2022-12-31")
        imprimir_avaliacao("2020-09 a 2022-12 regra natural", t2, m2, cr2)
        t2.to_csv(os.path.join(RESULTADOS, "estrutura_trades_2020_2022.csv"), index=False)
    print("\nM (linhas registadas em ensaios/estrutura.csv):", H.contar_ensaios(FAMILIA))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tudo")
