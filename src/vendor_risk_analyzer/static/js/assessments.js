const runner =
    document.querySelector(
        ".assessment-runner"
    );

const assessmentVendor =
    document.getElementById(
        "assessment-vendor"
    );

const readiness =
    document.getElementById(
        "assessment-readiness"
    );

const runButton =
    document.getElementById(
        "run-assessment-button"
    );

const message =
    document.getElementById(
        "assessment-message"
    );


async function checkReadiness(
    vendorId
) {
    runButton.disabled =
        true;


    if (!vendorId) {
        readiness.textContent =
            "Select a vendor.";

        readiness.className =
            "readiness neutral";

        return;
    }


    readiness.textContent =
        "Checking documents...";

    readiness.className =
        "readiness neutral";


    try {
        const response =
            await fetch(
                `/api/vendors/${vendorId}/documents`,
                {
                    credentials:
                        "same-origin",
                }
            );


        const documents =
            await response.json();


        if (!response.ok) {
            throw new Error(
                documents.detail
                ||
                "Unable to check documents."
            );
        }


        const ready =
            documents.filter(
                document =>
                    document.status
                    === "ready"
            );


        if (!ready.length) {
            readiness.textContent =
                "No ready documents.";

            readiness.className =
                "readiness blocked";

            return;
        }


        readiness.textContent =
            `${ready.length} ready document(s)`;

        readiness.className =
            "readiness ready";

        runButton.disabled =
            false;

    } catch (error) {
        readiness.textContent =
            error.message;

        readiness.className =
            "readiness blocked";
    }
}


assessmentVendor
    ?.addEventListener(
        "change",
        () => {
            message.textContent =
                "";

            checkReadiness(
                assessmentVendor.value
            );
        }
    );


runButton
    ?.addEventListener(
        "click",
        async () => {
            const vendorId =
                assessmentVendor.value;

            if (!vendorId) {
                return;
            }


            runButton.disabled =
                true;

            runButton.textContent =
                "Running assessment...";

            message.textContent =
                "Retrieving evidence and analyzing vendor risk...";


            try {
                const response =
                    await fetch(
                        `/api/vendors/${vendorId}/assessments`,
                        {
                            method: "POST",

                            credentials:
                                "same-origin",

                            headers: {
                                "X-CSRF-Token":
                                    runner
                                        .dataset
                                        .csrfToken,
                            },
                        }
                    );


                const body =
                    await response.json();


                if (!response.ok) {
                    throw new Error(
                        body.detail
                        ||
                        "Assessment failed."
                    );
                }


                window.location.href =
                    `/assessments/${body.id}`;

            } catch (error) {
                message.textContent =
                    error.message;

                runButton.textContent =
                    "Run Assessment";

                await checkReadiness(
                    vendorId
                );
            }
        }
    );