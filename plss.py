"""PLSS (Township / Range / Section) legal description lookup.

Parses legal descriptions like ``NE4NW4 Sec 12 T5N R3W`` and fetches the matching
boundary from the BLM National PLSS CadNSDI ArcGIS service.

Aliquot parts (NW4, N2, E2SW4, ...) come from the BLM quarter-quarter polygons
where the state publishes them; otherwise the section polygon is split
geometrically (approximate — ignores irregular sections / government lots).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

import requests
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

BLM_PLSS_URL = "https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer"
LAYER_TOWNSHIP = 1
LAYER_SECTION = 2
LAYER_INTERSECTED = 3  # quarter-quarters, gov lots

# States surveyed under PLSS (BLM CadNSDI coverage).
PLSS_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "FL": "Florida", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "LA": "Louisiana", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NM": "New Mexico", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "SD": "South Dakota", "UT": "Utah", "WA": "Washington",
    "WI": "Wisconsin", "WY": "Wyoming",
}

QUARTERS = ("NE", "NW", "SE", "SW")
HALVES = ("N", "S", "E", "W")


class PLSSError(Exception):
    pass


# ---------------- Parsing ----------------
@dataclass
class Legal:
    township: int
    township_dir: Optional[str]  # N / S, None if the legal omits it
    range_: int
    range_dir: Optional[str]  # E / W, None if the legal omits it
    section: int
    aliquot: List[str] = field(default_factory=list)  # e.g. ["NE", "NW"] = NE4NW4
    meridian: Optional[str] = None  # 2-digit BLM PRINMERCD, if given in the text
    raw: str = ""

    @property
    def aliquot_label(self) -> str:
        return "".join(
            t + ("4" if t in QUARTERS else "2") for t in self.aliquot
        )

    @property
    def label(self) -> str:
        parts = []
        if self.aliquot:
            parts.append(self.aliquot_label)
        parts.append(f"Sec {self.section}")
        parts.append(f"T{self.township}{self.township_dir or ''}")
        parts.append(f"R{self.range_}{self.range_dir or ''}")
        return " ".join(parts)


_TWP_RE = re.compile(r"\bT(?:WP|OWNSHIP)?\.?\s*(\d{1,3})\s*-?\s*([NS])(?:ORTH|OUTH)?\b", re.I)
_RNG_RE = re.compile(r"\bR(?:NG|GE|ANGE)?\.?\s*(\d{1,3})\s*-?\s*([EW])(?:AST|EST)?\b", re.I)
_SEC_RE = re.compile(r"\bSEC(?:TION)?S?\.?\s*(\d{1,2})\b", re.I)
_SEC_SHORT_RE = re.compile(r"\bS\s*(\d{1,2})\s*$", re.I)  # trailing 'S12'; avoids 'S2' aliquot
# Compact "12-5N-3W" style: section-township-range
# Directions are optional ('8-129-58'); lookup() resolves them against the state.
_COMPACT_RE = re.compile(r"\b(\d{1,2})\s*-\s*(\d{1,3})\s*([NS])?\s*-\s*(\d{1,3})\s*([EW])?(?![\w-])", re.I)
_PM_RE = re.compile(r"\b(\d{1,2})(?:ST|ND|RD|TH)?\s*(?:P\.?\s*M\.?|PRINCIPAL\s+MERIDIAN|MERIDIAN)\b", re.I)
_ALIQUOT_TOKEN_RE = re.compile(r"(NE|NW|SE|SW|N|S|E|W)(?:\s*(?:1\s*)?/\s*[24]|[24])?", re.I)


def _parse_aliquot(text: str) -> List[str]:
    """Turn 'NE/4 of the NW/4', 'E2SW4', 'N 1/2' into ['NE','NW'], ['E','SW'], ['N']."""
    t = text.upper().replace("½", "2").replace("¼", "4")
    # Spelled-out forms: 'South half', 'Northeast quarter', 'NE one-quarter'
    for word, abbr in (("NORTHEAST", "NE"), ("NORTHWEST", "NW"), ("SOUTHEAST", "SE"),
                       ("SOUTHWEST", "SW"), ("NORTH", "N"), ("SOUTH", "S"),
                       ("EAST", "E"), ("WEST", "W")):
        t = re.sub(rf"\b{word}(?:ERLY)?\b", abbr, t)
    t = re.sub(r"\b(?:ONE[\s-]*)?HALF\b", "2", t)
    t = re.sub(r"\b(?:ONE[\s-]*)?QUARTER\b", "4", t)
    t = re.sub(r"\b(OF|THE|AND|ALL)\b", " ", t)
    t = re.sub(r"[,;.]", " ", t)
    tokens: List[str] = []
    pos = 0
    t = re.sub(r"\s+", "", t)
    while pos < len(t):
        m = _ALIQUOT_TOKEN_RE.match(t, pos)
        if not m:
            raise PLSSError(f"can't read aliquot part '{text.strip()}'")
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


def parse_legal(text: str) -> Legal:
    raw = text.strip()
    if not raw:
        raise PLSSError("empty legal description")
    work = raw

    meridian = None
    pm = _PM_RE.search(work)
    if pm:
        meridian = f"{int(pm.group(1)):02d}"
        work = work[: pm.start()] + " " + work[pm.end():]

    compact = _COMPACT_RE.search(work)
    if compact:
        sec = int(compact.group(1))
        twp, tdir = int(compact.group(2)), (compact.group(3) or "").upper() or None
        rng, rdir = int(compact.group(4)), (compact.group(5) or "").upper() or None
        aliquot_text = work[: compact.start()]
    else:
        tm, rm = _TWP_RE.search(work), _RNG_RE.search(work)
        if not tm or not rm:
            raise PLSSError(f"couldn't find township and range in '{raw}'")
        # Remove T/R first so 'S' in '5S' isn't read as a section marker.
        spans = sorted([tm.span(), rm.span()], reverse=True)
        rest = work
        for a, b in spans:
            rest = rest[:a] + " " + rest[b:]
        sm = _SEC_RE.search(rest) or _SEC_SHORT_RE.search(rest.strip(" ,"))
        if sm and sm.re is _SEC_SHORT_RE:
            rest = rest.strip(" ,")
        if not sm:
            raise PLSSError(f"couldn't find section number in '{raw}'")
        sec = int(sm.group(1))
        twp, tdir = int(tm.group(1)), tm.group(2).upper()
        rng, rdir = int(rm.group(1)), rm.group(2).upper()
        aliquot_text = rest[: sm.start()]

    if not 1 <= sec <= 36:
        raise PLSSError(f"section {sec} is out of range (1–36)")

    aliquot = _parse_aliquot(aliquot_text) if aliquot_text.strip(" ,-") else []
    return Legal(twp, tdir, rng, rdir, sec, aliquot, meridian, raw)


# ---------------- Aliquot geometry ----------------
def aliquot_box(tokens: List[str]) -> Tuple[float, float, float, float]:
    """Fractional (x0, y0, x1, y1) of a section, origin at SW corner.

    Legals read innermost first ('NE4NW4' = NE quarter OF the NW quarter), so
    tokens are applied from last to first.
    """
    x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
    for tok in reversed(tokens):
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        if "N" in tok:
            y0 = my
        if "S" in tok:
            y1 = my
        if "E" in tok:
            x0 = mx
        if "W" in tok:
            x1 = mx
    return x0, y0, x1, y1


def _box_contains(outer, inner, eps=1e-9) -> bool:
    return (inner[0] >= outer[0] - eps and inner[1] >= outer[1] - eps
            and inner[2] <= outer[2] + eps and inner[3] <= outer[3] + eps)


def _is_qq_aligned(b) -> bool:
    return all(abs(v * 4 - round(v * 4)) < 1e-9 for v in b)


# ---------------- BLM service ----------------
def _query(layer: int, where: str, out_fields: str = "*", geometry: bool = True) -> dict:
    params = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if geometry else "false",
        "outSR": "4326",
        "f": "geojson" if geometry else "json",
    }
    try:
        r = requests.get(f"{BLM_PLSS_URL}/{layer}/query", params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise PLSSError(f"BLM PLSS service request failed: {e}") from e
    if "error" in data:
        raise PLSSError(f"BLM PLSS service error: {data['error'].get('message', data['error'])}")
    return data


def meridians_for_state(state: str) -> List[Tuple[str, str]]:
    """[(PRINMERCD, PRINMER name), ...] for a state."""
    params = {
        "where": f"STATEABBR='{state}'",
        "outFields": "PRINMERCD,PRINMER",
        "returnDistinctValues": "true",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        r = requests.get(f"{BLM_PLSS_URL}/{LAYER_TOWNSHIP}/query", params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise PLSSError(f"BLM PLSS service request failed: {e}") from e
    # Codes are sometimes unpadded ('5'), and some rows carry the code as the name.
    names = {}
    for f in data.get("features", []):
        code = str(f["attributes"]["PRINMERCD"] or "").strip().zfill(2)
        name = str(f["attributes"]["PRINMER"] or "").strip()
        if not code.strip("0"):
            continue
        if code not in names or names[code].zfill(2) == code:
            names[code] = name or code
    return sorted(names.items())


def plss_id(state: str, meridian: str, legal: Legal) -> str:
    """BLM township id, e.g. NE060050N0030W0."""
    return (f"{state}{meridian}{legal.township:03d}0{legal.township_dir}"
            f"{legal.range_:03d}0{legal.range_dir}0")


def resolve_directions(legal: Legal, state: str, meridian: str) -> Legal:
    """Fill in township/range directions the legal left out (e.g. '8-129-58')
    by checking which combinations exist in the state."""
    if legal.township_dir and legal.range_dir:
        return legal
    combos = [
        (t, r)
        for t in ([legal.township_dir] if legal.township_dir else ["N", "S"])
        for r in ([legal.range_dir] if legal.range_dir else ["E", "W"])
    ]
    ids = {plss_id(state, meridian, replace(legal, township_dir=t, range_dir=r)): (t, r)
           for t, r in combos}
    where = "PLSSID IN (" + ",".join(f"'{i}'" for i in ids) + ")"
    found = {f["attributes"]["PLSSID"] for f in
             _query(LAYER_TOWNSHIP, where, "PLSSID", geometry=False).get("features", [])}
    matches = [ids[i] for i in ids if i in found]
    if not matches:
        raise PLSSError(
            f"township {legal.township} range {legal.range_} not found in {state} (meridian {meridian})"
        )
    if len(matches) > 1:
        options = ", ".join(f"T{legal.township}{t} R{legal.range_}{r}" for t, r in matches)
        raise PLSSError(f"ambiguous township/range in {state} — add directions, e.g. one of: {options}")
    t, r = matches[0]
    return replace(legal, township_dir=t, range_dir=r)


def lookup(legal: Legal, state: str, meridian: str) -> dict:
    """Return a GeoJSON Feature for the legal description.

    properties include 'source' = 'BLM quarter-quarters' | 'BLM section' |
    'approximate split of section'.
    """
    meridian = legal.meridian or meridian
    legal = resolve_directions(legal, state, meridian)
    pid = plss_id(state, meridian, legal)
    sec_id = f"{pid}SN{legal.section:02d}0"

    sec = _query(LAYER_SECTION, f"FRSTDIVID='{sec_id}'")
    if not sec.get("features"):
        twp = _query(LAYER_TOWNSHIP, f"PLSSID='{pid}'", "PLSSID", geometry=False)
        if not twp.get("features"):
            raise PLSSError(
                f"township T{legal.township}{legal.township_dir} R{legal.range_}{legal.range_dir} "
                f"not found in {state} (meridian {meridian})"
            )
        raise PLSSError(f"section {legal.section} not found in township {pid}")

    section_geom = unary_union([shape(f["geometry"]) for f in sec["features"]])
    target = aliquot_box(legal.aliquot)
    geom, source = section_geom, "BLM section"

    if legal.aliquot:
        minx, miny, maxx, maxy = section_geom.bounds
        w, h = maxx - minx, maxy - miny
        approx_clip = box(minx + target[0] * w, miny + target[1] * h,
                          minx + target[2] * w, miny + target[3] * h)
        geom = None
        if _is_qq_aligned(target):
            # The intersected layer mixes labeled quarter-quarters (QQSEC / SECDIVLAB),
            # fragments of them, and unlabeled survey parcels. Take the hull of the
            # matching labeled pieces (aliquots are always rectangular in PLSS
            # space), plus the parts of unlabeled pieces inside the target box.
            pieces = _query(LAYER_INTERSECTED, f"FRSTDIVID='{sec_id}'", "QQSEC,SECDIVLAB,GOVLOT")
            labeled, unlabeled = [], []
            for f in pieces.get("features", []):
                p = f["properties"]
                label = (p.get("QQSEC") or p.get("SECDIVLAB") or "").upper()
                g = shape(f["geometry"])
                if len(label) == 4 and label[:2] in QUARTERS and label[2:] in QUARTERS:
                    if _box_contains(target, aliquot_box([label[:2], label[2:]])):
                        labeled.append(g)
                elif not p.get("GOVLOT"):
                    unlabeled.append(g)
            if labeled:
                hull = unary_union(labeled).convex_hull
                extra = [g.intersection(approx_clip) for g in unlabeled if g.intersects(approx_clip)]
                geom = unary_union([hull] + labeled + extra).intersection(section_geom)
                source = "BLM quarter-quarters"
        if geom is None:
            geom, source = section_geom.intersection(approx_clip), "approximate split of section"

    return {
        "type": "Feature",
        "properties": {
            "legal": legal.label,
            "input": legal.raw,
            "state": state,
            "meridian": meridian,
            "plss_id": pid,
            "section_id": sec_id,
            "source": source,
        },
        "geometry": mapping(geom),
    }
