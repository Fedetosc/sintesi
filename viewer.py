"""
viewer.py
=========
Server Flask che serve:
  - Mappa Leaflet con ortofoto come tile layer (via rasterio + rio-cogeo)
  - Overlay GeoJSON dei fabbricati (poligoni cliccabili)
  - Panel laterale con JPEG del tetto + metadati al click

Avvio: python viewer.py --output ./output/my_project
"""

import argparse
import base64
import json
import logging
from pathlib import Path

from flask import Flask, jsonify, render_template_string, send_file, abort

log = logging.getLogger("viewer")

app = Flask(__name__)

# Stato globale del viewer (impostato da main())
STATE = {
    "output_dir": None,
    "geojson_path": None,
    "orthophoto_path": None,
    "buildings_dir": None,
}


# ─────────────────────────────────────────────────────────────
# TEMPLATE HTML (Leaflet + sidebar)
# ─────────────────────────────────────────────────────────────
MAP_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Roof Analysis Viewer</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
         display: flex; height: 100vh; background: #0f1117; color: #e2e8f0; }
  
  #map { flex: 1; }
  
  #sidebar {
    width: 380px; min-width: 320px;
    background: #1a1d27;
    border-left: 1px solid #2d3148;
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  
  #sidebar-header {
    padding: 16px 20px;
    background: #13161f;
    border-bottom: 1px solid #2d3148;
  }
  #sidebar-header h1 { font-size: 15px; font-weight: 600; color: #f1f5f9; }
  #sidebar-header p  { font-size: 12px; color: #64748b; margin-top: 4px; }
  
  #building-panel { flex: 1; overflow-y: auto; padding: 16px; }
  
  .empty-state {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; height: 100%; color: #4a5568; text-align: center;
    gap: 12px;
  }
  .empty-state .icon { font-size: 48px; opacity: 0.4; }
  .empty-state p { font-size: 13px; line-height: 1.5; }
  
  #building-card { display: none; }
  
  .roof-img-wrap {
    border-radius: 10px; overflow: hidden;
    border: 1px solid #2d3148;
    background: #13161f;
    aspect-ratio: 4/3;
    display: flex; align-items: center; justify-content: center;
  }
  .roof-img-wrap img { width: 100%; height: 100%; object-fit: cover; }
  
  .meta-grid {
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 8px; margin-top: 12px;
  }
  .meta-item {
    background: #13161f;
    border: 1px solid #2d3148;
    border-radius: 8px;
    padding: 10px 12px;
  }
  .meta-item .label { font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }
  .meta-item .value { font-size: 14px; font-weight: 600; color: #e2e8f0; margin-top: 4px; font-variant-numeric: tabular-nums; }
  
  .building-id {
    font-size: 13px; font-weight: 700; color: #60a5fa;
    text-transform: uppercase; letter-spacing: 0.08em;
    margin-bottom: 10px;
  }
  
  .coords-box {
    background: #0f1117;
    border: 1px solid #2d3148;
    border-radius: 8px;
    padding: 10px 12px;
    margin-top: 8px;
    font-size: 12px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    color: #94a3b8;
    line-height: 1.6;
  }
  
  .download-btn {
    display: block; width: 100%;
    margin-top: 12px;
    padding: 10px;
    background: #2563eb;
    color: #fff;
    text-align: center;
    border-radius: 8px;
    text-decoration: none;
    font-size: 13px;
    font-weight: 600;
    transition: background 0.15s;
  }
  .download-btn:hover { background: #1d4ed8; }
  
  /* Stats bar */
  #stats-bar {
    padding: 10px 20px;
    background: #13161f;
    border-top: 1px solid #2d3148;
    font-size: 11px;
    color: #4a5568;
    display: flex;
    justify-content: space-between;
  }
  #stats-bar span { color: #94a3b8; font-weight: 500; }
  
  /* Leaflet overrides */
  .leaflet-container { background: #0f1117; }
  
  .building-popup {
    font-size: 12px;
    line-height: 1.5;
  }
</style>
</head>
<body>

<div id="map"></div>

<div id="sidebar">
  <div id="sidebar-header">
    <h1>🏠 Roof Analysis Viewer</h1>
    <p>Clicca su un fabbricato per analizzare il tetto</p>
  </div>
  
  <div id="building-panel">
    <div class="empty-state" id="empty-state">
      <div class="icon">🗺️</div>
      <p>Seleziona un fabbricato<br>dalla mappa per vedere<br>l'analisi del tetto</p>
    </div>
    
    <div id="building-card">
      <div class="building-id" id="b-id"></div>
      <div class="roof-img-wrap">
        <img id="b-img" src="" alt="Tetto"/>
      </div>
      <div class="meta-grid">
        <div class="meta-item">
          <div class="label">Area</div>
          <div class="value" id="b-area">—</div>
        </div>
        <div class="meta-item">
          <div class="label">Altitudine</div>
          <div class="value" id="b-alt">—</div>
        </div>
      </div>
      <div class="coords-box">
        <div>Lat: <span id="b-lat" style="color:#60a5fa"></span></div>
        <div>Lon: <span id="b-lon" style="color:#60a5fa"></span></div>
        <div id="b-bbox-row" style="display:none">
          BBox: <span id="b-bbox" style="color:#94a3b8"></span>
        </div>
      </div>
      <a class="download-btn" id="b-download" href="#" download>⬇ Scarica JPEG tetto</a>
    </div>
  </div>
  
  <div id="stats-bar">
    <span>Fabbricati: <span id="stats-count">0</span></span>
    <span>Sorgente: ODM ortofoto</span>
  </div>
</div>

<script>
// ──────────────────────────────────────────
// Mappa Leaflet
// ──────────────────────────────────────────
const map = L.map('map', { zoomControl: true });

// Base layer OpenStreetMap (usato sotto l'ortofoto semitrasparente)
const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap contributors',
  maxZoom: 22,
});

// Base layer satellite (Esri)
const satellite = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { attribution: 'Tiles © Esri', maxZoom: 22 }
);

satellite.addTo(map);

// Layer control
const baseLayers = { "Satellite": satellite, "OpenStreetMap": osm };

// ──────────────────────────────────────────
// Carica GeoJSON dei fabbricati
// ──────────────────────────────────────────
let buildingLayer = null;
let selectedLayer = null;

function styleDefault(feature) {
  return {
    color: '#3b82f6',
    weight: 2,
    opacity: 0.9,
    fillColor: '#60a5fa',
    fillOpacity: 0.25,
  };
}

function styleSelected(feature) {
  return {
    color: '#f59e0b',
    weight: 3,
    opacity: 1,
    fillColor: '#fbbf24',
    fillOpacity: 0.4,
  };
}

function showBuilding(feature, layer) {
  const props = feature.properties;
  
  // Reset precedente selezione
  if (selectedLayer) selectedLayer.setStyle(styleDefault(selectedLayer.feature));
  selectedLayer = layer;
  layer.setStyle(styleSelected(feature));

  // Sidebar
  document.getElementById('empty-state').style.display = 'none';
  const card = document.getElementById('building-card');
  card.style.display = 'block';
  
  document.getElementById('b-id').textContent = props.building_id;
  document.getElementById('b-area').textContent = props.area_m2 ? props.area_m2 + ' m²' : '—';
  document.getElementById('b-alt').textContent = props.mean_alt ? Math.round(props.mean_alt) + ' m' : '—';
  document.getElementById('b-lat').textContent = props.centroid_lat.toFixed(6);
  document.getElementById('b-lon').textContent = props.centroid_lon.toFixed(6);
  
  const bbox = props.bbox_gps;
  if (bbox) {
    document.getElementById('b-bbox').textContent = 
      `[${bbox.min_lat.toFixed(4)}, ${bbox.min_lon.toFixed(4)}, ${bbox.max_lat.toFixed(4)}, ${bbox.max_lon.toFixed(4)}]`;
    document.getElementById('b-bbox-row').style.display = 'block';
  }
  
  // Immagine tetto
  const imgUrl = `/building_image/${props.building_id}`;
  document.getElementById('b-img').src = imgUrl;
  document.getElementById('b-download').href = imgUrl;
  document.getElementById('b-download').download = `${props.building_id}.jpg`;
}

fetch('/geojson')
  .then(r => r.json())
  .then(data => {
    const count = data.features ? data.features.length : 0;
    document.getElementById('stats-count').textContent = count;
    
    buildingLayer = L.geoJSON(data, {
      style: styleDefault,
      onEachFeature: (feature, layer) => {
        layer.on('click', () => showBuilding(feature, layer));
        layer.bindTooltip(
          `<div class="building-popup"><b>${feature.properties.building_id}</b><br>` +
          `${feature.properties.area_m2 || '?'} m²</div>`,
          { sticky: true }
        );
      }
    }).addTo(map);
    
    if (count > 0) {
      map.fitBounds(buildingLayer.getBounds(), { padding: [40, 40] });
    } else {
      map.setView([41.9, 12.5], 14); // Roma default
    }
    
    // Layer control
    L.control.layers(baseLayers, { "Fabbricati": buildingLayer }, { position: 'topright' }).addTo(map);
  })
  .catch(e => {
    console.error('Errore caricamento GeoJSON:', e);
    map.setView([41.9, 12.5], 14);
    L.control.layers(baseLayers).addTo(map);
  });
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────
# ROUTES FLASK
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(MAP_TEMPLATE)


@app.route("/geojson")
def geojson():
    path = STATE["geojson_path"]
    if not path or not path.exists():
        return jsonify({"type": "FeatureCollection", "features": []})
    with open(path) as f:
        return jsonify(json.load(f))


@app.route("/building_image/<building_id>")
def building_image(building_id):
    buildings_dir = STATE["buildings_dir"]
    if not buildings_dir:
        abort(404)

    # Cerca .jpg o .jpeg
    for ext in ["jpg", "jpeg", "JPG", "JPEG"]:
        p = buildings_dir / f"{building_id}.{ext}"
        if p.exists():
            return send_file(str(p), mimetype="image/jpeg")

    abort(404)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "buildings_dir": str(STATE["buildings_dir"])})


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def start_viewer(output_dir: Path, port: int = 5050):
    """Avvia il viewer Flask. Cerca automaticamente i file nell'output_dir."""
    output_dir = Path(output_dir)

    # Cerca GeoJSON
    geojson_candidates = list(output_dir.rglob("buildings.geojson"))
    geojson_path = geojson_candidates[0] if geojson_candidates else None

    # Cerca ortofoto
    ortho_candidates = list(output_dir.rglob("orthophoto.tif"))
    ortho_path = ortho_candidates[0] if ortho_candidates else None

    # Cerca dir buildings (JPEG ritagliati)
    buildings_candidates = list(output_dir.rglob("buildings"))
    buildings_dir = next((d for d in buildings_candidates if d.is_dir()), None)

    STATE["output_dir"] = output_dir
    STATE["geojson_path"] = geojson_path
    STATE["orthophoto_path"] = ortho_path
    STATE["buildings_dir"] = buildings_dir

    log.info(f"🗺️  Viewer avviato")
    log.info(f"   GeoJSON:    {geojson_path}")
    log.info(f"   Ortofoto:   {ortho_path}")
    log.info(f"   Buildings:  {buildings_dir}")
    log.info(f"   URL:        http://localhost:{port}")

    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", help="Cartella output del pipeline (contiene orthophoto.tif, buildings.geojson, buildings/)")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()

    start_viewer(Path(args.output_dir), args.port)
