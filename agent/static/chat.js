/* ═══════════════════════════════════════════════
   HAMS.AI — Chat UI Logic (FIXED v2)
   Fixes: B1, B6, B7, B8, B19
   ═══════════════════════════════════════════════ */

// ── State ──────────────────────────────────────
let history = [];
let sessionId = null;
let isLoading = false;
let mode = 'chat';
let extended = false;
let currentChatId = null;

// ── B7 FIX: Global scope untuk orb interaction state ──
let isHovering = false;
let isNearOrb = false;

// ── Init ────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // Greeting
    const hour = new Date().getHours();
    const greetEl = document.getElementById('greetPart');
    if (greetEl) greetEl.textContent =
        hour < 12 ? 'Morning' : hour < 17 ? 'Afternoon' : 'Evening';

    // isi nama dari localStorage
    const user = JSON.parse(localStorage.getItem('hams_user') || '{}');
    const greetName = document.getElementById('greetingName');
    if (greetName && user.name) greetName.textContent = user.name;

    // Theme
    applyTheme(localStorage.getItem('hams_theme') || 'dark');

    // Load sidebar history
    renderHistoryList();

    // Initialize 3D Orb (Three.js)
    initOrb3DLight();

    initProfile();

    // Search input
    document.getElementById('searchInput')?.addEventListener('input', e => {
        const q = e.target.value.toLowerCase();
        document.querySelectorAll('.history-item').forEach(item => {
            item.style.display =
                item.querySelector('.history-title')?.textContent.toLowerCase().includes(q)
                    ? '' : 'none';
        });
    });
});

// ── Auth Guard + Google OAuth Token Extraction ──
(function () {
    // Check if returning from Google OAuth (token in URL)
    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get('token');
    const urlUser = urlParams.get('user');

    if (urlToken) {
        // Store token from Google OAuth redirect
        localStorage.setItem('hams_token', urlToken);

        if (urlUser) {
            try {
                const userData = JSON.parse(decodeURIComponent(urlUser));
                localStorage.setItem('hams_user', JSON.stringify(userData));
            } catch (e) {
                console.warn('[Auth] Failed to parse user data from URL:', e);
            }
        }

        // Clean URL (remove token/user params)
        const cleanUrl = window.location.pathname;
        window.history.replaceState({}, '', cleanUrl);
    }

    // Normal auth check
    const token = localStorage.getItem('hams_token');
    if (!token) {
        window.location.href = '/login';
        return;
    }

    // Validate token is not expired (optional but recommended)
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        const exp = payload.exp * 1000; // convert to ms
        if (Date.now() > exp) {
            localStorage.removeItem('hams_token');
            localStorage.removeItem('hams_user');
            window.location.href = '/login';
            return;
        }
    } catch (e) {
        // If token can't be decoded, let backend handle it
    }
})();

// ═══════════════════════════════════════════════
// HISTORY — localStorage helpers
// ═══════════════════════════════════════════════
const HISTORY_KEY = 'hams_chat_history';
const MAX_HISTORY = 50;

function loadAllChats() {
    try {
        return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    } catch { return []; }
}

function saveAllChats(chats) {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(chats));
}

function saveChatToHistory(title, msgs) {
    const chats = loadAllChats();
    const now = new Date();

    if (currentChatId) {
        const idx = chats.findIndex(c => c.id === currentChatId);
        if (idx !== -1) {
            chats[idx].messages = msgs;
            chats[idx].title = title;
            chats[idx].updatedAt = now.toISOString();
            saveAllChats(chats);
            renderHistoryList();
            return;
        }
    }

    currentChatId = Date.now().toString();
    chats.unshift({
        id: currentChatId,
        title,
        messages: msgs,
        createdAt: now.toISOString(),
        updatedAt: now.toISOString(),
    });
    saveAllChats(chats.slice(0, MAX_HISTORY));
    renderHistoryList();
}

function deleteChatFromHistory(id, e) {
    e.stopPropagation();
    const chats = loadAllChats().filter(c => c.id !== id);
    saveAllChats(chats);
    if (currentChatId === id) clearChat();
    else renderHistoryList();
}

function getDateLabel(isoStr) {
    const d = new Date(isoStr);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 86400 && now.getDate() === d.getDate()) return 'TODAY';
    if (diff < 172800) return 'YESTERDAY';
    const days = Math.floor(diff / 86400);
    if (days <= 7) return '7 DAYS AGO';
    return d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short' }).toUpperCase();
}

function renderHistoryList() {
    const container = document.getElementById('historyList');
    if (!container) return;

    const chats = loadAllChats();
    if (!chats.length) {
        container.innerHTML = '<div class="history-empty">Belum ada riwayat chat</div>';
        return;
    }

    const groups = {};
    chats.forEach(chat => {
        const label = getDateLabel(chat.updatedAt || chat.createdAt);
        if (!groups[label]) groups[label] = [];
        groups[label].push(chat);
    });

    container.innerHTML = '';
    Object.entries(groups).forEach(([label, items]) => {
        const groupEl = document.createElement('div');
        groupEl.className = 'history-group';
        groupEl.innerHTML = `<div class="history-date">${label}</div>`;

        items.forEach(chat => {
            const item = document.createElement('div');
            item.className = 'history-item' + (chat.id === currentChatId ? ' active' : '');
            item.dataset.id = chat.id;
            item.innerHTML = `
                <span class="history-title">${escHtml(chat.title)}</span>
                <button class="history-del" title="Hapus" onclick="deleteChatFromHistory('${chat.id}', event)">
                    <i class="bi bi-x"></i>
                </button>`;
            item.addEventListener('click', () => restoreChat(chat.id));
            groupEl.appendChild(item);
        });

        container.appendChild(groupEl);
    });
}

function restoreChat(id) {
    const chats = loadAllChats();
    const chat = chats.find(c => c.id === id);
    if (!chat) return;

    history = [];
    sessionId = null;
    currentChatId = id;

    const box = document.getElementById('chatBox');
    box.innerHTML = '';
    document.getElementById('welcome').style.display = 'none';
    box.classList.add('active');

    chat.messages.forEach(msg => {
        if (msg.role === 'user') {
            appendMsg('user', msg.content);
        } else if (msg.role === 'assistant') {
            appendMsg('ai', msg.content, msg.thinking || null);
        }
        history.push({ role: msg.role, content: msg.content });
    });

    box.scrollTop = box.scrollHeight;
    renderHistoryList();
    closeSidebar();
}

// ═══════════════════════════════════════════════
// THEME
// ═══════════════════════════════════════════════
function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('hams_theme', t);
    const icon = document.getElementById('themeIcon');
    if (icon) icon.className = t === 'dark' ? 'bi bi-sun' : 'bi bi-moon-stars';
}
function toggleTheme() {
    applyTheme(
        document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'
    );
}

// ═══════════════════════════════════════════════
// SIDEBAR (mobile)
// ═══════════════════════════════════════════════
function openSidebar() {
    document.getElementById('sidebar').classList.add('open');
    document.getElementById('sidebarOverlay').classList.add('open');
}
function closeSidebar() {
    document.getElementById('sidebar').classList.remove('open');
    document.getElementById('sidebarOverlay').classList.remove('open');
}

// ═══════════════════════════════════════════════
// SIDEBAR COLLAPSE
// ═══════════════════════════════════════════════
function toggleSidebarCollapse() {
    document.getElementById('sidebar')?.classList.toggle('collapsed');
}

// ═══════════════════════════════════════════════
// PROFESSIONAL ORB INTERACTIONS
// ═══════════════════════════════════════════════

function initProfessionalOrb() {
    const orbWrap = document.querySelector('.orb-wrap');
    const orb = document.getElementById('orb');
    if (!orbWrap || !orb) return;

    // Create magnetic cursor
    const magneticCursor = document.createElement('div');
    magneticCursor.className = 'magnetic-cursor';
    document.body.appendChild(magneticCursor);

    // Create status indicator
    const statusIndicator = document.createElement('div');
    statusIndicator.className = 'orb-status';
    orbWrap.appendChild(statusIndicator);

    // State variables — B7 FIX: use global isHovering/isNearOrb
    let mouseX = 0, mouseY = 0;
    let cursorX = 0, cursorY = 0;
    let orbCenterX = 0, orbCenterY = 0;
    let animationId = null;

    // Update orb center position
    function updateOrbCenter() {
        const rect = orbWrap.getBoundingClientRect();
        orbCenterX = rect.left + rect.width / 2;
        orbCenterY = rect.top + rect.height / 2;
    }

    // Track mouse movement
    document.addEventListener('mousemove', (e) => {
        mouseX = e.clientX;
        mouseY = e.clientY;

        const dx = mouseX - orbCenterX;
        const dy = mouseY - orbCenterY;
        const distance = Math.sqrt(dx * dx + dy * dy);

        const nearThreshold = 150;
        isNearOrb = distance < nearThreshold;   // B7: global
        isHovering = distance < 80;              // B7: global

        if (isNearOrb) {
            magneticCursor.classList.add('active');
            if (isHovering) {
                magneticCursor.classList.add('near-orb');
            } else {
                magneticCursor.classList.remove('near-orb');
            }
        } else {
            magneticCursor.classList.remove('active', 'near-orb');
        }
    });

    // Click interaction
    orbWrap.addEventListener('click', (e) => {
        e.preventDefault();

        const ripple = document.createElement('div');
        ripple.className = 'orb-ripple';
        const rect = orbWrap.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        ripple.style.left = `${x}px`;
        ripple.style.top = `${y}px`;
        ripple.style.width = '20px';
        ripple.style.height = '20px';
        orbWrap.appendChild(ripple);
        setTimeout(() => ripple.remove(), 600);

        // Eye blink on click
        const eyes = document.querySelectorAll('.orb-eye');
        eyes.forEach(eye => {
            eye.style.transition = 'transform 0.08s';
            eye.style.transform = 'scaleY(0.1)';
            setTimeout(() => { eye.style.transform = 'scaleY(1)'; }, 160);
        });

        orbWrap.style.transform = 'scale(0.92)';
        setTimeout(() => { orbWrap.style.transform = ''; }, 120);

        statusIndicator.classList.add('active');
        setTimeout(() => statusIndicator.classList.remove('active'), 2000);

        showToast('👋 HAMS AI is ready to help!');

        // 3D shockwave distortion on click
        const canvas = document.getElementById('orbCanvas');
        if (canvas && canvas._triggerShockwave) {
            canvas._triggerShockwave();
        }
    });

    // Animation loop
    function animate() {
        const dx = mouseX - cursorX;
        const dy = mouseY - cursorY;
        cursorX += dx * 0.15;
        cursorY += dy * 0.15;

        if (magneticCursor.classList.contains('active')) {
            let targetX = cursorX;
            let targetY = cursorY;

            if (isHovering) {
                const pullStrength = 0.3;
                targetX = cursorX + (orbCenterX - cursorX) * pullStrength;
                targetY = cursorY + (orbCenterY - cursorY) * pullStrength;
            }

            magneticCursor.style.left = `${targetX}px`;
            magneticCursor.style.top = `${targetY}px`;
        }

        // Eye tracking + 3D light position
        if (isNearOrb) {
            const edx = cursorX - orbCenterX;
            const edy = cursorY - orbCenterY;
            const dist = Math.sqrt(edx * edx + edy * edy) || 1;

            const maxPupil = 4;
            const norm = Math.min(dist / 200, 1);
            const px = (edx / dist) * norm * maxPupil;
            const py = (edy / dist) * norm * maxPupil;

            // 2D pupil tracking (preserved)
            document.querySelectorAll('.orb-pupil').forEach(p => {
                p.style.transform = `translate(${px}px, ${py}px)`;
            });

            // 3D light tracking — map cursor to Three.js light position
            if (orbPointLight) {
                const lightX = (edx / dist) * norm * 2.5;
                const lightY = -(edy / dist) * norm * 2.0; // invert Y for 3D
                orbPointLight.position.x = lightX;
                orbPointLight.position.y = lightY;
                orbPointLight.position.z = 2.5;
            }
        }

        animationId = requestAnimationFrame(animate);
    }

    window.addEventListener('scroll', updateOrbCenter);
    window.addEventListener('resize', updateOrbCenter);
    updateOrbCenter();
    animate();

    window.addEventListener('beforeunload', () => {
        if (animationId) cancelAnimationFrame(animationId);
    });
}

// Update orb mode — smooth 3D color transition + fallback CSS class
function updateOrbMode(newMode) {
    // Update hidden orb div for backward compat
    const orb = document.getElementById('orb');
    if (orb) {
        orb.classList.remove('chat-mode', 'agent-mode');
        orb.classList.add(`${newMode}-mode`);
    }

    // Status indicator color
    const status = document.querySelector('.orb-status');
    if (status) {
        status.style.background = newMode === 'agent' ? 'var(--green)' : 'var(--accent)';
    }

    // 3D Orb: smooth color transition via lerp in animation loop
    if (ORB_COLORS[newMode]) {
        orbTargetColors = ORB_COLORS[newMode];
        orbCurrentMode = newMode;
    }
}

// Enhanced blink with random intervals — B7 FIX: uses global isHovering
function scheduleProfessionalBlink() {
    const baseDelay = 3000 + Math.random() * 5000;
    const nextDelay = isHovering ? baseDelay * 0.7 : baseDelay;

    setTimeout(() => {
        const eyes = document.querySelectorAll('.orb-eye');
        eyes.forEach(eye => {
            eye.style.transition = 'transform 0.08s';
            eye.style.transform = 'scaleY(0.1)';
            setTimeout(() => { eye.style.transform = 'scaleY(1)'; }, 160);
        });
        scheduleProfessionalBlink();
    }, nextDelay);
}

// Breathing animation — now handled inside Three.js animate loop
// This function is kept as a no-op for backward compat (called in DOMContentLoaded)
function initBreathingAnimation() {
    // Breathing is now integrated into the Three.js animation loop
    // inside initOrb3DLight() → animate() → step 2 (breathing scale)
    // No separate requestAnimationFrame needed
}

// Initialize all professional orb features
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        initProfessionalOrb();
        scheduleProfessionalBlink();
        initBreathingAnimation();
    }, 100);
});

// ═══════════════════════════════════════════════
// MODE (Chat / Agent) — B1 FIX: SATU definisi saja + updateOrbMode()
// ═══════════════════════════════════════════════
function setMode(m) {
    mode = m;
    document.getElementById('btnChat')?.classList.toggle('active', m === 'chat');
    document.getElementById('btnAgent')?.classList.toggle('active', m === 'agent');

    const modeHint = document.getElementById('modeHint');
    if (modeHint) modeHint.textContent = m === 'agent' ? 'Agent 🤖' : 'Chat 💬';

    const input = document.getElementById('userInput');
    if (input) input.placeholder =
        m === 'agent' ? 'Describe your task for the agent...' : 'Message AI Chat...';

    const slider = document.getElementById('agentSlider');
    if (slider) slider.style.display = m === 'agent' ? 'flex' : 'none';

    updateFeatureCards(m);

    // B1 FIX: updateOrbMode() sekarang SELALU dipanggil
    updateOrbMode(m);

    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const targetNav = m === 'agent'
        ? document.getElementById('navAgent')
        : document.getElementById('navChat');
    targetNav?.classList.add('active');
}

function setModeAndFocus(m) {
    setMode(m);
    document.getElementById('userInput')?.focus();
}

function updateFeatureCards(m) {
    const container = document.getElementById('featureCards');
    if (!container) return;

    if (m === 'agent') {
        container.innerHTML = `
      <div class="feature-card" onclick="sendSuggestion('Cari informasi terbaru tentang framework JavaScript terpopuler 2025 dan buat laporan.')">
        <div class="feature-card-title"><i class="bi bi-search" style="color:var(--accent)"></i> &nbsp;Web Research</div>
        <div class="feature-card-desc">Agent mencari info terkini, menganalisis, dan menyusun laporan lengkap.</div>
      </div>
      <div class="feature-card" onclick="sendSuggestion('Buat script Python monitoring sistem: CPU, RAM, disk, simpan ke log tiap 5 menit.')">
        <div class="feature-card-title"><i class="bi bi-terminal" style="color:var(--accent)"></i> &nbsp;Run Scripts</div>
        <div class="feature-card-desc">Buat dan eksekusi script Python, Bash, otomasi sistem secara langsung.</div>
      </div>
      <div class="feature-card" onclick="sendSuggestion('Buat struktur project FastAPI lengkap: folder, config, README, dan boilerplate code.')">
        <div class="feature-card-title"><i class="bi bi-folder2-open" style="color:var(--accent)"></i> &nbsp;Project Setup</div>
        <div class="feature-card-desc">Scaffold project lengkap dengan struktur folder, config, dan dokumentasi.</div>
      </div>`;
    } else {
        container.innerHTML = `
      <div class="feature-card" onclick="sendSuggestion('Buatkan landing page modern untuk produk kopi premium, HTML CSS JS lengkap dengan animasi.')">
        <div class="feature-card-title"><i class="bi bi-globe2" style="color:var(--accent)"></i> &nbsp;Buat Website</div>
        <div class="feature-card-desc">Landing page, dashboard, UI component, dan animasi interaktif siap pakai.</div>
      </div>
      <div class="feature-card" onclick="sendSuggestion('Buatkan REST API FastAPI Python untuk todo list dengan CRUD dan auth JWT, kode lengkap.')">
        <div class="feature-card-title"><i class="bi bi-code-slash" style="color:var(--accent)"></i> &nbsp;Generate Kode</div>
        <div class="feature-card-desc">Python, JS, SQL, API — kode lengkap dengan komentar dan error handling.</div>
      </div>
      <div class="feature-card" onclick="sendSuggestion('Analisis perbandingan React vs Vue vs Svelte untuk startup kecil: performa, ekosistem, rekomendasi.')">
        <div class="feature-card-title"><i class="bi bi-bar-chart-line" style="color:var(--accent)"></i> &nbsp;Analisis &amp; Riset</div>
        <div class="feature-card-desc">Perbandingan teknologi, strategi, breakdown mendalam, dan rekomendasi aksi.</div>
      </div>`;
    }
}

// ═══════════════════════════════════════════════
// 3D ORB — Three.js Scene (replaces old CSS initOrb3DLight)
// ═══════════════════════════════════════════════

// Global Three.js orb state — accessible by other functions
let orbScene = null;
let orbCamera = null;
let orbRenderer = null;
let orbSphere = null;
let orbPointLight = null;
let orbAmbientLight = null;
let orbGlowMesh = null;
let orbAnimationId = null;

// Color palettes for mode switching
const ORB_COLORS = {
    chat: {
        base: new THREE.Color(0.85, 0.85, 0.90),   // cool white-blue
        emissive: new THREE.Color(0.15, 0.18, 0.25),   // subtle blue glow
        light: new THREE.Color(1.0, 1.0, 1.0),      // white light
        glow: new THREE.Color(0.6, 0.7, 1.0),      // blue-ish glow
        ambient: new THREE.Color(0.12, 0.12, 0.18),   // dark blue ambient
    },
    agent: {
        base: new THREE.Color(0.55, 0.95, 0.65),   // vibrant green
        emissive: new THREE.Color(0.08, 0.25, 0.12),   // green glow
        light: new THREE.Color(0.7, 1.0, 0.8),      // green-white light
        glow: new THREE.Color(0.3, 1.0, 0.5),      // green glow
        ambient: new THREE.Color(0.08, 0.18, 0.10),   // dark green ambient
    }
};

// Target colors for smooth transitions
let orbTargetColors = ORB_COLORS.chat;
let orbCurrentMode = 'chat';

function initOrb3DLight() {
    const canvas = document.getElementById('orbCanvas');
    if (!canvas || typeof THREE === 'undefined') {
        console.warn('[3D Orb] Three.js not loaded or canvas missing, skipping');
        // Fallback: show the old CSS orb
        const oldOrb = document.getElementById('orb');
        if (oldOrb) oldOrb.style.display = '';
        return;
    }

    const container = document.getElementById('orbWrap');
    if (!container) return;

    const size = container.offsetWidth || 130;

    // ── Scene ──
    orbScene = new THREE.Scene();

    // ── Camera ──
    orbCamera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    orbCamera.position.z = 3.2;

    // ── Renderer ──
    orbRenderer = new THREE.WebGLRenderer({
        canvas: canvas,
        alpha: true,
        antialias: true,
        powerPreference: 'high-performance',
    });
    orbRenderer.setSize(size, size);
    orbRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    orbRenderer.setClearColor(0x000000, 0);
    orbRenderer.toneMapping = THREE.ACESFilmicToneMapping;
    orbRenderer.toneMappingExposure = 1.2;

    // ── Sphere geometry — high segment count for smoothness ──
    const geometry = new THREE.SphereGeometry(1, 128, 128);

    // ── Material — glass-like with subsurface scattering feel ──
    const material = new THREE.MeshPhysicalMaterial({
        color: orbTargetColors.base,
        emissive: orbTargetColors.emissive,
        emissiveIntensity: 0.4,
        metalness: 0.1,
        roughness: 0.15,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
        reflectivity: 0.9,
        transparent: true,
        opacity: 0.92,
        envMapIntensity: 1.0,
    });

    orbSphere = new THREE.Mesh(geometry, material);
    orbScene.add(orbSphere);

    // ── Inner glow sphere (subsurface scattering simulation) ──
    const glowGeometry = new THREE.SphereGeometry(0.75, 64, 64);
    const glowMaterial = new THREE.MeshBasicMaterial({
        color: orbTargetColors.glow,
        transparent: true,
        opacity: 0.12,
    });
    orbGlowMesh = new THREE.Mesh(glowGeometry, glowMaterial);
    orbScene.add(orbGlowMesh);

    // ── Lights ──
    // Main point light — follows cursor for specular highlights
    orbPointLight = new THREE.PointLight(orbTargetColors.light, 2.5, 10);
    orbPointLight.position.set(1.5, 1.5, 2.5);
    orbScene.add(orbPointLight);

    // Secondary fill light — opposite side for rim lighting
    const fillLight = new THREE.PointLight(new THREE.Color(0.4, 0.4, 0.6), 0.8, 8);
    fillLight.position.set(-2, -1, 1);
    orbScene.add(fillLight);

    // Ambient light — base illumination
    orbAmbientLight = new THREE.AmbientLight(orbTargetColors.ambient, 0.6);
    orbScene.add(orbAmbientLight);

    // ── Fake environment map for reflections ──
    const envScene = new THREE.Scene();
    const envGeo = new THREE.SphereGeometry(5, 32, 32);
    const envMat = new THREE.MeshBasicMaterial({
        color: 0x111122,
        side: THREE.BackSide,
    });
    const envMesh = new THREE.Mesh(envGeo, envMat);
    envScene.add(envMesh);

    // Add some bright spots to env for reflections
    for (let i = 0; i < 6; i++) {
        const spotGeo = new THREE.SphereGeometry(0.3, 8, 8);
        const spotMat = new THREE.MeshBasicMaterial({
            color: new THREE.Color(0.5 + Math.random() * 0.5, 0.5 + Math.random() * 0.5, 0.5 + Math.random() * 0.5),
        });
        const spot = new THREE.Mesh(spotGeo, spotMat);
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.random() * Math.PI;
        spot.position.set(
            4 * Math.sin(phi) * Math.cos(theta),
            4 * Math.sin(phi) * Math.sin(theta),
            4 * Math.cos(phi)
        );
        envScene.add(spot);
    }

    const cubeRenderTarget = new THREE.WebGLCubeRenderTarget(128);
    const cubeCamera = new THREE.CubeCamera(0.1, 10, cubeRenderTarget);
    cubeCamera.update(orbRenderer, envScene);
    material.envMap = cubeRenderTarget.texture;

    // ── Animation state ──
    let time = 0;
    let shockwave = 0; // For click distortion
    const clock = new THREE.Clock();

    // ── Animation loop ──
    function animate() {
        orbAnimationId = requestAnimationFrame(animate);
        const delta = clock.getDelta();
        time += delta;

        // 1. Floating motion — sine/cosine on X, Y, Z + slight rotation
        orbSphere.position.x = Math.sin(time * 0.6) * 0.04;
        orbSphere.position.y = Math.cos(time * 0.8) * 0.06 + Math.sin(time * 0.3) * 0.02;
        orbSphere.position.z = Math.sin(time * 0.4) * 0.02;
        orbSphere.rotation.y = time * 0.15;
        orbSphere.rotation.x = Math.sin(time * 0.2) * 0.05;

        // 2. Breathing scale when idle (not hovering)
        if (!isHovering && !isNearOrb) {
            const breathScale = 1.0 + Math.sin(time * 1.2) * 0.018;
            orbSphere.scale.setScalar(breathScale);
        } else {
            // Slightly larger when hovered
            orbSphere.scale.lerp(new THREE.Vector3(1.05, 1.05, 1.05), 0.1);
        }

        // 3. Inner glow pulsing
        orbGlowMesh.position.copy(orbSphere.position);
        const glowPulse = 0.10 + Math.sin(time * 1.5) * 0.05;
        orbGlowMesh.material.opacity = glowPulse;
        const glowScale = 0.75 + Math.sin(time * 1.0) * 0.03;
        orbGlowMesh.scale.setScalar(glowScale);

        // 4. Shockwave decay (click distortion)
        // Uses cached original positions to avoid cloning geometry every frame
        if (shockwave > 0.01) {
            shockwave *= 0.92; // decay
            const positions = orbSphere.geometry.attributes.position;

            // Cache original positions on first shockwave frame
            if (!orbSphere._originalPositions) {
                orbSphere._originalPositions = new Float32Array(positions.array);
            }
            const orig = orbSphere._originalPositions;

            for (let i = 0; i < positions.count; i++) {
                const i3 = i * 3;
                const ox = orig[i3];
                const oy = orig[i3 + 1];
                const oz = orig[i3 + 2];
                const dist = Math.sqrt(ox * ox + oy * oy + oz * oz);
                const displacement = shockwave * Math.sin(dist * 12 - time * 8) * 0.08;
                positions.array[i3] = ox * (1 + displacement);
                positions.array[i3 + 1] = oy * (1 + displacement);
                positions.array[i3 + 2] = oz * (1 + displacement);
            }
            positions.needsUpdate = true;
        } else if (shockwave > 0) {
            // Reset geometry from cached original
            shockwave = 0;
            if (orbSphere._originalPositions) {
                const positions = orbSphere.geometry.attributes.position;
                positions.array.set(orbSphere._originalPositions);
                positions.needsUpdate = true;
                orbSphere._originalPositions = null;
            }
        }

        // 5. Smooth color transitions (mode switching)
        orbSphere.material.color.lerp(orbTargetColors.base, 0.03);
        orbSphere.material.emissive.lerp(orbTargetColors.emissive, 0.03);
        orbPointLight.color.lerp(orbTargetColors.light, 0.03);
        orbGlowMesh.material.color.lerp(orbTargetColors.glow, 0.03);
        orbAmbientLight.color.lerp(orbTargetColors.ambient, 0.03);

        // 6. Orbiting light source (automatic when cursor not near)
        if (!isNearOrb) {
            const lightOrbitSpeed = 0.5;
            orbPointLight.position.x = Math.cos(time * lightOrbitSpeed) * 2.0;
            orbPointLight.position.y = Math.sin(time * lightOrbitSpeed * 0.7) * 1.8;
            orbPointLight.position.z = 2.0 + Math.sin(time * lightOrbitSpeed * 0.3) * 0.5;
        }

        // 7. Render
        orbRenderer.render(orbScene, orbCamera);
    }

    animate();

    // ── Expose shockwave trigger for click handler ──
    canvas._triggerShockwave = function () {
        shockwave = 1.0;
    };

    // ── Handle resize ──
    function handleResize() {
        const newSize = container.offsetWidth || 130;
        orbRenderer.setSize(newSize, newSize);
        orbRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    }
    window.addEventListener('resize', handleResize);

    // ── Cleanup on page unload ──
    window.addEventListener('beforeunload', () => {
        if (orbAnimationId) cancelAnimationFrame(orbAnimationId);
        if (orbRenderer) orbRenderer.dispose();
    });
}

// ═══════════════════════════════════════════════
// EXTENDED THINKING
// ═══════════════════════════════════════════════
function toggleExtended() {
    extended = !extended;
    const pill = document.getElementById('extPill');
    if (pill) pill.classList.toggle('on', extended);
    const hint = document.getElementById('extHint');
    if (hint) {
        hint.textContent = extended ? 'Extended ON ✦' : 'Extended Off';
        hint.style.color = extended ? 'var(--accent)' : 'var(--text-3)';
    }
}

// ═══════════════════════════════════════════════
// TEXTAREA
// ═══════════════════════════════════════════════
function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
}
function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// ═══════════════════════════════════════════════
// MARKDOWN PARSER
// ═══════════════════════════════════════════════
function parseMarkdown(raw) {
    const codeBlocks = [];
    let text = raw.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        codeBlocks.push({ lang: lang.trim() || 'text', code: code.trim() });
        return `%%CB${codeBlocks.length - 1}%%`;
    });

    text = text
        .replace(/^### (.+)$/gm, '<hr class="section-divider"><h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<hr class="section-divider"><h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
        .replace(/^---$/gm, '<hr>')
        .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`([^`\n]+)`/g, '<code>$1</code>')
        .replace(/\$(.+?)\$\$(.+?)\$/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Tables
    text = text.replace(/((?:^\|.+\|\n?)+)/gm, tbl => {
        const rows = tbl.trim().split('\n').filter(r => !/^\|[-:\s|]+\|$/.test(r));
        if (!rows.length) return tbl;
        const hdr = rows[0].split('|').slice(1, -1).map(c => `<th>${c.trim()}</th>`).join('');
        const body = rows.slice(1).map(r =>
            '<tr>' + r.split('|').slice(1, -1).map(c => `<td>${c.trim()}</td>`).join('') + '</tr>'
        ).join('');
        return `<table><thead><tr>${hdr}</tr></thead><tbody>${body}</tbody></table>`;
    });

    // Lists
    text = text.replace(/((?:^[ \t]*[-*] .+\n?)+)/gm, b =>
        `<ul>${b.trim().split('\n').map(l => `<li>${l.replace(/^[ \t]*[-*] /, '')}</li>`).join('')}</ul>`
    );
    text = text.replace(/((?:^[ \t]*\d+\. .+\n?)+)/gm, b =>
        `<ol>${b.trim().split('\n').map(l => `<li>${l.replace(/^[ \t]*\d+\. /, '')}</li>`).join('')}</ol>`
    );

    // Paragraphs
    text = text.split('\n\n').map(c => {
        c = c.trim();
        if (!c) return '';
        if (/^<(h[1-3]|ul|ol|table|blockquote|hr)/.test(c)) return c;
        if (/^%%CB\d+%%$/.test(c)) return c;
        return `<p>${c.replace(/\n/g, '<br>')}</p>`;
    }).join('\n');

    return text.replace(/%%CB(\d+)%%/g, (_, i) => buildCodeBlock(codeBlocks[i]));
}

function buildCodeBlock({ lang, code }) {
    const isHTML = ['html', 'htm'].includes(lang.toLowerCase());
    const esc = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const b64 = btoa(unescape(encodeURIComponent(code)));
    const prev = isHTML
        ? `<button class="code-btn preview-btn" onclick="openPreview(this)"><i class="bi bi-eye"></i> Preview</button>`
        : '';
    return `
<div class="code-block" data-b64="${b64}">
  <div class="code-header">
    <span class="code-lang">${lang}</span>
    <div class="code-actions">
      ${prev}
      <button class="code-btn" onclick="copyCode(this)">
        <i class="bi bi-clipboard"></i> Copy
      </button>
    </div>
  </div>
  <pre>${esc}</pre>
</div>`;
}

function copyCode(btn) {
    const code = decodeURIComponent(escape(atob(btn.closest('.code-block').dataset.b64)));
    navigator.clipboard.writeText(code).then(() => {
        btn.innerHTML = '<i class="bi bi-check-lg"></i> Copied!';
        setTimeout(() => btn.innerHTML = '<i class="bi bi-clipboard"></i> Copy', 2000);
    });
}

// ═══════════════════════════════════════════════
// PREVIEW MODAL
// ═══════════════════════════════════════════════
function openPreview(btn) {
    const code = decodeURIComponent(escape(atob(btn.closest('.code-block').dataset.b64)));
    document.getElementById('previewFrame').srcdoc = code;
    document.getElementById('previewModal').classList.add('open');
}
function closePreview() {
    document.getElementById('previewModal').classList.remove('open');
    document.getElementById('previewFrame').srcdoc = '';
}
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('previewModal')?.addEventListener('click', e => {
        if (e.target === document.getElementById('previewModal')) closePreview();
    });
});

// ═══════════════════════════════════════════════
// THINKING BLOCKS
// ═══════════════════════════════════════════════
function buildThinkingBlock(thinkText) {
    const wrap = document.createElement('div');
    wrap.className = 'thinking-block';

    const header = document.createElement('div');
    header.className = 'thinking-header';
    header.innerHTML = `
    <i class="bi bi-lightbulb think-icon"></i>
    <span class="think-label">Thinking</span>
    <i class="bi bi-chevron-down think-chevron"></i>`;

    const body = document.createElement('div');
    body.className = 'thinking-body';
    body.textContent = thinkText;

    header.addEventListener('click', () => {
        header.classList.toggle('open');
        body.classList.toggle('open');
    });

    wrap.appendChild(header);
    wrap.appendChild(body);
    return wrap;
}

function buildThinkingLoading() {
    const wrap = document.createElement('div');
    wrap.className = 'thinking-block';
    wrap.id = 'thinkLoading';
    wrap.innerHTML = `
    <div class="thinking-header open">
      <i class="bi bi-lightbulb think-icon"></i>
      <span class="think-label"><span class="thinking-pulse">Sedang berpikir...</span></span>
      <i class="bi bi-chevron-down think-chevron"></i>
    </div>`;
    return wrap;
}

// ═══════════════════════════════════════════════
// RENDER HELPERS
// ═══════════════════════════════════════════════
function showContent() {
    document.getElementById('welcome').style.display = 'none';
    const box = document.getElementById('chatBox');
    box.classList.add('active');
    return box;
}

function appendMsg(role, content, thinkingText) {
    const box = showContent();
    const row = document.createElement('div');
    row.className = `msg-row ${role}`;

    const av = document.createElement('div');
    av.className = `avatar ${role}`;
    av.innerHTML = role === 'ai'
        ? '<i class="bi bi-stars"></i>'
        : '<i class="bi bi-person-fill"></i>';

    const bubble = document.createElement('div');
    bubble.className = `bubble ${role}`;

    if (role === 'ai') {
        if (thinkingText) bubble.appendChild(buildThinkingBlock(thinkingText));
        const md = document.createElement('div');
        md.className = 'md-body';
        md.innerHTML = parseMarkdown(content);
        bubble.appendChild(md);

        const actions = document.createElement('div');
        actions.className = 'msg-actions';
        actions.innerHTML = `
        <button class="msg-action-btn" title="Copy" onclick="copyBubble(this)">
            <i class="bi bi-copy"></i>
        </button>
        <button class="msg-action-btn" title="Good response" onclick="this.classList.toggle('active')">
            <i class="bi bi-hand-thumbs-up"></i>
        </button>
        <button class="msg-action-btn" title="Bad response" onclick="this.classList.toggle('active')">
            <i class="bi bi-hand-thumbs-down"></i>
        </button>
        <button class="msg-action-btn" title="Share" onclick="shareMsg(this)">
            <i class="bi bi-reply-fill" style="transform:scaleX(-1);display:inline-block"></i>
        </button>
        <button class="msg-action-btn" title="Regenerate" onclick="regenMsg(this)">
            <i class="bi bi-arrow-counterclockwise"></i>
        </button>
        <button class="msg-action-btn" title="More">
            <i class="bi bi-three-dots"></i>
        </button>`;
        bubble.appendChild(actions);
    } else {
        bubble.textContent = content;
    }

    row.appendChild(av);
    row.appendChild(bubble);
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
}

function showTypingWithTimer() {
    const box = showContent();
    const row = document.createElement('div');
    row.className = 'msg-row ai';
    row.id = 'typingRow';

    const av = document.createElement('div');
    av.className = 'avatar ai';
    av.innerHTML = '<i class="bi bi-stars"></i>';

    const bubble = document.createElement('div');
    bubble.className = 'bubble ai';
    bubble.innerHTML = `
        <div class="typing-dot" id="typingDots"><span></span><span></span><span></span></div>
        <div id="typingStatus" style="font-size:12px;color:var(--text-3);margin-top:6px;">
            Connecting...
        </div>`;

    row.appendChild(av);
    row.appendChild(bubble);
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;

    let secs = 0;
    const timer = setInterval(() => {
        secs++;
        const el = document.getElementById('typingStatus');
        if (!el) { clearInterval(timer); return; }
        if (secs < 5) el.textContent = 'Connecting...';
        else if (secs < 15) el.textContent = `Processing... (${secs}s)`;
        else if (secs < 30) el.textContent = `Model is thinking... (${secs}s)`;
        else el.textContent = `Almost done... (${secs}s)`;
    }, 1000);

    row._typingTimer = timer;
}

function removeTyping() {
    const row = document.getElementById('typingRow');
    if (row) {
        if (row._typingTimer) clearInterval(row._typingTimer);
        row.remove();
    }
}

// ═══════════════════════════════════════════════
// SEND — CHAT mode (streaming)
// ═══════════════════════════════════════════════
async function sendChat(text, model) {
    showTypingWithTimer();

    const bubbleId = 'bubble-' + Date.now();
    let firstChunk = true;

    try {
        const res = await fetch('/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId, message: text,
                history, model, extended
            })
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }

        let fullReply = '';
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6).trim();
                if (raw === '[DONE]') break;

                try {
                    const parsed = JSON.parse(raw);
                    if (parsed.error) throw new Error(parsed.error);
                    if (parsed.chunk) {
                        if (firstChunk) {
                            firstChunk = false;
                            removeTyping();
                            appendMsgStreaming('ai', '', bubbleId);
                        }
                        fullReply += parsed.chunk;
                        updateStreamingBubble(bubbleId, fullReply);
                    }
                } catch (e) {
                    if (e.message && !e.message.includes('JSON')) throw e;
                }
            }
        }

        if (firstChunk) {
            removeTyping();
            // B19 FIX: Jika tidak ada chunk sama sekali, tampilkan pesan
            if (fullReply) {
                appendMsg('ai', fullReply);
            }
        }

        // B19 FIX: Tambah actions ke streaming bubble setelah selesai
        const streamBubble = document.getElementById(bubbleId);
        if (streamBubble) {
            const bubble = streamBubble.closest('.bubble');
            if (bubble && !bubble.querySelector('.msg-actions')) {
                const actions = document.createElement('div');
                actions.className = 'msg-actions';
                actions.innerHTML = `
                <button class="msg-action-btn" title="Copy" onclick="copyBubble(this)">
                    <i class="bi bi-copy"></i>
                </button>
                <button class="msg-action-btn" title="Good response" onclick="this.classList.toggle('active')">
                    <i class="bi bi-hand-thumbs-up"></i>
                </button>
                <button class="msg-action-btn" title="Bad response" onclick="this.classList.toggle('active')">
                    <i class="bi bi-hand-thumbs-down"></i>
                </button>
                <button class="msg-action-btn" title="Regenerate" onclick="regenMsg(this)">
                    <i class="bi bi-arrow-counterclockwise"></i>
                </button>`;
                bubble.appendChild(actions);
            }
        }

        const reply = fullReply || 'Tidak ada respons.';
        history.push({ role: 'user', content: text });
        history.push({ role: 'assistant', content: reply });

        const title = history.find(m => m.role === 'user')?.content?.slice(0, 50) || text.slice(0, 50);
        saveChatToHistory(title, history);

    } catch (err) {
        removeTyping();

        const isNetwork = err.message.includes('fetch') || err.message.includes('network');
        const isTimeout = err.message.includes('timeout') || err.message.includes('Timeout');
        const isServer = err.message.includes('500');
        const isAuth = err.message.includes('401') || err.message.includes('403');

        let icon = '⚠️', title = 'Error', hint = '';
        if (isNetwork) { icon = '🌐'; title = 'Connection Error'; hint = 'Periksa koneksi internet kamu.'; }
        else if (isTimeout) { icon = '⏱️'; title = 'Request Timeout'; hint = 'Server terlalu lama merespons.'; }
        else if (isServer) { icon = '🔧'; title = 'Server Error'; hint = 'Ada masalah di server, coba beberapa saat lagi.'; }
        else if (isAuth) { icon = '🔑'; title = 'Auth Error'; hint = 'API key tidak valid.'; }

        appendMsg('ai', `${icon} **${title}**\n\n${err.message}${hint ? '\n\n> ' + hint : ''}`);

        const lastRow = document.getElementById('chatBox').lastElementChild;
        if (lastRow) {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'retry-btn';
            retryBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i> Coba Lagi';
            retryBtn.onclick = () => { retryBtn.remove(); sendMessage(text); };
            lastRow.querySelector('.bubble')?.appendChild(retryBtn);
        }

        showToast(`${icon} ${title}: ${err.message}`);
    }
}

function appendMsgStreaming(role, text, id) {
    const wrap = document.createElement('div');
    wrap.className = `msg-row ${role}`;

    const av = document.createElement('div');
    av.className = `avatar ${role}`;
    av.innerHTML = '<i class="bi bi-stars"></i>';

    const bubble = document.createElement('div');
    bubble.className = `bubble ${role}`;

    const mdBody = document.createElement('div');
    mdBody.className = 'md-body';
    mdBody.id = id;
    bubble.appendChild(mdBody);

    wrap.appendChild(av);
    wrap.appendChild(bubble);
    document.getElementById('chatBox').appendChild(wrap);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function updateStreamingBubble(id, text) {
    const mdBody = document.getElementById(id);
    if (!mdBody) return;
    mdBody.innerHTML = parseMarkdown(text);
    mdBody.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

// ═══════════════════════════════════════════════
// SEND — AGENT mode
// ═══════════════════════════════════════════════
async function sendAgent(text, model) {
    const box = showContent();

    const row = document.createElement('div'); row.className = 'msg-row ai';
    const av = document.createElement('div'); av.className = 'avatar ai';
    av.innerHTML = '<i class="bi bi-stars"></i>';

    const cont = document.createElement('div'); cont.className = 'bubble ai';
    const blk = document.createElement('div'); blk.className = 'agent-block';

    const statusBanner = document.createElement('div');
    statusBanner.className = 'agent-status';
    statusBanner.innerHTML = `<div class="spinner"></div><span id="agentStatusText">Connecting... (0s)</span>`;

    let agentSecs = 0;
    const agentTimer = setInterval(() => {
        agentSecs++;
        const el = document.getElementById('agentStatusText');
        if (!el) { clearInterval(agentTimer); return; }
        el.textContent = `Processing... (${agentSecs}s)`;
    }, 1000);
    statusBanner._timer = agentTimer;

    blk.appendChild(statusBanner);
    cont.appendChild(blk);
    row.appendChild(av);
    row.appendChild(cont);
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;

    try {
        const res = await fetch('/agent/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                task: text,
                model,
                max_steps: parseInt(document.getElementById('maxStepsRange')?.value || 15),
                extended
            })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const event = JSON.parse(line.slice(6));
                    if (statusBanner._timer) {
                        clearInterval(statusBanner._timer);
                        statusBanner._timer = null;
                    }
                    handleAgentEvent(event, blk, statusBanner, text);
                    box.scrollTop = box.scrollHeight;
                } catch (_) { }
            }
        }
    } catch (err) {
        if (statusBanner._timer) clearInterval(statusBanner._timer);
        statusBanner.innerHTML =
            `<i class="bi bi-x-circle" style="color:var(--red)"></i> ${escHtml(err.message)}`;
        showToast(err.message);
    }
}

function handleAgentEvent(ev, block, statusBanner, taskText) {
    const st = document.getElementById('agentStatusText');

    if (ev.type === 'start') {
        if (st) st.textContent = `Agent berjalan dengan ${ev.model}...`;

    } else if (ev.type === 'step') {
        if (st) st.textContent = `Step ${ev.step} — ${ev.tools?.length ? 'tool call...' : 'thinking...'}`;

        const toolsSummary = ev.tools?.length
            ? ev.tools.map(t => `<span style="color:var(--yellow)">${t.name}</span>`).join(', ')
            : `<span style="color:var(--text-3)">thinking</span>`;

        const card = document.createElement('div');
        card.className = 'step-card';
        card.innerHTML = `
      <div class="step-header" onclick="this.closest('.step-card').classList.toggle('collapsed')">
        <div class="step-num">${ev.step}</div>
        <div class="step-label">Step ${ev.step}</div>
        <div class="step-tools-summary">${toolsSummary}</div>
        <i class="bi bi-chevron-down step-chevron"></i>
      </div>
      <div class="step-body">
        ${ev.thought ? `<div><div class="thought-label"><i class="bi bi-chat-square-quote"></i> Thought</div><div class="thought-section">${escHtml(ev.thought)}</div></div>` : ''}
        ${(ev.tools || []).map(t => `
          <div class="tool-call-row">
            <div class="tool-call-header">
              <i class="bi bi-tools tool-icon"></i>
              <span class="tool-name">${escHtml(t.name)}</span>
              <span class="tool-args-preview">${escHtml(JSON.stringify(t.args)).slice(0, 90)}</span>
            </div>
            ${(ev.results || []).filter(r => r.tool === t.name).map(r =>
            `<div class="tool-result-body ${r.success ? '' : 'error'}">${escHtml(r.output || r.error || '')}</div>`
        ).join('')}
          </div>`).join('')}
      </div>`;
        block.insertBefore(card, statusBanner);

    } else if (ev.type === 'final') {
        statusBanner.remove();
        const card = document.createElement('div');
        card.className = 'final-card';
        const fb = document.createElement('div');
        fb.className = 'final-body md-body';
        fb.innerHTML = parseMarkdown(ev.answer || '');
        card.innerHTML = `
      <div class="final-header">
        <i class="bi bi-check-circle-fill"></i>
        Jawaban Final &nbsp;·&nbsp; ${ev.steps_taken} steps &nbsp;·&nbsp; ${ev.duration}s
      </div>`;
        card.appendChild(fb);
        block.appendChild(card);

        history.push({ role: 'user', content: taskText || '' });
        history.push({ role: 'assistant', content: ev.answer || '' });

        const title = (taskText || ev.answer || '').slice(0, 50);
        saveChatToHistory(title, history);

    } else if (ev.type === 'error') {
        statusBanner.innerHTML =
            `<i class="bi bi-x-circle" style="color:var(--red)"></i> ${escHtml(ev.message)}`;
        showToast(ev.message);
    }
}

// ═══════════════════════════════════════════════
// MAIN SEND DISPATCH — B6 FIX: terima parameter opsional
// ═══════════════════════════════════════════════
async function sendMessage(overrideText) {
    const input = document.getElementById('userInput');
    // B6 FIX: gunakan overrideText jika ada, fallback ke input.value
    const text = overrideText || input.value.trim();
    if (!text || isLoading) return;

    showContent();
    input.value = '';
    input.style.height = 'auto';
    isLoading = true;
    document.getElementById('sendBtn').disabled = true;
    appendMsg('user', text);

    const model = document.getElementById('modelSelect').value;
    try {
        if (mode === 'agent') await sendAgent(text, model);
        else await sendChat(text, model);
    } finally {
        isLoading = false;
        document.getElementById('sendBtn').disabled = false;
        input.focus();
    }
}

function sendSuggestion(text) {
    const input = document.getElementById('userInput');
    input.value = text;
    autoResize(input);
    sendMessage();
}

function clearChat() {
    history = [];
    sessionId = null;
    currentChatId = null;
    const box = document.getElementById('chatBox');
    box.innerHTML = '';
    box.classList.remove('active');
    document.getElementById('welcome').style.display = 'flex';
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('navHome')?.classList.add('active');
    renderHistoryList();
    closeSidebar();
}

// ═══════════════════════════════════════════════
// MODEL DROPDOWN
// ═══════════════════════════════════════════════
let modelDropdownOpen = false;

function toggleModelDropdown() {
    const panel = document.getElementById('modelPanel');
    const dropdown = document.getElementById('modelDropdown');
    modelDropdownOpen = !modelDropdownOpen;
    panel.classList.toggle('open', modelDropdownOpen);
    dropdown.classList.toggle('open', modelDropdownOpen);
}

function selectModel(el) {
    event.stopPropagation();
    const value = el.dataset.value;
    const label = el.childNodes[el.childNodes.length - 1].textContent.trim();

    document.getElementById('modelSelect').value = value;
    document.getElementById('modelLabel').textContent = label;

    document.querySelectorAll('.model-option').forEach(o => o.classList.remove('selected'));
    el.classList.add('selected');

    modelDropdownOpen = false;
    document.getElementById('modelPanel').classList.remove('open');
    document.getElementById('modelDropdown').classList.remove('open');
}

document.addEventListener('click', e => {
    const dropdown = document.getElementById('modelDropdown');
    if (dropdown && !dropdown.contains(e.target)) {
        modelDropdownOpen = false;
        document.getElementById('modelPanel')?.classList.remove('open');
        dropdown.classList.remove('open');
    }
});

function copyBubble(btn) {
    const md = btn.closest('.bubble').querySelector('.md-body');
    navigator.clipboard.writeText(md.innerText).then(() => {
        btn.innerHTML = '<i class="bi bi-check-lg"></i>';
        btn.classList.add('active');
        setTimeout(() => {
            btn.innerHTML = '<i class="bi bi-copy"></i>';
            btn.classList.remove('active');
        }, 1500);
    });
}

// B6 FIX: regenMsg sekarang bekerja karena sendMessage() terima parameter
function regenMsg(btn) {
    const lastUser = [...history].reverse().find(m => m.role === 'user');
    if (!lastUser) return;
    // Hapus response terakhir dari history
    if (history.length >= 2 && history[history.length - 1].role === 'assistant') {
        history.pop(); // hapus assistant
    }
    // Hapus bubble AI terakhir dari DOM
    const allRows = document.querySelectorAll('#chatBox .msg-row.ai');
    if (allRows.length) allRows[allRows.length - 1].remove();
    // Kirim ulang dengan teks user terakhir
    sendMessage(lastUser.content);
}

function shareMsg(btn) {
    const md = btn.closest('.bubble').querySelector('.md-body');
    const text = md.innerText;
    if (navigator.share) {
        navigator.share({ text });
    } else {
        navigator.clipboard.writeText(text);
        showToast('Copied to clipboard!');
    }
}

// ═══════════════════════════════════════════════
// KEYBOARD SHORTCUTS
// ═══════════════════════════════════════════════
document.addEventListener('keydown', (e) => {
    const isMac = navigator.platform.toUpperCase().includes('MAC');
    const mod = isMac ? e.metaKey : e.ctrlKey;
    const tag = document.activeElement.tagName;
    const isTyping = tag === 'TEXTAREA' || tag === 'INPUT';

    if (mod && e.key === 'k') { e.preventDefault(); document.getElementById('searchInput')?.focus(); }
    if (mod && e.key === 'n') { e.preventDefault(); clearChat(); }
    if (mod && e.key === 'l') { e.preventDefault(); document.getElementById('userInput')?.focus(); }
    if (mod && e.key === '/') {
        e.preventDefault();
        const sidebar = document.getElementById('sidebar');
        sidebar?.classList.toggle('open');
        document.getElementById('sidebarOverlay')?.classList.toggle('open');
    }
    if (mod && e.key === 'm') { e.preventDefault(); setMode(mode === 'chat' ? 'agent' : 'chat'); }
    if (mod && e.key === 'e') { e.preventDefault(); toggleExtended(); }
    if (mod && e.key === 'Enter') { e.preventDefault(); sendMessage(); }
    if (mod && e.shiftKey && e.key === 'C') {
        e.preventDefault();
        const bubbles = document.querySelectorAll('.md-body');
        if (bubbles.length) { navigator.clipboard.writeText(bubbles[bubbles.length - 1].innerText); showToast('✅ Copied last response!'); }
    }
    if (e.key === 'Escape') {
        closePreview();
        closeHistoryModal();
        closeSettings();
        closeHelp();
        closeProfileDropdown();
        document.getElementById('sidebar')?.classList.remove('open');
        document.getElementById('sidebarOverlay')?.classList.remove('open');
        document.activeElement?.blur();
    }
    if (e.key === '?' && !isTyping) {
        e.preventDefault();
        showToast('⌨️ K=Search · N=New · L=Input · /=Sidebar · M=Mode · E=Extended · ↵=Send · ⇧C=Copy');
    }
});

// ═══════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════
function escHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.display = 'block';
    setTimeout(() => t.style.display = 'none', 3500);
}

// ═══════════════════════════════════════════════
// HISTORY MODAL
// ═══════════════════════════════════════════════
function openHistoryModal() {
    renderHistoryModal('');
    document.getElementById('historyModal').classList.add('open');
    document.getElementById('hmSearch')?.focus();
    closeSidebar();
}

function closeHistoryModal() {
    document.getElementById('historyModal').classList.remove('open');
}

function filterHistoryModal(q) {
    renderHistoryModal(q.toLowerCase());
}

function renderHistoryModal(query = '') {
    const container = document.getElementById('hmList');
    if (!container) return;

    const chats = loadAllChats().filter(c =>
        !query || c.title.toLowerCase().includes(query)
    );

    if (!chats.length) {
        container.innerHTML = `
            <div class="hmodal-empty">
                <i class="bi bi-clock-history"></i>
                ${query ? 'Tidak ada hasil untuk "' + query + '"' : 'Belum ada riwayat chat'}
            </div>`;
        return;
    }

    const groups = {};
    chats.forEach(chat => {
        const label = getDateLabel(chat.updatedAt || chat.createdAt);
        if (!groups[label]) groups[label] = [];
        groups[label].push(chat);
    });

    container.innerHTML = '';
    Object.entries(groups).forEach(([label, items]) => {
        const groupEl = document.createElement('div');
        groupEl.innerHTML = `<div class="hmodal-group-label">${label}</div>`;

        items.forEach(chat => {
            const d = new Date(chat.updatedAt || chat.createdAt);
            const timeStr = d.toLocaleString('id-ID', {
                day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
            });
            const msgCount = chat.messages?.length || 0;

            const item = document.createElement('div');
            item.className = 'hmodal-item' + (chat.id === currentChatId ? ' active' : '');
            item.innerHTML = `
                <div class="hmodal-item-title">${escHtml(chat.title)}</div>
                <div class="hmodal-item-meta">
                    <span>${timeStr}</span>
                    <span>${msgCount} pesan</span>
                </div>`;
            item.addEventListener('click', () => {
                closeHistoryModal();
                restoreChat(chat.id);
            });
            groupEl.appendChild(item);
        });

        container.appendChild(groupEl);
    });
}

// ═══════════════════════════════════════════════
// SETTINGS MODAL
// ═══════════════════════════════════════════════
function openSettings() {
    // Isi nama dari localStorage
    const user = JSON.parse(localStorage.getItem('hams_user') || '{}');
    const nameInput = document.getElementById('settingsName');
    if (nameInput) nameInput.value = user.name || '';

    // Tandai theme yang aktif
    const currentTheme = localStorage.getItem('hams_theme') || 'dark';
    document.getElementById('themeOptDark')?.classList.toggle('active', currentTheme === 'dark');
    document.getElementById('themeOptLight')?.classList.toggle('active', currentTheme === 'light');

    document.getElementById('settingsModal').classList.add('open');
    closeSidebar();
}

function closeSettings() {
    document.getElementById('settingsModal').classList.remove('open');
}

// ═══════════════════════════════════════════════
// HELP MODAL
// ═══════════════════════════════════════════════
function openHelp() {
    document.getElementById('helpModal').classList.add('open');
    closeSidebar();
}
function closeHelp() {
    document.getElementById('helpModal').classList.remove('open');
}

// ═══════════════════════════════════════════════
// PROFILE DROPDOWN
// ═══════════════════════════════════════════════
function initProfile() {
    const user = JSON.parse(localStorage.getItem('hams_user') || '{}');
    const nameEl = document.getElementById('profileName');
    const emailEl = document.getElementById('profileEmail');
    const avatarEl = document.getElementById('profileAvatar');

    if (nameEl) nameEl.textContent = user.name || 'User';
    if (emailEl) emailEl.textContent = user.email || '';

    if (avatarEl) {
        if (user.avatar_url) {
            // Google avatar — show image instead of initial
            avatarEl.innerHTML = `<img src="${user.avatar_url}" alt="${user.name || 'User'}" 
                style="width:100%;height:100%;border-radius:50%;object-fit:cover;" />`;
        } else {
            // Default — show initial letter
            avatarEl.textContent = (user.name || 'U')[0].toUpperCase();
        }
    }

    // Sync profile from server (background, non-blocking)
    syncProfileFromServer();
}

async function syncProfileFromServer() {
    const token = localStorage.getItem('hams_token');
    if (!token) return;

    try {
        const res = await fetch('/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (!res.ok) {
            if (res.status === 401) {
                // Token expired — force re-login
                localStorage.removeItem('hams_token');
                localStorage.removeItem('hams_user');
                window.location.href = '/login';
            }
            return;
        }

        const serverUser = await res.json();

        // Update localStorage with latest server data
        const currentUser = JSON.parse(localStorage.getItem('hams_user') || '{}');
        const updatedUser = {
            ...currentUser,
            id: serverUser.user_id,
            name: serverUser.name,
            username: serverUser.username,
            email: serverUser.email,
            avatar_url: serverUser.avatar_url || currentUser.avatar_url || '',
        };

        localStorage.setItem('hams_user', JSON.stringify(updatedUser));

        // Update UI if data changed
        const nameEl = document.getElementById('profileName');
        const emailEl = document.getElementById('profileEmail');
        const avatarEl = document.getElementById('profileAvatar');

        if (nameEl && nameEl.textContent !== updatedUser.name) {
            nameEl.textContent = updatedUser.name;
        }
        if (emailEl && emailEl.textContent !== updatedUser.email) {
            emailEl.textContent = updatedUser.email;
        }
        if (avatarEl && updatedUser.avatar_url) {
            avatarEl.innerHTML = `<img src="${updatedUser.avatar_url}" alt="${updatedUser.name}" 
                style="width:100%;height:100%;border-radius:50%;object-fit:cover;" />`;
        }
    } catch (e) {
        // Network error — silently ignore, use cached data
        console.warn('[Profile] Sync failed:', e.message);
    }
}

function toggleProfileDropdown() {
    const dd = document.getElementById('profileDropdown');
    if (dd) dd.classList.toggle('open');
}

function closeProfileDropdown() {
    const dd = document.getElementById('profileDropdown');
    if (dd) dd.classList.remove('open');
}

document.addEventListener('click', e => {
    const profileArea = document.querySelector('.sidebar-profile');
    const dd = document.getElementById('profileDropdown');
    if (profileArea && dd && !profileArea.contains(e.target)) {
        dd.classList.remove('open');
    }
});

function logout() {
    localStorage.removeItem('hams_token');
    localStorage.removeItem('hams_user');
    localStorage.removeItem(HISTORY_KEY);
    sessionId = null;
    currentChatId = null;
    history = [];
    window.location.href = '/login';
}

// ═══════════════════════════════════════════════
// FILE UPLOAD (placeholder)
// ═══════════════════════════════════════════════
function triggerFileUpload() {
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.txt,.py,.js,.html,.css,.json,.md,.csv';
    fileInput.onchange = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => {
            const content = ev.target.result;
            const input = document.getElementById('userInput');
            input.value += `\n\n📎 File: ${file.name}\n\`\`\`\n${content}\n\`\`\``;
            autoResize(input);
            input.focus();
            showToast(`📎 ${file.name} attached`);
        };
        reader.readAsText(file);
    };
    fileInput.click();
}

// ═══════════════════════════════════════════════
// IMAGE UPLOAD (placeholder)
// ═══════════════════════════════════════════════
function triggerImageUpload() {
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'image/*';
    fileInput.onchange = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        showToast(`🖼️ Image upload coming soon: ${file.name}`);
    };
    fileInput.click();
}

// ═══════════════════════════════════════════════
// AGENT SLIDER (max steps)
// ═══════════════════════════════════════════════
function updateStepsLabel(val) {
    const label = document.getElementById('stepsLabel');
    if (label) label.textContent = val;
}

// ═══════════════════════════════════════════════
// AURORA BACKGROUND (WebGL)
// ═══════════════════════════════════════════════
(function initAurora() {
    const canvas = document.getElementById('auroraCanvas');
    if (!canvas) return;

    const gl = canvas.getContext('webgl');
    if (!gl) return;

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        gl.viewport(0, 0, canvas.width, canvas.height);
    }
    resize();
    window.addEventListener('resize', resize);

    const vs = `attribute vec2 p;void main(){gl_Position=vec4(p,0,1);}`;
    const fs = `
    precision mediump float;
    uniform float t;
    uniform vec2 r;
    void main(){
        vec2 u=gl_FragCoord.xy/r;
        float f=sin(u.x*3.0+t)*0.5+sin(u.y*2.0+t*0.7)*0.5;
        f=smoothstep(0.0,1.0,f*0.5+0.5);
        vec3 c=mix(vec3(0.02,0.02,0.04),vec3(0.06,0.04,0.12),f);
        c+=0.015*sin(vec3(t*0.3,t*0.5+2.0,t*0.4+4.0));
        gl_FragColor=vec4(c,1);
    }`;

    function compile(src, type) {
        const s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        return s;
    }

    const prog = gl.createProgram();
    gl.attachShader(prog, compile(vs, gl.VERTEX_SHADER));
    gl.attachShader(prog, compile(fs, gl.FRAGMENT_SHADER));
    gl.linkProgram(prog);
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);

    const pLoc = gl.getAttribLocation(prog, 'p');
    gl.enableVertexAttribArray(pLoc);
    gl.vertexAttribPointer(pLoc, 2, gl.FLOAT, false, 0, 0);

    const tLoc = gl.getUniformLocation(prog, 't');
    const rLoc = gl.getUniformLocation(prog, 'r');

    function frame(now) {
        gl.uniform1f(tLoc, now * 0.001);
        gl.uniform2f(rLoc, canvas.width, canvas.height);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
})();

// ═══════════════════════════════════════════════
// NAV ITEMS
// ═══════════════════════════════════════════════
function navTo(section) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    if (section === 'home') {
        document.getElementById('navHome')?.classList.add('active');
        clearChat();
    } else if (section === 'chat') {
        document.getElementById('navChat')?.classList.add('active');
        setMode('chat');
        document.getElementById('userInput')?.focus();
    } else if (section === 'agent') {
        document.getElementById('navAgent')?.classList.add('active');
        setMode('agent');
        document.getElementById('userInput')?.focus();
    } else if (section === 'history') {
        document.getElementById('navHistory')?.classList.add('active');
        openHistoryModal();
    } else if (section === 'settings') {
        document.getElementById('navSettings')?.classList.add('active');
        openSettings();
    }

    closeSidebar();
}

// ═══════════════════════════════════════════════
// EXPORT / DOWNLOAD CHAT
// ═══════════════════════════════════════════════
function exportChat() {
    if (!history.length) {
        showToast('Tidak ada chat untuk di-export');
        return;
    }

    let text = `HAMS.AI Chat Export\n${'='.repeat(40)}\n\n`;
    history.forEach(msg => {
        const role = msg.role === 'user' ? '👤 You' : '🤖 HAMS AI';
        text += `${role}:\n${msg.content}\n\n${'─'.repeat(40)}\n\n`;
    });

    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `hams-chat-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('📥 Chat exported!');
}

// ═══════════════════════════════════════════════
// DELETE ALL HISTORY
// ═══════════════════════════════════════════════
function deleteAllHistory() {
    if (!confirm('Hapus semua riwayat chat? Tindakan ini tidak bisa dibatalkan.')) return;
    localStorage.removeItem(HISTORY_KEY);
    currentChatId = null;
    renderHistoryList();
    renderHistoryModal('');
    showToast('🗑️ Semua riwayat dihapus');
}

// ═══════════════════════════════════════════════
// SETTINGS — Theme option & Save
// ═══════════════════════════════════════════════
function setThemeOpt(t) {
    document.getElementById('themeOptDark')?.classList.toggle('active', t === 'dark');
    document.getElementById('themeOptLight')?.classList.toggle('active', t === 'light');
    applyTheme(t);
}

function saveSettings() {
    const nameInput = document.getElementById('settingsName');
    const newName = nameInput?.value.trim();

    if (newName && newName.length >= 2) {
        const user = JSON.parse(localStorage.getItem('hams_user') || '{}');
        user.name = newName;
        localStorage.setItem('hams_user', JSON.stringify(user));

        const nameEl = document.getElementById('profileName');
        if (nameEl) nameEl.textContent = newName;

        // Sync ke server juga
        const token = localStorage.getItem('hams_token');
        if (token) {
            fetch('/auth/profile', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({ name: newName })
            }).catch(() => {}); // silent fail
        }
    }

    closeSettings();
    showToast('✅ Settings saved!');
}