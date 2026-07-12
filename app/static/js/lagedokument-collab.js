// Lagedokument: verbindet die bestehende Quill-Instanz per Yjs-CRDT mit dem
// Sync-Endpoint aus PR 2 (WS /ws/lagedokument/{lageId}). Nutzt die vendorten
// Pakete unter /static/js/collab/ (siehe dortige NOTICE.md), eingebunden per
// Browser-Import-Map im Template -- kein Build-Schritt noetig.
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import { QuillBinding } from '/static/js/collab/y-quill.js';

// Fester Shared-Type-Name, muss exakt dem serverseitigen Namen entsprechen
// (app/services/lagedokument_collab.py: doc.get("content", type=Text)).
const YTEXT_NAME = 'content';

// Deterministische Farbe je Nutzer-ID (gleicher Nutzer -> gleiche Cursorfarbe
// in jeder Sitzung), kein Server-Roundtrip noetig.
const PRESENCE_PALETTE = [
  '#4f8cff', '#ff6b6b', '#51cf66', '#f59f00', '#cc5de8',
  '#20c997', '#ff922b', '#5c7cfa', '#e64980', '#15aabf',
];
function colorForUserId(userId) {
  const idx = Math.abs(Number(userId) || 0) % PRESENCE_PALETTE.length;
  return PRESENCE_PALETTE[idx];
}

export function initLagedokumentCollab({ lageId, quill, userId, userName, onPresenceChange }) {
  const ydoc = new Y.Doc();
  const ytext = ydoc.getText(YTEXT_NAME);

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const serverUrl = protocol + '//' + location.host + '/ws/lagedokument';
  const provider = new WebsocketProvider(serverUrl, String(lageId), ydoc, {});
  const awareness = provider.awareness;

  if (userName) {
    awareness.setLocalStateField('user', {
      name: userName,
      color: colorForUserId(userId),
    });
  }

  let binding = null;
  provider.on('sync', function (isSynced) {
    if (isSynced && !binding) {
      // Erst NACH dem ersten erfolgreichen Sync binden, sonst wuerde Quill
      // kurzzeitig ein leeres Dokument anzeigen, bevor der Server-Stand da ist.
      binding = new QuillBinding(ytext, quill, awareness);
    }
  });

  if (typeof onPresenceChange === 'function') {
    const notify = function () {
      const seen = new Set();
      const others = [];
      awareness.getStates().forEach(function (state, clientId) {
        if (clientId !== awareness.clientID && state && state.user && !seen.has(state.user.name)) {
          seen.add(state.user.name);
          others.push(state.user);
        }
      });
      onPresenceChange(others);
    };
    awareness.on('change', notify);
    notify();
  }

  return { ydoc, ytext, provider, awareness };
}
