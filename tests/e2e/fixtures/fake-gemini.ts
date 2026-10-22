/**
 * Local stand-in for the Gemini REST API, used only by the milestone end-to-end test.
 *
 * It answers the three calls the backend makes: model listing, the free key test, and a streaming
 * generateContent call. A chat message containing the milestone prompt gets a functionCall for
 * launch_app; other messages get plain text. Requests with any other API key are refused.
 * Nothing here reaches the internet. Run by Playwright's webServer (see ../playwright.config.ts).
 */
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

const PORT = Number(process.env.FAKE_GEMINI_PORT ?? "8788");
const FAKE_GEMINI_KEY = "AIza-fake-e2e-key-0001";
const MODEL = "gemini-2.5-flash";
const PROMPT = "Open Chrome on my laptop.";

const MODELS = {
  models: [
    {
      name: `models/${MODEL}`,
      displayName: "Gemini 2.5 Flash",
      supportedGenerationMethods: ["generateContent", "streamGenerateContent"],
      supportsTools: true,
    },
  ],
};

function json(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

function sseChunk(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\r\n\r\n`;
}

function textChunk(text: string): unknown {
  return { candidates: [{ content: { role: "model", parts: [{ text }] }, finishReason: "STOP" }] };
}

function launchChunk(): unknown {
  const functionCall = { name: "launch_app", args: { device: "Laptop", app: "chrome" } };
  return {
    candidates: [{ content: { role: "model", parts: [{ functionCall }] }, finishReason: "STOP" }],
    usageMetadata: { promptTokenCount: 40, candidatesTokenCount: 12, totalTokenCount: 52 },
  };
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${PORT}`);
  if (req.method === "GET" && url.pathname === "/health") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("ok");
    return;
  }
  if (req.headers["x-goog-api-key"] !== FAKE_GEMINI_KEY) {
    json(res, 400, { error: { code: 400, status: "INVALID_ARGUMENT", message: "API key not valid." } });
    return;
  }
  if (req.method === "GET" && url.pathname === "/v1beta/models") {
    json(res, 200, MODELS);
    return;
  }
  if (req.method === "POST" && url.pathname === `/v1beta/models/${MODEL}:streamGenerateContent`) {
    const body = await readBody(req);
    let chunks: unknown[];
    if (body.includes("functionResponse")) {
      chunks = [textChunk("Noted.")];
    } else if (body.includes(PROMPT)) {
      chunks = [launchChunk()];
    } else {
      chunks = [textChunk("Hello.")];
    }
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.end(chunks.map(sseChunk).join(""));
    return;
  }
  json(res, 404, { error: { message: "not found in the milestone fake" } });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`fake-gemini listening on 127.0.0.1:${PORT}`);
});
