"""Sondagem: a Polymarket tem mercado de MÍNIMA (lowest temperature) para as
nossas cidades? Testa o slug lowest-temperature-in-<cidade>-on-... nos últimos
dias e reporta a cobertura, com um controle no Highest para validar o método.

Roda no GitHub Actions (a rede desta sessão é fechada).
Uso: python -m lowtemp.probe
"""
from __future__ import annotations

import datetime as dt
import sys

import requests

from tmax import polymarket as pm

GAMMA = "https://gamma-api.polymarket.com"
S = requests.Session()
S.headers["User-Agent"] = "lowtemp-probe/0.1"
DIAS = 14


def slug(kind: str, city: str, d: dt.date) -> str:
    return f"{kind}-temperature-in-{city}-on-{pm._MONTHS[d.month - 1]}-{d.day}-{d.year}"


def fetch(s: str):
    try:
        r = S.get(f"{GAMMA}/events", params={"slug": s}, timeout=25)
        r.raise_for_status()
        d = r.json()
    except Exception:  # noqa: BLE001
        return None
    ev = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
    return ev if ev and ev.get("markets") else None


def main() -> int:
    hoje = dt.date.today()
    print(f"Sondando LOWEST em {len(pm._ICAO_TO_CITY_SLUG)} cidades "
          f"(últimos {DIAS} dias)\n")
    achou = []
    for icao, city in pm._ICAO_TO_CITY_SLUG.items():
        hits, sample = 0, None
        for back in range(DIAS):
            d = hoje - dt.timedelta(days=back)
            ev = fetch(slug("lowest", city, d))
            if ev:
                hits += 1
                sample = sample or (slug("lowest", city, d), ev)
        marca = "✓" if hits else " "
        print(f"  {marca} {icao:<5} {city:<14} lowest: {hits}/{DIAS} dias")
        if hits:
            achou.append((icao, city, sample))

    # controle: o método funciona? (Highest de uma cidade deve aparecer)
    ctrl = fetch(slug("highest", "london", hoje - dt.timedelta(days=1)))
    print(f"\ncontrole (highest london ontem): "
          f"{'OK — método funciona' if ctrl else 'sem retorno (revisar método)'}")

    if not achou:
        print("\nNENHUMA cidade tem mercado de LOWEST. "
              "Replicar a Ceifa no Lowest não teria dado.")
        return 0

    icao, city, (s, ev) = achou[0]
    print(f"\n=== estrutura de exemplo: {s} ===")
    print("title:", ev.get("title"), "| closed:", ev.get("closed"))
    for m in (ev.get("markets") or [])[:4]:
        print("  q:", m.get("question"))
        print("     outcomes:", m.get("outcomes"),
              "| prices:", m.get("outcomePrices"),
              "| tokens:", bool(m.get("clobTokenIds")))
    print(f"\nRESUMO: {len(achou)}/{len(pm._ICAO_TO_CITY_SLUG)} cidades com lowest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
