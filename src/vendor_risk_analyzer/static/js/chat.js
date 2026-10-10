const chatApp =
    document.getElementById(
        "chat-app"
    );

const vendorSelect =
    document.getElementById(
        "chat-vendor"
    );

const messages =
    document.getElementById(
        "chat-messages"
    );

const chatForm =
    document.getElementById(
        "chat-form"
    );

const questionInput =
    document.getElementById(
        "chat-question"
    );

const sendButton =
    document.getElementById(
        "chat-send"
    );

const chatMessage =
    document.getElementById(
        "chat-message"
    );


let history = [];


function escapeHtml(value) {
    const div =
        document.createElement(
            "div"
        );

    div.textContent =
        value ?? "";

    return div.innerHTML;
}


function renderUserMessage(
    text
) {
    messages.insertAdjacentHTML(
        "beforeend",
        `
        <div class="user-message">

            <div class="message-bubble">
                <p>
                    ${escapeHtml(text)}
                </p>
            </div>

        </div>
        `
    );
}


function renderAssistantMessage(
    answer,
    sources
) {
    const formattedAnswer =
        escapeHtml(answer)
            .replace(
                /\n/g,
                "<br>"
            );


    const sourceHtml =
        sources.length
            ? `
                <div class="chat-sources">

                    <strong>
                        Sources
                    </strong>

                    ${
                        sources
                            .map(
                                source => `
                                    <span class="chat-source">
                                        S${source.source_number}
                                        · Doc ${escapeHtml(
                                            source.document_id.slice(
                                                0,
                                                8
                                            )
                                        )}
                                        · Chunk ${escapeHtml(
                                            source.chunk_id.slice(
                                                0,
                                                8
                                            )
                                        )}
                                        · ${Math.round(
                                            source.similarity
                                            * 100
                                        )}%
                                    </span>
                                `
                            )
                            .join("")
                    }

                </div>
            `
            : "";


    messages.insertAdjacentHTML(
        "beforeend",
        `
        <div class="assistant-message">

            <div class="message-avatar">
                AI
            </div>

            <div class="message-bubble">

                <p>
                    ${formattedAnswer}
                </p>

                ${sourceHtml}

            </div>

        </div>
        `
    );


    messages.scrollTop =
        messages.scrollHeight;
}


vendorSelect
    ?.addEventListener(
        "change",
        () => {
            history = [];

            chatMessage.textContent =
                "";

            messages.innerHTML =
                `
                <div class="assistant-message">

                    <div class="message-avatar">
                        AI
                    </div>

                    <div class="message-bubble">

                        <strong>
                            Vendor context changed
                        </strong>

                        <p>
                            Ask a question about the
                            selected vendor.
                        </p>

                    </div>

                </div>
                `;
        }
    );


document
    .querySelectorAll(
        ".chat-suggestion"
    )
    .forEach(
        button => {
            button.addEventListener(
                "click",
                () => {
                    questionInput.value =
                        button.textContent
                            .replace(/\s+/g, " ")
                            .trim();

                    questionInput.focus();
                }
            );
        }
    );


chatForm
    ?.addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();


            const vendorId =
                vendorSelect.value;

            const question =
                questionInput
                    .value
                    .trim();


            if (!vendorId) {
                chatMessage.textContent =
                    "Select a vendor first.";

                return;
            }


            if (!question) {
                return;
            }


            const previousHistory =
                history.slice(
                    -6
                );


            renderUserMessage(
                question
            );


            history.push(
                {
                    role: "user",
                    content: question,
                }
            );


            questionInput.value =
                "";

            sendButton.disabled =
                true;

            sendButton.textContent =
                "Thinking...";

            chatMessage.textContent =
                "";


            try {
                const response =
                    await fetch(
                        "/api/chat",
                        {
                            method: "POST",

                            credentials:
                                "same-origin",

                            headers: {
                                "Content-Type":
                                    "application/json",

                                "X-CSRF-Token":
                                    chatApp
                                        .dataset
                                        .csrfToken,
                            },

                            body:
                                JSON.stringify(
                                    {
                                        vendor_id:
                                            vendorId,

                                        question:
                                            question,

                                        history:
                                            previousHistory,
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
                        "Chat request failed."
                    );
                }


                renderAssistantMessage(
                    body.answer,
                    body.sources
                    || []
                );


                history.push(
                    {
                        role:
                            "assistant",

                        content:
                            body.answer,
                    }
                );


                history =
                    history.slice(
                        -6
                    );

            } catch (error) {
                chatMessage.textContent =
                    error.message;

            } finally {
                sendButton.disabled =
                    false;

                sendButton.textContent =
                    "Send";
            }
        }
    );
