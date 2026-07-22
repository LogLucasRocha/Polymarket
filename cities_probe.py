"""Sondagem: descobre TODAS as cidades de temperatura que a Polymarket lista
(máxima/mínima) e mostra quais não temos. Testa Milan e Wuhan explicitamente.

Roda no GitHub Actions (a rede desta sessão é fechada). Uso: python cities_probe.py
"""
from __future__ import annotations

import datetime as dt
import re
import sys

import requests

from tmax import polymarket as pm

GAMMA = "https://gamma-api.polymarket.com"
S = requests.Session()
S.headers["User-Agent"] = "cities-probe/0.1"
SLUG_RE = re.compile(r"^(highest|lowest)-temperature-in-(.+?)-on-[a-z]+-\d+-\d+")


def get(url: str, **p):
    r = S.get(url, params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def ev_ok(d):
    ev = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
    return ev if ev and ev.get("markets") else None


def scan(params, cidades):
    off = 0
    while off < 6000:
        try:
            evs = get(f"{GAMMA}/events", closed="false", limit=100, offset=off,
                      **params)
        except Exception as exc:  # noqa: BLE001
            print("scan err:", exc)
            break
        if not isinstance(evs, list) or not evs:
            break
        for e in evs:
            m = SLUG_RE.match(e.get("slug", "") or "")
            if m:
                cidades.add(m.group(2))
        if len(evs) < 100:
            break
        off += 100


# Cidades candidatas (slug provável na Polymarket). As que já temos ficam de
# fora do "novas". Testa highest (hoje/ontem) e, se achar, quantos dias de
# histórico + se tem lowest.
CANDIDATAS = [
    "milan", "wuhan", "berlin", "rome", "munich", "frankfurt", "hamburg",
    "barcelona", "lisbon", "dublin", "vienna", "prague", "budapest", "athens",
    "stockholm", "oslo", "copenhagen", "helsinki", "zurich", "geneva",
    "brussels", "rotterdam", "dubai", "abu-dhabi", "doha", "riyadh", "tel-aviv",
    "delhi", "new-delhi", "mumbai", "bangalore", "kolkata", "chennai",
    "hyderabad", "bangkok", "jakarta", "manila", "kuala-lumpur", "hanoi",
    "ho-chi-minh-city", "hong-kong", "taipei", "guangzhou", "shenzhen",
    "chengdu", "osaka", "cairo", "lagos", "nairobi", "johannesburg",
    "cape-town", "casablanca", "sydney", "melbourne", "brisbane", "perth",
    "auckland", "rio-de-janeiro", "brasilia", "lima", "bogota", "santiago",
    "montevideo", "caracas", "kyiv", "athens",
]


def dias_hist(city, kind, n=12):
    hoje = dt.date.today()
    hits = 0
    for back in range(n):
        d = hoje - dt.timedelta(days=back)
        slug = (f"{kind}-temperature-in-{city}-on-"
                f"{pm._MONTHS[d.month - 1]}-{d.day}-{d.year}")
        try:
            if ev_ok(get(f"{GAMMA}/events", slug=slug)):
                hits += 1
        except Exception:  # noqa: BLE001
            pass
    return hits


NOVAS_13 = ["milan", "wuhan", "munich", "helsinki", "tel-aviv", "manila",
            "kuala-lumpur", "hong-kong", "taipei", "guangzhou", "shenzhen",
            "chengdu", "cape-town"]


def main() -> int:
    # Fonte de resolução de cada cidade nova — precisamos do ICAO da descrição
    # (senão a estação METAR desalinha da fonte do mercado, como Istambul; e há
    # casos não-METAR, tipo Hong Kong, que devem ser descartados).
    hoje = dt.date.today()
    for city in NOVAS_13:
        ev = None
        for d in (hoje, hoje - dt.timedelta(days=1), hoje - dt.timedelta(days=2)):
            slug = (f"highest-temperature-in-{city}-on-"
                    f"{pm._MONTHS[d.month - 1]}-{d.day}-{d.year}")
            ev = ev_ok(get(f"{GAMMA}/events", slug=slug))
            if ev:
                break
        print("\n" + "=" * 66)
        print(f"CIDADE: {city}")
        if not ev:
            print("  (sem evento recente)")
            continue
        rs = ev.get("resolutionSource") or ""
        desc = (ev.get("description") or "").replace("\n", " ")
        mkts = ev.get("markets") or []
        mdesc = (mkts[0].get("description") or "").replace("\n", " ") if mkts else ""
        print(f"  resolutionSource: {rs[:200]}")
        print(f"  description: {desc[:280]}")
        if mdesc and mdesc != desc:
            print(f"  market.desc: {mdesc[:280]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
