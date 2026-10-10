const accessMessage = document.getElementById("access-message");
const membersContainer = document.getElementById("company-members");
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

async function accessRequest(path, method = "GET", body) {
    const response = await fetch(path, {
        method, credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Unable to save company access");
    return data;
}

function element(tag, text, attributes = {}) {
    const node = document.createElement(tag);
    if (text !== null) node.textContent = text;
    Object.assign(node, attributes);
    return node;
}

async function loadMembers() {
    const [data, vendors] = await Promise.all([
        accessRequest("/api/admin/access/members"), accessRequest("/api/vendors"),
    ]);
    document.getElementById("default-access").value = data.default_access;
    membersContainer.replaceChildren();
    for (const member of data.members) {
        const form = element("form", null, {className: "content-card access-member"});
        form.append(element("h2", member.name || member.email || member.subject), element("p", "User ID: " + member.subject));
        const mode = element("select", null, {"ariaLabel": "Vendor access"});
        mode.append(element("option", "Assigned vendors only", {value: "assigned"}), element("option", "All company vendors", {value: "all"}));
        mode.value = member.access_mode;
        const enabled = element("input", null, {type: "checkbox", checked: member.enabled});
        const enabledLabel = element("label", " Company access enabled ");
        enabledLabel.prepend(enabled);
        const assignments = element("fieldset", null);
        assignments.append(element("legend", "Assigned vendors"));
        const boxes = vendors.map((vendor) => {
            const checkbox = element("input", null, {type: "checkbox", value: vendor.id, checked: member.vendor_ids.includes(vendor.id)});
            const label = element("label", " " + vendor.name + " ");
            label.prepend(checkbox);
            assignments.append(label);
            return checkbox;
        });
        assignments.hidden = mode.value === "all";
        mode.addEventListener("change", () => { assignments.hidden = mode.value === "all"; });
        const save = element("button", "Save access", {type: "submit", className: "primary-button"});
        form.append(enabledLabel, mode, assignments, save);
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            save.disabled = true;
            try {
                await accessRequest("/api/admin/access/members/" + encodeURIComponent(member.id), "PUT", {
                    enabled: enabled.checked, access_mode: mode.value,
                    vendor_ids: mode.value === "all" ? [] : boxes.filter((box) => box.checked).map((box) => box.value),
                });
                accessMessage.textContent = "Access saved.";
            } catch (error) { accessMessage.textContent = error.message; }
            finally { save.disabled = false; }
        });
        membersContainer.append(form);
    }
}

document.getElementById("company-default-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
        await accessRequest("/api/admin/access/defaults", "PUT", {default_access: document.getElementById("default-access").value});
        accessMessage.textContent = "Default saved for new users.";
    } catch (error) { accessMessage.textContent = error.message; }
});

document.getElementById("add-member-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
        await accessRequest("/api/admin/access/members", "POST", {subject: document.getElementById("member-subject").value.trim(), access_mode: "assigned", vendor_ids: [], enabled: true});
        event.target.reset();
        await loadMembers();
        accessMessage.textContent = "User added. Choose their vendors below.";
    } catch (error) { accessMessage.textContent = error.message; }
});

loadMembers().catch((error) => { accessMessage.textContent = error.message; });
