const fs = require("fs");
const path = require("path");

const STOP_WORDS = new Set([
  "para", "com", "sobre", "isso", "essa", "esse", "seu", "sua", "porque", "quando",
  "where", "what", "from", "about", "there", "would", "could", "should", "have", "your",
  "que", "de", "da", "do", "das", "dos", "e", "o", "a", "os", "as", "um", "uma",
  "na", "no", "nas", "nos", "em", "pra", "por", "ser", "ter", "mais", "muito", "muita",
  "the", "and", "for", "you", "are", "was", "were", "this", "that", "with", "como", "vou",
  "vamos", "tambem", "aqui", "agora", "entao", "okay", "blz", "sim", "nao", "oi", "ola",
]);

const ensureDir = (filePath) => {
  const dir = path.dirname(filePath);
  fs.mkdirSync(dir, { recursive: true });
};

const nowIso = () => new Date().toISOString();
const stripBom = (text) => (text && text.charCodeAt(0) === 0xfeff ? text.slice(1) : text);

class MemoryStore {
  constructor({ dataFile, maxMessagesPerChat = 200, maxChatsInGraph = 10, maxTopicsPerChat = 3 }) {
    this.dataFile = dataFile;
    this.maxMessagesPerChat = maxMessagesPerChat;
    this.maxChatsInGraph = maxChatsInGraph;
    this.maxTopicsPerChat = maxTopicsPerChat;
    this.state = {
      updatedAt: nowIso(),
      chats: {},
      summaries: {},
    };
    this.load();
  }

  load() {
    try {
      if (fs.existsSync(this.dataFile)) {
        const raw = stripBom(fs.readFileSync(this.dataFile, "utf8"));
        const parsed = JSON.parse(raw);
        this.state = {
          updatedAt: parsed.updatedAt || nowIso(),
          chats: parsed.chats || {},
          summaries: parsed.summaries || {},
        };
      }
    } catch (error) {
      console.error("[memory] failed to load data file:", error.message);
    }
  }

  save() {
    ensureDir(this.dataFile);
    const payload = JSON.stringify(this.state, null, 2);
    fs.writeFileSync(this.dataFile, payload, "utf8");
  }

  touch() {
    this.state.updatedAt = nowIso();
  }

  sanitizeText(value) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.slice(0, 600);
  }

  keywordTokens(text) {
    return this.sanitizeText(text)
      .toLowerCase()
      .replace(/[^a-z0-9\sáéíóúàâêôãõç]/gi, " ")
      .split(/\s+/)
      .filter(
        (token) =>
          token.length >= 4 &&
          !STOP_WORDS.has(token) &&
          !/\d/.test(token) &&
          !token.startsWith("http"),
      );
  }

  upsertChat({ chatId, chatName, lastSeen = Date.now(), persist = true }) {
    if (!chatId) return null;

    const chat = this.state.chats[chatId] || {
      chatId,
      chatName: chatName || chatId,
      lastSeen: Number(lastSeen) || Date.now(),
      messages: [],
      topicCounts: {},
      messageCount: 0,
    };

    if (chatName && String(chatName).trim()) {
      chat.chatName = String(chatName).trim();
    }
    chat.lastSeen = Math.max(Number(chat.lastSeen) || 0, Number(lastSeen) || Date.now());
    this.state.chats[chatId] = chat;

    if (persist) {
      this.touch();
      this.save();
    }

    return chat;
  }

  upsertChatsBatch(entries) {
    let changed = 0;
    for (const entry of entries || []) {
      const before = this.state.chats[entry?.chatId]?.lastSeen || 0;
      const out = this.upsertChat({ ...entry, persist: false });
      if (out) {
        const after = out.lastSeen || 0;
        if (after !== before) changed += 1;
      }
    }
    if (changed > 0) {
      this.touch();
      this.save();
    }
    return changed;
  }

  addMessage({
    chatId,
    chatName,
    sender,
    text,
    fromMe = false,
    timestamp = Date.now(),
    externalId = null,
    media = null,
    persist = true,
  }) {
    if (!chatId || !text) return null;

    const cleanText = this.sanitizeText(text);
    if (!cleanText) return null;

    const id = `${chatId}:${timestamp}:${Math.random().toString(36).slice(2, 8)}`;
    const chat = this.upsertChat({ chatId, chatName, lastSeen: timestamp, persist: false });
    if (externalId) {
      const existing = chat.messages.find((m) => m.externalId === externalId);
      if (existing) {
        if (media && typeof media === "object") {
          if (!existing.media || typeof existing.media !== "object") {
            existing.media = media;
          } else {
            existing.media = { ...existing.media, ...media };
          }
          this.state.chats[chatId] = chat;
          if (persist) {
            this.touch();
            this.save();
          }
        }
        return null;
      }
    }
    chat.lastSeen = Math.max(Number(chat.lastSeen) || 0, Number(timestamp) || Date.now());
    chat.messageCount += 1;
    chat.messages.push({
      id,
      externalId,
      chatId,
      sender: sender || "unknown",
      text: cleanText,
      fromMe: Boolean(fromMe),
      timestamp,
      media: media && typeof media === "object" ? media : null,
    });

    if (chat.messages.length > this.maxMessagesPerChat) {
      chat.messages = chat.messages.slice(-this.maxMessagesPerChat);
    }

    const tokens = this.keywordTokens(cleanText);
    for (const token of tokens) {
      chat.topicCounts[token] = (chat.topicCounts[token] || 0) + 1;
    }

    this.state.chats[chatId] = chat;
    if (persist) {
      this.touch();
      this.save();
    }

    return chat.messages[chat.messages.length - 1];
  }

  addMessagesBatch(entries) {
    let added = 0;
    for (const entry of entries || []) {
      const out = this.addMessage({ ...entry, persist: false });
      if (out) added += 1;
    }
    if (added > 0) {
      this.touch();
      this.save();
    }
    return added;
  }

  setSummary(chatId, summary) {
    this.state.summaries[chatId] = {
      summary,
      updatedAt: nowIso(),
    };
    this.touch();
    this.save();
  }

  getChat(chatId) {
    return this.state.chats[chatId] || null;
  }

  getChatMessages(chatId, limit = 50) {
    const chat = this.getChat(chatId);
    if (!chat) return [];
    return chat.messages.slice(-Math.max(1, limit));
  }

  listChats() {
    return Object.values(this.state.chats)
      .sort((a, b) => (Number(b.lastSeen) || 0) - (Number(a.lastSeen) || 0))
      .map((chat) => ({
        chatId: chat.chatId,
        chatName: chat.chatName,
        lastSeen: chat.lastSeen,
        messageCount: chat.messageCount,
        lastMessage: chat.messages?.length ? chat.messages[chat.messages.length - 1].text : "",
        lastSender: chat.messages?.length ? chat.messages[chat.messages.length - 1].sender : "",
        topics: Object.entries(chat.topicCounts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 8)
          .map(([topic, count]) => ({ topic, count })),
      }));
  }

  buildGraph() {
    const chats = this.listChats().slice(0, this.maxChatsInGraph);
    const nodes = [];
    const edges = [];

    const hubId = "hub:whatsapp-memory";
    nodes.push({
      id: hubId,
      title: "WhatsApp Memory",
      info: "Hub central de conversas e topicos.",
      content: "conversas conectadas\nresumos e pendencias\nmemoria contextual",
      snippets: ["whatsapp", "memory", "clusters", "context"],
      meta: { type: "hub" },
    });

    for (const chat of chats) {
      const chatNodeId = `chat:${chat.chatId}`;
      const lastDate = new Date(chat.lastSeen).toLocaleString("pt-BR");
      const topTopics = chat.topics.filter((t) => t.count >= 2).slice(0, this.maxTopicsPerChat);
      const topicNames = topTopics.map((t) => t.topic);
      const preview = this.sanitizeText(chat.lastMessage || "").slice(0, 90);
      const chatKind = chat.chatId.endsWith("@g.us") ? "grupo" : "contato";
      const detailLine = preview ? `${chat.lastSender || "ultimo"}: ${preview}` : "sem mensagens de texto ainda";
      const rawChat = this.getChat(chat.chatId);
      const recentMessages = (rawChat?.messages || [])
        .slice(-8)
        .map((msg) => ({
          sender: String(msg.sender || "contato").slice(0, 18),
          text: this.sanitizeText(msg.text || "").slice(0, 140),
          fromMe: Boolean(msg.fromMe),
          timestamp: msg.timestamp || 0,
          media: msg.media && typeof msg.media === "object" ? msg.media : null,
        }));
      nodes.push({
        id: chatNodeId,
        title: chat.chatName,
        info: `${chatKind} | ${chat.messageCount} mensagens | ultima atividade ${lastDate}`,
        content: `chat_id: ${chat.chatId}\nmensagens: ${chat.messageCount}\nultimo evento: ${lastDate}\n${detailLine}`,
        snippets: topicNames.length ? topicNames : [chatKind, "recent", "message"],
        meta: { type: "chat", chatId: chat.chatId, chatKind, lastSeen: chat.lastSeen, recentMessages },
      });

      edges.push({
        from: hubId,
        to: chatNodeId,
        label: "conversation",
      });

      for (const topic of topTopics) {
        const topicNodeId = `topic:${chat.chatId}:${topic.topic}`;
        nodes.push({
          id: topicNodeId,
          title: `#${topic.topic}`,
          info: `tema recorrente (${topic.count}) em ${chat.chatName}`,
          content: `topic: ${topic.topic}\nfrequencia: ${topic.count}\nchat: ${chat.chatName}`,
          snippets: [chat.chatName, `freq-${topic.count}`, "semantic-link"],
          meta: { type: "topic", chatId: chat.chatId, topic: topic.topic },
        });
        edges.push({
          from: chatNodeId,
          to: topicNodeId,
          label: "topic",
        });
      }
    }

    return {
      updatedAt: this.state.updatedAt,
      nodeCount: nodes.length,
      edgeCount: edges.length,
      nodes,
      edges,
      chats,
    };
  }
}

module.exports = { MemoryStore };
