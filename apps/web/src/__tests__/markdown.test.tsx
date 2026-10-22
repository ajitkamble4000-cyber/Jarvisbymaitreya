import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown, isSafeHref, parseMarkdown } from "../components/markdown/Markdown";

describe("safe Markdown renderer", () => {
  it("renders raw HTML as text and never creates elements from it", () => {
    const { container } = render(<Markdown source={'Hello <script>alert(1)</script> <img src=x onerror="alert(1)">'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain('<script>alert(1)</script>');
  });

  it("does not turn javascript: links into anchors and keeps the label", () => {
    const { container } = render(<Markdown source={"[click me](javascript:alert(1))"} />);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("click me");
  });

  it("renders safe links with noopener noreferrer and a new tab target", () => {
    render(<Markdown source={"See [the docs](https://example.com/docs) for details."} />);
    const link = screen.getByRole("link", { name: "the docs" });
    expect(link).toHaveAttribute("href", "https://example.com/docs");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("formats bold, italic, inline code, lists and fenced code", () => {
    const source = ["# Title", "", "Some **bold**, *italic* and `code`.", "", "- one", "- two", "", "```ts", "const a = 1;", "```"].join("\n");
    const { container } = render(<Markdown source={source} />);
    expect(container.querySelector("h3")?.textContent).toBe("Title");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("italic");
    expect(container.querySelector("p code")?.textContent).toBe("code");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelector("pre code")?.textContent).toBe("const a = 1;");
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
  });

  it("keeps an unclosed fence as code while a reply is still streaming", () => {
    expect(parseMarkdown("```ts\nconst a = 1")).toEqual([{ type: "code", language: "ts", code: "const a = 1" }]);
  });

  it("allows only http, https, mailto and relative hrefs", () => {
    expect(isSafeHref("https://example.com")).toBe(true);
    expect(isSafeHref("http://example.com")).toBe(true);
    expect(isSafeHref("mailto:someone@example.com")).toBe(true);
    expect(isSafeHref("/chat/123")).toBe(true);
    expect(isSafeHref("javascript:alert(1)")).toBe(false);
    expect(isSafeHref("JaVaScRiPt:alert(1)")).toBe(false);
    expect(isSafeHref("data:text/html,<script>alert(1)</script>")).toBe(false);
    expect(isSafeHref("")).toBe(false);
  });
});
