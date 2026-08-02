"""
sources/ — Coletores de odds (infra própria).

Cada fonte sharp implementa `collect()` devolvendo "fair lines": linhas já
de-vigadas que servem de referência de probabilidade justa. Cada fonte alvo
(casas onde apostamos) devolve "offered lines". O motor cruza as duas.

Fontes sharp:  pinnacle  (+ fanduel/consenso no futuro)
Fontes alvo:   extensão Chrome hoje; scrapers próprios depois.
"""

from .betano import BetanoSource
from .estrelabet import EstrelaBetSource
from .betnacional import BetnacionalSource
from .pinnacle import PinnacleSource
from .polymarket import PolymarketSource
from .thunderpick import ThunderpickSource
