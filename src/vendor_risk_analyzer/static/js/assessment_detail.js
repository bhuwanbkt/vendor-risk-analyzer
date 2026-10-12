// ============================================================
// Live progress for a queued or running assessment.
//
// Polls the assessment every few seconds, updates the step
// indicator, and reloads the page into the full report once
// the assessment is completed or failed. Pauses in background
// tabs.
// ============================================================

const progressPanel =
    document.getElementById(
        "assessment-progress"
    );

const progressMessage =
    document.getElementById(
        "assessment-progress-message"
    );

const progressSteps =
    document.getElementById(
        "assessment-progress-steps"
    );

const STAGE_STEP = {
    queued: 0,
    starting: 1,
    retrieving: 1,
    analyzing: 2,
    saving: 3,
    scoring: 4,
};

const PROGRESS_POLL_MS =
    3000;

let progressTimer =
    null;


function renderProgress(
    progress
) {
    if (
        progressMessage
        && progress?.message
    ) {
        progressMessage.textContent =
            progress.message;
    }

    const current =
        STAGE_STEP[
            progress?.stage
        ] ?? 0;

    progressSteps
        ?.querySelectorAll(
            "li[data-step]"
        )
        .forEach(
            (item) => {
                const step =
                    Number(
                        item.dataset.step
                    );

                item.classList.toggle(
                    "done",
                    step < current
                );

                item.classList.toggle(
                    "current",
                    step === current
                );
            }
        );
}


function scheduleProgressCheck() {
    if (progressTimer !== null) {
        clearTimeout(
            progressTimer
        );
    }

    progressTimer =
        setTimeout(
            checkProgress,
            PROGRESS_POLL_MS
        );
}


async function checkProgress() {
    progressTimer =
        null;

    if (!progressPanel) {
        return;
    }

    // Background tab: check again when it becomes visible.
    if (window.document.hidden) {
        return;
    }

    try {
        const response =
            await fetch(
                `/api/assessments/${progressPanel.dataset.assessmentId}`,
                {
                    credentials:
                        "same-origin",
                }
            );

        if (!response.ok) {
            throw new Error(
                "Progress check failed."
            );
        }

        const body =
            await response.json();

        if (
            body.status === "completed"
            ||
            body.status === "failed"
        ) {
            window.location.reload();
            return;
        }

        renderProgress(
            body.metadata?.progress
        );

    } catch (error) {
        // Keep the current view and try again.
    }

    scheduleProgressCheck();
}


window.document.addEventListener(
    "visibilitychange",
    () => {
        if (
            !window.document.hidden
            && progressTimer === null
        ) {
            checkProgress();
        }
    }
);


if (progressPanel) {
    scheduleProgressCheck();
}
