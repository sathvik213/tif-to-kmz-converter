# Geo Converters

A Streamlit app with two converters for geospatial data:

1. **TIF → KMZ** — render GeoTIFFs as colorized ground overlays viewable in Google Earth.
2. **KML → GeoJSON** — extract polygons from KML files into MultiPolygon GeoJSON.

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
