"""Testes do backtest por eventos (seccao 11). Correr:  python3 testes/teste_backtest.py  (menos de 60 s, sem rede).

Series sinteticas: um OU de 15 m com meia-vida conhecida agregado a 1 h (reversao em
excesso, logo trades com expectancia bruta positiva) e passeios aleatorios com o portao
desligado (expectancia bruta nula: ausencia de lookahead). Determinismo bit a bit,
janelas de walk-forward, aceitacao e metricas com valores a mao.
"""
import math
import os
import random
import shutil
import statistics
import sys
import tempfile
import types
import unittest
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_reversao as bt  # noqa: E402
import nucleo as nu  # noqa: E402
import reversao as rv  # noqa: E402

NAN = float("nan")
T0 = 1_700_000_000_000 - 1_700_000_000_000 % bt.MS_DIA     # meia-noite UTC
SUB = 4
# Parametros reduzidos para o tempo de teste: ancora de 48 h, janela do AR(1) de 480 velas de 1 h,
# limiar fixo t_nulo_max = -3 (sem calibracao por simulacao). A geometria da grelha fica a de partida.
PARAMS_OU = bt.Params(h_a=48.0, n_ajuste=480, n_min=480, n_max=480, calibrar_nula=False, n_z=480)
PARAMS_GBM = bt.Params(h_a=24.0, n_ajuste=96, n_min=96, n_max=96, calibrar_nula=False, n_z=96)


def ou_15m(horas: int, meia_vida_h: float, sigma_eq: float, semente: int, sub: int = SUB) -> List[float]:
    """Caminho de log-preco OU em passos de 15/sub minutos: dx = -theta x dt + sigma sqrt(dt) eps, theta = ln2/H."""
    theta = math.log(2.0) / meia_vida_h
    dt = 0.25 / sub
    sig = sigma_eq * math.sqrt(2.0 * theta)
    g = random.Random(semente)
    x = [0.0]
    for _ in range(horas * 4 * sub):
        x.append(x[-1] - theta * dt * x[-1] + sig * math.sqrt(dt) * g.gauss(0.0, 1.0))
    return x


_CACHE: Dict[str, Any] = {}


def dados_ou(semente: int = 11, horas: int = 2000) -> bt.Dados:
    """OU com H = 8 h e sigma_eq = 2 %, 15 m agregadas a 1 h; guardado em memoria entre testes."""
    chave = "ou-%d-%d" % (semente, horas)
    if chave not in _CACHE:
        v15 = bt.velas_de_trajectoria(ou_15m(horas, 8.0, 0.02, semente), SUB, T0)
        _CACHE[chave] = bt.Dados("OU", bt.agregar_1h_de_15m(v15), v15, {})
    return _CACHE[chave]


def trades_ou() -> List[bt.Trade]:
    if "trades" not in _CACHE:
        d = dados_ou()
        _CACHE["trades"] = bt.simular(PARAMS_OU, d.velas_1h, d.velas_15m, {}, bt.Custos())
    return _CACHE["trades"]


def _trade(i: int, r_liq: float, r_bruto: float = NAN, motivo: str = "alvo", z0: float = -1.8, lado: int = 1,
           g: float = 131.0, l: float = 110.0, custo: float = 8.0, funding: float = 0.0, velas: int = 10,
           p_teo: float = 0.9) -> bt.Trade:
    """Trade construido a mao (so os campos que as metricas leem importam)."""
    if r_bruto != r_bruto:
        r_bruto = r_liq + custo + funding
    return bt.Trade(i, i + velas, T0 + i * bt.MS_15M, T0 + (i + velas) * bt.MS_15M, lado, "agressiva", 100.0,
                    100.0 * math.exp(lado * r_bruto / bt.BPS), 101.0 if lado > 0 else 99.0,
                    97.0 if lado > 0 else 103.0, math.log(100.0) - z0 * 0.01, 0.01, 0.917, 8.0, z0, z0 * 0.01,
                    abs(z0) + 0.3, 6, g, l, 64, p_teo, 0.002, motivo, velas, 20.0, 50.0, r_bruto, funding, custo, r_liq)


# --------------------------------------------------------------------------
# OU com reversao conhecida
# --------------------------------------------------------------------------
class OUComReversao(unittest.TestCase):
    def test_contexto_recupera_a_meia_vida_e_abre_o_portao(self):
        d = dados_ou()
        ctx = bt.contexto_1h(PARAMS_OU, d.velas_1h, d.velas_15m)
        self.assertEqual(len(ctx), len(d.velas_1h))
        verdes = [c for c in ctx if c.estado == rv.VERDE]
        self.assertGreater(len(verdes), 0.5 * len(ctx), "o portao devia abrir na maior parte das horas num OU")
        hs = [c.meia_vida for c in verdes]
        self.assertTrue(4.0 <= nu.mediana(hs) <= 12.0, "H estimado %.2f longe de 8" % nu.mediana(hs))
        self.assertTrue(all(c.t_nulo <= c.t_crit for c in verdes))
        self.assertTrue(all(c.vr < 1.0 for c in verdes))
        # (30): o quantil empirico exclui a vela corrente e so existe com min(n_z, n_min) valores
        self.assertTrue(all(c.q_z != c.q_z for c in ctx[:PARAMS_OU.n_min]))
        self.assertTrue(all(c.q_z == c.q_z for c in ctx[-100:]))
        # antes de n_min velas o ajuste nao e valido e o motivo e 'ajuste'
        self.assertIn("ajuste", ctx[10].motivos)
        self.assertFalse(ctx[10].valido)

    def test_trades_com_expectancia_bruta_positiva(self):
        trades = trades_ou()
        n = len(trades)
        self.assertGreaterEqual(n, 15, "poucos trades num OU de 2000 h")
        brutos = [t.r_bruto_bps for t in trades]
        media = statistics.fmean(brutos)
        ep = statistics.stdev(brutos) / math.sqrt(n)
        self.assertGreater(media, 0.0)
        self.assertGreater(media, 2.0 * ep, "expectancia bruta %.1f bps com EP %.1f" % (media, ep))
        met = bt.metricas(trades, 8.0, 13.0)
        self.assertEqual(met["n"], n)
        self.assertGreater(met["p_hat"], 0.5)
        self.assertGreater(met["expectancia"], 0.0)
        self.assertAlmostEqual(met["custo_medio"], statistics.fmean([t.custo_bps for t in trades]))

    def test_invariantes_de_cada_trade(self):
        p = PARAMS_OU
        c = bt.Custos()
        for t in trades_ou():
            self.assertIn(t.motivo, bt.MOTIVOS_SAIDA)
            self.assertEqual(t.lado, -int(math.copysign(1, t.z_0)))
            self.assertGreaterEqual(abs(t.z_0), p.z_out + p.z_min_resto)       # G3
            self.assertLess(abs(t.z_0), p.z_stop)                               # stop para la da entrada
            self.assertLess(t.ext, p.z_veto)                                    # G4
            self.assertGreater(t.lado * (t.p_alvo - t.p_entrada), 0.0)
            self.assertGreater(t.lado * (t.p_entrada - t.p_stop), 0.0)
            self.assertAlmostEqual(t.p_stop, rv.preco_stop(t.a_0, t.sigma_0, t.lado, p.z_stop))
            self.assertAlmostEqual(t.p_alvo, math.exp(t.a_0 + t.lado * p.z_out * t.sigma_0))
            self.assertAlmostEqual(t.custo_bps, c.custo_entrada("agressiva") + c.custo_saida(t.motivo))
            self.assertAlmostEqual(t.r_liq_bps, t.r_bruto_bps - t.custo_bps - t.funding_bps)
            self.assertAlmostEqual(t.r_bruto_bps, bt.BPS * t.lado * math.log(t.p_saida / t.p_entrada))
            self.assertGreaterEqual(t.g_bps, p.g_min_x_custo * 13.0 - 1e-9)   # G8 com c_L = 13
            self.assertLessEqual(t.velas, t.tmax_velas)
            self.assertGreater(t.i_saida, t.i_entrada)
            if t.motivo == "alvo":
                self.assertAlmostEqual(t.p_saida, t.p_alvo)
            if t.motivo == "stop":
                self.assertAlmostEqual(t.p_saida, t.p_stop)
        # uma entrada por excursao e nunca duas posicoes abertas
        ordenados = sorted(trades_ou(), key=lambda t: t.i_entrada)
        for a, b in zip(ordenados, ordenados[1:]):
            self.assertGreater(b.i_entrada, a.i_saida)

    def test_arrefecimento_e_funding(self):
        d = dados_ou()
        p = bt.Params(**{**PARAMS_OU.__dict__, "arrefecimento_velas": 8})
        base = trades_ou()
        com = bt.simular(p, d.velas_1h, d.velas_15m, {}, bt.Custos())
        stops = [t.i_saida for t in com if t.motivo == "stop"]
        for t in com:
            for s in stops:
                if s < t.i_entrada:
                    self.assertGreater(t.i_entrada, s + 8)
        self.assertLessEqual(len(com), len(base))
        # funding por hora inteira: taxa constante de 1e-4 por hora cobrada so nas horas inteiras dentro do trade
        funding = {v.T: 1e-4 for v in d.velas_1h}
        com_f = bt.simular(PARAMS_OU, d.velas_1h, d.velas_15m, funding, bt.Custos())
        self.assertEqual([(t.i_entrada, t.motivo) for t in com_f], [(t.i_entrada, t.motivo) for t in base])
        for t in com_f:
            horas = sum(1 for v in d.velas_1h if t.t_entrada_ms <= v.t and v.T <= t.t_saida_ms)
            self.assertAlmostEqual(t.funding_bps, t.lado * horas * 1e-4 * bt.BPS, places=9)

    def test_degradada_1h_e_rota_passiva(self):
        d = dados_ou()
        deg = bt.simular(PARAMS_OU, d.velas_1h, [], {}, bt.Custos(), degradada_1h=True)
        self.assertGreater(len(deg), 5)
        for t in deg:
            self.assertLessEqual(t.tmax_velas, PARAMS_OU.tmax_h)
            self.assertEqual(t.t_entrada_ms % bt.MS_1H, 0)
        pas = bt.simular(PARAMS_OU, d.velas_1h, d.velas_15m, {}, bt.Custos(pi_passiva=0.5), rota="passiva", semente=3)
        rotas = {t.rota for t in pas}
        self.assertTrue(rotas <= {"passiva", "passiva_taker"})
        self.assertIn("passiva", rotas)
        for t in pas:
            esperado = 1.5 if t.rota == "passiva" else 4.5 + 2.0 + 1.0
            self.assertAlmostEqual(t.custo_bps - bt.Custos().custo_saida(t.motivo), esperado)
        with self.assertRaises(ValueError):
            bt.simular(PARAMS_OU, d.velas_1h, d.velas_15m, {}, bt.Custos(), rota="outra")


# --------------------------------------------------------------------------
# Nulo GBM com o portao desligado: ausencia de lookahead
# --------------------------------------------------------------------------
class NuloGBM(unittest.TestCase):
    def test_expectancia_bruta_nula_e_liquida_menos_os_custos(self):
        g = bt.nulo_gbm(PARAMS_GBM, 0.004, 0.002, 24, 2026, bt.Custos(imp_bps=0.0), horas=400)
        self.assertGreaterEqual(g["n"], 60, "poucos trades para o teste ter potencia")
        self.assertLessEqual(abs(g["exp_bruta"]), 2.0 * g["ep_bruta"],
                             "media bruta %.2f bps, EP %.2f, n = %d" % (g["exp_bruta"], g["ep_bruta"], g["n"]))
        self.assertAlmostEqual(g["exp_liq"], g["exp_bruta"] - g["custo_medio"], places=9)
        self.assertGreaterEqual(g["custo_medio"], 7.0)      # entrada 5,5 + saida no alvo 1,5
        self.assertLessEqual(g["custo_medio"], 10.0)        # entrada 5,5 + saida a taker 4,5
        self.assertLessEqual(abs(g["exp_liq"] + g["custo_medio"]), 2.0 * g["ep_liq"])
        self.assertTrue(0.0 <= g["p_nulo"] <= 1.0)
        self.assertEqual(g["n_caminhos"], 24)

    def test_portao_ligado_quase_nao_entra_num_passeio_aleatorio(self):
        x = rv.simular_gbm(4 * 800 * SUB + 1, 0.002 / math.sqrt(SUB), 77)
        v15 = bt.velas_de_trajectoria(x, SUB, T0)
        v1h = bt.agregar_1h_de_15m(v15)
        com = bt.simular(PARAMS_GBM, v1h, v15, {}, bt.Custos(), portao=True)
        sem = bt.simular(PARAMS_GBM, v1h, v15, {}, bt.Custos(), portao=False)
        self.assertLess(len(com), max(1, len(sem)))
        self.assertGreater(len(sem), 0)


# --------------------------------------------------------------------------
# Determinismo bit a bit
# --------------------------------------------------------------------------
class Determinismo(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="backtest_teste_")

    def tearDown(self):
        shutil.rmtree(self.pasta, ignore_errors=True)

    def test_duas_corridas_dao_ficheiros_identicos(self):
        d = dados_ou()
        a = bt.simular(PARAMS_OU, d.velas_1h, d.velas_15m, {}, bt.Custos(pi_passiva=0.5), rota="passiva", semente=5)
        b = bt.simular(PARAMS_OU, d.velas_1h, d.velas_15m, {}, bt.Custos(pi_passiva=0.5), rota="passiva", semente=5)
        self.assertGreater(len(a), 0)
        self.assertEqual([repr(t) for t in a], [repr(t) for t in b])
        fa, fb = os.path.join(self.pasta, "a.csv"), os.path.join(self.pasta, "b.csv")
        bt.escrever_trades(fa, a)
        bt.escrever_trades(fb, b)
        with open(fa, "rb") as f1, open(fb, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())
        lidos = bt.ler_trades(fa)
        self.assertEqual(len(lidos), len(a))
        self.assertAlmostEqual(lidos[0].r_liq_bps, a[0].r_liq_bps, places=6)
        c = bt.simular(PARAMS_OU, d.velas_1h, d.velas_15m, {}, bt.Custos(pi_passiva=0.5), rota="passiva", semente=6)
        self.assertNotEqual([repr(t) for t in a], [repr(t) for t in c], "outra semente, outros preenchimentos")

    def test_nulo_e_placebo_deterministas(self):
        g1 = bt.nulo_gbm(PARAMS_GBM, 0.004, 0.002, 3, 9, bt.Custos(), horas=200)
        g2 = bt.nulo_gbm(PARAMS_GBM, 0.004, 0.002, 3, 9, bt.Custos(), horas=200)
        self.assertEqual(repr(g1), repr(g2))
        d = dados_ou()
        p1 = bt.placebo_ancora(PARAMS_OU, d, 2, 4, bt.Custos())
        p2 = bt.placebo_ancora(PARAMS_OU, d, 2, 4, bt.Custos())
        self.assertEqual(repr(p1), repr(p2))
        self.assertEqual(len(p1["us"]), 2)
        self.assertTrue(all(48 <= u <= 240 for u in p1["us"]))


# --------------------------------------------------------------------------
# Janelas de walk-forward, patamar e walk_forward
# --------------------------------------------------------------------------
class JanelasWalkForward(unittest.TestCase):
    def test_purga_e_embargo(self):
        n, is_v, oos_v, purga = 4992, 2160, 720, 16          # 208 dias de 1 h, seccao 11.3
        jan = bt.janelas_walk_forward(n, is_v, oos_v, purga, 0.01)
        self.assertEqual(len(jan), 4)
        embargo = math.ceil(0.01 * n)
        for (is_ini, is_fim, oos_ini, oos_fim) in jan:
            self.assertEqual(is_fim - is_ini, is_v)
            self.assertEqual(oos_ini - is_fim, purga + embargo)        # purga mais embargo entre IS e OOS
            self.assertLess(is_fim, oos_ini)
            self.assertLessEqual(oos_fim, n)
            self.assertGreater(oos_fim - oos_ini, 0)
        for a, b in zip(jan, jan[1:]):
            self.assertEqual(b[2], a[3], "as OOS encadeiam-se sem sobreposicao")
            self.assertEqual(b[0] - a[0], oos_v)
        self.assertEqual(jan[0], (0, 2160, 2160 + purga + embargo, 2160 + purga + embargo + 720))
        self.assertEqual(jan[-1][3], n)                                 # ultima OOS truncada mas com mais de metade
        self.assertEqual(bt.janelas_walk_forward(1000, 500, 100, 0, 0.0)[0], (0, 500, 500, 600))
        self.assertEqual(bt.janelas_walk_forward(52 * 96, 90 * 96, 30 * 96, 64, 0.01), [], "52 dias de 15 m: nenhuma")
        self.assertEqual(bt.janelas_walk_forward(0, 10, 5, 0, 0.0), [])
        with self.assertRaises(ValueError):
            bt.janelas_walk_forward(100, 0, 5, 0, 0.0)
        with self.assertRaises(ValueError):
            bt.janelas_walk_forward(100, 10, 5, 0, 1.5)

    def test_patamar_escolhe_o_centro_e_nao_o_maximo(self):
        base = bt.Params()
        grelha = bt.grelha_declarada(base, {"z_in": (1.8, 2.0, 2.2), "z_stop": (2.75, 3.0, 3.5)})
        self.assertEqual(len(grelha), 9)
        res = {}
        for p in grelha:
            res[p] = 10.0 if (p.z_in, p.z_stop) != (1.8, 2.75) else 100.0
        res[bt.Params(z_in=2.0, z_stop=2.75)] = -5.0          # vizinho do maximo isolado
        escolhido = bt.patamar(res)
        self.assertNotEqual((escolhido.z_in, escolhido.z_stop), (1.8, 2.75), "o maximo isolado nunca e escolhido")
        self.assertEqual(res[escolhido], 10.0)
        # todos negativos: devolve a maior media da vizinhanca, sem falhar
        self.assertIn(bt.patamar({p: -1.0 for p in grelha}), grelha)
        with self.assertRaises(ValueError):
            bt.patamar({})
        toda = bt.grelha_declarada(base)
        self.assertEqual(len(toda), 216)                        # a de partida e um dos 216 pontos
        self.assertEqual(toda[0], base)
        self.assertEqual(len({p.hash() for p in toda}), 216)

    def test_walk_forward_sobre_o_ou(self):
        d = dados_ou()
        cfg = types.SimpleNamespace(is_dias=30.0, oos_dias=10.0, embargo_frac=0.01, n_grelha_min=10 ** 6,
                                    c_w_bps=8.0, c_l_bps=13.0, custos=bt.Custos(), semente=1)
        res = bt.walk_forward([PARAMS_OU], d, cfg)
        self.assertFalse(res["grelha_corrida"])
        self.assertFalse(res["sanidade"])
        self.assertGreaterEqual(len(res["janelas"]), 2)
        for j in res["janelas"]:
            self.assertEqual(j["escolhido"], PARAMS_OU.hash())
        self.assertEqual(len(res["oos_trades"]), sum(j["n_oos"] for j in res["janelas"]))
        self.assertEqual(res["met_oos"]["n"], len(res["oos_trades"]))
        self.assertEqual(len(res["trocos"]), 4)
        self.assertEqual(res["n_total"], len(trades_ou()))
        self.assertTrue(res["pbo"] != res["pbo"], "sem grelha nao ha PBO")
        self.assertTrue(0.0 <= res["p_nulo"] <= 1.0)
        texto = bt.relatorio_texto(res)
        self.assertIn("WALK-FORWARD", texto)
        self.assertIn("ACEITACAO", texto)
        for ch in texto:
            self.assertLess(ord(ch), 128, "relatorio em ASCII")
        # grelha pequena corrida: escolha por janela e PBO calculado
        cfg.n_grelha_min = 5
        grelha = bt.grelha_declarada(PARAMS_OU, {"z_out": (0.25, 0.5), "z_min_resto": (0.5, 0.75)})
        res2 = bt.walk_forward(grelha, d, cfg)
        self.assertTrue(res2["grelha_corrida"])
        self.assertEqual(res2["n_configs"], 4)
        self.assertEqual(len(res2["ensaios"]), 4)
        self.assertTrue(0.0 <= res2["pbo"] <= 1.0)
        self.assertEqual(len(res2["matriz_pbo"][0]), 4)


# --------------------------------------------------------------------------
# Aceitacao e metricas com valores a mao
# --------------------------------------------------------------------------
def resultados_bons() -> dict:
    return {
        "met_oos": {"n": 150, "expectancia": 40.0, "ep": 10.0, "t": 4.0, "p_inf": 0.62, "p_estrela": 0.50, "sr": 0.4},
        "p_nulo": 0.55, "mediana_oos": 30.0, "fraccao_positiva": 0.75, "wfe": 0.7, "pbo": 0.1, "dsr": 0.97,
        "gbm": {"q95_liq": 20.0}, "placebo": {"exp": 5.0, "ep": 6.0}, "trocos": [10.0, 20.0, 5.0, 40.0],
        "vivo": {"n": 120, "r_3600_medio": 50.0, "custos": 10.5},
    }


class Aceitacao(unittest.TestCase):
    def test_tudo_certo(self):
        ok, motivos = bt.aceitacao(resultados_bons(), None)
        self.assertTrue(ok, motivos)
        self.assertEqual(motivos, [])

    def test_cada_criterio(self):
        casos = [
            ({"met_oos": {**resultados_bons()["met_oos"], "n": 99}}, "n_oos"),
            ({"met_oos": {**resultados_bons()["met_oos"], "p_inf": 0.59}}, "p_inf"),      # 0,59 < max(0,50, 0,55) + 0,05
            ({"p_nulo": 0.58}, "p_inf"),
            ({"met_oos": {**resultados_bons()["met_oos"], "t": 2.9}}, "t"),
            ({"mediana_oos": -1.0}, "mediana_oos"),
            ({"fraccao_positiva": 0.5}, "fraccao_positiva"),
            ({"wfe": 0.4}, "wfe"),
            ({"pbo": 0.25}, "pbo"),
            ({"dsr": 0.9}, "dsr"),
            ({"gbm": {"q95_liq": 45.0}}, "gbm"),
            ({"gbm": None}, "gbm"),
            ({"placebo": {"exp": 30.0, "ep": 6.0}}, "placebo"),     # 40 - 30 = 10 < 2 sqrt(100 + 36)
            ({"placebo": None}, "placebo"),
            ({"trocos": [10.0, -1.0, 5.0, 40.0]}, "trocos"),
            ({"vivo": {"n": 50, "r_3600_medio": 50.0, "custos": 10.5}}, "vivo"),
            ({"vivo": None}, "vivo"),
            ({"vivo": {"n": 120, "r_3600_medio": 80.0, "custos": 10.5}}, "vivo"),   # 69,5 - 40 > 2 x 10
        ]
        for alteracao, esperado in casos:
            r = resultados_bons()
            r.update(alteracao)
            ok, motivos = bt.aceitacao(r, None)
            self.assertFalse(ok, (alteracao, motivos))
            self.assertEqual(motivos, [esperado], (alteracao, motivos))

    def test_limiares_pelo_cfg_e_varias_falhas(self):
        r = resultados_bons()
        cfg = types.SimpleNamespace(n_oos_min=200, t_min=5.0)
        ok, motivos = bt.aceitacao(r, cfg)
        self.assertFalse(ok)
        self.assertEqual(motivos, ["n_oos", "t"])
        ok, motivos = bt.aceitacao({}, None)
        self.assertFalse(ok)
        self.assertEqual(len(motivos), 12)


class Metricas(unittest.TestCase):
    def test_valores_a_mao(self):
        trades = [_trade(0, 100.0, motivo="alvo"), _trade(20, -50.0, motivo="stop", custo=13.0),
                  _trade(40, 20.0, motivo="tempo", custo=13.0), _trade(60, -30.0, motivo="stop", custo=13.0),
                  _trade(80, -10.0, motivo="tempo", custo=13.0)]
        m = bt.metricas(trades, 8.0, 13.0)
        r = [100.0, -50.0, 20.0, -30.0, -10.0]
        self.assertEqual(m["n"], 5)
        self.assertAlmostEqual(m["expectancia"], 6.0)
        self.assertAlmostEqual(m["ep"], statistics.stdev(r) / math.sqrt(5))
        self.assertAlmostEqual(m["t"], 6.0 / (statistics.stdev(r) / math.sqrt(5)))
        self.assertAlmostEqual(m["p_hat"], 0.4)
        self.assertAlmostEqual(m["p_inf"], rv.wilson_inferior(0.4, 5))
        self.assertAlmostEqual(m["p_estrela"], 0.5)                     # (66) com G = 131, L = 110, 8 e 13
        self.assertAlmostEqual(m["pf"], 120.0 / 90.0)
        self.assertAlmostEqual(m["ganho_medio"], 60.0)
        self.assertAlmostEqual(m["perda_media"], -30.0)
        self.assertAlmostEqual(m["razao_ganho_perda"], 2.0)
        self.assertAlmostEqual(m["mediana"], -10.0)
        self.assertAlmostEqual(m["q05"], -46.0)                          # tipo 7: -50 + 0,2 x 20
        self.assertAlmostEqual(m["q95"], 84.0)
        self.assertAlmostEqual(m["dd_max"], 70.0)                        # acumulado 100, 50, 70, 40, 30
        self.assertEqual(m["perdas_seguidas"], 2)
        self.assertAlmostEqual(m["duracao_media"], 10.0)
        self.assertAlmostEqual(m["motivos"]["alvo"], 0.2)
        self.assertAlmostEqual(m["motivos"]["stop"], 0.4)
        self.assertAlmostEqual(m["motivos"]["tempo"], 0.4)
        self.assertAlmostEqual(m["motivos"]["invalidacao"], 0.0)
        self.assertAlmostEqual(m["custo_medio"], 12.0)
        self.assertAlmostEqual(m["exp_bruta"], 18.0)
        self.assertAlmostEqual(m["mae"], 20.0)
        self.assertAlmostEqual(m["mfe"], 50.0)
        self.assertAlmostEqual(m["dias"], 90 * bt.MS_15M / bt.MS_DIA)
        self.assertAlmostEqual(m["sinais_por_dia"], 5.0 / m["dias"])
        sr, g3, g4 = rv.sharpe_trade(r)
        self.assertAlmostEqual(m["sr"], sr)
        self.assertAlmostEqual(m["g3"], g3)
        self.assertAlmostEqual(m["brier"], (0.01 + 4 * 0.81) / 5)       # P_teo 0,9 contra 1 alvo e 4 nao
        self.assertTrue(0.0 <= m["ece"] <= 1.0)
        vazio = bt.metricas([], 8.0, 13.0)
        self.assertEqual(vazio["n"], 0)
        self.assertTrue(vazio["expectancia"] != vazio["expectancia"])

    def test_trocos_dsr_pbo(self):
        trades = [_trade(10 * i, float(i)) for i in range(8)]
        self.assertEqual(bt.consistencia_4_trocos(trades), [0.5, 2.5, 4.5, 6.5])
        t3 = bt.consistencia_4_trocos(trades[:3])
        self.assertTrue(t3[0] != t3[0] and t3[1] == 0.0 and t3[2] == 1.0 and t3[3] == 2.0)
        with self.assertRaises(ValueError):
            bt.consistencia_4_trocos(trades, 0)
        # DSR: um so ensaio nao deflaciona (SR* = 0): DSR = PSR(0)
        self.assertAlmostEqual(bt.dsr([0.3], 0.3, 100, 0.0, 3.0), rv.psr(0.3, 0.0, 100, 0.0, 3.0))
        self.assertLess(bt.dsr([0.1, 0.3, 0.5, 0.2, 0.4], 0.3, 100, 0.0, 3.0), bt.dsr([0.3], 0.3, 100, 0.0, 3.0))
        g = random.Random(1)
        matriz = [[g.gauss(0.0, 1.0) for _ in range(6)] for _ in range(64)]
        self.assertTrue(0.3 <= bt.pbo(matriz) <= 0.8, "sem edge o PBO anda perto de 0,5")
        com_edge = [[linha[0] + 2.0] + linha[1:] for linha in matriz]
        self.assertLess(bt.pbo(com_edge), 0.1)
        with self.assertRaises(ValueError):
            bt.pbo(matriz, 15)


# --------------------------------------------------------------------------
# Ficheiros de velas e funding
# --------------------------------------------------------------------------
class Ficheiros(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="backtest_velas_")

    def tearDown(self):
        shutil.rmtree(self.pasta, ignore_errors=True)

    def _escrever(self, nome: str, linhas: List[str], cab: str = "t,T,o,h,l,c,v,n") -> None:
        with open(os.path.join(self.pasta, nome), "w", encoding="utf-8") as f:
            f.write(cab + "\n" + "\n".join(linhas) + "\n")

    def test_carregar_velas_ordena_remove_repetidas_e_marca_buracos(self):
        dt = bt.MS_15M
        t1 = T0 + 23 * bt.MS_1H + 3 * dt                     # ultima vela do dia 1
        cab = "t,T,o,h,l,c,v,n,v_b,v_a,flx,corte"
        self._escrever("BTC_15m_2023-11-14.csv", [
            "%d,%d,100,101,99,100.5,3,7,10,5,0.2," % (t1, t1 + dt),
        ], cab)
        t2 = t1 + dt
        self._escrever("BTC_15m_2023-11-15.csv", [
            "%d,%d,100.5,102,100,101,2,4,,,," % (t2 + dt, t2 + 2 * dt),       # fora de ordem
            "%d,%d,100.5,101,100,100.8,2,4,1,1,0.1,1" % (t2, t2 + dt),        # corte = 1
            "%d,%d,100.5,101,100,100.9,2,4,1,1,0.1,0" % (t2, t2 + dt),        # repetida: fica esta
            "%d,%d,101,103,100,102,1,2,,,," % (t2 + 4 * dt, t2 + 5 * dt),     # buraco antes
            "%d,%d,x,103,100,102,1,2,,,," % (t2 + 5 * dt, t2 + 6 * dt),       # preco ilegivel: ignorada
        ], cab)
        self._escrever("BTC_1h_2023-11-15.csv", ["%d,%d,1,2,0.5,1.5,1,1" % (T0, T0 + bt.MS_1H)])
        self._escrever("ETH_15m_2023-11-15.csv", ["%d,%d,1,2,0.5,1.5,1,1" % (t2, t2 + dt)])
        velas = bt.carregar_velas(self.pasta, "BTC", "15m")
        self.assertEqual([v.t for v in velas], [t1, t2, t2 + dt, t2 + 4 * dt])
        self.assertEqual(velas[1].c, 100.9)
        self.assertEqual([v.completa for v in velas], [True, True, True, False])
        self.assertEqual(velas[0].n, 7)
        self.assertEqual(bt.contar_buracos(velas), 1)
        cols = bt.carregar_colunas_baleias(self.pasta, "BTC")
        self.assertEqual(len(cols), 4)
        self.assertAlmostEqual(cols[0]["flx"], 0.2)
        self.assertTrue(cols[2]["flx"] != cols[2]["flx"])
        self.assertEqual(len(bt.carregar_velas(self.pasta, "BTC", "1h")), 1)
        self.assertEqual(bt.carregar_velas(self.pasta, "SOL", "15m"), [])
        self.assertEqual(bt.carregar_velas(os.path.join(self.pasta, "nao_existe"), "BTC", "15m"), [])
        with self.assertRaises(ValueError):
            bt.intervalo_ms("15x")
        self.assertEqual(bt.intervalo_ms("1h"), bt.MS_1H)
        v = bt.interpretar_vela({"t": "1000", "o": "1", "h": "2", "l": "0.5", "c": "1.5"}, 900_000)
        self.assertEqual((v.t, v.T, v.v, v.n, v.completa), (1000, 901_000, 0.0, 0, True))
        self.assertIsNone(bt.interpretar_vela({"t": "1000", "o": "1", "h": "0.4", "l": "0.5", "c": "1.5"}, 900_000))

    def test_carregar_funding_e_registar_ensaio(self):
        os.makedirs(os.path.join(self.pasta, "funding"))
        with open(os.path.join(self.pasta, "funding", "BTC.csv"), "w", encoding="utf-8") as f:
            f.write("time,fundingRate,premium\n%d,0.0000125,0.0001\n%d,-0.00001,\n%d,x,\n" % (
                T0 + 17, T0 + bt.MS_1H, T0 + 2 * bt.MS_1H))
        fd = bt.carregar_funding(os.path.join(self.pasta, "funding"), "BTC")
        self.assertEqual(fd, {T0: 0.0000125, T0 + bt.MS_1H: -0.00001})
        self.assertEqual(bt.carregar_funding(os.path.join(self.pasta, "funding"), "ETH"), {})
        caminho = os.path.join(self.pasta, "ensaios.csv")
        met = bt.metricas([_trade(0, 10.0), _trade(10, -5.0), _trade(20, 7.0)], 8.0, 13.0)
        met.update({"ativo": "BTC", "variante": "A", "rota": "agressiva", "periodo": "x a y"})
        bt.registar_ensaio(caminho, bt.Params(), met)
        bt.registar_ensaio(caminho, bt.Params(z_in=2.2), met)
        with open(caminho, encoding="utf-8") as f:
            linhas = f.read().strip().splitlines()
        self.assertEqual(len(linhas), 3)
        self.assertTrue(linhas[0].startswith("data,ativo,variante,rota,hash,z_in"))
        self.assertIn(bt.Params().hash(), linhas[1])
        self.assertEqual(len(bt.ler_sr_ensaios(caminho)), 2)
        self.assertNotEqual(bt.Params().hash(), bt.Params(z_in=2.2).hash())
        self.assertEqual(len(bt.Params().hash()), 6)
        with self.assertRaises(ValueError):
            bt.validar_params(bt.Params(z_out=2.5))


if __name__ == "__main__":
    unittest.main(verbosity=1)
