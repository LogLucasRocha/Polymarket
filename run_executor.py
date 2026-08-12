"""Executor da Ceifa: lê os sinais decididos pelo alerta e envia as ordens.

Fluxo (fonte única de verdade): o ``send_telegram`` decide as entradas
(H-1, preço, livro, filtros) e grava a lista de parcelas num JSON. Este runner
lê esse JSON e executa cada uma via ``tmax.executor`` — com todas as travas de
segurança (desligado por padrão, dry-run, teto por ordem, teto de exposição,
kill switch, idempotência).

RODE NA SUA MÁQUINA (precisa da chave da carteira em POLYMARKET_PRIVATE_KEY).
Comece SEMPRE em dry-run e confira o que ele diz que compraria antes de ligar
de verdade.

Uso:
    python run_executor.py check           # testa a chave (offline): imprime o endereço
    python run_executor.py                 # executa os sinais gravados pelo alerta
    python run_executor.py sinais.json     # arquivo de sinais alternativo

Formato do JSON: lista de objetos
    {"ref": "ZGGG:2026-08-07:36°C:1105", "day": "2026-08-07",
     "token_id": "0x...", "price": 0.969, "size_usd": 8.45}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tmax import config, executor

DEFAULT_SIGNALS = config.CEIFA_EXEC_SIGNALS


def _load_signals(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[executor] nenhum arquivo de sinais em {path} — nada a fazer.")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"[executor] {path} deveria ser uma lista de sinais.")
    return data


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        try:
            address = executor.wallet_address()
        except executor.ExecutorError as exc:
            print(f"[executor] {exc}")
            return 1
        print(f"Chave OK (offline). Signer (EOA): {address}")
        print("→ Deve bater com o 'Signer Address' do Polymarket. Em contas por "
              "e-mail, é DIFERENTE do 'Polymarket Wallet Address' (o funder) — "
              "isso é normal; configure o funder em POLYMARKET_FUNDER.")
        return 0

    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        try:
            executor.build_client()
        except Exception as exc:  # noqa: BLE001 — mostra o motivo pro usuário
            print(f"[executor] auth FALHOU: {exc}")
            print("  Dica: confira POLYMARKET_FUNDER (o Polymarket Wallet "
                  "Address) e, se preciso, POLYMARKET_SIGNATURE_TYPE (1=e-mail, "
                  "2=carteira de navegador).")
            return 1
        print("Auth OK — cliente CLOB conectado. NENHUMA ordem foi enviada.")
        return 0

    cfg = executor.settings()
    print("[executor] configuração efetiva:")
    print(f"  ligado={cfg['enabled']}  dry_run={cfg['dry_run']}  "
          f"teto/ordem=${cfg['max_stake']:.2f}  teto/dia=${cfg['max_exposure']:.2f}")
    if not cfg["enabled"]:
        print("  → executor DESLIGADO (CEIFA_EXEC_ENABLED=False). "
              "Ligue por variável de ambiente quando quiser começar.")
    elif cfg["dry_run"]:
        print("  → DRY-RUN: nenhuma ordem real será enviada, só registrada.")
    else:
        print("  ⚠️  MODO REAL: ordens SERÃO enviadas à Polymarket.")

    signals_path = config.ROOT / (sys.argv[1] if len(sys.argv) > 1
                                  else DEFAULT_SIGNALS)
    signals = _load_signals(signals_path)
    if not signals:
        return 0
    executor.execute_signals(signals, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
