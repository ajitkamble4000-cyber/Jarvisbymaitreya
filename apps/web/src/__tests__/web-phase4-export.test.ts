import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadTasksCsv } from "../services/exportDownload";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function stubDownload(fileName = "jarvis-tasks.csv") {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response("id,title\r\n", { status: 200, headers: { "Content-Disposition": `attachment; filename="${fileName}"` } }));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:tasks", revokeObjectURL: () => {} }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  return { fetchMock, click };
}

describe("task CSV download", () => {
  it("sends no status filter for All and no search for blank text", async () => {
    const { fetchMock, click } = stubDownload();
    const name = await downloadTasksCsv({ status: "all", q: "   " });
    expect(name).toBe("jarvis-tasks.csv");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/v1/tasks/export.csv");
    expect(click).toHaveBeenCalledTimes(1);
  });

  it("sends the status tab and the trimmed search text", async () => {
    const { fetchMock, click } = stubDownload();
    await downloadTasksCsv({ status: "failed", q: " open chrome " });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toBe("/api/v1/tasks/export.csv?status=failed&q=open+chrome");
    click.mockRestore();
  });

  it("reports a refused export with the server's message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ error: { code: "not_authenticated", message: "Sign in to continue." } }), { status: 401 })),
    );
    await expect(downloadTasksCsv({})).rejects.toMatchObject({ code: "not_authenticated", message: "Sign in to continue." });
  });
});
