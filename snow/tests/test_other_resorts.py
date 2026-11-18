from datetime import datetime, timezone

import pytest

from snow_report import big_sky, grand_targhee, jackson_hole

# --- Big Sky: trimmed from the snow depth page and the webcams page ---

STATION_PANEL = """<section role="tabpanel" id="panel_{key}" aria-labelledby="tab_{key}" tabindex="0">{offline}
<div class="pb-6 pt-8 grid">
<div class="align-top"><div class="text-4xl font-semibold block align-top text-black">{new}<span class="text-4xl">&quot;</span></div><div class="text-sm font-normal block text-black">New Snow<br />4 p.m. to Now</div></div>
<div class="align-top"><div class="text-4xl font-semibold block align-top text-black">{day}<span class="text-4xl">&quot;</span></div><div class="text-sm font-normal block text-black">Last 24 Hours</div></div>
<div class="align-top"><div class="text-4xl font-semibold block align-top text-black">{week}<span class="text-4xl">&quot;</span></div><div class="text-sm font-normal block text-black">Last 7 Days</div></div>
<div class="align-top"><div class="text-4xl font-semibold block align-top text-black">{base}<span class="text-4xl">&quot;</span></div><div class="text-sm font-normal block text-black">Base Depth</div></div>
</div></section>"""
SNOW_PAGE = (
    '<button role="tab" id="tab_andesite" data-stationname="andesite"><span class="text-black">Andesite</span></button>'
    '<button role="tab" id="tab_libertybowl"><span class="text-bsr-blue">Liberty Bowl</span></button>'
    '<button role="tab" id="tab_lookoutridge"><span class="text-bsr-blue">Lookout Ridge</span></button>'
    + STATION_PANEL.format(key="andesite", offline="", new=6, day=6, week=6, base=8)
    + STATION_PANEL.format(key="libertybowl", offline="<p>This station is offline.</p>", new="--", day="--", week="--", base="--")
    + STATION_PANEL.format(key="lookoutridge", offline="", new=9, day=9, week=9, base=17)
)
WEBCAMS_PAGE = (
    '<iframe class="aspect-video" src="https://camstreamer.com/embed/abc?enablejsapi=1&amp;rel=0" loading="lazy" title="Lone Peak Tram Camera"></iframe>'
    '<iframe src="https://camstreamer.com/embed/abc?enablejsapi=1&amp;rel=0" title="Lone Peak Tram Camera"></iframe>'  # repeated in the page
    '<iframe src="https://camstreamer.com/embed/def" title="Everett&#x27;s 8800 Camera"></iframe>'
    '<iframe src="https://www.youtube.com/embed/xyz" title="Promo video"></iframe>'
)
NOW = datetime(2026, 11, 18, 19, 0, tzinfo=timezone.utc)


def test_big_sky_parses_each_station() -> None:
    andesite, liberty, lookout = big_sky.parse_snow(SNOW_PAGE)
    assert andesite == {
        "name": "Andesite", "offline": False, "Since 4 p.m.": '6"', "Last 24 hours": '6"',
        "Last 2 days": None, "Last 3 days": None, "Last 7 days": '6"', "Base depth": '8"',
    }
    assert (liberty["name"], liberty["offline"]) == ("Liberty Bowl", True)
    assert lookout["Base depth"] == '17"'


def test_big_sky_webcams_are_deduplicated_live_players() -> None:
    cams = big_sky.parse_webcams(WEBCAMS_PAGE)
    assert [(c.name, c.kind, c.src) for c in cams] == [
        ("Lone Peak Tram", "embed", "https://camstreamer.com/embed/abc?enablejsapi=1&rel=0"),
        ("Everett's 8800", "embed", "https://camstreamer.com/embed/def"),
    ]


def test_big_sky_summary_spans_online_stations() -> None:
    resort = big_sky.build(big_sky.parse_snow(SNOW_PAGE), big_sky.parse_webcams(WEBCAMS_PAGE), NOW)
    assert [(s.label, s.value) for s in resort.summary] == [
        ("Last 24 hours", "6–9″"), ("Last 7 days", "6–9″"), ("Base depth", "8–17″"),
    ]
    assert resort.tables[0].rows[1] == ["Liberty Bowl", "Offline", "–", "–", "–", "–", "–"]
    assert resort.featured_webcam == 0


# --- Jackson Hole: trimmed snow.json and web-cams.json ---

def inch(v):
    return {"unit": "INCH", "unitSymbol": '"', "value": v}


JH_SNOW = {
    "lastModified": "2026-11-18T08:30:00.000-07:00",
    "snow": {
        "detail": "October 27, 2026 — Winter is here.\n",
        "midMountain": {"name": "Mid-Mountain", "seasonTotalSnow": inch("6"), "newSnowLast24H": inch("2"), "newSnowLast7D": {}, "totalSnowDepth": inch("1")},
        "tramSummit": {"name": "Tram Summit", "seasonTotalSnow": inch("23"), "newSnowLast24H": inch("4"), "newSnowSince6am": inch("0"), "totalSnowDepth": inch("9")},
        "base": {},
    },
    "weather": {
        "tramSummit": {"name": "Tram Summit (Current)", "temperature": {"value": "12"}, "wind": {"value": "6"}},
        "base": {"name": "Base (Current)", "temperature": {"value": "30"}, "wind": {}},
    },
}
JH_CAMS = {"webcams": [
    {"isPanoCam": True, "thumbUrl": "https://backend.roundshot.com/cams/a/medium", "url": "https://backend.roundshot.com/cams/b/default", "liveStreamUrl": "", "youtubeId": "", "caption": "Summit - Pano Cam"},
    {"isPanoCam": False, "thumbUrl": "https://cams.jacksonhole.com/webcam/thumbnails/codybowl.jpg", "url": "https://cams.jacksonhole.com/webcam/codybowl.jpg", "liveStreamUrl": "", "youtubeId": "", "caption": "Cody Bowl"},
]}


def test_jackson_hole_summary_tables_and_webcams() -> None:
    resort = jackson_hole.build(JH_SNOW, JH_CAMS)
    assert [(s.label, s.value, s.detail) for s in resort.summary] == [
        ("Last 24 hours", "4″", "Summit"), ("Snow depth", "9″", "Summit"), ("Season total", "23″", "Summit"),
    ]
    snow, weather = resort.tables
    assert [row[0] for row in snow.rows] == ["Summit", "Mid-mountain"]  # no base readings
    assert snow.rows[0][:4] == ["Summit", "0″", "–", "4″"]
    assert weather.rows == [["Tram Summit", "12°F", "6 mph"], ["Base", "30°F", "–"]]
    # Pano cams use their still thumbnail; the featured camera is Cody Bowl.
    assert [w.src for w in resort.webcams] == ["https://backend.roundshot.com/cams/a/medium", "https://cams.jacksonhole.com/webcam/codybowl.jpg"]
    assert resort.featured_webcam == 1
    assert resort.message == "October 27, 2026 — Winter is here."
    assert resort.updated == "2026-11-18T08:30:00-07:00"


# --- Grand Targhee: trimmed SNOTEL response ---

def snotel(code: str, values: list[tuple[str, float]]) -> dict:
    return {"stationElement": {"elementCode": code}, "values": [{"date": d, "value": v} for d, v in values]}


SNOTEL = [{"stationTriplet": "1082:WY:SNTL", "data": [
    snotel("SNWD", [("2026-11-11 10:00", 6), ("2026-11-16 10:00", 7), ("2026-11-17 10:00", 8), ("2026-11-18 10:00", 14)]),
    snotel("WTEQ", [("2026-11-17 10:00", 1.9), ("2026-11-18 10:00", 2.6)]),
    snotel("TOBS", [("2026-11-18 10:00", 14.0)]),
]}]


def test_grand_targhee_depth_and_changes() -> None:
    resort = grand_targhee.build(SNOTEL)
    assert [(s.label, s.value) for s in resort.summary] == [
        ("Snow depth", "14″"), ("Depth change", "+6″"), ("Water content", "2.6″"),
    ]
    current, changes = resort.tables
    assert current.rows[-1] == ["Reading", "Nov 18, 11:00 AM MST"]  # 10:00 PST
    assert changes.rows == [
        ["Last 24 hours", "+6″", "+0.7″"],
        ["Last 48 hours", "+7″", "–"],  # no water reading that far back
        ["Last 7 days", "+8″", "–"],
    ]
    assert resort.webcams == [] and resort.webcams_page


def test_grand_targhee_needs_depth_readings() -> None:
    with pytest.raises(RuntimeError, match="No snow depth"):
        grand_targhee.build([{"data": []}])
