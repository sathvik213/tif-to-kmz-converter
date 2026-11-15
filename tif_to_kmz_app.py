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
import math
import re
import glob
import xml.etree.ElementTree as ET

st.set_page_config(page_title="Geo Converters", layout="centered")
st.title("Geo Converters")

tab_tif, tab_kml, tab_area, tab_shp, tab_legal = st.tabs(
    ["TIF → KMZ", "KML → GeoJSON", "KML/GeoJSON → Acreage", "Shapefile ZIP → Acreage",
     "Legal → Boundary"]
)

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


# ---------------- Area Calculator ----------------
WGS84_RADIUS = 6378137.0  # equatorial radius, meters
SQM_PER_ACRE = 4046.8564224
SQM_PER_HECTARE = 10000.0


def _ring_area_sphere(ring) -> float:
    """Chamberlain & Duquette (JPL 2007) spherical-excess ring area on WGS84 sphere.
    Same formula as @mapbox/geojson-area. Returns signed area in square meters.
    """
    n = len(ring)
    if n < 3:
        return 0.0
    g = 0.0
    for i in range(n):
        p1 = ring[(i - 1) % n]
        p2 = ring[i]
        p3 = ring[(i + 1) % n]
        g += (math.radians(p3[0]) - math.radians(p1[0])) * math.sin(math.radians(p2[1]))
    return g * WGS84_RADIUS * WGS84_RADIUS / 2.0


def _geom_polygons(geom):
    t = geom.get("type")
    if t == "Polygon":
        return [geom["coordinates"]]
    if t == "MultiPolygon":
        return geom["coordinates"]
    if t == "GeometryCollection":
        out = []
        for g in geom.get("geometries", []):
            out.extend(_geom_polygons(g))
        return out
    return []


def geometry_area_sqm(geom) -> float:
    total = 0.0
    for rings in _geom_polygons(geom):
        if not rings:
            continue
        total += abs(_ring_area_sphere(rings[0]))
        for hole in rings[1:]:
            total -= abs(_ring_area_sphere(hole))
    return total


def _kml_to_features(kml_bytes: bytes):
    """Parse KML and return a list of dicts with 'properties' and 'geometry'."""
    root = ET.fromstring(kml_bytes)
    features = []
    for placemark in root.iter(f"{KML_NS}Placemark"):
        polys = _extract_polygons(placemark)
        if not polys:
            continue
        features.append({
            "properties": _extract_properties(placemark),
            "geometry": {"type": "MultiPolygon", "coordinates": polys},
        })
    return features


def _geojson_to_features(gj_bytes: bytes):
    """Parse GeoJSON bytes into a list of feature-like dicts."""
    obj = json.loads(gj_bytes.decode("utf-8"))
    t = obj.get("type")
    if t == "FeatureCollection":
        return [{"properties": f.get("properties") or {}, "geometry": f["geometry"]}
                for f in obj.get("features", []) if f.get("geometry")]
    if t == "Feature":
        return [{"properties": obj.get("properties") or {}, "geometry": obj["geometry"]}]
    # Bare geometry
    return [{"properties": {}, "geometry": obj}]


with tab_area:
    st.header("KML/GeoJSON → Acreage")
    st.markdown(
        "**How it works:** Upload KML or GeoJSON files. For each polygon (or MultiPolygon) "
        "the area is computed on the WGS84 sphere using the Chamberlain–Duquette spherical-excess "
        "formula — the same algorithm used by `@mapbox/geojson-area` and popular online tools. "
        "Holes (inner rings) are subtracted, and points/lines are ignored. Accuracy is within "
        "~0.1% of true ellipsoidal area."
    )

    area_files = st.file_uploader(
        "Upload KML or GeoJSON file(s)",
        type=["kml", "geojson", "json"],
        accept_multiple_files=True,
        key="area_uploader",
    )

    unit = st.radio(
        "Primary unit",
        ["Acres", "Hectares", "Square meters"],
        index=0,
        horizontal=True,
    )

    if st.button("Compute area", key="btn_area"):
        if not area_files:
            st.warning("Please upload at least one file first.")
            st.stop()

        all_rows = []
        for f in area_files:
            try:
                data = f.read()
                f.seek(0)
                ext = os.path.splitext(f.name)[1].lower()
                feats = _kml_to_features(data) if ext == ".kml" else _geojson_to_features(data)
            except ET.ParseError as e:
                st.error(f"{f.name}: invalid KML XML — {e}")
                continue
            except json.JSONDecodeError as e:
                st.error(f"{f.name}: invalid JSON — {e}")
                continue
            except Exception as e:
                st.error(f"{f.name}: {e}")
                continue

            if not feats:
                st.warning(f"{f.name}: no polygon features found.")
                continue

            for i, feat in enumerate(feats):
                sqm = geometry_area_sqm(feat["geometry"])
                props = feat.get("properties") or {}
                label = (
                    props.get("name")
                    or props.get("level0")
                    or props.get("id")
                    or f"feature[{i}]"
                )
                all_rows.append({
                    "file": f.name,
                    "feature": str(label),
                    "geom": feat["geometry"]["type"],
                    "sqm": sqm,
                    "hectares": sqm / SQM_PER_HECTARE,
                    "acres": sqm / SQM_PER_ACRE,
                })

        if not all_rows:
            st.error("No polygons to measure.")
            st.stop()

        # Sort key by chosen unit
        sort_key = {"Acres": "acres", "Hectares": "hectares", "Square meters": "sqm"}[unit]

        # Render table
        st.subheader("Per-feature areas")
        st.dataframe(
            [
                {
                    "file": r["file"],
                    "feature": r["feature"],
                    "geometry": r["geom"],
                    "acres": round(r["acres"], 4),
                    "hectares": round(r["hectares"], 4),
                    "m²": round(r["sqm"], 2),
                }
                for r in all_rows
            ],
            use_container_width=True,
            hide_index=True,
        )

        # Totals
        tot_sqm = sum(r["sqm"] for r in all_rows)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total acres", f"{tot_sqm / SQM_PER_ACRE:,.4f}")
        c2.metric("Total hectares", f"{tot_sqm / SQM_PER_HECTARE:,.4f}")
        c3.metric("Total m²", f"{tot_sqm:,.2f}")

        # Per-file CSV download
        csv_lines = ["file,feature,geometry,acres,hectares,sqm"]
        for r in all_rows:
            feat_safe = r["feature"].replace(",", " ")
            csv_lines.append(
                f'{r["file"]},{feat_safe},{r["geom"]},{r["acres"]:.6f},{r["hectares"]:.6f},{r["sqm"]:.2f}'
            )
        st.download_button(
            "Download CSV",
            data="\n".join(csv_lines).encode("utf-8"),
            file_name="areas.csv",
            mime="text/csv",
            key="dl_areas_csv",
        )


# ---------------- Shapefile ZIP → Acreage ----------------
def _reproject_coords(coords, transformer):
    """Recursively transform a GeoJSON coordinate array in-place-ish.
    A coordinate is a [x, y] pair; anything else is a nested list of coords."""
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        x, y = transformer.transform(coords[0], coords[1])
        return [x, y]
    return [_reproject_coords(c, transformer) for c in coords]


def _shapefile_zip_to_features(zip_bytes: bytes):
    """Extract a zipped shapefile, read its polygons, and return feature dicts
    with geometry reprojected to WGS84 (EPSG:4326) lon/lat.

    Returns (features, crs_label). Raises ValueError if no .shp is found.
    """
    import shapefile  # pyshp
    from pyproj import CRS, Transformer

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(tmpdir)

        shp_paths = glob.glob(os.path.join(tmpdir, "**", "*.shp"), recursive=True)
        if not shp_paths:
            raise ValueError("no .shp file found inside the ZIP")

        features = []
        crs_labels = []
        for shp_path in shp_paths:
            base = os.path.splitext(shp_path)[0]

            # Figure out the source CRS from the sidecar .prj (WGS84 if absent).
            prj_path = base + ".prj"
            transformer = None
            if os.path.exists(prj_path):
                with open(prj_path, "r", encoding="utf-8", errors="replace") as pf:
                    wkt = pf.read()
                try:
                    src_crs = CRS.from_wkt(wkt)
                    crs_labels.append(src_crs.name)
                    if not src_crs.is_geographic:
                        transformer = Transformer.from_crs(
                            src_crs, CRS.from_epsg(4326), always_xy=True
                        )
                except Exception:
                    crs_labels.append("unknown (.prj unreadable)")
            else:
                crs_labels.append("assumed WGS84 (no .prj)")

            reader = shapefile.Reader(shp_path)
            field_names = [f[0] for f in reader.fields[1:]]  # skip DeletionFlag
            for sr in reader.shapeRecords():
                geom = sr.shape.__geo_interface__
                if geom.get("type") not in ("Polygon", "MultiPolygon"):
                    continue
                if transformer is not None:
                    geom = {
                        "type": geom["type"],
                        "coordinates": _reproject_coords(geom["coordinates"], transformer),
                    }
                props = dict(zip(field_names, sr.record))
                features.append({"properties": props, "geometry": geom})
            reader.close()

        crs_label = ", ".join(sorted(set(crs_labels))) if crs_labels else "unknown"
        return features, crs_label


with tab_shp:
    st.header("Shapefile ZIP → Acreage")
    st.markdown(
        "**How it works:** Upload a **zipped** shapefile (a `.zip` containing at least "
        "`.shp`, `.shx`, `.dbf` — plus `.prj` for the coordinate system). Each polygon is "
        "reprojected to WGS84 and measured with the same Chamberlain–Duquette spherical-excess "
        "formula used by the Area Calculator tab, so the acreage matches. If the `.prj` is "
        "missing, coordinates are assumed to already be WGS84 lon/lat."
    )

    shp_files = st.file_uploader(
        "Upload zipped shapefile(s)",
        type=["zip"],
        accept_multiple_files=True,
        key="shp_uploader",
    )

    shp_unit = st.radio(
        "Primary unit",
        ["Acres", "Hectares", "Square meters"],
        index=0,
        horizontal=True,
        key="shp_unit",
    )

    if st.button("Compute acreage", key="btn_shp"):
        if not shp_files:
            st.warning("Please upload at least one zipped shapefile first.")
            st.stop()

        all_rows = []
        for f in shp_files:
            try:
                data = f.read()
                f.seek(0)
                feats, crs_label = _shapefile_zip_to_features(data)
            except zipfile.BadZipFile:
                st.error(f"{f.name}: not a valid ZIP file.")
                continue
            except ValueError as e:
                st.error(f"{f.name}: {e}")
                continue
            except Exception as e:
                st.error(f"{f.name}: {e}")
                continue

            if not feats:
                st.warning(f"{f.name}: no polygon features found.")
                continue

            st.caption(f"{f.name}: CRS = {crs_label}")
            for i, feat in enumerate(feats):
                sqm = geometry_area_sqm(feat["geometry"])
                props = feat.get("properties") or {}
                label = (
                    props.get("name")
                    or props.get("Name")
                    or props.get("NAME")
                    or props.get("id")
                    or props.get("ID")
                    or f"feature[{i}]"
                )
                all_rows.append({
                    "file": f.name,
                    "feature": str(label),
                    "geom": feat["geometry"]["type"],
                    "sqm": sqm,
                    "hectares": sqm / SQM_PER_HECTARE,
                    "acres": sqm / SQM_PER_ACRE,
                })

        if not all_rows:
            st.error("No polygons to measure.")
            st.stop()

        st.subheader("Per-feature areas")
        st.dataframe(
            [
                {
                    "file": r["file"],
                    "feature": r["feature"],
                    "geometry": r["geom"],
                    "acres": round(r["acres"], 4),
                    "hectares": round(r["hectares"], 4),
                    "m²": round(r["sqm"], 2),
                }
                for r in all_rows
            ],
            use_container_width=True,
            hide_index=True,
        )

        tot_sqm = sum(r["sqm"] for r in all_rows)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total acres", f"{tot_sqm / SQM_PER_ACRE:,.4f}")
        c2.metric("Total hectares", f"{tot_sqm / SQM_PER_HECTARE:,.4f}")
        c3.metric("Total m²", f"{tot_sqm:,.2f}")

        csv_lines = ["file,feature,geometry,acres,hectares,sqm"]
        for r in all_rows:
            feat_safe = r["feature"].replace(",", " ")
            csv_lines.append(
                f'{r["file"]},{feat_safe},{r["geom"]},{r["acres"]:.6f},{r["hectares"]:.6f},{r["sqm"]:.2f}'
            )
        st.download_button(
            "Download CSV",
            data="\n".join(csv_lines).encode("utf-8"),
            file_name="shapefile_areas.csv",
            mime="text/csv",
            key="dl_shp_csv",
        )


# ---------------- Legal → Boundary ----------------
import plss


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def _cached_meridians(state: str):
    return plss.meridians_for_state(state)


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def _cached_lookup(legal_text: str, state: str, meridian: str):
    return plss.lookup(plss.parse_legal(legal_text), state, meridian)


def _drawn_feature_rows(drawings, legal_features):
    """Attach the overlapping legal + acreage to each drawn polygon."""
    from shapely.geometry import shape as _shape

    legal_shapes = [(f["properties"]["legal"], _shape(f["geometry"])) for f in legal_features]
    rows = []
    for d in drawings or []:
        geom = d.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        g = _shape(geom)
        overlaps = [name for name, ls in legal_shapes if ls.intersects(g)]
        rows.append({
            "geometry": geom,
            "legal": ", ".join(overlaps),
            "acres": geometry_area_sqm(geom) / SQM_PER_ACRE,
        })
    return rows


with tab_legal:
    import folium
    from folium.plugins import Draw
    from streamlit_folium import st_folium

    st.header("Legal Description → Field Boundary")
    st.markdown(
        "**How it works:** Enter one or more PLSS legal descriptions (one per line), e.g. "
        "`NE4NW4 Sec 12 T5N R3W` or `E2SW4 12-5N-3W`. The boundary is fetched from the BLM "
        "National PLSS dataset and drawn on satellite imagery. Then use the polygon / "
        "rectangle tools on the map to trace the actual field and download it as GeoJSON."
    )
    st.caption(
        "PLSS states only — Texas, the original 13 colonies, KY, TN, WV, VT, ME and HI use "
        "metes-and-bounds and won't resolve. Aliquot parts come from BLM quarter-quarter "
        "polygons where published; otherwise the section is split evenly (approximate)."
    )

    states = sorted(plss.PLSS_STATES.items(), key=lambda kv: kv[1])
    c1, c2 = st.columns(2)
    state = c1.selectbox(
        "State",
        [abbr for abbr, _ in states],
        index=[abbr for abbr, _ in states].index("NE"),
        format_func=lambda a: f"{plss.PLSS_STATES[a]} ({a})",
        key="legal_state",
    )
    try:
        meridians = _cached_meridians(state)
    except plss.PLSSError as e:
        st.error(str(e))
        meridians = []
    meridian = c2.selectbox(
        "Principal meridian",
        [code for code, _ in meridians] or ["06"],
        format_func=lambda c: next((f"{n} ({c})" for code, n in meridians if code == c), c),
        key="legal_meridian",
        help="Used when the legal text doesn't name a meridian (e.g. '6th P.M.').",
    )

    legal_text = st.text_area(
        "Legal description(s) — one per line",
        placeholder="NW4 Sec 12 T5N R3W\nE2SW4 Sec 13 T5N R3W",
        height=110,
        key="legal_text",
    )

    if st.button("Show on map", key="btn_legal"):
        lines = [ln for ln in legal_text.splitlines() if ln.strip()]
        if not lines:
            st.warning("Enter at least one legal description.")
        else:
            features, errors = [], []
            with st.spinner("Looking up legals from BLM PLSS…"):
                for ln in lines:
                    try:
                        features.append(_cached_lookup(ln.strip(), state, meridian))
                    except plss.PLSSError as e:
                        errors.append(f"`{ln.strip()}` — {e}")
            st.session_state["legal_features"] = features
            st.session_state["legal_errors"] = errors

    legal_features = st.session_state.get("legal_features", [])
    for err in st.session_state.get("legal_errors", []):
        st.error(err)

    if legal_features:
        st.dataframe(
            [
                {
                    "legal": f["properties"]["legal"],
                    "acres": round(geometry_area_sqm(f["geometry"]) / SQM_PER_ACRE, 2),
                    "source": f["properties"]["source"],
                }
                for f in legal_features
            ],
            use_container_width=True,
            hide_index=True,
        )

    # Map
    m = folium.Map(location=[39.5, -98.35], zoom_start=4, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite",
    ).add_to(m)
    folium.TileLayer("OpenStreetMap", name="Streets", show=False).add_to(m)

    if legal_features:
        fc = {"type": "FeatureCollection", "features": legal_features}
        folium.GeoJson(
            fc,
            name="Legal boundaries",
            style_function=lambda _: {"color": "#ffd400", "weight": 3, "fillOpacity": 0.08},
            # Non-interactive so clicks fall through to the draw tool.
            interactive=False,
        ).add_to(m)
        from shapely.geometry import shape as _shape
        from shapely.ops import unary_union as _union

        minx, miny, maxx, maxy = _union([_shape(f["geometry"]) for f in legal_features]).bounds
        m.fit_bounds([[miny, minx], [maxy, maxx]])

    Draw(
        export=False,
        draw_options={
            "polyline": False,
            "circle": False,
            "circlemarker": False,
            "marker": False,
            "polygon": {"allowIntersection": False, "showArea": True},
            "rectangle": True,
        },
        edit_options={"edit": True, "remove": True},
    ).add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)

    map_state = st_folium(
        m,
        key="legal_map",
        height=560,
        use_container_width=True,
        returned_objects=["all_drawings"],
    )

    # Drawn boundaries
    drawn = _drawn_feature_rows((map_state or {}).get("all_drawings"), legal_features)
    st.subheader("Drawn boundaries")
    if not drawn:
        st.info("Use the polygon or rectangle tool (left side of the map) to draw the field boundary.")
    else:
        field_name = st.text_input("Boundary name", value="field", key="legal_field_name")
        st.dataframe(
            [
                {"#": i + 1, "legal": r["legal"] or "—", "acres": round(r["acres"], 2)}
                for i, r in enumerate(drawn)
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.metric("Total drawn acres", f"{sum(r['acres'] for r in drawn):,.2f}")

        out_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": field_name if len(drawn) == 1 else f"{field_name}_{i + 1}",
                        "legal": r["legal"],
                        "acres": round(r["acres"], 4),
                    },
                    "geometry": r["geometry"],
                }
                for i, r in enumerate(drawn)
            ],
        }
        safe_name = re.sub(r"[^\w\-]+", "_", field_name).strip("_") or "field"
        st.download_button(
            "Download drawn boundary (GeoJSON)",
            data=json.dumps(out_fc, indent=2).encode("utf-8"),
            file_name=f"{safe_name}.geojson",
            mime="application/geo+json",
            key="dl_drawn_geojson",
        )

    if legal_features:
        st.download_button(
            "Download legal boundaries (GeoJSON)",
            data=json.dumps({"type": "FeatureCollection", "features": legal_features}, indent=2).encode("utf-8"),
            file_name="legal_boundaries.geojson",
            mime="application/geo+json",
            key="dl_legal_geojson",
        )
