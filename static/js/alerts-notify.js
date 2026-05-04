(function () {
    "use strict";

    var UPDATE_INTERVAL_MS = 10000;
    var apiUrl        = "/alerts/api/unread-count/";
    var recentApiUrl  = "/alerts/api/recent/";
    var inboxUrl      = "/alerts/inbox/";
    var previousUnreadCount = null;
    var SOUND_PREF_KEY = "alertsSoundMuted";
    var popupOpen = false;

    function isSoundMuted() {
        try {
            return window.localStorage.getItem(SOUND_PREF_KEY) === "1";
        } catch (error) {
            return false;
        }
    }

    function setSoundMuted(muted) {
        try {
            window.localStorage.setItem(SOUND_PREF_KEY, muted ? "1" : "0");
        } catch (error) {
            // Ignore storage failures and keep runtime behavior.
        }
    }

    function renderSoundToggleButtons() {
        var muted = isSoundMuted();
        var toggleButtons = document.querySelectorAll("[data-alert-sound-toggle]");

        toggleButtons.forEach(function (button) {
            var icon = button.querySelector("i");
            button.classList.toggle("is-muted", muted);
            button.setAttribute("aria-pressed", muted ? "true" : "false");
            button.setAttribute("title", muted ? "Enable alert sound" : "Mute alert sound");
            button.setAttribute("aria-label", muted ? "Enable alert sound" : "Mute alert sound");

            if (icon) {
                icon.classList.toggle("bi-volume-up-fill", !muted);
                icon.classList.toggle("bi-volume-mute-fill", muted);
            }
        });
    }

    function wireSoundToggleButtons() {
        var toggleButtons = document.querySelectorAll("[data-alert-sound-toggle]");

        toggleButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                setSoundMuted(!isSoundMuted());
                renderSoundToggleButtons();
            });
        });
    }

    function playNewAlertSound() {
        if (isSoundMuted()) {
            return;
        }

        try {
            var AudioContextCtor = window.AudioContext || window.webkitAudioContext;
            if (!AudioContextCtor) {
                return;
            }

            var audioCtx = new AudioContextCtor();

            // Respect autoplay policies: only play when context can run.
            if (audioCtx.state === "suspended") {
                audioCtx.close();
                return;
            }

            var oscillator = audioCtx.createOscillator();
            var gainNode = audioCtx.createGain();
            var now = audioCtx.currentTime;

            oscillator.type = "sine";
            oscillator.frequency.setValueAtTime(880, now);
            oscillator.frequency.exponentialRampToValueAtTime(660, now + 0.18);

            gainNode.gain.setValueAtTime(0.0001, now);
            gainNode.gain.exponentialRampToValueAtTime(0.06, now + 0.02);
            gainNode.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);

            oscillator.connect(gainNode);
            gainNode.connect(audioCtx.destination);

            oscillator.start(now);
            oscillator.stop(now + 0.24);

            oscillator.onended = function () {
                audioCtx.close();
            };
        } catch (error) {
            // Keep polling resilient even if audio is not available.
        }
    }

    function toBellState(linkElement, count) {
        var iconElement = linkElement.querySelector(".alerts-bell-icon");
        var badgeElement = linkElement.querySelector(".alerts-bell-badge");
        var hasUnread = count > 0;

        if (!iconElement || !badgeElement) {
            return;
        }

        if (hasUnread) {
            linkElement.classList.add("has-unread");
            iconElement.classList.add("is-shaking");
            badgeElement.classList.add("is-flashing");
            badgeElement.textContent = count > 99 ? "99+" : String(count);
            linkElement.setAttribute("aria-label", "Unread alerts: " + count);
            return;
        }

        linkElement.classList.remove("has-unread");
        iconElement.classList.remove("is-shaking");
        badgeElement.classList.remove("is-flashing");
        badgeElement.textContent = "0";
        linkElement.setAttribute("aria-label", "No unread alerts");
    }

    function updateAllBellLinks(count) {
        var bellLinks = document.querySelectorAll("[data-alert-bell]");

        bellLinks.forEach(function (linkElement) {
            toBellState(linkElement, count);
        });
    }

    function fetchUnreadCount() {
        return fetch(apiUrl, {
            method: "GET",
            headers: {
                "X-Requested-With": "XMLHttpRequest"
            },
            credentials: "same-origin"
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("Alert count request failed");
            }
            return response.json();
        }).then(function (data) {
            var count = Number(data.unread_count || 0);

            if (previousUnreadCount !== null && count > previousUnreadCount) {
                playNewAlertSound();
            }

            previousUnreadCount = count;
            updateAllBellLinks(count);
        }).catch(function () {
            // Keep UI stable if endpoint is unavailable for this page/session.
        });
    }

    if (document.querySelector("[data-alert-bell]")) {
        buildPopup();
        wireDesktopBells();
        renderSoundToggleButtons();
        wireSoundToggleButtons();
        fetchUnreadCount();
        window.setInterval(fetchUnreadCount, UPDATE_INTERVAL_MS);
    } else if (document.querySelector("[data-alert-sound-toggle]")) {
        renderSoundToggleButtons();
        wireSoundToggleButtons();
    }

    /* ── Popup panel ── */

    function buildPopup() {
        if (document.getElementById("alertsPopupPanel")) { return; }
        var el = document.createElement("div");
        el.id = "alertsPopupPanel";
        el.className = "alerts-popup";
        el.setAttribute("role", "dialog");
        el.setAttribute("aria-label", "Alerts");
        el.innerHTML =
            '<div class="alerts-popup__header">' +
                '<span class="alerts-popup__header-title">' +
                    '<i class="bi bi-bell-fill"></i> Alerts & Messages' +
                '</span>' +
                '<div class="alerts-popup__header-actions">' +
                    '<button id="alertsSoundBtn" data-alert-sound-toggle aria-label="Mute alert sound">' +
                        '<i class="bi bi-volume-up-fill"></i>' +
                    '</button>' +
                    '<button id="alertsCloseBtn" aria-label="Close">' +
                        '<i class="bi bi-x-lg"></i>' +
                    '</button>' +
                '</div>' +
            '</div>' +
            '<div class="alerts-popup__body" id="alertsPopupBody">' +
                '<div class="alerts-popup__empty">Loading\u2026</div>' +
            '</div>' +
            '<div class="alerts-popup__footer">' +
                '<a href="' + inboxUrl + '">View all messages &rarr;</a>' +
            '</div>';
        document.body.appendChild(el);

        document.getElementById("alertsCloseBtn").addEventListener("click", closePopup);

        /* Clicking outside the popup or a bell closes it */
        document.addEventListener("click", function (e) {
            if (!popupOpen) { return; }
            var panel = document.getElementById("alertsPopupPanel");
            var clickedBell = e.target.closest("[data-alert-bell-trigger]");
            var clickedPanel = panel && panel.contains(e.target);
            if (!clickedBell && !clickedPanel) { closePopup(); }
        }, true);

        /* Re-render sound toggle inside popup once wired */
        wireSoundToggleButtons();
    }

    function openPopup() {
        var panel = document.getElementById("alertsPopupPanel");
        if (!panel) { return; }
        panel.classList.add("is-open");
        popupOpen = true;
        loadRecentAlerts();
    }

    function closePopup() {
        var panel = document.getElementById("alertsPopupPanel");
        if (!panel) { return; }
        panel.classList.remove("is-open");
        popupOpen = false;
    }

    function togglePopup() {
        if (popupOpen) { closePopup(); } else { openPopup(); }
    }

    function loadRecentAlerts() {
        var body = document.getElementById("alertsPopupBody");
        if (!body) { return; }
        body.innerHTML = '<div class="alerts-popup__empty">Loading\u2026</div>';

        fetch(recentApiUrl, {
            headers: { "X-Requested-With": "XMLHttpRequest" },
            credentials: "same-origin"
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            var alerts = data.alerts || [];
            if (!alerts.length) {
                body.innerHTML = '<div class="alerts-popup__empty"><i class="bi bi-inbox" style="font-size:1.6rem;display:block;margin-bottom:6px;"></i>No messages yet</div>';
                return;
            }
            body.innerHTML = alerts.map(renderAlertItem).join("");
        })
        .catch(function () {
            body.innerHTML = '<div class="alerts-popup__empty">Could not load alerts</div>';
        });
    }

    function renderAlertItem(a) {
        var unreadClass = a.is_unread ? " is-unread" : "";
        return (
            '<a href="' + a.detail_url + '" class="alerts-popup__item' + unreadClass + '">' +
                '<div class="alerts-popup__dot"></div>' +
                '<div class="alerts-popup__content">' +
                    '<div class="alerts-popup__title">' + escHtml(a.title) + '</div>' +
                    '<div class="alerts-popup__meta">' +
                        escHtml(a.sender_name) + ' &middot; ' + escHtml(a.created_at) +
                    '</div>' +
                    '<div class="alerts-popup__msg">' + escHtml(a.message) + '</div>' +
                '</div>' +
                '<span class="alerts-popup__priority alerts-popup__priority--' + a.priority + '">' +
                    a.priority +
                '</span>' +
            '</a>'
        );
    }

    function escHtml(str) {
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function wireDesktopBells() {
        /* Desktop bells have data-alert-bell-trigger — wire popup toggle.
           Mobile topbar bells are plain <a href> links — left untouched. */
        var triggers = document.querySelectorAll("[data-alert-bell-trigger]");
        triggers.forEach(function (el) {
            el.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                togglePopup();
            });
        });
    }
})();
