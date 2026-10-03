"""Wersja posiadanego egzemplarza z `collection&version=1` i wydawcy z `thing`."""

import xml.etree.ElementTree as ET

from app.scraper.bgg_game import extract_collection_basics, extract_details, extract_owned_version

COLLECTION_ITEM = """
<item objecttype="thing" objectid="224517" subtype="boardgame" collid="1">
  <name sortindex="1">Brass: Birmingham</name>
  <status own="1" prevowned="0" fortrade="0" want="0" wanttoplay="0" wanttobuy="0"
          wishlist="0" preordered="0" lastmodified="2025-01-01 10:00:00"/>
  <numplays>3</numplays>
  <version>
    <item type="boardgameversion" id="512345">
      <name type="primary" sortindex="1" value="Polish edition"/>
      <link type="boardgameversion" id="224517" value="Brass: Birmingham" inbound="true"/>
      <link type="boardgamepublisher" id="7466" value="Phalanx"/>
      <link type="language" id="2192" value="Polish"/>
      <link type="language" id="2184" value="English"/>
      <yearpublished value="2019"/>
    </item>
  </version>
</item>
"""


def test_owned_version_name_and_languages():
    item = ET.fromstring(COLLECTION_ITEM)
    assert extract_owned_version(item) == {
        "version_name": "Polish edition",
        "version_languages": "Polish, English",
    }
    basics = extract_collection_basics(item)
    assert basics["version_name"] == "Polish edition"
    assert basics["version_languages"] == "Polish, English"


def test_no_version_set():
    item = ET.fromstring('<item objectid="1"><name>X</name><status own="1"/></item>')
    assert extract_owned_version(item) == {"version_name": None, "version_languages": None}


def test_publishers_from_thing():
    thing = ET.fromstring(
        """<item type="boardgame" id="224517">
             <name type="primary" value="Brass: Birmingham"/>
             <link type="boardgamepublisher" id="1" value="Roxley"/>
             <link type="boardgamepublisher" id="2" value="Phalanx"/>
             <link type="boardgamedesigner" id="3" value="Gavan Brown"/>
           </item>"""
    )
    assert extract_details(thing)["publishers"] == ["Roxley", "Phalanx"]
