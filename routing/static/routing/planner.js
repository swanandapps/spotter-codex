(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const plan = $('plan-data') ? JSON.parse($('plan-data').textContent) : null;
  const form = JSON.parse($('form-data').textContent);
  let requestMeta = plan?.meta;
  try {
    const previous = JSON.parse(sessionStorage.getItem('spotter-planning-request') || 'null');
    sessionStorage.removeItem('spotter-planning-request');
    if (previous && previous.query === location.search.slice(1) && Date.now() - previous.at < 120000) requestMeta = previous.meta;
  } catch {}

  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const phone = () => matchMedia('(max-width: 820px)').matches;
  const esc = t => String(t).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const money = n => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  const miles = n => Math.round(n).toLocaleString('en-US');
  const pump = n => { const p = Number(n).toFixed(3); return `$${p.slice(0,-1)}<sup class="tenth">${p.slice(-1)}</sup>`; };
  const drive = hours => { const mins = Math.round(hours * 60), h = Math.floor(mins / 60), m = mins % 60; return h ? `${h}h${m ? ` ${m}m` : ''}` : `${m} min`; };
  const title = name => name.split(' ').map(w => /[0-9&#]/.test(w) || w.length <= 2 ? w : w[0] + w.slice(1).toLowerCase()).join(' ');
  const isCoords = text => /^\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*$/.test(text || '');
  const inputs = {start: $('start'), finish: $('finish')};
  const notes = {start: $('start-note'), finish: $('finish-note')};
  const coords = {start:null, finish:null}, pins = {start:null, finish:null};
  const pinSequence = {start:0, finish:0}, searchControls = {};
  const replan = $('replan'), tripForm = $('trip-form'), status = $('form-status');
  const submitButton = tripForm.querySelector('button[type=submit]');
  const submitText = plan ? 'Update fuel plan' : 'Find my fuel stops';
  let map = null, activePick = null, dirty = false, selectedStop = null, lastPinEvent = null, busy = false;
  const planning = () => !busy && (!plan || replan.open);
  const watchedFields = ['start', 'finish', 'fuel', 'stop-penalty', 'driver-cost'];
  const formValues = () => watchedFields.map(id => $(id).value.trim()).join('|');
  const initialValues = formValues();
  const motion = ms => reduceMotion ? 0 : ms;
  const hint = text => { $('map-hint').textContent = text; $('map-hint').hidden = !text; };

  // Native dialogs provide Escape, focus containment, and focus restoration on mobile.
  function setupDialog(dialog, closeButton) {
    $(closeButton).addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', e => {
      if (e.target !== dialog) return;
      const r = dialog.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
    });
  }
  setupDialog($('about-dialog'), 'about-close');
  setupDialog($('details-dialog'), 'details-close');
  $('how-it-works').addEventListener('click', () => $('about-dialog').showModal());
  function closeCard() {
    if ($('details-dialog').open) $('details-dialog').close();
  }
  function showCard(html) {
    const dialog = $('details-dialog');
    const alreadyOpen = dialog.open;
    $('details-body').innerHTML = html;
    if (!dialog.open) phone() ? dialog.showModal() : dialog.show();
    dialog.scrollTop = 0;
    if (alreadyOpen) {
      const heading = dialog.querySelector('h3');
      if (heading) { heading.tabIndex = -1; heading.focus({preventScroll:true}); }
    }
  }

  // The route and stop list remain usable if WebGL or the map library is unavailable.
  try {
    if (!window.maplibregl) throw new Error('Map library unavailable');
    const raster = (tiles, attribution) => ({type:'raster', tiles, tileSize:256, attribution, maxzoom:19});
    const esri = service => `https://server.arcgisonline.com/ArcGIS/rest/services/${service}/MapServer/tile/{z}/{y}/{x}`;
    map = new maplibregl.Map({
      container:'map', center:[-98.5,39.5], zoom:3.5, maxPitch:60,
      attributionControl:{compact:true},
      style:{version:8, sources:{
        street:raster(['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], '© OpenStreetMap contributors'),
        satellite:raster([esri('World_Imagery')], 'Imagery © Esri, Maxar, Earthstar Geographics'),
        labels:raster([esri('Reference/World_Boundaries_and_Places')], 'Labels © Esri'),
        terrain:raster([esri('World_Topo_Map')], 'Topographic map © Esri'),
      }, layers:[
        {id:'street', type:'raster', source:'street', paint:{'raster-saturation':-.75, 'raster-contrast':-.08}},
        {id:'satellite', type:'raster', source:'satellite', layout:{visibility:'none'}},
        {id:'labels', type:'raster', source:'labels', layout:{visibility:'none'}},
        {id:'terrain', type:'raster', source:'terrain', layout:{visibility:'none'}},
      ]},
    });
    map.addControl(new maplibregl.NavigationControl({visualizePitch:true}), 'top-right');
    map.addControl(new maplibregl.ScaleControl({unit:'imperial'}), 'bottom-right');
  } catch {
    $('map-unavailable').hidden = false;
    if ($('map-welcome')) $('map-welcome').hidden = true;
    $$('.map-toolbar button, .pick-pin').forEach(button => { button.disabled = true; });
  }
  const layerGroups = {street:['street'], satellite:['satellite','labels'], terrain:['terrain']};
  function setView(name) {
    if (!map || !layerGroups[name]) return;
    if (!map.getLayer('street')) { map.once('load', () => setView(name)); return; }
    ['street','satellite','labels','terrain'].forEach(id => map.setLayoutProperty(id, 'visibility', layerGroups[name].includes(id) ? 'visible' : 'none'));
    $$('.views button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === name)));
    try { localStorage.setItem('spotter-codex-map-view', name); } catch {}
  }
  $$('.views button').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  if (map) map.on('load', () => {
    try { setView(localStorage.getItem('spotter-codex-map-view') || 'street'); } catch { setView('street'); }
    if (plan) drawRoute(); else { fitPins(); refreshHint(); }
  });

  // Endpoint coordinates are held separately from their labels and map markers.
  function refreshHint() {
    $$('.pick-pin').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.pick === activePick)));
    if (!map) return;
    map.getCanvas().style.cursor = activePick ? 'crosshair' : '';
    if (activePick) hint(`Click the map to set your ${activePick === 'start' ? 'start' : 'destination'}. Press Escape to cancel.`);
    else if (!planning()) hint('');
    else if (!coords.start && !inputs.start.value) hint('Click the map to set a start, or search for a city.');
    else if (!coords.finish && !inputs.finish.value) hint('Click the map to set your destination.');
    else hint('Drag either pin to adjust your trip.');
    if ($('map-welcome')) $('map-welcome').hidden = !!(coords.start || coords.finish || activePick);
  }
  function markDirty() {
    dirty = !!plan && formValues() !== initialValues;
    document.body.classList.toggle('stale', dirty);
    if ($('stale')) $('stale').hidden = !dirty;
    if (dirty) closeCard();
    if (map) {
      ['route-road','route-outline','route-casing','alternate-lines','recommended-line'].forEach(id => {
        if (map.getLayer(id)) map.setPaintProperty(id, 'line-opacity', dirty ? .4 : 1);
      });
      if (map.getLayer('route-halo')) map.setPaintProperty('route-halo', 'line-opacity', dirty ? .2 : .5);
    }
    $$('.route-badge').forEach(button => { button.disabled = dirty || busy; });
    $$('.route-option, .stop, #brief-link, #copy-trip, [data-restore]').forEach(el => el.setAttribute('aria-disabled', String(dirty)));
    if ($('copy-trip')) $('copy-trip').disabled = dirty;
  }
  function clearPin(which) {
    pinSequence[which]++;
    if (pins[which]) { pins[which].remove(); pins[which] = null; }
    coords[which] = null;
    notes[which].textContent = '';
    notes[which].classList.remove('invalid');
    inputs[which].setCustomValidity('');
  }
  async function nameSpot(which, point) {
    const sequence = ++pinSequence[which];
    notes[which].textContent = 'Finding the nearest town…';
    notes[which].classList.remove('invalid');
    inputs[which].setCustomValidity('');
    try {
      const response = await fetch(`/api/places/nearest/?lat=${point.lat}&lon=${point.lng}`);
      const data = await response.json();
      if (sequence !== pinSequence[which]) return;
      if (response.status === 400) {
        notes[which].textContent = 'Choose a point within the USA.';
        notes[which].classList.add('invalid');
        inputs[which].setCustomValidity('Choose a point within the USA.');
      } else notes[which].textContent = response.ok && data.label ? `Near ${data.label}` : 'Map pin selected';
    } catch { if (sequence === pinSequence[which]) notes[which].textContent = 'Map pin selected'; }
  }
  function setPin(which, point, fromMap = false) {
    coords[which] = {lng:point.lng, lat:point.lat};
    if (map) {
      if (!pins[which]) {
        const visual = document.createElement('div');
        visual.className = 'pin pin-end'; visual.style.background = which === 'start' ? '#174e3c' : '#477ba9';
        visual.textContent = which === 'start' ? 'A' : 'B';
        visual.setAttribute('aria-label', which === 'start' ? 'Start pin' : 'Destination pin');
        const shell = document.createElement('div'); shell.appendChild(visual);
        pins[which] = new maplibregl.Marker({element:shell, draggable:planning()}).setLngLat(point).addTo(map);
        pins[which].on('dragend', () => { replan.open = true; setPin(which, pins[which].getLngLat(), true); });
      } else pins[which].setLngLat(point);
      pins[which].getElement().firstChild.classList.toggle('draggable', planning());
    }
    if (fromMap) {
      searchControls[which]?.cancel();
      inputs[which].value = `${point.lat.toFixed(5)},${point.lng.toFixed(5)}`;
      nameSpot(which, point); markDirty();
    }
    refreshHint();
  }
  function fitPins() {
    if (!map) return;
    const points = Object.values(coords).filter(Boolean);
    if (points.length === 2) map.fitBounds(new maplibregl.LngLatBounds(points[0],points[0]).extend(points[1]), {padding:75, maxZoom:12, duration:motion(600)});
    else if (points.length === 1) map.easeTo({center:points[0], zoom:Math.max(6,map.getZoom()), duration:motion(500)});
  }
  $$('.pick-pin').forEach(button => button.addEventListener('click', () => {
    activePick = activePick === button.dataset.pick ? null : button.dataset.pick;
    closeCard(); refreshHint();
    if (phone() && activePick) document.querySelector('.map-stage').scrollIntoView({block:'start', behavior:reduceMotion ? 'auto' : 'smooth'});
  }));
  $$('.clear-field').forEach(button => button.addEventListener('click', () => {
    const which = button.dataset.clear;
    searchControls[which].cancel(); clearPin(which); inputs[which].value = '';
    markDirty(); refreshHint(); inputs[which].focus();
  }));
  $('swap-pins').addEventListener('click', () => {
    const previous = Object.fromEntries(['start','finish'].map(key => [key, {value:inputs[key].value, point:coords[key], note:notes[key].textContent}]));
    ['start','finish'].forEach(key => { searchControls[key].cancel(); clearPin(key); });
    [['start','finish'],['finish','start']].forEach(([to,from]) => {
      inputs[to].value = previous[from].value;
      if (previous[from].point) { setPin(to,previous[from].point); if (isCoords(inputs[to].value)) nameSpot(to,previous[from].point); }
      else notes[to].textContent = previous[from].note;
    });
    activePick = null; markDirty(); refreshHint();
  });
  replan.addEventListener('toggle', () => {
    if (!planning()) activePick = null;
    Object.values(pins).forEach(pin => { if (pin) { pin.setDraggable(planning()); pin.getElement().firstChild.classList.toggle('draggable', planning()); } });
    refreshHint();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { activePick = null; closeCard(); refreshHint(); } });
  if (map) map.on('click', e => {
    if (!planning() || e.originalEvent.target.closest('.maplibregl-marker')) return;
    if (!activePick && map.getLayer('alt-hit') && map.queryRenderedFeatures(e.point,{layers:['alt-hit']}).length) return;
    const which = activePick || (!inputs.start.value ? 'start' : !inputs.finish.value ? 'finish' : null);
    if (!which) return;
    lastPinEvent = e.originalEvent;
    activePick = null; setPin(which,e.lngLat,true); refreshHint();
  });

  // Invalidate at each keystroke, clear, or blur so old responses cannot reopen a list.
  function autocomplete(which) {
    const input = inputs[which], list = $(`${which}-list`);
    let items = [], active = -1, timer = null, controller = null, sequence = 0;
    const close = () => { list.hidden = true; input.setAttribute('aria-expanded','false'); input.removeAttribute('aria-activedescendant'); active = -1; };
    const cancel = () => { sequence++; clearTimeout(timer); controller?.abort(); items = []; close(); };
    searchControls[which] = {cancel};
    const message = text => { list.innerHTML = `<li class="empty" role="option" aria-disabled="true">${esc(text)}</li>`; list.hidden = false; input.setAttribute('aria-expanded','true'); };
    const choose = i => {
      const item = items[i]; if (!item) return;
      cancel(); clearPin(which); input.value = item.label;
      setPin(which,{lng:item.lon,lat:item.lat}); activePick = null;
      markDirty(); fitPins(); refreshHint();
      if (which === 'start' && !inputs.finish.value) inputs.finish.focus();
    };
    const render = () => {
      list.innerHTML = items.map((item,i) => `<li role="option" id="${which}-opt-${i}" aria-selected="${i === active}">${esc(item.label)}</li>`).join('');
      list.hidden = !items.length; input.setAttribute('aria-expanded',String(!!items.length));
      if (active >= 0) input.setAttribute('aria-activedescendant',`${which}-opt-${active}`); else input.removeAttribute('aria-activedescendant');
      [...list.children].forEach((item,i) => { item.addEventListener('pointerdown',e => e.preventDefault()); item.addEventListener('click',() => choose(i)); });
      if (active >= 0) list.children[active].scrollIntoView({block:'nearest'});
    };
    input.addEventListener('input', () => {
      cancel(); clearPin(which); markDirty(); refreshHint();
      const query = input.value.trim(), mine = sequence;
      if (query.length < 2 || isCoords(query)) return;
      timer = setTimeout(async () => {
        controller = new AbortController();
        try {
          const response = await fetch(`/api/places/?q=${encodeURIComponent(query)}`, {signal:controller.signal});
          if (!response.ok) throw new Error('Suggestions unavailable');
          const data = await response.json();
          if (mine !== sequence || document.activeElement !== input) return;
          items = data; active = -1;
          items.length ? render() : message('No matching city. Try City, ST, an address, or set a map pin.');
        } catch (error) { if (error.name !== 'AbortError' && mine === sequence && document.activeElement === input) message('Search is unavailable. You can still enter a city and state.'); }
      },150);
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') { cancel(); return; }
      if (list.hidden || !items.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % items.length; render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = active < 0 ? items.length - 1 : (active - 1 + items.length) % items.length; render(); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); choose(active); }
    });
    input.addEventListener('blur', () => {
      cancel();
      if (isCoords(input.value) && !coords[which]) {
        const [lat,lng] = input.value.split(',').map(Number);
        if (Math.abs(lat) <= 90 && Math.abs(lng) <= 180) { setPin(which,{lat,lng}); nameSpot(which,{lat,lng}); }
      }
    });
  }
  autocomplete('start'); autocomplete('finish');

  function updatePreferences() {
    const stop = Number($('stop-penalty').value), hourly = Number($('driver-cost').value);
    const preset = stop === 0 && hourly === 0 ? 'fuel' : stop === 10 && hourly === 30 ? 'balanced' : 'custom';
    const available = Math.max(0, (Number($('fuel').value) - Number(plan?.vehicle.reserve_gallons ?? 5)) * Number(plan?.vehicle.mpg ?? 10));
    $('fuel-reach').textContent = Number($('fuel').value) < Number(plan?.vehicle.reserve_gallons ?? 5) ? 'Below the reserve. Add fuel before departure.' : `About ${miles(available)} miles available before reaching the reserve.`;
    $('preference-label').textContent = preset === 'fuel' ? 'Lowest fuel cost' : preset === 'balanced' ? 'Balanced' : 'Custom';
    $$('[data-preset]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.preset === preset)));
  }
  $$('[data-preset]').forEach(button => button.addEventListener('click', () => {
    $('stop-penalty').value = button.dataset.preset === 'fuel' ? 0 : 10;
    $('driver-cost').value = button.dataset.preset === 'fuel' ? 0 : 30;
    updatePreferences(); markDirty();
  }));
  ['stop-penalty','driver-cost','fuel'].forEach(id => $(id).addEventListener('input', () => { updatePreferences(); markDirty(); }));
  updatePreferences();
  $('discard-edits')?.addEventListener('click', () => {
    if (busy) return;
    tripForm.reset();
    ['start','finish'].forEach(which => {
      searchControls[which].cancel(); clearPin(which);
      setPin(which,{lat:plan[which].lat,lng:plan[which].lon});
      if (isCoords(form[which])) notes[which].textContent = plan[which].query;
    });
    $('request-error').hidden = true;
    status.textContent = '';
    if ($('action-status')) $('action-status').textContent = 'Edits discarded. Your displayed plan is ready.';
    updatePreferences(); markDirty(); replan.open = false;
    replan.querySelector('summary').focus({preventScroll:true}); fitRoute();
  });
  $$('[data-jump]').forEach(button => button.addEventListener('click', () => {
    const target = document.querySelector('.' + button.dataset.jump);
    target.scrollIntoView({block:'start',behavior:reduceMotion ? 'auto' : 'smooth'});
    target.tabIndex = -1; target.focus({preventScroll:true});
  }));
  function setBusy(value) {
    busy = value;
    submitButton.disabled = value;
    submitButton.querySelector('span').textContent = value ? 'Planning your trip…' : submitText;
    tripForm.setAttribute('aria-busy', String(value));
    tripForm.querySelectorAll('input, button').forEach(el => { el.disabled = value || (!map && el.matches('.pick-pin')); });
    Object.values(pins).forEach(pin => pin?.setDraggable(planning()));
    document.body.classList.toggle('request-pending', value);
  }
  function errorMessage(data) {
    if (data.error) return data.error;
    const labels = {start:'Start',finish:'Destination',start_fuel_gallons:'Starting fuel',stop_penalty:'Cost per stop',driver_hourly_cost:'Driving cost',avoid_station_ids:'Excluded stations',route_option:'Route option'};
    return Object.entries(data).map(([key,value]) => `${labels[key] || key}: ${Array.isArray(value) ? value.join(' ') : JSON.stringify(value)}`).join(' ');
  }
  async function requestPlan(params, message = 'Finding routes and comparing fuel stops…', returnToMap = false) {
    if (busy) return;
    $('request-error').hidden = true;
    closeCard();
    Object.values(searchControls).forEach(control => control.cancel());
    activePick = null; setBusy(true); refreshHint();
    status.textContent = message;
    if ($('action-status')) $('action-status').textContent = message;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 65000);
    try {
      const response = await fetch('/api/route/?' + params, {signal:controller.signal, headers:{Accept:'application/json'}});
      let data;
      try { data = await response.json(); }
      catch { throw new Error('The planning service returned an unreadable response. Please try again.'); }
      if (!response.ok) throw new Error(errorMessage(data));
      try { sessionStorage.setItem('spotter-planning-request', JSON.stringify({query:params.toString(),meta:data.meta,at:Date.now()})); } catch {}
      location.assign('/api/route/map/?' + params + (returnToMap && phone() ? '#route-map' : ''));
    } catch (error) {
      const message = error.name === 'AbortError' ? 'The route request took too long. Your trip details are still here; try again.' : error instanceof TypeError ? 'Could not connect to the planner. Check your connection and try again.' : error.message;
      $('request-error').innerHTML = `<strong>We couldn't update the plan.</strong><p>${esc(message)}</p><span>${plan ? dirty ? 'Your previous plan is still shown. Discard edits to use it again.' : 'Your previous plan is unchanged. Try again or edit the trip.' : 'Your trip details are saved below.'}</span>`;
      $('request-error').hidden = false;
      replan.open = true;
      $('request-error').scrollIntoView({block:'nearest',behavior:reduceMotion ? 'auto' : 'smooth'});
      $('request-error').focus({preventScroll:true});
      status.textContent = '';
      if ($('action-status')) $('action-status').textContent = '';
    } finally {
      clearTimeout(timer); setBusy(false); refreshHint();
    }
  }
  tripForm.addEventListener('submit', e => {
    e.preventDefault();
    if (busy) return;
    const params = new URLSearchParams(new FormData(tripForm));
    requestPlan(params);
  });
  addEventListener('pageshow', () => { setBusy(false); status.textContent = ''; });
  const examples = {
    chicago:[{label:'Chicago, IL',lat:41.8781,lng:-87.6298},{label:'Denver, CO',lat:39.7392,lng:-104.9903}],
    coast:[{label:'New York, NY',lat:40.7128,lng:-74.006},{label:'Los Angeles, CA',lat:34.0522,lng:-118.2437}],
  };
  $$('[data-example]').forEach(button => button.addEventListener('click', () => {
    examples[button.dataset.example].forEach((item,i) => { const which = i ? 'finish' : 'start'; searchControls[which].cancel(); clearPin(which); inputs[which].value = item.label; setPin(which,item); });
    activePick = null; fitPins(); refreshHint();
    status.textContent = 'Trip filled in. Select Find my fuel stops to continue.';
    submitButton.focus({preventScroll:true});
  }));
  $('whole-route').addEventListener('click', () => {
    closeCard();
    if (!map) return;
    if (plan) fitRoute();
    else if (coords.start || coords.finish) fitPins();
    else map.easeTo({center:[-98.5,39.5],zoom:3.5,bearing:0,pitch:0,duration:motion(600)});
  });

  if (!plan) {
    ['start','finish'].forEach(which => {
      if (isCoords(form[which])) {
        const [lat,lng] = form[which].split(',').map(Number);
        if (Math.abs(lat) <= 90 && Math.abs(lng) <= 180) { setPin(which,{lat,lng}); nameSpot(which,{lat,lng}); }
      }
    });
    return;
  }

  // All totals and comparisons come from the same API response as the map.
  const stops = plan.fuel_stops, vehicle = plan.vehicle, summary = plan.summary, total = plan.route.distance_miles;
  const legs = plan.legs || [];
  const excluded = plan.excluded_stations || [];
  const options = plan.route_options;
  const routeFeature = plan.map.features.find(feature => feature.properties.kind === 'route');
  const shown = which => isCoords(form[which]) ? plan[which].query : form[which];
  $('from').textContent = shown('start'); $('to').textContent = shown('finish');
  $('facts').innerHTML = [[`${miles(total)} mi`,'Distance','distance','route'],[drive(plan.route.duration_hours),'Driving time','time','clock'],[stops.length,stops.length === 1 ? 'Fuel stop' : 'Fuel stops','fuel','fuel']].map(([value,label,tone,symbol]) => `<div class="fact-${tone}"><dt>${label}</dt><dd>${icon(symbol)}<span>${value}</span></dd></div>`).join('');
  $('amount').textContent = money(summary.total_fuel_cost);
  $('cost-note').textContent = summary.total_gallons_purchased ? `${summary.total_gallons_purchased.toFixed(1)} gallons to buy · starting fuel excluded` : 'Your starting fuel covers the trip, including the reserve.';
  $('stop-count').textContent = `${stops.length} planned`;
  $('chart-caption').textContent = `${vehicle.starting_fuel_gallons} gal at start · ${vehicle.reserve_gallons} gal reserve`;
  $('assumption').textContent = `${vehicle.range_miles} mi range at ${vehicle.mpg} mpg. Starting with ${vehicle.starting_fuel_gallons} gal, retaining at least ${vehicle.reserve_gallons} gal, and arriving with ${summary.fuel_at_arrival_gallons.toFixed(1)} gal. Stop driving times are estimates ${plan.data_quality.stop_driving_times === 'estimated_from_road_segments' ? 'based on road segments' : 'based on distance'}. `;
  const arrivalLeg = legs.at(-1);
  $('arrival').innerHTML = `${arrivalLeg ? `<div class="arrival-leg">${miles(arrivalLeg.distance_miles)} mi · about ${drive(arrivalLeg.driving_hours)} ${stops.length ? 'from the last stop' : 'from departure'}</div>` : ''}<span class="endpoint-dot finish-dot"></span><div><strong>Arrive in ${esc(shown('finish'))}</strong><p>${summary.fuel_at_arrival_gallons.toFixed(1)} gal remaining · ${miles(summary.fuel_at_arrival_gallons * vehicle.mpg)} mi of range</p></div>`;
  if (plan.savings && Math.abs(plan.savings.saved_usd) >= .01) {
    const saving = plan.savings;
    $('savings').innerHTML = saving.saved_usd > 0 ? `<b>${money(saving.saved_usd)} less</b> than the quarter-tank simulation` : `${money(-saving.saved_usd)} more than the quarter-tank simulation`;
    $('savings').title = `${saving.compared_with}. Baseline: ${money(saving.habit_fuel_cost)} at ${saving.habit_stops} stops on this route.`;
    $('savings').classList.toggle('above-baseline', saving.saved_usd < 0);
    $('savings').hidden = false;
  }
  $('performance').innerHTML = [
    ['Planning response',`${requestMeta.elapsed_ms} ms`],['Plan cache',requestMeta.cache_hit ? 'Hit' : 'Miss'],['Road cache',requestMeta.road_cache_hit ? 'Hit' : 'Miss'],
    ['Routing API calls',requestMeta.routing_api_calls],['Geocoding API calls',requestMeta.geocoding_api_calls],
    ['Routes compared',plan.meta.routes_compared],['Stations considered',plan.meta.stations_on_route_considered],
  ].map(([key,value]) => `<div><dt>${key}</dt><dd>${esc(value)}</dd></div>`).join('');
  const apiUrl = new URL(location.href); apiUrl.pathname = '/api/route/'; $('api-link').href = apiUrl.href;
  const briefUrl = new URL(location.href); briefUrl.pathname = '/api/route/brief/'; $('brief-link').href = briefUrl.href;
  $('brief-link').addEventListener('click', e => { if (dirty || busy) e.preventDefault(); });
  $('copy-trip').addEventListener('click', async () => {
    if (dirty || busy) return;
    try { await navigator.clipboard.writeText(location.href); $('action-status').textContent = 'Trip link copied. It opens this route with your current preferences.'; }
    catch { $('action-status').textContent = 'Copy the address from your browser to share this trip.'; }
  });
  if (excluded.length) {
    $('excluded-stations').hidden = false;
    $('excluded-stations').innerHTML = `<div class="excluded-heading"><span>${excluded.length} station${excluded.length === 1 ? '' : 's'} excluded</span><button type="button" data-restore="all">Restore all</button></div>` + excluded.map(station => `<button type="button" class="excluded-chip" data-restore="${station.id}" aria-label="Restore ${esc(title(station.name))}">${esc(title(station.name))} · ${esc(station.city)}, ${esc(station.state)} <span aria-hidden="true">×</span></button>`).join('');
    $$('[data-restore]').forEach(button => button.addEventListener('click', () => {
      if (dirty || busy) return;
      const params = new URLSearchParams(location.search); params.delete('avoid_station_ids'); params.delete('route_option');
      excluded.filter(s => button.dataset.restore !== 'all' && s.id !== Number(button.dataset.restore)).forEach(s => params.append('avoid_station_ids',s.id));
      requestPlan(params, 'Restoring stations and comparing fuel stops…');
    }));
  }
  const optionUrl = option => { const u = new URL(location.href); option === options.recommended ? u.searchParams.delete('route_option') : u.searchParams.set('route_option',option); return u.pathname + u.search; };
  const routeName = option => option.option === options.recommended ? 'Recommended' : option.fastest ? 'Fastest alternative' : 'Alternative route';
  if (options) {
    $('routes').innerHTML = `<div class="section-heading"><h2>${options.options.length > 1 ? 'Compare routes' : 'Route selection'}</h2><span>${options.options.length} available</span></div><p class="section-description">Prices below are fuel purchases only.</p>` + options.options.map(option => {
      const selected = option.option === options.viewing, recommended = option.option === options.recommended;
      return `<a class="route-option ${recommended ? 'recommended' : 'alternative'}${selected ? ' selected' : ''}" href="${esc(optionUrl(option.option))}" ${selected ? 'aria-current="true"' : ''}><div class="option-heading"><span><span class="option-route-icon" aria-hidden="true">${recommended ? icon('star') : '<i class="route-dash"></i>'}</span>${routeName(option)}</span><span class="option-price">${money(option.total_fuel_cost)}</span></div><div class="option-meta"><span>${miles(option.distance_miles)} mi</span><span>${drive(option.duration_hours)}</span><span>${option.fuel_stops} stop${option.fuel_stops === 1 ? '' : 's'}</span></div></a>`;
    }).join('') + `<p class="comparison-note">Ranked by ${esc(options.compared_on)}.${options.infeasible_routes ? ` ${options.infeasible_routes} route(s) excluded because fuel was out of reach.` : ''}</p>`;
    $('routes').hidden = false;
    $$('.route-option').forEach(link => link.addEventListener('click', e => { if (dirty || busy || link.hasAttribute('aria-current')) e.preventDefault(); }));
  }
  const priceTone = stop => Math.abs(stop.cents_vs_route_average) < .5 ? 'average' : stop.cents_vs_route_average < 0 ? 'below' : 'above';
  const priceComparison = stop => priceTone(stop) === 'average' ? 'Near route average' : `${Math.abs(stop.cents_vs_route_average).toFixed(0)}¢/gal ${priceTone(stop)} route average`;
  $('stop-list').innerHTML = stops.length ? stops.map((stop,i) => `<li class="stop-item" data-i="${i}">${legs[i] ? `<div class="leg-heading"><span>${icon('route')} ${miles(legs[i].distance_miles)} mi · about ${drive(legs[i].driving_hours)}</span><small>${i === 0 ? 'From departure' : 'From previous stop'}</small></div>` : ''}<button class="stop" type="button" data-stop="${i}" aria-label="View stop ${stop.stop_number}: ${esc(title(stop.station.name))}"><span class="diamond">${stop.stop_number}</span><span class="name">${esc(title(stop.station.name))}</span><span class="price">${pump(stop.station.price_per_gallon)}<small>per gallon</small></span><span class="place">${esc(stop.station.city)}, ${esc(stop.station.state)}</span><span class="exit">Mile ${miles(stop.route_mile)} · about ${drive(stop.drive_hours_from_start)} into trip</span><span class="price-comparison price-${priceTone(stop)}">${priceComparison(stop)}</span><span class="buy"><span>Buy ${stop.gallons_purchased.toFixed(2)} gal</span><b>${money(stop.cost)}</b></span></button><div class="stop-foot"><span>${stop.fuel_on_arrival_gallons.toFixed(1)} gal on arrival</span><span>View stop ${icon('arrow')}</span></div></li>`).join('') : '<li class="none">No fuel stops needed. Your starting fuel covers this trip while preserving the reserve.</li>';

  const directions = stop => 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(`${stop.station.name}, ${stop.station.address}, ${stop.station.city}, ${stop.station.state}`);
  function stopCard(stop, index) {
    const incoming = legs[index], onward = legs[index + 1];
    const rangeOnArrival = stop.fuel_on_arrival_gallons * vehicle.mpg;
    const reserveBuffer = Math.max(0, (stop.fuel_on_arrival_gallons - vehicle.reserve_gallons) * vehicle.mpg);
    const atReserve = reserveBuffer < 1;
    return `<div class="card stop-card">
      <div class="eyebrow">Fuel stop ${stop.stop_number} · Mile ${miles(stop.route_mile)}</div>
      <h3>${esc(title(stop.station.name))}</h3>
      <div class="meta">${esc(stop.station.address)}<br>${esc(stop.station.city)}, ${esc(stop.station.state)}</div>
      ${stop.chain ? `<span class="badge">${esc(stop.chain)}</span>` : ''}
      <section class="stop-travel" aria-label="Travel to this fuel stop">
        <h4>From trip start</h4>
        <dl>
          <div class="travel-time"><dt>${icon('clock')} Estimated drive</dt><dd>${drive(stop.drive_hours_from_start)}</dd></div>
          <div class="travel-distance"><dt>${icon('route')} Route distance</dt><dd>${miles(stop.route_mile)} <small>mi</small></dd></div>
        </dl>
        ${index > 0 && incoming ? `<p class="incoming-leg"><span>From fuel stop ${index}</span><b>${drive(incoming.driving_hours)} · ${miles(incoming.distance_miles)} mi</b></p>` : ''}
        <p class="travel-note">From your planned start, not live GPS. Excludes breaks and live traffic.</p>
      </section>
      <div class="priceline"><span class="big">${pump(stop.station.price_per_gallon)} <small>/ gal</small></span><span class="vs price-${priceTone(stop)}">${priceComparison(stop)}</span></div>
      <table>
        <tr class="purchase-row"><td>Purchase</td><td>${stop.gallons_purchased.toFixed(2)} gal · ${money(stop.cost)}</td></tr>
        <tr><td>Fuel on arrival</td><td>${stop.fuel_on_arrival_gallons.toFixed(1)} gal<span class="cell-note">About ${miles(rangeOnArrival)} mi of range</span></td></tr>
        <tr><td>Fuel after purchase</td><td>${stop.fuel_on_departure_gallons.toFixed(1)} gal</td></tr>
        <tr><td>${stop.next_is_destination ? 'Then to destination' : 'Then to next stop'}</td><td>${onward ? `${drive(onward.driving_hours)} · ` : ''}${miles(stop.miles_to_next)} mi</td></tr>
      </table>
      <p class="reserve-buffer${atReserve ? ' at-reserve' : ''}">${icon('fuel')}<span>${atReserve ? `Arrive at your planned ${vehicle.reserve_gallons} gal reserve.` : `About <b>${miles(reserveBuffer)} mi above your ${vehicle.reserve_gallons} gal reserve</b> on arrival.`}</span></p>
      <details class="stop-context"><summary>Why this stop &amp; planning notes ${icon('chevron')}</summary>
        <p class="why">${esc(stop.reason)}</p>
        <span class="approx">Approximate town location. Station detours and truck access aren't verified; check the station address before navigating.</span>
      </details>
      <span class="access-note">Approximate station location · verify truck access.</span>
      <div class="links"><a href="${directions(stop)}" target="_blank" rel="noopener">Check directions ${icon('link')}</a><a href="${esc(stop.navigation_url)}" target="_blank" rel="noopener">Photos & reviews ↗</a></div>
      <button class="avoid-station" type="button" data-avoid="${stop.station.id}" ${excluded.length >= 20 ? 'disabled' : ''}>Avoid this station and recalculate ${icon('arrow')}</button>
      <p class="avoid-note">${excluded.length >= 20 ? 'You can exclude up to 20 stations. Restore one to add another.' : 'Useful if a stop is unsuitable or unavailable.'}</p>
      <div class="pager"><button type="button" data-stop="${index-1}" ${index === 0 ? 'disabled' : ''}>← Previous</button><span>${index+1} of ${stops.length} stops</span><button type="button" data-stop="${index+1}" ${index === stops.length-1 ? 'disabled' : ''}>Next →</button></div>
    </div>`;
  }
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-avoid]');
    if (!button || button.disabled || dirty || busy) return;
    const params = new URLSearchParams(location.search); params.delete('route_option');
    params.append('avoid_station_ids',button.dataset.avoid);
    requestPlan(params, 'Excluding this station and comparing the remaining stops…');
  });
  ['start','finish'].forEach(which => { setPin(which,{lat:plan[which].lat,lng:plan[which].lon}); if (isCoords(form[which])) notes[which].textContent = plan[which].query; });
  const stopMarkers = stops.map((stop,i) => {
    if (!map) return null;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'pin pin-stop'; button.textContent = stop.stop_number;
    button.dataset.stop = i; button.setAttribute('aria-label',`View stop ${stop.stop_number}: ${title(stop.station.name)}`);
    const shell = document.createElement('div'); shell.appendChild(button);
    return new maplibregl.Marker({element:shell}).setLngLat([stop.station.lon,stop.station.lat]).addTo(map);
  });
  document.addEventListener('click', e => { const button = e.target.closest('[data-stop]'); if (button && !button.disabled) selectStop(Number(button.dataset.stop)); });
  function highlight(index) {
    $$('.stop-item').forEach(el => el.classList.toggle('active',Number(el.dataset.i) === index));
    $$('.pin-stop').forEach((el,i) => el.classList.toggle('active',i === index));
    $$('.profile .marker').forEach(el => el.classList.toggle('active',Number(el.dataset.stop) === index));
  }
  function selectStop(index) {
    if (dirty || busy || !stops[index]) return;
    selectedStop = index; highlight(index);
    const stop = stops[index], point = {lng:stop.station.lon,lat:stop.station.lat};
    if (map) {
      const offset = phone() ? [0,-40] : [-Math.min(170,map.getContainer().clientWidth * .2),-45];
      map.flyTo({center:point,zoom:10,offset,duration:motion(700)});
    }
    showCard(stopCard(stop,index),point);
    if (!phone()) document.querySelector(`.stop-item[data-i="${index}"]`)?.scrollIntoView({block:'nearest',behavior:reduceMotion ? 'auto' : 'smooth'});
  }
  function fitRoute() {
    if (!map || !routeFeature) return;
    const points = plan.map.features.filter(f => ['route','alternate_route'].includes(f.properties.kind)).flatMap(f => f.geometry.coordinates);
    const bounds = points.reduce((b,p) => b.extend(p),new maplibregl.LngLatBounds(points[0],points[0]));
    map.fitBounds(bounds,{padding:{top:100,bottom:phone() ? (options?.options.length > 1 ? 275 : 200) : 220,left:45,right:45},bearing:0,pitch:0,maxZoom:12,duration:motion(600)});
  }
  function selectRoute(option) {
    if (dirty || busy || activePick || option.option === options.viewing) return;
    const params = new URLSearchParams(new URL(optionUrl(option.option), location.origin).search);
    requestPlan(params, 'Switching route…', true);
  }

  // Keep badge positions tied to route geometry so they stay put when selection changes.
  // Geometry is already in the response; this does not request another route.
  function routeBadgePoint(feature, features) {
    const coords = feature.geometry.coordinates;
    const xScale = Math.cos(plan.start.lat * Math.PI / 180);
    const projected = point => [point[0] * xScale, point[1]];
    const distance = (a,b) => Math.hypot(a[0]-b[0],a[1]-b[1]);
    const points = coords.map(projected), cumulative = [0];
    points.slice(1).forEach((point,i) => cumulative.push(cumulative[i] + distance(points[i],point)));
    const length = cumulative.at(-1);
    if (!length) return coords[0];
    const otherLines = features.filter(f => f !== feature).map(f => {
      const step = Math.max(1,Math.floor(f.geometry.coordinates.length/120));
      return f.geometry.coordinates.filter((_,i,a) => i % step === 0 || i === a.length-1).map(projected);
    });
    const segmentDistance = (p,a,b) => {
      const dx=b[0]-a[0], dy=b[1]-a[1], denominator=dx*dx+dy*dy;
      const t=denominator ? Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/denominator)) : 0;
      return distance(p,[a[0]+t*dx,a[1]+t*dy]);
    };
    const target = feature.properties.recommended ? .42 : .65;
    let best = coords[Math.floor(coords.length/2)], bestScore = -Infinity;
    for (let fraction=.2; fraction<=.8; fraction+=.025) {
      const at = Math.min(coords.length-1,cumulative.findIndex(value => value >= length*fraction));
      const point = points[at];
      let separation = length;
      otherLines.forEach(line => { for (let i=1;i<line.length;i++) separation=Math.min(separation,segmentDistance(point,line[i-1],line[i])); });
      const score = separation - Math.abs(fraction-target)*length*.025;
      if (score > bestScore) { bestScore=score; best=coords[at]; }
    }
    return best;
  }

  function drawRoute() {
    const round = {'line-cap':'round','line-join':'round'};
    const width = (a,b) => ['interpolate',['linear'],['zoom'],3,a,10,b];
    const viewingRecommended = !options || options.viewing === options.recommended;
    const green = '#14784f', blue = '#316bc1';
    const alternates = plan.map.features.filter(f => f.properties.kind === 'alternate_route').map(f => ({...f,properties:{...f.properties,recommended:f.properties.option === options?.recommended}}));
    const selected = {...routeFeature,properties:{...routeFeature.properties,option:options?.viewing,recommended:viewingRecommended}};
    const features = [selected,...alternates];
    map.addSource('alternates',{type:'geojson',data:{type:'FeatureCollection',features:alternates}});
    map.addLayer({id:'alt-casing',type:'line',source:'alternates',layout:round,paint:{'line-color':'#fff','line-width':width(8,12)}});
    map.addLayer({id:'alternate-lines',type:'line',source:'alternates',filter:['==',['get','recommended'],false],layout:round,paint:{'line-color':blue,'line-width':width(3.5,5.5),'line-dasharray':[2,1.4]}});
    map.addLayer({id:'recommended-casing',type:'line',source:'alternates',filter:['==',['get','recommended'],true],layout:round,paint:{'line-color':'#fff','line-width':width(9,13)}});
    map.addLayer({id:'recommended-line',type:'line',source:'alternates',filter:['==',['get','recommended'],true],layout:round,paint:{'line-color':green,'line-width':width(3.5,5.5)}});
    // Selection has its own continuous outline, independent of recommendation color.
    // Draw it last so the chosen route stays visible where routes share the same road.
    map.addSource('route',{type:'geojson',data:selected});
    map.addLayer({id:'route-halo',type:'line',source:'route',layout:round,paint:{'line-color':'#76c9dc','line-width':width(22,29),'line-opacity':.5}});
    map.addLayer({id:'route-outline',type:'line',source:'route',layout:round,paint:{'line-color':'#163644','line-width':width(13,19)}});
    map.addLayer({id:'route-casing',type:'line',source:'route',layout:round,paint:{'line-color':'#fff','line-width':width(9,14)}});
    map.addLayer({id:'route-road',type:'line',source:'route',layout:round,paint:{'line-color':viewingRecommended ? green : blue,'line-width':width(5,9),...(viewingRecommended ? {} : {'line-dasharray':[2,1.4]})}});
    map.addLayer({id:'alt-hit',type:'line',source:'alternates',layout:round,paint:{'line-color':'#000','line-width':18,'line-opacity':0}});
    document.querySelector('.map-legend').innerHTML = `<span><i class="legend-route"></i>${icon('star')} Recommended</span>${alternates.length ? '<span><i class="legend-alternative"></i>Alternative</span>' : ''}<span><i class="legend-stop"></i>Fuel stop</span><span class="legend-note">Approximate station locations</span>`;
    if (options && features.length > 1) features.forEach(feature => {
      const option = options.options.find(o => o.option === feature.properties.option);
      if (!option) return;
      const recommended = feature.properties.recommended, selected = option.option === options.viewing;
      const button = document.createElement('button');
      button.type = 'button'; button.className = `route-badge ${recommended ? 'recommended' : 'alternative'}${selected ? ' selected' : ''}`;
      button.setAttribute('aria-label',`${selected ? 'Selected' : 'Select'} ${routeName(option).toLowerCase()}, ${drive(option.duration_hours)}, ${money(option.total_fuel_cost)} in fuel.`);
      button.setAttribute('aria-pressed', String(selected));
      button.title = `${routeName(option)} · ${drive(option.duration_hours)} · ${money(option.total_fuel_cost)} in fuel`;
      button.innerHTML = `${recommended ? icon('star') : ''}<span>${drive(option.duration_hours)}</span>`;
      button.addEventListener('click', e => { e.stopPropagation(); selectRoute(option); });
      const shell = document.createElement('div'); shell.className=`route-badge-anchor ${recommended ? 'above-route' : 'below-route'}`; shell.appendChild(button);
      new maplibregl.Marker({element:shell,anchor:recommended ? 'bottom' : 'top',offset:[0,recommended ? -25 : 25]}).setLngLat(routeBadgePoint(feature,features)).addTo(map);
    });
    fitRoute();
    map.on('mouseenter','alt-hit',() => { if (!activePick) map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave','alt-hit',refreshHint);
    map.on('click','alt-hit',e => {
      if (activePick || dirty || busy || !options || e.originalEvent === lastPinEvent) return;
      if (map.queryRenderedFeatures(e.point, {layers:['route-outline']}).length) return;
      const option = options.options.find(o => o.option === Number(e.features[0].properties.option));
      if (option) selectRoute(option);
    });
  }

  // Fuel gauge: the vertical segments are purchases; the downward lines are usage.
  const figure = $('profile');
  function drawProfile() {
    const W = figure.clientWidth, H = figure.clientHeight;
    if (W < 10 || H < 10) return;
    const L = 50, R = 20, T = 25, B = 23, tank = vehicle.tank_gallons;
    const x = m => L + m / total * (W-L-R), y = g => T + (1-g/tank)*(H-T-B);
    const points = [[0,vehicle.starting_fuel_gallons]];
    stops.forEach(stop => points.push([stop.route_mile,stop.fuel_on_arrival_gallons],[stop.route_mile,stop.fuel_on_arrival_gallons+stop.gallons_purchased]));
    points.push([total,summary.fuel_at_arrival_gallons]);
    const line = points.map(([m,g],i) => `${i ? 'L' : 'M'}${x(m).toFixed(1)},${y(g).toFixed(1)}`).join('');
    let lastLabel = -Infinity;
    const markers = stops.map((stop,i) => {
      const px = x(stop.route_mile), py = y(stop.fuel_on_arrival_gallons+stop.gallons_purchased), label = px-lastLabel > 52;
      if (label) lastLabel = px;
      return `<g class="marker" data-stop="${i}" tabindex="0" role="button" aria-label="View fuel stop ${stop.stop_number}"><line class="refill" x1="${px}" x2="${px}" y1="${y(stop.fuel_on_arrival_gallons)}" y2="${py}"/><rect x="${px-4}" y="${py-4}" width="8" height="8" rx="2" fill="#fff" stroke="#174e3c" stroke-width="1.5"/>${label ? `<text class="price-tag" x="${px}" y="13" text-anchor="middle">$${stop.station.price_per_gallon.toFixed(2)}</text>` : ''}</g>`;
    }).join('');
    figure.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Fuel in tank; select a refill to inspect the stop"><line class="grid" x1="${L}" x2="${W-R}" y1="${y(tank)}" y2="${y(tank)}"/><line class="grid" x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}"/><text class="axis" x="${L-8}" y="${y(tank)+4}" text-anchor="end">${tank} gal</text><line class="reserve" x1="${L}" x2="${W-R}" y1="${y(vehicle.reserve_gallons)}" y2="${y(vehicle.reserve_gallons)}"/><text class="reserve-label" x="${L-8}" y="${y(vehicle.reserve_gallons)+4}" text-anchor="end">${vehicle.reserve_gallons} gal</text><text class="axis" x="${L}" y="${H-4}">Start</text><text class="axis" x="${W-R}" y="${H-4}" text-anchor="end">${miles(total)} mi</text><path class="area" d="${line}L${x(total)},${y(0)}L${x(0)},${y(0)}Z"/><path class="level" d="${line}"/>${markers}</svg>`;
    figure.querySelectorAll('[data-stop]').forEach(el => el.addEventListener('keydown',e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectStop(Number(el.dataset.stop)); } }));
    if (selectedStop !== null) highlight(selectedStop);
  }
  $('toggle-profile').addEventListener('click', () => {
    figure.hidden = !figure.hidden;
    $('toggle-profile').setAttribute('aria-expanded',String(!figure.hidden));
    document.body.classList.toggle('chart-collapsed',figure.hidden);
    if (!figure.hidden) drawProfile();
  });
  new ResizeObserver(drawProfile).observe(figure);
  drawProfile();
})();
