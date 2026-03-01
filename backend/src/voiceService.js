const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const ffmpegPath = require("ffmpeg-static");

const ensureDir = (dirPath) => {
  fs.mkdirSync(dirPath, { recursive: true });
};

const randomTag = () => `${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;

const guessExtFromContentType = (contentType) => {
  const c = String(contentType || "").toLowerCase();
  if (c.includes("audio/mpeg") || c.includes("audio/mp3")) return "mp3";
  if (c.includes("audio/wav") || c.includes("audio/x-wav")) return "wav";
  if (c.includes("audio/ogg")) return "ogg";
  return "wav";
};

const fileExists = (filePath) => {
  try {
    return fs.existsSync(filePath) && fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
};

const isUuid = (value) => /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(value || ""));

class VoiceService {
  constructor({
    enabled,
    baseUrl,
    profileId,
    language,
    outputDir,
    timeoutMs = 60000,
  }) {
    this.enabled = Boolean(enabled);
    this.baseUrl = String(baseUrl || "http://127.0.0.1:17493").replace(/\/+$/, "");
    this.profileId = String(profileId || "gabriel");
    this.language = String(language || "pt");
    this.outputDir = path.resolve(outputDir || "./data/voice");
    this.timeoutMs = Math.max(5000, Number(timeoutMs) || 60000);
    ensureDir(this.outputDir);
    this.profileCache = null;
    this.profileCacheAt = 0;
    this.profileCacheTtlMs = 30_000;
  }

  _resolveInputPath(rawPath) {
    if (!rawPath) return null;
    const asString = String(rawPath).trim();
    if (!asString) return null;
    if (path.isAbsolute(asString) && fileExists(asString)) return asString;

    const candidates = [
      path.resolve(process.cwd(), asString),
      path.resolve(this.outputDir, asString),
    ];

    for (const candidate of candidates) {
      if (fileExists(candidate)) return candidate;
    }

    return null;
  }

  _writeBufferToTemp(buffer, ext = "wav") {
    const filePath = path.resolve(this.outputDir, `tts_in_${randomTag()}.${ext}`);
    fs.writeFileSync(filePath, buffer);
    return filePath;
  }

  _convertToOggOpus(inputPath) {
    if (!ffmpegPath || !fileExists(ffmpegPath)) {
      throw new Error("ffmpeg not found (install ffmpeg-static)");
    }

    const outputPath = path.resolve(this.outputDir, `tts_out_${randomTag()}.ogg`);
    const run = spawnSync(
      ffmpegPath,
      ["-y", "-loglevel", "error", "-i", inputPath, "-vn", "-codec:a", "libopus", "-b:a", "32k", "-vbr", "on", "-compression_level", "10", outputPath],
      { encoding: "utf8" },
    );

    if (run.status !== 0 || !fileExists(outputPath)) {
      const errTxt = String(run.stderr || run.stdout || "ffmpeg conversion failed").trim();
      throw new Error(errTxt || "ffmpeg conversion failed");
    }

    return outputPath;
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

  async _materializeInputFile(response) {
    const contentType = response.headers.get("content-type") || "";

    if (String(contentType).toLowerCase().includes("application/json")) {
      const data = await response.json();

      const localPath = this._resolveInputPath(
        data.file_path || data.path || data.wav_path || data.audio_path || data.output_path,
      );
      if (localPath) return localPath;

      const fromUrl = data.url || data.audio_url || data.file_url;
      if (fromUrl) {
        const mediaResp = await this._fetchWithTimeout(fromUrl, { method: "GET" });
        if (!mediaResp.ok) {
          throw new Error(`tts media url failed (${mediaResp.status})`);
        }
        const ext = guessExtFromContentType(mediaResp.headers.get("content-type") || "audio/wav");
        const arr = await mediaResp.arrayBuffer();
        return this._writeBufferToTemp(Buffer.from(arr), ext);
      }

      const b64 = data.audio_base64 || data.audio || data.base64 || data.wav_base64;
      if (b64) {
        let payload = String(b64).trim();
        const commaIdx = payload.indexOf(",");
        if (payload.startsWith("data:") && commaIdx > 0) {
          payload = payload.slice(commaIdx + 1);
        }
        const ext = guessExtFromContentType(contentType);
        return this._writeBufferToTemp(Buffer.from(payload, "base64"), ext);
      }

      throw new Error("tts json response has no audio payload");
    }

    const ext = guessExtFromContentType(contentType);
    const arr = await response.arrayBuffer();
    return this._writeBufferToTemp(Buffer.from(arr), ext);
  }

  async _listProfiles() {
    const now = Date.now();
    if (Array.isArray(this.profileCache) && now - this.profileCacheAt < this.profileCacheTtlMs) {
      return this.profileCache;
    }

    const response = await this._fetchWithTimeout(`${this.baseUrl}/profiles`, { method: "GET" });
    if (!response.ok) {
      throw new Error(`tts profiles failed (${response.status})`);
    }
    const profiles = await response.json();
    if (!Array.isArray(profiles)) {
      throw new Error("tts profiles response is not an array");
    }
    this.profileCache = profiles;
    this.profileCacheAt = now;
    return profiles;
  }

  async _resolveProfileId(profileRef) {
    const raw = String(profileRef || "").trim();
    if (!raw) throw new Error("missing profile_id");
    if (isUuid(raw)) return raw;

    const profiles = await this._listProfiles();
    const target = raw.toLowerCase();
    const found = profiles.find((p) => String(p?.name || "").trim().toLowerCase() === target);
    if (!found?.id) {
      throw new Error(`voice profile '${raw}' not found`);
    }
    return String(found.id);
  }

  async generateSpeechPttOgg(text, { profileId, language } = {}) {
    if (!this.enabled) {
      throw new Error("voice service disabled");
    }

    const cleanText = String(text || "").trim();
    if (!cleanText) {
      throw new Error("empty text for voice generation");
    }

    const profileRef = String(profileId || this.profileId).trim();
    const resolvedProfileId = await this._resolveProfileId(profileRef);

    const payload = {
      text: cleanText,
      profile_id: resolvedProfileId,
      language: String(language || this.language),
    };

    const response = await this._fetchWithTimeout(`${this.baseUrl}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const raw = await response.text().catch(() => "");
      throw new Error(`tts generate failed (${response.status}) ${String(raw).slice(0, 200)}`);
    }

    const inputPath = await this._materializeInputFile(response);
    if (!inputPath) {
      throw new Error("unable to materialize tts audio");
    }

    const ext = path.extname(inputPath).toLowerCase();
    if (ext === ".ogg") {
      return {
        oggPath: inputPath,
        profileId: profileRef,
        resolvedProfileId: payload.profile_id,
        language: payload.language,
      };
    }

    const oggPath = this._convertToOggOpus(inputPath);
    return {
      oggPath,
      profileId: profileRef,
      resolvedProfileId: payload.profile_id,
      language: payload.language,
    };
  }

  async generateSpeechMp3(text, opts = {}) {
    return this.generateSpeechPttOgg(text, opts);
  }
}

module.exports = { VoiceService };
