const ENDPOINTS = [
  'http://127.0.0.1:8765/api/autodarts/bridge',
  'http://localhost:8765/api/autodarts/bridge',
];

async function postToHimotheeLight(payload) {
  let lastError = null;
  for (const endpoint of ENDPOINTS) {
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(`HimotheeLight HTTP ${response.status}`);
      return await response.json();
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error('HimotheeLight is not reachable');
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) return;

  if (message.type === 'himotheelight-inject-capture') {
    const tabId = sender.tab?.id;
    if (tabId === undefined) {
      sendResponse({ok:false, error:'No Autodarts tab id'});
      return;
    }
    chrome.scripting.executeScript({
      target: {tabId},
      world: 'MAIN',
      files: ['capture.js'],
      injectImmediately: true,
    }).then(() => sendResponse({ok:true}))
      .catch(error => sendResponse({ok:false, error:String(error)}));
    return true;
  }

  if (message.type === 'himotheelight-bridge') {
    postToHimotheeLight(message.payload)
      .then(result => sendResponse({ok: true, result}))
      .catch(error => sendResponse({ok: false, error: String(error)}));
    return true;
  }
});
