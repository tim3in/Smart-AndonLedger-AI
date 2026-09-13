const ui = new WebUI();

const els = {
    connectionBadge: document.getElementById("connectionBadge"),
    longStopBanner: document.getElementById("longStopBanner"),
    machineState: document.getElementById("machineState"),
    stateExplanation: document.getElementById("stateExplanation"),
    activeLights: document.getElementById("activeLights"),
    confidence: document.getElementById("confidence"),
    stateDuration: document.getElementById("stateDuration"),
    runtime: document.getElementById("runtime"),
    downtime: document.getElementById("downtime"),
    warningTime: document.getElementById("warningTime"),
    unknownTime: document.getElementById("unknownTime"),
    availability: document.getElementById("availability"),
    faultCount: document.getElementById("faultCount"),
    warningCount: document.getElementById("warningCount"),
    visionStatus: document.getElementById("visionStatus"),
    shiftStart: document.getElementById("shiftStart"),
    thresholdSlider: document.getElementById("thresholdSlider"),
    thresholdValue: document.getElementById("thresholdValue"),
    resetShiftButton: document.getElementById("resetShiftButton"),
    eventRows: document.getElementById("eventRows"),
    cameraStream: document.getElementById("cameraStream"),
    cameraPlaceholder: document.getElementById("cameraPlaceholder"),
};

let lastTelemetryAt = 0;


// ---------------------------------------------------------------------
// Live annotated camera stream
// ---------------------------------------------------------------------

function startCameraStream() {
    const host = window.location.hostname;
    const url = `http://${host}:4912/embed`;

    els.cameraStream.src = url;

    els.cameraStream.addEventListener("load", () => {
        els.cameraPlaceholder.classList.add("hidden");
        els.cameraStream.classList.add("visible");
    });
}


// ---------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------

function formatDuration(rawSeconds) {
    let seconds = Math.max(0, Math.floor(Number(rawSeconds) || 0));

    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;

    const minutes = Math.floor(seconds / 60);
    seconds %= 60;

    return [
        String(hours).padStart(2, "0"),
        String(minutes).padStart(2, "0"),
        String(seconds).padStart(2, "0")
    ].join(":");
}


function formatTimestamp(value) {
    if (!value) {
        return "-";
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
        return value;
    }

    return date.toLocaleString();
}


function stateExplanation(state) {
    switch (state) {
        case "RUNNING":
            return "Green is the highest-priority active condition currently detected.";
        case "WARNING":
            return "Yellow is active and no red fault light is currently confirmed.";
        case "FAULT":
            return "Red is active. Fault takes priority over yellow or green if they overlap.";
        default:
            return "No trustworthy stack-light condition is currently confirmed.";
    }
}


// ---------------------------------------------------------------------
// Dashboard rendering
// ---------------------------------------------------------------------

function renderTelemetry(data) {
    lastTelemetryAt = Date.now();

    els.connectionBadge.textContent = "Sensor online";
    els.connectionBadge.className = "connection-badge online";

    const state = data.machine_state || "UNKNOWN";
    const active = Array.isArray(data.active_lights) ? data.active_lights : [];

    els.machineState.textContent = state;
    els.machineState.className = `state-pill ${state.toLowerCase()}`;
    els.stateExplanation.textContent = stateExplanation(state);

    els.activeLights.textContent = active.length
        ? active.map(x => x.toUpperCase()).join(" + ")
        : "None";

    els.confidence.textContent =
        `${Math.round((Number(data.confidence) || 0) * 100)}%`;

    els.stateDuration.textContent = formatDuration(data.state_duration_s);
    els.runtime.textContent = formatDuration(data.runtime_s);
    els.downtime.textContent = formatDuration(data.downtime_s);
    els.warningTime.textContent = formatDuration(data.warning_s);
    els.unknownTime.textContent = formatDuration(data.unknown_s);

    els.availability.textContent =
        `${Number(data.availability_pct || 0).toFixed(1)}%`;

    els.faultCount.textContent = data.fault_count ?? 0;
    els.warningCount.textContent = data.warning_count ?? 0;

    els.visionStatus.textContent =
        active.length ? "Detection valid" : "No active class";

    els.shiftStart.textContent = formatTimestamp(data.shift_started_utc);

    if (Number.isFinite(Number(data.threshold))) {
        const threshold = Number(data.threshold);
        els.thresholdSlider.value = threshold;
        els.thresholdValue.textContent = threshold.toFixed(2);
    }

    if (data.long_stop) {
        els.longStopBanner.classList.remove("hidden");
        els.longStopBanner.textContent =
            `PROLONGED STOP — FAULT active for ${formatDuration(data.state_duration_s)}`;
    } else {
        els.longStopBanner.classList.add("hidden");
    }

    renderEvents(data.recent_events || []);
}


function renderEvents(events) {
    els.eventRows.innerHTML = "";

    if (!events.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");

        cell.colSpan = 5;
        cell.textContent = "Waiting for state transitions...";

        row.appendChild(cell);
        els.eventRows.appendChild(row);
        return;
    }

    for (const event of events) {
        const row = document.createElement("tr");

        addCell(row, formatTimestamp(event.timestamp));
        addCell(row, event.old_state || "-");
        addCell(row, event.new_state || "-");

        const lights = Array.isArray(event.active_lights)
            ? event.active_lights.map(x => x.toUpperCase()).join(" + ")
            : "-";

        addCell(row, lights || "None");

        const confidence = Number(event.confidence);

        addCell(
            row,
            Number.isFinite(confidence)
                ? `${Math.round(confidence * 100)}%`
                : "-"
        );

        els.eventRows.appendChild(row);
    }
}


function addCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = value;
    row.appendChild(cell);
}


// ---------------------------------------------------------------------
// UI -> Python controls
// ---------------------------------------------------------------------

let thresholdTimer = null;

els.thresholdSlider.addEventListener("input", () => {
    const value = Number(els.thresholdSlider.value);
    els.thresholdValue.textContent = value.toFixed(2);

    clearTimeout(thresholdTimer);

    thresholdTimer = setTimeout(() => {
        ui.send_message("override_th", value);
    }, 150);
});


els.resetShiftButton.addEventListener("click", () => {
    const confirmed = window.confirm(
        "Reset runtime, downtime, warning time and event counters for the current shift?"
    );

    if (confirmed) {
        ui.send_message("reset_shift", {});
    }
});


// ---------------------------------------------------------------------
// Python -> UI telemetry
// ---------------------------------------------------------------------

ui.on_message("andon_update", data => {
    renderTelemetry(data);
});


// Mark the sensor offline if telemetry stops.
setInterval(() => {
    if (lastTelemetryAt === 0) {
        return;
    }

    if ((Date.now() - lastTelemetryAt) > 4000) {
        els.connectionBadge.textContent = "Telemetry offline";
        els.connectionBadge.className = "connection-badge offline";
        els.visionStatus.textContent = "No telemetry";
    }
}, 1000);


startCameraStream();
