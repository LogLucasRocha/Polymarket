"""Captura ao vivo do mercado de MÍNIMA (lowest temperature) — modo OBSERVAÇÃO.

Réplica do que fazemos no Highest, mas para o mercado de temperatura MÍNIMA das
cidades que a Polymarket cobre. A cada rodada, para cada cidade com mercado de
mínima, grava mercado/livro, distribuição prevista, METAR e a tabela completa
que originou o nowcast. A hora mais fria continua em ``pico_hora`` para a Ceifa
calcular H-1 sem duplicar código.

Grava no lago ``dados_low/`` (mercado/, previsao/, nowcast/ e metar/), com o
dia UTC corrente no buffer do Actions e consolidação dos dias fechados. NÃO
aposta e não ativa filtros novos por si só.

Roda no .github/workflows/lowtemp_live.yml. Uso: python -m lowtemp.capture
"""
from __future__ import annotations

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

import datetime as dt
import json
import sys
from pathlib import Path

from tmax import config, distribution, pipeline
from tmax import polymarket as pm

BUF_DIR = config.ROOT / "data_low"      # buffer (cache do Actions)
ARCH_DIR = config.ROOT / "dados_low"    # parquet por dia (commitado)


def lowest_slug(icao: str, d: dt.date):
    city = pm._ICAO_TO_CITY_SLUG.get(icao)
    if not city:
        return None
    return (f"lowest-temperature-in-{city}-on-"
            f"{pm._MONTHS[d.month - 1]}-{d.day}-{d.year}")


def _cold_hour(ctx):
    """Hora local do mínimo do dia (análoga ao pico_hora do Highest)."""
    times, _p10, p50, _p90, _raw = pipeline.hourly_percentiles(
        ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        ctx["shift"], ctx["now"], days={ctx["d0"]})
    valid = [(t, v) for t, v in zip(times, p50) if v is not None]
    return min(valid, key=lambda tv: tv[1])[0].hour if valid else None


def _minimum_locked(obs_today: list[dict], obs_min: float | None) -> bool:
    """O vale já passou quando as últimas horas ficaram acima da mínima."""
    if obs_min is None:
        return False
    hourly_min: dict[dt.datetime, float] = {}
    for observation in obs_today:
        hour = observation["time"].replace(minute=0, second=0, microsecond=0)
        hourly_min[hour] = min(
            hourly_min.get(hour, observation["temp"]), observation["temp"])
    last_hours = sorted(hourly_min)[-config.TMAX_LOCK_HOURS:]
    return (len(last_hours) >= config.TMAX_LOCK_HOURS
            and all(hourly_min[hour] > obs_min for hour in last_hours))


def _forecast_snapshot(ctx: dict, icao: str, day: dt.date,
                       captured_at: dt.datetime) -> dict | None:
    """Estado completo da previsão da mínima disponível nesta rodada."""
    observations = ctx.get("obs_today") or []
    observed_min = min((item["temp"] for item in observations), default=None)
    members = distribution.member_minima_for_day(
        day, ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        now=ctx["now"], shift=ctx["shift"], obs_min=observed_min)
    summary = distribution.empirical_extreme_summary(members, "tmin")
    if not summary:
        return None

    raw_members = distribution.member_minima_for_day(
        day, ctx["ens"]["time"], ctx["ens"]["members"], bias={})
    raw_summary = distribution.empirical_extreme_summary(raw_members, "tmin") or {}
    nowcast = ctx.get("nowcast") or {}
    latest = ctx.get("latest_metar") or {}
    median = summary["median"]
    return {
        "ts_utc": captured_at.isoformat(), "icao": icao,
        "dia": day.isoformat(),
        "media": round(summary["mean"], 2),
        "mediana": round(median, 2),
        "piso_ens": round(summary["min"], 2),
        "teto_ens": round(summary["max"], 2),
        "p10": round(summary["p10"], 2),
        "p90": round(summary["p90"], 2),
        # Para mínima, o risco relevante fica na cauda fria (mediana - piso).
        # Guardamos também a amplitude total para permitir outros estudos.
        "spread_frio": round(median - summary["min"], 2),
        "spread_ens": round(summary["max"] - summary["min"], 2),
        "n_membros": summary["n_members"],
        "mediana_bruta": (round(raw_summary["median"], 2)
                           if raw_summary else None),
        "p10_bruto": (round(raw_summary["p10"], 2)
                       if raw_summary else None),
        "p90_bruto": (round(raw_summary["p90"], 2)
                       if raw_summary else None),
        "bias_source": "tmax_station_lookback",
        "schema_version": 2,
        "pico_hora": _cold_hour(ctx),
        "obs_min": observed_min,
        "nowcast_shift": ctx.get("shift"),
        "nowcast_offset": nowcast.get("offset"),
        "nowcast_n_hours": nowcast.get("n_hours"),
        "nowcast_hour_weight": nowcast.get("hour_weight"),
        "travada": _minimum_locked(observations, observed_min),
        "latest_metar_time": latest.get("time"),
        "latest_metar_temp_c": latest.get("temp"),
        "latest_metar_raw": latest.get("raw"),
    }


def _nowcast_rows(ctx: dict, icao: str, day: dt.date,
                  captured_at: dt.datetime) -> list[dict]:
    nowcast = ctx.get("nowcast") or {}
    rows = []
    for order, component in enumerate(nowcast.get("components") or [], start=1):
        rows.append({
            "ts_utc": captured_at.isoformat(), "icao": icao,
            "dia": day.isoformat(),
            "nowcast_offset": nowcast.get("offset"),
            "nowcast_shift": nowcast.get("shift"),
            "nowcast_hour_weight": nowcast.get("hour_weight"),
            "nowcast_damping": nowcast.get("damping"),
            "nowcast_n_hours": nowcast.get("n_hours"),
            "component_order": order,
            "observation_time": component.get("observation_time"),
            "ensemble_hour": component.get("ensemble_hour"),
            "observed_temp_c": component.get("observed_temp_c"),
            "ensemble_mean_c": component.get("ensemble_mean_c"),
            "deviation_c": component.get("deviation_c"),
            "member_count": component.get("member_count"),
            "raw_metar": component.get("raw_metar"),
        })
    return rows


def _metar_rows(ctx: dict, icao: str, day: dt.date,
                captured_at: dt.datetime) -> list[dict]:
    return [{
        "ts_utc": captured_at.isoformat(), "icao": icao,
        "dia": day.isoformat(),
        "observation_time": observation.get("time"),
        "temp_c": observation.get("temp"),
        "raw_metar": observation.get("raw"),
    } for observation in (ctx.get("obs_today") or [])]


def coletar():
    now = dt.datetime.now(dt.timezone.utc)
    stations = {**config.STATIONS, **config.STATIONS_FAHRENHEIT}
    mkt_recs, fc_recs, nowcast_recs, metar_recs, achou = [], [], [], [], 0
    for icao, st in stations.items():
        d0 = dt.datetime.now(st.tz).date()
        slug = lowest_slug(icao, d0)
        if not slug:
            continue
        try:
            ev = pm.fetch_event(slug)
            pm.attach_best_asks(ev)
        except Exception:  # noqa: BLE001
            continue
        rows = pm.odds_rows(ev) if ev.get("rows") else []
        if not rows:
            continue                       # cidade sem mercado de mínima hoje
        achou += 1
        try:
            ctx = pipeline.build_context(st, log=lambda _m: None)
            forecast = _forecast_snapshot(ctx, icao, d0, now)
            nowcast_recs.extend(_nowcast_rows(ctx, icao, d0, now))
            metar_recs.extend(_metar_rows(ctx, icao, d0, now))
        except Exception as exc:  # noqa: BLE001
            print(f"[{icao}] contexto falhou: {exc}", file=sys.stderr)
            forecast = None
        tsiso, dia = now.isoformat(), d0.isoformat()
        for r in rows:
            if r["no"] is None:
                continue
            mkt_recs.append({"ts_utc": tsiso, "icao": icao, "dia": dia,
                             "faixa": r["label"], "preco_sim": r["yes"],
                             "preco_nao": r["no"],
                             "ask_sim": r.get("yes_ask"),
                             "ask_sim_volume": r.get("yes_ask_size"),
                             "ask_nao": r.get("no_ask"),
                             "ask_nao_volume": r.get("no_ask_size"),
                             "livro_consultado": bool(r.get("book_checked"))})
        if forecast is not None:
            fc_recs.append(forecast)
    print(f"cidades com mínima: {achou} · linhas mercado: {len(mkt_recs)} · "
          f"previsão: {len(fc_recs)} · nowcast: {len(nowcast_recs)} · "
          f"METAR: {len(metar_recs)}")
    return mkt_recs, fc_recs, nowcast_recs, metar_recs


def salvar(mkt: list, fc: list, nowcast: list | None = None,
           metar: list | None = None) -> None:
    import pandas as pd

    BUF_DIR.mkdir(exist_ok=True)
    bases = {"mercado": mkt, "previsao": fc,
             "nowcast": nowcast or [], "metar": metar or []}
    for name, recs in bases.items():
        if recs:
            with open(BUF_DIR / f"{name}.jsonl", "a", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, default=str) + "\n")
    hoje = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    keys = {
        "mercado": ["ts_utc", "icao", "dia", "faixa"],
        "previsao": ["ts_utc", "icao", "dia"],
        "nowcast": ["ts_utc", "icao", "dia", "observation_time"],
        "metar": ["icao", "dia", "observation_time"],
    }
    for name in bases:
        buf = BUF_DIR / f"{name}.jsonl"
        if not buf.exists():
            continue
        with buf.open(encoding="utf-8") as handle:
            linhas = [json.loads(line) for line in handle if line.strip()]
        if not linhas:
            continue
        df = pd.DataFrame(linhas)
        df["utc"] = df["ts_utc"].str[:10]
        dedup = keys[name]
        (ARCH_DIR / name).mkdir(parents=True, exist_ok=True)
        for utc, g in df.groupby("utc"):
            if utc >= hoje:
                continue                   # dia UTC corrente fica no buffer
            fp = ARCH_DIR / name / f"{utc}.parquet"
            g = g.drop(columns=["utc"])
            if fp.exists():
                g = pd.concat([pd.read_parquet(fp), g], ignore_index=True)
            g = g.drop_duplicates(dedup)
            g.to_parquet(fp, index=False)
            print(f"arquivado {name}/{utc}: {len(g)} linhas")
        resto = df[df["utc"] >= hoje].drop(columns=["utc"])
        with open(buf, "w", encoding="utf-8") as f:
            for _, r in resto.iterrows():
                f.write(json.dumps(r.to_dict(), default=str) + "\n")


def main() -> int:
    mkt, fc, nowcast, metar = coletar()
    try:
        salvar(mkt, fc, nowcast, metar)
    except Exception as exc:  # noqa: BLE001 — captura é acessória
        print(f"erro ao salvar: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
