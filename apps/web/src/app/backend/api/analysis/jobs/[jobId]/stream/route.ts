import { type NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_PROXY = process.env.API_PROXY_TARGET || "http://127.0.0.1:8515";

/**
 * Streaming proxy: attach to a running analysis job (agent-started etc.).
 */
export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ jobId: string }> },
) {
  const auth = req.headers.get("authorization") || "";
  const { jobId } = await ctx.params;
  const id = encodeURIComponent(jobId || "");

  let upstream: Response;
  try {
    upstream = await fetch(`${API_PROXY}/api/analysis/jobs/${id}/stream`, {
      method: "GET",
      headers: {
        Accept: "text/event-stream",
        ...(auth ? { Authorization: auth } : {}),
      },
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
