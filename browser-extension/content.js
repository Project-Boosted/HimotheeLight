(() => {
  const BRIDGE_VERSION = '0.8.0';
  let lastMatchId = null;
  let lastSendAt = 0;

  function routeInfo() {
    const href = location.href;
    const match = href.match(/\/(?:match|matches)\/([0-9a-f-]{16,})/i);
    if (match) return {route: 'match', matchId: match[1]};
    if (/\/lobb(?:y|ies)\//i.test(href)) return {route: 'lobby', matchId: null};
    if (/\/boards?\//i.test(href)) return {route: 'board', matchId: null};
    return {route: 'other', matchId: null};
  }

  function send(payload) {
    const full = {
      bridge_version: BRIDGE_VERSION,
      page_url: location.href,
      ...payload,
    };
    lastSendAt = Date.now();
    try {
      chrome.runtime.sendMessage({type: 'himotheelight-bridge', payload: full}, () => {
        void chrome.runtime.lastError;
      });
    } catch (_) {}
  }

  function playerName(players, index) {
    if (!Array.isArray(players) || index < 0 || index >= players.length) return '';
    return String(players[index]?.name || players[index]?.user?.name || '');
  }

  function isBot(player) {
    return player && player.cpuPPR !== null && player.cpuPPR !== undefined;
  }

  function targetNumber(value) {
    if (value && typeof value === 'object') return value.number ?? value.target ?? '';
    return value ?? '';
  }

  function currentTarget(data, playerIndex) {
    const variant = String(data?.variant || '').toUpperCase();
    const state = data?.state && typeof data.state === 'object' ? data.state : {};
    const round = Number(data?.round || 0) || 0;
    let value = '';

    if (variant === 'ATC') {
      const perPlayer = Array.isArray(state.targets?.[playerIndex]) ? state.targets[playerIndex] : [];
      const index = Number(state.currentTargets?.[playerIndex] ?? 0) || 0;
      value = targetNumber(perPlayer[index]);
      const mode = String(data?.settings?.mode || '').toUpperCase();
      if (Number(value) === 25 && (mode === 'DOUBLE' || mode === 'TRIPLE')) return 'BULL';
    } else if (variant === 'RTW' || variant === 'ROUND THE WORLD') {
      value = targetNumber(Array.isArray(state.targets) ? state.targets[Math.max(0, round - 1)] : '');
    } else if (variant === 'SHANGHAI') {
      value = targetNumber(Array.isArray(state.targets) ? state.targets[Math.max(0, round - 1)] : '');
    } else if (variant === "BOB'S 27" || variant === 'BOBS 27') {
      value = round;
    }

    if (value === null || value === undefined || value === '') return '';
    return String(value).toUpperCase();
  }

  function summarizeMatch(data) {
    if (!data || typeof data !== 'object' || data.body) return null;
    const route = routeInfo();
    if (route.route !== 'match') return null;
    if (route.matchId && data.id && route.matchId !== data.id && data.activated === undefined) return null;
    if (data.activated !== undefined && (!Array.isArray(data.turns) || !Array.isArray(data.players))) return null;

    const players = Array.isArray(data.players) ? data.players : [];
    const playerIndex = Number.isInteger(data.player) ? data.player : Number(data.player ?? -1);
    const currentPlayer = players[playerIndex] || null;
    const turns = Array.isArray(data.turns) ? data.turns : [];
    const turn = turns[0] || null;
    const throws = Array.isArray(turn?.throws) ? turn.throws : [];
    const lastThrow = throws.length ? throws[throws.length - 1] : null;
    const gameWinner = Number.isInteger(data.gameWinner) ? data.gameWinner : Number(data.gameWinner ?? -1);
    const winner = Number.isInteger(data.winner) ? data.winner : Number(data.winner ?? -1);

    return {
      match_id: String(data.id || route.matchId || ''),
      variant: String(data.variant || 'Unknown'),
      finished: Boolean(data.finished),
      winner: Number.isFinite(winner) ? winner : -1,
      winner_name: playerName(players, Number.isFinite(winner) ? winner : -1),
      game_finished: Boolean(data.gameFinished),
      game_winner: Number.isFinite(gameWinner) ? gameWinner : -1,
      game_winner_name: playerName(players, Number.isFinite(gameWinner) ? gameWinner : -1),
      player: Number.isFinite(playerIndex) ? playerIndex : -1,
      current_player_name: String(currentPlayer?.name || currentPlayer?.user?.name || ''),
      current_player_is_bot: Boolean(isBot(currentPlayer)),
      current_target: currentTarget(data, Number.isFinite(playerIndex) ? playerIndex : -1),
      turn_id: String(turn?.id || ''),
      turn_busted: Boolean(turn?.busted ?? data.turnBusted),
      turn_points: Number(turn?.points ?? data.turnScore ?? 0) || 0,
      turn_throw_count: throws.length,
      last_dart: String(lastThrow?.segment?.name || ''),
      round: Number(data.round || 0) || 0,
      leg: Number(data.leg || 0) || 0,
      set: Number(data.set || 0) || 0,
    };
  }

  window.addEventListener('himotheelight-websocket-incoming', event => {
    const raw = event.detail;
    if (typeof raw !== 'string') return;
    try {
      const message = JSON.parse(raw);
      if (message?.channel !== 'autodarts.matches') return;
      const state = summarizeMatch(message.data);
      if (!state) return;
      lastMatchId = state.match_id || lastMatchId;
      send({type: 'match_state', route: 'match', state});
    } catch (_) {}
  });

  try {
    chrome.runtime.sendMessage({type: 'himotheelight-inject-capture'}, () => {
      void chrome.runtime.lastError;
    });
  } catch (_) {}

  function heartbeat() {
    const route = routeInfo();
    send({type: 'heartbeat', route: route.route, match_id: route.matchId || lastMatchId});
  }

  heartbeat();
  setInterval(heartbeat, 3000);
  addEventListener('popstate', () => setTimeout(heartbeat, 0));
  addEventListener('hashchange', () => setTimeout(heartbeat, 0));
  addEventListener('pagehide', () => send({type: 'page_hidden', route: routeInfo().route}));

  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      heartbeat();
    } else if (Date.now() - lastSendAt > 4500) {
      heartbeat();
    }
  }, 1500);
})();
