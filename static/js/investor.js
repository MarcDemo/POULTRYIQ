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
