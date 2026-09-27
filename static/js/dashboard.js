// Egg Production Chart
const eggCtx = document.getElementById('eggChart');

if (window.Chart && eggCtx) new Chart(eggCtx, {
    type: 'line',
    data: {
        labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
        datasets: [{
            label: 'Eggs Collected',
            data: [1200, 1500, 1300, 1700, 1600, 1800, 1750],
            borderColor: '#1f3c88',
            backgroundColor: 'rgba(31, 60, 136, 0.1)',
            fill: true,
            tension: 0.4
        }]
    }
});

// Feed Consumption Chart
const feedCtx = document.getElementById('feedChart');

if (window.Chart && feedCtx) new Chart(feedCtx, {
    type: 'bar',
    data: {
        labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
        datasets: [{
            label: 'Feed Used (kg)',
            data: [300, 280, 320, 350, 330, 360, 340],
            backgroundColor: '#f39c12'
        }]
    }
});

// avewight chart
const weightCtx = document.getElementById('weightChart');


if (window.Chart && weightCtx) new Chart(weightCtx, {
    type: 'line',
    data: {
        labels: ['week 1', 'week 2', 'week 3', 'week 4', 'week 5', 'week 6', 'week 7'],
        datasets: [{
            label: 'Average Egg Weight (grams)',
            data: [58, 59, 57, 60, 59, 61, 60],
            borderColor: '#34881fff',
            backgroundColor: '#c2e497ff',
            fill: true,
            tension: 0.4
        }]
    }
});
