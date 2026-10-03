"""Akcesoria: szczegóły z BGG tylko dla nowych i zmienionych pozycji,
pobierane paczkami po 20 zamiast jednego zapytania na akcesorium."""

import xml.etree.ElementTree as ET

from app.scraper.bgg_accessory import (
    THING_BATCH_SIZE,
    chunks,
    ids_needing_details,
    parse_thing_batch,
)


def test_new_and_changed_need_details_unchanged_do_not():
    basics = {
        "1": {"last_modified": "2026-09-01 10:00:00"},   # bez zmian
        "2": {"last_modified": "2026-09-02 10:00:00"},   # zmienione
        "3": {"last_modified": "2026-09-03 10:00:00"},   # nowe
        "4": {"last_modified": None},                      # brak daty
    }
    known = {
        1: {"last_modified": "2026-09-01 10:00:00"},
        2: {"last_modified": "2026-08-01 10:00:00"},
        4: {"last_modified": "2026-08-01 10:00:00"},
    }
    assert ids_needing_details(basics, known) == ["2", "3", "4"]


def test_chunks_of_twenty():
    ids = [str(i) for i in range(45)]
    batches = chunks(ids)
    assert [len(b) for b in batches] == [THING_BATCH_SIZE, THING_BATCH_SIZE, 5]
    assert sum(batches, []) == ids


def test_parse_thing_batch_reads_every_item():
    root = ET.fromstring(
        """<items>
          <item type="boardgameaccessory" id="11">
            <description>Sleeves for cards</description>
            <link type="boardgamepublisher" id="1" value="Mayday Games"/>
            <link type="boardgamepublisher" id="2" value="Other"/>
          </item>
          <item type="boardgameaccessory" id="12">
            <description>Insert</description>
          </item>
        </items>"""
    )
    parsed = parse_thing_batch(root)
    assert parsed["11"] == {"description": "Sleeves for cards", "publisher": "Mayday Games, Other"}
    assert parsed["12"] == {"description": "Insert", "publisher": ""}
