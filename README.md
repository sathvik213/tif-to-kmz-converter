# Geo Converters

A Streamlit app with five tools for geospatial data:

1. **TIF → KMZ** — render GeoTIFFs as colorized ground overlays viewable in Google Earth.
2. **KML → GeoJSON** — extract polygons from KML files into MultiPolygon GeoJSON.
3. **Area Calculator** — compute geodesic area (acres / hectares / m²) for KML or GeoJSON polygons.
4. **Shapefile ZIP → Acreage** — measure polygons in a zipped shapefile.
5. **Legal → Boundary** — map a PLSS legal description and draw/export the field boundary as GeoJSON.

## Features

### TIF → KMZ
- Upload one or more GeoTIFF files
- Choose from multiple colormaps (YlGnBu, viridis, RdYlGn, etc.)
- Adjustable pixel upscale factor for crisp rendering in Google Earth
- Configurable nodata threshold
- Download KMZ files with transparent nodata regions

### KML → GeoJSON
- Upload one or more KML files
- Output as a single `MultiPolygon` feature per file (default) or one feature per Placemark
- Preserves `ExtendedData` fields and `<name>` as GeoJSON feature properties
- Outer + inner rings (holes) preserved
- Optional ZIP bundle of all outputs

### Area Calculator
- Upload KML or GeoJSON files
- Computes area on the WGS84 sphere using the Chamberlain–Duquette spherical-excess formula (same algorithm as `@mapbox/geojson-area`)
- Holes (inner rings) subtracted; non-polygon geometries ignored
- Reports per-feature acres, hectares, and m², plus totals
- CSV export of all measured features
- Within ~0.1% of true ellipsoidal area — well below survey tolerance

### Legal → Boundary
- Enter PLSS legals one per line (`NE4NW4 Sec 12 T5N R3W`, `E2SW4 12-5N-3W`, `N 1/2 Sec. 3, Twp 152N, Rge 97W`, ...)
- Boundaries come from the BLM National PLSS CadNSDI service; aliquot parts use BLM quarter-quarter polygons where published, otherwise an even split of the section
- Shown on satellite imagery; draw the actual field with the polygon/rectangle tools
- Download drawn boundaries as GeoJSON (with legal + acres properties), or the legal boundaries themselves
- PLSS states only (not TX, original 13 colonies, KY, TN, WV, VT, ME, HI)

## Run locally

```bash
pip install -r requirements.txt
streamlit run tif_to_kmz_app.py
```

## Deploy on Streamlit Cloud

1. Fork or push this repo to your GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Select this repo, branch `main`, and file `tif_to_kmz_app.py`
4. Deploy
