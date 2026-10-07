"""Simulacao de ponta a ponta (sem internet).

Levanta uma bolsa falsa que fala o formato documentado da Hyperliquid
(bbo, l2Book, trades, activeAssetCtx, userFills, ping/pong) e um servico falso
de liquidacoes no formato da CoinGlass. Corre o medidor contra eles durante
cerca de 20 segundos e confere os registos com o que a bolsa falsa enviou.

Correr:  python3 testes/simulacao.py
"""
import asyncio
import csv
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets  # noqa: E402

import medidor as md  # noqa: E402
import nucleo as nu  # noqa: E402

ENDERECO = "0x" + "ab" * 20
random.seed(7)


def ms():
    return int(time.time() * 1000)


# --------------------------------------------------------------------------
# Bolsa falsa
# --------------------------------------------------------------------------
class Moeda:
    def __init__(self, nome, preco, tick, sz_dec):
        self.nome, self.tick, self.sz_dec = nome, tick, sz_dec
        self.pb = preco            # melhor compra
        self.spread = 1            # em ticks
        self.qb, self.qa = 2.0, 2.0
        self.bbo_log = []          # (exch_ms, b, a)
        self.neg_log = []          # (exch_ms, px, sz)

    @property
    def pa(self):
        return round(self.pb + self.spread * self.tick, 8)


class Bolsa:
    def __init__(self):
        self.moedas = {"BTC": Moeda("BTC", 121000.0, 1.0, 5), "ETH": Moeda("ETH", 4300.0, 0.1, 4),
                       "SOL": Moeda("SOL", 210.0, 0.01, 2)}
        self.clientes = {}         # ligacao -> conjunto de subscricoes
        self.fills = []            # todos os fills enviados ate agora
        self.inicio = time.monotonic()
        self.rajada = False
        self.parado = False        # responde a pings mas nao envia dados de mercado
        self.tid = 0

    def t(self):
        return time.monotonic() - self.inicio

    async def atender(self, ws):
        subs = set()
        self.clientes[ws] = subs
        try:
            async for bruto in ws:
                msg = json.loads(bruto)
                if msg.get("method") == "ping":
                    await ws.send(json.dumps({"channel": "pong"}))
                elif msg.get("method") == "subscribe":
                    s = msg["subscription"]
                    subs.add((s["type"], s.get("coin") or s.get("user")))
                    await ws.send(json.dumps({"channel": "subscriptionResponse",
                                              "data": {"method": "subscribe", "subscription": s}}))
                    if s["type"] == "trades":  # lote de negocios antigos, a precos absurdos
                        await ws.send(json.dumps({"channel": "trades", "data": [{
                            "coin": s["coin"], "side": "A", "px": "1.0", "sz": "999", "time": ms() - 60_000,
                            "hash": "0x2", "tid": -i, "users": ["0xa", "0xb"]} for i in range(1, 6)]}))
                    if s["type"] == "userFills":
                        antigo = self.fazer_fill("BTC", "B", 100000.0, 0.5, "Open Long", True, 9, t_ms=ms() - 3600_000,
                                                 guardar=False)
                        await ws.send(json.dumps({"channel": "userFills", "data": {
                            "isSnapshot": True, "user": s["user"], "fills": [antigo] + list(self.fills)}}))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clientes.pop(ws, None)

    async def difundir(self, tipo, chave, canal, dados):
        txt = json.dumps({"channel": canal, "data": dados})
        for ws, subs in list(self.clientes.items()):
            if (tipo, chave) in subs:
                try:
                    await ws.send(txt)
                except Exception:
                    pass

    def fazer_fill(self, coin, side, px, sz, direc, crossed, oid, t_ms=None, guardar=True):
        self.tid += 1
        taxa = (0.00045 if crossed else 0.00015) * px * sz
        f = {"coin": coin, "px": repr(px), "sz": repr(sz), "side": side, "time": t_ms or ms(),
             "startPosition": "0.0", "dir": direc, "closedPnl": "0.0", "hash": "0x0", "oid": oid,
             "crossed": crossed, "fee": "%.8f" % taxa, "tid": self.tid, "feeToken": "USDC"}
        if guardar:
            self.fills.append(f)
        return f

    async def enviar_fill(self, *a, **k):
        f = self.fazer_fill(*a, **k)
        await self.difundir("userFills", ENDERECO, "userFills", {"user": ENDERECO, "fills": [f]})
        return f

    async def mercado(self):
        passo = 0
        while True:
            await asyncio.sleep(0.02)
            if self.parado:
                continue
            passo += 1
            agora = ms()
            for m in self.moedas.values():
                mudou = False
                salto = 4 if self.rajada else 1
                if random.random() < (0.6 if self.rajada else 0.3):
                    m.pb = round(m.pb + random.choice((-1, 1)) * salto * m.tick, 8)
                    mudou = True
                novo_spread = random.choice((2, 3, 4)) if self.rajada else 1
                if novo_spread != m.spread:
                    m.spread, mudou = novo_spread, True
                if random.random() < 0.3:
                    m.qb, m.qa = round(random.uniform(0.5, 4), 4), round(random.uniform(0.5, 4), 4)
                    mudou = True
                if mudou or not m.bbo_log:
                    m.bbo_log.append((agora, m.pb, m.pa))
                    await self.difundir("bbo", m.nome, "bbo", {"coin": m.nome, "time": agora, "bbo": [
                        {"px": repr(m.pb), "sz": repr(m.qb), "n": 3}, {"px": repr(m.pa), "sz": repr(m.qa), "n": 2}]})
                if passo % 5 == 0:
                    bids = [{"px": repr(round(m.pb - i * m.tick, 8)), "sz": repr(m.qb if i == 0 else 3.0), "n": 2}
                            for i in range(20)]
                    asks = [{"px": repr(round(m.pa + i * m.tick, 8)), "sz": repr(m.qa if i == 0 else 3.0), "n": 2}
                            for i in range(20)]
                    await self.difundir("l2Book", m.nome, "l2Book", {"coin": m.nome, "time": agora, "levels": [bids, asks]})
                if random.random() < (0.8 if self.rajada else 0.3):
                    compra = random.random() < 0.5
                    px = m.pa if compra else m.pb
                    sz = round(random.uniform(0.05, 1.5), 4)
                    m.neg_log.append((agora, px, sz))
                    await self.difundir("trades", m.nome, "trades", [{
                        "coin": m.nome, "side": "B" if compra else "A", "px": repr(px), "sz": repr(sz),
                        "time": agora, "hash": "0x1", "tid": passo, "users": ["0xa", "0xb"]}])
                if passo % 25 == 0:
                    mid = (m.pb + m.pa) / 2
                    await self.difundir("activeAssetCtx", m.nome, "activeAssetCtx", {"coin": m.nome, "ctx": {
                        "markPx": repr(round(mid * 1.0002, 6)), "oraclePx": repr(round(mid, 6)),
                        "funding": "0.0000125", "openInterest": "1000", "dayNtlVlm": "1", "prevDayPx": "1"}})

    async def cortar_ligacoes(self):
        for ws in list(self.clientes):
            await ws.close()


async def coinglass_falso(ws):
    async def empurrar():
        while True:
            await asyncio.sleep(0.3)
            await ws.send(json.dumps({"channel": "liquidation_orders", "data": [{
                "base_asset": "BTC", "exchange": "Binance", "price": 121000.0, "side": 2,
                "symbol": "BTCUSDT", "time": ms(), "volume_usd": 50000.0}]}))
    tarefa = asyncio.ensure_future(empurrar())
    try:
        async for bruto in ws:
            if bruto == "ping":
                await ws.send("pong")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        tarefa.cancel()


class MetaHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        corpo = json.dumps({"universe": [{"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
                                         {"name": "ETH", "szDecimals": 4, "maxLeverage": 25},
                                         {"name": "SOL", "szDecimals": 2, "maxLeverage": 20}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


# --------------------------------------------------------------------------
# Conferencia
# --------------------------------------------------------------------------
FALHAS = []


def confere(cond, texto):
    print(("  ok    " if cond else "  FALHA ") + texto)
    if not cond:
        FALHAS.append(texto)


def perto(a, b, tol):
    return a == a and b == b and abs(a - b) <= tol


def mid_log(log, t_ms, estrito):
    ult = None
    for ex, b, a in log:
        if ex < t_ms or (not estrito and ex == t_ms):
            ult = (a + b) / 2
        else:
            break
    return ult


def ler(caminho):
    with open(caminho, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


async def principal():
    pasta = tempfile.mkdtemp(prefix="medidor_sim_")
    http = HTTPServer(("127.0.0.1", 0), MetaHandler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    bolsa = Bolsa()
    srv = await websockets.serve(bolsa.atender, "127.0.0.1", 0)
    cg = await websockets.serve(coinglass_falso, "127.0.0.1", 0)
    porta = srv.sockets[0].getsockname()[1]
    porta_cg = cg.sockets[0].getsockname()[1]
    ini = os.path.join(pasta, "config.ini")
    with open(ini, "w", encoding="utf-8") as f:
        f.write("""
[geral]
ativos = BTC, ETH
endereco = %s
pasta_dados = dados
[orcamento]
alvo_bps = 30
tamanho_usd = 1000
[detector]
janela_horas = 0.01
aquecimento_min = 0.05
piso_spread_bps = 0
piso_profundidade_x = 1000000
piso_vol_razao = 0
piso_intensidade_razao = 0
piso_ofi = 0
piso_mark_oraculo_bps = 0
piso_liquidacoes_usd = 0
[sombra]
prazo_s = 2
horizontes_s = 1, 2, 3
horizonte_regra_s = 3
horizontes_fill_s = 1, 2
janela_sinal_s = 5
[painel]
intervalo_s = 4
[coinglass]
chave = TESTE
[avancado]
amostra_s = 0.1
passo_historico = 2
sem_dados_s = 2
vigia_s = 3
url_ws = ws://127.0.0.1:%d
url_info = http://127.0.0.1:%d
url_coinglass = ws://127.0.0.1:%d/?k=
""" % (ENDERECO, porta, http.server_address[1], porta_cg))
    cfg = md.Config(ini)

    print("1. Verificacao contra a bolsa falsa")
    mercado = asyncio.ensure_future(bolsa.mercado())
    confere(await md._verificar(cfg), "comando verificar passa")

    print("\n2. Corrida de 24 s: sinais, fills, uma rajada, um corte de ligacao e uma paragem de dados\n")
    sz = md.obter_sz_decimals(cfg)
    med = md.Medidor(cfg, sz)
    tarefas = [asyncio.ensure_future(x) for x in
               (med.tarefa_hyperliquid(), med.tarefa_amostras(), med.tarefa_coinglass())]
    bolsa.inicio = time.monotonic()

    async def ate(seg):
        await asyncio.sleep(max(0, seg - bolsa.t()))

    def escrever_sinal(linha):
        with open(cfg.sinais_csv, "a", encoding="utf-8") as f:
            f.write(linha + "\n")

    await ate(4.0)
    escrever_sinal(",BTC,compra,,,,primeiro")
    await ate(5.0)
    btc = bolsa.moedas["BTC"]
    f1 = await bolsa.enviar_fill("BTC", "B", btc.pa, 0.01, "Open Long", True, 111)
    await ate(5.05)
    f2 = await bolsa.enviar_fill("BTC", "B", round(btc.pa + btc.tick, 8), 0.01, "Open Long", True, 111)
    await bolsa.enviar_fill("SOL", "B", 210.0, 1.0, "Open Long", True, 333)       # activo nao seguido
    await ate(6.0)
    atrasado = md.iso_utc(ms() - 3000)
    escrever_sinal("%s,ETH,venda,4300,,,atrasado" % atrasado)
    escrever_sinal(",BTC,talvez,,,,invalido")
    escrever_sinal(",DOGE,compra,,,,desconhecido")
    await ate(6.3)
    escrever_sinal(",BTC,venda,,,,no corte")
    await ate(6.5)
    bolsa.rajada = True
    await ate(7.5)
    await bolsa.cortar_ligacoes()
    await ate(8.5)
    bolsa.rajada = False
    await ate(10.0)
    md.cmd_sinal(cfg, "BTC", "venda", "", "pelo comando")
    await ate(11.0)
    f3 = await bolsa.enviar_fill("BTC", "A", btc.pa, 0.02, "Close Long", False, 222)
    await ate(12.0)
    eth = bolsa.moedas["ETH"]
    f4 = await bolsa.enviar_fill("ETH", "A", eth.pb, 0.5, "Open Short", True, 444)
    # fill mais rapido do que a leitura do ficheiro de sinais, e uma segunda ordem para o mesmo sinal
    await ate(13.02)
    escrever_sinal(",BTC,compra,,,,rapido")
    await asyncio.sleep(0.03)
    f5 = await bolsa.enviar_fill("BTC", "B", btc.pa, 0.01, "Open Long", True, 555)
    await ate(13.6)
    f6 = await bolsa.enviar_fill("BTC", "B", btc.pa, 0.01, "Open Long", True, 556)
    await ate(14.5)
    escrever_sinal(",BTC,venda,,,,na paragem")
    await ate(15.0)
    bolsa.parado = True                      # a bolsa cala-se mas continua a responder a pings
    await ate(17.6)
    escrever_sinal(",ETH,compra,,,,sem dados")
    await ate(20.0)
    bolsa.parado = False
    await ate(22.3)
    escrever_sinal(",ETH,compra,,,,ao fechar")
    await ate(23.9)
    for tk in tarefas:
        tk.cancel()
    await asyncio.gather(*tarefas, return_exceptions=True)
    med.fechar()

    print("\n3. Conferencia dos registos")
    confere(med.religacoes >= 2, "religou depois do corte e depois da paragem (religacoes=%d)" % med.religacoes)
    confere(not med.erros, "nenhum passo do ciclo deu erro %s" % (med.erros or ""))
    confere(med.erros_msg == 0, "nenhuma mensagem deu erro ao ser lida")
    confere(med.negocios_velhos >= 10 and all(px > 1000 for a in med.ativos.values() for _, _, px, _ in a.hist_neg),
            "lote de negocios antigos ignorado (%d)" % med.negocios_velhos)

    todos = ler(cfg.reg_sinais)
    nota = {s["nota"]: s for s in todos}
    confere(sorted(nota) == sorted(["primeiro", "atrasado", "no corte", "pelo comando", "rapido", "na paragem",
                                    "sem dados", "ao fechar"]),
            "8 sinais registados, 2 invalidos ignorados (%d)" % len(todos))
    sd = nota.get("sem dados", {})
    confere(sd.get("estado") == "SEM_DADOS" and sd.get("mid0", "") == "",
            "sinal dado durante a paragem fica sem preco, nao com um preco velho")
    np_ = nota.get("na paragem", {})
    confere(np_.get("mid0", "") != "" and [np_.get("r_%d" % h) for h in (1, 2, 3)] == ["", "", ""],
            "sinal antes da paragem: os resultados que caem nela ficam em branco %s"
            % [np_.get("r_%d" % h) for h in (1, 2, 3)])
    af = nota.get("ao fechar", {})
    confere(af.get("mid0", "") != "" and af.get("r_1", "") != "" and af.get("r_3", "x") == "",
            "sinal a meio quando o medidor para: linha escrita, o que faltava em branco")
    sinais = [s for s in todos if s.get("mid0") and s["nota"] not in ("na paragem", "ao fechar")]
    confere({k: nota[k]["fresco"] for k in ("primeiro", "atrasado", "no corte", "pelo comando") if k in nota}
            == {"primeiro": "1", "atrasado": "0", "no corte": "1", "pelo comando": "1"},
            "sinal atrasado reconhecido como nao fresco")
    nc = nota.get("no corte", {})
    confere(nc.get("r_1") != "" and nc.get("r_2") == "" and nc.get("r_3") != "",
            "sinal apanhado pelo corte: o R que cai no corte fica em branco, nao inventado")
    confere(nota.get("atrasado", {}).get("imp_bps", "x") == "", "sinal atrasado nao recebe impacto de um livro mais novo")
    confere(len({s["id"] for s in sinais}) == len(sinais), "identificadores unicos")
    for s in sinais:
        log = bolsa.moedas[s["ativo"]].bbo_log
        lado = 1 if s["lado"] == "compra" else -1
        ts = md.interpretar_hora(s["hora_utc"], 0)
        m0 = float(s["mid0"])
        m0_bolsa = mid_log(log, ts, estrito=False)
        confere(perto(m0, m0_bolsa, 3 * bolsa.moedas[s["ativo"]].tick),
                "%s: mid do sinal %.2f condiz com a bolsa %.2f" % (s["id"], m0, m0_bolsa))
        for h in (1, 2, 3):
            if s["r_%d" % h] == "":
                continue
            esperado = nu.resultado_bps(lado, mid_log(log, ts + h * 1000, False), m0)
            confere(perto(float(s["r_%d" % h]), esperado, 0.6),
                    "%s: R a %d s = %s bps (bolsa %.3f)" % (s["id"], h, s["r_%d" % h], esperado))
        # registo sombra reproduzido com os negocios que a bolsa enviou
        ordem = nu.OrdemSombra(lado, float(s["sombra_preco"]), float(s["sombra_fila"]),
                               float(s["tamanho_usd"]) / m0, ts, 2000)
        for ex, px, szn in bolsa.moedas[s["ativo"]].neg_log:
            ordem.negocio(ex, px, szn)
        if s["sombra_preenchida"] != "":
            confere(str(int(ordem.preenchida)) == s["sombra_preenchida"],
                    "%s: registo sombra preenchida=%s igual ao reproduzido" % (s["id"], s["sombra_preenchida"]))
        confere(float(s["ganho_passiva_bps"]) > 0, "%s: ganho da passiva positivo (meio spread)" % s["id"])
        if s["fresco"] == "1":
            confere(s["imp_bps"] != "" and s["p_teto"] != "" and float(s["imp_bps"]) > 0,
                    "%s: impacto %s bps e teto %s calculados" % (s["id"], s["imp_bps"], s["p_teto"]))

    fills = ler(cfg.reg_fills)
    confere(len(fills) == 6, "6 fills registados; antigo, repetidos e SOL ignorados (%d)" % len(fills))
    confere(len({f["tid"] for f in fills}) == len(fills), "sem fills duplicados apos religar")
    por_tid = {int(f["tid"]): f for f in fills}
    for orig in (f1, f2, f3, f4):
        f = por_tid.get(orig["tid"])
        if not f:
            confere(False, "fill tid %s em falta" % orig["tid"])
            continue
        log = bolsa.moedas[orig["coin"]].bbo_log
        lado = 1 if orig["side"] == "B" else -1
        px = float(orig["px"])
        m1 = mid_log(log, orig["time"], estrito=True)
        confere(perto(float(f["mid_antes"]), m1, 1e-6), "tid %s: mid antes do fill exacto" % f["tid"])
        confere(perto(float(f["e_bps"]), nu.custo_execucao_bps(lado, px, m1), 2e-3),
                "tid %s: E = %s bps" % (f["tid"], f["e_bps"]))
        confere(perto(float(f["f_bps"]), 4.5 if orig["crossed"] else 1.5, 2e-3),
                "tid %s: F = %s bps (%s)" % (f["tid"], f["f_bps"], "taker" if orig["crossed"] else "maker"))
        for h in (1, 2):
            mh = mid_log(log, orig["time"] + h * 1000, estrito=False)
            confere(perto(float(f["a_%d" % h]), nu.seleccao_adversa_bps(lado, mh, m1), 2e-3),
                    "tid %s: A a %d s = %s bps" % (f["tid"], h, f["a_%d" % h]))
    rap = nota.get("rapido", {})
    g5, g6 = por_tid.get(f5["tid"], {}), por_tid.get(f6["tid"], {})
    confere(g5.get("sinal_id") == rap.get("id") and g5.get("d_bps", "") != "",
            "fill 50 ms depois do sinal encontra o sinal (D = %s bps)" % g5.get("d_bps"))
    confere(g6.get("sinal_id") == rap.get("id"), "segunda ordem para o mesmo sinal liga-se ao mesmo sinal")
    confere(g6.get("a_1", "") != "" and g6.get("a_2", "x") == "",
            "seleccao adversa que cai na paragem fica em branco (a_1=%s, a_2=%r)" % (g6.get("a_1"), g6.get("a_2")))
    a, b, c, d = (por_tid.get(x["tid"], {}) for x in (f1, f2, f3, f4))
    prim = nota.get("primeiro", {})
    confere(a.get("sinal_id") == prim.get("id") and b.get("sinal_id") == prim.get("id"),
            "os dois fills da mesma ordem ligam-se ao primeiro sinal")
    if a.get("d_bps"):
        esperado = nu.custo_decisao_bps(1, float(a["mid_antes"]), float(prim["mid0"]))
        confere(perto(float(a["d_bps"]), esperado, 2e-3), "D = %s bps confere com os mids registados" % a["d_bps"])
        confere(0.7 <= float(a["atraso_s"]) <= 1.4, "atraso sinal-fill de %s s (esperado cerca de 1)" % a["atraso_s"])
    else:
        confere(False, "fill de abertura sem D")
    confere(c.get("sinal_id", "") == "" and c.get("taker") == "0" and float(c.get("e_bps", "0")) < 0,
            "fecho passivo: sem sinal, maker, execucao negativa (ganha o meio spread)")
    confere(d.get("sinal_id", "") == "", "abertura fora da janela do sinal fica sem D")

    for nome in ("BTC", "ETH"):
        fich = [x for x in os.listdir(cfg.pasta_metricas) if x.startswith(nome + "_")]
        linhas = ler(os.path.join(cfg.pasta_metricas, fich[0])) if fich else []
        estados = {x["estado"] for x in linhas}
        confere(nu.SEM_DADOS in estados, "%s: o tempo sem dados tambem fica registado" % nome)
        linhas = [x for x in linhas if x["estado"] != nu.SEM_DADOS]
        confere(len(linhas) > 50, "%s: %d linhas de metricas" % (nome, len(linhas)))
        confere(nu.AQUECIMENTO in estados and bool(estados & {nu.VERDE, nu.AMARELO, nu.VERMELHO}),
                "%s: passou do aquecimento a estados reais %s" % (nome, sorted(estados)))
        durante = [x for x in linhas if 6800 <= int(x["local_ms"]) - med.inicio_ms <= 7400]
        fora = [x for x in linhas if 3000 <= int(x["local_ms"]) - med.inicio_ms <= 6000]
        if durante and fora:
            s1 = nu.media([float(x["spread_bps"]) for x in durante])
            s0 = nu.media([float(x["spread_bps"]) for x in fora])
            confere(s1 > 1.5 * s0, "%s: spread medido sobe na rajada (%.3f -> %.3f bps)" % (nome, s0, s1))
            confere(any(x["estado"] in (nu.AMARELO, nu.VERMELHO) for x in durante),
                    "%s: a rajada fez soar o alarme" % nome)
        depois = [x for x in linhas if int(x["local_ms"]) - med.inicio_ms > 10000]
        confere(len(depois) > 20, "%s: metricas continuam depois de religar" % nome)
        if nome == "BTC":
            confere(any(x["liq_60s_usd"] not in ("", "0") for x in linhas), "BTC: liquidacoes CoinGlass somadas")
            x = linhas[len(linhas) // 2]
            confere(perto(float(x["spread_bps"]), 1e4 * (float(x["ask"]) - float(x["bid"])) / float(x["mid"]), 1e-3),
                    "BTC: spread registado bate com bid e ask da mesma linha")
            confere(perto(float(x["mark_oraculo_bps"]), 2.0, 0.05), "BTC: desvio mark-oraculo = 2 bps (bolsa pos 2)")

    with open(cfg.estado_json, encoding="utf-8") as f:
        est = json.load(f)
    confere(set(est["ativos"]) == {"BTC", "ETH"} and "estado" in est["ativos"]["BTC"]
            and est["ativos"]["BTC"].get("p_teto_compra"), "estado.json valido, com teto de preco")

    print("\n4. Relatorio\n")
    md.cmd_relatorio(cfg)

    print("5. Rearranque")
    med2 = md.Medidor(cfg, sz)
    confere(len(med2.ativos["BTC"].jan["spread"]) > 20, "historico recarregado do disco (%d amostras)"
            % len(med2.ativos["BTC"].jan["spread"]))
    med2.fechar()

    mercado.cancel()
    srv.close()
    cg.close()
    http.shutdown()
    shutil.rmtree(pasta, ignore_errors=True)
    print("\n" + ("SIMULACAO PASSOU: %d conferencias" % CONTA[0] if not FALHAS
                  else "SIMULACAO FALHOU em %d ponto(s):\n  - %s" % (len(FALHAS), "\n  - ".join(FALHAS))))
    return not FALHAS


CONTA = [0]
_confere = confere


def confere(cond, texto):  # noqa: F811  (conta as conferencias)
    CONTA[0] += 1
    _confere(cond, texto)


if __name__ == "__main__":
    import logging
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(0 if asyncio.run(principal()) else 1)
