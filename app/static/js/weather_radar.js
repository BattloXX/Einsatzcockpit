/* global L, ecpIcon */
// Gemeinsame RainViewer-Animation für Wetterseite und Lagekarte.
(function () {
  'use strict';
  window.WeatherRadar = { create: function (map, root) {
  // ── Radar-Variablen ─────────────────────────────────────────────────────────
  var destroyed = false;
  var radarHost     = 'https://tilecache.rainviewer.com';
  var frames        = [];   // [{time, path, nowcast}]
  var frameLayers   = {};   // path -> L.tileLayer
  var animTimer     = null;
  var isPlaying     = true;
  var pauseTicks    = 0;
  var PAUSE_TICKS   = 3;   // 3 × 650 ms = ~2 s Pause am letzten Frame

  var scrubber    = root.querySelector('[data-radar-id="radar-scrubber"]') || document.getElementById('radar-scrubber');
  var playBtn     = root.querySelector('[data-radar-id="radar-play-btn"]') || document.getElementById('radar-play-btn');
  var timeLabel   = root.querySelector('[data-radar-id="radar-time-label"]') || document.getElementById('radar-time-label');
  var liveBadge   = root.querySelector('[data-radar-id="radar-live-badge"]') || document.getElementById('radar-live-badge');
  var loadingInfo = root.querySelector('[data-radar-id="radar-loading"]') || document.getElementById('radar-loading');
  var slabelStart = root.querySelector('[data-radar-id="slabel-start"]') || document.getElementById('slabel-start');
  var slabelMid   = root.querySelector('[data-radar-id="slabel-mid"]') || document.getElementById('slabel-mid');
  var slabelEnd   = root.querySelector('[data-radar-id="slabel-end"]') || document.getElementById('slabel-end');

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function fmtTime(ts) {
    var d = new Date(ts * 1000);
    return pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function showFrame(idx) {
    if (!frames.length) { return; }
    idx = Math.max(0, Math.min(idx, frames.length - 1));
    scrubber.value = idx;

    var f = frames[idx];
    // maxNativeZoom: 6 verhindert die "Zoom Level Not Supported"-Kacheln
    if (!frameLayers[f.path]) {
      frameLayers[f.path] = L.tileLayer(
        radarHost + f.path + '/256/{z}/{x}/{y}/2/1_1.png',
        {
          opacity:       0,
          zIndex:        5,
          maxNativeZoom: 6,
          maxZoom:       18,
          attribution:   'Radar: <a href="https://www.rainviewer.com/" target="_blank" rel="noopener">RainViewer</a>',
        }
      ).addTo(map);
    }
    Object.keys(frameLayers).forEach(function (p) {
      frameLayers[p].setOpacity(p === f.path ? 0.6 : 0);
    });

    var isLast = (idx === frames.length - 1);
    liveBadge.style.display = isLast ? '' : 'none';
    timeLabel.innerHTML = fmtTime(f.time) + (f.nowcast ? ' ' + ecpIcon("chevron-right", {size: "lg"}) + ' Prognose' : '');
  }

  function stopAnim() {
    if (animTimer) { clearInterval(animTimer); animTimer = null; }
  }

  function startAnim() {
    stopAnim();
    isPlaying  = true;
    pauseTicks = 0;
    playBtn.innerHTML = ecpIcon('player-pause');
    if (!frames.length) { return; }

    var idx = 0;
    animTimer = setInterval(function () {
      if (idx >= frames.length) {
        // Pause am letzten Frame, dann von vorne
        if (pauseTicks < PAUSE_TICKS) { pauseTicks++; return; }
        pauseTicks = 0;
        idx = 0;
      }
      showFrame(idx++);
    }, 650);
  }

  function loadFrames() {
    fetch('https://api.rainviewer.com/public/weather-maps.json')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (destroyed) { return; }
        loadingInfo.style.display = 'none';
        if (!d) { timeLabel.textContent = 'Radar nicht verfügbar'; return; }
        radarHost = d.host || radarHost;

        var past    = (d.radar && d.radar.past)    || [];
        var nowcast = (d.radar && d.radar.nowcast) || [];
        frames = past.slice(-8).map(function (f) {
          return { time: f.time, path: f.path, nowcast: false };
        }).concat(nowcast.map(function (f) {
          return { time: f.time, path: f.path, nowcast: true };
        }));

        if (!frames.length) { timeLabel.textContent = 'Kein Radarbild verfügbar'; return; }

        scrubber.max = frames.length - 1;
        slabelStart.textContent = fmtTime(frames[0].time);
        var mid = Math.floor(frames.length / 2);
        if (frames.length > 2) { slabelMid.textContent = fmtTime(frames[mid].time); }
        slabelEnd.textContent = 'jetzt';

        // Letzten Frame zeigen, dann Animation starten
        showFrame(frames.length - 1);
        startAnim();
      })
      .catch(function () {
        loadingInfo.style.display = 'none';
        timeLabel.textContent = 'Radar konnte nicht geladen werden';
      });
  }

  loadFrames();

  // Alle 5 min neue Frames
  var refreshTimer = setInterval(function () {
    Object.keys(frameLayers).forEach(function (p) { map.removeLayer(frameLayers[p]); });
    frameLayers = {};
    frames = [];
    loadingInfo.style.display = '';
    loadFrames();
  }, 300000);

  // ── Scrubber-Interaktion ────────────────────────────────────────────────────
  function pauseOnScrub() { stopAnim(); isPlaying = false; playBtn.innerHTML = ecpIcon('player-play'); }
  scrubber.addEventListener('mousedown',  pauseOnScrub);
  scrubber.addEventListener('touchstart', pauseOnScrub, { passive: true });
  scrubber.addEventListener('input', function () { showFrame(parseInt(this.value, 10)); });

  playBtn.addEventListener('click', function () {
    if (isPlaying) {
      stopAnim();
      isPlaying = false;
      playBtn.innerHTML = ecpIcon('player-play');
    } else {
      startAnim();
    }
  });

  return {
    stop: stopAnim,
    resume: function () { if (isPlaying) { startAnim(); } },
    destroy: function () {
      destroyed = true;
      stopAnim(); clearInterval(refreshTimer);
      Object.keys(frameLayers).forEach(function (p) { map.removeLayer(frameLayers[p]); });
    }
  };
  } };
})();
