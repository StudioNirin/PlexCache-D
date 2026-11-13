/* PlexCache operation pill ("Dynamic Island") motion engine.
 *
 * Loaded once from base.html. The banner template (components/
 * global_operation_banner.html) only holds markup and small per-state scripts;
 * it is swapped by htmx polls every few seconds, so nothing here may live in it.
 *
 * Design: the pill is an elastic bubble that shape-shifts between states (see
 * Apple's Live Activities guidance and WWDC23 "Design dynamic Live
 * Activities" / "Animate with springs"). One spring drives a whole change, the
 * bubble and everything inside it. Content that exists in both views travels
 * between them; everything else contracts into / extends out of the bubble.
 *
 * Adding a pill state (markup only, no JS needed):
 *   - give it .di-pill__compact and .di-pill__expanded views and data-state;
 *   - mark the same thing in both views with data-di-shared="<key>" (icon,
 *     label, run, spinner, ...) so it travels instead of fading; add
 *     data-di-anchor to a child to line up by that child (the icon circle is
 *     aligned by its glyph);
 *   - data-di-num="<key>" on a changing number makes it roll, data-di-bar on a
 *     progress bar makes it glide between polls;
 *   - call diAlert(selector) once per event for something that must not be
 *     missed (expands the pill and pulses the selector).
 *
 * Public API (used by the template's markup and scripts):
 *   diGetExpandState, diSetExpandState, diSmoothSetState, diMinimize,
 *   diHoverIn, diHoverOut, diPillClick, diAlert, diDismiss(url), diTriggerRun(dryRun),
 *   bannerToggleShow, showBannerWarning, window._diHoverTimer;
 *   PillMotion.config  (tuning: bounce, blur, contract, durations, alert hold)
 *   PillMotion.spring  (duration, bounce) -> { easing, total }
 */
(function () {
    var cfg = {
        bounce: 0.25,       /* spring bounce for every motion (Apple: ~0.3 is clearly bouncy) */
        blur: 3,            /* px: content swaps fade through a slight blur */
        contract: true,     /* unshared content contracts into / extends out of the bubble */
        expandMs: 380,      /* perceived duration of an expand */
        collapseMs: 280,    /* perceived duration of a collapse */
        exitOverlapMs: 100, /* non-morph collapse: start the flip this far into the exit fade */
        alertHoldMs: 6000   /* an alert stays expanded this long unless hovered */
    };

    function showBannerWarning(message) {
        var warning = document.getElementById('di-warning');
        var pill = document.querySelector('.di-pill');
        var textEl = document.getElementById('di-warning-text');
        if (!warning || !pill || !textEl) return;
        textEl.textContent = message;
        // Slow HTMX polling so warning isn't replaced before user sees it
        var pollEl = document.querySelector('#global-operation-banner [hx-trigger]');
        if (pollEl) { pollEl.setAttribute('hx-trigger', 'every 8s'); htmx.process(pollEl); }
        requestAnimationFrame(function() { requestAnimationFrame(function() {
            warning.style.maxHeight = '2.5rem';
            warning.style.maxWidth = '40rem';
            warning.style.opacity = '1';
            warning.style.paddingTop = '0.6rem';
            warning.style.marginTop = '0.6rem';
            warning.style.borderColor = 'color-mix(in srgb, var(--plex-warning) 30%, transparent)';
        }); });
        setTimeout(function() {
            if (!warning) return;
            warning.style.maxHeight = '0';
            warning.style.maxWidth = '0';
            warning.style.opacity = '0';
            warning.style.paddingTop = '0';
            warning.style.marginTop = '0';
            warning.style.borderColor = 'transparent';
        }, 4000);
    }

    /* ── Expand/Collapse interaction ───────────────────────────────────────
       Expansion state is stored as data-di-expanded on the OUTER
       #global-operation-banner div (NOT replaced by HTMX innerHTML swaps).
       CSS reads this attribute directly — no JS class reapplication needed,
       so there's zero flicker when HTMX polls replace the inner content. */

    function diGetExpandState() {
        var outer = document.getElementById('global-operation-banner');
        if (!outer) return 'false';
        /* Mid-morph, report where the pill is heading (an interrupted morph
           heads back to where it came from) */
        if (outer._flip) return outer._flip.target === 'end' ? outer._flip.toState : outer._flip.fromState;
        return outer.getAttribute('data-di-expanded') || 'false';
    }
    /* Public instant setter (dismiss handlers, Run, session restore, alerts):
       finishes any animation in flight first, so its temporary positioning
       never lingers on the new state. */
    function diSetExpandState(state) {
        var outer = document.getElementById('global-operation-banner');
        if (outer && outer._flipCleanup) outer._flipCleanup();
        _diApplyExpandState(state);
    }
    /* Raw setter: the animation itself swaps the views with this mid-flight */
    function _diApplyExpandState(state) {
        var outer = document.getElementById('global-operation-banner');
        if (outer) outer.setAttribute('data-di-expanded', state);
        // Also apply classes for JS-generated pills (completion banners)
        var pill = document.querySelector('#global-operation-banner .di-pill');
        if (pill) {
            pill.setAttribute('aria-expanded', state === 'tier2' || state === 'tier3' ? 'true' : 'false');
            pill.classList.remove('di-expanded', 'di-detail');
            if (state === 'tier2') pill.classList.add('di-expanded');
            if (state === 'tier3') { pill.classList.add('di-expanded'); pill.classList.add('di-detail'); }
        }
    }

    /* ── FLIP animation engine ───────────────────────────────────────
       Smooth expand/collapse inspired by Apple Dynamic Island.
       Uses FLIP (First-Last-Invert-Play) to animate display:none swaps.
       diSetExpandState (above) is the instant/raw setter — used by
       dismiss handlers, session restore, and HTMX callbacks.
       diSmoothSetState (below) is the animated version — used by
       hover, click, and minimize interactions only. */


    /* ── Springs ─────────────────────────────────────────────────────
       Set like SwiftUI's spring(duration:bounce:) (WWDC23 "Animate with
       springs"): duration is how long the motion is perceived to take, and bounce
       0 settles without overshoot, ~0.15 is barely bouncy, ~0.3 clearly bouncy,
       and above ~0.4 starts to look exaggerated. One spring drives a whole state
       change, the bubble and everything inside it, as in SwiftUI where every
       change in a transaction shares its animation: each element's give is then
       in proportion to how far it moves. That only reads as one organism if
       everything moves, which is why content that is not shared between the views
       contracts into (and extends out of) the bubble instead of fading in place
       (_diMorphStart). Both directions use the same spring. */

    var _diSpringCache = {};
    /* A spring as a CSS easing: { easing, total }, where total is how long (ms) the
       transition must run to settle. linear() samples the curve; browsers without
       it get a plain ease-out of the perceived duration. */
    function _diSpring(durationMs, bounce) {
        bounce = Math.max(0, Math.min(Number(bounce) || 0, 0.9));
        var key = durationMs + '|' + bounce;
        if (_diSpringCache[key]) return _diSpringCache[key];
        var spring;
        if (!(window.CSS && CSS.supports && CSS.supports('transition-timing-function', 'linear(0, 1)'))) {
            spring = { easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', total: durationMs };
        } else {
            var d = durationMs / 1000, w0 = 2 * Math.PI / d, zeta = 1 - bounce;
            var x = function(t) {
                if (zeta >= 1) return 1 - Math.exp(-w0 * t) * (1 + w0 * t);
                var wd = w0 * Math.sqrt(1 - zeta * zeta);
                return 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + (zeta * w0 / wd) * Math.sin(wd * t));
            };
            var step = d / 200, settled = d;
            for (var t = 0; t < 6 * d; t += step) { if (Math.abs(1 - x(t)) > 0.001) settled = t + step; }
            var n = 48, pts = [];
            for (var i = 0; i < n; i++) pts.push(x(settled * i / n).toFixed(4));
            pts.push('1');
            spring = { easing: 'linear(' + pts.join(', ') + ')', total: Math.round(settled * 1000) };
        }
        _diSpringCache[key] = spring;
        return spring;
    }

    /* The OS "reduce motion" setting: the pill changes state without animating
       (CSS covers the content animations; see custom.css). */
    function _diReducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    function diSmoothSetState(state) {
        var outer = document.getElementById('global-operation-banner');
        var pill = outer ? outer.querySelector('.di-pill') : null;
        if (!outer || !pill) { diSetExpandState(state); return; }

        var currentState = diGetExpandState();
        if (currentState === state) return;

        /* An in-flight morph between these two states turns round from where
           it is rather than snapping to its end first */
        var flip = outer._flip;
        if (flip && (state === flip.fromState || state === flip.toState) && !_diReducedMotion()) {
            flip.toward(state === flip.toState ? 'end' : 'start');
            return;
        }

        /* Cancel any in-flight animations (stored on outer — survives HTMX swaps) */
        if (outer._flipCleanup) { outer._flipCleanup(); outer._flipCleanup = null; }
        if (outer._exitTimeout) { clearTimeout(outer._exitTimeout); outer._exitTimeout = null; }

        if (_diReducedMotion()) { diSetExpandState(state); return; }

        var tiers = { 'false': 0, 'tier2': 1, 'tier3': 2 };
        var isExpanding = tiers[state] > tiers[currentState];

        if (isExpanding) {
            /* ── EXPAND: FLIP immediately ── */
            _diDoFlip(outer, pill, currentState, state, true);
        } else {
            /* ── COLLAPSE: exit-animate content, then FLIP after overlap ── */
            var isDetailOnly = (currentState === 'tier3' && state === 'tier2');
            var exitTarget = isDetailOnly
                ? pill.querySelector('.di-pill__detail')
                : pill.querySelector('.di-pill__expanded');

            /* Play exit animation (reverse of enter: scale 1→0.92, opacity 1→0) */
            if (exitTarget) {
                exitTarget.classList.remove('di-content-enter', 'di-content-exit');
                void exitTarget.offsetWidth;
                exitTarget.classList.add('di-content-exit');
            }

            /* Hide progress bar immediately so it can't slide during collapse */
            var pb = pill.querySelector('.di-pill__progress');
            if (pb) { pb.style.transition = 'none'; pb.style.opacity = '0'; }

            /* Back to compact morphs (every pill): the morph fades the expanded
               view itself, so skip the exit pre-animation and its delay. Only
               closing the detail panel (tier3 -> tier2) uses the exit fade. */
            if (state === 'false') {
                if (exitTarget) exitTarget.classList.remove('di-content-exit');
                _diDoFlip(outer, pill, currentState, state, false);
                return;
            }

            /* Start FLIP partway through exit — they overlap seamlessly */
            outer._exitTimeout = setTimeout(function() {
                outer._exitTimeout = null;
                var freshPill = outer.querySelector('.di-pill');
                if (!freshPill) { diSetExpandState(state); return; }
                if (exitTarget) exitTarget.classList.remove('di-content-exit');
                _diDoFlip(outer, freshPill, currentState, state, false);
            }, cfg.exitOverlapMs);
        }
    }

    /* ── Shared-element morph ────────────────────────────────────────
       Elements with the same data-di-shared key in the compact and the expanded
       view are one thing shown twice (the "PlexCache" label, its icon, the Run
       button). On a compact <-> expanded change they travel between the two
       spots on the pill's own easing instead of one fading out while the other
       fades in. The old view stays on screen as an overlay at its last position
       and fades out while the new view fades in; each shared pair moves along
       one path and crossfades, so a change of size or weight reads as one
       element restyling itself. Positions are kept relative to the pill's
       top-centre, which stays put while the pill grows or shrinks around it.
       A shared element can mark a child with data-di-anchor to be lined up by
       that child instead of its own box: the expanded icon's tinted circle is
       the shared element (so it travels with the icon), lined up by the glyph
       inside it against the compact view's bare glyph. */

    function _diViewFor(pill, state) {
        return pill.querySelector(state === 'false' ? '.di-pill__compact' : '.di-pill__expanded');
    }

    function _diPillFrame(pill) {
        var r = pill.getBoundingClientRect();
        /* k undoes the pill's hover scale(1.02) so positions are in layout px */
        return { cx: r.left + r.width / 2, top: r.top, k: pill.offsetWidth ? r.width / pill.offsetWidth : 1 };
    }

    function _diRel(el, frame) {
        var r = el.getBoundingClientRect();
        return { x: (r.left - frame.cx) / frame.k, y: (r.top - frame.top) / frame.k,
                 w: r.width / frame.k, h: r.height / frame.k };
    }

    /* Before the state swap: where the old view and its shared elements are. */
    function _diMorphCapture(pill, fromState, toState) {
        if ((fromState === 'false') === (toState === 'false')) return null;  /* tier2 <-> tier3 */
        var oldView = _diViewFor(pill, fromState);
        var newView = _diViewFor(pill, toState);
        if (!oldView || !newView) return null;
        var frame = _diPillFrame(pill);
        var pairs = [];
        oldView.querySelectorAll('[data-di-shared]').forEach(function(el) {
            var key = el.getAttribute('data-di-shared');
            var to = newView.querySelector('[data-di-shared="' + key + '"]');
            if (!to) return;
            var anchor = el.querySelector('[data-di-anchor]');
            var box = _diRel(el, frame);
            /* a: what lines up (the anchor if any); aBox: the box that moves */
            pairs.push({ from: el, to: to, aBox: box, a: anchor ? _diRel(anchor, frame) : box });
        });
        var ocs = getComputedStyle(oldView);
        var box = _diRel(oldView, frame);
        return {
            oldView: oldView, newView: newView, pairs: pairs,
            /* x: offset of the view's left edge from the pill's centre, which
               stays put while the pill resizes. Measured, not assumed centred:
               the compact idle pill's padding is asymmetric (12px / 8px). */
            view: { top: box.y - parseFloat(getComputedStyle(pill).borderTopWidth || 0), x: box.x, width: box.w,
                    display: ocs.display, direction: ocs.flexDirection, gap: ocs.gap }
        };
    }

    /* After the swap, with the pill in its end layout: where they land. */
    function _diMorphMeasureEnd(morph, pill) {
        var frame = _diPillFrame(pill);
        morph.pairs.forEach(function(p) {
            p.anchor = p.to.querySelector('[data-di-anchor]') || p.to;
            p.b = _diRel(p.anchor, frame);
        });
        var box = _diRel(morph.newView, frame);
        morph.newBox = { top: box.y - parseFloat(getComputedStyle(pill).borderTopWidth || 0), x: box.x, width: box.w };
    }

    /* Hold a view at a fixed offset from the pill's top-centre, which stays put
       while the pill resizes: out of the flow, so the pill's animating size and
       padding cannot move it. */
    function _diPinView(el, box) {
        el.style.position = 'absolute';
        el.style.top = box.top + 'px';
        el.style.left = '50%';
        el.style.width = box.width + 'px';
        el.style.margin = '0';
        el.style.transform = 'translateX(' + box.x + 'px)';
    }

    function _diUnpinView(el) {
        ['position', 'top', 'left', 'width', 'margin', 'transform'].forEach(function(p) { el.style[p] = ''; });
    }

    /* With the pill rewound to its start size: pin the old view where it was (as
       an overlay) and the new view where it will end up, put each new shared
       element on its old counterpart, then let both glide. Pinning the new view
       keeps each element's motion its own: left in the flow, the new view's
       resting spot would also move with the pill's animating size and padding
       (the compact row is centred in a shrinking pill), on top of its travel. */
    /* The blocks of a view that are not shared with the other view: the largest
       subtrees that contain no paired element (title, nudge, Verbose, Dry Run,
       ...). An element marked shared whose key has no partner in the other view
       (the spinner when a run finishes) counts as unshared. Skips anything
       without a box (hidden rows, display:contents). */
    function _diLooseBlocks(view, paired) {
        function isPaired(el) {
            return el.hasAttribute('data-di-shared') && paired.indexOf(el.getAttribute('data-di-shared')) !== -1;
        }
        function hasPaired(el) {
            return Array.prototype.some.call(el.querySelectorAll('[data-di-shared]'), isPaired);
        }
        var blocks = [];
        (function walk(el) {
            Array.prototype.forEach.call(el.children, function(child) {
                if (isPaired(child)) return;
                if (hasPaired(child)) { walk(child); return; }
                var r = child.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) blocks.push(child);
            });
        })(view);
        return blocks;
    }

    function _diMorphStart(morph, pill, perceived, spring, size) {
        var v = morph.oldView, box = morph.view;
        v.style.setProperty('display', box.display, 'important');
        v.style.flexDirection = box.direction;
        v.style.gap = box.gap;
        _diPinView(v, box);
        v.style.pointerEvents = 'none';
        v.style.zIndex = '2';
        _diPinView(morph.newView, morph.newBox);

        /* Elements move on the individual translate and scale properties, not on
           transform, about their centre. A shared element's own animation then
           still applies: the spinner's transform: rotate() is applied first, so
           it keeps turning in place while it travels, instead of the spin
           animation overriding the move (a CSS animation beats an inline
           transform). Uniform scale by height, aligned on the left edge and
           vertical centre. With the origin at the box centre c, a point q lands
           at c + t + s * (q - c), so t puts the anchor's top-left on the target. */
        function place(el, box, anchor, target, s) {
            var cx = box.x + box.w / 2, cy = box.y + box.h / 2;
            el.style.translate = (target.x - cx - s * (anchor.x - cx)) + 'px ' +
                                 (target.y - cy - s * (anchor.y - cy)) + 'px';
            el.style.scale = String(s);
        }
        /* Contract / extend: an unshared block's spot mapped into the other
           pill's box (the expanded layout squeezed into the compact pill, from the
           pill's top-centre), slightly shrunk. Leaving blocks are drawn in to it as
           the bubble contracts; arriving blocks bloom out of it as it extends. */
        function squeeze(el, from, to) {
            var b = _diRel(el, frame);
            var cx = b.x + b.w / 2, cy = b.y + b.h / 2;
            var sx = to.w / from.w, sy = to.h / from.h;
            el.style.translate = (cx * sx - cx) + 'px ' + (cy * sy - cy) + 'px';
            el.style.scale = String(Math.max(0.5, Math.min(0.9, Math.sqrt(sx * sy))));
        }
        var frame = _diPillFrame(pill);
        var startBox = { w: size.startW, h: size.startH }, endBox = { w: size.endW, h: size.endH };
        var paired = morph.pairs.map(function(p) { return p.from.getAttribute('data-di-shared'); });
        var leaving = cfg.contract ? _diLooseBlocks(v, paired) : [];
        var arriving = cfg.contract ? _diLooseBlocks(morph.newView, paired) : [];
        morph.blocks = leaving.concat(arriving);
        morph.pairs.forEach(function(p) {
            var box0 = _diRel(p.to, frame);  /* layout position at the start */
            var anc0 = p.anchor === p.to ? box0 : _diRel(p.anchor, frame);
            [p.to, p.from].forEach(function(el) {
                el.style.transition = 'none';
                el.style.transformOrigin = '50% 50%';
            });
            place(p.to, box0, anc0, p.a, p.a.h / anc0.h);
            p.toStart = _diGetPose(p.to);
            _diSetPose(p.from, IDENTITY);
        });
        morph.blocks.forEach(function(el) {
            el.style.transition = 'none';
            el.style.transformOrigin = '50% 50%';
        });
        leaving.forEach(function(el) { _diSetPose(el, IDENTITY); });
        arriving.forEach(function(el) { squeeze(el, endBox, startBox); el._diStart = _diGetPose(el); });
        pill.offsetHeight;  /* commit the start positions */
        var t = 'translate ' + spring.total + 'ms ' + spring.easing + ', scale ' + spring.total + 'ms ' + spring.easing;
        morph.pairs.forEach(function(p) {
            p.to.style.transition = t;
            _diSetPose(p.to, IDENTITY);
            /* Same rule in reverse: the old anchor ends on the new one */
            p.from.style.transition = t;
            place(p.from, p.aBox, p.a, p.b, p.b.h / p.a.h);
            p.fromEnd = _diGetPose(p.from);
        });
        leaving.forEach(function(el) { el.style.transition = t; squeeze(el, startBox, endBox); el._diEnd = _diGetPose(el); });
        arriving.forEach(function(el) { el.style.transition = t; _diSetPose(el, IDENTITY); });

        /* Complementary fades (same curve), so a pair never dips mid-crossfade,
           through a slight blur: it softens the moment two copies overlap. The
           leaving view is gone within the first part of the motion, so what it
           takes with it never crosses what is arriving. */
        var fade = { duration: Math.round(perceived * 0.6), easing: 'ease-in-out' };
        var blur = 'blur(' + cfg.blur + 'px)';
        /* fill both: either end holds while the motion settles, so a reversed
           fade cannot snap back to full opacity before cleanup */
        morph.anims = [
            v.animate([{ opacity: 1, filter: 'blur(0px)' }, { opacity: 0, filter: blur }],
                      Object.assign({ fill: 'both' }, fade)),
            morph.newView.animate([{ opacity: 0, filter: blur }, { opacity: 1, filter: 'blur(0px)' }],
                                  Object.assign({ fill: 'both' }, fade))
        ];

        /* Interruption: send every motion toward either end from wherever it is
           now (a CSS transition starts from the current value), and run the
           fades the other way. 'start' plays the change back. */
        morph.toward = function(which) {
            var back = which === 'start';
            morph.pairs.forEach(function(p) {
                _diSetPose(p.to, back ? p.toStart : IDENTITY);
                _diSetPose(p.from, back ? IDENTITY : p.fromEnd);
            });
            leaving.forEach(function(el) { _diSetPose(el, back ? IDENTITY : el._diEnd); });
            arriving.forEach(function(el) { _diSetPose(el, back ? el._diStart : IDENTITY); });
            morph.anims.forEach(function(a) {
                a.updatePlaybackRate(back ? -1 : 1);
                if (a.playState !== 'running') a.play();
            });
        };
    }

    var IDENTITY = { translate: '0px 0px', scale: '1' };
    function _diGetPose(el) { return { translate: el.style.translate, scale: el.style.scale }; }
    function _diSetPose(el, pose) { el.style.translate = pose.translate; el.style.scale = pose.scale; }

    /* ── State-to-state morph ────────────────────────────────────────
       When a poll brings a different state (Ready -> Caching -> Completed ->
       Ready), the bubble reshapes from the old pill into the new one instead of
       the content being swapped and slid in. Same machinery as compact <->
       expanded: before the swap the outgoing pill is snapshotted and its
       compact view cloned; between the swap and the settle (where the new
       state's scripts run, e.g. the completion pill builds itself) the clone
       holds the old look; then the new pill animates from the old geometry,
       shared keys (icon, label, ...) travel, and everything else contracts out
       of / extends into the bubble. Works from whichever view is showing: a
       phase change while the pill is open (Caching -> Restoring) reshapes the
       expanded bubble the same way. */
    var _diPrev = null;

    function _diSnapshotPill(outer) {
        var pill = outer.querySelector('.di-pill');
        if (!pill || outer._flipCleanup || _diReducedMotion()) return null;
        var expand = diGetExpandState();
        var view = _diViewFor(pill, expand);  /* whichever view is showing */
        var cs = getComputedStyle(pill);
        if (!view || !pill.offsetHeight || cs.opacity === '0') return null;
        var frame = _diPillFrame(pill);
        var box = _diRel(view, frame);
        var shared = {};
        view.querySelectorAll('[data-di-shared]').forEach(function(el) {
            var anchor = el.querySelector('[data-di-anchor]');
            var b = _diRel(el, frame);
            shared[el.getAttribute('data-di-shared')] = { aBox: b, a: anchor ? _diRel(anchor, frame) : b };
        });
        /* The clone keeps the old pill's colour classes (accents) and id
           (id-scoped layout), on a wrapper with every box style reset */
        var wrap = document.createElement('div');
        pill.classList.forEach(function(c) { if (c.indexOf('di-pill--') === 0) wrap.classList.add(c); });
        wrap.setAttribute('aria-hidden', 'true');
        wrap.style.cssText = 'height:auto;min-width:0;padding:0;margin:0;border:0;border-radius:0;' +
                             'background:none;box-shadow:none;animation:none;transform:none';
        wrap.appendChild(view.cloneNode(true));
        return {
            id: pill.id, wrap: wrap, shared: shared, expand: expand,
            size: { w: pill.offsetWidth, h: pill.offsetHeight },
            shape: { height: pill.offsetHeight + 'px', width: pill.offsetWidth + 'px',
                     borderRadius: cs.borderRadius, paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom },
            borderColor: cs.borderTopColor,
            view: { top: box.y - parseFloat(cs.borderTopWidth || 0), x: box.x, width: box.w,
                    display: 'block', direction: '', gap: '' }
        };
    }

    function _diHoldShape(pill, snap) {
        pill.style.transition = 'none';
        pill.style.minWidth = '0px';
        pill.style.overflow = 'hidden';
        Object.keys(snap.shape).forEach(function(k) { pill.style[k] = snap.shape[k]; });
    }

    /* afterSwap: keep showing the old pill until the new one has settled */
    function _diHold(outer, snap) {
        var pill = outer.querySelector('.di-pill');
        if (!pill) return;
        _diHoldShape(pill, snap);
        pill.classList.add('di-morphing');
        Array.prototype.forEach.call(pill.children, function(c) { c.style.visibility = 'hidden'; });
        _diPinView(snap.wrap, snap.view);
        pill.appendChild(snap.wrap);
        snap.held = pill;
    }

    function _diStateMorph(outer, snap) {
        var pill = outer.querySelector('.di-pill');
        var newView = pill && _diViewFor(pill, snap.expand);
        if (snap.wrap.parentNode) snap.wrap.parentNode.removeChild(snap.wrap);
        if (snap.held) {
            Array.prototype.forEach.call(snap.held.children, function(c) { c.style.visibility = ''; });
            ['transition', 'minWidth', 'overflow', 'height', 'width', 'borderRadius',
             'paddingTop', 'paddingBottom'].forEach(function(k) { snap.held.style[k] = ''; });
        }
        if (!pill || !newView || diGetExpandState() !== snap.expand || _diReducedMotion()) {
            _diPlace(outer);
            return;
        }
        if (snap.id && !document.getElementById(snap.id)) snap.wrap.id = snap.id;

        var perceived = cfg.expandMs;
        var spring = _diSpring(perceived, cfg.bounce);
        var progressBar = pill.querySelector('.di-pill__progress');

        /* End geometry, then hold the old one while everything is set up */
        var endCs = getComputedStyle(pill);
        var end = { height: pill.offsetHeight + 'px', width: pill.offsetWidth + 'px',
                    borderRadius: endCs.borderRadius, paddingTop: endCs.paddingTop,
                    paddingBottom: endCs.paddingBottom };
        var endBorder = endCs.borderTopColor;
        _diPinView(snap.wrap, snap.view);
        pill.appendChild(snap.wrap);
        var oldView = snap.wrap.querySelector(snap.expand === 'false' ? '.di-pill__compact' : '.di-pill__expanded');
        var pairs = [];
        Object.keys(snap.shared).forEach(function(key) {
            var from = oldView.querySelector('[data-di-shared="' + key + '"]');
            var to = newView.querySelector('[data-di-shared="' + key + '"]');
            if (from && to) pairs.push({ from: from, to: to, aBox: snap.shared[key].aBox, a: snap.shared[key].a });
        });
        var morph = { oldView: snap.wrap, newView: newView, pairs: pairs, view: snap.view };
        _diMorphMeasureEnd(morph, pill);
        _diHoldShape(pill, snap);
        if (progressBar) progressBar.style.opacity = '0';
        pill.offsetHeight;
        _diMorphStart(morph, pill, perceived, spring,
                      { startW: snap.size.w, startH: snap.size.h, endW: parseFloat(end.width), endH: parseFloat(end.height) });

        pill.style.transition = ['height', 'width', 'border-radius', 'padding-top', 'padding-bottom'].map(function(p) {
            return p + ' ' + spring.total + 'ms ' + spring.easing;
        }).join(', ');
        Object.keys(end).forEach(function(k) { pill.style[k] = end[k]; });
        /* A compact pill that grows into the page title slides clear of it on
           the same spring, instead of jumping on the next poll */
        if (snap.expand === 'false') {
            _diPlace(outer, { w: parseFloat(end.width), h: parseFloat(end.height) }, spring);
        }
        var border = pill.animate([{ borderColor: snap.borderColor }, { borderColor: endBorder }],
                                  { duration: perceived, easing: 'ease-in-out' });
        /* The new state's glow fades in over the second half */
        pill.classList.add('di-morphing');
        var glowTimer = setTimeout(function() { pill.classList.remove('di-morphing'); }, Math.round(perceived * 0.5));

        function cleanup() {
            _diMorphCleanup(morph);
            if (snap.wrap.parentNode) snap.wrap.parentNode.removeChild(snap.wrap);
            ['transition', 'minWidth', 'overflow', 'height', 'width', 'borderRadius',
             'paddingTop', 'paddingBottom'].forEach(function(k) { pill.style[k] = ''; });
            border.cancel();
            clearTimeout(glowTimer);
            pill.classList.remove('di-morphing');
            if (progressBar) progressBar.style.opacity = '';
            outer.style.transition = '';
            if (outer._flipCleanup === cleanup) outer._flipCleanup = null;
        }
        outer._flipCleanup = cleanup;
        setTimeout(function() { if (outer._flipCleanup === cleanup) cleanup(); }, spring.total + 50);
    }

    /* ── Placement in the page header (#216) ─────────────────────────
       The pill sits centred on top of the page, Dynamic-Island style. When a
       page's title, buttons or the theme toggle reach into that spot, it
       slides sideways just far enough to clear them and stays in the header
       row. Only when the row has no gap wide enough does it drop below the
       buttons as a full-width row of its own (.di-flow), moving back up as
       soon as there is room again.

       The slide is the banner's `translate` (its centring `transform` stays
       as it is). _diPlace works it out for a given pill size, so a state
       change that widens the pill, or opening and closing it, moves it on
       the reshape's own spring. The open pill is an overlay: it may cover
       the title and buttons, but not the theme toggle or the page edge, and
       it never switches fit mode. */
    var _DI_PLACE_GAP = 16;       /* clearance from the title, buttons and toggle */
    var _DI_PLACE_FLOW_GAP = 40;  /* clearance needed to leave fit mode: no flip-flopping at the edge */

    function _diPlace(outer, size, spring, overlay) {
        var header = outer && outer._diHeader;
        if (!header || !outer.querySelector('.di-pill')) return;
        var inFlow = outer.classList.contains('di-flow');
        if (overlay && inFlow) return;  /* fit mode centres the open pill itself */
        if (inFlow) outer.classList.remove('di-flow');  /* measured where it would sit on top */
        var r = outer.getBoundingClientRect();
        var slid = parseFloat(getComputedStyle(outer).translate) || 0;
        var w = size ? size.w : r.width;
        var top = r.top, bottom = r.top + Math.max(r.height, size ? size.h : 0);
        var centre = r.left + r.width / 2 - slid;
        /* Closed, as near the centre as the title allows; open, as near to
           where it already is as fits (the least movement) */
        var want = overlay ? centre + slid : centre;
        var bounds = header.getBoundingClientRect();
        var blocks = (overlay ? [document.querySelector('.top-controls')]
                              : [header.querySelector(':scope > .page-title'),
                                 header.querySelector(':scope > .action-buttons'),
                                 document.querySelector('.top-controls')])
            .map(function(el) { return el && el.getBoundingClientRect(); })
            .filter(function(b) { return b && b.width && b.height && b.top < bottom && b.bottom > top; });

        /* The free stretch of the row closest to the centre that fits the pill */
        function slideFor(gap) {
            var spans = blocks.map(function(b) { return [b.left - gap, b.right + gap]; })
                              .sort(function(a, b) { return a[0] - b[0]; });
            var free = [], x = bounds.left, best = null;
            spans.forEach(function(s) { if (s[0] > x) free.push([x, s[0]]); x = Math.max(x, s[1]); });
            if (bounds.right > x) free.push([x, bounds.right]);
            free.forEach(function(f) {
                if (f[1] - f[0] < w) return;
                var c = Math.min(Math.max(want, f[0] + w / 2), f[1] - w / 2);
                if (best === null || Math.abs(c - want) < Math.abs(best - want)) best = c;
            });
            return best === null ? null : Math.round(best - centre);
        }

        var slide = slideFor(inFlow ? _DI_PLACE_FLOW_GAP : _DI_PLACE_GAP);
        if (slide !== null && inFlow) slide = slideFor(_DI_PLACE_GAP);
        if (slide === null && overlay) slide = 0;  /* wider than the row: centred, as it always was */
        if (slide === null) {
            outer.classList.add('di-flow');
            outer.style.transition = '';
            outer.style.translate = '';
            return;
        }
        outer.style.transition = (spring && !inFlow && Math.abs(slide - slid) >= 1)
            ? 'translate ' + spring.total + 'ms ' + spring.easing : '';
        outer.style.translate = slide ? slide + 'px 0' : '';
    }

    /* Called from base.html once the page header is parsed */
    function placeIn(header) {
        var outer = document.getElementById('global-operation-banner');
        if (!outer || !header) return;
        /* A child of the header, so fit mode can put it in the header's flow;
           on top it is absolutely positioned and does not affect the layout */
        var title = header.querySelector(':scope > .page-title');
        header.insertBefore(outer, title ? title.nextSibling : header.firstChild);
        outer._diHeader = header;

        var retry = null;
        function fit() {
            clearTimeout(retry);
            /* Mid-animation the pill is between sizes; place it once it has settled */
            if (outer._flipCleanup) { retry = setTimeout(fit, 100); return; }
            var state = outer.getAttribute('data-di-expanded');
            _diPlace(outer, null, null, state === 'tier2' || state === 'tier3');
        }

        /* A replaced pill is placed before the next paint (microtask), so it
           never flashes in the wrong place; size changes (window, sidebar,
           the pill's own text) a frame later */
        if (window.ResizeObserver) {
            var ro = new ResizeObserver(function() { requestAnimationFrame(fit); });
            ro.observe(header);
            ro.observe(outer);
        } else {
            window.addEventListener('resize', fit);
        }
        new MutationObserver(function(list) {
            if (list.some(function(m) { return m.type === 'childList'; })) fit();
            if (list.some(function(m) { return m.type === 'attributes'; })) {
                clearTimeout(retry);
                retry = setTimeout(fit, 100);
            }
        }).observe(outer, {
            childList: true,  /* direct children only: the pill itself being replaced */
            attributes: true, attributeFilter: ['data-di-expanded']
        });
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
        window.addEventListener('load', fit);
        fit();
    }

    /* ── Continuity across page loads ────────────────────────────────
       Every page starts with an empty banner that the first poll fills, and
       a fresh page has no memory of the previous state, so each navigation
       replayed the pill's entrance (fade in, slide down 20px). Instead the
       pill is saved when a page is left and put back, unchanged, before the
       next page first paints (restoreBanner, called from base.html right
       after the banner element). The first poll then replaces it quietly:
       same state, no entrance and no completion flash; a state that changed
       in between morphs from it like any other state change. */
    var _DI_SAVE_KEY = 'di-banner';

    function _diSaveBanner() {
        var outer = document.getElementById('global-operation-banner');
        if (!outer) return;
        if (outer._flipCleanup) outer._flipCleanup();
        var pill = outer.querySelector('.di-pill');
        try {
            if (!pill || pill.getAttribute('data-state') === 'disconnected') {
                sessionStorage.removeItem(_DI_SAVE_KEY);
                return;
            }
            var copy = outer.cloneNode(true);
            copy.querySelectorAll('script').forEach(function(s) { s.parentNode.removeChild(s); });
            var p = copy.querySelector('.di-pill');
            p.classList.remove('di-pill--entrance', 'di-morphing', 'di-expanded', 'di-detail');
            p.removeAttribute('data-di-placeholder');
            sessionStorage.setItem(_DI_SAVE_KEY, JSON.stringify({
                state: pill.getAttribute('data-state'), html: copy.innerHTML, at: Date.now()
            }));
        } catch (e) { /* storage blocked or full: the next page loads the pill as before */ }
    }
    window.addEventListener('pagehide', _diSaveBanner);

    function restoreBanner() {
        var outer = document.getElementById('global-operation-banner');
        if (!outer || outer.firstElementChild) return;
        var saved = null;
        try { saved = JSON.parse(sessionStorage.getItem(_DI_SAVE_KEY) || 'null'); } catch (e) { saved = null; }
        if (!saved || !saved.html || Date.now() - saved.at > 10 * 60 * 1000) return;  /* only a recent one */
        outer.innerHTML = saved.html;  /* a stand-in: its scripts are gone; the first poll replaces it */
        var pill = outer.querySelector('.di-pill');
        if (!pill) { outer.innerHTML = ''; return; }
        pill.setAttribute('data-di-placeholder', '');
        outer.setAttribute('data-di-prev-state', saved.state);  /* not a new state: no entrance */
        outer.setAttribute('data-di-continued', '');           /* nor a replayed completion flash */
    }

    /* Run / Dry Run from the idle pill. The response is the next banner state;
       it is swapped in as a reshape of this bubble (collapsing first if the
       pill is open, while the request is in flight) */
    function diTriggerRun(dryRun) {
        var verbose = document.getElementById('di-verbose-cb');
        var formData = new FormData();
        if (verbose && verbose.checked) formData.append('verbose', 'true');
        if (dryRun) formData.append('dry_run', 'true');
        var outer = document.getElementById('global-operation-banner');
        if (diGetExpandState() !== 'false') diSmoothSetState('false');
        fetch('/operations/run', { method: 'POST', body: formData })
            .then(function(resp) { return resp.text(); })
            .then(function(html) {
                (function swapWhenStill() {
                    if (outer._flipCleanup) { setTimeout(swapWhenStill, 50); return; }
                    _diSwapBanner(outer, html);
                })();
            });
    }

    /* Put banner HTML in place outside htmx (its scripts do not run, as before),
       morphing from the current state when the state changes */
    function _diSwapBanner(outer, html) {
        var old = outer.querySelector('.di-pill');
        var m = /data-state="([^"]+)"/.exec(html);
        var snap = (old && m && m[1] !== old.getAttribute('data-state')) ? _diSnapshotPill(outer) : null;
        outer.innerHTML = html;
        htmx.process(outer);
        if (typeof lucide !== 'undefined') lucide.createIcons();
        if (snap) _diStateMorph(outer, snap);
    }

    /* Dismiss a finished/failed pill: back to compact first if it is open, then
       tell the server and fetch the next state, which the state morph turns
       into a reshape of this bubble */
    function diDismiss(url) {
        function go() {
            fetch(url, { method: 'POST' }).then(function() {
                htmx.ajax('GET', '/api/operation-banner', { target: '#global-operation-banner', swap: 'innerHTML' });
            });
        }
        var outer = document.getElementById('global-operation-banner');
        if (diGetExpandState() === 'false' || _diReducedMotion()) { go(); return; }
        diSmoothSetState('false');
        (function wait() {
            if (outer && outer._flipCleanup) setTimeout(wait, 50); else go();
        })();
    }

    function _diMorphCleanup(morph) {
        var v = morph.oldView;
        v.style.removeProperty('display');
        _diUnpinView(v);
        ['flexDirection', 'gap', 'pointerEvents', 'zIndex'].forEach(function(p) { v.style[p] = ''; });
        _diUnpinView(morph.newView);
        var moved = (morph.blocks || []).slice();
        morph.pairs.forEach(function(p) { moved.push(p.from, p.to); });
        moved.forEach(function(el) {
            el.style.translate = '';
            el.style.scale = '';
            el.style.transition = '';
            el.style.transformOrigin = '';
        });
        (morph.anims || []).forEach(function(a) { a.cancel(); });
    }

    function _diDoFlip(outer, pill, fromState, toState, isExpanding) {
        var perceived = isExpanding ? cfg.expandMs : cfg.collapseMs;
        var spring = _diSpring(perceived, cfg.bounce);  /* one spring for the bubble and its content */
        var duration = perceived;  /* fades and the progress bar follow the perceived pace */

        /* 1. Snapshot ALL dimensional properties */
        var cs = getComputedStyle(pill);
        var startHeight = pill.offsetHeight;
        var startWidth = pill.offsetWidth;
        var startBorderRadius = cs.borderRadius;
        var startPaddingTop = cs.paddingTop;
        var startPaddingBottom = cs.paddingBottom;

        /* 2. Freeze — lock current dimensions, hide progress bar.
           Width is locked and animated directly (min-width pinned to 0 so only
           the inline width counts). Animating min-width alone let the incoming
           content's natural width resize the pill in the first frame: the idle
           pill jumped from 202px to 311px before the animation started. */
        var progressBar = pill.querySelector('.di-pill__progress');
        if (progressBar && isExpanding) {
            progressBar.style.transition = 'none';
            progressBar.style.opacity = '0';
        }
        pill.style.willChange = 'height, width, border-radius, padding';
        pill.style.transition = 'none';
        pill.style.height = startHeight + 'px';
        pill.style.width = startWidth + 'px';
        pill.style.minWidth = '0px';
        pill.style.borderRadius = startBorderRadius;
        pill.style.paddingTop = startPaddingTop;
        pill.style.paddingBottom = startPaddingBottom;
        pill.style.overflow = 'hidden';

        /* Shared elements (data-di-shared) are located before the swap hides them */
        var morph = _diMorphCapture(pill, fromState, toState);

        /* 3. Swap state — CSS display:none swap happens here */
        _diApplyExpandState(toState);

        /* 4. Measure target — temporarily clear overrides to read CSS end values.
           Clear the height rather than forcing 'auto': the expanded states are
           height: auto in CSS, but the compact pill is a fixed 38px/44px, and
           'auto' measured only its content row (22px). The collapse then shrank
           past the real size and snapped back down at cleanup. */
        pill.style.height = '';
        pill.style.width = '';
        pill.style.minWidth = '';
        pill.style.borderRadius = '';
        pill.style.paddingTop = '';
        pill.style.paddingBottom = '';
        var endCs = getComputedStyle(pill);
        var endHeight = pill.offsetHeight;
        var endWidth = pill.offsetWidth;
        var endBorderRadius = endCs.borderRadius;
        var endPaddingTop = endCs.paddingTop;
        var endPaddingBottom = endCs.paddingBottom;

        /* Hold the content that is showing at its final width while the pill
           resizes around it, so it never reflows: the pill's edges uncover it
           (expand) or close in on it (collapse). The expanded pill is a column
           with align-items: center, which centres the content even while it is
           wider than the pill. The compact row needs auto margins, and is never
           wider than the pill during a collapse. */
        var content = pill.querySelector(isExpanding ? '.di-pill__expanded' : '.di-pill__compact');
        if (content) {
            content.style.width = content.offsetWidth + 'px';
            if (!isExpanding) {
                content.style.flexShrink = '0';
                content.style.marginLeft = 'auto';
                content.style.marginRight = 'auto';
            }
        }
        if (morph) _diMorphMeasureEnd(morph, pill);

        /* 5. Rewind ALL to start values */
        pill.style.height = startHeight + 'px';
        pill.style.width = startWidth + 'px';
        pill.style.minWidth = '0px';
        pill.style.borderRadius = startBorderRadius;
        pill.style.paddingTop = startPaddingTop;
        pill.style.paddingBottom = startPaddingBottom;

        /* 6. Force reflow */
        pill.offsetHeight;
        if (morph) _diMorphStart(morph, pill, perceived, spring,
                                 { startW: startWidth, startH: startHeight, endW: endWidth, endH: endHeight });

        /* 7. Animate ALL dimensional properties on the shape spring */
        var shapeAt = {
            start: { height: startHeight + 'px', width: startWidth + 'px', borderRadius: startBorderRadius,
                     paddingTop: startPaddingTop, paddingBottom: startPaddingBottom },
            end:   { height: endHeight + 'px', width: endWidth + 'px', borderRadius: endBorderRadius,
                     paddingTop: endPaddingTop, paddingBottom: endPaddingBottom }
        };
        var animProps = ['height', 'width', 'border-radius', 'padding-top', 'padding-bottom'];
        pill.style.transition = animProps.map(function(p) {
            return p + ' ' + spring.total + 'ms ' + spring.easing;
        }).join(', ');
        Object.keys(shapeAt.end).forEach(function(k) { pill.style[k] = shapeAt.end[k]; });

        /* The banner's slide follows the bubble on the same spring: an opening
           pill keeps clear of the theme toggle and the page edge, a closing one
           goes back to its place beside the title */
        var slideAt = { start: outer.style.translate };
        _diPlace(outer, { w: endWidth, h: endHeight }, spring, toState !== 'false');
        slideAt.end = outer.style.translate;

        /* 8. Content transitions (a morph runs its own crossfade instead) */
        if (isExpanding) {
            var enterTarget = (fromState === 'tier2' && toState === 'tier3')
                ? pill.querySelector('.di-pill__detail')
                : pill.querySelector('.di-pill__expanded');
            if (enterTarget && !morph) {
                enterTarget.classList.remove('di-content-enter');
                void enterTarget.offsetWidth;
                enterTarget.classList.add('di-content-enter');
            }
        } else {
            /* Collapse: settle-fade compact content, progress bar fades in last 45% */
            var compact = pill.querySelector('.di-pill__compact');
            if (compact && !morph) {
                compact.classList.remove('di-content-settle');
                void compact.offsetWidth;
                compact.classList.add('di-content-settle');
            }
            if (progressBar) {
                setTimeout(function() {
                    progressBar.style.transition = 'opacity ' + Math.round(duration * 0.45) + 'ms ease';
                    progressBar.style.opacity = '';
                }, Math.round(duration * 0.55));
            }
        }

        /* 9. Cleanup — remove ALL inline overrides so CSS takes over. A flip
           that was sent back (interrupted) first returns to the state it came
           from, while everything is still pinned, so nothing flashes. */
        var flip = null;
        function cleanup() {
            if (flip) {
                clearTimeout(flip.timer);
                if (flip.target === 'start') _diApplyExpandState(fromState);
                if (outer._flip === flip) outer._flip = null;
                flip = null;
            }
            if (morph) _diMorphCleanup(morph);
            pill.style.height = '';
            pill.style.width = '';
            if (content) {
                content.style.width = '';
                content.style.flexShrink = '';
                content.style.marginLeft = '';
                content.style.marginRight = '';
            }
            pill.style.borderRadius = '';
            pill.style.paddingTop = '';
            pill.style.paddingBottom = '';
            pill.style.minWidth = '';
            pill.style.overflow = '';
            pill.style.transition = '';
            pill.style.willChange = '';
            pill.style.opacity = '';
            outer.style.transition = '';
            if (progressBar) {
                if (isExpanding) {
                    progressBar.style.transition = '';
                    progressBar.style.opacity = '';
                } else {
                    setTimeout(function() { progressBar.style.transition = ''; }, 50);
                }
            }
            var c = pill.querySelector('.di-pill__compact');
            if (c) c.classList.remove('di-content-settle');
            var e = pill.querySelector('.di-pill__expanded');
            if (e) { e.classList.remove('di-content-enter'); e.classList.remove('di-content-exit'); }
            outer._flipCleanup = null;
        }
        /* Clean up once the spring has settled (on a timer rather than the
           height's transitionend, which a zero-height change never fires) */
        outer._flipCleanup = cleanup;
        function settleLater() {
            var timer = setTimeout(function() { if (outer._flipCleanup === cleanup) cleanup(); },
                                   spring.total + 50);
            if (flip) flip.timer = timer;
        }
        if (morph) {
            /* Interruptible: diSmoothSetState sends an in-flight morph to either
               end (hover out half way through an expand plays it back from
               where it is, instead of snapping to the end and collapsing) */
            flip = outer._flip = {
                fromState: fromState, toState: toState, target: 'end', timer: null,
                toward: function(which) {
                    flip.target = which;
                    Object.keys(shapeAt[which]).forEach(function(k) { pill.style[k] = shapeAt[which][k]; });
                    outer.style.translate = slideAt[which];
                    morph.toward(which);
                    clearTimeout(flip.timer);
                    settleLater();
                }
            };
        }
        settleLater();
    }

    function diMinimize() { diSmoothSetState('false'); }

    /* ── Alerts ──────────────────────────────────────────────────────
       For an event that should not be missed (a run that ended with errors, or a
       failed run) the bubble expands by itself to present it, as the Dynamic
       Island does instead of sending a separate notification, pulses the part
       that caused it, and settles back to compact after cfg.alertHoldMs unless
       the pointer is on it. It never dismisses itself. Callers fire it once per
       event (sessionStorage), not on every poll or page load. */
    function diAlert(causeSelector) {
        var outer = document.getElementById('global-operation-banner');
        var pill = outer && outer.querySelector('.di-pill');
        if (!pill) return;
        var stateAttr = pill.getAttribute('data-state');
        setTimeout(function() {
            /* A second poll can re-render the same state meanwhile (it then counts
               as a repoll and does not alert), so check the state, not the element */
            var current = outer.querySelector('.di-pill');
            if (!current || current.getAttribute('data-state') !== stateAttr) return;
            if (diGetExpandState() === 'false') diSmoothSetState('tier2');
            setTimeout(function() {
                var cause = causeSelector && outer.querySelector('.di-pill__expanded ' + causeSelector);
                if (cause && !_diReducedMotion()) {
                    cause.classList.add('di-attention');
                    cause.addEventListener('animationend', function() { cause.classList.remove('di-attention'); }, { once: true });
                }
            }, cfg.expandMs);
            setTimeout(function() {
                var p = outer.querySelector('.di-pill');
                if (p && p.getAttribute('data-state') === stateAttr && !p.matches(':hover')
                        && diGetExpandState() === 'tier2') {
                    diSmoothSetState('false');
                }
            }, cfg.alertHoldMs);
        }, 400);
    }

    window._diHoverTimer = null;  /* public: the Run buttons clear it */
    var _diLeaveTimer = null;
    function diHoverIn() {
        clearTimeout(_diLeaveTimer);
        if (diGetExpandState() === 'false') {
            window._diHoverTimer = setTimeout(function() { diSmoothSetState('tier2'); }, 200);
        }
    }
    function diHoverOut() {
        clearTimeout(window._diHoverTimer);
        if (diGetExpandState() === 'tier2') {
            _diLeaveTimer = setTimeout(function() {
                if (diGetExpandState() === 'tier2') diSmoothSetState('false');
            }, 300);
        }
    }
    function diPillClick() {
        var state = diGetExpandState();
        if (state === 'false') diSmoothSetState('tier2');
        else if (state === 'tier2') diSmoothSetState('tier3');
        else diSmoothSetState('false');  /* tier3 → collapse back to compact */
    }

    // Toggle a grouped show row in the completed-state detail view.
    // Hides/shows the adjacent .di-file-row--ep sub-rows without re-rendering
    // (which would require replaying the IIFE that built detailHtml).
    // `data-show` carries the group id minted by core.media_grouping — not the show
    // name — so two groups for the same show (cached vs restored in one run) toggle
    // independently. The id is stable across the 2s poll re-renders, which is what
    // lets the expanded state below survive.
    function bannerToggleShow(evt, el) {
        if (evt && evt.stopPropagation) evt.stopPropagation();
        var groupId = el.getAttribute('data-show');
        var chev = el.querySelector('.di-expand-chev');
        var expand = !chev.classList.contains('open');
        chev.classList.toggle('open');

        var subs = document.querySelectorAll('.di-file-row--ep[data-parent-show="' + (groupId || '') + '"]');
        subs.forEach(function(s) { s.style.display = expand ? 'flex' : 'none'; });

        if (!window.__bannerExpandedShows) window.__bannerExpandedShows = {};
        window.__bannerExpandedShows[groupId] = expand;
    }

    // Preserve window scroll position across banner swaps.
    // During running ops the banner polls every 2s with innerHTML swaps; if the
    // user had focus or selection inside the banner, the destroyed element can
    // cause the browser to reset scroll to top. Snapshot before, restore after.
    // Stored on window because this script re-runs on every innerHTML swap and
    // would otherwise wipe the saved value between beforeSwap and afterSwap.
    if (typeof window.__diSavedScrollY === 'undefined') window.__diSavedScrollY = null;

    /* ── Live values: numbers roll, bars glide ─────────────────────────
       Polls replace the whole pill, so on their own every number snaps and every
       progress bar jumps once a poll. Elements tagged data-di-num="<key>" or
       data-di-bar="<key>" are remembered before a swap; after it, a number whose
       text changed rolls the changed part in (upward when the value grew,
       downward when it shrank, like a countdown), and a bar glides from its old
       width. Only the differing middle of the text moves, so "Next run in 9m" ->
       "Next run in 10m" rolls just "10". */
    function _diNumberOf(text) {
        var m = String(text).match(/-?\d+(?:\.\d+)?/);
        return m ? parseFloat(m[0]) : NaN;
    }

    function _diRollText(el, oldText, newText) {
        if (!el || oldText === newText || _diReducedMotion()) return;
        var p = 0;
        while (p < oldText.length && p < newText.length && oldText[p] === newText[p]) p++;
        var s = 0;
        while (s < oldText.length - p && s < newText.length - p
               && oldText[oldText.length - 1 - s] === newText[newText.length - 1 - s]) s++;
        var middle = newText.slice(p, newText.length - s);
        if (!middle.trim()) return;
        var up = !(_diNumberOf(middle) < _diNumberOf(oldText.slice(p, oldText.length - s)));
        var span = document.createElement('span');
        span.className = 'di-roll-char';
        span.textContent = middle;
        el.textContent = '';
        el.append(newText.slice(0, p), span, newText.slice(newText.length - s));
        var token = (el._diRoll = (el._diRoll || 0) + 1);
        span.animate([
            { transform: 'translateY(' + (up ? '0.6em' : '-0.6em') + ')', opacity: 0, filter: 'blur(2px)' },
            { transform: 'translateY(0)', opacity: 1, filter: 'blur(0)' }
        ], { duration: 350, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' }).onfinish = function() {
            if (el._diRoll === token && el.textContent === newText) el.textContent = newText;  /* plain text again */
        };
    }

    /* Set an element's text in place, rolling the change */
    function _diSetText(el, text) {
        var old = el.textContent;
        if (old === text) return;
        el.textContent = text;
        _diRollText(el, old, text);
    }

    function _diSnapshotLive(root) {
        var snap = { nums: {}, bars: {} };
        root.querySelectorAll('[data-di-num]').forEach(function(el) {
            snap.nums[el.getAttribute('data-di-num')] = el.textContent;
        });
        root.querySelectorAll('[data-di-bar]').forEach(function(el) {
            snap.bars[el.getAttribute('data-di-bar')] = el.style.width;
        });
        return snap;
    }

    function _diAnimateLive(root, snap) {
        root.querySelectorAll('[data-di-num]').forEach(function(el) {
            var key = el.getAttribute('data-di-num');
            if (key in snap.nums) _diRollText(el, snap.nums[key], el.textContent);
        });
        if (_diReducedMotion()) return;
        root.querySelectorAll('[data-di-bar]').forEach(function(el) {
            var from = snap.bars[el.getAttribute('data-di-bar')], to = el.style.width;
            if (!from || !to || from === to) return;
            el.style.transition = 'none';   /* start where the old bar was ... */
            el.style.width = from;
            void el.offsetWidth;
            el.style.transition = '';       /* ... and glide on the CSS width transition */
            el.style.width = to;
        });
    }

    /* Bound once (guards are belt and braces should the file be included twice) */
    if (!window.__diLiveBound) {
        window.__diLiveBound = true;
        document.addEventListener('htmx:beforeSwap', function(evt) {
            var target = evt.detail.target;
            if (!target || target.id !== 'global-operation-banner') return;
            /* A poll landing mid expand/collapse would replace the pill under the
               animation; skip it, the next poll is a couple of seconds away */
            if (target._flipCleanup) { evt.detail.shouldSwap = false; return; }
            window.__diLiveSnap = _diSnapshotLive(target);
            /* A different state is arriving: snapshot the outgoing pill for the
               state morph (the response's first data-state is its pill's) */
            var oldPill = target.querySelector('.di-pill');
            var m = /data-state="([^"]+)"/.exec(evt.detail.serverResponse || '');
            var changed = oldPill && m && m[1] !== oldPill.getAttribute('data-state');
            _diPrev = changed ? _diSnapshotPill(target) : null;
            /* A real state change ends a restored page's continuation: its
               one-shot animations (completion flash) play again */
            if (changed) target.removeAttribute('data-di-continued');
        });
        document.addEventListener('htmx:afterSwap', function(evt) {
            var target = evt.detail.target;
            if (!target || target.id !== 'global-operation-banner') return;
            if (window.__diLiveSnap) {
                _diAnimateLive(target, window.__diLiveSnap);
                window.__diLiveSnap = null;
            }
            if (_diPrev) _diHold(target, _diPrev);
        });
        document.addEventListener('htmx:afterSettle', function(evt) {
            var target = evt.detail.target;
            if (!target || target.id !== 'global-operation-banner' || !_diPrev) return;
            var snap = _diPrev;
            _diPrev = null;
            _diStateMorph(target, snap);
        });
    }

    // Skip the swap if the banner state hasn't meaningfully changed.
    // Prevents the pill from flashing on every idle/completed poll cycle.
    // innerHTML comparison doesn't work because script tags differ between
    // server response and post-execution DOM.  Instead, parse the response
    // into a temporary fragment, compare data-state, and patch text in-place.
    // Running/maintenance polls always swap (they show live progress).
    // Document-level listeners, bound once. (When this code lived inline in the
    // banner template it re-ran on every swap, and every poll added another copy
    // of each listener: the per-swap work grew for the whole run.)
    if (!window.__diBannerListenersBound) {
    window.__diBannerListenersBound = true;
    document.addEventListener('htmx:beforeSwap', function(evt) {
        if (evt.detail.target && evt.detail.target.id === 'global-operation-banner') {
            window.__diSavedScrollY = window.scrollY;
            var banner = evt.detail.target;
            var oldPill = banner.querySelector('.di-pill');
            if (!oldPill) return;  // No pill yet — allow first swap
            if (oldPill.hasAttribute('data-di-placeholder')) return;  // a restored stand-in: always replace it

            var oldState = oldPill.getAttribute('data-state') || '';

            // Only skip for static states — running states need live updates
            if (oldState.indexOf('idle') !== 0 && oldState.indexOf('completed') !== 0
                && oldState.indexOf('maint-completed') !== 0) return;

            // Parse response into a temporary DOM to read new state
            var tmp = document.createElement('div');
            tmp.innerHTML = evt.detail.serverResponse || '';
            var newPill = tmp.querySelector('.di-pill');
            if (!newPill) return;  // No pill in response — allow swap

            var newState = newPill.getAttribute('data-state') || '';
            if (oldState !== newState) return;  // State changed — allow swap

            // If pill is invisible (stale animation state), allow the swap to reset it
            var cs = getComputedStyle(oldPill);
            if (cs.opacity === '0' || oldPill.offsetHeight === 0) return;

            // Same state — patch countdown text in-place without full DOM swap
            var newMetric = newPill.querySelector('.di-metric');
            var oldMetric = oldPill.querySelector('.di-metric');
            if (newMetric && oldMetric) _diSetText(oldMetric, newMetric.textContent);

            // Every live value by key (the Ready pill's "Ran 2 hr ago", ...)
            newPill.querySelectorAll('[data-di-num]').forEach(function(n) {
                var o = oldPill.querySelector('[data-di-num="' + n.getAttribute('data-di-num') + '"]');
                if (o) _diSetText(o, n.textContent);
            });

            var newLabel = newPill.querySelector('.di-pill__expanded .di-label');
            var oldLabel = oldPill.querySelector('.di-pill__expanded .di-label');
            if (newLabel && oldLabel) _diSetText(oldLabel, newLabel.textContent);

            evt.detail.shouldSwap = false;
        }
    });

    // ── Connection-lost detection ─────────────────────────────────────────
    // Track consecutive banner poll failures. After 3 failures, replace the
    // pill with a "Connection Lost" state so the user knows the server is
    // unreachable (instead of the banner just freezing on its last state).
    var _diBannerFailCount = 0;
    var _diBannerDisconnected = false;

    document.addEventListener('htmx:sendError', function(evt) {
        if (!evt.detail.elt || !evt.detail.elt.closest('#global-operation-banner')) return;
        _diBannerFailCount++;
        if (_diBannerFailCount >= 3 && !_diBannerDisconnected) {
            _diBannerDisconnected = true;
            var banner = document.getElementById('global-operation-banner');
            if (!banner) return;
            // Preserve expansion state on outer div — only replace inner content
            banner.innerHTML =
                '<div class="di-pill di-pill--disconnected" data-state="disconnected"' +
                '     hx-get="/api/operation-banner" hx-trigger="every 5s" hx-swap="innerHTML" hx-target="#global-operation-banner">' +
                '  <div class="di-pill__compact">' +
                '    <div class="di-pill__left">' +
                '      <div class="di-icon" data-di-shared="icon"><i data-di-anchor data-lucide="wifi-off" style="width:14px;height:14px;"></i></div>' +
                '      <span class="di-label" data-di-shared="label">Connection Lost</span>' +
                '    </div>' +
                '  </div>' +
                '  <div class="di-pill__expanded">' +
                '    <div class="di-expanded__header">' +
                '      <div class="di-expanded__header-left">' +
                '        <div class="di-icon" data-di-shared="icon"><i data-di-anchor data-lucide="wifi-off" style="width:14px;height:14px;"></i></div>' +
                '        <span class="di-label" data-di-shared="label">Server Unreachable</span>' +
                '      </div>' +
                '    </div>' +
                '    <div style="font-size:0.78rem;color:var(--di-t50);text-align:center;padding:0.3rem 0;">' +
                '      Unable to reach the server. Retrying&hellip;' +
                '    </div>' +
                '  </div>' +
                '</div>';
            if (typeof lucide !== 'undefined') lucide.createIcons();
            if (typeof htmx !== 'undefined') htmx.process(banner);
        }
    });

    // Also catch HTTP errors (5xx) on the banner poll as connection issues
    document.addEventListener('htmx:responseError', function(evt) {
        if (!evt.detail.elt || !evt.detail.elt.closest('#global-operation-banner')) return;
        _diBannerFailCount++;
        if (_diBannerFailCount >= 3 && !_diBannerDisconnected) {
            // Trigger the same logic — dispatch a synthetic sendError
            document.dispatchEvent(new CustomEvent('htmx:sendError', {
                detail: { elt: evt.detail.elt }
            }));
        }
    });

    // After every HTMX swap: re-render icons, sync animations, one-shot entrance
    document.addEventListener('htmx:afterSwap', function(evt) {
        if (evt.detail.target && evt.detail.target.id === 'global-operation-banner') {
            // Restore scroll position snapshotted in beforeSwap.
            if (window.__diSavedScrollY !== null) {
                window.scrollTo(0, window.__diSavedScrollY);
                window.__diSavedScrollY = null;
            }

            // Successful swap — reset connection failure tracking
            _diBannerFailCount = 0;
            _diBannerDisconnected = false;

            if (typeof lucide !== 'undefined') lucide.createIcons();

            // Sync looping animations to wall clock so they don't visibly reset on 2s swap.
            // A negative animation-delay makes the new element start mid-cycle at the
            // exact phase the old element was at before it was replaced.
            var now = Date.now();
            document.querySelectorAll('#global-operation-banner .di-spinner').forEach(function(el) {
                el.style.animationDelay = -(now % 800) + 'ms';   // 800ms spin cycle
            });
            document.querySelectorAll('#global-operation-banner .di-action-tag--active').forEach(function(el) {
                el.style.animationDelay = -(now % 1500) + 'ms';  // 1500ms pulse cycle
            });

            // Detect state changes — only animate entrance when the banner state actually changes
            var outer = document.getElementById('global-operation-banner');
            var pill = outer ? outer.querySelector('.di-pill') : null;
            if (pill && outer) {
                var newState = pill.getAttribute('data-state') || pill.className;
                var prevState = outer.getAttribute('data-di-prev-state') || '';
                if (newState !== prevState) {
                    outer.setAttribute('data-di-prev-state', newState);
                    /* The slide-in entrance is only for a pill that appears
                       without a state morph taking it over */
                    if (!_diPrev) {
                        pill.classList.add('di-pill--entrance');
                        pill.addEventListener('animationend', function() { pill.classList.remove('di-pill--entrance'); }, { once: true });
                    }

                    // When a background action finishes, cache/pin sizes may have
                    // changed on disk. Dispatch pinned-updated so the Settings
                    // "Currently Pinned" chip list refetches — harmless on pages
                    // that don't render the container.
                    var wasRunning = prevState.indexOf('maint-running') === 0 || prevState.indexOf('op-running') === 0;
                    var nowIdle = newState.indexOf('maint-completed') === 0 || newState.indexOf('op-completed') === 0
                        || newState.indexOf('idle') === 0;
                    if (wasRunning && nowIdle) {
                        document.body.dispatchEvent(new CustomEvent('pinned-updated'));
                    }
                }
            }

            // Re-inject live log lines from WebSocket buffer (overwrites stale server data)
            if (window.BannerLogStream) BannerLogStream.renderIfVisible();
        }
    });
    }  // __diBannerListenersBound

    window.PillMotion = { config: cfg, spring: _diSpring, reducedMotion: _diReducedMotion,
                          restoreBanner: restoreBanner, placeIn: placeIn };
    window.showBannerWarning = showBannerWarning;
    window.diGetExpandState = diGetExpandState;
    window.diSetExpandState = diSetExpandState;
    window.diSmoothSetState = diSmoothSetState;
    window.diMinimize = diMinimize;
    window.diAlert = diAlert;
    window.diHoverIn = diHoverIn;
    window.diHoverOut = diHoverOut;
    window.diPillClick = diPillClick;
    window.bannerToggleShow = bannerToggleShow;
    window.diDismiss = diDismiss;
    window.diTriggerRun = diTriggerRun;
})();
