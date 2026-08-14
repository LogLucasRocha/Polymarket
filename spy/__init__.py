"""Estudo observacional de mercados binários diários da Polymarket.

Cada mercado (SPY Up or Down, Bitcoin Above, ...) tem o mesmo formato: dois
lados cujos preços somam ~1, com o dia no slug. A estratégia aloca no lado que
estiver na faixa de compra, 1% do caixa livre a cada 5 min, em janelas
relativas ao fechamento, até 3% do patrimônio por posição. O SPY Up/Down usa
H-1 em produção com alerta no
Telegram; suas outras janelas e todos os demais mercados seguem em observação.

O pacote se chama ``spy`` por herança (foi o primeiro mercado), mas hoje é
genérico: novos mercados entram só no registro ``MERCADOS`` abaixo.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Mercado:
    key: str            # identificador curto → pastas dados_{key}/ e data_{key}/
    nome: str           # rótulo no painel
    slug_prefix: str    # ex.: "spy-up-or-down-on", "bitcoin-above-on"
    close_hour: int = 16              # hora do fechamento/resolução (em ``tz``)
    tz: str = "America/New_York"      # fuso do fechamento
    # rolling=False: o dia do slug é a data do calendário em ``tz``.
    # rolling=True: o dia do slug vira na própria hora do fechamento (Bitcoin:
    #   o "dia" começa e resolve às 16:00 UTC — a janela do dia N é
    #   [N-1 16:00, N 16:00] e o slug é a data da resolução).
    rolling: bool = False
    # Mercados da bolsa americana não abrem sábado/domingo. Feriados são
    # resolvidos pela captura, que procura o próximo slug disponível.
    weekdays_only: bool = False
    # kind="binary": um mercado por dia, 2 desfechos (SPY: Up/Down).
    # kind="strikes": vários strikes por dia, cada um Yes/No (Bitcoin Above:
    #   "acima de 60k?", "acima de 62k?", ...). Cada strike vira um contrato.
    kind: str = "binary"
    # Teto exclusivo da faixa de compra. O piso de 95¢ continua global.
    band_high: float = 0.995


MERCADOS: dict[str, Mercado] = {
    "spy": Mercado("spy", "SPY Daily Up or Down", "spy-up-or-down-on",
                   rolling=True, weekdays_only=True),
    "bitcoin": Mercado("bitcoin", "Bitcoin Above", "bitcoin-above-on",
                       close_hour=16, tz="UTC", rolling=True, kind="strikes"),
    # Binário diário do BTC (mesmo esquema do SPY), mas o dia vira/resolve às
    # 16:00 UTC como o Bitcoin Above — mesma convenção da Polymarket pro BTC.
    "btc_updown": Mercado("btc_updown", "Bitcoin Up or Down",
                          "bitcoin-up-or-down-on", close_hour=16, tz="UTC",
                          rolling=True, kind="binary"),
    # Solana segue a mesma janela diária dos mercados cripto do Bitcoin:
    # o slug do dia N cobre o período até 16:00 UTC do próprio dia N.
    "solana": Mercado("solana", "Solana Above", "solana-above-on",
                       close_hour=16, tz="UTC", rolling=True, kind="strikes"),
    "sol_updown": Mercado("sol_updown", "Solana Up or Down",
                          "solana-up-or-down-on", close_hour=16, tz="UTC",
                          rolling=True, kind="binary"),
    # Ethereum usa a mesma janela diária das outras criptos: resolve às 16:00
    # UTC e, nesse instante, o slug passa para a data do dia seguinte.
    "ethereum": Mercado("ethereum", "Ethereum Above", "ethereum-above-on",
                         close_hour=16, tz="UTC", rolling=True,
                         kind="strikes"),
    "eth_updown": Mercado("eth_updown", "Ethereum Up or Down",
                           "ethereum-up-or-down-on", close_hour=16, tz="UTC",
                           rolling=True, kind="binary"),
    # SPY multi-strike ("fecha acima de X?"), resolve no fechamento do pregão
    # (16:00 ET) como o SPY Up or Down; após o fechamento, passa ao próximo
    # pregão e pula fins de semana.
    "spy_above": Mercado("spy_above", "SPY Closes Above", "spy-closes-above-on",
                         rolling=True, weekdays_only=True, kind="strikes"),
    # WTI usa o fechamento da sessão regular divulgado pela Pyth às 17:00
    # ET. As duas modalidades avançam ao próximo pregão e pulam fins de
    # semana, da mesma forma que os mercados de bolsa do SPY.
    "wti": Mercado("wti", "WTI Closes Above", "wti-closes-above-on",
                   close_hour=17, rolling=True, weekdays_only=True,
                   kind="strikes"),
    "wti_updown": Mercado("wti_updown", "WTI Up or Down",
                          "wti-up-or-down-on", close_hour=17, rolling=True,
                          weekdays_only=True, kind="binary"),
    # Natural Gas encerra a sessão às 17:00 ET, como o WTI.
    "ng_updown": Mercado("ng_updown", "Natural Gas Up or Down",
                         "ng-up-or-down-on", close_hour=17, rolling=True,
                         weekdays_only=True, kind="binary"),
    # Ações americanas usam o fechamento regular das 16:00 ET.
    "aapl_updown": Mercado("aapl_updown", "Apple Up or Down",
                           "aapl-up-or-down-on", rolling=True,
                           weekdays_only=True, kind="binary"),
    "googl_updown": Mercado("googl_updown", "Google Up or Down",
                            "googl-up-or-down-on", rolling=True,
                            weekdays_only=True, kind="binary"),
    "tsla_updown": Mercado("tsla_updown", "Tesla Up or Down",
                           "tsla-up-or-down-on", rolling=True,
                           weekdays_only=True, kind="binary"),
    "msft_updown": Mercado("msft_updown", "Microsoft Up or Down",
                           "msft-up-or-down-on", rolling=True,
                           weekdays_only=True, kind="binary"),
}

# Faixa padrão de todos os mercados em teste: >95¢ e <99,5¢.
BAND = (0.95, 0.995)
# Veto de custo do par: as melhores ofertas dos dois lados precisam somar
# menos de 105¢. Em 105¢ exatos ou acima, o snapshot não gera parcela.
PAIR_ASK_CEILING = 1.05
INTERVAL_MINUTES = 5


def close_utc(mercado: Mercado, d: dt.date) -> dt.datetime:
    """Fechamento (UTC, tz-aware) do mercado do dia ``d``."""
    close = dt.datetime(d.year, d.month, d.day, mercado.close_hour, 0,
                        tzinfo=ZoneInfo(mercado.tz))
    return close.astimezone(dt.timezone.utc)


def next_close(mercado: Mercado, now_utc: dt.datetime) -> dt.datetime:
    """Próximo fechamento (resolução) do mercado após ``now`` (tz-aware)."""
    local = now_utc.astimezone(ZoneInfo(mercado.tz))
    close = local.replace(hour=mercado.close_hour, minute=0, second=0,
                          microsecond=0)
    if local >= close:
        close += dt.timedelta(days=1)
    if mercado.weekdays_only:
        while close.weekday() >= 5:
            close += dt.timedelta(days=1)
    return close


def market_date(mercado: Mercado, now_utc: dt.datetime) -> dt.date:
    """Data do slug do mercado ativo agora.

    rolling → a data da próxima resolução (o dia vira na hora do fechamento).
    caso contrário → a data do calendário no fuso do mercado (pregão dos EUA).
    """
    if mercado.rolling:
        return next_close(mercado, now_utc).date()
    return now_utc.astimezone(ZoneInfo(mercado.tz)).date()
