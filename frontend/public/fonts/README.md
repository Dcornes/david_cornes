# Map fonts

Glyphs for the run names on the Ski Stats 3D map (MapLibre `glyphs`), so the map doesn't
depend on an outside font server.

- `Open Sans Semibold/0-255.pbf`: Latin letters, digits and punctuation.
- `Open Sans Semibold/8192-8447.pbf`: curly quotes, dashes and other general punctuation.

Every run name in `lifts/lift_vertical/data/pistes.json` uses only these ranges. If a new name
needs another range, add its `.pbf` from https://demotiles.maplibre.org/font/Open%20Sans%20Semibold/{range}.pbf

Open Sans is by Steve Matteson, licensed under the Apache License 2.0.
