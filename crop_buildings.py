"""
crop_buildings.py
=================
Ritaglia ogni fabbricato dall'ortofoto GeoTIFF e produce:
  - Un JPEG per fabbricato con EXIF GPS iniettato
  - Il JPEG mantiene: lat/lon centroide, altitudine media, timestamp

Dipendenze: rasterio, Pillow, piexif
"""

import json
import logging
import struct
import time
from pathlib import Path
from typing import Optional

import numpy as np
import piexif
import rasterio
import rasterio.mask
from PIL import Image
from rasterio.crs import CRS
from shapely.geometry import shape

log = logging.getLogger("crop_buildings")

JPEG_QUALITY = 92
PADDING_PX = 20          # padding attorno al bounding box del fabbricato


# ─────────────────────────────────────────────────────────────
# EXIF GPS UTILITIES
# ─────────────────────────────────────────────────────────────
def _decimal_to_dms_rational(decimal: float) -> tuple:
    """Converte decimale in gradi/minuti/secondi come tuple di rational."""
    decimal = abs(decimal)
    degrees = int(decimal)
    minutes_float = (decimal - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60

    # Rational: (numerator, denominator)
    return (
        (degrees, 1),
        (minutes, 1),
        (int(seconds * 10000), 10000),
    )


def _build_exif_gps(lat: float, lon: float, alt: Optional[float] = None) -> dict:
    """Costruisce il dict piexif.GPSIFD con lat/lon/alt."""
    gps_ifd = {
        piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef:  b"N" if lat >= 0 else b"S",
        piexif.GPSIFD.GPSLatitude:     _decimal_to_dms_rational(lat),
        piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
        piexif.GPSIFD.GPSLongitude:    _decimal_to_dms_rational(lon),
        piexif.GPSIFD.GPSMapDatum:     b"WGS-84",
    }
    if alt is not None:
        gps_ifd[piexif.GPSIFD.GPSAltitudeRef] = 0  # above sea level
        gps_ifd[piexif.GPSIFD.GPSAltitude] = (int(alt * 100), 100)

    return gps_ifd


def _inject_exif(jpeg_path: Path, lat: float, lon: float, alt: Optional[float], timestamp: Optional[str]):
    """Legge il JPEG esistente, inietta EXIF GPS e sovrascrive."""
    try:
        try:
            exif_dict = piexif.load(str(jpeg_path))
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

        exif_dict["GPS"] = _build_exif_gps(lat, lon, alt)

        # Zeroth IFD - metadati di base
        exif_dict["0th"][piexif.ImageIFD.Software] = b"roof_pipeline"

        if timestamp:
            # timestamp formato EXIF: "YYYY:MM:DD HH:MM:SS"
            ts_exif = timestamp.replace("-", ":").replace("T", " ")[:19].encode()
            exif_dict["0th"][piexif.ImageIFD.DateTime] = ts_exif
            exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = ts_exif

        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(jpeg_path))
        log.debug(f"  EXIF iniettato: lat={lat:.6f} lon={lon:.6f} alt={alt}")

    except Exception as e:
        log.warning(f"  ⚠️ Errore iniezione EXIF in {jpeg_path.name}: {e}")


# ─────────────────────────────────────────────────────────────
# CROP SINGOLO FABBRICATO
# ─────────────────────────────────────────────────────────────
def _crop_single_building(
    src: rasterio.DatasetReader,
    feature: dict,
    output_dir: Path,
    padding_px: int = PADDING_PX,
) -> Optional[Path]:
    """
    Ritaglia il fabbricato dall'ortofoto usando il poligono GeoJSON.
    Salva JPEG con EXIF GPS iniettato.
    """
    props = feature["properties"]
    building_id = props["building_id"]
    geom = feature["geometry"]
    centroid_lat = props["centroid_lat"]
    centroid_lon = props["centroid_lon"]
    mean_alt = props.get("mean_alt")

    # Usa rasterio.mask per ritagliare col poligono esatto
    try:
        from shapely.geometry import shape as shp_shape, mapping
        geom_shape = shp_shape(geom)

        # Aggiunge padding espandendo il bbox
        px_bbox = props.get("px_bbox", {})
        if px_bbox:
            # Crop con bounding box paddato (più semplice e più veloce)
            x = max(0, px_bbox["x"] - padding_px)
            y = max(0, px_bbox["y"] - padding_px)
            w = min(src.width - x, px_bbox["w"] + padding_px * 2)
            h = min(src.height - y, px_bbox["h"] + padding_px * 2)

            window = rasterio.windows.Window(x, y, w, h)
            cropped = src.read(window=window)  # shape: (bands, h, w)
        else:
            # Fallback: mask con poligono
            cropped, _ = rasterio.mask.mask(src, [geom], crop=True, pad=True, pad_width=padding_px)

        # Converti a uint8 RGB
        if cropped.shape[0] >= 3:
            rgb = cropped[:3]  # prendi solo R,G,B
        elif cropped.shape[0] == 1:
            rgb = np.repeat(cropped, 3, axis=0)
        else:
            rgb = cropped[:3]

        # Normalizza se non uint8
        if rgb.dtype != np.uint8:
            rgb_norm = np.zeros_like(rgb, dtype=np.uint8)
            for i in range(rgb.shape[0]):
                band = rgb[i].astype(float)
                valid = band[band > 0]
                if len(valid) > 0:
                    lo, hi = np.percentile(valid, [2, 98])
                    rgb_norm[i] = np.clip((band - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
                else:
                    rgb_norm[i] = band.astype(np.uint8)
            rgb = rgb_norm

        # HWC per PIL
        img_array = rgb.transpose(1, 2, 0)
        pil_img = Image.fromarray(img_array, mode="RGB")

        # Salva JPEG
        jpeg_path = output_dir / f"{building_id}.jpg"
        pil_img.save(str(jpeg_path), "JPEG", quality=JPEG_QUALITY, optimize=True)

        # Inietta EXIF GPS
        _inject_exif(jpeg_path, centroid_lat, centroid_lon, mean_alt, timestamp=None)

        log.info(f"  ✅ {building_id}.jpg — {img_array.shape[1]}×{img_array.shape[0]}px  "
                 f"lat={centroid_lat:.5f} lon={centroid_lon:.5f}")
        return jpeg_path

    except Exception as e:
        log.error(f"  ❌ Errore crop {building_id}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# FUNZIONE PRINCIPALE
# ─────────────────────────────────────────────────────────────
def crop_buildings(
    orthophoto_path: Path,
    geojson_path: Path,
    output_dir: Path,
) -> list:
    """
    Ritaglia tutti i fabbricati dal GeoJSON e produce JPEG con EXIF.
    
    Returns: lista di dict {building_id, jpeg_path, lat, lon, area_m2}
    """
    t0 = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(geojson_path) as f:
        geojson = json.load(f)

    features = geojson.get("features", [])
    log.info(f"🏗️  Crop di {len(features)} fabbricati da {orthophoto_path.name}")

    results = []
    with rasterio.open(orthophoto_path) as src:
        for feature in features:
            jpeg_path = _crop_single_building(src, feature, output_dir)
            if jpeg_path:
                props = feature["properties"]
                results.append({
                    "building_id": props["building_id"],
                    "jpeg_path": str(jpeg_path),
                    "lat": props["centroid_lat"],
                    "lon": props["centroid_lon"],
                    "area_m2": props.get("area_m2"),
                    "bbox_gps": props.get("bbox_gps"),
                    "mean_alt": props.get("mean_alt"),
                })

    log.info(f"✅ Crop completato: {len(results)}/{len(features)} fabbricati salvati in {time.time()-t0:.1f}s")
    return results
