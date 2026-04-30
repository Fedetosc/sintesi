"""
building_detector.py
====================
Rileva fabbricati (tetti) su un ortofoto GeoTIFF.

Strategia a due livelli:
  1. Tenta rilevamento ML via ONNX (se il modello è disponibile)
  2. Fallback classico: threshold HSV/LAB + morfologia + contour filtering

Output: GeoJSON con un Feature per ogni fabbricato rilevato.
        Ogni feature ha: geometry (Polygon in EPSG:4326), properties con
        centroid_lat/lon, area_m2, bbox_gps, mean_alt, building_id.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import shapes
from rasterio.transform import xy
from rasterio.warp import transform_geom
from shapely.geometry import shape, mapping, MultiPolygon
from shapely.ops import unary_union

log = logging.getLogger("building_detector")

# ─────────────────────────────────────────────────────────────
# PARAMETRI DETECTION
# ─────────────────────────────────────────────────────────────
MIN_AREA_M2 = 20        # filtra oggetti piccoli (< 20 m²)
MAX_AREA_M2 = 5000      # filtra oggetti troppo grandi (vegetazione estesa)
MIN_SOLIDITY = 0.55     # forma compatta (solidity = area / convex_hull_area)
MIN_EXTENT = 0.35       # extent = area / bounding_rect_area
DILATE_ITER = 3         # iterazioni dilatazione morfologica (chiude buchi nel tetto)
ERODE_ITER = 2
SIMPLIFY_TOL = 0.5      # semplificazione contorno (m nel CRS proiettato)


# ─────────────────────────────────────────────────────────────
# UTILS GEOSPAZIALI
# ─────────────────────────────────────────────────────────────
def pixel_area_m2(transform: rasterio.Affine) -> float:
    """Area di un pixel in m². Funziona se il CRS è in metri o se approssimato da gradi."""
    px = abs(transform.a)
    py = abs(transform.e)
    # Se il CRS è geografico (gradi), converti approssimativamente
    # 1° lat ≈ 111320 m
    if px < 0.01:
        px_m = px * 111320
        py_m = py * 111320
    else:
        px_m = px
        py_m = py
    return px_m * py_m


def pixel_to_latlon(row: int, col: int, transform: rasterio.Affine, src_crs, dst_crs=CRS.from_epsg(4326)):
    """Converte pixel (row, col) in lat/lon WGS84."""
    x, y = xy(transform, row, col)
    if src_crs.to_epsg() == 4326:
        return y, x  # lat, lon
    from pyproj import Transformer
    t = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    lon, lat = t.transform(x, y)
    return lat, lon


# ─────────────────────────────────────────────────────────────
# DETECTION CLASSICA (FALLBACK)
# ─────────────────────────────────────────────────────────────
def _mask_buildings_classic(img_rgb: np.ndarray) -> np.ndarray:
    """
    Ritorna maschera binaria (uint8, 0/255) dei fabbricati.
    Strategia: combina edge density + color-space thresholding.
    
    I tetti tendono ad avere:
    - colori grigi/bianchi/rossi distinti dalla vegetazione
    - texture uniforme a bassa frequenza
    - alta densità di bordi ai margini, bassa all'interno
    """
    h, w = img_rgb.shape[:2]

    # --- Canale Gray
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)

    # --- Vegetazione mask (verde intenso → escludi)
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    veg_mask = cv2.inRange(hsv, (35, 40, 40), (85, 255, 255))  # verde HSV

    # --- Rilevamento bordi (Canny) per trovare strutture con contorni netti
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 30, 100)

    # Dilata bordi per creare regioni chiuse
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    dilated_edges = cv2.dilate(edges, kernel, iterations=DILATE_ITER)

    # Fill regioni chiuse
    filled = dilated_edges.copy()
    flood_mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(filled, flood_mask, (0, 0), 255)
    filled_inv = cv2.bitwise_not(filled)
    combined = cv2.bitwise_or(dilated_edges, filled_inv)

    # --- Rimuovi vegetazione
    combined[veg_mask > 0] = 0

    # --- Morph cleanup
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    closed = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel_close, iterations=2)
    kernel_erode = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    eroded = cv2.erode(closed, kernel_erode, iterations=ERODE_ITER)

    return eroded


def _filter_contours(contours, transform: rasterio.Affine, min_area_px: int, max_area_px: int):
    """Filtra contorni per area, solidity, extent."""
    valid = []
    for cnt in contours:
        area_px = cv2.contourArea(cnt)
        if area_px < min_area_px or area_px > max_area_px:
            continue

        hull = cv2.convexHull(cnt)
        hull_area = cv2.contourArea(hull)
        solidity = area_px / hull_area if hull_area > 0 else 0
        if solidity < MIN_SOLIDITY:
            continue

        x, y, bw, bh = cv2.boundingRect(cnt)
        extent = area_px / (bw * bh) if (bw * bh) > 0 else 0
        if extent < MIN_EXTENT:
            continue

        valid.append(cnt)
    return valid


# ─────────────────────────────────────────────────────────────
# DETECTION ML (opzionale — richiede ONNX model)
# ─────────────────────────────────────────────────────────────
def _mask_buildings_onnx(img_rgb: np.ndarray, model_path: Path) -> Optional[np.ndarray]:
    """
    Se disponibile, usa un modello ONNX per segmentazione semantica.
    Modello atteso: input [1,3,H,W] float32 normalizzato, output [1,1,H,W] sigmoid.
    
    Modelli compatibili:
    - microsoft/birdseye-segment-roof (HuggingFace)
    - qualsiasi modello U-Net/DeepLab trainato su aerial imagery
    """
    try:
        import onnxruntime as ort

        h, w = img_rgb.shape[:2]
        target_size = 512

        # Resize + normalize
        resized = cv2.resize(img_rgb, (target_size, target_size)).astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406])
        std  = np.array([0.229, 0.224, 0.225])
        normalized = (resized - mean) / std
        inp = normalized.transpose(2, 0, 1)[np.newaxis].astype(np.float32)

        sess = ort.InferenceSession(str(model_path))
        out = sess.run(None, {sess.get_inputs()[0].name: inp})[0]  # [1,1,H,W]

        prob = out[0, 0]  # [H,W]
        mask_small = (prob > 0.5).astype(np.uint8) * 255
        mask = cv2.resize(mask_small, (w, h), interpolation=cv2.INTER_NEAREST)
        log.info("✅ Usato modello ONNX per detection")
        return mask

    except ImportError:
        log.warning("onnxruntime non installato, uso detection classica")
        return None
    except Exception as e:
        log.warning(f"ONNX inference fallita: {e}, uso detection classica")
        return None


# ─────────────────────────────────────────────────────────────
# CONTOURS → GEOJSON FEATURES
# ─────────────────────────────────────────────────────────────
def _contour_to_latlon_polygon(contour: np.ndarray, transform: rasterio.Affine, src_crs) -> Optional[list]:
    """Converte un contorno OpenCV (pixel) in lista di [lon, lat] per GeoJSON."""
    dst_crs = CRS.from_epsg(4326)
    pts = contour.squeeze()
    if pts.ndim != 2 or len(pts) < 4:
        return None

    coords = []
    for col, row in pts:
        x, y = xy(transform, row, col)
        if src_crs.to_epsg() != 4326:
            from pyproj import Transformer
            t = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
            lon, lat = t.transform(x, y)
        else:
            lon, lat = x, y
        coords.append([round(lon, 7), round(lat, 7)])

    # Chiudi il poligono
    if coords[0] != coords[-1]:
        coords.append(coords[0])

    return coords


def _compute_centroid(coords: list) -> tuple:
    lons = [c[0] for c in coords[:-1]]
    lats = [c[1] for c in coords[:-1]]
    return round(sum(lats) / len(lats), 7), round(sum(lons) / len(lons), 7)


def _compute_bbox_gps(coords: list) -> dict:
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return {
        "min_lat": round(min(lats), 7),
        "max_lat": round(max(lats), 7),
        "min_lon": round(min(lons), 7),
        "max_lon": round(max(lons), 7),
    }


# ─────────────────────────────────────────────────────────────
# FUNZIONE PRINCIPALE
# ─────────────────────────────────────────────────────────────
def detect_buildings(
    orthophoto_path: Path,
    output_geojson: Path,
    onnx_model_path: Optional[Path] = None,
    mean_alt: Optional[float] = None,
) -> list:
    """
    Rileva fabbricati sull'ortofoto e salva GeoJSON.
    
    Returns: lista di feature dict (GeoJSON Features)
    """
    t0 = time.time()
    log.info(f"🔍 Building detection su: {orthophoto_path}")

    with rasterio.open(orthophoto_path) as src:
        transform = src.transform
        src_crs = src.crs
        profile = src.profile

        # Leggi le prime 3 bande come RGB
        n_bands = src.count
        if n_bands >= 3:
            r = src.read(1)
            g = src.read(2)
            b = src.read(3)
            img_rgb = np.stack([r, g, b], axis=-1)
        elif n_bands == 1:
            # Greyscale → replica su 3 canali
            gray = src.read(1)
            img_rgb = np.stack([gray, gray, gray], axis=-1)
        else:
            raise ValueError(f"Numero bande non supportato: {n_bands}")

        # Normalizza a uint8 se necessario
        if img_rgb.dtype != np.uint8:
            for i in range(3):
                band = img_rgb[:, :, i].astype(float)
                pmin, pmax = np.percentile(band[band > 0], [2, 98]) if np.any(band > 0) else (0, 255)
                img_rgb[:, :, i] = np.clip((band - pmin) / max(pmax - pmin, 1) * 255, 0, 255).astype(np.uint8)

        log.info(f"📐 Dimensioni ortofoto: {img_rgb.shape[1]}×{img_rgb.shape[0]} px")
        log.info(f"🗺️  CRS: {src_crs.to_string()}")

        px_area = pixel_area_m2(transform)
        min_px = int(MIN_AREA_M2 / px_area)
        max_px = int(MAX_AREA_M2 / px_area)
        log.info(f"📏 Area pixel: {px_area:.4f} m² → min {min_px}px max {max_px}px")

    # --- Detection mask
    mask = None
    if onnx_model_path and onnx_model_path.exists():
        mask = _mask_buildings_onnx(img_rgb, onnx_model_path)

    if mask is None:
        log.info("🔬 Uso detection classica (CV)")
        mask = _mask_buildings_classic(img_rgb)

    # --- Trova contorni
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    log.info(f"📦 Contorni trovati: {len(contours)}")

    valid_contours = _filter_contours(contours, transform, min_px, max_px)
    log.info(f"✅ Fabbricati validi dopo filtro: {len(valid_contours)}")

    # --- Costruisci GeoJSON
    features = []
    for idx, cnt in enumerate(valid_contours):
        coords = _contour_to_latlon_polygon(cnt, transform, src_crs)
        if not coords:
            continue

        centroid_lat, centroid_lon = _compute_centroid(coords)
        bbox = _compute_bbox_gps(coords)
        area_px = cv2.contourArea(cnt)
        area_m2 = round(area_px * px_area, 1)

        # Bounding box in pixel per il crop successivo
        px_x, px_y, px_w, px_h = cv2.boundingRect(cnt)

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords]
            },
            "properties": {
                "building_id": f"building_{idx+1:03d}",
                "centroid_lat": centroid_lat,
                "centroid_lon": centroid_lon,
                "area_m2": area_m2,
                "bbox_gps": bbox,
                "mean_alt": mean_alt,
                # pixel bbox per crop (coordinate immagine originale)
                "px_bbox": {
                    "x": int(px_x),
                    "y": int(px_y),
                    "w": int(px_w),
                    "h": int(px_h),
                }
            }
        }
        features.append(feature)

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    with open(output_geojson, "w") as f:
        json.dump(geojson, f, indent=2)

    log.info(f"💾 GeoJSON salvato: {output_geojson} ({len(features)} fabbricati)")
    log.info(f"⏱️  Detection completata in {time.time()-t0:.1f}s")

    return features
