const vendorForm =
    document.getElementById(
        "vendor-form"
    );

const vendorName =
    document.getElementById(
        "vendor-name"
    );

const vendorWebsite =
    document.getElementById(
        "vendor-website"
    );

const vendorMessage =
    document.getElementById(
        "vendor-message"
    );


if (vendorForm) {
    vendorForm.addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();

            vendorMessage.textContent =
                "";

            const csrf =
                vendorForm
                    .dataset
                    .csrfToken;


            try {
                const response =
                    await fetch(
                        "/api/vendors",
                        {
                            method: "POST",

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
                                        name:
                                            vendorName
                                                .value
                                                .trim(),

                                        website:
                                            vendorWebsite
                                                .value
                                                .trim()
                                            || null,
                                    }
                                ),
                        }
                    );


                const body =
                    await response.json();


                if (!response.ok) {
                    throw new Error(
                        body.detail
                        ||
                        "Unable to create vendor."
                    );
                }


                window.location.reload();

            } catch (error) {
                vendorMessage
                    .textContent =
                    error.message;
            }
        }
    );
}