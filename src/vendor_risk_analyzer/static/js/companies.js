document.querySelectorAll(".company-select").forEach((button) => {
    button.addEventListener("click", async () => {
        const message = document.getElementById("company-message");
        button.disabled = true;
        try {
            const response = await fetch("/auth/company", {
                method: "POST", credentials: "same-origin",
                headers: {"Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content},
                body: JSON.stringify({organization_id: button.dataset.orgId}),
            });
            const body = await response.json();
            if (!response.ok) throw new Error(body.detail || "Unable to open this company");
            window.location.assign("/dashboard");
        } catch (error) {
            message.textContent = error.message;
            button.disabled = false;
        }
    });
});
