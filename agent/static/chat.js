/* ═══════════════════════════════════════════════
   HAMS.AI — Chat UI Logic
   ═══════════════════════════════════════════════ */

// ── State ──────────────────────────────────────
let history = [];
let sessionId = null;
let isLoading = false;
let mode = 'chat';
let extended = false;
let currentChatId = null;

// ── Init ────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // Greeting
    const hour = new Date().getHours();
    const greetEl = document.getElementById('greetPart');
    if (greetEl) greetEl.textContent =
        hour < 12 ? 'Morning' : hour < 17 ? 'Afternoon' : 'Evening';

    // Theme
    applyTheme(localStorage.getItem('hams_theme') || 'dark');

    // Orb eyes
    initOrbEyes();

    // Auto-blink loop
    scheduleNextBlink();

    // Load sidebar history
    renderHistoryList();

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
// INTERACTIVE ORB EYES
// ═══════════════════════════════════════════════
function initOrbEyes() {
    const orbWrap = document.querySelector('.orb-wrap');
    if (!orbWrap) return;

    const pupils = document.querySelectorAll('.orb-pupil');
    const eyes = document.querySelectorAll('.orb-eye');
    if (!pupils.length) return;

    const MAX_PUPIL = 3;
    const MAX_EYE = 2;

    function trackCursor(e) {
        const orbRect = orbWrap.getBoundingClientRect();
        const cx = orbRect.left + orbRect.width / 2;
        const cy = orbRect.top + orbRect.height / 2;

        const clientX = e.clientX ?? (e.touches?.[0]?.clientX ?? cx);
        const clientY = e.clientY ?? (e.touches?.[0]?.clientY ?? cy);

        const dx = clientX - cx;
        const dy = clientY - cy;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const norm = Math.min(dist / 200, 1);

        const ex = (dx / dist) * norm * MAX_EYE;
        const ey = (dy / dist) * norm * MAX_EYE;
        eyes.forEach(eye => {
            eye.style.transform = `translate(${ex}px, ${ey}px)`;
        });

        const px = (dx / dist) * norm * MAX_PUPIL;
        const py = (dy / dist) * norm * MAX_PUPIL;
        pupils.forEach(p => {
            p.style.transform = `translate(${px}px, ${py}px)`;
        });
    }

    document.addEventListener('mousemove', trackCursor);
    document.addEventListener('touchmove', e => trackCursor(e.touches[0]), { passive: true });
}

// ── Blink ──
let blinkTimeout;
function scheduleNextBlink() {
    const delay = 2000 + Math.random() * 4000;
    blinkTimeout = setTimeout(doBlink, delay);
}
function doBlink() {
    const eyes = document.querySelectorAll('.orb-eye');
    eyes.forEach(e => {
        e.classList.add('blink');
        setTimeout(() => e.classList.remove('blink'), 160);
    });
    scheduleNextBlink();
}

// ═══════════════════════════════════════════════
// ORB 3D LIGHT ANIMATION
// ═══════════════════════════════════════════════
function initOrb3DLight() {
    const orb = document.getElementById('orb');
    if (!orb) return;

    let angle = 0;
    let speed = 0.004;
    let rx = 0.38, ry = 0.32;
    const radiusX = 0.28, radiusY = 0.22;

    // Secondary highlight
    const shine = document.createElement('div');
    shine.id = 'orbShine';
    shine.style.cssText = `
        position:absolute;
        border-radius:50%;
        pointer-events:none;
        z-index:2;
        transition:none;
        mix-blend-mode:screen;
    `;
    orb.appendChild(shine);

    // Rim light
    const rim = document.createElement('div');
    rim.id = 'orbRim';
    rim.style.cssText = `
        position:absolute;
        inset:0;
        border-radius:50%;
        pointer-events:none;
        z-index:1;
    `;
    orb.appendChild(rim);

    function tick() {
        angle += speed;

        // Light source position (circular orbit)
        rx = 0.30 + Math.cos(angle) * radiusX;
        ry = 0.22 + Math.sin(angle * 0.7) * radiusY;

        // Opposite side dim
        const dx = 1 - rx;
        const dy = 1 - ry;

        // Update main orb background — realistic 3D sphere shading
        const lightPct = Math.round(rx * 100);
        const lightPctY = Math.round(ry * 100);

        orb.style.background = `
            radial-gradient(
                circle at ${lightPct}% ${lightPctY}%,
                #ffffff    0%,
                #f5f5f5    4%,
                #e0e0e0   10%,
                #c0c0c0   22%,
                #909090   40%,
                #505050   62%,
                #141414  100%
            )
        `;

        // Primary specular highlight — small bright spot
        const hSize = 28 + Math.sin(angle * 1.3) * 6;
        const hLeft = (rx * 130) - hSize / 2;
        const hTop  = (ry * 130) - hSize / 2;
        shine.style.cssText = `
            position:absolute;
            width:${hSize}px;
            height:${hSize * 0.75}px;
            left:${hLeft}px;
            top:${hTop}px;
            border-radius:50%;
            pointer-events:none;
            z-index:9;
            background: radial-gradient(ellipse,
                rgba(255,255,255,0.9) 0%,
                rgba(255,255,255,0.4) 40%,
                transparent 70%
            );
            transform: rotate(${-30 + Math.sin(angle)*15}deg);
        `;

        // Rim light — opposite side of light source
        const rimAngle = angle + Math.PI;
        const rimX = 50 + Math.cos(rimAngle) * 40;
        const rimY = 50 + Math.sin(rimAngle * 0.7) * 35;
        rim.style.background = `
            radial-gradient(
                circle at ${rimX}% ${rimY}%,
                rgba(255,255,255,0.08) 0%,
                transparent 55%
            )
        `;

        // Subtle box-shadow glow follows light
        const glowX = (rx - 0.5) * 20;
        const glowY = (ry - 0.5) * 20;
        orb.style.boxShadow = `
            ${glowX}px ${glowY}px 40px rgba(255,255,255,0.15),
            0 0 80px rgba(180,180,180,0.08),
            inset 0 2px 4px rgba(255,255,255,0.5),
            inset 0 0 40px rgba(0,0,0,0.45)
        `;

        requestAnimationFrame(tick);
    }

    tick();
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
// MODE (Chat / Agent)
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
        .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

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

function showTyping(withThink) {
    const box = showContent();
    const row = document.createElement('div');
    row.className = 'msg-row ai';
    row.id = 'typingRow';

    const av = document.createElement('div');
    av.className = 'avatar ai';
    av.innerHTML = '<i class="bi bi-stars"></i>';

    const bubble = document.createElement('div');
    bubble.className = 'bubble ai';
    if (withThink) bubble.appendChild(buildThinkingLoading());
    const dots = document.createElement('div');
    dots.innerHTML = '<div class="typing-dot"><span></span><span></span><span></span></div>';
    bubble.appendChild(dots);

    row.appendChild(av);
    row.appendChild(bubble);
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
}

// ── Typing indicator dengan counter waktu ──────
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

    // Update status tiap detik
    let secs = 0;
    const timer = setInterval(() => {
        secs++;
        const el = document.getElementById('typingStatus');
        if (!el) { clearInterval(timer); return; }
        if (secs < 5)       el.textContent = 'Connecting...';
        else if (secs < 15) el.textContent = `Processing... (${secs}s)`;
        else if (secs < 30) el.textContent = `Model is thinking... (${secs}s)`;
        else                el.textContent = `Almost done... (${secs}s)`;
    }, 1000);

    // Simpan timer id di row agar bisa di-clear
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
    // Tampilkan typing indicator dengan timer — tetap sampai chunk pertama tiba
    showTypingWithTimer();

    const bubbleId = 'bubble-' + Date.now();
    let firstChunk = true;  // flag: typing belum dihapus

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

        // JANGAN removeTyping() di sini — tunggu chunk pertama dulu

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
                        // Chunk pertama tiba → baru hapus typing dan buat bubble
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

        // Kalau stream selesai tapi tidak ada chunk (reply kosong)
        if (firstChunk) {
            removeTyping();
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
        const isServer  = err.message.includes('500');
        const isAuth    = err.message.includes('401') || err.message.includes('403');

        let icon = '⚠️', title = 'Error', hint = '';
        if (isNetwork) { icon = '🌐'; title = 'Connection Error'; hint = 'Periksa koneksi internet kamu.'; }
        else if (isTimeout) { icon = '⏱️'; title = 'Request Timeout'; hint = 'Server terlalu lama merespons.'; }
        else if (isServer)  { icon = '🔧'; title = 'Server Error';    hint = 'Ada masalah di server, coba beberapa saat lagi.'; }
        else if (isAuth)    { icon = '🔑'; title = 'Auth Error';      hint = 'API key tidak valid.'; }

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

// ── Helper: buat bubble streaming kosong ──────────
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

// ── Helper: update bubble dengan teks terbaru ─────
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

    // Timer untuk agent
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
                    // Clear timer saat event pertama datang
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
// MAIN SEND DISPATCH
// ═══════════════════════════════════════════════
async function sendMessage() {
    const input = document.getElementById('userInput');
    const text = input.value.trim();
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

function regenMsg(btn) {
    const lastUser = [...history].reverse().find(m => m.role === 'user');
    if (!lastUser) return;
    history = history.slice(0, -1);
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
    const mod   = isMac ? e.metaKey : e.ctrlKey;
    const tag   = document.activeElement.tagName;
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
    if (mod && e.key === 'm') { e.preventDefault(); const current = document.getElementById('btnChat')?.classList.contains('active'); setMode(current ? 'agent' : 'chat'); }
    if (mod && e.key === 'e') { e.preventDefault(); toggleExtended(); }
    if (mod && e.key === 'Enter') { e.preventDefault(); sendMessage(); }
    if (mod && e.shiftKey && e.key === 'C') {
        e.preventDefault();
        const bubbles = document.querySelectorAll('.md-body');
        if (bubbles.length) { navigator.clipboard.writeText(bubbles[bubbles.length - 1].innerText); showToast('✅ Copied last response!'); }
    }
    if (e.key === 'Escape') {
        closePreview();
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