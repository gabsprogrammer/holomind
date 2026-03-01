const path = require("path");
require("dotenv").config({ path: path.resolve(process.cwd(), ".env") });

const asBool = (value, fallback) => {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value).toLowerCase() === "true";
};

const asInt = (value, fallback) => {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const config = {
  host: process.env.HOST || "127.0.0.1",
  port: asInt(process.env.PORT, 8787),
  aiProvider: (process.env.AI_PROVIDER || "gemini").toLowerCase(),
  aiRequestTimeoutMs: asInt(process.env.AI_REQUEST_TIMEOUT_MS, 45000),
  openaiApiKey: process.env.OPENAI_API_KEY || "",
  openaiModel: process.env.OPENAI_MODEL || "gpt-4.1-mini",
  geminiApiKey: process.env.GEMINI_API_KEY || "",
  geminiModel: process.env.GEMINI_MODEL || "gemini-flash-lite-latest",
  whatsappEnabled: asBool(process.env.WHATSAPP_ENABLED, true),
  whatsappAutoReply: asBool(process.env.WHATSAPP_AUTO_REPLY, false),
  whatsappAuthDir: path.resolve(process.cwd(), process.env.WHATSAPP_AUTH_DIR || "./auth"),
  dataFile: path.resolve(process.cwd(), process.env.DATA_FILE || "./data/memory.json"),
  mediaDir: path.resolve(process.cwd(), process.env.MEDIA_DIR || "./data/media"),
  voiceEnabled: asBool(process.env.VOICE_ENABLED, true),
  voiceServerUrl: process.env.VOICE_SERVER_URL || "http://127.0.0.1:17493",
  voiceProfileId: process.env.VOICE_PROFILE_ID || "dgff",
  voiceLanguage: process.env.VOICE_LANGUAGE || "pt",
  voiceOutputDir: path.resolve(process.cwd(), process.env.VOICE_OUTPUT_DIR || "./data/voice"),
  voiceFallbackToText: asBool(process.env.VOICE_FALLBACK_TO_TEXT, false),
  voiceAsAudioDefault: asBool(process.env.VOICE_AS_AUDIO_DEFAULT, true),
  voiceRequestTimeoutMs: asInt(process.env.VOICE_REQUEST_TIMEOUT_MS, 180000),
  maxMessagesPerChat: asInt(process.env.MAX_MESSAGES_PER_CHAT, 200),
  maxChatsInGraph: asInt(process.env.MAX_CHATS_IN_GRAPH, 10),
  maxTopicsPerChat: asInt(process.env.MAX_TOPICS_PER_CHAT, 3),
};

module.exports = { config };

