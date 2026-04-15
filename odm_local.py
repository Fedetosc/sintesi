import shutil
import subprocess
import logging
from pathlib import Path

log = logging.getLogger("odm_local")


# ─────────────────────────────────────────────
# PREP INPUT
# ─────────────────────────────────────────────
def prepare_odm_input(folder: Path) -> Path:
    tmp = folder.parent / (folder.name + "_odm")
    images_dir = tmp / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for f in folder.glob("*"):
        if f.suffix.lower() in [".jpg", ".jpeg", ".png", ".tif"]:
            shutil.copy2(f, images_dir / f.name)
            count += 1

    log.info(f"📦 ODM input preparato: {images_dir} ({count} immagini)")
    return tmp


# ─────────────────────────────────────────────
# RUN ODM
# ─────────────────────────────────────────────
def run_odm_local(
    input_folder: Path,
    output_root: Path
) -> Path:

    input_folder = input_folder.resolve()
    output_root = output_root.resolve()

    log.info("🚁 Avvio OpenDroneMap (Docker)")
    log.info(f"📂 Input:  {input_folder}")
    log.info(f"📂 Output: {output_root}")

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{input_folder}:/datasets/input",
        "-v", f"{output_root}:/datasets/output",
        "opendronemap/odm",
        "--project-path", "/datasets/output",
        "/datasets/input",
        "--orthophoto-resolution", "5",
        "--feature-quality", "medium",
    ]

    log.info("🐳 CMD: " + " ".join(cmd))

    subprocess.run(cmd, check=True)

    log.info("✅ ODM completato")

    # ricerca output
    base = input_folder 

    log.info("🔍 Cerco ortofoto...")

    candidates = list(base.rglob("odm_orthophoto*.tif"))

    if not candidates:
        log.warning("⚠ nessuna ortofoto trovata in .tif, provo png")
        candidates = list(base.rglob("odm_orthophoto*.png"))

    if not candidates:
        log.error("❌ Nessuna ortofoto trovata")
        raise FileNotFoundError(f"No orthophoto in {base}")

    best = candidates[0]

    log.info(f"🎯 Ortofoto trovata: {best}")

    return best


# ─────────────────────────────────────────────
# COPY FINAL
# ─────────────────────────────────────────────
def copy_final_orthophoto(
    orthophoto_path: Path,
    output_root: Path,
    project_name: str
) -> Path:

    output_root = Path(output_root)

    dest_dir = output_root / project_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / "orthophoto.tif"

    shutil.copy2(orthophoto_path, dest)

    log.info(f"💾 Ortofoto copiata in: {dest}")

    return dest