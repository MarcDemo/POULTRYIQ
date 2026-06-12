(function () {
    const dataNode = document.getElementById("investor-chart-data");
    if (!dataNode || typeof Chart === "undefined") return;

    const reportData = JSON.parse(dataNode.textContent);
    const reportType = document.getElementById("report_type");
    const chartType = document.getElementById("chart_type");
    const chartTitle = document.getElementById("chartTitle");
    const chartUnit = document.getElementById("chartUnit");
    const breakdownTable = document.getElementById("breakdownTable");
    const canvas = document.getElementById("reportChart");
    const reportForm = document.getElementById("investorReportForm");
    const generateButton = document.getElementById("generateReportButton");
    if (!reportType || !chartType || !chartTitle || !chartUnit || !breakdownTable || !canvas) return;
    let chart;

    const palette = [
        "#2563eb",
        "#16a34a",
        "#dc2626",
        "#f59e0b",
        "#7c3aed",
        "#0891b2",
        "#be123c",
        "#475569",
    ];

    function formatValue(value, unit) {
        const number = Number(value || 0);
        if (unit === "UGX") {
            return "UGX " + number.toLocaleString(undefined, { maximumFractionDigits: 0 });
        }
        return number.toLocaleString(undefined, { maximumFractionDigits: 2 }) + (unit ? " " + unit : "");
    }

    function getActiveReport() {
        return reportData[reportType.value] || reportData.profit;
    }

    function buildPieData(report) {
        if (Array.isArray(report.pie) && report.pie.length) {
            return {
                labels: report.pie.map((item) => item.label),
                values: report.pie.map((item) => item.value),
            };
        }

        if (!report.series || !report.series.length) {
            return { labels: [], values: [] };
        }

        return {
            labels: report.series.map((series) => series.label),
            values: report.series.map((series) => {
                return (series.values || []).reduce((total, value) => total + Number(value || 0), 0);
            }),
        };
    }

    function renderBreakdown(report) {
        const pieData = buildPieData(report);
        breakdownTable.innerHTML = "";

        if (!pieData.labels.length) {
            breakdownTable.innerHTML = '<tr><td colspan="2" class="text-muted">No breakdown data for this selection.</td></tr>';
            return;
        }

        pieData.labels.forEach((label, index) => {
            const row = document.createElement("tr");
            row.innerHTML = `
                <td>${label}</td>
                <td class="text-end">${formatValue(pieData.values[index], report.unit)}</td>
            `;
            breakdownTable.appendChild(row);
        });
    }

    function renderChart() {
        const report = getActiveReport();
        const selectedChartType = chartType.value;
        const isPie = selectedChartType === "pie";

        if (chart) chart.destroy();

        chartTitle.textContent = report.title || "Report Visualization";
        chartUnit.textContent = report.unit || "";
        renderBreakdown(report);

        if (isPie) {
            const pieData = buildPieData(report);
            chart = new Chart(canvas, {
                type: "pie",
                data: {
                    labels: pieData.labels,
                    datasets: [{
                        data: pieData.values,
                        backgroundColor: palette,
                        borderColor: "#ffffff",
                        borderWidth: 2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        tooltip: {
                            callbacks: {
                                label: (context) => `${context.label}: ${formatValue(context.raw, report.unit)}`,
                            },
                        },
                    },
                },
            });
            return;
        }

        const labels = report.series && report.series.length ? report.series[0].labels : [];
        const datasets = (report.series || []).map((series, index) => ({
            label: series.label,
            data: series.values,
            borderColor: palette[index % palette.length],
            backgroundColor: palette[index % palette.length] + "33",
            borderWidth: 2,
            tension: 0.25,
            fill: selectedChartType === "line" ? false : true,
        }));

        chart = new Chart(canvas, {
            type: selectedChartType,
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: "index",
                    intersect: false,
                },
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: (context) => `${context.dataset.label}: ${formatValue(context.raw, report.unit)}`,
                        },
                    },
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: (value) => report.unit === "UGX" ? Number(value).toLocaleString() : value,
                        },
                    },
                },
            },
        });
    }

    reportType.addEventListener("change", renderChart);
    chartType.addEventListener("change", renderChart);
    if (reportForm && generateButton) {
        reportForm.addEventListener("submit", function () {
            generateButton.disabled = true;
            generateButton.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Generating';
        });
    }
    renderChart();
})();

(function () {
    const dataNode = document.getElementById("investor-financial-data");
    if (!dataNode || typeof Chart === "undefined") return;

    const data = JSON.parse(dataNode.textContent);
    const palette = {
        revenue: "#0b73b9",
        expenses: "#dc3545",
        profit: "#159c72",
        warning: "#d97706",
        neutral: "#526273",
    };

    function money(value) {
        return "UGX " + Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
    }

    function makeChart(canvasId, config) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;
        new Chart(canvas, config);
    }

    const profitTrend = data.profitTrend || {};
    const profitSeries = profitTrend.series || [];
    makeChart("financialTrendChart", {
        type: "bar",
        data: {
            labels: profitSeries[0] ? profitSeries[0].labels : [],
            datasets: profitSeries.map((series, index) => ({
                label: series.label,
                data: series.values,
                type: series.label === "Profit" ? "line" : "bar",
                borderColor: [palette.revenue, palette.expenses, palette.profit][index] || palette.neutral,
                backgroundColor: ([palette.revenue, palette.expenses, palette.profit][index] || palette.neutral) + "55",
                borderWidth: 2,
                tension: 0.25,
                fill: false,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "bottom" } },
            scales: {
                y: { beginAtZero: true, ticks: { callback: (value) => Number(value).toLocaleString() } },
            },
        },
    });

    const costDrivers = data.costDrivers || [];
    makeChart("costDriverChart", {
        type: "bar",
        data: {
            labels: costDrivers.map((item) => item.label),
            datasets: [{
                label: "Expenses",
                data: costDrivers.map((item) => item.value),
                backgroundColor: "#dc354566",
                borderColor: "#dc3545",
                borderWidth: 2,
                borderRadius: 6,
            }],
        },
        options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: (context) => money(context.raw) } },
            },
            scales: {
                x: { beginAtZero: true, ticks: { callback: (value) => Number(value).toLocaleString() } },
            },
        },
    });

    const unitEconomics = data.unitEconomics || [];
    makeChart("unitEconomicsChart", {
        type: "bar",
        data: {
            labels: unitEconomics.map((item) => item.label),
            datasets: [{
                label: "UGX",
                data: unitEconomics.map((item) => item.value),
                backgroundColor: ["#0b73b966", "#d9770666", "#159c7266", "#dc354566"],
                borderColor: ["#0b73b9", "#d97706", "#159c72", "#dc3545"],
                borderWidth: 2,
                borderRadius: 6,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: (context) => money(context.raw) } },
            },
            scales: {
                y: { beginAtZero: true, ticks: { callback: (value) => Number(value).toLocaleString() } },
            },
        },
    });

    const cashRisk = data.cashRisk || [];
    makeChart("cashRiskChart", {
        type: "doughnut",
        data: {
            labels: cashRisk.map((item) => item.label),
            datasets: [{
                data: cashRisk.map((item) => item.value),
                backgroundColor: ["#159c72", "#d97706", "#dc3545"],
                borderColor: "#ffffff",
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: "bottom" },
                tooltip: { callbacks: { label: (context) => `${context.label}: ${money(context.raw)}` } },
            },
        },
    });
})();

(function () {
    const dataNode = document.getElementById("investor-builder-data");
    const root = document.getElementById("investorBuilder");
    if (!dataNode || !root) return;

    const builderMount = document.getElementById("builderCardMount");
    if (builderMount && root.parentElement !== builderMount) {
        builderMount.replaceChildren();
        builderMount.appendChild(root);
    }
    root.hidden = false;

    const builderData = JSON.parse(dataNode.textContent);
    const indicators = builderData.indicators || [];
    const indicatorByKey = new Map(indicators.map((indicator) => [indicator.key, indicator]));
    const presetByKey = new Map((builderData.presets || []).map((preset) => [preset.key, preset]));
    const dimensionByKey = new Map((builderData.dimensions || []).map((dimension) => [dimension.key, dimension]));

    const indicatorList = document.getElementById("builderIndicatorList");
    const searchInput = document.getElementById("builderSearch");
    const dimensionSelect = document.getElementById("builderDimension");
    const viewMode = document.getElementById("builderViewMode");
    const selectedChips = document.getElementById("builderSelectedChips");
    const indicatorCount = document.getElementById("builderIndicatorCount");
    const breakdownLabel = document.getElementById("builderBreakdownLabel");
    const rowCount = document.getElementById("builderRowCount");
    const chartTitle = document.getElementById("builderChartTitle");
    const chartSubtitle = document.getElementById("builderChartSubtitle");
    const generatedMeta = document.getElementById("builderGeneratedMeta");
    const resultSummary = document.getElementById("builderResultSummary");
    const resultFinding = document.getElementById("builderResultFinding");
    const resultRecommendation = document.getElementById("builderResultRecommendation");
    const chartCanvas = document.getElementById("builderChart");
    const chartWrap = document.getElementById("builderChartWrap");
    const tableWrap = document.getElementById("builderTableWrap");
    const table = document.getElementById("builderTable");
    const emptyState = document.getElementById("builderEmpty");
    const generateButton = document.getElementById("builderGenerate");
    const exportCsvButton = document.getElementById("builderExportCsv");
    const exportImageButton = document.getElementById("builderExportImage");
    const fullscreenButton = document.getElementById("builderFullscreen");
    const output = document.getElementById("builderOutput");
    const guideNote = document.getElementById("builderGuideNote");
    const guideNav = document.getElementById("builderGuideNav");
    const stepBackButton = document.getElementById("builderStepBack");
    const stepNextButton = document.getElementById("builderStepNext");
    const guidePanels = root.querySelectorAll("[data-guide-panel]");
    const guideStepButtons = root.querySelectorAll("[data-guide-step-target]");
    const builderStartDate = document.getElementById("builderStartDate");
    const builderEndDate = document.getElementById("builderEndDate");
    const rangeLabel = document.getElementById("builderRangeLabel");
    const investorReportForm = document.getElementById("investorReportForm");
    const globalStartDate = document.getElementById("start_date");
    const globalEndDate = document.getElementById("end_date");

    let activeTheme = "all";
    let activeMode = "guided";
    let activeView = "bar";
    let activeGuideStep = "theme";
    let hasGenerated = false;
    let chart;
    let currentRows = [];
    let currentIndicators = [];

    const guideSteps = [
        {
            key: "theme",
            title: "Choose a theme",
            text: "Start with the financial area the investor wants to understand.",
            next: "Continue",
        },
        {
            key: "question",
            title: "Pick a business question",
            text: "Choose the investor question to answer, or continue and pick indicators manually.",
            next: "Continue",
        },
        {
            key: "indicators",
            title: "Select indicators",
            text: "Pick the financial and production-cost indicators to compare.",
            next: "Continue",
        },
        {
            key: "locations",
            title: "Pick location",
            text: "Choose whether the result should be broken down by batch, house, customer, expense category, product, or time.",
            next: "Continue",
        },
        {
            key: "time",
            title: "Choose time range",
            text: "Set the period the investor wants to analyse. The builder will reload the range only when needed.",
            next: "Continue",
        },
        {
            key: "generate",
            title: "Generate visualization",
            text: "Choose chart or table view, then generate and export the result from this same window.",
            next: "Generate visualization",
        },
    ];

    const palette = [
        "#0b73b9",
        "#159c72",
        "#d97706",
        "#c2415b",
        "#6d5dfc",
        "#0f8b8d",
        "#7c2d12",
        "#455a64",
        "#4f46e5",
        "#16a34a",
    ];

    function formatValue(value, indicator) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
        const number = Number(value || 0);
        const format = indicator && indicator.format;
        if (format === "currency") {
            return "UGX " + number.toLocaleString(undefined, { maximumFractionDigits: 0 });
        }
        if (format === "percent") {
            return number.toLocaleString(undefined, { maximumFractionDigits: 1 }) + "%";
        }
        if (format === "kg") {
            return number.toLocaleString(undefined, { maximumFractionDigits: 2 }) + " kg";
        }
        if (format === "ratio") {
            return number.toLocaleString(undefined, { maximumFractionDigits: 3 });
        }
        if (format === "decimal") {
            return number.toLocaleString(undefined, { maximumFractionDigits: 2 });
        }
        return number.toLocaleString(undefined, { maximumFractionDigits: 0 });
    }

    function formatDateLabel(value) {
        if (!value) return "";
        const date = new Date(`${value}T00:00:00`);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    }

    function getBuilderRange() {
        const start = builderStartDate ? builderStartDate.value : builderData.range.start;
        const end = builderEndDate ? builderEndDate.value : builderData.range.end;
        return { start, end };
    }

    function updateRangeLabel() {
        const range = getBuilderRange();
        if (rangeLabel) {
            rangeLabel.textContent = `${formatDateLabel(range.start)} - ${formatDateLabel(range.end)}`;
        }
    }

    function persistBuilderState(stepKey) {
        const range = getBuilderRange();
        const state = {
            activeTheme,
            activeMode,
            activeView,
            activeGuideStep: stepKey || activeGuideStep,
            generated: hasGenerated,
            selectedKeys: getCheckedKeys(),
            dimension: dimensionSelect.value,
            range,
        };
        sessionStorage.setItem("poultryiqInvestorBuilderState", JSON.stringify(state));
    }

    function restoreBuilderState() {
        const rawState = sessionStorage.getItem("poultryiqInvestorBuilderState");
        if (!rawState) return;

        sessionStorage.removeItem("poultryiqInvestorBuilderState");
        try {
            const state = JSON.parse(rawState);
            activeTheme = state.activeTheme || activeTheme;
            activeMode = state.activeMode || activeMode;
            activeGuideStep = state.activeGuideStep || activeGuideStep;
            hasGenerated = Boolean(state.generated);
            if (Array.isArray(state.selectedKeys)) setCheckedKeys(state.selectedKeys);
            if (state.dimension && dimensionByKey.has(state.dimension)) dimensionSelect.value = state.dimension;
            if (state.activeView) setView(state.activeView);
            if (builderStartDate && state.range && state.range.start) builderStartDate.value = state.range.start;
            if (builderEndDate && state.range && state.range.end) builderEndDate.value = state.range.end;
        } catch (error) {
            activeGuideStep = "theme";
        }
    }

    function syncThemeButtons() {
        document.querySelectorAll("[data-theme-key]").forEach((themeButton) => {
            themeButton.classList.toggle("is-active", themeButton.dataset.themeKey === activeTheme);
        });
    }

    function syncPresetButtons() {
        document.querySelectorAll("[data-preset-key]").forEach((button) => {
            const matchesTheme = activeTheme === "all" || button.dataset.presetTheme === activeTheme;
            button.classList.toggle("is-hidden", !matchesTheme);
        });
    }

    function syncModeButtons() {
        document.querySelectorAll("[data-builder-mode]").forEach((modeButton) => {
            modeButton.classList.toggle("is-active", modeButton.dataset.builderMode === activeMode);
        });
    }

    function getGuideStepIndex(stepKey) {
        return Math.max(0, guideSteps.findIndex((step) => step.key === stepKey));
    }

    function setGeneratedState(value) {
        hasGenerated = Boolean(value);
        root.classList.toggle("is-generated", hasGenerated);
    }

    function syncGuide() {
        const currentIndex = getGuideStepIndex(activeGuideStep);
        const hasSelection = getCheckedKeys().length > 0;
        root.dataset.builderModeState = activeMode;
        root.dataset.guideStep = activeGuideStep;
        setGeneratedState(hasGenerated);
        syncModeButtons();
        syncThemeButtons();
        syncPresetButtons();
        updateRangeLabel();

        guideStepButtons.forEach((button) => {
            const stepIndex = getGuideStepIndex(button.dataset.guideStepTarget);
            button.classList.toggle("is-active", button.dataset.guideStepTarget === activeGuideStep);
            button.classList.toggle("is-complete", stepIndex < currentIndex);
            button.disabled = activeMode === "guided" && stepIndex > 2 && !hasSelection;
        });

        guidePanels.forEach((panel) => {
            const panelSteps = (panel.dataset.guidePanel || "").split(/\s+/);
            panel.classList.toggle("is-guide-visible", activeMode === "advanced" || panelSteps.includes(activeGuideStep));
        });

        const step = guideSteps[currentIndex] || guideSteps[0];
        if (guideNote) {
            guideNote.innerHTML = `<strong>${step.title}</strong><span>${step.text}</span>`;
        }

        if (stepBackButton) {
            stepBackButton.disabled = currentIndex === 0;
        }
        if (stepNextButton) {
            stepNextButton.innerHTML = `${step.next} <i class="bi bi-arrow-right ms-1"></i>`;
            stepNextButton.disabled = activeGuideStep === "indicators" && !hasSelection;
        }
        if (guideNav) {
            guideNav.classList.add("is-guide-visible");
        }
    }

    function setGuideStep(stepKey) {
        if (!guideSteps.some((step) => step.key === stepKey)) return;
        if (getGuideStepIndex(stepKey) > 2 && !getCheckedKeys().length) {
            activeGuideStep = "indicators";
        } else {
            activeGuideStep = stepKey;
        }
        setGeneratedState(false);
        syncGuide();
    }

    function goToNextStep() {
        const currentIndex = getGuideStepIndex(activeGuideStep);
        if (activeGuideStep === "generate") {
            generateVisualization();
            return;
        }
        const nextStep = guideSteps[Math.min(currentIndex + 1, guideSteps.length - 1)];
        setGuideStep(nextStep.key);
    }

    function goToPreviousStep() {
        const currentIndex = getGuideStepIndex(activeGuideStep);
        const previousStep = guideSteps[Math.max(currentIndex - 1, 0)];
        setGuideStep(previousStep.key);
    }

    function getCheckedKeys() {
        return Array.from(indicatorList.querySelectorAll("input[type='checkbox']:checked"))
            .map((input) => input.value)
            .filter((key) => indicatorByKey.has(key));
    }

    function setCheckedKeys(keys) {
        const selected = new Set(keys);
        indicatorList.querySelectorAll("input[type='checkbox']").forEach((input) => {
            input.checked = selected.has(input.value);
        });
    }

    function getPoints(indicator, dimensionKey) {
        if (indicator.dimensions && Array.isArray(indicator.dimensions[dimensionKey])) {
            return indicator.dimensions[dimensionKey];
        }
        return [{ label: "Selected period", value: indicator.summary || 0 }];
    }

    function buildRows(selectedIndicators, dimensionKey) {
        const labels = [];
        const valueMaps = selectedIndicators.map((indicator) => {
            const map = new Map();
            getPoints(indicator, dimensionKey).forEach((point) => {
                if (!labels.includes(point.label)) labels.push(point.label);
                map.set(point.label, Number(point.value || 0));
            });
            return map;
        });

        return labels.map((label) => ({
            label,
            values: selectedIndicators.map((indicator, index) => {
                const map = valueMaps[index];
                return map.has(label) ? map.get(label) : null;
            }),
        }));
    }

    function renderTable(rows, selectedIndicators, dimensionKey) {
        const dimension = dimensionByKey.get(dimensionKey) || { label: "Breakdown" };
        const head = table.querySelector("thead");
        const body = table.querySelector("tbody");

        head.innerHTML = "";
        body.innerHTML = "";

        const header = document.createElement("tr");
        header.innerHTML = `<th>${dimension.label}</th>` + selectedIndicators
            .map((indicator) => `<th class="text-end">${indicator.label}</th>`)
            .join("");
        head.appendChild(header);

        if (!rows.length) {
            const row = document.createElement("tr");
            row.innerHTML = `<td colspan="${selectedIndicators.length + 1}" class="text-center text-muted py-4">No data for this selection.</td>`;
            body.appendChild(row);
            return;
        }

        rows.forEach((item) => {
            const row = document.createElement("tr");
            row.innerHTML = `<td>${item.label}</td>` + item.values
                .map((value, index) => `<td class="text-end">${formatValue(value, selectedIndicators[index])}</td>`)
                .join("");
            body.appendChild(row);
        });
    }

    function renderChips(selectedIndicators) {
        selectedChips.innerHTML = "";
        if (!selectedIndicators.length) {
            selectedChips.innerHTML = '<span class="text-muted fw-semibold">No indicators selected</span>';
            return;
        }

        selectedIndicators.forEach((indicator) => {
            const chip = document.createElement("span");
            chip.className = "builder-chip";
            chip.innerHTML = `
                <i class="${indicator.icon}"></i>
                ${indicator.label}
                <button type="button" aria-label="Remove ${indicator.label}" data-remove-indicator="${indicator.key}">
                    <i class="bi bi-x"></i>
                </button>
            `;
            selectedChips.appendChild(chip);
        });
    }

    function renderChart(rows, selectedIndicators, dimensionKey) {
        if (chart) chart.destroy();

        if (activeView === "table" || typeof Chart === "undefined") {
            chartWrap.classList.add("is-hidden");
            tableWrap.classList.remove("is-hidden");
            if (typeof Chart === "undefined") {
                generatedMeta.textContent = "Table";
            }
            return;
        }

        chartWrap.classList.remove("is-hidden");
        tableWrap.classList.remove("is-hidden");

        if (activeView === "pie") {
            const isSingleIndicator = selectedIndicators.length === 1;
            const labels = isSingleIndicator ? rows.map((row) => row.label) : selectedIndicators.map((indicator) => indicator.label);
            const values = isSingleIndicator
                ? rows.map((row) => row.values[0] || 0)
                : selectedIndicators.map((indicator) => Number(indicator.summary || 0));

            chart = new Chart(chartCanvas, {
                type: "pie",
                data: {
                    labels,
                    datasets: [{
                        data: values,
                        backgroundColor: labels.map((_, index) => palette[index % palette.length]),
                        borderColor: "#ffffff",
                        borderWidth: 2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: "bottom" },
                        tooltip: {
                            callbacks: {
                                label: (context) => {
                                    const indicator = isSingleIndicator ? selectedIndicators[0] : selectedIndicators[context.dataIndex];
                                    return `${context.label}: ${formatValue(context.raw, indicator)}`;
                                },
                            },
                        },
                    },
                },
            });
            return;
        }

        const labels = rows.map((row) => row.label);
        const isLine = activeView === "line" || activeView === "area";
        const chartType = isLine ? "line" : "bar";
        const datasets = selectedIndicators.map((indicator, index) => ({
            label: indicator.label,
            data: rows.map((row) => row.values[index] === null ? 0 : row.values[index]),
            borderColor: palette[index % palette.length],
            backgroundColor: activeView === "area" ? `${palette[index % palette.length]}33` : palette[index % palette.length],
            borderWidth: 2,
            tension: 0.28,
            fill: activeView === "area",
            borderRadius: chartType === "bar" ? 6 : 0,
        }));

        chart = new Chart(chartCanvas, {
            type: chartType,
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: chartType === "bar" && labels.length > 8 ? "y" : "x",
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom" },
                    tooltip: {
                        callbacks: {
                            label: (context) => {
                                const indicator = selectedIndicators[context.datasetIndex];
                                return `${indicator.label}: ${formatValue(context.raw, indicator)}`;
                            },
                        },
                    },
                },
                scales: {
                    y: { beginAtZero: true },
                    x: { ticks: { maxRotation: 0, autoSkip: true } },
                },
            },
        });
    }

    function renderResultBrief(rows, selectedIndicators, dimension) {
        if (!resultSummary || !resultFinding || !resultRecommendation) return;
        if (!selectedIndicators.length) {
            resultSummary.textContent = "No indicators selected yet.";
            resultFinding.textContent = "Pick at least one financial indicator to generate an analysis.";
            resultRecommendation.textContent = "Start with a business question such as loss drivers, high costs, or cash risk.";
            return;
        }

        resultSummary.textContent = `${selectedIndicators.length} indicators by ${dimension.label.toLowerCase()} across ${rows.length} rows.`;

        let strongest = null;
        rows.forEach((row) => {
            row.values.forEach((value, index) => {
                const numeric = Number(value || 0);
                if (!strongest || Math.abs(numeric) > Math.abs(strongest.value)) {
                    strongest = { row: row.label, indicator: selectedIndicators[index], value: numeric };
                }
            });
        });

        if (strongest) {
            resultFinding.textContent = `${strongest.indicator.label} is highest around ${strongest.row}: ${formatValue(strongest.value, strongest.indicator)}.`;
        } else {
            resultFinding.textContent = "No measurable values were found for this selection.";
        }

        const selectedKeys = new Set(selectedIndicators.map((indicator) => indicator.key));
        if (selectedKeys.has("profit") || selectedKeys.has("profit_margin") || selectedKeys.has("expense_ratio")) {
            resultRecommendation.textContent = "If profit or margin is weak, inspect the cost drivers and compare expense ratio against sales before expanding.";
        } else if (selectedKeys.has("cost_per_egg") || selectedKeys.has("feed_cost") || selectedKeys.has("labour_cost")) {
            resultRecommendation.textContent = "Focus first on the largest cost row, then set a weekly cost-per-egg target for the farm team.";
        } else if (selectedKeys.has("receivables_outstanding") || selectedKeys.has("collection_rate")) {
            resultRecommendation.textContent = "Prioritize customers with high balances and tighten credit terms where collections are slow.";
        } else if (selectedKeys.has("batch_profit") || selectedKeys.has("batch_allocated_expenses")) {
            resultRecommendation.textContent = "Compare loss-making batches against feed use, mortality, and allocated expenses before restocking.";
        } else if (selectedKeys.has("rejected_egg_loss") || selectedKeys.has("mortality_loss_estimate")) {
            resultRecommendation.textContent = "Treat loss drivers as financial leaks: investigate causes and assign corrective actions by house or batch.";
        } else {
            resultRecommendation.textContent = "Use the table to spot outliers, then drill down by batch, house, customer, or expense category.";
        }
    }

    function updateIndicatorClasses() {
        const selected = new Set(getCheckedKeys());
        indicatorList.querySelectorAll(".indicator-option").forEach((option) => {
            option.classList.toggle("is-selected", selected.has(option.dataset.indicatorKey));
        });
    }

    function updateFilters() {
        const search = (searchInput.value || "").trim().toLowerCase();
        indicatorList.querySelectorAll(".indicator-option").forEach((option) => {
            const matchesTheme = activeTheme === "all" || option.dataset.theme === activeTheme;
            const matchesSearch = !search || (option.dataset.search || "").includes(search);
            const matchesMode = activeMode === "advanced" || option.dataset.featured === "1" || option.querySelector("input").checked;
            option.classList.toggle("is-hidden", !(matchesTheme && matchesSearch && matchesMode));
        });
    }

    function render() {
        const dimensionKey = dimensionSelect.value || "period";
        const selectedIndicators = getCheckedKeys().map((key) => indicatorByKey.get(key)).filter(Boolean);
        const rows = buildRows(selectedIndicators, dimensionKey);
        const dimension = dimensionByKey.get(dimensionKey) || { label: "Breakdown" };

        currentRows = rows;
        currentIndicators = selectedIndicators;

        indicatorCount.textContent = `${selectedIndicators.length} selected`;
        breakdownLabel.textContent = dimension.label;
        rowCount.textContent = rows.length.toLocaleString();
        chartTitle.textContent = selectedIndicators.length
            ? selectedIndicators.slice(0, 2).map((indicator) => indicator.label).join(" + ") + (selectedIndicators.length > 2 ? ` + ${selectedIndicators.length - 2} more` : "")
            : "Custom farm indicators";
        const range = getBuilderRange();
        chartSubtitle.textContent = `${dimension.label} | ${range.start} to ${range.end}`;
        generatedMeta.textContent = activeView.charAt(0).toUpperCase() + activeView.slice(1);

        renderChips(selectedIndicators);
        updateIndicatorClasses();
        updateFilters();
        syncGuide();
        renderResultBrief(rows, selectedIndicators, dimension);

        const hasSelection = selectedIndicators.length > 0;
        emptyState.classList.toggle("d-none", hasSelection);
        chartWrap.classList.toggle("d-none", !hasSelection);
        tableWrap.classList.toggle("d-none", !hasSelection);

        if (!hasSelection) {
            if (chart) chart.destroy();
            return;
        }

        renderTable(rows, selectedIndicators, dimensionKey);
        renderChart(rows, selectedIndicators, dimensionKey);
    }

    function applyPreset(preset) {
        if (!preset) return;
        setGeneratedState(false);
        if (preset.theme) activeTheme = preset.theme;
        setCheckedKeys(preset.indicators || []);
        if (preset.dimension) dimensionSelect.value = preset.dimension;
        if (preset.view) setView(preset.view);
        document.querySelectorAll(".builder-preset").forEach((button) => {
            button.classList.toggle("is-active", button.dataset.presetKey === preset.key);
        });
        render();
        if (activeMode === "guided" && ["theme", "question"].includes(activeGuideStep)) {
            setGuideStep("indicators");
        }
    }

    function setView(view) {
        activeView = view;
        viewMode.querySelectorAll("button").forEach((button) => {
            button.classList.toggle("is-active", button.dataset.viewMode === activeView);
        });
    }

    function exportCsv() {
        if (!currentIndicators.length) return;
        const dimension = dimensionByKey.get(dimensionSelect.value) || { label: "Breakdown" };
        const lines = [];
        lines.push([dimension.label, ...currentIndicators.map((indicator) => indicator.label)]);
        currentRows.forEach((row) => {
            lines.push([
                row.label,
                ...row.values.map((value) => value === null || value === undefined ? "" : value),
            ]);
        });
        const csv = lines.map((line) => line.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(",")).join("\n");
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "poultryiq-investor-builder.csv";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    function exportImage() {
        if (!chart || activeView === "table") return;
        const link = document.createElement("a");
        link.href = chartCanvas.toDataURL("image/png", 1);
        link.download = "poultryiq-investor-builder.png";
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    function generateVisualization() {
        const range = getBuilderRange();
        const rangeChanged = range.start !== builderData.range.start || range.end !== builderData.range.end;

        if (rangeChanged && investorReportForm && globalStartDate && globalEndDate) {
            setGeneratedState(true);
            persistBuilderState("generate");
            globalStartDate.value = range.start;
            globalEndDate.value = range.end;
            investorReportForm.submit();
            return;
        }

        activeGuideStep = "generate";
        setGeneratedState(true);
        render();
        output.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    document.querySelectorAll("[data-builder-mode]").forEach((button) => {
        button.addEventListener("click", () => {
            activeMode = button.dataset.builderMode;
            setGeneratedState(false);
            updateFilters();
            syncGuide();
        });
    });

    guideStepButtons.forEach((button) => {
        button.addEventListener("click", () => setGuideStep(button.dataset.guideStepTarget));
    });

    document.querySelectorAll("[data-theme-key]").forEach((button) => {
        button.addEventListener("click", () => {
            activeTheme = button.dataset.themeKey;
            setGeneratedState(false);
            document.querySelectorAll(".builder-preset").forEach((presetButton) => {
                presetButton.classList.remove("is-active");
            });
            syncThemeButtons();
            syncPresetButtons();
            updateFilters();
        });
    });

    document.querySelectorAll("[data-preset-key]").forEach((button) => {
        button.addEventListener("click", () => applyPreset(presetByKey.get(button.dataset.presetKey)));
    });

    indicatorList.addEventListener("change", () => {
        setGeneratedState(false);
        render();
    });
    searchInput.addEventListener("input", updateFilters);
    dimensionSelect.addEventListener("change", () => {
        setGeneratedState(false);
        render();
    });
    generateButton.addEventListener("click", generateVisualization);
    exportCsvButton.addEventListener("click", exportCsv);
    exportImageButton.addEventListener("click", exportImage);
    stepBackButton.addEventListener("click", goToPreviousStep);
    stepNextButton.addEventListener("click", goToNextStep);
    if (builderStartDate) builderStartDate.addEventListener("change", () => {
        setGeneratedState(false);
        updateRangeLabel();
    });
    if (builderEndDate) builderEndDate.addEventListener("change", () => {
        setGeneratedState(false);
        updateRangeLabel();
    });
    fullscreenButton.addEventListener("click", () => {
        if (output.requestFullscreen) output.requestFullscreen();
    });

    viewMode.querySelectorAll("button").forEach((button) => {
        button.addEventListener("click", () => {
            setGeneratedState(false);
            setView(button.dataset.viewMode);
            render();
        });
    });

    selectedChips.addEventListener("click", (event) => {
        const button = event.target.closest("[data-remove-indicator]");
        if (!button) return;
        const input = indicatorList.querySelector(`input[value="${button.dataset.removeIndicator}"]`);
        if (input) input.checked = false;
        setGeneratedState(false);
        render();
    });

    restoreBuilderState();
    if (!getCheckedKeys().length && Array.isArray(builderData.defaultSelection)) {
        setCheckedKeys(builderData.defaultSelection);
    }
    render();
})();
