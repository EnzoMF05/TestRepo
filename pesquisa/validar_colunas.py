# -*- coding: utf-8 -*-
"""Validação das colunas de métricas (liquidações, OI, ls_ratio, funding): trazem informação própria?

Para cada activo, só velas NÃO imputadas: regressão OLS do valor sobre |retorno|, volume_quote,
taker_buy e taker_sell da MESMA vela (R2 linear e em logaritmos), mais uma versão estendida com OI
(uma série modelada costuma ser OI x f(|retorno|)). Se o R2 passar 0,9 a coluna é derivada do preço
e do volume e NÃO se usa como sinal. Também: fracção de zeros, autocorrelação a 1 vela, assimetria
direccional das liquidações (longs devem ser liquidados em velas de queda) e R2 por ano.

Correr: python validar_colunas.py   (escreve resultados/validar_colunas.csv e imprime a conclusão)
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

ACTIVOS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
RESULTADOS = os.path.join(H.AQUI, "resultados")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)


def r2_ols(y: np.ndarray, X: np.ndarray) -> float:
    """R2 de OLS com intercepto (numpy lstsq)."""
    ok = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    y, X = y[ok], X[ok]
    if len(y) < 50:
        return float("nan")
    A = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    res = y - A @ beta
    sst = ((y - y.mean()) ** 2).sum()
    return float(1.0 - (res ** 2).sum() / sst) if sst > 0 else float("nan")


def log1(x: np.ndarray) -> np.ndarray:
    return np.log1p(np.abs(x))


def analisar(df: pd.DataFrame, activo: str) -> list:
    d = df[~df["imputado"]].copy()
    d["ret"] = H.retorno_log(df["close"]).reindex(d.index)
    d = d.dropna(subset=["ret"])
    aret = d["ret"].abs().to_numpy()
    vq = d["volume_quote"].to_numpy(dtype=float)
    tb = d["taker_buy_vol_btc"].to_numpy(dtype=float) * d["close"].to_numpy()
    ts = d["taker_sell_vol_btc"].to_numpy(dtype=float) * d["close"].to_numpy()
    oi = d["open_interest_usd"].to_numpy(dtype=float)
    X_lin = np.column_stack([aret, vq, tb, ts])
    X_log = np.column_stack([log1(aret * 1e4), log1(vq), log1(tb), log1(ts)])
    X_ext_lin = np.column_stack([X_lin, oi, aret * oi, vq * aret])
    X_ext_log = np.column_stack([X_log, log1(oi), log1(aret * 1e4) * log1(oi)])
    linhas = []
    alvos = {
        "long_liq_usd": d["long_liq_usd"].abs().to_numpy(dtype=float),
        "short_liq_usd": d["short_liq_usd"].to_numpy(dtype=float),
        "liq_total_usd": (d["long_liq_usd"].abs() + d["short_liq_usd"]).to_numpy(dtype=float),
        "oi_change_pct": d["oi_change_pct"].to_numpy(dtype=float),
        "|oi_change_pct|": d["oi_change_pct"].abs().to_numpy(dtype=float),
        "ls_ratio_top": d["ls_ratio_top"].to_numpy(dtype=float),
        "d_ls_ratio_top": d["ls_ratio_top"].diff().to_numpy(dtype=float),
        "ls_ratio_global": d["ls_ratio_global"].to_numpy(dtype=float),
        "funding_rate_pct": d["funding_rate_pct"].to_numpy(dtype=float),
        "d_funding": d["funding_rate_pct"].diff().to_numpy(dtype=float),
        "open_interest_usd": oi,
    }
    neg = d["ret"].to_numpy() < 0
    for nome, y in alvos.items():
        s = pd.Series(y)
        e_oi = nome == "open_interest_usd"   # o OI é regressor da versão estendida: não se regride sobre si próprio
        p1, p50 = float(np.nanpercentile(y, 1)), float(np.nanpercentile(y, 50))
        linha = {
            "activo": activo, "coluna": nome, "n": int(np.isfinite(y).sum()),
            "R2_lin": r2_ols(y, X_lin), "R2_log": r2_ols(log1(y), X_log),
            "R2_ext_lin": float("nan") if e_oi else r2_ols(y, X_ext_lin),
            "R2_ext_log": float("nan") if e_oi else r2_ols(log1(y), X_ext_log),
            "frac_zeros": float((s == 0).mean()), "frac_distintos": float(s.nunique() / max(1, s.notna().sum())),
            "acf1": float(s.autocorr(1)), "p1": p1, "p50": p50,
            "p99": float(np.nanpercentile(y, 99)), "p1_sobre_p50": p1 / p50 if p50 else float("nan"),
        }
        if nome in ("long_liq_usd", "short_liq_usd"):
            # assimetria direccional: média em velas de queda / média em velas de subida
            linha["racio_queda_subida"] = float(np.nanmean(y[neg]) / np.nanmean(y[~neg]))
            # correlação com a variação de OI (liquidações fecham posições: OI deve cair)
            linha["corr_oi_change"] = float(np.corrcoef(y, d["oi_change_pct"].to_numpy())[0, 1])
        if nome == "oi_change_pct":
            # é só a variação percentual da coluna OI?
            calc = d["open_interest_usd"].pct_change().to_numpy() * 100
            ok = np.isfinite(calc) & np.isfinite(y)
            linha["corr_com_pct_change_OI"] = float(np.corrcoef(calc[ok], y[ok])[0, 1])
        if nome == "funding_rate_pct":
            linha["frac_igual_0.01"] = float((np.abs(y - 0.01) < 1e-9).mean())
            linha["mudancas_por_dia"] = float((np.diff(y) != 0).sum() / (len(y) / 96))
        linhas.append(linha)
    return linhas


def r2_por_ano(df: pd.DataFrame, coluna: str) -> dict:
    d = df[~df["imputado"]].copy()
    d["ret"] = H.retorno_log(df["close"]).reindex(d.index)
    d = d.dropna(subset=["ret"])
    out = {}
    for ano, g in d.groupby(d.index.year):
        if len(g) < 2000:
            continue
        aret = g["ret"].abs().to_numpy()
        X = np.column_stack([log1(aret * 1e4), log1(g["volume_quote"].to_numpy(dtype=float)),
                             log1(g["taker_buy_vol_btc"].to_numpy(dtype=float) * g["close"].to_numpy()),
                             log1(g["taker_sell_vol_btc"].to_numpy(dtype=float) * g["close"].to_numpy())])
        out[int(ano)] = round(r2_ols(log1(g[coluna].abs().to_numpy(dtype=float)), X), 3)
    return out


def liquidacoes_por_activo(simbolos) -> pd.DataFrame:
    """Para os 18 activos: tem a série de liquidações um piso comum e assimetria direccional?

    Uma série observada é pesada na cauda (p1/p50 pequeno) e liquida longs sobretudo em velas de queda
    (racio >> 1). Um piso semelhante em activos de dimensão muito diferente e racio perto de 1 indicam
    série modelada, mesmo com is_imputed_metrics = 0.
    """
    linhas = []
    for a in simbolos:
        df = H.carregar(a)
        d = df[~df["imputado"]].copy()
        d["ret"] = H.retorno_log(df["close"]).reindex(d.index)
        d = d.dropna(subset=["ret"])
        neg = d["ret"].to_numpy() < 0
        ll = d["long_liq_usd"].abs().to_numpy(dtype=float)
        sl = d["short_liq_usd"].to_numpy(dtype=float)
        aret = d["ret"].abs().to_numpy()
        X = np.column_stack([log1(aret * 1e4), log1(d["volume_quote"].to_numpy(dtype=float))])
        p1, p50 = np.percentile(ll, 1), np.percentile(ll, 50)
        racio_l = float(ll[neg].mean() / ll[~neg].mean())
        racio_s = float(sl[neg].mean() / sl[~neg].mean())
        piso = p1 / p50 >= 0.25
        fraca = racio_l < 1.5 or racio_s > 0.67
        veredicto = "MODELADA: nao usar" if (piso or fraca) else "usavel com reservas"
        linhas.append({"activo": a, "n": len(d), "desde": str(d.index[0].date()), "long_p1": p1, "long_p50": p50,
                       "p1_sobre_p50": p1 / p50, "frac_abaixo_1.5p1": float((ll < 1.5 * p1).mean()),
                       "racio_queda_subida_long": racio_l, "racio_queda_subida_short": racio_s,
                       "R2_log_long": r2_ols(log1(ll), X), "veredicto": veredicto})
    return pd.DataFrame(linhas).set_index("activo")


def conclusao(r: pd.DataFrame) -> pd.DataFrame:
    """Conclusão por coluna a partir do R2 máximo (mediana entre activos)."""
    g = r.groupby("coluna")
    res = pd.DataFrame({
        "R2_lin_med": g["R2_lin"].median(), "R2_log_med": g["R2_log"].median(),
        "R2_ext_max": g[["R2_ext_lin", "R2_ext_log"]].max().max(axis=1),
        "frac_zeros": g["frac_zeros"].median(), "acf1_med": g["acf1"].median(),
    })
    res["R2_max"] = res[["R2_lin_med", "R2_log_med", "R2_ext_max"]].max(axis=1)

    def rotulo(x):
        if x >= 0.9:
            return "DERIVADA: nao usar como sinal"
        if x >= 0.5:
            return "usavel com reservas"
        return "usavel (validar ao vivo)"
    res["conclusao"] = res["R2_max"].map(rotulo)
    return res.sort_values("R2_max", ascending=False)


def main():
    os.makedirs(RESULTADOS, exist_ok=True)
    linhas = []
    anos = {}
    for a in ACTIVOS:
        df = H.carregar(a)
        linhas += analisar(df, a)
        anos[a] = r2_por_ano(df, "long_liq_usd")
        print(f"{a}: {len(df)} velas, {int((~df['imputado']).sum())} nao imputadas", flush=True)
    r = pd.DataFrame(linhas)
    r.to_csv(os.path.join(RESULTADOS, "validar_colunas.csv"), index=False)
    print("\n=== R2 da coluna sobre |retorno|, volume_quote, taker_buy, taker_sell da mesma vela (nao imputadas) ===")
    cols = ["activo", "coluna", "n", "R2_lin", "R2_log", "R2_ext_lin", "R2_ext_log", "frac_zeros", "frac_distintos", "acf1",
            "p1", "p50", "p99", "p1_sobre_p50", "racio_queda_subida", "corr_oi_change", "corr_com_pct_change_OI", "frac_igual_0.01", "mudancas_por_dia"]
    cols = [c for c in cols if c in r]
    for col in ["long_liq_usd", "short_liq_usd", "liq_total_usd", "oi_change_pct", "|oi_change_pct|", "open_interest_usd",
                "ls_ratio_top", "d_ls_ratio_top", "ls_ratio_global", "funding_rate_pct", "d_funding"]:
        sub = r[r["coluna"] == col][cols].dropna(axis=1, how="all")
        print(f"\n--- {col} ---")
        print(sub.to_string(index=False, float_format=lambda x: f"{x:.4g}"))
    print("\n=== R2 (logs) de |long_liq| por ano e activo ===")
    print(pd.DataFrame(anos).T.to_string())
    c = conclusao(r)
    c.to_csv(os.path.join(RESULTADOS, "validar_colunas_conclusao.csv"))
    print("\n=== Conclusao por coluna (mediana entre activos; limiar 0,9 = derivada) ===")
    print(c.to_string(float_format=lambda x: f"{x:.3f}"))
    liq = liquidacoes_por_activo(H.SIMBOLOS)
    liq.to_csv(os.path.join(RESULTADOS, "validar_liquidacoes_por_activo.csv"))
    print("\n=== Liquidacoes por activo (18): piso comum (p1/p50 >= 0,25) ou assimetria fraca (long < 1,5 ou short > 0,67) = modelada ===")
    print(liq.to_string(float_format=lambda x: f"{x:.3g}"))
    print("\nActivos com liquidacoes usaveis com reservas:", ", ".join(liq.index[liq["veredicto"].str.startswith("usavel")]))
    print("Activos com liquidacoes modeladas (nao usar):", ", ".join(liq.index[liq["veredicto"].str.startswith("MODELADA")]))


if __name__ == "__main__":
    main()
