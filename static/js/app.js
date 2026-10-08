"use strict";


/* =========================================================
   PASSWORD
========================================================= */

function togglePassword(inputId, button) {

    const input = document.getElementById(inputId);

    if (!input) {
        return;
    }

    if (input.type === "password") {

        input.type = "text";
        button.textContent = "Ocultar";

    } else {

        input.type = "password";
        button.textContent = "Mostrar";

    }

}


/* =========================================================
   LIKE
========================================================= */

async function toggleLike(button) {

    const postId = button.dataset.postId;

    if (!postId) {
        return;
    }

    button.disabled = true;

    try {

        const response = await fetch(
            `/api/posts/${postId}/like`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                }
            }
        );

        const data = await response.json();

        if (!response.ok) {

            console.error(data.error);

            button.disabled = false;

            return;
        }


        button.classList.toggle(
            "liked",
            Boolean(data.liked)
        );


        const count =
            button.querySelector(".like-count");

        if (count) {
            count.textContent = data.count;
        }


        const icon =
            button.querySelector("span:first-child");

        if (icon) {
            icon.textContent =
                data.liked ? "♥" : "♡";
        }

    } catch (error) {

        console.error(error);

    } finally {

        button.disabled = false;

    }

}


/* =========================================================
   FOLLOW
========================================================= */

async function toggleFollow(button) {

    const username = button.dataset.username;

    if (!username) {
        return;
    }

    button.disabled = true;

    try {

        const response = await fetch(
            `/api/users/${encodeURIComponent(username)}/follow`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                }
            }
        );

        const data = await response.json();

        if (!response.ok) {

            alert(
                data.error ||
                "Não foi possível alterar o seguimento."
            );

            return;
        }


        button.dataset.following =
            data.following ? "true" : "false";


        button.textContent =
            data.following ? "Seguindo" : "Seguir";


        button.classList.toggle(
            "following",
            Boolean(data.following)
        );


        const followerCounters =
            document.querySelectorAll(
                "[data-followers-count]"
            );

        followerCounters.forEach(function (counter) {

            if (
                typeof data.followers !==
                "undefined"
            ) {
                counter.textContent =
                    data.followers;
            }

        });

    } catch (error) {

        console.error(error);

        alert("Erro de conexão.");

    } finally {

        button.disabled = false;

    }

}


/* =========================================================
   AVATAR PREVIEW
========================================================= */

function previewAvatar(input) {

    if (
        !input ||
        !input.files ||
        !input.files[0]
    ) {
        return;
    }

    const file = input.files[0];

    const preview =
        document.getElementById("avatar-preview");

    const placeholder =
        document.getElementById("avatar-placeholder");

    if (!preview) {
        return;
    }

    const reader = new FileReader();

    reader.onload = function (event) {

        preview.src = event.target.result;
        preview.hidden = false;

        if (placeholder) {
            placeholder.hidden = true;
        }

    };

    reader.readAsDataURL(file);

}


/* =========================================================
   POST IMAGE PREVIEW
========================================================= */

function previewPostImage(input) {

    if (
        !input ||
        !input.files ||
        !input.files[0]
    ) {
        return;
    }

    const file = input.files[0];

    const container =
        document.getElementById(
            "post-image-preview"
        );

    if (!container) {
        return;
    }

    const image =
        container.querySelector("img");

    const reader = new FileReader();

    reader.onload = function (event) {

        image.src =
            event.target.result;

        container.hidden = false;

    };

    reader.readAsDataURL(file);

}


function clearPostImage() {

    const input =
        document.getElementById("post-image");

    const container =
        document.getElementById(
            "post-image-preview"
        );

    if (input) {
        input.value = "";
    }

    if (container) {

        container.hidden = true;

        const image =
            container.querySelector("img");

        if (image) {
            image.src = "";
        }

    }

}


/* =========================================================
   HEADER PREVIEW
========================================================= */

function updateHeaderColor(color) {

    const preview =
        document.getElementById(
            "header-preview"
        );

    if (!preview) {
        return;
    }

    preview.style.background =
        color;

    preview.style.backgroundImage =
        "none";

}


function updateHeaderGradient(gradient) {

    const preview =
        document.getElementById(
            "header-preview"
        );

    if (!preview) {
        return;
    }

    preview.style.background =
        gradient;

    preview.style.backgroundImage =
        "none";

}


function previewHeader(input) {

    if (
        !input ||
        !input.files ||
        !input.files[0]
    ) {
        return;
    }

    const file = input.files[0];

    const preview =
        document.getElementById(
            "header-preview"
        );

    if (!preview) {
        return;
    }

    const reader = new FileReader();

    reader.onload = function (event) {

        preview.style.backgroundImage =
            `url("${event.target.result}")`;

        preview.style.backgroundSize =
            "cover";

        preview.style.backgroundPosition =
            "center";

    };

    reader.readAsDataURL(file);

}


/* =========================================================
   TEXTAREA AUTO HEIGHT
========================================================= */

document.addEventListener(
    "input",
    function (event) {

        if (
            event.target.tagName !==
            "TEXTAREA"
        ) {
            return;
        }

        event.target.style.height =
            "auto";

        event.target.style.height =
            event.target.scrollHeight + "px";

    }
);


/* =========================================================
   SMOOTH PROFILE TABS
========================================================= */

document.addEventListener(
    "click",
    function (event) {

        const tab =
            event.target.closest(
                ".profile-tab"
            );

        if (!tab) {
            return;
        }

        const target =
            tab.getAttribute("href");

        if (!target || !target.startsWith("#")) {
            return;
        }

        document.querySelectorAll(
            ".profile-tab"
        ).forEach(function (item) {

            item.classList.remove("active");

        });

        tab.classList.add("active");

    }
);


/* =========================================================
   REMOVE FLASH MESSAGES
========================================================= */

document.addEventListener(
    "DOMContentLoaded",
    function () {

        setTimeout(function () {

            document
                .querySelectorAll(
                    ".flash-message"
                )
                .forEach(function (message) {

                    message.classList.add(
                        "flash-hiding"
                    );

                    setTimeout(function () {
                        message.remove();
                    }, 400);

                });

        }, 5000);

    }
);