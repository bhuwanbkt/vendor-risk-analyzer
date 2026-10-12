const page =
    document.getElementById(
        "documents-page"
    );

const canManage =
    page?.dataset.canManage
    === "true";


const uploadForm =
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

const uploadMessage =
    document.getElementById(
        "document-upload-message"
    );


const libraryVendor =
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


function escapeHtml(value) {
    const div =
        document.createElement(
            "div"
        );

    div.textContent =
        value ?? "";

    return div.innerHTML;
}


function getContentType(file) {
    if (file.type) {
        return file.type;
    }

    const extension =
        file.name
            .split(".")
            .pop()
            .toLowerCase();


    const map = {
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
        map[extension]
        ||
        "application/octet-stream"
    );
}


async function sha256File(
    file
) {
    const buffer =
        await file.arrayBuffer();

    const digest =
        await crypto.subtle.digest(
            "SHA-256",
            buffer
        );

    return Array.from(
        new Uint8Array(
            digest
        )
    )
        .map(
            value =>
                value
                    .toString(16)
                    .padStart(
                        2,
                        "0"
                    )
        )
        .join("");
}


// ------------------------------------------------------------
// Automatic refresh while documents are still being processed.
//
// Parsing and embedding continue in the background after the
// request returns, so the list re-checks itself every few
// seconds until nothing is in progress. Polling pauses while
// the tab is hidden and stops when the vendor changes.
// ------------------------------------------------------------

const IN_PROGRESS_STATUSES =
    new Set([
        "processing",
        "embedding_pending",
        "embedding",
    ]);

const POLL_INTERVAL_MS =
    5000;

let pollTimer =
    null;

let resumeVendorId =
    null;


function stopPolling() {
    if (pollTimer !== null) {
        clearTimeout(
            pollTimer
        );

        pollTimer =
            null;
    }

    resumeVendorId =
        null;
}


function scheduleRefresh(
    vendorId
) {
    stopPolling();

    pollTimer =
        setTimeout(
            () => {
                pollTimer =
                    null;

                // The user switched to another vendor.
                if (
                    libraryVendor?.value
                    !== vendorId
                ) {
                    return;
                }

                // Background tab: resume when it is visible again.
                if (
                    window.document.hidden
                ) {
                    resumeVendorId =
                        vendorId;

                    return;
                }

                loadDocuments(
                    vendorId,
                    { silent: true }
                );
            },
            POLL_INTERVAL_MS
        );
}


function scheduleRefreshIfProcessing(
    vendorId,
    documents
) {
    // A stale "processing" document is stuck, not progressing;
    // it shows a Retry button instead of being polled.
    const stillWorking =
        documents.some(
            doc =>
                IN_PROGRESS_STATUSES.has(
                    doc.status
                )
                &&
                doc.stale_processing
                !== true
        );

    if (stillWorking) {
        scheduleRefresh(
            vendorId
        );
    }
}


window.document.addEventListener(
    "visibilitychange",
    () => {
        if (
            window.document.hidden
            ||
            resumeVendorId === null
        ) {
            return;
        }

        const vendorId =
            resumeVendorId;

        resumeVendorId =
            null;

        if (
            libraryVendor?.value
            === vendorId
        ) {
            loadDocuments(
                vendorId,
                { silent: true }
            );
        }
    }
);


async function loadDocuments(
    vendorId,
    { silent = false } = {}
) {
    // Any explicit or automatic load replaces a pending refresh.
    stopPolling();

    if (!vendorId) {
        documentList.innerHTML =
            `
            <div class="empty-state">
                Select a vendor to view documents.
            </div>
            `;

        documentCount.textContent =
            "0 documents";

        return;
    }


    // Automatic refreshes update the list in place without
    // flashing a loading message.
    if (!silent) {
        documentList.innerHTML =
            `
            <div class="empty-state">
                Loading documents...
            </div>
            `;
    }


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
            await response.json();


        if (!response.ok) {
            throw new Error(
                body.detail
                ||
                "Unable to load documents."
            );
        }


        documentCount.textContent =
            `${body.length} documents`;


        if (!body.length) {
            documentList.innerHTML =
                `
                <div class="empty-state">
                    No documents for this vendor.
                </div>
                `;

            return;
        }


        documentList.innerHTML =
            body
                .map(
                    doc => {
                        const canParse =
                            canManage
                            &&
                            [
                                "uploaded",
                                "failed",
                                "parsed",
                                "embedding_failed",
                                "ready",
                            ].includes(
                                doc.status
                            )
                            ||
                            (
                                canManage
                                &&
                                doc.stale_processing
                                === true
                            );


                        return `
                            <article class="document-row">

                                <div>

                                    <strong>
                                        ${escapeHtml(
                                            doc.filename
                                        )}
                                    </strong>

                                    <span>
                                        ${escapeHtml(
                                            doc.file_type
                                                .toUpperCase()
                                        )}
                                    </span>

                                </div>


                                <div class="document-row-actions">

                                    <span
                                        class="
                                            document-status
                                            status-${escapeHtml(
                                                doc.status
                                            )}
                                        "
                                    >
                                        ${escapeHtml(
                                            doc.status
                                        )}
                                    </span>


                                    ${
                                        canParse
                                            ? `
                                                <button
                                                    type="button"
                                                    class="secondary-button ingest-button"
                                                    data-vendor="${escapeHtml(
                                                        doc.vendor_id
                                                    )}"
                                                    data-document="${escapeHtml(
                                                        doc.id
                                                    )}"
                                                >
                                                    ${
                                                        doc.status
                                                        === "uploaded"
                                                        ||
                                                        doc.status
                                                        === "failed"
                                                            ? "Parse"
                                                            : doc.status
                                                            === "processing"
                                                                ? "Retry"
                                                                : "Re-parse"
                                                    }
                                                </button>
                                            `
                                            : ""
                                    }

                                </div>

                            </article>
                        `;
                    }
                )
                .join("");


        scheduleRefreshIfProcessing(
            vendorId,
            body
        );

    } catch (error) {
        // A failed background refresh keeps the current list
        // and tries again later.
        if (silent) {
            scheduleRefresh(
                vendorId
            );

            return;
        }

        documentList.innerHTML =
            `
            <div class="empty-state error-text">
                ${escapeHtml(
                    error.message
                )}
            </div>
            `;
    }
}


libraryVendor
    ?.addEventListener(
        "change",
        () => {
            loadDocuments(
                libraryVendor.value
            );
        }
    );


documentList
    ?.addEventListener(
        "click",
        async (event) => {
            const button =
                event.target.closest(
                    ".ingest-button"
                );

            if (!button) {
                return;
            }


            button.disabled =
                true;

            button.textContent =
                "Processing...";


            try {
                const csrf =
                    uploadForm
                        ?.dataset
                        .csrfToken;


                const response =
                    await fetch(
                        `/api/vendors/${button.dataset.vendor}/documents/${button.dataset.document}/ingest`,
                        {
                            method: "POST",

                            credentials:
                                "same-origin",

                            headers: {
                                "X-CSRF-Token":
                                    csrf,
                            },
                        }
                    );


                const body =
                    await response.json();


                if (!response.ok) {
                    throw new Error(
                        body.detail
                        ||
                        "Document processing failed."
                    );
                }


                await loadDocuments(
                    libraryVendor.value
                );

            } catch (error) {
                window.alert(
                    error.message
                );

                button.disabled =
                    false;
            }
        }
    );


uploadForm
    ?.addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();

            const vendorId =
                uploadVendor.value;

            const file =
                documentFile.files[0];

            const csrf =
                uploadForm
                    .dataset
                    .csrfToken;


            if (
                !vendorId
                ||
                !file
            ) {
                return;
            }


            if (
                file.size
                >
                25
                * 1024
                * 1024
            ) {
                uploadMessage.textContent =
                    "File must be 25 MB or smaller.";

                return;
            }


            try {
                uploadMessage.textContent =
                    "Preparing secure upload...";


                const sha256 =
                    await sha256File(
                        file
                    );


                const contentType =
                    getContentType(
                        file
                    );


                const prepare =
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
                                    csrf,
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
                    await prepare.json();


                if (!prepare.ok) {
                    throw new Error(
                        prepareBody.detail
                        ||
                        "Unable to prepare upload."
                    );
                }


                uploadMessage.textContent =
                    "Uploading...";


                const storageResponse =
                    await fetch(
                        prepareBody
                            .upload_url,
                        {
                            method: "PUT",

                            headers: {
                                "Content-Type":
                                    contentType,
                            },

                            body:
                                file,
                        }
                    );


                if (
                    !storageResponse.ok
                ) {
                    throw new Error(
                        "Object storage upload failed."
                    );
                }


                uploadMessage.textContent =
                    "Verifying upload...";


                const complete =
                    await fetch(
                        `/api/vendors/${vendorId}/documents/${prepareBody.document_id}/complete`,
                        {
                            method:
                                "POST",

                            credentials:
                                "same-origin",

                            headers: {
                                "X-CSRF-Token":
                                    csrf,
                            },
                        }
                    );


                const completeBody =
                    await complete.json();


                if (!complete.ok) {
                    throw new Error(
                        completeBody.detail
                        ||
                        "Unable to complete upload."
                    );
                }


                documentFile.value =
                    "";

                uploadMessage.textContent =
                    "Document uploaded successfully.";


                if (
                    libraryVendor.value
                    === vendorId
                ) {
                    await loadDocuments(
                        vendorId
                    );
                }

            } catch (error) {
                uploadMessage.textContent =
                    error.message;
            }
        }
    );