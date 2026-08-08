"""Estudo observacional de mercados binários diários da Polymarket.

Cada mercado (SPY Up or Down, Bitcoin Above, ...) tem o mesmo formato: dois
lados cujos preços somam ~1, com o dia no slug. A estratégia aloca no lado que
estiver na faixa de compra, 1% do caixa livre a cada 10 min, em janelas
relativas ao fechamento. Só observa — sem alerta e sem ordem.

O pacote se chama ``spy`` por herança (foi o primeiro mercado), mas hoje é
genérico: novos mercados entram só no registro ``MERCADOS`` abaixo.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mercado:
    key: str            # identificador curto → pastas dados_{key}/ e data_{key}/
    nome: str           # rótulo no painel
    slug_prefix: str    # ex.: "spy-up-or-down-on", "bitcoin-above-on"
    close_hour: int = 16              # fechamento local (16:00 ET, como o SPY)
    tz: str = "America/New_York"


MERCADOS: dict[str, Mercado] = {
    "spy": Mercado("spy", "SPY Daily Up or Down", "spy-up-or-down-on"),
    "bitcoin": Mercado("bitcoin", "Bitcoin Above", "bitcoin-above-on"),
}

# Faixa de compra (>95¢ e <99,8¢) e cadência — iguais para todos os mercados.
BAND = (0.95, 0.998)
INTERVAL_MINUTES = 10
