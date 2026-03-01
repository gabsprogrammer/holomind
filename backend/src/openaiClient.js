const OpenAI = require("openai");

class OpenAIClient {
  constructor({ provider, apiKey, model, geminiApiKey, geminiModel, timeoutMs = 45000 }) {
    this.provider = String(provider || "auto").toLowerCase();
    this.openaiModel = model || "gpt-4.1-mini";
    this.geminiModel = geminiModel || "gemini-flash-lite-latest";
    this.openaiApiKey = String(apiKey || "").trim();
    this.geminiApiKey = String(geminiApiKey || "").trim();
    this.timeoutMs = Math.max(5000, Number(timeoutMs) || 45000);

    const hasOpenAI = Boolean(this.openaiApiKey);
    const hasGemini = Boolean(this.geminiApiKey);

    if (this.provider === "gemini") {
      this.selectedProvider = hasGemini ? "gemini" : "none";
    } else if (this.provider === "openai") {
      this.selectedProvider = hasOpenAI ? "openai" : "none";
    } else {
      this.selectedProvider = hasGemini ? "gemini" : hasOpenAI ? "openai" : "none";
    }

    this.enabled = this.selectedProvider !== "none";
    this.client = this.selectedProvider === "openai" ? new OpenAI({ apiKey: this.openaiApiKey }) : null;
  }

  async _fetchWithTimeout(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  async _generateWithOpenAI(systemText, userText) {
    if (!this.client) throw new Error("openai client not configured");
    const response = await this.client.responses.create({
      model: this.openaiModel,
      input: [
        {
          role: "system",
          content: [
            {
              type: "input_text",
              text: systemText,
            },
          ],
        },
        {
          role: "user",
          content: [
            {
              type: "input_text",
              text: userText,
            },
          ],
        },
      ],
    });
    return response.output_text?.trim() || "";
  }

  async _generateWithGeminiCall(modelName, systemText, userText, { maxOutputTokens = 180, temperature = 0.55 } = {}) {
    if (!this.geminiApiKey) throw new Error("gemini api key not configured");
    const raw = String(modelName || "").trim();
    const normalized = raw.startsWith("models/") ? raw.slice("models/".length) : raw;
    const model = encodeURIComponent(normalized);
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(this.geminiApiKey)}`;

    const payload = {
      systemInstruction: {
        parts: [{ text: String(systemText || "") }],
      },
      contents: [
        {
          role: "user",
          parts: [{ text: String(userText || "") }],
        },
      ],
      generationConfig: {
        temperature,
        maxOutputTokens,
      },
    };

    const response = await this._fetchWithTimeout(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const raw = await response.text().catch(() => "");
      throw new Error(`gemini error (${response.status}) ${String(raw).slice(0, 260)}`);
    }

    const data = await response.json();
    const parts = data?.candidates?.[0]?.content?.parts;
    if (!Array.isArray(parts) || !parts.length) {
      return "";
    }

    return parts
      .map((p) => (p && typeof p.text === "string" ? p.text : ""))
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  }

  async _generateWithGemini(systemText, userText, opts = {}) {
    try {
      return await this._generateWithGeminiCall(this.geminiModel, systemText, userText, opts);
    } catch (error) {
      const message = String(error?.message || "");
      const fallbackModel = "gemini-flash-lite-latest";
      const current = String(this.geminiModel || "").replace(/^models\//, "").trim();
      if (!message.includes("gemini error (404)") || current === fallbackModel) {
        throw error;
      }
      return this._generateWithGeminiCall(fallbackModel, systemText, userText, opts);
    }
  }

  async _generate(systemText, userText, opts = {}) {
    if (this.selectedProvider === "gemini") {
      return this._generateWithGemini(systemText, userText, opts);
    }
    if (this.selectedProvider === "openai") {
      return this._generateWithOpenAI(systemText, userText, opts);
    }
    throw new Error("no ai provider configured");
  }

  async buildChatSummary(chatName, messages) {
    const recent = (messages || []).slice(-60);
    if (!recent.length) {
      return "Sem mensagens suficientes para resumir.";
    }

    if (!this.enabled) {
      const preview = recent.slice(-5).map((m) => `- ${m.fromMe ? "Eu" : m.sender}: ${m.text}`).join("\n");
      return `Resumo local de ${chatName}:\n${preview}`;
    }

    const transcript = recent
      .map((m) => `${m.fromMe ? "Eu" : m.sender}: ${m.text}`)
      .join("\n");

    try {
      const out = await this._generate(
        "Voce gera resumos curtos para WhatsApp em portugues brasileiro. Seja objetivo e liste pendencias com bullet points.",
        `Chat: ${chatName}\n\nConversa:\n${transcript}\n\nCrie: 1) resumo em ate 4 linhas; 2) proximos passos em ate 4 bullets.`,
        { maxOutputTokens: 260, temperature: 0.45 },
      );
      return out || "Nao foi possivel gerar resumo agora.";
    } catch (error) {
      const preview = recent.slice(-5).map((m) => `- ${m.fromMe ? "Eu" : m.sender}: ${m.text}`).join("\n");
      return `Resumo local de ${chatName}:\n${preview}`;
    }
  }

  async buildReplySuggestion(chatName, messages, incomingText) {
    const history = (messages || []).slice(-25);

    if (!this.enabled) {
      return `Recebi sua mensagem sobre \"${incomingText}\". Te respondo com mais detalhes em instantes.`;
    }

    const context = history
      .map((m) => `${m.fromMe ? "Eu" : m.sender}: ${m.text}`)
      .join("\n");

    try {
      const out = await this._generate(
        "Voce escreve respostas curtas para WhatsApp em portugues. Responda de forma simples, natural e objetiva. Nunca invente fatos.",
        `Chat: ${chatName}\n\nHistorico:\n${context}\n\nMensagem nova: ${incomingText}\n\nSugira somente 1 resposta curta (preferencialmente 1 frase, maximo 18 palavras).`,
        { maxOutputTokens: 90, temperature: 0.55 },
      );
      return out || "Perfeito, vou verificar e te retorno em seguida.";
    } catch (error) {
      const clean = String(incomingText || "").replace(/\s+/g, " ").trim().slice(0, 80);
      return clean ? `Fechado, entendi. Sobre "${clean}", te confirmo isso agora.` : "Beleza, entendi. Ja te respondo certinho.";
    }
  }
}

module.exports = { OpenAIClient };
