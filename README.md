# Roof Analysis Pipeline

Pipeline completo per analisi di tetti da immagini drone: da JPEG grezzi a mappa interattiva con ogni fabbricato ritagliato e georeferenziato.

---

## Struttura progetto

```
roof_pipeline/
├── pipeline.py          # Entrypoint principale
├── exif_reader.py       # Lettura EXIF/GPS dalle immagini originali
├── odm_local.py         # Wrapper Docker per OpenDroneMap
├── building_detector.py # Detection fabbricati sull'ortofoto
├── crop_buildings.py    # Crop + iniezione EXIF GPS per fabbricato
├── viewer.py            # Server Flask + mappa Leaflet
└── requirements.txt
```

---

## Setup

### 1. Dipendenze Python

```bash
pip install -r requirements.txt
```

### 2. Docker + OpenDroneMap

```bash
# Verifica Docker
docker --version

# Scarica immagine ODM (una tantum, ~4GB)
docker pull opendronemap/odm
```

---

## Utilizzo

### Pipeline completa (raccomandato)

```bash
python pipeline.py ./immagini_drone --output ./output --viewer
```

Questo comando:
1. Legge EXIF GPS da tutte le immagini
2. Esegue ODM via Docker → ortofoto GeoTIFF georeferenziata
3. Rileva i fabbricati sull'ortofoto
4. Ritaglia ogni fabbricato come JPEG con EXIF GPS iniettato
5. Avvia il viewer su http://localhost:5050

### Solo detection + viewer (se hai già l'ortofoto)

```bash
python pipeline.py ./immagini_drone --output ./output --skip-odm --viewer
```

### Con modello ONNX (detection ML)

```bash
python pipeline.py ./immagini_drone --output ./output \
  --onnx ./models/rooftop.onnx \
  --viewer
```

### Solo viewer (output già pronto)

```bash
python viewer.py ./output/my_project --port 5050
```

---

## Output

Dopo l'esecuzione trovi in `./output/<nome_cartella>/`:

```
output/
└── my_project/
    ├── orthophoto.tif          # GeoTIFF georeferenziato (ODM)
    ├── buildings.geojson       # Poligoni fabbricati in WGS84
    ├── buildings/
    │   ├── building_001.jpg    # Tetto ritagliato con EXIF GPS
    │   ├── building_002.jpg
    │   └── ...
    └── pipeline_summary.json   # Riepilogo con timing e metadati
```

---

## Building detector: due strategie

### Strategia A — CV classica (default, zero dipendenze extra)

Usa OpenCV per:
- Rimozione vegetazione (HSV masking verde)
- Edge detection + flood fill per trovare strutture chiuse
- Filtro per area, solidity, extent

Parametri configurabili in `building_detector.py`:
```python
MIN_AREA_M2 = 20        # filtra strutture troppo piccole
MAX_AREA_M2 = 5000      # filtra vegetazione estesa
MIN_SOLIDITY = 0.55     # forma compatta (0=irregolare, 1=convesso)
```

### Strategia B — Modello ONNX (opzionale)

Installa `onnxruntime` e fornisci un modello con `--onnx`.

Modelli compatibili (cerca su HuggingFace):
- Modelli U-Net/DeepLab trainati su aerial/satellite imagery
- Input: [1, 3, 512, 512] float32 normalizzato ImageNet
- Output: [1, 1, 512, 512] sigmoid (maschera tetto)

---

## EXIF iniettati

Ogni `building_NNN.jpg` contiene:

| Campo EXIF | Valore |
|------------|--------|
| `GPS GPSLatitude` | Centroide del poligono (lat) |
| `GPS GPSLongitude` | Centroide del poligono (lon) |
| `GPS GPSAltitude` | Altitudine media volo (da EXIF originali) |
| `GPS GPSMapDatum` | WGS-84 |
| `Image Software` | roof_pipeline |

---

## Viewer

Apri http://localhost:5050 dopo aver avviato con `--viewer`.

- Base layer: Satellite (Esri) + OpenStreetMap
- Overlay: poligoni blu dei fabbricati
- Click su fabbricato: sidebar con JPEG tetto, area, coordinate, download

---

## Tempi attesi

| Fase | N immagini | Tempo |
|------|-----------|-------|
| ODM photogrammetry | 50-100 img | 10-25 min |
| Building detection | — | 1-3 min |
| Crop + EXIF | 20 fabbricati | < 30 sec |

Con `--feature-quality high --pc-quality high` ODM può richiedere più tempo su hardware lento.
Per test rapidi usa `--feature-quality medium --fast-orthophoto`.

---

## Troubleshooting

**"Nessun fabbricato rilevato"**
- Controlla che l'ortofoto abbia buona risoluzione (< 10 cm/pixel)
- Abbassa `MIN_SOLIDITY` a 0.45 se i tetti hanno forme complesse
- Prova con `--fast-orthophoto` rimosso per avere un'ortofoto migliore

**Errore Docker**
- Verifica che Docker sia in esecuzione: `docker ps`
- Controlla i permessi sul volume: la cartella input deve essere leggibile

**EXIF non scritti**
- Verifica che `piexif` sia installato: `pip install piexif`
- I file TIFF non supportano EXIF via piexif — converti in JPEG prima
