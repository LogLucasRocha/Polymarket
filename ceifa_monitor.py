"""Monitor local da estratégia Ceifa.

Inicie com ``python -m streamlit run ceifa_monitor.py`` ou pelo atalho criado
na Área de Trabalho.
"""
from __future__ import annotations

import math

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

GREEN = "#38d39f"
RED = "#ff6b6b"
AMBER = "#f2b84b"
INK = "#dce8e3"
MUTED = "#8fa39a"
BLUE = "#68a8ff"
PLOT = "#0c1319"
GRID = "#24323b"

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


@st.cache_data
def load_losses(stats: dict) -> pd.DataFrame:
    return monitor.loss_details(stats)


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


def daily_chart(stats: dict) -> go.Figure:
    daily = pd.DataFrame(stats.get("per_day", []))
    if daily.empty:
        return go.Figure()
    daily["day"] = pd.to_datetime(daily["day"])
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
    fig.update_layout(
        height=330, margin=dict(l=15, r=15, t=10, b=10),
        xaxis_title=None, yaxis_title="Retorno do dia (%)",
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False)
    return dark_figure(fig)


def overview(stats: dict, full_stats: dict, minimum: bool, side: str,
             consolidated: bool = False) -> None:
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

    left, right = st.columns([1.65, 1])
    with left:
        st.subheader("Evolução da estratégia")
        st.plotly_chart(equity_chart(stats), width="stretch")
    with right:
        st.subheader("Leitura de risco")
        r1, r2 = st.columns(2)
        r1.metric("Melhor dia", pct(risk["best_day"], 2))
        r2.metric("Pior dia", pct(risk["worst_day"], 2))
        r3, r4 = st.columns(2)
        r3.metric("Fator de lucro",
                  "∞" if math.isinf(risk["profit_factor"])
                  else f"{risk['profit_factor']:.2f}x")
        r4.metric("Contratos com erro", str(unique_losses))
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
            "Fator de lucro = ganhos brutos ÷ perdas brutas. As parcelas "
            "repetidas no mesmo contrato não são eventos independentes.")

    st.subheader("Retorno de cada dia")
    st.plotly_chart(daily_chart(stats), width="stretch")
    if daily:
        st.caption(
            f"Período com {len(daily)} dia(s) de apostas · melhor dia "
            f"{pct(risk['best_day'], 2)} · pior dia {pct(risk['worst_day'], 2)}.")


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
            })
        losses = pd.DataFrame(rows).sort_values(
            ["Data", "Estratégia", "Cidade"], ascending=[False, True, True])
        c1, c2, c3 = st.columns(3)
        c1.metric("Erros", len(losses))
        c2.metric("Máximas", int((losses["Estratégia"] == "Máxima").sum()))
        c3.metric("Mínimas", int((losses["Estratégia"] == "Mínima").sum()))
        st.info(
            "Esta é a lista consolidada. Para abrir a autópsia meteorológica "
            "de um contrato, selecione Máximas ou Mínimas no topo.")
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
            day_losses[["Cidade", "Dia", "Faixa", "Preço (¢)", "Diagnóstico"]],
            hide_index=True, width="stretch")
        return

    a, b, c, d, e = st.columns(5)
    unit = selected["Unidade"]
    a.metric("Observado na entrada",
             f"{selected['Observado na entrada']:.1f} °{unit}")
    b.metric("Máxima final", f"{selected['Máxima final']:.1f} °{unit}",
             selected["Horário da máxima"])
    c.metric("Mediana", f"{selected['Mediana']:.1f} °{unit}")
    d.metric("P90", f"{selected['P90']:.1f} °{unit}")
    e.metric("Spread", f"{selected['Spread (°C)']:.2f} °C")

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
                        "Diagnóstico"]],
            hide_index=True, width="stretch")
        return

    st.subheader("Por que os filtros permitiram a entrada")
    diagnostic = pd.DataFrame([
        ["Desvio observado usado", selected["Desvio usado (°C)"],
         config.CEIFA_OBS_DEVIATION_MIN,
         "Passou" if selected["Desvio usado (°C)"] < config.CEIFA_OBS_DEVIATION_MIN else "Cortaria"],
        ["Desvio usando a hora real do METAR", selected["Desvio com hora METAR (°C)"],
         config.CEIFA_OBS_DEVIATION_MIN,
         "Passou" if selected["Desvio com hora METAR (°C)"] < config.CEIFA_OBS_DEVIATION_MIN else "Cortaria"],
        ["Nowcast shift", selected["Shift (°C)"],
         config.CEIFA_NOWCAST_SHIFT_MIN,
         "Passou" if selected["Shift (°C)"] < config.CEIFA_NOWCAST_SHIFT_MIN else "Cortaria"],
        ["Spread absoluto", selected["Spread (°C)"],
         config.CEIFA_SPREAD_ABS,
         "Passou" if selected["Spread (°C)"] < config.CEIFA_SPREAD_ABS else "Cortaria"],
    ], columns=["Indicador", "Valor", "Limite", "Resultado"])
    st.dataframe(diagnostic.style.format({"Valor": "{:.2f} °C", "Limite": "{:.2f} °C"}),
                 hide_index=True, width="stretch")

    st.subheader(f"Erros em {selected_day_label}")
    table = day_losses[["Cidade", "Dia", "Faixa", "Preço (¢)",
                       "Observado na entrada", "Máxima final", "Diagnóstico"]].copy()
    st.dataframe(table, hide_index=True, width="stretch")


def cities_page(stats: dict, full_stats: dict,
                consolidated: bool = False) -> None:
    minimum = stats.get("archive_kind") == "minimum"
    side = stats.get("side", "NAO")
    cities = monitor.city_frame(stats)
    st.subheader("Desempenho por cidade")
    if not cities.empty:
        view = cities.copy()
        view["Assertividade"] = view["Assertividade"].map(lambda value: f"{value:.1%}")
        st.dataframe(view, hide_index=True, width="stretch", height=460)

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


def main() -> None:
    refresh_notice = st.session_state.pop("refresh_notice", None)
    if refresh_notice:
        if refresh_notice["ok"]:
            st.toast(refresh_notice["message"], icon="✅")
        else:
            st.warning(refresh_notice["message"])

    with st.container(key="bottom_navigation"):
        page = st.radio(
            "Navegação",
            ["▦  Visão geral", "◎  Erros", "⌁  Cidades e filtros"],
            horizontal=True,
            label_visibility="collapsed",
            key="page_navigation",
        )

    with st.container(key="strategy_picker"):
        strategy_col, side_col, period_col, refresh_col = st.columns(
            [1.8, 1.45, 1.25, .65], vertical_alignment="bottom")
        strategy_label = strategy_col.radio(
            "Estratégia monitorada",
            ["📊 Ativas consolidadas", "🌡️ Máximas", "❄️ Mínimas"],
            horizontal=True,
            key="strategy_navigation")
        consolidated = strategy_label == "📊 Ativas consolidadas"
        side_label = side_col.radio(
            "Lado testado", ["NÃO", "SIM"], horizontal=True,
            key="side_navigation", disabled=consolidated)
        period_label = period_col.selectbox(
            "Período",
            ["Todo o histórico", "Últimos 30 dias", "Últimos 14 dias",
             "Últimos 7 dias"],
            key="period_navigation")
        if refresh_col.button("↻ Atualizar", width="stretch"):
            with st.spinner("Buscando os dados mais recentes no GitHub…"):
                refresh_result = monitor.sync_dashboard_data()
            load_strategy.clear()
            load_losses.clear()
            load_timeline.clear()
            st.session_state["refresh_notice"] = refresh_result
            st.rerun()

    kind = ("consolidated" if consolidated else
            "minimum" if strategy_label == "❄️ Mínimas" else "maximum")
    minimum = kind == "minimum"
    side = "NÃO" if consolidated else side_label
    if consolidated:
        subtitle = (
            "Retorno conjunto do NÃO de máximas e mínimas, calculado com "
            "uma única banca compartilhada.")
    elif side == "SIM":
        subtitle = (
            f"Temperaturas {'mínimas' if minimum else 'máximas'} · teste do SIM "
            "em observação, sem apostas reais.")
    else:
        subtitle = (
            "Temperaturas mínimas · estratégia ativa, parcelada a cada cinco minutos."
            if minimum else
            "Temperaturas máximas · estratégia ativa, parcelada a cada cinco minutos.")
    hero(subtitle)
    freshness = monitor.data_freshness(kind)
    st.caption(
        f"Último dia capturado: {freshness['latest_day'] or '—'} · "
        "indicadores calculados exclusivamente a partir dos nossos snapshots.")

    lookback = {
        "Todo o histórico": None, "Últimos 30 dias": 30,
        "Últimos 14 dias": 14, "Últimos 7 dias": 7,
    }[period_label]
    if consolidated:
        full_stats = monitor.combine_active_strategies(
            load_strategy("maximum", "NÃO"),
            load_strategy("minimum", "NÃO"),
        )
    else:
        full_stats = load_strategy(kind, side)
    stats = monitor.slice_strategy(full_stats, lookback)
    if "Visão geral" in page:
        overview(stats, full_stats, minimum, side, consolidated)
    elif "Erros" in page:
        errors_page(stats)
    else:
        cities_page(stats, full_stats, consolidated)


if __name__ == "__main__":
    main()
