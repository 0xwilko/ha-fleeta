/* FLEETA Car card - comes with the FLEETA (BlackVue) integration for Home Assistant.
 * Unofficial community card. Not made or endorsed by BlackVue or Pittasoft.
 * By Tim Wilkinson (0xwilko). MIT licence. https://github.com/0xwilko/ha-fleeta */
(() => {
  const TAG = 'fleeta-card';
  if (customElements.get(TAG)) return;
  const VERSION = '1.3.4';

  const DEFAULTS = { place: 'zone', hours_to_show: 24, map_theme: 'light', show_map: true };
  const BAD = ['unavailable', 'unknown', ''];

  // One shared cache of place names, so several cards and page reloads don't repeat lookups.
  const GEO_KEY = 'fleeta-card-places';
  const geoMem = new Map();
  let geoLast = 0;
  let geoBusy = false;
  const geoRead = () => { try { return JSON.parse(localStorage.getItem(GEO_KEY) || '{}') || {}; } catch (e) { return {}; } };
  const geoSave = (k, v) => {
    try {
      const all = geoRead();
      all[k] = v;
      const keys = Object.keys(all);
      if (keys.length > 200) keys.slice(0, keys.length - 200).forEach((x) => { delete all[x]; });
      localStorage.setItem(GEO_KEY, JSON.stringify(all));
    } catch (e) { /* storage unavailable */ }
  };
  const clean = (s) => String(s == null ? '' : s).replace(/[<>]/g, '').trim().slice(0, 60);

  // Street/area and town from OpenStreetMap. Only used when the card is set to "Street and town".
  async function lookupPlace(lat, lon) {
    const key = lat.toFixed(3) + ',' + lon.toFixed(3);
    if (geoMem.has(key)) return geoMem.get(key);
    const saved = geoRead()[key];
    if (saved) { geoMem.set(key, saved); return saved; }
    // OpenStreetMap asks for no more than one lookup a second; one a minute is plenty here.
    if (geoBusy || Date.now() - geoLast < 60000) return null;
    geoBusy = true;
    geoLast = Date.now();
    try {
      const url = 'https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=16&addressdetails=1&accept-language=en'
        + '&lat=' + encodeURIComponent(lat.toFixed(5)) + '&lon=' + encodeURIComponent(lon.toFixed(5));
      const r = await fetch(url, { headers: { Accept: 'application/json' }, referrerPolicy: 'strict-origin-when-cross-origin' });
      if (!r.ok) return null;
      const j = await r.json();
      const a = (j && j.address) || {};
      const area = clean(a.suburb || a.neighbourhood || a.quarter || a.village || a.hamlet || a.road);
      const town = clean(a.city || a.town || a.municipality || a.county);
      const text = [area, town].filter((x, i, arr) => x && arr.indexOf(x) === i).join(', ');
      if (!text) return null;
      geoMem.set(key, text);
      geoSave(key, text);
      return text;
    } catch (e) {
      return null;
    } finally {
      geoBusy = false;
    }
  }

  const ago = (iso) => {
    const t = Date.parse(iso);
    if (!isFinite(t)) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 45) return 'Just now';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    if (s < 7 * 86400) return Math.floor(s / 86400) + ' d ago';
    return new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  };

  const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
  const words = (s) => { const t = String(s || '').replace(/_/g, ' ').trim(); return t ? t.charAt(0).toUpperCase() + t.slice(1) : ''; };
  const mins = (m) => {
    const n = Number(m);
    if (!isFinite(n) || n <= 0) return '';
    if (n < 60) return Math.round(n) + ' min';
    const h = Math.floor(n / 60), r = Math.round(n % 60);
    return h + ' h' + (r ? ' ' + r + ' min' : '');
  };

  const CSS = `
  :host{display:block}
  *{box-sizing:border-box}
  ha-card{position:relative;overflow:hidden;padding:18px;border-radius:26px;color:#fff;
    background:radial-gradient(120% 80% at 0% 0%,rgba(70,110,255,.20),rgba(70,110,255,0) 60%),linear-gradient(165deg,#172650 0%,#0e1834 55%,#0a1228 100%);
    border:1.5px solid rgba(125,155,255,.38);
    box-shadow:0 12px 34px rgba(4,10,30,.55),inset 0 1px 0 rgba(255,255,255,.07),inset 0 0 40px rgba(80,120,255,.08);
    font-family:var(--ha-font-family-body,Roboto,"Helvetica Neue",Arial,sans-serif)}
  .head{display:flex;align-items:center;gap:14px;min-width:0;cursor:pointer;outline:none}
  .badge{flex:none;width:58px;height:58px;border-radius:16px;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(160deg,#4b7cff,#1f46c8);border:1px solid rgba(160,190,255,.55);
    box-shadow:0 0 18px rgba(70,120,255,.45),inset 0 1px 0 rgba(255,255,255,.35)}
  .badge ha-icon{--mdc-icon-size:32px;color:#fff}
  .sep{flex:none;width:1.5px;align-self:stretch;margin:6px 2px;background:rgba(140,165,255,.30)}
  .who{flex:1;min-width:0}
  .name{font-size:24px;font-weight:700;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .place{margin-top:3px;font-size:16px;font-weight:500;color:#86a6ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .pill{flex:none;display:flex;align-items:center;gap:6px;height:36px;padding:0 14px 0 11px;border-radius:18px;font-size:15px;font-weight:600;
    border:1.5px solid #35c47c;color:#eafff3;background:rgba(53,196,124,.10)}
  .pill ha-icon{--mdc-icon-size:20px;color:#35c47c}
  .pill.off{border-color:#7d879c;color:#cfd6e4;background:rgba(125,135,156,.12)}
  .pill.off ha-icon{color:#9aa4b8}
  .map{margin-top:16px;border-radius:18px;overflow:hidden;background:#dfe6ee;min-height:60px;
    --ha-card-border-radius:18px;--ha-card-border-width:0;--ha-card-box-shadow:none;--ha-card-background:transparent;
    box-shadow:0 0 0 1px rgba(140,165,255,.25)}
  .map.hide{display:none}
  .map .none{display:flex;align-items:center;justify-content:center;height:180px;color:#5b6780;font-size:14px}
  .tiles{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
  .tile{display:flex;align-items:center;gap:12px;min-width:0;padding:14px 16px;border-radius:18px;cursor:pointer;outline:none;
    background:linear-gradient(160deg,rgba(28,46,96,.85),rgba(16,28,62,.85));border:1px solid rgba(125,155,255,.24);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}
  .tile:focus-visible,.head:focus-visible{box-shadow:0 0 0 2px #6f95ff}
  .tile ha-icon{flex:none;--mdc-icon-size:30px;color:#5a86ff}
  .txt{flex:1;min-width:0;text-align:right}
  .val{font-size:21px;font-weight:700;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .val.long{font-size:16.5px;line-height:1.15;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  .lbl{margin-top:2px;font-size:13.5px;color:#9db0da;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tile.wide{grid-column:1 / -1;padding:10px 16px 10px 10px;gap:14px}
  .pic{flex:none;width:46%;max-width:220px;aspect-ratio:16/9;border-radius:12px;overflow:hidden;background:#0a1228;
    border:1px solid rgba(125,155,255,.30);box-shadow:0 4px 14px rgba(0,0,0,.35)}
  .pic img{display:block;width:100%;height:100%;object-fit:cover}
  .tile.wide .ic{flex:none;--mdc-icon-size:30px;color:#5a86ff;margin-left:6px}
  .tile.has-pic .ic{display:none}
  .tile:not(.has-pic) .pic{display:none}
  .dir{transition:transform .6s ease}
  .msg{padding:22px 6px;text-align:center;color:#c9d4ee;font-size:15px;line-height:1.5}
  .ver{margin-top:10px;text-align:right;font-size:11px;letter-spacing:.3px;color:rgba(157,176,218,.55);user-select:none}
  @media (max-width:420px){.name{font-size:21px}.val{font-size:18px}.tile{padding:12px}.tile ha-icon{--mdc-icon-size:26px}}
  `;

  class FleetaCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._config = null;
      this._mapCfg = '';
    }

    static getStubConfig(hass) {
      const tracker = FleetaCard._firstTracker(hass);
      return Object.assign({}, DEFAULTS, tracker ? { entity: tracker } : {});
    }

    static getConfigForm() {
      const LABELS = {
        entity: 'Car (FLEETA dashcam)',
        name: 'Name (leave empty to use the car name)',
        place: 'Place shown under the name',
        hours_to_show: 'Route trail',
        map_theme: 'Map style',
        show_map: 'Show the map',
      };
      const HELP = {
        place: 'Street and town looks the position up on OpenStreetMap, so the position is sent to them.',
        hours_to_show: 'Hours of route to draw on the map. 0 for none.',
      };
      return {
        schema: [
          { name: 'entity', selector: { entity: { domain: 'device_tracker', integration: 'fleeta' } } },
          { name: 'name', selector: { text: {} } },
          { name: 'place', selector: { select: { mode: 'dropdown', options: [
            { value: 'zone', label: 'Zone (Home, Work or Away)' },
            { value: 'address', label: 'Street and town (OpenStreetMap)' },
          ] } } },
          { name: '', type: 'grid', schema: [
            { name: 'hours_to_show', selector: { number: { min: 0, max: 168, step: 1, mode: 'box', unit_of_measurement: 'h' } } },
            { name: 'map_theme', selector: { select: { mode: 'dropdown', options: [
              { value: 'light', label: 'Light' },
              { value: 'dark', label: 'Dark' },
              { value: 'auto', label: 'Follow my theme' },
            ] } } },
          ] },
          { name: 'show_map', selector: { boolean: {} } },
        ],
        computeLabel: (s) => LABELS[s.name],
        computeHelper: (s) => HELP[s.name],
      };
    }

    static _firstTracker(hass) {
      if (!hass) return null;
      const e = Object.values(hass.entities || {}).find((x) => x.platform === 'fleeta' && x.entity_id.startsWith('device_tracker.'));
      if (e) return e.entity_id;
      const s = Object.keys(hass.states || {}).find((id) => id.startsWith('device_tracker.') && /fleeta/i.test(String((hass.states[id].attributes || {}).attribution || '')));
      return s || null;
    }

    setConfig(config) {
      if (!config || typeof config !== 'object') throw new Error('FLEETA card: invalid configuration');
      const c = Object.assign({}, DEFAULTS, config);
      c.hours_to_show = Math.max(0, Math.min(168, Number(c.hours_to_show) || 0));
      if (!['zone', 'address'].includes(c.place)) c.place = 'zone';
      if (!['light', 'dark', 'auto'].includes(c.map_theme)) c.map_theme = 'light';
      this._config = c;
      this._built = false;
      this._mapCfg = '';
      this._map = null;
      if (this._hass) this._render(true);
    }

    set hass(h) {
      this._hass = h;
      this._render(false);
    }

    getCardSize() { return this._config && this._config.show_map === false ? 4 : 9; }
    getGridOptions() { return { columns: 12, min_columns: 6, rows: 'auto' }; }

    connectedCallback() {
      clearInterval(this._timer);
      // keep "5 min ago" style times fresh
      this._timer = setInterval(() => this._render(true), 30000);
    }
    disconnectedCallback() { clearInterval(this._timer); this._timer = null; }

    /* ---------- finding the car's entities ---------- */
    _tracker() {
      const h = this._hass;
      const id = (this._config && this._config.entity) || FleetaCard._firstTracker(h);
      return id && h.states[id] ? id : null;
    }
    _related(trackerId) {
      const h = this._hass;
      const out = {};
      const reg = h.entities || {};
      const dev = reg[trackerId] && reg[trackerId].device_id;
      const want = {
        status: ['sensor', 'status'],
        speed: ['sensor', 'speed'],
        gps: ['sensor', 'last_gps_fix'],
        conn: ['binary_sensor', 'cloud_connection'],
        event: ['event', 'dashcam_event'],
        trip: ['sensor', 'last_trip'],
        picture: ['image', 'last_event_picture'],
      };
      const mine = dev ? Object.values(reg).filter((x) => x.device_id === dev) : [];
      Object.keys(want).forEach((k) => {
        const [domain, key] = want[k];
        let hit = mine.find((x) => x.entity_id.startsWith(domain + '.') && x.translation_key === key);
        if (!hit) hit = mine.find((x) => x.entity_id.startsWith(domain + '.') && x.entity_id.endsWith('_' + key));
        if (!hit && !dev) {
          const base = trackerId.split('.')[1];
          const guess = domain + '.' + base + '_' + key;
          if (h.states[guess]) hit = { entity_id: guess };
        }
        if (hit && h.states[hit.entity_id]) out[k] = hit.entity_id;
      });
      out.device = dev ? (h.devices || {})[dev] : null;
      return out;
    }

    /* ---------- building ---------- */
    _build() {
      this._built = true;
      const root = this.shadowRoot;
      root.innerHTML = '';
      const style = document.createElement('style');
      style.textContent = CSS;
      const card = document.createElement('ha-card');
      card.innerHTML = `
        <div class="head" role="button" tabindex="0">
          <div class="badge"><ha-icon icon="mdi:car"></ha-icon></div>
          <div class="sep"></div>
          <div class="who"><div class="name"></div><div class="place"></div></div>
          <div class="pill"><ha-icon icon="mdi:cloud-check-outline"></ha-icon><span></span></div>
        </div>
        <div class="map"></div>
        <div class="tiles">
          <div class="tile t-status" role="button" tabindex="0"><ha-icon icon="mdi:car-brake-parking"></ha-icon><div class="txt"><div class="val"></div><div class="lbl">Status</div></div></div>
          <div class="tile t-speed" role="button" tabindex="0"><ha-icon icon="mdi:speedometer"></ha-icon><div class="txt"><div class="val"></div><div class="lbl">Speed</div></div></div>
          <div class="tile t-gps" role="button" tabindex="0"><ha-icon icon="mdi:crosshairs-gps"></ha-icon><div class="txt"><div class="val"></div><div class="lbl">Last GPS fix</div></div></div>
          <div class="tile t-trip" role="button" tabindex="0"><ha-icon icon="mdi:map-marker-distance"></ha-icon><div class="txt"><div class="val"></div><div class="lbl"></div></div></div>
          <div class="tile wide t-event" role="button" tabindex="0"><div class="pic"><img alt="Dashcam picture of the last event"></div><ha-icon class="ic" icon="mdi:cctv"></ha-icon><div class="txt"><div class="val"></div><div class="lbl"></div></div></div>
        </div>
        <div class="msg" hidden></div>
        <div class="ver"></div>`;
      root.append(style, card);
      const q = (s) => card.querySelector(s);
      q('.ver').textContent = 'FLEETA Car v' + VERSION;
      this._el = {
        card, head: q('.head'), name: q('.name'), place: q('.place'), pill: q('.pill'), pillIcon: q('.pill ha-icon'), pillTxt: q('.pill span'),
        map: q('.map'), tiles: q('.tiles'), msg: q('.msg'), ver: q('.ver'),
        status: q('.t-status'), statusIcon: q('.t-status ha-icon'), statusVal: q('.t-status .val'),
        speed: q('.t-speed'), speedIcon: q('.t-speed ha-icon'), speedVal: q('.t-speed .val'), speedLbl: q('.t-speed .lbl'),
        gps: q('.t-gps'), gpsVal: q('.t-gps .val'),
        trip: q('.t-trip'), tripVal: q('.t-trip .val'), tripLbl: q('.t-trip .lbl'),
        event: q('.t-event'), eventIcon: q('.t-event .ic'), eventVal: q('.t-event .val'), eventLbl: q('.t-event .lbl'), eventImg: q('.t-event img'),
      };
      const open = (key) => () => { const id = this._ids && this._ids[key]; if (id) this._moreInfo(id); };
      const bind = (node, key) => {
        node.addEventListener('click', open(key));
        node.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(key)(); } });
      };
      bind(this._el.head, 'tracker');
      bind(this._el.status, 'status');
      bind(this._el.speed, 'speed');
      bind(this._el.gps, 'gps');
      bind(this._el.trip, 'trip');
      bind(this._el.event, 'eventTap');
    }
    _moreInfo(entityId) {
      this.dispatchEvent(new CustomEvent('hass-more-info', { detail: { entityId }, bubbles: true, composed: true }));
    }

    async _ensureMap(trackerId) {
      const c = this._config;
      const box = this._el.map;
      box.classList.toggle('hide', c.show_map === false);
      if (c.show_map === false) return;
      const cfg = {
        type: 'map',
        entities: [{ entity: trackerId, label_mode: 'icon' }],
        hours_to_show: c.hours_to_show,
        theme_mode: c.map_theme,
        default_zoom: 15,
        auto_fit: true,
        aspect_ratio: '16:10',
      };
      const key = JSON.stringify(cfg);
      if (key === this._mapCfg) { if (this._map) this._map.hass = this._hass; return; }
      this._mapCfg = key;
      try {
        const helpers = window.loadCardHelpers ? await window.loadCardHelpers() : null;
        if (!helpers) throw new Error('no card helpers');
        if (key !== this._mapCfg) return;
        const el = await helpers.createCardElement(cfg);
        if (key !== this._mapCfg) return;
        el.hass = this._hass;
        box.textContent = '';
        box.appendChild(el);
        this._map = el;
      } catch (e) {
        box.innerHTML = '<div class="none">Map not available</div>';
        this._map = null;
      }
    }

    /* ---------- drawing ---------- */
    _render(force) {
      const h = this._hass;
      if (!h || !this._config) return;
      if (!this._built) this._build();
      const el = this._el;
      const trackerId = this._tracker();
      if (!trackerId) {
        el.head.hidden = true; el.map.hidden = true; el.tiles.hidden = true;
        el.msg.hidden = false;
        el.msg.textContent = 'No FLEETA car found. Add the FLEETA integration, then pick your car in this card\'s settings.';
        return;
      }
      el.head.hidden = false; el.map.hidden = false; el.tiles.hidden = false; el.msg.hidden = true;
      const ids = this._related(trackerId);
      ids.tracker = trackerId;
      this._ids = ids;

      // only redraw when one of our entities has changed (or the clock ticks)
      const watch = ['tracker', 'status', 'speed', 'gps', 'conn', 'event', 'trip', 'picture'].map((k) => ids[k] && h.states[ids[k]]);
      if (!force && this._last && watch.every((s, i) => s === this._last[i])) { if (this._map) this._map.hass = h; return; }
      this._last = watch;
      this._ensureMap(trackerId);

      const st = (k) => (ids[k] ? h.states[ids[k]] : null);
      const ok = (s) => !!s && !BAD.includes(s.state);
      const fmt = (s) => { try { return h.formatEntityState ? h.formatEntityState(s) : s.state; } catch (e) { return s.state; } };
      const tracker = st('tracker');

      // header
      const dev = ids.device;
      const name = clean(this._config.name) || clean(dev && (dev.name_by_user || dev.name)) || clean(tracker.attributes.friendly_name) || 'Car';
      el.name.textContent = name;
      this._drawPlace(tracker, ok, fmt);
      const status = st('status');
      const conn = st('conn');
      const online = conn ? conn.state === 'on' : (ok(status) && status.state !== 'offline');
      el.pill.classList.toggle('off', !online);
      el.pillIcon.setAttribute('icon', online ? 'mdi:cloud-check-outline' : 'mdi:cloud-off-outline');
      el.pillTxt.textContent = online ? 'Online' : 'Offline';

      // tiles
      el.statusVal.textContent = ok(status) ? fmt(status) : '-';
      el.statusIcon.setAttribute('icon', !ok(status) ? 'mdi:car-info' : status.state === 'driving' ? 'mdi:car-arrow-right' : status.state === 'offline' ? 'mdi:car-off' : 'mdi:car-brake-parking');
      const speed = st('speed');
      el.speedVal.textContent = ok(speed) ? fmt(speed) : '-';
      // While driving, the speed tile shows which way the car is heading.
      const heading = Number((tracker.attributes || {}).heading);
      const driving = ok(status) && status.state === 'driving' && tracker.attributes.heading != null && isFinite(heading);
      el.speedIcon.setAttribute('icon', driving ? 'mdi:navigation' : 'mdi:speedometer');
      el.speedIcon.classList.toggle('dir', driving);
      el.speedIcon.style.transform = driving ? 'rotate(' + Math.round(heading) + 'deg)' : '';
      el.speedLbl.textContent = driving ? 'Speed · ' + compass(heading) : 'Speed';

      const gps = st('gps');
      el.gpsVal.textContent = ok(gps) ? (ago(gps.state) || '-') : '-';

      const trip = st('trip');
      el.trip.hidden = !ids.trip;
      el.tripVal.textContent = ok(trip) ? fmt(trip) : '-';
      const tripTime = ok(trip) ? mins(trip.attributes.duration_minutes) : '';
      el.tripLbl.textContent = 'Last trip' + (tripTime ? ' · ' + tripTime : '');

      this._drawEvent(st('event'), st('picture'), ok);
      // long names (e.g. "Harsh acceleration") drop a size and may use two lines
      [el.statusVal, el.speedVal, el.gpsVal, el.tripVal, el.eventVal].forEach((v) => v.classList.toggle('long', v.textContent.length > 12));
    }

    _drawEvent(ev, pic, ok) {
      const h = this._hass;
      const el = this._el;
      const evId = ok(ev) ? ev.attributes.event_id : null;
      const pa = (pic && pic.attributes) || {};
      const src = typeof pa.entity_picture === 'string' && pa.entity_picture.startsWith('/api/image_proxy/') ? pa.entity_picture : '';
      // Only show the picture when it belongs to the latest event.
      const showPic = !!src && ok(pic) && (!evId || pa.event_id === evId);
      el.event.classList.toggle('has-pic', showPic);
      el.eventIcon.style.display = showPic ? 'none' : '';
      if (showPic && el.eventImg.getAttribute('src') !== src) el.eventImg.setAttribute('src', src);
      if (!showPic && el.eventImg.hasAttribute('src')) el.eventImg.removeAttribute('src');
      this._ids.eventTap = showPic ? this._ids.picture : this._ids.event;

      if (ok(ev) && ev.attributes.event_type) {
        let label = ev.attributes.event_type;
        try { if (h.formatEntityAttributeValue) label = h.formatEntityAttributeValue(ev, 'event_type'); } catch (e) { /* keep raw */ }
        el.eventVal.textContent = clean(label);
        el.eventLbl.textContent = 'Last event · ' + ago(ev.state);
      } else if (showPic && pa.event_type) {
        el.eventVal.textContent = clean(words(pa.event_type));
        el.eventLbl.textContent = 'Last event' + (pa.occurred_at ? ' · ' + ago(pa.occurred_at) : '');
      } else {
        el.eventVal.textContent = 'None yet';
        el.eventLbl.textContent = 'Last event';
      }
    }

    _drawPlace(tracker, ok, fmt) {
      const el = this._el;
      if (!ok(tracker)) { el.place.textContent = 'Position unknown'; return; }
      const a = tracker.attributes || {};
      const zoneText = clean(fmt(tracker));
      // In a zone (Home, Work...) the zone name always wins.
      if (this._config.place !== 'address' || tracker.state !== 'not_home' || !isFinite(a.latitude) || !isFinite(a.longitude)) {
        el.place.textContent = zoneText;
        return;
      }
      const lat = Number(a.latitude), lon = Number(a.longitude);
      const key = lat.toFixed(3) + ',' + lon.toFixed(3);
      const known = geoMem.get(key) || geoRead()[key];
      if (known) { el.place.textContent = known; return; }
      if (!el.place.textContent) el.place.textContent = zoneText;
      if (this._looking === key) return;
      this._looking = key;
      lookupPlace(lat, lon).then((text) => {
        if (this._looking === key) this._looking = null;
        const cur = this._hass && this._ids && this._hass.states[this._ids.tracker];
        const ca = (cur && cur.attributes) || {};
        if (text && isFinite(ca.latitude) && Number(ca.latitude).toFixed(3) + ',' + Number(ca.longitude).toFixed(3) === key) el.place.textContent = text;
      });
    }
  }

  customElements.define(TAG, FleetaCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: TAG,
    name: 'FLEETA Car',
    description: 'Your BlackVue dashcam car: map, route, status, speed, last trip and the last camera event with its picture.',
    preview: true,
    documentationURL: 'https://github.com/0xwilko/ha-fleeta',
  });
  console.info('%c FLEETA CAR %c v' + VERSION + ' ', 'background:#2d5bff;color:#fff;font-weight:700;border-radius:3px 0 0 3px', 'background:#0e1834;color:#fff;border-radius:0 3px 3px 0');
})();
