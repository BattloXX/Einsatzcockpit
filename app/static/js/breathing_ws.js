/* Gemeinsamer kleiner WS-Client fuer Logistikansichten, ohne Alarmfunktionen. */
(function () {
  'use strict';
  window.ECPBreathingWS = {
    connect: function (options) {
      let attempt = 0;
      let stopped = false;
      let socket;
      let reconnect;
      let opened = false;
      function connect() {
        if (stopped) return;
        socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://')
          + location.host + '/ws/incident/' + options.incidentId);
        let lastActivity = Date.now();
        let ping;
        let watchdog;
        socket.onopen = function () {
          attempt = 0;
          options.connection(true);
          if (opened) options.refresh();
          opened = true;
          lastActivity = Date.now();
          ping = setInterval(function () { if (socket.readyState === 1) socket.send('ping'); }, 30000);
          watchdog = setInterval(function () { if (Date.now() - lastActivity > 70000) socket.close(); }, 15000);
        };
        socket.onmessage = function (event) {
          lastActivity = Date.now();
          if (event.data === 'pong') return;
          let payload;
          try { payload = JSON.parse(event.data); } catch (_) { return; }
          if (options.events.includes(payload.type)) options.refresh();
        };
        socket.onclose = function () {
          clearInterval(ping);
          clearInterval(watchdog);
          if (stopped) return;
          options.connection(false);
          reconnect = setTimeout(connect, ECPBreathingNet.backoffMs(attempt++));
        };
        socket.onerror = function () { socket.close(); };
      }
      connect();
      window.addEventListener('pagehide', function () {
        stopped = true;
        clearTimeout(reconnect);
        socket.close();
      }, {once: true});
    }
  };
})();
