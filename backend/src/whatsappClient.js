const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const pino = require("pino");
const qrcode = require("qrcode-terminal");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason,
  downloadMediaMessage,
} = require("@whiskeysockets/baileys");

const mediaLogger = pino({ level: "silent" });

const toNumber = (value) => {
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : NaN;
  }
  if (value && typeof value.toNumber === "function") return value.toNumber();
  if (value && typeof value.low === "number") return value.low;
  return NaN;
};

const toEpochMs = (value) => {
  const raw = toNumber(value);
  if (!Number.isFinite(raw)) return Date.now();
  return raw > 1e12 ? raw : raw * 1000;
};

const unwrapMessageContent = (content) => {
  let cur = content || {};
  while (cur) {
    if (cur.ephemeralMessage?.message) {
      cur = cur.ephemeralMessage.message;
      continue;
    }
    if (cur.viewOnceMessage?.message) {
      cur = cur.viewOnceMessage.message;
      continue;
    }
    if (cur.viewOnceMessageV2?.message) {
      cur = cur.viewOnceMessageV2.message;
      continue;
    }
    if (cur.viewOnceMessageV2Extension?.message) {
      cur = cur.viewOnceMessageV2Extension.message;
      continue;
    }
    if (cur.documentWithCaptionMessage?.message) {
      cur = cur.documentWithCaptionMessage.message;
      continue;
    }
    if (cur.editedMessage?.message) {
      cur = cur.editedMessage.message;
      continue;
    }
    break;
  }
  return cur || {};
};

const toThumbnailB64 = (bytes) => {
  if (!bytes) return null;
  try {
    const b64 = Buffer.from(bytes).toString("base64");
    return b64.length > 16 ? b64 : null;
  } catch {
    return null;
  }
};

const extractMessagePayload = (msg) => {
  const content = unwrapMessageContent(msg?.message);

  if (content.conversation) return { text: content.conversation, media: null };
  if (content.extendedTextMessage?.text) return { text: content.extendedTextMessage.text, media: null };

  if (content.imageMessage) {
    return {
      text: content.imageMessage.caption || "[imagem]",
      media: {
        type: "image",
        mimeType: content.imageMessage.mimetype || "image/jpeg",
        thumbnailB64: toThumbnailB64(content.imageMessage.jpegThumbnail),
      },
    };
  }

  if (content.videoMessage) {
    return {
      text: content.videoMessage.caption || "[video]",
      media: {
        type: "video",
        mimeType: content.videoMessage.mimetype || "video/mp4",
        thumbnailB64: toThumbnailB64(content.videoMessage.jpegThumbnail),
      },
    };
  }

  if (content.audioMessage) {
    const seconds = Number(content.audioMessage.seconds || 0) || 0;
    return {
      text: seconds > 0 ? `[audio ${seconds}s]` : "[audio]",
      media: {
        type: "audio",
        mimeType: content.audioMessage.mimetype || "audio/ogg",
        seconds,
      },
    };
  }

  if (content.documentMessage) {
    const fileName = content.documentMessage.fileName || "arquivo";
    return {
      text: content.documentMessage.caption || `[arquivo] ${fileName}`,
      media: {
        type: "document",
        mimeType: content.documentMessage.mimetype || "application/octet-stream",
        fileName,
      },
    };
  }

  if (content.stickerMessage) {
    return {
      text: "[sticker]",
      media: {
        type: "sticker",
        mimeType: content.stickerMessage.mimetype || "image/webp",
      },
    };
  }

  return { text: "", media: null };
};

const fallbackChatName = (chatId) => {
  if (!chatId) return "unknown-chat";
  if (chatId.endsWith("@g.us")) return `Grupo ${chatId.slice(0, 8)}`;
  return chatId.replace(/@.+$/, "");
};

const resolveSender = (msg, fromMe) => {
  if (fromMe) return "Eu";
  if (msg?.pushName) return msg.pushName;
  if (msg?.key?.participant) return msg.key.participant.replace(/@.+$/, "");
  return "Contato";
};

const shouldSkipChat = (chatId) => !chatId || chatId === "status@broadcast" || chatId.endsWith("@newsletter");

const firstNonEmpty = (...values) => {
  for (const value of values) {
    if (value === undefined || value === null) continue;
    const clean = String(value).trim();
    if (clean) return clean;
  }
  return "";
};

const getChatTimestamp = (chat) => {
  const ts = chat?.conversationTimestamp ?? chat?.lastMessageRecvTimestamp ?? chat?.timestamp;
  const n = toNumber(ts);
  if (!Number.isFinite(n)) return null;
  return toEpochMs(n);
};

const mediaExtFromMime = (mime, fallbackType = "bin") => {
  const m = String(mime || "").toLowerCase();
  if (m.includes("jpeg") || m.includes("jpg")) return "jpg";
  if (m.includes("png")) return "png";
  if (m.includes("webp")) return "webp";
  if (m.includes("gif")) return "gif";
  if (m.includes("mp4")) return "mp4";
  if (m.includes("quicktime")) return "mov";
  if (m.includes("ogg")) return "ogg";
  if (m.includes("mpeg")) return "mp3";
  if (m.includes("wav")) return "wav";
  if (m.includes("pdf")) return "pdf";
  if (fallbackType === "image") return "jpg";
  if (fallbackType === "video") return "mp4";
  if (fallbackType === "audio") return "ogg";
  if (fallbackType === "sticker") return "webp";
  return "bin";
};

const buildMediaId = (seed) => crypto.createHash("sha1").update(String(seed)).digest("hex");

async function createWhatsAppBridge({ authDir, autoReply, store, aiClient, mediaDir }) {
  let socket = null;
  let connected = false;
  const knownChatNames = new Map();
  fs.mkdirSync(mediaDir, { recursive: true });

  const setChatName = (chatId, name) => {
    const cleanId = String(chatId || "").trim();
    const cleanName = String(name || "").trim();
    if (!cleanId || !cleanName) return;
    knownChatNames.set(cleanId, cleanName);
  };

  const upsertChatFromMeta = (chatId, chatName, lastSeen = null, persist = true) => {
    if (shouldSkipChat(chatId)) return;
    if (chatName) setChatName(chatId, chatName);

    const existing = store.getChat(chatId);
    const resolvedName = firstNonEmpty(chatName, knownChatNames.get(chatId), existing?.chatName, fallbackChatName(chatId));
    const resolvedSeen = Number(lastSeen) || Number(existing?.lastSeen) || Date.now();
    store.upsertChat({ chatId, chatName: resolvedName, lastSeen: resolvedSeen, persist });
  };

  const resolveChatName = (msg, chatId) => {
    const existing = store.getChat(chatId);
    if (chatId.endsWith("@g.us")) {
      return firstNonEmpty(knownChatNames.get(chatId), existing?.chatName, fallbackChatName(chatId));
    }

    const directName = firstNonEmpty(msg?.pushName, knownChatNames.get(chatId), existing?.chatName, fallbackChatName(chatId));
    if (directName) setChatName(chatId, directName);
    return directName;
  };

  const enrichEntryWithMedia = async (msg, entry, allowDownload = true) => {
    if (!entry?.media || !socket) return entry;

    const mtype = String(entry.media.type || "");
    const ext = mediaExtFromMime(entry.media.mimeType, mtype);
    const mediaId = buildMediaId(entry.externalId || `${entry.chatId}:${entry.timestamp}:${mtype}`);
    const localPath = path.resolve(mediaDir, `${mediaId}.${ext}`);

    entry.media.mediaId = mediaId;
    if (fs.existsSync(localPath)) {
      entry.media.localPath = localPath;
      return entry;
    }
    if (!allowDownload) {
      return entry;
    }

    try {
      const buffer = await downloadMediaMessage(msg, "buffer", {}, {
        logger: mediaLogger,
        reuploadRequest: socket.updateMediaMessage,
      });
      if (buffer && buffer.length > 0) {
        fs.writeFileSync(localPath, buffer);
        entry.media.localPath = localPath;
      }
    } catch (error) {
      entry.media.downloadError = String(error.message || "media download failed").slice(0, 100);
    }

    return entry;
  };

  const mapMessageToStoreEntry = async (msg, allowDownloadMedia = true) => {
    const chatId = msg?.key?.remoteJid;
    if (shouldSkipChat(chatId)) return null;

    const payload = extractMessagePayload(msg);
    const text = String(payload.text || "").trim();
    if (!text) return null;

    const fromMe = Boolean(msg.key?.fromMe);
    const chatName = resolveChatName(msg, chatId);
    const timestamp = toEpochMs(msg.messageTimestamp);

    const entry = {
      chatId,
      chatName,
      sender: resolveSender(msg, fromMe),
      text,
      media: payload.media,
      fromMe,
      timestamp,
      externalId: msg?.key?.id ? `${chatId}:${msg.key.id}` : null,
    };

    return enrichEntryWithMedia(msg, entry, allowDownloadMedia);
  };

  const upsertOneMessage = async (msg) => {
    const entry = await mapMessageToStoreEntry(msg, true);
    if (!entry) return null;
    return store.addMessage(entry);
  };

  const connect = async () => {
    const { state, saveCreds } = await useMultiFileAuthState(authDir);
    const { version } = await fetchLatestBaileysVersion();

    socket = makeWASocket({
      auth: state,
      version,
      logger: pino({ level: "warn" }),
      browser: ["HoloMind", "Desktop", "1.0.0"],
      syncFullHistory: true,
      markOnlineOnConnect: false,
    });

    socket.ev.on("creds.update", saveCreds);

    socket.ev.on("connection.update", async ({ connection, lastDisconnect, qr }) => {
      if (qr) {
        console.log("[whatsapp] QR received. Scan with WhatsApp > Linked Devices.");
        qrcode.generate(qr, { small: true });
      }

      if (connection === "open") {
        connected = true;
        try {
          const groups = await socket.groupFetchAllParticipating();
          for (const [groupId, groupInfo] of Object.entries(groups || {})) {
            setChatName(groupId, groupInfo?.subject);
          }
        } catch (error) {
          console.log("[whatsapp] group cache warning:", error.message);
        }
        console.log("[whatsapp] connected");
      }

      if (connection === "close") {
        connected = false;
        const code = lastDisconnect?.error?.output?.statusCode;
        const shouldReconnect = code !== DisconnectReason.loggedOut;
        console.log("[whatsapp] disconnected", { code, shouldReconnect });
        if (shouldReconnect) {
          setTimeout(connect, 1500);
        }
      }
    });

    socket.ev.on("contacts.update", (contacts) => {
      for (const contact of contacts || []) {
        setChatName(contact?.id, firstNonEmpty(contact?.notify, contact?.name, contact?.verifiedName));
      }
    });

    socket.ev.on("contacts.upsert", (contacts) => {
      for (const contact of contacts || []) {
        setChatName(contact?.id, firstNonEmpty(contact?.notify, contact?.name, contact?.verifiedName));
      }
    });

    socket.ev.on("chats.update", (chats) => {
      for (const chat of chats || []) {
        const chatId = chat?.id;
        if (shouldSkipChat(chatId)) continue;
        const chatName = firstNonEmpty(chat?.name, chat?.subject, knownChatNames.get(chatId));
        const lastSeen = getChatTimestamp(chat);
        if (lastSeen || store.getChat(chatId)) {
          upsertChatFromMeta(chatId, chatName, lastSeen || Date.now(), true);
        } else if (chatName) {
          setChatName(chatId, chatName);
        }
      }
    });

    socket.ev.on("messaging-history.set", async ({ chats, contacts, messages, isLatest }) => {
      for (const contact of contacts || []) {
        setChatName(contact?.id, firstNonEmpty(contact?.notify, contact?.name, contact?.verifiedName));
      }

      const chatBatch = [];
      for (const chat of chats || []) {
        const chatId = chat?.id;
        if (shouldSkipChat(chatId)) continue;
        const chatName = firstNonEmpty(chat?.name, chat?.subject, knownChatNames.get(chatId), fallbackChatName(chatId));
        const lastSeen = getChatTimestamp(chat) || Date.now();
        setChatName(chatId, chatName);
        chatBatch.push({ chatId, chatName, lastSeen });
      }
      if (chatBatch.length > 0) {
        store.upsertChatsBatch(chatBatch);
      }

      const messageBatch = [];
      let mediaDownloadBudget = 32;
      for (const msg of messages || []) {
        const allow = mediaDownloadBudget > 0;
        const entry = await mapMessageToStoreEntry(msg, allow);
        if (entry) {
          messageBatch.push(entry);
          if (allow && entry.media && entry.media.localPath) {
            mediaDownloadBudget -= 1;
          }
        }
      }

      const added = store.addMessagesBatch(messageBatch);
      if (messageBatch.length > 0 || chatBatch.length > 0) {
        console.log(`[whatsapp] history sync: chats=${chatBatch.length} messages=${added}/${messageBatch.length}`);
      }
      if (isLatest) {
        console.log("[whatsapp] history sync complete");
      }
    });

    socket.ev.on("messages.upsert", async ({ messages }) => {
      for (const msg of messages || []) {
        const saved = await upsertOneMessage(msg);
        if (!saved) continue;

        const chatId = saved.chatId;
        if (autoReply && !saved.fromMe) {
          try {
            const recent = store.getChatMessages(chatId, 25);
            const suggestion = await aiClient.buildReplySuggestion(saved.chatName, recent, saved.text);
            if (suggestion && socket) {
              await socket.sendMessage(chatId, { text: suggestion });
              store.addMessage({
                chatId,
                chatName: saved.chatName,
                sender: "Eu",
                text: suggestion,
                fromMe: true,
                timestamp: Date.now(),
              });
            }
          } catch (error) {
            console.error("[whatsapp] auto-reply error:", error.message);
          }
        }
      }
    });
  };

  await connect();

  return {
    isConnected: () => connected,
    sendText: async (chatId, text) => {
      if (!socket) throw new Error("WhatsApp socket not initialized");
      await socket.sendMessage(chatId, { text });
    },
    sendAudioFile: async (chatId, filePath, { mimeType = "audio/ogg; codecs=opus", ptt = true } = {}) => {
      if (!socket) throw new Error("WhatsApp socket not initialized");
      const absPath = path.resolve(filePath);
      if (!fs.existsSync(absPath)) {
        throw new Error("audio file does not exist");
      }
      const audioBuffer = fs.readFileSync(absPath);
      await socket.sendMessage(chatId, {
        audio: audioBuffer,
        mimetype: mimeType,
        ptt: Boolean(ptt),
      });
    },
  };
}

module.exports = { createWhatsAppBridge };
