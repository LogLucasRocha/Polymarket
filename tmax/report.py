"""Geração do relatório HTML (gráficos matplotlib embutidos em base64)."""
from __future__ import annotations

import base64
import datetime as dt
import html
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from . import config
from .pipeline import hourly_percentiles

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

BLUE = "#1a5fb4"
ORANGE = "#e56c00"
RED = "#c01c28"
GREEN = "#26a269"


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def chart_hourly(ens_times, member_series, bias, shift, now, obs, days) -> str:
    """Trajetória horária: banda P10–P90 e mediana do ensemble (corrigido de
    viés; horas futuras de hoje com o ajuste do nowcast) + METARs observados."""
    times, p10, p50, p90, _p50_raw = hourly_percentiles(
        ens_times, member_series, bias, shift, now, days)

    fig, ax = plt.subplots(figsize=(9.5, 3.6))
    ax.fill_between(times, p10, p90, color=BLUE, alpha=0.18,
                    label="Ensemble P10–P90 (corrigido)")
    ax.plot(times, p50, color=BLUE, lw=1.8, label="Mediana do ensemble")
    if obs:
        ax.plot([o["time"] for o in obs], [o["temp"] for o in obs], "o-",
                color=RED, ms=4, lw=1.2, label="Observado (METAR)")
    if now is not None:
        ax.axvline(now, color="#666", lw=1, ls="--")
        ax.annotate("agora", (now, ax.get_ylim()[1]), fontsize=8,
                    color="#666", ha="left", va="top", xytext=(4, -2),
                    textcoords="offset points")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m\n%Hh", tz=times[0].tzinfo))
    ax.set_ylabel("Temperatura (°C)")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    return _fig_to_b64(fig)


def chart_distribution(dist, title, det_points=None, taf_tx=None) -> str:
    """Barras de probabilidade por faixa de 1 °C + marcadores dos modelos
    determinísticos e do TAF."""
    buckets = dist["buckets"]
    xs = [b["low"] + 0.5 for b in buckets]
    ps = [b["prob"] * 100 for b in buckets]

    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    bars = ax.bar(xs, ps, width=0.92, color=BLUE, alpha=0.75)
    for bar, p in zip(bars, ps):
        if p >= 4:
            ax.annotate(f"{p:.0f}%", (bar.get_x() + bar.get_width() / 2, p),
                        ha="center", va="bottom", fontsize=8, color="#333")
    med = dist["quantiles"][50]
    ax.axvline(med, color=RED, lw=1.6, ls="--", label=f"Mediana {med:.1f} °C")
    if det_points:
        for i, (label, v) in enumerate(det_points.items()):
            ax.plot(v, 0, marker="^", ms=9, color=ORANGE, clip_on=False, zorder=5)
    if taf_tx is not None:
        ax.axvline(taf_tx, color=GREEN, lw=1.6, ls=":", label=f"TAF TX {taf_tx:.0f} °C")
    ax.set_xticks([b["low"] for b in buckets] + [buckets[-1]["high"]])
    ax.set_xlabel("Faixa da máxima (°C) — triângulos: modelos determinísticos")
    ax.set_ylabel("Probabilidade (%)")
    ax.set_title(title, fontsize=11)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax.set_ylim(0, max(ps) * 1.25 + 2)
    return _fig_to_b64(fig)


def _fmt_prob_table(dist) -> str:
    rows = "".join(
        f"<tr><td>{b['low']}–{b['high']} °C</td>"
        f"<td class='num'>{b['prob'] * 100:.1f}%</td>"
        f"<td><div class='bar' style='width:{b['prob'] * 220:.0f}px'></div></td></tr>"
        for b in dist["buckets"]
    )
    return (
        "<table class='probs'><tr><th>Faixa</th><th>Prob.</th><th></th></tr>"
        f"{rows}</table>"
    )


def _fmt_exceed_table(dist) -> str:
    items = sorted(dist["exceed"].items())
    rows = "".join(
        f"<tr><td>≥ {t} °C</td><td class='num'>{p * 100:.1f}%</td></tr>"
        for t, p in items
    )
    return f"<table class='probs'><tr><th>Limiar</th><th>Prob.</th></tr>{rows}</table>"


def render_html(ctx: dict) -> str:
    e = html.escape
    station = ctx["station"]
    q0, q1 = ctx["dist_d0"]["quantiles"], ctx["dist_d1"]["quantiles"]

    def det_rows(day_key):
        rows = []
        for model, info in ctx["det_corrected"][day_key].items():
            rows.append(
                f"<tr><td>{e(config.MODEL_LABELS.get(model, model))}</td>"
                f"<td class='num'>{info['raw']:.1f}</td>"
                f"<td class='num'>{info['bias']:+.1f}</td>"
                f"<td class='num'><b>{info['corrected']:.1f}</b></td></tr>"
            )
        return "".join(rows)

    def bias_rows():
        rows = []
        for fam, b in sorted(ctx["bias"].items()):
            rows.append(
                f"<tr><td>{e(config.MODEL_LABELS.get(b.get('model', fam), fam))}</td>"
                f"<td class='num'>{b['bias']:+.2f}</td>"
                f"<td class='num'>{b['resid_std']:.2f}</td>"
                f"<td class='num'>{b['mae']:.2f}</td>"
                f"<td class='num'>{b['n_days']}</td></tr>"
            )
        return "".join(rows)

    nowcast_html = ""
    if ctx.get("nowcast"):
        nc = ctx["nowcast"]
        direction = "mais quente" if nc["offset"] > 0 else "mais frio"
        nowcast_html = (
            f"<p class='nowcast'>📡 <b>Nowcast:</b> nas últimas {nc['n_hours']}h o "
            f"observado está <b>{abs(nc['offset']):.1f} °C {direction}</b> que o "
            f"ensemble corrigido → ajuste de <b>{nc['shift']:+.1f} °C</b> aplicado "
            f"às horas restantes de hoje.</p>"
        )

    taf_html = ""
    if ctx.get("taf"):
        tx_lines = "".join(
            f"<li>Máxima prevista (TX): <b>{t['temp']} °C</b> — dia "
            f"{t['local_date'].strftime('%d/%m')}, válido ~{t['valid_local'].strftime('%Hh')} local</li>"
            for t in ctx.get("taf_tx", [])
        )
        taf_html = (
            "<div class='card'><h2>TAF (meteorologista da estação)</h2>"
            f"<pre class='taf'>{e(ctx['taf'])}</pre>"
            f"<ul>{tx_lines}</ul></div>"
        )

    metar_html = ""
    if ctx.get("latest_metar"):
        m = ctx["latest_metar"]
        metar_html = (
            f"<p><b>Último METAR</b> ({m['time'].strftime('%d/%m %H:%M')} local): "
            f"<code>{e(m['raw'])}</code><br>"
            f"Temperatura: <b>{m['temp']:.0f} °C</b>"
            + (f" · Máxima já observada em {ctx['d0'].strftime('%d/%m')}: "
               f"<b>{ctx['obs_max_today']:.0f} °C</b>"
               if ctx.get("obs_max_today") is not None else "")
            + "</p>"
        )

    def day_section(label, date, dist, chart_b64, day_key, taf_tx):
        q = dist["quantiles"]
        taf_note = (f" · TAF TX: <b>{taf_tx:.0f} °C</b>" if taf_tx is not None else "")
        return f"""
<div class='card'>
  <h2>{label} — {date.strftime('%A, %d/%m/%Y')}</h2>
  <p class='headline'>Máxima esperada: <b>{q[50]:.1f} °C</b>
     <span class='range'>(P10–P90: {q[10]:.1f} a {q[90]:.1f} °C)</span>{taf_note}</p>
  <img src='data:image/png;base64,{chart_b64}' alt='distribuição'>
  <div class='cols'>
    <div>
      <h3>Probabilidade por faixa</h3>
      {_fmt_prob_table(dist)}
    </div>
    <div>
      <h3>Probabilidade de exceder</h3>
      {_fmt_exceed_table(dist)}
    </div>
    <div>
      <h3>Modelos determinísticos (Tmax °C)</h3>
      <table class='probs'>
        <tr><th>Modelo</th><th>Bruto</th><th>Viés</th><th>Corrigido</th></tr>
        {det_rows(day_key)}
      </table>
    </div>
  </div>
  <p class='fine'>Base: {dist['n_members']} membros de ensemble (ECMWF ENS + GEFS),
  corrigidos de viés e suavizados pelo erro residual histórico.</p>
</div>"""

    return f"""<!DOCTYPE html>
<html lang='pt-BR'><head><meta charset='utf-8'>
<title>Tmax {station.icao} — {ctx['generated'].strftime('%d/%m/%Y %H:%M')}</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; max-width: 1000px;
         margin: 24px auto; padding: 0 16px; color: #222; }}
  h1 {{ font-size: 22px; margin-bottom: 2px; }}
  h2 {{ font-size: 17px; border-bottom: 2px solid #1a5fb4; padding-bottom: 4px; }}
  h3 {{ font-size: 13px; margin: 8px 0 4px; }}
  .sub {{ color: #666; font-size: 13px; margin-top: 0; }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 10px;
           padding: 14px 18px; margin: 14px 0; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .headline {{ font-size: 16px; }}
  .range {{ color: #666; font-size: 13px; }}
  .cols {{ display: flex; gap: 28px; flex-wrap: wrap; }}
  table.probs {{ border-collapse: collapse; font-size: 12.5px; }}
  table.probs td, table.probs th {{ padding: 2px 10px 2px 0; text-align: left; }}
  table.probs th {{ color: #555; border-bottom: 1px solid #ccc; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .bar {{ height: 9px; background: #1a5fb4; border-radius: 2px; }}
  img {{ max-width: 100%; }}
  code, pre.taf {{ background: #f4f4f4; padding: 2px 5px; border-radius: 4px;
                   font-size: 12px; }}
  pre.taf {{ padding: 8px; white-space: pre-wrap; }}
  .nowcast {{ background: #fff6e5; border-left: 4px solid #e56c00;
              padding: 8px 12px; border-radius: 4px; }}
  .fine {{ color: #777; font-size: 11.5px; }}
</style></head><body>
<h1>🌡️ Previsão da máxima — {station.icao} ({e(station.city)})</h1>
<p class='sub'>{e(station.airport)} · Gerado em {ctx['generated'].strftime('%d/%m/%Y %H:%M %Z')} ·
Verdade terrestre: METAR de {station.icao} · Fontes: Open-Meteo (multi-modelo +
ensembles), aviationweather.gov, arquivo IEM ·
🕐 Todos os horários em hora local de {e(station.city)}
({e(station.timezone)}, UTC{ctx['generated'].strftime('%z')[:3]})</p>
{metar_html}
{nowcast_html}
<div class='card'>
  <h2>Trajetória horária ({ctx['d0'].strftime('%d/%m')} e {ctx['d1'].strftime('%d/%m')}, hora local)</h2>
  <img src='data:image/png;base64,{ctx['chart_hourly']}' alt='trajetória horária'>
</div>
{day_section('D0 · Hoje', ctx['d0'], ctx['dist_d0'], ctx['chart_d0'], 'd0', ctx.get('taf_tx_d0'))}
{day_section('D+1 · Amanhã', ctx['d1'], ctx['dist_d1'], ctx['chart_d1'], 'd1', ctx.get('taf_tx_d1'))}
{taf_html}
<div class='card'>
  <h2>Viés aprendido por modelo (últimos {config.BIAS_LOOKBACK_DAYS} dias)</h2>
  <table class='probs'>
    <tr><th>Modelo</th><th>Viés médio</th><th>Desvio residual</th><th>MAE</th><th>Dias</th></tr>
    {bias_rows()}
  </table>
  <p class='fine'>Viés = previsto − observado (METAR). Valor positivo → o modelo
  superestima a máxima em {station.icao} e a correção subtrai esse valor.</p>
</div>
</body></html>"""
