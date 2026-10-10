"""Preparacao do armazem de investigacao a partir dos masters de 15 m de perpetuos (kbsingh1399/Backtesting_Data).

Uso: python3 preparar_dados.py <pasta_destino>   (precisa de pandas e pyarrow)
"""
"""Armazem de investigacao: le os masters de 15 m, guarda so as colunas brutas e resume a qualidade."""
import glob, os, sys
import pyarrow.parquet as pq, pandas as pd, numpy as np
ORIG = "/home/user/kbsingh1399/backtesting_data/Binance_Data"
DEST = sys.argv[1]
COLS = ["open_time_ms", "close_time_ms", "open", "high", "low", "close", "volume_base", "volume_quote", "trade_count",
        "taker_buy_vol_btc", "taker_sell_vol_btc", "taker_buy_count", "taker_sell_count",
        "funding_rate_pct", "basis_usd", "open_interest_usd", "oi_change_pct", "long_liq_usd", "short_liq_usd",
        "ls_ratio_global", "ls_ratio_top", "top_account_ratio", "spot_close", "index_close", "is_imputed_metrics"]
resumo = []
for p in sorted(glob.glob(os.path.join(ORIG, "*_15m_master_2020_2026.parquet"))):
    sym = os.path.basename(p).split("_")[0]
    cols = [c for c in COLS if c in pq.ParquetFile(p).schema_arrow.names]
    df = pq.read_table(p, columns=cols).to_pandas()
    df = df.sort_values("open_time_ms").drop_duplicates("open_time_ms").reset_index(drop=True)
    dt = np.diff(df["open_time_ms"].values)
    buracos = int((dt != 900_000).sum())
    ini, fim = pd.to_datetime(df.open_time_ms.iloc[0], unit="ms"), pd.to_datetime(df.open_time_ms.iloc[-1], unit="ms")
    imput = float(df["is_imputed_metrics"].mean()) if "is_imputed_metrics" in df else float("nan")
    def desde(col):
        s = df[col]; ok = s.notna() & (s != 0)
        return str(pd.to_datetime(df.open_time_ms[ok].iloc[0], unit="ms").date()) if ok.any() else "nunca"
    resumo.append((sym, len(df), str(ini.date()), str(fim.date()), buracos, imput, desde("open_interest_usd"), desde("long_liq_usd"), desde("funding_rate_pct"), desde("ls_ratio_top"), float((df.high < df.low).mean()), float((df.close <= 0).mean())))
    df.to_parquet(os.path.join(DEST, sym + "_15m.parquet"), index=False)
r = pd.DataFrame(resumo, columns=["sym", "velas", "inicio", "fim", "buracos", "imputadas", "oi_desde", "liq_desde", "funding_desde", "lsr_top_desde", "h<l", "c<=0"])
pd.set_option("display.width", 250); print(r.to_string(index=False))
