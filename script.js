async function checkAuth() {
    const loading = document.getElementById("loading-spinner");
    const adminPanel = document.getElementById("admin-panel-view");
    const userPanel = document.getElementById("user-view");
    const nav = document.getElementById("admin-nav");

    const params = new URLSearchParams(window.location.search);
    const tgId = params.get("tg_id");

    if (!tgId) {
        if (loading) loading.innerHTML = "<p class='text-red-500'>❌ Ошибка: отсутствует tg_id</p>";
        return;
    }

    const BASE_URL = "https://viewless-anaya-unambulant.ngrok-free.dev"; // <-- твой ngrok-домен

    try {
        const response = await fetch(`${BASE_URL}/api/auth?tg_id=${tgId}`);
        const data = await response.json();

        if (!data.authorized) {
            if (loading) loading.innerHTML = `<p class='text-red-500'>🚫 ${data.message}</p>`;
            return;
        }

        // 🔹 Creator / Admin
        if (data.role === "creator" || data.role === "admin") {
            const adminUser = document.getElementById("admin-username");
            const userRole = document.getElementById("user-role-display");

            if (adminUser) adminUser.textContent = `ID: ${data.telegram_id}`;
            if (userRole) userRole.textContent = data.role;

            fadeOut(loading);
            fadeIn(adminPanel);
            fadeIn(nav);

            if (data.role === "creator") {
                initCreatorFeatures(tgId);
            }

        } else {
            // 🔹 Обычный пользователь
            const userName = document.getElementById("user-welcome-name");
            if (userName) userName.textContent = `ID: ${data.telegram_id}`;
            fadeOut(loading);
            fadeIn(userPanel);
        }

    } catch (error) {
        console.error("Ошибка при авторизации:", error);
        if (loading)
            loading.innerHTML = "<p class='text-red-500'>⚠️ Ошибка соединения с сервером</p>";
    }
}

// ------------------------------------------------------
// ✅ Основная логика для Creator-панели
// ------------------------------------------------------
function initCreatorFeatures(tgId) {
    console.log("✅ Creator-панель активирована");

    // ---------- 🔹 Переключение вкладок ----------
    const buttons = document.querySelectorAll(".nav-button");
    const sections = document.querySelectorAll(".content-section");

    buttons.forEach((btn) => {
        btn.addEventListener("click", (e) => {
            createRipple(e); // 💧 эффект нажатия
            buttons.forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");

            const target = btn.dataset.section;

            sections.forEach((sec) => {
                if (sec.id === target) {
                    fadeIn(sec);
                } else {
                    fadeOut(sec);
                }
            });
        });
    });

    // ---------- 🏆 Создание конкурса ----------
    const createBtn = document.getElementById("create-contest-btn");
    if (createBtn) {
        createBtn.addEventListener("click", (e) => {
            createRipple(e);
            openModal("Создание конкурса", `
                <label class="block mb-2 text-sm text-gray-400">Название конкурса</label>
                <input type="text" id="contest-name" class="input-field mb-3" placeholder="Введите название...">

                <label class="block mb-2 text-sm text-gray-400">Дата окончания</label>
                <input type="date" id="contest-date" class="input-field mb-3">

                <label class="block mb-2 text-sm text-gray-400">Приз</label>
                <input type="text" id="contest-prize" class="input-field mb-3" placeholder="Введите приз...">

                <button id="save-contest-btn" class="neon-button w-full">✅ Создать</button>
            `);

            document.getElementById("save-contest-btn").addEventListener("click", () => {
                const name = document.getElementById("contest-name").value.trim();
                const date = document.getElementById("contest-date").value.trim();
                const prize = document.getElementById("contest-prize").value.trim();

                if (!name || !date || !prize) {
                    alert("⚠️ Все поля должны быть заполнены!");
                    return;
                }

                console.log("Создан новый конкурс:", { name, date, prize });
                closeModal();
            });
        });
    }

    // ---------- 👥 Добавление администратора ----------
    const addAdminBtn = document.getElementById("add-admin-btn");
    if (addAdminBtn) {
        addAdminBtn.addEventListener("click", (e) => {
            createRipple(e);

            const id = document.getElementById("admin-id")?.value.trim();
            const username = document.getElementById("admin-username")?.value.trim();
            const channel = document.getElementById("admin-channel")?.value.trim();
            const chat = document.getElementById("admin-chat")?.value.trim();

            if (!id || !username || !channel) {
                alert("⚠️ Заполни ID, Username и ссылку на канал!");
                return;
            }

            console.log("Добавляем администратора:", { id, username, channel, chat });
            alert(`✅ Админ ${username} добавлен!`);
        });
    }

    // ---------- ⚙️ Настройки ----------
    const toggleBotBtn = document.getElementById("toggle-bot-btn");
    if (toggleBotBtn) {
        toggleBotBtn.addEventListener("click", (e) => {
            createRipple(e);
            const confirmStop = confirm("Вы уверены, что хотите выключить бота для всех пользователей?");
            if (confirmStop) {
                alert("🛠️ Бот переведён в режим технических работ");
            }
        });
    }

    // ---------- 🎨 Смена темы ----------
    const themeSelect = document.getElementById("theme-select");
    if (themeSelect) {
        themeSelect.addEventListener("change", (e) => {
            const theme = e.target.value;
            applyTheme(theme);
        });
    }

    // ---------- 👤 Профиль ----------
    const profileId = document.getElementById("profile-id");
    if (profileId) profileId.textContent = tgId;
}

// ------------------------------------------------------
// 💧 Ripple эффект (волны при клике)
// ------------------------------------------------------
function createRipple(event) {
    const button = event.currentTarget;
    const circle = document.createElement("span");
    const diameter = Math.max(button.clientWidth, button.clientHeight);
    const radius = diameter / 2;

    circle.style.width = circle.style.height = `${diameter}px`;
    circle.style.left = `${event.clientX - button.getBoundingClientRect().left - radius}px`;
    circle.style.top = `${event.clientY - button.getBoundingClientRect().top - radius}px`;
    circle.classList.add("ripple");

    const ripple = button.getElementsByClassName("ripple")[0];
    if (ripple) ripple.remove();

    button.appendChild(circle);
    setTimeout(() => circle.remove(), 600);
}

// ------------------------------------------------------
// ⚙️ Темы
// ------------------------------------------------------
function applyTheme(theme) {
    const body = document.body;
    body.classList.remove("theme-newyear", "theme-halloween", "theme-default");

    switch (theme) {
        case "newyear":
            body.classList.add("theme-newyear");
            break;
        case "halloween":
            body.classList.add("theme-halloween");
            break;
        default:
            body.classList.add("theme-default");
    }
}

// ------------------------------------------------------
// 🌫️ Анимации появления/исчезновения секций
// ------------------------------------------------------
function fadeIn(el) {
    if (!el) return;
    el.classList.remove("hidden");
    el.style.opacity = 0;
    el.style.transition = "opacity 0.4s ease";
    requestAnimationFrame(() => {
        el.style.opacity = 1;
    });
}

function fadeOut(el) {
    if (!el) return;
    el.style.transition = "opacity 0.3s ease";
    el.style.opacity = 0;
    setTimeout(() => {
        el.classList.add("hidden");
    }, 300);
}

// ------------------------------------------------------
// 🪟 Модальные окна
// ------------------------------------------------------
function openModal(title, contentHTML) {
    const modal = document.createElement("div");
    modal.className = "modal-overlay fixed inset-0 bg-black bg-opacity-80 flex items-center justify-center z-[9999]";
    modal.innerHTML = `
        <div class="modal bg-gray-800 p-6 rounded-2xl w-96 shadow-xl transform scale-95 transition-all relative z-[10000]">
            <h3 class="text-lg font-semibold mb-3 text-center">${title}</h3>
            <div class="modal-content">${contentHTML}</div>
            <button id="close-modal-btn" class="neon-button mt-4 w-full">❌ Закрыть</button>
        </div>
    `;

    // скрываем остальные секции и навигацию
    document.querySelectorAll(".content-section").forEach(s => s.classList.add("hidden"));
    const nav = document.getElementById("admin-nav");
    if (nav) nav.classList.add("hidden");

    document.body.appendChild(modal);

    // анимация появления
    setTimeout(() => {
        const modalBox = modal.querySelector(".modal");
        if (modalBox) modalBox.classList.add("scale-100");
    }, 10);

    // 🎯 фокусируемся на первом input
    setTimeout(() => {
        const firstInput = modal.querySelector("input");
        if (firstInput) firstInput.focus();
    }, 50);

    document.getElementById("close-modal-btn").addEventListener("click", () => closeModal());
}


function closeModal() {
    const modal = document.querySelector(".modal-overlay");
    if (!modal) return;

    modal.classList.remove("active");
    modal.classList.add("opacity-0");
    setTimeout(() => {
        modal.remove();
        // возвращаем интерфейс
        const activeBtn = document.querySelector(".nav-button.active");
        const targetSection = activeBtn ? activeBtn.dataset.section : "contests-section";
        const section = document.getElementById(targetSection);
        if (section) fadeIn(section);
        const nav = document.getElementById("admin-nav");
        if (nav) fadeIn(nav);
    }, 300);
}

// ------------------------------------------------------
document.addEventListener("DOMContentLoaded", checkAuth);
