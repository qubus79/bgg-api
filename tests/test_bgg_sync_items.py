"""Synchronizacja wybranych gier: zapis obecnych, usunięcie brakujących."""

import asyncio
import xml.etree.ElementTree as ET

import pytest

from app.scraper import bgg_game as scraper

COLLECTION = """<items totalitems="1">
  <item objecttype="thing" objectid="10" subtype="boardgame" collid="5">
    <name>Ark Nova</name>
    <status own="1" prevowned="0" fortrade="0" want="0" wanttoplay="0" wanttobuy="0"
            wishlist="0" preordered="0" lastmodified="2026-10-18 10:00:00"/>
  </item>
</items>"""

THING = """<items><item type="boardgame" id="10"><name type="primary" value="Ark Nova"/></item></items>"""


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *args, **kwargs):
        class _Result:
            def scalars(self):
                class _Scalars:
                    def all(self):
                        return []
                return _Scalars()
        return _Result()


def _run(monkeypatch, collection_xml, in_collection=None):
    in_collection = in_collection if in_collection is not None else {20: False}
    persisted = {}

    async def fake_fetch_xml(client, url, on_wait=None):
        return ET.fromstring(collection_xml if "/collection" in url else THING)

    async def fake_private(client, auth, bgg_id):
        return None

    async def fake_persist(games_data, collection_ids=None, delete_ids=None):
        persisted["games"] = games_data
        persisted["delete"] = delete_ids
        return 0, len(games_data), len(delete_ids or ()), [], [], []

    monkeypatch.setattr(scraper, "fetch_xml", fake_fetch_xml)
    monkeypatch.setattr(scraper, "fetch_private_collection_item", fake_private)
    async def fake_in_collection(client, auth, bgg_id):
        return in_collection.get(bgg_id)

    monkeypatch.setattr(scraper, "is_in_private_collection", fake_in_collection)
    monkeypatch.setattr(scraper, "_persist_games", fake_persist)
    monkeypatch.setattr(scraper, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(scraper, "PRIVATE_PAUSE_SECONDS", 0)
    result = asyncio.run(scraper.sync_collection_items("qubus", [10, 20]))
    return result, persisted


def test_present_game_is_saved_and_missing_one_removed(monkeypatch):
    result, persisted = _run(monkeypatch, COLLECTION)
    assert [g["bgg_id"] for g in persisted["games"]] == [10]
    assert persisted["games"][0]["status_owned"] is True
    assert persisted["delete"] == {20}
    assert result["removed"] == [20]


def test_bgg_error_never_deletes(monkeypatch):
    with pytest.raises(RuntimeError):
        _run(monkeypatch, "<errors><error><message>Invalid username</message></error></errors>")


def test_missing_but_unconfirmed_game_is_kept(monkeypatch):
    result, persisted = _run(monkeypatch, COLLECTION, in_collection={20: None})
    assert persisted["delete"] == set()
    assert result["removed"] == []
