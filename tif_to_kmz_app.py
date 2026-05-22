import streamlit as st
import rasterio
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
import zipfile
import io
import tempfile
import os
import json
import re
import xml.etree.ElementTree as ET

st.set_page_config(page_title="Geo Converters", layout="centered")
st.title("Geo Converters")

tab_tif, tab_kml = st.tabs(["TIF → KMZ", "KML → GeoJSON"])

# ---------------- TIF → KMZ ----------------
with tab_tif:
    st.header("TIF to KMZ Converter")
    st.markdown(
        "**How it works:** Your TIF pixel values get mapped to colors using the settings below. "
        "Nodata pixels become transparent. The result is a KMZ file you can open in Google Earth."
    )

    colormap = st.selectbox(
        "Colormap",
        ["YlGnBu", "viridis", "RdYlGn", "coolwarm", "plasma", "Spectral"],
        index=0,
        help="The color scheme used to represent your data. "
             "YlGnBu = Yellow (low) → Green → Blue (high). "
             "RdYlGn = Red (low) → Yellow → Green (high). "
             "Try different ones to see what looks best for your data.",
    )
    scale = st.slider(
        "Pixel upscale factor",
        1, 20, 10,
        help="Makes pixels appear as crisp squares in Google Earth instead of blurry blobs. "
             "Higher = sharper but larger file size. "
             "Set to 1 if your TIF already has high resolution.",
    )
    st.caption(
        "**What does upscale factor do?** — Your TIF may have a small number of actual data pixels "
        "(e.g. 30x30) spread across a large grid. When Google Earth stretches this tiny image over "
        "a map area, it smooths/blurs the pixels. Upscaling multiplies each pixel into a block of "
        "identical pixels (e.g. 10x means each pixel becomes a 10x10 block) using nearest-neighbor "
        "interpolation, so they stay as sharp squares instead of getting blurred. Higher values = "
        "crisper pixels but bigger file. 10 is a good default."
    )
    nodata_threshold = st.number_input(
        "Nodata value threshold",
        value=-9998.0,
        help="Pixels with values below this number are treated as 'no data' and made transparent. "
             "Common nodata values are -9999 or -3.4e+38. "
             "Check your TIF metadata if unsure.",
    )

    uploaded_files = st.file_uploader(
        "Upload GeoTIFF file(s)", type=["tif", "tiff"], accept_multiple_files=True
    )

    if st.button("Convert to KMZ"):
        if not uploaded_files:
            st.warning("Please upload at least one TIF file first.")
            st.stop()
        # Find global min/max across all files
        all_data = []
        for f in uploaded_files:
            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
                tmp.write(f.read())
                tmp_path = tmp.name
            f.seek(0)
            with rasterio.open(tmp_path) as src:
                data = src.read(1)
                valid = data[(~np.isnan(data)) & (data > nodata_threshold)]
                if len(valid) > 0:
                    all_data.append((np.min(valid), np.max(valid)))
            os.unlink(tmp_path)

        if not all_data:
            st.error("No valid data found in uploaded files.")
        else:
            vmin = min(d[0] for d in all_data)
            vmax = max(d[1] for d in all_data)
            st.info(f"Value range: {vmin:.4f} to {vmax:.4f}")

            cmap = plt.get_cmap(colormap)

            for f in uploaded_files:
                with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
                    tmp.write(f.read())
                    tmp_path = tmp.name
                f.seek(0)

                with rasterio.open(tmp_path) as src:
                    data = src.read(1)
                    bounds = src.bounds
                os.unlink(tmp_path)

                nodata_mask = np.isnan(data) | (data < nodata_threshold)
                norm = np.clip((data - vmin) / (vmax - vmin), 0, 1)
                rgba = (cmap(norm) * 255).astype(np.uint8)
                rgba[nodata_mask, 3] = 0

                img = Image.fromarray(rgba, "RGBA")
                if scale > 1:
                    img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)

                name = os.path.splitext(f.name)[0]
                kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{name}</name>
    <GroundOverlay>
      <name>{name}</name>
      <Icon><href>overlay.png</href></Icon>
      <LatLonBox>
        <north>{bounds.top}</north>
        <south>{bounds.bottom}</south>
        <east>{bounds.right}</east>
        <west>{bounds.left}</west>
      </LatLonBox>
    </GroundOverlay>
  </Document>
</kml>'''

                kmz_buf = io.BytesIO()
                with zipfile.ZipFile(kmz_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr("doc.kml", kml)
                    png_buf = io.BytesIO()
                    img.save(png_buf, format="PNG")
                    zf.writestr("overlay.png", png_buf.getvalue())

                st.download_button(
                    label=f"Download {name}.kmz",
                    data=kmz_buf.getvalue(),
                    file_name=f"{name}.kmz",
                    mime="application/vnd.google-earth.kmz",
                )


# ---------------- KML → GeoJSON ----------------
KML_NS = "{http://www.opengis.net/kml/2.2}"


def _parse_ring(text: str):
    ring = []
    for tok in re.split(r"\s+", text.strip()):
        if not tok:
            continue
        parts = tok.split(",")
        if len(parts) < 2:
            continue
        ring.append([float(parts[0]), float(parts[1])])
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def _extract_polygons(placemark):
    polygons = []
    for poly in placemark.iter(f"{KML_NS}Polygon"):
        outer = poly.find(f"{KML_NS}outerBoundaryIs/{KML_NS}LinearRing/{KML_NS}coordinates")
        if outer is None or not outer.text:
            continue
        rings = [_parse_ring(outer.text)]
        for inner in poly.findall(f"{KML_NS}innerBoundaryIs/{KML_NS}LinearRing/{KML_NS}coordinates"):
            if inner.text:
                rings.append(_parse_ring(inner.text))
        polygons.append(rings)
    return polygons


def _extract_properties(placemark):
    props = {}
    name_el = placemark.find(f"{KML_NS}name")
    if name_el is not None and name_el.text:
        props["name"] = name_el.text.strip()
    for data in placemark.iter(f"{KML_NS}Data"):
        key = data.get("name")
        val_el = data.find(f"{KML_NS}value")
        if key and val_el is not None and val_el.text is not None:
            props[key] = val_el.text.strip()
    return props


def kml_bytes_to_geojson(kml_bytes: bytes, merge_to_multipolygon: bool = True) -> dict:
    """Parse KML bytes into a GeoJSON FeatureCollection.

    If merge_to_multipolygon=True, every polygon across all Placemarks is combined
    into a single MultiPolygon Feature whose properties come from the first Placemark
    (other Placemark properties are kept under a 'placemarks' list).
    Otherwise each Placemark becomes its own Feature (MultiPolygon).
    """
    root = ET.fromstring(kml_bytes)
    placemark_records = []
    for placemark in root.iter(f"{KML_NS}Placemark"):
        polys = _extract_polygons(placemark)
        if not polys:
            continue
        placemark_records.append((_extract_properties(placemark), polys))

    if not placemark_records:
        return {"type": "FeatureCollection", "features": []}

    if merge_to_multipolygon:
        all_polys = [p for _, polys in placemark_records for p in polys]
        props = dict(placemark_records[0][0])
        if len(placemark_records) > 1:
            props["placemarks"] = [p for p, _ in placemark_records]
        feature = {
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "MultiPolygon", "coordinates": all_polys},
        }
        return {"type": "FeatureCollection", "features": [feature]}

    features = [
        {
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "MultiPolygon", "coordinates": polys},
        }
        for props, polys in placemark_records
    ]
    return {"type": "FeatureCollection", "features": features}


with tab_kml:
    st.header("KML to GeoJSON Converter")
    st.markdown(
        "**How it works:** Upload one or more KML files. Each file's polygons are "
        "extracted (outer + inner rings preserved) and written as a `MultiPolygon` "
        "GeoJSON `FeatureCollection`. KML `ExtendedData` fields and `<name>` become "
        "GeoJSON feature properties."
    )

    merge_mode = st.radio(
        "Output structure",
        ["Single MultiPolygon feature per file", "One feature per Placemark"],
        index=0,
        help="Most KMLs here have a single Placemark, so the default just merges everything "
             "into one MultiPolygon. Pick the second option if you need per-Placemark features.",
    )
    pretty = st.checkbox("Pretty-print JSON (indent=2)", value=True)
    bundle_zip = st.checkbox(
        "Also offer a ZIP of all outputs",
        value=False,
        help="Useful when uploading many files.",
    )

    kml_files = st.file_uploader(
        "Upload KML file(s)",
        type=["kml"],
        accept_multiple_files=True,
        key="kml_uploader",
    )

    if st.button("Convert to GeoJSON"):
        if not kml_files:
            st.warning("Please upload at least one KML file first.")
            st.stop()

        results = []  # list of (filename, geojson_bytes)
        for f in kml_files:
            try:
                data = f.read()
                f.seek(0)
                fc = kml_bytes_to_geojson(
                    data,
                    merge_to_multipolygon=(merge_mode == "Single MultiPolygon feature per file"),
                )
                text = json.dumps(fc, indent=2 if pretty else None)
                out_name = os.path.splitext(f.name)[0] + ".geojson"
                results.append((out_name, text.encode("utf-8"), fc))
            except ET.ParseError as e:
                st.error(f"{f.name}: invalid KML XML — {e}")
            except Exception as e:
                st.error(f"{f.name}: {e}")

        if results:
            total_polys = 0
            for _, _, fc in results:
                for feat in fc["features"]:
                    coords = feat["geometry"]["coordinates"]
                    total_polys += len(coords)
            st.success(
                f"Converted {len(results)} file(s), {total_polys} polygon(s) total."
            )

            for out_name, payload, _ in results:
                st.download_button(
                    label=f"Download {out_name}",
                    data=payload,
                    file_name=out_name,
                    mime="application/geo+json",
                    key=f"dl_{out_name}",
                )

            if bundle_zip and len(results) > 1:
                zbuf = io.BytesIO()
                with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for out_name, payload, _ in results:
                        zf.writestr(out_name, payload)
                st.download_button(
                    label="Download all as ZIP",
                    data=zbuf.getvalue(),
                    file_name="geojson_bundle.zip",
                    mime="application/zip",
                    key="dl_zip",
                )
