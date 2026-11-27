/* global L */
const section = document.querySelector("#shopping-map");
let mapData;
try {
  mapData = JSON.parse(section.dataset.map);
} catch {
  document.querySelector("#map-panel").textContent = section.dataset.error;
}
if (mapData) initialize(mapData);

function initialize({ origin, pins, messages, searchCenter, radius }) {
  const panel = document.querySelector("#map-panel");
  const picker = document.querySelector("#map-stores");
  const center = searchCenter || origin || pins[0]?.point || [35.61, 140.1];
  const map = L.map("map", { scrollWheelZoom: false, minZoom: 11, maxZoom: 18 }).setView(center, 14);
  const tiles = L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html">国土地理院</a>',
  }).addTo(map);
  const shops = [];
  let selected = null;

  function element(tag, text, parent, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    parent.append(node);
    return node;
  }

  function overview() {
    if (!pins.length) return;
    map.fitBounds([...pins.map(pin => pin.point), ...(origin ? [origin] : [])], {
      padding: [30, 32], maxZoom: 15,
    });
  }

  function openStore(shop, fromPicker = false) {
    const { pin, marker, button } = shop;
    if (selected) {
      selected.marker.getElement().classList.remove("store-pin-selected");
      selected.marker.setZIndexOffset(0);
      selected.marker.closeTooltip();
      selected.button.setAttribute("aria-pressed", "false");
    }
    selected = shop;
    marker.getElement().classList.add("store-pin-selected");
    marker.setZIndexOffset(1000);
    button.setAttribute("aria-pressed", "true");
    if (fromPicker) map.panTo(pin.point);
    marker.openTooltip();

    panel.replaceChildren();
    const heading = element("h2", pin.name, panel);
    heading.tabIndex = -1;
    const distance = pin.distance == null ? "" : messages.distance.replace("{distance}", pin.distance.toFixed(2));
    element("p", distance + messages.count.replace("{count}", String(pin.count)), panel, "map-store-meta");
    if (pin.preview.length) {
      const list = element("ul", "", panel, "map-prices");
      for (const item of pin.preview) {
        const row = element("li", "", list);
        element("a", item.name, row).href = item.url;
        if (item.spec) element("small", item.spec, row);
        element("strong", item.price, row);
      }
      element("a", messages.active, panel, "map-action").href = pin.url;
    } else {
      element("p", messages.empty, panel);
    }
    element("a", messages.history, panel, "map-history").href = pin.history;
  }

  if (origin) {
    const home = L.circleMarker(origin, {
      radius: 7, color: "#fff", weight: 3, fillColor: "#2166c4", fillOpacity: 1,
    }).addTo(map);
    const label = document.createElement("span");
    label.textContent = messages.origin;
    home.bindTooltip(label, { direction: "bottom", offset: [0, 6] });
  }

  for (const pin of pins) {
    const marker = L.marker(pin.point, {
      title: pin.name,
      icon: L.divIcon({ className: "store-pin", iconSize: [24, 24], iconAnchor: [12, 12], tooltipAnchor: [0, -15] }),
    }).addTo(map);
    const node = marker.getElement();
    node.classList.toggle("store-pin-empty", !pin.count);
    node.setAttribute("aria-label", pin.name);
    const label = document.createElement("span");
    label.textContent = pin.name;
    marker.bindTooltip(label, { direction: "top", className: "map-shop-tooltip" });

    const button = element("button", pin.name, picker, "map-shop-button");
    button.type = "button";
    button.setAttribute("aria-pressed", "false");
    const shop = { pin, marker, button };
    shops.push(shop);
    button.addEventListener("click", () => openStore(shop, true));
    marker.on("click", () => {
      openStore(shop);
      if (window.matchMedia("(max-width: 720px)").matches) {
        panel.scrollIntoView({ block: "nearest" });
      }
    });
  }

  const radiusInput = document.querySelector("#compare-radius");
  const countLabel = document.querySelector("#compare-count");
  const compareLink = document.querySelector("#compare-link");
  const circle = L.circle(center, {
    radius: radius * 1000, color: "#376a94", weight: 2, fillOpacity: 0.08, interactive: false,
  }).addTo(map);

  function updateCircle() {
    const km = radiusInput.valueAsNumber;
    const valid = radiusInput.validity.valid && Number.isFinite(km);
    compareLink.hidden = !valid;
    if (!valid) {
      countLabel.textContent = "";
      return;
    }
    const point = circle.getLatLng();
    circle.setRadius(km * 1000);
    let count = 0;
    for (const { pin, marker } of shops) {
      const inside = map.distance(point, pin.point) <= km * 1000;
      marker.getElement().classList.toggle("store-pin-outside", !inside);
      if (inside) count++;
    }
    countLabel.textContent = messages.within.replace("{count}", String(count));
    compareLink.href = "/compare?" + new URLSearchParams({ lat: point.lat, lng: point.lng, km });
  }
  radiusInput.addEventListener("input", updateCircle);
  map.on("click", ({ latlng }) => {
    circle.setLatLng(latlng.wrap());
    updateCircle();
  });
  document.querySelector("#map-overview").addEventListener("click", overview);
  overview();
  const initial = shops.find(({ pin }) => pin.count) || shops[0];
  if (initial) openStore(initial);
  picker.hidden = !shops.length;
  updateCircle();
  let warning = null;
  tiles.on("tileerror", () => {
    if (!warning && !section.querySelector(".leaflet-tile-loaded")) {
      warning = element("p", messages.tiles, section, "map-note");
    }
  });
  tiles.on("tileload", () => {
    warning?.remove();
    warning = null;
  });
}
