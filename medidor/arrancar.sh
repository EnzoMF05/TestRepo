#!/bin/bash
# Prepara o ambiente na primeira vez e corre o medidor.
#   bash arrancar.sh verificar     testa as ligacoes
#   bash arrancar.sh               corre o medidor (Ctrl+C para parar)
#   bash arrancar.sh relatorio     mostra o resumo do que foi medido
#   bash arrancar.sh sinal BTC compra
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Nao encontrei o python3. Instale o Python em https://www.python.org/downloads/ e repita."
  exit 1
fi
if [ ! -d .venv ]; then
  echo "Primeira vez: a preparar o ambiente (demora cerca de um minuto)..."
  python3 -m venv .venv || { echo "Nao consegui criar o ambiente .venv"; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate || exit 1
if ! python3 -c "import websockets" >/dev/null 2>&1; then
  python3 -m pip install --quiet --disable-pip-version-check websockets || {
    echo "Nao consegui instalar a biblioteca websockets. Verifique a ligacao a internet."; exit 1; }
fi
if [ $# -eq 0 ] && command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i python3 medidor.py   # impede o Mac de adormecer enquanto mede
fi
exec python3 medidor.py "$@"
