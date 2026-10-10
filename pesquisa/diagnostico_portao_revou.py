"""Portao REVOU em perps reais (15 m agregados a 1 h): fraccao de horas VERDE desde 2025."""
import sys, math
import pyarrow.parquet as pq, pandas as pd, numpy as np
sys.path.insert(0, "/home/user/TestRepo/medidor")
import reversao as rv
H_A, N, NMIN = 96, 2160, 480
lam = rv.lambda_ancora(H_A)
print("lambda_A = %.5f (H nula = %d h)" % (lam, H_A))
for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]:
    p = "/home/user/kbsingh1399/backtesting_data/Binance_Data/%s_15m_master_2020_2026.parquet" % sym
    df = pq.read_table(p, columns=["open_time_ms", "close"]).to_pandas().sort_values("open_time_ms")
    df = df[df.open_time_ms >= pd.Timestamp("2024-06-01").value // 10**6]
    df["h"] = df.open_time_ms // 3_600_000
    h1 = df.groupby("h").close.last().reset_index()
    x = np.log(h1.close.values)
    anc = rv.Ancora(H_A); d = np.array([anc.juntar(v) for v in x])
    aq = int(math.ceil(4 * H_A)); res = []
    for t in range(aq + NMIN, len(d)):
        aj = rv.ajustar_ar1(list(d[max(aq, t - N):t + 1]), lam)
        res.append((h1.h.iloc[t] * 3600, aj.phi_c, aj.meia_vida, aj.t_nulo, aj.valido))
    r = pd.DataFrame(res, columns=["t", "phi_c", "H", "t_nulo", "valido"])
    r = r[r.t >= pd.Timestamp("2025-01-01").timestamp()]
    tc = rv.calibrar_t_crit(N, lam, 300, 0.001, 7)
    verde = (r.valido & (r.H >= 3) & (r.H <= 24) & (r.t_nulo <= tc)).mean()
    print("%-8s horas %5d | phi_c mediana %.4f | H mediana %5.0f h (p5 %5.0f, p95 %5.0f) | t_nulo mediana %+.2f min %+.2f | t_crit %.2f | H<=24: %5.1f%% | t<=t_crit: %5.1f%% | VERDE %.2f%%"
          % (sym, len(r), r.phi_c.median(), r.H.median(), r.H.quantile(.05), r.H.quantile(.95), r.t_nulo.median(), r.t_nulo.min(), tc, 100*(r.H <= 24).mean(), 100*(r.t_nulo <= tc).mean(), 100*verde))
