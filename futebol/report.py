"""Relatório diário da observação IN-PLAY do futebol (Ceifa no futebol).

Lê os NOSSOS snapshots ao vivo (dados_futebol/), pega a 1ª entrada de cada lado
na banda [0,95; 0,995), "compra" no bestAsk (preço real de execução), segura até
o jogo liquidar, e reporta os mesmos números dos outros relatórios: testes,
assertividade, retorno diário médio, drawdown diário médio — mais o ask médio de
entrada (executabilidade). Cruza com a resolução real do jogo (Gamma API).

Só observação — não aposta.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ARCH = ROOT / "dados_futebol"
GAMMA = "https://gamma-api.polymarket.com"
STAKE_FRAC = 0.10
BAND_LO, BAND_HI = 0.95, 0.995
STOP_EXIT_FRAC = 0.15        # sai a −15% da entrada (mesmo valor da Ceifa)

S = requests.Session()
S.headers["User-Agent"] = "futebol-report/0.1"


def _load():
    import pandas as pd

    files = sorted(ARCH.glob("*.parquet")) if ARCH.exists() else []
    if not files:
        return None
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df.sort_values("ts")


def _resolve(slug: str, cache: dict) -> dict:
    """{team_label: venceu?} para os mercados JÁ resolvidos do jogo."""
    if slug in cache:
        return cache[slug]
    res = {}
    try:
        d = S.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30).json()
        ev = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else {})
        for m in (ev or {}).get("markets", []) or []:
            lbl = m.get("groupItemTitle")
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices)
            if lbl and prices and m.get("closed"):
                res[lbl] = str(prices[0]) in ("1", "1.0")
    except Exception as exc:  # noqa: BLE001
        print(f"[resolve] {slug}: {exc}", file=sys.stderr)
    cache[slug] = res
    return res


def simulate(log=lambda m: None) -> dict:
    import pandas as pd

    df = _load()
    if df is None or df.empty:
        log("futebol in-play: sem dados capturados ainda.")
        return {"n": 0, "signals": []}
    band = df[df["band"].astype(bool)]
    if band.empty:
        return {"n": 0, "signals": []}
    chaves = band[["slug", "team"]].drop_duplicates().itertuples(index=False)
    cache: dict = {}
    signals = []
    for slug, team in chaves:
        grp = df[(df["slug"] == slug) & (df["team"] == team)].sort_values("ts")
        ent = grp[grp["band"].astype(bool)].iloc[0]        # 1ª entrada na banda
        entry = float(ent["price"])
        ask = (float(ent["ask"]) if pd.notna(ent["ask"]) and float(ent["ask"]) > 0
               else entry)
        # STOP fiel (regra da Ceifa): depois da entrada, só conta se o preço
        # persistir abaixo do nível na rodada SEGUINTE — saída pelo preço do +1.
        depois = grp[grp["ts"] > ent["ts"]]
        precos = [float(p) for p in depois["price"].tolist()]
        stop_lv = entry * (1 - STOP_EXIT_FRAC)
        stopped, exit_px = False, None
        for i in range(len(precos) - 1):
            if precos[i] <= stop_lv and precos[i + 1] <= stop_lv:
                stopped, exit_px = True, precos[i + 1]
                break
        res = _resolve(slug, cache)
        won = res.get(team)
        if not stopped and won is None:                    # ainda em aberto
            continue
        signals.append({"day": ent["dia"], "slug": slug, "team": team,
                        "ask": ask, "stopped": stopped, "exit": exit_px,
                        "won": bool(won), "ts": ent["ts"], "liga": ent["liga"]})
    return _stats(signals)


def _stats(signals: list) -> dict:
    n = len(signals)
    if n == 0:
        return {"n": 0, "signals": []}
    wins = sum(1 for s in signals if s["won"] and not s["stopped"])
    n_stop = sum(1 for s in signals if s["stopped"])
    by_day: dict = defaultdict(list)
    for s in signals:
        by_day[s["day"]].append(s)
    real, rpeak, real_dd = 1.0, 1.0, 0.0
    per_day = []
    for day in sorted(by_day):
        bets = sorted(by_day[day], key=lambda x: x["ts"])
        disp, liq = real, 0.0
        for s in bets:
            stake = STAKE_FRAC * disp
            disp -= stake
            if s["stopped"]:
                liq += stake * (s["exit"] / s["ask"])   # saiu no stop (parcial)
            elif s["won"]:
                liq += stake / s["ask"]                 # liquidou em 1,0
            # virada sem stop → perda total (0)
        novo = disp + liq
        ret = (novo / real - 1.0) if real else 0.0
        real = novo
        rpeak = max(rpeak, real)
        ddn = (1 - real / rpeak) if rpeak else 0.0
        real_dd = max(real_dd, ddn)
        per_day.append({"day": day, "n": len(bets),
                        "wins": sum(1 for x in bets if x["won"]),
                        "ret": ret, "dd": ddn})
    byl: dict = defaultdict(lambda: [0, 0])
    for s in signals:
        byl[s["liga"]][0] += 1
        byl[s["liga"]][1] += 1 if s["won"] else 0
    return {"n": n, "wins": wins, "hit": wins / n, "real_mult": real,
            "real_dd": real_dd, "per_day": per_day, "n_stopped": n_stop,
            "avg_ask": sum(s["ask"] for s in signals) / n,
            "by_league": {k: list(v) for k, v in byl.items()},
            "signals": signals}


def report_text(st: dict) -> str:
    faixa = f"{BAND_LO:.3f}–{BAND_HI:.3f}"
    if st["n"] == 0:
        return ("⚽ <b>Ceifa Futebol in-play — observação</b>\n"
                f"Nenhuma entrada na banda {faixa} resolvida ainda. "
                "Coletando ao vivo; o relatório encorpa nos próximos dias.")
    real = st["real_mult"]
    per = st["per_day"]
    retm = sum(d["ret"] for d in per) / len(per) if per else 0.0
    ddm = sum(d["dd"] for d in per) / len(per) if per else 0.0
    return "\n".join([
        "⚽ <b>Ceifa Futebol in-play — observação (nossos snapshots)</b>",
        f"Comprar o lado quase-certo na banda <b>{faixa}</b> DURANTE o jogo, "
        f"no bestAsk · {len(per)} dia(s) com apostas",
        f"• <b>Testes:</b> {st['n']} · <b>Assertividade:</b> "
        f"{st['hit']:.1%} ({st['wins']}/{st['n']})",
        f"• <b>Rendimento total (sem alavancar):</b> R$100 → "
        f"<b>R${real * 100:.2f}</b> ({(real - 1) * 100:+.1f}%)",
        f"• <b>Retorno diário médio:</b> {retm * 100:+.2f}%",
        f"• <b>Drawdown diário médio:</b> {ddm:.1%} (máximo {st['real_dd']:.1%})",
        f"• <b>Ask médio de entrada:</b> {st['avg_ask']:.3f} "
        "(preço real de compra)",
        _stops_line(st),
        "<i>Observação, sem apostar. Compra no bestAsk quando o lado entra na "
        "banda in-play e segura até liquidar. Stop fiel: sai a −15% se persistir "
        "na rodada seguinte (saída pelo preço dela). Virada sem stop = perda "
        "total. 10% do capital por aposta, compõe dia a dia.</i>",
    ])


def _stops_line(st: dict) -> str:
    n_stop = st.get("n_stopped", 0)
    perdas = [1 - s["exit"] / s["ask"] for s in st.get("signals", [])
              if s.get("stopped") and s.get("exit") and s.get("ask")]
    viradas = sum(1 for s in st.get("signals", [])
                  if not s.get("stopped") and not s.get("won"))
    if n_stop and perdas:
        media = sum(perdas) / len(perdas)
        return (f"• {n_stop} stop(s) · perda real média −{media:.1%} · "
                f"{viradas} virada(s) sem stop (perda total)")
    return f"• {n_stop} stop(s) · {viradas} virada(s) sem stop (perda total)"
