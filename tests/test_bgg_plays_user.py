"""Sprawdza, że dziennik z `xmlapi2/plays` wypełnia model `BGGPlay` tak samo,
jak robił to prywatny `geekplay.php`.

BGG jest z tego środowiska nieosiągalne, więc testem jest odpowiedź o kształcie
udokumentowanym przez serwis. To nie zastąpi pierwszego przebiegu na produkcji,
ale wyłapuje to, co można wyłapać bez sieci: literówki w nazwach atrybutów,
zgubione pola i regułę zachowywania kolumn, których XML nie zna.
"""

import xml.etree.ElementTree as ET

import pytest

from app.models.bgg_plays import BGGPlay
from app.scraper.bgg_plays_user import (
    FIELDS_XML_DOES_NOT_KNOW,
    PRESERVE_IF_MISSING,
    apply_to_row,
    _play_to_model_data,
)


SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<plays username="qubus" userid="2382533" total="2" page="1">
  <play id="93847362" date="2025-12-17" quantity="1" length="90"
        incomplete="0" nowinstats="1" location="Dom">
    <item name="Brass: Birmingham" objecttype="thing" objectid="224517">
      <subtypes>
        <subtype value="boardgame"/>
      </subtypes>
    </item>
    <comments>Wygrana o trzy punkty</comments>
    <players>
      <player username="qubus" userid="2382533" name="Pawel" startposition=""
              color="czerwony" score="145" new="0" rating="0" win="1"/>
      <player username="" userid="0" name="Ania" startposition=""
              color="" score="142" new="1" rating="0" win="0"/>
    </players>
  </play>
  <play id="93847363" date="2025-12-18" quantity="2" length="0"
        incomplete="0" nowinstats="1">
    <item name="Azul" objecttype="thing" objectid="230802"/>
  </play>
</plays>
"""


@pytest.fixture
def plays():
    root = ET.fromstring(SAMPLE)
    return [
        _play_to_model_data(el, user_id=2382533, username="qubus")
        for el in root.findall("play")
    ]


def test_wszystkie_pola_z_xml(plays):
    p = plays[0]
    assert p["play_id"] == 93847362
    assert p["object_id"] == 224517
    assert p["object_type"] == "thing"
    assert p["user_id"] == 2382533
    assert p["username"] == "qubus"
    assert p["play_date"] == "2025-12-17"
    assert p["quantity"] == 1
    assert p["length"] == 90
    assert p["location"] == "Dom"
    assert p["num_players"] == 2
    assert p["comments_value"] == "Wygrana o trzy punkty"
    assert p["incomplete"] is False
    assert p["now_in_stats"] is True
    assert p["game_name"] == "Brass: Birmingham"
    assert p["subtypes"] == [{"subtype": "boardgame"}]


def test_kazde_pole_pasuje_do_kolumny_modelu(plays):
    """Nic, czego model nie ma — inaczej `BGGPlay(**data)` wywali się przy zapisie."""
    columns = {c.name for c in BGGPlay.__table__.columns}
    for p in plays:
        assert set(p).issubset(columns), set(p) - columns


def test_gracze_w_ksztalcie_ktory_czyta_aplikacja(plays):
    """`BGGPlaysRemoteData.BGGPlayPlayer` czyta te klucze i to jako łańcuchy."""
    gracze = plays[0]["players"]
    assert len(gracze) == 2

    ja = gracze[0]
    assert ja["name"] == "Pawel"
    assert ja["username"] == "qubus"
    assert ja["score"] == "145"
    assert ja["win"] == "1"
    assert ja["new"] == "0"
    # Identyfikator aplikacja bierze z `uplayerid`; XML ma `userid`.
    assert ja["uplayerid"] == "2382533"

    # Gość bez konta: pusty username na None, a `userid="0"` nie udaje identyfikatora.
    obcy = gracze[1]
    assert obcy["username"] is None
    assert obcy["uplayerid"] is None
    assert obcy["win"] == "0"


def test_win_state_odtworzony_z_graczy(plays):
    assert plays[0]["win_state"] == "1"


def test_rozgrywka_bez_graczy_i_komentarza(plays):
    p = plays[1]
    assert p["play_id"] == 93847363
    assert p["object_id"] == 230802
    assert p["players"] == []
    assert p["comments_value"] is None
    assert p["num_players"] is None
    assert p["win_state"] is None


def test_rozgrywka_bez_gry_odpada():
    el = ET.fromstring('<play id="1" date="2025-01-01"><item name="x"/></play>')
    assert _play_to_model_data(el, user_id=1, username="qubus") is None


class _Wiersz:
    """Atrapa wiersza — wystarczy, żeby sprawdzić regułę scalania."""

    def __init__(self, **kwargs):
        for column in BGGPlay.__table__.columns:
            setattr(self, column.name, None)
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_pola_spoza_xml_nie_sa_kasowane(plays):
    """Przejście na nowe źródło nie może wyczyścić tego, co zebrało stare."""
    row = _Wiersz(
        play_id=93847362,
        tstamp="2025-12-17 20:11:03",
        length_ms=5400000,
        online=False,
        comments_value="stary tekst",
    )
    apply_to_row(row, plays[0])

    for pole in FIELDS_XML_DOES_NOT_KNOW:
        assert getattr(row, pole) is not None, pole
    assert row.tstamp == "2025-12-17 20:11:03"
    # To, co XML zna, ma się zaktualizować.
    assert row.comments_value == "Wygrana o trzy punkty"


def test_pola_pochodne_nie_kasuja_istniejacych(plays):
    """Rozgrywka bez listy graczy nie może wyzerować zapisanej liczby graczy."""
    row = _Wiersz(play_id=93847363, num_players=4, win_state="1")
    apply_to_row(row, plays[1])
    assert row.num_players == 4
    assert row.win_state == "1"
    assert set(PRESERVE_IF_MISSING) == {"num_players", "win_state"}


def test_brak_zmian_to_brak_zapisu(plays):
    row = _Wiersz(**{k: v for k, v in plays[0].items()})
    assert apply_to_row(row, plays[0]) is False


def test_zmiana_komentarza_jest_wykryta(plays):
    row = _Wiersz(**{k: v for k, v in plays[0].items()})
    row.comments_value = "co innego"
    assert apply_to_row(row, plays[0]) is True
    assert row.comments_value == "Wygrana o trzy punkty"
