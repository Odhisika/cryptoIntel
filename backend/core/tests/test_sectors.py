from core.models import Asset
from core.scoring.sectors import classify_sector


def test_classifies_l1():
    assert classify_sector(["Layer 1 (L1)", "Smart Contract Platform"]) == Asset.Sector.L1


def test_classifies_meme_even_with_l1_categories():
    # Meme takes priority over L1 even when both tags are present.
    assert classify_sector(["Meme", "Layer 1 (L1)"]) == Asset.Sector.MEME


def test_classifies_dex():
    assert classify_sector(["Decentralized Exchange (DEX)"]) == Asset.Sector.DEX


def test_classifies_lending():
    assert classify_sector(["Lending/Borrowing"]) == Asset.Sector.LENDING


def test_classifies_depin():
    assert classify_sector(["DePIN"]) == Asset.Sector.DEPIN


def test_classifies_stablecoin_infra():
    assert classify_sector(["Stablecoin", "Ethereum Ecosystem"]) == Asset.Sector.STABLECOIN_INFRA


def test_unmatched_categories_return_none():
    assert classify_sector(["FTX Holdings", "Binance Launchpool"]) is None


def test_empty_categories_return_none():
    assert classify_sector([]) is None


def test_case_insensitive_matching():
    assert classify_sector(["LAYER 2"]) == Asset.Sector.L2
