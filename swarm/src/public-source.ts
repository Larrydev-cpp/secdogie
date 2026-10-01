import { sha256Hex } from "./graph.js";
import type { PublicFetchTask, RouteEvidence } from "./protocol.js";

const DISALLOWED_SCHEMES = new Set(["data:", "blob:", "file:", "javascript:", "ws:", "wss:"]);
const PRIVATE_HOSTS = ["localhost", "::1", "127.0.0.1", "0.0.0.0", "169.254.169.254"];

export interface PublicFetchPolicy {
  readonly allowedOrigins: readonly string[];
  readonly maxBytes: number;
  readonly timeoutMs: number;
}

function validatePolicy(policy: PublicFetchPolicy): void {
  if (!Number.isSafeInteger(policy.maxBytes) || policy.maxBytes <= 0) throw new RangeError("maxBytes must be a positive safe integer");
  if (!Number.isSafeInteger(policy.timeoutMs) || policy.timeoutMs <= 0) throw new RangeError("timeoutMs must be a positive safe integer");
  if (!policy.allowedOrigins.length) throw new Error("at least one approved public origin is required");
}

export function sanitizePublicUrl(raw: string, policy: PublicFetchPolicy): URL {
  validatePolicy(policy);
  const url = new URL(raw);
  if (url.protocol !== "https:") throw new TypeError("public topology fetches require HTTPS");
  if (DISALLOWED_SCHEMES.has(url.protocol)) throw new TypeError("unsupported URL scheme");
  if (url.username || url.password) throw new TypeError("credential-bearing URLs are refused");
  const hostname = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (PRIVATE_HOSTS.includes(hostname) || hostname.endsWith(".local") || hostname.startsWith("10.") || hostname.startsWith("192.168.")) {
    throw new TypeError("private or local hosts are refused by the public-topology fetch policy");
  }
  const ipv4 = hostname.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (ipv4) {
    const octets = ipv4.slice(1).map(Number);
    const a = octets[0]!;
    const b = octets[1]!;
    if (a === 127 || a === 10 || (a === 192 && b === 168) || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31)) {
      throw new TypeError("private IPv4 hosts are refused by the public-topology fetch policy");
    }
  }
  if (url.hash) url.hash = "";
  url.search = "";
  if (!policy.allowedOrigins.includes(url.origin)) throw new TypeError(`origin ${url.origin} is outside the approved public-origin set`);
  return url;
}

export async function fetchPublicSource(task: PublicFetchTask, policy: PublicFetchPolicy): Promise<{ url: string; body: string; hash: string }> {
  const url = sanitizePublicUrl(task.url, policy);
  if (task.origin !== url.origin) throw new TypeError("task origin does not match the sanitized URL origin");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), policy.timeoutMs);
  try {
    const response = await fetch(url, {
      credentials: "omit",
      cache: "no-store",
      redirect: "manual",
      headers: { accept: "text/html,application/javascript,application/json;q=0.9,text/plain;q=0.5" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`public source returned HTTP ${response.status}`);
    const contentType = ((response.headers.get("content-type") ?? "").split(";", 1)[0] ?? "").trim().toLowerCase();
    const textualTypes = new Set(["", "text/html", "application/javascript", "text/javascript", "application/json", "text/plain"]);
    if (!textualTypes.has(contentType)) throw new Error(`public source content type ${contentType || "unknown"} is not textual`);
    const declared = Number(response.headers.get("content-length") ?? 0);
    if (declared > policy.maxBytes) throw new Error("public source exceeds byte budget");
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > policy.maxBytes) throw new Error("public source exceeds byte budget");
    const body = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
    return { url: url.toString(), body, hash: await sha256Hex(bytes) };
  } finally {
    clearTimeout(timer);
  }
}

export interface RouteAnalyzer {
  analyze(source: string, sourceHash: string): readonly RouteEvidence[];
}

export class ConservativeRouteAnalyzer implements RouteAnalyzer {
  private readonly patterns = [
    /\b(?:path|route|href|to)\s*[:=]\s*["'`]([^"'`?#\s]+)["'`]/g,
    /\b(?:push|replace|navigate|redirect|goto)\s*\(\s*["'`]([^"'`?#\s]+)["'`]/g,
    /\b(?:get|post|put|delete|patch)\s*\(\s*["'`]([^"'`?#\s]+)["'`]/gi,
  ];

  analyze(source: string, sourceHash: string): readonly RouteEvidence[] {
    const routes = new Map<string, RouteEvidence>();
    for (const pattern of this.patterns) {
      for (const match of source.matchAll(pattern)) {
        const raw = match[1];
        if (!raw || !raw.startsWith("/")) continue;
        routes.set(raw, { path: raw, sourceHash, confidence: 0.72, kind: "literal" });
      }
    }
    return [...routes.values()].sort((a, b) => a.path.localeCompare(b.path));
  }
}
