"""Relatório diário unificado da Ceifa na temperatura MÍNIMA — monitoramento.

Lê o lago dados_low/ e reúne as cidades cujos contratos resolvem em °C e °F.
Roda no cron das 06:00 em modo observação: não aposta.

Uso local: python run_ceifa_low.py [--no-telegram]
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

from tmax import backtest, ceifa, config, notify

ARCHIVE = config.ROOT / "dados_low"
TITULO = "❄️ <b>Ceifa Mínima — monitoramento (nossos snapshots)</b>"
NOTA = ("<i>Temperatura MÍNIMA (lowest) das cidades em °C e °F — observação, "
        "ainda sem apostar. Cada contrato preserva sua unidade original; "
        "H-1 = hora antes do mínimo previsto.</i>")


def minimum_station_icaos() -> set[str]:
    """Todas as cidades do estudo de mínima, independentemente da unidade."""
    return set(config.STATIONS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    log = lambda msg: print(msg, flush=True)  # noqa: E731
    st = ceifa.simulate_repeated(
        log, icaos=minimum_station_icaos(), archive=ARCHIVE,
        warm_target_filter=False, uncertainty_filter=False,
        ensemble_band_filter=config.CEIFA_ENSEMBLE_BAND_FILTER,
        interval_minutes=config.CEIFA_REPEAT_MINUTES,
        stake_frac=config.CEIFA_STAKE_FRAC)
    text = backtest.ceifa_report_text(
        st, titulo=TITULO, nota=NOTA, extreme_label="mínimo previsto")

    print("\n" + text.replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", ""))

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not args.no_telegram and token and chat_id:
        notify.send_message(token, chat_id, text)
        print("[telegram] relatório da Ceifa Mínima enviado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
