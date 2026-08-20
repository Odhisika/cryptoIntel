"""
Sector classification (section 21).

CoinGecko's `categories` field on /coins/{id} is noisy and overlapping —
a single asset commonly has 5-15 category tags ("Layer 1 (L1)", "Smart
Contract Platform", "Ethereum Ecosystem", "FTX Holdings", ...), most of
which aren't useful sector buckets for scoring purposes. This module maps
that raw list onto the fixed, controlled taxonomy on Asset.sector, using
a priority-ordered keyword match — first match wins, so ordering here is
a real decision (e.g. an asset tagged both "Meme" and "Layer 1" — some
memecoins launch their own L1 — is classified by whichever keyword
appears first in PRIORITY_RULES below).

An asset whose categories don't match anything in this table stays
sector=None (unclassified) rather than being force-fit into the closest
guess — a wrong sector label actively corrupts sector-aware scoring and
comparables, which is worse than just not having one yet.

This mapping is a judgment call, not a definitive taxonomy — expect to
revise PRIORITY_RULES as real classification results get manually spot-
checked (see the 'Known issues' item about this in the phase notes).
"""

from core.models import Asset

# (Asset.Sector value, list of case-insensitive substrings to match against
# CoinGecko's raw category strings). Order matters — first matching rule
# wins. More specific/definitive categories are placed before more
# generic ones (e.g. "meme" before "layer 1", since a project's meme
# identity is usually the more scoring-relevant classification even if it
# also happens to run its own chain).
PRIORITY_RULES: list[tuple[str, list[str]]] = [
    (Asset.Sector.MEME, ["meme"]),
    (Asset.Sector.STABLECOIN_INFRA, ["stablecoin"]),
    (Asset.Sector.DEX, ["decentralized exchange", "dex"]),
    (Asset.Sector.LENDING, ["lending", "borrow"]),
    (Asset.Sector.DERIVATIVES, ["derivatives", "perpetual", "synthetic"]),
    (Asset.Sector.ORACLE, ["oracle"]),
    (Asset.Sector.INTEROPERABILITY, ["interoperability", "bridge", "cross-chain", "cross chain"]),
    (Asset.Sector.DEPIN, ["depin", "decentralized physical infrastructure"]),
    (Asset.Sector.AI, ["artificial intelligence", "ai agent", "\"ai\""]),
    (Asset.Sector.RWA, ["real world asset", "rwa"]),
    (Asset.Sector.GAMING, ["gaming", "gamefi", "play to earn", "play-to-earn"]),
    (Asset.Sector.SOCIAL, ["social", "socialfi"]),
    (Asset.Sector.PRIVACY, ["privacy"]),
    (Asset.Sector.PAYMENTS, ["payment"]),
    (Asset.Sector.L2, ["layer 2", "layer-2", "l2", "rollup"]),
    (Asset.Sector.L1, ["layer 1", "layer-1", "smart contract platform"]),
    (Asset.Sector.INFRASTRUCTURE, ["infrastructure"]),
    (Asset.Sector.DEFI, ["decentralized finance", "defi", "yield", "liquid staking", "restaking"]),
]


def classify_sector(categories: list[str]) -> str | None:
    """Return the first Asset.Sector value whose keywords match any of
    the given raw category strings, or None if nothing matches."""
    lowered = [c.lower() for c in categories]
    for sector, keywords in PRIORITY_RULES:
        for keyword in keywords:
            if any(keyword in cat for cat in lowered):
                return sector
    return None
