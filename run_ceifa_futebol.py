"""Relatório diário da observação in-play do futebol (Ceifa no futebol).

Roda pelo mesmo cron das 06:00 (ceifa_report.yml), mandando uma mensagem
apartada com os mesmos números dos outros: testes, assertividade, retorno
diário médio, drawdown — mais o ask médio de entrada e os stops. Modo
observação: não aposta.

Uso local: python run_ceifa_futebol.py [--no-telegram]
"""
from __future__ import annotations

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

import argparse
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from futebol import report as futreport
from tmax import notify


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    log = lambda msg: print(msg, flush=True)  # noqa: E731
    st = futreport.simulate(log)
    text = futreport.report_text(st)
    if st["n"]:
        parts = [f"{k} {v[1] / v[0]:.0%} (n={v[0]})"
                 for k, v in sorted(st["by_league"].items(),
                                    key=lambda kv: -kv[1][0])[:6]]
        text += "\n<i>Top ligas:</i> " + " · ".join(parts)

    print("\n" + text.replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", ""))

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not args.no_telegram and token and chat_id:
        notify.send_message(token, chat_id, text)
        print("[telegram] relatório da Ceifa Futebol enviado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
