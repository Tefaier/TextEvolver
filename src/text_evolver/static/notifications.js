const notificationLifetime = 4500;
const activeFormSubmissions = new WeakSet();

function dismissNotification(notification) {
    if (notification.classList.contains("notification-leaving")) {
        return;
    }
    notification.classList.add("notification-leaving");
    window.setTimeout(() => notification.remove(), 400);
}

function activateNotification(notification) {
    window.requestAnimationFrame(() => notification.classList.add("notification-visible"));
    window.setTimeout(() => dismissNotification(notification), notificationLifetime);
    notification.addEventListener("click", () => dismissNotification(notification), {once: true});
}

function showNotification(message, category = "error") {
    const container = document.getElementById("notification-container");
    if (container === null) {
        return;
    }
    const notification = document.createElement("div");
    const safeCategory = category === "success" ? "success" : "error";
    notification.className = `notification notification-${safeCategory}`;
    notification.setAttribute("role", safeCategory === "error" ? "alert" : "status");
    notification.textContent = message;
    container.appendChild(notification);
    activateNotification(notification);
}

window.showNotification = showNotification;

async function responsePayload(response) {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
        return {};
    }
    return response.json();
}

function responseMessage(payload) {
    if (typeof payload.message === "string") {
        return payload.message;
    }
    if (typeof payload.detail === "string") {
        return payload.detail;
    }
    return "The form could not be submitted. Please try again.";
}

async function submitFormWithoutReload(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
        return;
    }
    if (form.method.toUpperCase() !== "POST" || form.dataset.nativeSubmit !== undefined) {
        return;
    }

    event.preventDefault();
    if (activeFormSubmissions.has(form)) {
        return;
    }
    activeFormSubmissions.add(form);

    const submitter = event.submitter;
    if (submitter instanceof HTMLButtonElement || submitter instanceof HTMLInputElement) {
        submitter.disabled = true;
    }

    try {
        const body = new FormData(form);
        if (submitter?.name) {
            body.append(submitter.name, submitter.value);
        }
        const response = await fetch(form.action, {
            method: "POST",
            body,
            credentials: "same-origin",
            headers: {Accept: "application/json"},
        });
        if (response.redirected) {
            window.location.assign(response.url);
            return;
        }
        const payload = await responsePayload(response);
        if (!response.ok) {
            showNotification(responseMessage(payload), payload.category);
            return;
        }
        if (typeof payload.redirect === "string") {
            window.location.assign(payload.redirect);
            return;
        }
        if (typeof payload.message === "string") {
            showNotification(payload.message, payload.category);
        }
    } catch (_error) {
        showNotification("The form could not be submitted. Check your connection and try again.");
    } finally {
        activeFormSubmissions.delete(form);
        if (submitter instanceof HTMLButtonElement || submitter instanceof HTMLInputElement) {
            submitter.disabled = false;
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".notification").forEach(activateNotification);
    document.addEventListener("submit", submitFormWithoutReload);
});
