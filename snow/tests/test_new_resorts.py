from datetime import datetime, timezone

from snow_report import palisades_tahoe, revelstoke

# --- Revelstoke: trimmed from the snow report and webcams pages ---


def figure(label: str, value: str, unit: str = "cm") -> str:
    return (f'<div class="snow-report__amount"> <h2 class="snow-report__title">{label}</h2> <span class="snow-report__data"> '
            f'<span class="value">{value}</span> <span class="unit">{unit}</span> </span> </div>')


SNOW_PAGE = figure("New Snow", "1") + figure("24 hours", "1") + figure("7 days", "57") + figure("Base Depth", "38") + figure("Season Total", "143")
WEBCAMS_PAGE = (
    '<iframe allowfullscreen src="https://relay.ozolio.com/pub.api?cmd=embed&amp;oid=EMB_NJJN000010BA" '
    'title="Gnorm the Powder Gnome (27cm | 10.6in tall) - Cleared @ 3pm Daily"></iframe>'
    '<iframe src=" https://relay.ozolio.com/pub.api?cmd=embed&amp;oid=EMB_PVWK000010B0" title="Revelstoke Mountain Resort -  Village Webcam"></iframe>'
)
NOW = datetime(2026, 11, 18, 19, 0, tzinfo=timezone.utc)


def test_revelstoke_reads_figures_in_centimetres_and_shows_inches() -> None:
    figures = revelstoke.parse_snow(SNOW_PAGE)
    assert figures == {"New Snow": 1, "24 hours": 1, "7 days": 57, "Base Depth": 38, "Season Total": 143}
    resort = revelstoke.build(figures, revelstoke.parse_webcams(WEBCAMS_PAGE), NOW)
    assert [(s.label, s.value, s.detail) for s in resort.summary] == [
        ("Last 24 hours", "0.4″", "1 cm"), ("Base depth", "15″", "38 cm"), ("Season total", "56.3″", "143 cm"),
    ]
    assert resort.tables[0].rows[-1] == ["Season Total", "56.3″", "143 cm"]


def test_revelstoke_webcam_names() -> None:
    cams = revelstoke.parse_webcams(WEBCAMS_PAGE)
    assert [(c.name, c.kind) for c in cams] == [("Gnorm the Powder Gnome", "embed"), ("Village", "embed")]
    assert cams[1].src == "https://relay.ozolio.com/pub.api?cmd=embed&oid=EMB_PVWK000010B0"


# --- Palisades Tahoe: trimmed from the webcams page ---

PALISADES_PAGE = (
    '<iframe class="optanon-category-C0003" data-src="https://relay.ozolio.com/pub.api?cmd=embed&amp;oid=EMB_ZIGI00000147"></iframe>'
    "<p>Palisades Scope 8200' Cam</p><div>rich-text, responsive-table</div>"
    '<iframe src="https://relay.ozolio.com/pub.api?cmd=embed&oid=EMB_EIFC0000015B"></iframe><h3>Palisades Siberia Cam</h3>'
    '<iframe data-src="https://relay.ozolio.com/pub.api?cmd=embed&amp;oid=EMB_EMST000001C4" frameborder="0"></iframe>'
    "<h2>Alpine</h2><p>Alpine Peak Cam</p>"
    '<iframe src="https://relay.ozolio.com/pub.api?cmd=embed&oid=EMB_FPOI000010AB"></iframe><div>dynamic-tabs</div>'
)


def test_palisades_webcam_names_come_from_the_text_after_each_player() -> None:
    cams = palisades_tahoe.parse_webcams(PALISADES_PAGE)
    assert [c.name for c in cams] == ["Scope 8200'", "Siberia", "Alpine Peak", "Webcam 4"]
    assert cams[0].src == "https://relay.ozolio.com/pub.api?cmd=embed&oid=EMB_ZIGI00000147"


def test_palisades_uses_its_snotel_station() -> None:
    data = [{"data": [
        {"stationElement": {"elementCode": "SNWD"}, "values": [{"date": "2026-11-17 10:00", "value": 23}, {"date": "2026-11-18 10:00", "value": 22}]},
        {"stationElement": {"elementCode": "WTEQ"}, "values": [{"date": "2026-11-18 10:00", "value": 3.4}]},
    ]}]
    resort = palisades_tahoe.build(data, palisades_tahoe.parse_webcams(PALISADES_PAGE))
    assert [(s.label, s.value) for s in resort.summary] == [("Snow depth", "22″"), ("Depth change", "-1″"), ("Water content", "3.4″")]
    assert resort.webcams[resort.featured_webcam].name == "Siberia"
    assert resort.tables[0].rows[-1] == ["Reading", "Nov 18, 10:00 AM PST"]
    assert "Palisades Tahoe station" in resort.source
