(() => {
  if (window.__himotheeLightWebSocketCapture) return;
  window.__himotheeLightWebSocketCapture = true;

  try {
    const descriptor = Object.getOwnPropertyDescriptor(MessageEvent.prototype, 'data');
    if (!descriptor || typeof descriptor.get !== 'function') return;
    const originalGetter = descriptor.get;

    descriptor.get = function () {
      const value = originalGetter.call(this);
      try {
        if (this.currentTarget instanceof WebSocket && typeof value === 'string') {
          window.dispatchEvent(new CustomEvent('himotheelight-websocket-incoming', {
            detail: value,
          }));
        }
      } catch (_) {}
      return value;
    };

    Object.defineProperty(MessageEvent.prototype, 'data', descriptor);
  } catch (error) {
    console.warn('[HimotheeLight] WebSocket capture failed', error);
  }
})();
