# -*- coding: utf-8 -*-
"""Testes do harness com dados sintéticos (unittest, < 60 s).

Correr: python -m unittest teste_harness -v   (a partir de /home/user/TestRepo/pesquisa)
"""
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402


def df_sintetico(n: int = 2000, semente: int = 0, inicio: str = "2024-01-01 00:00") -> pd.DataFrame:
    """Passeio aleatório de 15 m com todas as colunas do parquet, indexado pela abertura (UTC)."""
    rng = np.random.default_rng(semente)
    idx = pd.date_range(inicio, periods=n, freq="15min", tz="UTC", name="abertura_em")
    r = rng.normal(0, 0.002, n)
    close = 100.0 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[100.0], close[:-1]]) * np.exp(rng.normal(0, 0.0002, n))
    hi = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.001, n)))
    lo = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.001, n)))
    vb = rng.lognormal(5, 0.5, n)
    tb = vb * rng.uniform(0.3, 0.7, n)
    df = pd.DataFrame({
        "open": open_, "high": hi, "low": lo, "close": close,
        "volume_base": vb, "volume_quote": vb * (open_ + close) / 2, "trade_count": rng.integers(100, 1000, n),
        "taker_buy_vol_btc": tb, "taker_sell_vol_btc": vb - tb,
        "taker_buy_count": rng.integers(10, 500, n), "taker_sell_count": rng.integers(10, 500, n),
        "funding_rate_pct": np.repeat(rng.normal(0.01, 0.005, n // 32 + 1), 32)[:n],
        "basis_usd": rng.normal(0, 1, n), "open_interest_usd": 1e9 * np.exp(np.cumsum(rng.normal(0, 0.001, n))),
        "oi_change_pct": rng.normal(0, 0.1, n), "long_liq_usd": -rng.lognormal(10, 1, n),
        "short_liq_usd": rng.lognormal(10, 1, n), "ls_ratio_global": rng.uniform(0.8, 2.5, n),
        "ls_ratio_top": rng.uniform(0.8, 2.5, n), "top_account_ratio": rng.uniform(0.8, 2.5, n),
        "spot_close": close * 0.999, "index_close": close * 0.9995,
        "is_imputed_metrics": (rng.uniform(size=n) < 0.05).astype("int8"),
    }, index=idx)
    df["fecho_em"] = df.index + pd.Timedelta(minutes=15)
    df["imputado"] = df["is_imputed_metrics"].astype(bool)
    df["activo"] = "SIN"
    return df


def df_manual(barras, inicio="2024-01-01 00:00"):
    """DataFrame de 15 m a partir de tuplos (open, high, low, close)."""
    idx = pd.date_range(inicio, periods=len(barras), freq="15min", tz="UTC", name="abertura_em")
    a = np.asarray(barras, dtype=float)
    df = pd.DataFrame({"open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3]}, index=idx)
    df["volume_base"] = 1.0
    df["volume_quote"] = df["close"]
    df["fecho_em"] = df.index + pd.Timedelta(minutes=15)
    df["activo"] = "MAN"
    return df


def sinal_em(df, posicoes):
    s = pd.Series(0, index=df.index, dtype=int)
    for p, lado in posicoes:
        s.iloc[p] = lado
    return s


def iguais_com_nan(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], rtol=0, atol=1e-12)


class TesteDados(unittest.TestCase):
    def test_carregar_parquet(self):
        df = df_sintetico(400)
        bruto = df.drop(columns=["fecho_em", "imputado", "activo"]).reset_index(drop=True)
        bruto.insert(0, "open_time_ms", (df.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1))
        bruto.insert(1, "close_time_ms", bruto["open_time_ms"] + 899_999)
        with tempfile.TemporaryDirectory() as d:
            bruto.sample(frac=1, random_state=1).to_parquet(os.path.join(d, "SINUSDT_15m.parquet"), index=False)
            out = H.carregar("SIN", pasta=d)
            self.assertEqual(len(out), 400)
            self.assertTrue(out.index.is_monotonic_increasing and out.index.is_unique)
            self.assertEqual(str(out.index.tz), "UTC")
            self.assertEqual(out["fecho_em"].iloc[0], out.index[0] + pd.Timedelta(minutes=15))
            self.assertEqual(out["imputado"].dtype, bool)
            self.assertEqual(out["activo"].iloc[0], "SIN")
            self.assertTrue(np.allclose(out["close"].to_numpy(), df["close"].to_numpy()))
            rec = H.carregar("SINUSDT", de="2024-01-01 01:00", ate="2024-01-01 02:00", pasta=d)
            self.assertEqual(len(rec), 5)

    def test_agregar_1h_completa_e_sem_lookahead(self):
        df = df_sintetico(4 * 24 + 3, inicio="2024-01-01 00:15")  # começa desalinhado
        h1 = H.agregar(df, "1h")
        # a 1.a hora (00:00) só tem 3 velas e a última só tem 2: ambas excluídas
        self.assertEqual(h1.index[0], pd.Timestamp("2024-01-01 01:00", tz="UTC"))
        self.assertEqual(len(h1), 24)
        sub = df.loc["2024-01-01 01:00":"2024-01-01 01:45"]
        self.assertEqual(h1["open"].iloc[0], sub["open"].iloc[0])
        self.assertEqual(h1["high"].iloc[0], sub["high"].max())
        self.assertEqual(h1["low"].iloc[0], sub["low"].min())
        self.assertEqual(h1["close"].iloc[0], sub["close"].iloc[-1])
        self.assertAlmostEqual(h1["volume_base"].iloc[0], sub["volume_base"].sum())
        self.assertAlmostEqual(h1["taker_buy_vol_btc"].iloc[0], sub["taker_buy_vol_btc"].sum())
        self.assertAlmostEqual(h1["long_liq_usd"].iloc[0], sub["long_liq_usd"].sum())
        self.assertEqual(h1["funding_rate_pct"].iloc[0], sub["funding_rate_pct"].iloc[-1])
        self.assertEqual(h1["open_interest_usd"].iloc[0], sub["open_interest_usd"].iloc[-1])
        self.assertEqual(h1["fecho_em"].iloc[0], pd.Timestamp("2024-01-01 02:00", tz="UTC"))
        self.assertEqual(h1["imputado"].iloc[0], bool(sub["imputado"].any()))
        h4 = H.agregar(df, "4h")
        self.assertEqual(len(h4), 5)   # 00:00 (15 velas) e o dia seguinte ficam de fora
        self.assertEqual(h4["fecho_em"].iloc[0] - h4.index[0], pd.Timedelta(hours=4))
        d1 = H.agregar(df_sintetico(96 * 3 + 10), "1d")
        self.assertEqual(len(d1), 3)

    def test_ultima_superior_fechada(self):
        fecho = pd.DatetimeIndex(["2024-01-01 01:00", "2024-01-01 02:00"], tz="UTC")
        inst = pd.DatetimeIndex(["2024-01-01 00:45", "2024-01-01 01:00", "2024-01-01 01:15", "2024-01-01 02:00"], tz="UTC")
        self.assertEqual(list(H.ultima_superior_fechada(fecho, inst)), [-1, 0, 0, 1])

    def test_projectar_superior(self):
        df = df_sintetico(40)
        h1 = H.agregar(df, "1h")
        pr = H.projectar_superior(df, h1, ["close"])
        self.assertTrue(pr["close"].iloc[:3].isna().all())       # antes de fechar a 1.a hora
        self.assertEqual(pr["close"].iloc[3], h1["close"].iloc[0])  # vela 00:45 fecha às 01:00
        self.assertEqual(pr["close"].iloc[6], h1["close"].iloc[0])
        self.assertEqual(pr["close"].iloc[7], h1["close"].iloc[1])

    def test_mascarar_imputado(self):
        df = df_sintetico(300)
        s = H.mascarar_imputado(df, "funding_rate_pct")
        self.assertEqual(int(s.isna().sum()), int(df["imputado"].sum()))


class TesteCaracteristicasCausais(unittest.TestCase):
    """Perturba a cauda da série e confirma que nada antes do corte muda."""

    def _par(self):
        a = df_sintetico(1200, semente=1)
        b = a.copy()
        cauda = df_sintetico(1200, semente=2)
        corte = 900
        for c in a.columns:
            if c not in ("fecho_em", "activo"):
                b.iloc[corte:, b.columns.get_loc(c)] = cauda[c].iloc[corte:].to_numpy()
        return a, b, corte

    def _verifica(self, f):
        a, b, corte = self._par()
        fa, fb = f(a), f(b)
        if not fa.index.equals(a.index):          # saídas com outra grelha (p.ex. VR móvel): corta pelo tempo
            fa, fb = fa[fa.index < a.index[corte]], fb[fb.index < a.index[corte]]
            corte = len(fa)
        if isinstance(fa, pd.DataFrame):
            for c in fa.columns:
                self.assertTrue(iguais_com_nan(fa[c].iloc[:corte], fb[c].iloc[:corte]), c)
        else:
            self.assertTrue(iguais_com_nan(fa.iloc[:corte], fb.iloc[:corte]))

    def test_retorno_e_vols(self):
        self._verifica(lambda d: H.retorno_log(d["close"]))
        self._verifica(lambda d: H.vol_realizada_ewma(H.retorno_log(d["close"]), 24))
        self._verifica(lambda d: H.vol_parkinson_ewma(d["high"], d["low"], 24))
        self._verifica(lambda d: H.atr(d, 14))
        self._verifica(lambda d: H.atr(d, 14, "sma"))

    def test_ancoras(self):
        self._verifica(lambda d: H.vwap_movel(d, 48))
        self._verifica(lambda d: H.zscore_ancorado(d, 48, "vwap"))
        self._verifica(lambda d: H.zscore_ancorado(d, 48, "media"))
        self._verifica(lambda d: H.zscore_ancorado(d, 48, "ewma", escala=H.atr(d, 14)))

    def test_extremos_e_fluxo(self):
        self._verifica(lambda d: H.extremos_moveis(d, 20))
        self._verifica(lambda d: H.varrimento(d, 20))
        self._verifica(lambda d: H.posicao_no_intervalo(d, 20))
        self._verifica(lambda d: H.desequilibrio_taker(d, 4))
        self._verifica(lambda d: H.cvd(d))
        self._verifica(lambda d: H.cvd(d, 16))

    def test_robustos(self):
        self._verifica(lambda d: H.z_robusto(H.mascarar_imputado(d, "oi_change_pct"), 96))
        self._verifica(lambda d: H.percentil_movel(d["volume_quote"], 96))
        self._verifica(lambda d: H.racio_variancias_movel(H.retorno_log(d["close"]), 4, 200, passo=50)["vr"])

    def test_projeccao_superior_causal(self):
        def f(d):
            h1 = H.agregar(d, "1h")
            h1["z"] = H.zscore_ancorado(h1, 12, "media")
            return H.projectar_superior(d, h1, ["close", "z"])
        self._verifica(f)


class TesteCaracteristicasValores(unittest.TestCase):
    def test_retorno_log(self):
        s = pd.Series([100.0, 110.0, 99.0])
        r = H.retorno_log(s)
        self.assertTrue(np.isnan(r.iloc[0]))
        self.assertAlmostEqual(r.iloc[1], np.log(1.1))

    def test_extremos_excluem_vela_actual(self):
        df = df_manual([(1, 10, 1, 5), (1, 8, 2, 5), (1, 9, 3, 5), (1, 20, 0.5, 5)])
        ex = H.extremos_moveis(df, 2)
        self.assertEqual(ex["max_anterior"].iloc[3], 9)
        self.assertEqual(ex["min_anterior"].iloc[3], 2)
        self.assertTrue(np.isnan(ex["max_anterior"].iloc[1]))

    def test_varrimento(self):
        # mínimos anteriores (2 velas) = 2; vela 3 fura a 1.5 e fecha a 3: varrimento de mínimos
        df = df_manual([(5, 6, 2, 5), (5, 6, 3, 5), (5, 6, 1.5, 3), (3, 7.5, 2.5, 5.9), (6, 6.5, 5.5, 6)])
        v = H.varrimento(df, 2)
        self.assertEqual(v["varrimento"].iloc[2], 1)
        self.assertEqual(v["nivel"].iloc[2], 2.0)
        self.assertAlmostEqual(v["excesso"].iloc[2], 0.5 / 3)
        # vela 3: máximo anterior = 6, máxima 7.5, fecho 5.9 < 6: varrimento de máximos
        self.assertEqual(v["varrimento"].iloc[3], -1)
        self.assertEqual(v["nivel"].iloc[3], 6.0)
        self.assertEqual(v["varrimento"].iloc[4], 0)

    def test_posicao_no_intervalo(self):
        df = df_manual([(1, 10, 0, 5), (1, 8, 2, 5), (1, 9, 3, 10)])
        p = H.posicao_no_intervalo(df, 3)
        self.assertAlmostEqual(p.iloc[2], 1.0)

    def test_desequilibrio_e_cvd(self):
        df = df_sintetico(10)
        df["taker_buy_vol_btc"] = 3.0
        df["taker_sell_vol_btc"] = 1.0
        self.assertAlmostEqual(H.desequilibrio_taker(df).iloc[0], 0.5)
        self.assertAlmostEqual(H.cvd(df).iloc[-1], 20.0)
        self.assertAlmostEqual(H.cvd(df, 4).iloc[-1], 8.0)

    def test_z_robusto_e_percentil(self):
        rng = np.random.default_rng(3)
        s = pd.Series(rng.normal(0, 1, 5000))
        z = H.z_robusto(s, 500)
        self.assertTrue(0.8 < z.dropna().std() < 1.3)
        self.assertLess(abs(z.dropna().mean()), 0.1)
        cte = pd.Series(np.ones(300))
        self.assertTrue(H.z_robusto(cte, 50).isna().all())
        self.assertTrue((H.z_robusto(cte, 50, escala_min=1.0).dropna() == 0).all())
        p = H.percentil_movel(pd.Series(np.arange(200.0)), 50)
        self.assertTrue((p.dropna() == 1.0).all())

    def test_racio_variancias(self):
        rng = np.random.default_rng(4)
        rw = rng.normal(0, 1, 30000)
        vr, z, n = H.racio_variancias(rw, 4)
        self.assertEqual(n, 30000)
        self.assertTrue(0.93 < vr < 1.07)
        self.assertLess(abs(z), 3.5)
        ar = np.zeros(30000)
        for i in range(1, 30000):
            ar[i] = 0.5 * ar[i - 1] + rng.normal()
        vr2, z2, _ = H.racio_variancias(ar, 2)
        self.assertTrue(1.4 < vr2 < 1.6)       # VR(2) = 1 + rho1
        self.assertGreater(z2, 5)
        self.assertTrue(np.isnan(H.racio_variancias(rw[:5], 4)[0]))
        rho, ep, _ = H.autocorrelacao(ar, 1)
        self.assertTrue(0.45 < rho < 0.55)

    def test_vol_parkinson_constante(self):
        df = df_manual([(100, 110, 100, 105)] * 100)
        vp = H.vol_parkinson_ewma(df["high"], df["low"], 10)
        self.assertAlmostEqual(vp.iloc[-1], np.log(1.1) / np.sqrt(4 * np.log(2)), places=6)


class TesteSimulacao(unittest.TestCase):
    """Entrada 100 na abertura da vela 1; stop 98 (R = 2); alvo 104 (2 R)."""

    def _base(self, barras):
        df = df_manual([(99, 99.5, 98.5, 99)] + barras)
        return df

    def _corre(self, barras, lado=1, stop=98.0, alvo=104.0, n_max=10, **kw):
        df = self._base(barras)
        s = sinal_em(df, [(0, lado)])
        stop_s = pd.Series(stop, index=df.index)
        alvo_s = pd.Series(alvo, index=df.index)
        return H.simular(df, s, stop_s, alvo_s, n_max, **kw)

    def test_alvo_no_nivel_e_custos(self):
        t = self._corre([(100, 101, 99.5, 100.5), (100.5, 104.5, 100, 103)])
        self.assertEqual(len(t), 1)
        r = t.iloc[0]
        self.assertEqual(r["entrada"], 100.0)
        self.assertEqual(r["entrada_em"], pd.Timestamp("2024-01-01 00:15", tz="UTC"))
        self.assertEqual(r["sinal_em"], pd.Timestamp("2024-01-01 00:15", tz="UTC"))
        self.assertEqual(r["motivo"], "alvo")
        self.assertEqual(r["saida"], 104.0)
        self.assertEqual(r["duracao"], 2)
        self.assertEqual(r["saida_em"], pd.Timestamp("2024-01-01 00:45", tz="UTC"))
        self.assertAlmostEqual(r["R_bruto"], 2.0)
        self.assertAlmostEqual(r["R_liquido"], 2.0 - (100 + 104) * 6.5e-4 / 2.0)

    def test_stop_no_nivel(self):
        t = self._corre([(100, 101, 99.5, 100.5), (100.5, 101, 97.5, 99)])
        r = t.iloc[0]
        self.assertEqual(r["motivo"], "stop")
        self.assertEqual(r["saida"], 98.0)
        self.assertAlmostEqual(r["R_bruto"], -1.0)

    def test_stop_com_salto_preenche_na_abertura(self):
        t = self._corre([(100, 101, 99.5, 100.5), (97, 99, 96, 98.5)])
        r = t.iloc[0]
        self.assertEqual(r["motivo"], "stop")
        self.assertEqual(r["saida"], 97.0)
        self.assertAlmostEqual(r["R_bruto"], -1.5)

    def test_alvo_e_stop_na_mesma_vela_conta_stop(self):
        t = self._corre([(100, 101, 99.5, 100.5), (100.5, 104.5, 97.5, 99)])
        self.assertEqual(t.iloc[0]["motivo"], "stop")
        self.assertEqual(t.iloc[0]["saida"], 98.0)

    def test_stop_na_vela_de_entrada(self):
        t = self._corre([(100, 101, 97.9, 100.5), (100.5, 104.5, 100, 103)])
        r = t.iloc[0]
        self.assertEqual(r["motivo"], "stop")
        self.assertEqual(r["duracao"], 1)

    def test_saida_por_tempo(self):
        barras = [(100, 101, 99.5, 100.5), (100.5, 101, 99.5, 100.8), (100.8, 101, 99.5, 101.0), (101, 103, 99, 102)]
        t = self._corre(barras, n_max=3)
        r = t.iloc[0]
        self.assertEqual(r["motivo"], "tempo")
        self.assertEqual(r["saida"], 101.0)
        self.assertEqual(r["duracao"], 3)
        self.assertAlmostEqual(r["R_bruto"], 0.5)
        self.assertEqual(r["saida_em"], pd.Timestamp("2024-01-01 01:00", tz="UTC"))

    def test_fim_dos_dados(self):
        t = self._corre([(100, 101, 99.5, 100.5), (100.5, 101, 99.5, 100.8)], n_max=10)
        self.assertEqual(t.iloc[0]["motivo"], "fim_dados")

    def test_venda_simetrica(self):
        t = self._corre([(100, 100.5, 99, 99.5), (99.5, 102.5, 99, 101)], lado=-1, stop=102.0, alvo=96.0)
        r = t.iloc[0]
        self.assertEqual(r["lado"], -1)
        self.assertEqual(r["motivo"], "stop")
        self.assertEqual(r["saida"], 102.0)
        self.assertAlmostEqual(r["R_bruto"], -1.0)
        t = self._corre([(100, 100.5, 99, 99.5), (103, 103.5, 101, 101)], lado=-1, stop=102.0, alvo=96.0)
        self.assertEqual(t.iloc[0]["saida"], 103.0)
        t = self._corre([(100, 100.5, 99, 99.5), (99.5, 100, 95.5, 97)], lado=-1, stop=102.0, alvo=96.0)
        self.assertEqual(t.iloc[0]["motivo"], "alvo")
        self.assertAlmostEqual(t.iloc[0]["R_bruto"], 2.0)

    def test_stop_e_alvo_em_multiplos_de_escala(self):
        df = self._base([(100, 101, 99.5, 100.5), (100.5, 104.5, 100, 103)])
        s = sinal_em(df, [(0, 1)])
        esc = pd.Series(1.0, index=df.index)
        t = H.simular(df, s, 2.0, 2.0, 10, escala=esc, alvo_em_R=True)
        self.assertEqual(t.iloc[0]["stop"], 98.0)
        self.assertEqual(t.iloc[0]["alvo"], 104.0)
        t = H.simular(df, s, 2.0, 4.0, 10, escala=esc)
        self.assertEqual(t.iloc[0]["alvo"], 104.0)
        with self.assertRaises(ValueError):
            H.simular(df, s, 2.0, 2.0, 10)

    def test_sinais_ignorados(self):
        df = self._base([(100, 101, 99.5, 100.5), (100.5, 101, 99.5, 100.8), (100.8, 101, 99.5, 101.0)])
        # stop do lado errado
        t = H.simular(df, sinal_em(df, [(0, 1)]), pd.Series(101.0, index=df.index), pd.Series(104.0, index=df.index), 10)
        self.assertEqual(len(t), 0)
        self.assertEqual(t.attrs["ignorados"], 1)
        # sinal na última vela
        t = H.simular(df, sinal_em(df, [(3, 1)]), pd.Series(98.0, index=df.index), pd.Series(104.0, index=df.index), 10)
        self.assertEqual(len(t), 0)
        self.assertEqual(t.attrs["ignorados"], 1)

    def test_uma_posicao_de_cada_vez(self):
        barras = [(100, 101, 99.5, 100.5), (100.5, 101, 99.5, 100.8), (100.8, 101, 99.5, 101.0), (101, 101.5, 100, 101)]
        df = self._base(barras)
        s = sinal_em(df, [(0, 1), (1, 1), (3, -1)])
        stop_s = pd.Series([98.0] * 3 + [103.0] * 2, index=df.index)   # o sinal de venda está na vela 3
        alvo_s = pd.Series([104.0] * 3 + [99.0] * 2, index=df.index)
        t = H.simular(df, s, stop_s, alvo_s, 3)
        self.assertEqual(len(t), 2)                      # o 2.o sinal cai dentro do 1.o trade
        self.assertEqual(t.attrs["ignorados"], 1)
        self.assertEqual(t.iloc[1]["lado"], -1)
        t2 = H.simular(df, s, stop_s, alvo_s, 3, uma_posicao=False)
        self.assertEqual(len(t2), 3)

    def test_sinais_aleatorios(self):
        df = df_sintetico(500)
        s = H.sinais_aleatorios(df, 50, semente=1)
        self.assertEqual(int((s != 0).sum()), 50)
        self.assertEqual(s.iloc[-1], 0)
        s2 = H.sinais_aleatorios(df, 50, semente=1)
        self.assertTrue((s == s2).all())

    def test_simulacao_em_dados_sinteticos(self):
        df = df_sintetico(3000, semente=7)
        v = H.varrimento(df, 24)
        t = H.simular(df, v["varrimento"], 1.5, 2.0, 48, escala=H.atr(df, 14), alvo_em_R=True)
        self.assertGreater(len(t), 10)
        self.assertTrue(set(t["motivo"]).issubset({"stop", "alvo", "tempo", "fim_dados"}))
        self.assertTrue((t["entrada_em"] > t["sinal_em"] - pd.Timedelta(minutes=15)).all())
        self.assertTrue((t["saida_em"] > t["entrada_em"]).all())
        # sem sobreposição
        self.assertTrue((t["entrada_em"].iloc[1:].to_numpy() >= t["saida_em"].iloc[:-1].to_numpy()).all())


class TesteMetricas(unittest.TestCase):
    def _trades(self):
        return pd.DataFrame({
            "activo": ["A", "A", "B", "B"],
            "sinal_em": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"], utc=True),
            "saida_em": pd.to_datetime(["2024-01-02", "2024-02-02", "2024-03-02", "2024-04-02"], utc=True),
            "R_liquido": [1.0, -1.0, 2.0, -0.5], "R_bruto": [1.1, -0.9, 2.1, -0.4],
            "motivo": ["alvo", "stop", "alvo", "stop"], "duracao_h": [1.0, 2.0, 3.0, 4.0],
        })

    def test_metricas(self):
        m = H.metricas(self._trades(), "2024-01-01", "2024-04-30")
        g = m["global"]
        self.assertEqual(g["n"], 4)
        self.assertAlmostEqual(g["media"], 0.375)
        self.assertAlmostEqual(g["PF"], 2.0)
        self.assertAlmostEqual(g["taxa_acerto"], 0.5)
        self.assertAlmostEqual(g["dd_max"], 1.0)
        self.assertAlmostEqual(m["por_activo"].loc["A", "soma"], 0.0)
        self.assertAlmostEqual(m["por_activo"].loc["B", "soma"], 1.5)
        self.assertEqual(list(m["por_metade"]["n"]), [2, 2])
        self.assertEqual(list(m["por_ano"].index), [2024])
        vazio = H.metricas(self._trades().iloc[:0])
        self.assertEqual(vazio["global"]["n"], 0)

    def test_criterios_falham(self):
        c = H.criterios(self._trades(), "2024-01-01", "2024-04-30")
        self.assertEqual(list(c.index), ["H1", "H2", "H3", "H4", "H5", "H6"])
        self.assertFalse(c.loc["H1", "passa"])
        self.assertTrue(c.loc["H3", "passa"])
        self.assertFalse(c.loc["H4", "passa"])
        self.assertTrue(c.loc["H5", "passa"])
        self.assertFalse(c.attrs["passa_tudo"])

    def test_criterios_passam(self):
        n = 120
        t = pd.DataFrame({
            "activo": ["A", "B", "C", "A"] * (n // 4),
            "sinal_em": pd.date_range("2024-01-01", periods=n, freq="3D", tz="UTC"),
            "R_liquido": [0.5, 0.4, 0.6, -0.3] * (n // 4), "R_bruto": [0.5, 0.4, 0.6, -0.3] * (n // 4),
            "motivo": ["alvo", "alvo", "alvo", "stop"] * (n // 4), "duracao_h": 1.0,
        })
        t["saida_em"] = t["sinal_em"] + pd.Timedelta(hours=1)
        c = H.criterios(t, "2024-01-01", "2024-12-31")
        self.assertTrue(c["passa"].all(), c)
        self.assertTrue(c.attrs["passa_tudo"])
        # fora do período fica de fora
        c2 = H.criterios(t, "2025-01-01", "2025-12-31")
        self.assertEqual(c2.loc["H1", "valor"], 0)

    def test_registar_ensaio(self):
        m = H.metricas(self._trades())
        with tempfile.TemporaryDirectory() as d:
            p = H.registar_ensaio("fam", {"a": 1, "b": "x"}, m, periodo="IS", pasta=d)
            H.registar_ensaio("fam", {"a": 2, "b": "y"}, m, periodo="IS", pasta=d)
            self.assertEqual(H.contar_ensaios("fam", pasta=d), 2)
            self.assertEqual(H.contar_ensaios("outra", pasta=d), 0)
            lido = pd.read_csv(p)
            self.assertEqual(list(lido["n"]), [4, 4])
            import json
            self.assertEqual(json.loads(lido["parametros"].iloc[1])["a"], 2)
            self.assertEqual(json.loads(lido["por_activo"].iloc[0])["B"], 0.75)


class TesteEstudoEventos(unittest.TestCase):
    def test_retornos_deterministicos(self):
        n = 60
        k = np.arange(n)
        close = np.exp(0.001 * k)
        open_ = np.exp(0.001 * (k - 0.5))
        df = df_manual(list(zip(open_, close * 1.001, open_ * 0.999, close)))
        ev = sinal_em(df, [(10, 1), (30, -1)])
        r = H.estudo_de_eventos(df, ev, [4, 16])
        self.assertEqual(r.loc[4, "n"], 2)
        self.assertAlmostEqual(r.loc[4, "mediana_bps"], 0.0)     # +35 e -35 bps
        r_pos = H.estudo_de_eventos(df, sinal_em(df, [(10, 1)]), [4, 16])
        self.assertAlmostEqual(r_pos.loc[4, "media_bps"], 10 * (4 - 0.5))
        self.assertAlmostEqual(r_pos.loc[16, "media_bps"], 10 * (16 - 0.5))
        r_fecho = H.estudo_de_eventos(df, sinal_em(df, [(10, 1)]), [4], desde_abertura_seguinte=False)
        self.assertAlmostEqual(r_fecho.loc[4, "media_bps"], 40.0)
        # booleano = sem sinal
        rb = H.estudo_de_eventos(df, ev != 0, [4])
        self.assertAlmostEqual(rb.loc[4, "media_bps"], 35.0)
        # horizonte para lá do fim fica de fora
        r_fim = H.estudo_de_eventos(df, sinal_em(df, [(58, 1)]), [4])
        self.assertEqual(r_fim.loc[4, "n"], 0)

    def test_espacamento(self):
        df = df_sintetico(500)
        ev = pd.Series(1, index=df.index)
        r0 = H.estudo_de_eventos(df, ev, [4])
        r1 = H.estudo_de_eventos(df, ev, [4], espacamento_min=10)
        self.assertEqual(r0.loc[4, "n"], 496)
        self.assertEqual(r1.loc[4, "n"], 50)


if __name__ == "__main__":
    unittest.main()
