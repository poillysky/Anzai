import { type NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_PROXY = process.env.API_PROXY_TARGET || "http://127.0.0.1:8515";

/**
 * Streaming proxy for 安崽 chat SSE.
 * Next rewrites buffer external streams; this Route Handler pipes bytes through.
 */
export async function POST(req: NextRequest) {
  const auth = req.headers.get("authorization") || "";
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${API_PROXY}/api/agent/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(auth ? { Authorization: auth } : {}),
      },
      body,
      cache: "no-store",
      signal: req.signal,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "upstream unreachable";
    return new Response(JSON.stringify({ type: "error", message: `代理失败：${msg}` }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }

  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text();
    return new Response(text || `Upstream ${upstream.status}`, {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
