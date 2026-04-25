document.addEventListener("DOMContentLoaded", () => {
    const toggles = document.querySelectorAll("[data-sidebar-toggle]");

    toggles.forEach((toggle) => {
        const sidebarId = toggle.getAttribute("data-sidebar-toggle");
        const sidebar = document.getElementById(sidebarId);
        const overlay = document.querySelector(`[data-sidebar-overlay="${sidebarId}"]`);

        if (!sidebar) {
            return;
        }

        const closeSidebar = () => {
            sidebar.classList.remove("show");
            document.body.classList.remove("sidebar-open");
            toggle.setAttribute("aria-expanded", "false");
        };

        const openSidebar = () => {
            sidebar.classList.add("show");
            document.body.classList.add("sidebar-open");
            toggle.setAttribute("aria-expanded", "true");
        };

        toggle.addEventListener("click", () => {
            if (sidebar.classList.contains("show")) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });

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
});
