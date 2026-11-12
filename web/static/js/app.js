/**
 * PlexCache-D Web UI JavaScript
 * Shared utilities and HTMX error handling
 */

// Handle HTMX errors
document.addEventListener('htmx:responseError', function(event) {
    var alertContainer = document.getElementById('alert-container');
    if (alertContainer) {
        var article = document.createElement('article');
        article.className = 'alert alert-error';
        article.textContent = 'Request failed: ' + event.detail.xhr.status + ' ' + event.detail.xhr.statusText;
        var btn = document.createElement('button');
        btn.className = 'close';
        btn.textContent = '\u00d7';
        btn.onclick = function() { article.remove(); };
        article.appendChild(btn);
        alertContainer.innerHTML = '';
        alertContainer.appendChild(article);
    }
});

// Auto-dismiss alerts marked with `.alert-auto-dismiss`.
// Centralized here so every success/info action-result alert — regardless of
// which router or template emits it — fades out after 4s without each caller
// duplicating the setTimeout. Runs on initial load and after every HTMX swap.
function _pcScheduleAutoDismiss(root) {
    var scope = root || document;
    var alerts = scope.querySelectorAll('.alert-auto-dismiss');
    for (var i = 0; i < alerts.length; i++) {
        var el = alerts[i];
        if (el.dataset.autoDismissScheduled === '1') continue;
        el.dataset.autoDismissScheduled = '1';
        (function(alert) {
            setTimeout(function() {
                alert.classList.add('alert-fade-out');
                setTimeout(function() { if (alert.parentNode) alert.remove(); }, 300);
            }, 4000);
        })(el);
    }
}
document.addEventListener('DOMContentLoaded', function() { _pcScheduleAutoDismiss(document); });
document.addEventListener('htmx:afterSettle', function(e) { _pcScheduleAutoDismiss(e.target); });

// Handle showAlert event from HX-Trigger response header
document.addEventListener('showAlert', function(event) {
    var detail = event.detail || {};
    var type = detail.type || 'warning';
    var message = detail.message || 'Something went wrong';
    var alertContainer = document.getElementById('alert-container');
    if (alertContainer) {
        var safeType = ['success', 'error', 'warning', 'info'].indexOf(type) !== -1 ? type : 'warning';
        var iconName = safeType === 'success' ? 'check-circle' : safeType === 'error' ? 'alert-circle' : 'alert-triangle';

        var div = document.createElement('div');
        div.className = 'alert alert-' + safeType;
        div.id = 'hx-trigger-alert';

        var icon = document.createElement('i');
        icon.setAttribute('data-lucide', iconName);
        div.appendChild(icon);

        var span = document.createElement('span');
        span.textContent = message;
        div.appendChild(span);

        alertContainer.innerHTML = '';
        alertContainer.appendChild(div);
        lucide.createIcons();
        setTimeout(function() {
            var el = document.getElementById('hx-trigger-alert');
            if (el) el.remove();
        }, 5000);
    }
});

// Path-mapping view/edit toggle. Shared by settings/libraries, settings/paths,
// and the path_mapping_card partial (which is HTMX-swapped — relying on the
// globally-loaded function avoids redefining it per card render).
function toggleEditMode(index) {
    var view = document.getElementById('mapping-view-' + index);
    var edit = document.getElementById('mapping-edit-' + index);
    if (!view || !edit) return;
    if (view.style.display === 'none') {
        view.style.display = 'block';
        edit.style.display = 'none';
    } else {
        view.style.display = 'none';
        edit.style.display = 'block';
    }
    lucide.createIcons();
}

// Format a byte count like core.system_utils.format_bytes (1024-based, 2 decimals,
// e.g. "42.93 GB"), so sizes computed in the browser match the ones the server renders.
// settings/cache.html declares its own GB/TB-only formatBytes() before this file loads;
// leave that one alone so its output does not change.
if (typeof window.formatBytes !== 'function') {
    window.formatBytes = function formatBytes(bytes) {
        var size = Number(bytes) || 0;
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var i = 0;
        while (size >= 1024 && i < units.length - 1) {
            size /= 1024;
            i++;
        }
        return i === 0 ? Math.round(size) + ' B' : size.toFixed(2) + ' ' + units[i];
    };
}

// Operation pill keyboard access. Pills are focusable (tabindex="0"); Enter or
// Space acts like a click (expand / detail / collapse), Escape collapses and
// returns focus to the pill. Delegated once here because the pill markup is
// swapped by HTMX every few seconds.
document.addEventListener('keydown', function(e) {
    var banner = document.getElementById('global-operation-banner');
    if (!banner || !banner.contains(e.target)) return;
    var pill = e.target.classList && e.target.classList.contains('di-pill') ? e.target : null;
    if (pill && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault();
        pill.click();
    } else if (e.key === 'Escape' && typeof diSmoothSetState === 'function') {
        diSmoothSetState('false');
        var focusPill = banner.querySelector('.di-pill');
        if (focusPill) focusPill.focus();
    }
});

// The Dashboard header used to keep its own Verbose toggle under a separate key.
// Carry a saved preference over to the pill's setting once, then drop the old key.
try {
    var legacyVerbose = localStorage.getItem('plexcache_dashboard_verbose');
    if (legacyVerbose !== null) {
        if (localStorage.getItem('verbose_mode') === null) {
            localStorage.setItem('verbose_mode', legacyVerbose === 'true' ? 'true' : 'false');
        }
        localStorage.removeItem('plexcache_dashboard_verbose');
    }
} catch (e) { /* storage unavailable: nothing to migrate */ }

// Themed confirm dialog. pcConfirm({title, message, confirmLabel, danger})
// resolves true/false. Every hx-confirm in the app goes through it via the
// htmx:confirm hook below, so no page shows the browser's own confirm() box.
// Optional attributes on the hx-confirm element: data-confirm-title,
// data-confirm-label (defaults to the button's text) and data-confirm-danger
// (implied by .btn-danger) for a red confirm button.
(function() {
    var modal = null, els = {}, resolver = null, opener = null;

    function build() {
        modal = document.createElement('div');
        modal.className = 'modal pc-confirm';
        modal.style.display = 'none';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'pc-confirm-title');
        modal.innerHTML =
            '<div class="modal-backdrop"></div>' +
            '<div class="modal-content">' +
                '<div class="modal-header">' +
                    '<h3 id="pc-confirm-title"></h3>' +
                    '<button type="button" class="modal-close" aria-label="Close"><i data-lucide="x"></i></button>' +
                '</div>' +
                '<div class="modal-body"><p class="pc-confirm-message"></p></div>' +
                '<div class="modal-footer">' +
                    '<button type="button" class="btn btn-secondary pc-confirm-cancel">Cancel</button>' +
                    '<button type="button" class="btn pc-confirm-ok"></button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(modal);
        els.title = modal.querySelector('#pc-confirm-title');
        els.message = modal.querySelector('.pc-confirm-message');
        els.ok = modal.querySelector('.pc-confirm-ok');
        els.cancel = modal.querySelector('.pc-confirm-cancel');
        els.ok.addEventListener('click', function() { close(true); });
        els.cancel.addEventListener('click', function() { close(false); });
        modal.querySelector('.modal-close').addEventListener('click', function() { close(false); });
        modal.querySelector('.modal-backdrop').addEventListener('click', function() { close(false); });
        // Capture phase so Escape/Tab stay inside the dialog while it is open.
        document.addEventListener('keydown', function(e) {
            if (!resolver) return;
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                close(false);
            } else if (e.key === 'Tab') {
                var items = modal.querySelectorAll('button');
                var first = items[0], last = items[items.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
                else if (!modal.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
            }
        }, true);
    }

    function close(result) {
        if (!resolver) return;
        var done = resolver;
        resolver = null;
        modal.style.display = 'none';
        if (opener && document.contains(opener)) opener.focus();
        opener = null;
        done(result);
    }

    window.pcConfirm = function(opts) {
        opts = opts || {};
        if (!document.body) return Promise.resolve(window.confirm(opts.message || ''));
        if (!modal) build();
        if (resolver) close(false);
        els.title.textContent = opts.title || 'Please confirm';
        els.message.textContent = opts.message || '';
        els.ok.textContent = opts.confirmLabel || 'Confirm';
        els.ok.className = 'btn pc-confirm-ok ' + (opts.danger ? 'btn-danger' : 'btn-primary');
        opener = document.activeElement;
        modal.style.display = 'flex';
        if (typeof lucide !== 'undefined') lucide.createIcons();
        // Like the browser dialog, Enter confirms; a destructive action starts on Cancel.
        (opts.danger ? els.cancel : els.ok).focus();
        return new Promise(function(resolve) { resolver = resolve; });
    };

    function labelFor(elt, evt) {
        var source = elt.getAttribute('data-confirm-label');
        if (source) return source;
        var button = (evt && evt.submitter) || (elt.tagName === 'FORM' ? elt.querySelector('[type="submit"]') : elt);
        var text = button ? button.textContent.replace(/\s+/g, ' ').trim() : '';
        return text && text.length <= 30 ? text : 'Confirm';
    }

    document.addEventListener('htmx:confirm', function(e) {
        var question = e.detail.question;
        if (!question) return;
        e.preventDefault();
        var elt = e.detail.elt;
        var source = elt.closest('[hx-confirm]') || elt;
        var button = (e.detail.triggeringEvent && e.detail.triggeringEvent.submitter) || elt;
        window.pcConfirm({
            title: source.getAttribute('data-confirm-title'),
            message: question,
            confirmLabel: labelFor(source, e.detail.triggeringEvent),
            danger: source.hasAttribute('data-confirm-danger') ||
                    !!(button.classList && button.classList.contains('btn-danger'))
        }).then(function(ok) {
            if (ok) e.detail.issueRequest(true);
        });
    });
})();

// Forms marked data-reset-on-success clear after a successful request.
// Handled here rather than with hx-on: htmx compiles hx-on handlers with
// Function(), which the Content-Security-Policy (no 'unsafe-eval') blocks.
// A response retargeted elsewhere (an error alert) leaves the input as typed.
// data-reset-clear="#selector" empties another element too.
document.addEventListener('htmx:afterRequest', function(e) {
    var form = e.detail.elt;
    if (!form || form.tagName !== 'FORM' || !form.hasAttribute('data-reset-on-success')) return;
    if (!e.detail.successful || e.detail.xhr.getResponseHeader('HX-Retarget')) return;
    form.reset();
    var clear = form.getAttribute('data-reset-clear');
    var target = clear && document.querySelector(clear);
    if (target) target.innerHTML = '';
});
