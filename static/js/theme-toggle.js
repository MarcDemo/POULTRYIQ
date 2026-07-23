(function () {
    var storageKey = "poultryiq-theme-mode";
    var modeOrder = ["light", "dark", "system"];
    var labelMap = {
        light: "Light",
        dark: "Dark",
        system: "System",
    };
    var iconMap = {
        light: "bi-sun-fill",
        dark: "bi-moon-stars-fill",
        system: "bi-circle-half",
    };

    function getSystemTheme() {
        if (!window.matchMedia) {
            return "light";
        }
        return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }

    function resolveTheme(mode) {
        return mode === "system" ? getSystemTheme() : mode;
    }

    function getCurrentMode() {
        return document.documentElement.getAttribute("data-theme-mode") || "system";
    }

    function setMode(mode) {
        var resolvedTheme = resolveTheme(mode);
        var root = document.documentElement;

        root.setAttribute("data-theme-mode", mode);
        root.setAttribute("data-theme", resolvedTheme);
        root.style.colorScheme = resolvedTheme;

        try {
            localStorage.setItem(storageKey, mode);
        } catch (error) {
            // Ignore storage write errors.
        }

        document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
            var icon = button.querySelector("[data-theme-icon]");
            var label = button.querySelector("[data-theme-label]");

            if (icon) {
                icon.className = "bi " + iconMap[mode] + " theme-toggle-icon";
            }

            if (label) {
                label.textContent = labelMap[mode];
            }

            button.setAttribute("aria-label", "Theme: " + labelMap[mode]);
            button.setAttribute("title", "Theme: " + labelMap[mode]);
        });
    }

    function getNextMode(mode) {
        var currentIndex = modeOrder.indexOf(mode);
        var nextIndex = (currentIndex + 1) % modeOrder.length;
        return modeOrder[nextIndex];
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
            button.addEventListener("click", function () {
                setMode(getNextMode(getCurrentMode()));
            });
        });

        setMode(getCurrentMode());
    });

    if (window.matchMedia) {
        var mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
        var handleSystemChange = function () {
            if (getCurrentMode() === "system") {
                setMode("system");
            }
        };

        if (typeof mediaQuery.addEventListener === "function") {
            mediaQuery.addEventListener("change", handleSystemChange);
        } else if (typeof mediaQuery.addListener === "function") {
            mediaQuery.addListener(handleSystemChange);
        }
    }
})();