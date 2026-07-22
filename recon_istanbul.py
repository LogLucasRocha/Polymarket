"""Reconstroi a Istambul de 07-22 (o estouro de 34°C) via prices-history e roda
o comparativo do filtro de incerteza × stop COM a perda dentro da amostra.

Roda no GitHub Actions (rede fechada nesta sessão). Uso: python recon_istanbul.py
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import sys

import pandas as pd
import requests

from tmax import ceifa, config

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
S = requests.Session()
S.headers["User-Agent"] = "recon-istanbul/0.1"


def get(u, **p):
    r = S.get(u, params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def loads(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:  # noqa: BLE001
            return []
    return x or []


def recon():
    """mercado reconstruído de LTFM 07-22 (preço do NÃO por faixa, horário)."""
    d = get(f"{GAMMA}/events",
            slug="highest-temperature-in-istanbul-on-july-22-2026")
    ev = d[0] if isinstance(d, list) and d else d
    st = int(dt.datetime(2026, 7, 22, 0, 0, tzinfo=dt.timezone.utc).timestamp())
    en = int(dt.datetime(2026, 7, 23, 6, 0, tzinfo=dt.timezone.utc).timestamp())
    rows = []
    for m in (ev or {}).get("markets", []):
        outs = [str(o).lower() for o in loads(m.get("outcomes"))]
        toks = loads(m.get("clobTokenIds"))
        if "no" not in outs or len(toks) != len(outs):
            continue
        no_tok = toks[outs.index("no")]
        faixa = m.get("groupItemTitle") or m.get("question")
        try:
            h = get(f"{CLOB}/prices-history", market=no_tok, startTs=st,
                    endTs=en, fidelity=60)
        except Exception as exc:  # noqa: BLE001
            print("prices-history erro:", faixa, exc)
            continue
        pts = h.get("history") if isinstance(h, dict) else h
        for pt in (pts or []):
            rows.append({
                "ts_utc": dt.datetime.fromtimestamp(
                    pt["t"], dt.timezone.utc).isoformat(),
                "icao": "LTFM", "dia": "2026-07-22", "faixa": faixa,
                "preco_nao": float(pt["p"]), "preco_sim": 1 - float(pt["p"])})
    df = pd.DataFrame(rows)
    print(f"reconstruído: {len(df)} linhas · "
          f"{df['faixa'].nunique() if len(df) else 0} faixas · "
          f"NÃO final por faixa:")
    if len(df):
        fim = df.sort_values("ts_utc").groupby("faixa")["preco_nao"].last()
        for fx, v in fim.items():
            print(f"    {fx:<16} NÃO_final={v:.3f}")
    return df


def build_signals():
    mkt = ceifa._load("mercado")
    prev = ceifa._load("previsao")
    mkt = mkt[~((mkt["icao"] == "LTFM") & (mkt["dia"] == "2026-07-22"))]
    rec = recon()
    if len(rec):
        rec["ts"] = pd.to_datetime(rec["ts_utc"], utc=True)
        mkt = pd.concat([mkt, rec], ignore_index=True)
    mkt = mkt.sort_values("ts")
    Hs = (prev.dropna(subset=["pico_hora"]).groupby(["icao", "dia"])["pico_hora"]
             .agg(lambda s: int(s.mode().iat[0])).to_dict())
    mkt["hloc"] = mkt.groupby("icao", group_keys=False).apply(ceifa._local_hour)
    pv = prev.dropna(subset=["teto_ens", "mediana"]).copy()
    pv["spread"] = pv["teto_ens"] - pv["mediana"]
    pmin, pmax = config.CEIFA_PRICE_MIN, config.CEIFA_PRICE_MAX
    stop = config.STOP_EXIT_FRAC
    sigs = []
    for (icao, dia, faixa), g in mkt.groupby(["icao", "dia", "faixa"]):
        H = Hs.get((icao, dia))
        if H is None:
            continue
        h1 = g[g["hloc"] == ((H - 1) % 24)]
        if h1.empty:
            continue
        e = h1.iloc[-1]
        entry = float(e["preco_nao"])
        if not (pmin < entry < pmax):
            continue
        nao_final = float(g["preco_nao"].iloc[-1])
        resolvido = nao_final > 0.90 or nao_final < 0.10
        depois = g[g["ts"] > e["ts"]].reset_index(drop=True)
        precos = depois["preco_nao"].tolist()
        stop_lv = entry * (1 - stop)
        stopped, loss_frac = False, None
        for i in range(len(precos) - 1):
            if precos[i] <= stop_lv and precos[i + 1] <= stop_lv:
                stopped, loss_frac = True, 1.0 - precos[i + 1] / entry
                break
        if not (resolvido or stopped):
            continue
        p = pv[(pv["icao"] == icao) & (pv["dia"] == dia)]
        p = p[p["ts"] <= e["ts"]]
        spread = float(p["spread"].iloc[-1]) if len(p) else None
        sigs.append({"icao": icao, "day": dia, "faixa": faixa, "ts": e["ts"],
                     "price": entry, "won_final": nao_final > 0.5,
                     "stopped": stopped, "loss_frac": loss_frac,
                     "spread": spread})
    return sigs


def scen(sigs, use_stop, thr=None):
    ss = []
    for s in sigs:
        if not use_stop and thr is not None and (
                s["spread"] is None or s["spread"] > thr):
            continue
        if use_stop:
            won = (not s["stopped"]) and s["won_final"]
            ss.append({**s, "won": won})
        else:
            ss.append({**s, "won": s["won_final"], "stopped": False,
                       "loss_frac": None})
    st = ceifa._stats(ss, len({s["day"] for s in ss}))
    pd_ = st.get("per_day", [])
    retm = sum(d["ret"] for d in pd_) / len(pd_) if pd_ else 0
    return st["n"], st["hit"], st["real_mult"], retm, st["real_dd"]


def main() -> int:
    sigs = build_signals()
    print("\ncenário                       n   acerto   R$100→  ret/dia  dd_máx")
    for nome, us, thr in [("1) COM stop", True, None),
                          ("2) SEM stop, sem filtro", False, None),
                          ("3) SEM stop, filtro<=3.0", False, 3.0),
                          ("4) SEM stop, filtro<=2.5", False, 2.5),
                          ("5) SEM stop, filtro<=2.0", False, 2.0)]:
        n, hit, mult, retm, ddx = scen(sigs, us, thr)
        print(f"{nome:<26} {n:>4} {hit:>7.1%} {mult * 100:>8.2f} "
              f"{retm * 100:>7.2f}% {ddx:>6.1%}")

    print("\n=== apostas de Istambul 07-22 reconstruídas ===")
    for s in sigs:
        if s["icao"] == "LTFM" and s["day"] == "2026-07-22":
            tag = ("STOP" if s["stopped"] else
                   ("WIN" if s["won_final"] else "PERDA TOTAL"))
            sp = None if s["spread"] is None else round(s["spread"], 2)
            print(f"  {s['faixa']:<16} entrada={s['price']:.3f} -> {tag} "
                  f"| spread_entrada={sp}")

    perdas = [s for s in sigs if not s["won_final"] and not s["stopped"]]
    print(f"\nperdas totais na amostra: {len(perdas)}")
    for s in perdas:
        print(f"  {s['icao']} {s['day']} {s['faixa']} spread="
              f"{None if s['spread'] is None else round(s['spread'], 2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
