"""Envia a previsão de máxima das estações configuradas para o Telegram.

Uso local:
    set TELEGRAM_TOKEN=123:abc      (Windows: use "set"; Linux/CI: export)
    set TELEGRAM_CHAT_ID=987654321
    python send_telegram.py [--station SBGR ...]

Na nuvem roda pelo GitHub Actions (.github/workflows/main.yml), com o
token e o chat_id guardados como *secrets* do repositório.

Modo silencioso — o Telegram só recebe:
  1. Alertas de compra — estratégias CEIFA de MÁXIMA e MÍNIMA (Edge e
     Colheita desligadas). Compra o NÃO na hora local anterior ao extremo
     previsto (H-1)
     quando CEIFA_PRICE_MIN < preço do NÃO < CEIFA_PRICE_MAX (só o preço, mas
     só na janela H-1 — perto do pico há pouca incerteza). O alerta REPETE a
     cada rodada de 5 minutos da H-1, mesmo se já houver posição: cada aviso é
     uma nova parcela de 1% do saldo livre no momento do sinal. A 1ª
     aparição leva o bloco enxuto: um gráfico com a TRAJETÓRIA hora a hora + a
     distribuição, e texto com o horário local, o extremo previsto, a
     mediana, P10/P90 e o piso/teto do ensemble — SEM tabela de probabilidades;
     as repetições vêm em texto curto. O desempenho da Ceifa vai num relatório
     diário às 06:00 (run_ceifa.py), medido SÓ nos nossos snapshots (dados/).
  2. Para cidades com posição aberta: SEM bloco — apenas avisos pontuais em
     texto de platô (2h de lado) e fuga do envelope do ensemble, uma vez por
     episódio.
  3. Stop loss: claro e urgente, texto puro, toda rodada enquanto o mercado
     estiver ≥ STOP_ALERT_FRAC abaixo da entrada.

Comandos (getUpdates, processados a cada rodada — latência de até ~5 min):
  • /relatorio <cidade>  → bloco completo de qualquer cidade (ICAO ou nome)
  • /cidades             → lista as cidades monitoradas
  • /ajuda               → como usar o bot
O botão dos alertas dispara o mesmo relatório completo da cidade.

Tudo D0 (o D+1 não aparece); com a máxima travada (TMAX_LOCK_HOURS), sinais
e tabela somem e o hora a hora corta as horas restantes. Estações sem
novidade (data/digest_state.json) são omitidas.
"""
from __future__ import annotations

# Proxy corporativo (máquina Windows local): usa os certificados do sistema.
# Em ambientes sem o proxy (GitHub Actions) o import pode não existir/ser
# desnecessário — por isso é opcional.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import re
import sys
import time
import unicodedata

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tmax import (calibration, capture, ceifa, config, distribution, notify,
                  pipeline, polymarket)
from lowtemp import capture as minimum_capture


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--station", action="append", choices=sorted(config.STATIONS),
                    help="estação a incluir (repetível; padrão: todas)")
    ap.add_argument("--force-bias", action="store_true",
                    help="recalcula a correção de viés mesmo com cache válido")
    ap.add_argument("--no-positions", action="store_true",
                    help="não anexa o resumo de posições da Polymarket "
                         "(mesmo com POLYMARKET_WALLET definido)")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERRO: defina TELEGRAM_TOKEN e TELEGRAM_CHAT_ID no ambiente.",
              file=sys.stderr)
        return 2

    icaos = args.station or list(config.STATIONS)
    stations = [config.STATIONS[i] for i in icaos]

    # 1) Monta o contexto de todas as estações antes de enviar qualquer coisa:
    # as posições (que vão primeiro) dependem das distribuições para estimar a
    # chance de cada aposta dar certo.
    contexts: dict[str, dict] = {}
    errors: dict[str, str] = {}

    def _build(station):
        def log(msg: str, _s=station) -> None:
            print(f"[{_s.icao}] {msg}")
        return pipeline.build_context(station, force_bias=args.force_bias,
                                      log=log)

    # Paralelo: com ~25 estações, sequencial estouraria o timeout do job.
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_build, s): s for s in stations}
        for fut in cf.as_completed(futs):
            s = futs[fut]
            try:
                contexts[s.icao] = fut.result()
            except Exception as exc:  # noqa: BLE001 — falha de uma estação não derruba o resto
                errors[s.icao] = str(exc)
                print(f"[{s.icao}] ERRO: {exc}", file=sys.stderr)

    def yes_prob(title: str | None, end_iso) -> float | None:
        """Nossa P(o Yes acontecer) para um mercado de máxima, pela previsão
        atual (última observação + ensemble corrigido). None se o mercado não
        casa com uma estação/dia que temos."""
        ev = polymarket.parse_temp_market(title)
        if not ev:
            return None
        ctx = contexts.get(ev["icao"])
        if not ctx:
            return None
        try:
            end_date = dt.date.fromisoformat(str(end_iso or "")[:10])
        except ValueError:
            return None
        dist = (ctx["dist_d0"] if end_date == ctx["d0"]
                else ctx["dist_d1"] if end_date == ctx["d1"] else None)
        p = distribution.market_prob(dist, ev["lo"], ev["hi"], ev["mode"],
                                     ev["unit"])
        # Probabilidade CALIBRADA (curva empírica do backtest): D0 usa o
        # período do dia local; D+1 usa o período menos informado, porque a
        # projeção de amanhã sabe ainda menos que a madrugada de hoje.
        hour = ctx["now"].hour if end_date == ctx["d0"] else None
        return calibration.apply(p, hour)

    def position_success_prob(p: dict) -> float | None:
        """P(a posição dar certo): P(Yes) se apostou Yes, senão 1−P(Yes) —
        a mesma Probabilidade Real (modelo calibrado) da tabela de odds."""
        p_yes = yes_prob(p.get("title"), p.get("endDate"))
        if p_yes is None:
            return None
        outcome = str(p.get("outcome") or "").strip().lower()
        if outcome == "yes":
            return p_yes
        if outcome == "no":
            return 1.0 - p_yes
        return None

    state = _load_digest_state()
    station_state = state.get("stations", {})

    # Novidade por estação: observado/projeção diferentes do último envio.
    # Estação com contexto quebrado conta como novidade (envia o aviso).
    fps = {s.icao: (_station_fingerprint(contexts[s.icao])
                    if s.icao in contexts else None) for s in stations}
    novidade = {s.icao for s in stations
                if fps[s.icao] is None
                or station_state.get(s.icao) != fps[s.icao]}

    # Sinais são computados cedo para identificar oportunidades novas; as
    # repetições posteriores vêm sozinhas em texto.
    signal_rows = _collect_signal_rows(stations, contexts, yes_prob)
    if config.CEIFA_MINIMUM_ENABLED:
        (minimum_signal_rows, minimum_forecasts,
         minimum_members) = _collect_minimum_signal_rows(stations, contexts)
    else:
        minimum_signal_rows, minimum_forecasts, minimum_members = {}, {}, {}
    prev_probs = state.get("signal_probs", {})
    # Estratégia Edge pausada (config.EDGE_ENABLED): sem edge, o digest opera só
    # a colheita. Zerar aqui propaga para sig_msgs, fresh_icaos e a captura.
    edges_now = ({k: v for k, v in signal_rows.items() if _is_edge(v)}
                 if config.EDGE_ENABLED else {})
    new_edges = {k: v for k, v in edges_now.items()
                 if _in_signal_window(v["icao"])}
    prev_edges = state.get("edges", {})
    fresh_icaos = {v["icao"] for k, v in new_edges.items()
                   if k not in prev_edges}

    # Captura ao vivo: previsão derivada (toda rodada) + ensemble bruto (dedup
    # por ciclo) de cada estação. O mercado é capturado dentro de
    # _collect_signal_rows (reaproveitando a busca do evento).
    for s in stations:
        c = contexts.get(s.icao)
        if c is not None:
            _cap(_capture_context, s, c)

    # 2) Posições: buscadas TODA rodada para os controles internos. O resumo
    # geral só é enviado se POSITIONS_SUMMARY_ENABLED estiver explicitamente
    # ligado; por padrão permanece desativado.
    positions: list[dict] = []
    wallet = os.environ.get("POLYMARKET_WALLET")
    if wallet and not args.no_positions:
        try:
            positions = polymarket.fetch_positions(wallet)
        except Exception as exc:  # noqa: BLE001 — leitura da carteira é acessório
            print(f"[polymarket] ERRO ao ler posições: {exc}", file=sys.stderr)
        # Evolução do portfólio: no máximo 1x por hora (decisão do Lucas).
        pnl_sent_at = float(state.get("pnl_sent_at") or 0)
        if (config.POSITIONS_SUMMARY_ENABLED and positions and novidade
                and time.time() - pnl_sent_at >= 3600):
            try:
                notify.send_message(
                    token, chat_id,
                    polymarket.positions_message(positions,
                                                 position_success_prob))
                state["pnl_sent_at"] = time.time()
                print("[polymarket] posições enviadas.")
            except Exception as exc:  # noqa: BLE001
                print(f"[polymarket] ERRO no resumo: {exc}", file=sys.stderr)

    # 2b) Stop loss: mercado precificando a posição STOP_ALERT_FRAC (ou mais)
    # abaixo da entrada → alerta em TODA rodada enquanto persistir (pedido
    # explícito: não parar de mandar até sumir).
    _cap(lambda: capture.record_stops(dt.datetime.now(dt.timezone.utc),
                                      _stop_rows(positions)))
    # Decisão (22/07): o stop loss saiu — no lugar entrou o filtro de incerteza
    # (não entra em dia de ensemble largo na H-1). O alerta de "SAIR −15%" agora
    # contradiz a estratégia (o stop não pega gap e vende whipsaw), então só
    # dispara se CEIFA_STOP_ENABLED voltar a True. Seguimos gravando o
    # record_stops acima só como dado de drawdown para análise.
    stop_msg = _stop_alerts(positions) if config.CEIFA_STOP_ENABLED else None
    if stop_msg:
        try:
            notify.send_message(token, chat_id, stop_msg)
            print("[stop] alerta de stop loss enviado.")
        except Exception as exc:  # noqa: BLE001
            print(f"[stop] ERRO: {exc}", file=sys.stderr)

    # Cidades onde há posição aberta — o centro do modo silencioso: só elas
    # recebem bloco completo e alertas de condição.
    pos_icaos = set()
    for p in positions:
        if p.get("redeemable"):
            continue
        pm = polymarket.parse_temp_market(p.get("title"))
        if pm:
            pos_icaos.add(pm["icao"])

    # 2c) Alertas de condição observada — platô de 2h ("andou de lado") e fuga
    # do envelope do ensemble ("acima do teto / abaixo do piso"). DESLIGADOS
    # por config.COND_ALERTS_ENABLED (decisão do Lucas, 16/07). Reversível.
    cond_state = state.get("cond_alerts", {})
    for station in (stations if config.COND_ALERTS_ENABLED else ()):
        ctx = contexts.get(station.icao)
        if ctx is None or station.icao not in pos_icaos:
            continue
        for kind, res in (("flat", _flat_alert(ctx)),
                          ("ens", _ens_escape_alert(ctx))):
            skey = f"{station.icao}:{kind}"
            if res is None:
                cond_state.pop(skey, None)
                continue
            key, texto = res
            if cond_state.get(skey) == key:
                continue  # mesmo episódio já avisado
            try:
                notify.send_message(token, chat_id, texto)
                cond_state[skey] = key
                print(f"[{station.icao}] alerta de condição ({kind}).")
            except Exception as exc:  # noqa: BLE001 — alerta é acessório
                print(f"[{station.icao}] ERRO no alerta {kind}: {exc}",
                      file=sys.stderr)

    # 2d) Ceifa de máximas (ativa): comprar o NÃO quando
    # CEIFA_PRICE_MIN < preço do NÃO < CEIFA_PRICE_MAX, na H-1, desde que os
    # vetos de incerteza (ensemble largo / nowcast quente) permitam. O
    # alerta REPETE em toda rodada elegível, mesmo quando já existe posição:
    # cada aviso corresponde a uma nova parcela relativa ao saldo livre atual.
    ceifa_seen = set(state.get("ceifa", []))
    ceifa_last_sent = state.get("ceifa_last_sent", {})
    run_now_utc = dt.datetime.now(dt.timezone.utc)
    ceifa_pending: dict[str, list] = {}     # máximas: icao -> contratos
    minimum_pending: dict[str, list] = {}   # mínimas: icao -> contratos
    ceifa_fresh: set[tuple[str, str]] = set()  # (extremo, icao) novo
    ceifa_keep: list = []
    # H-1: a entrada é SÓ na hora local ANTERIOR ao pico previsto pelo modelo
    # (H = hora do pico). Perto do pico há pouca incerteza — é onde o mercado
    # quase-certo é confiável (decisão do Lucas, 15/07).
    peak_by_icao = {s.icao: _cap(_peak_hour, contexts[s.icao])
                    for s in stations if s.icao in contexts}
    # FILTRO DE INCERTEZA (mesma regra do backtest, agora AO VIVO): não alerta
    # entrada em dia de ensemble largo na H-1. spread_norm = spread normal por
    # cidade (do lago de dados); o spread de agora vem do contexto. Fonte única:
    # ceifa.is_uncertain — backtest e ao vivo nunca divergem.
    spread_norm = _cap(ceifa.spread_norm_map) or {}
    spread_agora: dict[str, float | None] = {}     # cache por cidade (1 spread/dia)
    if config.CEIFA_ENABLED:
        for k, v in signal_rows.items():
            icao = v["icao"]
            if v["yes"] is None:
                continue
            price = ceifa.normalize_market_price(
                v.get("no_ask"))              # oferta realmente comprável do NÃO
            if price is None:
                indicative = ceifa.normalize_market_price(v.get("no"))
                if ceifa.is_ceifa_price(indicative):
                    print(f"[ceifa] {icao}: sem oferta executável para NÃO "
                          f"{v['label']} (indicativo=${indicative:.3f}).")
                continue
            if not ceifa.is_ceifa_price(price):
                continue
            H = peak_by_icao.get(icao)
            ctx_i = contexts.get(icao)
            if H is None or ctx_i is None or ctx_i["now"].hour != (H - 1) % 24:
                continue                     # fora da janela H-1
            if icao not in spread_agora:
                spread_agora[icao] = _cap(_ens_spread, ctx_i)
            spr = spread_agora[icao]
            if ceifa.is_uncertain(icao, spr, spread_norm):
                print(f"[ceifa] {icao}: filtrado — ensemble largo na H-1 "
                      f"(spread={spr:.1f}°C).")
                continue                     # dia perigoso → não entra
            q = ctx_i["dist_d0"]["quantiles"]
            observed_deviation = (ctx_i.get("nowcast") or {}).get("offset")
            plateau_temp = ceifa.plateau_temperature(ctx_i["obs_today"])
            if ceifa.is_warm_target_risk(
                    icao, v["label"], ctx_i.get("shift"), q.get(50), q.get(90),
                    observed_deviation=observed_deviation,
                    plateau_temp=plateau_temp):
                desvio_txt = (f"{observed_deviation:+.1f}°C"
                              if observed_deviation is not None else "—")
                print(f"[ceifa] {icao}: filtrado — desvio/nowcast quente na H-1 "
                      f"(desvio={desvio_txt}, ajuste={ctx_i['shift']:+.1f}°C, "
                      f"faixa={v['label']}, "
                      f"mediana={q.get(50):.1f}°C, P90={q.get(90):.1f}°C).")
                continue
            ceifa_keep.append(k)
            if not _ceifa_send_due(
                    ceifa_last_sent.get(k), run_now_utc,
                    config.CEIFA_REPEAT_MINUTES):
                continue
            ceifa_pending.setdefault(icao, []).append(
                (k, v["label"], price, v.get("no_ask_size")))
            if k not in ceifa_seen:
                ceifa_fresh.add(("maximum", icao))

    # MÍNIMAS ATIVAS: mesma banda, livro executável, H-1 e repetição das
    # máximas. Os vetos de cauda quente não se aplicam ao extremo frio. Em
    # contrapartida, TSRA/VCTS previstos no TAF para o restante do dia local
    # bloqueiam a entrada, pois chuva convectiva pode criar uma nova mínima.
    minimum_taf_logged: set[str] = set()
    if config.CEIFA_MINIMUM_ENABLED:
        for k, value in minimum_signal_rows.items():
            icao = value["icao"]
            price = ceifa.normalize_market_price(value.get("no_ask"))
            if price is None:
                indicative = ceifa.normalize_market_price(value.get("no"))
                if ceifa.is_ceifa_price(indicative):
                    print(f"[ceifa mínima] {icao}: sem oferta executável para "
                          f"NÃO {value['label']} "
                          f"(indicativo=${indicative:.3f}).")
                continue
            if not ceifa.is_ceifa_price(price):
                continue
            forecast = minimum_forecasts.get(icao) or {}
            cold_hour = forecast.get("pico_hora")
            ctx_i = contexts.get(icao)
            if (cold_hour is None or ctx_i is None
                    or ctx_i["now"].hour != (int(cold_hour) - 1) % 24):
                continue
            taf_risk = ctx_i.get("taf_minimum_convection") or []
            if config.CEIFA_MINIMUM_TAF_FILTER and taf_risk:
                if icao not in minimum_taf_logged:
                    codes = sorted({code for window in taf_risk
                                    for code in window.get("codes", [])})
                    print(f"[ceifa mínima] {icao}: filtrado pelo TAF "
                          f"({', '.join(codes)} no restante do dia local).")
                    minimum_taf_logged.add(icao)
                continue
            ceifa_keep.append(k)
            if not _ceifa_send_due(
                    ceifa_last_sent.get(k), run_now_utc,
                    config.CEIFA_REPEAT_MINUTES):
                continue
            minimum_pending.setdefault(icao, []).append(
                (k, value["label"], price, value.get("no_ask_size")))
            if k not in ceifa_seen:
                ceifa_fresh.add(("minimum", icao))

    # Stake relativa: no instante de cada rodada com oportunidade, recomenda
    # 1% do pUSD livre. Se as compras anteriores foram executadas, o próprio
    # saldo menor reduz naturalmente a parcela seguinte.
    free_pusd = None
    if ceifa_pending or minimum_pending:
        if not wallet:
            print("[ceifa] ERRO ao apurar stake atual: carteira não definida.",
                  file=sys.stderr)
            ceifa_pending, minimum_pending = {}, {}
        else:
            try:
                free_pusd, first_stake = _current_ceifa_stake(wallet)
                combined_pending, order = {}, []
                for station in stations:
                    for extreme, pending in (
                            ("maximum", ceifa_pending),
                            ("minimum", minimum_pending)):
                        contracts = pending.get(station.icao)
                        if not contracts:
                            continue
                        group = f"{extreme}:{station.icao}"
                        combined_pending[group] = contracts
                        order.append(group)
                allocated = _allocate_relative_stakes(
                    combined_pending, order, free_pusd,
                    config.CEIFA_STAKE_FRAC)
                ceifa_pending = {
                    station.icao: allocated[f"maximum:{station.icao}"]
                    for station in stations
                    if f"maximum:{station.icao}" in allocated
                }
                minimum_pending = {
                    station.icao: allocated[f"minimum:{station.icao}"]
                    for station in stations
                    if f"minimum:{station.icao}" in allocated
                }
                print(f"[ceifa] primeira stake atual: ${first_stake:.2f} "
                      f"de ${free_pusd:.2f} livres.")
            except Exception as exc:
                print(f"[ceifa] ERRO ao apurar stake atual: {exc}",
                      file=sys.stderr)
                ceifa_pending, minimum_pending = {}, {}

    # 3) Um alerta por cidade com oportunidade de Ceifa. A PRIMEIRA aparição
    # leva o bloco enxuto (gráfico da distribuição do ensemble + TAF + mediana,
    # texto com pico, mediana, P10/P90 e teto); as repetições também mostram o
    # resumo do ensemble, mas continuam sem gráfico ou hora a hora.
    for station in stations:
        icao = station.icao
        contratos = ceifa_pending.get(icao)
        if not contratos:
            continue
        ctx = contexts.get(icao)
        fp = fps[icao]
        try:
            if ("maximum", icao) in ceifa_fresh and ctx is not None:
                _send_ceifa_block(
                    token, chat_id, station, ctx, contratos,
                    extreme="maximum")
                _cap(lambda st=station, c=ctx: capture.record_report(
                    c["now"], st.icao, c["d0"], _report_snapshot(st, c)))
                marca = "bloco"
            else:
                notify.send_message(
                    token, chat_id,
                    _ceifa_repeat_text(
                        station, ctx, contratos, extreme="maximum"))
                marca = "repetição"
            if fp is not None:
                station_state[icao] = fp
            for key, *_rest in contratos:
                ceifa_last_sent[key] = run_now_utc.isoformat()
            print(f"[ceifa] {icao}: {len(contratos)} contrato(s) ({marca}).")
        except Exception as exc:  # noqa: BLE001 — falha de uma cidade não derruba as demais
            print(f"[ceifa] {icao}: ERRO: {exc}", file=sys.stderr)

    for station in stations:
        icao = station.icao
        contratos = minimum_pending.get(icao)
        if not contratos:
            continue
        ctx = contexts.get(icao)
        forecast = minimum_forecasts.get(icao)
        try:
            if ("minimum", icao) in ceifa_fresh and ctx is not None:
                _send_ceifa_block(
                    token, chat_id, station, ctx, contratos,
                    extreme="minimum", forecast=forecast,
                    member_values=minimum_members.get(icao, []))
                marca = "bloco"
            else:
                notify.send_message(
                    token, chat_id,
                    _ceifa_repeat_text(
                        station, ctx, contratos, extreme="minimum",
                        forecast=forecast))
                marca = "repetição"
            for key, *_rest in contratos:
                ceifa_last_sent[key] = run_now_utc.isoformat()
            print(f"[ceifa mínima] {icao}: {len(contratos)} contrato(s) "
                  f"({marca}).")
        except Exception as exc:  # noqa: BLE001
            print(f"[ceifa mínima] {icao}: ERRO: {exc}", file=sys.stderr)

    # Captura dos alertas de estratégia desta rodada (Ceifa).
    _cap(lambda: capture.record_alerts(
        dt.datetime.now(dt.timezone.utc),
        (_ceifa_alert_rows(
            ceifa_pending, ceifa_seen, signal_rows, extreme="maximum")
         + _ceifa_alert_rows(
             minimum_pending, ceifa_seen, minimum_signal_rows,
             extreme="minimum"))))

    # 4) Comandos e cliques de botão recebidos desde a última rodada
    # (getUpdates). É aqui que o relatório completo sai, sob demanda —
    # com latência de até uma rodada (~5 min). Nunca derruba o digest.
    tg_offset = state.get("tg_offset")
    try:
        tg_offset = _process_updates(
            token, chat_id, tg_offset, state, stations, contexts,
            positions, errors, yes_prob, position_success_prob)
    except Exception as exc:  # noqa: BLE001
        print(f"[comandos] ERRO ao processar updates: {exc}", file=sys.stderr)

    _save_digest_state({"stations": station_state, "edges": edges_now,
                        "signal_probs": signal_rows,
                        "cond_alerts": cond_state,
                        "ceifa": ceifa_keep,
                        "ceifa_last_sent": ceifa_last_sent,
                        "pnl_sent_at": state.get("pnl_sent_at", 0),
                        "commands_set": state.get("commands_set", False),
                        "tg_offset": tg_offset})

    # Consolida no arquivo definitivo (dados/) os dias de buffer JÁ FECHADOS —
    # na prática, 1x/dia, na 1ª rodada após 00:00 UTC. O workflow commita
    # dados/ se algo mudou.
    changed = _cap(capture.flush)
    if changed:
        print(f"[captura] {len(changed)} arquivo(s) consolidado(s) em dados/.")

    return 1 if len(errors) == len(stations) else 0


def _current_ceifa_stake(wallet: str) -> tuple[float, float]:
    """Saldo livre e sua parcela de 1%, consultados no momento do sinal."""
    free_pusd = polymarket.fetch_pusd_balance(wallet)
    stake = free_pusd * config.CEIFA_STAKE_FRAC
    if stake <= 0:
        raise ValueError("saldo livre da carteira está zerado")
    return free_pusd, stake


def _allocate_relative_stakes(pending: dict, stations, free_pusd: float,
                              stake_frac: float) -> dict:
    """Anexa a stake a cada contrato, abatendo as parcelas anteriores."""
    remaining = free_pusd
    allocated = {}
    for station in stations:
        key = station if isinstance(station, str) else station.icao
        contracts = pending.get(key, [])
        if not contracts:
            continue
        rows = []
        for contract in contracts:
            stake = remaining * stake_frac
            remaining -= stake
            rows.append((*contract, stake))
        allocated[key] = rows
    return allocated


def _collect_signal_rows(stations, contexts, yes_prob) -> dict:
    """Todas as faixas do D0 de cada estação (nada quando a máxima de hoje já
    travou — mercado resolvido), com preço e projeção, indexadas por
    "icao:data:faixa" (a data na chave re-arma os sinais na virada do dia).
    Cada valor: {icao, day_label, label, yes, mp}. Normalizado via JSON para
    comparar com o estado salvo em disco."""
    rows: dict = {}
    for station in stations:
        ctx = contexts.get(station.icao)
        if ctx is None or ctx["tmax_locked"]:
            continue
        day = ctx["d0"]
        slug = polymarket.event_slug(station.icao, day)
        if not slug:
            continue
        try:
            event = polymarket.fetch_event(slug)
        except Exception as exc:  # noqa: BLE001 — sinal é acessório
            print(f"[sinais] ERRO evento {slug}: {exc}", file=sys.stderr)
            continue
        try:
            polymarket.attach_best_asks(event)
        except Exception as exc:  # sem livro, captura indicativo mas não alerta
            print(f"[sinais] ERRO livro {slug}: {exc}", file=sys.stderr)
        odds = polymarket.odds_rows(event, yes_prob)
        # Captura de mercado: reaproveita esta busca (sem fetch extra).
        _cap(capture.record_market, ctx["now"], station.icao, day, odds)
        for r in odds:
            if r["yes"] is None or r["mp"] is None:
                continue
            key = f"{station.icao}:{day.isoformat()}:{r['label']}"
            rows[key] = {"icao": station.icao, "label": r["label"],
                          "day_label": f"hoje {day.strftime('%d/%m')}",
                          "yes": r["yes"], "no": r.get("no"),
                          "book_checked": r.get("book_checked", False),
                          "no_ask": r.get("no_ask"),
                          "no_ask_size": r.get("no_ask_size"),
                          "mp": r["mp"]}
    return json.loads(json.dumps(rows))


def _collect_minimum_signal_rows(stations, contexts) -> tuple[dict, dict, dict]:
    """Mercados de mínima D0 com livro e distribuição usados pelo alerta."""
    rows: dict = {}
    forecasts: dict = {}
    members_by_icao: dict = {}
    captured_at = dt.datetime.now(dt.timezone.utc)
    for station in stations:
        ctx = contexts.get(station.icao)
        if ctx is None:
            continue
        day = ctx["d0"]
        forecast = _cap(
            minimum_capture.forecast_snapshot,
            ctx, station.icao, day, captured_at)
        if not forecast or forecast.get("travada"):
            continue
        slug = minimum_capture.lowest_slug(station.icao, day)
        if not slug:
            continue
        try:
            event = polymarket.fetch_event(slug)
            polymarket.attach_best_asks(event)
        except Exception as exc:  # noqa: BLE001
            print(f"[ceifa mínima] {station.icao}: ERRO livro {slug}: {exc}",
                  file=sys.stderr)
            continue
        # Nas mínimas precisamos preservar também o NÃO entre 99,0¢ e
        # 99,5¢. Sem uma probabilidade do modelo, o corte visual padrão de
        # ``odds_rows`` descartaria justamente essas faixas.
        odds = polymarket.odds_rows(event, include_all=True)
        forecasts[station.icao] = forecast
        members = _cap(minimum_capture.minimum_members, ctx, day) or []
        members_by_icao[station.icao] = [
            float(member["tmin"]) for member in members
            if member.get("tmin") is not None
        ]
        for row in odds:
            if row.get("no") is None:
                continue
            key = f"min:{station.icao}:{day.isoformat()}:{row['label']}"
            rows[key] = {
                "icao": station.icao, "label": row["label"],
                "day_label": f"hoje {day.strftime('%d/%m')}",
                "yes": row.get("yes"), "no": row.get("no"),
                "book_checked": row.get("book_checked", False),
                "no_ask": row.get("no_ask"),
                "no_ask_size": row.get("no_ask_size"),
                "extreme": "minimum",
            }
    return json.loads(json.dumps(rows)), forecasts, members_by_icao


def _stop_alerts(positions: list) -> str | None:
    """Mensagem de stop loss (ou None): posições abertas cujo preço atual está
    STOP_ALERT_FRAC (ou mais) abaixo do preço médio de entrada."""
    linhas = []
    for p in positions:
        if p.get("redeemable"):
            continue
        try:
            avg = float(p.get("avgPrice") or 0)
            cur = float(p.get("curPrice") or 0)
            val = float(p.get("currentValue") or 0)
        except (TypeError, ValueError):
            continue
        if avg <= 0 or val < polymarket.DUST_USD:
            continue
        dd = 1.0 - cur / avg
        if dd < config.STOP_ALERT_FRAC:
            continue
        linhas.append(
            f"• {html.escape(str(p.get('title', '?'))[:80])} — "
            f"<b>{html.escape(str(p.get('outcome', '?')))}</b>: entrada "
            f"${avg:.3f} → agora ${cur:.3f} (<b>−{dd * 100:.0f}%</b>)")
    if not linhas:
        return None
    return ("🛑🛑🛑 <b>STOP LOSS — AÇÃO NECESSÁRIA</b> 🛑🛑🛑\n"
            + "\n".join(linhas)
            + f"\n<b>Saída de referência: −{config.STOP_EXIT_FRAC:.0%} da "
            "entrada.</b> Este alerta repete a cada rodada até você agir.")


def _flat_alert(ctx) -> tuple[str, str] | None:
    """(chave, texto) se o observado está de lado — mesma temperatura — há
    pelo menos 2 horas. A chave (dia+temp+início) evita repetir o aviso do
    mesmo platô; um platô novo re-arma."""
    obs = ctx["obs_today"]
    if len(obs) < 3:
        return None
    last = obs[-1]
    start = last["time"]
    for o in reversed(obs):
        if o["temp"] != last["temp"]:
            break
        start = o["time"]
    horas = (last["time"] - start).total_seconds() / 3600.0
    if horas < 2.0:
        return None
    s = ctx["station"]
    key = f"{ctx['d0'].isoformat()}:{last['temp']:g}:{start:%H%M}"
    texto = (f"⏸️ <b>{html.escape(s.city)} ({s.icao})</b>: observado de lado "
             f"em <b>{last['temp']:.0f} °C</b> há {horas:.1f}h "
             f"(desde {start:%H:%M}) · máx. do dia "
             f"{ctx['obs_max_today']:.0f} °C.")
    return key, texto


def _ens_escape_alert(ctx) -> tuple[str, str] | None:
    """(chave, texto) se a última observação saiu do envelope do ensemble
    corrigido (média ± 1 desvio entre os membros na hora correspondente).
    Avisa na entrada (ou troca de lado) e re-arma quando volta para dentro."""
    lm = ctx["latest_metar"]
    if not lm or lm["time"].date() != ctx["d0"]:
        return None
    hour = lm["time"].replace(minute=0, second=0, microsecond=0)
    try:
        i = ctx["ens"]["time"].index(hour)
    except ValueError:
        return None
    vals = []
    for (model, _mid), series in ctx["ens"]["members"].items():
        v = series[i]
        if v is None:
            continue
        fam = config.ENS_MODELS.get(model)
        b = ctx["bias"].get(fam, {}).get("bias", 0.0) if fam else 0.0
        vals.append(v - b)
    if len(vals) < 10:
        return None
    piso, teto = min(vals), max(vals)
    if piso <= lm["temp"] <= teto:
        return None
    acima = lm["temp"] > teto
    lado = "acima do TETO" if acima else "abaixo do PISO"
    s = ctx["station"]
    key = f"{ctx['d0'].isoformat()}:{'acima' if acima else 'abaixo'}"
    texto = (f"{'📈' if acima else '📉'} <b>{html.escape(s.city)} "
             f"({s.icao})</b>: observado <b>{lado} do ensemble</b> — "
             f"{lm['temp']:.0f} °C às {lm['time']:%H:%M} vs envelope "
             f"[{piso:.1f}, {teto:.1f}] °C dos {len(vals)} membros. "
             "Nenhum membro previu isso: a projeção tende a se mover.")
    return key, texto


def _in_signal_window(icao: str) -> bool:
    """Hora local da estação dentro da janela de envio de sinais."""
    h = dt.datetime.now(config.STATIONS[icao].tz).hour
    return config.SIGNAL_HOURS[0] <= h <= config.SIGNAL_HOURS[1]


def _is_edge(row: dict) -> bool:
    """Sinal acionável: divergência mínima E o lado indicado (comprar Yes se
    está barato, No se está caro) com mais de EDGE_MIN_CONFIDENCE de chance
    segundo a Probabilidade Real (modelo calibrado) — o MESMO número exibido
    na tabela de odds, para o alerta e a tabela nunca discordarem."""
    diff = row["mp"] - row["yes"]
    if abs(diff) < config.EDGE_ALERT_MIN:
        return False
    side = "SIM" if diff > 0 else "NAO"
    if side not in config.SIGNAL_SIDES:
        return False
    if side == "NAO" and (1.0 - row["yes"]) < config.NAO_MIN_PRICE:
        return False  # não brigar com mercado quase-certo do Yes
    side_prob = row["mp"] if diff > 0 else 1.0 - row["mp"]
    return side_prob > config.EDGE_MIN_CONFIDENCE


def _edges_messages(edges: dict, prev_probs: dict) -> list[tuple[str, str]]:
    """Mensagens de sinais, uma POR CIDADE: [(icao, texto HTML)]. Cada linha é
    uma ação (Comprar SIM ou Comprar NÃO) com as probabilidades DO LADO
    COMPRADO — mercado × modelo — e o modelo da rodada anterior."""
    by_icao: dict[str, list[str]] = {}
    for key, e in edges.items():
        prev_mp = prev_probs.get(key, {}).get("mp")
        if e["mp"] > e["yes"]:           # Yes subvalorizado pelo mercado
            dot, acao = "🟢", "Comprar SIM"
            mkt, mdl = e["yes"], e["mp"]
            prev_side = prev_mp
        else:                            # Yes sobrevalorizado → comprar o Não
            dot, acao = "🔴", "Comprar NÃO"
            mkt, mdl = 1.0 - e["yes"], 1.0 - e["mp"]
            prev_side = None if prev_mp is None else 1.0 - prev_mp
        antes = ("antes —" if prev_side is None
                 else f"antes {prev_side * 100:.0f}%")
        by_icao.setdefault(e["icao"], []).append(
            f"{dot} <b>{acao}</b> — <b>{html.escape(e['label'])}</b> "
            f"({e['day_label']}): mercado {mkt * 100:.0f}% × "
            f"modelo {mdl * 100:.0f}% ({antes})")
    out = []
    for icao, lines in by_icao.items():
        st = config.STATIONS[icao]
        head = (f"🚨 <b>Sinais — {html.escape(st.city)}</b> {st.flag} · "
                f"mercado × modelo no lado comprado · edge ≥ "
                f"{config.EDGE_ALERT_MIN * 100:.0f} p.p., modelo &gt; "
                f"{config.EDGE_MIN_CONFIDENCE * 100:.0f}%")
        out.append((icao, "\n".join([head, *lines])))
    return out


def _station_fingerprint(ctx: dict) -> dict:
    """Assinatura do que o digest comunica de uma estação: muda quando o
    observado muda (nova temperatura ou nova máxima) ou quando a projeção muda
    (quantis de hoje/amanhã). Normalizada via JSON para comparar com o estado
    salvo em disco (chaves viram string)."""
    lm = ctx["latest_metar"]
    fp = {
        "obs_max": ctx["obs_max_today"],
        "latest_temp": lm["temp"] if lm else None,
        "q_d0": ctx["dist_d0"]["quantiles"],
    }
    return json.loads(json.dumps(fp))


def _load_digest_state() -> dict:
    try:
        return json.loads(config.DIGEST_STATE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_digest_state(state: dict) -> None:
    try:
        config.DIGEST_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.DIGEST_STATE_FILE.write_text(json.dumps(state), "utf-8")
    except OSError as exc:
        print(f"AVISO: não salvou o estado do digest ({exc})", file=sys.stderr)


def _ceifa_send_due(last_sent: str | None, now_utc: dt.datetime,
                    interval_minutes: int) -> bool:
    """Impede parcelas duplicadas quando cron externo e fallback coincidem."""
    if not last_sent:
        return True
    try:
        previous = dt.datetime.fromisoformat(last_sent)
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return True
    return now_utc - previous >= dt.timedelta(minutes=interval_minutes)


# ------------------------------------------------------------- captura de dados

def _cap(fn, *args, **kwargs):
    """Executa uma gravação de captura sem NUNCA derrubar o digest."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — captura é 100% acessória
        print(f"[captura] ERRO em {getattr(fn, '__name__', fn)}: {exc}",
              file=sys.stderr)
        return None


def _capture_context(station, ctx) -> None:
    """Grava a previsão derivada (a cada rodada) e o ensemble bruto (dedup por
    ciclo) de uma estação, tudo a partir do contexto já calculado (sem fetch)."""
    now, d0 = ctx["now"], ctx["d0"]
    dist = ctx["dist_d0"]
    q = dist["quantiles"]
    media = sum(b["prob"] * (b["low"] + b["high"]) / 2 for b in dist["buckets"])
    mm = distribution.member_maxima_for_day(
        d0, ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        now=now, shift=ctx["shift"], obs_max=ctx["obs_max_today"])
    mm_vals = [m["tmax"] for m in mm]   # máxima diária corrigida por membro
    times, _p10, p50, _p90, _raw = pipeline.hourly_percentiles(
        ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        ctx["shift"], now, days={d0})
    valid = [(t, v) for t, v in zip(times, p50) if v is not None]
    pico_hora = max(valid, key=lambda tv: tv[1])[0].hour if valid else None
    _cap(capture.record_forecast, now, station.icao, d0,
         media=round(media, 2), mediana=q.get(50),
         piso_ens=(round(min(mm_vals), 2) if mm_vals else None),
         teto_ens=(round(max(mm_vals), 2) if mm_vals else None),
         p10=q.get(10), p90=q.get(90), pico_hora=pico_hora,
         obs_max=ctx["obs_max_today"], nowcast_shift=ctx["shift"],
         nowcast_offset=(ctx.get("nowcast") or {}).get("offset"),
         nowcast_n_hours=(ctx.get("nowcast") or {}).get("n_hours"),
         nowcast_hour_weight=(ctx.get("nowcast") or {}).get("hour_weight"),
         plateau_temp=ceifa.plateau_temperature(ctx["obs_today"]),
         travada=ctx["tmax_locked"])
    _cap(capture.record_nowcast, now, station.icao, d0, ctx.get("nowcast"))
    members = {f"{m}:{mid}": s for (m, mid), s in ctx["ens"]["members"].items()}
    _cap(capture.record_ensemble, now, station.icao, d0,
         ctx["ens"]["time"], members, ctx["bias"])


def _ens_spread(ctx) -> float | None:
    """Incerteza do ensemble AGORA = teto_ens − mediana, exatamente como é
    gravado na base previsao (max da máxima por membro − mediana da
    distribuição). É o valor que o filtro de incerteza lê na H-1."""
    dist = ctx.get("dist_d0") if ctx else None
    if not dist:
        return None
    mediana = dist["quantiles"].get(50)
    if mediana is None:
        return None
    mm = distribution.member_maxima_for_day(
        ctx["d0"], ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        now=ctx["now"], shift=ctx["shift"], obs_max=ctx["obs_max_today"])
    mm_vals = [m["tmax"] for m in mm]
    if not mm_vals:
        return None
    return max(mm_vals) - mediana


def _report_snapshot(station, ctx) -> dict:
    """Snapshot serializável do contexto no momento de um alerta (caixa-preta)."""
    dist = ctx["dist_d0"]
    lm = ctx["latest_metar"]
    times, p10, p50, p90, _raw = pipeline.hourly_percentiles(
        ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        ctx["shift"], ctx["now"], days={ctx["d0"]})
    hourly = [{"hora": t.strftime("%Y-%m-%dT%H:%M"), "p10": a, "p50": b,
               "p90": c} for t, a, b, c in zip(times, p10, p50, p90)
              if b is not None]
    return {
        "icao": station.icao, "dia": ctx["d0"].isoformat(),
        "now": ctx["now"].strftime("%Y-%m-%dT%H:%M%z"),
        "obs_max": ctx["obs_max_today"], "tmax_locked": ctx["tmax_locked"],
        "latest_metar": (None if not lm else {
            "temp": lm["temp"],
            "time": lm["time"].strftime("%Y-%m-%dT%H:%M")}),
        "nowcast_shift": ctx["shift"],
        "nowcast_offset": (ctx.get("nowcast") or {}).get("offset"),
        "nowcast_n_hours": (ctx.get("nowcast") or {}).get("n_hours"),
        "nowcast_hour_weight": (ctx.get("nowcast") or {}).get("hour_weight"),
        "nowcast_components": (ctx.get("nowcast") or {}).get("components", []),
        "plateau_temp": ceifa.plateau_temperature(ctx["obs_today"]),
        "quantiles": {str(k): v for k, v in dist["quantiles"].items()},
        "buckets": dist["buckets"], "exceed": dist.get("exceed"),
        "taf_tx_d0": ctx["taf_tx_d0"], "hourly": hourly,
    }


def _stop_rows(positions: list) -> list[dict]:
    """Linhas estruturadas das posições >STOP_ALERT_FRAC abaixo da entrada."""
    out = []
    for p in positions:
        if p.get("redeemable"):
            continue
        try:
            avg = float(p.get("avgPrice") or 0)
            cur = float(p.get("curPrice") or 0)
            val = float(p.get("currentValue") or 0)
        except (TypeError, ValueError):
            continue
        if avg <= 0 or val < polymarket.DUST_USD:
            continue
        dd = 1.0 - cur / avg
        if dd < config.STOP_ALERT_FRAC:
            continue
        pm = polymarket.parse_temp_market(p.get("title"))
        out.append({
            "icao": pm["icao"] if pm else None,
            "dia": str(p.get("endDate") or "")[:10] or None,
            "faixa": str(p.get("title") or "")[:80],
            "lado": p.get("outcome"),
            "entrada": round(avg, 4), "atual": round(cur, 4),
            "queda_pct": round(dd * 100, 1)})
    return out


def _alert_rows(new_edges, prev_edges, harvest_pending, harvest_seen,
                signal_rows) -> list[dict]:
    """Linhas estruturadas de todo alerta de estratégia desta rodada, com a
    flag `repeticao` separando a entrada (1ª vez) das repetições."""
    rows = []
    for k, v in new_edges.items():
        st = config.STATIONS[v["icao"]]
        rows.append({
            "icao": v["icao"], "dia": k.split(":")[1], "estrategia": "edge",
            "faixa": v["label"], "lado": "NAO",
            "preco": round(1.0 - v["yes"], 4), "modelo": round(1.0 - v["mp"], 4),
            "edge_pp": round(abs(v["mp"] - v["yes"]) * 100, 1),
            "hora_local": dt.datetime.now(st.tz).hour,
            "repeticao": k in prev_edges})
    for icao, lst in harvest_pending.items():
        st = config.STATIONS[icao]
        for k, _linha in lst:
            v = signal_rows.get(k)
            if not v:
                continue
            rows.append({
                "icao": icao, "dia": k.split(":")[1], "estrategia": "colheita",
                "faixa": v["label"], "lado": "NAO",
                "preco": round(1.0 - v["yes"], 4),
                "modelo": round(1.0 - v["mp"], 4), "edge_pp": None,
                "hora_local": dt.datetime.now(st.tz).hour,
                "repeticao": k in harvest_seen})
    return rows


# --------------------------------------------------------------- Ceifa (ativa)

def _peak_hour(ctx):
    """Hora local do pico previsto (máximo da mediana horária corrigida)."""
    times, _p10, p50, _p90, _raw = pipeline.hourly_percentiles(
        ctx["ens"]["time"], ctx["ens"]["members"], ctx["bias"],
        ctx["shift"], ctx["now"], days={ctx["d0"]})
    valid = [(t, v) for t, v in zip(times, p50) if v is not None]
    return max(valid, key=lambda tv: tv[1])[0].hour if valid else None


def _ceifa_distribution_line(station, ctx, extreme="maximum",
                             forecast=None) -> str:
    """Resumo completo da distribuição corrigida do extremo anunciado."""
    unit = station.unit
    if extreme == "minimum":
        values = forecast or {}
        q10 = notify.temperature_for_unit(values.get("p10"), unit)
        q50 = notify.temperature_for_unit(values.get("mediana"), unit)
        q90 = notify.temperature_for_unit(values.get("p90"), unit)
        floor = notify.temperature_for_unit(values.get("piso_ens"), unit)
        ceiling = notify.temperature_for_unit(values.get("teto_ens"), unit)
        label = "mínima"
    else:
        q = ctx["dist_d0"]["quantiles"]
        q10 = notify.temperature_for_unit(q.get(10), unit)
        q50 = notify.temperature_for_unit(q.get(50), unit)
        q90 = notify.temperature_for_unit(q.get(90), unit)
        member_values = [
            component[0] for component in ctx["dist_d0"].get("comps", [])
            if component and component[0] is not None
        ]
        floor = (notify.temperature_for_unit(min(member_values), unit)
                 if member_values else None)
        ceiling = (notify.temperature_for_unit(max(member_values), unit)
                   if member_values else None)
        label = "máxima"
    details = f"P10 {q10:.1f} · P90 {q90:.1f}"
    if floor is not None:
        details += f" · Piso ens. {floor:.1f} °{unit}"
    if ceiling is not None:
        details += f" · Teto ens. {ceiling:.1f} °{unit}"
    return (f"📊 Mediana da {label}: <b>{q50:.1f} °{unit}</b> "
            f"({details})")


def _ceifa_timing_line(ctx, extreme="maximum", forecast=None) -> str:
    hour = ((forecast or {}).get("pico_hora") if extreme == "minimum"
            else _peak_hour(ctx))
    now = ctx.get("now")
    now_text = now.strftime("%H:%M") if now is not None else "—"
    hour_text = f"{int(hour):02d}h" if hour is not None else "—"
    icon, label = (("📉", "Mínimo previsto") if extreme == "minimum"
                   else ("📈", "Pico previsto"))
    return (f"🕐 Agora: <b>{now_text}</b> (local) · {icon} {label}: "
            f"<b>{hour_text}</b>")


def _ceifa_text(station, ctx, contratos, extreme="maximum", forecast=None) -> str:
    """Alerta de Ceifa com extremo, preço, liquidez e distribuição completa."""
    title = "❄️ MÍNIMA" if extreme == "minimum" else "🌡️ MÁXIMA"
    linhas = [f"🌾 <b>Ceifa — {title} — {station.flag} "
              f"{html.escape(station.city)} ({station.icao})</b>"]
    for _k, faixa, price, size, stake in contratos:
        available = f"{size:g} cota(s)" if size is not None else "—"
        linhas.append(f"• Comprar <b>NÃO {html.escape(str(faixa))}</b> "
                      f"@ ${price:.3f} · stake: <b>${stake:,.2f}</b> "
                      f"· disponível: {available}")
    linhas.append(_ceifa_timing_line(ctx, extreme, forecast))
    linhas.append(_ceifa_distribution_line(
        station, ctx, extreme, forecast))
    return "\n".join(linhas)


def _ceifa_repeat_text(station, ctx, contratos, extreme="maximum",
                       forecast=None) -> str:
    """Nova parcela com preço, stake e resumo atual do ensemble."""
    title = "❄️ MÍNIMA" if extreme == "minimum" else "🌡️ MÁXIMA"
    linhas = [f"🌾 <b>Ceifa — {title} — {station.flag} "
              f"{html.escape(station.city)} ({station.icao})</b> "
              f"<i>(nova parcela)</i>"]
    for _k, faixa, price, size, stake in contratos:
        available = f"{size:g} cota(s)" if size is not None else "—"
        linhas.append(f"• Comprar <b>NÃO {html.escape(str(faixa))}</b> "
                      f"@ ${price:.3f} · stake: <b>${stake:,.2f}</b> "
                      f"· disponível: {available}")
    try:
        linhas.append(_ceifa_timing_line(ctx, extreme, forecast))
    except (KeyError, TypeError, ValueError):
        pass
    linhas.append(_ceifa_distribution_line(
        station, ctx, extreme, forecast))
    return "\n".join(linhas)


def _send_ceifa_block(token, chat_id, station, ctx, contratos,
                      extreme="maximum", forecast=None,
                      member_values=None) -> None:
    """Bloco enxuto da Ceifa: divisor, texto (compra + pico + mediana) e o
    gráfico da distribuição (ensemble + TAF + mediana). Sem tabela de
    probabilidades e sem hora a hora."""
    notify.send_message(token, chat_id, notify.station_divider(station))
    notify.send_message(
        token, chat_id,
        _ceifa_text(station, ctx, contratos, extreme, forecast))
    if extreme == "minimum":
        chart = notify.ceifa_minimum_chart_png(ctx, member_values or [])
        caption = (f"📉 <b>{html.escape(station.city)}</b> — trajetória hora "
                   "a hora + distribuição da MÍNIMA de hoje (ensemble · "
                   "mediana)")
    else:
        chart = notify.ceifa_chart_png(ctx)
        caption = (f"📈 <b>{html.escape(station.city)}</b> — trajetória hora "
                   "a hora + distribuição da MÁXIMA de hoje (ensemble · TAF "
                   "· mediana)")
    notify.send_photo(
        token, chat_id, chart, caption)


def _ceifa_alert_rows(ceifa_pending, ceifa_seen, signal_rows,
                      extreme="maximum") -> list:
    """Linhas estruturadas dos alertas de Ceifa desta rodada (para a captura),
    com a flag repeticao separando a entrada das repetições."""
    rows = []
    for icao, contratos in ceifa_pending.items():
        hloc = dt.datetime.now(config.STATIONS[icao].tz).hour
        for k, faixa, price, size, stake_usd in contratos:
            v = signal_rows.get(k, {})
            mp = v.get("mp")
            parts = k.split(":")
            day = parts[2] if k.startswith("min:") else parts[1]
            rows.append({
                "icao": icao, "dia": day,
                "estrategia": f"ceifa_{'minima' if extreme == 'minimum' else 'maxima'}",
                "extremo": extreme,
                "faixa": faixa, "lado": "NAO", "preco": round(price, 4),
                "volume_disponivel": (round(size, 4)
                                       if size is not None else None),
                "stake_usd": round(stake_usd, 4),
                "modelo": (round(1.0 - mp, 4) if mp is not None else None),
                "edge_pp": None, "hora_local": hloc,
                "repeticao": k in ceifa_seen})
    return rows


# --------------------------------------------------- comandos (recebimento)

_BOT_COMMANDS = [
    {"command": "relatorio", "description": "Relatório completo de uma cidade"},
    {"command": "cidades", "description": "Lista as cidades monitoradas"},
    {"command": "ajuda", "description": "Como usar o bot"},
]


def _norm(s: str) -> str:
    """Minúsculas sem acento, para casar nomes de cidade digitados livremente."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()


def _resolve_station(arg: str) -> str | None:
    """ICAO exato (SBGR) ou nome de cidade (Guarulhos), sem depender de acento
    ou caixa. None se nada casar de forma inequívoca."""
    up = arg.strip().upper()
    if up in config.STATIONS:
        return up
    n = _norm(arg)
    if not n:
        return None
    exato = [i for i, s in config.STATIONS.items() if _norm(s.city) == n]
    if exato:
        return exato[0]
    parc = [i for i, s in config.STATIONS.items()
            if _norm(s.city).startswith(n) or n in _norm(s.city)]
    return parc[0] if len(parc) == 1 else None


def _help_text() -> str:
    return (
        "🤖 <b>Bot de Tmax</b>\n\n"
        "Você recebe só os <b>alertas</b> (sinais de compra, colheita, stop e "
        "avisos de condição). Cada alerta traz o botão "
        "<b>“📄 Ver relatório completo”</b> — toque nele quando quiser o "
        "detalhe da cidade (tabela mercado × modelo, gráfico e hora a hora).\n\n"
        "<b>Comandos</b>\n"
        "• <code>/relatorio &lt;cidade&gt;</code> — relatório completo de "
        "qualquer cidade. Ex.: <code>/relatorio Guarulhos</code> ou "
        "<code>/relatorio SBGR</code>\n"
        "• <code>/cidades</code> — lista as cidades monitoradas\n"
        "• <code>/ajuda</code> — esta mensagem\n\n"
        "<i>As respostas podem levar até uma rodada (~5 min): o bot processa "
        "os comandos junto do envio agendado.</i>")


def _cities_text() -> str:
    linhas = [f"{s.flag} {html.escape(s.city)} — <code>{i}</code>"
              for i, s in sorted(config.STATIONS.items(),
                                 key=lambda kv: kv[1].city)]
    return "🌎 <b>Cidades monitoradas</b>\n" + "\n".join(linhas)


def _process_updates(token, chat_id, offset, state, stations, contexts,
                     positions, errors, yes_prob, position_success_prob):
    """Lê os updates pendentes do Telegram (comandos e cliques de botão),
    responde e devolve o novo offset. Só atende o chat configurado."""
    if not state.get("commands_set"):
        try:
            notify.set_my_commands(token, _BOT_COMMANDS)
            state["commands_set"] = True
        except Exception as exc:  # noqa: BLE001 — só afeta o menu "/"
            print(f"[comandos] ERRO ao registrar comandos: {exc}",
                  file=sys.stderr)

    for u in notify.get_updates(token, offset):
        offset = u["update_id"] + 1
        try:
            if "callback_query" in u:
                _handle_callback(token, chat_id, u["callback_query"], stations,
                                 contexts, positions, errors, yes_prob,
                                 position_success_prob)
            elif "message" in u:
                _handle_message(token, chat_id, u["message"], stations,
                                contexts, positions, errors, yes_prob,
                                position_success_prob)
        except Exception as exc:  # noqa: BLE001 — um update ruim não trava o resto
            print(f"[comandos] ERRO no update {u.get('update_id')}: {exc}",
                  file=sys.stderr)
    return offset


def _handle_message(token, chat_id, msg, stations, contexts, positions,
                    errors, yes_prob, position_success_prob) -> None:
    if str(msg.get("chat", {}).get("id")) != str(chat_id):
        return  # ignora quem não é o dono do chat
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return
    parts = text.split()
    cmd = parts[0][1:].split("@")[0].lower()   # tira "/" e um eventual @bot
    arg = " ".join(parts[1:]).strip()

    if cmd in ("start", "help", "ajuda"):
        notify.send_message(token, chat_id, _help_text())
    elif cmd in ("cidades", "estacoes", "cities"):
        notify.send_message(token, chat_id, _cities_text())
    elif cmd in ("relatorio", "rel", "report", "cidade"):
        if not arg:
            notify.send_message(
                token, chat_id,
                "Uso: <code>/relatorio &lt;cidade&gt;</code>\n"
                "Ex.: <code>/relatorio Guarulhos</code> ou "
                "<code>/relatorio SBGR</code>\n\n" + _cities_text())
            return
        icao = _resolve_station(arg)
        if not icao:
            notify.send_message(
                token, chat_id,
                f"❓ Não encontrei “{html.escape(arg)}”.\n\n" + _cities_text())
            return
        _send_report_ondemand(token, chat_id, icao, contexts, positions,
                              errors, yes_prob, position_success_prob)
    else:
        notify.send_message(token, chat_id,
                            "Comando não reconhecido.\n\n" + _help_text())


def _handle_callback(token, chat_id, cq, stations, contexts, positions,
                     errors, yes_prob, position_success_prob) -> None:
    data = cq.get("data") or ""
    cq_chat = str(((cq.get("message") or {}).get("chat") or {}).get("id"))
    if cq_chat != str(chat_id):
        notify.answer_callback_query(token, cq["id"])
        return
    if data.startswith("rel:"):
        icao = data[4:].strip().upper()
        st = config.STATIONS.get(icao)
        notify.answer_callback_query(
            token, cq["id"],
            f"Gerando relatório de {st.city if st else icao}…")
        _send_report_ondemand(token, chat_id, icao, contexts, positions,
                              errors, yes_prob, position_success_prob)
    else:
        notify.answer_callback_query(token, cq["id"])


def _send_report_ondemand(token, chat_id, icao, contexts, positions, errors,
                          yes_prob, position_success_prob) -> None:
    """Envia o bloco completo de UMA cidade sob demanda (botão/comando)."""
    station = config.STATIONS.get(icao)
    if station is None:
        notify.send_message(token, chat_id,
                            f"❓ Estação desconhecida: {html.escape(icao)}.")
        return
    try:
        _send_station_block(token, chat_id, station, contexts.get(icao),
                            positions, errors, yes_prob, position_success_prob)
        print(f"[comandos] relatório de {icao} enviado sob demanda.")
    except Exception as exc:  # noqa: BLE001
        print(f"[comandos] ERRO ao enviar relatório de {icao}: {exc}",
              file=sys.stderr)
        notify.send_message(
            token, chat_id,
            f"⚠️ Falha ao gerar o relatório de {html.escape(station.city)}.")


def _send_station_block(token, chat_id, station, ctx, positions,
                        errors, yes_prob, position_success_prob,
                        pre_msgs=None) -> None:
    """Envia o bloco completo de UMA estação (divisor, sinais/alertas da
    rodada, posições, tabela de odds, gráfico e hora a hora). Levanta na
    primeira falha de envio."""
    notify.send_message(token, chat_id, notify.station_divider(station))
    for m in pre_msgs or []:
        notify.send_message(token, chat_id, m)

    # 4a) posições abertas desta cidade, com a chance de dar certo
    if positions:
        msg = polymarket.station_positions_message(
            station, positions, position_success_prob)
        if msg:
            notify.send_message(token, chat_id, msg)

    if ctx is None:
        notify.send_message(
            token, chat_id,
            f"⚠️ Sem dados suficientes agora "
            f"({errors.get(station.icao, '')}).")
        return

    # 4b) tabela: probabilidade real vs. preço do mercado — só o D0; com a
    # máxima travada o mercado de hoje está resolvido e não há o que comparar.
    day_tables: list[tuple] = []
    days = () if ctx["tmax_locked"] else (("Hoje", ctx["d0"]),)
    for day_label, date in days:
        slug = polymarket.event_slug(station.icao, date)
        if not slug:
            continue
        try:
            event = polymarket.fetch_event(slug)
        except Exception as exc:  # noqa: BLE001 — tabela é acessória
            print(f"[polymarket] ERRO evento {slug}: {exc}", file=sys.stderr)
            continue
        rows = polymarket.odds_rows(event, yes_prob)
        if rows:
            day_tables.append((day_label, date, rows))
    if day_tables:
        try:
            notify.send_photo(token, chat_id,
                              notify.odds_table_png(station, day_tables),
                              notify.odds_caption(station))
            print(f"[{station.icao}] tabela de odds enviada "
                  f"({len(day_tables)} dia(s)).")
        except Exception as exc:  # noqa: BLE001 — tabela é acessória
            print(f"[{station.icao}] ERRO ao enviar tabela: {exc}",
                  file=sys.stderr)
    else:
        print(f"[{station.icao}] sem mercado de odds relevante.")

    # 4c) gráfico com nowcast + distribuições e o hora a hora
    notify.send_photo(token, chat_id, notify.station_chart_png(ctx),
                      notify.station_lines(ctx))
    notify.send_message(token, chat_id, notify.station_hourly_lines(ctx))
    print(f"[{station.icao}] enviado.")


if __name__ == "__main__":
    sys.exit(main())
