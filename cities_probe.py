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


def main() -> int:
    # 1) achar a tag de temperatura/clima
    tagid = None
    try:
        for t in get(f"{GAMMA}/tags", limit=1000):
            lab = (str(t.get("label", "")) + str(t.get("slug", ""))).lower()
            if "temperature" in lab or "weather" in lab or "climate" in lab:
                print("tag candidata:", t.get("id"), t.get("slug"), t.get("label"))
                tagid = tagid or t.get("id")
    except Exception as exc:  # noqa: BLE001
        print("tags err:", exc)

    cidades: set = set()
    if tagid:
        scan({"tag_id": str(tagid)}, cidades)
    if not cidades:
        print("(sem tag útil — varrendo eventos ativos)")
        scan({}, cidades)

    nossas = set(pm._ICAO_TO_CITY_SLUG.values())
    print(f"\n=== cidades de temperatura ativas na Polymarket: {len(cidades)} ===")
    for c in sorted(cidades):
        print(("  NOVA → " if c not in nossas else "         ") + c)
    novas = sorted(cidades - nossas)
    print(f"\nNOVAS (não temos): {novas or 'nenhuma'}")
    faltando_ativas = sorted(nossas - cidades)
    print(f"(nossas sem evento ativo agora: {faltando_ativas})")

    # 2) Milan / Wuhan explícitos
    print("\n=== Milan / Wuhan (highest e lowest, últimos 12 dias) ===")
    hoje = dt.date.today()
    for nome, variants in (("Milan", ["milan", "milano"]),
                           ("Wuhan", ["wuhan"])):
        for city in variants:
            for kind in ("highest", "lowest"):
                hits = 0
                for back in range(12):
                    d = hoje - dt.timedelta(days=back)
                    slug = (f"{kind}-temperature-in-{city}-on-"
                            f"{pm._MONTHS[d.month - 1]}-{d.day}-{d.year}")
                    try:
                        if ev_ok(get(f"{GAMMA}/events", slug=slug)):
                            hits += 1
                    except Exception:  # noqa: BLE001
                        pass
                print(f"  {nome:<6} [{city:<7}] {kind:<7}: {hits}/12 dias")
    return 0


if __name__ == "__main__":
    sys.exit(main())
