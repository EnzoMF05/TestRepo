"""Testes das pecas do medidor que nao precisam de rede. Correr:  python3 testes/teste_medidor.py"""
import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import medidor as md  # noqa: E402
import nucleo as nu  # noqa: E402


class Base(unittest.TestCase):
    EXTRA = ""

    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="medidor_teste_")
        ini = os.path.join(self.pasta, "config.ini")
        with open(ini, "w", encoding="utf-8") as f:
            f.write("[geral]\nativos = BTC, ETH\n[sombra]\njanela_sinal_s = 900\n" + self.EXTRA)
        self.cfg = md.Config(ini)

    def tearDown(self):
        shutil.rmtree(self.pasta, ignore_errors=True)

    def ler(self, caminho):
        with open(caminho, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))


class Horas(unittest.TestCase):
    T = 1791380525000  # 2026-10-07T13:42:05Z

    def test_variantes_aceites(self):
        h = md.interpretar_hora
        self.assertEqual(h("2026-10-07T13:42:05Z", 0), self.T)
        self.assertEqual(h("2026-10-07T13:42:05.2Z", 0), self.T + 200)        # 1 casa decimal
        self.assertEqual(h("2026-10-07T13:42:05.123456789Z", 0), self.T + 123)  # 9 casas
        self.assertEqual(h("2026-10-07 13:42:05,250+0000", 0), self.T + 250)   # virgula e fuso sem dois pontos
        self.assertEqual(h("2026-10-07T14:42:05+01:00", 0), self.T)
        self.assertEqual(h("2026-10-07T08:42:05-0500", 0), self.T)
        self.assertEqual(h("2026-10-07T13:42Z", 0), self.T - 5000)             # sem segundos
        self.assertEqual(h(str(self.T), 0), self.T)
        self.assertEqual(h(str(self.T // 1000), 0), self.T)
        self.assertEqual(h("  ", 77), 77)

    def test_texto_invalido_da_valueerror(self):
        for mau in ("ontem", "1e400", "-5", "2026-13-40T99:00:00Z", "13:42:05", "nan"):
            with self.assertRaises(ValueError, msg=mau):
                md.interpretar_hora(mau, 0)


class Registos(Base):
    def test_ficheiro_apagado_ou_substituido_continua_a_receber_linhas(self):
        p = os.path.join(self.pasta, "r.csv")
        r = md.Registo(p, ["a", "b"])
        r.escrever({"a": 1, "b": 2})
        os.remove(p)                                   # alguem apagou o ficheiro
        r.escrever({"a": 3, "b": 4})
        self.assertEqual(self.ler(p), [{"a": "3", "b": "4"}])
        tmp = p + ".tmp"                               # um editor grava por substituicao
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("a,b\n9,9\n")
        os.replace(tmp, p)
        r.escrever({"a": 5, "b": 6})
        self.assertEqual(self.ler(p), [{"a": "9", "b": "9"}, {"a": "5", "b": "6"}])

    def test_colunas_novas_migram_sem_perder_linhas(self):
        p = os.path.join(self.pasta, "r.csv")
        r = md.Registo(p, ["id", "r_5", "r_60"])
        r.escrever({"id": "x", "r_5": "1.0", "r_60": "2.0"})
        r2 = md.Registo(p, ["id", "r_5", "r_300"])     # o horizonte de 60 s passou a 300 s
        r2.escrever({"id": "y", "r_5": "3.0", "r_300": "4.0"})
        linhas = self.ler(p)
        self.assertEqual([ln["id"] for ln in linhas], ["x", "y"])
        self.assertEqual(linhas[0]["r_60"], "2.0")     # o dado antigo continua la
        self.assertEqual(linhas[1]["r_300"], "4.0")
        self.assertTrue(any(".anterior-" in n for n in os.listdir(self.pasta)))


class DadosEmFalta(Base):
    def setUp(self):
        super().setUp()
        self.at = md.Ativo("BTC", self.cfg, 5)

    def test_paragem_em_curso_nao_devolve_preco_velho(self):
        at = self.at
        at.on_bbo(1000, 1000, 100.0, 1.0, 100.2, 1.0)
        self.assertAlmostEqual(at.mid_em_local(2500), 100.1)   # dentro da tolerancia de 3 s
        self.assertIsNone(at.mid_em_local(4500))                # sem mensagens ha mais de 3 s
        self.assertFalse(at.coberto(2500))
        at.on_livro(3000, 3000, [(100.0, 1.0)], [(100.2, 1.0)])  # chega o livro: ha dados ate aos 3 s
        self.assertTrue(at.coberto(2500))
        self.assertAlmostEqual(at.mid_em_local(2500), 100.1)

    def test_primeiro_preco_depois_de_um_corte_e_valido_na_hora_da_bolsa(self):
        at = self.at
        at.on_bbo(1000, 1300, 100.0, 1.0, 100.2, 1.0)           # dados chegam 300 ms depois da bolsa
        at.desligado(5000)
        at.on_bbo(9000, 9300, 101.0, 1.0, 101.2, 1.0)
        self.assertAlmostEqual(at.mid_exch(9100, estrito=True), 101.1)
        self.assertIsNone(at.mid_exch(8000, estrito=True))       # a meio do corte
        self.assertIsNone(at.mid_exch(9000, estrito=True))       # antes do primeiro preco novo

    def test_estado_de_uma_amostra_velha_e_sem_dados(self):
        at = self.at
        at.amostras.append((1000, 100.1, 100.0, 1.0, 100.2, 1.0, nu.VERDE))
        self.assertEqual(at.estado_em_local(2000), nu.VERDE)
        self.assertEqual(at.estado_em_local(60_000), nu.SEM_DADOS)
        self.assertEqual(at.estado_em_local(500), "")


class LigacaoDoFillAoSinal(Base):
    def setUp(self):
        super().setUp()
        self.med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})
        self.s1 = self.sinal("s1", 1_000, 100.0)
        self.s2 = self.sinal("s2", 5_000, 101.0)

    def sinal(self, ident, ts, m0, lado=1, ativo="BTC"):
        s = md.SinalAberto(ident, ts, ativo, lado)
        s.m0 = m0
        s.escrito = True
        self.med.sinais.append(s)
        return s

    def fill(self, exch_ms, m1=102.0, lado=1, abre=True, local_ms=None, ativo="BTC"):
        fa = md.FillAberto(ativo, lado, exch_ms, local_ms or exch_ms + 300, m1, [], abre)
        fa.linha = {"tid": exch_ms}
        self.med._escrever_fill(fa)
        return fa.linha

    def test_liga_ao_sinal_mais_recente_antes_do_fill(self):
        ln = self.fill(5_500)
        self.assertEqual(ln["sinal_id"], "s2")
        self.assertAlmostEqual(float(ln["d_bps"]), 1e4 * (102.0 - 101.0) / 101.0, places=2)
        self.assertEqual(ln["atraso_s"], "0.50")

    def test_segunda_ordem_fica_no_mesmo_sinal(self):
        self.assertEqual(self.fill(5_500)["sinal_id"], "s2")
        self.assertEqual(self.fill(6_500)["sinal_id"], "s2")

    def test_fill_reenviado_tarde_nao_se_liga_a_sinal_posterior(self):
        ln = self.fill(3_000, local_ms=99_000)        # aconteceu aos 3 s, chegou aos 99 s
        self.assertEqual(ln["sinal_id"], "s1")

    def test_fill_mais_rapido_do_que_o_relogio_local(self):
        self.assertEqual(self.fill(4_600)["sinal_id"], "s2")   # ate 1 s de diferenca de relogios

    def test_sem_ligacao_quando_nao_abre_ou_e_outro_lado_ou_fora_da_janela(self):
        self.assertNotIn("sinal_id", self.fill(5_500, abre=False))
        self.assertNotIn("sinal_id", self.fill(5_500, lado=-1))
        self.assertNotIn("sinal_id", self.fill(5_500, ativo="ETH"))
        self.assertNotIn("sinal_id", self.fill(5_000 + 901_000))
        self.assertNotIn("sinal_id", self.fill(5_500, m1=None))


class CicloRobusto(Base):
    def setUp(self):
        super().setUp()
        self.med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})

    def test_um_passo_com_erro_nao_rebenta(self):
        def rebenta():
            raise OSError("disco cheio")
        for _ in range(5):
            self.assertIsNone(self.med._passo("gravar", rebenta))
        self.assertEqual(self.med.erros["gravar"], 5)
        self.assertEqual(self.med._passo("soma", lambda a, b: a + b, 1, 2), 3)

    def test_linhas_de_sinais_estragadas_nao_levam_as_boas(self):
        with open(self.cfg.sinais_csv, "ab") as f:
            f.write(b",BTC,compra,,,,boa1\n")
            f.write(b"\x00\x00lixo\x00,,,\n")
            f.write(b'"aspas por fechar,BTC,compra\n')
            f.write(b"1e400,BTC,compra,,,,hora absurda\n")
            f.write(b",ETH,venda,,,,boa2\n")
            f.write(b",BTC,compra,,,,a meio")              # sem fim de linha: ainda a ser escrita
        linhas = self.med._ler_sinais_novos()
        notas = [ln["nota"] for ln in linhas]
        self.assertIn("boa1", notas)
        self.assertIn("boa2", notas)
        self.assertNotIn("a meio", notas)
        for ln in linhas:                                   # nenhuma destas pode levantar erro
            self.med._novo_sinal(ln, md.agora_ms())
        with open(self.cfg.sinais_csv, "ab") as f:
            f.write(b"\n")
        self.assertEqual([ln["nota"] for ln in self.med._ler_sinais_novos()], ["a meio"])

    def test_parar_escreve_o_que_estava_a_meio(self):
        at = self.med.ativos["BTC"]
        t = md.agora_ms()
        at.on_bbo(t - 50, t - 50, 100.0, 2.0, 100.2, 2.0)
        self.med._novo_sinal({"hora": "", "ativo": "BTC", "lado": "compra", "nota": "a meio"}, t)
        self.assertEqual(self.ler(self.cfg.reg_sinais), [])
        self.med.fechar()
        linhas = self.ler(self.cfg.reg_sinais)
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["sombra_preenchida"], "")   # nao se sabe: fica em branco
        self.assertEqual(linhas[0]["r_300"], "")
        self.assertNotEqual(linhas[0]["mid0"], "")


class Relatorio(Base):
    def test_fills_parciais_contam_como_uma_ordem_ponderada_pelo_valor(self):
        fills = [{"ativo": "BTC", "oid": "1", "tid": str(i), "valor_usd": "100", "e_bps": "10", "f_bps": "4.5",
                  "a_60": "0", "taker": "1", "estado": nu.VERDE, "d_bps": "", "atraso_s": ""} for i in range(9)]
        fills.append({"ativo": "BTC", "oid": "1", "tid": "9", "valor_usd": "99100", "e_bps": "1", "f_bps": "4.5",
                      "a_60": "2", "taker": "1", "estado": nu.VERDE, "d_bps": "3", "atraso_s": "1.5"})
        fills.append({"ativo": "ETH", "oid": "2", "tid": "10", "valor_usd": "500", "e_bps": "-0.5", "f_bps": "1.5",
                      "a_60": "", "taker": "0", "estado": nu.AMARELO, "d_bps": "", "atraso_s": ""})
        ordens = md._ordens(fills, [60])
        self.assertEqual(len(ordens), 2)
        o = ordens[0]
        self.assertEqual(o["fills"], 10)
        self.assertAlmostEqual(o["e"], (9 * 100 * 10 + 99100 * 1) / 100000.0)   # 1,081 e nao 9,1
        self.assertAlmostEqual(o["d"], 3.0)
        self.assertTrue(o["taker"])
        self.assertFalse(ordens[1]["taker"])
        self.assertTrue(ordens[1]["a"][60] != ordens[1]["a"][60])                # sem dado: nan, nao zero

    def test_relatorio_corre_com_registos_vazios_e_com_dados(self):
        md.cmd_relatorio(self.cfg)
        med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})
        for i, (r, ench, via) in enumerate([(8.0, 1, 1), (2.0, 0, 1), (5.0, 1, 0), (1.0, 1, "")]):
            med.reg_sinais.escrever({"id": "s%d" % i, "ativo": "BTC", "lado": "compra", "mid0": "100",
                                     "imp_bps": "1.5" if via == 1 else "", "imp_viavel": via,
                                     "sombra_preenchida": ench, "ganho_passiva_bps": "0.5", "r_300": r})
        md.cmd_relatorio(self.cfg)
        with open(os.path.join(self.cfg.pasta, "relatorio.txt"), encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("4 de 100 sinais medidos (4 completos, 2 entram na regra)", txt)
        self.assertIn("1 em que a ordem nao cabia", txt)
        # regra so nos 2 sinais com impacto viavel: R medio 5, imp 1.5, taker 4.5 -> V_ag = -1.0
        # passiva: pi = 0.5, R nos preenchidos 8, ganho 0.5, maker 1.5 -> V_pas = 3.5
        self.assertIn("regra (n=2): impacto 1.50 bps | V agressiva -1.00 | V passiva 3.50", txt)


class RelatorioPorTipoDeOrdem(Base):
    """Aberturas e fechos nao se misturam; D so conta na primeira ordem de cada sinal."""

    def fill(self, oid, direc, e, taker=1, valor=1000, f=4.5, d="", n="", sinal="", estado=nu.VERDE):
        return {"ativo": "BTC", "oid": str(oid), "tid": str(oid), "valor_usd": str(valor), "e_bps": str(e),
                "f_bps": str(f), "a_5": "0.1", "a_30": "0.2", "a_60": "0.3", "taker": str(taker), "dir": direc,
                "estado": estado, "sinal_id": sinal, "ordem_no_sinal": str(n), "d_bps": str(d), "atraso_s": "2"}

    def test_stops_nao_contaminam_o_custo_das_entradas(self):
        med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})
        linhas = [self.fill(1, "Open Long", 1.0, d=3.0, n=1, sinal="s1"),
                  self.fill(2, "Open Long", 1.0, d=40.0, n=2, sinal="s1"),      # segunda abertura no mesmo sinal
                  self.fill(3, "Open Short", 1.0, d=5.0, n=1, sinal="s2", valor=9000),
                  self.fill(4, "Close Long", 20.0),                              # stop a mercado, caro
                  self.fill(5, "Close Short", -0.5, taker=0, f=1.5),             # alvo passivo
                  self.fill(6, "Long > Short", 7.0)]                             # inversao de posicao
        for ln in linhas:
            med.reg_fills.escrever(ln)
        md.cmd_relatorio(self.cfg)
        with open(os.path.join(self.cfg.pasta, "relatorio.txt"), encoding="utf-8") as f:
            txt = f.read()
        ab = txt[txt.index("ABERTURAS"):txt.index("FECHOS")]
        fe = txt[txt.index("FECHOS"):txt.index("OUTROS")]
        self.assertIn("3 de 30 ordens de abertura (6 ordens, 6 fills)", txt)
        self.assertIn("todas            n=3    E 1.00", ab)                      # o stop de 20 bps nao entra aqui
        self.assertIn("D 4.00 (n=2)", ab)                                        # (3 + 5) / 2: a de 40 fica fora
        self.assertIn("1 aberturas adicionais no mesmo sinal", ab)
        self.assertIn("A 5s 0.10 / 30s 0.20 / 60s 0.30", ab)
        self.assertIn("taker            n=1    E 20.00", fe)
        self.assertIn("maker            n=1    E -0.50", fe)
        self.assertIn("todas            n=1    E 7.00", txt[txt.index("OUTROS"):])

    def test_taxas_medidas_substituem_as_do_config_quando_ha_ordens_que_cheguem(self):
        med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})
        for i in range(5):
            med.reg_fills.escrever(self.fill(i, "Open Long", 1.0, f=3.6))        # taker com desconto de staking
        med.reg_sinais.escrever({"id": "s", "ativo": "BTC", "lado": "compra", "mid0": "100", "imp_bps": "1.0",
                                 "imp_viavel": 1, "sombra_preenchida": 0, "ganho_passiva_bps": "0.5", "r_300": 8.0,
                                 "estado": nu.VERDE, "ofi_lado": "2.0", "fresco": 1,
                                 "liq_favor_usd": "50000", "liq_contra_usd": "0"})
        md.cmd_relatorio(self.cfg)
        with open(os.path.join(self.cfg.pasta, "relatorio.txt"), encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("taker 3.60 bps (medida em 5 ordens), maker 1.50 bps (config.ini)", txt)
        self.assertIn("V agressiva 3.40", txt)                                   # 8 - (1.0 + 3.6)
        self.assertIn("  verde          n=1", txt)                               # estado no instante do sinal
        self.assertIn("  fluxo a favor  n=1", txt)
        self.assertIn("  liq. a favor   n=1", txt)                               # liquidacoes a favor da ordem

    def test_um_sinal_nao_chega_para_mudar_a_rota(self):
        med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})
        med.reg_sinais.escrever({"id": "s", "ativo": "BTC", "lado": "compra", "mid0": "100", "imp_bps": "1.0",
                                 "imp_viavel": 1, "sombra_preenchida": 0, "ganho_passiva_bps": "0.5", "r_300": 8.0})
        m = md.medida_do_activo(self.cfg, "btc")
        self.assertEqual(m["n"], 1)
        self.assertAlmostEqual(m["v_ag"], 2.5)          # 8 - (1.0 + 4.5 do config)
        self.assertEqual(m["v_pas"], 0.0)
        self.assertEqual(nu.rota_com_medida("agressiva", m["n"], m["escolha"]), "agressiva")
        self.assertEqual(md.medida_do_activo(self.cfg, "ETH")["n"], 0)


class PrecoDoSinalETaxa(Base):
    def setUp(self):
        super().setUp()
        self.med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})
        self.med.inicio_ms = 0
        self.t = md.agora_ms()
        self.at = self.med.ativos["BTC"]
        self.at.on_bbo(self.t - 100, self.t - 100, 100.0, 2.0, 100.2, 2.0)       # mid 100,1

    def um_fill(self, tid, oid, px, token="USDC", direc="Open Long"):
        self.med._um_fill({"coin": "BTC", "px": str(px), "sz": "1", "side": "B", "time": self.t + 50, "dir": direc,
                           "startPosition": "0", "oid": oid, "crossed": True, "fee": "0.045", "tid": tid,
                           "feeToken": token}, self.t + 60)
        fa = self.med.fills.pop()
        self.med._escrever_fill(fa)
        return fa.linha

    def test_preco_indicado_no_sinal_entra_nas_contas(self):
        self.med._novo_sinal({"hora": "", "ativo": "BTC", "lado": "compra", "preco": "100.0", "nota": ""}, self.t)
        s = self.med.sinais[0]
        self.assertAlmostEqual(float(s.linha["desvio_preco_bps"]), 10.0, places=2)   # mid 100,1 contra 100,0
        ln1 = self.um_fill(1, 11, 100.2)
        ln2 = self.um_fill(2, 12, 100.3)
        self.assertAlmostEqual(float(ln1["c_preco_bps"]), 20.0, places=2)            # pagou 100,2 contra 100,0
        self.assertEqual((ln1["ordem_no_sinal"], ln2["ordem_no_sinal"]), (1, 2))
        self.assertEqual(ln1["sinal_id"], ln2["sinal_id"])

    def test_sinal_sem_preco_nao_inventa_desvio(self):
        self.med._novo_sinal({"hora": "", "ativo": "BTC", "lado": "compra", "preco": "", "nota": ""}, self.t)
        self.assertNotIn("desvio_preco_bps", self.med.sinais[0].linha)
        self.assertNotIn("c_preco_bps", self.um_fill(1, 11, 100.2))

    def test_taxa_noutra_moeda_fica_em_branco(self):
        self.assertAlmostEqual(float(self.um_fill(1, 11, 100.0)["f_bps"]), 4.5, places=3)
        ln = self.um_fill(2, 12, 100.0, token="HYPE")
        self.assertEqual(ln["f_bps"], "")
        self.assertEqual(ln["moeda_taxa"], "HYPE")


class EstadoDoLivro(Base):
    EXTRA = ("[coinglass]\nchave = X\n[detector]\naquecimento_min = 0.1\npiso_liquidacoes_usd = 0\n"
             "piso_ofi = 0\n[avancado]\namostra_s = 1\npasso_historico = 1\n")

    def correr(self, at, ticks, liq=lambda i: 0.0, ofi=lambda i: 0.0, t0=1_000_000):
        ultimo = {}
        for i in range(ticks):
            t = t0 + i * 1000
            at.on_livro(t, t, [(100.0, 5.0), (99.9, 5.0)], [(100.2, 5.0), (100.3, 5.0)])
            if liq(i):
                at.liq.juntar(t, liq(i))
            if ofi(i):
                at.ofi.juntar(t, ofi(i))
            ultimo = at.amostrar(t)
        return ultimo, t0 + ticks * 1000

    def test_primeiras_liquidacoes_nao_pintam_vermelho(self):
        at = md.Ativo("BTC", self.cfg, 5)
        u, t = self.correr(at, 10)                                    # livro aquecido, sem liquidacoes
        self.assertEqual(u["estado"], nu.VERDE)
        u, t = self.correr(at, 3, liq=lambda i: 5e6, t0=t)            # chegam as primeiras
        self.assertNotIn("liquidacoes", " ".join(u["motivos"]))
        self.assertEqual(u["estado"], nu.VERDE)

    def test_com_historico_uma_liquidacao_grande_alarma(self):
        at = md.Ativo("BTC", self.cfg, 5)
        u, t = self.correr(at, 80, liq=lambda i: 1000.0 + i)           # historico de valores pequenos
        u, t = self.correr(at, 1, liq=lambda i: 5e7, t0=t)
        self.assertIn("liquidacoes", " ".join(u["motivos"]))
        self.assertEqual(u["estado"], nu.VERMELHO)

    def test_fluxo_fica_fora_do_estado_por_defeito(self):
        at = md.Ativo("BTC", self.cfg, 5)
        u, t = self.correr(at, 40, ofi=lambda i: 0.1)
        u, t = self.correr(at, 1, ofi=lambda i: 5000.0, t0=t)
        self.assertGreater(abs(u["ofi_norm"]), 100)                    # o fluxo mede-se...
        self.assertNotIn("fluxo", " ".join(u["motivos"]))              # ...mas nao entra no estado
        self.assertEqual(u["estado"], nu.VERDE)

    def test_activo_da_liquidacao_pela_base_ou_pelo_simbolo(self):
        b = md.base_da_liquidacao
        self.assertEqual(b({"base_asset": "BTC", "symbol": "BTCUSDT"}), "BTC")
        self.assertEqual(b({"baseAsset": "eth"}), "ETH")
        self.assertEqual(b({"symbol": "BTCUSDT"}), "BTC")
        self.assertEqual(b({"symbol": "ETH-USD-PERP"}), "ETH")
        self.assertEqual(b({"symbol": "SOL_USDT"}), "SOL")
        self.assertEqual(b({"symbol": "HYPE/USDC:USDC"}), "HYPE")
        self.assertEqual(b({}), "")


class CoinGlassEHorizonte(Base):
    def test_volume_e_chave_juntam_websocket_e_rest(self):
        self.assertEqual(md.volume_liquidacao({"volume_usd": 12}), 12)
        self.assertEqual(md.volume_liquidacao({"usd_value": 20.5}), 20.5)
        ws = {"exchange": "Binance", "base_asset": "BTC", "price": 100, "side": 2,
              "time": 5000, "volume_usd": 1000}
        rest = {"exchange_name": "binance", "base_asset": "BTC", "price": 100.0, "side": 2,
                "time": 5499, "usd_value": 1004}
        self.assertEqual(md.chave_liquidacao(ws), md.chave_liquidacao(rest))
        camel = {"exName": "Binance", "baseAsset": "BTC", "price": 100, "side": 2,
                 "time": 5000, "volUsd": 1000}                         # grafia do canal liquidationOrders
        self.assertEqual(md.volume_liquidacao(camel), 1000)
        self.assertEqual(md.chave_liquidacao(camel), md.chave_liquidacao(ws))

    def test_a_mesma_ordem_nao_soma_duas_vezes_e_o_lado_separa(self):
        med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})
        it = {"exchange": "Binance", "base_asset": "BTC", "price": 100, "side": 1,
              "time": 1_700_000_000_000, "volume_usd": 25000}
        rest = {"exchange_name": "BINANCE", "base_asset": "BTC", "price": 100.0, "side": 1,
                "time": 1_700_000_000_400, "usd_value": 25002}
        med._ingerir_liq(it, 10)
        med._ingerir_liq(rest, 10)
        self.assertEqual(med.cg_msgs, 1)
        at = med.ativos["BTC"]
        self.assertAlmostEqual(at.liq.soma(10), 25000)
        self.assertAlmostEqual(at.liq_long.soma(10), 25000)
        self.assertEqual(at.liq_short.soma(10), 0.0)

    def test_frase_curta_e_de_entrada_e_a_longa_e_o_drift(self):
        self.assertIn("ENTRADA", md.frase_do_horizonte(300))
        self.assertIn("drift", md.frase_do_horizonte(3600))
        self.assertNotIn("ENTRADA", md.frase_do_horizonte(3600))


class BilheteDoEstado(Base):
    """O bilhete le o estado.json: recusa um estado velho e da o tamanho no passo da bolsa."""
    EXTRA = ("[orcamento]\nalvo_bps = 200\n[detector]\naquecimento_min = 0.1\n"
             "[avancado]\namostra_s = 1\npasso_historico = 1\n")

    def setUp(self):
        super().setUp()
        self.med = md.Medidor(self.cfg, {"BTC": 5, "ETH": 4})
        self.med.ligado = True
        at = self.med.ativos["BTC"]
        t = 1_000_000
        for i in range(12):
            t = 1_000_000 + i * 1000
            at.on_livro(t, t, [(100.0, 5.0), (99.9, 5.0)], [(100.2, 5.0), (100.3, 5.0)])
            at.amostrar(t)
        self.t = t
        self.med._escrever_estado(t)
        with open(self.cfg.estado_json, encoding="utf-8") as f:
            self.doc = json.load(f)

    def bilhete(self, tamanho="100", agora=None, lado="compra", doc=None):
        return md.montar_bilhete(self.cfg, doc or self.doc, "btc", lado, tamanho,
                                 agora=self.t + 500 if agora is None else agora)

    def copia(self, **campos):
        doc = json.loads(json.dumps(self.doc))
        doc["ativos"]["BTC"].update(campos)
        return doc

    def test_estado_fresco_da_bilhete_com_tamanho_em_unidades(self):
        self.assertEqual(self.doc["ativos"]["BTC"]["estado"], nu.VERDE)
        pr = self.bilhete()
        b = pr.bilhete
        # teto = 100,1 x (1 + 30 bps) = 100,4003, arredondado para dentro a 100,4; pior preco 100,2 cabe
        self.assertEqual((b.rota, b.tipo, b.preco), ("agressiva", "IOC", 100.4))
        self.assertEqual(pr.q_unidades, 0.999)                      # 100 / 100,1 = 0,99900..., 5 casas, para baixo
        self.assertAlmostEqual(pr.usd_arredondado, 0.999 * 100.4)
        self.assertEqual(pr.avisos, [])
        self.assertEqual(pr.idade_ms, 500)

    def test_estado_velho_ou_sem_ligacao_nao_da_bilhete(self):
        pr = self.bilhete(agora=self.t + self.cfg.sem_dados_ms + 1)
        self.assertEqual((pr.bilhete.rota, pr.bilhete.tipo), ("sem_dados", ""))
        self.assertIn("nao esta a correr", pr.bilhete.nota)
        self.assertTrue(pr.q_unidades != pr.q_unidades)
        pr = self.bilhete(doc=dict(self.doc, ligado=False))
        self.assertEqual(pr.bilhete.rota, "sem_dados")
        self.assertIn("sem ligacao", pr.bilhete.nota)
        self.assertEqual(self.bilhete(doc=dict(self.doc, hora_utc="ontem")).bilhete.rota, "sem_dados")
        self.assertEqual(md.idade_do_estado_ms({"hora_utc": ""}, 5), sys.maxsize)
        self.assertEqual(md.idade_do_estado_ms({"hora_utc": md.iso_utc(4000)}, 5000), 1000)

    def test_tamanho_nao_enviavel_avisa_sem_mudar_a_rota(self):
        pr = self.bilhete("5")                          # 0,04995 BTC = 5,01 usd, abaixo do minimo de 10
        self.assertEqual(pr.bilhete.rota, "agressiva")
        self.assertEqual(pr.q_unidades, 0.04995)
        self.assertEqual(len(pr.avisos), 1)
        self.assertIn("minimo", pr.avisos[0])
        pr = self.bilhete("0.0005")                     # 0,000005 BTC arredonda a zero com 5 casas
        self.assertEqual(pr.q_unidades, 0.0)
        self.assertIn("zero", pr.avisos[0])

    def test_fotografia_do_livro_velha_avisa_mas_nao_tira_a_rota(self):
        pr = self.bilhete(doc=self.copia(idade_livro_ms=60_000))
        self.assertEqual(pr.bilhete.rota, "agressiva")
        self.assertTrue(any("fotografia" in a for a in pr.avisos))

    def test_aquecimento_sem_ordem_e_vermelho_passiva(self):
        pr = self.bilhete(doc=self.copia(estado=nu.AQUECIMENTO))
        self.assertEqual((pr.bilhete.rota, pr.bilhete.tipo), ("aguardar", ""))
        self.assertTrue(pr.q_unidades != pr.q_unidades)             # sem ordem nao ha tamanho
        pr = self.bilhete(doc=self.copia(estado=nu.VERMELHO))
        self.assertEqual((pr.bilhete.rota, pr.bilhete.tipo, pr.bilhete.preco), ("passiva", "ALO", 100.0))
        self.assertEqual(pr.q_unidades, 0.999)
        pr = self.bilhete(doc=self.copia(estado=nu.VERMELHO), lado="venda")
        self.assertEqual((pr.bilhete.rota, pr.bilhete.tipo, pr.bilhete.preco), ("passiva", "ALO", 100.2))

    def test_aos_100_sinais_a_medida_tira_a_travessia_e_nunca_a_da(self):
        linha = {"ativo": "BTC", "lado": "compra", "mid0": "100", "imp_bps": "1.0", "imp_viavel": 1,
                 "sombra_preenchida": 1, "ganho_passiva_bps": "0.5", "r_300": 8.0}
        for i in range(99):
            self.med.reg_sinais.escrever(dict(linha, id="s%d" % i))
        pr = self.bilhete()
        self.assertEqual((pr.bilhete.rota, pr.medida["n"], pr.medida["escolha"]), ("agressiva", 99, "passiva"))
        self.med.reg_sinais.escrever(dict(linha, id="s99"))
        pr = self.bilhete()
        # V_ag = 8 - (1 + 4,5) = 2,5 ; V_pas = 1 x (8 + 0,5 - 1,5) = 7 -> a passiva vale mais
        self.assertEqual((pr.bilhete.rota, pr.bilhete.tipo, pr.bilhete.preco), ("passiva", "ALO", 100.0))
        self.assertIn("100 sinais", pr.bilhete.nota)
        self.assertAlmostEqual(pr.medida["v_ag"], 2.5)
        self.assertAlmostEqual(pr.medida["v_pas"], 7.0)
        self.assertAlmostEqual(pr.medida["dif"], 4.5)
        self.assertEqual((pr.medida["erro"], pr.medida["clara"], pr.medida["travada"]), (0.0, True, False))
        # o contrario nao acontece: mesmo com a medida a preferir a agressiva, o vermelho fica passivo
        for i in range(100):
            self.med.reg_sinais.escrever(dict(linha, id="t%d" % i, sombra_preenchida=0, r_300=20.0))
        pr = self.bilhete(doc=self.copia(estado=nu.VERMELHO))
        self.assertEqual(pr.medida["escolha"], "agressiva")         # R medio 14, V_ag 8,5 > V_pas 3,5
        self.assertEqual((pr.bilhete.rota, pr.bilhete.tipo), ("passiva", "ALO"))


class BilheteComMargemExigida(BilheteDoEstado):
    """Com regra_exige_margem = sim, uma passiva melhor so por pouco nao tira a travessia."""
    EXTRA = "regra_exige_margem = sim\n" + BilheteDoEstado.EXTRA   # a Base ja abre a seccao [sombra]

    def test_dentro_do_ruido_fica_a_rota_do_livro(self):
        # 55 sinais com R=8 preenchidos e 45 com R=10 por preencher: V_ag 3,40, V_pas 3,85, diferenca 0,45
        # com erro padrao 0,45 -> nao passa 2 erros padrao (0,90)
        base = {"ativo": "BTC", "lado": "compra", "mid0": "100", "imp_bps": "1.0", "imp_viavel": 1,
                "ganho_passiva_bps": "0.5"}
        for i in range(100):
            cheia = i < 55
            self.med.reg_sinais.escrever(dict(base, id="s%d" % i, sombra_preenchida=int(cheia),
                                              r_300=8.0 if cheia else 10.0))
        pr = self.bilhete()
        m = pr.medida
        self.assertAlmostEqual(m["v_ag"], 3.4)
        self.assertAlmostEqual(m["v_pas"], 3.85)
        self.assertAlmostEqual(m["dif"], 0.45)
        self.assertAlmostEqual(m["erro"], 0.45)                 # desvio amostral 4,5 / sqrt(100)
        self.assertEqual((m["clara"], m["travada"], m["escolha"]), (False, True, "agressiva"))
        self.assertEqual((pr.bilhete.rota, pr.bilhete.tipo), ("agressiva", "IOC"))
        # com uma diferenca clara a medida volta a tirar a travessia
        for i in range(100):
            self.med.reg_sinais.escrever(dict(base, id="u%d" % i, sombra_preenchida=1, r_300=8.0))
        pr = self.bilhete()
        self.assertTrue(pr.medida["clara"])
        self.assertFalse(pr.medida["travada"])
        self.assertEqual((pr.bilhete.rota, pr.bilhete.tipo), ("passiva", "ALO"))


class HistoricoEmDisco(Base):
    """Ao arrancar, o medidor rele as metricas de toda a janela, nao so as de ontem e de hoje."""

    def cfg_com_janela(self, horas):
        ini = os.path.join(self.pasta, "config_%d.ini" % horas)
        with open(ini, "w", encoding="utf-8") as f:
            f.write("[geral]\nativos = BTC\n[detector]\njanela_horas = %d\n" % horas)
        return md.Config(ini)

    def test_janela_de_tres_dias_rele_o_ficheiro_de_anteontem(self):
        cfg = self.cfg_com_janela(72)
        os.makedirs(cfg.pasta_metricas, exist_ok=True)
        anteontem = md.agora_ms() - 2 * 86_400_000
        dia = datetime.fromtimestamp(anteontem / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        reg = md.Registo(os.path.join(cfg.pasta_metricas, "BTC_%s.csv" % dia), md.COLUNAS_METRICAS)
        for i in range(7):
            reg.escrever({"hora_utc": md.iso_utc(anteontem + i * 5000), "local_ms": anteontem + i * 5000,
                          "spread_ticks": "1", "prof_util_usd": "50000", "vol_razao": "1.0", "intens_razao": "1.0",
                          "ofi_norm": "0.1", "mark_oraculo_bps": "1.0", "liq_60s_usd": "", "estado": nu.VERDE})
        self.assertEqual(len(md.Medidor(cfg, {"BTC": 5}).ativos["BTC"].jan["spread"]), 7)
        self.assertEqual(len(md.Medidor(self.cfg_com_janela(24), {"BTC": 5}).ativos["BTC"].jan["spread"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
