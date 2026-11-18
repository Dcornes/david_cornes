"""Grand Targhee: snow depth from the NRCS SNOTEL station on the mountain.

Grand Targhee's own website doesn't allow automated requests, so its snow report and
webcams can't be read. Instead this uses the USDA's public SNOTEL station "Grand
Targhee" (1082:WY:SNTL, 9,260 ft); see snotel.py. The page links to Targhee's webcams.
"""

from zoneinfo import ZoneInfo

import httpx

from . import snotel
from .models import Resort

ID = "grand-targhee"
NAME = "Grand Targhee"
WEBSITE = "https://www.grandtarghee.com"
WEBCAMS_PAGE = "https://www.grandtarghee.com/the-mountain/webcams"
STATION = "1082:WY:SNTL"
ZONE = ZoneInfo("America/Denver")  # shown in the resort's own time


def build(data: list[dict]) -> Resort:
    return snotel.build(
        data, id=ID, name=NAME, website=WEBSITE, station_name="Grand Targhee", station_number=1082,
        elevation="9,260 ft", zone=ZONE, webcams_page=WEBCAMS_PAGE,
    )


async def fetch(client: httpx.AsyncClient) -> Resort:
    return build(await snotel.fetch_data(client, STATION))
