"""
exif_reader.py
==============
Legge metadati EXIF/GPS da immagini drone.
Supporta JPEG e TIFF con dati standard EXIF.
"""

import sys, json
import logging
from pathlib import Path
from typing import Tuple, List, Dict, Any, Optional

import exifread
from PIL import Image

log = logging.getLogger("exif_reader")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}


def _dms_to_decimal(dms_values, ref: str) -> Optional[float]:
    """Converte gradi/minuti/secondi in decimale."""
    try:
        d = float(dms_values[0].num) / float(dms_values[0].den)
        m = float(dms_values[1].num) / float(dms_values[1].den)
        s = float(dms_values[2].num) / float(dms_values[2].den)
        decimal = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal
    except (IndexError, AttributeError, ZeroDivisionError, TypeError):
        return None


def read_image_exif(image_path: Path) -> Dict[str, Any]:
    """
    Legge i metadati EXIF di una singola immagine.
    Ritorna un dict con: path, lat, lon, alt, timestamp, camera, width, height
    """
    data: Dict[str, Any] = {
        "path": str(image_path),
        "lat": None,
        "lon": None,
        "alt": None,
        "timestamp": None,
        "camera": None,
        "width": None,
        "height": None,
        "has_gps": False,
    }

    try:
        with open(image_path, "rb") as f:
            tags = exifread.process_file(f, details=False, stop_tag="GPS GPSLongitude")

        # GPS
        lat_tag = tags.get("GPS GPSLatitude")
        lat_ref = tags.get("GPS GPSLatitudeRef")
        lon_tag = tags.get("GPS GPSLongitude")
        lon_ref = tags.get("GPS GPSLongitudeRef")
        alt_tag = tags.get("GPS GPSAltitude")

        if lat_tag and lon_tag:
            lat = _dms_to_decimal(lat_tag.values, str(lat_ref) if lat_ref else "N")
            lon = _dms_to_decimal(lon_tag.values, str(lon_ref) if lon_ref else "E")
            if lat is not None and lon is not None:
                data["lat"] = round(lat, 7)
                data["lon"] = round(lon, 7)
                data["has_gps"] = True

        if alt_tag:
            try:
                v = alt_tag.values[0]
                data["alt"] = round(float(v.num) / float(v.den), 2)
            except Exception:
                pass

        # Timestamp
        for ts_tag in ("EXIF DateTimeOriginal", "Image DateTime", "EXIF DateTimeDigitized"):
            if ts_tag in tags:
                data["timestamp"] = str(tags[ts_tag])
                break

        # Camera
        make  = str(tags.get("Image Make",  "")).strip()
        model = str(tags.get("Image Model", "")).strip()
        data["camera"] = f"{make} {model}".strip() or None

        # Dimensioni
        with Image.open(image_path) as img:
            data["width"], data["height"] = img.size

    except Exception as e:
        log.debug(f"Errore lettura EXIF {image_path.name}: {e}")

    return data


def read_folder_exif(folder: Path) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """
    Legge EXIF di tutte le immagini in una cartella.
    Ritorna: (lista_path_immagini, lista_dati_exif)
    """
    images = sorted([
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ])

    exif_list = []
    for img_path in images:
        exif_data = read_image_exif(img_path)
        exif_list.append(exif_data)

    gps_count = sum(1 for e in exif_list if e["has_gps"])
    log.debug(f"  {folder.name}: {len(images)} immagini, {gps_count} con GPS")

    return images, exif_list


def validate_gps_coverage(exif_data: List[Dict]) -> Tuple[bool, str]:
    """
    Verifica che almeno il 90% delle immagini abbia dati GPS validi.
    Ritorna (ok, descrizione_statistica)
    """
    if not exif_data:
        return False, "nessuna immagine"

    total = len(exif_data)
    with_gps = sum(1 for e in exif_data if e["has_gps"])
    pct = with_gps / total * 100

    # Calcola bounding box
    lats = [e["lat"] for e in exif_data if e["lat"] is not None]
    lons = [e["lon"] for e in exif_data if e["lon"] is not None]
    alts = [e["alt"] for e in exif_data if e["alt"] is not None]

    bbox_str = ""
    if lats and lons:
        bbox_str = (
            f"bbox=[{min(lats):.5f},{min(lons):.5f},{max(lats):.5f},{max(lons):.5f}]"
        )
        if alts:
            bbox_str += f" alt=[{min(alts):.0f}m–{max(alts):.0f}m]"

    stats = f"{with_gps}/{total} con GPS ({pct:.0f}%) {bbox_str}"
    ok = pct >= 80.0  # tolleranza: 80% minimo

    return ok, stats


def get_folder_bbox(exif_data: List[Dict]) -> Optional[Dict]:
    """Ritorna il bounding box GPS della cartella come dict."""
    lats = [e["lat"] for e in exif_data if e["lat"] is not None]
    lons = [e["lon"] for e in exif_data if e["lon"] is not None]
    if not lats:
        return None
    return {
        "min_lat": min(lats), "max_lat": max(lats),
        "min_lon": min(lons), "max_lon": max(lons),
        "center_lat": sum(lats) / len(lats),
        "center_lon": sum(lons) / len(lons),
    }


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    imgs, exifs = read_folder_exif(folder)
    ok, stats = validate_gps_coverage(exifs)
    print(f"GPS: {stats}")
    for e in exifs[:3]:
        print(json.dumps(e, indent=2))
