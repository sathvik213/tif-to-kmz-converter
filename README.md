# TIF to KMZ Converter

A Streamlit app that converts GeoTIFF files into KMZ files for viewing in Google Earth.

## Features

- Upload one or more GeoTIFF files
- Choose from multiple colormaps (YlGnBu, viridis, RdYlGn, etc.)
- Adjustable pixel upscale factor for crisp rendering in Google Earth
- Configurable nodata threshold
- Download KMZ files with transparent nodata regions

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
