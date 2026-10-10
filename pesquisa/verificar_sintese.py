# -*- coding: utf-8 -*-
"""Verificação independente para a SÍNTESE: recalcula as métricas OOS de cada família a partir dos
ficheiros de trades em resultados/, sem passar pelo código das famílias.

Para cada ficheiro OOS: n, média, erro padrão, PF, DD máximo (curva acumulada por saída), metades
(ponto médio entre 2025-04-01 e 2026-09-09 23:59:59, como em harness.metricas), activos positivos
(soma > 0), datas do primeiro e último sinal (têm de ficar dentro do OOS), fracção de stops
preenchidos abaixo/acima do nível (abertura já tinha saltado o stop), médias por lado, e os
critérios H1 a H6. Imprime também o conjunto dos seis OOS primários juntos.

Correr: python verificar_sintese.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

RES = os.path.join(H.AQUI, "resultados")
OOS_DE, OOS_ATE = H.PERIODOS["OOS"]

FICHEIROS = {
    "ESTRUTURA (18 activos)": "estrutura_trades_OOS.csv",
    "BANDAS (6 principais)": "bandas_trades_OOS6.csv",
    "BANDAS (18 activos)": "bandas_trades_OOS18.csv",
    "AGLOMERACAO (5 activos)": "aglomeracao_trades_oos_OOS.csv",
    "FLUXO (6 principais)": "fluxo_trades_OOS6.csv",
    "FLUXO (18 activos)": "fluxo_trades_OOS18.csv",
    "TRANSVERSAL (5 alts)": "transversal_trades_natural_OOS.csv",
    "TRANSVERSAL (17 alts)": "transversal_trades_natural18_OOS.csv",
    "TRANSVERSAL perna BTC (5 alts)": "transversal_trades_naturalBTC_OOS.csv",
}
PRIMARIOS = ["ESTRUTURA (18 activos)", "BANDAS (6 principais)", "AGLOMERACAO (5 activos)",
             "FLUXO (6 principais)", "TRANSVERSAL (5 alts)"]


def resumo(t: pd.DataFrame) -> dict:
    r = t["R_liquido"].to_numpy(dtype=float)
    n = len(r)
    if n == 0:
        return {"n": 0}
    ganhos, perdas = r[r > 0].sum(), -r[r < 0].sum()
    ordem = t.sort_values("saida_em")
    curva = ordem["R_liquido"].cumsum().to_numpy()
    dd = float((np.maximum.accumulate(curva) - curva).max())
    stops = t[t["motivo"] == "stop"]
    salto = (stops["lado"] * (stops["saida"] - stops["stop"]) < -1e-9).mean() if len(stops) else np.nan
    return {
        "n": n, "media": r.mean(), "ep": r.std(ddof=1) / np.sqrt(n), "PF": ganhos / perdas if perdas > 0 else np.inf,
        "acerto": (r > 0).mean(), "dd": dd, "frac_stop": (t["motivo"] == "stop").mean(),
        "stops_com_salto": salto, "R_bruto": t["R_bruto"].mean(),
    }


def analisar(nome: str, ficheiro: str) -> dict:
    t = pd.read_csv(os.path.join(RES, ficheiro))
    t["sinal_em"] = pd.to_datetime(t["sinal_em"], utc=True)
    t["saida_em"] = pd.to_datetime(t["saida_em"], utc=True)
    de, ate = pd.Timestamp(OOS_DE, tz="UTC"), pd.Timestamp(OOS_ATE, tz="UTC")
    dentro = ((t["sinal_em"] >= de) & (t["sinal_em"] <= ate)).all()
    meio = de + (ate - de) / 2
    g = resumo(t)
    m1 = resumo(t[t["sinal_em"] < meio])
    m2 = resumo(t[t["sinal_em"] >= meio])
    por_activo = t.groupby("activo")["R_liquido"].agg(["count", "mean", "sum"])
    n_pos = int((por_activo["sum"] > 0).sum())
    lados = t.groupby("lado")["R_liquido"].agg(["count", "mean"])
    crit = {
        "H1": g["n"] >= 100, "H2": g["media"] >= 0.15, "H3": g["PF"] >= 1.3, "H4": n_pos >= 3,
        "H5": g["dd"] <= 10, "H6": (m1["n"] > 0 and m2["n"] > 0 and m1["media"] * m1["n"] > 0 and m2["media"] * m2["n"] > 0),
    }
    print(f"\n== {nome} ({ficheiro})")
    print(f"  sinais dentro do OOS: {dentro}; primeiro {t['sinal_em'].min()}, último {t['sinal_em'].max()}")
    print(f"  n {g['n']}, média {g['media']:+.3f} R (ep {g['ep']:.3f}), PF {g['PF']:.2f}, acerto {g['acerto']:.1%}, "
          f"DD {g['dd']:.1f} R, R bruto {g['R_bruto']:+.3f}, stops {g['frac_stop']:.0%}, "
          f"stops com salto da abertura {g['stops_com_salto']:.1%}")
    print(f"  metades: 1.a n {m1['n']} média {m1['media']:+.3f} (soma {m1['media'] * m1['n']:+.1f}); "
          f"2.a n {m2['n']} média {m2['media']:+.3f} (soma {m2['media'] * m2['n']:+.1f})")
    print(f"  activos positivos {n_pos} de {len(por_activo)}; "
          + ", ".join(f"{a} {v['mean']:+.3f} (n {int(v['count'])})" for a, v in por_activo.sort_values("mean", ascending=False).iterrows()))
    print("  por lado: " + ", ".join(f"{'compra' if l > 0 else 'venda'} n {int(v['count'])} média {v['mean']:+.3f}" for l, v in lados.iterrows()))
    print("  critérios: " + ", ".join(f"{k} {'passa' if v else 'FALHA'}" for k, v in crit.items()) + f"; passa tudo: {all(crit.values())}")
    return {"nome": nome, "trades": t, **g, "n_pos": n_pos, "m1": m1["media"] * m1["n"], "m2": m2["media"] * m2["n"], "passa": all(crit.values())}


def main():
    pd.set_option("display.width", 220)
    tudo = {nome: analisar(nome, f) for nome, f in FICHEIROS.items()}
    print("\n== Os cinco OOS primários juntos (ESTRUTURA 18, BANDAS 6, AGLOMERACAO 5, FLUXO 6, TRANSVERSAL 5)")
    juntos = pd.concat([tudo[n]["trades"] for n in PRIMARIOS], ignore_index=True)
    g = resumo(juntos)
    print(f"  n {g['n']}, média {g['media']:+.3f} R (ep {g['ep']:.3f}), PF {g['PF']:.2f}, R bruto {g['R_bruto']:+.3f}")
    sem_fluxo = pd.concat([tudo[n]["trades"] for n in PRIMARIOS if not n.startswith("FLUXO")], ignore_index=True)
    g = resumo(sem_fluxo)
    print(f"  sem FLUXO (que tem 4661 trades e domina): n {g['n']}, média {g['media']:+.3f} R (ep {g['ep']:.3f}), PF {g['PF']:.2f}, R bruto {g['R_bruto']:+.3f}")
    print("\n== Tabela resumida")
    linhas = [{"familia": n, "n": v["n"], "media": round(v["media"], 3), "ep": round(v["ep"], 3), "PF": round(v["PF"], 2),
               "DD": round(v["dd"], 1), "activos_pos": v["n_pos"], "metade1": round(v["m1"], 1), "metade2": round(v["m2"], 1),
               "passa": v["passa"]} for n, v in tudo.items()]
    print(pd.DataFrame(linhas).to_string(index=False))


if __name__ == "__main__":
    main()
