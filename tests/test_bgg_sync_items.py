"""Synchronizacja wybranych gier: prywatne API jako źródło statusów,
zapis obecnych, usunięcie tylko potwierdzonych."""

import asyncio
import xml.etree.ElementTree as ET

import pytest

from app.scraper import bgg_game as scraper

# Publiczne XML jeszcze ze starym stanem: Ark Nova bez statusu „own".
COLLECTION = """<items totalitems="1">
  <item objecttype="thing" objectid="10" subtype="boardgame" collid="5">
    <name>Ark Nova</name>
    <numplays>3</numplays>
    <status own="0" prevowned="0" fortrade="0" want="0" wanttoplay="0" wanttobuy="0"
            wishlist="1" wishlistpriority="2" preordered="0" lastmodified="2026-10-18 10:00:00"/>
  </item>
</items>"""

THING = """<items>
  <item type="boardgame" id="10">
    <name type="primary" value="Ark Nova"/>
    <versions>
      <item type="boardgameversion" id="555">
        <name type="primary" value="Polish edition"/>
        <link type="language" id="1" value="Polish"/>
      </item>
    </versions>
  </item>
  <item type="boardgame" id="30">
    <name type="primary" value="Brand New"/>
    <yearpublished value="2026"/>
    <image>https://img/30.jpg</image>
  </item>
</items>"""


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


def _run(monkeypatch, collection_xml, entries, ids):
    persisted = {}

    async def fake_fetch_xml(client, url, on_wait=None):
        return ET.fromstring(collection_xml if "/collection" in url else THING)

    async def fake_entry(client, auth, bgg_id):
        return entries.get(bgg_id)

    async def fake_persist(games_data, collection_ids=None, delete_ids=None):
        persisted["games"] = {g["bgg_id"]: g for g in games_data}
        persisted["delete"] = delete_ids
        return 0, len(games_data), len(delete_ids or ()), [], [], []

    monkeypatch.setattr(scraper, "fetch_xml", fake_fetch_xml)
    monkeypatch.setattr(scraper, "fetch_private_collection_entry", fake_entry)
    monkeypatch.setattr(scraper, "_persist_games", fake_persist)
    monkeypatch.setattr(scraper, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(scraper, "PRIVATE_PAUSE_SECONDS", 0)
    monkeypatch.setattr(scraper, "THING_REQUEST_PAUSE_SECONDS", 0)
    result = asyncio.run(scraper.sync_collection_items("qubus", ids))
    return result, persisted


ARK_ENTRY = {
    "collid": "5", "status": {"own": True, "wishlist": False},
    "wishlistpriority": 0, "versionid": "555", "pricepaid": 199.0, "pp_currency": "PLN",
}


def test_private_entry_wins_over_stale_xml(monkeypatch):
    _, persisted = _run(monkeypatch, COLLECTION, {10: ARK_ENTRY}, [10])
    ark = persisted["games"][10]
    assert ark["status_owned"] is True and ark["status_wishlist"] is False
    assert ark["num_plays"] == 3
    assert ark["version_name"] == "Polish edition" and ark["version_languages"] == "Polish"
    assert ark["purchase_price_paid"] == 199.0


def test_game_added_on_bgg_but_not_yet_in_xml_is_saved(monkeypatch):
    entry = {"collid": "9", "status": {"wishlist": True}, "wishlistpriority": 1}
    _, persisted = _run(monkeypatch, COLLECTION, {10: ARK_ENTRY, 30: entry}, [10, 30])
    new = persisted["games"][30]
    assert new["title"] == "Brand New" and new["year_published"] == 2026
    assert new["status_wishlist"] is True and new["status_wishlist_priority"] == 1


def test_only_confirmed_removals_are_deleted(monkeypatch):
    result, persisted = _run(monkeypatch, COLLECTION, {10: ARK_ENTRY, 20: {}, 40: None}, [10, 20, 40])
    assert persisted["delete"] == {20}
    assert result["removed"] == [20]
    assert 40 not in persisted["games"]


def test_bgg_error_never_deletes(monkeypatch):
    with pytest.raises(RuntimeError):
        _run(monkeypatch, "<errors><error><message>Invalid username</message></error></errors>", {20: {}}, [20])
