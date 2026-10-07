// ============================================================
// Vendor Risk Analyzer - Frontend
// ============================================================


// ------------------------------------------------------------
// DOM ELEMENTS
// ------------------------------------------------------------

// Vendor elements
const vendorForm = document.getElementById("vendor-form");
const vendorList = document.getElementById("vendor-list");
const vendorMessage = document.getElementById("vendor-message");

const vendorNameInput =
    document.getElementById("vendor-name");

const vendorWebsiteInput =
    document.getElementById("vendor-website");


// Document elements
const documentForm =
    document.getElementById("document-upload-form");

const documentVendor =
    document.getElementById("document-vendor");

const documentFile =
    document.getElementById("document-file");

const documentMessage =
    document.getElementById("document-upload-message");

const documentList =
    document.getElementById("document-list");


// ------------------------------------------------------------
// SECURITY / DISPLAY HELPERS
// ------------------------------------------------------------

function escapeHtml(value) {
    const div = window.document.createElement("div");

    div.textContent = value ?? "";

    return div.innerHTML;
}


// ------------------------------------------------------------
// VENDORS
// ------------------------------------------------------------

async function loadVendors() {
    // The user may not be logged in,
    // so these elements may not exist.
    if (!vendorList) {
        return;
    }

    try {
        const response = await fetch(
            "/api/vendors",
            {
                credentials: "same-origin",
            }
        );

        if (!response.ok) {
            throw new Error(
                "Unable to load vendors"
            );
        }

        const vendors = await response.json();


        // ----------------------------------------------------
        // Render vendor list
        // ----------------------------------------------------

        vendorList.innerHTML = "";

        if (vendors.length === 0) {
            vendorList.innerHTML =
                "<p>No vendors have been added yet.</p>";
        } else {
            for (const vendor of vendors) {
                const item =
                    window.document.createElement("div");

                item.className = "vendor-item";

                item.innerHTML = `
                    <div>
                        <strong>
                            ${escapeHtml(vendor.name)}
                        </strong>

                        <div class="vendor-website">
                            ${
                                vendor.website
                                    ? escapeHtml(vendor.website)
                                    : "No website"
                            }
                        </div>
                    </div>

                    <span class="vendor-status">
                        ${escapeHtml(vendor.status)}
                    </span>
                `;

                vendorList.appendChild(item);
            }
        }


        // ----------------------------------------------------
        // Populate document vendor dropdown
        // ----------------------------------------------------

        if (documentVendor) {
            const currentSelection =
                documentVendor.value;

            documentVendor.innerHTML =
                '<option value="">Select vendor</option>';

            for (const vendor of vendors) {
                const option =
                    window.document.createElement(
                        "option"
                    );

                option.value = vendor.id;
                option.textContent = vendor.name;

                documentVendor.appendChild(option);
            }

            // Preserve selection if vendor still exists
            if (
                currentSelection &&
                vendors.some(
                    (vendor) =>
                        vendor.id === currentSelection
                )
            ) {
                documentVendor.value =
                    currentSelection;
            }
        }

    } catch (error) {
        console.error(
            "Vendor loading failed:",
            error
        );

        vendorList.innerHTML =
            "<p>Unable to load vendors.</p>";
    }
}


// ------------------------------------------------------------
// CREATE VENDOR
// ------------------------------------------------------------

if (vendorForm) {
    vendorForm.addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();

            vendorMessage.textContent = "";

            const name =
                vendorNameInput.value.trim();

            const website =
                vendorWebsiteInput.value.trim();

            const csrfToken =
                vendorForm.dataset.csrfToken;

            if (!name) {
                vendorMessage.textContent =
                    "Vendor name is required.";

                return;
            }

            try {
                const response = await fetch(
                    "/api/vendors",
                    {
                        method: "POST",

                        credentials: "same-origin",

                        headers: {
                            "Content-Type":
                                "application/json",

                            "X-CSRF-Token":
                                csrfToken,
                        },

                        body: JSON.stringify({
                            name: name,

                            website:
                                website || null,
                        }),
                    }
                );

                const body =
                    await response.json();

                if (!response.ok) {
                    throw new Error(
                        body.detail ||
                        "Unable to create vendor"
                    );
                }

                vendorMessage.textContent =
                    "Vendor created successfully.";

                vendorForm.reset();

                await loadVendors();

            } catch (error) {
                console.error(
                    "Vendor creation failed:",
                    error
                );

                vendorMessage.textContent =
                    error.message;
            }
        }
    );
}


// ------------------------------------------------------------
// FILE HASHING
// ------------------------------------------------------------

async function sha256File(file) {
    const buffer =
        await file.arrayBuffer();

    const digest =
        await window.crypto.subtle.digest(
            "SHA-256",
            buffer
        );

    const bytes =
        new Uint8Array(digest);

    return Array.from(bytes)
        .map(
            (byte) =>
                byte
                    .toString(16)
                    .padStart(2, "0")
        )
        .join("");
}


// ------------------------------------------------------------
// CONTENT TYPE DETECTION
// ------------------------------------------------------------

function getContentType(file) {
    // Browser already knows the MIME type
    // in many cases.
    if (file.type) {
        return file.type;
    }

    const extension =
        file.name
            .split(".")
            .pop()
            .toLowerCase();

    const types = {
        pdf: "application/pdf",

        docx:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",

        xlsx:
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",

        csv: "text/csv",

        txt: "text/plain",

        md: "text/markdown",
    };

    return (
        types[extension] ||
        "application/octet-stream"
    );
}


// ------------------------------------------------------------
// DOCUMENT UPLOAD
// ------------------------------------------------------------

if (documentForm) {
    documentForm.addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();

            documentMessage.textContent =
                "";

            const vendorId =
                documentVendor.value;

            const file =
                documentFile.files[0];

            const csrfToken =
                documentForm.dataset.csrfToken;


            // ------------------------------------------------
            // Basic browser validation
            // ------------------------------------------------

            if (!vendorId) {
                documentMessage.textContent =
                    "Please select a vendor.";

                return;
            }

            if (!file) {
                documentMessage.textContent =
                    "Please select a document.";

                return;
            }


            // Same 25 MB limit as FastAPI
            const maxFileSize =
                25 * 1024 * 1024;

            if (file.size > maxFileSize) {
                documentMessage.textContent =
                    "File must be 25 MB or smaller.";

                return;
            }


            try {
                // --------------------------------------------
                // STEP 1
                // Calculate SHA-256 in the browser
                // --------------------------------------------

                documentMessage.textContent =
                    "Preparing document...";

                const sha256 =
                    await sha256File(file);

                const contentType =
                    getContentType(file);


                // --------------------------------------------
                // STEP 2
                // Ask FastAPI for a presigned upload URL
                // --------------------------------------------

                documentMessage.textContent =
                    "Requesting secure upload...";

                const prepareResponse =
                    await fetch(
                        `/api/vendors/${vendorId}/documents/upload-url`,
                        {
                            method: "POST",

                            credentials:
                                "same-origin",

                            headers: {
                                "Content-Type":
                                    "application/json",

                                "X-CSRF-Token":
                                    csrfToken,
                            },

                            body: JSON.stringify({
                                filename:
                                    file.name,

                                content_type:
                                    contentType,

                                size_bytes:
                                    file.size,

                                sha256:
                                    sha256,
                            }),
                        }
                    );


                const prepareBody =
                    await prepareResponse.json();


                if (!prepareResponse.ok) {
                    throw new Error(
                        prepareBody.detail ||
                        "Unable to prepare upload"
                    );
                }


                // --------------------------------------------
                // STEP 3
                // Upload directly to Neon Object Storage
                //
                // The file does NOT pass through FastAPI.
                // --------------------------------------------

                documentMessage.textContent =
                    "Uploading document...";

                const uploadResponse =
                    await fetch(
                        prepareBody.upload_url,
                        {
                            method: "PUT",

                            headers: {
                                "Content-Type":
                                    contentType,
                            },

                            body: file,
                        }
                    );


                if (!uploadResponse.ok) {
                    throw new Error(
                        "Object storage upload failed"
                    );
                }


                // --------------------------------------------
                // STEP 4
                // Tell FastAPI that upload finished
                // --------------------------------------------

                documentMessage.textContent =
                    "Verifying upload...";

                const completeResponse =
                    await fetch(
                        `/api/vendors/${vendorId}/documents/${prepareBody.document_id}/complete`,
                        {
                            method: "POST",

                            credentials:
                                "same-origin",

                            headers: {
                                "X-CSRF-Token":
                                    csrfToken,
                            },
                        }
                    );


                const completeBody =
                    await completeResponse.json();


                if (!completeResponse.ok) {
                    throw new Error(
                        completeBody.detail ||
                        "Unable to complete upload"
                    );
                }


                // --------------------------------------------
                // SUCCESS
                // --------------------------------------------

                documentMessage.textContent =
                    "Document uploaded successfully.";

                documentFile.value = "";

                await loadDocuments(
                    vendorId
                );

            } catch (error) {
                console.error(
                    "Document upload failed:",
                    error
                );

                documentMessage.textContent =
                    error.message;
            }
        }
    );
}


// ------------------------------------------------------------
// LOAD DOCUMENTS
// ------------------------------------------------------------

async function loadDocuments(vendorId) {
    if (!documentList) {
        return;
    }

    if (!vendorId) {
        documentList.textContent =
            "Select a vendor to view documents.";

        return;
    }

    documentList.textContent =
        "Loading documents...";

    try {
        const response = await fetch(
            `/api/vendors/${vendorId}/documents`,
            {
                credentials: "same-origin",
            }
        );

        if (!response.ok) {
            throw new Error(
                "Unable to load documents"
            );
        }

        const documents =
            await response.json();

        documentList.innerHTML = "";


        if (documents.length === 0) {
            documentList.textContent =
                "No documents uploaded.";

            return;
        }


        for (const doc of documents) {
            const item =
                window.document.createElement("div");

            item.className = "document-item";

            const fileSize =
                formatFileSize(doc.size_bytes);

            let actionButton = "";

            const canParse =
                doc.status === "uploaded" ||
                doc.status === "failed" ||
                doc.status === "parsed" ||
                doc.status === "embedding_pending" ||
                doc.status === "embedding_failed" ||
                doc.status === "ready";

            if (
                documentForm &&
                canParse
            ) {
                const isReparse =
                    doc.status !== "uploaded" &&
                    doc.status !== "failed";

                const buttonText =
                    isReparse
                        ? "Re-parse"
                        : "Parse";

                actionButton = `
                    <button
                        type="button"
                        class="ingest-button"
                        data-document-id="${escapeHtml(doc.id)}"
                        data-vendor-id="${escapeHtml(doc.vendor_id)}"
                    >
                        ${buttonText}
                    </button>
                `;
            }

            item.innerHTML = `
                <div>
                    <strong>
                        ${escapeHtml(doc.filename)}
                    </strong>

                    <div class="document-details">
                        ${escapeHtml(
                            doc.file_type.toUpperCase()
                        )}

                        ${
                            fileSize
                                ? ` • ${escapeHtml(fileSize)}`
                                : ""
                        }
                    </div>
                </div>

                <div class="document-actions">
                    <span class="document-status">
                        ${escapeHtml(doc.status)}
                    </span>

                    ${actionButton}
                </div>
            `;

            documentList.appendChild(item);
        }

    } catch (error) {
        console.error(
            "Document loading failed:",
            error
        );

        documentList.textContent =
            "Unable to load documents.";
    }
}


// ------------------------------------------------------------
// Ingest Document
// ------------------------------------------------------------

async function ingestDocument(
    vendorId,
    documentId,
    button
) {
    if (!documentForm) {
        return;
    }

    const csrfToken =
        documentForm.dataset.csrfToken;

    const originalText =
        button.textContent;

    button.disabled = true;
    button.textContent = "Parsing...";

    documentMessage.textContent =
        "Parsing document...";

    try {
        const response = await fetch(
            `/api/vendors/${vendorId}/documents/${documentId}/ingest`,
            {
                method: "POST",

                credentials: "same-origin",

                headers: {
                    "X-CSRF-Token":
                        csrfToken,
                },
            }
        );

        const body =
            await response.json();

        if (!response.ok) {
            throw new Error(
                body.detail ||
                "Document ingestion failed"
            );
        }

        documentMessage.textContent =
            "Document parsed successfully. Embedding pending.";

        await loadDocuments(
            vendorId
        );

    } catch (error) {
        console.error(
            "Document ingestion failed:",
            error
        );

        documentMessage.textContent =
            error.message;

        button.disabled = false;
        button.textContent =
            originalText;
    }
}


if (documentList) {
    documentList.addEventListener(
        "click",
        async (event) => {
            const button =
                event.target.closest(
                    ".ingest-button"
                );

            if (!button) {
                return;
            }

            const vendorId =
                button.dataset.vendorId;

            const documentId =
                button.dataset.documentId;

            await ingestDocument(
                vendorId,
                documentId,
                button
            );
        }
    );
}


// ------------------------------------------------------------
// DOCUMENT VENDOR SELECT
// ------------------------------------------------------------

if (documentVendor) {
    documentVendor.addEventListener(
        "change",
        async () => {
            const vendorId =
                documentVendor.value;

            if (!vendorId) {
                documentList.textContent =
                    "Select a vendor to view documents.";

                return;
            }

            await loadDocuments(
                vendorId
            );
        }
    );
}


// ------------------------------------------------------------
// FILE SIZE DISPLAY
// ------------------------------------------------------------

function formatFileSize(bytes) {
    if (
        bytes === null ||
        bytes === undefined
    ) {
        return "";
    }

    if (bytes < 1024) {
        return `${bytes} B`;
    }

    if (bytes < 1024 * 1024) {
        return `${
            (bytes / 1024).toFixed(1)
        } KB`;
    }

    return `${
        (
            bytes /
            (1024 * 1024)
        ).toFixed(1)
    } MB`;
}


// ------------------------------------------------------------
// INITIAL PAGE LOAD
// ------------------------------------------------------------

async function initializePage() {
    await loadVendors();
}


// Start application
initializePage();