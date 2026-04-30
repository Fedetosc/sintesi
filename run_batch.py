from pathlib import Path
from pipeline import run_pipeline

BASE = Path("./immagini/17novembre2025")
OUTPUT = Path("./output")

for folder in BASE.iterdir():
    if folder.is_dir() and folder.name.startswith("DJI_"):
        print(f"\n🚀 PROCESSO: {folder.name}\n")

        run_pipeline(
            input_folder=str(folder),
            output_root=str(OUTPUT),
        )