(function () {
    "use strict";

    document.querySelectorAll("[data-auto-dismiss]").forEach(function (message) {
        var delay = Number(message.getAttribute("data-auto-dismiss")) || 6000;
        var remaining = delay;
        var startedAt = Date.now();
        var timer;

        function closeMessage() {
            if (window.bootstrap && window.bootstrap.Alert) {
                window.bootstrap.Alert.getOrCreateInstance(message).close();
                return;
            }
            message.remove();
        }

        function startTimer() {
            startedAt = Date.now();
            timer = window.setTimeout(closeMessage, remaining);
        }

        function pauseTimer() {
            window.clearTimeout(timer);
            remaining = Math.max(0, remaining - (Date.now() - startedAt));
        }

        message.addEventListener("mouseenter", pauseTimer);
        message.addEventListener("mouseleave", startTimer);
        message.addEventListener("focusin", pauseTimer);
        message.addEventListener("focusout", startTimer);
        startTimer();
    });
})();
