"""Camada de notificação por Telegram: formata o contexto da previsão em texto
(HTML do Telegram) e compõe um gráfico por estação (trajetória horária +
distribuições D0/D+1) num único PNG pronto para `sendPhoto`.

Reaproveita o núcleo do pipeline; nada de coleta acontece aqui."""
from __future__ import annotations

import html
import io
import json
import math
import time

import matplotlib

matplotlib.use("Agg")  # sem display (roda no GitHub Actions)
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import requests

from . import config, distribution
from .pipeline import hourly_percentiles

TELEGRAM_API = "https://api.telegram.org"

BLUE = "#1a5fb4"
ORANGE = "#e56c00"
RED = "#c01c28"
GREEN = "#26a269"

plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "#fafafa",
    "axes.grid": True,
    "grid.color": "#e0e0e0",
    "grid.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.size": 10,
})


# ------------------------------------------------------------------ gráfico

def temperature_for_unit(value, unit: str = "C"):
    """Converte uma temperatura interna em °C para a unidade de exibição."""
    if value is None:
        return None
    return float(value) * 9.0 / 5.0 + 32.0 if unit.upper() == "F" else value


def _temperature_series(values, unit: str) -> list:
    return [temperature_for_unit(value, unit) for value in values]


def _tick_label(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.1f}"


def _fahrenheit_market_buckets(dist: dict) -> list[dict]:
    """Reagrupa a distribuição nos contratos americanos de 2 °F."""
    if not dist.get("comps") or not dist.get("buckets"):
        return []
    min_f = temperature_for_unit(dist["buckets"][0]["low"], "F")
    max_f = temperature_for_unit(dist["buckets"][-1]["high"], "F")
    first = int(2 * math.floor((min_f + 0.5) / 2.0))
    last = int(2 * math.ceil((max_f + 0.5) / 2.0))
    buckets = []
    for low in range(first, last + 1, 2):
        prob = distribution.market_prob(dist, low, low + 1, "exact", "F")
        if prob is not None and prob >= 0.005:
            buckets.append({"low": low, "high": low + 1, "prob": prob})
    total = sum(bucket["prob"] for bucket in buckets)
    if total:
        for bucket in buckets:
            bucket["prob"] /= total
    return buckets


def _draw_hourly(ax, ctx) -> None:
    unit = ctx["station"].unit
    times, p10, p50, p90, _raw = hourly_percentiles(
        ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        ctx["shift"], ctx["now"], days={ctx["d0"]})
    p10 = _temperature_series(p10, unit)
    p50 = _temperature_series(p50, unit)
    p90 = _temperature_series(p90, unit)
    ax.fill_between(times, p10, p90, color=BLUE, alpha=0.18, label="P10–P90")
    ax.plot(times, p50, color=BLUE, lw=1.8, label="Mediana (corrigida)")
    if ctx["obs_today"]:
        ax.plot([o["time"] for o in ctx["obs_today"]],
                [temperature_for_unit(o["temp"], unit)
                 for o in ctx["obs_today"]], "o-",
                color=RED, ms=4, lw=1.2, label="Observado (METAR)")
    ax.axvline(ctx["now"], color="#666", lw=1, ls="--")
    ax.annotate("agora", (ctx["now"], ax.get_ylim()[1]), fontsize=8,
                color="#666", ha="left", va="top", xytext=(4, -2),
                textcoords="offset points")
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%d/%m\n%Hh", tz=times[0].tzinfo))
    ax.set_ylabel(f"°{unit}")
    if unit == "F":
        ax.yaxis.set_major_locator(mticker.MultipleLocator(2))
        ax.yaxis.set_minor_locator(mticker.MultipleLocator(1))
        ax.grid(which="minor", axis="y", color="#ececec", linewidth=0.4)
        ax.tick_params(axis="y", which="minor", length=2)
    ax.set_title("Trajetória horária (hora local)", fontsize=11)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)


def _draw_dist(ax, dist, title, det_points, taf_tx, unit: str = "C") -> None:
    buckets = dist["buckets"]
    market_buckets = _fahrenheit_market_buckets(dist) if unit == "F" else []
    if market_buckets:
        buckets = market_buckets
        xs = [b["low"] + 0.5 for b in buckets]
        widths = [2.0 * 0.92 for _b in buckets]
    else:
        lows = [temperature_for_unit(b["low"], unit) for b in buckets]
        highs = [temperature_for_unit(b["high"], unit) for b in buckets]
        xs = [(low + high) / 2.0 for low, high in zip(lows, highs)]
        widths = [(high - low) * 0.92 for low, high in zip(lows, highs)]
    ps = [b["prob"] * 100 for b in buckets]
    bars = ax.bar(xs, ps, width=widths, color=BLUE, alpha=0.75)
    for bar, p in zip(bars, ps):
        if p >= 5:
            ax.annotate(f"{p:.0f}%", (bar.get_x() + bar.get_width() / 2, p),
                        ha="center", va="bottom", fontsize=7, color="#333")
    med = temperature_for_unit(dist["quantiles"][50], unit)
    ax.axvline(med, color=RED, lw=1.5, ls="--", label=f"Mediana {med:.1f}")
    if det_points:
        for v in det_points.values():
            ax.plot(temperature_for_unit(v, unit), 0, marker="^", ms=8,
                    color=ORANGE, clip_on=False, zorder=5)
    if taf_tx is not None:
        taf_tx = temperature_for_unit(taf_tx, unit)
        ax.axvline(taf_tx, color=GREEN, lw=1.5, ls=":", label=f"TAF {taf_tx:.0f}")
    if market_buckets:
        ax.set_xticks(xs)
        ax.set_xticklabels(
            [f"{bucket['low']}–{bucket['high']}" for bucket in buckets])
    else:
        ticks = lows + [highs[-1]]
        ax.set_xticks(ticks)
        ax.set_xticklabels([_tick_label(value) for value in ticks])
    ax.set_xlabel(f"Faixa da máxima (°{unit})")
    ax.set_ylabel("Prob. (%)")
    ax.set_title(title, fontsize=10)
    ax.legend(loc="upper right", fontsize=7.5, framealpha=0.9)
    ax.set_ylim(0, max(ps) * 1.25 + 2)


def station_chart_png(ctx: dict) -> bytes:
    """Um único PNG por estação, só o D0: trajetória horária de hoje no topo
    e a distribuição da máxima de hoje embaixo."""
    fig = plt.figure(figsize=(9, 8))
    gs = fig.add_gridspec(2, 1, height_ratios=[1.05, 1], hspace=0.35)
    _draw_hourly(fig.add_subplot(gs[0, 0]), ctx)

    det0 = {m: v["corrected"] for m, v in ctx["det_corrected"]["d0"].items()}
    _draw_dist(fig.add_subplot(gs[1, 0]), ctx["dist_d0"],
               f"Hoje ({ctx['d0'].strftime('%d/%m')})", det0,
               ctx["taf_tx_d0"], unit=ctx["station"].unit)

    station = ctx["station"]
    # Sem a bandeira: emojis não existem na fonte do matplotlib (viram tofu).
    fig.suptitle(f"{station.city} — {station.icao}",
                 fontsize=13, fontweight="bold")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def distribution_png(ctx: dict) -> bytes:
    """PNG SÓ com a distribuição da máxima de hoje: barras (ensemble) + mediana
    do ensemble + TAF. Sem hora a hora e sem os determinísticos."""
    fig, ax = plt.subplots(figsize=(9, 5))
    _draw_dist(ax, ctx["dist_d0"], f"Hoje ({ctx['d0'].strftime('%d/%m')})",
               det_points=None, taf_tx=ctx["taf_tx_d0"],
               unit=ctx["station"].unit)
    station = ctx["station"]
    fig.suptitle(f"{station.city} — {station.icao}",
                 fontsize=13, fontweight="bold")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def equity_chart_png(per_day: list, inicial: float = 100.0) -> bytes:
    """Curva de evolução patrimonial da Ceifa: capital ao FIM de cada dia
    (base R$100). `per_day` = [{day, cap, ...}] com cap = multiplicador."""
    labels = ["início"] + [f"{d['day'][8:10]}/{d['day'][5:7]}" for d in per_day]
    caps = [inicial] + [d["cap"] * inicial for d in per_day]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.plot(range(len(caps)), caps, "o-", color=BLUE, lw=2.2, ms=6)
    ax.fill_between(range(len(caps)), inicial, caps,
                    where=[c >= inicial for c in caps], color=GREEN, alpha=0.12)
    ax.axhline(inicial, color="#999", lw=1, ls="--")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("R$")
    ax.set_title("Evolução patrimonial — Ceifa (base R$100, sem alavancar)",
                 fontsize=12, fontweight="bold")
    for i, c in enumerate(caps):
        ax.annotate(f"R${c:.2f}", (i, c), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=8, color="#333")
    ax.margins(y=0.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def ceifa_chart_png(ctx: dict) -> bytes:
    """O conjunto da Ceifa num PNG só: em cima a TRAJETÓRIA hora a hora (o
    andamento da temperatura — observado + mediana corrigida + faixa P10–P90);
    embaixo a distribuição da máxima (ensemble + TAF + mediana, sem os
    determinísticos)."""
    fig = plt.figure(figsize=(9, 8))
    gs = fig.add_gridspec(2, 1, height_ratios=[1.05, 1], hspace=0.35)
    _draw_hourly(fig.add_subplot(gs[0, 0]), ctx)
    _draw_dist(fig.add_subplot(gs[1, 0]), ctx["dist_d0"],
               f"Hoje ({ctx['d0'].strftime('%d/%m')})",
               det_points=None, taf_tx=ctx["taf_tx_d0"],
               unit=ctx["station"].unit)
    station = ctx["station"]
    fig.suptitle(f"{station.city} — {station.icao}",
                 fontsize=13, fontweight="bold")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def ceifa_minimum_chart_png(ctx: dict, member_values: list[float]) -> bytes:
    """Trajetória horária e distribuição empírica da mínima diária."""
    fig = plt.figure(figsize=(9, 8))
    gs = fig.add_gridspec(2, 1, height_ratios=[1.05, 1], hspace=0.35)
    _draw_hourly(fig.add_subplot(gs[0, 0]), ctx)
    ax = fig.add_subplot(gs[1, 0])
    unit = ctx["station"].unit
    values = [temperature_for_unit(value, unit) for value in member_values]
    if values:
        width = 2.0 if unit == "F" else 1.0
        lower = width * math.floor(min(values) / width)
        upper = width * math.ceil(max(values) / width) + width
        edges = []
        cursor = lower
        while cursor <= upper + 1e-9:
            edges.append(cursor)
            cursor += width
        counts = [
            sum(edge <= value < edge + width for value in values)
            for edge in edges[:-1]
        ]
        probabilities = [count / len(values) * 100 for count in counts]
        centers = [edge + width / 2 for edge in edges[:-1]]
        bars = ax.bar(centers, probabilities, width=width * 0.92,
                      color=BLUE, alpha=0.75)
        for bar, probability in zip(bars, probabilities):
            if probability >= 5:
                ax.annotate(
                    f"{probability:.0f}%",
                    (bar.get_x() + bar.get_width() / 2, probability),
                    ha="center", va="bottom", fontsize=7, color="#333")
        median = sorted(values)[len(values) // 2]
        ax.axvline(median, color=RED, lw=1.5, ls="--",
                   label=f"Mediana {median:.1f}")
        ax.set_xticks(centers)
        ax.set_xticklabels([
            (f"{edge:.0f}–{edge + width:.0f}" if width > 1
             else _tick_label(edge))
            for edge in edges[:-1]
        ])
        ax.set_ylim(0, max(probabilities) * 1.25 + 2)
        ax.legend(loc="upper right", fontsize=7.5, framealpha=0.9)
    ax.set_xlabel(f"Faixa da mínima (°{unit})")
    ax.set_ylabel("Prob. (%)")
    ax.set_title(f"Hoje ({ctx['d0'].strftime('%d/%m')})", fontsize=10)
    station = ctx["station"]
    fig.suptitle(f"{station.city} — {station.icao} · MÍNIMA",
                 fontsize=13, fontweight="bold")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# --------------------------------------------------------- tabela de odds PNG

# Divergência (nossa prob. − preço do mercado) a partir da qual destacamos a
# faixa: verde quando achamos o Yes mais provável que o mercado, vermelho quando
# menos. Abaixo disso, sem cor (ruído).
_EDGE = 0.08
_GREEN_BG = "#d7f0dd"
_RED_BG = "#f7d6da"


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _draw_odds_table(ax, day_label: str, date, rows: list[dict]) -> None:
    ax.axis("off")
    ax.set_title(f"{day_label} ({date.strftime('%d/%m')})",
                 fontsize=11, fontweight="bold", pad=10)
    col_labels = ["Faixa", "Yes", "No",
                  "Probabilidade\nReal de Sim", "Probabilidade\nReal de Não"]
    cell_text = [[r["label"], _pct(r["yes"]), _pct(r["no"]),
                  _pct(r["mp"]), _pct(r["mp_no"])] for r in rows]
    tbl = ax.table(cellText=cell_text, colLabels=col_labels,
                   cellLoc="center", loc="upper center",
                   colWidths=[0.30, 0.105, 0.105, 0.245, 0.245])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.55)

    for (row, col), cell in tbl.get_celld().items():
        cell.set_edgecolor("#d9d9d9")
        if row == 0:                                  # cabeçalho (2 linhas)
            cell.set_facecolor(BLUE)
            cell.set_text_props(color="white", fontweight="bold")
            cell.set_fontsize(8)
            cell.set_height(cell.get_height() * 1.7)
        elif row % 2 == 0:                            # zebra
            cell.set_facecolor("#f4f6fa")
        if col == 0 and row > 0:
            cell.get_text().set_ha("left")
            cell.PAD = 0.04

    # Realce da divergência mercado × nós, na coluna "Sim".
    for i, r in enumerate(rows, start=1):
        if r["mp"] is None or r["yes"] is None:
            continue
        edge = r["mp"] - r["yes"]
        if edge >= _EDGE:
            tbl[i, 3].set_facecolor(_GREEN_BG)
        elif edge <= -_EDGE:
            tbl[i, 3].set_facecolor(_RED_BG)


def odds_table_png(station, day_tables: list[tuple]) -> bytes:
    """Um PNG por estação comparando mercado × nossa previsão. `day_tables` é uma
    lista de (rótulo_do_dia, date, rows) — normalmente Hoje e Amanhã."""
    n = len(day_tables)
    max_rows = max(len(rows) for _, _, rows in day_tables)
    fig, axes = plt.subplots(
        1, n, figsize=(5.4 * n, max_rows * 0.42 + 1.4))
    if n == 1:
        axes = [axes]
    for ax, (day_label, date, rows) in zip(axes, day_tables):
        _draw_odds_table(ax, day_label, date, rows)
    fig.suptitle(f"{station.city} ({station.icao}) — mercado × projetado",
                 fontsize=13, fontweight="bold")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def odds_caption(station) -> str:
    return (f"📊 <b>{html.escape(station.city)} ({station.icao})</b> — "
            "mercado × projetado\n"
            "<i>Yes/No = preço do mercado (prob. implícita) · Sim/Não = "
            "probabilidade projetada de o resultado acontecer · verde/vermelho = "
            "onde divergimos do mercado.</i>")


# -------------------------------------------------------------------- texto

def _exceed_summary(dist: dict) -> str:
    """Prob. de exceder os limiares inteiros ao redor da mediana (info-chave
    para os mercados de temperatura)."""
    med = round(dist["quantiles"][50])
    parts = []
    for t in (med - 1, med, med + 1, med + 2):
        p = dist["exceed"].get(t)
        if p is not None:
            parts.append(f"≥{t} {p * 100:.0f}%")
    return " · ".join(parts)


def station_lines(ctx: dict) -> str:
    """Legenda (HTML do Telegram) do gráfico de uma estação. Cada tópico é um
    parágrafo separado por linha em branco, para não se perder na leitura."""
    s = ctx["station"]
    q0 = ctx["dist_d0"]["quantiles"]

    head = [f"{s.flag} <b>{html.escape(s.city)} ({s.icao})</b>"]
    if ctx["latest_metar"]:
        agora = f"Agora: <b>{ctx['latest_metar']['temp']:.0f} °C</b>"
        if ctx["obs_max_today"] is not None:
            agora += f" · máx. já hoje: {ctx['obs_max_today']:.0f} °C"
        head.append(agora)
    blocks = ["\n".join(head)]

    # Máxima de hoje travada: nowcast e resumo viram ruído — só o fato.
    if ctx.get("tmax_locked"):
        blocks.append(
            f"🔒 <b>Máx. de hoje já definida: "
            f"{ctx['obs_max_today']:.0f} °C</b> — mercado resolvido.")
    else:
        if ctx["nowcast"]:
            nc = ctx["nowcast"]
            direction = "mais quente" if nc["offset"] > 0 else "mais frio"
            blocks.append(
                f"📡 <b>Nowcast:</b> nas últimas {nc['n_hours']}h o observado está "
                f"<b>{abs(nc['offset']):.1f} °C {direction}</b> que o ensemble "
                f"corrigido → ajuste de <b>{nc['shift']:+.1f} °C</b> nas horas "
                "restantes de hoje.")

        taf0 = (f" · TAF {ctx['taf_tx_d0']:.0f}"
                if ctx["taf_tx_d0"] is not None else "")
        blocks.append(
            f"Hoje {ctx['d0'].strftime('%d/%m')}: <b>{q0[50]:.1f} °C</b> "
            f"(P10–P90 {q0[10]:.1f}–{q0[90]:.1f}){taf0}\n"
            f"{_exceed_summary(ctx['dist_d0'])}")

    return "\n\n".join(blocks)


def station_hourly_lines(ctx: dict) -> str:
    """Mensagem separada com a trajetória hora a hora (a mesma do gráfico):
    mediana corrigida + faixa P10–P90 por hora, agrupada por dia. Vai como
    `sendMessage` (limite de 4096) e não na legenda do gráfico (limite 1024)."""
    s = ctx["station"]
    times, p10, p50, p90, _raw = hourly_percentiles(
        ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        ctx["shift"], ctx["now"], days={ctx["d0"]})

    # Observado por hora, para marcar as horas já decorridas de hoje.
    obs_by_hour = {
        o["time"].replace(minute=0, second=0, microsecond=0): o["temp"]
        for o in ctx["obs_today"]}

    head = (f"{s.flag} <b>{html.escape(s.city)} ({s.icao})</b> — hora a hora\n"
            "<i>mediana corrigida · faixa P10–P90 · obs = METAR observado</i>")

    # Máxima de hoje já travada: as horas restantes viram ruído — mantém só
    # as já observadas e avisa.
    locked = bool(ctx.get("tmax_locked"))
    if locked:
        head += (f"\n🔒 <i>máx. de hoje já definida "
                 f"(<b>{ctx['obs_max_today']:.0f} °C</b>) — horas restantes "
                 "omitidas</i>")
    blocks = [head]

    current_day = None
    day_lines: list[str] = []
    for t, lo, md, hi in zip(times, p10, p50, p90):
        if md is None:
            continue
        if locked and t > ctx["now"]:
            continue
        if t.date() != current_day:
            if day_lines:
                blocks.append("\n".join(day_lines))
            current_day = t.date()
            day_lines = [f"<b>Hoje {current_day.strftime('%d/%m')}</b>"]
        obs = obs_by_hour.get(t.replace(minute=0, second=0, microsecond=0))
        line = f"{t.strftime('%Hh')}  <b>{md:.1f}°</b> (P10–P90 {lo:.0f}–{hi:.0f})"
        if obs is not None:
            line += f" · obs {obs:.0f}°"
        day_lines.append(line)
    if day_lines:
        blocks.append("\n".join(day_lines))

    return "\n\n".join(blocks)


def station_divider(station) -> str:
    """Separador visual que abre o bloco de uma estação no digest."""
    return (f"━━━━━━━━━━━━━━━\n"
            f"{station.flag} <b>{html.escape(station.city).upper()} "
            f"({station.icao})</b>\n"
            f"━━━━━━━━━━━━━━━")


# ----------------------------------------------------------- envio Telegram

_RETRY_WAITS = (3, 8, 20)  # segundos entre tentativas


def _tg_post(token: str, method: str, data: dict, files=None,
             timeout: int = 60) -> None:
    """POST na Bot API com retry para falhas transitórias (timeout, conexão,
    5xx e 429 respeitando o retry_after do flood control).

    Um timeout DEPOIS de o Telegram receber o envio pode duplicar a mensagem
    no chat ao repetir — aceitável num digest; pior seria perder o resto."""
    for wait in (*_RETRY_WAITS, None):
        try:
            r = requests.post(f"{TELEGRAM_API}/bot{token}/{method}",
                              data=data, files=files, timeout=timeout)
            r.raise_for_status()
            return
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            if wait is None:
                raise
        except requests.exceptions.HTTPError as exc:
            resp = exc.response
            status = resp.status_code if resp is not None else 0
            if wait is None or (status < 500 and status != 429):
                raise
            if status == 429:
                try:
                    wait = max(wait, int(resp.json()["parameters"]["retry_after"]))
                except Exception:
                    pass
        time.sleep(wait)


def send_message(token: str, chat_id: str, text: str, reply_markup=None) -> None:
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true"}
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup)
    _tg_post(token, "sendMessage", data, timeout=30)


def send_photo(token: str, chat_id: str, png: bytes, caption: str) -> None:
    _tg_post(token, "sendPhoto",
             {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
             files={"photo": ("previsao.png", png, "image/png")},
             timeout=90)


# --------------------------------------------------- comandos (recebimento)

def report_keyboard(icao: str) -> dict:
    """Teclado inline com o botão que pede o relatório completo da cidade.
    O callback_data (`rel:<ICAO>`) é lido no próximo getUpdates."""
    return {"inline_keyboard": [[
        {"text": "📄 Ver relatório completo", "callback_data": f"rel:{icao}"}
    ]]}


def get_updates(token: str, offset=None) -> list:
    """Updates pendentes (mensagens e cliques de botão). Sem long polling
    (timeout=0): drena o que chegou desde a última rodada e volta na hora.
    `offset` confirma os já processados (update_id + 1)."""
    params = {"timeout": 0,
              "allowed_updates": json.dumps(["message", "callback_query"])}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{TELEGRAM_API}/bot{token}/getUpdates",
                     params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("result", [])


def answer_callback_query(token: str, callback_id: str, text=None) -> None:
    """Confirma o clique (para o botão parar de "rodando"); `text` aparece
    como um toast rápido no topo do Telegram."""
    data = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
    _tg_post(token, "answerCallbackQuery", data, timeout=15)


def set_my_commands(token: str, commands: list) -> None:
    """Registra os comandos que aparecem no menu "/" do Telegram."""
    _tg_post(token, "setMyCommands", {"commands": json.dumps(commands)},
             timeout=15)
