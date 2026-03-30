let chart;

function loadReport(){

let type = document.getElementById("report_type").value;
let chartType = document.getElementById("chart_type").value;

// Dummy data (replace with AJAX later)
let labels = ["Jan", "Feb", "Mar", "Apr"];
let data = [1200, 1900, 1500, 2200];

// Destroy old chart
if(chart){
    chart.destroy();
}

let ctx = document.getElementById("reportChart");

chart = new Chart(ctx, {
    type: chartType,
    data: {
        labels: labels,
        datasets: [{
            label: type,
            data: data,
        }]
    }
});

// Update table
let table = document.getElementById("reportTable");
table.innerHTML = "";

labels.forEach((label, i) => {
    table.innerHTML += `<tr>
        <td>${label}</td>
        <td>${data[i]}</td>
    </tr>`;
});

// Summary (example)
document.getElementById("total_revenue").innerText = 5000;
document.getElementById("total_expenses").innerText = 3000;
document.getElementById("profit").innerText = 2000;

}