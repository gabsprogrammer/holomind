const express = require("express");
const cors = require("cors");

const { config } = require("./config");
const { MemoryStore } = require("./memoryStore");
const { OpenAIClient } = require("./openaiClient");
const { createWhatsAppBridge } = require("./whatsappClient");
const { VoiceService } = require("./voiceService");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const app = express();
  app.use(cors());
  app.use(express.json({ limit: "1mb" }));

  const store = new MemoryStore({
    dataFile: config.dataFile,
    maxMessagesPerChat: config.maxMessagesPerChat,
    maxChatsInGraph: config.maxChatsInGraph,
    maxTopicsPerChat: config.maxTopicsPerChat,
  });

  const aiClient = new OpenAIClient({
    provider: config.aiProvider,
    timeoutMs: config.aiRequestTimeoutMs,
    apiKey: config.openaiApiKey,
    model: config.openaiModel,
    geminiApiKey: config.geminiApiKey,
    geminiModel: config.geminiModel,
  });
  const voiceService = new VoiceService({
    enabled: config.voiceEnabled,
    baseUrl: config.voiceServerUrl,
    profileId: config.voiceProfileId,
    language: config.voiceLanguage,
    outputDir: config.voiceOutputDir,
    timeoutMs: config.voiceRequestTimeoutMs,
  });

  let whatsapp = null;
  if (config.whatsappEnabled) {
    try {
      whatsapp = await createWhatsAppBridge({
        authDir: config.whatsappAuthDir,
        autoReply: config.whatsappAutoReply,
        store,
        aiClient,
        mediaDir: config.mediaDir,
      });
    } catch (error) {
      console.error("[startup] WhatsApp bridge error:", error.message);
    }
  }

  app.get("/health", (_req, res) => {
    res.json({
      ok: true,
      whatsappConnected: whatsapp ? whatsapp.isConnected() : false,
      aiProvider: aiClient.selectedProvider,
      aiEnabled: aiClient.enabled,
      openaiEnabled: aiClient.enabled,
      voiceEnabled: voiceService.enabled,
      updatedAt: store.state.updatedAt,
    });
  });

  app.get("/memory/graph", (_req, res) => {
    res.json(store.buildGraph());
  });

  app.get("/memory/chats", (_req, res) => {
    res.json({ chats: store.listChats() });
  });

  app.get("/memory/stats", (_req, res) => {
    const chats = store.listChats();
    const totalMessages = chats.reduce((acc, chat) => acc + (chat.messageCount || 0), 0);
    res.json({
      chatCount: chats.length,
      totalMessages,
      updatedAt: store.state.updatedAt,
    });
  });

  app.post("/memory/messages", (req, res) => {
    const { chatId, chatName, sender, text, fromMe, timestamp } = req.body || {};
    if (!chatId || !text) {
      return res.status(400).json({ error: "chatId and text are required" });
    }

    const message = store.addMessage({
      chatId,
      chatName,
      sender,
      text,
      fromMe,
      timestamp: timestamp || Date.now(),
    });

    return res.status(201).json({ ok: true, message });
  });

  app.post("/memory/summaries/:chatId", async (req, res) => {
    try {
      const { chatId } = req.params;
      const { send = false } = req.body || {};
      const chat = store.getChat(chatId);
      if (!chat) {
        return res.status(404).json({ error: "chat not found" });
      }

      const messages = store.getChatMessages(chatId, 60);
      const summary = await aiClient.buildChatSummary(chat.chatName, messages);
      store.setSummary(chatId, summary);

      if (send && whatsapp && whatsapp.isConnected()) {
        await whatsapp.sendText(chatId, summary);
        store.addMessage({
          chatId,
          chatName: chat.chatName,
          sender: "Eu",
          text: summary,
          fromMe: true,
          timestamp: Date.now(),
        });
      }

      return res.json({ ok: true, chatId, summary });
    } catch (error) {
      return res.status(500).json({ error: error.message });
    }
  });

  app.post("/memory/reply/:chatId", async (req, res) => {
    try {
      const { chatId } = req.params;
      const { incomingText } = req.body || {};
      const chat = store.getChat(chatId);
      if (!chat) {
        return res.status(404).json({ error: "chat not found" });
      }
      if (!incomingText) {
        return res.status(400).json({ error: "incomingText is required" });
      }

      const messages = store.getChatMessages(chatId, 25);
      const suggestion = await aiClient.buildReplySuggestion(chat.chatName, messages, incomingText);
      return res.json({ ok: true, chatId, suggestion });
    } catch (error) {
      return res.status(500).json({ error: error.message });
    }
  });

  app.post("/memory/send/:chatId", async (req, res) => {
    try {
      const { chatId } = req.params;
      const {
        text,
        asAudio = config.voiceAsAudioDefault,
        profileId = config.voiceProfileId,
        language = config.voiceLanguage,
      } = req.body || {};
      if (!text || !String(text).trim()) {
        return res.status(400).json({ error: "text is required" });
      }

      const chat = store.getChat(chatId);
      if (!chat) {
        return res.status(404).json({ error: "chat not found" });
      }
      if (!whatsapp || !whatsapp.isConnected()) {
        return res.status(503).json({ error: "whatsapp not connected" });
      }

      const cleanText = String(text).trim().slice(0, 1200);
      if (asAudio) {
        let lastAudioError = null;
        for (let attempt = 1; attempt <= 2; attempt += 1) {
          try {
            const voice = await voiceService.generateSpeechPttOgg(cleanText, { profileId, language });
            await whatsapp.sendAudioFile(chatId, voice.oggPath, { mimeType: "audio/ogg; codecs=opus", ptt: true });
            store.addMessage({
              chatId,
              chatName: chat.chatName,
              sender: "Eu",
              text: `[audio] ${cleanText.slice(0, 140)}`,
              media: {
                type: "audio",
                mimeType: "audio/ogg; codecs=opus",
                localPath: voice.oggPath,
                generated: true,
                profileId: voice.profileId,
                language: voice.language,
              },
              fromMe: true,
              timestamp: Date.now(),
            });
            return res.json({ ok: true, chatId, mode: "audio", attempts: attempt });
          } catch (error) {
            lastAudioError = error;
            if (attempt < 2) {
              await sleep(250);
              continue;
            }
          }
        }

        if (lastAudioError) {
          if (!config.voiceFallbackToText) {
            return res.status(500).json({ error: `voice send failed: ${lastAudioError.message}` });
          }
          await whatsapp.sendText(chatId, cleanText);
          store.addMessage({
            chatId,
            chatName: chat.chatName,
            sender: "Eu",
            text: cleanText,
            fromMe: true,
            timestamp: Date.now(),
          });
          return res.json({ ok: true, chatId, mode: "text-fallback", warning: String(lastAudioError.message).slice(0, 180) });
        }
      }

      await whatsapp.sendText(chatId, cleanText);
      store.addMessage({
        chatId,
        chatName: chat.chatName,
        sender: "Eu",
        text: cleanText,
        fromMe: true,
        timestamp: Date.now(),
      });

      return res.json({ ok: true, chatId, mode: "text" });
    } catch (error) {
      return res.status(500).json({ error: error.message });
    }
  });

  app.listen(config.port, config.host, () => {
    console.log(`[server] listening on http://${config.host}:${config.port}`);
    console.log(`[server] data file: ${config.dataFile}`);
    console.log(`[server] whatsapp: ${config.whatsappEnabled ? "enabled" : "disabled"}`);
    console.log(`[server] ai: ${aiClient.enabled ? "enabled" : "disabled"} (${aiClient.selectedProvider})`);
  });
}

main().catch((error) => {
  console.error("[fatal]", error);
  process.exit(1);
});
