/* ═══════════════════════════════════════════════
   Zilf.ai — Chat UI Logic (CLEANED - NO ORB)
   ═══════════════════════════════════════════════ */

// ── State ──────────────────────────────────────
let history = [];
let sessionId = null;
let isLoading = false;
let mode = 'chat';
let extended = false;
let currentChatId = null;
let attachedFiles = [];

// ── Init ────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // Greeting
    const hour = new Date().getHours();
    const greetEl = document.getElementById('greetPart');
    if (greetEl) greetEl.textContent =
        hour < 12 ? 'Morning' : hour < 17 ? 'Afternoon' : 'Evening';

    // isi nama dari localStorage
    const user = JSON.parse(localStorage.getItem('zilf_user') || '{}');
    const greetName = document.getElementById('greetingName');
    if (greetName && user.name) greetName.textContent = user.name;

    // Theme
    applyTheme(localStorage.getItem('zilf_theme') || 'dark');

    // Load sidebar history
    renderHistoryList();

    initProfile();

    applyLanguage(currentLang);

    // Search input
    document.getElementById('searchInput')?.addEventListener('input', e => {
        const q = e.target.value.toLowerCase();
        document.querySelectorAll('.history-item').forEach(item => {
            item.style.display =
                item.querySelector('.history-title')?.textContent.toLowerCase().includes(q)
                    ? '' : 'none';
        });
    });

    // === FILE ATTACHMENT (Fase 1 + 2) ===
    const attachBtn = document.getElementById('attachBtn');
    const fileInput = document.getElementById('fileInput');

    if (attachBtn && fileInput) {
        attachBtn.addEventListener('click', () => fileInput.click());

        fileInput.addEventListener('change', async (e) => {
            const files = Array.from(e.target.files);
            if (!files.length) return;

            for (const file of files) {
                await processFile(file);
            }
            fileInput.value = ''; // reset agar bisa upload ulang file yang sama
        });
    }
});

// ── Auth Guard + Google OAuth Token Extraction ──
(function () {
    // Check if returning from Google OAuth (token in URL)
    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get('token');
    const urlUser = urlParams.get('user');

    if (urlToken) {
        // Store token from Google OAuth redirect
        localStorage.setItem('zilf_token', urlToken);

        if (urlUser) {
            try {
                const userData = JSON.parse(decodeURIComponent(urlUser));
                localStorage.setItem('zilf_user', JSON.stringify(userData));
            } catch (e) {
                console.warn('[Auth] Failed to parse user data from URL:', e);
            }
        }

        // Clean URL (remove token/user params)
        const cleanUrl = window.location.pathname;
        window.history.replaceState({}, '', cleanUrl);
    }

    // Normal auth check
    const token = localStorage.getItem('zilf_token');
    if (!token) {
        window.location.href = '/login';
        return;
    }

    // Validate token is not expired (optional but recommended)
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        const exp = payload.exp * 1000; // convert to ms
        if (Date.now() > exp) {
            localStorage.removeItem('zilf_token');
            localStorage.removeItem('zilf_user');
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
const HISTORY_KEY = 'zilf_chat_history';
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
    localStorage.setItem('zilf_theme', t);
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

function sendSuggestion(text) {
    const input = document.getElementById('userInput');
    if (!input) return;
    input.value = text;
    autoResize(input);
    input.focus();
    sendMessage();
}

function clearChat() {
    history = [];
    sessionId = null;
    currentChatId = null;
    attachedFiles = [];

    const box = document.getElementById('chatBox');
    if (box) {
        box.innerHTML = '';
        box.classList.remove('active');
    }

    const welcome = document.getElementById('welcome');
    if (welcome) welcome.style.display = '';

    renderAttachmentChips();
    renderHistoryList();

    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('navHome')?.classList.add('active');

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
    // Gunakan library "marked.js" yang sudah di-load di chat.html agar format jauh lebih rapi (Tabel, Bold, List, dll)
    if (typeof marked !== 'undefined') {
        if (!window.__markedConfigured) {
            // Konfigurasi custom code block renderer agar tombol Copy & Preview tetap berfungsi
            const renderer = new marked.Renderer();
            renderer.code = function (code, language, isEscaped) {
                return buildCodeBlock({ lang: language || 'text', code: code });
            };
            marked.setOptions({
                renderer: renderer,
                gfm: true,
                breaks: true // allow single newline rendering
            });
            window.__markedConfigured = true;
        }
        return marked.parse(raw);
    }

    // Fallback darurat (jika CDN mati): custom parser regex statis
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
        .replace(/\$(.+?)\$\$(.+?)\$/g, '<a href="$2" target="_blank">$1</a>');

    text = text.replace(/((?:^\|.+\|\n?)+)/gm, tbl => {
        const rows = tbl.trim().split('\n').filter(r => !/^\|[-:\s|]+\|$/.test(r));
        if (!rows.length) return tbl;
        const hdr = rows[0].split('|').slice(1, -1).map(c => `<th>${c.trim()}</th>`).join('');
        const body = rows.slice(1).map(r => '<tr>' + r.split('|').slice(1, -1).map(c => `<td>${c.trim()}</td>`).join('') + '</tr>').join('');
        return `<table><thead><tr>${hdr}</tr></thead><tbody>${body}</tbody></table>`;
    });

    text = text.replace(/((?:^[ \t]*[-*] .+\n?)+)/gm, b => `<ul>${b.trim().split('\n').map(l => `<li>${l.replace(/^[ \t]*[-*] /, '')}</li>`).join('')}</ul>`);
    text = text.replace(/((?:^[ \t]*\d+\. .+\n?)+)/gm, b => `<ol>${b.trim().split('\n').map(l => `<li>${l.replace(/^[ \t]*\d+\. /, '')}</li>`).join('')}</ol>`);

    text = text.split('\n\n').map(c => {
        c = c.trim();
        if (!c) return '';
        if (/^<(h[1-3]|ul|ol|table|blockquote|hr)/.test(c) || /^%%CB\d+%%$/.test(c)) return c;
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

function buildUserAvatar() {
    const user = JSON.parse(localStorage.getItem('zilf_user') || '{}');
    if (user.avatar_url) {
        const img = document.createElement('img');
        img.src = user.avatar_url;
        img.className = 'avatar-user-photo';
        img.alt = user.name || 'User';
        img.onerror = function () {
            const fallback = buildUserInitials(user);
            img.replaceWith(fallback);
        };
        return img;
    }
    return buildUserInitials(user);
}

function buildUserInitials(user) {
    const el = document.createElement('div');
    el.className = 'avatar-user-initials';
    el.textContent = (user.name || 'U')[0].toUpperCase();
    return el;
}

function buildAiAvatar() {
    const el = document.createElement('div');
    el.className = 'avatar-orb-mini';
    return el;
}

function appendMsg(role, content, thinkingText) {
    const box = showContent();
    const row = document.createElement('div');
    row.className = `msg-row ${role}`;

    const av = role === 'ai' ? buildAiAvatar() : buildUserAvatar();

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

    const av = buildAiAvatar();

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
async function sendChat(content, model) {   // content bisa string atau array
    showTypingWithTimer();
    const bubbleId = 'bubble-' + Date.now();
    let firstChunk = true;

    try {
        const res = await fetch('/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId,
                message: content,        // ← sekarang array untuk multimodal
                history,
                model,
                extended
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
            if (fullReply) {
                appendMsg('ai', fullReply);
            }
        }

        // Add actions to streaming bubble
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
        let userTextForHistory = typeof content === 'string' ? content : (Array.isArray(content) ? content.find(c => c.type === 'text')?.text || '📎 Attached files' : '📎 Attached files');

        // TRUNCATE text before saving to history to avoid localStorage QuotaExceededError (5MB limit)
        if (userTextForHistory.length > 1500) {
            userTextForHistory = userTextForHistory.substring(0, 1400) + '\n...\n[FILE CONTENT TRUNCATED DARI HISTORY]';
        }
        history.push({ role: 'user', content: userTextForHistory });
        history.push({ role: 'assistant', content: reply });

        // Simpan dulu dengan judul sementara (seperti sebelumnya)
        const tempTitle = history.find(m => m.role === 'user')?.content?.slice(0, 50) || userTextForHistory.slice(0, 50);
        saveChatToHistory(tempTitle, history);

        // === AUTO GENERATE JUDUL PINTAR ===
        if (history.length === 2) {           // hanya pada percakapan pertama
            window.generateSmartTitle();
        }

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
            let retryText = typeof content === 'string' ? content : (Array.isArray(content) ? content.find(c => c.type === 'text')?.text || '' : '');
            retryBtn.onclick = () => { retryBtn.remove(); sendMessage(retryText); };
            lastRow.querySelector('.bubble')?.appendChild(retryBtn);
        }

        showToast(`${icon} ${title}: ${err.message}`);
    }
}

function appendMsgStreaming(role, text, id) {
    const wrap = document.createElement('div');
    wrap.className = `msg-row ${role}`;

    const av = buildAiAvatar();

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

    const row = document.createElement('div');
    row.className = 'msg-row ai';

    const av = buildAiAvatar();

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

        const tempTitle = (taskText || ev.answer || '').slice(0, 50);
        saveChatToHistory(tempTitle, history);

        // === AUTO GENERATE JUDUL PINTAR ===
        if (history.length === 2) {
            window.generateSmartTitle();
        }

    } else if (ev.type === 'error') {
        statusBanner.innerHTML =
            `<i class="bi bi-x-circle" style="color:var(--red)"></i> ${escHtml(ev.message)}`;
        showToast(ev.message);
    }
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
    if (history.length >= 2 && history[history.length - 1].role === 'assistant') {
        history.pop();
    }
    const allRows = document.querySelectorAll('#chatBox .msg-row.ai');
    if (allRows.length) allRows[allRows.length - 1].remove();
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

    // Apply colors according to success/error. Using 'background' instead of 'backgroundColor' to override CSS gradients!
    t.style.fontWeight = '500';
    if (msg.includes('Gagal') || msg.includes('Error') || msg.includes('❌') || msg.includes('⚠️')) {
        t.style.background = 'linear-gradient(135deg, #ef4444, #dc2626)'; // Red for errors
        t.style.color = '#ffffff';
        t.style.border = '1px solid #b91c1c';
        t.style.boxShadow = '0 4px 16px rgba(239, 68, 68, 0.4)';
    } else if (msg.includes('berhasil') || msg.includes('✅') || msg.includes('🖼️') || msg.includes('📄') || msg.includes('📝') || msg.includes('📎')) {
        t.style.background = 'linear-gradient(135deg, #10b981, #059669)'; // Green for success
        t.style.color = '#ffffff';
        t.style.border = '1px solid #047857';
        t.style.boxShadow = '0 4px 16px rgba(16, 185, 129, 0.4)';
    } else {
        t.style.background = 'var(--surface2)'; // Default
        t.style.color = 'var(--text)';
        t.style.border = '1px solid var(--border2)';
        t.style.boxShadow = '0 4px 16px rgba(0, 0, 0, 0.3)';
    }

    t.style.display = 'block';
    setTimeout(() => {
        t.style.display = 'none';
        t.style.background = '';
        t.style.color = '';
        t.style.border = '';
        t.style.boxShadow = '';
    }, 3500);
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
    const user = JSON.parse(localStorage.getItem('zilf_user') || '{}');
    const nameInput = document.getElementById('settingsName');
    if (nameInput) nameInput.value = user.name || '';

    const currentTheme = localStorage.getItem('zilf_theme') || 'dark';
    document.getElementById('themeOptDark')?.classList.toggle('active', currentTheme === 'dark');
    document.getElementById('themeOptLight')?.classList.toggle('active', currentTheme === 'light');

    document.getElementById('settingsModal').classList.add('open');
    closeSidebar();
}

function closeSettings() {
    document.getElementById('settingsModal').classList.remove('open');
}

// ═══════════════════════════════════════════════
// i18n — LANGUAGE SYSTEM
// ═══════════════════════════════════════════════

const LANGUAGES = [
    { code: 'en-US', label: 'English (United States)', flag: '🇺🇸' },
    { code: 'id-ID', label: 'Indonesia (Indonesia)', flag: '🇮🇩' },
    { code: 'fr-FR', label: 'Français (France)', flag: '🇫🇷' },
    { code: 'de-DE', label: 'Deutsch (Deutschland)', flag: '🇩🇪' },
    { code: 'hi-IN', label: 'हिन्दी (भारत)', flag: '🇮🇳' },
    { code: 'it-IT', label: 'Italiano (Italia)', flag: '🇮🇹' },
    { code: 'ja-JP', label: '日本語 (日本)', flag: '🇯🇵' },
    { code: 'ko-KR', label: '한국어 (대한민국)', flag: '🇰🇷' },
    { code: 'pt-BR', label: 'Português (Brasil)', flag: '🇧🇷' },
    { code: 'es-419', label: 'Español (Latinoamérica)', flag: '🇪🇸' },
    { code: 'es-ES', label: 'Español (España)', flag: '🇪🇸' },
];

const TRANSLATIONS = {
    'en-US': {
        newChat: 'New chat',
        searchChats: 'Search chats',
        home: 'Home',
        chat: 'Chat',
        agent: 'Agent',
        code: 'Code',
        history: 'History',
        theme: 'Theme',
        settings: 'Settings',
        language: 'Language',
        getHelp: 'Get help',
        logout: 'Log out',
        messagePH: 'Message AI Chat...',
        agentPH: 'Describe your task for the agent...',
        extended: 'Extended',
        extOn: 'Extended ON ✦',
        extOff: 'Extended Off',
        modeHintChat: 'Chat 💬',
        modeHintAgent: 'Agent 🤖',
        inputHint: 'Enter to send · Shift+Enter new line · Mode:',
        welcomeQ: 'Can I help you with anything?',
        welcomeSub: 'Powered by ZILF-MAX — Groq & NVIDIA models',
        greetMorning: 'Morning',
        greetAfternoon: 'Afternoon',
        greetEvening: 'Evening',
        noHistory: 'No chat history yet',
        connecting: 'Connecting...',
        processing: 'Processing...',
        thinking: 'Thinking...',
        almostDone: 'Almost done...',
    },
    'id-ID': {
        newChat: 'Obrolan baru',
        searchChats: 'Cari obrolan',
        home: 'Beranda',
        chat: 'Obrolan',
        agent: 'Agen',
        code: 'Kode',
        history: 'Riwayat',
        theme: 'Tema',
        settings: 'Pengaturan',
        language: 'Bahasa',
        getHelp: 'Bantuan',
        logout: 'Keluar',
        messagePH: 'Kirim pesan ke AI...',
        agentPH: 'Deskripsikan tugasmu untuk agen...',
        extended: 'Extended',
        extOn: 'Extended ON ✦',
        extOff: 'Extended Off',
        modeHintChat: 'Obrolan 💬',
        modeHintAgent: 'Agen 🤖',
        inputHint: 'Enter kirim · Shift+Enter baris baru · Mode:',
        welcomeQ: 'Ada yang bisa saya bantu?',
        welcomeSub: 'Didukung ZILF-MAX — Model Groq & NVIDIA',
        greetMorning: 'Selamat Pagi',
        greetAfternoon: 'Selamat Siang',
        greetEvening: 'Selamat Malam',
        noHistory: 'Belum ada riwayat chat',
        connecting: 'Menghubungkan...',
        processing: 'Memproses...',
        thinking: 'Sedang berpikir...',
        almostDone: 'Hampir selesai...',
    },
    'fr-FR': {
        newChat: 'Nouvelle discussion',
        searchChats: 'Rechercher',
        home: 'Accueil',
        chat: 'Discussion',
        agent: 'Agent',
        code: 'Code',
        history: 'Historique',
        theme: 'Thème',
        settings: 'Paramètres',
        language: 'Langue',
        getHelp: 'Aide',
        logout: 'Déconnexion',
        messagePH: 'Envoyer un message...',
        agentPH: 'Décrivez votre tâche...',
        extended: 'Étendu',
        extOn: 'Étendu ACTIVÉ ✦',
        extOff: 'Étendu Désactivé',
        modeHintChat: 'Discussion 💬',
        modeHintAgent: 'Agent 🤖',
        inputHint: 'Entrée pour envoyer · Maj+Entrée nouvelle ligne · Mode :',
        welcomeQ: 'Comment puis-je vous aider ?',
        welcomeSub: 'Propulsé par ZILF-MAX — Modèles Groq & NVIDIA',
        greetMorning: 'Bonjour',
        greetAfternoon: 'Bon après-midi',
        greetEvening: 'Bonsoir',
        noHistory: 'Aucun historique de chat',
        connecting: 'Connexion...',
        processing: 'Traitement...',
        thinking: 'Réflexion...',
        almostDone: 'Presque terminé...',
    },
    'de-DE': {
        newChat: 'Neues Gespräch',
        searchChats: 'Gespräche suchen',
        home: 'Startseite',
        chat: 'Chat',
        agent: 'Agent',
        code: 'Code',
        history: 'Verlauf',
        theme: 'Design',
        settings: 'Einstellungen',
        language: 'Sprache',
        getHelp: 'Hilfe',
        logout: 'Abmelden',
        messagePH: 'Nachricht senden...',
        agentPH: 'Aufgabe beschreiben...',
        extended: 'Erweitert',
        extOn: 'Erweitert AN ✦',
        extOff: 'Erweitert Aus',
        modeHintChat: 'Chat 💬',
        modeHintAgent: 'Agent 🤖',
        inputHint: 'Enter zum Senden · Shift+Enter neue Zeile · Modus:',
        welcomeQ: 'Wie kann ich Ihnen helfen?',
        welcomeSub: 'Betrieben von ZILF-MAX — Groq & NVIDIA Modelle',
        greetMorning: 'Guten Morgen',
        greetAfternoon: 'Guten Tag',
        greetEvening: 'Guten Abend',
        noHistory: 'Noch kein Chatverlauf',
        connecting: 'Verbinden...',
        processing: 'Verarbeiten...',
        thinking: 'Denken...',
        almostDone: 'Fast fertig...',
    },
    'hi-IN': {
        newChat: 'नई बातचीत',
        searchChats: 'बातचीत खोजें',
        home: 'होम',
        chat: 'चैट',
        agent: 'एजेंट',
        code: 'कोड',
        history: 'इतिहास',
        theme: 'थीम',
        settings: 'सेटिंग्स',
        language: 'भाषा',
        getHelp: 'सहायता',
        logout: 'लॉग आउट',
        messagePH: 'संदेश भेजें...',
        agentPH: 'अपना कार्य बताएं...',
        extended: 'विस्तृत',
        extOn: 'विस्तृत चालू ✦',
        extOff: 'विस्तृत बंद',
        modeHintChat: 'चैट 💬',
        modeHintAgent: 'एजेंट 🤖',
        inputHint: 'Enter भेजें · Shift+Enter नई लाइन · मोड:',
        welcomeQ: 'मैं आपकी कैसे मदद कर सकता हूं?',
        welcomeSub: 'ZILF-MAX द्वारा संचालित — Groq & NVIDIA मॉडल',
        greetMorning: 'सुप्रभात',
        greetAfternoon: 'नमस्ते',
        greetEvening: 'शुभ संध्या',
        noHistory: 'अभी तक कोई चैट नहीं',
        connecting: 'कनेक्ट हो रहा है...',
        processing: 'प्रसंस्करण...',
        thinking: 'सोच रहा हूं...',
        almostDone: 'लगभग हो गया...',
    },
    'it-IT': {
        newChat: 'Nuova chat',
        searchChats: 'Cerca chat',
        home: 'Home',
        chat: 'Chat',
        agent: 'Agente',
        code: 'Codice',
        history: 'Cronologia',
        theme: 'Tema',
        settings: 'Impostazioni',
        language: 'Lingua',
        getHelp: 'Aiuto',
        logout: 'Esci',
        messagePH: 'Invia un messaggio...',
        agentPH: 'Descrivi il tuo compito...',
        extended: 'Esteso',
        extOn: 'Esteso ATTIVO ✦',
        extOff: 'Esteso Disattivo',
        modeHintChat: 'Chat 💬',
        modeHintAgent: 'Agente 🤖',
        inputHint: 'Invio per inviare · Shift+Invio nuova riga · Modalità:',
        welcomeQ: 'Come posso aiutarti?',
        welcomeSub: 'Alimentato da ZILF-MAX — Modelli Groq & NVIDIA',
        greetMorning: 'Buongiorno',
        greetAfternoon: 'Buon pomeriggio',
        greetEvening: 'Buonasera',
        noHistory: 'Nessuna cronologia chat',
        connecting: 'Connessione...',
        processing: 'Elaborazione...',
        thinking: 'Sto pensando...',
        almostDone: 'Quasi finito...',
    },
    'ja-JP': {
        newChat: '新しいチャット',
        searchChats: 'チャットを検索',
        home: 'ホーム',
        chat: 'チャット',
        agent: 'エージェント',
        code: 'コード',
        history: '履歴',
        theme: 'テーマ',
        settings: '設定',
        language: '言語',
        getHelp: 'ヘルプ',
        logout: 'ログアウト',
        messagePH: 'メッセージを送信...',
        agentPH: 'タスクを説明してください...',
        extended: '拡張',
        extOn: '拡張 オン ✦',
        extOff: '拡張 オフ',
        modeHintChat: 'チャット 💬',
        modeHintAgent: 'エージェント 🤖',
        inputHint: 'Enterで送信 · Shift+Enterで改行 · モード:',
        welcomeQ: '何かお手伝いできることはありますか？',
        welcomeSub: 'ZILF-MAX搭載 — Groq & NVIDIAモデル',
        greetMorning: 'おはようございます',
        greetAfternoon: 'こんにちは',
        greetEvening: 'こんばんは',
        noHistory: 'チャット履歴はまだありません',
        connecting: '接続中...',
        processing: '処理中...',
        thinking: '考え中...',
        almostDone: 'もうすぐ完了...',
    },
    'ko-KR': {
        newChat: '새 대화',
        searchChats: '대화 검색',
        home: '홈',
        chat: '채팅',
        agent: '에이전트',
        code: '코드',
        history: '기록',
        theme: '테마',
        settings: '설정',
        language: '언어',
        getHelp: '도움말',
        logout: '로그아웃',
        messagePH: '메시지 보내기...',
        agentPH: '작업을 설명해 주세요...',
        extended: '확장',
        extOn: '확장 켜짐 ✦',
        extOff: '확장 꺼짐',
        modeHintChat: '채팅 💬',
        modeHintAgent: '에이전트 🤖',
        inputHint: 'Enter로 전송 · Shift+Enter 줄바꿈 · 모드:',
        welcomeQ: '무엇을 도와드릴까요?',
        welcomeSub: 'ZILF-MAX 제공 — Groq & NVIDIA 모델',
        greetMorning: '좋은 아침이에요',
        greetAfternoon: '안녕하세요',
        greetEvening: '안녕하세요',
        noHistory: '아직 대화 기록이 없습니다',
        connecting: '연결 중...',
        processing: '처리 중...',
        thinking: '생각 중...',
        almostDone: '거의 다 됐어요...',
    },
    'pt-BR': {
        newChat: 'Nova conversa',
        searchChats: 'Pesquisar conversas',
        home: 'Início',
        chat: 'Chat',
        agent: 'Agente',
        code: 'Código',
        history: 'Histórico',
        theme: 'Tema',
        settings: 'Configurações',
        language: 'Idioma',
        getHelp: 'Ajuda',
        logout: 'Sair',
        messagePH: 'Enviar mensagem...',
        agentPH: 'Descreva sua tarefa...',
        extended: 'Estendido',
        extOn: 'Estendido ATIVO ✦',
        extOff: 'Estendido Desativo',
        modeHintChat: 'Chat 💬',
        modeHintAgent: 'Agente 🤖',
        inputHint: 'Enter para enviar · Shift+Enter nova linha · Modo:',
        welcomeQ: 'Como posso te ajudar?',
        welcomeSub: 'Desenvolvido por ZILF-MAX — Modelos Groq & NVIDIA',
        greetMorning: 'Bom dia',
        greetAfternoon: 'Boa tarde',
        greetEvening: 'Boa noite',
        noHistory: 'Nenhum histórico de chat',
        connecting: 'Conectando...',
        processing: 'Processando...',
        thinking: 'Pensando...',
        almostDone: 'Quase pronto...',
    },
    'es-419': {
        newChat: 'Nueva conversación',
        searchChats: 'Buscar conversaciones',
        home: 'Inicio',
        chat: 'Chat',
        agent: 'Agente',
        code: 'Código',
        history: 'Historial',
        theme: 'Tema',
        settings: 'Configuración',
        language: 'Idioma',
        getHelp: 'Ayuda',
        logout: 'Cerrar sesión',
        messagePH: 'Enviar mensaje...',
        agentPH: 'Describe tu tarea...',
        extended: 'Extendido',
        extOn: 'Extendido ACTIVO ✦',
        extOff: 'Extendido Inactivo',
        modeHintChat: 'Chat 💬',
        modeHintAgent: 'Agente 🤖',
        inputHint: 'Enter para enviar · Shift+Enter nueva línea · Modo:',
        welcomeQ: '¿En qué puedo ayudarte?',
        welcomeSub: 'Impulsado por ZILF-MAX — Modelos Groq & NVIDIA',
        greetMorning: 'Buenos días',
        greetAfternoon: 'Buenas tardes',
        greetEvening: 'Buenas noches',
        noHistory: 'Aún no hay historial de chat',
        connecting: 'Conectando...',
        processing: 'Procesando...',
        thinking: 'Pensando...',
        almostDone: 'Casi listo...',
    },
    'es-ES': {
        newChat: 'Nueva conversación',
        searchChats: 'Buscar conversaciones',
        home: 'Inicio',
        chat: 'Chat',
        agent: 'Agente',
        code: 'Código',
        history: 'Historial',
        theme: 'Tema',
        settings: 'Configuración',
        language: 'Idioma',
        getHelp: 'Ayuda',
        logout: 'Cerrar sesión',
        messagePH: 'Enviar mensaje...',
        agentPH: 'Describe tu tarea...',
        extended: 'Extendido',
        extOn: 'Extendido ACTIVO ✦',
        extOff: 'Extendido Inactivo',
        modeHintChat: 'Chat 💬',
        modeHintAgent: 'Agente 🤖',
        inputHint: 'Enter para enviar · Shift+Enter nueva línea · Modo:',
        welcomeQ: '¿En qué puedo ayudarte?',
        welcomeSub: 'Con tecnología ZILF-MAX — Modelos Groq & NVIDIA',
        greetMorning: 'Buenos días',
        greetAfternoon: 'Buenas tardes',
        greetEvening: 'Buenas noches',
        noHistory: 'Aún no hay historial de chat',
        connecting: 'Conectando...',
        processing: 'Procesando...',
        thinking: 'Pensando...',
        almostDone: 'Casi listo...',
    },
};

// ── Language state ──
let currentLang = localStorage.getItem('zilf_lang') || 'en-US';

function t(key) {
    const lang = TRANSLATIONS[currentLang] || TRANSLATIONS['en-US'];
    return lang[key] || TRANSLATIONS['en-US'][key] || key;
}

function applyLanguage(code) {
    currentLang = code;
    localStorage.setItem('zilf_lang', code);
    document.documentElement.lang = code.split('-')[0];

    // Update semua elemen [data-i18n]
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.dataset.i18n;
        el.textContent = t(key);
    });

    // Update placeholders
    const userInput = document.getElementById('userInput');
    if (userInput) {
        userInput.placeholder = mode === 'agent' ? t('agentPH') : t('messagePH');
    }
    const searchInput = document.getElementById('searchInput');
    if (searchInput) searchInput.placeholder = t('searchChats');

    // Update input hint
    const inputHint = document.querySelector('.input-hint');
    if (inputHint) {
        const modeSpan = document.getElementById('modeHint');
        const extSpan = document.getElementById('extHint');
        const modeText = modeSpan ? modeSpan.outerHTML : '';
        const extText = extSpan ? extSpan.outerHTML : '';
        inputHint.innerHTML =
            `${t('inputHint')} ${modeText} &nbsp;·&nbsp; ${extText}`;
    }

    // Update welcome sub
    const welcomeSub = document.querySelector('.welcome-sub');
    if (welcomeSub) welcomeSub.textContent = t('welcomeSub');

    // Update welcome question
    const welcomeQ = document.querySelector('.welcome-greeting');
    if (welcomeQ) {
        const hour = new Date().getHours();
        const greet = hour < 12 ? t('greetMorning') : hour < 17 ? t('greetAfternoon') : t('greetEvening');
        const nameEl = document.getElementById('greetingName');
        const name = nameEl ? nameEl.textContent : 'Zilf.ai';
        welcomeQ.innerHTML = `${greet}, <span class="greeting-name" id="greetingName">${name}</span>.<br />${t('welcomeQ')}`;
    }

    // Update history empty state
    const historyEmpty = document.querySelector('.history-empty');
    if (historyEmpty) historyEmpty.textContent = t('noHistory');

    // Update extended hint
    const extHint = document.getElementById('extHint');
    if (extHint) {
        extHint.textContent = extended ? t('extOn') : t('extOff');
    }

    // Update mode hint
    const modeHint = document.getElementById('modeHint');
    if (modeHint) {
        modeHint.textContent = mode === 'agent' ? t('modeHintAgent') : t('modeHintChat');
    }

    // Re-render lang menu to update checkmark
    renderLangMenu();
}

function renderLangMenu() {
    const list = document.getElementById('langMenuList');
    if (!list) return;

    list.innerHTML = LANGUAGES.map(lang => `
        <div class="lang-item ${lang.code === currentLang ? 'active' : ''}"
             onclick="selectLanguage('${lang.code}')">
            <span class="lang-item-flag">${lang.flag}</span>
            <span class="lang-item-name">${lang.label}</span>
            <i class="bi bi-check2 lang-item-check"></i>
        </div>
    `).join('');
}

function selectLanguage(code) {
    applyLanguage(code);
    closeLangMenu();
    closeProfileDropdown();
    showToast(`🌐 Language changed to ${LANGUAGES.find(l => l.code === code)?.label}`);
}

function toggleLangMenu(e) {
    e.stopPropagation();
    const menu = document.getElementById('langMenu');
    const trigger = document.getElementById('langTrigger');
    const isOpen = menu.classList.contains('open');

    if (isOpen) {
        closeLangMenu();
    } else {
        renderLangMenu();
        menu.classList.add('open');
        trigger.classList.add('active');
    }
}

function closeLangMenu() {
    document.getElementById('langMenu')?.classList.remove('open');
    document.getElementById('langTrigger')?.classList.remove('active');
}

// Close lang menu when clicking outside
document.addEventListener('click', e => {
    const menu = document.getElementById('langMenu');
    const trigger = document.getElementById('langTrigger');
    if (menu && !menu.contains(e.target) && trigger && !trigger.contains(e.target)) {
        closeLangMenu();
    }
});

// ── Init language on page load ──
// Tambahkan baris ini di dalam DOMContentLoaded, setelah initProfile():
// applyLanguage(currentLang);

// ═══════════════════════════════════════════════
// GET HELP — FAQ DATA & LOGIC
// ═══════════════════════════════════════════════

const FAQ_DATA = [
    // GENERAL
    {
        cat: 'general',
        q: 'Apa perbedaan mode Chat dan Agent?',
        a: 'Mode <code>Chat</code> adalah percakapan biasa — cepat dan ringan. Mode <code>Agent</code> menggunakan reasoning loop multi-step: agent bisa browsing, menulis file, menjalankan kode, dan mengeksekusi task kompleks secara otonom.'
    },
    {
        cat: 'general',
        q: 'Bagaimana cara memilih model AI?',
        a: 'Klik dropdown <strong>ZILF-MAX</strong> di topbar. Tersedia ZILF-MAX (routing otomatis ke 12 model), Gemini Flash (cepat & murah), Gemini Pro (paling pintar). Untuk tugas umum, ZILF-MAX sudah optimal.'
    },
    {
        cat: 'general',
        q: 'Apakah riwayat chat tersimpan?',
        a: 'Ya, riwayat disimpan di browser (<code>localStorage</code>). Klik ikon <strong>History</strong> di sidebar atau Ctrl+H untuk melihat semua chat. Riwayat akan hilang jika browser storage dibersihkan.'
    },
    {
        cat: 'general',
        q: 'Bisakah mengubah nama profil?',
        a: 'Buka <strong>Profile → Settings</strong> lalu ubah Display Name. Perubahan langsung tersimpan ke akun dan muncul di greeting.'
    },
    // FEATURES
    {
        cat: 'features',
        q: 'Apa itu Extended Thinking?',
        a: 'Extended Thinking memberi model waktu lebih untuk berpikir sebelum menjawab — cocok untuk soal matematika, logika, coding kompleks, atau analisis mendalam. Toggle di topbar, atau tekan <code>Ctrl+E</code>.'
    },
    {
        cat: 'features',
        q: 'Bagaimana cara preview kode HTML?',
        a: 'Saat AI menghasilkan kode HTML, akan muncul tombol <strong>Preview</strong> hijau di header code block. Klik untuk membuka live preview di iframe sandbox. Tekan Esc atau klik Tutup untuk menutup.'
    },
    {
        cat: 'features',
        q: 'Cara menggunakan Agent mode?',
        a: 'Switch ke mode Agent, lalu ketik task secara deskriptif — misalnya <em>"Buat REST API FastAPI dengan CRUD dan auth JWT"</em>. Agent akan merencanakan, menulis kode, dan mengeksekusi step by step. Atur max steps via slider di bawah input.'
    },
    {
        cat: 'features',
        q: 'Bisakah upload file ke chat?',
        a: 'Klik ikon <strong>paperclip</strong> di input area untuk melampirkan file teks (.py, .js, .html, .json, dll). Konten file akan dimasukkan ke konteks percakapan.'
    },
    {
        cat: 'features',
        q: 'Bagaimana cara export chat?',
        a: 'Saat ini export tersedia via keyboard shortcut <code>Ctrl+Shift+S</code> (coming soon di UI). Chat bisa di-copy manual dari bubble dengan klik ikon copy.'
    },
    // SHORTCUTS
    {
        cat: 'shortcuts',
        q: 'Apa saja keyboard shortcut yang tersedia?',
        a: '', // Rendered separately as shortcuts grid
        isShortcuts: true
    },
    // TROUBLESHOOT
    {
        cat: 'troubleshoot',
        q: 'Respons AI sangat lambat atau tidak muncul',
        a: 'Coba: (1) Refresh halaman, (2) Ganti ke model lain di dropdown, (3) Matikan Extended Thinking untuk respons lebih cepat, (4) Periksa koneksi internet. Jika masalah berlanjut, coba model Gemini Flash yang lebih ringan.'
    },
    {
        cat: 'troubleshoot',
        q: 'Muncul error "HTTP 401" atau diminta login ulang',
        a: 'Sesi kamu sudah expired. Ini normal terjadi setelah beberapa jam tidak aktif. Klik <strong>Log out</strong> lalu login kembali. Riwayat chat tetap tersimpan di browser.'
    },
    {
        cat: 'troubleshoot',
        q: 'Agent berhenti di tengah jalan',
        a: 'Agent memiliki batas maksimal langkah (default 15). Jika task terlalu kompleks, naikkan slider Max Steps hingga 30, atau pecah task menjadi beberapa bagian yang lebih spesifik.'
    },
    {
        cat: 'troubleshoot',
        q: 'Riwayat chat hilang',
        a: 'Riwayat tersimpan di localStorage browser. Kemungkinan penyebab: browser dibersihkan, mode incognito, atau storage penuh. Gunakan browser yang sama dan hindari membersihkan site data.'
    },
];

const SHORTCUTS = [
    { label: 'Kirim pesan', keys: ['Enter'] },
    { label: 'Baris baru', keys: ['Shift', 'Enter'] },
    { label: 'Chat baru', keys: ['Ctrl', 'N'] },
    { label: 'Fokus input', keys: ['Ctrl', 'L'] },
    { label: 'Buka search', keys: ['Ctrl', 'K'] },
    { label: 'Toggle sidebar', keys: ['Ctrl', '/'] },
    { label: 'Ganti mode', keys: ['Ctrl', 'M'] },
    { label: 'Extended thinking', keys: ['Ctrl', 'E'] },
    { label: 'Copy respons terakhir', keys: ['Ctrl', '⇧', 'C'] },
    { label: 'Tutup modal / blur', keys: ['Esc'] },
];

let _helpActiveCat = 'all';
let _helpQuery = '';

function openHelp() {
    document.getElementById('helpModal').classList.add('open');
    document.getElementById('helpSearchInput').value = '';
    _helpQuery = '';
    _helpActiveCat = 'all';
    // Reset tabs
    document.querySelectorAll('.help-tab').forEach(t =>
        t.classList.toggle('active', t.dataset.cat === 'all')
    );
    renderFAQ();
    closeSidebar();
}

function closeHelp() {
    document.getElementById('helpModal').classList.remove('open');
}

function switchHelpTab(el) {
    document.querySelectorAll('.help-tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    _helpActiveCat = el.dataset.cat;
    renderFAQ();
}

function filterFAQ(q) {
    _helpQuery = q.toLowerCase().trim();
    renderFAQ();
}

function renderFAQ() {
    const container = document.getElementById('faqList');
    if (!container) return;

    let items = FAQ_DATA.filter(f => {
        const catMatch = _helpActiveCat === 'all' || f.cat === _helpActiveCat;
        const queryMatch = !_helpQuery ||
            f.q.toLowerCase().includes(_helpQuery) ||
            f.a.toLowerCase().includes(_helpQuery);
        return catMatch && queryMatch;
    });

    if (!items.length) {
        container.innerHTML = `
            <div class="help-empty">
                <i class="bi bi-search"></i>
                Tidak ada hasil untuk "<strong>${_helpQuery}</strong>"
            </div>`;
        return;
    }

    // Group by category
    const groups = {};
    const CAT_META = {
        general: { label: 'Umum', icon: 'bi-info-circle' },
        features: { label: 'Fitur', icon: 'bi-stars' },
        shortcuts: { label: 'Keyboard Shortcuts', icon: 'bi-keyboard' },
        troubleshoot: { label: 'Troubleshoot', icon: 'bi-tools' },
    };

    items.forEach(f => {
        if (!groups[f.cat]) groups[f.cat] = [];
        groups[f.cat].push(f);
    });

    container.innerHTML = '';

    Object.entries(groups).forEach(([cat, catItems]) => {
        const meta = CAT_META[cat] || { label: cat, icon: 'bi-question-circle' };

        const section = document.createElement('div');

        // Section label — only show when mixing categories
        if (_helpActiveCat === 'all') {
            section.innerHTML = `
                <div class="faq-section-label">
                    <i class="bi ${meta.icon}"></i>
                    ${meta.label}
                </div>`;
        }

        catItems.forEach(f => {
            if (f.isShortcuts) {
                // Render shortcuts grid instead of FAQ item
                const grid = document.createElement('div');
                grid.className = 'shortcuts-grid';
                grid.innerHTML = SHORTCUTS.map(s => `
                    <div class="shortcut-row">
                        <span class="shortcut-label">${s.label}</span>
                        <div class="shortcut-keys">
                            ${s.keys.map((k, i) => `
                                ${i > 0 ? '<span class="kbd-plus">+</span>' : ''}
                                <span class="kbd">${k}</span>
                            `).join('')}
                        </div>
                    </div>
                `).join('');
                section.appendChild(grid);
                return;
            }

            const item = document.createElement('div');
            item.className = 'faq-item';
            item.innerHTML = `
                <div class="faq-q" onclick="toggleFAQ(this)">
                    ${f.q}
                    <i class="bi bi-chevron-down faq-chevron"></i>
                </div>
                <div class="faq-a">${f.a}</div>`;
            section.appendChild(item);
        });

        container.appendChild(section);
    });
}

function toggleFAQ(el) {
    const item = el.closest('.faq-item');
    const wasOpen = item.classList.contains('open');
    // Close all
    document.querySelectorAll('.faq-item.open').forEach(i => i.classList.remove('open'));
    // Toggle clicked
    if (!wasOpen) item.classList.add('open');
}

// ═══════════════════════════════════════════════
// PROFILE DROPDOWN
// ═══════════════════════════════════════════════
function initProfile() {
    const user = JSON.parse(localStorage.getItem('zilf_user') || '{}');
    const nameEl = document.getElementById('profileName');
    const emailEl = document.getElementById('profileEmail');
    const avatarEl = document.getElementById('profileAvatar');

    if (nameEl) nameEl.textContent = user.name || 'User';
    if (emailEl) emailEl.textContent = user.email || '';

    if (avatarEl) {
        if (user.avatar_url) {
            avatarEl.innerHTML = `<img src="${user.avatar_url}" alt="${user.name || 'User'}" 
                style="width:100%;height:100%;border-radius:50%;object-fit:cover;" />`;
        } else {
            avatarEl.textContent = (user.name || 'U')[0].toUpperCase();
        }
    }

    syncProfileFromServer();
}

async function syncProfileFromServer() {
    const token = localStorage.getItem('zilf_token');
    if (!token) return;

    try {
        const res = await fetch('/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (!res.ok) {
            if (res.status === 401) {
                localStorage.removeItem('zilf_token');
                localStorage.removeItem('zilf_user');
                window.location.href = '/login';
            }
            return;
        }

        const serverUser = await res.json();

        const currentUser = JSON.parse(localStorage.getItem('zilf_user') || '{}');
        const updatedUser = {
            ...currentUser,
            id: serverUser.user_id,
            name: serverUser.name,
            username: serverUser.username,
            email: serverUser.email,
            avatar_url: serverUser.avatar_url || currentUser.avatar_url || '',
        };

        localStorage.setItem('zilf_user', JSON.stringify(updatedUser));

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
    localStorage.removeItem('zilf_token');
    localStorage.removeItem('zilf_user');
    localStorage.removeItem(HISTORY_KEY);
    sessionId = null;
    currentChatId = null;
    history = [];
    window.location.href = '/login';
}

// ═══════════════════════════════════════════════
// FILE UPLOAD (placeholder)
// ═══════════════════════════════════════════════
// function triggerFileUpload() {
//     const fileInput = document.createElement('input');
//     fileInput.type = 'file';
//     fileInput.accept = '.txt,.py,.js,.html,.css,.json,.md,.csv';
//     fileInput.onchange = (e) => {
//         const file = e.target.files[0];
//         if (!file) return;
//         const reader = new FileReader();
//         reader.onload = (ev) => {
//             const content = ev.target.result;
//             const input = document.getElementById('userInput');
//             input.value += `\n\n📎 File: ${file.name}\n\`\`\`\n${content}\n\`\`\``;
//             autoResize(input);
//             input.focus();
//             showToast(`📎 ${file.name} attached`);
//         };
//         reader.readAsText(file);
//     };
//     fileInput.click();
// }

// ═══════════════════════════════════════════════
// IMAGE UPLOAD (placeholder)
// ═══════════════════════════════════════════════
// function triggerImageUpload() {
//     const fileInput = document.createElement('input');
//     fileInput.type = 'file';
//     fileInput.accept = 'image/*';
//     fileInput.onchange = (e) => {
//         const file = e.target.files[0];
//         if (!file) return;
//         showToast(`🖼️ Image upload coming soon: ${file.name}`);
//     };
//     fileInput.click();
// }

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

    let text = `Zilf.ai Chat Export\n${'='.repeat(40)}\n\n`;
    history.forEach(msg => {
        const role = msg.role === 'user' ? '👤 You' : '🤖 Zilf AI';
        text += `${role}:\n${msg.content}\n\n${'─'.repeat(40)}\n\n`;
    });

    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `zilf-chat-${new Date().toISOString().slice(0, 10)}.txt`;
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


// ═══════════════════════════════════════════════
// FILE ATTACHMENT SYSTEM
// ═══════════════════════════════════════════════

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB

async function processFile(file) {
    if (file.size > MAX_FILE_SIZE) {
        showToast(`❌ File terlalu besar: ${file.name} (max 10MB)`);
        return;
    }

    const ext = file.name.split('.').pop().toLowerCase();

    if (['txt', 'js', 'py', 'html', 'css', 'json', 'md'].includes(ext)) {
        await processTextFile(file);
    } else if (ext === 'pdf') {
        await processPDF(file);
    } else if (ext === 'docx' || ext === 'doc') {
        await processDOCX(file);
    } else if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) {
        await processImage(file);
    } else if (['mp4', 'mov', 'webm'].includes(ext)) {
        showToast(`🎥 Video support belum di-support model API untuk saat ini`);
    } else {
        showToast(`⚠️ Format belum didukung: .${ext}`);
    }
}

async function processImage(file) {
    const fileId = Date.now().toString() + Math.random().toString();
    attachedFiles.push({ id: fileId, type: 'image', name: file.name, size: file.size, loading: true });
    renderAttachmentChips();

    return new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const idx = attachedFiles.findIndex(f => f.id === fileId);
            if (idx !== -1) {
                attachedFiles[idx] = { id: fileId, type: 'image', name: file.name, size: file.size, base64: e.target.result, loading: false };
                renderAttachmentChips();
                showToast(`🖼️ ${file.name} berhasil di-attach`);
            }
            resolve();
        };
        reader.onerror = () => {
            const idx = attachedFiles.findIndex(f => f.id === fileId);
            if (idx !== -1) {
                attachedFiles[idx].error = true;
                attachedFiles[idx].loading = false;
                renderAttachmentChips();
                showToast(`❌ Gagal render gambar: ${file.name}`);
            }
            resolve();
        };
        reader.readAsDataURL(file);
    });
}

async function processTextFile(file) {
    const fileId = Date.now().toString() + Math.random().toString();
    attachedFiles.push({ id: fileId, type: 'text', name: file.name, size: file.size, loading: true });
    renderAttachmentChips();

    return new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const idx = attachedFiles.findIndex(f => f.id === fileId);
            if (idx !== -1) {
                attachedFiles[idx] = { id: fileId, type: 'text', name: file.name, size: file.size, content: e.target.result, loading: false };
                renderAttachmentChips();
                showToast(`📎 ${file.name} berhasil di-attach`);
            }
            resolve();
        };
        reader.readAsText(file);
    });
}

async function processPDF(file) {
    const fileId = Date.now().toString() + Math.random().toString();
    attachedFiles.push({ id: fileId, type: 'pdf', name: file.name, size: file.size, content: '', loading: true });
    renderAttachmentChips();

    try {
        const arrayBuffer = await file.arrayBuffer();
        const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
        let fullText = '';

        for (let i = 1; i <= pdf.numPages; i++) {
            const page = await pdf.getPage(i);
            const textContent = await page.getTextContent();
            fullText += textContent.items.map(item => item.str).join(' ') + '\n\n';
        }

        const idx = attachedFiles.findIndex(f => f.id === fileId);
        if (idx !== -1) {
            attachedFiles[idx] = { id: fileId, type: 'pdf', name: file.name, size: file.size, content: fullText.trim(), loading: false };
            renderAttachmentChips();
            showToast(`📄 ${file.name} berhasil di-attach`);
        }
    } catch (err) {
        const idx = attachedFiles.findIndex(f => f.id === fileId);
        if (idx !== -1) {
            attachedFiles[idx].error = true;
            attachedFiles[idx].loading = false;
            renderAttachmentChips();
            showToast(`❌ Gagal baca PDF: ${file.name}`);
        }
    }
}

async function processDOCX(file) {
    const fileId = Date.now().toString() + Math.random().toString();
    attachedFiles.push({ id: fileId, type: 'docx', name: file.name, size: file.size, content: '', loading: true });
    renderAttachmentChips();

    try {
        const arrayBuffer = await file.arrayBuffer();
        const result = await mammoth.extractRawText({ arrayBuffer });

        const idx = attachedFiles.findIndex(f => f.id === fileId);
        if (idx !== -1) {
            attachedFiles[idx] = { id: fileId, type: 'docx', name: file.name, size: file.size, content: result.value.trim(), loading: false };
            renderAttachmentChips();
            showToast(`📝 ${file.name} berhasil di-attach`);
        }
    } catch (err) {
        const idx = attachedFiles.findIndex(f => f.id === fileId);
        if (idx !== -1) {
            attachedFiles[idx].error = true;
            attachedFiles[idx].loading = false;
            renderAttachmentChips();
            showToast(`❌ Gagal baca DOCX: ${file.name}`);
        }
    }
}

// Render chips
function renderAttachmentChips() {
    const area = document.getElementById('attachmentArea');
    const chipsContainer = document.getElementById('attachmentChips');
    if (!area || !chipsContainer) return;

    chipsContainer.innerHTML = '';
    if (attachedFiles.length === 0) {
        area.classList.remove('has-files');
        return;
    }
    area.classList.add('has-files');

    attachedFiles.forEach((f, i) => {
        const chip = document.createElement('div');
        chip.className = `attachment-chip chip-${f.type} ${f.error ? 'error' : ''}`;

        let iconHTML = '';
        if (f.type === 'image' && f.base64) {
            iconHTML = `<div class="chip-icon" style="width:32px;height:32px;overflow:hidden;border-radius:6px;border:1px solid rgba(255,255,255,0.15);"><img src="${f.base64}" alt="${f.name}" style="width:100%;height:100%;object-fit:cover;"></div>`;
        } else if (f.type === 'pdf') {
            iconHTML = `<i class="bi bi-file-earmark-pdf chip-icon"></i>`;
        } else if (f.type === 'docx') {
            iconHTML = `<i class="bi bi-file-earmark-word chip-icon"></i>`;
        } else {
            iconHTML = `<i class="bi bi-file-earmark-text chip-icon"></i>`;
        }

        chip.innerHTML = `
            ${iconHTML}
            <div class="chip-content">
                <div class="chip-name">${f.name}</div>
                <div class="chip-size">${(f.size / 1024).toFixed(1)} KB</div>
            </div>
            <button class="chip-remove" onclick="removeAttachment('${f.id}'); event.stopImmediatePropagation();">×</button>
        `;
        chipsContainer.appendChild(chip);
    });
}

window.removeAttachment = function (id) {
    const idx = attachedFiles.findIndex(f => f.id === id);
    if (idx !== -1) {
        attachedFiles.splice(idx, 1);
        renderAttachmentChips();
    }
};

// ═══════════════════════════════════════════════
// MAIN SEND DISPATCH
// ═══════════════════════════════════════════════

async function sendMessage(overrideText) {
    const input = document.getElementById('userInput');
    let userText = overrideText || input.value.trim();

    if ((!userText && attachedFiles.length === 0) || isLoading) return;

    showContent();
    input.value = '';
    input.style.height = 'auto';
    isLoading = true;
    document.getElementById('sendBtn').disabled = true;

    // 1. Tampilan di User Bubble (Hanya preview nama file/icon)
    let displayText = userText || '';
    if (attachedFiles.some(f => f.type === 'image')) displayText += '\n🖼️ Attached Images';
    if (attachedFiles.some(f => f.type !== 'image')) displayText += '\n📎 Attached Files';
    appendMsg('user', displayText.trim());

    // 2. Format untuk Backend API. 
    // Backend API `message` parameter bertipe string, BUKAN array. Oleh karena itu, kita gabungkan teksnya disini.
    let fullPrompt = userText || '';
    if (attachedFiles.length > 0) {
        fullPrompt += `\n\n[FILE CONTEXT]\nBerikut adalah isi dari file-file yang dilampirkan:\n`;
        attachedFiles.forEach(f => {
            if (f.content) {
                fullPrompt += `\n--- START OF ${f.name} ---\n${f.content}\n--- END OF ${f.name} ---\n`;
            } else if (f.base64) {
                fullPrompt += `\n--- IMAGE ${f.name} ---\n(Image Base64 is omitted API limitation for string)\n`;
            }
        });
    }

    const model = document.getElementById('modelSelect').value;

    try {
        if (mode === 'agent') {
            await sendAgent(fullPrompt || "[Files attached]", model);
        } else {
            await sendChat(fullPrompt, model);   // Kirim String (Bukan Array)
        }
    } finally {
        attachedFiles = [];
        renderAttachmentChips();
        isLoading = false;
        document.getElementById('sendBtn').disabled = false;
        input.focus();
    }
}

// ═══════════════════════════════════════════════
// 3D ORB & INTERACTIVE EYES — UPDATED
// ═══════════════════════════════════════════════

(function initOrbSystem() {
    // ── 1. THREE.JS SCENE SETUP ─────────────────────
    const canvas = document.getElementById('orbCanvas');
    const container = document.getElementById('orbWrap');
    if (!canvas || !container || typeof THREE === 'undefined') return;

    const size = container.offsetWidth || 130;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.z = 3.0;

    const renderer = new THREE.WebGLRenderer({
        canvas: canvas,
        alpha: true,
        antialias: true,
        powerPreference: 'high-performance'
    });
    renderer.setSize(size, size);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;

    // ── 2. ORB MATERIAL (BLUE-TEAL → BLACK) ────────
    const geometry = new THREE.SphereGeometry(1, 64, 64);
    const material = new THREE.MeshPhysicalMaterial({
        color: 0x001e2b,      // Sangat gelap — cahaya yang bikin teal
        metalness: 0.1,
        roughness: 0.25,
        clearcoat: 1.0,
        clearcoatRoughness: 0.08,
        envMapIntensity: 0.2,
        emissive: 0x000000,   // Matikan emissive — biarkan lighting yang kerja
        emissiveIntensity: 0.0
    });

    const orb = new THREE.Mesh(geometry, material);
    scene.add(orb);

    // ── 3. INNER GLOW (BLUE-TEAL) ──────────────────
    const glowGeo = new THREE.SphereGeometry(0.92, 32, 32);
    const glowMat = new THREE.MeshBasicMaterial({
        color: 0x00c8d4,
        transparent: true,
        opacity: 0.12,        // Tipis — hanya rim glow
        side: THREE.BackSide
    });
    const glowMesh = new THREE.Mesh(glowGeo, glowMat);
    scene.add(glowMesh);

    // ── 4. LIGHTING ────────────────────────────────
    const ambient = new THREE.AmbientLight(0xffffff, 0.04);
    scene.add(ambient);

    // PointLight utama (mengikuti kursor)
    const pointLight = new THREE.PointLight(0x00c8d4, 4.5, 100);
    pointLight.position.set(-3, 3, 4);
    scene.add(pointLight);

    // Fake Environment Map
    const pmremGenerator = new THREE.PMREMGenerator(renderer);
    pmremGenerator.compileEquirectangularShader();
    const cubeRenderTarget = new THREE.WebGLCubeRenderTarget(256);
    const cubeCamera = new THREE.CubeCamera(0.1, 10, cubeRenderTarget);

    const envScene = new THREE.Scene();
    envScene.background = new THREE.Color(0x111111);
    const envLight1 = new THREE.Mesh(
        new THREE.SphereGeometry(0.5, 8, 8),
        new THREE.MeshBasicMaterial({ color: 0xffffff })
    );
    envLight1.position.set(4, 4, 4);
    envScene.add(envLight1);

    cubeCamera.update(renderer, envScene);
    material.envMap = cubeRenderTarget.texture;

    // ── 5. ANIMATION LOOP ──────────────────────────
    let time = 0;
    const clock = new THREE.Clock();
    let mouseX = 0, mouseY = 0;

    function animate() {
        requestAnimationFrame(animate);
        const delta = clock.getDelta();
        time += delta;

        // Floating
        orb.position.x = Math.sin(time * 0.5) * 0.03;
        orb.position.y = Math.cos(time * 0.7) * 0.04;
        orb.rotation.y = time * 0.2;
        orb.rotation.x = Math.sin(time * 0.3) * 0.1;

        // Light follow mouse
        const targetX = mouseX * 4.0;
        const targetY = mouseY * 4.0;
        pointLight.position.x += (targetX - pointLight.position.x) * 0.05;
        pointLight.position.y += (targetY - pointLight.position.y) * 0.05;
        pointLight.position.z = 4.0;

        // Glow pulse
        glowMesh.material.opacity = 0.12 + Math.sin(time * 1.2) * 0.05;

        renderer.render(scene, camera);
    }
    animate();

    // ── 6. EYE TRACKING (LEBIH LELUASA) ────────────
    const eyeLeft = document.getElementById('eyeLeft');
    const eyeRight = document.getElementById('eyeRight');

    // Jarak maksimal digeser (ditingkatkan dari 7 menjadi 25)
    const maxMove = 25;

    function updateEyes(e) {
        if (!e) return;
        const centerX = window.innerWidth / 2;
        const centerY = window.innerHeight / 2;

        // Hitung jarak relatif dari tengah layar
        const dx = (e.clientX - centerX) / centerX; // -1 sampai 1
        const dy = (e.clientY - centerY) / centerY; // -1 sampai 1

        // Kalikan dengan maxMove
        // Jika kursor di ujung layar, mata akan bergeser 25px
        const moveX = dx * maxMove;
        const moveY = dy * maxMove;

        const transformVal = `translate(calc(-50% + ${moveX}px), calc(-50% + ${moveY}px))`;

        if (eyeLeft) eyeLeft.style.transform = transformVal;
        if (eyeRight) eyeRight.style.transform = transformVal;
    }

    function resetEyes() {
        if (eyeLeft) eyeLeft.style.transform = 'translate(-50%, -50%)';
        if (eyeRight) eyeRight.style.transform = 'translate(-50%, -50%)';
    }

    document.addEventListener('mousemove', (e) => {
        updateEyes(e);
        // Update light target
        mouseX = (e.clientX / window.innerWidth) * 2 - 1;
        mouseY = -(e.clientY / window.innerHeight) * 2 + 1;
    });

    document.addEventListener('mouseleave', () => {
        resetEyes();
        mouseX = 0;
        mouseY = 0;
    });

    // ── 7. BLINK LOGIC ─────────────────────────────
    function triggerBlink() {
        if (eyeLeft) {
            eyeLeft.classList.add('blinking');
            setTimeout(() => eyeLeft.classList.remove('blinking'), 150);
        }
        if (eyeRight) {
            eyeRight.classList.add('blinking');
            setTimeout(() => eyeRight.classList.remove('blinking'), 150);
        }

        const nextBlink = 3000 + Math.random() * 3000;
        setTimeout(triggerBlink, nextBlink);
    }

    setTimeout(triggerBlink, 2000);

    // ── 8. RESIZE HANDLER ──────────────────────────
    window.addEventListener('resize', () => {
        const newSize = container.offsetWidth || 130;
        renderer.setSize(newSize, newSize);
    });

    // ═══════════════════════════════════════════════
    // AUTO GENERATE CHAT TITLE — FIXED
    // ═══════════════════════════════════════════════
    window.generateSmartTitle = async function () {
        if (history.length < 2 || !currentChatId) return;

        const lastUserMsg = history[history.length - 2].content || "";

        try {
            const res = await fetch('/v1/chat/generate-title', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: lastUserMsg,
                    history: history.slice(-6),
                    provider: document.getElementById('modelSelect')?.value === 'nvidia' ? 'nvidia' : 'groq'
                })
            });

            if (!res.ok) return;

            const data = await res.json();
            let smartTitle = (data.title || '').trim();

            if (!smartTitle) return;

            // Update title di history
            const chats = loadAllChats();
            const idx = chats.findIndex(c => c.id === currentChatId);
            if (idx !== -1) {
                chats[idx].title = smartTitle;
                chats[idx].updatedAt = new Date().toISOString();
                saveAllChats(chats);
                renderHistoryList();
            }

            console.log('%c[Smart Title] ' + smartTitle, 'color:#00ff9d;font-weight:bold');
        } catch (err) {
            console.warn('[generateSmartTitle] Gagal:', err.message);
        }
    };

})();