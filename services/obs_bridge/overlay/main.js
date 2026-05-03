(function () {
  "use strict";

  var container = document.getElementById("subtitle-container");
  var ws = null;
  var reconnectTimer = null;
  var reconnectDelay = 1000;

  function connect() {
    var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    var url = protocol + "//" + window.location.host + "/ws/subtitle";
    ws = new WebSocket(url);
    ws.onopen = function () {
      reconnectDelay = 1000;
    };
    ws.onmessage = function (event) {
      try {
        renderMessage(JSON.parse(event.data));
      } catch (e) {
        return;
      }
    };
    ws.onclose = scheduleReconnect;
    ws.onerror = function () {};
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  }

  function renderMessage(msg) {
    if (msg.type !== "subtitle_state") return;
    renderState(msg.state);
  }

  function renderState(state) {
    container.innerHTML = "";
    if (!state) return;

    var history = Array.isArray(state.history) ? state.history : [];
    for (var i = 0; i < history.length; i++) {
      container.appendChild(buildLine(history[i], "history"));
    }
    if (state.active_line) {
      container.appendChild(buildLine(state.active_line, "active"));
    }
  }

  function buildLine(line, mode) {
    var el = document.createElement("div");
    el.className = "subtitle-line " + mode;
    if (mode === "active") {
      el.classList.add("typing");
    }
    if (typeof line.revealed_text === "string") {
      el.textContent = line.revealed_text;
    } else {
      el.textContent = line.text || "";
    }
    return el;
  }

  connect();
})();
