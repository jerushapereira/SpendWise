document.addEventListener("DOMContentLoaded", () => {
    if (window.lucide) {
        window.lucide.createIcons();
    }

    const menuButton = document.getElementById("mobileMenu");
    const sidebar = document.querySelector(".sidebar");
    if (menuButton && sidebar) {
        menuButton.addEventListener("click", () => sidebar.classList.toggle("open"));
    }

    renderCharts();
    wireInsightButton();
});

function palette() {
    return ["#85431E", "#D39858", "#EACEAA", "#34150F", "#150C0C", "#a86615", "#b87942", "#6f2f18"];
}

function setEmpty(canvasId, isEmpty) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    canvas.closest(".chart-panel").classList.toggle("empty", isEmpty);
}

function renderCharts() {
    const data = window.analyticsData;
    if (!data || !window.Chart) return;

    setEmpty("categoryChart", data.category_values.length === 0);
    setEmpty("monthlyChart", data.monthly_labels.length === 0);
    setEmpty("trendChart", data.trend_values.length === 0);

    const categoryCanvas = document.getElementById("categoryChart");
    if (categoryCanvas && data.category_values.length) {
        new Chart(categoryCanvas, {
            type: "doughnut",
            data: {
                labels: data.category_labels,
                datasets: [{ data: data.category_values, backgroundColor: palette(), borderWidth: 0 }]
            },
            options: {
                plugins: { legend: { position: "bottom" } },
                cutout: "64%",
                responsive: true,
                maintainAspectRatio: false
            }
        });
    }

    const monthlyCanvas = document.getElementById("monthlyChart");
    if (monthlyCanvas && data.monthly_labels.length) {
        new Chart(monthlyCanvas, {
            type: "bar",
            data: {
                labels: data.monthly_labels,
                datasets: [
                    { label: "Income", data: data.monthly_income, backgroundColor: "#D39858", borderRadius: 6 },
                    { label: "Expenses", data: data.monthly_expenses, backgroundColor: "#85431E", borderRadius: 6 }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: { y: { beginAtZero: true } },
                plugins: { legend: { position: "bottom" } }
            }
        });
    }

    const trendCanvas = document.getElementById("trendChart");
    if (trendCanvas && data.trend_values.length) {
        new Chart(trendCanvas, {
            type: "line",
            data: {
                labels: data.trend_labels,
                datasets: [{
                    label: "Daily spending",
                    data: data.trend_values,
                    borderColor: "#85431E",
                    backgroundColor: "rgba(211, 152, 88, 0.18)",
                    tension: 0.35,
                    fill: true,
                    pointRadius: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: { y: { beginAtZero: true } },
                plugins: { legend: { display: false } }
            }
        });
    }
}

function wireInsightButton() {
    const button = document.getElementById("insightButton");
    const state = document.getElementById("insightState");
    const list = document.getElementById("insightList");
    if (!button || !state || !list) return;

    button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "Generating...";
        state.textContent = "Reviewing your spending patterns...";
        list.innerHTML = "";

        try {
            const response = await fetch("/api/ai-insights", { method: "POST" });
            const payload = await response.json();
            state.textContent = payload.source === "groq"
                ? "Powered by Groq AI"
                : "Local insights shown because Groq is not configured.";
            list.innerHTML = (payload.insights || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
        } catch (error) {
            state.textContent = "Could not generate insights right now.";
        } finally {
            button.disabled = false;
            button.textContent = "Generate";
        }
    });
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}
