"use strict";

// Cloud Studio 大制品接收器。仅接受一个由签名发布流程预先冻结的文件，
// 分片写入期间校验偏移，最终校验长度、文件头和 SHA-256 后才公开下载。
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { pipeline } = require("stream");

const PORT = Number.parseInt(process.env.PORT || "3000", 10);
const ROOT = process.env.ROOT || "/workspace";
const METADATA_PATH = path.join(ROOT, "upload-metadata.json");
const metadata = JSON.parse(fs.readFileSync(METADATA_PATH, "utf8"));
const FILE_NAME = String(metadata.file_name || "");
const EXPECTED_BYTES = Number(metadata.bytes);
const EXPECTED_SHA256 = String(metadata.sha256 || "").toLowerCase();
const EXPECTED_MAGIC = String(metadata.magic_hex || "").toLowerCase();
const CONTENT_TYPE = String(metadata.content_type || "application/octet-stream");
const VERSION = String(metadata.version || "");
const MAX_CHUNK_BYTES = 16 * 1024 * 1024;

if (
  !/^[A-Za-z0-9._+-]+$/.test(FILE_NAME) ||
  !Number.isSafeInteger(EXPECTED_BYTES) ||
  EXPECTED_BYTES < 1 ||
  !/^[a-f0-9]{64}$/.test(EXPECTED_SHA256) ||
  !/^[a-f0-9]{8}$/.test(EXPECTED_MAGIC) ||
  !/^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$/.test(VERSION)
) {
  throw new Error("UPLOAD_METADATA_INVALID");
}

const DOWNLOADS_DIR = path.join(ROOT, "downloads");
const FINAL_PATH = path.join(DOWNLOADS_DIR, FILE_NAME);
const TEMP_PATH = path.join(DOWNLOADS_DIR, `.${FILE_NAME}.uploading`);
const TOKEN_PATH = path.join(ROOT, ".upload-token");
const COMPLETE_PATH = path.join(ROOT, ".upload-complete");
fs.mkdirSync(DOWNLOADS_DIR, { recursive: true });

function sendJson(response, status, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
    "Cache-Control": "no-store",
  });
  response.end(body);
}

function tokenIsValid(request) {
  if (!fs.existsSync(TOKEN_PATH) || fs.existsSync(COMPLETE_PATH)) return false;
  const expected = fs.readFileSync(TOKEN_PATH, "utf8").trim();
  const supplied = String(request.headers["x-partyops-upload-token"] || "");
  if (!expected || expected.length !== supplied.length) return false;
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(supplied));
}

function hashFile(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const stream = fs.createReadStream(filePath);
    stream.on("error", reject);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("end", () => resolve(hash.digest("hex")));
  });
}

function serveFile(request, response) {
  if (!fs.existsSync(FINAL_PATH) || !fs.existsSync(COMPLETE_PATH)) {
    return sendJson(response, 404, { error: "not_ready" });
  }
  const size = fs.statSync(FINAL_PATH).size;
  const headers = {
    "Content-Type": CONTENT_TYPE,
    "Accept-Ranges": "bytes",
    "Content-Disposition": `attachment; filename="${FILE_NAME}"`,
    "X-Content-Type-Options": "nosniff",
  };
  const range = request.headers.range;
  if (range) {
    const match = /^bytes=(\d+)-(\d*)$/.exec(range);
    if (!match) {
      response.writeHead(416, { "Content-Range": `bytes */${size}` });
      return response.end();
    }
    const start = Number(match[1]);
    const end = Math.min(match[2] ? Number(match[2]) : size - 1, size - 1);
    if (!Number.isSafeInteger(start) || start < 0 || start > end || start >= size) {
      response.writeHead(416, { "Content-Range": `bytes */${size}` });
      return response.end();
    }
    response.writeHead(206, {
      ...headers,
      "Content-Length": end - start + 1,
      "Content-Range": `bytes ${start}-${end}/${size}`,
    });
    if (request.method === "HEAD") return response.end();
    return fs.createReadStream(FINAL_PATH, { start, end }).pipe(response);
  }
  response.writeHead(200, { ...headers, "Content-Length": size });
  if (request.method === "HEAD") return response.end();
  return fs.createReadStream(FINAL_PATH).pipe(response);
}

function receiveChunk(request, response) {
  if (!tokenIsValid(request)) return sendJson(response, 404, { error: "not_found" });
  const contentLength = Number(request.headers["content-length"] || 0);
  const offset = Number(request.headers["x-upload-offset"] || -1);
  const currentSize = fs.existsSync(TEMP_PATH) ? fs.statSync(TEMP_PATH).size : 0;
  if (!Number.isSafeInteger(contentLength) || contentLength < 1 || contentLength > MAX_CHUNK_BYTES) {
    return sendJson(response, 413, { error: "chunk_size_invalid" });
  }
  if (!Number.isSafeInteger(offset) || offset !== currentSize) {
    return sendJson(response, 409, { error: "offset_mismatch", expected_offset: currentSize });
  }
  if (currentSize + contentLength > EXPECTED_BYTES) {
    return sendJson(response, 413, { error: "file_size_exceeded" });
  }
  const output = fs.createWriteStream(TEMP_PATH, { flags: "a", mode: 0o600 });
  pipeline(request, output, (error) => {
    if (error) return sendJson(response, 500, { error: "chunk_write_failed" });
    return sendJson(response, 200, { ok: true, next_offset: fs.statSync(TEMP_PATH).size });
  });
}

async function finalizeUpload(request, response) {
  if (!tokenIsValid(request)) return sendJson(response, 404, { error: "not_found" });
  if (!fs.existsSync(TEMP_PATH)) return sendJson(response, 409, { error: "upload_missing" });
  const size = fs.statSync(TEMP_PATH).size;
  if (size !== EXPECTED_BYTES) return sendJson(response, 409, { error: "size_mismatch", bytes: size });
  const handle = fs.openSync(TEMP_PATH, "r");
  const magic = Buffer.alloc(4);
  fs.readSync(handle, magic, 0, magic.length, 0);
  fs.closeSync(handle);
  if (magic.toString("hex") !== EXPECTED_MAGIC) return sendJson(response, 409, { error: "magic_mismatch" });
  const sha256 = await hashFile(TEMP_PATH);
  if (sha256 !== EXPECTED_SHA256) return sendJson(response, 409, { error: "sha256_mismatch" });
  if (fs.existsSync(FINAL_PATH)) fs.unlinkSync(FINAL_PATH);
  fs.renameSync(TEMP_PATH, FINAL_PATH);
  fs.writeFileSync(COMPLETE_PATH, `${new Date().toISOString()}\n`, { mode: 0o600 });
  fs.unlinkSync(TOKEN_PATH);
  return sendJson(response, 200, { ok: true, bytes: size, sha256 });
}

http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url, "http://127.0.0.1");
    if (request.method === "POST" && url.pathname === "/__partyops_upload_chunk") {
      return receiveChunk(request, response);
    }
    if (request.method === "POST" && url.pathname === "/__partyops_upload_finalize") {
      return await finalizeUpload(request, response);
    }
    if (
      (request.method === "GET" || request.method === "HEAD") &&
      url.pathname === `/downloads/${FILE_NAME}`
    ) {
      return serveFile(request, response);
    }
    if (request.method === "GET" && url.pathname === "/health") {
      return sendJson(response, 200, {
        ok: true,
        version: VERSION,
        file_ready: fs.existsSync(FINAL_PATH) && fs.existsSync(COMPLETE_PATH),
      });
    }
    return sendJson(response, 404, { error: "not_found" });
  } catch {
    return sendJson(response, 500, { error: "internal_error" });
  }
}).listen(PORT, () => {
  console.log(`PartyOps download receiver listening on ${PORT}`);
});
