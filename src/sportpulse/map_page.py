"""Interface cartographique de démonstration SportPulse AI."""

MAP_HTML = r"""
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SportPulse AI — Trajets sportifs</title>

  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
    crossorigin=""
  >

  <style>
    body {
      margin: 0;
      background: #f3f7fb;
      color: #17365d;
      font-family: Arial, sans-serif;
    }
    header {
      padding: 18px 24px;
      background: white;
      border-bottom: 1px solid #d9e6f2;
    }
    h1 { margin: 0 0 8px; font-size: 23px; }
    header p { margin: 0; color: #52677e; }
    .toolbar, #results {
      margin: 16px 24px;
      padding: 16px;
      background: white;
      border-radius: 10px;
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
    }
    select, button {
      padding: 10px 12px;
      border-radius: 6px;
      border: 1px solid #c5d6e5;
      font-size: 14px;
    }
    button {
      background: #087e8b;
      color: white;
      border: 0;
      cursor: pointer;
    }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    #map { height: 60vh; min-height: 380px; }
    #summary { margin-top: 10px; line-height: 1.6; }
    #summary.error { color: #b00020; }
    .hint { font-size: 13px; color: #52677e; margin: 6px 0 0; }
    @media (max-width: 600px) {
      #map { height: 55vh; min-height: 320px; }
      .toolbar, #results { margin: 10px; }
    }
  </style>
</head>

<body>
  <header>
    <h1>SportPulse AI — Trajets sportifs</h1>
    <p>
      Sélectionnez un départ et une arrivée pour calculer
      un itinéraire indicatif.
    </p>
  </header>

  <section class="toolbar">
    <label for="mode">Mode de déplacement :</label>
    <select id="mode">
      <option value="bicycle">Vélo</option>
      <option value="scooter">Trottinette (approximation vélo)</option>
      <option value="walking">Marche</option>
      <option value="running">Course à pied</option>
      <option value="car">Voiture (comparaison)</option>
    </select>
    <button id="reset" type="button">Réinitialiser</button>
  </section>

  <div id="map" role="application" aria-label="Carte interactive"></div>

  <section id="results" aria-live="polite">
    <strong>Comment utiliser la carte ?</strong>
    <p class="hint">
      Cliquez sur la carte pour le départ, puis pour l'arrivée.
      Utilisez uniquement des coordonnées de démonstration.
    </p>
    <div id="summary"></div>
  </section>

  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""
  ></script>

  <script>
    const map = L.map("map").setView([48.8566, 2.3522], 12);

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">' +
        'OpenStreetMap contributors</a>'
    }).addTo(map);

    let origin = null;
    let destination = null;
    let originMarker = null;
    let destinationMarker = null;
    let routeLayer = null;
    let currentController = null;   // pour annuler les requêtes en cours

    const summary = document.getElementById("summary");
    const modeSelect = document.getElementById("mode");

    function setSummary(text, isError = false) {
      summary.textContent = text;
      summary.classList.toggle("error", isError);
    }

    function clearRoute() {
      if (routeLayer) {
        map.removeLayer(routeLayer);
        routeLayer = null;
      }
    }

    function resetMap() {
      if (currentController) currentController.abort();

      [originMarker, destinationMarker].forEach(layer => {
        if (layer) map.removeLayer(layer);
      });
      clearRoute();

      origin = destination = null;
      originMarker = destinationMarker = null;
      setSummary("");
    }

    async function calculateRoute() {
      // Annule la requête précédente si elle est encore en cours
      if (currentController) currentController.abort();
      currentController = new AbortController();
      const { signal } = currentController;

      clearRoute();
      setSummary("Calcul de l'itinéraire…");

      const params = new URLSearchParams({
        origin_lon: origin.lng.toFixed(6),
        origin_lat: origin.lat.toFixed(6),
        destination_lon: destination.lng.toFixed(6),
        destination_lat: destination.lat.toFixed(6),
        mode: modeSelect.value
      });

      try {
        const response = await fetch("/geo/route?" + params.toString(), { signal });

        let data;
        try {
          data = await response.json();
        } catch {
          throw new Error("Réponse invalide du serveur (code " + response.status + ").");
        }

        if (!response.ok) {
          throw new Error(data.detail || "Calcul impossible.");
        }

        routeLayer = L.geoJSON(data.geometry, {
          style: { color: "#087e8b", weight: 5, opacity: 0.9 }
        }).addTo(map);

        map.fitBounds(routeLayer.getBounds(), { padding: [25, 25] });

        setSummary(
          "Mode : " + data.mode_label +
          " — Distance estimée : " + data.distance_km + " km" +
          " — Durée estimée : " + data.duration_min + " min. " +
          (data.note || "")
        );
      } catch (error) {
        if (error.name === "AbortError") return;  // annulation volontaire
        setSummary("Erreur : " + error.message, true);
      }
    }

    map.on("click", event => {
      if (origin && destination) resetMap();

      if (!origin) {
        origin = event.latlng;
        originMarker = L.marker(origin).addTo(map).bindPopup("Départ");
        originMarker.openPopup();
        setSummary("Départ sélectionné. Cliquez sur l'arrivée.");
        return;
      }

      destination = event.latlng;
      destinationMarker = L.marker(destination).addTo(map).bindPopup("Arrivée");
      destinationMarker.openPopup();

      calculateRoute();
    });

    modeSelect.addEventListener("change", () => {
      if (origin && destination) calculateRoute();
    });

    document.getElementById("reset").addEventListener("click", resetMap);
  </script>
</body>
</html>
"""