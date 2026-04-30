"""
pipeline.py
===========
Pipeline completa per analisi tetti da immagini drone.

Fasi:
  1. Validazione EXIF / GPS
  2. Photogrammetry con ODM (Docker)
  3. Building detection (CV classico o ONNX)
  4. Crop fabbricati + iniezione EXIF GPS
  5. Avvio viewer Leaflet

Uso:
  python pipeline.py ./immagini_drone --output ./output --viewer
"""

import sys
import time
import json
import logging
import argparse
from pathlib import Path

from exif_reader import read_folder_exif, validate_gps_coverage, get_folder_bbox
from odm_local import run_odm_local, prepare_odm_input, copy_final_orthophoto
from building_detector import detect_buildings
from crop_buildings import crop_buildings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")

DEFAULT_OUTPUT = "./output"
MIN_IMAGES = 5


# ─────────────────────────────────────────────────────────────
# FASE 1+2: ODM
# ─────────────────────────────────────────────────────────────
def run_photogrammetry(folder: Path, output_root: Path) -> tuple:
    """Esegue ODM e ritorna (orthophoto_path, mean_alt)."""
    log.info("━━━ FASE 1: Lettura EXIF ━━━")
    images, exif_data = read_folder_exif(folder)
    log.info(f"📷 Immagini trovate: {len(images)}")

    if len(images) < MIN_IMAGES:
        log.error(f"❌ Servono almeno {MIN_IMAGES} immagini, trovate {len(images)}")
        sys.exit(1)

    gps_ok, stats = validate_gps_coverage(exif_data)
    log.info(f"📍 GPS: {stats}")
    if not gps_ok:
        log.warning("⚠️  GPS incompleto — ODM continuerà senza georeferenziazione precisa")

    # Altitudine media da EXIF originali (usata come fallback per i crop)
    alts = [e["alt"] for e in exif_data if e.get("alt") is not None]
    mean_alt = round(sum(alts) / len(alts), 1) if alts else None
    log.info(f"📐 Altitudine media volo: {mean_alt} m")

    log.info("━━━ FASE 2: OpenDroneMap ━━━")
    odm_input = prepare_odm_input(folder)
    project_out = output_root / folder.name
    project_out.mkdir(parents=True, exist_ok=True)

    orthophoto_raw = run_odm_local(
        input_folder=odm_input,
        output_root=project_out,
    )

    final_ortho = copy_final_orthophoto(
        orthophoto_path=orthophoto_raw,
        output_root=output_root,
        project_name=folder.name,
    )

    return final_ortho, mean_alt


# ─────────────────────────────────────────────────────────────
# FASE 3: DETECTION
# ─────────────────────────────────────────────────────────────
def run_detection(
    orthophoto_path: Path,
    output_dir: Path,
    onnx_model: Path = None,
    mean_alt: float = None,
) -> Path:
    """Rileva fabbricati e salva GeoJSON."""
    log.info("━━━ FASE 3: Building Detection ━━━")
    geojson_path = output_dir / "buildings.geojson"

    features = detect_buildings(
        orthophoto_path=orthophoto_path,
        output_geojson=geojson_path,
        onnx_model_path=onnx_model,
        mean_alt=mean_alt,
    )

    if not features:
        log.warning("⚠️  Nessun fabbricato rilevato — controlla la qualità dell'ortofoto")
    else:
        log.info(f"🏗️  Fabbricati rilevati: {len(features)}")

    return geojson_path


# ─────────────────────────────────────────────────────────────
# FASE 4: CROP
# ─────────────────────────────────────────────────────────────
def run_crop(orthophoto_path: Path, geojson_path: Path, output_dir: Path) -> list:
    """Ritaglia i fabbricati e inietta EXIF."""
    log.info("━━━ FASE 4: Crop fabbricati + EXIF ━━━")
    buildings_dir = output_dir / "buildings"

    results = crop_buildings(
        orthophoto_path=orthophoto_path,
        geojson_path=geojson_path,
        output_dir=buildings_dir,
    )

    return results


# ─────────────────────────────────────────────────────────────
# PIPELINE PRINCIPALE
# ─────────────────────────────────────────────────────────────
def run_pipeline(
    input_folder: str,
    output_root: str,
    viewer: bool = False,
    viewer_port: int = 5050,
):
    t_start = time.time()
    input_folder = Path(input_folder)
    output_root = Path(output_root)
    output_dir = output_root / input_folder.name
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"🚀 PIPELINE START")
    log.info(f"   Input:  {input_folder}")
    log.info(f"   Output: {output_dir}")

    # ── FASE 1+2: ODM
    orthophoto_path, mean_alt = run_photogrammetry(input_folder, output_root)

    t_odm = time.time()
    log.info(f"⏱️  ODM: {t_odm - t_start:.0f}s")

    # ── FASE 3: Detection
    # onnx_path = Path(onnx_model) if onnx_model else None
    # geojson_path = run_detection(orthophoto_path, output_dir, onnx_path, mean_alt)

    # t_det = time.time()
    # log.info(f"⏱️  Detection: {t_det - t_odm:.0f}s")

    # ── FASE 4: Crop
    # crop_results = run_crop(orthophoto_path, geojson_path, output_dir)

    # t_crop = time.time()
    # log.info(f"⏱️  Crop: {t_crop - t_det:.0f}s")

    # ── Salva risultati JSON
    # summary = {
    #     "input_folder": str(input_folder),
    #     "orthophoto": str(orthophoto_path),
    #     "geojson": str(geojson_path),
    #     "buildings_count": len(crop_results),
    #     "buildings": crop_results,
    #     "timing": {
    #         "odm_s": round(t_odm - t_start, 1),
    #         "detection_s": round(t_det - t_odm, 1),
    #         "crop_s": round(t_crop - t_det, 1),
    #         "total_s": round(t_crop - t_start, 1),
    #     }
    # }

    # summary_path = output_dir / "pipeline_summary.json"
    # with open(summary_path, "w") as f:
    #     json.dump(summary, f, indent=2)

    # log.info("")
    # log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    # log.info(f"🎉 PIPELINE COMPLETATA in {t_crop - t_start:.0f}s")
    # log.info(f"   Fabbricati: {len(crop_results)}")
    # log.info(f"   GeoJSON:    {geojson_path}")
    # log.info(f"   JPEG tetti: {output_dir / 'buildings'}")
    # log.info(f"   Summary:    {summary_path}")
    # log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # # ── FASE 5: Viewer (opzionale)
    # if viewer:
    #     log.info(f"🗺️  Avvio viewer su http://localhost:{viewer_port}")
    #     from viewer import start_viewer
    #     start_viewer(output_dir, port=viewer_port)

    # return summary


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline analisi tetti da immagini drone",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Cartella con immagini drone")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Cartella output")
    # parser.add_argument("--viewer", action="store_true", help="Avvia viewer Leaflet dopo il pipeline")
    # parser.add_argument("--viewer-port", type=int, default=5050)

    args = parser.parse_args()

    run_pipeline(
        input_folder=args.input,
        output_root=args.output,
        # viewer=args.viewer,
        # viewer_port=args.viewer_port,
    )
