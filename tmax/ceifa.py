"""Backtest da estratégia Ceifa SOBRE OS NOSSOS SNAPSHOTS (dados/), não sobre o
arquivo reconstruído de APIs.

Regra (decisão do Lucas, 15/07): a entrada é SÓ em H-1 — a hora local anterior
ao pico previsto pelo modelo (H = pico_hora da base previsao). Nessa hora, se o
preço do NÃO está na banda (CEIFA_PRICE_MIN, CEIFA_PRICE_MAX), é uma entrada.
Perto do pico há pouca incerteza — é onde o mercado quase-certo é confiável.
Antes de entrar, dois vetos usam apenas o snapshot da H-1: ensemble largo; ou
desvio bruto >= +1°C OU nowcast >= +1°C, com a faixa vendida dentro da região
mediana−0,5°C a P90+0,5°C. Quando a máxima está em platô há pelo menos 2h, o
limite inferior desce até a temperatura já observada. Também não vende um
bucket que contenha o P90 ou o membro mais quente do ensemble.

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
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pa_dataset
import pyarrow.parquet as pa_parquet

from . import config

ARCHIVE = config.ROOT / "dados"
BACKTEST_ARCHIVE = config.ROOT / "backtest_data"
STAKE_FRAC = 0.10
_ROOT_FRAME_CACHE: dict[tuple[str, str], tuple[tuple, pd.DataFrame]] = {}
_BR_TZ = ZoneInfo("America/Sao_Paulo")


def _brasilia_day(ts) -> str | None:
    """Data (ISO) no fuso de Brasília do instante da entrada.

    A ``day`` do sinal é a data-alvo do mercado (calendário da cidade); para o
    gráfico "Retorno de cada dia" o Lucas quer o dia em que ELE operou, no fuso
    de Brasília. Convertemos o ``ts`` real (UTC) para America/Sao_Paulo."""
    try:
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        return stamp.tz_convert(_BR_TZ).date().isoformat()
    except Exception:  # noqa: BLE001 — ts ausente/estranho: cai no day do mercado
        return None


def _day_bucket(signal: dict) -> str:
    """Chave do dia de um sinal: dia de Brasília (Ceifa) ou data-alvo (mercado).

    Fonte única para agrupar E fatiar por janela — se divergirem, um dia de
    fronteira ganha/perde parcelas ao trocar de janela."""
    return str(signal.get("day_br") or signal["day"])


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


def _parquet_schema(files: list[Path]) -> pa.Schema:
    """Une schemas diarios, normalizando colunas legadas equivalentes."""
    fields: dict[str, list[pa.DataType]] = defaultdict(list)
    order = []
    for path in files:
        for field in pa_parquet.read_schema(path):
            if field.name not in fields:
                order.append(field.name)
            fields[field.name].append(field.type)
    unified = []
    for name in order:
        types = [kind for kind in fields[name] if not pa.types.is_null(kind)]
        if name == "livro_consultado":
            # Arquivos antigos guardavam a ausencia do campo como NaN. Em
            # float, bool vira 0/1 e NaN continua ausente; converter NaN
            # diretamente para bool o transformaria incorretamente em True.
            kind = pa.float64()
        elif not types:
            kind = pa.large_string()
        elif all(pa.types.is_string(kind) or pa.types.is_large_string(kind)
                 for kind in types):
            kind = pa.large_string()
        else:
            kind = types[0]
        unified.append(pa.field(name, kind))
    return pa.schema(unified)


def _load_root(root: Path, base: str) -> pd.DataFrame:
    """Le uma raiz Parquet e a reutiliza enquanto seus arquivos nao mudarem."""
    if not root.exists():
        return pd.DataFrame()
    files = sorted(root.rglob("*.parquet"))
    signature = tuple(
        (str(path), stat.st_size, stat.st_mtime_ns)
        for path in files for stat in (path.stat(),))
    cache_key = (str(root.resolve()), base)
    cached = _ROOT_FRAME_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    if files:
        try:
            schema = _parquet_schema(files)
            table = pa_dataset.dataset(
                [str(path) for path in files], format="parquet",
                schema=schema).to_table()
            frame = table.to_pandas()
        except (pa.ArrowException, OSError, ValueError):
            frame = pd.concat(
                (pd.read_parquet(path) for path in files), ignore_index=True)
    else:
        frame = pd.DataFrame()
    _ROOT_FRAME_CACHE[cache_key] = (signature, frame)
    return frame


def _load(base: str, archive=ARCHIVE) -> pd.DataFrame:
    archive = Path(archive)
    live_archive = archive.with_name(f"{archive.name}_live")
    frames = [frame for root in (archive / base, live_archive / base)
              if not (frame := _load_root(root, base)).empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    keys = {
        "mercado": ["ts_utc", "icao", "dia", "faixa"],
        "previsao": ["ts_utc", "icao", "dia"],
        "nowcast": ["ts_utc", "icao", "dia", "observation_time"],
    }.get(base)
    if keys:
        df = df.drop_duplicates(
            subset=[key for key in keys if key in df.columns], keep="last")
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df.sort_values("ts")


def _resolved_price(group: pd.DataFrame, column: str) -> float | None:
    """Preço final, exigindo 0/1 real quando a linha veio do pacote ao vivo."""
    value = normalize_market_price(group[column].iloc[-1])
    if value is None:
        return None
    live = group.iloc[-1].get("snapshot_live", False)
    live = False if live is None or pd.isna(live) else bool(live)
    if live:
        # Corte do ao vivo afrouxado para 1¢ (decisão do Lucas, 07/08): um NÃO
        # a 0,6¢ está resolvido na prática, mas o corte antigo (0,1¢) o deixava
        # pendente — o dashboard demorava a refletir a perda.
        return value if value >= 0.99 or value <= 0.01 else None
    return value if value > 0.90 or value < 0.10 else None


def _resolved_prices(frame: pd.DataFrame, column: str) -> dict[tuple, float]:
    """Resolve todos os contratos sem materializar um ``group`` por faixa.

    O dashboard antes percorria centenas de milhares de snapshots apenas para
    descobrir o ultimo preco de cada contrato. A ultima linha por chave contem
    exatamente a mesma informacao usada por :func:`_resolved_price`.
    """
    keys = ["icao", "dia", "faixa"]
    last = (frame.sort_values("ts")
            .groupby(keys, sort=False, observed=True)
            .tail(1).copy())
    values = pd.to_numeric(last[column], errors="coerce").round(6)
    if "snapshot_live" in last:
        live = last["snapshot_live"].fillna(False).astype(bool)
    else:
        live = pd.Series(False, index=last.index)
    resolved = values.notna() & (
        (live & ((values >= 0.99) | (values <= 0.01)))  # ao vivo: 1¢ (07/08)
        | (~live & ((values > 0.90) | (values < 0.10))))
    return {
        (row.icao, row.dia, row.faixa): float(value)
        for row, value in zip(last.loc[resolved, keys].itertuples(index=False),
                              values.loc[resolved])
    }


def _execution_candidates(frame: pd.DataFrame, side: str) -> pd.DataFrame:
    """Pre-filtra vetorialmente snapshots com oferta executavel da Ceifa."""
    checked = (frame["livro_consultado"].fillna(False).astype(bool)
               if "livro_consultado" in frame
               else pd.Series(False, index=frame.index))
    price_column = "preco_nao" if side == "NAO" else "preco_sim"
    ask_column = "ask_nao" if side == "NAO" else "ask_sim"
    legacy = pd.to_numeric(frame.get(price_column), errors="coerce")
    ask = pd.to_numeric(frame.get(
        ask_column, pd.Series(float("nan"), index=frame.index)),
        errors="coerce")
    execution = legacy.where(~checked, ask).round(6)
    eligible = (
        execution.gt(config.CEIFA_PRICE_MIN)
        & execution.lt(config.CEIFA_PRICE_MAX)
        & (~checked | ask.notna())
    )
    candidates = frame.loc[eligible].copy()
    candidates["_entry_price"] = execution.loc[eligible]
    return candidates


def _h1_candidates(candidates: pd.DataFrame,
                   forecasts: pd.DataFrame) -> pd.DataFrame:
    """Mantem ofertas na H-1 e anexa a previsao vigente a cada uma."""
    if candidates.empty or forecasts.empty:
        return candidates.iloc[0:0].copy()
    selected = []
    forecast_groups = {}
    for key, group in forecasts.groupby(["icao", "dia"]):
        right = group.sort_values("ts").copy()
        right["_forecast_ts"] = right["ts"]
        right = right.rename(columns={
            column: f"_forecast_{column}" for column in right.columns
            if column != "ts" and not column.startswith("_forecast_")
        })
        forecast_groups[key] = right
    indexed = candidates.assign(_candidate_index=candidates.index)
    for key, group in indexed.groupby(["icao", "dia"]):
        forecast = forecast_groups.get(key)
        if forecast is None or forecast.empty:
            continue
        aligned = pd.merge_asof(
            group.sort_values("ts"), forecast, on="ts", direction="backward")
        peak = pd.to_numeric(aligned["_forecast_pico_hora"], errors="coerce")
        valid = peak.notna() & aligned["hloc"].eq((peak - 1) % 24)
        if valid.any():
            selected.append(aligned.loc[valid])
    return (pd.concat(selected, ignore_index=True).sort_values("ts")
            if selected else candidates.iloc[0:0].copy())


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


def market_temperature_interval_c(icao: str, faixa) -> tuple[float, float] | None:
    """Converte o bucket discreto do mercado em um intervalo contínuo em °C.

    Um contrato exato de 32°C representa valores que arredondam para 32°C,
    portanto cobre [31,5; 32,5). Faixas americanas como 90–91°F recebem a
    mesma margem de meio grau antes da conversão para Celsius.
    """
    label = str(faixa or "").lower()
    raw_values = re.findall(
        r"(?<![\d.,])-?\d+(?:[.,]\d+)?", label)
    if not raw_values:
        return None
    values = [float(value.replace(",", ".")) for value in raw_values]

    lower_open = any(marker in label for marker in (
        "or lower", "or below", "or less", "ou menos", "ou abaixo"))
    upper_open = any(marker in label for marker in (
        "or higher", "or above", "or more", "ou mais", "ou acima"))
    if lower_open:
        lower, upper = float("-inf"), values[0] + 0.5
    elif upper_open:
        lower, upper = values[0] - 0.5, float("inf")
    elif len(values) >= 2:
        lower, upper = min(values[:2]) - 0.5, max(values[:2]) + 0.5
    else:
        lower, upper = values[0] - 0.5, values[0] + 0.5

    if _station_unit(icao) == "F":
        lower = ((lower - 32.0) * 5.0 / 9.0
                 if lower != float("-inf") else lower)
        upper = ((upper - 32.0) * 5.0 / 9.0
                 if upper != float("inf") else upper)
    return lower, upper


def is_ensemble_inside_market_band_risk(icao: str, faixa, p10, p90) -> bool:
    """A faixa vendida toca ou se sobrepõe ao intervalo central P10–P90?

    Predicado puro (geometria), sem consultar o liga/desliga do filtro — assim
    a tela de erros pode perguntar "este filtro, se ativado, pegaria o erro?".
    O liga/desliga (``CEIFA_ENSEMBLE_BAND_FILTER``) é aplicado em quem chama.
    """
    interval = market_temperature_interval_c(icao, faixa)
    if (interval is None or p10 is None or p90 is None
            or pd.isna(p10) or pd.isna(p90)):
        return False
    lower, upper = interval
    central_lower, central_upper = sorted((float(p10), float(p90)))
    # O toque na borda também é risco: 37,5°C deve vetar o bucket de
    # 37°C, cujo limite superior discreto é justamente 37,5°C.
    return max(lower, central_lower) <= min(upper, central_upper)


def is_lower_tail_floor_risk(icao: str, faixa, p10, piso_ens,
                             margin: float | None = None) -> bool:
    """A faixa vendida entra na cauda fria (piso do ensemble → P10)?

    Simétrico da 'cauda superior perto do teto' das máximas, para o lado frio
    das mínimas. A banda P10–P90 cobre só o miolo; a cauda fria (entre o membro
    mais frio do ensemble e o P10) fica de fora, e é onde as mínimas perdem —
    ex.: NÃO 14°C com piso 14,47°C dentro da faixa. Predicado puro (geometria);
    o liga/desliga (``CEIFA_LOWER_TAIL_FILTER``) fica em quem chama.
    """
    interval = market_temperature_interval_c(icao, faixa)
    if (interval is None or p10 is None or piso_ens is None
            or pd.isna(p10) or pd.isna(piso_ens)):
        return False
    lower, upper = interval
    margin = (config.CEIFA_LOWER_TAIL_MARGIN if margin is None
              else float(margin))
    cold_lower, cold_upper = sorted((float(piso_ens) - margin, float(p10)))
    return max(lower, cold_lower) <= min(upper, cold_upper)


def is_upper_tail_ceiling_risk(icao: str, faixa, ensemble_ceiling,
                               margin: float | None = None) -> bool:
    """Veta "X ou mais" quando X está perto demais do teto do ensemble."""
    if not config.CEIFA_UPPER_TAIL_FILTER:
        return False
    label = str(faixa or "").lower()
    upper_open = any(marker in label for marker in (
        "or higher", "or above", "or more", "ou mais", "ou acima"))
    if not upper_open or ensemble_ceiling is None or pd.isna(ensemble_ceiling):
        return False
    target = target_temperature_c(icao, faixa)
    if target is None:
        return False
    margin = (config.CEIFA_UPPER_TAIL_MARGIN if margin is None
              else float(margin))
    return target <= float(ensemble_ceiling) + margin


def is_wide_book_risk(no_price, yes_ask,
                      max_overround: float | None = None) -> bool:
    """Veta quando os asks do par não comprovam soma abaixo do teto.

    Com o limite padrão, ``ask_sim + ask_nao`` deve ser menor que 105¢. Soma
    igual a 105¢ também bloqueia. Snapshots históricos sem uma das duas ofertas
    continuam sem esse veto, pois não há uma soma observada a comparar.
    """
    if not config.CEIFA_WIDE_BOOK_FILTER:
        return False
    if yes_ask is None or pd.isna(yes_ask) or no_price is None \
            or pd.isna(no_price):
        return False
    limit = (config.CEIFA_WIDE_BOOK_MAX_OVERROUND if max_overround is None
             else float(max_overround))
    return round(float(no_price) + float(yes_ask), 6) >= round(1.0 + limit, 6)


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
             warm_target_filter=True, uncertainty_filter=True) -> dict:
    """Roda a Ceifa (entrada em H-1) nos snapshots e devolve estatísticas no
    formato que backtest.ceifa_report_text espera.

    icaos: se dado, restringe a análise a esse conjunto de estações (usado
    também pelos estudos separados de temperatura mínima).
    archive: raiz do lago de dados (padrão dados/ = máxima; dados_low/ = mínima).
    warm_target_filter: desliga o veto de nowcast quente nos relatórios de
    mínima, onde a direção de risco é diferente.
    uncertainty_filter: desliga o veto de ensemble largo. A estratégia de
    mínima arquiva agora as colunas do ensemble, mas precisa de um limite
    próprio para a cauda fria antes que esse veto possa ser ativado.
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
    # A máxima usa teto - mediana como risco de cauda quente. A mínima já
    # arquiva sua distribuição, mas o chamador mantém este filtro desligado
    # até calibrarmos separadamente o risco da cauda fria.
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
    n_filtrado_ensemble_band = 0
    n_filtrado_upper_tail = 0
    n_filtrado_wide_book = 0
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
        nao_final = _resolved_price(g, "preco_nao")
        if nao_final is None:
            continue                              # dia ainda em aberto
        # FILTRO DE INCERTEZA (no lugar do stop): não entra em dia de ensemble
        # largo na H-1 — é onde o estouro (NÃO → zero) acontece.
        forecast = previsao_na_entrada(icao, dia, e["ts"])
        spr = float(forecast["spread"]) if forecast is not None else None
        if uncertainty_filter and is_uncertain(icao, spr, spread_norm):
            n_filtrado += 1
            n_filtrado_spread += 1
            if nao_final > 0.5:
                n_filtrado_100c += 1
            else:
                n_filtrado_0c += 1
            continue
        ceiling = (forecast.get("teto_ens") if forecast is not None else None)
        p10 = (forecast.get("p10") if forecast is not None else None)
        p90 = (forecast.get("p90") if forecast is not None else None)
        if (warm_target_filter and config.CEIFA_ENSEMBLE_BAND_FILTER
                and is_ensemble_inside_market_band_risk(icao, faixa, p10, p90)):
            n_filtrado += 1
            n_filtrado_ensemble_band += 1
            if nao_final > 0.5:
                n_filtrado_100c += 1
            else:
                n_filtrado_0c += 1
            continue
        if (warm_target_filter and is_upper_tail_ceiling_risk(
                icao, faixa, ceiling)):
            n_filtrado += 1
            n_filtrado_upper_tail += 1
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
        if checked and is_wide_book_risk(entry, e.get("ask_sim")):
            n_filtrado += 1
            n_filtrado_wide_book += 1
            if nao_final > 0.5:
                n_filtrado_100c += 1
            else:
                n_filtrado_0c += 1
            continue
        # Sem stop: segura até liquidar. Vitória se o NÃO foi para ~1,0.
        won = nao_final > 0.5
        signals.append({"icao": icao, "day": dia, "faixa": faixa,
                        "day_br": _brasilia_day(e["ts"]),
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
    st["n_filtrado_ensemble_band"] = n_filtrado_ensemble_band
    st["n_filtrado_upper_tail"] = n_filtrado_upper_tail
    st["n_filtrado_wide_book"] = n_filtrado_wide_book
    st["n_filtrado_100c"] = n_filtrado_100c
    st["n_filtrado_0c"] = n_filtrado_0c
    return st


def simulate_repeated(log=lambda m: None, icaos=None, archive=ARCHIVE,
                      warm_target_filter=True, interval_minutes: int = 5,
                      stake_frac: float = 0.01,
                      uncertainty_filter: bool = True,
                      minimum_taf_filter: bool = False,
                      ensemble_band_filter: bool = False,
                      lower_tail_filter: bool = False,
                      single_band: bool = False) -> dict:
    """Ceifa parcelada: uma stake relativa a cada rodada elegível da H-1.

    A stake de cada parcela é ``stake_frac`` do caixa ainda livre naquele
    instante, limitada ao espaço restante até 3% do patrimônio inicial do dia
    naquele contrato. Não há alavancagem; como a parcela diminui junto com o
    saldo disponível, o caixa nunca é esgotado matematicamente.
    Cada snapshot respeita novamente preço e livro. ``warm_target_filter`` e
    ``uncertainty_filter`` permitem manter os vetos meteorológicos das máximas
    desligados no estudo de mínimas. ``minimum_taf_filter`` reproduz o veto
    operacional de TSRA/VCTS quando essa informação existe no snapshot.
    ``ensemble_band_filter`` liga isoladamente o veto de faixa dentro do
    intervalo P10–P90 (o mesmo das máximas), para que as mínimas o apliquem
    sem herdar os vetos de cauda quente — ainda gated por
    ``CEIFA_ENSEMBLE_BAND_FILTER``. ``single_band`` ativa apenas o cenário
    comparativo que trava a faixa com maior ask executável na primeira rodada
    de cada cidade/data.
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

    resolutions = _resolved_prices(mkt, "preco_nao")
    candidates = _execution_candidates(mkt, "NAO")
    candidates["hloc"] = candidates.groupby("icao")["ts"].transform(
        lambda series: series.dt.tz_convert(_tz(series.name)).dt.hour)
    candidates = _h1_candidates(candidates, prev)
    if {"teto_ens", "mediana"}.issubset(prev.columns):
        ps = prev.dropna(subset=["teto_ens", "mediana"]).copy()
        ps["spread"] = ps["teto_ens"] - ps["mediana"]
        spread_norm = ps.groupby("icao")["spread"].median().to_dict()
    else:
        spread_norm = {}

    min_gap = pd.Timedelta(minutes=interval_minutes)
    signals = []
    filtered = filtered_spread = filtered_nowcast = filtered_plateau = 0
    filtered_ensemble_band = 0
    filtered_upper_tail = 0
    filtered_lower_tail = 0
    filtered_taf = 0
    filtered_wide_book = 0
    filtered_100c = filtered_0c = 0
    filtered_records: list[dict] = []
    for (icao, day, faixa), group in candidates.groupby(
            ["icao", "dia", "faixa"], observed=True):
        group = group.sort_values("ts")
        final_no = resolutions.get((icao, day, faixa))
        if final_no is None:
            continue
        last_entry_ts = None
        for _, entry_row in group.iterrows():
            fget = lambda name, default=None: entry_row.get(  # noqa: E731
                f"_forecast_{name}", default)
            if (last_entry_ts is not None
                    and entry_row["ts"] - last_entry_ts < min_gap):
                continue

            price = float(entry_row["_entry_price"])
            # Bloqueio carimbado no dia de Brasília da entrada (igual ao
            # "Retorno de cada dia"), não na data-alvo do mercado.
            day_br = _brasilia_day(entry_row["ts"]) or day

            taf_blocked = fget("taf_convective_blocked")
            taf_blocked = (taf_blocked is not None
                           and not pd.isna(taf_blocked)
                           and bool(taf_blocked))
            if minimum_taf_filter and taf_blocked:
                filtered += 1
                filtered_taf += 1
                filtered_records.append({"dia": day_br, "motivo": "TAF convectivo"})
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            spread = None
            if not pd.isna(fget("teto_ens")) and not pd.isna(
                    fget("mediana")):
                spread = float(fget("teto_ens") - fget("mediana"))
            if uncertainty_filter and is_uncertain(icao, spread, spread_norm):
                filtered += 1
                filtered_spread += 1
                filtered_records.append({"dia": day_br, "motivo": "Ensemble largo"})
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            if ((warm_target_filter or ensemble_band_filter)
                    and config.CEIFA_ENSEMBLE_BAND_FILTER
                    and is_ensemble_inside_market_band_risk(
                        icao, faixa, fget("p10"), fget("p90"))):
                filtered += 1
                filtered_ensemble_band += 1
                filtered_records.append(
                    {"dia": day_br, "motivo": "Faixa dentro de P10–P90"})
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            if (warm_target_filter and is_upper_tail_ceiling_risk(
                    icao, faixa, fget("teto_ens"))):
                filtered += 1
                filtered_upper_tail += 1
                filtered_records.append(
                    {"dia": day_br, "motivo": "Cauda superior perto do teto"})
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            if (lower_tail_filter and config.CEIFA_LOWER_TAIL_FILTER
                    and is_lower_tail_floor_risk(
                        icao, faixa, fget("p10"), fget("piso_ens"))):
                filtered += 1
                filtered_lower_tail += 1
                filtered_records.append(
                    {"dia": day_br, "motivo": "Cauda inferior perto do piso"})
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            plateau = fget("plateau_temp")
            if plateau is None or pd.isna(plateau):
                plateau = reconstructed_plateau_temperature(
                    icao, day, fget("ts"))
            observed_deviation = fget("nowcast_offset")
            if observed_deviation is None or pd.isna(observed_deviation):
                observed_deviation = reconstructed_observed_deviation(
                    icao, fget("ts"), fget("nowcast_shift"))
            warm_without_plateau = (
                warm_target_filter and is_warm_target_risk(
                    icao, faixa, fget("nowcast_shift"),
                    fget("mediana"), fget("p90"),
                    observed_deviation=observed_deviation))
            warm_with_plateau = (
                warm_target_filter and is_warm_target_risk(
                    icao, faixa, fget("nowcast_shift"),
                    fget("mediana"), fget("p90"),
                    observed_deviation=observed_deviation,
                    plateau_temp=plateau))
            if warm_with_plateau:
                filtered += 1
                filtered_nowcast += 1
                filtered_records.append(
                    {"dia": day_br, "motivo": "Desvio/nowcast quente"})
                if not warm_without_plateau:
                    filtered_plateau += 1
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            # Por último: qualidade do livro. Fica no fim para contar só as
            # entradas que passaram por todos os vetos meteorológicos mas têm
            # o Sim também caro (livro largo) — sem roubar a contagem deles.
            if is_wide_book_risk(price, entry_row.get("ask_sim")):
                filtered += 1
                filtered_wide_book += 1
                filtered_records.append(
                    {"dia": day_br, "motivo": "Livro largo (Sim caro)"})
                if final_no > 0.5:
                    filtered_100c += 1
                else:
                    filtered_0c += 1
                continue

            signals.append({
                "icao": icao, "day": day, "faixa": faixa,
                "day_br": _brasilia_day(entry_row["ts"]),
                "ts": entry_row["ts"], "price": price,
                "won": final_no > 0.5, "stopped": False,
                "loss_frac": None, "spread": spread,
            })
            last_entry_ts = entry_row["ts"]

    filtered_single_band = 0
    if single_band:
        signals, filtered_single_band = _select_single_band_signals(signals)
    stats = _stats_relative_available_stake(
        signals, mkt["dia"].nunique(), stake_frac)
    stats.update({
        "repeat_minutes": interval_minutes,
        "n_filtrado": filtered,
        "n_filtrado_spread": filtered_spread,
        "n_filtrado_nowcast": filtered_nowcast,
        "n_filtrado_plateau": filtered_plateau,
        "n_filtrado_ensemble_band": filtered_ensemble_band,
        "n_filtrado_upper_tail": filtered_upper_tail,
        "n_filtrado_lower_tail": filtered_lower_tail,
        "n_filtrado_taf": filtered_taf,
        "n_filtrado_wide_book": filtered_wide_book,
        "n_filtrado_faixa_unica": filtered_single_band,
        "single_band": single_band,
        "n_filtrado_100c": filtered_100c,
        "n_filtrado_0c": filtered_0c,
        "filtered_records": filtered_records,
    })
    log(f"ceifa parcelada ({interval_minutes} min): {stats['n']} parcelas · "
        f"{filtered} oportunidades recusadas por incerteza.")
    return stats


def _select_single_band_signals(signals: list[dict]) -> tuple[list[dict], int]:
    """Mantém um bucket por cidade/data durante toda a janela H-1.

    A primeira rodada executável escolhe o maior ask do NÃO. A faixa fica
    travada no restante do evento, reproduzindo o comportamento ao vivo e
    impedindo exposição correlacionada em buckets da mesma cidade/data.
    """
    by_event: dict[tuple, list[dict]] = defaultdict(list)
    for signal in signals:
        by_event[(signal["icao"], signal["day"])].append(signal)

    selected: list[dict] = []
    removed = 0
    for event_signals in by_event.values():
        first_ts = min(signal["ts"] for signal in event_signals)
        first_round = [signal for signal in event_signals
                       if signal["ts"] == first_ts]
        best = max(first_round,
                   key=lambda signal: (float(signal["price"]),
                                       str(signal["faixa"])))
        kept = [signal for signal in event_signals
                if signal["faixa"] == best["faixa"]]
        selected.extend(kept)
        removed += len(event_signals) - len(kept)
    return selected, removed


def simulate_yes_repeated(log=lambda m: None, icaos=None, archive=ARCHIVE,
                          interval_minutes: int = 5,
                          stake_frac: float = 0.01) -> dict:
    """Teste paralelo do SIM com oferta executável, sem filtros meteorológicos.

    Só considera snapshots que arquivaram explicitamente a melhor oferta do
    token SIM. Isso impede que preços indicativos antigos sejam tratados como
    compras que poderiam ter sido executadas.
    """
    market = _load("mercado", archive)
    forecast = _load("previsao", archive)
    if icaos is not None:
        icaos = set(icaos)
        market = (market[market["icao"].isin(icaos)]
                  if not market.empty else market)
        forecast = (forecast[forecast["icao"].isin(icaos)]
                    if not forecast.empty else forecast)
    if market.empty or forecast.empty or "ask_sim" not in market:
        stats = _stats_relative_available_stake([], 0, stake_frac)
        stats.update({
            "repeat_minutes": interval_minutes, "side": "SIM",
            "executable_snapshots": 0,
        })
        return stats

    market["hloc"] = market.groupby("icao")["ts"].transform(
        lambda series: series.dt.tz_convert(_tz(series.name)).dt.hour)
    forecast_by = {}
    for key, group in forecast.groupby(["icao", "dia"]):
        ordered = group.sort_values("ts")
        forecast_by[key] = (
            [timestamp.value for timestamp in ordered["ts"]],
            [row for _, row in ordered.iterrows()],
        )

    def forecast_at(icao, day, timestamp):
        lookup = forecast_by.get((icao, day))
        if lookup is None:
            return None
        timestamps, rows = lookup
        index = bisect_right(timestamps, timestamp.value) - 1
        return rows[index] if index >= 0 else None

    minimum_gap = pd.Timedelta(minutes=interval_minutes)
    signals = []
    executable_snapshots = 0
    for (icao, day, faixa), group in market.groupby(
            ["icao", "dia", "faixa"]):
        group = group.sort_values("ts")
        final_yes = _resolved_price(group, "preco_sim")
        if final_yes is None:
            continue
        last_entry_ts = None
        for _, entry_row in group.iterrows():
            checked = entry_row.get("livro_consultado")
            checked = (checked is not None and not pd.isna(checked)
                       and bool(checked))
            ask = entry_row.get("ask_sim")
            if not checked or ask is None or pd.isna(ask):
                continue
            executable_snapshots += 1

            entry_forecast = forecast_at(icao, day, entry_row["ts"])
            if (entry_forecast is None
                    or pd.isna(entry_forecast.get("pico_hora"))):
                continue
            extreme_hour = int(entry_forecast["pico_hora"])
            if int(entry_row["hloc"]) != (extreme_hour - 1) % 24:
                continue
            if (last_entry_ts is not None
                    and entry_row["ts"] - last_entry_ts < minimum_gap):
                continue

            price = normalize_market_price(ask)
            if not is_ceifa_price(price):
                continue
            if is_wide_book_risk(price, entry_row.get("ask_nao")):
                continue
            signals.append({
                "icao": icao, "day": day, "faixa": faixa,
                "day_br": _brasilia_day(entry_row["ts"]),
                "ts": entry_row["ts"], "price": price,
                "won": final_yes > 0.5, "stopped": False,
                "loss_frac": None, "spread": None, "side": "SIM",
            })
            last_entry_ts = entry_row["ts"]

    stats = _stats_relative_available_stake(
        signals, market["dia"].nunique(), stake_frac)
    stats.update({
        "repeat_minutes": interval_minutes, "side": "SIM",
        "executable_snapshots": executable_snapshots,
    })
    log(f"teste SIM parcelado ({interval_minutes} min): "
        f"{stats['n']} parcelas executáveis.")
    return stats


def _stats_relative_available_stake(signals: list, days: int,
                                    stake_frac: float,
                                    position_cap_frac: float =
                                    config.CEIFA_POSITION_CAP_FRAC) -> dict:
    """Parcela o caixa livre e limita cada contrato a uma fração do capital."""
    # Bucket do dia: para a Ceifa, o dia em que o Lucas operou (fuso de
    # Brasília, ``day_br``); para SPY/Bitcoin, que não têm ``day_br``, a
    # data-alvo do próprio mercado (``day``), definida pelo fechamento dele.
    by_day: dict = defaultdict(list)
    for signal in signals:
        by_day[_day_bucket(signal)].append(signal)
    capital, peak, max_drawdown = 1.0, 1.0, 0.0
    executed, per_day = [], []
    position_cap_blocked = position_cap_trimmed = 0
    for day in sorted(by_day):
        start = capital
        available, settled = start, 0.0
        day_executed = []
        allocated_by_contract: dict[tuple, float] = defaultdict(float)
        for signal in sorted(by_day[day], key=lambda item: item["ts"]):
            stake = stake_frac * available
            contract = (
                signal.get("extreme") or signal.get("archive_kind"),
                signal.get("icao"), str(signal.get("day")),
                signal.get("faixa"), signal.get("pick") or signal.get("side"),
            )
            position_cap = position_cap_frac * start
            room = max(position_cap - allocated_by_contract[contract], 0.0)
            if room <= 1e-12:
                position_cap_blocked += 1
                continue
            if stake > room:
                stake = room
                position_cap_trimmed += 1
            if stake <= 1e-12:
                position_cap_blocked += 1
                continue
            allocated_by_contract[contract] += stake
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
        "candidate_signals": list(signals),
        "stake_frac": stake_frac,
        "position_cap_frac": position_cap_frac,
        "n_position_cap_blocked": position_cap_blocked,
        "n_position_cap_trimmed": position_cap_trimmed,
        "n_capital_limited": position_cap_blocked,
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
