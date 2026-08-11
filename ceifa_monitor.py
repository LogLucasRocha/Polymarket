"""Monitor local da estratégia Ceifa.

Inicie com ``python -m streamlit run ceifa_monitor.py`` ou pelo atalho criado
na Área de Trabalho.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

from tmax import config, monitor
from spy import MERCADOS
from spy import study as spy_study

GREEN = "#38d39f"
RED = "#ff6b6b"
AMBER = "#f2b84b"
INK = "#dce8e3"
MUTED = "#8fa39a"
BLUE = "#68a8ff"
PLOT = "#0c1319"
GRID = "#24323b"

# Fuso do relógio em que o alerta chega para você — usado para mostrar em que
# horários do dia as parcelas históricas mais aparecem (quando ficar online).
USER_TZ = "America/Sao_Paulo"

# Cor fixa por motivo de bloqueio, para a legenda ficar estável entre rodadas.
# Tons saturados (não os pastéis do resto do painel) para as fatias empilhadas
# se distinguirem bem no fundo escuro.
BLOCK_COLORS = {
    "Ensemble largo": "#2b8cff",
    "Desvio/nowcast quente": "#ff9500",
    "Faixa dentro de P10–P90": "#00e0a0",
    "Cauda superior perto do teto": "#ff3b3b",
    "Cauda inferior perto do piso": "#22d3ee",
    "TAF convectivo": "#a855f7",
    "Livro largo (Sim caro)": "#f43f5e",
}

# Janela de exibição dos gráficos por dia — evita que o histórico inteiro
# amasse as barras com o tempo. "Todo o histórico" mantém o comportamento antigo.
WINDOW_OPTIONS = {
    "Últimos 30 dias": 30,
    "Últimos 90 dias": 90,
    "Todo o histórico": None,
}


def window_selector(key: str) -> int | None:
    """Seletor de janela; devolve o nº de dias (ou None para tudo)."""
    label = st.radio(
        "Janela dos gráficos", list(WINDOW_OPTIONS), horizontal=True,
        key=key, label_visibility="collapsed")
    return WINDOW_OPTIONS[label]


def clip_last_days(frame: pd.DataFrame, days: int | None,
                   column: str = "day") -> pd.DataFrame:
    """Mantém apenas os últimos ``days`` dias do frame (por ``column``)."""
    if days is None or frame.empty or column not in frame:
        return frame
    dates = pd.to_datetime(frame[column])
    cutoff = dates.max() - pd.Timedelta(days=days - 1)
    return frame[dates >= cutoff]

st.set_page_config(
    page_title="Ceifa Monitor",
    page_icon="🌾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.5rem; padding-bottom: 8rem; max-width: 1500px;}
      [data-testid="stSidebar"], [data-testid="collapsedControl"] {display: none;}
      [data-testid="stMetric"] {
        background: #101920; border: 1px solid #233139; border-radius: 14px;
        padding: 14px 16px; box-shadow: 0 5px 18px rgba(0, 0, 0, .22);
      }
      [data-testid="stMetricLabel"] {color: #91a59c;}
      .hero {
        padding: 22px 26px; border-radius: 18px; margin-bottom: 18px;
        background: linear-gradient(120deg, #0b3f35 0%, #11634f 66%, #168264 100%);
        color: white; border: 1px solid #1d8067;
        box-shadow: 0 12px 34px rgba(0, 0, 0, .28);
      }
      .hero h1 {font-size: 2rem; margin: 0 0 4px; color: white;}
      .hero p {margin: 0; opacity: .9;}
      .section-note {
        padding: 12px 15px; border-left: 4px solid #38d39f;
        background: #10251f; border-radius: 0 10px 10px 0; margin-bottom: 14px;
      }
      .loss-card {
        padding: 14px 16px; border: 1px solid #5b302f; border-radius: 12px;
        background: #231516; margin-bottom: 10px;
      }
      .tiny {color: #9aada5; font-size: .84rem;}
      div[data-testid="stDataFrame"] {border: 1px solid #26363e; border-radius: 12px;}
      .st-key-strategy_picker {
        background: #101920; border: 1px solid #233139; border-radius: 14px;
        padding: .45rem .85rem .1rem; margin-bottom: .7rem;
      }
      .st-key-bottom_navigation {
        position: fixed; z-index: 999; left: 0; right: 0; bottom: 0;
        background: rgba(9, 14, 19, .96); border-top: 1px solid #27363e;
        box-shadow: 0 -10px 30px rgba(0, 0, 0, .32);
        padding: .65rem max(1rem, calc((100vw - 820px) / 2));
        backdrop-filter: blur(14px);
      }
      .st-key-bottom_navigation [data-testid="stRadio"] > div {
        justify-content: center; gap: .7rem;
      }
      .st-key-bottom_navigation label {
        background: #111c23; border: 1px solid #293942;
        border-radius: 999px; padding: .45rem .9rem;
      }
      @media (max-width: 720px) {
        .block-container {padding-left: 1rem; padding-right: 1rem; padding-bottom: 9rem;}
        .st-key-bottom_navigation {padding: .55rem .5rem;}
        .st-key-bottom_navigation label {padding: .35rem .45rem; font-size: .78rem;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value * 100:+.{digits}f}%"


def mean_daily_return(stats: dict) -> float | None:
    """Média aritmética do retorno nos dias com parcelas da variante."""
    returns = [day.get("ret") for day in stats.get("per_day", [])
               if day.get("ret") is not None]
    return sum(returns) / len(returns) if returns else None


def mean_daily_parcels(stats: dict, resolved_days: int) -> float | None:
    """Média de parcelas por dia resolvido, incluindo dias sem entrada."""
    if not resolved_days:
        return None
    return stats.get("n", 0) / resolved_days


def num_or_dash(value, suffix: str = "", digits: int = 1) -> str:
    """Formata um número; devolve '—' quando o valor é nulo/NaN.

    Vários campos da autópsia (ex.: 'Máxima final' quando o mercado ainda não
    resolveu, ou os campos meteorológicos do teste do SIM) podem vir vazios.
    """
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.{digits}f}{suffix}"


def hero(subtitle: str) -> None:
    st.markdown(
        f"<div class='hero'><h1>🌾 Ceifa Monitor</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner="Recalculando a Ceifa nos nossos snapshots…")
def load_strategy(kind: str, side: str) -> dict:
    if side == "SIM":
        return monitor.run_yes_strategy(kind)
    return (monitor.run_minimum_strategy() if kind == "minimum"
            else monitor.run_strategy())


@st.cache_data(show_spinner="Recalculando o estudo do mercado…")
def load_market(market: str) -> list[tuple[str, dict]]:
    return spy_study.run_variants(market)


@st.cache_data
def load_losses(stats: dict) -> pd.DataFrame:
    return monitor.loss_details(stats)


@st.cache_data
def load_tail(stats: dict) -> dict | None:
    return monitor.observed_cvar(stats)


@st.cache_data
def load_timeline(icao: str, day: str, faixa: str, entry: str,
                  archive_kind: str, side: str) -> dict:
    return monitor.error_timeline(
        icao, day, faixa, entry, archive_kind, side)


def dark_figure(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        plot_bgcolor=PLOT, paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK),
        xaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
        yaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
    )
    return fig


def equity_chart(stats: dict) -> go.Figure:
    daily = pd.DataFrame(stats.get("per_day", []))
    if daily.empty:
        return go.Figure()
    daily["day"] = pd.to_datetime(daily["day"])
    daily["capital_pct"] = (daily["cap"] - 1.0) * 100
    colors = [GREEN if value >= 0 else RED for value in daily["capital_pct"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[daily["day"].min() - pd.Timedelta(days=1), *daily["day"]],
        y=[0.0, *daily["capital_pct"]],
        mode="lines+markers", line=dict(color=GREEN, width=3),
        marker=dict(size=8, color=[GREEN, *colors]),
        fill="tozeroy", fillcolor="rgba(56,211,159,.10)",
        hovertemplate="%{x|%d/%m}<br>Rendimento acumulado: %{y:+.2f}%<extra></extra>",
    ))
    fig.add_hline(y=0, line_dash="dot", line_color="#61736b")
    fig.update_layout(
        height=390, margin=dict(l=15, r=15, t=20, b=10),
        xaxis_title=None, yaxis_title="Rendimento acumulado (%)",
        showlegend=False, hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(zeroline=False)
    return dark_figure(fig)


def daily_chart(stats: dict, days: int | None = None) -> go.Figure:
    daily = pd.DataFrame(stats.get("per_day", []))
    if daily.empty:
        return go.Figure()
    daily["day"] = pd.to_datetime(daily["day"])
    daily = clip_last_days(daily, days)
    if daily.empty:
        return go.Figure()
    daily["return_pct"] = daily["ret"] * 100
    daily["resultado"] = daily["return_pct"].map(
        lambda value: "Positivo" if value >= 0 else "Negativo")
    fig = px.bar(
        daily, x="day", y="return_pct", color="resultado",
        color_discrete_map={"Positivo": GREEN, "Negativo": RED},
        custom_data=["n", "wins"],
    )
    fig.update_traces(
        hovertemplate=("%{x|%d/%m}<br>Retorno: %{y:+.2f}%"
                       "<br>Parcelas: %{customdata[0]}"
                       "<br>Acertos: %{customdata[1]}<extra></extra>"))
    mean_ret = daily["return_pct"].mean()
    fig.add_hline(
        y=mean_ret, line_dash="dash", line_color=AMBER,
        annotation_text=f"média {mean_ret:+.2f}%",
        annotation_position="top left", annotation_font_color=AMBER)
    fig.update_layout(
        height=330, margin=dict(l=15, r=15, t=10, b=10),
        xaxis_title=None, yaxis_title="Retorno do dia (%)",
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False)
    return dark_figure(fig)


def blocks_by_day_chart(blocks: pd.DataFrame,
                        days: int | None = None) -> go.Figure:
    """Uma barra por dia, empilhada pelos motivos de bloqueio."""
    blocks = clip_last_days(blocks, days, column="dia")
    if blocks.empty:
        return go.Figure()
    fig = px.bar(
        blocks, x="dia", y="bloqueios", color="motivo",
        color_discrete_map=BLOCK_COLORS, barmode="stack",
        custom_data=["motivo"],
    )
    fig.update_traces(
        hovertemplate=("%{x|%d/%m}<br>%{customdata[0]}"
                       "<br>Bloqueios: %{y}<extra></extra>"))
    fig.update_layout(
        height=340, margin=dict(l=15, r=15, t=10, b=10),
        xaxis_title=None, yaxis_title="Entradas bloqueadas",
        legend_title_text=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0),
    )
    fig.update_xaxes(showgrid=False)
    return dark_figure(fig)


def _local_timestamps(stats: dict) -> pd.Series:
    """Instantes das parcelas convertidos para o fuso do usuário."""
    stamps = [signal.get("ts") for signal in stats.get("signals", [])
              if signal.get("ts") is not None]
    if not stamps:
        return pd.Series(dtype="datetime64[ns, America/Sao_Paulo]")
    stamps = pd.to_datetime(pd.Series(stamps), utc=True)
    return stamps.dt.tz_convert(USER_TZ)


def brasilia_time_labels(stamps: pd.Series) -> pd.Series:
    """Rótulos locais para tooltips cujos eixos permanecem em UTC."""
    converted = pd.to_datetime(stamps, utc=True).dt.tz_convert(USER_TZ)
    return converted.dt.strftime("%d/%m/%Y %H:%M")


def _local_hours(stats: dict) -> pd.Series:
    """Hora do dia (fuso do usuário) de cada parcela executada no histórico."""
    return _local_timestamps(stats).dt.hour


def hourly_counts(stats: dict) -> pd.Series:
    """Total de parcelas por hora do dia (0–23), no fuso do usuário."""
    hours = _local_hours(stats)
    if hours.empty:
        return pd.Series(dtype="int64")
    return hours.value_counts().reindex(range(24), fill_value=0).sort_index()


def hourly_day_count(stats: dict) -> int:
    """Dias com parcelas, incluindo zero nas horas sem entrada daquele dia."""
    stamps = _local_timestamps(stats)
    if stamps.empty:
        return 0
    return int(stamps.dt.normalize().nunique())


def hourly_average(stats: dict) -> pd.Series:
    """Média diária de parcelas em cada hora, no período selecionado."""
    counts = hourly_counts(stats)
    days = hourly_day_count(stats)
    if counts.empty or not days:
        return pd.Series(dtype="float64")
    return counts.astype(float) / days


def hourly_by_category(stats: dict,
                       side_labels: tuple[str, str] | None = None
                       ) -> pd.DataFrame:
    """Média diária por hora, agrupada por extremo ou lado do mercado."""
    rows = []
    for signal in stats.get("signals", []):
        if signal.get("ts") is None:
            continue
        if side_labels is not None:
            category = {"up": side_labels[0], "down": side_labels[1]}.get(
                signal.get("pick"), "Outro")
        else:
            category = {"maximum": "Máxima", "minimum": "Mínima"}.get(
                signal.get("extreme"), "Outra")
        rows.append((signal.get("ts"), category))
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["ts", "cat"])
    ts = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(USER_TZ)
    frame["hora"] = ts.dt.hour
    days = int(ts.dt.normalize().nunique()) or 1
    piv = (frame.groupby(["hora", "cat"]).size().unstack(fill_value=0)
           .reindex(range(24), fill_value=0))
    return piv.astype(float) / days


def hourly_by_extreme(stats: dict) -> pd.DataFrame:
    """Compatibilidade: agrupamento de máxima/mínima usado em produção."""
    return hourly_by_category(stats)


def hourly_chart(stats: dict,
                 side_labels: tuple[str, str] | None = None) -> go.Figure:
    """Média diária das parcelas por horário, empilhada por máxima/mínima.

    Cada barra é a média de parcelas/dia naquela hora (fuso de Brasília),
    quebrada por cor: laranja = máxima, azul = mínima. A tracejada é a média
    do total por hora."""
    piv = hourly_by_category(stats, side_labels)
    if piv.empty:
        return go.Figure()
    total = piv.sum(axis=1)
    mean = float(total.mean())
    hours = [f"{hour:02d}h" for hour in piv.index]
    if side_labels is None:
        categories = ("Máxima", "Mínima", "Outra")
        cores = {"Máxima": RED, "Mínima": BLUE, "Outra": GREEN}
    else:
        categories = (*side_labels, "Outro")
        cores = {side_labels[0]: BLUE, side_labels[1]: AMBER, "Outro": GREEN}
    fig = go.Figure()
    for cat in categories:
        if cat not in piv.columns:
            continue
        fig.add_trace(go.Bar(
            x=hours, y=piv[cat].values, name=cat, marker_color=cores[cat],
            hovertemplate=("%{x} (Brasília)<br>" + cat +
                           ": %{y:.2f}/dia<extra></extra>")))
    fig.add_hline(
        y=mean, line_dash="dash", line_color=INK,
        annotation_text=f"média {mean:.2f}/dia",
        annotation_position="top left", annotation_font_color=INK)
    fig.update_layout(
        barmode="stack", height=330, margin=dict(l=15, r=15, t=20, b=10),
        xaxis_title=None, yaxis_title="Média de parcelas por dia",
        bargap=0.15,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(showgrid=False)
    return dark_figure(fig)


def render_hourly_section(stats: dict,
                          side_labels: tuple[str, str] | None = None) -> None:
    """Renderiza a distribuição horária compartilhada por produção e testes."""
    counts = hourly_counts(stats)
    if counts.empty:
        return
    averages = hourly_average(stats)
    days = hourly_day_count(stats)
    st.subheader("Parcelas por horário do dia (hora de Brasília)")
    st.plotly_chart(hourly_chart(stats, side_labels), width="stretch")
    top = averages.sort_values(ascending=False).head(3)
    picos = " · ".join(f"{hour:02d}h ({value:.2f}/dia)"
                       for hour, value in top.items())
    st.caption(
        f"Média por dia nos horários de pico: {picos}. "
        f"Cálculo sobre {days} dia(s) com apostas, incluindo zero nas "
        "horas sem entrada. O acumulado continua disponível ao passar "
        "o mouse — o relógio é o de Brasília.")


def overview(stats: dict, full_stats: dict, minimum: bool, side: str,
             consolidated: bool = False,
             experimental_stats: dict | None = None,
             proximity_stats: dict | None = None) -> None:
    risk = monitor.risk_metrics(stats)
    unique_n, unique_losses = monitor.unique_contracts(stats)
    errors = stats.get("n", 0) - stats.get("wins", 0)
    daily = stats.get("per_day", [])

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Rendimento acumulado", pct(stats.get("real_mult", 1) - 1))
    c2.metric("Retorno diário médio", pct(risk["avg_daily"], 2))
    count_name = ("parcelas" if stats.get("repeat_minutes") is not None
                  else "entradas")
    c3.metric("Assertividade", f"{stats.get('hit', 0):.2%}",
              f"{stats.get('wins', 0)}/{stats.get('n', 0)} {count_name}")
    c4.metric("Erros", str(errors), f"em {unique_n} contratos")
    c5.metric("Drawdown máximo", f"{stats.get('real_dd', 0):.2%}")

    if experimental_stats is not None and proximity_stats is not None:
        comparison = []
        for label, result in (("Regra ativa", stats),
                              ("Faixa única (experimental)",
                               experimental_stats),
                              ("Proximidade (experimental)",
                               proximity_stats)):
            total = result.get("n", 0)
            wins = result.get("wins", 0)
            comparison.append({
                "Regra": label,
                "Parcelas": total,
                "Acertos": wins,
                "Erros": total - wins,
                "Assertividade": f"{result.get('hit', 0):.2%}",
                "Retorno": pct(result.get("real_mult", 1) - 1, 2),
            })
        st.subheader("Regra ativa versus cenários experimentais")
        st.dataframe(pd.DataFrame(comparison), hide_index=True,
                     width="stretch")
        st.caption(
            "Faixa única e proximidade são somente cenários de backtest. "
            "Nenhum deles altera os alertas, as stakes ou os indicadores da "
            "estratégia ativa. Proximidade usa um grau na unidade do contrato, "
            "acima da máxima ou abaixo da mínima já observada.")

    left, right = st.columns([1.65, 1])
    with left:
        st.subheader("Evolução da estratégia")
        st.plotly_chart(equity_chart(stats), width="stretch")
    with right:
        st.subheader("Leitura de risco")
        r1, r2 = st.columns(2)
        r1.metric("Melhor dia", pct(risk["best_day"], 2))
        r2.metric("Pior dia", pct(risk["worst_day"], 2))
        st.metric("Contratos com erro", str(unique_losses))

        tail = load_tail(stats)
        if tail:
            st.metric("Média dos 1% piores dias (CVaR)",
                      pct(tail["cvar"], 2))
            st.caption(
                f"Média do retorno diário nos {tail['tail_days']} pior(es) "
                f"de {tail['n_days']} dias reais. Com histórico curto equivale "
                "ao pior dia; vira média de vários dias conforme os dias "
                "acumulam. Só dias observados — sem simulação.")

        if consolidated:
            components = full_stats.get("active_components") or {}
            st.markdown(
                "<div class='section-note'><b>Estratégias ativas consolidadas</b><br>"
                f"Máximas: {components.get('maximum', 0):,} parcelas · "
                f"Mínimas: {components.get('minimum', 0):,} parcelas · "
                "uma única banca compartilhada entre todos os sinais.</div>"
                .replace(",", "."), unsafe_allow_html=True)
        elif side == "SIM":
            st.markdown(
                "<div class='section-note'><b>Teste separado do SIM</b><br>"
                "Somente ofertas executáveis acima de 95¢ e abaixo de 99,5¢, "
                "na H-1. Sem apostas reais e sem filtros meteorológicos.</div>",
                unsafe_allow_html=True)
            st.caption(
                f"Snapshots com oferta executável do SIM: "
                f"{full_stats.get('executable_snapshots', 0):,}."
                .replace(",", "."))
        elif minimum:
            st.markdown(
                "<div class='section-note'><b>Estratégia ativa de mínimas</b><br>"
                "Parcelas de 1% do caixa livre a cada cinco minutos na H-1. "
                "A estratégia ainda não possui filtro meteorológico de "
                "incerteza próprio.</div>",
                unsafe_allow_html=True)
        else:
            filtered = full_stats.get("n_filtrado", 0)
            prevented_losses = full_stats.get("n_filtrado_0c", 0)
            st.markdown(
                f"<div class='section-note'><b>{filtered:,} entradas evitadas</b><br>"
                f"O filtro bloqueou {prevented_losses} contratos que terminariam em 0¢ "
                f"e {full_stats.get('n_filtrado_100c', 0)} que terminariam em 100¢.</div>"
                .replace(",", "."), unsafe_allow_html=True)
        st.caption(
            "As parcelas repetidas no mesmo contrato não são eventos "
            "independentes.")

    st.subheader("Retorno de cada dia (hora de Brasília)")
    days = window_selector(f"daily_window_{stats.get('archive_kind')}_{side}")
    st.plotly_chart(daily_chart(stats, days), width="stretch")
    if daily:
        shown = len(clip_last_days(
            pd.DataFrame(daily).assign(day=lambda f: pd.to_datetime(f["day"])),
            days))
        st.caption(
            f"Mostrando {shown} de {len(daily)} dia(s) de apostas · melhor dia "
            f"{pct(risk['best_day'], 2)} · pior dia {pct(risk['worst_day'], 2)} "
            "· linha tracejada = retorno médio do período exibido.")

    render_hourly_section(stats)


def errors_page(stats: dict) -> None:
    if stats.get("archive_kind") == "consolidated":
        losing = [signal for signal in monitor.signals_with_stakes(stats)
                  if not signal.get("won")]
        if not losing:
            st.success("Nenhum erro no período selecionado.")
            return
        rows = []
        for signal in losing:
            station = config.STATIONS.get(signal.get("icao"))
            rows.append({
                "Data": str(signal.get("day")),
                "Estratégia": ("Mínima" if signal.get("extreme") == "minimum"
                               else "Máxima"),
                "Cidade": station.city if station else signal.get("icao"),
                "ICAO": signal.get("icao"),
                "Faixa": signal.get("faixa"),
                "Preço (¢)": float(signal.get("price") or 0) * 100,
                "Parcela (% banca inicial)": float(signal.get("stake") or 0) * 100,
                "Filtro que bloquearia": monitor.inactive_filter_hit(signal) or "—",
            })
        losses = pd.DataFrame(rows).sort_values(
            ["Data", "Estratégia", "Cidade"], ascending=[False, True, True])
        c1, c2, c3 = st.columns(3)
        c1.metric("Erros", len(losses))
        c2.metric("Máximas", int((losses["Estratégia"] == "Máxima").sum()))
        c3.metric("Mínimas", int((losses["Estratégia"] == "Mínima").sum()))
        st.info(
            "Lista consolidada de erros (máximas e mínimas na mesma banca).")
        st.dataframe(losses, hide_index=True, width="stretch")
        return

    minimum = stats.get("archive_kind") == "minimum"
    side = stats.get("side", "NAO")
    losses = load_losses(stats)
    if losses.empty:
        st.success("Nenhum erro no período selecionado.")
        return

    total_cost = losses["Parcela (% banca inicial)"].sum() / 100
    c1, c2, c3 = st.columns(3)
    c1.metric("Erros", len(losses))
    c2.metric("Impacto direto", pct(-total_cost, 2),
              "fração da banca inicial")
    c3.metric("Cidades afetadas", losses["ICAO"].nunique())

    note = ("Selecione um caso para acompanhar o preço do contrato. O arquivo "
            "de mínimas ainda não guarda ensemble e METAR detalhados."
            if minimum else
            "Selecione um caso para ver o que o ensemble, o METAR e o mercado "
            "mostravam no momento exato da entrada.")
    st.markdown(f"<div class='section-note'>{note}</div>",
                unsafe_allow_html=True)

    scope = f"{'minimum' if minimum else 'maximum'}_{side.lower()}"
    date_key = f"loss_date_{scope}"
    contract_key = f"loss_contract_{scope}"
    days = monitor.loss_days(losses)
    if st.session_state.get(date_key) not in days:
        st.session_state[date_key] = days[0]

    date_col, contract_col = st.columns([1, 2])
    with date_col:
        selected_day = st.selectbox(
            "Data do erro",
            days,
            format_func=lambda value: pd.Timestamp(value).strftime("%d/%m/%Y"),
            key=date_key,
        )

    day_losses = monitor.losses_on_day(losses, selected_day)
    contracts = day_losses.drop_duplicates("Chave", keep="first")
    contract_options = contracts["Chave"].tolist()
    contract_labels = {
        row["Chave"]: (
            f"{row['Cidade']} ({row['ICAO']}) · {side} {row['Faixa']}"
        )
        for _, row in contracts.iterrows()
    }
    if st.session_state.get(contract_key) not in contract_options:
        st.session_state[contract_key] = contract_options[0]
    with contract_col:
        selected_key = st.selectbox(
            "Contrato perdido",
            contract_options,
            format_func=contract_labels.get,
            key=contract_key,
        )
    selected = day_losses[day_losses["Chave"] == selected_key].iloc[0]
    selected_day_label = pd.Timestamp(selected_day).strftime("%d/%m/%Y")

    st.markdown(
        f"<div class='loss-card'><b>{selected['Cidade']} ({selected['ICAO']}) · "
        f"{side} {selected['Faixa']}</b><br>{selected['Diagnóstico']}<br>"
        f"<span class='tiny'>Entrada em {selected['Entrada local']} a "
        f"{selected['Preço (¢)']:.1f}¢</span></div>", unsafe_allow_html=True)

    blocker = selected.get("Filtro que bloquearia", "—")
    if blocker and blocker != "—":
        st.warning(
            f"🛡️ Um filtro **inativo** teria bloqueado este erro: **{blocker}**.")
    else:
        st.caption("Nenhum filtro inativo teria bloqueado este contrato.")

    if minimum:
        timeline = load_timeline(
            selected["ICAO"], selected["Dia"], selected["Faixa"],
            selected["Entrada UTC"], "minimum", side)
        market = timeline["market"]
        fig = go.Figure()
        if not market.empty:
            fig.add_trace(go.Scatter(
                x=market["Horário"], y=market["Preço (¢)"],
                mode="lines", fill="tozeroy", name=f"Preço do {side}",
                line=dict(color=BLUE, width=2), fillcolor="rgba(104,168,255,.13)",
                hovertemplate="%{x|%d/%m %H:%M} · %{y:.1f}¢<extra></extra>"))
        fig.add_hrect(y0=95, y1=99.5, fillcolor="rgba(56,211,159,.10)",
                      line_width=0, annotation_text="faixa de entrada")
        fig.add_vline(x=timeline["entry"].timestamp() * 1000,
                      line_color=INK, line_dash="dash",
                      annotation_text="entrada")
        fig.update_layout(
            title=f"Evolução do preço do {side}", height=390,
            margin=dict(l=10, r=10, t=45, b=10),
            xaxis_title=None, yaxis_title="Preço (¢)", yaxis_range=[0, 102],
            showlegend=False)
        st.plotly_chart(dark_figure(fig), width="stretch")
        st.info(
            "Para permitir uma autópsia meteorológica equivalente à Ceifa de "
            "máximas, a captura de mínimas ainda precisa arquivar METAR, mediana, "
            "P90, spread e nowcast no momento da entrada.")
        st.subheader(f"Erros de mínimas em {selected_day_label}")
        st.dataframe(
            day_losses[["Cidade", "Dia", "Faixa", "Preço (¢)",
                        "Filtro que bloquearia", "Diagnóstico"]],
            hide_index=True, width="stretch")
        return

    a, b, c, d, e = st.columns(5)
    unit = selected["Unidade"]
    a.metric("Observado na entrada",
             num_or_dash(selected["Observado na entrada"], f" °{unit}"))
    hora_max = selected["Horário da máxima"]
    b.metric("Máxima final",
             num_or_dash(selected["Máxima final"], f" °{unit}"),
             hora_max if isinstance(hora_max, str) or pd.notna(hora_max)
             else None)
    c.metric("Mediana", num_or_dash(selected["Mediana"], f" °{unit}"))
    d.metric("P90", num_or_dash(selected["P90"], f" °{unit}"))
    e.metric("Spread", num_or_dash(selected["Spread (°C)"], " °C", 2))

    timeline = load_timeline(
        selected["ICAO"], selected["Dia"], selected["Faixa"],
        selected["Entrada UTC"], "maximum", side)
    left, right = st.columns(2)
    with left:
        observations = timeline["observations"]
        fig = go.Figure()
        if not observations.empty:
            fig.add_trace(go.Scatter(
                x=observations["Horário"], y=observations["Temperatura"],
                mode="lines+markers", name="METAR", line=dict(color=RED, width=2),
                hovertemplate=f"%{{x|%H:%M}} · %{{y:.1f}} °{unit}<extra></extra>"))
        for label, value, color, dash in (
            ("Mediana", selected["Mediana"], GREEN, "dash"),
            ("P90", selected["P90"], AMBER, "dot"),
        ):
            if value is not None and not pd.isna(value):
                fig.add_hline(y=value, line_color=color, line_dash=dash,
                              annotation_text=label)
        fig.add_vline(x=timeline["entry"].timestamp() * 1000,
                      line_color=INK, line_dash="dash",
                      annotation_text="entrada")
        fig.update_layout(
            title="Temperatura observada", height=360,
            margin=dict(l=10, r=10, t=45, b=10),
            xaxis_title=None, yaxis_title=f"Temperatura (°{unit})",
            legend=dict(orientation="h"),
        )
        st.plotly_chart(dark_figure(fig), width="stretch")
    with right:
        market = timeline["market"]
        fig = go.Figure()
        if not market.empty:
            fig.add_trace(go.Scatter(
                x=market["Horário"], y=market["Preço (¢)"],
                mode="lines", fill="tozeroy", name=f"Preço do {side}",
                line=dict(color=BLUE, width=2), fillcolor="rgba(104,168,255,.13)",
                hovertemplate="%{x|%H:%M} · %{y:.1f}¢<extra></extra>"))
        fig.add_hrect(y0=95, y1=99.5, fillcolor="rgba(56,211,159,.10)",
                      line_width=0, annotation_text="faixa de entrada")
        fig.add_vline(x=timeline["entry"].timestamp() * 1000,
                      line_color=INK, line_dash="dash",
                      annotation_text="entrada")
        fig.update_layout(
            title=f"Preço do {side}", height=360,
            margin=dict(l=10, r=10, t=45, b=10),
            xaxis_title=None, yaxis_title="Preço (¢)", yaxis_range=[0, 102],
            showlegend=False,
        )
        st.plotly_chart(dark_figure(fig), width="stretch")

    if side == "SIM":
        st.info(
            "Este teste do SIM é deliberadamente bruto: exige oferta executável "
            "na faixa de preço e H-1, mas ainda não aplica filtros meteorológicos.")
        st.subheader(f"Erros em {selected_day_label}")
        st.dataframe(
            day_losses[["Cidade", "Dia", "Lado", "Faixa", "Preço (¢)",
                        "Filtro que bloquearia", "Diagnóstico"]],
            hide_index=True, width="stretch")
        return

    def passou(value, limit) -> str:
        if value is None or pd.isna(value):
            return "—"
        return "Passou" if value < limit else "Cortaria"

    st.subheader("Por que os filtros permitiram a entrada")
    diagnostic = pd.DataFrame([
        ["Desvio observado usado", selected["Desvio usado (°C)"],
         config.CEIFA_OBS_DEVIATION_MIN,
         passou(selected["Desvio usado (°C)"], config.CEIFA_OBS_DEVIATION_MIN)],
        ["Desvio usando a hora real do METAR",
         selected["Desvio com hora METAR (°C)"], config.CEIFA_OBS_DEVIATION_MIN,
         passou(selected["Desvio com hora METAR (°C)"],
                config.CEIFA_OBS_DEVIATION_MIN)],
        ["Nowcast shift", selected["Shift (°C)"],
         config.CEIFA_NOWCAST_SHIFT_MIN,
         passou(selected["Shift (°C)"], config.CEIFA_NOWCAST_SHIFT_MIN)],
        ["Spread absoluto", selected["Spread (°C)"],
         config.CEIFA_SPREAD_ABS,
         passou(selected["Spread (°C)"], config.CEIFA_SPREAD_ABS)],
    ], columns=["Indicador", "Valor", "Limite", "Resultado"])
    st.dataframe(
        diagnostic.style.format(
            {"Valor": "{:.2f} °C", "Limite": "{:.2f} °C"}, na_rep="—"),
        hide_index=True, width="stretch")

    st.subheader(f"Erros em {selected_day_label}")
    table = day_losses[["Cidade", "Dia", "Faixa", "Preço (¢)",
                       "Observado na entrada", "Máxima final",
                       "Filtro que bloquearia", "Diagnóstico"]].copy()
    st.dataframe(table, hide_index=True, width="stretch")


def cities_page(stats: dict, full_stats: dict,
                consolidated: bool = False) -> None:
    minimum = stats.get("archive_kind") == "minimum"
    side = stats.get("side", "NAO")

    st.subheader("Filtros ativos")
    if consolidated:
        components = full_stats.get("active_components") or {}
        st.info(
            "O consolidado soma as duas estratégias ativas usando a mesma "
            "banca. As máximas usam ensemble/nowcast/platô; as mínimas "
            "bloqueiam TSRA/VCTS previstos no TAF.")
        f1, f2 = st.columns(2)
        f1.metric("Parcelas de máximas", components.get("maximum", 0))
        f2.metric("Parcelas de mínimas", components.get("minimum", 0))
    elif side == "SIM":
        st.info(
            "O teste do SIM ainda não usa filtro de ensemble, nowcast ou platô. "
            "Ele mede apenas H-1, preço e liquidez executável.")
    elif minimum:
        st.info(
            "As mínimas bloqueiam entradas quando o TAF prevê TSRA ou VCTS "
            "no restante do dia local. Ensemble, nowcast e platô continuam "
            "exclusivos das máximas.")
    if side != "SIM":
        filters = monitor.filter_frame(full_stats)
        st.dataframe(
            filters, hide_index=True, width="stretch",
            column_config={
                "Estratégia": st.column_config.TextColumn(width="small"),
                "Filtro": st.column_config.TextColumn(width="medium"),
                "Quando bloqueia": st.column_config.TextColumn(width="large"),
                "Entradas bloqueadas": st.column_config.NumberColumn(
                    width="small", format="%d"),
                "Observação": st.column_config.TextColumn(width="large"),
            })

        # Bloqueios por dia e por motivo. "Platô observado" não vira motivo
        # próprio: já está contido em "Desvio/nowcast quente".
        blocks = monitor.blocks_by_day_frame(full_stats)
        if not blocks.empty:
            st.markdown("**Bloqueios por dia (hora de Brasília)**")
            days = window_selector(f"blocks_window_{stats.get('archive_kind')}")
            st.plotly_chart(
                blocks_by_day_chart(blocks, days), width="stretch")
            st.caption(
                "Uma barra por dia com o total de bloqueios; cada fatia é um "
                "motivo. Platô já está somado em desvio/nowcast.")

        f1, f2, f3 = st.columns(3)
        f1.metric("Total de entradas bloqueadas",
                  full_stats.get("n_filtrado", 0))
        f2.metric("Terminariam em 100¢",
                  full_stats.get("n_filtrado_100c", 0))
        f3.metric("Terminariam em 0¢",
                  full_stats.get("n_filtrado_0c", 0))
        st.caption(
            "A contagem de platô já está incluída em desvio/nowcast. "
            "Os desfechos aparecem depois que o contrato é resolvido.")

        if full_stats.get("n_filtrado", 0):
            outcomes = pd.DataFrame({
                "Desfecho": ["Terminaria em 100¢", "Terminaria em 0¢"],
                "Entradas": [full_stats.get("n_filtrado_100c", 0),
                             full_stats.get("n_filtrado_0c", 0)],
            })
            fig = px.bar(outcomes, x="Entradas", y="Desfecho", orientation="h",
                         color="Desfecho", color_discrete_map={
                             "Terminaria em 100¢": MUTED,
                             "Terminaria em 0¢": GREEN})
            fig.update_layout(height=230, showlegend=False,
                              margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(dark_figure(fig), width="stretch")

    freshness = monitor.data_freshness(stats.get("archive_kind", "maximum"))
    st.subheader("Saúde do monitor")
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Cidades ativas", len(config.STATIONS))
    h2.metric("Arquivos de mercado", freshness["files"])
    h3.metric("Último dia capturado", freshness["latest_day"] or "—")
    h4.metric("Última atualização local",
              freshness["updated"].strftime("%d/%m %H:%M")
              if freshness["updated"] else "—")
    if consolidated:
        st.caption(
            "Consolidado ativo: NÃO de máximas + NÃO de mínimas · "
            "uma única banca · sem alavancagem.")
    elif minimum:
        st.caption(
            f"Regra ativa: NÃO entre {config.CEIFA_PRICE_MIN * 100:.1f}¢ e "
            f"{config.CEIFA_PRICE_MAX * 100:.1f}¢ · H-1 do mínimo previsto · "
            f"{config.CEIFA_STAKE_FRAC:.0%} do caixa livre a cada "
            f"{config.CEIFA_REPEAT_MINUTES} minutos · sem teto por contrato.")
    else:
        st.caption(
            f"Regra ativa: NÃO entre {config.CEIFA_PRICE_MIN * 100:.1f}¢ e "
            f"{config.CEIFA_PRICE_MAX * 100:.1f}¢ · H-1 · "
            f"{config.CEIFA_STAKE_FRAC:.0%} do caixa livre a cada "
            f"{config.CEIFA_REPEAT_MINUTES} minutos · sem alavancagem.")


def market_page(market: str) -> None:
    """Estudo observacional de um mercado binário diário (SPY, Bitcoin, ...)."""
    variants = load_market(market)
    latest = spy_study.latest_day(market)
    lado_a, lado_b = spy_study.side_labels(market)
    fechamento = spy_study.close_label(market)
    st.caption(
        f"Último dia capturado: {latest or '—'} · aloca no lado "
        f"({lado_a} ou {lado_b}) que estiver entre 95¢ e 99,8¢, com 1% do caixa "
        "livre a cada 5 min. Fase de observação — sem apostas reais.")

    daily = spy_study.daily_summary(market)
    prices = spy_study.latest_prices(market)
    strikes = spy_study.latest_strikes(market)
    if daily.empty and prices.empty and strikes.empty:
        st.info(
            "Ainda sem captura. A coleta roda a cada rodada no GitHub Actions; "
            "assim que houver snapshots, o dia aparece aqui. Clique em "
            "**Atualizar** para puxar o mais recente.")
        return

    # Multi-strike (Bitcoin): tabela dos strikes do último snapshot, marcando
    # quem está na faixa de compra (95–99,8¢) num dos lados.
    if not strikes.empty:
        st.subheader("Strikes do último snapshot")
        view = strikes.rename(columns={
            "faixa": "Strike", "preco_up": f"{lado_a} (¢)",
            "preco_down": f"{lado_b} (¢)", "na_faixa": "Na faixa"})
        for col in (f"{lado_a} (¢)", f"{lado_b} (¢)"):
            view[col] = (view[col].astype(float) * 100).round(1)
        st.dataframe(view, hide_index=True, width="stretch",
                     column_config={"Na faixa": st.column_config.CheckboxColumn()})
        st.caption(
            "Cada strike é um contrato. Qualquer lado (Yes/No) entre 95¢ e "
            "99,8¢ vira parcela — a coluna 'Na faixa' marca quais entrariam.")

    # Preços do dia com a faixa marcada (mercados binários, tipo SPY).
    if not prices.empty:
        days = spy_study.price_days(market)
        day_key = f"market_price_day_{market}"
        if st.session_state.get(day_key) not in days:
            st.session_state[day_key] = days[-1]
        day_index = days.index(st.session_state[day_key])
        previous_col, title_col, next_col = st.columns([1, 8, 1])
        if previous_col.button(
                "◀", key=f"price_previous_{market}",
                disabled=day_index == 0, help="Ver dia anterior"):
            st.session_state[day_key] = days[day_index - 1]
            st.rerun()
        dia = st.session_state[day_key]
        title_col.subheader(f"Preços do dia {dia}")
        if next_col.button(
                "▶", key=f"price_next_{market}",
                disabled=day_index == len(days) - 1, help="Ver próximo dia"):
            st.session_state[day_key] = days[day_index + 1]
            st.rerun()
        prices = spy_study.prices_for_day(market, dia)
        figp = go.Figure()
        # Um único snapshot precisa de marcador porque ainda não forma linha.
        # A partir do segundo, mantém a curva limpa como a do Bitcoin; o
        # hovertemplate abaixo continua exibindo o preço sem bolinhas visíveis.
        price_mode = "lines+markers" if len(prices) == 1 else "lines"
        brasilia_times = brasilia_time_labels(prices["ts"]).to_frame()
        hover_price = (
            "<b>%{fullData.name}</b>: %{y:.3f}"
            "<br>Brasília: %{customdata[0]}<extra></extra>")
        figp.add_hrect(y0=0.95, y1=0.998, fillcolor=GREEN, opacity=0.12,
                       line_width=0, annotation_text="faixa de compra",
                       annotation_position="top left")
        figp.add_trace(go.Scatter(
            x=prices["ts"], y=prices["preco_up"], name=lado_a,
            mode=price_mode, line=dict(color=BLUE, width=2),
            marker=dict(size=6), customdata=brasilia_times,
            hovertemplate=hover_price))
        figp.add_trace(go.Scatter(
            x=prices["ts"], y=prices["preco_down"], name=lado_b,
            mode=price_mode, line=dict(color=AMBER, width=2),
            marker=dict(size=6), customdata=brasilia_times,
            hovertemplate=hover_price))
        figp.update_layout(
            height=280, margin=dict(l=15, r=15, t=10, b=10),
            yaxis_title="Preço", xaxis_title=None, yaxis_range=[-0.02, 1.02],
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            hovermode="x unified", hoverdistance=-1,
            xaxis_hoverformat="%d/%m/%Y %H:%M UTC")
        st.plotly_chart(dark_figure(figp), width="stretch")
        st.caption(
            "Faixa verde = 95–99,8¢, onde a estratégia entra. Sem nenhum lado "
            "dentro dela, não há parcela naquele instante.")

    if daily.empty or daily["parcelas"].sum() == 0:
        resolvidos = daily["resolvido"].sum() if not daily.empty else 0
        if resolvidos:
            st.info(
                "O mercado do dia **resolveu**, mas nenhum snapshot capturado "
                "caiu na faixa (95–99,8¢) — então **0 parcelas**. Costuma "
                "acontecer quando o mercado já estava resolvido (preço em 0 ou "
                "1) durante as capturas.")
        else:
            st.info(
                "Capturando — ainda sem nenhum lado na faixa (95–99,8¢). "
                "As parcelas aparecem quando um dos lados entrar na faixa.")
        return

    # Gráfico dia a dia de parcelas — mostra sempre que há captura, mesmo com
    # o dia em andamento (barra âmbar = ainda em aberto; verde = já resolvido).
    st.subheader("Parcelas por dia")
    plot = daily.copy()
    plot["estado"] = plot["resolvido"].map(
        {True: "Resolvido", False: "Em aberto"})
    fig = px.bar(
        plot, x="dia", y="parcelas", color="estado", text="parcelas",
        color_discrete_map={"Resolvido": GREEN, "Em aberto": AMBER},
        custom_data=["resultado"])
    fig.update_traces(
        textposition="outside", cliponaxis=False,
        hovertemplate=("%{x|%d/%m}<br>Parcelas: %{y}"
                       "<br>%{customdata[0]}<extra></extra>"))
    fig.update_layout(
        height=300, margin=dict(l=15, r=15, t=10, b=10),
        xaxis_title=None, yaxis_title="Parcelas", legend_title_text=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_xaxes(showgrid=False)
    st.plotly_chart(dark_figure(fig), width="stretch")

    resolvidos = int(daily["resolvido"].sum())
    if not any(stats.get("n", 0) for _, stats in variants):
        abertas = int(daily.loc[~daily["resolvido"], "parcelas"].sum())
        st.info(
            f"**{abertas} parcela(s) em aberto** hoje (lado na faixa 95–99,8¢). "
            "O resultado financeiro por janela aparece depois do fechamento do "
            f"mercado ({fechamento}), quando o dia resolve.")
        return

    st.subheader("Resultado por janela de entrada")
    st.caption(
        "Rendimento percentual composto: cada parcela usa 1% do patrimônio "
        "ainda livre no dia (1%, depois 0,99%, depois 0,9801% da banca inicial "
        "do dia, e assim por diante). O saldo liquidado vira a base do dia "
        "seguinte.")
    rows = []
    for label, stats in variants:
        pick = stats.get("by_pick", {})
        per_day = stats.get("per_day", [])
        worst = min((d["ret"] for d in per_day), default=None)
        tail = monitor.observed_cvar(stats, 0.01)
        cvar = tail["cvar"] if tail else None
        rows.append({
            "Janela": label,
            "Parcelas": stats.get("n", 0),
            "Média parcelas/dia": num_or_dash(
                mean_daily_parcels(stats, resolvidos), digits=2),
            "Acerto": f"{stats.get('hit', 0):.2%}",
            "Erros": stats.get("n", 0) - stats.get("wins", 0),
            "Rendimento": pct(stats.get("real_mult", 1) - 1, 2),
            "Média diária": pct(mean_daily_return(stats), 2),
            "Pior dia": pct(worst, 2),
            "CVaR 1%": pct(cvar, 2),
            "Drawdown": f"{stats.get('real_dd', 0):.2%}",
            f"{lado_a}/{lado_b}": f"{pick.get('up', 0)}/{pick.get('down', 0)}",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        f"{resolvidos} dia(s) resolvido(s). **Média parcelas/dia** = parcelas "
        "da janela divididas por todos os dias resolvidos, incluindo dias com "
        "zero entrada; **Média diária** = média aritmética dos retornos dos "
        "dias com parcelas naquela janela; **Pior dia** = o "
        "retorno do dia mais negativo; **CVaR 1%** = média dos 1% piores dias "
        "(com histórico curto equivale ao pior dia). H-n = só as parcelas nas "
        f"últimas n horas antes do fechamento ({fechamento}); 'Sem janela' "
        "entra o dia todo. "
        f"{lado_a}/{lado_b} = parcelas em cada lado.")

    base = variants[0][1]
    if base.get("per_day"):
        left, right = st.columns(2)
        with left:
            st.subheader("Evolução (sem janela)")
            st.plotly_chart(equity_chart(base), width="stretch")
        with right:
            st.subheader("Retorno de cada dia")
            st.plotly_chart(daily_chart(base), width="stretch")

    render_hourly_section(base, (lado_a, lado_b))


def main() -> None:
    refresh_notice = st.session_state.pop("refresh_notice", None)
    if refresh_notice:
        if refresh_notice["ok"]:
            st.toast(refresh_notice["message"], icon="✅")
        else:
            st.warning(refresh_notice["message"])

    with st.container(key="bottom_navigation"):
        area = st.radio(
            "Navegação",
            ["✅  Em produção", "🧪  Em teste (hipóteses)"],
            horizontal=True,
            label_visibility="collapsed",
            key="area_navigation",
        )
    producao = "produção" in area

    with st.container(key="strategy_picker"):
        pick_col, period_col, refresh_col = st.columns(
            [2.6, 1.25, .65], vertical_alignment="bottom")
        if producao:
            # Só a visão consolidada faz sentido (máximas + mínimas numa banca).
            choice = "📊 Ativas consolidadas"
            pick_col.markdown(
                "<div style='display:flex;align-items:center;min-height:2.6rem;"
                "padding-bottom:.15rem;font-weight:600;font-size:1.02rem'>"
                "📊 Estratégias ativas consolidadas</div>",
                unsafe_allow_html=True)
        else:
            choice = pick_col.radio(
                "Hipótese em teste",
                ["◇ SPY", "↕ BTC Up/Down", "↕ SOL Up/Down",
                 "▲ SPY Above", "₿ Bitcoin Above", "◎ Solana Above"],
                horizontal=True, key="test_navigation")
        period_label = period_col.selectbox(
            "Período",
            ["Todo o histórico", "Últimos 30 dias", "Últimos 14 dias",
             "Últimos 7 dias"],
            key="period_navigation")
        if refresh_col.button("↻ Atualizar", width="stretch"):
            with st.spinner("Buscando os dados mais recentes no GitHub…"):
                refresh_result = monitor.sync_dashboard_data()
            if refresh_result.get("updated"):
                load_strategy.clear()
                load_losses.clear()
                load_timeline.clear()
                load_market.clear()
            st.session_state["refresh_notice"] = refresh_result
            st.rerun()

    lookback = {
        "Todo o histórico": None, "Últimos 30 dias": 30,
        "Últimos 14 dias": 14, "Últimos 7 dias": 7,
    }[period_label]

    # Área "Em teste": mercados binários diários (SPY, Bitcoin, Solana).
    # sem lado NÃO/SIM nem filtros meteorológicos.
    if not producao:
        market = {
            "◇ SPY": "spy",
            "↕ BTC Up/Down": "btc_updown",
            "↕ SOL Up/Down": "sol_updown",
            "▲ SPY Above": "spy_above",
            "₿ Bitcoin Above": "bitcoin",
            "◎ Solana Above": "solana",
        }[choice]
        if market not in MERCADOS:
            # O Streamlit recarrega este arquivo mas mantém o pacote spy antigo
            # em memória (sem o mercado novo). Reiniciar o processo resolve.
            st.warning(
                f"O mercado **{choice}** ainda não está carregado nesta sessão. "
                "O Streamlit recarregou a tela mas manteve o código antigo em "
                "memória. **Reinicie o processo** (feche o `streamlit run` e "
                "suba de novo) para carregar — recarregar a aba não basta.")
            return
        hero(f"{MERCADOS[market].nome} · hipótese em teste · aloca no lado na "
             "faixa 95–99,8¢, 1% do caixa livre a cada 5 min.")
        market_page(market)
        return

    # Daqui pra baixo é só produção (estratégias ativas, lado NÃO).
    consolidated = choice == "📊 Ativas consolidadas"
    side = "NÃO"
    kind = ("consolidated" if consolidated else
            "minimum" if choice == "❄️ Mínimas" else "maximum")
    minimum = kind == "minimum"

    if consolidated:
        subtitle = ("Estratégias ativas em produção · NÃO de máximas e mínimas "
                    "numa única banca compartilhada.")
    else:
        subtitle = (f"Temperaturas {'mínimas' if minimum else 'máximas'} · "
                    "estratégia ativa (NÃO), parcelada a cada cinco minutos.")
    hero(subtitle)
    freshness = monitor.data_freshness(kind)
    st.caption(
        f"Último dia capturado: {freshness['latest_day'] or '—'} · "
        "indicadores calculados exclusivamente a partir dos nossos snapshots.")

    experimental_stats = None
    proximity_stats = None
    if consolidated:
        maximum_stats = load_strategy("maximum", "NÃO")
        minimum_stats = load_strategy("minimum", "NÃO")
        full_stats = monitor.combine_active_strategies(
            maximum_stats, minimum_stats)
        experimental_full = monitor.single_band_scenario(
            maximum_stats, minimum_stats)
        experimental_stats = monitor.slice_strategy(
            experimental_full, lookback)
        proximity_full = monitor.proximity_scenario(
            maximum_stats, minimum_stats)
        proximity_stats = monitor.slice_strategy(
            proximity_full, lookback)
    else:
        full_stats = load_strategy(kind, side)
    stats = monitor.slice_strategy(full_stats, lookback)

    # Visão geral + Erros + Cidades e filtros reunidos em abas.
    tab_overview, tab_errors, tab_cities = st.tabs(
        ["▦ Visão geral", "◎ Erros", "⌁ Cidades e filtros"])
    with tab_overview:
        overview(stats, full_stats, minimum, side, consolidated,
                 experimental_stats, proximity_stats)
    with tab_errors:
        errors_page(stats)
    with tab_cities:
        cities_page(stats, full_stats, consolidated)


if __name__ == "__main__":
    main()
