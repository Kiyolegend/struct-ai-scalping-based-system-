from .scalp1 import check as scalp1
from .scalp2 import check as scalp2
from .scalp3 import check as scalp3
from .scalp4 import check as scalp4
from .scalp5 import check as scalp5

# All strategies are evaluated every cycle.
# The engine picks the one with the highest confluence score (>= 70).
# Plug in your scalping logic inside each strategy file.
STRATEGIES = [
    ("MTF Pullback Precision Scalping", scalp1),
    ("Liquidity Sweep Reversal Scalping", scalp2),
    ("ICT OB/FVG Zone Reaction", scalp3),
    ("Volatility Compression Breakout", scalp4),
    ("Session Open Momentum Scalp", scalp5),
]
