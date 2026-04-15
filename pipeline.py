import sys
import time
import json
import logging
import argparse
from pathlib import Path

from exif_reader import read_folder_exif, validate_gps_coverage
from odm_local import run_odm_local, prepare_odm_input, copy_final_orthophoto


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("pipeline")

DEFAULT_OUTPUT = "./output"
MIN_IMAGES = 5


# ─────────────────────────────────────────────
# PROCESS FOLDER
# ─────────────────────────────────────────────
def process_folder(folder: Path, output_root: Path) -> dict:

    t0 = time.time()
    log.info("🚀 START PIPELINE")
    log.info(f"━━━ PROCESSING: {folder.name} ━━━")

    result = {
        "folder": str(folder),
        "status": "pending",
        "images_count": 0,
        "has_gps": False,
        "orthophoto": None,
        "error": None,
    }

    # ── EXIF
    images, exif_data = read_folder_exif(folder)
    result["images_count"] = len(images)

    log.info(f"📷 immagini trovate: {len(images)}")

    if len(images) < MIN_IMAGES:
        log.warning("⚠ troppe poche immagini")
        result["status"] = "skipped"
        return result

    gps_ok, stats = validate_gps_coverage(exif_data)
    result["has_gps"] = gps_ok

    log.info(f"📍 GPS: {gps_ok} | {stats}")

    if not gps_ok:
        log.warning("⚠ GPS incompleto (ODM continua comunque)")

    log.info(f"⏱ GPS finito in {time.time() - t0:.2f} sec")
    t1 = time.time()

    # ── ODM INPUT
    odm_input = prepare_odm_input(folder)

    # ── OUTPUT DIR
    project_out = output_root / folder.name
    project_out.mkdir(parents=True, exist_ok=True)

    # ── RUN ODM
    orthophoto_raw = run_odm_local(
        input_folder=odm_input,
        output_root=project_out,
    )

    log.info(f"🧾 raw orthophoto: {orthophoto_raw}")
    log.info(f"⏱ ODM finito in {time.time() - t1:.2f} sec")

    # ── COPY FINAL
    final_path = copy_final_orthophoto(
        orthophoto_path=orthophoto_raw,
        output_root=output_root,
        project_name=folder.name
    )

    result["orthophoto"] = str(final_path)
    result["status"] = "done"

    log.info("🏁 FOLDER DONE")
    log.info(f"🏁 TOTAL TIME: {time.time() - t0:.2f} sec")
    return result


# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────
def run_pipeline(input_folder: str, output_root: str):

    input_folder = Path(input_folder)
    output_root = Path(output_root)

    output_root.mkdir(parents=True, exist_ok=True)

    log.info(f"📂 INPUT: {input_folder}")
    log.info(f"📂 OUTPUT: {output_root}")

    image_exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

    images = [
        f for f in input_folder.iterdir()
        if f.is_file() and f.suffix.lower() in image_exts
    ]
    log.info(f"📷 immagini trovate: {len(images)}")

    if len(images) < 5:
        log.error("❌ meno di 5 immagini")
        sys.exit(1)

    result = process_folder(input_folder, output_root)

    out_json = output_root / input_folder / "results.json"
    with open(out_json, "w") as f:
        json.dump(result, f, indent=2)

    log.info(f"💾 results salvati in: {out_json}")
    log.info("🎉 PIPELINE COMPLETATA")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)

    args = parser.parse_args()

    run_pipeline(args.input, args.output)