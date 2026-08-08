"""Estudo observacional de mercados binários diários da Polymarket.

Cada mercado (SPY Up or Down, Bitcoin Above, ...) tem o mesmo formato: dois
lados cujos preços somam ~1, com o dia no slug. A estratégia aloca no lado que
estiver na faixa de compra, 1% do caixa livre a cada 10 min, em janelas
relativas ao fechamento. Só observa — sem alerta e sem ordem.

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
    # rolling=False: o dia do slug é a data do calendário em ``tz`` (SPY: pregão
    #   dos EUA, vira à meia-noite ET; resolve às 16:00 ET).
    # rolling=True: o dia do slug vira na própria hora do fechamento (Bitcoin:
    #   o "dia" começa e resolve às 16:00 UTC — a janela do dia N é
    #   [N-1 16:00, N 16:00] e o slug é a data da resolução).
    rolling: bool = False


MERCADOS: dict[str, Mercado] = {
    "spy": Mercado("spy", "SPY Daily Up or Down", "spy-up-or-down-on"),
    "bitcoin": Mercado("bitcoin", "Bitcoin Above", "bitcoin-above-on",
                       close_hour=16, tz="UTC", rolling=True),
}

# Faixa de compra (>95¢ e <99,8¢) e cadência — iguais para todos os mercados.
BAND = (0.95, 0.998)
INTERVAL_MINUTES = 10


def next_close(mercado: Mercado, now_utc: dt.datetime) -> dt.datetime:
    """Próximo fechamento (resolução) do mercado após ``now`` (tz-aware)."""
    local = now_utc.astimezone(ZoneInfo(mercado.tz))
    close = local.replace(hour=mercado.close_hour, minute=0, second=0,
                          microsecond=0)
    if local >= close:
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
