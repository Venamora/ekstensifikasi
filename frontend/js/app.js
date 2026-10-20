const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const number = (value) => value == null ? 'Tidak tersedia' : new Intl.NumberFormat('id-ID').format(value);
const date = (value) => value ? new Intl.DateTimeFormat('id-ID', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Jakarta' }).format(new Date(value)) + ' WIB' : 'Belum tersedia';
const el = (tag, text, className) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; };
const icon = (name) => { const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); const use = document.createElementNS(node.namespaceURI, 'use'); use.setAttribute('href', `#i-${name}`); node.setAttribute('aria-hidden', 'true'); node.append(use); return node; };
const state = { segment: 'pebisnis', revision: 0, savedFilters: {}, geoAreas: [], crossAreas: [], businessPeriod: '', page: 1, pageSize: 25, sort: 'businessName', direction: 'asc', areaId: '', route: 'overview', view: 'split',
  options: { locations: [], areas: [], periods: [] }, status: {}, items: [], stats: null, areas: [], shortlistPage: 1, shortlistTotal: 0 };
const businessColumns = [
  ['businessName', 'Nama usaha'], ['businessCategory', 'Kategori'], ['kbliCode', 'KBLI'], ['businessType', 'Jenis usaha'],
  ['district', 'Kecamatan'], ['subdistrict', 'Kelurahan'], ['address', 'Alamat lengkap'], ['employeeCount', 'TKI'],
  ['areaId', 'Wilayah'], ['geocodingStatus', 'Koordinat'], ['dataPeriod', 'Periode'], ['actions', 'Aksi']
];
const schoolColumns = [['schoolName', 'Nama sekolah'], ['npsn', 'NPSN'], ['level', 'Jenjang'], ['schoolStatus', 'Status sekolah'],
  ['city', 'Kota/Kabupaten'], ['district', 'Kecamatan'], ['subdistrict', 'Kelurahan'], ['address', 'Alamat'],
  ['phone', 'Telepon'], ['phoneStatus', 'Kondisi telepon'], ['email', 'Email'], ['areaId', 'Wilayah'], ['actions', 'Aksi']];
let columns = businessColumns;
const isSchool = () => state.segment !== 'pebisnis';
const segmentName = () => state.segment === 'gmm' ? 'GMM' : state.segment === 'simpel' ? 'Tabungan SimPel' : 'Pebisnis';
const recordName = record => record.schoolName || record.businessName || 'Nama tidak tersedia';
const recordId = record => record.npsn || record.id;
const shortlistPath = () => isSchool() ? '/api/individuals/shortlist' : '/api/shortlist';
const phoneLabel = value => ({available: 'Tercantum · belum diverifikasi', missing: 'Tidak tersedia', review: 'Perlu cek format', unavailable: 'Detail belum tersedia'}[value] || 'Tidak tersedia');
let visible = new Set(['businessName', 'businessCategory', 'subdistrict', 'employeeCount', 'areaId', 'dataPeriod', 'actions']);
try { const saved = JSON.parse(localStorage.getItem('geoprospect.columns')); if (Array.isArray(saved)) visible = new Set(saved.filter(k => columns.some(c => c[0] === k))); } catch { /* Browser storage may be disabled. */ }
visible.add('businessName'); visible.add('actions');
let controller, toastTimer, searchTimer, refreshTimer, map, mapLayer, fullBounds, shortlistRequest = 0, detailRequest = 0;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : `Permintaan gagal (${response.status}). Periksa isian atau coba lagi.`);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message, error = false) {
  const node = $('#toast'); node.textContent = message; node.classList.toggle('error', error); node.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true; }, 5000);
}

function selectedFilters() {
  const params = new URLSearchParams();
  for (const [key, value] of new FormData($('#filters-form'))) if (value && value !== 'all' || key === 'period' && value === 'all') params.append(key, value);
  if (state.areaId) params.set('area_id', state.areaId);
  if (isSchool()) params.set('program', state.segment);
  return params;
}

function tableParams() {
  const params = selectedFilters(); params.set('page', state.page); params.set('page_size', state.pageSize);
  params.set('sort', state.sort); params.set('direction', state.direction); return params;
}

function options(select, values, placeholder, selected = '') {
  select.replaceChildren(); if (placeholder != null) select.append(new Option(placeholder, ''));
  values.forEach(value => select.append(new Option(value, value)));
  if ([...select.options].some(o => o.value === selected)) select.value = selected;
}

function updateLocations() {
  const district = $('#district-filter').value;
  const current = $('#subdistrict-filter').value;
  const city = $('#city-filter').value;
  const locations = [...state.options.locations, ...state.options.areas].filter(a => !city || (a.city || 'Jakarta Selatan') === city);
  options($('#district-filter'), [...new Set(locations.map(a => a.district).filter(Boolean))].sort(), 'Semua kecamatan', district);
  const names = [...new Set(locations.filter(a => !$('#district-filter').value || a.district === $('#district-filter').value).map(a => a.subdistrict).filter(Boolean))].sort();
  options($('#subdistrict-filter'), names, 'Semua kelurahan', current);
}

async function loadOptions(previous = new FormData($('#filters-form'))) {
  const revision = state.revision;
  const result = await api(isSchool() ? '/api/individuals/filters' : '/api/filters');
  if (revision !== state.revision) return;
  state.options = {...result, periods: result.periods || [], areas: state.geoAreas.length ? state.geoAreas : result.areas};
  options($('#city-filter'), [...new Set([...state.options.locations, ...state.options.areas].map(a => a.city || 'Jakarta Selatan'))].sort(), 'Semua kota/kabupaten', previous.get('city'));
  if (!isSchool()) {
  options($('#period-filter'), state.options.periods, null, previous.get('period'));
  $('#period-filter').append(new Option('Semua periode', 'all'));
  if (previous.get('period') === 'all') $('#period-filter').value = 'all';
  options($('#district-filter'), [...new Set([...state.options.locations, ...state.options.areas].map(a => a.district).filter(Boolean))].sort(), 'Semua kecamatan', previous.get('district'));
  for (const [id, key, name] of [['category-filter', 'categories', 'category'], ['kbli-filter', 'kbli', 'kbli'], ['type-filter', 'businessTypes', 'business_type']]) {
    options($(`#${id}`), state.options[key], null);
    [...$(`#${id}`).options].forEach(o => { o.selected = previous.getAll(name).includes(o.value); });
  }
  }
  for (const input of $('#filters-form').elements) {
    if (input.name && !input.multiple && !['city', 'district', 'subdistrict', 'period'].includes(input.name)) input.value = previous.get(input.name) || (['area_quality', 'employee_data'].includes(input.name) ? 'all' : '');
  }
  options($('#district-filter'), [...new Set([...state.options.locations, ...state.options.areas].filter(a => !$('#city-filter').value || (a.city || 'Jakarta Selatan') === $('#city-filter').value).map(a => a.district).filter(Boolean))].sort(), 'Semua kecamatan', previous.get('district'));
  updateLocations();
  $('#subdistrict-filter').value = previous.get('subdistrict') || '';
}

function updateStatus(status) {
  state.status = status;
  if (isSchool()) { renderSchoolStatus(status); return; }
  $('#sync-label').textContent = date(status.snapshot?.fetchedAt);
  $('#refresh-button').disabled = status.refreshState === 'running';
  const messages = [];
  if (!status.snapshot) messages.push('Belum ada snapshot registry. Jalankan sinkronisasi untuk menampilkan data pemerintah yang sebenarnya.');
  if (status.refreshState === 'running') messages.push('Sinkronisasi sedang berjalan. Snapshot terakhir tetap dapat digunakan.');
  if (status.lastError) messages.push(status.lastError);
  if (status.boundaryError) messages.push(status.boundaryError);
  $('#global-alert').textContent = messages.join(' '); $('#global-alert').hidden = !messages.length;
  const snapshot = status.snapshot || {};
  definitionList($('#source-details'), [
    ['Snapshot berhasil', date(snapshot.fetchedAt)], ['Periode tersedia', (snapshot.periods || []).join(', ') || 'Belum tersedia'],
    ['Baris sumber DKI Jakarta', number(snapshot.upstreamRows)], ['Baris berlabel Jakarta Selatan', number(snapshot.southRawRows)],
    ['Entri tersimpan', number(snapshot.storedRows)], ['Duplikasi identik dihapus', number(snapshot.exactDuplicatesRemoved)],
    ['Last-Modified dari sumber', snapshot.lastModified || 'Tidak disediakan'], ['SHA-256 snapshot', snapshot.id || 'Belum tersedia']
  ]);
  definitionList($('#boundary-details'), [['Sumber', 'GIS DPMPTSP Pemprov Jakarta'], ['Kelurahan tersimpan', number(status.areaCount)],
    ['Diambil pada', date(status.boundaries?.fetchedAt)], ['Sistem koordinat', 'WGS 84 / EPSG:4326']]);
}

function definitionList(node, pairs) {
  node.replaceChildren(); for (const [label, value] of pairs) { const wrap = el('div'); wrap.append(el('dt', label), el('dd', value ?? 'Tidak tersedia')); node.append(wrap); }
}

function renderStats(stats) {
  state.stats = stats;
  if (isSchool()) { renderSchoolStats(stats); return; }
  const ready = Boolean(state.status.snapshot);
  for (const [id, value] of [['total', stats.periodTotal], ['filtered', stats.total], ['matched', stats.matched], ['employees', stats.reportedEmployees], ['categories', stats.categories], ['shortlist', stats.shortlisted]]) {
    $(`#kpi-${id}`).textContent = ready ? (value == null ? '—' : number(value)) : '—';
  }
  $('#kpi-matched-note').textContent = ready ? `${number(stats.unmatched)} entri belum cocok` : 'Terhubung ke kelurahan';
  $('#kpi-employees-note').textContent = `Tersedia pada ${number(stats.employeeAvailable)} entri`;
  $('#period-label').textContent = stats.period === 'all' ? 'Semua tahun · entri lintas periode' : (stats.period || '—');
  const area = state.options.areas.find(a => a.id === state.areaId);
  const label = area ? `${area.subdistrict}, ${area.city || 'Jakarta Selatan'}` : $('#subdistrict-filter').value || $('#district-filter').value || $('#city-filter').value || 'Jakarta Selatan';
  $('#selected-area').textContent = label;
  $('#filter-summary').textContent = ready ? `${number(stats.total)} entri • ${label} • ${stats.period === 'all' ? 'Semua periode (bukan usaha unik)' : 'Periode ' + stats.period}` : 'Belum ada data yang berhasil disinkronkan';
  const percent = stats.total ? Math.round(100 * stats.matched / stats.total) : null;
  $('#quality-pill').textContent = percent == null ? 'Cakupan wilayah —' : `${percent}% wilayah cocok`;
  $('#quality-pill').classList.toggle('warning', stats.unmatched > 0);
  const chart = $('#category-chart'); chart.replaceChildren();
  const maximum = stats.categoryBreakdown[0]?.count || 1;
  stats.categoryBreakdown.forEach(c => {
    const row = el('div', null, 'category-row'); const label = el('div'); label.append(el('span', c.name), el('strong', number(c.count)));
    const bar = el('div', null, 'category-bar'); const fill = el('i'); fill.style.width = `${100 * c.count / maximum}%`; bar.append(fill); row.append(label, bar); chart.append(row);
  });
  if (!stats.categoryBreakdown.length) chart.append(el('p', 'Tidak ada kategori pada hasil filter.'));
}

function renderColumns() {
  const menu = $('#column-options'); menu.replaceChildren();
  columns.forEach(([key, title]) => {
    const label = el('label'); const input = el('input'); input.type = 'checkbox'; input.checked = visible.has(key);
    input.disabled = key === 'businessName' || key === 'schoolName' || key === 'actions';
    input.addEventListener('change', () => {
      if (input.checked) visible.add(key); else visible.delete(key);
      try { localStorage.setItem(`geoprospect.columns.${state.segment}`, JSON.stringify([...visible])); } catch { /* Optional display preference. */ }
      renderTable();
    }); label.append(input, document.createTextNode(title)); menu.append(label);
  });
}

function smallButton(label, title, action) {
  const button = el('button', label, 'icon-button'); button.type = 'button'; button.title = title; button.setAttribute('aria-label', title);
  button.addEventListener('click', event => { event.stopPropagation(); action(); }); return button;
}

function mapsLink(record, compact = false) {
  const query = [recordName(record), record.address, record.subdistrict, record.district, record.city || 'Jakarta Selatan', 'Indonesia'].filter(Boolean).join(', ');
  const link = el('a', compact ? '' : 'Cari di Google Maps', compact ? 'icon-button' : 'button');
  const url = new URL('https://www.google.com/maps/search/'); url.searchParams.set('api', '1'); url.searchParams.set('query', query.slice(0, 550));
  link.href = url.toString(); link.target = '_blank'; link.rel = 'noopener noreferrer'; link.setAttribute('aria-label', `Cari ${recordName(record)} di Google Maps`);
  link.append(icon('external')); link.addEventListener('click', e => e.stopPropagation()); return link;
}

function renderTable() {
  const head = $('#business-table thead'); const body = $('#business-table tbody'); head.replaceChildren(); body.replaceChildren();
  const header = el('tr'); const shown = columns.filter(([key]) => visible.has(key));
  for (const [key, title] of shown) {
    const th = el('th'); th.scope = 'col';
    if (!['actions', 'geocodingStatus'].includes(key)) {
      const sort = el('button', title + (state.sort === key ? (state.direction === 'asc' ? ' ↑' : ' ↓') : ''));
      sort.addEventListener('click', () => { state.direction = state.sort === key && state.direction === 'asc' ? 'desc' : 'asc'; state.sort = key; state.page = 1; loadData(); });
      th.setAttribute('aria-sort', state.sort === key ? (state.direction === 'asc' ? 'ascending' : 'descending') : 'none'); th.append(sort);
    } else th.textContent = title;
    header.append(th);
  }
  head.append(header);
  for (const record of state.items) {
    const row = el('tr');
    row.addEventListener('click', () => showDetail(recordId(record)));
    for (const [key] of shown) {
      const cell = el('td');
      if (key === 'businessName' || key === 'schoolName') {
        cell.className = 'business-name'; const button = el('button', recordName(record));
        button.addEventListener('click', e => { e.stopPropagation(); showDetail(recordId(record)); });
        cell.append(button, el('small', record.educationForm || record.businessType || 'Jenis usaha tidak tersedia'));
      } else if (key === 'businessCategory') cell.append(el('span', record[key] || 'Tidak tersedia', 'category-chip'));
      else if (key === 'employeeCount') { cell.textContent = record[key] == null ? '—' : number(record[key]); cell.title = record[key] == null ? 'Tidak tersedia' : 'Jumlah TKI dilaporkan'; }
      else if (key === 'areaId') cell.append(el('span', record.areaId ? 'Cocok' : 'Perlu tinjau', `status-badge${record.areaId ? '' : ' warning'}`));
      else if (key === 'geocodingStatus') cell.textContent = 'Tidak tersedia';
      else if (key === 'phoneStatus') cell.textContent = phoneLabel(record.phoneStatus);
      else if (key === 'actions') {
        const actions = el('div', null, 'row-actions'); actions.append(mapsLink(record, true));
        const add = smallButton(record.shortlisted ? '✓' : '+', record.shortlisted ? 'Sudah tersimpan' : `Tambahkan ${recordName(record)} ke shortlist`, () => addShortlist(record, add));
        add.disabled = Boolean(record.shortlisted); actions.append(add); cell.append(actions);
      } else { cell.textContent = record[key] || '—'; cell.className = key === 'address' ? 'address-cell' : 'nowrap'; }
      row.append(cell);
    }
    body.append(row);
  }
  $('#directory-empty').hidden = state.items.length > 0;
  $('#directory-empty').textContent = state.status.snapshot ? `Tidak ada ${isSchool() ? 'sekolah' : 'usaha'} yang sesuai. Coba ubah filter.` : 'Belum ada snapshot data. Impor data melalui server.';
}

async function loadData() {
  controller?.abort(); controller = new AbortController(); const current = controller;
  $('#explorer').classList.add('loading'); $('#directory-card').setAttribute('aria-busy', 'true');
  $('#export-csv').disabled = true; $('#export-xlsx').disabled = true;
  try {
    const params = selectedFilters(); const signal = current.signal;
    if (!isSchool()) state.businessPeriod = $('#period-filter').value;
    const prefix = isSchool() ? '/api/individuals' : '/api';
    const [directory, stats, areas, comparison, cross] = await Promise.all([
      api(`${isSchool() ? '/api/individuals' : '/api/businesses'}?${tableParams()}`, { signal }), api(`${prefix}/statistics?${params}`, { signal }),
      api(`${prefix}/areas?${params}`, { signal }), api(`${prefix}/opportunities?${params}&level=${$('#opportunity-level').value}`, { signal }),
      api(`/api/map/areas${state.businessPeriod ? '?period=' + encodeURIComponent(state.businessPeriod) : ''}`, { signal })
    ]);
    if (current !== controller) return;
    state.items = directory.items; state.areas = areas.items; state.total = directory.total;
    state.crossAreas = cross.items; state.businessPeriod = cross.period;
    renderTable(); renderStats(stats); renderOpportunities(comparison); renderMapCounts();
    $('#directory-count').textContent = number(directory.total);
    $('#page-info').textContent = directory.total ? `${number((state.page - 1) * state.pageSize + 1)}–${number(Math.min(state.page * state.pageSize, directory.total))} dari ${number(directory.total)}` : '0 hasil';
    $('#page-number').textContent = `${state.page} / ${Math.max(1, Math.ceil(directory.total / state.pageSize))}`;
    $('#prev-page').disabled = state.page <= 1; $('#next-page').disabled = state.page * state.pageSize >= directory.total;
    $('#export-csv').disabled = !directory.total; $('#export-xlsx').disabled = !directory.total;
  } catch (error) {
    if (current === controller && error.name !== 'AbortError') {
      toast(error.message, true); state.items = []; renderTable();
      $('#directory-empty').textContent = `Data gagal dimuat: ${error.message}`;
      $('#filter-summary').textContent = 'Hasil filter gagal dimuat. Ubah filter atau muat ulang halaman.';
      $$('.kpi strong').forEach(n => { n.textContent = '—'; });
      $('#directory-count').textContent = '—'; $('#page-info').textContent = 'Gagal memuat';
      $('#prev-page').disabled = true; $('#next-page').disabled = true;
      state.areas = []; renderMapCounts(); $('#opportunity-table tbody').replaceChildren(); $('#category-chart').replaceChildren();
    }
  } finally { if (current === controller) { $('#explorer').classList.remove('loading'); $('#directory-card').setAttribute('aria-busy', 'false'); } }
}

function setupMap(geojson) {
  state.geoAreas = geojson.features.map(f => f.properties);
  state.options.areas = state.geoAreas;
  options($('#city-filter'), [...new Set(state.geoAreas.map(a => a.city || 'Jakarta Selatan'))].sort(), 'Semua kota/kabupaten', $('#city-filter').value);
  updateLocations();
  if (!window.L) { $('#map-loading').textContent = 'Library peta tidak tersedia. Gunakan filter wilayah di atas.'; return; }
  if (!map) {
    map = L.map('map', { scrollWheelZoom: true, preferCanvas: false });
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors' }).addTo(map);
    let tileWarning = false; tiles.on('tileerror', () => { if (!tileWarning) { toast('Peta dasar gagal dimuat. Batas kelurahan dan filter tetap dapat digunakan.', true); tileWarning = true; } });
  }
  if (mapLayer) map.removeLayer(mapLayer);
  if (!geojson.features.length) {
    $('#map-loading').hidden = false; $('#map-loading').textContent = 'Batas wilayah belum tersedia. Gunakan dropdown kecamatan/kelurahan.';
    // View center only; no business marker is created from this coordinate.
    map.setView([-6.26, 106.81], 11); return;
  }
  mapLayer = L.geoJSON(geojson, { style: { color: '#fff', weight: 1.5, fillOpacity: .65 }, onEachFeature(feature, layer) {
    layer.on('click', () => selectArea(feature.properties.id));
    layer.on('mouseover', () => layer.setStyle({ weight: 3, color: '#ffd100' }));
    layer.on('mouseout', renderMapCounts);
    const tooltip = el('div', feature.properties.subdistrict); layer.bindTooltip(tooltip, { sticky: true });
    layer.bindPopup(el('div', feature.properties.subdistrict));
  } }).addTo(map);
  fullBounds = mapLayer.getBounds(); fitSegmentBounds();
  mapLayer.eachLayer(layer => {
    const path = layer.getElement(); path.setAttribute('tabindex', '0'); path.setAttribute('role', 'button');
    const a = layer.feature.properties;
    path.setAttribute('aria-label', `Pilih kelurahan ${a.subdistrict}, ${a.district}, ${a.city}`);
    path.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectArea(a.id); layer.openPopup(); } });
  });
  $('#map-loading').hidden = true; $('#map-area-count').textContent = `${geojson.features.length} kelurahan`;
  renderMapCounts();
}

function renderMapCounts() {
  if (!mapLayer) return;
  const lookup = new Map(state.areas.map(a => [a.id, a]));
  const cross = new Map(state.crossAreas.map(a => [a.id, a]));
  const max = Math.max(0, ...state.areas.map(a => a.count));
  $('#legend-max').textContent = number(max);
  mapLayer.eachLayer(layer => {
    const a = layer.feature.properties, item = lookup.get(a.id), totals = cross.get(a.id);
    const covered = isSchool() || a.id.startsWith('3174');
    const value = item?.count || 0, ratio = max ? value / max : 0;
    const chosen = a.id === state.areaId;
    layer.setStyle({color: chosen ? '#c49c00' : '#ffffff', weight: chosen ? 3 : 1.3,
      fillColor: !covered ? '#c9cdd2' : chosen ? '#ffd100' : ratio > .66 ? '#003d79' : ratio > .33 ? '#5a8aba' : value ? '#acc4df' : '#e3ecf6', fillOpacity: chosen ? .45 : .7});
    const tooltip = el('div'); tooltip.append(el('strong', a.subdistrict), el('div', a.city),
      el('div', covered ? `${number(value)} hasil filter ${segmentName()}` : 'Pebisnis: belum ada cakupan data'));
    layer.setTooltipContent(tooltip);
    const popup = el('div'); popup.append(el('strong', a.subdistrict), el('p', `${a.district}, ${a.city}`),
      el('p', `Pebisnis (${state.businessPeriod || 'periode terbaru'}): ${totals?.pebisnis == null ? 'Belum ada cakupan data' : number(totals.pebisnis)}`),
      el('p', `GMM · seluruh SMA: ${number(totals?.gmm)}`), el('p', `SimPel · seluruh SD/SMP: ${number(totals?.simpel)}`),
      el('p', covered ? `Hasil filter ${segmentName()}: ${number(value)}` : 'Belum ada cakupan data Pebisnis.'));
    layer.setPopupContent(popup);
    layer.getElement()?.setAttribute('aria-pressed', String(chosen));
  });
  const a = state.options.areas.find(a => a.id === state.areaId);
  const item = lookup.get(state.areaId);
  $('#map-selection').textContent = a ? `${a.subdistrict}, ${a.district}, ${a.city} · ${!isSchool() && !a.id.startsWith('3174') ? 'Belum ada cakupan data Pebisnis' : number(item?.count || 0) + ' hasil filter ' + segmentName()}` :
    state.stats?.unmatched ? `${number(state.stats.unmatched)} entri belum cocok dengan kelurahan; tetap tersedia di direktori.` : 'Pilih kelurahan di peta atau gunakan filter wilayah.';
}

function fitSegmentBounds() {
  if (!mapLayer || !fullBounds?.isValid()) return;
  if (state.areaId) { mapLayer.eachLayer(layer => { if (layer.feature.properties.id === state.areaId) map.fitBounds(layer.getBounds(), {padding:[25,25],maxZoom:14}); }); return; }
  const layers = [];
  mapLayer.eachLayer(layer => { if (isSchool() || layer.feature.properties.id.startsWith('3174')) layers.push(layer); });
  if (layers.length) map.fitBounds(L.featureGroup(layers).getBounds(), {padding:[15,15],animate:false});
}

function selectArea(id) {
  const area = state.options.areas.find(a => a.id === id); if (!area) return;
  state.areaId = id; $('#city-filter').value = area.city || 'Jakarta Selatan'; updateLocations(); $('#district-filter').value = area.district; updateLocations(); $('#subdistrict-filter').value = area.subdistrict;
  state.page = 1; loadData();
  mapLayer?.eachLayer(layer => { if (layer.feature.properties.id === id) map.fitBounds(layer.getBounds(), { padding: [35, 35], maxZoom: 14 }); });
}

function setView(view) {
  state.view = view; $('#explorer').className = `explorer ${view}`;
  $$('[data-view]').forEach(b => { b.classList.toggle('active', b.dataset.view === view); b.setAttribute('aria-pressed', String(b.dataset.view === view)); });
  requestAnimationFrame(() => map?.invalidateSize());
}

function navigate(route) {
  if (!['overview', 'map', 'directory', 'opportunities', 'shortlist', 'quality'].includes(route)) route = 'overview';
  state.route = route; const selected = $(`[data-nav="${route}"]`);
  $$('[data-nav]').forEach(b => { b.classList.toggle('active', b === selected); if (b === selected) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
  $('#section-label').textContent = selected.childNodes[1]?.textContent || 'Ringkasan';
  $('#overview-section').hidden = route !== 'overview';
  $('#filters-section').hidden = ['shortlist', 'quality'].includes(route);
  $('#explorer-section').hidden = !['overview', 'map', 'directory'].includes(route);
  $('#insights-section').hidden = !['overview', 'opportunities'].includes(route);
  $('#shortlist-section').hidden = route !== 'shortlist'; $('#quality-section').hidden = route !== 'quality';
  if (route === 'map') setView('map'); else if (route === 'directory') setView('table'); else if (route === 'overview') setView('split');
  if (route === 'shortlist') loadShortlist().catch(e => toast(e.message, true));
  history.replaceState(null, '', `#${route}`);
  requestAnimationFrame(() => map?.invalidateSize());
}

function renderOpportunities(data) {
  const body = $('#opportunity-table tbody'); body.replaceChildren();
  for (const item of data.items) {
    const row = el('tr'); const first = el('td'); const button = el('button', item.name, 'area-link');
    const dominant = (item.dominantCategories || []).map(c => `${c.name} (${number(c.count)})`).join(', ');
    button.title = dominant ? `Kategori dominan: ${dominant}` : 'Tidak ada kategori';
    button.append(el('small', [$('#opportunity-level').value === 'subdistrict' ? item.district : '', item.city].filter(Boolean).join(', ')));
    button.addEventListener('click', () => {
      navigate('overview');
      if ($('#opportunity-level').value === 'subdistrict') selectArea(item.id);
      else { state.areaId = ''; if (item.city) $('#city-filter').value = item.city; updateLocations(); $('#district-filter').value = item.name; $('#subdistrict-filter').value = ''; updateLocations(); state.page = 1; loadData(); }
      $('#explorer-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }); first.append(button); row.append(first);
    [item.count, isSchool() ? item.levels : item.categories, isSchool() ? item.phoneAvailable : item.employees].forEach(value => row.append(el('td', value == null ? '—' : number(value))));
    const coverage = isSchool() ? item.phoneCoverage : item.employeeCoverage;
    row.append(el('td', coverage == null ? '—' : `${number(coverage)}%`), el('td', number(item.shortlisted)));
    const index = el('td', null, 'index-cell'); index.append(el('span', number(item.index))); const bar = el('div', null, 'index-bar');
    const fill = el('i'); fill.style.width = `${item.index}%`; bar.append(fill); index.append(bar); row.append(index); body.append(row);
  }
  if (!data.items.length) { const row = el('tr'); const cell = el('td', 'Tidak ada wilayah yang dapat dibandingkan.'); cell.colSpan = 7; row.append(cell); body.append(row); }
}

async function showDetail(id) {
  const revision = state.revision, request = ++detailRequest;
  if (isSchool()) { await showSchoolDetail(id, revision, request); return; }
  try {
    const record = await api(`/api/businesses/${id}`); if (revision !== state.revision || request !== detailRequest) return; const container = $('#detail-content'); container.replaceChildren();
    container.append(el('h2', record.businessName || 'Nama tidak tersedia'), el('p', record.address || 'Alamat tidak tersedia', 'detail-subtitle'));
    const list = el('dl', null, 'detail-grid'); definitionList(list, [
      ['Kecamatan', record.district], ['Kelurahan', record.subdistrict], ['Kategori usaha', record.businessCategory], ['Jenis usaha', record.businessType],
      ['KBLI', record.kbliCode || (record.rawKbli ? `Tidak valid: ${record.rawKbli}` : null)], ['Uraian KBLI', record.kbliDescription],
      ['TKI dilaporkan', number(record.employeeCount)], ['Periode data', record.dataPeriod], ['Kecocokan wilayah', record.areaId ? 'Cocok dengan batas kelurahan' : 'Belum cocok'],
      ['Koordinat usaha', 'Tidak tersedia dari sumber'], ['Provenance', `Satu Data Jakarta · rawIndex ${record.rawIndex} · snapshot ${record.snapshotId}`]
    ]); container.append(list);
    if (record.quality.length) container.append(el('p', record.quality.join(' · '), 'detail-warning'));
    const actions = el('div', null, 'detail-actions'); actions.append(mapsLink(record));
    const add = el('button', record.shortlisted ? '✓ Dalam shortlist' : '+ Tambah shortlist', 'button primary'); add.disabled = Boolean(record.shortlisted);
    add.addEventListener('click', () => addShortlist(record, add)); actions.append(add);
    if (record.areaId) { const area = el('button', 'Lihat kelurahannya', 'button'); area.addEventListener('click', () => { $('#detail-dialog').close(); navigate('map'); selectArea(record.areaId); }); actions.append(area); }
    container.append(actions, el('p', 'Pencarian alamat di Google Maps belum memastikan lokasi usaha. Street View dapat dibuka di Google Maps jika tersedia. Registry tidak membuktikan usaha masih beroperasi atau belum menjadi nasabah Mandiri.', 'detail-footnote'));
    const source = el('a', 'Lihat sumber registry ↗', 'text-link'); source.href = record.source; source.target = '_blank'; source.rel = 'noopener noreferrer'; container.append(source);
    $('#detail-dialog').showModal();
  } catch (e) { toast(e.message, true); }
}

async function addShortlist(record, button) {
  const revision = state.revision;
  button.disabled = true;
  try {
    await api(record.npsn ? '/api/individuals/shortlist' : '/api/shortlist', {method:'POST',body:JSON.stringify(record.npsn ? {npsn:record.npsn} : {businessRef:record.id})});
    if (revision !== state.revision) return;
    button.textContent = '✓ Tersimpan'; toast('Prospek ditambahkan dengan status Identified.');
    await Promise.all([loadData(), loadShortlist()]);
  } catch(e) { if (revision === state.revision) {button.disabled=false;toast(e.message,true);} }
}

async function loadShortlist() {
  const revision = state.revision, request = ++shortlistRequest, path = shortlistPath();
  const result = await api(`${path}?page=${state.shortlistPage}&page_size=25${isSchool() ? '&program=' + state.segment : ''}`);
  if (revision !== state.revision || request !== shortlistRequest) return;
  state.shortlistTotal = result.total;
  $('#nav-shortlist').textContent = number(result.total); $('#shortlist-empty').hidden = result.total > 0;
  $('#shortlist-info').textContent = `${number(result.total)} prospek · halaman ${state.shortlistPage}`;
  $('#shortlist-prev').disabled = state.shortlistPage <= 1; $('#shortlist-next').disabled = state.shortlistPage * 25 >= result.total;
  const body = $('#shortlist-table tbody'); body.replaceChildren();
  for (const record of result.items) {
    const row = el('tr'); const title = el('td', null, 'business-name'); const name = el('strong', recordName(record));
    title.append(name, el('small', [record.subdistrict, record.district, record.city, record.program || record.dataPeriod].filter(Boolean).join(' · ')), el('small', record.address || 'Alamat tidak tersedia'));
    if (!record.inCurrentSnapshot) title.append(el('div', 'Record berubah / tidak ada pada snapshot aktif. Tinjau ulang.', 'shortlist-stale'));
    row.append(title);
    const priority = el('select'); options(priority, ['High', 'Medium', 'Low'], null, record.priority); priority.setAttribute('aria-label', `Prioritas ${recordName(record)}`);
    const status = el('select'); options(status, ['Identified', 'To Be Visited', 'Contacted', 'Follow-up', 'Qualified', 'Not Interested', 'Closed'], null, record.status); status.setAttribute('aria-label', `Status ${recordName(record)}`);
    const notes = el('textarea'); notes.value = record.notes; notes.maxLength = 4000; notes.placeholder = 'Rencana kunjungan / tindak lanjut'; notes.setAttribute('aria-label', `Catatan ${recordName(record)}`);
    for (const input of [priority, status, notes]) { const cell = el('td'); cell.append(input); row.append(cell); }
    const updated = el('td', date(record.updatedAt)); row.append(updated);
    const actionsCell = el('td'); const actions = el('div', null, 'row-actions');
    const save = smallButton('Simpan', `Simpan ${recordName(record)}`, async () => {
      save.disabled = true;
      try { const result = await api(`${path}/${record.id}`, { method: 'PATCH', body: JSON.stringify({ priority: priority.value, status: status.value, notes: notes.value }) }); if (revision !== state.revision) return; updated.textContent = date(result.updatedAt); toast('Perubahan prospek tersimpan.'); }
      catch (e) { toast(e.message, true); } finally { save.disabled = false; }
    });
    const remove = smallButton('Hapus', `Hapus ${recordName(record)} dari shortlist`, async () => {
      if (!confirm(`Hapus ${recordName(record)} dari shortlist?`)) return;
      remove.disabled = true;
      try { await api(`${path}/${record.id}`, { method: 'DELETE' }); if (revision !== state.revision) return; if (result.items.length === 1 && state.shortlistPage > 1) state.shortlistPage--; await Promise.all([loadShortlist(), loadData()]); toast('Prospek dihapus dari shortlist.'); }
      catch (e) { remove.disabled = false; toast(e.message, true); }
    }); remove.classList.add('danger'); actions.append(save, mapsLink(record), remove); actionsCell.append(actions); row.append(actionsCell); body.append(row);
  }
}

async function exportBusinesses(format) {
  const school = isSchool(); const button = $(`#export-${format}`); button.disabled = true;
  try {
    const params = selectedFilters(); params.set('format', format); params.set('sort', state.sort); params.set('direction', state.direction);
    const response = await fetch(`${school ? '/api/individuals/export' : '/api/export/businesses'}?${params}`);
    if (!response.ok) throw new Error('Ekspor gagal. Coba lagi.');
    const url = URL.createObjectURL(await response.blob()); const link = el('a'); link.href = url; link.download = `geoprospect-${school ? 'schools' : 'businesses'}.${format}`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); toast('Hasil filter berhasil diekspor.');
  } catch (e) { toast(e.message, true); } finally { button.disabled = !state.total; }
}

async function pollRefresh() {
  try {
    const status = await api('/api/data/status'); if (!isSchool()) updateStatus(status);
    if (status.refreshState === 'running') { refreshTimer = setTimeout(pollRefresh, 2500); return; }
    await loadOptions(); setupMap(await api('/api/areas/geojson?scope=jabodetabek')); await Promise.all([loadData(), loadShortlist()]);
    toast(status.lastError || 'Sinkronisasi selesai. Snapshot siap digunakan.', Boolean(status.lastError));
  } catch (e) { toast(e.message, true); $('#refresh-button').disabled = false; }
}

$('#filters-form').addEventListener('submit', e => e.preventDefault());
$('#filters-form').addEventListener('change', event => {
  if (event.target.tagName === 'INPUT' && event.target.type !== 'number') return;
  clearTimeout(searchTimer);
  if (event.target.name === 'city') { state.areaId = ''; $('#district-filter').value = ''; $('#subdistrict-filter').value = ''; updateLocations(); }
  if (event.target.name === 'district') { state.areaId = ''; $('#subdistrict-filter').value = ''; updateLocations(); }
  if (event.target.name === 'subdistrict') {
    state.areaId = ''; const matches = state.options.areas.filter(a => a.subdistrict === event.target.value && (!$('#city-filter').value || a.city === $('#city-filter').value) && (!$('#district-filter').value || a.district === $('#district-filter').value));
    if (matches.length === 1) { state.areaId = matches[0].id; $('#city-filter').value = matches[0].city || 'Jakarta Selatan'; updateLocations(); $('#district-filter').value = matches[0].district; updateLocations(); }
  }
  state.page = 1; loadData();
});
$('#filters-form').addEventListener('input', event => {
  if (event.target.tagName !== 'INPUT' || event.target.type === 'number') return;
  clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.page = 1; loadData(); }, 350);
});
$('#advanced-toggle').addEventListener('click', () => { const node = $('#advanced-filters'); node.hidden = !node.hidden; $('#advanced-toggle').setAttribute('aria-expanded', String(!node.hidden)); });
$('#clear-filters').addEventListener('click', () => {
  clearTimeout(searchTimer); map?.closePopup(); $('#filters-form').reset(); $('#period-filter').value = state.options.periods[0] || 'all';
  for (const select of $$('#advanced-filters select[multiple]')) [...select.options].forEach(o => { o.selected = false; });
  state.areaId = ''; state.page = 1; updateLocations(); loadData(); fitSegmentBounds();
});
$('#reset-map').addEventListener('click', () => { map?.closePopup(); state.areaId = ''; $('#city-filter').value = ''; $('#district-filter').value = ''; $('#subdistrict-filter').value = ''; updateLocations(); state.page = 1; loadData(); fitSegmentBounds(); });
$$('[data-view]').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
$$('[data-nav]').forEach(b => b.addEventListener('click', () => navigate(b.dataset.nav)));
window.addEventListener('hashchange', () => navigate(location.hash.slice(1)));
$('#page-size').addEventListener('change', e => { state.pageSize = Number(e.target.value); state.page = 1; loadData(); });
$('#prev-page').addEventListener('click', () => { state.page--; loadData(); });
$('#next-page').addEventListener('click', () => { state.page++; loadData(); });
$('#opportunity-level').addEventListener('change', loadData);
$('#shortlist-prev').addEventListener('click', () => { state.shortlistPage--; loadShortlist().catch(e => toast(e.message, true)); });
$('#shortlist-next').addEventListener('click', () => { state.shortlistPage++; loadShortlist().catch(e => toast(e.message, true)); });
$('#export-csv').addEventListener('click', () => exportBusinesses('csv'));
$('#export-xlsx').addEventListener('click', () => exportBusinesses('xlsx'));
$$('.close-dialog').forEach(b => b.addEventListener('click', () => { b.closest('dialog').close(); $('#admin-token').value = ''; }));
$('#refresh-button').addEventListener('click', () => {
  $('#refresh-error').textContent = ''; $('#admin-token').value = '';
  if (!state.status.refreshConfigured) { toast('Atur GEOPROSPECT_ADMIN_TOKEN di .env server, lalu restart aplikasi. Alternatif: jalankan python -m backend.ingestion.', true); return; }
  $('#refresh-dialog').showModal();
});
$('#refresh-dialog').addEventListener('close', () => { $('#admin-token').value = ''; });
$('#refresh-form').addEventListener('submit', async event => {
  event.preventDefault(); const submit = $('button[type=submit]', event.target); submit.disabled = true;
  try {
    await api('/api/data/refresh', { method: 'POST', headers: { 'X-Admin-Token': $('#admin-token').value } });
    $('#admin-token').value = ''; $('#refresh-dialog').close(); clearTimeout(refreshTimer); pollRefresh();
  } catch (e) { $('#refresh-error').textContent = e.message; } finally { submit.disabled = false; }
});

// Keep the existing business copy when returning from an individual program.
const segmentCopy = [
  ['.page-heading p', 'Eksplorasi sekolah Jabodetabek untuk akuisisi Individu'],
  ['.intro-card p', 'Pilih kelurahan untuk mengenali sekolah dan menyusun prioritas kunjungan RM.'],
  ['#explorer-section .section-heading h2', 'Jelajahi sekolah di wilayah Anda'],
  ['#explorer-section .section-heading p', 'Klik kelurahan untuk memfilter sekolah pada program yang dipilih.'],
  ['#directory-card .panel-heading strong', 'Direktori sekolah'],
  ['#insights-section .section-heading p', 'Konsentrasi sekolah untuk membantu perencanaan kunjungan.'],
  ['.categories-card .panel-heading strong', 'Komposisi jenjang'],
  ['.categories-card .method-note', 'Bentuk pendidikan dari sumber, termasuk SPK.'],
  ['#insights-section > .notice', 'Jumlah sekolah menunjukkan sasaran eksplorasi, bukan jumlah siswa atau New CIF terkonfirmasi.'],
  ['#shortlist-table th', 'Sekolah / wilayah'],
  ['#shortlist-empty', 'Shortlist masih kosong. Tambahkan sekolah dari direktori.'],
  ['#shortlist-section > .notice', 'Catat hasil tindak lanjut sekolah. Jangan menyimpan data pribadi siswa atau nasabah.'],
  ['.map-caption', 'Batas kelurahan · bukan titik lokasi sekolah'],
  ['#legend-title', 'Sekolah'],
  ['.historical-note', 'Snapshot sekolah · telepon belum diverifikasi aktif'],
  ['.quality-detail h3', 'Data Induk Sekolah · Kemendikdasmen'],
].map(([selector, school]) => ({node: $(selector), school, business: $(selector).textContent}));
const businessKpiTitles = $$('.kpi-title').map(n => n.textContent);
const businessKpiNotes = $$('.kpi small').map(n => n.textContent);
const businessOpportunityTitles = $$('#opportunity-table th').map(n => n.textContent);
const businessSource = $('.quality-detail a').href;

function renderSegment() {
  const school = isSchool();
  segmentCopy.forEach(({node, school: copy, business}) => { node.textContent = school ? copy : business; });
  $('#segment-select').value = state.segment;
  $('#segment-breadcrumb').textContent = school ? `Individu · ${segmentName()}` : 'Pebisnis';
  $('#individual-submenu').hidden = !school;
  $('#individual-menu').setAttribute('aria-expanded', String(school));
  $('#individual-menu').classList.toggle('active', school);
  $$('[data-segment]').forEach(b => { const active = b.dataset.segment === state.segment; b.classList.toggle('active', active); b.setAttribute('aria-pressed', String(active)); });
  for (const [route, title] of [['map', school ? 'Peta Sekolah' : 'Peta Bisnis'], ['directory', school ? 'Direktori Sekolah' : 'Direktori Usaha']]) $(`[data-nav="${route}"]`).childNodes[1].textContent = title;
  $('#refresh-button').hidden = school; $('#snapshot-label').hidden = !school;
  $('#school-quality-note').hidden = !school;
  $$('.quality-detail')[2].hidden = school;
  $('#map-coverage-note').hidden = school;
  const businessFields = ['period', 'category', 'kbli', 'business_type', 'name', 'address', 'kbli_description', 'min_employees', 'max_employees', 'employee_data'];
  for (const name of businessFields) { const input = $(`[name="${name}"]`, $('#filters-form')); input.disabled = school; input.closest('label').hidden = school; }
  $$('[data-school-filter]').forEach(label => { label.hidden = !school; $('select', label).disabled = !school; });
  const search = $('[name="q"]'); search.placeholder = school ? 'Cari nama sekolah, NPSN, atau alamat…' : 'Cari nama usaha, alamat, atau kategori…';
  $('.search-field .sr-only').textContent = search.placeholder;
  $('#directory-scroll').setAttribute('aria-label', `Tabel ${school ? 'sekolah' : 'usaha'}, geser untuk kolom lainnya`);
  const titles = ['Sekolah ' + segmentName(), 'Hasil filter aktif', 'Wilayah cocok', 'Telepon tercantum', 'Jenjang', 'Dalam shortlist'];
  const notes = ['Seluruh Jabodetabek · snapshot aktif', 'Sekolah, bukan siswa atau CIF', 'Terhubung ke kelurahan', 'Belum diverifikasi aktif', 'Berdasarkan bentuk pendidikan', 'Dari hasil filter aktif'];
  $$('.kpi-title').forEach((n, i) => { n.textContent = school ? titles[i] : businessKpiTitles[i]; });
  $$('.kpi small').forEach((n, i) => { n.textContent = school ? notes[i] : businessKpiNotes[i]; });
  const opportunityTitles = ['Wilayah', 'Sekolah', 'Jenjang', 'Telepon', 'Cakupan telepon', 'Shortlist', 'Indeks'];
  $$('#opportunity-table th').forEach((n, i) => { n.textContent = school ? opportunityTitles[i] : businessOpportunityTitles[i]; });
  $('#shortlist-section .section-heading p').textContent = `Seluruh prospek tersimpan, terpisah dari filter direktori. ${school ? 'Individu · ' : 'Segmen '}${segmentName()}.`;
  $('#shortlist-section .section-heading a').href = school ? `/api/individuals/shortlist/export?program=${state.segment}` : '/api/export/shortlist';
  const source = $('.quality-detail a'); source.href = school ? 'https://data.kemendikdasmen.go.id/data-induk/satpen/daftar' : businessSource;
  source.textContent = school ? 'Buka Data Induk Kemendikdasmen ↗' : 'Buka Satu Data Jakarta ↗';
  $('footer > span').textContent = school ? 'Data Source: Kemendikdasmen · Snapshot sekolah' : 'Data Source: Satu Data Jakarta | Public Business Registry';
  columns = school ? schoolColumns : businessColumns;
  visible = new Set(school ? ['schoolName', 'npsn', 'level', 'city', 'subdistrict', 'phone', 'areaId', 'actions'] : ['businessName', 'businessCategory', 'subdistrict', 'employeeCount', 'areaId', 'dataPeriod', 'actions']);
  try { const saved = JSON.parse(localStorage.getItem(`geoprospect.columns.${state.segment}`) || (!school ? localStorage.getItem('geoprospect.columns') : 'null')); if (Array.isArray(saved)) visible = new Set(saved.filter(k => columns.some(c => c[0] === k))); } catch { /* Optional preference. */ }
  visible.add(school ? 'schoolName' : 'businessName'); visible.add('actions');
  renderColumns(); navigate(state.route);
}

function renderSchoolStatus(status) {
  const snapshot = status.snapshot || {};
  $('#sync-label').textContent = date(snapshot.fetchedAt);
  $('#global-alert').hidden = Boolean(status.snapshot);
  $('#global-alert').textContent = 'Snapshot sekolah belum tersedia. Impor bundle sekolah melalui server.';
  definitionList($('#source-details'), [['Sumber', 'Data Induk Kemendikdasmen'], ['Pengambilan terakhir', date(snapshot.fetchedAt)],
    ['Diimpor', date(snapshot.importedAt)], ['Diperbarui oleh sumber', (snapshot.sourceUpdatedAt || []).map(date).join(', ') || 'Tidak tersedia'],
    ['Sekolah tersimpan · semua program', number(snapshot.storedRows)], ['Detail belum tersedia', number(snapshot.detailUnavailable)],
    ['SHA-256 snapshot', snapshot.id || 'Belum tersedia'], ['Pembaruan', 'Impor statis oleh administrator']]);
  definitionList($('#boundary-details'), [['Sumber', 'GIS DPMPTSP Jakarta dan BIG'], ['Kelurahan tersimpan', number(status.areaCount)],
    ['Edisi', status.boundaries?.edition], ['Diambil', date(status.boundaries?.fetchedAt)],
    ['Penyederhanaan untuk tampilan', status.boundaries?.displayTolerance], ['Sistem koordinat', 'WGS 84 / EPSG:4326']]);
}

function renderSchoolStats(stats) {
  $('#school-quality-note').textContent = `${segmentName()}: ${number(stats.matched)} dari ${number(stats.total)} sekolah sesuai filter cocok dengan kelurahan. ${number(stats.unmatched)} belum cocok tetap tersedia di direktori dan ekspor, tetapi tidak masuk hitungan peta atau ranking. SD/SMP termasuk SPK untuk SimPel; SMA termasuk SPK untuk GMM. Snapshot sekolah bukan data siswa atau New CIF. Nomor telepon belum diverifikasi aktif; koordinat sumber tidak dipakai untuk menebak kelurahan.`;
  for (const [id, value] of [['total', stats.snapshotTotal], ['filtered', stats.total], ['matched', stats.matched], ['employees', stats.phoneAvailable], ['categories', stats.levels], ['shortlist', stats.shortlisted]]) $(`#kpi-${id}`).textContent = state.status.snapshot ? number(value) : '—';
  $('#kpi-matched-note').textContent = `${number(stats.unmatched)} sekolah belum cocok`;
  $('#kpi-employees-note').textContent = `${number(stats.phoneMissing)} kosong · ${number(stats.phoneReview)} perlu cek · ${number(stats.phoneUnavailable)} detail belum tersedia`;
  $('#period-label').textContent = 'Snapshot statis';
  const area = state.options.areas.find(a => a.id === state.areaId);
  const label = area ? `${area.subdistrict}, ${area.city}` : $('#subdistrict-filter').value || $('#district-filter').value || $('#city-filter').value || 'Jabodetabek';
  $('#selected-area').textContent = label;
  $('#filter-summary').textContent = `${number(stats.total)} sekolah · ${segmentName()} · ${label}`;
  $('#quality-pill').textContent = stats.total ? `${Math.round(100 * stats.matched / stats.total)}% wilayah cocok` : 'Cakupan wilayah —';
  $('#quality-pill').classList.toggle('warning', stats.unmatched > 0);
  const chart = $('#category-chart'); chart.replaceChildren();
  const maximum = stats.categoryBreakdown[0]?.count || 1;
  for (const c of stats.categoryBreakdown) {
    const row = el('div', null, 'category-row'), label = el('div'), bar = el('div', null, 'category-bar'), fill = el('i');
    label.append(el('span', c.name), el('strong', number(c.count))); fill.style.width = `${100 * c.count / maximum}%`;
    bar.append(fill); row.append(label, bar); chart.append(row);
  }
  if (!stats.categoryBreakdown.length) chart.append(el('p', 'Tidak ada sekolah pada hasil filter.'));
}

async function showSchoolDetail(id, revision, request) {
  try {
    const record = await api(`/api/individuals/${encodeURIComponent(id)}`);
    if (revision !== state.revision || request !== detailRequest) return;
    const container = $('#detail-content'); container.replaceChildren(el('h2', record.schoolName), el('p', record.address || 'Alamat tidak tersedia', 'detail-subtitle'));
    const list = el('dl', null, 'detail-grid'); definitionList(list, [
      ['NPSN', record.npsn], ['Program', record.program === 'gmm' ? 'GMM' : 'Tabungan SimPel'], ['Bentuk pendidikan', record.educationForm],
      ['Status', record.schoolStatus], ['Kota/Kabupaten', record.city], ['Kecamatan', record.district], ['Kelurahan', record.subdistrict],
      ['Telepon sumber', record.phone], ['Kondisi telepon', phoneLabel(record.phoneStatus)], ['Email', record.email], ['Website sumber', record.website],
      ['Akreditasi', record.accreditation], ['Kecocokan wilayah', record.areaId ? 'Cocok dengan batas kelurahan' : 'Belum cocok; tetap tersedia di direktori'],
      ['Koordinat sumber', record.latitude == null ? 'Tidak tersedia' : `${record.latitude}, ${record.longitude}`],
      ['Detail sumber', record.detailStatus === 'available' ? 'Tersedia' : 'Belum tersedia; memakai daftar sekolah'],
      ['Diperbarui sumber', date(record.sourceUpdatedAt)], ['Diambil', date(record.fetchedAt)], ['Snapshot', record.snapshotId]
    ]); container.append(list);
    const actions = el('div', null, 'detail-actions'); actions.append(mapsLink(record));
    const add = el('button', record.shortlisted ? '✓ Dalam shortlist' : '+ Tambah shortlist', 'button primary'); add.disabled = Boolean(record.shortlisted);
    add.addEventListener('click', () => addShortlist(record, add)); actions.append(add);
    if (record.areaId) { const area = el('button', 'Lihat kelurahannya', 'button'); area.addEventListener('click', () => { $('#detail-dialog').close(); navigate('map'); selectArea(record.areaId); }); actions.append(area); }
    container.append(actions, el('p', 'Telepon belum diverifikasi aktif. Data sekolah bukan data siswa, jumlah calon nasabah, atau New CIF terkonfirmasi. Hasil pencarian Google Maps perlu diperiksa.', 'detail-footnote'));
    const source = el('a', 'Lihat detail sumber ↗', 'text-link'); source.href = `https://api.data.belajar.id/data-portal-backend/v1/master-data/satuan-pendidikan/details/${encodeURIComponent(record.npsn)}`; source.target = '_blank'; source.rel = 'noopener noreferrer'; container.append(source);
    $('#detail-dialog').showModal();
  } catch (e) { if (revision === state.revision && request === detailRequest) toast(e.message, true); }
}

async function switchSegment(segment) {
  if (!['pebisnis', 'gmm', 'simpel'].includes(segment) || segment === state.segment) return;
  const previous = new URLSearchParams(new FormData($('#filters-form')));
  state.savedFilters[state.segment] = previous;
  const next = new URLSearchParams(state.savedFilters[segment]);
  for (const key of ['city', 'district', 'subdistrict']) { if (previous.get(key)) next.set(key, previous.get(key)); else next.delete(key); }
  controller?.abort(); clearTimeout(searchTimer); ++detailRequest; ++shortlistRequest;
  const revision = ++state.revision; state.segment = segment; state.page = state.shortlistPage = 1;
  state.sort = isSchool() ? 'schoolName' : 'businessName'; state.direction = 'asc';
  state.items = []; state.areas = []; state.stats = null; state.total = 0; state.status = {};
  $('#detail-dialog').close(); $('#refresh-dialog').close();
  // Retain geography while requests are in flight, including rapid segment changes.
  $('#filters-form').reset();
  for (const key of ['city', 'district', 'subdistrict']) { $(`[name="${key}"]`).value = previous.get(key) || ''; if (key !== 'subdistrict') updateLocations(); }
  renderSegment(); renderTable(); renderMapCounts();
  $$('.kpi strong').forEach(n => { n.textContent = '—'; });
  $('#shortlist-table tbody').replaceChildren(); $('#nav-shortlist').textContent = '—';
  $('#opportunity-table tbody').replaceChildren(); $('#category-chart').replaceChildren(); $('#filter-summary').textContent = 'Memuat data…';
  $('#export-csv').disabled = $('#export-xlsx').disabled = true;
  try {
    const [status] = await Promise.all([api(isSchool() ? '/api/individuals/data/status' : '/api/data/status'), loadOptions(next)]);
    if (revision !== state.revision) return;
    updateStatus(status); await Promise.all([loadData(), loadShortlist()]);
    if (revision === state.revision) fitSegmentBounds();
  } catch (e) { if (revision === state.revision) toast(e.message, true); }
}

$$('[data-segment]').forEach(b => b.addEventListener('click', () => switchSegment(b.dataset.segment)));
$('#individual-menu').addEventListener('click', () => switchSegment('gmm'));
$('#segment-select').addEventListener('change', e => switchSegment(e.target.value));

async function start() {
  $('#segment-select').disabled = true; $$('[data-segment], #individual-menu').forEach(b => { b.disabled = true; });
  renderColumns(); navigate(location.hash.slice(1) || 'overview');
  $('#business-table tbody').replaceChildren(...Array.from({ length: 6 }, () => { const row = el('tr'); const cell = el('td', null, 'skeleton-row'); row.append(cell); return row; }));
  try {
    const [status, geo] = await Promise.all([api('/api/data/status'), api('/api/areas/geojson?scope=jabodetabek'), loadOptions()]);
    updateStatus(status); setupMap(geo); await Promise.all([loadData(), loadShortlist()]);
    if (status.refreshState === 'running') pollRefresh();
  } catch (e) { $('#global-alert').textContent = `Aplikasi gagal memuat: ${e.message}`; $('#global-alert').hidden = false; $('#map-loading').textContent = 'Peta belum tersedia. Muat ulang halaman setelah backend siap.'; toast(e.message, true); }
  finally { $('#segment-select').disabled = false; $$('[data-segment], #individual-menu').forEach(b => { b.disabled = false; }); }
}
start();
