/*!
 * Brilyx AI Chat Widget (Phase 7)
 *
 * Single-file, dependency-free embeddable widget. Mounts into an isolated
 * Shadow DOM so host-page CSS/JS can never collide with it (see
 * docs/frontend-widget.md, section "Host Page Isolation").
 *
 * Embed with:
 *   <script src=".../brilyx-chatbot.js" data-api-base-url="https://api.example.com"></script>
 *
 * This file contains NO business logic (pricing, qualification, lead
 * rules, conversation intelligence) — it only calls the existing FastAPI
 * backend and renders what comes back. The backend remains the sole
 * source of truth.
 */
(function () {
  "use strict";

  if (window.__brilyxChatbotLoaded) return;
  window.__brilyxChatbotLoaded = true;

  // ---------------------------------------------------------------------
  // Configuration (read synchronously from the <script> tag's own attrs)
  // ---------------------------------------------------------------------
  var scriptEl = document.currentScript;

  function attr(name, fallback) {
    var value = scriptEl && scriptEl.getAttribute(name);
    return value === null || value === undefined || value === "" ? fallback : value;
  }

  var config = {
    apiBaseUrl: attr("data-api-base-url", "http://127.0.0.1:8000").replace(/\/+$/, ""),
    title: attr("data-title", "Brilyx AI"),
    subtitle: attr("data-subtitle", "AI & Automation Assistant"),
    maxMessageLength: parseInt(attr("data-max-message-length", "4000"), 10) || 4000,
    devMode: attr("data-dev-mode", "false") === "true",
    requestTimeoutMs: 30000,
  };

  var STORAGE_KEY = "brilyx_chatbot_conversation_id";
  var TOKEN_STORAGE_KEY = "brilyx_chatbot_session_token";

  var WELCOME_MESSAGE =
    "Hi! 👋 Welcome to Brilyx. I'm Brilyx AI. I can help you explore our AI and " +
    "automation solutions, answer questions, or help you find the right solution for your " +
    "business. What can I help you with today?";

  // Each quick action is sent as a normal visitor chat message through the
  // existing /api/chat pipeline — the backend (not this file) decides how
  // to interpret and respond to it. No canned answers live here.
  var QUICK_ACTIONS = [
    { label: "AI Chatbot", message: "I'm interested in an AI chatbot for my website." },
    { label: "WhatsApp AI", message: "I'm interested in a WhatsApp AI agent." },
    { label: "Business Automation", message: "I'm interested in business automation." },
    { label: "Website/Software", message: "I'm interested in website or software development." },
    { label: "Not sure what I need", message: "I'm not sure what I need. Can you help me figure it out?" },
  ];

  var ICONS = {
    chat:
      '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 ' +
      '4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 ' +
      '3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
    close:
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/>' +
      '<line x1="6" y1="6" x2="18" y2="18"/></svg>',
    newChat:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>',
    send:
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/>' +
      '<polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  };

  function log() {
    if (config.devMode && window.console) {
      console.log.apply(console, ["[brilyx-chatbot]"].concat(Array.prototype.slice.call(arguments)));
    }
  }

  // ---------------------------------------------------------------------
  // Tiny safe DOM builder — every node is created via createElement/
  // textContent, never via innerHTML with untrusted content. The only
  // innerHTML usages in this file are the static, author-authored SVG
  // icon strings above (never anything from the API or the visitor).
  // ---------------------------------------------------------------------
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.indexOf("on") === 0 && typeof value === "function") node.addEventListener(key.slice(2), value);
        else if (value !== false && value !== null && value !== undefined) node.setAttribute(key, value);
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // Renders AI/visitor text as safe DOM nodes. Supports only a minimal,
  // whitelisted subset (line breaks + **bold**) — never HTML, never links,
  // never anything that could carry an executable payload. This is the
  // XSS boundary (Phase 7 spec sections 19-20): treat all chat text,
  // model output included, as untrusted plain text.
  function renderFormattedText(container, text) {
    var safeText = typeof text === "string" ? text : String(text == null ? "" : text);
    var lines = safeText.split("\n");
    lines.forEach(function (line, index) {
      if (index > 0) container.appendChild(document.createElement("br"));
      var parts = line.split(/(\*\*[^*]+\*\*)/g);
      parts.forEach(function (part) {
        if (part.length > 4 && part.slice(0, 2) === "**" && part.slice(-2) === "**") {
          container.appendChild(el("strong", null, [part.slice(2, -2)]));
        } else if (part) {
          container.appendChild(document.createTextNode(part));
        }
      });
    });
  }

  // ---------------------------------------------------------------------
  // Backend API calls. Exact schemas from backend/app/routers/{chat,
  // conversations}.py — see docs/frontend-widget.md for the contract this
  // was built against. No request ever carries lead_score/lead_level/
  // lead_status/qualification fields; the backend never accepts them.
  // ---------------------------------------------------------------------
  function apiUrl(path) {
    return config.apiBaseUrl + path;
  }

  function fetchWithTimeout(url, options) {
    var controller = window.AbortController ? new AbortController() : null;
    var timeoutId = controller
      ? setTimeout(function () {
          controller.abort();
        }, config.requestTimeoutMs)
      : null;
    var finalOptions = controller ? Object.assign({}, options, { signal: controller.signal }) : options;
    return fetch(url, finalOptions).then(
      function (response) {
        if (timeoutId) clearTimeout(timeoutId);
        return response;
      },
      function (err) {
        if (timeoutId) clearTimeout(timeoutId);
        throw err;
      }
    );
  }

  function handleJsonResponse(response) {
    return response
      .json()
      .catch(function () {
        return null;
      })
      .then(function (data) {
        if (!response.ok) {
          var error = new Error("HTTP " + response.status);
          error.status = response.status;
          error.data = data;
          throw error;
        }
        return data;
      });
  }

  function apiCreateConversation() {
    return fetchWithTimeout(apiUrl("/api/conversations"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }).then(handleJsonResponse);
  }

  function apiSendChat(conversationId, message) {
    return fetchWithTimeout(apiUrl("/api/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId, message: message }),
    }).then(handleJsonResponse);
  }

  // `token` is the conversation's `session_id` (Phase 9 — see
  // docs/security.md "Conversation Access Control"): the server-issued
  // secret companion to `conversation_id` that proves this browser is the
  // one that created/owns the conversation. Sent as a custom header, never
  // in the URL (keeps it out of server access logs).
  function apiFetchConversation(conversationId, token) {
    return fetchWithTimeout(apiUrl("/api/conversations/" + encodeURIComponent(conversationId)), {
      method: "GET",
      headers: { "X-Conversation-Token": token },
    }).then(handleJsonResponse);
  }

  function apiCloseConversation(conversationId, token) {
    return fetchWithTimeout(apiUrl("/api/conversations/" + encodeURIComponent(conversationId) + "/close"), {
      method: "POST",
      headers: { "X-Conversation-Token": token },
    }).then(handleJsonResponse);
  }

  // ---------------------------------------------------------------------
  // Session persistence — sessionStorage only, and only the conversation
  // id + its access token (never message content, never lead/qualification
  // data). Message content is re-fetched from the backend (the source of
  // truth) if the page reloads within the same tab session.
  // ---------------------------------------------------------------------
  function persistConversation(id, token) {
    try {
      sessionStorage.setItem(STORAGE_KEY, id);
      sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
    } catch (e) {
      log("sessionStorage unavailable, continuing without persistence", e);
    }
  }
  function loadPersistedConversationId() {
    try {
      return sessionStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null;
    }
  }
  function loadPersistedToken() {
    try {
      return sessionStorage.getItem(TOKEN_STORAGE_KEY);
    } catch (e) {
      return null;
    }
  }
  function clearPersistedConversationId() {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
      sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  // ---------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------
  var state = {
    conversationId: null,
    sessionToken: null,
    conversationClosed: false,
    sending: false,
    open: false,
  };

  // ---------------------------------------------------------------------
  // Build the widget DOM inside an isolated Shadow DOM
  // ---------------------------------------------------------------------
  var host = document.createElement("div");
  host.id = "brilyx-chatbot-host";
  var shadow = host.attachShadow({ mode: "open" });

  var styleEl = document.createElement("style");
  styleEl.textContent = WIDGET_CSS();
  shadow.appendChild(styleEl);

  var launcherBtn = el("button", {
    type: "button",
    class: "bcw-launcher",
    "aria-label": "Open " + config.title + " chat",
    "aria-expanded": "false",
    "aria-controls": "bcw-panel",
  });
  launcherBtn.innerHTML = ICONS.chat;

  var headerTitle = el("p", { class: "bcw-header-title", text: config.title });
  var headerSubtitle = el("p", { class: "bcw-header-subtitle", text: config.subtitle });
  var newChatBtn = el("button", {
    type: "button",
    class: "bcw-icon-btn",
    "aria-label": "Start a new conversation",
    title: "New conversation",
  });
  newChatBtn.innerHTML = ICONS.newChat;
  var headerCloseBtn = el("button", {
    type: "button",
    class: "bcw-icon-btn",
    "aria-label": "Close " + config.title + " chat",
  });
  headerCloseBtn.innerHTML = ICONS.close;

  var header = el("div", { class: "bcw-header" }, [
    el("div", { class: "bcw-header-text" }, [headerTitle, headerSubtitle]),
    el("div", { class: "bcw-header-actions" }, [newChatBtn, headerCloseBtn]),
  ]);

  var messagesEl = el("div", { class: "bcw-messages", role: "log", "aria-live": "polite" });

  var typingRow = el("div", { class: "bcw-message bcw-message-assistant bcw-typing-row" }, [
    el("div", { class: "bcw-bubble bcw-typing-bubble" }, [
      el("span", { class: "bcw-typing-text" }, [config.title + " is typing"]),
      el("span", { class: "bcw-typing-dots", "aria-hidden": "true" }, [
        el("span", { class: "bcw-dot" }),
        el("span", { class: "bcw-dot" }),
        el("span", { class: "bcw-dot" }),
      ]),
    ]),
  ]);

  var quickActionsEl = el("div", { class: "bcw-quick-actions", role: "group", "aria-label": "Quick actions" });
  QUICK_ACTIONS.forEach(function (action) {
    var btn = el("button", { type: "button", class: "bcw-quick-action" }, [action.label]);
    btn.addEventListener("click", function () {
      handleQuickAction(action);
    });
    quickActionsEl.appendChild(btn);
  });

  var bannerEl = el("div", { class: "bcw-banner", hidden: "hidden" });

  var textarea = el("textarea", {
    id: "bcw-input",
    class: "bcw-input",
    rows: "1",
    maxlength: String(config.maxMessageLength),
    placeholder: "Type your message…",
    "aria-label": "Message " + config.title,
  });
  var sendBtn = el("button", {
    type: "submit",
    class: "bcw-send",
    "aria-label": "Send message",
    disabled: "disabled",
  });
  sendBtn.innerHTML = ICONS.send;
  var composerForm = el("form", { class: "bcw-composer" }, [textarea, sendBtn]);

  var panel = el(
    "section",
    {
      id: "bcw-panel",
      class: "bcw-panel",
      role: "dialog",
      "aria-modal": "false",
      "aria-label": config.title + " chat",
      hidden: "hidden",
    },
    [header, messagesEl, quickActionsEl, bannerEl, composerForm]
  );

  shadow.appendChild(launcherBtn);
  shadow.appendChild(panel);

  // ---------------------------------------------------------------------
  // Scrolling — follow new messages only if the visitor was already near
  // the bottom, so reading older messages is never interrupted.
  // ---------------------------------------------------------------------
  function isNearBottom() {
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
  }
  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendMessage(role, text) {
    var wasNearBottom = isNearBottom();
    var row = el("div", { class: "bcw-message " + (role === "user" ? "bcw-message-user" : "bcw-message-assistant") });
    var bubble = el("div", { class: "bcw-bubble" });
    renderFormattedText(bubble, text);
    row.appendChild(bubble);
    messagesEl.appendChild(row);
    if (wasNearBottom) scrollToBottom();
    return row;
  }

  function showTyping() {
    messagesEl.appendChild(typingRow);
    scrollToBottom();
  }
  function hideTyping() {
    if (typingRow.parentNode === messagesEl) messagesEl.removeChild(typingRow);
  }

  function hideQuickActions() {
    quickActionsEl.hidden = true;
  }

  // ---------------------------------------------------------------------
  // Inline banner (errors, closed-conversation notice, new-conversation
  // confirmation) — never a browser alert()/confirm().
  // ---------------------------------------------------------------------
  function hideBanner() {
    bannerEl.hidden = true;
    clearChildren(bannerEl);
  }
  function showBanner(opts) {
    clearChildren(bannerEl);
    bannerEl.appendChild(el("p", { class: "bcw-banner-text", text: opts.text }));
    if (opts.actions && opts.actions.length) {
      var row = el("div", { class: "bcw-banner-actions" });
      opts.actions.forEach(function (action) {
        var btn = el("button", { type: "button", class: "bcw-banner-btn bcw-banner-btn-" + (action.variant || "secondary") }, [
          action.label,
        ]);
        btn.addEventListener("click", action.onClick);
        row.appendChild(btn);
      });
      bannerEl.appendChild(row);
    }
    bannerEl.hidden = false;
  }
  function showBannerError(message, retryText) {
    showBanner({
      text: message,
      actions: retryText
        ? [
            {
              label: "Retry",
              variant: "primary",
              onClick: function () {
                hideBanner();
                sendUserMessage(retryText);
              },
            },
          ]
        : [],
    });
  }
  function showClosedBanner() {
    showBanner({
      text: "This conversation has ended. Start a new one to keep chatting.",
      actions: [
        {
          label: "New conversation",
          variant: "primary",
          onClick: function () {
            hideBanner();
            startNewConversation();
          },
        },
      ],
    });
  }

  // ---------------------------------------------------------------------
  // Sending messages
  // ---------------------------------------------------------------------
  function updateSendButtonState() {
    var hasText = textarea.value.trim().length > 0;
    sendBtn.disabled = !hasText || state.sending || state.conversationClosed;
  }

  function setSending(isSending) {
    state.sending = isSending;
    textarea.disabled = isSending;
    updateSendButtonState();
  }

  function ensureConversation() {
    if (state.conversationId) return Promise.resolve(state.conversationId);
    return apiCreateConversation().then(function (data) {
      state.conversationId = data.conversation_id;
      state.sessionToken = data.session_id;
      persistConversation(state.conversationId, state.sessionToken);
      return state.conversationId;
    });
  }

  function sendUserMessage(text) {
    if (state.conversationClosed) {
      showClosedBanner();
      return;
    }
    hideBanner();
    appendMessage("user", text);
    setSending(true);
    showTyping();
    ensureConversation()
      .then(function (conversationId) {
        return apiSendChat(conversationId, text);
      })
      .then(function (data) {
        hideTyping();
        setSending(false);
        // /api/chat always echoes the conversation's access token — keep
        // it in sync even though this endpoint itself doesn't require it
        // (see docs/security.md for why /api/chat is scoped differently
        // from the read/close endpoints).
        if (data.session_id && data.session_id !== state.sessionToken) {
          state.sessionToken = data.session_id;
          persistConversation(state.conversationId, state.sessionToken);
        }
        appendMessage("assistant", data.response);
      })
      .catch(function (err) {
        hideTyping();
        setSending(false);
        handleSendError(err, text);
      });
  }

  function handleSendError(err, originalText) {
    log("send error", err);
    if (err && err.name === "AbortError") {
      showBannerError("That's taking longer than expected. Please try again.", originalText);
      return;
    }
    var status = err && err.status;
    if (status === 409) {
      state.conversationClosed = true;
      updateSendButtonState();
      showClosedBanner();
      return;
    }
    if (status === 404) {
      state.conversationId = null;
      clearPersistedConversationId();
      showBannerError("Your session needs to be refreshed. Please try sending that again.", originalText);
      return;
    }
    if (status === 422 || status === 400) {
      showBannerError("That message couldn't be sent. Please shorten it or rephrase and try again.", originalText);
      return;
    }
    showBannerError("Sorry, I’m having trouble connecting right now. Please try again in a moment.", originalText);
  }

  function handleQuickAction(action) {
    if (state.sending) return;
    hideQuickActions();
    sendUserMessage(action.message);
  }

  function autosizeTextarea() {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
  }

  function submitComposer(e) {
    if (e) e.preventDefault();
    if (state.sending) return;
    var trimmed = textarea.value.trim();
    if (!trimmed) return;
    if (state.conversationClosed) {
      showClosedBanner();
      return;
    }
    hideQuickActions();
    textarea.value = "";
    updateSendButtonState();
    autosizeTextarea();
    sendUserMessage(trimmed);
  }

  composerForm.addEventListener("submit", submitComposer);
  textarea.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitComposer();
    }
  });
  textarea.addEventListener("input", function () {
    updateSendButtonState();
    autosizeTextarea();
  });

  // ---------------------------------------------------------------------
  // New conversation
  // ---------------------------------------------------------------------
  function conversationHasMessages() {
    return messagesEl.querySelectorAll(".bcw-message-user").length > 0;
  }

  function startNewConversation() {
    var previousId = state.conversationId;
    var previousToken = state.sessionToken;
    var wasClosed = state.conversationClosed;
    if (previousId && !wasClosed) {
      apiCloseConversation(previousId, previousToken).catch(function (err) {
        log("closing previous conversation failed (non-fatal)", err);
      });
    }
    state.conversationId = null;
    state.sessionToken = null;
    state.conversationClosed = false;
    clearPersistedConversationId();
    clearChildren(messagesEl);
    quickActionsEl.hidden = false;
    hideBanner();
    appendMessage("assistant", WELCOME_MESSAGE);
    updateSendButtonState();
    textarea.focus();
  }

  newChatBtn.addEventListener("click", function () {
    if (conversationHasMessages() && !state.conversationClosed) {
      showBanner({
        text: "Start a new conversation? Your current conversation is saved, not deleted.",
        actions: [
          { label: "Cancel", variant: "secondary", onClick: hideBanner },
          {
            label: "Start new",
            variant: "primary",
            onClick: function () {
              hideBanner();
              startNewConversation();
            },
          },
        ],
      });
    } else {
      startNewConversation();
    }
  });

  // ---------------------------------------------------------------------
  // Open / close panel
  // ---------------------------------------------------------------------
  function onDocumentKeydown(e) {
    if (e.key === "Escape") closePanel();
  }

  function openPanel() {
    state.open = true;
    panel.hidden = false;
    launcherBtn.setAttribute("aria-expanded", "true");
    launcherBtn.setAttribute("aria-label", "Close " + config.title + " chat");
    clearChildren(launcherBtn);
    launcherBtn.innerHTML = ICONS.close;
    document.addEventListener("keydown", onDocumentKeydown, true);
    window.setTimeout(function () {
      textarea.focus();
    }, 0);
  }

  function closePanel() {
    state.open = false;
    panel.hidden = true;
    launcherBtn.setAttribute("aria-expanded", "false");
    launcherBtn.setAttribute("aria-label", "Open " + config.title + " chat");
    clearChildren(launcherBtn);
    launcherBtn.innerHTML = ICONS.chat;
    document.removeEventListener("keydown", onDocumentKeydown, true);
    launcherBtn.focus();
  }

  launcherBtn.addEventListener("click", function () {
    if (state.open) closePanel();
    else openPanel();
  });
  headerCloseBtn.addEventListener("click", closePanel);

  // ---------------------------------------------------------------------
  // Init: rehydrate an existing session (same-tab reload) or show the
  // welcome message + quick actions for a fresh visitor. A backend
  // conversation is only ever created lazily, on the first real message
  // (see ensureConversation) — opening/closing the widget without typing
  // anything never creates a database row.
  // ---------------------------------------------------------------------
  function init() {
    var storedId = loadPersistedConversationId();
    var storedToken = loadPersistedToken();
    if (!storedId || !storedToken) {
      appendMessage("assistant", WELCOME_MESSAGE);
      return;
    }
    apiFetchConversation(storedId, storedToken)
      .then(function (data) {
        state.conversationId = storedId;
        state.sessionToken = storedToken;
        state.conversationClosed = data.status === "closed";
        if (data.messages && data.messages.length) {
          data.messages.forEach(function (m) {
            appendMessage(m.role, m.content);
          });
          hideQuickActions();
        } else {
          appendMessage("assistant", WELCOME_MESSAGE);
        }
        if (state.conversationClosed) showClosedBanner();
        updateSendButtonState();
      })
      .catch(function (err) {
        log("failed to rehydrate stored conversation, starting fresh", err);
        clearPersistedConversationId();
        appendMessage("assistant", WELCOME_MESSAGE);
      });
  }

  function mount() {
    document.body.appendChild(host);
    init();
  }

  if (document.body) {
    mount();
  } else {
    document.addEventListener("DOMContentLoaded", mount);
  }

  // ---------------------------------------------------------------------
  // Styles
  // ---------------------------------------------------------------------
  function WIDGET_CSS() {
    return "" +
      ":host{all:initial;position:fixed;inset:auto 20px 20px auto;z-index:2147483000;" +
      "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;}\n" +
      "*{box-sizing:border-box;}\n" +
      ".bcw-launcher{width:60px;height:60px;border-radius:50%;border:none;cursor:pointer;" +
      "background:linear-gradient(135deg,#4f46e5,#2563eb);color:#fff;display:flex;align-items:center;" +
      "justify-content:center;box-shadow:0 10px 25px rgba(37,99,235,.35);transition:transform .15s ease,box-shadow .15s ease;" +
      "position:absolute;bottom:0;right:0;padding:0;}\n" +
      ".bcw-launcher:hover{transform:translateY(-2px);box-shadow:0 14px 30px rgba(37,99,235,.45);}\n" +
      ".bcw-launcher:focus-visible{outline:3px solid #93c5fd;outline-offset:3px;}\n" +
      ".bcw-panel{position:absolute;bottom:76px;right:0;width:380px;max-width:calc(100vw - 40px);height:min(640px,calc(100vh - 120px));" +
      "background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(15,23,42,.25);display:flex;flex-direction:column;" +
      "overflow:hidden;border:1px solid rgba(15,23,42,.06);}\n" +
      ".bcw-panel[hidden]{display:none;}\n" +
      ".bcw-header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 18px;" +
      "background:linear-gradient(135deg,#4f46e5,#2563eb);color:#fff;flex-shrink:0;}\n" +
      ".bcw-header-text{min-width:0;}\n" +
      ".bcw-header-title{margin:0;font-size:15px;font-weight:700;line-height:1.3;}\n" +
      ".bcw-header-subtitle{margin:2px 0 0;font-size:12px;font-weight:400;opacity:.9;line-height:1.3;}\n" +
      ".bcw-header-actions{display:flex;align-items:center;gap:4px;flex-shrink:0;}\n" +
      ".bcw-icon-btn{width:32px;height:32px;border-radius:8px;border:none;background:rgba(255,255,255,.15);" +
      "color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0;transition:background .15s ease;}\n" +
      ".bcw-icon-btn:hover{background:rgba(255,255,255,.28);}\n" +
      ".bcw-icon-btn:focus-visible{outline:2px solid #fff;outline-offset:2px;}\n" +
      ".bcw-messages{flex:1 1 auto;min-height:0;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;background:#f8fafc;}\n" +
      ".bcw-message{display:flex;max-width:88%;}\n" +
      ".bcw-message-assistant{align-self:flex-start;}\n" +
      ".bcw-message-user{align-self:flex-end;}\n" +
      ".bcw-bubble{padding:10px 14px;border-radius:16px;font-size:14px;line-height:1.5;white-space:pre-wrap;" +
      "overflow-wrap:anywhere;word-break:break-word;}\n" +
      ".bcw-message-assistant .bcw-bubble{background:#fff;color:#1e293b;border:1px solid #e2e8f0;border-bottom-left-radius:4px;}\n" +
      ".bcw-message-user .bcw-bubble{background:#2563eb;color:#fff;border-bottom-right-radius:4px;}\n" +
      ".bcw-typing-bubble{display:flex;align-items:center;gap:8px;color:#64748b;font-size:13px;font-style:italic;}\n" +
      ".bcw-typing-dots{display:inline-flex;gap:3px;}\n" +
      ".bcw-dot{width:5px;height:5px;border-radius:50%;background:#94a3b8;animation:bcw-bounce 1.2s infinite ease-in-out;}\n" +
      ".bcw-dot:nth-child(2){animation-delay:.15s;}\n" +
      ".bcw-dot:nth-child(3){animation-delay:.3s;}\n" +
      "@keyframes bcw-bounce{0%,80%,100%{opacity:.3;transform:translateY(0);}40%{opacity:1;transform:translateY(-3px);}}\n" +
      ".bcw-quick-actions{display:flex;flex-wrap:wrap;gap:8px;padding:0 16px 12px;flex-shrink:0;background:#f8fafc;}\n" +
      ".bcw-quick-actions[hidden]{display:none;}\n" +
      ".bcw-quick-action{border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:999px;padding:8px 14px;" +
      "font-size:13px;cursor:pointer;transition:background .15s ease,border-color .15s ease;}\n" +
      ".bcw-quick-action:hover{background:#eff6ff;border-color:#93c5fd;color:#1d4ed8;}\n" +
      ".bcw-quick-action:focus-visible{outline:2px solid #2563eb;outline-offset:2px;}\n" +
      ".bcw-banner{margin:0 16px 12px;padding:10px 12px;border-radius:12px;background:#fffbeb;border:1px solid #fde68a;flex-shrink:0;}\n" +
      ".bcw-banner[hidden]{display:none;}\n" +
      ".bcw-banner-text{margin:0 0 8px;font-size:13px;color:#78350f;line-height:1.4;}\n" +
      ".bcw-banner-actions{display:flex;gap:8px;}\n" +
      ".bcw-banner-btn{border-radius:8px;padding:6px 12px;font-size:12px;cursor:pointer;border:1px solid transparent;}\n" +
      ".bcw-banner-btn-primary{background:#2563eb;color:#fff;}\n" +
      ".bcw-banner-btn-primary:hover{background:#1d4ed8;}\n" +
      ".bcw-banner-btn-secondary{background:#fff;color:#78350f;border-color:#fde68a;}\n" +
      ".bcw-banner-btn-secondary:hover{background:#fef3c7;}\n" +
      ".bcw-banner-btn:focus-visible{outline:2px solid #2563eb;outline-offset:2px;}\n" +
      ".bcw-composer{display:flex;align-items:flex-end;gap:8px;padding:12px 16px;border-top:1px solid #e2e8f0;background:#fff;flex-shrink:0;}\n" +
      ".bcw-input{flex:1 1 auto;resize:none;max-height:120px;min-height:40px;padding:10px 12px;border-radius:12px;" +
      "border:1px solid #cbd5e1;font-size:14px;font-family:inherit;line-height:1.4;color:#1e293b;background:#f8fafc;}\n" +
      ".bcw-input:focus{outline:none;border-color:#2563eb;background:#fff;box-shadow:0 0 0 3px rgba(37,99,235,.15);}\n" +
      ".bcw-input:disabled{opacity:.6;}\n" +
      ".bcw-send{width:40px;height:40px;border-radius:12px;border:none;background:#2563eb;color:#fff;cursor:pointer;" +
      "display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:background .15s ease;}\n" +
      ".bcw-send:hover:not(:disabled){background:#1d4ed8;}\n" +
      ".bcw-send:disabled{background:#cbd5e1;cursor:not-allowed;}\n" +
      ".bcw-send:focus-visible{outline:2px solid #1d4ed8;outline-offset:2px;}\n" +
      "@media (max-width:480px){" +
      ".bcw-panel{position:fixed;top:16px;left:16px;right:16px;bottom:16px;width:auto;max-width:none;height:auto;}" +
      ".bcw-launcher{position:fixed;bottom:20px;right:20px;}" +
      "}\n";
  }
})();
