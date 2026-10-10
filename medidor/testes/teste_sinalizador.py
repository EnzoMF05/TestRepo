"""Testes das pecas do sinalizador que nao precisam de rede. Correr:  python3 testes/teste_sinalizador.py

Pasta temporaria com um config.ini minimo ([geral], [sombra] e [sinalizador]), como a
classe Base do medidor. O contexto de 1 h e construido a mao (AjusteOU e Contexto VERDE)
para que o gatilho, o trade virtual, os disjuntores e o relatorio se testem sem velas de
historico nem calibracao. Nada aqui abre ligacoes: as duas tarefas de rede que se tocam
(CoinGlass) sao substituidas por funcoes que falham de proposito.
"""
import asyncio
import builtins
import contextlib
import csv
import io
import json
import logging
import math
import os
import random
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_reversao as bt  # noqa: E402
import medidor as md  # noqa: E402
import nucleo as nu  # noqa: E402
import reversao as rv  # noqa: E402
import sinalizador as sn  # noqa: E402

# Sem handlers o logging escreveria os avisos no stderr durante os testes; os testes que
# precisam de ler o log instalam o seu proprio handler (classe Captura).
logging.getLogger("sinalizador").addHandler(logging.NullHandler())
logging.getLogger("medidor").addHandler(logging.NullHandler())

NAN = float("nan")
MS_15M = sn.MS_15M
MS_1H = sn.MS_1H
LAM_96 = rv.lambda_ancora(96.0)
PHI_8 = 2.0 ** (-1.0 / 8.0)          # phi_c de uma meia-vida de 8 velas de 1 h
A0 = math.log(100.0)                 # ancora em vigor: preco 100
SIG = 0.01                           # sigma_eq em vigor: 1 %
T0 = 1704880800000                   # 2024-01-10T10:00:00Z (quarta-feira, sessao europa), hora certa
CHAVE = "SEGREDO-COINGLASS-XYZ-123"


def _ms(ano: int, mes: int, dia: int, hora: int, minuto: int = 0) -> int:
    return int(datetime(ano, mes, dia, hora, minuto, tzinfo=timezone.utc).timestamp() * 1000)


def _ajuste(phi_c: float = PHI_8, t_nulo: float = -5.0, n: int = 720, sigma_eq: float = SIG) -> rv.AjusteOU:
    """AjusteOU valido construido a mao: meia-vida 8 h, t_nulo bem abaixo do limiar."""
    theta = -math.log(phi_c)
    return rv.AjusteOU(phi_hat=phi_c / (1 + 2.0 / n), phi_c=phi_c, s2=sigma_eq ** 2 * (1 - phi_c ** 2), se_rob=0.002,
                       t_nulo=t_nulo, theta=theta, meia_vida=math.log(2.0) / theta, sigma_eq=sigma_eq,
                       theta_x=math.log(LAM_96 / phi_c), se_theta=0.002 / phi_c, n=n, valido=True)


def _vela(k: int, z: float, z_lo: Optional[float] = None, z_hi: Optional[float] = None, n: int = 10,
          amp: float = 0.4, a: float = A0, sig: float = SIG, t0: int = T0, ms: int = MS_15M) -> rv.Vela:
    """Vela k (a contar de t0) cujo fecho fica a z sigma da ancora; minima e maxima a amp sigma (ou a z_lo e z_hi).

    T = t + intervalo - 1 ms, como a Hyperliquid envia no canal candle e no candleSnapshot
    (exemplo documentado: t=1681923600000, T=1681924499999): os fechos de hora, o funding e
    o arrefecimento tem de funcionar com velas cujo T acaba em 59:59.999.
    """
    c = math.exp(a + z * sig)
    lo = math.exp(a + (z - amp if z_lo is None else z_lo) * sig)
    hi = math.exp(a + (z + amp if z_hi is None else z_hi) * sig)
    return rv.Vela(t0 + k * ms, t0 + (k + 1) * ms - 1, c, hi, lo, c, 1.0, n, True)


def _fechado(t_ms: int, r_liq: float, motivo: str = "alvo", g: float = 124.0, l: float = 120.0) -> Dict[str, Any]:
    """Um trade virtual fechado no formato de ActivoSinal.fechados."""
    return {"t_ms": t_ms, "r_liq_bps": r_liq, "motivo": motivo, "G": g, "L": l, "fase": "cal", "z_0": -1.8}


class Captura(logging.Handler):
    """Guarda as mensagens do log do sinalizador durante um teste."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.mensagens: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.mensagens.append(self.format(record))

    def texto(self) -> str:
        return "\n".join(self.mensagens)


class Base(unittest.TestCase):
    EXTRA = ""
    INI = ("[geral]\nativos = BTC, ETH\n[sombra]\njanela_sinal_s = 900\n"
           "horizontes_s = 5, 30, 60, 300, 900, 3600, 14400\nhorizonte_regra_s = 3600\n[sinalizador]\n"
           "emitir = sim\n")                     # os testes das pecas de emissao precisam dela ligada

    def setUp(self) -> None:
        self.pasta = tempfile.mkdtemp(prefix="sinalizador_teste_")
        self.ini = os.path.join(self.pasta, "config.ini")
        self.cfg = self.config(self.EXTRA)
        self.captura = Captura()
        self.captura.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        log = logging.getLogger("sinalizador")
        self._nivel = log.level
        log.setLevel(logging.DEBUG)                 # tambem os INFO (latencia da escrita, trades fechados)
        log.addHandler(self.captura)
        sn._AVISO_MEDIDOR["ms"] = 0

    def tearDown(self) -> None:
        log = logging.getLogger("sinalizador")
        log.removeHandler(self.captura)
        log.setLevel(self._nivel)
        shutil.rmtree(self.pasta, ignore_errors=True)

    def config(self, extra: str, nome: str = "config.ini") -> sn.ConfigSinalizador:
        caminho = os.path.join(self.pasta, nome)
        base = self.INI
        if "emitir =" in extra:                     # o teste decide a emissao; evita a opcao duplicada
            base = base.replace("emitir = sim\n", "")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(base + extra)
        return sn.ConfigSinalizador(caminho)

    def ler(self, caminho: str) -> List[Dict[str, str]]:
        with open(caminho, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def texto(self, caminho: str) -> str:
        with open(caminho, encoding="utf-8") as f:
            return f.read()

    # ---- contexto de 1 h construido a mao (seccao 4) ---------------------------------
    def preparar(self, at: sn.ActivoSinal, t_ctx: int = T0, t_nulo: float = -5.0, vr: float = 0.8) -> None:
        """Contexto VERDE em vigor: ancora em 100, sigma_eq 1 %, H = 8 h, k_max 64, z_in_ef 2."""
        aj = _ajuste(t_nulo=t_nulo)
        at.aj = aj
        at.t_crit = -3.0
        at.ctx = rv.classificar_contexto(aj, vr, -1.0, 1.0, False, at.t_crit, self.cfg.p_ctx)
        at.a_ult, at.sigma_ult, at.phi_ult, at.h_ult = A0, SIG, aj.phi_c, aj.meia_vida
        at.sigma_r = 0.005
        at.ctx_ms = t_ctx
        at.k_max = rv.k_maximo(aj.meia_vida, self.cfg.p.k_h, self.cfg.p.k_max_tecto)
        at.z_in_ef = self.cfg.p.z_in

    def fechar(self, at: sn.ActivoSinal, k: int, z: float, **kw: Any) -> Tuple[Optional[rv.Sinal], str, Optional[Dict[str, Any]]]:
        """Fecho de 15 m como em Sinalizador._fechar: trade virtual primeiro, gatilho depois; contexto fresco."""
        v = _vela(k, z, **kw)
        at.ctx_ms = max(at.ctx_ms, v.t)      # o ultimo fecho de 1 h nunca tem mais de 75 min
        linha = at.avancar_trade_virtual(v)
        sinal, motivo = at.fecho_15m(v, v.T + 500)
        return sinal, motivo, linha


# ==========================================================================
# Config
# ==========================================================================
class Config(Base):
    def test_minimo_carrega_com_os_defeitos(self):
        cfg = self.cfg
        self.assertEqual(cfg.ativos, ["BTC", "ETH"])
        self.assertEqual((cfg.p.h_a, cfg.p.n_ajuste, cfg.p.n_min, cfg.p.n_max), (96.0, 720, 480, 2160))
        self.assertTrue(cfg.p.calibrar_nula)
        self.assertEqual((cfg.p.z_in, cfg.p_gat.z_out, cfg.p.z_stop, cfg.p_ctx.z_veto), (2.0, 0.5, 3.0, 4.0))
        self.assertEqual((cfg.p_ctx.h_min, cfg.p_ctx.h_max, cfg.p_ctx.t_amarelo), (3.0, 24.0, -2.0))
        self.assertEqual((cfg.p_tam.n_cal, cfg.p_tam.tamanho_base, cfg.p_tam.k_kelly), (30, 1000.0, 0.25))
        self.assertEqual((cfg.p_disj.perdas_seguidas, cfg.p_disj.perdas_dia_x_g, cfg.p_disj.cvar_x_l), (5, 3.0, 2.0))
        self.assertEqual((cfg.p_bal.b_veto, cfg.p_bal.fb_alto, cfg.p.capital_usd, cfg.p.expo_max), (-2, 1.25, 20000.0, 0.75))
        self.assertEqual((cfg.p.arrefecimento_velas, cfg.p.g_min_x_custo, cfg.p.c_w_bps, cfg.p.c_l_bps), (2, 3.0, 8.0, 13.0))
        self.assertEqual(cfg.p.veto_sessao, ())
        self.assertEqual(len(cfg.p.enderecos_excluidos), 2)
        self.assertAlmostEqual(cfg.lam_a, LAM_96)
        self.assertEqual(cfg.liqsrc, "na")
        self.assertRegex(cfg.variante, r"^[0-9a-f]{6}$")
        self.assertEqual(cfg.pasta_velas, os.path.join(self.pasta, "dados", "velas"))
        self.assertEqual(cfg.reg_sinalizador, os.path.join(self.pasta, "dados", "registo_sinalizador.csv"))
        self.assertIn(3600, cfg.horizontes)
        self.assertEqual(self.captura.mensagens, [])          # nada a avisar com o config minimo

    def test_parametro_invalido_da_systemexit_em_portugues(self):
        for extra, chave in (("z_out = 2.5\n", "z_out"), ("h_min = 30\n", "h_min"), ("capital_usd = 0\n", "capital_usd"),
                             ("z_in = abc\n", "z_in"), ("veto_sessao = noite\n", "veto_sessao")):
            with self.assertRaises(SystemExit, msg=extra) as cm:
                self.config(extra, "mau.ini")
            msg = str(cm.exception)
            self.assertIn("config.ini", msg)
            self.assertIn("[sinalizador]", msg)
            self.assertIn(chave, msg)
            self.assertTrue(any(p in msg for p in ("tem de", "e preciso", "nao pode", "so aceita")), msg)

    def test_horizontes_sem_3600_avisa_no_log(self):
        self.captura.mensagens.clear()
        ini = os.path.join(self.pasta, "curto.ini")
        with open(ini, "w", encoding="utf-8") as f:
            f.write("[geral]\nativos = BTC\n[sombra]\nhorizontes_s = 5, 30, 60, 300\n[sinalizador]\n")
        cfg = sn.ConfigSinalizador(ini)
        self.assertNotIn(3600, cfg.horizontes)
        avisos = [m for m in self.captura.mensagens if m.startswith("WARNING") and "horizontes_s" in m]
        self.assertTrue(any("3600" in m for m in avisos), self.captura.mensagens)
        self.assertTrue(any("14400" in m for m in avisos))

    def test_a_chave_da_coinglass_nao_aparece_em_repr_nem_no_log(self):
        cfg = self.config("h_a = 90\n[coinglass]\nchave = %s\n" % CHAVE, "cg.ini")   # h_a < 4 h_max: um aviso no log
        self.assertEqual(cfg.cg_chave, CHAVE)
        self.assertEqual(cfg.liqsrc, "na")           # cg so quando o fluxo confirmar liquidacoes da Hyperliquid (H7)
        self.assertNotIn(CHAVE, repr(cfg))
        self.assertNotIn(CHAVE, str(cfg))
        self.assertTrue(any("h_a" in m for m in self.captura.mensagens))
        self.assertNotIn(CHAVE, self.captura.texto())
        self.assertEqual(sn.sem_chave("erro com %s dentro" % CHAVE, CHAVE), "erro com *** dentro")

    def test_variante_muda_com_um_parametro(self):
        outro = self.config("z_out = 0.25\n", "outro.ini")
        self.assertNotEqual(outro.variante, self.cfg.variante)
        self.assertEqual(self.config("", "igual.ini").variante, self.cfg.variante)


# ==========================================================================
# Velas
# ==========================================================================
class Velas(Base):
    MSG = {"channel": "candle", "data": {"t": 1704880800000, "T": 1704881700000, "s": "BTC", "i": "15m",
                                         "o": "100.5", "c": "101", "h": "102", "l": "100", "v": "3.5", "n": 7}}

    def test_interpretar_vela_le_o_formato_documentado(self):
        v = sn.interpretar_vela(self.MSG)
        self.assertEqual((v.t, v.T, v.o, v.h, v.l, v.c, v.v, v.n, v.completa),
                         (1704880800000, 1704881700000, 100.5, 102.0, 100.0, 101.0, 3.5, 7, True))
        self.assertEqual(sn.interpretar_vela(self.MSG["data"]), v)             # objecto do candleSnapshot
        numeros = dict(self.MSG["data"], o=100.5, c=101, h=102, l=100, v=3.5)   # numeros em vez de texto
        self.assertEqual(sn.interpretar_vela(numeros), v)
        sem_v = {k: x for k, x in self.MSG["data"].items() if k not in ("v", "n")}
        self.assertEqual((sn.interpretar_vela(sem_v).v, sn.interpretar_vela(sem_v).n), (0.0, 0))

    def test_interpretar_vela_recusa_campos_em_falta_ou_impossiveis(self):
        for falta in ("t", "T", "o", "h", "l", "c"):
            d = {k: x for k, x in self.MSG["data"].items() if k != falta}
            with self.assertRaises(ValueError, msg=falta) as cm:
                sn.interpretar_vela({"channel": "candle", "data": d})
            self.assertIn(falta, str(cm.exception))
        for mau in (dict(self.MSG["data"], h="99"), dict(self.MSG["data"], l="0"), dict(self.MSG["data"], c="abc"),
                    dict(self.MSG["data"], T=1), dict(self.MSG["data"], o="200")):
            with self.assertRaises(ValueError):
                sn.interpretar_vela(mau)
        with self.assertRaises(ValueError):
            sn.interpretar_vela({"channel": "candle"})

    def test_vela_fechada_com_atraso_e_folga(self):
        t, T = 1000, 1_000_000
        self.assertFalse(rv.vela_fechada(t, T, T + 1500 + 200 - 1, 200, 1500))
        self.assertTrue(rv.vela_fechada(t, T, T + 1500 + 200, 200, 1500))
        self.assertTrue(rv.vela_fechada(t, T, T + 1500, 0, 1500))
        self.assertFalse(rv.vela_fechada(t, T, T + 1499, 0, 1500))
        with self.assertRaises(ValueError):
            rv.vela_fechada(T, t, T, 0, 0)

    def test_carregar_snapshot_de_json_em_disco_e_guardar_em_csv(self):
        fechadas = [{"t": T0 + i * MS_1H, "T": T0 + (i + 1) * MS_1H, "s": "BTC", "i": "1h", "o": "100", "c": str(100 + i),
                     "h": str(101 + i), "l": "99", "v": "2.5", "n": 3} for i in range(5)]
        aberta = dict(fechadas[-1], t=T0 + 5 * MS_1H, T=T0 + 6 * MS_1H)       # ainda aberta: T para la do fim
        caminho = os.path.join(self.pasta, "snapshot.json")
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(fechadas + [aberta], f)
        pedidos: List[Dict[str, Any]] = []

        def pedir(url: str, corpo: Dict[str, Any]) -> Any:
            pedidos.append(corpo)
            with open(caminho, encoding="utf-8") as f:
                return json.load(f)

        h = sn.Historico("BTC", "1h", self.cfg.pasta_velas)
        fim = fechadas[-1]["T"]
        self.assertEqual(h.carregar_snapshot(self.cfg, T0, fim, pedir=pedir), 5)
        self.assertEqual(len(pedidos), 1)
        self.assertEqual(pedidos[0]["type"], "candleSnapshot")
        self.assertEqual(pedidos[0]["req"]["coin"], "BTC")
        self.assertEqual(pedidos[0]["req"]["interval"], "1h")
        self.assertEqual(len(h), 5)
        self.assertEqual(h.ultima().T, fim)
        self.assertEqual(h.ultimas(2)[0].c, 103.0)
        for v in h.ultimas(len(h)):
            h.guardar(v, {})
        dia = sn.dia_utc(T0)
        ficheiro = os.path.join(self.cfg.pasta_velas, "BTC_1h_%s.csv" % dia)
        linhas = self.ler(ficheiro)
        self.assertEqual(len(linhas), 5)
        self.assertEqual(list(linhas[0].keys()), sn.COLUNAS_VELAS)              # no 1 h so as colunas comuns
        self.assertEqual(linhas[2]["c"], "102.00000000")
        h.guardar(h.ultima(), {})                                                # ja em disco: nao repete
        self.assertEqual(len(self.ler(ficheiro)), 5)
        h2 = sn.Historico("BTC", "1h", self.cfg.pasta_velas)
        self.assertEqual(h2.carregar_disco(), 5)
        self.assertEqual([v.c for v in h2.ultimas(5)], [100.0, 101.0, 102.0, 103.0, 104.0])

    def test_guardar_acrescenta_ao_csv_diario_com_as_colunas_enriquecidas(self):
        h = sn.Historico("BTC", "15m", self.cfg.pasta_velas)
        v1, v2 = _vela(0, 0.0), _vela(1, 0.5)
        h.guardar(v1, {"v_b": 1234.5, "v_a": 0.0, "n_grandes": 2, "raj": 1, "twap": -1, "corte": 0, "flx": NAN})
        h.guardar(v2, None)
        ficheiro = os.path.join(self.cfg.pasta_velas, "BTC_15m_%s.csv" % sn.dia_utc(T0))
        linhas = self.ler(ficheiro)
        self.assertEqual(list(linhas[0].keys()), sn.COLUNAS_VELAS + sn.COLUNAS_ENRIQUECIDAS)
        self.assertEqual(len(linhas), 2)
        self.assertEqual((linhas[0]["t"], linhas[0]["T"]), (str(v1.t), str(v1.T)))
        self.assertEqual((linhas[0]["v_b"], linhas[0]["n_grandes"], linhas[0]["raj"], linhas[0]["twap"], linhas[0]["corte"]),
                         ("1234.500000", "2", "1", "-1", "0"))
        self.assertEqual((linhas[0]["flx"], linhas[0]["pos"], linhas[1]["v_b"]), ("", "", ""))   # vazias quando nao se sabem
        self.assertEqual(linhas[1]["c"], "%.8f" % v2.c)
        h.guardar(_vela(96, 0.0), {})                                            # dia seguinte: ficheiro proprio
        self.assertTrue(os.path.exists(os.path.join(self.cfg.pasta_velas, "BTC_15m_%s.csv" % sn.dia_utc(T0 + 96 * MS_15M))))
        self.assertEqual(len(self.ler(ficheiro)), 2)

    def test_fecho_comum_actualiza_o_contexto_de_1h_antes_do_gatilho(self):
        s = sn.Sinalizador(self.cfg)
        at = s.ativos["BTC"]
        ordem: List[Tuple[Any, ...]] = []
        orig_1h, orig_15m = at.fecho_1h, at.fecho_15m

        def f1h(vela: rv.Vela, completo: bool = True) -> Any:
            ordem.append(("1h", vela.t))
            return orig_1h(vela, completo)

        def f15m(vela: rv.Vela, agora: int, avaliar: bool = True) -> Any:
            ordem.append(("15m", vela.t, at.ctx_ms, at.ancora.n))
            return orig_15m(vela, agora, avaliar)

        at.fecho_1h, at.fecho_15m = f1h, f15m
        v1h = rv.Vela(T0, T0 + MS_1H - 1, 100.0, 101.0, 99.0, 100.5, 10.0, 40, True)
        v15 = _vela(3, 0.2)                                                       # T = T0 + 1 h - 1 ms: fecho comum
        self.assertEqual(v15.T, v1h.T)
        s.pendentes[("BTC", "1h")] = v1h
        s.pendentes[("BTC", "15m")] = v15
        s._fechar(("BTC", "15m"), v15, v15.T + 700)
        self.assertEqual(ordem[0], ("1h", v1h.t))
        self.assertEqual(ordem[1], ("15m", v15.t, v1h.T, 1))     # o gatilho ja ve o contexto da vela de 1 h fechada
        self.assertEqual(s.fechadas, {("BTC", "1h"): v1h.t, ("BTC", "15m"): v15.t})
        self.assertEqual(s.pendentes, {})
        self.assertEqual(len(s.hist[("BTC", "15m")]), 1)
        self.assertTrue(os.path.exists(os.path.join(self.cfg.pasta_velas, "BTC_15m_%s.csv" % sn.dia_utc(T0))))
        self.assertTrue(os.path.exists(os.path.join(self.cfg.pasta_velas, "BTC_1h_%s.csv" % sn.dia_utc(T0))))
        # uma vela com t maior no canal candle fecha a pendente; a mesma vela so a substitui
        v4, v5 = _vela(4, 0.1), _vela(5, 0.3)
        s._tratar({"channel": "candle", "data": {"t": v4.t, "T": v4.T, "s": "BTC", "i": "15m", "o": str(v4.o), "c": str(v4.c),
                                                  "h": str(v4.h), "l": str(v4.l), "v": "1", "n": 3}})
        self.assertEqual(s.pendentes[("BTC", "15m")].t, v4.t)
        self.assertEqual(len(ordem), 2)
        s._tratar({"channel": "candle", "data": {"t": v5.t, "T": v5.T, "s": "BTC", "i": "15m", "o": str(v5.o), "c": str(v5.c),
                                                  "h": str(v5.h), "l": str(v5.l), "v": "1", "n": 3}})
        self.assertEqual(ordem[2][:2], ("15m", v4.t))
        self.assertEqual(s.fechadas[("BTC", "15m")], v4.t)
        self.assertEqual(s.pendentes[("BTC", "15m")].t, v5.t)
        s._tratar({"channel": "candle", "data": {"t": v5.t, "T": v5.T, "s": "ETH", "i": "4h", "o": "1", "c": "1", "h": "1", "l": "1"}})
        self.assertEqual(len(ordem), 3)                                           # intervalo desconhecido: ignorado

    def test_z_prev_e_o_valor_registado_e_nao_o_recalculado_com_a_ancora_nova(self):
        at = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(at)
        self.fechar(at, 0, 0.0)
        self.fechar(at, 1, -2.3)
        z_registado = at.exc.z
        self.assertAlmostEqual(z_registado, -2.3)
        at.a_ult = A0 + 0.5 * SIG                   # um fecho de 1 h moveu a ancora
        self.fechar(at, 2, -1.8)
        self.assertAlmostEqual(at.exc.z_ant, z_registado)
        self.assertNotAlmostEqual(at.exc.z_ant, rv.z_score(A0 - 2.3 * SIG, at.a_ult, SIG))
        self.assertAlmostEqual(at.exc.z, -2.3)       # z_k com a ancora nova: -1.8 - 0.5


# ==========================================================================
# Escrita de sinais (seccao 9)
# ==========================================================================
class EscritaDeSinais(Base):
    def sinal(self, nota: str = "revou v=2 var=abc123 fase=cal", ativo: str = "BTC", lado: int = 1) -> rv.Sinal:
        return rv.Sinal(T0 + 123, ativo, lado, 100.5, 131.26, 1000.4, nota)

    def test_cabecalho_so_quando_o_ficheiro_esta_vazio_e_linha_de_7_campos(self):
        t1 = sn.escrever_sinal(self.cfg, self.sinal())
        self.assertGreater(t1, 0)
        texto = self.texto(self.cfg.sinais_csv)
        self.assertEqual(texto.count("\n"), 2)
        self.assertTrue(texto.endswith("\n"))
        linhas = texto.split("\n")
        self.assertEqual(linhas[0], sn.CAB_SINAIS)
        self.assertEqual(linhas[0], "hora,ativo,lado,preco,alvo_bps,tamanho_usd,nota")
        campos = linhas[1].split(",")
        self.assertEqual(len(campos), 7)
        self.assertEqual(campos[0], md.iso_utc(T0 + 123))
        self.assertRegex(campos[0], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
        self.assertEqual(campos[1:6], ["BTC", "compra", "100.5", "131.3", "1000"])
        self.assertEqual(campos[6], "revou v=2 var=abc123 fase=cal")
        sn.escrever_sinal(self.cfg, self.sinal(lado=-1, ativo="ETH"))
        texto = self.texto(self.cfg.sinais_csv)
        self.assertEqual(texto.count(sn.CAB_SINAIS), 1)
        self.assertEqual(texto.count("\n"), 3)
        self.assertIn(",ETH,venda,", texto.split("\n")[2])
        self.assertTrue(any("Sinal escrito" in m and "latencia" in m for m in self.captura.mensagens))

    def test_nunca_trunca_o_ficheiro(self):
        os.makedirs(self.cfg.pasta, exist_ok=True)
        antes = sn.CAB_SINAIS + "\n2024-01-01T00:00:00.000Z,BTC,compra,100,,,manual\n"
        with open(self.cfg.sinais_csv, "w", encoding="utf-8") as f:
            f.write(antes)
        sn.escrever_sinal(self.cfg, self.sinal())
        depois = self.texto(self.cfg.sinais_csv)
        self.assertTrue(depois.startswith(antes))
        self.assertEqual(depois.count("\n"), 3)
        self.assertEqual(depois.count("hora,ativo"), 1)

    def test_activo_fora_da_lista_e_recusado_com_aviso(self):
        self.assertEqual(sn.escrever_sinal(self.cfg, self.sinal(ativo="SOL")), 0)
        self.assertFalse(os.path.exists(self.cfg.sinais_csv))
        self.assertTrue(any(m.startswith("WARNING") and "SOL" in m and "recusado" in m for m in self.captura.mensagens))
        self.assertEqual(sn.escrever_sinal(self.cfg, self.sinal(ativo="btc")), 0)   # o nome e exacto

    def test_a_nota_nao_tem_virgulas_e_o_medidor_le_a_linha(self):
        med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})            # cria sinais.csv com o cabecalho
        nota = rv.nota_sinal({"var": "abc123", "fase": "cal", "z0": -1.85, "P": 0.899, "liq": NAN, "ctx": "VERDE"})
        self.assertNotIn(",", nota)
        suja = nota + ', "aspas", e\nquebra'
        sn.escrever_sinal(self.cfg, self.sinal(nota=suja))
        texto = self.texto(self.cfg.sinais_csv)
        self.assertEqual(texto.count("\n"), 2)
        ultima = texto.split("\n")[1]
        self.assertEqual(ultima.count(","), 6)
        self.assertNotIn('"', ultima)
        novos = med._ler_sinais_novos()
        self.assertEqual(len(novos), 1)
        self.assertEqual(sorted(novos[0]), sorted(sn.CAB_SINAIS.split(",")))
        self.assertEqual((novos[0]["ativo"], novos[0]["lado"], novos[0]["preco"], novos[0]["alvo_bps"], novos[0]["tamanho_usd"]),
                         ("BTC", "compra", "100.5", "131.3", "1000"))
        self.assertTrue(novos[0]["nota"].startswith(nota))
        self.assertEqual(rv.interpretar_nota(novos[0]["nota"])["z0"], "-1.85")
        with self.assertRaises(ValueError):
            sn.escrever_sinal(self.cfg, rv.Sinal(T0, "BTC", 1, 0.0, 10.0, 1000.0, ""))


# ==========================================================================
# Avaliacao no fecho de 15 m (seccoes 5 a 8)
# ==========================================================================
class Avaliacao(Base):
    def setUp(self) -> None:
        super().setUp()
        self.at = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(self.at)

    def reentrada(self, at: Optional[sn.ActivoSinal] = None, k0: int = 0, z_fora: float = -2.3, z_in: float = -1.8,
                  **kw: Any) -> Tuple[Optional[rv.Sinal], str]:
        """DENTRO, FORA, reentrada do mesmo lado: a sequencia minima que dispara."""
        at = at or self.at
        self.fechar(at, k0, 0.0, **kw)
        self.fechar(at, k0 + 1, z_fora, **kw)
        sinal, motivo, _ = self.fechar(at, k0 + 2, z_in, **kw)
        return sinal, motivo

    def test_reentrada_com_tudo_verde_da_sinal(self):
        at = self.at
        self.assertEqual(at.ctx.estado, rv.VERDE)
        sinal, motivo = self.reentrada()
        self.assertEqual(motivo, "ok")
        self.assertIsNotNone(sinal)
        self.assertEqual((sinal.ativo, sinal.lado, sinal.preco), ("BTC", 1, _vela(2, -1.8).c))
        self.assertEqual(sinal.hora_ms, _vela(2, -1.8).T + 500)
        tau, tmax = rv.tempo_maximo(8.0, 2.0, 16.0)
        g = rv.alvo_bps(-1.8 * SIG, SIG, PHI_8, LAM_96, tau, 0.5)
        self.assertAlmostEqual(sinal.alvo_bps, g, places=6)
        self.assertGreater(g, 3 * 13.0)
        self.assertEqual(sinal.tamanho_usd, 1000.0)                   # fase cal: tamanho_base
        campos = rv.interpretar_nota(sinal.nota)
        self.assertEqual(sinal.nota.split()[:2], ["revou", "v=2"])
        self.assertEqual((campos["var"], campos["fase"], campos["ctx"], campos["med"], campos["ses"], campos["cap"]),
                         (self.cfg.variante, "cal", "VERDE", "off", "europa", "base"))
        self.assertEqual((campos["z0"], campos["zp"], campos["ext"], campos["kf"], campos["tmax"], campos["liq"], campos["liqsrc"]),
                         ("-1.80", "-2.30", "2.30", "1", str(tmax), "na", "na"))
        self.assertAlmostEqual(float(campos["L"]), rv.stop_bps(-1.8, SIG, 3.0), places=1)
        self.assertAlmostEqual(float(campos["P"]), rv.prob_alvo_antes_stop(-1.8, 0.5, 3.0), places=3)
        self.assertIsNotNone(at.trade)
        self.assertEqual(at.carteira, {"BTC": 1000.0})
        self.assertEqual(at.trade_info["fase"], "cal")
        self.assertAlmostEqual(at.trade_info["p_stop"], math.exp(A0 - 3.0 * SIG))
        self.assertAlmostEqual(at.trade_info["p_alvo"], math.exp(A0 - 0.5 * SIG))   # banda z_out do mesmo lado
        self.assertAlmostEqual(at.trade_info["p_alvo"], rv.preco_alvo(A0, SIG, 1, 0.5))
        self.assertEqual(at.ultima["corte"], 0)

    def test_g0_dados(self):
        at = self.at
        self.fechar(at, 0, 0.0)
        self.fechar(at, 1, -2.3)
        at.corte(_vela(2, 0.0).t)
        _, motivo, _ = self.fechar(at, 2, -1.8)
        self.assertEqual(motivo, "G0")
        self.assertIn("corte", at.ultimo_detalhe)
        at2 = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(at2)
        self.fechar(at2, 0, 0.0)
        self.fechar(at2, 1, -2.3)
        v = _vela(2, -1.8)
        at2.ctx_ms = v.T - 76 * 60_000
        self.assertEqual(at2.fecho_15m(v, v.T + 500), (None, "G0"))
        self.assertIn("idade", at2.ultimo_detalhe)
        at3 = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(at3)
        at3.atrasos.extend([3500] * 5)
        self.assertEqual(self.reentrada(at3)[1], "G0")
        self.assertIn("atraso", at3.ultimo_detalhe)
        at4 = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(at4)
        self.assertEqual(self.reentrada(at4, n=0)[1], "G0")
        self.assertIn("negocios", at4.ultimo_detalhe)

    def test_g1_contexto_nao_verde(self):
        self.preparar(self.at, t_nulo=-2.5)                                    # AMARELO: regista-se, nao se sinaliza
        self.assertEqual(self.at.ctx.estado, rv.AMARELO)
        self.assertEqual(self.reentrada()[1], "G1")
        self.assertIn("AMARELO nulo", self.at.ultimo_detalhe)
        at = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(at, vr=1.3)
        self.assertEqual(at.ctx.estado, rv.VERMELHO)
        self.assertEqual(self.reentrada(at)[1], "G1")
        self.assertIn("vr", at.ultimo_detalhe)

    def test_g2_a_g5_pela_ordem(self):
        at = self.at
        self.assertEqual(self.fechar(at, 0, -1.8)[1], "G2")                     # sem FORA anterior
        self.assertEqual(self.fechar(at, 1, -2.3)[1], "G2")                     # ainda FORA
        self.assertEqual(self.fechar(at, 2, 1.8)[1], "G2")                      # termina do lado oposto
        self.assertEqual(at.exc.estado, rv.DENTRO)
        self.fechar(at, 3, -2.3)
        self.assertEqual(self.fechar(at, 4, -1.0)[1], "G3")                     # caminho curto: 1,0 < 0,5 + 0,75
        self.fechar(at, 5, -4.5)                                                # quebra de regime
        self.assertEqual(self.fechar(at, 6, -1.8)[1], "G4")
        self.fechar(at, 7, -2.3)
        self.assertEqual(self.fechar(at, 8, -1.8)[1], "ok")
        at2 = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(at2)
        self.assertEqual(self.reentrada(at2, amp=0.001)[1], "G5")              # velas sem amplitude: o fecho e choque
        self.assertIn("z=", at2.ultimo_detalhe)
        at3 = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(at3)
        at3.k_max = 1
        self.fechar(at3, 0, 0.0)
        self.fechar(at3, 1, -2.3)
        self.fechar(at3, 2, -2.4)
        self.assertEqual(self.fechar(at3, 3, -1.8)[1], "G4")                    # k_fora 2 > k_max 1

    def test_g6_rajada_de_liquidacao_contra(self):
        at = self.at
        self.fechar(at, 0, 0.0)
        self.fechar(at, 1, -2.3)
        v = _vela(2, -1.8)
        t = v.t + 1000
        for i in range(100):                                                   # 100 amostras: Q99 = 1000 usd
            at.negocio({"px": "100", "sz": "10", "side": "B", "time": t + i * 100, "users": ["0x%040x" % (i + 1), "0x%040x" % 7000],
                        "hash": "0x1"}, t + i * 100)
        t_bloco = v.T - 30_000                                                   # bloco de 3 negocios do mesmo vendedor
        for i in range(3):
            px = 100.0 * (1.0 - 0.0002 * i)                                      # cai 4 bps dentro do bloco
            at.negocio({"px": repr(px), "sz": "5", "side": "A", "time": t_bloco + i * 400, "users": ["0x%040x" % 9000, "0x%040x" % 9001],
                        "hash": "0x0"}, t_bloco + i * 400)
        self.assertTrue(at.grandes.rajada_contra(v.T, 1, 60.0, 1000, 2.0))
        self.assertEqual(at.fecho_15m(v, v.T + 500), (None, "G6"))
        self.assertIn("rajada", at.ultimo_detalhe)
        self.assertEqual(at.ultima["raj"], -1)       # a coluna e o lado da rajada (vendas), nao "contra o candidato"
        self.assertEqual(at.ultima["n_grandes"], 0)  # cada negocio do bloco (500) fica abaixo de Q99 (1000): e a soma que qualifica
        self.assertIsNone(at.trade)

    def test_g7_uma_entrada_por_excursao_e_trade_aberto(self):
        at = self.at
        self.assertEqual(self.reentrada()[1], "ok")
        self.assertEqual(at.n_exc, 1)
        self.assertEqual(at.exc_entrada, 1)
        self.assertEqual(self.fechar(at, 3, -1.5)[1], "G2")                    # ja DENTRO: nao dispara duas vezes
        self.assertEqual(self.fechar(at, 4, -2.3)[1], "G2")                    # nova excursao com o trade aberto
        _, motivo, linha = self.fechar(at, 5, -1.8)
        self.assertEqual((motivo, linha), ("G7", None))
        self.assertIn("trade virtual aberto", at.ultimo_detalhe)
        self.assertEqual(at.n_exc, 2)
        _, motivo, linha = self.fechar(at, 6, 0.6)                             # fecho passa o alvo: trade fechado
        self.assertEqual(linha["motivo"], "alvo")
        self.assertIsNone(at.trade)
        self.fechar(at, 7, -2.3)
        self.assertEqual(self.fechar(at, 8, -1.8)[1], "ok")
        self.assertEqual(at.exc_entrada, 3)

    def test_arrefecimento_de_duas_velas_apos_stop(self):
        at = self.at
        self.assertEqual(self.reentrada()[1], "ok")
        _, motivo, linha = self.fechar(at, 3, -2.5, z_lo=-3.2)                 # a minima toca o stop; fecha FORA
        self.assertEqual(linha["motivo"], "stop")
        self.assertEqual((motivo, at.arrefecimento, at.stops_seguidos), ("G2", 2, 1))
        self.assertEqual(self.fechar(at, 4, -1.9)[1], "G7")                    # 1.a vela depois do stop
        self.assertIn("arrefecimento (2 velas)", at.ultimo_detalhe)
        self.assertEqual(at.arrefecimento, 1)
        self.assertEqual(self.fechar(at, 5, -2.4)[1], "G2")                    # 2.a vela depois do stop: ainda conta
        self.assertEqual(at.arrefecimento, 0)
        self.assertEqual(self.fechar(at, 6, -1.9)[1], "ok")                    # 3.a vela: livre
        _, motivo, linha = self.fechar(at, 7, -2.5, z_lo=-3.2)                 # segundo stop
        self.assertEqual((linha["motivo"], at.stops_seguidos, at.arrefecimento), ("stop", 2, 2))
        self.assertEqual(self.fechar(at, 8, -2.6)[1], "G2")                    # 1.a vela depois do stop (FORA)
        self.assertEqual(self.fechar(at, 9, -1.9)[1], "G7")                    # 2.a vela depois do stop: reentrada travada
        self.assertIn("arrefecimento (1 velas)", at.ultimo_detalhe)
        self.assertIsNone(at.trade)
        self.fechar(at, 10, -2.4)
        self.assertEqual(self.fechar(at, 11, -1.9)[1], "ok")

    def test_recusa_quando_a_soma_dos_abertos_passa_expo_max(self):
        at = self.at
        at.carteira = {"ETH": 14_500.0}                                        # 0,75 x 20 000 = 15 000
        self.assertEqual(self.reentrada()[1], "G7")
        self.assertIn("exposicao", at.ultimo_detalhe)
        self.assertIsNone(at.trade)
        at.carteira = {"ETH": 14_000.0}
        self.fechar(at, 3, -2.3)
        self.assertEqual(self.fechar(at, 4, -1.8)[1], "ok")
        self.assertEqual(at.carteira, {"ETH": 14_000.0, "BTC": 1000.0})

    def test_g8_geometria(self):
        at = self.at
        at.z_in_ef = 3.5                                                        # caudas pesadas: a banda alargou para la do stop
        self.fechar(at, 0, 0.0)
        self.fechar(at, 1, -3.8)
        self.assertEqual(self.fechar(at, 2, -3.05)[1], "G8")                   # reentrada valida, mas |z| >= z_stop
        self.assertIn("z_stop", at.ultimo_detalhe)
        self.assertIsNone(at.trade)
        at2 = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(at2)
        at2.custos = (8.0, 60.0, 30)                                            # c_L medido: 3 x 60 = 180 bps > G
        self.assertEqual(self.reentrada(at2)[1], "G8")
        self.assertRegex(at2.ultimo_detalhe, r"G = [\d.]+ < 180\.0 bps")
        self.assertIsNone(at2.trade)

    def test_fase_sombra_nao_escreve_mas_regista_o_trade_virtual(self):
        s = sn.Sinalizador(self.cfg)
        at = s.ativos["BTC"]
        self.preparar(at)
        at.fechados = [_fechado(T0 - (i + 1) * MS_1H, -10.0, "stop") for i in range(30)]
        self.assertEqual(at.fase(), "op")
        for k, z in ((0, 0.0), (1, -2.3), (2, -1.8)):
            v = _vela(k, z)
            at.ctx_ms = v.t
            s._fechar(("BTC", "15m"), v, v.T + 500)
        self.assertEqual(at.ultimo_motivo, "G8")
        self.assertEqual(at.ultimo_detalhe, "fase sombra")
        self.assertIsNotNone(at.trade)
        self.assertEqual((at.trade_info["fase"], at.trade_info["tamanho_usd"]), ("sombra", 0.0))
        self.assertEqual(at.ultima_nota["fase"], "sombra")
        self.assertFalse(os.path.exists(self.cfg.sinais_csv))
        self.assertEqual(s.carteira, {"BTC": 0.0})
        v = _vela(3, 0.6)
        at.ctx_ms = v.t
        s._fechar(("BTC", "15m"), v, v.T + 500)                                  # o trade virtual continua e fecha
        self.assertIsNone(at.trade)
        linhas = self.ler(self.cfg.reg_sinalizador)
        self.assertEqual((len(linhas), linhas[0]["fase"], linhas[0]["motivo"]), (1, "sombra", "alvo"))
        self.assertEqual(len(at.fechados), 31)

    def test_fase_op_escreve_com_o_tamanho_dimensionado(self):
        s = sn.Sinalizador(self.cfg)
        at = s.ativos["BTC"]
        self.preparar(at)
        at.fechados = [_fechado(T0 - (i + 1) * MS_1H, 50.0) for i in range(40)]   # p_hat = 1: fase op
        for k, z in ((0, 0.0), (1, -2.3), (2, -1.8)):
            v = _vela(k, z)
            at.ctx_ms = v.t
            s._fechar(("BTC", "15m"), v, v.T + 500)
        self.assertEqual(at.ultimo_motivo, "ok")
        linhas = self.texto(self.cfg.sinais_csv).strip().split("\n")
        self.assertEqual(len(linhas), 2)
        campos = linhas[1].split(",")
        l_bps = rv.stop_bps(-1.8, SIG, 3.0)
        n_risco = 0.005 * 20000.0 * nu.BPS / (l_bps + 13.0)
        self.assertEqual(campos[5], "%.0f" % (0.5 * n_risco))                   # f_B = 0,5 (B = 0) x N_risco manda
        nota = rv.interpretar_nota(campos[6])
        self.assertEqual((nota["fase"], nota["cap"], nota["fb"], nota["bal"]), ("op", "risco", "0.50", "0"))
        self.assertAlmostEqual(s.carteira["BTC"], 0.5 * n_risco)


# ==========================================================================
# Trade virtual e registo proprio (seccao 7.5)
# ==========================================================================
class TradeVirtual(Base):
    def setUp(self) -> None:
        super().setUp()
        self.at = sn.ActivoSinal("BTC", self.cfg, 5)
        self.preparar(self.at)
        self.at.registo = md.Registo(self.cfg.reg_sinalizador, sn.COLUNAS_REGISTO)
        self.at.contexto_activo({"funding": "0.0001", "openInterest": "1000", "markPx": "100", "oraclePx": "100",
                                 "premium": "0", "dayNtlVlm": "1e8"}, T0)

    def abrir(self, k: int, lado: int = 1, tmax_15: int = 8, z0: float = -1.8) -> rv.Vela:
        v = _vela(k, z0)
        p_alvo = rv.preco_alvo(A0, SIG, lado, 0.5)
        p_stop = math.exp(A0 - lado * 3.0 * SIG)
        self.at._abrir_trade(v, v.T + 500, lado, p_alvo, p_stop, tmax_15, 124.0, 120.0, "cal", 0.5, 1000.0,
                             "revou v=2 var=abc123 fase=cal z0=%.2f P=0.899" % z0, z0)
        return v

    def test_fecha_por_alvo_stop_tempo_e_invalidacao_e_escreve_o_registo(self):
        at = self.at
        p_e = self.abrir(0).c
        self.assertIsNone(at.avancar_trade_virtual(_vela(1, -1.5)))              # a maxima nao chega ao alvo
        self.assertIsNone(at.avancar_trade_virtual(_vela(2, -1.0, z_hi=0.4)))    # a maxima passa, o fecho nao
        self.assertIsNotNone(at.trade)
        linha = at.avancar_trade_virtual(_vela(3, 0.6))                          # fim T0 + 1 h: hora inteira, cobra funding
        self.assertEqual(linha["motivo"], "alvo")
        p_alvo = rv.preco_alvo(A0, SIG, 1, 0.5)
        r_bruto = nu.BPS * math.log(p_alvo / p_e)
        self.assertEqual(linha["preco_saida"], "%.8f" % p_alvo)
        self.assertEqual(linha["r_bruto_bps"], "%.2f" % r_bruto)
        self.assertEqual(linha["funding_bps"], "1.000")                          # 0,0001 x 1e4, pago pelo long
        self.assertEqual(linha["r_liq_bps"], "%.2f" % (r_bruto - 8.0 - 1.0))     # c_W de defeito
        self.assertEqual((linha["id"], linha["ativo"], linha["lado"], linha["velas"], linha["fase"]),
                         (at.fechados[0] and linha["id"], "BTC", "compra", 3, "cal"))
        self.assertTrue(linha["id"].startswith("BTC-"))
        self.assertEqual(linha["hora_saida"], md.iso_utc(_vela(3, 0.6).T))
        self.assertEqual(float(linha["mfe_bps"]), round(nu.BPS * math.log(_vela(3, 0.6).h / p_e), 2))
        self.assertIsNone(at.trade)
        self.assertEqual(at.carteira, {})
        # stop: a minima toca P_stop; preenchido no nivel (o deslize ja esta no imp de c_L)
        p_e = self.abrir(4).c
        linha = at.avancar_trade_virtual(_vela(5, -2.0, z_lo=-3.1))
        p_stop = math.exp(A0 - 3.0 * SIG)
        self.assertEqual((linha["motivo"], linha["preco_saida"]), ("stop", "%.8f" % p_stop))
        r_bruto = nu.BPS * math.log(p_stop / p_e)
        self.assertEqual(linha["r_liq_bps"], "%.2f" % (r_bruto - 13.0))         # c_L de defeito: taker + imp, duas vezes
        self.assertEqual((at.stops_seguidos, at.arrefecimento, at.t_stop_ms), (1, 2, _vela(5, -2.0).t))
        # tempo: ao fecho da vela tmax_15 apos a entrada
        p_e = self.abrir(6, tmax_15=2).c
        self.assertIsNone(at.avancar_trade_virtual(_vela(7, -1.6)))              # T = T0 + 2 h: cobra funding
        linha = at.avancar_trade_virtual(_vela(8, -1.5))
        self.assertEqual((linha["motivo"], linha["preco_saida"], linha["velas"]), ("tempo", "%.8f" % _vela(8, -1.5).c, 2))
        self.assertEqual(linha["funding_bps"], "1.000")
        self.assertEqual(linha["r_liq_bps"], "%.2f" % (nu.BPS * math.log(_vela(8, -1.5).c / p_e) - 13.0 - 1.0))
        self.assertEqual(at.stops_seguidos, 0)
        # invalidacao: marcada no fecho de 1 h, executada ao fecho de 15 m seguinte
        p_e = self.abrir(9).c
        at.inval_motivo = "nulo"
        linha = at.avancar_trade_virtual(_vela(10, -2.0))
        self.assertEqual((linha["motivo"], linha["preco_saida"]), ("invalidacao", "%.8f" % _vela(10, -2.0).c))
        self.assertEqual(linha["funding_bps"], "0.000")                          # T0 + 2 h 45: nenhuma hora inteira
        self.assertEqual(linha["r_liq_bps"], "%.2f" % (nu.BPS * math.log(_vela(10, -2.0).c / p_e) - 13.0))
        self.assertIsNone(at.inval_motivo)
        linhas = self.ler(self.cfg.reg_sinalizador)
        self.assertEqual(list(linhas[0].keys()), sn.COLUNAS_REGISTO)
        self.assertEqual([ln["motivo"] for ln in linhas], ["alvo", "stop", "tempo", "invalidacao"])
        self.assertEqual([ln["r_liq_bps"] != "" for ln in linhas], [True] * 4)
        self.assertEqual(linhas[0]["nota"], "revou v=2 var=abc123 fase=cal z0=-1.80 P=0.899")
        self.assertEqual(linhas[0]["A_0"], "%.8f" % A0)
        self.assertEqual(linhas[0]["H_0"], "8.000")
        self.assertTrue(all(m.startswith("INFO") for m in self.captura.mensagens if "fechado por" in m))
        # p_hat e p_inf recalculados a partir do registo
        self.assertEqual(at.taxa_acerto(), (0.5, 4))
        novo = sn.ActivoSinal("BTC", self.cfg, 5)
        novo.carregar_fechados(sn._ler_csv(self.cfg.reg_sinalizador))
        self.assertEqual(novo.taxa_acerto(), (0.5, 4))
        self.assertEqual(novo.stops_seguidos, 0)
        est = novo.estado()
        self.assertEqual((est["p_hat"], est["n_fechados"], est["fase"]), (0.5, 4, "cal"))
        self.assertAlmostEqual(est["p_inf"], rv.wilson_inferior(0.5, 4))
        self.assertEqual(sn.ActivoSinal("ETH", self.cfg, 4).taxa_acerto()[1], 0)

    def test_migracao_de_colunas_sem_perder_linhas(self):
        antigas = [c for c in sn.COLUNAS_REGISTO if c != "f_B"] + ["coluna_velha"]
        reg = md.Registo(self.cfg.reg_sinalizador, antigas)
        for i, r in enumerate((30.0, -40.0)):
            reg.escrever({"id": "BTC-%d" % i, "hora": md.iso_utc(T0 + i * MS_1H), "hora_saida": md.iso_utc(T0 + (i + 1) * MS_1H),
                          "ativo": "BTC", "lado": "compra", "motivo": "alvo" if r > 0 else "stop", "G": "124.00", "L": "120.00",
                          "fase": "cal", "r_liq_bps": "%.2f" % r, "coluna_velha": "x%d" % i})
        s = sn.Sinalizador(self.cfg)
        linhas = self.ler(self.cfg.reg_sinalizador)
        self.assertEqual([ln["id"] for ln in linhas], ["BTC-0", "BTC-1"])
        self.assertEqual(linhas[1]["coluna_velha"], "x1")                        # o dado antigo continua la
        self.assertIn("f_B", linhas[0])
        self.assertTrue(any(".anterior-" in n for n in os.listdir(self.cfg.pasta)))
        at = s.ativos["BTC"]
        self.assertEqual(at.taxa_acerto(), (0.5, 2))
        self.assertEqual(at.stops_seguidos, 1)
        self.assertEqual(s.ativos["ETH"].taxa_acerto()[1], 0)
        at.registo.escrever({"id": "BTC-2", "ativo": "BTC", "r_liq_bps": "5.00", "f_B": "0.50"})
        self.assertEqual(len(self.ler(self.cfg.reg_sinalizador)), 3)

    def test_restaurar_trade_do_estado(self):
        at = self.at
        self.abrir(0)
        at.trade.velas = 2
        info = at.estado()["trade"]
        self.assertEqual(info["velas"], 2)
        novo = sn.ActivoSinal("BTC", self.cfg, 5)
        novo.restaurar_trade(info)
        self.assertEqual((novo.trade.velas, novo.trade.lado, novo.trade.p_entrada), (2, 1, at.trade.p_entrada))
        self.assertEqual(novo.carteira, {"BTC": 1000.0})
        self.assertNotIn("velas", novo.trade_info)
        novo.ids_fechados.add(info["id"])                                        # ja fechado no registo: recusa
        with self.assertRaises(ValueError):
            novo.restaurar_trade(info)


# ==========================================================================
# Disjuntores (seccao 7.6)
# ==========================================================================
class Disjuntores(Base):
    def setUp(self) -> None:
        super().setUp()
        self.s = sn.Sinalizador(self.cfg)
        self.at = self.s.ativos["BTC"]
        self.preparar(self.at)

    def abrir(self, at: sn.ActivoSinal, k: int = 0) -> None:
        v = _vela(k, -1.8)
        at._abrir_trade(v, v.T + 500, 1, math.exp(A0 + 0.5 * SIG), math.exp(A0 - 3.0 * SIG), 8, 124.0, 120.0, "cal", 0.5,
                        1000.0, "revou v=2", -1.8)

    def test_cinco_stops_seguidos_param_o_activo_sem_fechar_o_trade(self):
        s, at = self.s, self.at
        self.abrir(at)
        at.stops_seguidos = 5
        s._aplicar_disjuntores(T0 + MS_15M)
        self.assertEqual(at.parado, "seguidas")
        self.assertIsNone(s.ativos["ETH"].parado)                               # seguidas e por activo
        self.assertIsNotNone(at.trade)
        self.assertEqual(self.fechar(at, 1, -1.5)[1:], ("disjuntor", None))
        self.assertEqual(at.ultimo_detalhe, "seguidas")
        self.assertIsNotNone(at.trade)                                           # o trade virtual continua
        self.assertEqual(self.fechar(at, 2, 0.6)[2]["motivo"], "alvo")           # e fecha pelas suas regras
        self.assertTrue(any("stops seguidos" in m for m in self.captura.mensagens))
        s.reset()
        self.assertEqual((at.parado, at.stops_seguidos), (None, 0))
        self.assertEqual(self.fechar(at, 3, -2.3)[1], "G2")                       # volta a avaliar

    def test_perdas_do_dia_param_a_emissao_ate_a_meia_noite(self):
        s, at = self.s, self.at
        t = T0 + 3 * MS_1H
        at.fechados = [_fechado(T0 + i * MS_1H, -150.0, "stop", g=100.0) for i in range(3)]   # -450 <= -3 x 100
        s._aplicar_disjuntores(t)
        self.assertEqual(s.disjuntores["perdas_dia"], sn.dia_utc(t))
        self.assertEqual([a.parado for a in s.ativos.values()], ["perdas_dia", "perdas_dia"])
        self.assertEqual(self.fechar(at, 0, 0.0)[1], "disjuntor")
        s._escrever_estado(t)
        with open(self.cfg.estado_sinalizador_json, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["disjuntores"], {"perdas_dia": sn.dia_utc(t), "cvar": False})
        self.assertEqual(doc["ativos"]["BTC"]["parado"], "perdas_dia")
        outro = sn.Sinalizador(self.cfg)                                           # rearranque noutro dia (2024 ja passou)
        self.assertNotEqual(sn.dia_utc(md.agora_ms()), sn.dia_utc(t))
        self.assertEqual([a.parado for a in outro.ativos.values()], [None, None])
        self.assertEqual(outro.disjuntores["perdas_dia"], sn.dia_utc(t))
        doc["disjuntores"]["perdas_dia"] = sn.dia_utc(md.agora_ms())                # o mesmo dia: continua parado
        sn.escrever_json_atomico(self.cfg.estado_sinalizador_json, doc)
        self.assertEqual([a.parado for a in sn.Sinalizador(self.cfg).ativos.values()], ["perdas_dia", "perdas_dia"])
        s.disjuntores["perdas_dia"] = ""
        s._levantar("perdas_dia")
        self.assertEqual([a.parado for a in s.ativos.values()], [None, None])

    def test_cvar_para_tudo_ate_reset_e_o_estado_persiste(self):
        s, at = self.s, self.at
        ontem = T0 - 2 * sn.MS_DIA
        at.fechados = ([_fechado(ontem + i * MS_15M, 10.0) for i in range(95)]
                       + [_fechado(ontem + (95 + i) * MS_15M, -300.0, "stop") for i in range(5)])   # media dos 5 piores -300 <= -2 x 110
        self.abrir(s.ativos["ETH"])
        s._aplicar_disjuntores(T0)
        self.assertTrue(s.disjuntores["cvar"])
        self.assertEqual([a.parado for a in s.ativos.values()], ["cvar", "cvar"])
        self.assertIsNotNone(s.ativos["ETH"].trade)
        self.assertTrue(any("CVaR" in m for m in self.captura.mensagens))
        trocas: List[Tuple[str, str]] = []
        real = os.replace

        def replace(src: str, dst: str) -> None:
            trocas.append((src, dst))
            real(src, dst)

        with mock.patch("os.replace", replace):
            s._escrever_estado(T0)
        self.assertEqual(trocas, [(self.cfg.estado_sinalizador_json + ".tmp", self.cfg.estado_sinalizador_json)])
        self.assertFalse(os.path.exists(self.cfg.estado_sinalizador_json + ".tmp"))
        with open(self.cfg.estado_sinalizador_json, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertTrue(doc["disjuntores"]["cvar"])
        self.assertEqual(doc["ativos"]["ETH"]["trade"]["lado"], 1)
        self.assertEqual(doc["ativos"]["BTC"]["parado"], "cvar")
        self.assertEqual(doc["ligado"], False)
        self.assertIsNone(doc["ativos"]["BTC"]["atraso_ms"])                        # nan vira null, nunca NaN
        outro = sn.Sinalizador(self.cfg)                                           # rearranque: continua parado
        self.assertEqual([a.parado for a in outro.ativos.values()], ["cvar", "cvar"])
        self.assertIsNotNone(outro.ativos["ETH"].trade)                            # o trade virtual foi reposto
        self.assertEqual(outro.ativos["ETH"].trade.p_entrada, s.ativos["ETH"].trade.p_entrada)
        with contextlib.redirect_stdout(io.StringIO()):
            sn.cmd_reset(self.cfg)
        with open(self.cfg.estado_sinalizador_json, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["disjuntores"], {"perdas_dia": "", "cvar": False})
        self.assertIsNone(doc["ativos"]["BTC"]["parado"])
        depois = sn.Sinalizador(self.cfg)
        self.assertEqual([a.parado for a in depois.ativos.values()], [None, None])
        self.assertEqual(rv.disjuntores([], 0, [f["r_liq_bps"] for f in at.fechados], 124.0, 110.0, self.cfg.p_disj), "cvar")

    def test_disjuntor_de_regime_e_sessao_vetada(self):
        s, at = self.s, self.at
        at.parado = "regime"
        self.assertEqual(self.fechar(at, 0, 0.0)[1], "disjuntor")
        s._parar_todos("cvar")
        self.assertEqual(at.parado, "regime")                                       # o regime so sai com reset
        s.reset()
        self.assertIsNone(at.parado)
        cfg = self.config("veto_sessao = europa, fds\n", "sessao.ini")
        at2 = sn.ActivoSinal("BTC", cfg, 5)
        self.preparar(at2)
        self.assertEqual(self.fechar(at2, 0, 0.0)[1], "sessao")                     # T0 e as 10:00 UTC de quarta
        self.assertEqual(at2.ultimo_detalhe, "europa")


# ==========================================================================
# Convivencia com o medidor (seccao 10)
# ==========================================================================
class Convivencia(Base):
    def estado_medidor(self, hora_ms: int, ligado: bool = True) -> None:
        os.makedirs(self.cfg.pasta, exist_ok=True)
        doc = {"hora_utc": md.iso_utc(hora_ms), "ligado": ligado, "versao": "2.5",
               "ativos": {"BTC": {"estado": "VERDE", "imp_ref_bps": 1.25, "qmax_usd_compra": 50000, "qmax_usd_venda": 48000}}}
        with open(self.cfg.estado_json, "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_ler_estado_medidor_devolve_none_e_avisa(self):
        t = T0
        self.assertIsNone(sn.ler_estado_medidor(self.cfg, t))                        # ausente
        self.estado_medidor(t - 10_001)
        self.assertIsNone(sn.ler_estado_medidor(self.cfg, t + 61_000))              # mais de 10 s
        self.estado_medidor(t + 122_000, ligado=False)
        self.assertIsNone(sn.ler_estado_medidor(self.cfg, t + 122_000))             # fresco mas sem ligacao
        avisos = [m for m in self.captura.mensagens if "med=off" in m]
        self.assertEqual(len(avisos), 3)
        self.assertTrue(all(m.startswith("WARNING") for m in avisos))
        self.assertIn("nao existe", avisos[0])
        self.assertIn("tem 71 s", avisos[1])
        self.assertIn("sem ligacao", avisos[2])
        self.estado_medidor(t + 130_000, ligado=False)
        self.assertIsNone(sn.ler_estado_medidor(self.cfg, t + 130_000))             # menos de um minuto depois: sem aviso novo
        self.assertEqual(len([m for m in self.captura.mensagens if "med=off" in m]), 3)
        self.estado_medidor(t)
        doc = sn.ler_estado_medidor(self.cfg, t + 4000)
        self.assertEqual(doc["idade_ms"], 4000)
        self.assertEqual(doc["ativos"]["BTC"]["estado"], "VERDE")
        with open(self.cfg.estado_json, "w", encoding="utf-8") as f:
            f.write("{meio ficheiro")
        self.assertIsNone(sn.ler_estado_medidor(self.cfg, t + 200_000))

    def test_o_sinal_escreve_se_na_mesma_com_o_medidor_parado(self):
        s = sn.Sinalizador(self.cfg)
        at = s.ativos["BTC"]
        self.preparar(at)
        s.estado_medidor = s._passo("estado do medidor", sn.ler_estado_medidor, self.cfg, T0)
        self.assertIsNone(s.estado_medidor)
        for k, z in ((0, 0.0), (1, -2.3), (2, -1.8)):
            v = _vela(k, z)
            at.ctx_ms = v.t
            s._fechar(("BTC", "15m"), v, v.T + 500)
        self.assertEqual(at.ultimo_motivo, "ok")
        linhas = self.texto(self.cfg.sinais_csv).strip().split("\n")
        self.assertEqual(len(linhas), 2)
        self.assertEqual(rv.interpretar_nota(linhas[1].split(",")[6])["med"], "off")
        # com o medidor fresco a nota diz med=on e o impacto medido entra no trade virtual
        self.estado_medidor(T0 + 4 * MS_15M)
        at2 = s.ativos["ETH"]
        self.preparar(at2)
        at2.medidor = sn.ler_estado_medidor(self.cfg, T0 + 4 * MS_15M + 100)
        at2.medidor["ativos"]["ETH"] = at2.medidor["ativos"]["BTC"]
        for k, z in ((3, 0.0), (4, -2.3), (5, -1.8)):
            v = _vela(k, z)
            at2.ctx_ms = v.t
            sinal, motivo = at2.fecho_15m(v, v.T + 500)
        self.assertEqual(motivo, "ok")
        self.assertEqual(rv.interpretar_nota(sinal.nota)["med"], "on")
        self.assertEqual(at2.imp_bps, 1.25)
        self.assertEqual(at2.trade.imp_bps, 0.0)     # o impacto esta em c_L; o stop preenche em P_stop sem deslize

    def test_custos_medidos_devolve_defeitos_com_menos_de_30_aberturas(self):
        self.assertEqual(sn.custos_medidos(self.cfg, "BTC"), (8.0, 13.0, 0))
        colunas = ["hora_utc", "ativo", "lado", "px", "sz", "valor_usd", "taxa", "moeda_taxa", "taker", "dir", "oid", "tid",
                   "mid_antes", "e_bps", "f_bps", "a_5", "a_30", "a_60", "estado", "sinal_id", "ordem_no_sinal", "d_bps",
                   "atraso_s", "c_preco_bps"]
        reg = md.Registo(self.cfg.reg_fills, colunas)

        def fill(i: int, e: float = 1.0, taker: int = 1, direc: str = "Open Long") -> None:
            reg.escrever({"ativo": "BTC", "oid": str(i), "tid": str(i), "valor_usd": "1000", "e_bps": "%.2f" % e, "f_bps": "4.5",
                          "a_5": "0.1", "a_30": "0.3", "a_60": "0.5", "taker": str(taker), "dir": direc, "d_bps": "1.0"})

        for i in range(29):
            fill(i)
        self.assertEqual(sn.custos_medidos(self.cfg, "BTC"), (8.0, 13.0, 29))
        fill(29, direc="Close Long")                                             # um fecho nao conta como abertura
        self.assertEqual(sn.custos_medidos(self.cfg, "BTC"), (8.0, 13.0, 29))
        fill(30)
        c_w, c_l, n = sn.custos_medidos(self.cfg, "BTC")                         # taker: E 1 + F 4,5 + A_60 0,5 + D 1 = 7
        self.assertEqual(n, 30)
        self.assertAlmostEqual(c_w, 7.0 + 1.5)                                  # saida no alvo a maker: taxa maker
        self.assertAlmostEqual(c_l, 14.0)
        self.assertEqual(sn.custos_medidos(self.cfg, "ETH"), (8.0, 13.0, 0))

    def test_nunca_abre_em_escrita_os_ficheiros_do_medidor(self):
        cfg = self.cfg
        self.estado_medidor(T0)
        md.Registo(cfg.reg_fills, ["ativo", "oid", "tid", "valor_usd", "e_bps", "f_bps", "a_60", "taker", "dir", "d_bps"]).escrever(
            {"ativo": "BTC", "oid": "1", "tid": "1", "valor_usd": "100", "e_bps": "1", "f_bps": "4.5", "a_60": "0", "taker": "1",
             "dir": "Open Long", "d_bps": "1"})
        md.Registo(cfg.reg_sinais, ["id", "hora_utc", "ativo", "lado", "nota", "r_3600"]).escrever(
            {"id": "s1", "hora_utc": md.iso_utc(T0), "ativo": "BTC", "lado": "compra", "nota": "", "r_3600": "5"})
        os.makedirs(cfg.pasta_metricas, exist_ok=True)
        proibidos = {os.path.abspath(cfg.reg_sinais), os.path.abspath(cfg.reg_fills), os.path.abspath(cfg.estado_json)}
        metricas = os.path.abspath(cfg.pasta_metricas)
        escritas: List[str] = []
        lidos: List[str] = []
        open_real = builtins.open

        def vigiado(caminho: Any, modo: str = "r", *a: Any, **kw: Any) -> Any:
            if isinstance(caminho, (str, bytes, os.PathLike)):
                p = os.path.abspath(os.fsdecode(caminho))
                if p in proibidos or p.startswith(metricas + os.sep):
                    (escritas if any(ch in modo for ch in "wax+") else lidos).append(p)
            return open_real(caminho, modo, *a, **kw)

        def destino(nome: str) -> Any:
            real = getattr(os, nome)

            def f(*args: Any) -> Any:
                for p in args:
                    if isinstance(p, str) and (os.path.abspath(p) in proibidos or os.path.abspath(p).startswith(metricas + os.sep)):
                        escritas.append(p)
                return real(*args)
            return f

        with mock.patch("builtins.open", vigiado), mock.patch("os.replace", destino("replace")), \
                mock.patch("os.remove", destino("remove")), mock.patch("os.rename", destino("rename")):
            s = sn.Sinalizador(cfg)
            at = s.ativos["BTC"]
            self.preparar(at)
            s.estado_medidor = sn.ler_estado_medidor(cfg, T0 + 100)
            for k, z in ((0, 0.0), (1, -2.3), (2, -1.8), (3, 0.6)):
                v = _vela(k, z)
                at.ctx_ms = v.t
                s._fechar(("BTC", "15m"), v, v.T + 500)
            self.assertEqual(len(at.fechados), 1)
            s._aplicar_disjuntores(T0 + 4 * MS_15M)
            s._escrever_estado(T0 + 4 * MS_15M)
            s.reset()
            self.assertEqual(sn.custos_medidos(cfg, "BTC")[2], 1)
            with contextlib.redirect_stdout(io.StringIO()):
                sn.cmd_relatorio(cfg)
                sn.cmd_reset(cfg)
                sn.cmd_ensaios(cfg)
            s.parar()
        self.assertEqual(escritas, [])
        self.assertTrue(any(p.endswith("estado.json") for p in lidos))           # leu, nao escreveu
        self.assertTrue(any(p.endswith("registo_sinais.csv") for p in lidos))
        self.assertTrue(any(p.endswith("registo_fills.csv") for p in lidos))
        self.assertTrue(os.path.exists(cfg.sinais_csv))
        self.assertTrue(os.path.exists(cfg.estado_sinalizador_json))
        self.assertTrue(os.path.exists(os.path.join(cfg.pasta, "relatorio_sinalizador.txt")))
        self.assertEqual(os.listdir(cfg.pasta_metricas), [])


# ==========================================================================
# Privacidade (seccao 6.5 e chave da CoinGlass)
# ==========================================================================
class Privacidade(Base):
    EXTRA = "[coinglass]\nchave = %s\n" % CHAVE

    @staticmethod
    def linha(end: str, valor: float = 2e6, roi_sem: float = 0.1, roi_mes: float = 0.2, vlm_mes: float = 1e7) -> Dict[str, Any]:
        return {"ethAddress": end, "accountValue": str(valor), "windowPerformances": [
            ["day", {"roi": "0.01", "pnl": "1", "vlm": "1"}], ["week", {"roi": str(roi_sem), "pnl": "5", "vlm": "100"}],
            ["month", {"roi": str(roi_mes), "pnl": "9", "vlm": str(vlm_mes)}]]}

    def test_baleias_csv_so_tem_prefixo_e_hash(self):
        cfg = self.cfg
        e_ok = "0x" + "ab" * 20
        e_grande = "0x" + "cd" * 20
        e_pobre, e_perde, e_roda, e_liq, e_mm = ("0x" + x * 20 for x in ("01", "02", "03", "04", "05"))
        excluido = cfg.p.enderecos_excluidos[0]
        rows = [self.linha(e_ok), self.linha(e_grande, valor=5e6), self.linha(e_pobre, valor=5e5), self.linha(e_perde, roi_mes=-0.1),
                self.linha(e_roda, vlm_mes=2e8), self.linha(e_liq), self.linha(e_mm), self.linha(excluido, valor=9e6),
                {"ethAddress": "lixo"}, "nao e dicionario"]
        negocios = {rv.hash_endereco(e_mm): (1000.0, 100000.0), rv.hash_endereco(e_ok): (60000.0, 100000.0)}
        lista = sn.construir_baleias(cfg, rows, negocios, {rv.hash_endereco(e_liq)}, set(cfg.p.enderecos_excluidos))
        self.assertEqual(lista, [e_grande, e_ok])                                 # por accountValue decrescente
        sn.guardar_baleias(cfg.baleias_csv, rows, lista, T0)
        texto = self.texto(cfg.baleias_csv)
        for e in (e_ok, e_grande, e_pobre, excluido):
            self.assertNotIn(e, texto)
        linhas = self.ler(cfg.baleias_csv)
        self.assertEqual(list(linhas[0].keys()), sn.COLUNAS_BALEIAS)
        self.assertEqual([(ln["prefixo"], ln["hash"]) for ln in linhas],
                         [(e_grande[:10], rv.hash_endereco(e_grande)), (e_ok[:10], rv.hash_endereco(e_ok))])
        self.assertEqual(len(linhas[0]["hash"]), 10)
        self.assertEqual((linhas[0]["valor_usd"], linhas[0]["roi_semana"], linhas[0]["hora_utc"]), ("5000000", "0.1000", md.iso_utc(T0)))
        self.assertEqual(sn.carregar_baleias(cfg.baleias_csv), [rv.hash_endereco(e_grande), rv.hash_endereco(e_ok)])
        self.assertEqual(sn.carregar_baleias(os.path.join(self.pasta, "nao_existe.csv")), [])
        cfg1 = self.config("baleias_max = 1\n", "uma.ini")
        self.assertEqual(sn.construir_baleias(cfg1, rows, {}, set(), set()), [excluido])   # sem exclusoes manda o maior
        # a sonda so guarda hashes e posicoes; um endereco que falha fica de fora com o prefixo no log
        pedidos: List[str] = []

        def pedir(url: str, corpo: Dict[str, Any]) -> Any:
            pedidos.append(corpo["user"])
            if corpo["user"] == e_ok:
                raise RuntimeError("falhou para %s" % corpo["user"])
            return {"assetPositions": [{"position": {"coin": "BTC", "szi": "-2", "positionValue": "200000", "liquidationPx": "105"}},
                                       {"position": {"coin": "ETH", "szi": "0", "positionValue": "1"}}]}

        pos = sn.sondar_baleias(cfg, lista, sn.OrcamentoPeso(400), pedir)
        self.assertEqual(pedidos, [e_grande, e_ok])
        self.assertEqual(list(pos), [rv.hash_endereco(e_grande)])
        self.assertEqual(pos[rv.hash_endereco(e_grande)], [rv.Posicao("BTC", -2.0, 200000.0, 105.0)])
        aviso = [m for m in self.captura.mensagens if "Sonda de" in m]
        self.assertEqual(len(aviso), 1)
        self.assertIn(e_ok[:10] + "...", aviso[0])
        self.assertIn("falhou para", aviso[0])
        self.assertNotIn(e_ok, aviso[0])                                         # mesmo quando o erro ecoa o pedido

    def test_a_chave_nao_aparece_em_ficheiros_nem_no_log_depois_de_falhar(self):
        cfg = self.cfg
        self.assertEqual(cfg.cg_chave, CHAVE)
        os.makedirs(cfg.pasta, exist_ok=True)
        ficheiro_log = logging.FileHandler(cfg.log_sinalizador, encoding="utf-8")
        ficheiro_log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger("sinalizador").addHandler(ficheiro_log)
        try:
            import websockets
            s = sn.Sinalizador(cfg)

            def ligar_falha(uri: str, **kw: Any) -> Any:
                raise RuntimeError("ligacao recusada em %s" % uri)                # a biblioteca ecoa o URL com a chave

            def rest_falha(cfg_: Any) -> Any:
                raise RuntimeError("HTTP 401 para CG-API-KEY %s" % cfg_.cg_chave)

            async def correr() -> None:
                tarefa = asyncio.ensure_future(s.tarefa_coinglass())
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if s.cg_estado == "a religar":
                        break
                tarefa.cancel()
                try:
                    await tarefa
                except asyncio.CancelledError:
                    pass

            with mock.patch.object(websockets, "connect", ligar_falha), mock.patch.object(sn, "fundo_de_liquidacoes", rest_falha):
                asyncio.run(correr())
                s._poll_rest()
            self.assertEqual(s.cg_estado, "a religar")
            self.assertTrue(s.cg_rest_parado)
            falhas = [m for m in self.captura.mensagens if "CoinGlass" in m]
            self.assertEqual(len(falhas), 2)
            self.assertIn("ligacao falhou", falhas[0])
            self.assertIn("***", falhas[0])
            self.assertIn("REST parado", falhas[1])
            self.assertIn("***", falhas[1])
            self.assertNotIn(CHAVE, self.captura.texto())
            at = s.ativos["BTC"]
            self.preparar(at)
            for k, z in ((0, 0.0), (1, -2.3), (2, -1.8)):
                v = _vela(k, z)
                at.ctx_ms = v.t
                s._fechar(("BTC", "15m"), v, v.T + 500)
            self.assertEqual(at.ultimo_motivo, "ok")
            self.assertEqual(rv.interpretar_nota(at.ultima_nota and self.texto(cfg.sinais_csv).strip().split("\n")[1].split(",")[6])["liqsrc"], "na")
            s._escrever_estado(T0 + MS_1H)
            with contextlib.redirect_stdout(io.StringIO()):
                sn.cmd_relatorio(cfg)
                sn.cmd_ensaios(cfg)
            s.parar()
        finally:
            logging.getLogger("sinalizador").removeHandler(ficheiro_log)
            ficheiro_log.close()
        vistos = 0
        for raiz, _, nomes in os.walk(self.pasta):
            for nome in nomes:
                if nome == "config.ini":
                    continue
                vistos += 1
                with open(os.path.join(raiz, nome), "r", encoding="utf-8", errors="replace") as f:
                    self.assertNotIn(CHAVE, f.read(), nome)
        self.assertGreaterEqual(vistos, 6)       # log, sinais, registo, estado, velas, relatorio, ensaios
        self.assertIn("ligacao falhou", self.texto(cfg.log_sinalizador))
        self.assertIn(CHAVE, self.texto(self.ini))                                 # so o config.ini a tem


# ==========================================================================
# Relatorio (seccoes 11.5 e 14)
# ==========================================================================
class Relatorio(Base):
    R_LIQ = [50.0, -30.0, 80.0, -20.0, 40.0, 60.0]
    R_3600 = [10.0, -5.0, 20.0, 0.0, 15.0, 12.0]
    ENCH = [1, 0, 1, 1, 0, 1]

    def preencher(self) -> None:
        reg = md.Registo(self.cfg.reg_sinalizador, sn.COLUNAS_REGISTO)
        reg_med = md.Registo(self.cfg.reg_sinais, ["id", "hora_utc", "ativo", "lado", "mid0", "imp_bps", "imp_viavel",
                                                   "sombra_preenchida", "ganho_passiva_bps", "r_900", "r_3600", "r_14400", "nota"])
        for i, r in enumerate(self.R_LIQ):
            hora = T0 + i * 8 * MS_15M
            nota = rv.nota_sinal({"var": "abc123", "fase": "cal", "z0": -1.80 - 0.02 * i, "P": 0.899, "bal": 1,
                                  "flx": 0.4 if i % 2 == 0 else -0.2, "fz": -1.2, "liq": NAN, "ses": "europa", "ctx": "VERDE"})
            reg.escrever({"id": "BTC-%d" % i, "hora": md.iso_utc(hora), "ativo": "BTC", "lado": "compra", "preco": "100",
                          "G": "124.00", "L": "120.00", "fase": "cal", "motivo": "alvo" if r > 0 else "stop",
                          "hora_saida": md.iso_utc(hora + 4 * MS_1H), "r_bruto_bps": "%.2f" % (r + 8), "funding_bps": "0.000",
                          "r_liq_bps": "%.2f" % r, "nota": nota})
            reg_med.escrever({"id": "m%d" % i, "hora_utc": md.iso_utc(hora + 700), "ativo": "BTC", "lado": "compra", "mid0": "100",
                              "imp_bps": "1.5", "imp_viavel": "1", "sombra_preenchida": str(self.ENCH[i]),
                              "ganho_passiva_bps": "0.5", "r_900": "3", "r_3600": "%.1f" % self.R_3600[i], "r_14400": "9", "nota": nota})
        reg.escrever({"id": "BTC-aberto", "hora": md.iso_utc(T0 + 60 * MS_15M), "ativo": "BTC", "lado": "venda", "G": "100", "L": "90"})
        reg_med.escrever({"id": "m9", "hora_utc": md.iso_utc(T0 + 99 * MS_15M), "ativo": "ETH", "lado": "venda", "r_3600": "1"})

    def relatorio(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            sn.cmd_relatorio(self.cfg)
        texto = self.texto(os.path.join(self.cfg.pasta, "relatorio_sinalizador.txt"))
        self.assertEqual(saida.getvalue(), texto)
        return texto

    def test_relatorio_corre_sem_registos(self):
        texto = self.relatorio()
        self.assertIn("Trades virtuais fechados: 0 (0 ligados", texto)
        self.assertIn("Ainda nao ha trades virtuais fechados.", texto)
        self.assertIn("Ainda nao ha trades ligados a sinais medidos.", texto)
        self.assertIn("Sem trades com z0 e P na nota.", texto)
        self.assertNotIn("custos c_W", texto)                                    # sem trades nao ha linha por activo

    def test_relatorio_cruza_os_registos_e_agrupa_pela_nota(self):
        self.preencher()
        texto = self.relatorio()
        r = self.R_LIQ
        n = len(r)
        ep = math.sqrt(sum((x - nu.media(r)) ** 2 for x in r) / (n - 1) / n)
        p_hat = 4 / 6
        p_inf = rv.wilson_inferior(p_hat, n)
        p_est = nu.acerto_equilibrio(124.0, 120.0, 8.0, 13.0)
        m = rv.margem_decisao(p_hat, n, 0.05)
        self.assertIn("Trades virtuais fechados: 6 (6 ligados a sinais medidos pelo medidor)", texto)
        self.assertIn("BTC: custos c_W 8.0 c_L 13.0 bps (defeitos)", texto)
        todos = next(ln for ln in texto.split("\n") if ln.strip().startswith("todos"))
        self.assertIn("n=6", todos)
        self.assertIn("expectancia %.2f bps (EP %.2f)" % (nu.media(r), ep), todos)
        self.assertIn("p_hat %.3f p_inf %.3f" % (p_hat, p_inf), todos)
        self.assertIn("p* %.2f margem %.2f -> sombra" % (p_est, m), todos)
        self.assertIn("mediana G 124.00 L 120.00", todos)
        self.assertIn("alvo 4 stop 2", todos)
        self.assertIn("fase cal", texto)
        for grupo in ("var=abc123", "ctx=VERDE", "bal=1", "fz=neg", "liq=na", "ses=europa"):
            linha = next(ln for ln in texto.split("\n") if ln.strip().startswith(grupo))
            self.assertIn("n=6", linha)
        pos = next(ln for ln in texto.split("\n") if ln.strip().startswith("flx=pos"))
        neg = next(ln for ln in texto.split("\n") if ln.strip().startswith("flx=neg"))
        self.assertIn("n=3", pos)
        self.assertIn("expectancia %.2f bps" % nu.media([r[0], r[2], r[4]]), pos)
        self.assertIn("expectancia %.2f bps" % nu.media([r[1], r[3], r[5]]), neg)
        self.assertIn("p_hat 1.000", pos)
        self.assertIn("p_hat 0.333", neg)
        # cruzamento com o medidor: r_3600 e a regra passiva/agressiva reproduzem o nucleo
        rs = self.R_3600
        ep_r = math.sqrt(sum((x - nu.media(rs)) ** 2 for x in rs) / (n - 1) / n)
        self.assertIn("r_3600   n=6    media %.2f bps (EP %.2f) | r_liq virtual nos mesmos %.2f bps" % (nu.media(rs), ep_r, nu.media(r)), texto)
        self.assertIn("r_14400  n=6    media 9.00 bps", texto)
        ench = [x for x, e in zip(rs, self.ENCH) if e]
        v_ag, v_pas, escolha = nu.regra_rotas(nu.media(rs), 1.5, 4 / 6, nu.media(ench), 0.5, 4.5, 1.5)
        dif, erro = nu.margem_regra(rs, [1.5] * 6, [bool(e) for e in self.ENCH], [0.5] * 6, 4.5, 1.5)
        self.assertAlmostEqual(v_ag, nu.media(rs) - 6.0)
        self.assertIn("regra aos 3600 s (n=6): V agressiva %.2f | V passiva %.2f -> %s; diferenca %.2f bps, EP %.2f: %s"
                      % (v_ag, v_pas, escolha.upper(), dif, erro, "clara (2 EP)" if nu.margem_clara(dif, erro) else "dentro do ruido"),
                      texto)
        # calibracao de P_teo por caixas de z0
        brier = nu.media([(0.899 - (1.0 if x > 0 else 0.0)) ** 2 for x in r])
        self.assertIn("|z0| em [1.75, 2.00): n=6    P_teo 0.899 | acertos %.3f (Wilson inf %.3f)" % (p_hat, p_inf), texto)
        self.assertIn("ECE %.3f | Brier %.3f (n=6)" % (abs(p_hat - 0.899), brier), texto)
        self.assertNotIn("[2.00, 2.50)", texto)
        self.assertIn("congelados", texto)
        # juntar_registos: so liga activo, lado e hora a menos de 2 s (ou a nota igual)
        ligados = sn.juntar_registos(sn._ler_csv(self.cfg.reg_sinalizador), sn._ler_csv(self.cfg.reg_sinais))
        self.assertEqual([x["medidor"]["id"] if x["medidor"] else None for x in ligados], ["m%d" % i for i in range(6)] + [None])
        self.assertEqual(ligados[0]["campos"]["flx"], "0.40")
        self.assertEqual(sn._caixa("liq", "0.3"), "<=0.5")
        self.assertEqual((sn._caixa("fz", "na"), sn._caixa("flx", "0"), sn._caixa("ses", "")), ("na", "zero", "na"))


# ==========================================================================
# Correccoes da revisao (um teste por achado corrigido no sinalizador)
# ==========================================================================
def _msg_vela(v: rv.Vela, intervalo: str = "15m") -> Dict[str, Any]:
    return {"channel": "candle", "data": {"t": v.t, "T": v.T, "s": "BTC", "i": intervalo, "o": str(v.o), "c": str(v.c),
                                          "h": str(v.h), "l": str(v.l), "v": "1", "n": v.n}}


def _negocio(t_ms: int, side: str, sz: float, tid: int, px: float = 100.0, comprador: int = 1, vendedor: int = 2,
             hash_: str = "0x1") -> Dict[str, Any]:
    return {"coin": "BTC", "px": repr(px), "sz": repr(sz), "side": side, "time": t_ms, "tid": tid, "hash": hash_,
            "users": ["0x%040x" % comprador, "0x%040x" % vendedor]}


def _passeio_1h(n: int, t0: int, semente: int, preco: float = 100.0, sd: float = 0.005, phi: float = 1.0) -> List[rv.Vela]:
    """n velas de 1 h de um passeio aleatorio em log-preco (phi = 1) ou de um OU x_t = mu + phi (x_{t-1} - mu) + eps, com T = t + 1 h - 1 ms."""
    g = random.Random(semente)
    mu = math.log(preco)
    x = mu
    velas = []
    for i in range(n):
        o = math.exp(x)
        x = mu + phi * (x - mu) + sd * g.gauss(0.0, 1.0)
        c = math.exp(x)
        velas.append(rv.Vela(t0 + i * MS_1H, t0 + (i + 1) * MS_1H - 1, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 5.0, 20, True))
    return velas


class Correccoes(Base):
    CTX = {"funding": "0.0001", "openInterest": "1000", "markPx": "100", "oraclePx": "100", "premium": "0", "dayNtlVlm": "1e8"}

    def activo(self, cfg: Optional[sn.ConfigSinalizador] = None) -> sn.ActivoSinal:
        at = sn.ActivoSinal("BTC", cfg or self.cfg, 5)
        self.preparar(at)
        at.registo = md.Registo((cfg or self.cfg).reg_sinalizador, sn.COLUNAS_REGISTO)
        return at

    def test_T_inclusivo_funding_na_marca_de_hora_sessao_e_arrefecimento(self):
        # as velas de _vela tem T = t + 15 min - 1 ms, como a Hyperliquid: o fim calcula-se pela abertura
        at = self.activo()
        at.contexto_activo(dict(self.CTX), T0)
        self.assertEqual(_vela(3, 0.0).T % MS_1H, MS_1H - 1)
        self.fechar(at, 1, 0.0)
        self.fechar(at, 2, -2.3)
        sinal, motivo, _ = self.fechar(at, 3, -1.8)                               # 10:45-11:00: fecha a hora
        self.assertEqual(motivo, "ok")
        self.assertEqual(rv.interpretar_nota(sinal.nota)["hr"], "11")              # sessao e hora pelo fim da vela
        self.assertEqual(at.trade.funding_bps, 0.0)                                # a vela da entrada nao cobra
        for k in (4, 5, 6):
            self.fechar(at, k, -1.6)
        self.assertEqual(at.trade.funding_bps, 0.0)
        self.fechar(at, 7, -1.6)                                                    # 11:45-12:00: marca de hora
        self.assertAlmostEqual(at.trade.funding_bps, 1.0)                           # 1e-4 x BPS, pago pelo long
        _, motivo, linha = self.fechar(at, 8, -2.5, z_lo=-3.2)                      # stop (FORA)
        self.assertEqual((linha["motivo"], linha["funding_bps"]), ("stop", "1.000"))
        self.assertEqual(linha["hora_saida"], md.iso_utc(_vela(8, -2.5).T))
        self.assertEqual((at.t_stop_ms, at.arrefecimento), (_vela(8, -2.5).t, 2))   # o inicio da vela do stop
        self.assertEqual(self.fechar(at, 9, -1.9)[1], "G7")                          # 1.a vela depois do stop
        self.assertEqual(at.arrefecimento, 1)
        self.assertEqual(self.fechar(at, 10, -2.4)[1], "G2")                         # 2.a: ainda conta
        self.assertEqual(at.arrefecimento, 0)
        self.assertEqual(self.fechar(at, 11, -1.9)[1], "ok")                         # 3.a: livre

    def test_colunas_raj_twap_abs_sao_absolutas_e_o_backtest_le_as_nos_dois_lados(self):
        # o sinalizador grava o lado da rajada e do TWAP e o residuo sem o factor lado; _Baleias le-os com o lado do sinal
        resultados = {}
        for lado, z_fora, z_in in ((1, -2.3, -1.8), (-1, 2.3, 1.8)):
            at = self.activo()
            at.r15_hist.extend([0.001 * (1 if j % 2 else -1) + 0.0004 * (1 if j % 3 else -1) for j in range(40)])
            at.u_hist.extend([1.0 if j % 2 else -1.0 for j in range(40)])
            self.fechar(at, 0, 0.0)
            self.fechar(at, 1, z_fora)
            at.ewma_vol.juntar(20_000.0)
            den = at.ewma_vol.valor                                                  # (45) EWMA_96(V)_{k-1}: as velas sem negocios contam 0
            v = _vela(2, z_in)
            t = v.t + 1000
            for i in range(100):                                                     # Q99 = 1000 usd
                at.negocio(_negocio(t + i * 100, "B", 10.0, 100 + i, 100.0, i + 1, 7000), t + i * 100)
            for i in range(4):                                                       # TWAP de 4 vendas a 30 s, hash a zeros
                at.negocio(_negocio(v.T - 200_000 + 30_000 * i, "A", 0.5, 500 + i, 100.0, 8000, 9500, "0x0"), v.T - 200_000 + 30_000 * i)
            t_bloco = v.T - 30_000                                                    # rajada de vendas: 3 x 500 usd, -4 bps
            for i in range(3):
                at.negocio(_negocio(t_bloco + i * 400, "A", 5.0, 900 + i, 100.0 * (1.0 - 0.0002 * i), 8001, 9000, "0x0"), t_bloco + i * 400)
            sinal, motivo = at.fecho_15m(v, v.T + 500)
            col = dict(at.ultima)
            self.assertEqual((col["raj"], col["twap"]), (-1, -1))                    # vendas, independentemente do lado
            v_b, v_a = col["v_b"], col["v_a"]
            u_k = (v_b - v_a) / den
            r_k = math.log(v.c / _vela(1, z_fora).c)
            _, _, a_abs = rv.absorcao_residual(list(at.r15_hist)[:-1], list(at.u_hist)[:-1], r_k, u_k, 1)
            self.assertAlmostEqual(col["abs"], a_abs, places=9)
            leitor = bt._Baleias()
            leitor.ewma_ant = den                                                    # a EWMA_{k-1} que o backtest teria das colunas anteriores
            leitor.vela(v, col)
            ok, b_k, f_b = leitor.avaliar(lado, col, at.exc, self.cfg.p_bal)
            resultados[lado] = (motivo, ok, b_k, f_b, sinal)
        motivo, ok, b_k, f_b, _ = resultados[1]                                      # compra: rajada de vendas CONTRA
        self.assertEqual(motivo, "G6")
        self.assertFalse(ok)
        motivo, ok, b_k, f_b, sinal = resultados[-1]                                 # venda: a rajada e o TWAP sao a favor
        self.assertEqual(motivo, "ok")
        self.assertTrue(ok)
        nota = rv.interpretar_nota(sinal.nota)
        self.assertEqual((nota["raj"], nota["twap"]), ("0", "1"))                    # na nota: relativos ao lado do sinal
        self.assertEqual(int(nota["bal"]), b_k)                                      # a mesma pontuacao nos dois modulos
        self.assertAlmostEqual(float(nota["fb"]), f_b, places=2)

    def test_negocio_e_contexto_depois_de_T_nao_entram_no_fecho(self):
        cfg = self.config("[coinglass]\nchave = %s\n" % CHAVE, "cg.ini")
        at = self.activo(cfg)
        at.fecho_15m(_vela(-1, 0.0), _vela(-1, 0.0).T + 500, avaliar=False)       # vela anterior: r_k passa a existir
        at.ewma_vol.juntar(1000.0)
        v = _vela(0, 0.0)
        at.contexto_activo(dict(self.CTX, openInterest="10"), v.T - 100)
        at.contexto_activo(dict(self.CTX, openInterest="999"), v.T + 300)            # depois de T: nao conta
        at.negocio(_negocio(v.T - 1000, "B", 1.0, 1), v.T - 900)
        at.negocio(_negocio(v.T + 500, "B", 7.0, 2), v.T + 600)                       # da folga: nao conta
        liq = {"base_asset": "BTC", "exchange": "Hyperliquid", "side": 1, "symbol": "BTCUSDT", "price": 100.0}
        at.liquidacao(dict(liq, volume_usd=300.0, time=v.T - 10), v.T - 10)
        at.liquidacao(dict(liq, volume_usd=5000.0, time=v.T + 10), v.T + 10)
        at.fecho_15m(v, v.T + 700)
        self.assertEqual((at.ultima["v_b"], at.ultima["v_a"]), (100.0, 0.0))
        self.assertAlmostEqual(at.u_hist[-1], 0.1)                                    # (100 - 0) / EWMA anterior
        self.assertEqual(at.ultima["oi_usd"], 1000.0)                                 # 10 x markPx 100
        self.assertEqual(at.ultima["liq_long"], 300.0)
        self.assertEqual(at.ultima["n_grandes"], 0)
        self.assertEqual(at.ctx_em(v.T - 200)["openInterest"], 10.0)                  # todos posteriores: o mais antigo
        self.assertEqual(at.ctx_em(v.T + 300)["openInterest"], 999.0)
        self.assertEqual(sn.ActivoSinal("ETH", cfg, 4).ctx_em(v.T), {})                # sem nenhum contexto

    def test_ewma_do_volume_nao_e_alimentada_no_aquecimento(self):
        at = self.activo()
        for k in range(200):
            at.fecho_15m(_vela(k, 0.0), _vela(k, 0.0).T, avaliar=False)
        self.assertEqual(at.ewma_vol.n, 0)
        v = _vela(200, 0.0)
        at.negocio(_negocio(v.T - 100, "B", 10.0, 1), v.T - 50)                       # V = 1000
        at.fecho_15m(v, v.T + 500)
        self.assertEqual(at.ewma_vol.n, 1)
        v2 = _vela(201, 0.0)
        at.negocio(_negocio(v2.T - 100, "B", 6.0, 2), v2.T - 50)
        at.negocio(_negocio(v2.T - 90, "A", 4.0, 3), v2.T - 50)                       # V_B - V_A = 200 = 0,2 V
        at.fecho_15m(v2, v2.T + 500)
        self.assertAlmostEqual(at.u_hist[-1], 0.2, places=9)                          # e nao 28 vezes mais

    def test_stop_nao_conta_o_impacto_duas_vezes(self):
        at = self.activo()
        at.imp_bps = 2.0
        self.fechar(at, 0, 0.0)
        self.fechar(at, 1, -2.3)
        self.assertEqual(self.fechar(at, 2, -1.8)[1], "ok")
        self.assertEqual(at.trade.imp_bps, 0.0)
        _, _, linha = self.fechar(at, 3, -2.5, z_lo=-3.2)
        p_stop, p_e = math.exp(A0 - 3.0 * SIG), _vela(2, -1.8).c
        self.assertEqual(linha["preco_saida"], "%.8f" % p_stop)                       # preenchido no nivel
        self.assertEqual(linha["r_liq_bps"], "%.2f" % (nu.BPS * math.log(p_stop / p_e) - 13.0))   # c_L ja tem imp x 2

    def test_funding_cobrado_em_todas_as_marcas_de_hora_apos_a_entrada(self):
        # entrada as :15, saida a 1:30: exactamente um funding (o da 1:00), como no backtest
        at = self.activo()
        t0 = T0 - MS_1H
        at.contexto_activo(dict(self.CTX), t0)
        for k, z in ((0, 0.0), (1, 0.0), (2, 0.0), (3, -2.3)):
            self.fechar(at, k, z, t0=t0)
        sinal, motivo, _ = self.fechar(at, 4, -1.8, t0=t0)                            # 10:00-10:15
        self.assertEqual(motivo, "ok")
        self.assertEqual((sinal.hora_ms - 500 + 1) % MS_1H, 15 * 60_000)
        for k in (5, 6, 7, 8):
            self.fechar(at, k, -1.5, t0=t0)
        self.assertAlmostEqual(at.trade.funding_bps, 1.0)
        _, _, linha = self.fechar(at, 9, -0.4, t0=t0)                                 # 11:15-11:30: passa o alvo (z = -0,5)
        self.assertEqual((linha["motivo"], linha["funding_bps"]), ("alvo", "1.000"))

    def test_corte_nao_fecha_pelo_relogio_e_a_religacao_recupera_a_vela_final(self):
        s = sn.Sinalizador(self.cfg)
        at = s.ativos["BTC"]
        self.preparar(at)
        s.aquecido = True
        s.ligado = True
        v0, v1 = _vela(0, 0.0), _vela(1, -2.3)
        s._fechar(("BTC", "15m"), v0, v0.T + 500)
        s._fechar(("BTC", "15m"), v1, v1.T + 500)
        final2, final3 = _vela(2, -1.8), _vela(3, -1.5)
        parcial = rv.Vela(final2.t, final2.T, final2.o, final2.o * 1.0005, final2.o * 0.9995, final2.o * 1.0002, 0.3, 2, True)
        s.pendentes[("BTC", "15m")] = parcial
        s._cortar(at, final2.t + 300_000)                                             # corte a meio da vela
        self.assertEqual(s.pendentes, {})
        s.ligado = False
        s.pendentes[("BTC", "15m")] = parcial
        s._fechos_pelo_relogio(final2.T + 10_000)                                     # sem ligacao o relogio nao fecha
        self.assertEqual(s.fechadas[("BTC", "15m")], v1.t)
        s.pendentes.clear()
        pedidos: List[Dict[str, Any]] = []

        def pedir(url: str, corpo: Dict[str, Any]) -> Any:
            pedidos.append(corpo)
            req = corpo["req"]
            if req["interval"] != "15m":
                return []
            return [dict(_msg_vela(v)["data"]) for v in (final2, final3) if v.t >= req["startTime"] and v.T <= req["endTime"]]

        s.ligado = True
        motivo_antes = at.ultimo_motivo
        with mock.patch.object(sn, "pedir_info", pedir), mock.patch.object(sn, "agora_ms", lambda: final3.T + 2000):
            asyncio.run(s._recuperar(["BTC"]))
        self.assertEqual(pedidos[0]["req"]["startTime"], v1.t + MS_15M)
        self.assertEqual(s.fechadas[("BTC", "15m")], final3.t)
        self.assertEqual(s.hist[("BTC", "15m")]._velas[final2.t].c, final2.c)        # a versao final, nao a parcial
        self.assertAlmostEqual(at.exc.z_ant, -1.8)
        self.assertEqual(at.ultimo_motivo, motivo_antes)                              # recuperadas sem avaliacao
        self.assertIsNone(at.trade)
        self.assertFalse(os.path.exists(self.cfg.sinais_csv))
        linhas = self.ler(os.path.join(self.cfg.pasta_velas, "BTC_15m_%s.csv" % sn.dia_utc(T0)))
        por_t = {ln["t"]: ln for ln in linhas}
        self.assertEqual(por_t[str(final2.t)]["c"], "%.8f" % final2.c)
        self.assertEqual((por_t[str(final2.t)]["corte"], por_t[str(v1.t)]["corte"]), ("1", "0"))
        self.assertEqual(s.recuperando, set())
        # velas fechadas pelo canal durante uma recuperacao esperam pela ordem certa
        v4, v5 = _vela(4, -1.2), _vela(5, -1.0)
        s.recuperando.add("BTC")
        s._tratar(_msg_vela(v4))
        s._tratar(_msg_vela(v5))
        self.assertEqual(s.fechadas[("BTC", "15m")], final3.t)
        self.assertEqual([v.t for v in s.em_espera[("BTC", "15m")]], [v4.t])
        s._alimentar_em_falta("BTC", {}, v5.T + 2000)
        s.recuperando.discard("BTC")
        self.assertEqual(s.fechadas[("BTC", "15m")], v4.t)
        self.assertEqual(s.pendentes[("BTC", "15m")].t, v5.t)

    def test_lote_reenviado_na_religacao_nao_entra_duas_vezes(self):
        s = sn.Sinalizador(self.cfg)
        at = s.ativos["BTC"]
        s.ligacao_ms = T0
        lote = [_negocio(T0 + i * 100, "B", 1.0, 1000 + i) for i in range(5)]
        s._tratar({"channel": "trades", "data": lote})
        v_b, n = at.fluxo_15m.v_b(T0 + 1000), at.n_negocios
        self.assertEqual((v_b, n), (500.0, 5))
        s._tratar({"channel": "trades", "data": lote})                                 # reenviado ao subscrever
        self.assertEqual((at.fluxo_15m.v_b(T0 + 1000), at.n_negocios), (v_b, n))
        self.assertEqual(len(at.grandes._neg), 5)
        sem_tid = dict(lote[0])
        sem_tid.pop("tid")
        s._tratar({"channel": "trades", "data": [sem_tid, sem_tid]})                  # sem tid nao ha como saber
        self.assertEqual(at.n_negocios, 7)

    def test_rearranque_replay_entrelacado_e_trade_ja_fechado(self):
        # so |z| >= z_veto pode invalidar (t_amarelo, vr_veto e desloc_alvo_max fora de alcance): a hora extrema e a segunda
        cfg = self.config("h_a = 4\nn_min = 24\nn_ajuste = 24\nn_max = 200\nn_z = 24\ncalibrar_nula = nao\n"
                          "t_amarelo = 100\nvr_veto = 100\ndesloc_alvo_max = 100\n", "replay.ini")
        t0 = T0 - 200 * MS_1H
        v1h = _passeio_1h(200, t0, 3, phi=0.5)                                      # OU de meia-vida 1 h: z limitado
        c = v1h[-1].c
        extrema = rv.Vela(T0 + MS_1H, T0 + 2 * MS_1H - 1, c, c * 1.55, c * 0.999, c * 1.5, 5.0, 20, True)   # |z| >> z_veto
        v1h += [rv.Vela(T0, T0 + MS_1H - 1, c, c * 1.001, c * 0.999, c, 5.0, 20, True), extrema]
        v15 = [rv.Vela(T0 - 8 * MS_1H + k * MS_15M, T0 - 8 * MS_1H + (k + 1) * MS_15M - 1, c, c * 1.0001, c * 0.9999, c, 1.0, 5, True)
               for k in range(40)]                                                    # 8 h antes e 2 h depois de T0, planas
        for intervalo, velas in (("1h", v1h), ("15m", v15)):
            h = sn.Historico("BTC", intervalo, cfg.pasta_velas)
            for v in velas:
                h.guardar(v, {})
        sn.guardar_funding(cfg, "BTC", [{"time": T0 + MS_1H, "fundingRate": "0.0001", "premium": "0"}])
        desde = T0 + MS_15M - 1                                                       # a instancia anterior viu ate as 10:15
        s1 = sn.Sinalizador(cfg)
        at1 = s1.ativos["BTC"]
        self.preparar(at1)
        v_ent = next(v for v in v15 if v.T == desde)
        at1._abrir_trade(v_ent, desde + 500, 1, c * 1.05, c * 0.9, 64, 500.0, 1000.0, "cal", 0.5, 1000.0, "revou v=2", -1.8)
        at1.t_15m_ult = desde
        s1._escrever_estado(desde + 600)
        s2 = sn.Sinalizador(cfg)
        at2 = s2.ativos["BTC"]
        self.assertIsNotNone(at2.trade)
        s2.aquecer(rede=False)
        self.assertTrue(at2.aj.valido, "precondicao: ajuste valido na serie OU")
        self.assertLess(max(abs(z) for z in list(at2.z_1h_hist)[-3:-1]), cfg.p_ctx.z_veto)   # 10:00 e 11:00 normais
        self.assertGreater(abs(at2.z_1h_hist[-1]), cfg.p_ctx.z_veto)                           # 12:00 extrema
        self.assertIsNone(at2.trade)
        linhas = self.ler(cfg.reg_sinalizador)
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["motivo"], "invalidacao")
        self.assertEqual(linhas[0]["hora_saida"], md.iso_utc(T0 + 2 * MS_1H - 1))    # ao fecho de 15 m do fecho de 1 h que invalidou
        self.assertEqual(linhas[0]["funding_bps"], "1.000")                           # funding das 11:00 vindo de dados/funding
        self.assertEqual(at2.ids_fechados, {linhas[0]["id"]})
        s3 = sn.Sinalizador(cfg)                                                      # o estado ainda tem o trade: nao se repoe
        self.assertIsNone(s3.ativos["BTC"].trade)
        self.assertTrue(any("ja esta fechado no registo" in m for m in self.captura.mensagens))
        s3.aquecer(rede=False)
        self.assertEqual(len(self.ler(cfg.reg_sinalizador)), 1)

    def test_contexto_1h_do_backtest_e_fecho_1h_dao_o_mesmo_ajuste_t_crit_e_q_z(self):
        cfg = self.config("h_a = 24\nn_min = 96\nn_ajuste = 96\nn_max = 200\nn_z = 96\nreplicas_nula = 40\n", "igual.ini")
        at = sn.ActivoSinal("BTC", cfg, 5)
        velas = _passeio_1h(500, T0 - 500 * MS_1H, 11)
        n_a_meio = 0
        for i, v in enumerate(velas):
            at.fecho_1h(v)
            if i == 149:
                n_a_meio = at.aj.n
        at.calibrar()
        params = bt.Params(h_a=24.0, n_min=96, n_ajuste=96, n_max=200, n_z=96, replicas_nula=40, calibrar_nula=True,
                           alpha_nula=0.001, semente_nula=7)
        ctx = bt.contexto_1h(params, velas)
        self.assertEqual((ctx[149].n, n_a_meio), (150 - 96, 150 - 96))               # d so depois de 4 h_A velas
        c = ctx[-1]
        self.assertEqual(c.n, at.aj.n)
        self.assertEqual(c.phi, at.phi_ult)
        self.assertEqual(c.sigma_eq, at.sigma_ult)
        self.assertEqual(c.meia_vida, at.h_ult)
        self.assertEqual(c.t_nulo, at.aj.t_nulo)
        self.assertEqual(c.t_crit, at.t_crit)                                          # a mesma calibracao bit a bit
        self.assertTrue(c.t_crit == c.t_crit and c.t_crit != cfg.p.t_nulo_max)
        self.assertEqual(c.q_z, at.q_z)
        self.assertTrue(c.q_z == c.q_z)
        self.assertEqual(c.vr, at.ctx.vr)
        self.assertEqual(c.sigma_r, at.sigma_r)
        self.assertEqual(c.z, at.z_1h_hist[-1])

    def test_liquidados_7d_alimentado_pelas_rajadas_e_persistido(self):
        s = sn.Sinalizador(self.cfg)
        at = s.ativos["BTC"]
        self.preparar(at)
        v = _vela(2, -1.8)
        t = v.t + 1000
        for i in range(100):
            at.negocio(_negocio(t + i * 100, "B", 10.0, 100 + i, 100.0, i + 1, 7000), t + i * 100)
        t_bloco = v.T - 30_000
        for i in range(3):
            at.negocio(_negocio(t_bloco + i * 400, "A", 5.0, 900 + i, 100.0 * (1.0 - 0.0002 * i), 8001, 9000, "0x0"), t_bloco + i * 400)
        at.fecho_15m(v, v.T + 500)
        h = rv.hash_endereco("0x%040x" % 9000)
        self.assertEqual(s.liquidados_7d, {h: v.T})
        s._escrever_estado(v.T + 1000)
        with open(self.cfg.estado_sinalizador_json, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["liquidados_7d"], {h: v.T})
        with mock.patch.object(sn, "agora_ms", lambda: v.T + 2000):
            s2 = sn.Sinalizador(self.cfg)
        self.assertEqual(s2.liquidados_7d, {h: v.T})
        rows = [{"ethAddress": "0x%040x" % n, "accountValue": "3000000", "windowPerformances": [
            ["week", {"roi": "0.1", "pnl": "1", "vlm": "10"}], ["month", {"roi": "0.2", "pnl": "1", "vlm": "10"}]]} for n in (9000, 9001)]
        with mock.patch.object(sn, "obter_leaderboard", lambda cfg: rows):
            s2._refazer_baleias(v.T + 2000)
        self.assertEqual(s2.baleias_enderecos, ["0x%040x" % 9001])                   # o liquidado fica de fora

    def test_orcamento_de_peso_no_snapshot_e_no_funding(self):
        orc = sn.OrcamentoPeso(100_000)
        h = sn.Historico("BTC", "1h", self.cfg.pasta_velas)

        def pedir(url: str, corpo: Dict[str, Any]) -> Any:
            req = corpo["req"]
            out = []
            t = req["startTime"]
            while t + MS_1H - 1 <= req["endTime"] and len(out) < 120:
                out.append({"t": t, "T": t + MS_1H - 1, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "1", "n": 1})
                t += MS_1H
            return out

        self.assertEqual(h.carregar_snapshot(self.cfg, T0, T0 + 150 * MS_1H, pedir, orc), 150)
        self.assertEqual(orc.gasto(), (20 + 2) + (20 + 1))                            # 120 velas e 30 velas

        def pedir_f(url: str, corpo: Dict[str, Any]) -> Any:
            return [{"time": corpo["startTime"] + i * MS_1H, "fundingRate": "0.0001", "premium": "0"} for i in range(61)]

        linhas = sn.obter_funding_history(self.cfg, "BTC", T0, T0 + 61 * MS_1H, pedir_f, orc)
        self.assertEqual(len(linhas), 61)
        self.assertEqual(orc.gasto(), 43 + 20 + 2)

    def test_pm_na_nota_e_o_premio_mark_oraculo_em_bps(self):
        at = self.activo()
        at.contexto_activo(dict(self.CTX, markPx="100.5", oraclePx="100", premium="0.0001"), T0)
        self.fechar(at, 0, 0.0)
        self.fechar(at, 1, -2.3)
        sinal, motivo, _ = self.fechar(at, 2, -1.8)
        self.assertEqual(motivo, "ok")
        self.assertEqual(rv.interpretar_nota(sinal.nota)["pm"], "50.00")             # (34) BPS (100,5 - 100)/100
        self.assertEqual(at.ultima["premium"], 0.0001)                                # a coluna guarda o premium bruto

    def test_lev_na_nota_e_max_leverage_da_meta(self):
        def pedir(url: str, corpo: Dict[str, Any]) -> Any:
            self.assertEqual(corpo, {"type": "meta"})
            return {"universe": [{"name": "BTC", "szDecimals": 5, "maxLeverage": 40}, {"name": "SOL", "szDecimals": 2, "maxLeverage": 20},
                                 {"name": "ETH", "szDecimals": 4}]}

        with mock.patch.object(sn, "pedir_info", pedir):
            self.assertEqual(sn.obter_max_leverage(self.cfg), {"BTC": 40.0})
        s = sn.Sinalizador(self.cfg, {"BTC": 5, "ETH": 4}, {"BTC": 40.0})
        for nome, esperado in (("BTC", 40.0), ("ETH", NAN)):
            at = s.ativos[nome]
            self.preparar(at)
            self.fechar(at, 0, 0.0)
            self.fechar(at, 1, -2.3)
            sinal, motivo, _ = self.fechar(at, 2, -1.8)
            self.assertEqual(motivo, "ok")
            lev = rv.interpretar_nota(sinal.nota)["lev"]
            if esperado == esperado:
                self.assertAlmostEqual(float(lev), rv.alavancagem_maxima(rv.stop_bps(-1.8, SIG, 3.0), 40.0), places=3)
            else:
                self.assertEqual(lev, "na")

    def test_liqsrc_so_passa_a_cg_quando_o_fluxo_confirma_a_hyperliquid(self):
        cfg = self.config("[coinglass]\nchave = %s\n" % CHAVE, "cg.ini")
        self.assertEqual(cfg.liqsrc, "na")
        s = sn.Sinalizador(cfg)
        base = {"base_asset": "BTC", "side": 1, "symbol": "BTCUSDT", "price": 100.0, "volume_usd": 1000.0}
        s._ingerir_liq(dict(base, exchange="Binance", time=T0), T0)
        self.assertEqual(cfg.liqsrc, "na")
        s._ingerir_liq(dict(base, exchange="Hyperliquid", time=T0 + 1000), T0 + 1000)
        self.assertEqual(cfg.liqsrc, "cg")
        self.assertEqual(s.ativos["BTC"].liq_long.soma(T0 + 1000), 2000.0)


class Notificacao(Base):
    """A notificacao do macOS nunca para o ciclo e nao corre sem osascript."""

    def test_sem_osascript_nao_corre_nada(self):
        with mock.patch.object(sn.shutil, "which", return_value=None):
            self.assertFalse(sn.notificar_mac("t", "x", correr=lambda *a, **k: self.fail("nao devia correr")))

    def test_com_osascript_corre_o_guiao_sem_aspas_nem_quebras(self):
        vistos = []
        with mock.patch.object(sn.shutil, "which", return_value="/usr/bin/osascript"):
            ok = sn.notificar_mac('Sinal "BTC"', 'compra\n1000 usd \\ fim', correr=lambda cmd, **k: vistos.append((cmd, k)))
        self.assertTrue(ok)
        cmd, k = vistos[0]
        self.assertEqual(cmd[:2], ["osascript", "-e"])
        self.assertEqual(cmd[2], 'display notification "compra1000 usd  fim" with title "Sinal BTC" sound name "Glass"')
        self.assertEqual(k.get("timeout"), 5)

    def test_falha_do_comando_nao_levanta_e_fica_no_log(self):
        def rebenta(*a, **k):
            raise OSError("sem permissao")
        with mock.patch.object(sn.shutil, "which", return_value="/usr/bin/osascript"):
            self.assertFalse(sn.notificar_mac("t", "x", correr=rebenta))
        self.assertIn("Notificacao falhou", self.captura.texto())

    def test_chave_notificar_no_config(self):
        self.assertTrue(self.cfg.p.notificar)
        self.assertFalse(self.config("notificar = nao\n", "n.ini").p.notificar)


class EmissaoDesligada(Base):
    """Por defeito o sinalizador nao escreve em sinais.csv e corre o teste pre-registado so em registo."""

    def test_defeitos_e_chaves(self):
        caminho = os.path.join(self.pasta, "minimo.ini")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("[geral]\nativos = BTC\n[sinalizador]\n")   # sem a chave: o defeito e nao emitir
        minimo = sn.ConfigSinalizador(caminho)
        self.assertFalse(minimo.p.emitir)
        self.assertTrue(minimo.p.teste3)
        cfg = self.config("emitir = sim\nteste3 = nao\n", "e.ini")
        self.assertTrue(cfg.p.emitir)
        self.assertFalse(cfg.p.teste3)

    def test_fecho_com_sinal_nao_escreve_em_sinais_csv(self):
        cfg = self.config("emitir = nao\n", "off.ini")
        s = sn.Sinalizador(cfg, {"BTC": 5, "ETH": 4})
        at = s.ativos["BTC"]
        self.assertIsNotNone(at.teste3)
        self.assertTrue(os.path.exists(self.cfg.reg_teste3))
        sinal = rv.Sinal(T0, "BTC", 1, 100.0, 50.0, 1000.0, "revou v=2")
        with mock.patch.object(at, "fecho_15m", return_value=(sinal, "ok")), \
             mock.patch.object(at, "avancar_trade_virtual", return_value=None):
            s._fechar(("BTC", "15m"), rv.Vela(T0, T0 + 900_000 - 1, 100.0, 100.5, 99.5, 100.2, 1.0, 1), T0 + 1000)
        self.assertFalse(os.path.exists(cfg.sinais_csv) and os.path.getsize(cfg.sinais_csv) > len(sn.CAB_SINAIS) + 2)
        self.assertIn("NAO emitido", self.captura.texto())

    def test_comando_teste3sigma_recusa_antes_de_100(self):
        s = sn.Sinalizador(self.cfg, {"BTC": 5, "ETH": 4})
        s.ativos["BTC"].registo_teste3.escrever({"id": "BTC-1", "ativo": "BTC", "R": "0.5", "entrada": "100", "stop": "99"})
        res = sn.resumo_teste3(sn._ler_csv(self.cfg.reg_teste3))
        self.assertEqual(res["n"], 1)
        self.assertFalse(res["avaliavel"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sn.cmd_teste3sigma(self.cfg)
        self.assertIn("Faltam 99", buf.getvalue())

    def test_resumo_com_100_trades(self):
        linhas = [{"id": "BTC-%d" % i, "ativo": ["BTC", "ETH", "SOL"][i % 3], "R": "0.5" if i % 2 else "-0.2", "entrada": "100", "stop": "99"} for i in range(100)]
        res = sn.resumo_teste3(linhas)
        self.assertTrue(res["avaliavel"] and res["h1"])
        self.assertAlmostEqual(res["media"], 0.15)
        self.assertAlmostEqual(res["pf"], 25.0 / 10.0)
        self.assertAlmostEqual(res["nula"], -13.0 / 100.0)
        self.assertTrue(res["h4"] and res["h6"])
        self.assertLessEqual(res["dd_ativo"], res["dd"])

    def test_h5_e_por_activo_como_pre_registado(self):
        # 3 activos, cada um com uma sequencia de 11 perdas de -1 R (DD 11 R por activo, 33 R no total)
        linhas = [{"id": "%s-%d" % (a, i), "ativo": a, "R": "-1.0", "entrada": "100", "stop": "99"}
                  for a in ("BTC", "ETH", "SOL") for i in range(11)]
        res = sn.resumo_teste3(linhas, minimo=10)
        self.assertAlmostEqual(res["dd"], 33.0)
        self.assertAlmostEqual(res["dd_ativo"], 11.0)
        self.assertFalse(res["h5"])
        # com 9 perdas por activo (DD 9 R por activo, 27 R no total) H5 passa por activo e falharia no total
        linhas = [ln for ln in linhas if int(ln["id"].split("-")[1]) < 9]
        res = sn.resumo_teste3(linhas, minimo=10)
        self.assertAlmostEqual(res["dd"], 27.0)
        self.assertAlmostEqual(res["dd_ativo"], 9.0)
        self.assertTrue(res["h5"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
