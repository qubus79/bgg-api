"""Co pobiera sync kolekcji: szczegóły paczkami, dane prywatne tylko dla zmian."""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from app.scraper.bgg_game import chunks, extract_collection_basics, parse_thing_batch, plan_game_fetches

NOW = datetime(2026, 10, 4, 12, 0, 0)


def test_new_game_needs_details_and_private():
    details, private = plan_game_fetches({"1": {"last_modified": "a"}}, {}, NOW)
    assert details == ["1"] and private == ["1"]


def test_unchanged_fresh_game_needs_nothing():
    known = {1: {"last_modified": "a", "details_fetched_at": NOW - timedelta(days=3)}}
    assert plan_game_fetches({"1": {"last_modified": "a"}}, known, NOW) == ([], [])


def test_changed_entry_needs_private_only():
    known = {1: {"last_modified": "a", "details_fetched_at": NOW - timedelta(days=3)}}
    assert plan_game_fetches({"1": {"last_modified": "b"}}, known, NOW) == ([], ["1"])


def test_old_or_missing_details_are_refreshed():
    known = {
        1: {"last_modified": "a", "details_fetched_at": NOW - timedelta(days=31)},
        2: {"last_modified": "a", "details_fetched_at": None},
    }
    basics = {"1": {"last_modified": "a"}, "2": {"last_modified": "a"}}
    assert plan_game_fetches(basics, known, NOW, max_age_days=30) == (["1", "2"], [])


def test_first_run_without_stored_date_trusts_private_data():
    known = {1: {"last_modified": None, "details_fetched_at": None}}
    details, private = plan_game_fetches({"1": {"last_modified": "x"}}, known, NOW)
    assert details == ["1"] and private == []


def test_chunks_of_twenty():
    ids = [str(i) for i in range(45)]
    assert [len(c) for c in chunks(ids, 20)] == [20, 20, 5]


def test_parse_thing_batch_by_id():
    root = ET.fromstring(
        """<items>
          <item type="boardgame" id="1"><name type="primary" value="A"/><minplayers value="2"/></item>
          <item type="boardgameexpansion" id="2"><name type="primary" value="B"/></item>
        </items>"""
    )
    parsed = parse_thing_batch(root)
    assert set(parsed) == {"1", "2"}
    assert parsed["1"]["original_title"] == "A" and parsed["1"]["min_players"] == 2
    assert parsed["2"]["type"] == "boardgameexpansion"


def test_collection_basics_keep_last_modified():
    item = ET.fromstring(
        """<item objectid="1"><name>A</name>
           <status own="1" lastmodified="2026-10-01 10:00:00"/></item>"""
    )
    assert extract_collection_basics(item)["last_modified"] == "2026-10-01 10:00:00"
