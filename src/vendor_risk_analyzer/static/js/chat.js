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

// Saved conversation being continued; null starts a new one.
let conversationId = null;


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
                                        conversation_id:
                                            conversationId,
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

                if (body.conversation_id) {
                    conversationId =
                        body.conversation_id;

                    loadConversations(
                        vendorId
                    );
                }

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


// ============================================================
// Saved conversations
// ============================================================

const conversationList =
    document.getElementById(
        "chat-conversations"
    );

const newChatButton =
    document.getElementById(
        "chat-new"
    );


function showWelcome(
    title,
    text
) {
    messages.innerHTML =
        `
        <div class="assistant-message">
            <div class="message-avatar">
                AI
            </div>
            <div class="message-bubble">
                <strong>
                    ${escapeHtml(title)}
                </strong>
                <p>
                    ${escapeHtml(text)}
                </p>
            </div>
        </div>
        `;
}


function renderConversationList(
    conversations
) {
    if (!conversationList) {
        return;
    }

    if (!conversations.length) {
        conversationList.innerHTML =
            `
            <li class="chat-history-empty">
                No saved conversations for this vendor yet.
            </li>
            `;
        return;
    }

    conversationList.innerHTML =
        conversations
            .map(
                conversation => `
                    <li
                        class="${
                            conversation.id === conversationId
                                ? "active"
                                : ""
                        }"
                    >
                        <button
                            type="button"
                            class="chat-conversation-open"
                            data-id="${escapeHtml(conversation.id)}"
                        >
                            ${escapeHtml(conversation.title)}
                            <small>
                                ${escapeHtml(
                                    (conversation.updated_at || "")
                                        .slice(0, 16)
                                        .replace("T", " ")
                                )}
                            </small>
                        </button>
                        <button
                            type="button"
                            class="chat-conversation-delete"
                            data-id="${escapeHtml(conversation.id)}"
                            aria-label="Delete conversation"
                        >
                            ×
                        </button>
                    </li>
                `
            )
            .join("");
}


async function loadConversations(
    vendorId
) {
    if (!conversationList) {
        return;
    }

    if (!vendorId) {
        conversationList.innerHTML =
            `
            <li class="chat-history-empty">
                Select a vendor to see your saved conversations.
            </li>
            `;
        return;
    }

    try {
        const response =
            await fetch(
                `/api/chat/conversations?vendor_id=${encodeURIComponent(vendorId)}`,
                {
                    credentials:
                        "same-origin",
                }
            );

        if (!response.ok) {
            throw new Error(
                "Unable to load saved conversations."
            );
        }

        // Ignore a slow response for a vendor that is no longer selected.
        if (vendorSelect.value !== vendorId) {
            return;
        }

        renderConversationList(
            await response.json()
        );

    } catch (error) {
        conversationList.innerHTML =
            `
            <li class="chat-history-empty">
                ${escapeHtml(error.message)}
            </li>
            `;
    }
}


async function openConversation(
    id
) {
    try {
        const response =
            await fetch(
                `/api/chat/conversations/${encodeURIComponent(id)}`,
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
                "Unable to open the conversation."
            );
        }

        conversationId =
            body.id;

        messages.innerHTML =
            "";

        history =
            [];

        for (const message of body.messages) {
            if (message.role === "user") {
                renderUserMessage(
                    message.content
                );
            } else {
                renderAssistantMessage(
                    message.content,
                    message.sources
                    || []
                );
            }

            history.push(
                {
                    role: message.role,
                    content: message.content,
                }
            );
        }

        history =
            history.slice(
                -6
            );

        chatMessage.textContent =
            "";

        loadConversations(
            vendorSelect.value
        );

    } catch (error) {
        chatMessage.textContent =
            error.message;
    }
}


async function deleteConversation(
    id
) {
    const response =
        await fetch(
            `/api/chat/conversations/${encodeURIComponent(id)}`,
            {
                method: "DELETE",
                credentials:
                    "same-origin",
                headers: {
                    "X-CSRF-Token":
                        chatApp
                            .dataset
                            .csrfToken,
                },
            }
        );

    if (!response.ok) {
        chatMessage.textContent =
            "Unable to delete the conversation.";
        return;
    }

    if (conversationId === id) {
        startNewConversation();
    }

    loadConversations(
        vendorSelect.value
    );
}


function startNewConversation() {
    conversationId =
        null;

    history =
        [];

    chatMessage.textContent =
        "";

    showWelcome(
        "New conversation",
        "Ask a question about the selected vendor."
    );

    loadConversations(
        vendorSelect.value
    );
}


conversationList
    ?.addEventListener(
        "click",
        (event) => {
            const deleteButton =
                event.target.closest(
                    ".chat-conversation-delete"
                );

            if (deleteButton) {
                if (
                    window.confirm(
                        "Delete this saved conversation?"
                    )
                ) {
                    deleteConversation(
                        deleteButton.dataset.id
                    );
                }
                return;
            }

            const openButton =
                event.target.closest(
                    ".chat-conversation-open"
                );

            if (openButton) {
                openConversation(
                    openButton.dataset.id
                );
            }
        }
    );


newChatButton
    ?.addEventListener(
        "click",
        startNewConversation
    );


vendorSelect
    ?.addEventListener(
        "change",
        () => {
            conversationId =
                null;

            loadConversations(
                vendorSelect.value
            );
        }
    );

