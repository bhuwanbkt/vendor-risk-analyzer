const vendorForm = document.getElementById("vendor-form");
const vendorList = document.getElementById("vendor-list");
const vendorMessage = document.getElementById("vendor-message");

async function loadVendors() {
    if (!vendorList) {
        return;
    }

    try {
        const response = await fetch("/api/vendors");

        if (!response.ok) {
            throw new Error("Unable to load vendors");
        }

        const vendors = await response.json();

        vendorList.innerHTML = "";

        if (vendors.length === 0) {
            vendorList.innerHTML =
                "<p>No vendors have been added yet.</p>";
            return;
        }

        for (const vendor of vendors) {
            const item = document.createElement("div");
            item.className = "vendor-item";

            item.innerHTML = `
                <div>
                    <strong>${escapeHtml(vendor.name)}</strong>
                    <div class="vendor-website">
                        ${escapeHtml(vendor.website || "No website")}
                    </div>
                </div>

                <span class="vendor-status">
                    ${escapeHtml(vendor.status)}
                </span>
            `;

            vendorList.appendChild(item);
        }
    } catch (error) {
        vendorList.innerHTML =
            "<p>Unable to load vendors.</p>";
    }
}


if (vendorForm) {
    vendorForm.addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();

            vendorMessage.textContent = "";

            const name =
                document.getElementById("vendor-name").value;

            const website =
                document.getElementById("vendor-website").value;

            const csrfToken =
                vendorForm.dataset.csrfToken;

            try {
                const response = await fetch(
                    "/api/vendors",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-CSRF-Token": csrfToken,
                        },
                        body: JSON.stringify({
                            name: name,
                            website: website || null,
                        }),
                    }
                );

                const body = await response.json();

                if (!response.ok) {
                    throw new Error(
                        body.detail || "Unable to create vendor"
                    );
                }

                vendorMessage.textContent =
                    "Vendor created successfully.";

                vendorForm.reset();

                await loadVendors();

            } catch (error) {
                vendorMessage.textContent =
                    error.message;
            }
        }
    );
}


function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value;
    return div.innerHTML;
}


loadVendors();