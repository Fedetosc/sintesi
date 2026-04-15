# Drone Orthophoto Pipeline

Pipeline Python per:
- estrazione EXIF/GPS da immagini drone
- validazione copertura GPS
- generazione ortofoto tramite OpenDroneMap (ODM in Docker)
- copia output finale in una cartella strutturata

---

## 🚀 Requisiti

### Software
- Python 3.9+
- Docker installato e funzionante

### Python deps
```bash
pip install -r requirements.txt
```

### RUN
```bash
python pipeline.py /path/to/images --output ./output
```