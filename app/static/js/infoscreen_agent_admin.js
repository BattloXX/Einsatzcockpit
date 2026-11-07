/* Dialog-Lebensdauer begrenzt HTMX-Polling; alle Texte bleiben reine Textwerte. */
(function () {
    'use strict';
    function closeDialog() {
        const dialog = document.getElementById('agent-dialog');
        if (!dialog) return;
        dialog.close();
        const body = document.getElementById('agent-dialog-body');
        if (window.htmx) {
            window.htmx.trigger(body, 'htmx:abort');
            body.querySelectorAll('[hx-get], [hx-post]').forEach(element => window.htmx.trigger(element, 'htmx:abort'));
        }
        body.replaceChildren();
    }
    function rotationValues(root) {
        return JSON.parse(root.querySelector('[data-rotation-value]').value);
    }
    function drawRotation(root, rows) {
        root.querySelector('[data-rotation-value]').value = JSON.stringify(rows);
        const container = root.querySelector('[data-rotation-rows]');
        container.replaceChildren();
        rows.forEach((row, index) => {
            const line = document.createElement('div');
            line.className = 'agent-form';
            const select = root.querySelector('[data-rotation-url]').cloneNode(true);
            select.removeAttribute('data-rotation-url');
            select.setAttribute('aria-label', 'Rotationsseite ' + (index + 1));
            const selected = row.builtin || String(row.url_id);
            if (!Array.from(select.options).some(option => option.value === selected)) {
                const option = document.createElement('option');
                option.value = selected;
                option.textContent = 'Nicht mehr aktive URL #' + selected;
                select.append(option);
            }
            select.value = selected;
            select.addEventListener('change', () => {
                rows[index] = select.value === 'weather' ? {builtin: 'weather', dwell_sec: row.dwell_sec} : {url_id: Number(select.value), dwell_sec: row.dwell_sec};
                drawRotation(root, rows);
            });
            const dwell = document.createElement('input');
            dwell.className = 'form-input';
            dwell.type = 'number'; dwell.min = '5'; dwell.max = '3600'; dwell.required = true;
            dwell.value = row.dwell_sec;
            dwell.setAttribute('aria-label', 'Verweildauer in Sekunden ' + (index + 1));
            dwell.addEventListener('input', () => {
                rows[index].dwell_sec = Number(dwell.value);
                root.querySelector('[data-rotation-value]').value = JSON.stringify(rows);
            });
            line.append(select, dwell);
            [['Nach oben', -1], ['Nach unten', 1], ['Entfernen', 0]].forEach(([label, direction]) => {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'btn'; button.textContent = label;
                button.disabled = direction === -1 && index === 0 || direction === 1 && index === rows.length - 1;
                button.addEventListener('click', () => {
                    if (direction === 0) rows.splice(index, 1);
                    else [rows[index], rows[index + direction]] = [rows[index + direction], rows[index]];
                    drawRotation(root, rows);
                });
                line.append(button);
            });
            container.append(line);
        });
    }
    function initEditors(root) {
        root.querySelectorAll('[data-rotation]').forEach(editor => drawRotation(editor, rotationValues(editor)));
    }
    document.addEventListener('DOMContentLoaded', () => initEditors(document));
    document.addEventListener('htmx:load', event => initEditors(event.detail.elt));
    document.addEventListener('htmx:configRequest', function (event) {
        const form = event.detail.elt.closest('form');
        if (!form) return;
        form.querySelectorAll('[data-replacements]').forEach(editor => {
            const rows = Array.from(editor.querySelectorAll('[data-replacement-row]')).map(row => {
                const value = {};
                row.querySelectorAll('[data-replacement-field]').forEach(field => {
                    value[field.dataset.replacementField] = field.dataset.replacementField === 'priority' ? Number(field.value) : field.value;
                });
                value.rotation_json = rotationValues(row.querySelector('[data-rotation]'));
                return value;
            });
            const value = JSON.stringify(rows);
            editor.querySelector('[data-replacement-value]').value = value;
            event.detail.parameters.slots_ersatz = value;
        });
    });
    document.addEventListener('click', async function (event) {
        if (event.target.closest('[data-agent-close]')) closeDialog();
        const addReplacement = event.target.closest('[data-replacement-add]');
        if (addReplacement) {
            const root = addReplacement.closest('[data-replacements]');
            if (root.querySelectorAll('[data-replacement-row]').length >= 200) return;
            const fragment = root.querySelector('template').content.cloneNode(true);
            initEditors(fragment);
            root.querySelector('[data-replacement-rows]').append(fragment);
        }
        const removeReplacement = event.target.closest('[data-replacement-remove]');
        if (removeReplacement) removeReplacement.closest('[data-replacement-row]').remove();
        const add = event.target.closest('[data-rotation-add]');
        if (add) {
            const root = add.closest('[data-rotation]');
            const message = root.querySelector('[data-rotation-error]');
            const value = rotationValues(root);
            const dwell = Number(root.querySelector('[data-rotation-dwell]').value);
            if (value.length >= 30 || !Number.isInteger(dwell) || dwell < 5 || dwell > 3600) {
                message.textContent = 'Maximal 30 Seiten, Verweildauer 5-3600 Sekunden.';
                return;
            }
            const url = root.querySelector('[data-rotation-url]').value;
            value.push(url === 'weather' ? {builtin: 'weather', dwell_sec: dwell} : {url_id: Number(url), dwell_sec: dwell});
            drawRotation(root, value);
            message.textContent = '';
        }
        const copy = event.target.closest('[data-agent-copy]');
        if (!copy) return;
        const root = copy.closest('.agent-pairing');
        const field = root.querySelector('[data-agent-command]');
        const status = root.querySelector('[data-agent-copy-status]');
        try {
            await navigator.clipboard.writeText(field.value);
            status.textContent = 'Einzeiler kopiert.';
        } catch (_) {
            field.focus();
            field.select();
            status.textContent = 'Text markiert. Bitte mit Strg+C oder dem Kopieren-Menue kopieren.';
        }
    });
    document.addEventListener('change', function (event) {
        if (!event.target.matches('[data-agent-env]')) return;
        const root = event.target.closest('.agent-pairing');
        root.querySelector('[data-agent-command]').value = root.querySelector(event.target.checked ? '[data-agent-command-env]' : '[data-agent-command-normal]').value;
    });
    document.addEventListener('htmx:beforeSwap', function (event) {
        if (!event.detail.target.closest('.agent-admin')) return;
        if (event.detail.xhr.status === 422) {
            const form = event.detail.requestConfig.elt.closest('form');
            if (form) {
                let error = form.querySelector('[data-agent-form-error]');
                if (!error) {
                    error = document.createElement('div');
                    error.dataset.agentFormError = '';
                    form.prepend(error);
                }
                event.detail.target = error;
                event.detail.swapOverride = 'innerHTML';
            }
            event.detail.shouldSwap = true;
            event.detail.isError = false;
        }
    });
    document.addEventListener('htmx:afterSwap', function (event) {
        const dialog = document.getElementById('agent-dialog');
        if (!dialog) return;
        if (event.detail.target.id === 'agent-dialog-body' && !dialog.open) dialog.showModal();
        if (['agent-pairing-status', 'agent-dialog-body'].includes(event.detail.target.id) && dialog.querySelector('[data-agent-paired]')) {
            dialog.querySelectorAll('[data-agent-command], [data-agent-command-normal], [data-agent-command-env]').forEach(field => { field.value = ''; });
            const copy = dialog.querySelector('[data-agent-copy]');
            if (copy) copy.disabled = true;
            if (window.htmx) window.htmx.trigger(document.body, 'agentChanged');
        }
    });
    document.addEventListener('cancel', function (event) {
        if (event.target.id === 'agent-dialog') { event.preventDefault(); closeDialog(); }
    }, true);
})();
