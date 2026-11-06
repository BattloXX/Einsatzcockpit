(function (root) {
  'use strict';
  function nextContrast(enabled) { return !enabled; }
  function apply(board, enabled) {
    board.classList.toggle('breathing-hc', enabled);
    return enabled;
  }
  function restore(board, storage) {
    let enabled = false;
    try { enabled = storage().getItem('ec-breathing-hc') === '1'; } catch (_) {}
    return apply(board, enabled);
  }
  function toggle(board, storage) {
    const enabled = apply(board, nextContrast(board.classList.contains('breathing-hc')));
    try { storage().setItem('ec-breathing-hc', enabled ? '1' : '0'); } catch (_) {}
    return enabled;
  }
  const api = { nextContrast: nextContrast, apply: apply, restore: restore, toggle: toggle };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else {
    root.ECPBreathingContrast = api;
    document.addEventListener('DOMContentLoaded', function () {
      const board = document.getElementById('breathingBoard');
      const button = document.getElementById('breathingContrastToggle');
      if (!board || !button) return;
      button.setAttribute('aria-pressed', String(board.classList.contains('breathing-hc')));
      button.addEventListener('click', function () {
        button.setAttribute('aria-pressed', String(toggle(board, function () { return root.localStorage; })));
      });
    });
  }
})(typeof window !== 'undefined' ? window : globalThis);
