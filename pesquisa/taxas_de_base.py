# -*- coding: utf-8 -*-
"""Taxas de base dos 18 activos: autocorrelação, rácio de variâncias, retorno após velas extremas e a nula.

1. Autocorrelação a 1 desfasamento dos retornos a 15 m, 1 h, 4 h e 24 h, por activo (amostra inteira).
2. Rácio de variâncias de Lo-MacKinlay a 2, 4, 8 e 24 h (sobre retornos de 1 h) com z robusto, por
   activo e por ano.
3. Retorno médio nas 4, 24 e 96 h seguintes a uma vela de 1 h com |retorno| acima do percentil 99
   (percentil móvel de 365 dias, causal), separado pelo sinal da vela, com erro padrão; medido da
   abertura da vela seguinte (execução manual) ao fecho de k+h.
4. A nula: sinais aleatórios com os mesmos stop e alvo que uma regra típica (stop em múltiplos de
   ATR de 1 h, alvo 2 R, saída por tempo às 24 h), líquidos de 6,5 bps por lado.

Correr: python taxas_de_base.py   (escreve resultados/*.csv e imprime as tabelas)
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

RESULTADOS = os.path.join(H.AQUI, "resultados")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 200)
FMT = lambda x: f"{x:.3f}"  # noqa: E731

Q_HORAS = [2, 4, 8, 24]
HORIZONTES_H = [4, 24, 96]
NULA_CFG = [  # (multiplo ATR 1h para o stop, alvo em R, n_max velas 15m)
    (1.0, 2.0, 96), (2.0, 2.0, 96), (4.0, 2.0, 96), (2.0, 1.0, 96), (2.0, 3.0, 192),
]
NULA_SINAIS_POR_ACTIVO = 400
NULA_DE, NULA_ATE = "2023-01-01", "2026-09-09"


def main():
    os.makedirs(RESULTADOS, exist_ok=True)
    acf_linhas, vr_linhas, ev_linhas, nula_trades = [], [], [], {cfg: [] for cfg in NULA_CFG}
    ev_pool = {s: [] for s in (1, -1)}
    for a in H.SIMBOLOS:
        df = H.carregar(a)
        h1, h4, d1 = H.agregar(df, "1h"), H.agregar(df, "4h"), H.agregar(df, "1d")
        r15, r1, r4, r24 = (H.retorno_log(x["close"]) for x in (df, h1, h4, d1))
        # 1. autocorrelação
        linha = {"activo": a, "inicio": str(df.index[0].date())}
        for nome, r in (("15m", r15), ("1h", r1), ("4h", r4), ("24h", r24)):
            rho, ep, n = H.autocorrelacao(r, 1)
            linha[f"acf_{nome}"] = rho
            linha[f"z_{nome}"] = rho / ep
        acf_linhas.append(linha)
        # 2. rácio de variâncias por activo e por ano (retornos de 1 h)
        for ano, g in list(r1.groupby(r1.index.year)) + [("todos", r1)]:
            if len(g.dropna()) < 1000:
                continue
            l2 = {"activo": a, "ano": ano, "n_h": int(g.notna().sum())}
            for q in Q_HORAS:
                vr, z, _ = H.racio_variancias(g, q)
                l2[f"VR{q}"] = vr
                l2[f"z{q}"] = z
            vr_linhas.append(l2)
        # 3. velas extremas de 1 h: |ret| > percentil 99 móvel (365 dias, mínimo 90 dias)
        lim = r1.abs().rolling(24 * 365, min_periods=24 * 90).quantile(0.99).shift(1)
        extremo = r1.abs() > lim
        for sinal in (1, -1):
            ev = pd.Series(0, index=h1.index, dtype=int)
            ev[extremo & (np.sign(r1) == sinal)] = 1  # retorno SEM sinal: positivo = continua no sentido da vela? nao: mede-se o retorno bruto
            est = H.estudo_de_eventos(h1, ev.astype(bool), HORIZONTES_H)
            for hzt, row in est.iterrows():
                ev_linhas.append({"activo": a, "vela_extrema": "subida" if sinal > 0 else "queda", "horizonte_h": hzt, **row.to_dict()})
            # para o conjunto, guardo os retornos individuais
            pos = np.flatnonzero(ev.to_numpy())
            c = np.log(h1["close"].to_numpy())
            o = np.log(h1["open"].to_numpy())
            for hzt in HORIZONTES_H:
                ok = pos[pos + hzt < len(h1)]
                ev_pool[sinal].append(pd.DataFrame({"h": hzt, "r_bps": (c[ok + hzt] - o[ok + 1]) * 1e4, "activo": a,
                                                    "t": h1.index[ok]}))
        # 4. nula
        atr1h = H.projectar_superior(df, h1.assign(atr=H.atr(h1, 14)), ["atr"])["atr"]
        for (m_stop, m_alvo, n_max) in NULA_CFG:
            for semente in range(2):
                s = H.sinais_aleatorios(df, NULA_SINAIS_POR_ACTIVO, semente=semente * 100 + H.SIMBOLOS.index(a), de=NULA_DE, ate=NULA_ATE)
                t = H.simular(df, s, m_stop, m_alvo, n_max, escala=atr1h, alvo_em_R=True, uma_posicao=False, activo=a)
                nula_trades[(m_stop, m_alvo, n_max)].append(t)
        print(f"{a}: ok ({len(df)} velas de 15 m, {len(h1)} de 1 h)", flush=True)

    acf = pd.DataFrame(acf_linhas).set_index("activo")
    acf.to_csv(os.path.join(RESULTADOS, "taxas_acf.csv"))
    print("\n=== 1. Autocorrelacao a 1 desfasamento dos retornos (amostra inteira; z = rho*sqrt(n)) ===")
    print(acf.to_string(float_format=FMT))
    print("mediana entre activos:", acf[[c for c in acf if c.startswith("acf_")]].median().round(4).to_dict())

    vr = pd.DataFrame(vr_linhas)
    vr.to_csv(os.path.join(RESULTADOS, "taxas_vr.csv"), index=False)
    print("\n=== 2. Racio de variancias (retornos 1 h) por activo, amostra inteira; z robusto (Lo-MacKinlay M2) ===")
    print(vr[vr["ano"] == "todos"].set_index("activo").drop(columns="ano").to_string(float_format=FMT))
    print("\n--- por ano: mediana do VR entre activos e numero de activos com z < -2 / z > +2 ---")
    por_ano = vr[vr["ano"] != "todos"].copy()
    por_ano["ano"] = por_ano["ano"].astype(int)
    res = []
    for ano, g in por_ano.groupby("ano"):
        l3 = {"ano": ano, "activos": len(g)}
        for q in Q_HORAS:
            l3[f"VR{q}_med"] = g[f"VR{q}"].median()
            l3[f"z{q}<-2"] = int((g[f"z{q}"] < -2).sum())
            l3[f"z{q}>2"] = int((g[f"z{q}"] > 2).sum())
        res.append(l3)
    print(pd.DataFrame(res).set_index("ano").to_string(float_format=FMT))
    print("\n--- VR por activo e ano (q = 24 h) ---")
    print(por_ano.pivot(index="activo", columns="ano", values="VR24").to_string(float_format=FMT))

    ev = pd.DataFrame(ev_linhas)
    ev.to_csv(os.path.join(RESULTADOS, "taxas_eventos_extremos.csv"), index=False)
    print("\n=== 3. Retorno (bps, log) apos vela de 1 h com |ret| > p99 movel, da abertura seguinte ao fecho de k+h ===")
    print("--- conjunto dos 18 activos. ep_naive assume eventos independentes; ep_hora agrupa por hora do evento "
          "(os activos caem juntos), com n_horas observacoes ---")

    def tabela(pool):
        g = pool.groupby("h")["r_bps"]
        por_hora = pool.groupby(["h", "t"])["r_bps"].mean().groupby(level=0)
        tab = pd.DataFrame({"n": g.size(), "n_horas": por_hora.size(), "media_bps": g.mean(),
                            "ep_naive": g.std() / np.sqrt(g.size()),
                            "ep_hora": por_hora.std() / np.sqrt(por_hora.size()),
                            "mediana_bps": g.median(), "frac_pos": g.apply(lambda x: (x > 0).mean())})
        tab["t_hora"] = tab["media_bps"] / tab["ep_hora"]
        return tab

    def periodo(t):
        t = pd.DatetimeIndex(t)
        return np.where(t < pd.Timestamp("2023-01-01", tz="UTC"), "a) 2020-22",
                        np.where(t < pd.Timestamp("2025-04-01", tz="UTC"), "b) IS 2023-25.03", "c) OOS 2025.04-"))

    for sinal, nome in ((1, "subida extrema"), (-1, "queda extrema")):
        pool = pd.concat(ev_pool[sinal], ignore_index=True)
        tab = tabela(pool)
        print(f"\n{nome} (amostra inteira):")
        print(tab.to_string(float_format=FMT))
        tab.to_csv(os.path.join(RESULTADOS, f"taxas_eventos_extremos_pool_{'subida' if sinal > 0 else 'queda'}.csv"))
        pool["periodo"] = periodo(pool["t"])
        partes = []
        for per, gp in pool.groupby("periodo"):
            tp = tabela(gp)
            tp.insert(0, "periodo", per)
            partes.append(tp)
        tper = pd.concat(partes).reset_index().set_index(["periodo", "h"])
        print(f"{nome} por periodo:")
        print(tper[["n", "n_horas", "media_bps", "ep_hora", "mediana_bps", "frac_pos", "t_hora"]].to_string(float_format=FMT))
        tper.to_csv(os.path.join(RESULTADOS, f"taxas_eventos_extremos_periodo_{'subida' if sinal > 0 else 'queda'}.csv"))
    print("\n--- por activo: media_bps a 24 h (subida | queda) e n ---")
    piv = ev[ev["horizonte_h"] == 24].pivot(index="activo", columns="vela_extrema", values=["media_bps", "erro_padrao_bps", "n"])
    print(piv.to_string(float_format=FMT))

    print("\n=== 4. A nula: sinais aleatorios (lado ao acaso), 2023-01 a 2026-09, 18 activos, 800 sinais por activo ===")
    print("stop = m x ATR(14) de 1 h (projectado causalmente), alvo em R, saida por tempo ao fecho da vela n_max; custo 6,5 bps por lado")
    linhas = []
    for cfg, lst in nula_trades.items():
        t = pd.concat(lst, ignore_index=True)
        m = H.metricas(t)
        g = m["global"]
        R_pct = (t["R_preco"] / t["entrada"]).median() * 100
        custo_teorico = -2 * H.CUSTO_BPS / 1e4 / (R_pct / 100)
        pa = m["por_activo"]["media"]
        linhas.append({"stop_xATR1h": cfg[0], "alvo_R": cfg[1], "n_max": cfg[2], "n": int(g["n"]),
                       "R_mediano_%": R_pct, "media_R_liq": g["media"], "erro_padrao": g["erro_padrao"],
                       "media_R_bruto": g["R_bruto_medio"], "custo_teorico_R": custo_teorico, "PF": g["PF"],
                       "taxa_acerto": g["taxa_acerto"], "frac_stop": g["frac_stop"],
                       "min_activo": pa.min(), "max_activo": pa.max(), "activos_positivos": int((pa > 0).sum())})
    nula = pd.DataFrame(linhas)
    nula.to_csv(os.path.join(RESULTADOS, "taxas_nula.csv"), index=False)
    print(nula.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("\nLeitura: a media liquida da nula e aproximadamente -custo/R; uma regra sem vantagem rende isto. Para "
          "-0,05 R o R tem de ser cerca de 2,6 % do preco (13 bps / 0,05). O limiar H2 (+0,15 R) exige uma "
          "vantagem bruta de ~0,2 R acima da nula.")


if __name__ == "__main__":
    main()
