const sidebar =
    document.getElementById(
        "app-sidebar"
    );

const overlay =
    document.getElementById(
        "sidebar-overlay"
    );

const mobileMenuButton =
    document.getElementById(
        "mobile-menu-button"
    );

const profileMenuButton =
    document.getElementById(
        "profile-menu-button"
    );

const profileMenu =
    document.getElementById(
        "profile-menu"
    );


function openSidebar() {
    sidebar?.classList.add(
        "open"
    );

    overlay?.classList.add(
        "visible"
    );
}


function closeSidebar() {
    sidebar?.classList.remove(
        "open"
    );

    overlay?.classList.remove(
        "visible"
    );
}


mobileMenuButton
    ?.addEventListener(
        "click",
        openSidebar
    );


overlay
    ?.addEventListener(
        "click",
        closeSidebar
    );


profileMenuButton
    ?.addEventListener(
        "click",
        (event) => {
            event.stopPropagation();

            if (!profileMenu) {
                return;
            }

            profileMenu.hidden =
                !profileMenu.hidden;
        }
    );


document.addEventListener(
    "click",
    (event) => {
        if (
            !profileMenu ||
            profileMenu.hidden
        ) {
            return;
        }

        if (
            profileMenu.contains(
                event.target
            )
            ||
            profileMenuButton
                ?.contains(
                    event.target
                )
        ) {
            return;
        }

        profileMenu.hidden =
            true;
    }
);