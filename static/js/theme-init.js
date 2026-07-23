(function () {
    var storageKey = "poultryiq-theme-mode";
    var defaultMode = "system";
    var preferredMode = defaultMode;

    try {
        preferredMode = localStorage.getItem(storageKey) || defaultMode;
    } catch (error) {
        preferredMode = defaultMode;
    }

    var darkPreference = false;
    if (window.matchMedia) {
        darkPreference = window.matchMedia("(prefers-color-scheme: dark)").matches;
    }

    var resolvedTheme = preferredMode === "system"
        ? (darkPreference ? "dark" : "light")
        : preferredMode;

    var root = document.documentElement;
    root.setAttribute("data-theme-mode", preferredMode);
    root.setAttribute("data-theme", resolvedTheme);
    root.style.colorScheme = resolvedTheme;
})();