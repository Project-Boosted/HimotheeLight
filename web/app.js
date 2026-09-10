const state = {
  config: null,
  status: null,
  selectedDeviceId: null,
  mode: 'idle',
  metadata: {},
  connected: new Set(),
  autodarts: null,
  autodartsSettingsLoaded: false,
  selectedTriggerId: null,
  triggerReferenceDeviceId: null,
  triggerDirty: false,
  triggerSaveInFlight: false,
  selectedSceneId: null,
  sceneReferenceDeviceId: null,
  profileImportBundle: null,
  profileImportPreview: null,
};

const $ = (id) => document.getElementById(id);
const pages = ['dashboard','devices','hue','modes','autodarts','triggers','scenes','profiles','logs'];

async function api(path, options = {}) {
  const init = { headers: { 'Content-Type': 'application/json' }, ...options };
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const res = await fetch(path, init);
  let payload = {};
  try { payload = await res.json(); } catch (_) {}
  if (!res.ok && res.status !== 207) throw new Error(payload.error || `Request failed (${res.status})`);
  return payload;
}

function toast(message, kind='success') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  $('toastHost').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[c]));
}

function showPage(name) {
  pages.forEach(page => {
    $(`page-${page}`).classList.toggle('active', page === name);
    document.querySelector(`.nav-item[data-page="${page}"]`)?.classList.toggle('active', page === name);
  });
  const titles = {dashboard:'Dashboard', devices:'WLED Devices', hue:'Philips Hue', modes:'Idle & Active', autodarts:'Autodarts', triggers:'Triggers', scenes:'Scenes & Groups', profiles:'Profiles', logs:'Diagnostics'};
  $('pageTitle').textContent = titles[name] || 'HimotheeLight';
  if (name === 'logs') refreshLogs();
  if (name === 'hue') renderHue();
  if (name === 'modes') renderModeEditor();
  if (name === 'autodarts') refreshAutodarts(true);
  if (name === 'triggers') { refreshTriggerStatus(true); ensureTriggerMetadata(); }
  if (name === 'scenes') { renderScenes(); ensureSceneMetadata(); }
  if (name === 'profiles') renderProfiles();
}

function lightingEndpoints() {
  const wled = (state.config?.devices || []).map(d => ({...d, _type:'wled'}));
  const hue = (state.config?.hue_lights || []).map(d => ({...d, _type:'hue'}));
  return [...wled, ...hue];
}

function currentDevice() {
  return lightingEndpoints().find(d => d.id === state.selectedDeviceId) || null;
}

async function loadAll() {
  try {
    const [statusPayload, configPayload, autodartsPayload] = await Promise.all([
      api('/api/status'), api('/api/config'), api('/api/autodarts/status')
    ]);
    state.status = statusPayload;
    state.config = configPayload.config;
    state.autodarts = autodartsPayload;
    if (!state.selectedDeviceId && lightingEndpoints().length) state.selectedDeviceId = lightingEndpoints()[0].id;
    if (!state.selectedTriggerId && state.config.triggers?.length) state.selectedTriggerId = state.config.triggers[0].id;
    if (!state.triggerReferenceDeviceId && state.config.devices.length) state.triggerReferenceDeviceId = state.config.devices[0].id;
    if (!state.sceneReferenceDeviceId && state.config.devices.length) state.sceneReferenceDeviceId = state.config.devices[0].id;
    if (!state.selectedSceneId && state.config.scenes?.length) state.selectedSceneId = state.config.scenes[0].id;
    if (state.selectedDeviceId && !currentDevice()) state.selectedDeviceId = lightingEndpoints()[0]?.id || null;
    $('versionLabel').textContent = `v${state.status.version} • Hue Integration`;
    $('backendText').textContent = 'Backend online';
    $('backendDot').className = 'dot online';
    populateAutodartsSettings();
    renderAll();
  } catch (err) {
    $('backendText').textContent = 'Backend offline';
    $('backendDot').className = 'dot offline';
    toast(err.message, 'error');
  }
}

function renderAll() {
  renderDashboard();
  renderDevices();
  renderHue();
  renderModeDeviceSelect();
  renderModeEditor();
  renderAutodarts();
  renderTriggers();
  renderScenes();
  renderProfiles();
}

function lightingSnapshot() {
  return state.autodarts?.lighting || state.status?.lighting || {mode:'idle', source:'startup', reason:'Startup'};
}

function autodartsSnapshot() {
  return state.autodarts?.status || state.status?.autodarts || {};
}

function gameBridgeSnapshot() {
  return state.autodarts?.game_bridge || state.status?.game_bridge || {};
}

function renderDashboard() {
  const devices = lightingEndpoints();
  const ad = autodartsSnapshot();
  const lighting = lightingSnapshot();
  $('deviceCount').textContent = devices.length;
  $('currentMode').textContent = String(lighting.mode || 'idle').toUpperCase();
  $('heroModeDot').className = `dot ${lighting.mode === 'active' ? 'online' : 'idle-dot'}`;
  $('modeReason').textContent = `${lighting.source || 'state'} • ${lighting.reason || 'No reason'}${lighting.applied_at ? ` • ${lighting.applied_at}` : ''}`;

  const streamUp = !!ad.transport_connected;
  $('dashAutodartsState').textContent = streamUp ? 'CONNECTED' : ad.connecting ? 'CONNECTING' : 'OFFLINE';
  $('dashAutodartsState').className = streamUp ? 'good-strong' : ad.connecting ? 'warn-strong' : 'muted-strong';
  $('dashAutodartsDetail').textContent = `${ad.running ? 'Detection running' : 'Detection stopped'}${ad.status ? ` • ${ad.status}` : ''}`;
  $('dashStream').textContent = streamUp ? 'Connected' : ad.connecting ? 'Connecting' : 'Offline';
  $('dashRunning').textContent = ad.running ? 'Running' : 'Stopped';
  $('dashBoardStatus').textContent = ad.status || 'Unknown';
  $('dashLastEvent').textContent = ad.event || '—';
  $('dashLastDart').textContent = ad.last_throw?.name || '—';
  $('dashLastDartScore').textContent = ad.last_throw ? `${ad.last_throw.score} points` : 'waiting';
  const activeTrigger = state.autodarts?.triggers?.runtime?.active || state.status?.triggers?.runtime?.active || null;
  $('dashTriggerState').textContent = activeTrigger ? String(activeTrigger.name || 'TRIGGER').toUpperCase() : 'NONE';
  $('dashTriggerDetail').textContent = activeTrigger ? (activeTrigger.remaining_ms == null ? `looping${activeTrigger.choreography ? ` • step ${activeTrigger.step || 1} • repeat ${activeTrigger.repeat || 1}` : ''}` : `${(Math.max(0, Number(activeTrigger.remaining_ms))/1000).toFixed(1)}s remaining${activeTrigger.choreography ? ` • step ${activeTrigger.step || 1}` : ''}`) : 'base lighting';

  $('sideAutodartsDot').className = `dot ${streamUp ? 'online' : 'offline'}`;
  $('sideAutodartsText').textContent = streamUp ? 'Autodarts connected' : ad.connecting ? 'Autodarts connecting' : 'Autodarts offline';

  if (!devices.length) {
    $('dashboardDevices').className = 'device-overview empty-state';
    $('dashboardDevices').textContent = 'No WLED or Hue lights configured yet.';
    return;
  }
  $('dashboardDevices').className = 'device-overview';
  $('dashboardDevices').innerHTML = devices.map(device => {
    const probe = device.last_probe || {};
    const online = state.connected.has(device.id) || probe.ok;
    return `<div class="overview-row">
      <div class="device-main">
        <span class="dot ${online ? 'online' : 'offline'}"></span>
        <div><strong>${device._type === 'hue' ? 'Hue' : 'WLED'} — ${escapeHtml(device.name)}</strong><small>${device._type === 'hue' ? 'Philips Hue' : escapeHtml(device.host)}</small></div>
      </div>
      <div class="mini-status">${device._type === 'hue' ? 'HUE' : escapeHtml(probe.version || 'Not checked')}</div>
    </div>`;
  }).join('');
}

function renderDevices() {
  const host = $('deviceList');
  const devices = state.config?.devices || [];
  if (!devices.length) {
    host.className = 'device-list empty-state';
    host.textContent = 'No devices configured.';
    return;
  }
  host.className = 'device-list';
  host.innerHTML = devices.map(device => {
    const probe = device.last_probe || {};
    const online = state.connected.has(device.id) || probe.ok;
    return `<div class="device-row" data-device="${device.id}">
      <div class="device-main">
        <div class="device-icon">W</div>
        <div>
          <strong>${escapeHtml(device.name)} ${device.enabled ? '' : '<span class="mini-status">disabled</span>'}</strong>
          <small>${escapeHtml(device.host)} • ${escapeHtml(probe.version || 'not checked')} ${probe.led_count ? `• ${probe.led_count} LEDs` : ''}</small>
        </div>
      </div>
      <div class="device-actions">
        <span class="mini-status"><span class="dot ${online ? 'online' : 'offline'}"></span> ${online ? 'known' : 'offline'}</span>
        <button class="btn ghost" data-action="test">Test</button>
        <button class="btn ghost" data-action="configure">Lighting</button>
        <button class="btn secondary" data-action="toggle">${device.enabled ? 'Disable' : 'Enable'}</button>
        <button class="btn danger" data-action="remove">Remove</button>
      </div>
    </div>`;
  }).join('');
}

function renderHue() {
  if (!$('hueBridgeList')) return;
  const bridges = state.config?.hue_bridges || [];
  const lights = state.config?.hue_lights || [];
  $('hueBridgeList').className = bridges.length ? 'device-list' : 'device-list empty-state';
  $('hueBridgeList').innerHTML = bridges.length ? bridges.map(b => `<div class="device-row" data-hue-bridge="${escapeHtml(b.id)}"><div class="device-main"><div class="device-icon">H</div><div><strong>${escapeHtml(b.name)}</strong><small>${escapeHtml(b.host)} • ${b.paired ? 'paired' : 'not paired'} • ${lights.filter(l=>l.bridge_id===b.id).length} light(s)</small></div></div><div class="device-actions"><button class="btn ghost" data-hue-bridge-action="test">Test</button><button class="btn ghost" data-hue-bridge-action="sync">Sync Lights</button><button class="btn secondary" data-hue-bridge-action="toggle">${b.enabled===false?'Enable':'Disable'}</button><button class="btn danger" data-hue-bridge-action="remove">Remove</button></div></div>`).join('') : 'No Hue Bridges paired.';
  $('hueLightList').className = lights.length ? 'device-list' : 'device-list empty-state';
  $('hueLightList').innerHTML = lights.length ? lights.map(l => `<div class="device-row" data-hue-light="${escapeHtml(l.id)}"><div class="device-main"><div class="device-icon">💡</div><div><strong>${escapeHtml(l.name)} ${l.enabled===false?'<span class="mini-status">disabled</span>':''}</strong><small>${l.supports_color?'Colour':'White'}${l.supports_dimming?' • dimming':''} • ${escapeHtml(l.archetype || 'light')}</small></div></div><div class="device-actions"><button class="btn ghost" data-hue-light-action="test">Test</button><button class="btn ghost" data-hue-light-action="configure">Lighting</button><button class="btn secondary" data-hue-light-action="toggle">${l.enabled===false?'Enable':'Disable'}</button></div></div>`).join('') : 'Pair a Hue Bridge to discover lights.';
}

function renderModeDeviceSelect() {
  const select = $('modeDeviceSelect');
  const devices = lightingEndpoints();
  select.innerHTML = devices.map(d => `<option value="${d.id}">${d._type === 'hue' ? 'Hue' : 'WLED'} — ${escapeHtml(d.name)}</option>`).join('');
  if (state.selectedDeviceId) select.value = state.selectedDeviceId;
}

function setSelectOptions(select, items, selectedValue, emptyLabel) {
  const list = [...items];
  const selectedPresent = list.some(item => String(item.id) === String(selectedValue));
  let html = emptyLabel ? `<option value="">${escapeHtml(emptyLabel)}</option>` : '';
  if (!selectedPresent && selectedValue !== null && selectedValue !== undefined && selectedValue !== '') {
    html += `<option value="${escapeHtml(selectedValue)}">Unavailable ID ${escapeHtml(selectedValue)}</option>`;
  }
  html += list.map(item => `<option value="${item.id}">${item.id} — ${escapeHtml(item.name)}</option>`).join('');
  select.innerHTML = html;
  if (selectedValue !== null && selectedValue !== undefined) select.value = String(selectedValue);
}

function rgbToHex(rgb) {
  const safe = Array.isArray(rgb) ? rgb : [0,0,0];
  return '#' + [0,1,2].map(i => Math.max(0, Math.min(255, Number(safe[i] || 0))).toString(16).padStart(2,'0')).join('');
}

function hexToRgb(hex) {
  const value = hex.replace('#','');
  return [parseInt(value.slice(0,2),16), parseInt(value.slice(2,4),16), parseInt(value.slice(4,6),16)];
}

function updateRangeOutputs() {
  $('brightnessOut').textContent = $('brightness').value;
  $('speedOut').textContent = $('speed').value;
  $('intensityOut').textContent = $('intensity').value;
  $('presetBrightnessOut').textContent = $('presetBrightness').value;
}

function renderModeEditor() {
  const device = currentDevice();
  $('noModeDevice').classList.toggle('hidden', !!device);
  $('modeEditor').classList.toggle('hidden', !device);
  if (!device) return;

  const mode = device.modes?.[state.mode] || {};
  const isHue = device._type === 'hue';
  const meta = isHue ? null : (state.metadata[device.id] || null);
  $('modeSourceChoice').classList.toggle('hidden', isHue);
  $('hueModeNote').classList.toggle('hidden', !isHue);
  document.querySelectorAll('#directFields .wled-only').forEach(el => el.classList.toggle('hidden', isHue));
  $('modeOn').checked = mode.on !== false;
  document.querySelectorAll('input[name="source"]').forEach(radio => radio.checked = radio.value === (mode.source || 'direct'));
  $('brightness').value = mode.brightness ?? 128;
  $('presetBrightness').value = mode.brightness ?? 128;
  $('speed').value = mode.speed ?? 128;
  $('intensity').value = mode.intensity ?? 128;
  $('transitionMs').value = mode.transition_ms ?? 300;
  $('presetTransitionMs').value = mode.transition_ms ?? 300;
  $('color1').value = rgbToHex(mode.colors?.[0]);
  $('color2').value = rgbToHex(mode.colors?.[1]);
  $('color3').value = rgbToHex(mode.colors?.[2]);

  setSelectOptions($('effectSelect'), meta?.effects || [], mode.effect_id ?? 0);
  setSelectOptions($('paletteSelect'), meta?.palettes || [], mode.palette_id ?? 0);
  setSelectOptions($('presetSelect'), meta?.presets || [], mode.preset_id ?? 1, meta?.presets?.length ? null : 'No presets found');
  setSelectOptions($('segmentSelect'), meta?.segments || [], mode.segment_id, 'Main segment');

  const source = isHue ? 'direct' : (mode.source || 'direct');
  $('directFields').classList.toggle('hidden', source !== 'direct');
  $('presetFields').classList.toggle('hidden', isHue || source !== 'preset');
  updateRangeOutputs();

  if (state.mode === 'idle') {
    $('modeEyebrow').textContent = 'IDLE MODE';
    $('modeHeading').textContent = 'Out-of-game lighting';
    $('modeDescription').textContent = 'Used when Autodarts detection is stopped.';
  } else {
    $('modeEyebrow').textContent = 'ACTIVE MODE';
    $('modeHeading').textContent = 'In-game base lighting';
    $('modeDescription').textContent = 'Used while Autodarts detection is running. Trigger effects temporarily override this and then return here.';
  }

  $('metadataSummary').innerHTML = isHue
    ? `<strong>${escapeHtml(device.name)}</strong> • Philips Hue • ${device.supports_color ? 'colour' : 'white/dimming'}${device.supports_dimming ? ' • dimming' : ''}`
    : (meta ? `<strong>${escapeHtml(meta.name)}</strong> • WLED ${escapeHtml(meta.version)} • ${meta.effects.length} effects • ${meta.palettes.length} palettes • ${meta.presets.length} presets` : 'WLED metadata not loaded yet. Click “Reload WLED”.');
  $('reloadMetadata').textContent = isHue ? 'Test Hue Light' : 'Reload WLED';
}

function collectMode() {
  const source = currentDevice()?._type === 'hue' ? 'direct' : (document.querySelector('input[name="source"]:checked')?.value || 'direct');
  return {
    source,
    on: $('modeOn').checked,
    brightness: Number(source === 'preset' ? $('presetBrightness').value : $('brightness').value),
    effect_id: Number($('effectSelect').value || 0),
    palette_id: Number($('paletteSelect').value || 0),
    speed: Number($('speed').value || 128),
    intensity: Number($('intensity').value || 128),
    segment_id: $('segmentSelect').value === '' ? null : Number($('segmentSelect').value),
    transition_ms: Number(source === 'preset' ? $('presetTransitionMs').value : $('transitionMs').value || 0),
    colors: [hexToRgb($('color1').value), hexToRgb($('color2').value), hexToRgb($('color3').value)],
    preset_id: Number($('presetSelect').value || 1),
  };
}

async function refreshMetadata(deviceId, quiet=false) {
  if (!deviceId) return;
  try {
    const result = await api(`/api/devices/${deviceId}/metadata`);
    state.metadata[deviceId] = result.metadata;
    state.connected.add(deviceId);
    if (!quiet) toast(`Connected to ${result.metadata.name} — WLED ${result.metadata.version}`);
    const cfg = await api('/api/config');
    state.config = cfg.config;
    renderAll();
  } catch (err) {
    state.connected.delete(deviceId);
    renderAll();
    if (!quiet) toast(err.message, 'error');
    throw err;
  }
}

async function refreshSystemStatus() {
  try {
    state.status = await api('/api/status');
    renderDashboard();
  } catch (_) {}
}

async function applyAll(mode) {
  try {
    const result = await api(`/api/modes/${mode}/apply-all`, { method:'POST', body:{} });
    const [cfg, ad] = await Promise.all([api('/api/config'), api('/api/autodarts/status')]);
    state.config = cfg.config;
    state.autodarts = ad;
    renderAll();
    const failed = result.results?.filter(r => !r.ok).length || 0;
    toast(failed ? `${mode} applied with ${failed} failure(s)` : `${mode === 'idle' ? 'Idle' : 'Active'} mode applied to all enabled lighting endpoints`, failed ? 'error':'success');
  } catch (err) { toast(err.message, 'error'); }
}

async function saveMode(apply=false) {
  const device = currentDevice();
  if (!device) return;
  try {
    const mode = collectMode();
    const base = device._type === 'hue' ? '/api/hue/lights' : '/api/devices';
    await api(`${base}/${device.id}/modes/${state.mode}${apply ? '/apply':''}`, { method:'POST', body:{mode} });
    const cfg = await api('/api/config');
    state.config = cfg.config;
    renderAll();
    toast(`${state.mode === 'idle' ? 'Idle' : 'Active'} mode ${apply ? 'saved and applied' : 'saved'}`);
  } catch (err) { toast(err.message, 'error'); }
}

function populateAutodartsSettings(force=false) {
  if (state.autodartsSettingsLoaded && !force) return;
  const settings = state.autodarts?.settings || state.config?.autodarts || {};
  $('autodartsHost').value = settings.host || '127.0.0.1';
  $('autodartsPort').value = settings.port ?? 3180;
  $('autodartsReconnect').value = settings.reconnect_seconds ?? 2;
  $('autodartsBridgeTimeout').value = settings.bridge_timeout_seconds ?? 10;
  $('autodartsEnabled').checked = settings.enabled !== false;
  $('autodartsAutoConnect').checked = settings.auto_connect !== false;
  $('autodartsAutoLighting').checked = settings.auto_lighting !== false;
  $('autodartsIdleDisconnect').checked = settings.idle_on_disconnect !== false;
  $('autodartsTakeoutFlow').checked = settings.takeout_flow_enabled !== false;
  $('autodartsTakeoutAfterDarts').value = settings.takeout_after_darts ?? 3;
  $('autodartsTakeoutFlashMs').value = settings.takeout_finish_flash_ms ?? 500;
  $('autodartsInactivityIdle').checked = settings.inactivity_idle_enabled !== false;
  $('autodartsInactivitySeconds').value = settings.inactivity_idle_seconds ?? 60;
  state.autodartsSettingsLoaded = true;
}

function collectAutodartsSettings() {
  return {
    host: $('autodartsHost').value.trim() || '127.0.0.1',
    port: Number($('autodartsPort').value || 3180),
    reconnect_seconds: Number($('autodartsReconnect').value || 2),
    bridge_timeout_seconds: Number($('autodartsBridgeTimeout').value || 10),
    enabled: $('autodartsEnabled').checked,
    auto_connect: $('autodartsAutoConnect').checked,
    auto_lighting: $('autodartsAutoLighting').checked,
    idle_on_disconnect: $('autodartsIdleDisconnect').checked,
    takeout_flow_enabled: $('autodartsTakeoutFlow').checked,
    takeout_after_darts: 3,
    takeout_finish_flash_ms: Number($('autodartsTakeoutFlashMs').value || 500),
    inactivity_idle_enabled: $('autodartsInactivityIdle').checked,
    inactivity_idle_seconds: Number($('autodartsInactivitySeconds').value || 60),
  };
}

function renderAutodarts() {
  if (!state.autodarts) return;
  const ad = state.autodarts.status || {};
  const lighting = state.autodarts.lighting || lightingSnapshot();
  const streamUp = !!ad.transport_connected;
  const badge = $('autodartsConnectionBadge');
  badge.textContent = streamUp ? 'CONNECTED' : ad.connecting ? 'CONNECTING' : 'OFFLINE';
  badge.className = `connection-badge ${streamUp ? 'online' : ad.connecting ? 'connecting' : 'offline'}`;

  $('adStreamState').textContent = streamUp ? 'CONNECTED' : ad.connecting ? 'CONNECTING' : 'OFFLINE';
  $('adStreamDetail').textContent = streamUp ? `Since ${ad.connected_at || 'now'}` : ad.desired_connected ? 'Automatic reconnect enabled' : 'Connection stopped';
  $('adBoardConnected').textContent = ad.board_connected ? 'CONNECTED' : 'NOT READY';
  $('adRunning').textContent = ad.running ? 'RUNNING' : 'STOPPED';
  $('adStatus').textContent = ad.status || 'Unknown';
  $('adLightingMode').textContent = String(lighting.mode || 'idle').toUpperCase();
  $('adLightingReason').textContent = lighting.reason || 'Waiting for state';
  $('adLastDart').textContent = ad.last_throw?.name || '—';
  $('adLastScore').textContent = ad.last_throw ? `${ad.last_throw.score} points • ${ad.last_throw.bed || 'segment'}` : 'No dart received yet';
  $('adLastEvent').textContent = ad.event || '—';
  $('adNumThrows').textContent = ad.num_throws ?? 0;
  $('adLastMessage').textContent = ad.last_message_at || '—';
  $('adReconnects').textContent = ad.reconnects ?? 0;

  const activity = state.autodarts.activity || {};
  if ($('adInactivityState')) {
    $('adInactivityState').textContent = activity.inactivity_latched ? 'IDLE LATCHED' : activity.game_active ? 'WATCHING' : 'WAITING';
    const remaining = activity.remaining_ms;
    $('adInactivityRemaining').textContent = activity.inactivity_latched
      ? 'NEW GAME REQUIRED'
      : (typeof remaining === 'number' ? `${Math.max(0, Math.ceil(remaining / 1000))}s` : '—');
  }

  const throws = ad.throws || [];
  $('adThrows').innerHTML = throws.length
    ? throws.map(d => `<span class="dart-chip">${escapeHtml(d.name)}<b>${escapeHtml(d.score)}</b></span>`).join('')
    : '<i>Waiting for throws</i>';

  const error = ad.error || (!ad.dependency_ok ? 'websocket-client is missing. Run Setup HimotheeLight.bat again.' : null);
  $('adError').classList.toggle('hidden', !error);
  $('adError').textContent = error || '';

  const bridge = state.autodarts.game_bridge || {};
  const bridgeOnline = !!bridge.extension_connected;
  const bridgeAuthoritative = !!bridge.authoritative;
  const bridgeBadge = $('gameBridgeBadge');
  if (bridgeBadge) {
    bridgeBadge.textContent = bridgeAuthoritative ? 'MATCH CONNECTED' : bridgeOnline ? 'BRIDGE CONNECTED' : 'OFFLINE';
    bridgeBadge.className = `connection-badge ${bridgeOnline ? 'online' : 'offline'}`;
    $('bridgeState').textContent = bridgeAuthoritative ? 'AUTHORITATIVE' : bridgeOnline ? 'CONNECTED' : 'OFFLINE';
    $('bridgeHeartbeat').textContent = bridge.last_heartbeat_at ? `Heartbeat ${bridge.last_heartbeat_at}` : 'Waiting for browser extension';
    $('bridgeRoute').textContent = String(bridge.route || '—').toUpperCase();
    $('bridgeMatchId').textContent = bridge.match_id ? `Match ${bridge.match_id}` : 'No match detected';
    $('bridgePlayer').textContent = bridge.current_player || '—';
    $('bridgeVariant').textContent = bridge.variant || '—';
    if ($('bridgeTarget')) $('bridgeTarget').textContent = bridge.current_target || '—';
    $('bridgeMatchState').textContent = bridge.match_active ? 'ACTIVE' : bridge.match_finished ? 'FINISHED' : 'IDLE';
    $('bridgeLastEvent').textContent = bridge.last_game_event ? `Last event: ${String(bridge.last_game_event).replaceAll('_',' ')}` : 'No game event yet';
    const bridgeEvents = bridge.events || [];
    $('gameBridgeEventList').innerHTML = bridgeEvents.length ? bridgeEvents.slice().reverse().map(item => `<div class="event-row">
      <span class="event-time">${escapeHtml(item.time)}</span>
      <span class="event-name">${escapeHtml(String(item.event || '').replaceAll('_',' '))}</span>
      <span class="event-status">${escapeHtml(item.player || '—')}</span>
      <span class="event-dart">${escapeHtml(item.target ? `Target ${item.target}` : (item.last_dart || '—'))}</span>
      <span class="event-mode active">GAME</span>
    </div>`).join('') : '<div class="empty-state">No game events received yet.</div>';
  }

  renderAutodartsEvents(ad.events || []);
  renderDashboard();
  renderTriggerRuntime();
  renderTriggerHistory();
}

function renderAutodartsEvents(events) {
  const host = $('autodartsEventList');
  if (!events?.length) {
    host.innerHTML = '<div class="empty-state">No Autodarts events received yet.</div>';
    return;
  }
  host.innerHTML = events.slice().reverse().map(item => `<div class="event-row">
    <span class="event-time">${escapeHtml(item.time)}</span>
    <span class="event-name">${escapeHtml(item.event)}</span>
    <span class="event-status">${escapeHtml(item.status)}</span>
    <span class="event-dart">${item.dart ? `${escapeHtml(item.dart)} <b>${escapeHtml(item.score)}</b>` : '—'}</span>
    <span class="event-mode ${item.running ? 'active' : 'idle'}">${item.running ? 'ACTIVE' : 'IDLE'}${item.simulated ? ' • TEST' : ''}</span>
  </div>`).join('');
}

async function refreshAutodarts(quiet=false) {
  try {
    state.autodarts = await api('/api/autodarts/status');
    renderAutodarts();
  } catch (err) {
    if (!quiet) toast(err.message, 'error');
  }
}


function selectedTrigger() {
  return state.config?.triggers?.find(t => t.id === state.selectedTriggerId) || null;
}

function triggerConditionLabel(trigger) {
  if (trigger.trigger_type === 'dart') return `Dart ${trigger.value || '—'}`;
  if (trigger.trigger_type === 'visit_exact') return `Visit = ${trigger.value}`;
  if (trigger.trigger_type === 'visit_range') return `Visit ${trigger.minimum}–${trigger.maximum}`;
  if (trigger.trigger_type === 'combination') return `Combo • ${String(trigger.value || '—').replaceAll('_',' → ')}`;
  if (trigger.trigger_type === 'target') return `Target • ${trigger.value || '—'}`;
  if (trigger.trigger_type === 'game_event') return `Game • ${String(trigger.value || 'event').replaceAll('_',' ')}`;
  if (trigger.trigger_type === 'player_turn') return `Player turn • ${trigger.value || '—'}`;
  if (trigger.trigger_type === 'board_event') return `Board • ${trigger.value || '—'}`;
  return trigger.value || trigger.trigger_type;
}

function renderTriggers() {
  const triggers = state.config?.triggers || [];
  if (state.selectedTriggerId && !triggers.some(t => t.id === state.selectedTriggerId)) state.selectedTriggerId = triggers[0]?.id || null;
  if (!state.selectedTriggerId && triggers.length) state.selectedTriggerId = triggers[0].id;
  const host = $('triggerList');
  if (host) {
    host.innerHTML = triggers.length ? triggers.map(trigger => `<div class="trigger-row ${trigger.id === state.selectedTriggerId ? 'selected' : ''}" data-trigger-id="${escapeHtml(trigger.id)}">
      <div class="trigger-row-top"><strong>${escapeHtml(trigger.name)}</strong><span class="trigger-enabled ${trigger.enabled ? '' : 'off'}">${trigger.enabled ? 'ON' : 'OFF'}</span></div>
      <small>${escapeHtml(triggerConditionLabel(trigger))} • ${(Number(trigger.duration_ms || 0)/1000).toFixed(2)}s • <span class="trigger-priority">priority ${escapeHtml(trigger.priority)}</span></small>
    </div>`).join('') : '<div class="empty-state">No trigger rules yet.</div>';
  }
  renderTriggerEditor();
  renderTriggerRuntime();
  renderTriggerHistory();
}

function renderTriggerRuntime() {
  if (!$('triggerRuntimeName')) return;
  const snap = state.autodarts?.triggers || state.status?.triggers || {};
  const active = snap.runtime?.active || null;
  $('triggerRuntimeName').textContent = active ? active.name : 'No trigger effect active';
  $('triggerRuntimeReason').textContent = active ? `${active.reason || 'Trigger'} • priority ${active.priority}${active.choreography ? ` • step ${active.step || 1} • repeat ${active.repeat || 1}` : ''}${active.followup ? ` • then ${active.followup}` : ''}` : `HimotheeLight is showing the ${String(snap.runtime?.base_mode || lightingSnapshot().mode || 'idle').toUpperCase()} base state.`;
  $('triggerRuntimeRemaining').textContent = active ? (active.remaining_ms == null ? 'LOOP' : `${(Math.max(0, Number(active.remaining_ms))/1000).toFixed(1)}s`) : '—';
  $('cancelTrigger').disabled = !active;
}

function renderTriggerHistory() {
  if (!$('triggerHistory')) return;
  const history = state.autodarts?.triggers?.history || state.status?.triggers?.history || [];
  $('triggerHistory').innerHTML = history.length ? history.slice().reverse().map(row => `<div class="trigger-history-row">
    <span class="event-time">${escapeHtml(row.time)}</span>
    <span><strong>${escapeHtml(row.trigger)}</strong></span>
    <span>P${escapeHtml(row.priority)}</span>
    <span class="${row.accepted ? 'accepted' : 'ignored'}">${row.accepted ? 'FIRED' : 'IGNORED'}</span>
    <span class="trigger-reason">${escapeHtml(row.reason || '')}</span>
  </div>`).join('') : '<div class="empty-state">No trigger effects fired yet.</div>';
}

function populateTriggerDevices(trigger) {
  const devices = lightingEndpoints();
  $('triggerDevices').innerHTML = devices.map(d => `<option value="${escapeHtml(d.id)}">${d._type === 'hue' ? 'Hue' : 'WLED'} — ${escapeHtml(d.name)}</option>`).join('');
  const selected = new Set(trigger?.device_ids || []);
  [...$('triggerDevices').options].forEach(o => o.selected = selected.has(o.value));

  const wledRefs = devices.filter(d => d._type === 'wled');
  $('triggerReferenceDevice').innerHTML = wledRefs.map(d => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.name)}</option>`).join('');
  if (!state.triggerReferenceDeviceId || !wledRefs.some(d => d.id === state.triggerReferenceDeviceId)) state.triggerReferenceDeviceId = wledRefs[0]?.id || null;
  if (state.triggerReferenceDeviceId) $('triggerReferenceDevice').value = state.triggerReferenceDeviceId;
}

function updateTriggerConditionFields() {
  const type = $('triggerType').value;
  const range = type === 'visit_range';
  const gameEvent = type === 'game_event';
  const boardEvent = type === 'board_event';
  $('triggerValueWrap').classList.toggle('hidden', range);
  $('triggerMinWrap').classList.toggle('hidden', !range);
  $('triggerMaxWrap').classList.toggle('hidden', !range);
  $('triggerValue').classList.toggle('hidden', gameEvent || boardEvent);
  $('triggerGameEvent').classList.toggle('hidden', !gameEvent);
  $('triggerBoardEvent').classList.toggle('hidden', !boardEvent);
  if (type === 'dart') { $('triggerValueLabel').textContent = 'Dart'; $('triggerValue').placeholder = 'T20, D16, BULL, 25 or MISS'; }
  if (type === 'visit_exact') { $('triggerValueLabel').textContent = 'Visit score'; $('triggerValue').placeholder = '180'; }
  if (type === 'combination') { $('triggerValueLabel').textContent = 'Dart combination'; $('triggerValue').placeholder = 'T20_T20_T20 or S10_D16'; }
  if (type === 'target') { $('triggerValueLabel').textContent = 'Current target'; $('triggerValue').placeholder = '1-20, 25 or BULL'; }
  if (type === 'game_event') $('triggerValueLabel').textContent = 'Game event';
  if (type === 'player_turn') { $('triggerValueLabel').textContent = 'Autodarts player name'; $('triggerValue').placeholder = 'Player name exactly as shown in Autodarts'; }
  if (type === 'board_event') $('triggerValueLabel').textContent = 'Board Manager event';
}


function updateTriggerRangeOutputs() {
  $('triggerBrightnessOut').textContent = $('triggerBrightness').value;
  $('triggerSpeedOut').textContent = $('triggerSpeed').value;
  $('triggerIntensityOut').textContent = $('triggerIntensity').value;
  $('triggerPresetBrightnessOut').textContent = $('triggerPresetBrightness').value;
}

function renderTriggerEditor() {
  if (!$('triggerEditor')) return;
  const trigger = selectedTrigger();
  $('noTrigger').classList.toggle('hidden', !!trigger);
  $('triggerEditor').classList.toggle('hidden', !trigger);
  if (!trigger) return;
  $('triggerEditorTitle').textContent = trigger.name || 'Trigger';
  $('triggerEnabled').checked = !!trigger.enabled;
  $('triggerName').value = trigger.name || '';
  $('triggerType').value = trigger.trigger_type || 'dart';
  $('triggerValue').value = trigger.value ?? '';
  if ($('triggerGameEvent')) $('triggerGameEvent').value = trigger.trigger_type === 'game_event' ? (trigger.value || 'bust') : 'bust';
  if ($('triggerBoardEvent')) $('triggerBoardEvent').value = trigger.trigger_type === 'board_event' ? (trigger.value || 'Takeout started') : 'Takeout started';
  $('triggerMinimum').value = trigger.minimum ?? 0;
  $('triggerMaximum').value = trigger.maximum ?? 180;
  $('triggerDuration').value = trigger.duration_ms ?? 1500;
  $('triggerPriority').value = trigger.priority ?? 20;
  populateTriggerDevices(trigger);
  $('triggerSceneSelect').innerHTML = '<option value="">Direct trigger effect</option>' + (state.config?.scenes || []).map(scene => `<option value="${escapeHtml(scene.id)}">${escapeHtml(scene.name)}</option>`).join('');
  $('triggerSceneSelect').value = trigger.scene_id || '';
  updateTriggerConditionFields();

  const effect = trigger.effect || {};
  const source = effect.source || 'direct';
  document.querySelectorAll('input[name="triggerSource"]').forEach(r => r.checked = r.value === source);
  $('triggerDirectFields').classList.toggle('hidden', source !== 'direct');
  $('triggerPresetFields').classList.toggle('hidden', source !== 'preset');
  $('triggerBrightness').value = effect.brightness ?? 255;
  $('triggerPresetBrightness').value = effect.brightness ?? 255;
  $('triggerSpeed').value = effect.speed ?? 200;
  $('triggerIntensity').value = effect.intensity ?? 220;
  $('triggerTransitionMs').value = effect.transition_ms ?? 0;
  $('triggerPresetTransitionMs').value = effect.transition_ms ?? 0;
  $('triggerColor1').value = rgbToHex(effect.colors?.[0] || [255,255,255]);
  $('triggerColor2').value = rgbToHex(effect.colors?.[1] || [138,43,226]);
  $('triggerColor3').value = rgbToHex(effect.colors?.[2] || [0,0,0]);

  const meta = state.metadata[state.triggerReferenceDeviceId] || null;
  setSelectOptions($('triggerEffectSelect'), meta?.effects || [], effect.effect_id ?? 0);
  setSelectOptions($('triggerPaletteSelect'), meta?.palettes || [], effect.palette_id ?? 0);
  setSelectOptions($('triggerPresetSelect'), meta?.presets || [], effect.preset_id ?? 1, meta?.presets?.length ? null : 'No presets found');
  setSelectOptions($('triggerSegmentSelect'), meta?.segments || [], effect.segment_id, 'Main segment');
  updateTriggerRangeOutputs();
}

function collectTriggerEffect() {
  const source = document.querySelector('input[name="triggerSource"]:checked')?.value || 'direct';
  return {
    source,
    on: true,
    brightness: Number(source === 'preset' ? $('triggerPresetBrightness').value : $('triggerBrightness').value),
    effect_id: Number($('triggerEffectSelect').value || 0),
    palette_id: Number($('triggerPaletteSelect').value || 0),
    speed: Number($('triggerSpeed').value || 128),
    intensity: Number($('triggerIntensity').value || 128),
    segment_id: $('triggerSegmentSelect').value === '' ? null : Number($('triggerSegmentSelect').value),
    transition_ms: Number(source === 'preset' ? $('triggerPresetTransitionMs').value : $('triggerTransitionMs').value || 0),
    colors: [hexToRgb($('triggerColor1').value), hexToRgb($('triggerColor2').value), hexToRgb($('triggerColor3').value)],
    preset_id: Number($('triggerPresetSelect').value || 1),
  };
}

function collectTrigger() {
  const triggerType = $('triggerType').value;
  const triggerValue = triggerType === 'game_event' ? $('triggerGameEvent').value : (triggerType === 'board_event' ? $('triggerBoardEvent').value : $('triggerValue').value.trim());
  return {
    name: $('triggerName').value.trim() || 'Trigger',
    enabled: $('triggerEnabled').checked,
    trigger_type: triggerType,
    value: triggerValue,
    minimum: Number($('triggerMinimum').value || 0),
    maximum: Number($('triggerMaximum').value || 180),
    duration_ms: Number($('triggerDuration').value || 1500),
    priority: Number($('triggerPriority').value || 20),
    device_ids: [...$('triggerDevices').selectedOptions].map(o => o.value),
    scene_id: $('triggerSceneSelect').value || '',
    effect: collectTriggerEffect(),
  };
}

function setTriggerDirty(dirty=true) {
  state.triggerDirty = !!dirty;
  const button = $('saveTrigger');
  if (button) button.textContent = state.triggerDirty ? 'Save Trigger *' : 'Save Trigger';
}

async function saveTriggerBeforeContextChange() {
  if (!state.triggerDirty || !selectedTrigger()) return true;
  try {
    await saveTrigger(true);
    return true;
  } catch (err) {
    toast(`Could not save trigger: ${err.message}`, 'error');
    return false;
  }
}

async function saveTrigger(quiet=false) {
  const trigger = selectedTrigger();
  if (!trigger) return null;
  const result = await api(`/api/triggers/${trigger.id}`, {method:'PATCH', body:collectTrigger()});
  const cfg = await api('/api/config');
  state.config = cfg.config;
  setTriggerDirty(false);
  if (!quiet) toast(`Saved ${result.trigger.name}`);
  renderTriggers();
  return result.trigger;
}

async function refreshTriggerStatus(quiet=false) {
  try {
    const result = await api('/api/triggers/status');
    if (!state.autodarts) state.autodarts = {};
    state.autodarts.triggers = {triggers: result.triggers, runtime: result.runtime, history: result.history};
    const cfg = await api('/api/config');
    state.config = cfg.config;
    renderTriggers();
  } catch (err) { if (!quiet) toast(err.message, 'error'); }
}

async function ensureTriggerMetadata() {
  const id = state.triggerReferenceDeviceId || state.config?.devices?.[0]?.id;
  if (!id) return;
  state.triggerReferenceDeviceId = id;
  if (!state.metadata[id]) {
    try { await refreshMetadata(id, true); } catch (_) {}
  }
  renderTriggerEditor();
}


function selectedScene() {
  return state.config?.scenes?.find(scene => scene.id === state.selectedSceneId) || null;
}

function renderSceneTargetOptions() {
  if (!$('sceneTargetType')) return;
  const type = $('sceneTargetType').value;
  const target = $('sceneTargetId');
  if (type === 'all') {
    target.innerHTML = '<option value="">All enabled lights</option>';
    target.disabled = true;
  } else if (type === 'device') {
    target.disabled = false;
    target.innerHTML = lightingEndpoints().map(d => `<option value="${escapeHtml(d.id)}">${d._type === 'hue' ? 'Hue' : 'WLED'} — ${escapeHtml(d.name)}</option>`).join('');
  } else {
    target.disabled = false;
    target.innerHTML = (state.config?.device_groups || []).map(g => `<option value="${escapeHtml(g.id)}">${escapeHtml(g.name)}</option>`).join('');
  }
}

function renderScenes() {
  if (!$('groupList')) return;
  const devices = lightingEndpoints();
  const groups = state.config?.device_groups || [];
  const scenes = state.config?.scenes || [];
  $('groupDevices').innerHTML = devices.map(d => `<option value="${escapeHtml(d.id)}">${d._type === 'hue' ? 'Hue' : 'WLED'} — ${escapeHtml(d.name)}</option>`).join('');
  $('groupList').className = groups.length ? 'device-list' : 'device-list empty-state';
  $('groupList').innerHTML = groups.length ? groups.map(g => {
    const names = (g.device_ids || []).map(id => devices.find(d => d.id === id)?.name || id).join(', ') || 'No devices';
    return `<div class="device-row" data-group-id="${escapeHtml(g.id)}"><div class="device-main"><div class="device-icon">G</div><div><strong>${escapeHtml(g.name)}</strong><small>${escapeHtml(names)}</small></div></div><div class="device-actions"><button class="btn danger" data-group-action="delete">Delete</button></div></div>`;
  }).join('') : 'No groups.';

  if (state.selectedSceneId && !scenes.some(x => x.id === state.selectedSceneId)) state.selectedSceneId = scenes[0]?.id || null;
  if (!state.selectedSceneId && scenes.length) state.selectedSceneId = scenes[0].id;
  $('sceneSelect').innerHTML = scenes.map(scene => `<option value="${escapeHtml(scene.id)}">${escapeHtml(scene.name)}</option>`).join('');
  if (state.selectedSceneId) $('sceneSelect').value = state.selectedSceneId;
  $('testScene').disabled = !state.selectedSceneId;
  $('deleteScene').disabled = !state.selectedSceneId;
  const scene = selectedScene();
  $('sceneActions').className = scene?.actions?.length ? 'device-list' : 'device-list empty-state';
  $('sceneActions').innerHTML = scene?.actions?.length ? scene.actions.map((action, index) => {
    let targetName = 'All enabled lights';
    if (action.target_type === 'device') targetName = devices.find(d => d.id === action.target_id)?.name || 'Unknown device';
    if (action.target_type === 'group') targetName = groups.find(g => g.id === action.target_id)?.name || 'Unknown group';
    const effectName = action.effect?.source === 'preset' ? `Preset ${action.effect?.preset_id ?? 1}` : `Effect ${action.effect?.effect_id ?? 0}`;
    const step = Number(action.step || 1), delay = Number(action.delay_ms || 0), hold = Number(action.hold_ms || 0);
    return `<div class="device-row" data-scene-action-index="${index}"><div class="device-main"><div class="device-icon">${step}</div><div><strong>${escapeHtml(action.name)}</strong><small>Step ${step} • ${escapeHtml(targetName)} • ${escapeHtml(effectName)} • delay ${delay}ms • hold ${hold}ms</small></div></div><div class="device-actions"><button class="btn ghost" data-scene-action="up">↑</button><button class="btn ghost" data-scene-action="down">↓</button><button class="btn danger" data-scene-action="delete">Delete</button></div></div>`;
  }).join('') : (scene ? 'No actions in this scene yet.' : 'No scene selected.');

  if ($('sceneRepeatCount')) $('sceneRepeatCount').value = scene?.repeat_count ?? 1;
  if ($('saveSceneTiming')) $('saveSceneTiming').disabled = !scene;
  if ($('sceneTimingSummary')) {
    if (!scene) $('sceneTimingSummary').textContent = 'No scene selected.';
    else {
      const actions = scene.actions || [];
      const choreo = Number(scene.repeat_count ?? 1) !== 1 || actions.some(a => Number(a.step || 1) > 1 || Number(a.delay_ms || 0) > 0 || Number(a.hold_ms || 0) > 0);
      const steps = [...new Set(actions.map(a => Number(a.step || 1)))].sort((a,b)=>a-b);
      const repeatCount = Number(scene.repeat_count ?? 1);
      const repeat = repeatCount === 0 ? 'loops until interrupted' : `${repeatCount} repeat(s)`;
      let oneRunMs = 0;
      for (const step of steps) {
        const rows = actions.filter(a => Number(a.step || 1) === step);
        oneRunMs += rows.reduce((max,a)=>Math.max(max, Number(a.delay_ms || 0)+Number(a.hold_ms || 0)),0);
      }
      const total = repeatCount === 0 ? '∞' : `${((oneRunMs * Math.max(1, repeatCount))/1000).toFixed(2)}s`;
      $('sceneTimingSummary').textContent = choreo ? `Choreography • ${steps.length || 0} step(s) • ${repeat} • timeline ${total}` : 'Simple parallel scene • trigger duration controls how long it stays active.';
    }
  }

  const wledRefs = devices.filter(d => d._type === 'wled');
  $('sceneReferenceDevice').innerHTML = wledRefs.map(d => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.name)}</option>`).join('');
  if (!state.sceneReferenceDeviceId || !wledRefs.some(d => d.id === state.sceneReferenceDeviceId)) state.sceneReferenceDeviceId = wledRefs[0]?.id || null;
  if (state.sceneReferenceDeviceId) $('sceneReferenceDevice').value = state.sceneReferenceDeviceId;
  renderSceneTargetOptions();
  renderTriggerEditor();
}

async function ensureSceneMetadata() {
  const id = state.sceneReferenceDeviceId || state.config?.devices?.[0]?.id;
  if (!id) return;
  state.sceneReferenceDeviceId = id;
  if (!state.metadata[id]) { try { await refreshMetadata(id, true); } catch (_) {} }
  const meta = state.metadata[id] || {};
  setSelectOptions($('sceneEffectSelect'), meta.effects || [], 0);
  setSelectOptions($('scenePaletteSelect'), meta.palettes || [], 0);
  setSelectOptions($('scenePresetSelect'), meta.presets || [], 1, meta.presets?.length ? null : 'No presets found');
}

function collectSceneEffect() {
  const source = document.querySelector('input[name="sceneSource"]:checked')?.value || 'direct';
  return {
    source, on:true,
    brightness:Number($('sceneBrightness').value || 255),
    effect_id:Number($('sceneEffectSelect').value || 0),
    palette_id:Number($('scenePaletteSelect').value || 0),
    speed:Number($('sceneSpeed').value || 200),
    intensity:Number($('sceneIntensity').value || 220),
    segment_id:null, transition_ms:Number($('sceneTransition').value || 0),
    colors:[hexToRgb($('sceneColor1').value), hexToRgb($('sceneColor2').value), hexToRgb($('sceneColor3').value)],
    preset_id:Number($('scenePresetSelect').value || 1),
  };
}

async function reloadConfigAndScenes() {
  const cfg = await api('/api/config');
  state.config = cfg.config;
  renderScenes(); renderTriggers();
}

function profileData() {
  const profiles = Array.isArray(state.config?.profiles) ? state.config.profiles : [];
  const activeId = state.config?.app?.active_profile_id || profiles[0]?.id || null;
  return {profiles, activeId};
}

function renderProfiles() {
  if (!$('profileList')) return;
  const {profiles, activeId} = profileData();
  const select = $('profileSelect');
  select.innerHTML = profiles.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join('');
  if (activeId && profiles.some(p => p.id === activeId)) select.value = activeId;
  const active = profiles.find(p => p.id === activeId) || null;
  $('profileActiveSummary').textContent = active
    ? `${active.name} • ${(active.triggers || []).filter(t => t?.enabled).length} enabled trigger(s) • ${Object.keys(active.device_modes || {}).length} WLED mode set(s) • autosave on`
    : 'No active profile.';
  $('applyProfile').disabled = !profiles.length;
  $('exportSelectedProfile').disabled = !profiles.length;
  $('profileList').className = profiles.length ? 'device-list' : 'device-list empty-state';
  $('profileList').innerHTML = profiles.length ? profiles.map(profile => {
    const enabled = (profile.triggers || []).filter(t => t?.enabled).length;
    const activeTag = profile.id === activeId ? '<span class="trigger-enabled">ACTIVE</span>' : '';
    return `<div class="device-row" data-profile-id="${escapeHtml(profile.id)}">
      <div class="device-main"><div class="device-icon">P</div><div><strong>${escapeHtml(profile.name)} ${activeTag}</strong><small>${enabled}/${(profile.triggers || []).length} triggers enabled • ${Object.keys(profile.device_modes || {}).length} device setup(s)${profile.updated_at ? ` • saved ${escapeHtml(profile.updated_at)}` : ''}</small></div></div>
      <div class="device-actions">
        <button class="btn primary" data-profile-action="apply" ${profile.id === activeId ? 'disabled' : ''}>Apply</button>
        <button class="btn secondary" data-profile-action="export">Export</button>
        <button class="btn ghost" data-profile-action="rename">Rename</button>
        <button class="btn danger" data-profile-action="delete" ${profile.id === activeId || profiles.length <= 1 ? 'disabled' : ''}>Delete</button>
      </div>
    </div>`;
  }).join('') : 'No profiles.';
}

function downloadJsonFile(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || 'HimotheeLight-Profile.json';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function clearProfileImport() {
  state.profileImportBundle = null;
  state.profileImportPreview = null;
  if ($('profileImportFile')) $('profileImportFile').value = '';
  if ($('profileImportPreview')) $('profileImportPreview').classList.add('hidden');
  if ($('profileImportSummary')) $('profileImportSummary').textContent = 'Choose a HimotheeLight profile JSON file to preview it.';
  if ($('profileImportMappings')) $('profileImportMappings').innerHTML = '';
  if ($('profileImportWarnings')) $('profileImportWarnings').textContent = '';
  if ($('profileImportName')) $('profileImportName').value = '';
  if ($('profileImportApply')) $('profileImportApply').checked = false;
}

function updateProfileImportReady() {
  const preview = state.profileImportPreview;
  if (!preview || !$('importProfileNow')) return;
  const missing = (preview.devices || []).filter(device => device.required && !$(`profileMap-${device.slot_id}`)?.value);
  $('importProfileNow').disabled = missing.length > 0;
  if (missing.length && $('profileImportWarnings')) {
    const base = (preview.warnings || []).join(' ');
    $('profileImportWarnings').textContent = `${base} Required mapping still needed: ${missing.map(x => x.name).join(', ')}.`;
  } else if ($('profileImportWarnings')) {
    $('profileImportWarnings').textContent = (preview.warnings || []).join(' ');
  }
}

function renderProfileImportPreview(preview) {
  state.profileImportPreview = preview;
  $('profileImportPreview').classList.remove('hidden');
  $('profileImportName').value = preview.profile_name || 'Imported Profile';
  $('profileImportSummary').innerHTML = `<strong>${escapeHtml(preview.profile_name)}</strong><br>${preview.enabled_trigger_count}/${preview.trigger_count} triggers enabled • ${preview.scene_count} scene(s) • ${preview.group_count} group(s) • ${preview.devices.length} lighting slot(s)`;
  const local = preview.local_devices || [];
  $('profileImportMappings').innerHTML = (preview.devices || []).length ? preview.devices.map(device => {
    const options = [`<option value="">${device.required ? `Select local ${device.type === 'hue' ? 'Hue light' : 'WLED'}…` : 'Not mapped / skip modes'}</option>`]
      .concat(local.filter(item => (item.type || 'wled') === (device.type || 'wled')).map(item => `<option value="${escapeHtml(item.id)}" ${item.id === device.suggested_device_id ? 'selected' : ''}>${item.type === 'hue' ? 'Hue' : 'WLED'} — ${escapeHtml(item.name)}</option>`));
    return `<div class="device-row"><div class="device-main"><div class="device-icon">↔</div><div><strong>${device._type === 'hue' ? 'Hue' : 'WLED'} — ${escapeHtml(device.name)}</strong><small>${device.required ? 'Required by a trigger/scene' : 'Optional profile mode mapping'}${device.suggestion === 'name_match' ? ' • matched by name' : ''}</small></div></div><div class="device-actions"><select id="profileMap-${escapeHtml(device.slot_id)}" data-profile-map="${escapeHtml(device.slot_id)}">${options.join('')}</select></div></div>`;
  }).join('') : '<div class="empty-state">This profile does not require any lighting endpoint mapping.</div>';
  document.querySelectorAll('[data-profile-map]').forEach(select => select.addEventListener('change', updateProfileImportReady));
  updateProfileImportReady();
}

async function previewProfileImport(file) {
  if (!file) return clearProfileImport();
  if (file.size > 1500000) throw new Error('Profile file is too large (maximum 1.5 MB)');
  const text = await file.text();
  let bundle;
  try { bundle = JSON.parse(text); } catch (_) { throw new Error('Profile file is not valid JSON'); }
  const preview = await api('/api/profiles/import/preview', {method:'POST', body:{bundle}});
  state.profileImportBundle = bundle;
  renderProfileImportPreview(preview);
}

async function exportProfile(profileId) {
  const profile = state.config?.profiles?.find(p => p.id === profileId);
  const result = await api(`/api/profiles/${profileId}/export`);
  downloadJsonFile(result.filename, result.bundle);
  toast(`Exported ${profile?.name || 'profile'} — no WLED hosts or Hue Bridge credentials included`);
}

async function applyProfile(profileId) {
  if (!profileId) return;
  const profile = state.config?.profiles?.find(p => p.id === profileId);
  try {
    await api(`/api/profiles/${profileId}/apply`, {method:'POST', body:{}});
    state.selectedTriggerId = null;
    await loadAll();
    toast(`Applied ${profile?.name || 'lighting profile'}`);
  } catch (err) { toast(err.message, 'error'); }
}

async function refreshLogs() {
  try {
    const result = await api('/api/logs');
    $('logList').innerHTML = result.logs.length ? result.logs.slice().reverse().map(line => `<div class="log-line"><span class="log-time">${escapeHtml(line.time)}</span><span class="log-level ${escapeHtml(line.level)}">${escapeHtml(line.level)}</span><span class="log-msg">${escapeHtml(line.message)}</span></div>`).join('') : '<div class="empty-state">No log entries yet.</div>';
  } catch (err) { toast(err.message, 'error'); }
}

// Navigation
async function navigateTo(page) {
  const leavingTriggers = $('page-triggers')?.classList.contains('active') && page !== 'triggers';
  if (leavingTriggers && !(await saveTriggerBeforeContextChange())) return;
  showPage(page);
}
document.querySelectorAll('.nav-item').forEach(btn => btn.addEventListener('click', () => navigateTo(btn.dataset.page)));
document.querySelectorAll('[data-go]').forEach(btn => btn.addEventListener('click', () => navigateTo(btn.dataset.go)));

// Philips Hue
$('hueDiscover').addEventListener('click', async () => {
  const button=$('hueDiscover'); button.disabled=true; button.textContent='Discovering…';
  try { const result=await api('/api/hue/discover'); const rows=result.bridges||[]; $('hueDiscoveryList').className=rows.length?'device-list':'device-list empty-state'; $('hueDiscoveryList').innerHTML=rows.length?rows.map(b=>`<div class="device-row"><div class="device-main"><div class="device-icon">H</div><div><strong>Hue Bridge</strong><small>${escapeHtml(b.ip)}${b.bridge_id?` • ${escapeHtml(b.bridge_id)}`:''}</small></div></div><div class="device-actions"><button class="btn ghost" data-hue-discovered="${escapeHtml(b.ip)}">Use</button></div></div>`).join(''):'No bridges returned. Enter the bridge IP manually.'; }
  catch(err){toast(err.message,'error');} finally {button.disabled=false;button.textContent='Discover Bridges';}
});
$('hueDiscoveryList').addEventListener('click', e=>{const b=e.target.closest('[data-hue-discovered]');if(b)$('hueBridgeHost').value=b.dataset.hueDiscovered;});
$('huePairForm').addEventListener('submit', async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;button.textContent='Pairing…';try{const r=await api('/api/hue/bridges/pair',{method:'POST',body:{name:$('hueBridgeName').value,host:$('hueBridgeHost').value}});toast(`Paired ${r.bridge.name} • ${r.lights.length} light(s)`);await loadAll();}catch(err){toast(err.message,'error');}finally{button.disabled=false;button.textContent='Pair After Button Press';}});
$('hueBridgeList').addEventListener('click',async e=>{const b=e.target.closest('[data-hue-bridge-action]');if(!b)return;const id=b.closest('[data-hue-bridge]')?.dataset.hueBridge;const bridge=(state.config?.hue_bridges||[]).find(x=>x.id===id);if(!bridge)return;try{if(b.dataset.hueBridgeAction==='test'){await api(`/api/hue/bridges/${id}/test`,{method:'POST',body:{}});toast(`${bridge.name} connected`);}else if(b.dataset.hueBridgeAction==='sync'){const r=await api(`/api/hue/bridges/${id}/sync`,{method:'POST',body:{}});toast(`Synced ${r.light_count} Hue light(s)`);await loadAll();}else if(b.dataset.hueBridgeAction==='toggle'){await api(`/api/hue/bridges/${id}`,{method:'PATCH',body:{enabled:bridge.enabled===false}});await loadAll();}else if(b.dataset.hueBridgeAction==='remove'){if(!confirm(`Remove ${bridge.name} and its Hue lights?`))return;await api(`/api/hue/bridges/${id}`,{method:'DELETE'});await loadAll();toast('Hue Bridge removed');}}catch(err){toast(err.message,'error');}});
$('hueLightList').addEventListener('click',async e=>{const b=e.target.closest('[data-hue-light-action]');if(!b)return;const id=b.closest('[data-hue-light]')?.dataset.hueLight;const light=(state.config?.hue_lights||[]).find(x=>x.id===id);if(!light)return;try{if(b.dataset.hueLightAction==='test'){await api(`/api/hue/lights/${id}/test`,{method:'POST',body:{}});toast(`Tested ${light.name}`);}else if(b.dataset.hueLightAction==='configure'){state.selectedDeviceId=id;showPage('modes');renderModeDeviceSelect();renderModeEditor();}else if(b.dataset.hueLightAction==='toggle'){await api(`/api/hue/lights/${id}`,{method:'PATCH',body:{enabled:light.enabled===false}});await loadAll();}}catch(err){toast(err.message,'error');}});

// Device creation
$('addDeviceForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  button.textContent = 'Connecting…';
  try {
    const result = await api('/api/devices', {method:'POST', body:{name:$('deviceName').value, host:$('deviceHost').value}});
    state.metadata[result.device.id] = result.metadata;
    state.connected.add(result.device.id);
    state.selectedDeviceId = result.device.id;
    $('deviceHost').value = '';
    toast(`Added ${result.device.name}`);
    await loadAll();
  } catch (err) { toast(err.message, 'error'); }
  finally { button.disabled = false; button.textContent = 'Add & Connect'; }
});

$('deviceList').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const row = button.closest('[data-device]');
  const id = row.dataset.device;
  const device = state.config.devices.find(d => d.id === id);
  if (!device) return;
  const action = button.dataset.action;
  try {
    if (action === 'test') {
      button.disabled = true; button.textContent = 'Testing…';
      await refreshMetadata(id);
    } else if (action === 'configure') {
      state.selectedDeviceId = id;
      showPage('modes');
      if (!state.metadata[id]) refreshMetadata(id, true).catch(()=>{});
    } else if (action === 'toggle') {
      await api(`/api/devices/${id}`, {method:'PATCH', body:{enabled:!device.enabled}});
      await loadAll();
      toast(`${device.name} ${device.enabled ? 'disabled' : 'enabled'}`);
    } else if (action === 'remove') {
      if (!confirm(`Remove ${device.name} from HimotheeLight?`)) return;
      await api(`/api/devices/${id}`, {method:'DELETE'});
      delete state.metadata[id]; state.connected.delete(id);
      if (state.selectedDeviceId === id) state.selectedDeviceId = null;
      await loadAll();
      toast(`Removed ${device.name}`);
    }
  } catch (err) { toast(err.message, 'error'); }
  finally { if (button?.isConnected) { button.disabled = false; if (action === 'test') button.textContent = 'Test'; } }
});

$('refreshAll').addEventListener('click', async () => {
  const devices = state.config?.devices || [];
  if (!devices.length) return;
  let ok = 0;
  for (const device of devices) {
    try { await refreshMetadata(device.id, true); ok++; } catch (_) {}
  }
  toast(`Refreshed ${ok}/${devices.length} WLED device(s)`, ok === devices.length ? 'success' : 'error');
});

// Mode editor
$('modeDeviceSelect').addEventListener('change', async () => {
  state.selectedDeviceId = $('modeDeviceSelect').value || null;
  renderModeEditor();
  const selected = currentDevice();
  if (selected?._type === 'wled' && !state.metadata[state.selectedDeviceId]) refreshMetadata(state.selectedDeviceId, true).catch(()=>{});
});

document.querySelectorAll('.mode-tab').forEach(tab => tab.addEventListener('click', () => {
  state.mode = tab.dataset.mode;
  document.querySelectorAll('.mode-tab').forEach(t => t.classList.toggle('active', t === tab));
  renderModeEditor();
}));

document.querySelectorAll('input[name="source"]').forEach(radio => radio.addEventListener('change', () => {
  $('directFields').classList.toggle('hidden', radio.value !== 'direct' || !radio.checked);
  $('presetFields').classList.toggle('hidden', radio.value !== 'preset' || !radio.checked);
}));

['brightness','speed','intensity','presetBrightness'].forEach(id => $(id).addEventListener('input', updateRangeOutputs));
$('reloadMetadata').addEventListener('click', async () => { const d=currentDevice(); if(!d)return; if(d._type==='hue'){ try{await api(`/api/hue/lights/${d.id}/test`,{method:'POST',body:{}});toast(`Tested ${d.name}`);}catch(err){toast(err.message,'error');} } else refreshMetadata(d.id); });
$('saveMode').addEventListener('click', () => saveMode(false));
$('saveApplyMode').addEventListener('click', () => saveMode(true));

// Autodarts
$('autodartsSettingsForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter;
  if (button) { button.disabled = true; button.textContent = 'Saving…'; }
  try {
    const result = await api('/api/autodarts/settings', {method:'POST', body:collectAutodartsSettings()});
    state.autodartsSettingsLoaded = false;
    toast('Autodarts settings saved');
    await refreshAutodarts(true);
    populateAutodartsSettings(true);
  } catch (err) { toast(err.message, 'error'); }
  finally { if (button) { button.disabled = false; button.textContent = 'Save & Connect'; } }
});

$('autodartsTest').addEventListener('click', async () => {
  const button = $('autodartsTest');
  button.disabled = true; button.textContent = 'Testing…';
  try {
    const result = await api('/api/autodarts/test', {method:'POST', body:{host:$('autodartsHost').value, port:Number($('autodartsPort').value || 3180)}});
    toast(`Board Manager reachable — ${result.status}${result.running ? ' • running' : ' • stopped'}`);
  } catch (err) { toast(err.message, 'error'); }
  finally { button.disabled = false; button.textContent = 'Test HTTP'; }
});

$('autodartsConnect').addEventListener('click', async () => {
  try { await api('/api/autodarts/connect', {method:'POST', body:{}}); toast('Autodarts connection started'); await refreshAutodarts(true); }
  catch (err) { toast(err.message, 'error'); }
});
$('autodartsDisconnect').addEventListener('click', async () => {
  try { await api('/api/autodarts/disconnect', {method:'POST', body:{}}); toast('Autodarts connection stopped'); await refreshAutodarts(true); }
  catch (err) { toast(err.message, 'error'); }
});
$('refreshAutodarts').addEventListener('click', () => refreshAutodarts(false));
document.querySelectorAll('[data-simulate]').forEach(button => button.addEventListener('click', async () => {
  try {
    await api('/api/autodarts/simulate', {method:'POST', body:{event:button.dataset.simulate}});
    await refreshAutodarts(true);
    toast(`Simulated ${button.dataset.simulate} event`);
  } catch (err) { toast(err.message, 'error'); }
}));

document.querySelectorAll('[data-game-simulate]').forEach(button => button.addEventListener('click', async () => {
  try {
    await api('/api/autodarts/bridge/simulate', {method:'POST', body:{event:button.dataset.gameSimulate}});
    await refreshAutodarts(true);
    toast(`Simulated ${button.dataset.gameSimulate.replaceAll('_',' ')} game event`);
  } catch (err) { toast(err.message, 'error'); }
}));


// Trigger Engine
$('triggerList').addEventListener('click', async (event) => {
  const row = event.target.closest('[data-trigger-id]');
  if (!row || row.dataset.triggerId === state.selectedTriggerId) return;
  if (!(await saveTriggerBeforeContextChange())) return;
  state.selectedTriggerId = row.dataset.triggerId;
  setTriggerDirty(false);
  renderTriggers();
  ensureTriggerMetadata();
});
$('addTrigger').addEventListener('click', async () => {
  try {
    const result = await api('/api/triggers', {method:'POST', body:{}});
    state.selectedTriggerId = result.trigger.id;
    const cfg = await api('/api/config'); state.config = cfg.config;
    setTriggerDirty(false);
    renderTriggers(); ensureTriggerMetadata(); toast('New trigger added');
  } catch (err) { toast(err.message, 'error'); }
});
$('triggerType').addEventListener('change', () => { updateTriggerConditionFields(); setTriggerDirty(true); });
$('triggerReferenceDevice').addEventListener('change', async () => {
  state.triggerReferenceDeviceId = $('triggerReferenceDevice').value || null;
  await ensureTriggerMetadata();
});
document.querySelectorAll('input[name="triggerSource"]').forEach(radio => radio.addEventListener('change', () => {
  const source = document.querySelector('input[name="triggerSource"]:checked')?.value || 'direct';
  $('triggerDirectFields').classList.toggle('hidden', source !== 'direct');
  $('triggerPresetFields').classList.toggle('hidden', source !== 'preset');
  setTriggerDirty(true);
}));
['triggerBrightness','triggerSpeed','triggerIntensity','triggerPresetBrightness'].forEach(id => $(id).addEventListener('input', () => { updateTriggerRangeOutputs(); setTriggerDirty(true); }));
// Editing a rule should never silently lose changes. The enabled switch is
// persisted immediately; all other fields are marked dirty and auto-save
// before trigger/page changes.
['triggerName','triggerValue','triggerGameEvent','triggerBoardEvent','triggerMinimum','triggerMaximum','triggerDuration','triggerPriority',
 'triggerDevices','triggerSceneSelect','triggerEffectSelect','triggerPaletteSelect','triggerSegmentSelect','triggerTransitionMs',
 'triggerColor1','triggerColor2','triggerColor3','triggerPresetSelect','triggerPresetTransitionMs']
  .forEach(id => $(id).addEventListener('change', () => setTriggerDirty(true)));

$('triggerEnabled').addEventListener('change', async () => {
  const wanted = $('triggerEnabled').checked;
  setTriggerDirty(true);
  $('triggerEnabled').disabled = true;
  try {
    const saved = await saveTrigger(true);
    toast(`${saved.name} ${wanted ? 'enabled' : 'disabled'}`);
  } catch (err) {
    toast(`Could not ${wanted ? 'enable' : 'disable'} trigger: ${err.message}`, 'error');
    const cfg = await api('/api/config').catch(() => null);
    if (cfg?.config) state.config = cfg.config;
    setTriggerDirty(false);
    renderTriggers();
  } finally {
    $('triggerEnabled').disabled = false;
  }
});

$('saveTrigger').addEventListener('click', async () => { try { await saveTrigger(false); } catch (err) { toast(err.message, 'error'); } });
$('testTrigger').addEventListener('click', async () => {
  try {
    const saved = await saveTrigger(true);
    await api(`/api/triggers/${saved.id}/test`, {method:'POST', body:{}});
    await refreshTriggerStatus(true);
    toast(`Testing ${saved.name}`);
  } catch (err) { toast(err.message, 'error'); }
});
$('deleteTrigger').addEventListener('click', async () => {
  const trigger = selectedTrigger(); if (!trigger) return;
  if (!confirm(`Delete trigger ${trigger.name}?`)) return;
  try {
    await api(`/api/triggers/${trigger.id}`, {method:'DELETE'});
    state.selectedTriggerId = null;
    const cfg = await api('/api/config'); state.config = cfg.config;
    setTriggerDirty(false);
    renderTriggers(); toast(`Deleted ${trigger.name}`);
  } catch (err) { toast(err.message, 'error'); }
});
$('cancelTrigger').addEventListener('click', async () => {
  try { await api('/api/triggers/cancel', {method:'POST', body:{}}); await refreshTriggerStatus(true); toast('Trigger effect stopped'); }
  catch (err) { toast(err.message, 'error'); }
});
$('refreshTriggers').addEventListener('click', () => refreshTriggerStatus(false));

// Profiles
$('applyProfile').addEventListener('click', () => applyProfile($('profileSelect').value));
$('exportSelectedProfile').addEventListener('click', async () => { try { await exportProfile($('profileSelect').value); } catch (err) { toast(err.message, 'error'); } });
$('createProfileForm').addEventListener('submit', async event => {
  event.preventDefault();
  const name = $('createProfileName').value.trim();
  if (!name) return;
  try {
    await api('/api/profiles', {method:'POST', body:{name}});
    $('createProfileName').value = '';
    await loadAll();
    toast(`Created profile ${name}`);
  } catch (err) { toast(err.message, 'error'); }
});
$('profileImportFile').addEventListener('change', async event => {
  try { await previewProfileImport(event.target.files?.[0]); }
  catch (err) { clearProfileImport(); toast(err.message, 'error'); }
});
$('clearProfileImport').addEventListener('click', clearProfileImport);
$('importProfileNow').addEventListener('click', async () => {
  if (!state.profileImportBundle || !state.profileImportPreview) return;
  const mapping = {};
  document.querySelectorAll('[data-profile-map]').forEach(select => { if (select.value) mapping[select.dataset.profileMap] = select.value; });
  const button = $('importProfileNow');
  button.disabled = true; button.textContent = 'Importing…';
  try {
    const result = await api('/api/profiles/import', {method:'POST', body:{
      bundle: state.profileImportBundle,
      mapping,
      name: $('profileImportName').value.trim() || state.profileImportPreview.profile_name,
    }});
    const imported = result.result;
    const applyNow = $('profileImportApply').checked;
    if (applyNow) await api(`/api/profiles/${imported.profile_id}/apply`, {method:'POST', body:{}});
    clearProfileImport();
    state.selectedTriggerId = null;
    await loadAll();
    toast(`${imported.name} imported${applyNow ? ' and applied' : ''} • ${imported.trigger_count} triggers • ${imported.scene_count} scenes`);
  } catch (err) { toast(err.message, 'error'); updateProfileImportReady(); }
  finally { button.textContent = 'Import Profile'; if (!state.profileImportPreview) button.disabled = true; }
});

$('profileList').addEventListener('click', async event => {
  const button = event.target.closest('[data-profile-action]');
  if (!button) return;
  const row = button.closest('[data-profile-id]');
  const id = row?.dataset.profileId;
  const profile = state.config?.profiles?.find(p => p.id === id);
  if (!id || !profile) return;
  const action = button.dataset.profileAction;
  if (action === 'apply') return applyProfile(id);
  if (action === 'export') {
    try { await exportProfile(id); } catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (action === 'rename') {
    const name = prompt('Profile name', profile.name);
    if (!name || name.trim() === profile.name) return;
    try { await api(`/api/profiles/${id}`, {method:'PATCH', body:{name:name.trim()}}); await loadAll(); toast('Profile renamed'); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (action === 'delete') {
    if (!confirm(`Delete profile ${profile.name}?`)) return;
    try { await api(`/api/profiles/${id}`, {method:'DELETE'}); await loadAll(); toast(`Deleted ${profile.name}`); }
    catch (err) { toast(err.message, 'error'); }
  }
});


// Scenes & device groups
$('createGroupForm').addEventListener('submit', async event => {
  event.preventDefault();
  const name = $('groupName').value.trim();
  const device_ids = [...$('groupDevices').selectedOptions].map(o => o.value);
  try { await api('/api/device-groups', {method:'POST', body:{name, device_ids}}); $('groupName').value=''; await reloadConfigAndScenes(); toast(`Created group ${name}`); }
  catch (err) { toast(err.message, 'error'); }
});
$('groupList').addEventListener('click', async event => {
  const btn = event.target.closest('[data-group-action="delete"]'); if (!btn) return;
  const id = btn.closest('[data-group-id]')?.dataset.groupId; if (!id) return;
  try { await api(`/api/device-groups/${id}`, {method:'DELETE'}); await reloadConfigAndScenes(); toast('Group deleted'); }
  catch (err) { toast(err.message, 'error'); }
});
$('createSceneForm').addEventListener('submit', async event => {
  event.preventDefault(); const name=$('sceneName').value.trim();
  try { const result=await api('/api/scenes',{method:'POST',body:{name,repeat_count:1,actions:[]}}); state.selectedSceneId=result.scene.id; $('sceneName').value=''; await reloadConfigAndScenes(); toast(`Created scene ${name}`); }
  catch(err){ toast(err.message,'error'); }
});
$('sceneSelect').addEventListener('change', ()=>{ state.selectedSceneId=$('sceneSelect').value || null; renderScenes(); });
$('sceneTargetType').addEventListener('change', renderSceneTargetOptions);
$('sceneReferenceDevice').addEventListener('change', async ()=>{ state.sceneReferenceDeviceId=$('sceneReferenceDevice').value || null; await ensureSceneMetadata(); });
['sceneBrightness','sceneSpeed','sceneIntensity'].forEach(id => $(id).addEventListener('input', ()=>{ $(`${id}Out`).textContent=$(id).value; }));
$('addSceneAction').addEventListener('click', async ()=>{
  const scene=selectedScene(); if(!scene) return toast('Create or select a scene first','error');
  const target_type=$('sceneTargetType').value;
  const target_id=target_type==='all' ? '' : $('sceneTargetId').value;
  const actions=[...(scene.actions || []), {name:$('sceneActionName').value.trim() || 'Scene Action', target_type, target_id, step:Number($('sceneActionStep').value || 1), delay_ms:Number($('sceneActionDelay').value || 0), hold_ms:Number($('sceneActionHold').value || 0), effect:collectSceneEffect()}];
  try { await api(`/api/scenes/${scene.id}`,{method:'PATCH',body:{name:scene.name,repeat_count:scene.repeat_count ?? 1,actions}}); await reloadConfigAndScenes(); toast('Scene action added'); }
  catch(err){ toast(err.message,'error'); }
});
$('sceneActions').addEventListener('click', async event=>{
  const btn=event.target.closest('[data-scene-action]'); if(!btn) return;
  const scene=selectedScene(); const index=Number(btn.closest('[data-scene-action-index]')?.dataset.sceneActionIndex); if(!scene || Number.isNaN(index)) return;
  const action=btn.dataset.sceneAction; const actions=[...(scene.actions || [])];
  if(action==='delete') actions.splice(index,1);
  else if(action==='up' && index>0) [actions[index-1],actions[index]]=[actions[index],actions[index-1]];
  else if(action==='down' && index<actions.length-1) [actions[index+1],actions[index]]=[actions[index],actions[index+1]];
  else return;
  try { await api(`/api/scenes/${scene.id}`,{method:'PATCH',body:{name:scene.name,repeat_count:scene.repeat_count ?? 1,actions}}); await reloadConfigAndScenes(); toast(action==='delete'?'Scene action deleted':'Scene action reordered'); }
  catch(err){ toast(err.message,'error'); }
});
$('saveSceneTiming').addEventListener('click', async ()=>{ const scene=selectedScene(); if(!scene)return; const repeat_count=Math.max(0,Math.min(20,Number($('sceneRepeatCount').value || 0))); try{await api(`/api/scenes/${scene.id}`,{method:'PATCH',body:{name:scene.name,repeat_count,actions:scene.actions || []}});await reloadConfigAndScenes();toast(repeat_count===0?'Scene will loop until interrupted':`Scene repeat count saved: ${repeat_count}`);}catch(err){toast(err.message,'error');} });
$('testScene').addEventListener('click', async ()=>{ const scene=selectedScene(); if(!scene)return; try{await api(`/api/scenes/${scene.id}/test`,{method:'POST',body:{duration_ms:2000}}); toast(`Testing ${scene.name}`);}catch(err){toast(err.message,'error');} });
$('deleteScene').addEventListener('click', async ()=>{ const scene=selectedScene(); if(!scene)return; if(!confirm(`Delete scene ${scene.name}?`))return; try{await api(`/api/scenes/${scene.id}`,{method:'DELETE'});state.selectedSceneId=null;await reloadConfigAndScenes();toast('Scene deleted');}catch(err){toast(err.message,'error');} });

// Global quick actions
$('applyIdleAll').addEventListener('click', () => applyAll('idle'));
$('applyActiveAll').addEventListener('click', () => applyAll('active'));
$('dashIdle').addEventListener('click', () => applyAll('idle'));
$('dashActive').addEventListener('click', () => applyAll('active'));
$('refreshLogs').addEventListener('click', refreshLogs);

loadAll().then(async () => {
  if (currentDevice()?._type === 'wled') refreshMetadata(state.selectedDeviceId, true).catch(()=>{});
  refreshAutodarts(true);
});

// Live-state polling. The WebSocket stays in the Python backend; the browser
// only polls HimotheeLight's local status so closing/reloading this page never affects play.
setInterval(() => refreshAutodarts(true), 750);
