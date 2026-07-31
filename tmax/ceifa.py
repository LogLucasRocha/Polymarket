"""Backtest da estratégia Ceifa SOBRE OS NOSSOS SNAPSHOTS (dados/), não sobre o
arquivo reconstruído de APIs.

Regra (decisão do Lucas, 15/07): a entrada é SÓ em H-1 — a hora local anterior
ao pico previsto pelo modelo (H = pico_hora da base previsao). Nessa hora, se o
preço do NÃO está na banda (CEIFA_PRICE_MIN, CEIFA_PRICE_MAX), é uma entrada.
Perto do pico há pouca incerteza — é onde o mercado quase-certo é confiável.
Antes de entrar, dois vetos usam apenas o snapshot da H-1: ensemble largo; ou
desvio bruto >= +1°C OU nowcast >= +1°C, com a faixa vendida dentro da região
mediana−0,5°C a P90+0,5°C. Quando a máxima está em platô há pelo menos 2h, o
limite inferior desce até a temperatura já observada.

Resolução pela convergência do preço: o NÃO venceu se o preço do NÃO no fim do
dia foi para ~1,0. Stop: se depois da entrada o preço do NÃO cair
STOP_EXIT_FRAC abaixo da entrada, sai a −STOP_EXIT_FRAC (alerta a −10%, saída a
−15% pelo delay de reação).
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import re
from bisect import bisect_right
from collections import defaultdict
from functools import lru_cache

import pandas as pd

from . import config

ARCHIVE = config.ROOT / "dados"
BACKTEST_ARCHIVE = config.ROOT / "backtest_data"
STAKE_FRAC = 0.10


def normalize_market_price(value) -> float | None:
    """Remove somente o ruído binário antes de comparar os limites.

    Valores vindos de subtrações em ponto flutuante podem transformar 0,950
    em 0,9500000000000001 e atravessar indevidamente o limite exclusivo. Seis
    casas preservam preços válidos subcentavo, como 0,9505.
    """
    if value is None or pd.isna(value):
        return None
    return round(float(value), 6)


def is_ceifa_price(value) -> bool:
    """Preço normalizado está estritamente dentro da faixa ativa da Ceifa?"""
    price = normalize_market_price(value)
    return (
        price is not None
        and config.CEIFA_PRICE_MIN < price < config.CEIFA_PRICE_MAX
    )


def _load(base: str, archive=ARCHIVE) -> pd.DataFrame:
    root = archive / base
    files = sorted(root.rglob("*.parquet")) if root.exists() else []
    if not files:
        return pd.DataFrame()
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df.sort_values("ts")


def _tz(icao: str):
    """Fuso da estação ativa ou de um grupo auxiliar — senão UTC."""
    if icao in config.STATIONS:
        return config.STATIONS[icao].tz
    if icao in config.STATIONS_FAHRENHEIT:
        return config.STATIONS_FAHRENHEIT[icao].tz
    if icao in getattr(config, "STATIONS_OBSERVE", {}):
        return config.STATIONS_OBSERVE[icao].tz
    return dt.timezone.utc


def spread_norm_map(archive=ARCHIVE) -> dict:
    """Spread NORMAL (mediana histórica de teto_ens − mediana) por estação, a
    partir do lago de dados. É a base do filtro relativo — usado tanto no
    backtest quanto no alerta ao vivo, pra os dois nunca divergirem."""
    prev = _load("previsao", archive)
    if prev.empty:
        return {}
    ps = prev.dropna(subset=["teto_ens", "mediana"]).copy()
    ps["spread"] = ps["teto_ens"] - ps["mediana"]
    return ps.groupby("icao")["spread"].median().to_dict()


def is_uncertain(icao, spread, spread_norm) -> bool:
    """Dia perigoso? Spread do ensemble alto na H-1 — no ABSOLUTO
    (>= CEIFA_SPREAD_ABS) OU RELATIVO ao normal da estação
    (>= CEIFA_SPREAD_REL × mediana histórica). É onde o NÃO estoura pra zero
    (Istambul 34°C). Fonte única da regra: backtest e ao vivo chamam isto."""
    if spread is None or not config.CEIFA_SPREAD_FILTER:
        return False
    if spread >= config.CEIFA_SPREAD_ABS:
        return True
    norm = spread_norm.get(icao)
    return norm is not None and norm > 0 and spread >= config.CEIFA_SPREAD_REL * norm


def _station_unit(icao: str) -> str:
    """Unidade do contrato; as previsões armazenadas permanecem em °C."""
    for stations in (config.STATIONS, config.STATIONS_FAHRENHEIT,
                     getattr(config, "STATIONS_OBSERVE", {})):
        station = stations.get(icao)
        if station is not None:
            return station.unit
    return "C"


def target_temperature_c(icao: str, faixa) -> float | None:
    """Extrai o primeiro limite da faixa do mercado e normaliza para °C."""
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(faixa or ""))
    if not match:
        return None
    value = float(match.group(0).replace(",", "."))
    return (value - 32.0) * 5.0 / 9.0 if _station_unit(icao) == "F" else value


def plateau_temperature(obs: list[dict], min_hours: float | None = None) -> float | None:
    """Temperatura do platô atual quando ele coincide com a máxima observada.

    Um platô exige a mesma leitura por pelo menos duas horas. Se a temperatura
    já estiver caindo depois do pico, não usamos a máxima antiga como piso.
    """
    min_hours = (config.CEIFA_PLATEAU_HOURS if min_hours is None
                 else float(min_hours))
    if len(obs) < 2:
        return None
    ordered = sorted(obs, key=lambda item: item["time"])
    last = ordered[-1]
    start = last["time"]
    for item in reversed(ordered):
        if item["temp"] != last["temp"]:
            break
        start = item["time"]
    hours = (last["time"] - start).total_seconds() / 3600.0
    observed_max = max(item["temp"] for item in ordered)
    if hours >= min_hours and last["temp"] == observed_max:
        return float(last["temp"])
    return None


@lru_cache(maxsize=4096)
def _archived_observations(icao: str, day: str) -> tuple[tuple[dt.datetime, float], ...]:
    """METARs históricos do backtest, usados em snapshots sem plateau_temp."""
    path = BACKTEST_ARCHIVE / icao / f"{day}.json.gz"
    if not path.exists():
        return ()
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            raw = json.load(handle).get("obs", [])
        return tuple((dt.datetime.fromisoformat(str(ts)).replace(tzinfo=_tz(icao)),
                      float(temp)) for ts, temp in raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ()


def reconstructed_plateau_temperature(icao: str, day: str, snapshot_ts) -> float | None:
    """Reconstrói o platô na H-1 para snapshots gravados antes desse campo."""
    if snapshot_ts is None:
        return None
    ts = pd.Timestamp(snapshot_ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    cutoff = ts.tz_convert(_tz(icao)).to_pydatetime()
    obs = [{"time": when, "temp": temp}
           for when, temp in _archived_observations(icao, str(day))
           if when <= cutoff]
    return plateau_temperature(obs)


def reconstructed_observed_deviation(icao: str, snapshot_ts,
                                     nowcast_shift) -> float | None:
    """Limite inferior conservador do desvio bruto em snapshots antigos.

    Antes de 26/07 o lago guardava apenas o shift já amortecido. O peso real
    usa a hora do último METAR, que nunca é posterior à hora do snapshot; usar
    a hora do snapshot produz o maior peso possível e, portanto, o menor
    desvio bruto compatível com aquele shift.
    """
    if nowcast_shift is None or pd.isna(nowcast_shift) or snapshot_ts is None:
        return None
    ts = pd.Timestamp(snapshot_ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    local_hour = ts.tz_convert(_tz(icao)).hour
    hour_weight = min(max((local_hour - 6) / 6.0, 0.25), 1.0)
    return float(nowcast_shift) / (config.NOWCAST_DAMPING * hour_weight)


def is_warm_target_risk(icao: str, faixa, nowcast_shift, mediana, p90,
                        observed_deviation=None, plateau_temp=None) -> bool:
    """Veta faixa plausível quando desvio bruto OU shift chega a +1°C.

    A regra usa somente informações disponíveis na H-1. ``faixa`` pode estar
    em °C ou °F; os indicadores meteorológicos são sempre armazenados em °C.
    """
    if not config.CEIFA_NOWCAST_FILTER:
        return False
    if any(v is None or pd.isna(v) for v in (mediana, p90)):
        return False
    target = target_temperature_c(icao, faixa)
    if target is None:
        return False
    margin = config.CEIFA_TARGET_MARGIN
    lower = float(mediana) - margin
    if plateau_temp is not None and not pd.isna(plateau_temp):
        lower = min(lower, float(plateau_temp))
    target_is_plausible = lower <= target <= float(p90) + margin
    shift_hot = (nowcast_shift is not None and not pd.isna(nowcast_shift)
                 and float(nowcast_shift) >= config.CEIFA_NOWCAST_SHIFT_MIN)
    observed_hot = (
        observed_deviation is not None and not pd.isna(observed_deviation)
        and float(observed_deviation) >= config.CEIFA_OBS_DEVIATION_MIN
    )
    return target_is_plausible and (observed_hot or shift_hot)


def simulate(log=lambda m: None, icaos=None, archive=ARCHIVE,
             warm_target_filter=True) -> dict:
    """Roda a Ceifa (entrada em H-1) nos snapshots e devolve estatísticas no
    formato que backtest.ceifa_report_text espera.

    icaos: se dado, restringe a análise a esse conjunto de estações (usado
    também pelos estudos separados de temperatura mínima).
    archive: raiz do lago de dados (padrão dados/ = máxima; dados_low/ = mínima).
    warm_target_filter: desliga o veto de nowcast quente nos relatórios de
    mínima, onde a direção de risco é diferente.
    A base 'previsao' guarda a hora do extremo em pico_hora — para o Lowest é a
    hora mais FRIA, então a entrada em H-1 sai natural sem mudar este código.
    """
    mkt = _load("mercado", archive)
    prev = _load("previsao", archive)
    if icaos is not None:
        icaos = set(icaos)
        mkt = mkt[mkt["icao"].isin(icaos)] if not mkt.empty else mkt
        prev = prev[prev["icao"].isin(icaos)] if not prev.empty else prev
    if mkt.empty or prev.empty:
        log("ceifa (snapshots): sem dados capturados suficientes ainda.")
        return {"n": 0, "days": 0, "signals": []}

    # H (hora do pico previsto) por cidade-dia = moda da pico_hora
    Hs = (prev.dropna(subset=["pico_hora"]).groupby(["icao", "dia"])["pico_hora"]
             .agg(lambda s: int(s.mode().iat[0])).to_dict())
    # ``groupby.apply`` devolve DataFrame (em vez de Series) quando existe uma
    # única cidade em algumas versões do pandas; transform mantém o índice e o
    # formato estáveis tanto no relatório completo quanto em recortes locais.
    mkt["hloc"] = mkt.groupby("icao")["ts"].transform(
        lambda s: s.dt.tz_convert(_tz(s.name)).dt.hour)

    # Incerteza do ensemble = teto_ens − mediana. Guardamos as séries por
    # (icao, dia) para pegar o spread NA H-1, e a mediana por cidade para o
    # filtro relativo (spread alto RELATIVO ao normal daquela estação).
    # O lago de temperatura mínima guarda somente a hora do extremo; nesse
    # caso não há métricas de ensemble e os vetos meteorológicos são pulados.
    forecast_cols = {"teto_ens", "mediana"}
    if forecast_cols.issubset(prev.columns):
        ps = prev.dropna(subset=list(forecast_cols)).copy()
        ps["spread"] = ps["teto_ens"] - ps["mediana"]
        spread_norm = ps.groupby("icao")["spread"].median().to_dict()
        spread_by = {
            k: v.sort_values("ts") for k, v in ps.groupby(["icao", "dia"])
        }
    else:
        spread_norm = {}
        spread_by = {}

    def previsao_na_entrada(icao, dia, e_ts):
        d = spread_by.get((icao, dia))
        if d is None:
            return None
        ate = d[d["ts"] <= e_ts]
        return ate.iloc[-1] if len(ate) else None

    pmin, pmax = config.CEIFA_PRICE_MIN, config.CEIFA_PRICE_MAX
    signals = []
    n_filtrado = 0
    n_filtrado_spread = 0
    n_filtrado_nowcast = 0
    n_filtrado_plateau = 0
    n_filtrado_100c = 0
    n_filtrado_0c = 0
    for (icao, dia, faixa), g in mkt.groupby(["icao", "dia", "faixa"]):
        H = Hs.get((icao, dia))
        if H is None:
            continue
        h1 = g[g["hloc"] == ((H - 1) % 24)]      # snapshots na hora H-1
        if h1.empty:
            continue
        e = h1.iloc[-1]                            # último da hora H-1
        checked = e.get("livro_consultado")
        checked = (checked is not None and not pd.isna(checked)
                   and bool(checked))
        ask = e.get("ask_nao")
        if checked and (ask is None or pd.isna(ask)):
            continue                              # não havia oferta para comprar
        entry = normalize_market_price(
            ask if checked else e["preco_nao"])
        if entry is None or not (pmin < entry < pmax):
            continue
        nao_final = float(g["preco_nao"].iloc[-1])
        if not (nao_final > 0.90 or nao_final < 0.10):
            continue                              # dia ainda em aberto
        # FILTRO DE INCERTEZA (no lugar do stop): não entra em dia de ensemble
        # largo na H-1 — é onde o estouro (NÃO → zero) acontece.
        forecast = previsao_na_entrada(icao, dia, e["ts"])
        spr = float(forecast["spread"]) if forecast is not None else None
        if is_uncertain(icao, spr, spread_norm):
            n_filtrado += 1
            n_filtrado_spread += 1
            if nao_final > 0.5:
                n_filtrado_100c += 1
            else:
                n_filtrado_0c += 1
            continue
        plateau = forecast.get("plateau_temp") if forecast is not None else None
        if forecast is not None and (plateau is None or pd.isna(plateau)):
            plateau = reconstructed_plateau_temperature(
                icao, dia, forecast.get("ts"))
        observed_deviation = None
        if forecast is not None:
            observed_deviation = (
                forecast.get("nowcast_offset")
                if not pd.isna(forecast.get("nowcast_offset"))
                else reconstructed_observed_deviation(
                    icao, forecast.get("ts"), forecast.get("nowcast_shift")))
        warm_without_plateau = (
            warm_target_filter and forecast is not None
            and is_warm_target_risk(
                icao, faixa, forecast.get("nowcast_shift"),
                forecast.get("mediana"), forecast.get("p90"),
                observed_deviation=observed_deviation))
        warm_with_plateau = (
            warm_target_filter and forecast is not None
            and is_warm_target_risk(
                icao, faixa, forecast.get("nowcast_shift"),
                forecast.get("mediana"), forecast.get("p90"),
                observed_deviation=observed_deviation,
                plateau_temp=plateau))
        if warm_with_plateau:
            n_filtrado += 1
            n_filtrado_nowcast += 1
            if not warm_without_plateau:
                n_filtrado_plateau += 1
            if nao_final > 0.5:
                n_filtrado_100c += 1
            else:
                n_filtrado_0c += 1
            continue
        # Sem stop: segura até liquidar. Vitória se o NÃO foi para ~1,0.
        won = nao_final > 0.5
        signals.append({"icao": icao, "day": dia, "faixa": faixa,
                        "ts": e["ts"], "price": entry, "won": won,
                        "stopped": False, "loss_frac": None,
                        "spread": spr})
    log(f"ceifa (H-1, filtro de incerteza): {len(signals)} apostas · "
        f"{n_filtrado} cortadas por incerteza.")
    st = _stats(signals, mkt["dia"].nunique())
    st["n_filtrado"] = n_filtrado
    st["n_filtrado_spread"] = n_filtrado_spread
    st["n_filtrado_nowcast"] = n_filtrado_nowcast
    st["n_filtrado_plateau"] = n_filtrado_plateau
    st["n_filtrado_100c"] = n_filtrado_100c
    st["n_filtrado_0c"] = n_filtrado_0c
    return st


def simulate_repeated(log=lambda m: None, icaos=None, archive=ARCHIVE,
                      warm_target_filter=True, interval_minutes: int = 5,
                      stake_frac: float = 0.01) -> dict:
    """Ceifa parcelada: uma stake relativa a cada rodada elegível da H-1.

    A stake de cada parcela é ``stake_frac`` do caixa ainda livre naquele
    instante. Não há teto por contrato nem alavancagem; como a parcela diminui
    junto com o saldo disponível, o caixa nunca é esgotado matematicamente.
    Cada snapshot respeita novamente preço, livro, ensemble, nowcast e platô.
    """
    mkt = _load("mercado", archive)
    prev = _load("previsao", archive)
    if icaos is not None:
        icaos = set(icaos)
        mkt = mkt[mkt["icao"].isin(icaos)] if not mkt.empty else mkt
        prev = prev[prev["icao"].isin(icaos)] if not prev.empty else prev
    if mkt.empty or prev.empty:
        return {"n": 0, "days": 0, "signals": [],
                "stake_frac": stake_frac, "repeat_minutes": interval_minutes}

    mkt["hloc"] = mkt.groupby("icao")["ts"].transform(
        lambda series: series.dt.tz_convert(_tz(series.name)).dt.hour)
    prev_by = {}
    for key, group in prev.groupby(["icao", "dia"]):
        ordered = group.sort_values("ts")
        prev_by[key] = ([timestamp.value for timestamp in ordered["ts"]],
                        [row for _, row in ordered.iterrows()])
    if {"teto_ens", "mediana"}.issubset(prev.columns):
        ps = prev.dropna(subset=["teto_ens", "mediana"]).copy()
        ps["spread"] = ps["teto_ens"] - ps["mediana"]
        spread_norm = ps.groupby("icao")["spread"].median().to_dict()
    else:
        spread_norm = {}

    def forecast_at(icao, day, timestamp):
        lookup = prev_by.get((icao, day))
        if lookup is None:
            return None
        timestamps, rows = lookup
        index = bisect_right(timestamps, timestamp.value) - 1
        return rows[index] if index >= 0 else None

    pmin, pmax = config.CEIFA_PRICE_MIN, config.CEIFA_PRICE_MAX
    min_gap = pd.Timedelta(minutes=interval_minutes)
    signals = []
    filtered = filtered_spread = filtered_nowcast = filtered_plateau = 0
    filtered_100c = filtered_0c = 0
    for (icao, day, faixa), group in mkt.groupby(["icao", "dia", "faixa"]):
        group = group.sort_values("ts")
        final_no = float(group["preco_nao"].iloc[-1])
        if not (final_no > 0.90 or final_no < 0.10):
            continue
        last_entry_ts = None
        for _, entry_row in group.iterrows():
            forecast = forecast_at(icao, day, entry_row["ts"])
            if forecast is None or pd.isna(forecast.get("pico_hora")):
                continue
            peak = int(forecast["pico_hora"])
            if int(entry_row["hloc"]) != (peak - 1) % 24:
                continue
            if (last_entry_ts is not None
                    and entry_row["ts"] - last_entry_ts < min_gap):
                continue

            checked = entry_row.get("livro_consultado")
            checked = (checked is not None and not pd.isna(checked)
                       and bool(checked))
            ask = entry_row.get("ask_nao")
            if checked and (ask is None or pd.isna(ask)):
                continue
            price = normalize_market_price(
                ask if checked else entry_row["preco_nao"])
            if price is None or not (pmin < price < pmax):
                continue

            spread = None
            if not pd.isna(forecast.get("teto_ens")) and not pd.isna(
                    forecast.get("mediana")):
                spread = float(forecast["teto_ens"] - forecast["mediana"])
            if is_uncertain(icao, spread, spread_norm):
                filtered += 1
                filtered_spread += 1
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            plateau = forecast.get("plateau_temp")
            if plateau is None or pd.isna(plateau):
                plateau = reconstructed_plateau_temperature(
                    icao, day, forecast.get("ts"))
            observed_deviation = forecast.get("nowcast_offset")
            if observed_deviation is None or pd.isna(observed_deviation):
                observed_deviation = reconstructed_observed_deviation(
                    icao, forecast.get("ts"), forecast.get("nowcast_shift"))
            warm_without_plateau = (
                warm_target_filter and is_warm_target_risk(
                    icao, faixa, forecast.get("nowcast_shift"),
                    forecast.get("mediana"), forecast.get("p90"),
                    observed_deviation=observed_deviation))
            warm_with_plateau = (
                warm_target_filter and is_warm_target_risk(
                    icao, faixa, forecast.get("nowcast_shift"),
                    forecast.get("mediana"), forecast.get("p90"),
                    observed_deviation=observed_deviation,
                    plateau_temp=plateau))
            if warm_with_plateau:
                filtered += 1
                filtered_nowcast += 1
                if not warm_without_plateau:
                    filtered_plateau += 1
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            signals.append({
                "icao": icao, "day": day, "faixa": faixa,
                "ts": entry_row["ts"], "price": price,
                "won": final_no > 0.5, "stopped": False,
                "loss_frac": None, "spread": spread,
            })
            last_entry_ts = entry_row["ts"]

    stats = _stats_relative_available_stake(
        signals, mkt["dia"].nunique(), stake_frac)
    stats.update({
        "repeat_minutes": interval_minutes,
        "n_filtrado": filtered,
        "n_filtrado_spread": filtered_spread,
        "n_filtrado_nowcast": filtered_nowcast,
        "n_filtrado_plateau": filtered_plateau,
        "n_filtrado_100c": filtered_100c,
        "n_filtrado_0c": filtered_0c,
    })
    log(f"ceifa parcelada ({interval_minutes} min): {stats['n']} parcelas · "
        f"{filtered} oportunidades recusadas por incerteza.")
    return stats


def _stats_relative_available_stake(signals: list, days: int,
                                    stake_frac: float) -> dict:
    """Parcela cada entrada com uma fração do caixa ainda disponível."""
    by_day: dict = defaultdict(list)
    for signal in signals:
        by_day[signal["day"]].append(signal)
    capital, peak, max_drawdown = 1.0, 1.0, 0.0
    executed, per_day = [], []
    for day in sorted(by_day):
        start = capital
        available, settled = start, 0.0
        day_executed = []
        for signal in sorted(by_day[day], key=lambda item: item["ts"]):
            stake = stake_frac * available
            available -= stake
            settled += stake / signal["price"] if signal["won"] else 0.0
            placed = dict(signal, stake=stake)
            executed.append(placed)
            day_executed.append(placed)
        capital = available + settled
        peak = max(peak, capital)
        drawdown = 1.0 - capital / peak if peak else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        per_day.append({
            "day": day, "n": len(day_executed),
            "wins": sum(1 for item in day_executed if item["won"]),
            "ret": capital / start - 1.0, "cap": capital, "dd": drawdown,
            "first_stake": (day_executed[0]["stake"]
                            if day_executed else 0.0),
            "last_stake": (day_executed[-1]["stake"]
                           if day_executed else 0.0),
        })

    n = len(executed)
    wins = sum(1 for signal in executed if signal["won"])
    by_city = defaultdict(lambda: [0, 0])
    for signal in executed:
        by_city[signal["icao"]][0] += 1
        by_city[signal["icao"]][1] += int(signal["won"])
    return {
        "n": n, "days": days, "wins": wins,
        "hit": wins / n if n else 0.0,
        "avg_price": (sum(signal["price"] for signal in executed) / n
                      if n else 0.0),
        "real_mult": capital, "real_dd": max_drawdown,
        "per_day": per_day, "by_city": dict(by_city), "signals": executed,
        "stake_frac": stake_frac, "n_capital_limited": 0,
        "n_stopped": 0,
    }


def _stats(signals: list, days: int) -> dict:
    n = len(signals)
    if n == 0:
        return {"n": 0, "days": days, "signals": []}
    wins = sum(1 for s in signals if s["won"])
    n_stopped = sum(1 for s in signals if s["stopped"])

    # Modelo de banca (pedido do Lucas, 16/07): a cada dia as apostas entram em
    # ORDEM DE TEMPO; cada uma aposta STAKE_FRAC (10%) do capital AINDA
    # DISPONÍVEL — o dinheiro fica TRAVADO na aposta. Só no FECHAMENTO do dia o
    # mercado liquida e a banca se recompõe (o que sobrou + o que as apostas
    # pagaram); esse total vira a base do dia seguinte. Sem alavancagem.
    by_day: dict = defaultdict(list)
    for s in signals:
        by_day[s["day"]].append(s)
    real, rpeak, real_dd = 1.0, 1.0, 0.0
    per_day = []
    for day in sorted(by_day):
        bets = sorted(by_day[day], key=lambda x: x.get("ts"))
        disponivel = real
        liquidado = 0.0
        for s in bets:
            stake = STAKE_FRAC * disponivel
            disponivel -= stake                 # trava até o dia fechar
            if s["stopped"]:
                # perda REAL do stop: saída pelo preço do snapshot +1
                lf = s.get("loss_frac")
                lf = config.STOP_EXIT_FRAC if lf is None else lf
                liquidado += stake * (1 - lf)
            elif s["won"]:
                liquidado += stake / s["price"]
            # NÃO perdeu inteiro → 0
        novo = disponivel + liquidado           # liquida no fechamento
        ret = (novo / real - 1.0) if real else 0.0
        real = novo
        rpeak = max(rpeak, real)
        dd_after = (1 - real / rpeak) if rpeak else 0.0
        real_dd = max(real_dd, dd_after)
        per_day.append({"day": day, "n": len(bets),
                        "wins": sum(1 for x in bets if x["won"]),
                        "ret": ret, "cap": real, "dd": dd_after})

    by = defaultdict(lambda: [0, 0])
    for s in signals:
        by[s["icao"]][0] += 1
        by[s["icao"]][1] += 1 if s["won"] else 0
    return {"n": n, "days": days, "wins": wins, "hit": wins / n,
            "n_stopped": n_stopped,
            "avg_price": sum(s["price"] for s in signals) / n,
            "real_mult": real, "real_dd": real_dd, "per_day": per_day,
            "by_city": {k: [v[0], v[1]] for k, v in by.items()},
            "signals": signals}
