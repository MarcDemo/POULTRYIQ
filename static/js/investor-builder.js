(() => {
    "use strict";

    const root = document.getElementById("investorBuilderV2");
    const catalogNode = document.getElementById("investor-builder-catalog");
    const mount = document.getElementById("builderCardMount");
    if (!root || !catalogNode || !mount) return;

    let catalog;
    try {
        catalog = JSON.parse(catalogNode.textContent);
    } catch (_error) {
        return;
    }

    const qs = (selector, parent = root) => parent.querySelector(selector);
    const qsa = (selector, parent = root) => Array.from(parent.querySelectorAll(selector));
    const metricByKey = new Map(catalog.metrics.map((metric) => [metric.key, metric]));
    const dimensionByKey = new Map(catalog.dimensions.map((dimension) => [dimension.key, dimension]));
    const questionByKey = new Map(catalog.questions.map((question) => [question.key, question]));
    const csrfToken = qs('input[name="csrfmiddlewaretoken"]').value;
    const panels = qsa("[data-analysis-panel]");
    const charts = [];
    const state = {
        mode: "guided",
        step: "question",
        questionKey: null,
        report: null,
        theme: "all",
        scopeReturn: "question",
    };

    try {
        sessionStorage.removeItem("poultryiqInvestorBuilderState");
        sessionStorage.removeItem("investorAnalysisBuilderState");
    } catch (_error) {
        // Session storage can be unavailable in privacy-restricted browsers.
    }

    mount.replaceChildren(root);
    root.hidden = false;

    function escapeHtml(value) {
        const node = document.createElement("div");
        node.textContent = value == null ? "" : String(value);
        return node.innerHTML;
    }

    function showStatus(message = "", type = "") {
        const node = qs("#analysisStatus");
        node.className = `analysis-status${message ? "" : " d-none"}${type ? ` is-${type}` : ""}`;
        node.textContent = message;
    }

    function setBusy(isBusy) {
        qsa("button", root).forEach((button) => {
            if (button.closest(".modal")) return;
            if (isBusy) {
                button.dataset.analysisWasDisabled = button.disabled ? "1" : "0";
                button.disabled = true;
            } else if ("analysisWasDisabled" in button.dataset) {
                button.disabled = button.dataset.analysisWasDisabled === "1";
                delete button.dataset.analysisWasDisabled;
            }
        });
        root.setAttribute("aria-busy", String(isBusy));
    }

    function showPanel(name) {
        state.step = name;
        panels.forEach((panel) => panel.classList.toggle("d-none", panel.dataset.analysisPanel !== name));
        const guidedNavigation = state.mode === "guided";
        qs("#analysisStepper").classList.toggle("d-none", !guidedNavigation);
        const order = ["question", "scope", "report"];
        qsa("[data-analysis-step-target]").forEach((button) => {
            const index = order.indexOf(button.dataset.analysisStepTarget);
            const currentIndex = order.indexOf(name);
            button.classList.toggle("is-active", button.dataset.analysisStepTarget === name);
            button.classList.toggle("is-complete", index < currentIndex);
            button.disabled = index > currentIndex || (index === 1 && !state.questionKey);
        });
        showStatus();
    }

    function setMode(mode) {
        state.mode = mode;
        qsa("[data-analysis-mode]").forEach((button) => {
            button.classList.toggle("is-active", button.dataset.analysisMode === mode);
            button.setAttribute("aria-pressed", String(button.dataset.analysisMode === mode));
        });
        showPanel(mode === "guided" ? "question" : "analyst");
    }

    function selectedMetricKeys() {
        return qsa('#analysisMetricList input[type="checkbox"]:checked').map((input) => input.value);
    }

    function sharedDimensions(keys) {
        if (!keys.length) return [];
        return catalog.dimensions.filter((dimension) =>
            keys.every((key) => metricByKey.get(key).dimensions.includes(dimension.key))
        );
    }

    function refreshAnalystControls() {
        const keys = selectedMetricKeys();
        qs("#analysisSelectionCount").textContent = `${keys.length} of 6 selected`;
        const dimensions = sharedDimensions(keys);
        const dimensionSelect = qs("#analysisDimension");
        const previousDimension = dimensionSelect.value;
        dimensionSelect.replaceChildren();
        if (!dimensions.length) {
            dimensionSelect.add(new Option(keys.length ? "No shared breakdown available" : "Select indicators first", ""));
        } else {
            dimensions.forEach((dimension) => dimensionSelect.add(new Option(dimension.label, dimension.key)));
            dimensionSelect.value = dimensions.some((item) => item.key === previousDimension)
                ? previousDimension
                : dimensions[0].key;
        }
        refreshViewOptions();
        const enabled = keys.length > 0 && dimensions.length > 0;
        qs("#analysisAnalystGenerate").disabled = !enabled;
    }

    function refreshViewOptions() {
        const keys = selectedMetricKeys();
        const dimension = qs("#analysisDimension").value;
        const current = qs("#analysisView").value;
        const options = [
            ["bar", "Bar chart"],
            ["table", "Table"],
        ];
        if (dimension === "period") options.splice(1, 0, ["line", "Line chart"], ["area", "Area chart"]);
        if (keys.length === 1 && metricByKey.get(keys[0])?.additive) options.splice(1, 0, ["pie", "Pie chart"]);
        const select = qs("#analysisView");
        select.replaceChildren();
        options.forEach(([value, label]) => select.add(new Option(label, value)));
        select.value = options.some(([value]) => value === current)
            ? current
            : (dimension === "period" ? "line" : "bar");
    }

    function filterMetrics() {
        const search = qs("#analysisMetricSearch").value.trim().toLowerCase();
        qsa(".analysis-metric").forEach((row) => {
            const themeMatch = state.theme === "all" || row.dataset.theme === state.theme;
            const searchMatch = !search || row.dataset.search.includes(search);
            row.classList.toggle("is-hidden", !themeMatch || !searchMatch);
        });
    }

    function scopePayload() {
        const scopeType = qs("#analysisScopeType").value;
        const scopeId = scopeType === "house"
            ? qs("#analysisHouse").value
            : scopeType === "batch"
                ? qs("#analysisBatch").value
                : null;
        if (scopeType !== "farm" && !scopeId) {
            throw new Error(`Select ${scopeType === "house" ? "a poultry house" : "a flock batch"}.`);
        }
        const startDate = qs("#analysisStartDate").value;
        const endDate = qs("#analysisEndDate").value;
        if (!startDate || !endDate) throw new Error("Select both start and end dates.");
        if (startDate > endDate) throw new Error("Start date cannot be after end date.");
        return {
            scopeType,
            scopeId,
            startDate,
            endDate,
            groupBy: qs("#analysisGroupBy").value,
        };
    }

    function scopeLabel(meta) {
        if (meta.scope.type === "farm") return "Whole farm";
        const select = meta.scope.type === "house" ? qs("#analysisHouse") : qs("#analysisBatch");
        return select.options[select.selectedIndex]?.text || `${meta.scope.type} ${meta.scope.id}`;
    }

    async function generateReport() {
        let payload;
        try {
            payload = {
                mode: state.mode,
                questionKey: state.questionKey,
                metricKeys: state.mode === "analyst" ? selectedMetricKeys() : [],
                dimension: state.mode === "analyst"
                    ? qs("#analysisDimension").value
                    : questionByKey.get(state.questionKey)?.dimension || "period",
                view: state.mode === "analyst" ? qs("#analysisView").value : "line",
                ...scopePayload(),
            };
            if (state.mode === "guided" && !state.questionKey) throw new Error("Select a business question.");
            if (state.mode === "analyst" && !payload.metricKeys.length) throw new Error("Select at least one indicator.");
        } catch (error) {
            showStatus(error.message, "error");
            return;
        }

        setBusy(true);
        showStatus("Building the analysis from the selected farm records…", "loading");
        try {
            const response = await fetch(root.dataset.analysisUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken,
                },
                body: JSON.stringify(payload),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "The analysis could not be generated.");
            state.report = data;
            renderReport(data);
            showPanel("report");
            showStatus(data.meta.viewAdjustment || "Report generated successfully.", "success");
        } catch (error) {
            showStatus(error.message, "error");
        } finally {
            setBusy(false);
            refreshAnalystControls();
        }
    }

    function formatDate(value) {
        if (!value) return "—";
        return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
            .format(new Date(`${value}T00:00:00Z`));
    }

    function formatValue(value, metric) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return "Unavailable";
        const number = Number(value);
        if (metric.format === "currency") {
            return `UGX ${new Intl.NumberFormat("en-UG", { maximumFractionDigits: 0 }).format(number)}`;
        }
        if (metric.format === "percent") return `${number.toFixed(1)}%`;
        if (metric.format === "kg" || metric.unit === "kg") {
            return `${new Intl.NumberFormat("en-UG", { maximumFractionDigits: 2 }).format(number)} kg`;
        }
        const digits = metric.format === "number" ? 0 : 2;
        return `${new Intl.NumberFormat("en-UG", { maximumFractionDigits: digits }).format(number)}${metric.unit && !["records", "units"].includes(metric.unit) ? ` ${metric.unit}` : ""}`;
    }

    function varianceSignal(metric) {
        if (metric.previousValue === null || metric.previousValue === undefined) {
            return '<span class="analysis-signal neutral">Prior: unavailable</span>';
        }
        if (metric.percentChange === null || metric.percentChange === undefined) {
            return '<span class="analysis-signal neutral">Prior baseline: 0</span>';
        }
        const change = Number(metric.percentChange);
        const style = metric.varianceState === "favorable"
            ? "good"
            : metric.varianceState === "unfavorable"
                ? "bad"
                : "neutral";
        const arrow = change > 0 ? "↑" : change < 0 ? "↓" : "→";
        return `<span class="analysis-signal ${style}">${arrow} ${Math.abs(change).toFixed(1)}% vs prior</span>`;
    }

    function targetSignal(metric) {
        const target = metric.target || {};
        if (!target.configured) return '<span class="analysis-signal neutral">No target set</span>';
        const style = target.status === "met" ? "good" : target.status === "missed" ? "bad" : "neutral";
        return `<span class="analysis-signal ${style}">${escapeHtml(target.label)} · ${escapeHtml(formatValue(target.value, metric))}</span>`;
    }

    function destroyCharts() {
        while (charts.length) charts.pop().destroy();
    }

    function renderReport(report) {
        destroyCharts();
        const metrics = new Map(report.metrics.map((metric) => [metric.key, metric]));
        qs("#analysisReportTitle").textContent = report.meta.title;
        qs("#analysisReportMeta").textContent =
            `${scopeLabel(report.meta)} · ${formatDate(report.meta.range.start)}–${formatDate(report.meta.range.end)} · ` +
            `comparison ${formatDate(report.meta.previousRange.start)}–${formatDate(report.meta.previousRange.end)}`;
        const revenueCoverage = report.coverage.revenueAllocationPercent;
        const expenseCoverage = report.coverage.expenseAllocationPercent;
        qs("#analysisCoverage").innerHTML =
            `<strong>Record coverage:</strong> ${revenueCoverage == null ? "Unavailable" : `${Number(revenueCoverage).toFixed(1)}%`} of line revenue is batch-linked; ` +
            `${expenseCoverage == null ? "Unavailable" : `${Number(expenseCoverage).toFixed(1)}%`} of expenses is explicitly allocated. ` +
            `<span>${revenueCoverage == null ? "" : `${(100 - Number(revenueCoverage)).toFixed(1)}% revenue`} ${revenueCoverage != null && expenseCoverage != null ? "and " : ""}${expenseCoverage == null ? "" : `${(100 - Number(expenseCoverage)).toFixed(1)}% expenses`} are unallocated.</span>`;

        qs("#analysisKpis").innerHTML = report.metrics.map((metric) => `
            <article class="analysis-kpi">
                <span class="analysis-kpi-label">${escapeHtml(metric.label)}</span>
                <strong class="analysis-kpi-value">${escapeHtml(formatValue(metric.value, metric))}</strong>
                <div class="analysis-kpi-signals">${varianceSignal(metric)}${targetSignal(metric)}</div>
            </article>
        `).join("");

        const highlights = qs("#analysisHighlights");
        highlights.classList.toggle("d-none", !report.highlights.length);
        highlights.innerHTML = report.highlights.length
            ? `<strong>Variance highlights</strong><ul>${report.highlights.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
            : "";

        const sectionList = qs("#analysisSections");
        sectionList.innerHTML = report.sections.map((section, index) => `
            <section class="analysis-report-section">
                <h4>${escapeHtml(section.title)}</h4>
                ${report.meta.view === "table"
                    ? `<div class="table-responsive" id="analysisSectionTable${index}"></div>`
                    : `<div class="analysis-chart"><canvas id="analysisSectionChart${index}" aria-label="${escapeHtml(section.title)} chart"></canvas></div>
                       <details class="analysis-supporting-data"><summary>View supporting data</summary><div class="table-responsive" id="analysisSectionTable${index}"></div></details>`}
            </section>
        `).join("");

        report.sections.forEach((section, index) => {
            const sectionMetrics = section.metricKeys.map((key) => metrics.get(key)).filter(Boolean);
            if (report.meta.view === "table") {
                renderSectionTable(qs(`#analysisSectionTable${index}`), sectionMetrics);
            } else {
                renderSectionChart(qs(`#analysisSectionChart${index}`), sectionMetrics, report.meta.view);
                renderSectionTable(qs(`#analysisSectionTable${index}`), sectionMetrics);
            }
        });

        qs("#analysisDetailTable").innerHTML = `
            <table class="table table-sm align-middle mb-0">
                <thead><tr><th>Indicator</th><th>Current</th><th>Previous</th><th>Change</th><th>Farm target</th><th>Records</th></tr></thead>
                <tbody>${report.metrics.map((metric) => `
                    <tr>
                        <th>${escapeHtml(metric.label)}<small class="d-block text-muted">${escapeHtml(metric.source)}</small></th>
                        <td>${escapeHtml(formatValue(metric.value, metric))}</td>
                        <td>${escapeHtml(formatValue(metric.previousValue, metric))}</td>
                        <td>${metric.percentChange == null ? "Unavailable" : `${Number(metric.percentChange).toFixed(1)}%`}</td>
                        <td>${escapeHtml(metric.target?.configured ? `${metric.target.label}: ${formatValue(metric.target.value, metric)}` : "No target set")}</td>
                        <td>${metric.records == null ? "—" : new Intl.NumberFormat("en-UG").format(metric.records)}</td>
                    </tr>
                `).join("")}</tbody>
            </table>`;
        qs("#analysisGeneratedAt").textContent = `Generated ${new Date(report.meta.generatedAt).toLocaleString("en-GB")}`;
        qs("#analysisPng").disabled = report.meta.view === "table";
    }

    function alignedSeries(metrics) {
        const labels = [];
        metrics.forEach((metric) => metric.points.forEach((point) => {
            if (!labels.includes(point.label)) labels.push(point.label);
        }));
        return {
            labels,
            datasets: metrics.map((metric, index) => {
                const values = new Map(metric.points.map((point) => [point.label, point.value]));
                const colors = ["#087abd", "#f59e0b", "#16a34a", "#dc3545", "#7c3aed", "#64748b"];
                const color = colors[index % colors.length];
                return {
                    label: metric.label,
                    data: labels.map((label) => values.has(label) ? values.get(label) : null),
                    borderColor: color,
                    backgroundColor: color,
                    borderWidth: 2,
                    fill: false,
                    tension: 0.25,
                };
            }),
        };
    }

    function renderSectionChart(canvas, metrics, requestedView) {
        if (!canvas || !metrics.length || typeof Chart === "undefined") return;
        const data = alignedSeries(metrics);
        let type = requestedView === "area" ? "line" : requestedView;
        if (type === "pie" && metrics.length !== 1) type = "bar";
        if (requestedView === "area") {
            data.datasets.forEach((dataset) => {
                dataset.fill = true;
                dataset.backgroundColor = `${dataset.backgroundColor}22`;
            });
        }
        if (type === "pie") {
            data.datasets[0].backgroundColor = ["#087abd", "#f59e0b", "#16a34a", "#dc3545", "#7c3aed", "#64748b", "#0ea5e9", "#84cc16"];
        }
        charts.push(new Chart(canvas, {
            type,
            data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: { legend: { position: "bottom" } },
                scales: type === "pie" ? {} : { y: { beginAtZero: false } },
            },
        }));
    }

    function renderSectionTable(container, metrics) {
        if (!container) return;
        const { labels } = alignedSeries(metrics);
        container.innerHTML = `<table class="table table-sm mb-0"><thead><tr><th>Breakdown</th>${metrics.map((metric) => `<th>${escapeHtml(metric.label)}</th>`).join("")}</tr></thead>
            <tbody>${labels.map((label) => `<tr><th>${escapeHtml(label)}</th>${metrics.map((metric) => {
                const point = metric.points.find((item) => item.label === label);
                return `<td>${escapeHtml(point ? formatValue(point.value, metric) : "—")}</td>`;
            }).join("")}</tr>`).join("")}</tbody></table>`;
    }

    function csvCell(value) {
        return `"${String(value ?? "").replaceAll('"', '""')}"`;
    }

    function exportCsv() {
        if (!state.report) return;
        const report = state.report;
        const rows = [
            ["Investor analysis", report.meta.title],
            ["Scope", scopeLabel(report.meta)],
            ["Period", `${report.meta.range.start} to ${report.meta.range.end}`],
            ["Previous period", `${report.meta.previousRange.start} to ${report.meta.previousRange.end}`],
            ["Generated", report.meta.generatedAt],
            [],
            ["Indicator", "Unit", "Current", "Previous", "Change %", "Target", "Target status", "Records"],
            ...report.metrics.map((metric) => [
                metric.label, metric.unit, metric.value, metric.previousValue, metric.percentChange,
                metric.target?.configured ? metric.target.value : "", metric.target?.label || "No target set", metric.records,
            ]),
            [],
            ["Breakdown data"],
        ];
        report.metrics.forEach((metric) => {
            rows.push([metric.label]);
            rows.push(["Label", "Value"]);
            metric.points.forEach((point) => rows.push([point.label, point.value]));
        });
        const blob = new Blob([rows.map((row) => row.map(csvCell).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `investor-analysis-${report.meta.range.end}.csv`;
        link.click();
        URL.revokeObjectURL(link.href);
    }

    function exportPng() {
        const canvas = qs("#analysisSections canvas");
        if (!canvas) {
            showStatus("Choose a chart visualization before exporting a PNG.", "error");
            return;
        }
        const link = document.createElement("a");
        link.href = canvas.toDataURL("image/png", 1);
        link.download = `investor-analysis-${state.report.meta.range.end}.png`;
        link.click();
    }

    function updateScopeVisibility() {
        const type = qs("#analysisScopeType").value;
        qs("#analysisHouseWrap").classList.toggle("d-none", type !== "house");
        qs("#analysisBatchWrap").classList.toggle("d-none", type !== "batch");
    }

    function applyPreset(preset) {
        const today = new Date();
        let start;
        if (preset === "month") start = new Date(today.getFullYear(), today.getMonth(), 1);
        if (preset === "quarter") start = new Date(today.getFullYear(), Math.floor(today.getMonth() / 3) * 3, 1);
        if (preset === "year") start = new Date(today.getFullYear(), 0, 1);
        const localIso = (date) => {
            const offset = date.getTimezoneOffset();
            return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 10);
        };
        qs("#analysisStartDate").value = localIso(start);
        qs("#analysisEndDate").value = localIso(today);
    }

    async function loadTargets() {
        const status = qs("#analysisTargetStatus");
        status.textContent = "Loading target history…";
        try {
            const response = await fetch(root.dataset.targetsUrl, { credentials: "same-origin" });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Target history could not be loaded.");
            qs("#analysisTargetHistory").innerHTML = data.targets.length
                ? data.targets.map((target) => `
                    <tr><th>${escapeHtml(target.metricLabel)}</th><td>${target.direction === "MINIMUM" ? "At least" : "At most"}</td>
                    <td>${escapeHtml(String(target.value))}</td><td>${escapeHtml(formatDate(target.effectiveFrom))} – ${target.effectiveTo ? escapeHtml(formatDate(target.effectiveTo)) : "Current"}</td>
                    <td>${escapeHtml(target.createdBy)}</td></tr>`).join("")
                : '<tr><td colspan="5" class="text-muted">No targets have been configured.</td></tr>';
            status.textContent = "";
        } catch (error) {
            status.textContent = error.message;
            status.className = "mt-3 text-danger";
        }
    }

    qsa("[data-analysis-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.analysisMode)));
    qsa(".analysis-question").forEach((button) => button.addEventListener("click", () => {
        state.questionKey = button.dataset.questionKey;
        qsa(".analysis-question").forEach((item) => item.classList.toggle("is-selected", item === button));
        qs("#analysisQuestionNext").disabled = false;
    }));
    qs("#analysisQuestionNext").addEventListener("click", () => {
        state.scopeReturn = "question";
        showPanel("scope");
    });
    qs("#analysisScopeBack").addEventListener("click", () => showPanel(state.mode === "analyst" ? "analyst" : state.scopeReturn));
    qs("#analysisScopeGenerate").addEventListener("click", generateReport);
    qs("#analysisAnalystGenerate").addEventListener("click", generateReport);
    qs("#analysisEditScope").addEventListener("click", () => {
        state.scopeReturn = "analyst";
        showPanel("scope");
    });
    qs("#analysisReportBack").addEventListener("click", () => showPanel(state.mode === "guided" ? "scope" : "analyst"));
    qs("#analysisScopeType").addEventListener("change", updateScopeVisibility);
    qsa("[data-date-preset]").forEach((button) => button.addEventListener("click", () => applyPreset(button.dataset.datePreset)));
    qsa("[data-analysis-theme]").forEach((button) => button.addEventListener("click", () => {
        state.theme = button.dataset.analysisTheme;
        qsa("[data-analysis-theme]").forEach((item) => item.classList.toggle("is-active", item === button));
        filterMetrics();
    }));
    qs("#analysisMetricSearch").addEventListener("input", filterMetrics);
    qsa('#analysisMetricList input[type="checkbox"]').forEach((input) => input.addEventListener("change", () => {
        if (input.checked && selectedMetricKeys().length > 6) {
            input.checked = false;
            showStatus("Analyst reports support up to six indicators.", "error");
        } else {
            showStatus();
        }
        refreshAnalystControls();
    }));
    qs("#analysisDimension").addEventListener("change", refreshViewOptions);
    qs("#analysisCsv").addEventListener("click", exportCsv);
    qs("#analysisPng").addEventListener("click", exportPng);
    qs("#analysisPrint").addEventListener("click", () => window.print());

    const targetsButton = qs("#analysisTargetsButton");
    if (targetsButton) targetsButton.addEventListener("click", () => {
        loadTargets();
        bootstrap.Modal.getOrCreateInstance(qs("#analysisTargetsModal")).show();
    });
    const targetForm = qs("#analysisTargetForm");
    if (targetForm) targetForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const status = qs("#analysisTargetStatus");
        status.className = "mt-3";
        status.textContent = "Saving target…";
        try {
            const response = await fetch(root.dataset.targetsUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
                body: JSON.stringify({
                    metricKey: qs("#analysisTargetMetric").value,
                    targetValue: qs("#analysisTargetValue").value,
                    effectiveFrom: qs("#analysisTargetDate").value,
                }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "The target could not be saved.");
            status.className = "mt-3 text-success";
            status.textContent = "Target version saved.";
            qs("#analysisTargetValue").value = "";
            await loadTargets();
        } catch (error) {
            status.className = "mt-3 text-danger";
            status.textContent = error.message;
        }
    });

    updateScopeVisibility();
    refreshAnalystControls();
    setMode("guided");
})();
