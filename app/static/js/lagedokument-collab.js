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

export function initLagedokumentCollab({ lageId, quill, userName, userColor }) {
  const ydoc = new Y.Doc();
  const ytext = ydoc.getText(YTEXT_NAME);

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const serverUrl = protocol + '//' + location.host + '/ws/lagedokument';
  const provider = new WebsocketProvider(serverUrl, String(lageId), ydoc, {});

  if (userName) {
    provider.awareness.setLocalStateField('user', {
      name: userName,
      color: userColor || '#4f8cff',
    });
  }

  let binding = null;
  provider.on('sync', function (isSynced) {
    if (isSynced && !binding) {
      // Erst NACH dem ersten erfolgreichen Sync binden, sonst wuerde Quill
      // kurzzeitig ein leeres Dokument anzeigen, bevor der Server-Stand da ist.
      binding = new QuillBinding(ytext, quill, provider.awareness);
    }
  });

  return { ydoc, ytext, provider };
}
