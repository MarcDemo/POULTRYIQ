document.addEventListener("DOMContentLoaded", () => {
    const toggles = document.querySelectorAll("[data-sidebar-toggle]");
    const configuredSidebars = new Set();

    toggles.forEach((toggle) => {
        const sidebarId = toggle.getAttribute("data-sidebar-toggle");
        const sidebar = document.getElementById(sidebarId);
        const overlay = document.querySelector(`[data-sidebar-overlay="${sidebarId}"]`);

        if (!sidebar) {
            return;
        }

        const matchingToggles = document.querySelectorAll(`[data-sidebar-toggle="${sidebarId}"]`);
        const setExpanded = (expanded) => {
            matchingToggles.forEach((item) => item.setAttribute("aria-expanded", String(expanded)));
        };

        const closeSidebar = () => {
            sidebar.classList.remove("show");
            document.body.classList.remove("sidebar-open");
            setExpanded(false);
        };

        const openSidebar = () => {
            sidebar.classList.add("show");
            document.body.classList.add("sidebar-open");
            setExpanded(true);
        };

        toggle.addEventListener("click", () => {
            if (sidebar.classList.contains("show")) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });

        if (configuredSidebars.has(sidebarId)) {
            return;
        }
        configuredSidebars.add(sidebarId);

        if (overlay) {
            overlay.addEventListener("click", closeSidebar);
        }

        sidebar.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", () => {
                if (window.innerWidth < 992) {
                    closeSidebar();
                }
            });
        });

        window.addEventListener("resize", () => {
            if (window.innerWidth >= 992) {
                closeSidebar();
            }
        });
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        document.querySelectorAll(".sidebar.show").forEach((sidebar) => {
            sidebar.classList.remove("show");
            document.body.classList.remove("sidebar-open");
            document.querySelectorAll(`[data-sidebar-toggle="${sidebar.id}"]`).forEach((toggle) => {
                toggle.setAttribute("aria-expanded", "false");
            });
        });
    });

    document.querySelectorAll(".manager-nav-trigger").forEach((trigger) => {
        trigger.setAttribute("aria-expanded", "false");
        trigger.addEventListener("click", () => {
            const group = trigger.closest(".manager-nav-group");
            const isOpen = group.classList.toggle("is-open");
            trigger.setAttribute("aria-expanded", String(isOpen));
        });
    });
});
