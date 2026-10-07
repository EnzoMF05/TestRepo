"""Testes das formulas (sem rede). Correr:  python3 testes/teste_nucleo.py"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleo as nu  # noqa: E402
from medidor import interpretar_hora  # noqa: E402


class TopoDoLivro(unittest.TestCase):
    def test_mid_spread_desequilibrio(self):
        self.assertAlmostEqual(nu.mid(99.0, 101.0), 100.0)
        self.assertAlmostEqual(nu.spread_bps(99.0, 101.0), 200.0)
        self.assertAlmostEqual(nu.desequilibrio(30, 10), 0.5)
        self.assertEqual(nu.desequilibrio(0, 0), 0.0)

    def test_mid_ponderado_igual_a_definicao_directa(self):
        b, a, qb, qa = 100.0, 100.2, 7.0, 3.0
        directo = (a * qb + b * qa) / (qa + qb)
        self.assertAlmostEqual(nu.mid_ponderado(b, a, qb, qa), directo, places=12)
        self.assertGreater(nu.mid_ponderado(b, a, qb, qa), nu.mid(b, a))  # mais compra puxa para cima


class ImpactoETeto(unittest.TestCase):
    asks = [(100.01, 10), (100.02, 10), (100.03, 10), (100.05, 20)]
    bids = [(99.99, 10), (99.98, 10), (99.97, 10), (99.95, 20)]

    def test_compra_percorre_niveis(self):
        r = nu.impacto(self.asks, 15, 100.0, 1)
        self.assertTrue(r.viavel)
        self.assertAlmostEqual(r.preco_medio, (100.01 * 10 + 100.02 * 5) / 15)
        self.assertAlmostEqual(r.imp_bps, 1e4 * (r.preco_medio - 100) / 100)
        self.assertEqual((r.niveis, r.preco_pior), (2, 100.02))

    def test_venda_e_simetrica(self):
        c = nu.impacto(self.asks, 15, 100.0, 1)
        v = nu.impacto(self.bids, 15, 100.0, -1)
        self.assertAlmostEqual(c.imp_bps, v.imp_bps, places=9)
        self.assertGreater(v.imp_bps, 0)

    def test_desconto_theta(self):
        cheio = nu.impacto(self.asks, 10, 100.0, 1, theta=1.0)
        meio = nu.impacto(self.asks, 10, 100.0, 1, theta=0.5)
        self.assertGreater(meio.imp_bps, cheio.imp_bps)
        self.assertEqual(meio.niveis, 2)

    def test_livro_insuficiente_e_inviavel(self):
        # caso apontado na revisao: 30 unidades em livro, pedido de 40
        r = nu.impacto([(100.01, 10), (100.02, 10), (100.03, 10)], 40, 100.0, 1)
        self.assertFalse(r.viavel)
        self.assertEqual(r.imp_bps, float("inf"))
        self.assertAlmostEqual(r.preenchido, 30)

    def test_teto_e_tamanho_maximo(self):
        teto = nu.preco_teto(100.0, 1, 3.0)
        self.assertAlmostEqual(teto, 100.03)
        qm = nu.tamanho_max(self.asks, teto + 1e-9, 1, theta=0.5)
        self.assertAlmostEqual(qm.q, 15.0)
        self.assertFalse(qm.truncado)
        # custo medio do tamanho maximo fica abaixo do orcamento
        r = nu.impacto(self.asks, qm.q, 100.0, 1, theta=0.5)
        self.assertAlmostEqual(r.imp_bps, 2.0, places=6)
        self.assertLess(r.imp_bps, 3.0)

    def test_teto_de_venda_e_truncado(self):
        teto = nu.preco_teto(100.0, -1, 10.0)
        self.assertAlmostEqual(teto, 99.9)
        qm = nu.tamanho_max(self.bids, teto, -1)
        self.assertAlmostEqual(qm.q, 50)
        self.assertTrue(qm.truncado)  # todos os niveis visiveis cabem: e um minimo

    def test_teto_antes_do_melhor_preco(self):
        qm = nu.tamanho_max(self.asks, 100.005, 1)
        self.assertEqual(qm.q, 0.0)


class Arredondamento(unittest.TestCase):
    def test_exemplos_da_documentacao(self):
        self.assertEqual(nu.arredondar_preco(1234.56, 0, True), 1234.5)
        self.assertEqual(nu.arredondar_preco(1234.56, 0, False), 1234.6)
        self.assertEqual(nu.arredondar_preco(0.0012345, 0, True), 0.001234)
        self.assertEqual(nu.arredondar_preco(0.0012345, 0, False), 0.001235)

    def test_precos_grandes_ficam_inteiros(self):
        self.assertEqual(nu.arredondar_preco(121345.7, 5, True), 121345.0)
        self.assertEqual(nu.arredondar_preco(121345.2, 5, False), 121346.0)

    def test_limite_de_casas_por_szdecimals(self):
        self.assertEqual(nu.arredondar_preco(3.14159, 4, True), 3.14)   # 6-4 = 2 casas
        self.assertEqual(nu.arredondar_preco(3.14159, 1, True), 3.1415)  # 5 algarismos
        self.assertEqual(nu.passo_preco(3.14159, 4), 0.01)
        self.assertEqual(nu.passo_preco(121345.0, 5), 1.0)
        self.assertEqual(nu.passo_preco(187.25, 2), 0.01)

    def test_valor_ja_valido_nao_muda(self):
        self.assertEqual(nu.arredondar_preco(1234.5, 0, True), 1234.5)
        self.assertEqual(nu.arredondar_preco(1234.5, 0, False), 1234.5)

    def test_tamanho(self):
        self.assertEqual(nu.arredondar_tamanho(1.23456, 3), 1.234)
        self.assertEqual(nu.arredondar_tamanho(0.0099, 2), 0.0)


class VolatilidadeELatencia(unittest.TestCase):
    def test_lambdas_do_documento(self):
        self.assertAlmostEqual(nu.lam(60), 0.9885, places=4)
        self.assertAlmostEqual(nu.lam(1800), 0.99961, places=5)

    def test_ewma_corrigida_no_arranque(self):
        e = nu.Ewma(60)
        self.assertAlmostEqual(e.juntar(4.0), 4.0)
        for _ in range(50):
            e.juntar(4.0)
        self.assertAlmostEqual(e.valor, 4.0)

    def test_ewma_segue_a_formula_depois_de_aquecida(self):
        e = nu.Ewma(10)
        for _ in range(2000):
            e.juntar(1.0)
        antes = e.valor
        depois = e.juntar(5.0)
        self.assertAlmostEqual(depois, nu.lam(10) * antes + (1 - nu.lam(10)) * 5.0, places=9)

    def test_meia_vida(self):
        e = nu.Ewma(20)
        for _ in range(5000):
            e.juntar(0.0)
        e2 = nu.Ewma(20)
        for _ in range(5000):
            e2.juntar(1.0)
        for _ in range(20):
            e2.juntar(0.0)
        self.assertAlmostEqual(e2.valor, 0.5, places=6)

    def test_desvio_da_latencia(self):
        self.assertAlmostEqual(nu.desvio_latencia_bps(1.5, 1.0), 1.1968, places=4)
        self.assertAlmostEqual(nu.desvio_latencia_bps(2.0, 4.0), 3.1915, places=4)

    def test_retorno(self):
        self.assertAlmostEqual(nu.retorno_bps(100.01, 100.0), 1e4 * math.log(1.0001))


class FluxoDeOrdens(unittest.TestCase):
    def test_sem_mudanca_da_zero(self):
        self.assertEqual(nu.ofi_evento(100, 5, 101, 7, 100, 5, 101, 7), 0.0)

    def test_compra_sobe_de_preco(self):
        self.assertEqual(nu.ofi_evento(100, 5, 101, 7, 100.5, 3, 101, 7), 3.0)

    def test_compra_aumenta_tamanho(self):
        self.assertEqual(nu.ofi_evento(100, 5, 101, 7, 100, 9, 101, 7), 4.0)

    def test_compra_recua(self):
        self.assertEqual(nu.ofi_evento(100, 5, 101, 7, 99.5, 8, 101, 7), -5.0)

    def test_venda_desce_de_preco(self):
        self.assertEqual(nu.ofi_evento(100, 5, 101, 7, 100, 5, 100.5, 4), -4.0)

    def test_venda_recua(self):
        self.assertEqual(nu.ofi_evento(100, 5, 101, 7, 100, 5, 101.5, 2), 7.0)

    def test_janela_soma_expira(self):
        j = nu.JanelaSoma(10_000)
        j.juntar(0, 1.0)
        j.juntar(5_000, 2.0)
        self.assertEqual(j.soma(9_999), 3.0)
        self.assertEqual(j.soma(10_000), 2.0)
        self.assertEqual(j.soma(20_000), 0.0)


class Percentis(unittest.TestCase):
    def test_posicao_simples(self):
        j = nu.JanelaPercentil(100)
        for x in range(1, 101):
            j.juntar(float(x))
        self.assertAlmostEqual(j.posicao(100.5), 1.0)
        self.assertAlmostEqual(j.posicao(0.5), 0.0)
        self.assertAlmostEqual(j.posicao(50.5), 0.50)
        self.assertAlmostEqual(j.mediana(), 50.5)

    def test_empates_contam_metade(self):
        j = nu.JanelaPercentil(100)
        for _ in range(90):
            j.juntar(1.0)
        for _ in range(8):
            j.juntar(2.0)
        for _ in range(2):
            j.juntar(5.0)
        self.assertAlmostEqual(j.posicao(1.0), 0.45)          # o valor habitual fica a meio
        self.assertAlmostEqual(j.posicao(2.0), 0.94)
        self.assertAlmostEqual(j.posicao(5.0), 0.99)

    def test_janela_esquece_o_mais_antigo(self):
        j = nu.JanelaPercentil(3)
        for x in (10.0, 1.0, 2.0, 3.0):
            j.juntar(x)
        self.assertEqual(len(j), 3)
        self.assertAlmostEqual(j.posicao(4.0), 1.0)

    def test_nan_e_ignorado(self):
        j = nu.JanelaPercentil(5)
        j.juntar(float("nan"))
        self.assertEqual(len(j), 0)
        self.assertTrue(math.isnan(j.posicao(1.0)))


class Detector(unittest.TestCase):
    def L(self, nome, pos, relevante=True, invertida=False):
        return nu.Leitura(nome, 1.0, pos, relevante, invertida)

    def test_verde(self):
        self.assertEqual(nu.classificar([self.L("a", 0.5), self.L("b", 0.79)])[0], nu.VERDE)

    def test_amarelo_e_vermelho(self):
        est, mot = nu.classificar([self.L("a", 0.85), self.L("b", 0.2)])
        self.assertEqual(est, nu.AMARELO)
        self.assertEqual(mot, ["a p85"])
        est, mot = nu.classificar([self.L("a", 0.85), self.L("b", 0.97)])
        self.assertEqual(est, nu.VERMELHO)
        self.assertEqual(len(mot), 2)

    def test_raro_mas_irrelevante_nao_alarma(self):
        self.assertEqual(nu.classificar([self.L("spread", 0.99, relevante=False)])[0], nu.VERDE)

    def test_profundidade_invertida(self):
        self.assertEqual(nu.classificar([self.L("prof", 0.03, invertida=True)])[0], nu.VERMELHO)
        self.assertEqual(nu.classificar([self.L("prof", 0.15, invertida=True)])[0], nu.AMARELO)
        self.assertEqual(nu.classificar([self.L("prof", 0.90, invertida=True)])[0], nu.VERDE)

    def test_sem_historico_nao_conta(self):
        self.assertEqual(nu.classificar([self.L("a", float("nan"))])[0], nu.VERDE)


class RegistoSombra(unittest.TestCase):
    def nova(self, lado=1, preco=100.0, fila=5.0, tam=1.0):
        return nu.OrdemSombra(lado, preco, fila, tam, t0_ms=1000, prazo_ms=30_000)

    def test_negocio_que_atravessa_preenche(self):
        o = self.nova()
        self.assertTrue(o.negocio(2000, 99.99, 0.01))
        self.assertEqual(o.t_fill_ms, 2000)

    def test_volume_no_preco_tem_de_passar_a_fila(self):
        o = self.nova()
        self.assertFalse(o.negocio(2000, 100.0, 5.5))   # fila 5 + tamanho 1 = 6
        self.assertTrue(o.negocio(3000, 100.0, 0.5))
        self.assertEqual(o.t_fill_ms, 3000)

    def test_negocios_acima_nao_contam(self):
        o = self.nova()
        self.assertFalse(o.negocio(2000, 100.01, 1000))

    def test_fora_do_prazo_nao_conta(self):
        o = self.nova()
        self.assertFalse(o.negocio(500, 99.0, 1))        # antes do sinal
        self.assertFalse(o.negocio(31_001, 99.0, 1))     # depois do prazo
        self.assertTrue(o.expirada(31_001))
        self.assertFalse(o.preenchida)

    def test_venda_e_o_espelho(self):
        o = self.nova(lado=-1)
        self.assertFalse(o.negocio(2000, 99.99, 1000))
        self.assertTrue(o.negocio(2500, 100.01, 0.01))


class RegrasECustos(unittest.TestCase):
    def test_exemplo_da_regra_passiva_agressiva(self):
        self.assertAlmostEqual(nu.valor_agressiva(8.0, 1.5, 4.5), 2.0)
        self.assertAlmostEqual(nu.valor_passiva(0.6, 5.0, 0.5, 1.5), 2.4)
        self.assertAlmostEqual(nu.valor_passiva(0.4, 4.0, 0.5, 1.5), 1.2)

    def test_resultado_do_sinal(self):
        self.assertAlmostEqual(nu.resultado_bps(1, 100.1, 100.0), 10.0)
        self.assertAlmostEqual(nu.resultado_bps(-1, 100.1, 100.0), -10.0)

    def test_custos_de_um_fill_de_compra(self):
        # sinal a 100,00; mid sobe para 100,05; compra executada a 100,06; taxa de taker
        self.assertAlmostEqual(nu.custo_decisao_bps(1, 100.05, 100.0), 5.0)
        self.assertAlmostEqual(nu.custo_execucao_bps(1, 100.06, 100.05), 1e4 * 0.01 / 100.05)
        self.assertAlmostEqual(nu.custo_taxa_bps(0.045027, 100.06, 1.0), 4.5, places=3)
        self.assertGreater(nu.seleccao_adversa_bps(1, 100.00, 100.05), 0)   # caiu depois da compra
        self.assertLess(nu.seleccao_adversa_bps(1, 100.10, 100.05), 0)      # subiu: a favor

    def test_custos_de_uma_venda_passiva(self):
        # venda passiva na melhor venda 100,01 com mid 100,00: execucao negativa (ganha meio spread)
        self.assertLess(nu.custo_execucao_bps(-1, 100.01, 100.0), 0)
        self.assertGreater(nu.custo_decisao_bps(-1, 99.95, 100.0), 0)       # mid caiu antes de vender
        self.assertLess(nu.custo_taxa_bps(-0.001, 100.0, 1.0), 0)           # rebate

    def test_tabela_de_equilibrio_do_documento(self):
        casos = [(9, 9, 63.3, 45.3, 39.3), (12, 12, 73.3, 49.3, 41.3), (6, 9, 57.6, 43.6, 38.6),
                 (3, 6, 48.5, 39.7, 36.6), (18, 18, 93.3, 57.3, 45.3), (6, 12, 61.1, 45.7, 39.7)]
        for cw, cl, p20, p50, p100 in casos:
            self.assertAlmostEqual(100 * nu.acerto_equilibrio(20, 10, cw, cl), p20, places=1)
            self.assertAlmostEqual(100 * nu.acerto_equilibrio(50, 25, cw, cl), p50, places=1)
            self.assertAlmostEqual(100 * nu.acerto_equilibrio(100, 50, cw, cl), p100, places=1)
        self.assertAlmostEqual(100 * nu.acerto_equilibrio(20, 10, 0, 0), 33.3, places=1)


class Leitura(unittest.TestCase):
    def test_lado(self):
        for t in ("compra", "LONG", "buy", "C", "b"):
            self.assertEqual(nu.interpretar_lado(t), 1)
        for t in ("venda", "Short", "sell", "V", "a"):
            self.assertEqual(nu.interpretar_lado(t), -1)
        with self.assertRaises(ValueError):
            nu.interpretar_lado("talvez")

    def test_hora(self):
        self.assertEqual(interpretar_hora("", 123), 123)
        self.assertEqual(interpretar_hora("1791370000", 0), 1791370000000)
        self.assertEqual(interpretar_hora("1791370000123", 0), 1791370000123)
        self.assertEqual(interpretar_hora("2026-10-07T13:42:05Z", 0), 1791380525000)
        self.assertEqual(interpretar_hora("2026-10-07 14:42:05+01:00", 0), 1791380525000)
        self.assertEqual(interpretar_hora("2026-10-07T13:42:05.250Z", 0), 1791380525250)


class CortesDeDados(unittest.TestCase):
    """O medidor nao pode inventar precos para instantes em que esteve sem dados."""

    def setUp(self):
        import tempfile
        import medidor as md
        self.pasta = tempfile.mkdtemp(prefix="medidor_teste_")
        ini = os.path.join(self.pasta, "config.ini")
        with open(ini, "w", encoding="utf-8") as f:
            f.write("[geral]\nativos = BTC\n")
        self.at = md.Ativo("BTC", md.Config(ini), 5)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.pasta, ignore_errors=True)

    def test_consultas_dentro_e_fora_do_corte(self):
        at = self.at
        at.on_bbo(1000, 1000, 100.0, 1.0, 100.2, 1.0)
        at.on_bbo(2000, 2000, 100.2, 1.0, 100.4, 1.0)
        self.assertAlmostEqual(at.mid_em_local(1500), 100.1)
        at.desligado(5000)                                  # ligacao cai aos 5 s
        self.assertIsNone(at.b)
        self.assertTrue(at.em_corte(4000))                  # desde o ultimo dado (2 s) nada e certo
        self.assertIsNone(at.mid_em_local(6000))
        self.assertAlmostEqual(at.mid_em_local(1500), 100.1)  # antes do corte continua valido
        at.on_bbo(9000, 9000, 101.0, 1.0, 101.2, 1.0)       # dados voltam aos 9 s
        self.assertIsNone(at.mid_em_local(8999))
        self.assertAlmostEqual(at.mid_em_local(9500), 101.1)
        self.assertIsNone(at.mid_exch(7000, estrito=True))
        self.assertAlmostEqual(at.mid_exch(9000, estrito=False), 101.1)
        self.assertIsNone(at.mid_exch(9000, estrito=True))  # estrito: nada antes dos 9 s senao o corte

    def test_janela_que_toca_o_corte(self):
        at = self.at
        at.on_bbo(1000, 1000, 100.0, 1.0, 100.2, 1.0)
        at.desligado(3000)
        at.on_bbo(6000, 6000, 100.0, 1.0, 100.2, 1.0)
        self.assertFalse(at.em_corte(500, 900))
        self.assertTrue(at.em_corte(900, 2000))
        self.assertTrue(at.em_corte(5000, 7000))
        self.assertFalse(at.em_corte(6000, 7000))

    def test_mensagens_paradas_sem_cair_a_ligacao(self):
        at = self.at
        at.on_bbo(1000, 1000, 100.0, 1.0, 100.2, 1.0)
        at.on_bbo(40_000, 40_000, 105.0, 1.0, 105.2, 1.0)   # 39 s de silencio
        self.assertIsNone(at.mid_em_local(20_000))           # nada de preco velho a meio do silencio
        self.assertIsNone(at.mid_em_local(1500))             # nao se sabe quando o silencio comecou
        self.assertAlmostEqual(at.mid_em_local(1000), 100.1)
        self.assertAlmostEqual(at.mid_em_local(41_000), 105.1)

    def test_livro_velho_acerta_pelo_melhor_preco(self):
        at = self.at
        at.on_livro(1000, 1000, [(100.0, 5.0), (99.9, 5.0)], [(100.2, 5.0), (100.3, 5.0)])
        at.on_bbo(1100, 1100, 99.9, 2.0, 100.1, 3.0)        # o topo desceu depois da fotografia
        self.assertEqual(at.niveis(1), [(100.1, 3.0), (100.2, 5.0), (100.3, 5.0)])
        self.assertEqual(at.niveis(-1), [(99.9, 2.0)])
        imp = nu.impacto(at.niveis(-1), 1.0, nu.mid(99.9, 100.1), -1)
        self.assertGreater(imp.imp_bps, 0)                  # nunca negativo por livro desactualizado


class Bilhete(unittest.TestCase):
    def test_vermelho_nao_atravessa(self):
        b = nu.emitir_bilhete(1, 1000, 4.5, nu.VERMELHO, 100.0, 100.2, 0.4, 50000, 100.45)
        self.assertEqual((b.rota, b.tipo, b.preco), ("passiva", "ALO", 100.0))

    def test_verde_atravessa_quando_qmax_chega(self):
        b = nu.emitir_bilhete(1, 1000, 4.5, nu.VERDE, 100.0, 100.2, 9.0, 5000, 100.45)
        self.assertEqual((b.rota, b.tipo, b.preco), ("agressiva", "IOC", 100.45))

    def test_verde_fica_passiva_se_qmax_nao_chega(self):
        b = nu.emitir_bilhete(-1, 1000, 4.5, nu.AMARELO, 100.0, 100.2, 0.2, 200, 99.5)
        self.assertEqual(b.rota, "passiva")
        self.assertIn("200", b.nota)

    def test_aquecimento_nao_escolhe_rota(self):
        b = nu.emitir_bilhete(1, 1000, 4.5, nu.AQUECIMENTO, 100.0, 100.2, 0.2, 9000, 100.45)
        self.assertEqual(b.rota, "aguardar")

    def test_rota_segue_o_pior_preco_e_nao_o_impacto_medio(self):
        # teto arredondado a 100.04. O nivel 100.05 fica de fora.
        asks = [(100.01, 100.0), (100.05, 100.0)]
        pequeno = nu.decidir_rota(1, 500, 4.5, nu.VERDE, 99.99, 100.01, asks, 2, theta=1)
        self.assertEqual(pequeno.rota, "agressiva")
        self.assertEqual(pequeno.preco, 100.04)
        self.assertLessEqual(pequeno.imp_bps, 4.5)
        grande = nu.decidir_rota(1, 20000, 4.5, nu.VERDE, 99.99, 100.01, asks, 2, theta=1)
        self.assertEqual(grande.rota, "passiva")
        self.assertEqual(grande.tipo, "ALO")
        vermelho = nu.decidir_rota(1, 500, 4.5, nu.VERMELHO, 99.99, 100.01, asks, 2, theta=1)
        self.assertEqual((vermelho.rota, vermelho.preco), ("passiva", 99.99))


class RegraMedida(unittest.TestCase):
    def test_empate_fica_na_agressiva(self):
        # R=6, Imp=1, taker=1 -> V_ag=4. pi=1, R preenchido=6, ganho=0, maker=2 -> V_pas=4.
        v_ag, v_pas, escolha = nu.regra_rotas(6, 1, 1, 6, 0, 1, 2)
        self.assertEqual((v_ag, v_pas, escolha), (4, 4, "agressiva"))

    def test_passiva_ganha_quando_o_preenchimento_compensa(self):
        _, _, escolha = nu.regra_rotas(2, 3, 0.8, 4, 1, 4.5, 1.5)
        self.assertEqual(escolha, "passiva")

    def test_sem_preenchimento_a_passiva_vale_zero(self):
        v_ag, v_pas, escolha = nu.regra_rotas(10, 1, 0, float("nan"), float("nan"), 4.5, 1.5)
        self.assertEqual(v_pas, 0.0)
        self.assertEqual(escolha, "agressiva")
        self.assertAlmostEqual(v_ag, 4.5)

    def test_a_medida_so_tira_a_travessia_e_so_depois_do_minimo(self):
        self.assertEqual(nu.rota_com_medida("agressiva", 99, "passiva"), "agressiva")
        self.assertEqual(nu.rota_com_medida("agressiva", 100, "passiva"), "passiva")
        self.assertEqual(nu.rota_com_medida("passiva", 200, "agressiva"), "passiva")
        self.assertEqual(nu.rota_com_medida("aguardar", 200, "agressiva"), "aguardar")


if __name__ == "__main__":
    unittest.main(verbosity=1)
