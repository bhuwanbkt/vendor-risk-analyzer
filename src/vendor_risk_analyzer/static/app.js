// ============================================================
// Vendor Risk Analyzer - Frontend
// Separate vendor workspaces: upload, document library, assessment
// ============================================================


// ------------------------------------------------------------
// DOM ELEMENTS
// ------------------------------------------------------------

// Vendor management
const vendorForm =
    document.getElementById(
        "vendor-form"
    );

const vendorList =
    document.getElementById(
        "vendor-list"
    );

const vendorMessage =
    document.getElementById(
        "vendor-message"
    );

const vendorNameInput =
    document.getElementById(
        "vendor-name"
    );

const vendorWebsiteInput =
    document.getElementById(
        "vendor-website"
    );

const vendorCount =
    document.getElementById(
        "vendor-count"
    );


// Upload workspace
const documentForm =
    document.getElementById(
        "document-upload-form"
    );

const uploadVendor =
    document.getElementById(
        "upload-vendor"
    );

const documentFile =
    document.getElementById(
        "document-file"
    );

const documentMessage =
    document.getElementById(
        "document-upload-message"
    );


// Document library workspace
const documentLibraryVendor =
    document.getElementById(
        "document-library-vendor"
    );

const documentList =
    document.getElementById(
        "document-list"
    );

const documentCount =
    document.getElementById(
        "document-count"
    );


// Assessment workspace
const assessmentVendor =
    document.getElementById(
        "assessment-vendor"
    );

const assessmentReadyStatus =
    document.getElementById(
        "assessment-ready-status"
    );

const runAssessmentButton =
    document.getElementById(
        "run-assessment-button"
    );

const assessmentMessage =
    document.getElementById(
        "assessment-message"
    );

const assessmentResult =
    document.getElementById(
        "assessment-result"
    );


// ------------------------------------------------------------
// GENERIC HELPERS
// ------------------------------------------------------------

function escapeHtml(value) {
    const div =
        window.document.createElement(
            "div"
        );

    div.textContent =
        value ?? "";

    return div.innerHTML;
}


function setMessage(
    element,
    message
) {
    if (!element) {
        return;
    }

    element.textContent =
        message || "";
}


function formatFileSize(
    bytes
) {
    if (
        bytes === null ||
        bytes === undefined
    ) {
        return "";
    }

    if (
        bytes < 1024
    ) {
        return `${bytes} B`;
    }

    if (
        bytes
        < 1024 * 1024
    ) {
        return `${
            (
                bytes /
                1024
            ).toFixed(1)
        } KB`;
    }

    return `${
        (
            bytes /
            (
                1024
                * 1024
            )
        ).toFixed(1)
    } MB`;
}


function formatLabel(
    value
) {
    if (!value) {
        return "Other";
    }

    return String(value)
        .replaceAll(
            "_",
            " "
        )
        .replace(
            /\b\w/g,
            (letter) =>
                letter.toUpperCase()
        );
}


function severityClass(
    value
) {
    const severity =
        String(
            value ||
            "unrated"
        ).toLowerCase();

    const allowed = [
        "low",
        "medium",
        "high",
        "critical",
        "unrated",
    ];

    return allowed.includes(
        severity
    )
        ? severity
        : "unrated";
}


function populateVendorSelect(
    selectElement,
    vendors
) {
    if (!selectElement) {
        return;
    }

    const currentSelection =
        selectElement.value;

    selectElement.innerHTML =
        '<option value="">Select vendor</option>';

    for (
        const vendor
        of vendors
    ) {
        const option =
            window.document
                .createElement(
                    "option"
                );

        option.value =
            vendor.id;

        option.textContent =
            vendor.name;

        selectElement
            .appendChild(
                option
            );
    }

    if (
        currentSelection &&
        vendors.some(
            (vendor) =>
                vendor.id
                === currentSelection
        )
    ) {
        selectElement.value =
            currentSelection;
    }
}


function clearAssessmentOutput() {
    setMessage(
        assessmentMessage,
        ""
    );

    if (
        assessmentResult
    ) {
        assessmentResult
            .innerHTML =
            "";
    }
}


async function readJsonSafely(
    response
) {
    try {
        return await response
            .json();

    } catch {
        return {};
    }
}


function responseError(
    body,
    response,
    fallback
) {
    const detail =
        body?.detail;

    if (
        typeof detail
            === "string"
        &&
        detail.trim()
    ) {
        return `${
            detail
        } (HTTP ${
            response.status
        })`;
    }

    return `${
        fallback
    } (HTTP ${
        response.status
    })`;
}


// ------------------------------------------------------------
// VENDORS
// ------------------------------------------------------------

async function loadVendors() {
    if (!vendorList) {
        return [];
    }

    try {
        const response =
            await fetch(
                "/api/vendors",
                {
                    credentials:
                        "same-origin",
                }
            );

        const body =
            await readJsonSafely(
                response
            );

        if (
            !response.ok
        ) {
            throw new Error(
                responseError(
                    body,
                    response,
                    "Unable to load vendors"
                )
            );
        }

        const vendors =
            Array.isArray(
                body
            )
                ? body
                : [];


        // ----------------------------------------------------
        // Dashboard vendor count
        // ----------------------------------------------------

        if (
            vendorCount
        ) {
            vendorCount
                .textContent =
                String(
                    vendors.length
                );
        }


        // ----------------------------------------------------
        // Render vendor list
        // ----------------------------------------------------

        vendorList.innerHTML =
            "";

        if (
            vendors.length === 0
        ) {
            vendorList.innerHTML =
                `
                <div class="empty-state">
                    No vendors have been added yet.
                </div>
                `;

        } else {
            for (
                const vendor
                of vendors
            ) {
                const item =
                    window.document
                        .createElement(
                            "div"
                        );

                item.className =
                    "vendor-item";

                item.innerHTML = `
                    <div>
                        <strong>
                            ${escapeHtml(
                                vendor.name
                            )}
                        </strong>

                        <div class="vendor-website">
                            ${
                                vendor.website
                                    ? escapeHtml(
                                        vendor.website
                                    )
                                    : "No website"
                            }
                        </div>
                    </div>

                    <span class="vendor-status">
                        ${escapeHtml(
                            vendor.status
                        )}
                    </span>
                `;

                vendorList
                    .appendChild(
                        item
                    );
            }
        }


        // ----------------------------------------------------
        // IMPORTANT
        //
        // Each workflow gets a completely separate
        // vendor selector.
        //
        // Changing one does NOT change the other two.
        // ----------------------------------------------------

        populateVendorSelect(
            uploadVendor,
            vendors
        );

        populateVendorSelect(
            documentLibraryVendor,
            vendors
        );

        populateVendorSelect(
            assessmentVendor,
            vendors
        );

        return vendors;

    } catch (error) {
        console.error(
            "Vendor loading failed:",
            error
        );

        vendorList.innerHTML =
            `
            <div class="empty-state">
                Unable to load vendors.
            </div>
            `;

        return [];
    }
}


// ------------------------------------------------------------
// CREATE VENDOR
// ------------------------------------------------------------

if (
    vendorForm
) {
    vendorForm.addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();

            setMessage(
                vendorMessage,
                ""
            );

            const name =
                vendorNameInput
                    ?.value
                    .trim()
                || "";

            const website =
                vendorWebsiteInput
                    ?.value
                    .trim()
                || "";

            const csrfToken =
                vendorForm
                    .dataset
                    .csrfToken;


            if (!name) {
                setMessage(
                    vendorMessage,
                    "Vendor name is required."
                );

                return;
            }


            try {
                const response =
                    await fetch(
                        "/api/vendors",
                        {
                            method:
                                "POST",

                            credentials:
                                "same-origin",

                            headers: {
                                "Content-Type":
                                    "application/json",

                                "X-CSRF-Token":
                                    csrfToken,
                            },

                            body:
                                JSON.stringify(
                                    {
                                        name:
                                            name,

                                        website:
                                            website
                                            || null,
                                    }
                                ),
                        }
                    );


                const body =
                    await readJsonSafely(
                        response
                    );


                if (
                    !response.ok
                ) {
                    throw new Error(
                        responseError(
                            body,
                            response,
                            "Unable to create vendor"
                        )
                    );
                }


                setMessage(
                    vendorMessage,
                    "Vendor created successfully."
                );

                vendorForm
                    .reset();

                await loadVendors();

            } catch (error) {
                console.error(
                    "Vendor creation failed:",
                    error
                );

                setMessage(
                    vendorMessage,
                    error.message
                );
            }
        }
    );
}


// ------------------------------------------------------------
// FILE HELPERS
// ------------------------------------------------------------

async function sha256File(
    file
) {
    const buffer =
        await file
            .arrayBuffer();

    const digest =
        await window.crypto
            .subtle
            .digest(
                "SHA-256",
                buffer
            );

    const bytes =
        new Uint8Array(
            digest
        );

    return Array
        .from(
            bytes
        )
        .map(
            (byte) =>
                byte
                    .toString(16)
                    .padStart(
                        2,
                        "0"
                    )
        )
        .join("");
}


function getContentType(
    file
) {
    if (
        file.type
    ) {
        return file.type;
    }

    const extension =
        file.name
            .split(".")
            .pop()
            .toLowerCase();

    const types = {
        pdf:
            "application/pdf",

        docx:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",

        xlsx:
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",

        csv:
            "text/csv",

        txt:
            "text/plain",

        md:
            "text/markdown",
    };

    return (
        types[
            extension
        ]
        ||
        "application/octet-stream"
    );
}


// ------------------------------------------------------------
// UPLOAD WORKSPACE
// ------------------------------------------------------------

if (
    documentForm
) {
    documentForm
        .addEventListener(
            "submit",
            async (event) => {
                event
                    .preventDefault();

                setMessage(
                    documentMessage,
                    ""
                );


                const vendorId =
                    uploadVendor
                        ?.value
                    || "";

                const file =
                    documentFile
                        ?.files
                        ?.[0];

                const csrfToken =
                    documentForm
                        .dataset
                        .csrfToken;


                if (!vendorId) {
                    setMessage(
                        documentMessage,
                        "Please select an upload vendor."
                    );

                    return;
                }


                if (!file) {
                    setMessage(
                        documentMessage,
                        "Please select a document."
                    );

                    return;
                }


                const maxFileSize =
                    25
                    * 1024
                    * 1024;


                if (
                    file.size
                    > maxFileSize
                ) {
                    setMessage(
                        documentMessage,
                        "File must be 25 MB or smaller."
                    );

                    return;
                }


                try {
                    // ----------------------------------------
                    // Step 1 - Hash file
                    // ----------------------------------------

                    setMessage(
                        documentMessage,
                        "Preparing document..."
                    );

                    const sha256 =
                        await sha256File(
                            file
                        );

                    const contentType =
                        getContentType(
                            file
                        );


                    // ----------------------------------------
                    // Step 2 - Request presigned upload URL
                    // ----------------------------------------

                    setMessage(
                        documentMessage,
                        "Requesting secure upload..."
                    );

                    const prepareResponse =
                        await fetch(
                            `/api/vendors/${vendorId}/documents/upload-url`,
                            {
                                method:
                                    "POST",

                                credentials:
                                    "same-origin",

                                headers: {
                                    "Content-Type":
                                        "application/json",

                                    "X-CSRF-Token":
                                        csrfToken,
                                },

                                body:
                                    JSON.stringify(
                                        {
                                            filename:
                                                file.name,

                                            content_type:
                                                contentType,

                                            size_bytes:
                                                file.size,

                                            sha256:
                                                sha256,
                                        }
                                    ),
                            }
                        );


                    const prepareBody =
                        await readJsonSafely(
                            prepareResponse
                        );


                    if (
                        !prepareResponse.ok
                    ) {
                        throw new Error(
                            responseError(
                                prepareBody,
                                prepareResponse,
                                "Unable to prepare upload"
                            )
                        );
                    }


                    // ----------------------------------------
                    // Step 3 - Upload directly to storage
                    // ----------------------------------------

                    setMessage(
                        documentMessage,
                        "Uploading document..."
                    );

                    const uploadResponse =
                        await fetch(
                            prepareBody
                                .upload_url,
                            {
                                method:
                                    "PUT",

                                headers: {
                                    "Content-Type":
                                        contentType,
                                },

                                body:
                                    file,
                            }
                        );


                    if (
                        !uploadResponse.ok
                    ) {
                        throw new Error(
                            `Object storage upload failed (HTTP ${uploadResponse.status})`
                        );
                    }


                    // ----------------------------------------
                    // Step 4 - Complete upload
                    // ----------------------------------------

                    setMessage(
                        documentMessage,
                        "Verifying upload..."
                    );

                    const completeResponse =
                        await fetch(
                            `/api/vendors/${vendorId}/documents/${prepareBody.document_id}/complete`,
                            {
                                method:
                                    "POST",

                                credentials:
                                    "same-origin",

                                headers: {
                                    "X-CSRF-Token":
                                        csrfToken,
                                },
                            }
                        );


                    const completeBody =
                        await readJsonSafely(
                            completeResponse
                        );


                    if (
                        !completeResponse.ok
                    ) {
                        throw new Error(
                            responseError(
                                completeBody,
                                completeResponse,
                                "Unable to complete upload"
                            )
                        );
                    }


                    setMessage(
                        documentMessage,
                        "Document uploaded successfully. Use Document Library to parse it."
                    );


                    documentFile.value =
                        "";


                    // ----------------------------------------
                    // Do NOT change other workspace selections.
                    //
                    // Only refresh another workspace when it
                    // already happens to be viewing this vendor.
                    // ----------------------------------------

                    if (
                        documentLibraryVendor
                            ?.value
                        === vendorId
                    ) {
                        await loadDocuments(
                            vendorId
                        );
                    }


                    if (
                        assessmentVendor
                            ?.value
                        === vendorId
                    ) {
                        await loadAssessmentReadiness(
                            vendorId
                        );
                    }

                } catch (error) {
                    console.error(
                        "Document upload failed:",
                        error
                    );

                    setMessage(
                        documentMessage,
                        error.message
                    );
                }
            }
        );
}


// ------------------------------------------------------------
// DOCUMENT LIBRARY WORKSPACE
// ------------------------------------------------------------

async function loadDocuments(
    vendorId
) {
    if (
        !documentList
    ) {
        return [];
    }


    if (!vendorId) {
        documentList
            .classList
            .add(
                "empty-state"
            );

        documentList
            .textContent =
            "Select a vendor to view documents.";

        if (
            documentCount
        ) {
            documentCount
                .textContent =
                "0";
        }

        return [];
    }


    documentList
        .classList
        .add(
            "empty-state"
        );

    documentList
        .textContent =
        "Loading documents...";


    try {
        const response =
            await fetch(
                `/api/vendors/${vendorId}/documents`,
                {
                    credentials:
                        "same-origin",
                }
            );


        const body =
            await readJsonSafely(
                response
            );


        if (
            !response.ok
        ) {
            throw new Error(
                responseError(
                    body,
                    response,
                    "Unable to load documents"
                )
            );
        }


        const documents =
            Array.isArray(
                body
            )
                ? body
                : [];


        if (
            documentCount
        ) {
            documentCount
                .textContent =
                String(
                    documents.length
                );
        }


        documentList.innerHTML =
            "";


        if (
            documents.length
            === 0
        ) {
            documentList
                .classList
                .add(
                    "empty-state"
                );

            documentList
                .textContent =
                "No documents uploaded for this vendor.";

            return documents;
        }


        documentList
            .classList
            .remove(
                "empty-state"
            );


        for (
            const doc
            of documents
        ) {
            const item =
                window.document
                    .createElement(
                        "div"
                    );

            item.className =
                "document-item";


            const fileSize =
                formatFileSize(
                    doc.size_bytes
                );


            let actionButton =
                "";


            const canParse =
                [
                    "uploaded",
                    "failed",
                    "parsed",
                    "embedding_pending",
                    "embedding_failed",
                    "ready",
                ].includes(
                    doc.status
                );


            // documentForm only exists for
            // analyst/admin users.
            if (
                documentForm
                &&
                canParse
            ) {
                const isReparse =
                    ![
                        "uploaded",
                        "failed",
                    ].includes(
                        doc.status
                    );


                const buttonText =
                    isReparse
                        ? "Re-parse"
                        : "Parse";


                actionButton = `
                    <button
                        type="button"
                        class="ingest-button"
                        data-document-id="${escapeHtml(
                            doc.id
                        )}"
                        data-vendor-id="${escapeHtml(
                            doc.vendor_id
                        )}"
                    >
                        ${buttonText}
                    </button>
                `;
            }


            item.innerHTML = `
                <div class="document-main">

                    <strong>
                        ${escapeHtml(
                            doc.filename
                        )}
                    </strong>

                    <div class="document-details">

                        ${escapeHtml(
                            String(
                                doc.file_type
                                || "file"
                            ).toUpperCase()
                        )}

                        ${
                            fileSize
                                ? ` • ${escapeHtml(
                                    fileSize
                                )}`
                                : ""
                        }

                    </div>
                </div>


                <div class="document-actions">

                    <span
                        class="document-status status-${escapeHtml(
                            doc.status
                        )}"
                    >
                        ${escapeHtml(
                            doc.status
                        )}
                    </span>

                    ${actionButton}

                </div>
            `;


            documentList
                .appendChild(
                    item
                );
        }


        return documents;

    } catch (error) {
        console.error(
            "Document loading failed:",
            error
        );


        documentList
            .classList
            .add(
                "empty-state"
            );


        documentList
            .textContent =
            error.message;


        if (
            documentCount
        ) {
            documentCount
                .textContent =
                "0";
        }


        return [];
    }
}


// ------------------------------------------------------------
// DOCUMENT LIBRARY VENDOR CHANGE
// ------------------------------------------------------------

if (
    documentLibraryVendor
) {
    documentLibraryVendor
        .addEventListener(
            "change",
            async () => {
                await loadDocuments(
                    documentLibraryVendor
                        .value
                );
            }
        );
}


// ------------------------------------------------------------
// PARSE / RE-PARSE DOCUMENT
// ------------------------------------------------------------

async function ingestDocument(
    vendorId,
    documentId,
    button
) {
    if (
        !documentForm
    ) {
        return;
    }


    const csrfToken =
        documentForm
            .dataset
            .csrfToken;


    const originalText =
        button
            .textContent;


    button.disabled =
        true;


    button.textContent =
        "Parsing...";


    try {
        const response =
            await fetch(
                `/api/vendors/${vendorId}/documents/${documentId}/ingest`,
                {
                    method:
                        "POST",

                    credentials:
                        "same-origin",

                    headers: {
                        "X-CSRF-Token":
                            csrfToken,
                    },
                }
            );


        const body =
            await readJsonSafely(
                response
            );


        if (
            !response.ok
        ) {
            throw new Error(
                responseError(
                    body,
                    response,
                    "Document ingestion failed"
                )
            );
        }


        await loadDocuments(
            vendorId
        );


        if (
            assessmentVendor
                ?.value
            === vendorId
        ) {
            await loadAssessmentReadiness(
                vendorId
            );
        }

    } catch (error) {
        console.error(
            "Document ingestion failed:",
            error
        );


        window.alert(
            error.message
        );

    } finally {
        button.disabled =
            false;

        button.textContent =
            originalText;
    }
}


// ------------------------------------------------------------
// DOCUMENT LIBRARY BUTTON CLICK
// ------------------------------------------------------------

if (
    documentList
) {
    documentList
        .addEventListener(
            "click",
            async (event) => {
                const button =
                    event.target
                        .closest(
                            ".ingest-button"
                        );


                if (!button) {
                    return;
                }


                await ingestDocument(
                    button
                        .dataset
                        .vendorId,

                    button
                        .dataset
                        .documentId,

                    button
                );
            }
        );
}


// ------------------------------------------------------------
// ASSESSMENT READINESS
// ------------------------------------------------------------

function setAssessmentReadiness(
    message,
    state = "neutral",
    enabled = false
) {
    if (
        assessmentReadyStatus
    ) {
        assessmentReadyStatus
            .className =
            `readiness-box ${state}`;

        assessmentReadyStatus
            .textContent =
            message;
    }


    if (
        runAssessmentButton
    ) {
        runAssessmentButton
            .disabled =
            !enabled;
    }
}


async function loadAssessmentReadiness(
    vendorId
) {
    if (!vendorId) {
        setAssessmentReadiness(
            "Select a vendor to check assessment readiness.",
            "neutral",
            false
        );

        return;
    }


    setAssessmentReadiness(
        "Checking ready documents...",
        "neutral",
        false
    );


    try {
        const response =
            await fetch(
                `/api/vendors/${vendorId}/documents`,
                {
                    credentials:
                        "same-origin",
                }
            );


        const body =
            await readJsonSafely(
                response
            );


        if (
            !response.ok
        ) {
            throw new Error(
                responseError(
                    body,
                    response,
                    "Unable to check assessment readiness"
                )
            );
        }


        const documents =
            Array.isArray(
                body
            )
                ? body
                : [];


        const readyDocuments =
            documents
                .filter(
                    (doc) =>
                        doc.status
                        === "ready"
                );


        if (
            documents.length
            === 0
        ) {
            setAssessmentReadiness(
                "No documents exist for this vendor. Upload and process documents first.",
                "warning",
                false
            );

            return;
        }


        if (
            readyDocuments.length
            === 0
        ) {
            const processingCount =
                documents
                    .filter(
                        (doc) =>
                            [
                                "upload_pending",
                                "uploaded",
                                "processing",
                                "embedding_pending",
                                "embedding",
                                "parsed",
                            ].includes(
                                doc.status
                            )
                    )
                    .length;


            const failedCount =
                documents
                    .filter(
                        (doc) =>
                            [
                                "failed",
                                "embedding_failed",
                            ].includes(
                                doc.status
                            )
                    )
                    .length;


            let message =
                `${
                    documents.length
                } document(s), but none are ready yet.`;


            if (
                processingCount
                > 0
            ) {
                message +=
                    ` ${
                        processingCount
                    } still processing.`;
            }


            if (
                failedCount
                > 0
            ) {
                message +=
                    ` ${
                        failedCount
                    } failed.`;
            }


            setAssessmentReadiness(
                message,
                "warning",
                false
            );


            return;
        }


        setAssessmentReadiness(
            `${
                readyDocuments.length
            } ready document(s). This vendor can be assessed.`,
            "ready",
            true
        );

    } catch (error) {
        console.error(
            "Assessment readiness failed:",
            error
        );


        setAssessmentReadiness(
            error.message,
            "error",
            false
        );
    }
}


// ------------------------------------------------------------
// ASSESSMENT VENDOR CHANGE
// ------------------------------------------------------------

if (
    assessmentVendor
) {
    assessmentVendor
        .addEventListener(
            "change",
            async () => {
                clearAssessmentOutput();


                await loadAssessmentReadiness(
                    assessmentVendor
                        .value
                );
            }
        );
}


// ------------------------------------------------------------
// RENDER ASSESSMENT RESULT
// ------------------------------------------------------------

function renderAssessmentResult(
    body
) {
    if (
        !assessmentResult
    ) {
        return;
    }


    const findings =
        Array.isArray(
            body.findings
        )
            ? body.findings
            : [];


    const summary =
        body.metadata
            ?.analysis_summary
        ||
        "Assessment completed using grounded vendor evidence.";


    let findingsHtml =
        "";


    if (
        findings.length
        === 0
    ) {
        findingsHtml = `
            <div class="assessment-empty">
                No findings were returned for this assessment.
            </div>
        `;

    } else {
        findingsHtml =
            findings
                .map(
                    (
                        finding,
                        index
                    ) => {
                        const severity =
                            severityClass(
                                finding
                                    .severity
                            );


                        const evidence =
                            Array.isArray(
                                finding
                                    .metadata
                                    ?.evidence
                            )
                                ? finding
                                    .metadata
                                    .evidence
                                : [];


                        const evidenceHtml =
                            evidence.length
                                ? `
                                    <div class="finding-evidence">

                                        <strong>
                                            Evidence sources
                                        </strong>

                                        <div class="evidence-chips">

                                            ${
                                                evidence
                                                    .map(
                                                        (
                                                            item
                                                        ) => `
                                                            <span class="evidence-chip">

                                                                Doc ${
                                                                    escapeHtml(
                                                                        String(
                                                                            item.document_id
                                                                            || ""
                                                                        )
                                                                        .slice(
                                                                            0,
                                                                            8
                                                                        )
                                                                    )
                                                                }

                                                                · Chunk ${
                                                                    escapeHtml(
                                                                        String(
                                                                            item.chunk_id
                                                                            || ""
                                                                        )
                                                                        .slice(
                                                                            0,
                                                                            8
                                                                        )
                                                                    )
                                                                }

                                                                · ${
                                                                    escapeHtml(
                                                                        Number(
                                                                            item.similarity
                                                                            || 0
                                                                        )
                                                                        .toFixed(
                                                                            3
                                                                        )
                                                                    )
                                                                }

                                                            </span>
                                                        `
                                                    )
                                                    .join("")
                                            }

                                        </div>
                                    </div>
                                `
                                : "";


                        return `
                            <article
                                class="
                                    assessment-finding
                                    finding-${severity}
                                "
                            >

                                <div class="finding-top-row">

                                    <div>

                                        <div class="finding-number">
                                            Finding ${
                                                index + 1
                                            }
                                        </div>

                                        <h4>
                                            ${escapeHtml(
                                                finding.title
                                            )}
                                        </h4>

                                    </div>


                                    <span
                                        class="
                                            severity-badge
                                            severity-${severity}
                                        "
                                    >
                                        ${escapeHtml(
                                            finding.severity
                                            || "unrated"
                                        )}
                                    </span>

                                </div>


                                <div class="finding-meta-grid">

                                    <div>
                                        <span class="meta-label">
                                            Category
                                        </span>

                                        <strong>
                                            ${escapeHtml(
                                                formatLabel(
                                                    finding.category
                                                )
                                            )}
                                        </strong>
                                    </div>


                                    <div>
                                        <span class="meta-label">
                                            Type
                                        </span>

                                        <strong>
                                            ${escapeHtml(
                                                formatLabel(
                                                    finding
                                                        .metadata
                                                        ?.finding_type
                                                    || "other"
                                                )
                                            )}
                                        </strong>
                                    </div>


                                    <div>
                                        <span class="meta-label">
                                            Rule
                                        </span>

                                        <strong>
                                            ${escapeHtml(
                                                finding.rule_id
                                                || "No rule"
                                            )}
                                        </strong>
                                    </div>


                                    <div>
                                        <span class="meta-label">
                                            Confidence
                                        </span>

                                        <strong>
                                            ${escapeHtml(
                                                finding.confidence
                                                ?? "Unknown"
                                            )}
                                        </strong>
                                    </div>

                                </div>


                                <p class="finding-description">
                                    ${escapeHtml(
                                        finding.description
                                    )}
                                </p>


                                ${evidenceHtml}

                            </article>
                        `;
                    }
                )
                .join("");
    }


    assessmentResult.innerHTML = `
        <section class="assessment-output-card">

            <div class="assessment-output-header">

                <div>

                    <div class="eyebrow">
                        Completed Assessment
                    </div>

                    <h2>
                        Vendor Risk Assessment Result
                    </h2>

                    <p>
                        ${escapeHtml(
                            summary
                        )}
                    </p>

                </div>


                <div
                    class="
                        overall-risk
                        overall-${severityClass(
                            body.overall_risk
                        )}
                    "
                >

                    <span>
                        Overall Risk
                    </span>

                    <strong>
                        ${escapeHtml(
                            body.overall_risk
                            || "unrated"
                        )}
                    </strong>

                </div>

            </div>


            <div class="assessment-stat-grid">

                <div class="assessment-stat">

                    <span>
                        Assessment ID
                    </span>

                    <strong>
                        ${escapeHtml(
                            body.id
                        )}
                    </strong>

                </div>


                <div class="assessment-stat">

                    <span>
                        Status
                    </span>

                    <strong>
                        ${escapeHtml(
                            body.status
                        )}
                    </strong>

                </div>


                <div class="assessment-stat">

                    <span>
                        Type
                    </span>

                    <strong>
                        ${escapeHtml(
                            body.assessment_type
                        )}
                    </strong>

                </div>


                <div class="assessment-stat">

                    <span>
                        Findings
                    </span>

                    <strong>
                        ${findings.length}
                    </strong>

                </div>


                <div class="assessment-stat">

                    <span>
                        Overall Score
                    </span>

                    <strong>
                        ${escapeHtml(
                            body.overall_score
                            ?? "Not calculated"
                        )}
                    </strong>

                </div>


                <div class="assessment-stat">

                    <span>
                        Policy
                    </span>

                    <strong>
                        ${escapeHtml(
                            body.metadata
                                ?.risk_policy_version
                            || "Not applied"
                        )}
                    </strong>

                </div>

            </div>


            <div class="assessment-findings">

                ${findingsHtml}

            </div>

        </section>
    `;


    assessmentResult
        .scrollIntoView(
            {
                behavior:
                    "smooth",

                block:
                    "start",
            }
        );
}


// ------------------------------------------------------------
// RUN ASSESSMENT
// ------------------------------------------------------------

if (
    runAssessmentButton
) {
    runAssessmentButton
        .addEventListener(
            "click",
            async () => {
                const vendorId =
                    assessmentVendor
                        ?.value
                    || "";


                if (!vendorId) {
                    setMessage(
                        assessmentMessage,
                        "Please select an assessment vendor."
                    );

                    return;
                }


                const csrfToken =
                    runAssessmentButton
                        .dataset
                        .csrfToken;


                if (!csrfToken) {
                    setMessage(
                        assessmentMessage,
                        "Security token is missing. Refresh the page and try again."
                    );

                    return;
                }


                const originalText =
                    runAssessmentButton
                        .textContent;


                runAssessmentButton
                    .disabled =
                    true;


                runAssessmentButton
                    .textContent =
                    "Running assessment...";


                setMessage(
                    assessmentMessage,
                    "Retrieving vendor evidence and running grounded risk analysis..."
                );


                if (
                    assessmentResult
                ) {
                    assessmentResult
                        .innerHTML =
                        "";
                }


                try {
                    const response =
                        await fetch(
                            `/api/vendors/${vendorId}/assessments`,
                            {
                                method:
                                    "POST",

                                credentials:
                                    "same-origin",

                                headers: {
                                    "X-CSRF-Token":
                                        csrfToken,
                                },
                            }
                        );


                    const body =
                        await readJsonSafely(
                            response
                        );


                    if (
                        !response.ok
                    ) {
                        throw new Error(
                            responseError(
                                body,
                                response,
                                "Assessment creation failed"
                            )
                        );
                    }


                    setMessage(
                        assessmentMessage,
                        "Assessment completed successfully."
                    );


                    renderAssessmentResult(
                        body
                    );

                } catch (error) {
                    console.error(
                        "Assessment failed:",
                        error
                    );


                    setMessage(
                        assessmentMessage,
                        error.message
                    );


                    if (
                        assessmentResult
                    ) {
                        assessmentResult
                            .innerHTML = `
                                <div class="assessment-error-card">

                                    <strong>
                                        Assessment could not be completed.
                                    </strong>

                                    <p>
                                        ${escapeHtml(
                                            error.message
                                        )}
                                    </p>

                                    <p>
                                        Check the Northflank logs
                                        for this request before
                                        running another assessment.
                                    </p>

                                </div>
                            `;
                    }

                } finally {
                    runAssessmentButton
                        .textContent =
                        originalText;


                    // Re-check whether the vendor
                    // still has ready documents.
                    await loadAssessmentReadiness(
                        vendorId
                    );
                }
            }
        );
}


// ------------------------------------------------------------
// INITIAL PAGE LOAD
// ------------------------------------------------------------

async function initializePage() {
    await loadVendors();


    if (
        documentLibraryVendor
            ?.value
    ) {
        await loadDocuments(
            documentLibraryVendor
                .value
        );
    }


    if (
        assessmentVendor
            ?.value
    ) {
        await loadAssessmentReadiness(
            assessmentVendor
                .value
        );

    } else {
        setAssessmentReadiness(
            "Select a vendor to check assessment readiness.",
            "neutral",
            false
        );
    }
}


initializePage();