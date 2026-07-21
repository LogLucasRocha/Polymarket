"""Sondagem da estrutura de ESPORTES da Polymarket.

Objetivo: descobrir, a partir da API pública, como a Polymarket modela ligas e
jogos, para depois construir a captura horária (odds na abertura → a cada hora →
dia do evento). NÃO grava nada e NÃO aposta — só imprime o que a API devolve.

Roda no GitHub Actions (a rede desta sessão é fechada). Uso:
    python -m futebol.probe
"""
from __future__ import annotations

import json
import sys
import time

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
S = requests.Session()
S.headers["User-Agent"] = "futebol-research/0.1"


def get(url: str, **params):
    r = S.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def sec(t: str) -> None:
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72, flush=True)


def dump(obj, n=3500):
    s = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    print(s[:n] + (" …[truncado]" if len(s) > n else ""), flush=True)


def main() -> int:
    # 1) Tags: procurar como as ligas de esporte são identificadas.
    sec("1) TAGS — procurando ligas/esportes")
    for params in ({"limit": 1000}, {}, {"is_carousel": "true"}):
        try:
            tags = get(f"{GAMMA}/tags", **params)
            if isinstance(tags, list) and tags:
                print(f"[/tags {params}] -> {len(tags)} tags. Exemplo:")
                dump(tags[0], 800)
                alvo = [t for t in tags if any(
                    k in str(t.get("slug", "")).lower()
                    + str(t.get("label", "")).lower()
                    for k in ("soccer", "football", "serie", "liga", "premier",
                              "brasil", "-bra", "nba", "nfl", "mlb", "league",
                              "cup", "champions", "laliga", "epl", "sport"))]
                print(f"\n{len(alvo)} tags parecem esporte. Slugs:")
                for t in alvo[:80]:
                    print("  •", t.get("id"), "|", t.get("slug"), "|",
                          t.get("label"))
                break
        except Exception as e:  # noqa: BLE001
            print(f"[/tags {params}] ERRO: {e}")

    # 2) Endpoint dedicado de esportes (se existir).
    sec("2) ENDPOINTS dedicados de esporte (tentativas)")
    for path in ("/sports", "/series", "/sports/events", "/leagues"):
        try:
            d = get(f"{GAMMA}{path}", limit=20)
            print(f"[{path}] OK -> tipo={type(d).__name__}, "
                  f"len={len(d) if hasattr(d, '__len__') else '?'}")
            dump(d, 1500)
        except Exception as e:  # noqa: BLE001
            print(f"[{path}] ERRO: {e}")

    # 3) Eventos filtrando por tag (tenta os nomes de parâmetro possíveis).
    sec("3) EVENTS por tag do Brasileirão (tentando params)")
    game = None
    for params in ({"tag_slug": "bra"}, {"tag": "bra"},
                   {"tag_slug": "brasileirao"}, {"series_slug": "bra"},
                   {"tag_slug": "soccer"}, {"tag_slug": "football"}):
        try:
            evs = get(f"{GAMMA}/events", closed="false", limit=6,
                      order="startDate", ascending="true", **params)
            n = len(evs) if isinstance(evs, list) else 0
            print(f"[events {params}] -> {n} eventos")
            if n:
                for e in evs[:6]:
                    print("   •", e.get("slug"), "|", e.get("title"))
                if game is None:
                    game = evs[0]
        except Exception as e:  # noqa: BLE001
            print(f"[events {params}] ERRO: {e}")

    # 4) Estrutura completa de UM jogo (para ver times, horário, tokens).
    sec("4) ESTRUTURA de um jogo (evento + mercados + tokens)")
    if game is None:
        try:  # fallback: qualquer evento com 2 outcomes (jogo)
            evs = get(f"{GAMMA}/events", closed="false", limit=40)
            game = next((e for e in evs if len(e.get("markets", []) or []) >= 1
                         and "vs" in (e.get("title", "").lower()
                                      + e.get("slug", ""))), None)
        except Exception as e:  # noqa: BLE001
            print("fallback ERRO:", e)
    if game:
        print("Campos do evento:", sorted(game.keys()))
        for k in ("slug", "title", "startDate", "startTime", "gameStartTime",
                  "closed", "series", "tags"):
            if k in game:
                print(f"  {k}: {json.dumps(game[k], ensure_ascii=False, default=str)[:200]}")
        mkts = game.get("markets") or []
        print(f"\n{len(mkts)} mercado(s) no evento. Primeiro mercado:")
        if mkts:
            m = mkts[0]
            print("Campos do mercado:", sorted(m.keys()))
            for k in ("question", "outcomes", "outcomePrices", "clobTokenIds",
                      "lastTradePrice", "bestBid", "bestAsk", "volume",
                      "startDate", "endDate", "closed"):
                if k in m:
                    print(f"  {k}: {json.dumps(m[k], ensure_ascii=False, default=str)[:240]}")
    else:
        print("Não achei um jogo de exemplo pelas tentativas acima.")

    # 5) prices-history: a série histórica que dá abertura→hora a hora→evento.
    sec("5) PRICES-HISTORY (o histórico horário que queremos)")
    token = None
    if game and (game.get("markets")):
        raw = game["markets"][0].get("clobTokenIds")
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            token = ids[0] if ids else None
        except Exception:  # noqa: BLE001
            token = None
    print("token de teste:", str(token)[:40], "…" if token else "(nenhum)")
    if token:
        for params in ({"interval": "max", "fidelity": 60},
                       {"interval": "1w", "fidelity": 60},
                       {"startTs": int(time.time()) - 14 * 86400,
                        "endTs": int(time.time()), "fidelity": 60}):
            try:
                d = get(f"{CLOB}/prices-history", market=token, **params)
                pts = d.get("history", d) if isinstance(d, dict) else d
                print(f"[prices-history {params}] -> {len(pts)} pontos")
                if pts:
                    print("  primeiro:", pts[0], "| último:", pts[-1])
                break
            except Exception as e:  # noqa: BLE001
                print(f"[prices-history {params}] ERRO: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
