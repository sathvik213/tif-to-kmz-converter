import streamlit as st
import rasterio
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
import zipfile
import io
import tempfile
import os

st.set_page_config(page_title="TIF to KMZ Converter", layout="centered")
st.title("TIF to KMZ Converter")

st.markdown("""
**How it works:** Your TIF pixel values get mapped to colors using the settings below.
Nodata pixels become transparent. The result is a KMZ file you can open in Google Earth.
""")

colormap = st.selectbox(
    "Colormap",
    ["YlGnBu", "viridis", "RdYlGn", "coolwarm", "plasma", "Spectral"],
    index=0,
    help="The color scheme used to represent your data. "
         "YlGnBu = Yellow (low) → Green → Blue (high). "
         "RdYlGn = Red (low) → Yellow → Green (high). "
         "Try different ones to see what looks best for your data."
)
scale = st.slider(
    "Pixel upscale factor",
    1, 20, 10,
    help="Makes pixels appear as crisp squares in Google Earth instead of blurry blobs. "
         "Higher = sharper but larger file size. "
         "Set to 1 if your TIF already has high resolution."
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
         "Check your TIF metadata if unsure."
)

uploaded_files = st.file_uploader("Upload GeoTIFF file(s)", type=["tif", "tiff"], accept_multiple_files=True)

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
