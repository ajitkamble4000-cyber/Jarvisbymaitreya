import { Fragment, useMemo, useState, type ReactNode } from "react";
import { cn } from "../ui/cn";

/**
 * Safe Markdown for assistant output. It builds React elements directly and never uses
 * dangerouslySetInnerHTML, so raw HTML in the source is displayed as text. Supported:
 * headings (# to ###), paragraphs, bullet and numbered lists, block quotes, fenced code blocks,
 * bold, italic, inline code, and links. Links are rendered only for http, https and mailto.
 */

export type MarkdownBlock =
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "paragraph"; text: string }
  | { type: "quote"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "code"; language: string; code: string };

const FENCE = /^\s*```\s*([\w+#.-]*)\s*$/;
const HEADING = /^(#{1,3})\s+(.+?)\s*#*\s*$/;
const UNORDERED = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;

/** Splits Markdown source into blocks. An unclosed code fence runs to the end, so streamed output renders. */
export function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";

    const fence = FENCE.exec(line);
    if (fence) {
      const language = fence[1] ?? "";
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !FENCE.test(lines[index] ?? "")) {
        code.push(lines[index] ?? "");
        index += 1;
      }
      index += 1;
      blocks.push({ type: "code", language, code: code.join("\n") });
      continue;
    }

    if (line.trim() === "") {
      index += 1;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      const level = (heading[1] ?? "#").length as 1 | 2 | 3;
      blocks.push({ type: "heading", level, text: heading[2] ?? "" });
      index += 1;
      continue;
    }

    const unordered = UNORDERED.exec(line);
    const ordered = ORDERED.exec(line);
    if (unordered || ordered) {
      const isOrdered = Boolean(ordered);
      const pattern = isOrdered ? ORDERED : UNORDERED;
      const items: string[] = [];
      while (index < lines.length) {
        const match = pattern.exec(lines[index] ?? "");
        if (!match) break;
        items.push(match[1] ?? "");
        index += 1;
      }
      blocks.push({ type: "list", ordered: isOrdered, items });
      continue;
    }

    const quote = QUOTE.exec(line);
    if (quote) {
      const quoted: string[] = [];
      while (index < lines.length) {
        const match = QUOTE.exec(lines[index] ?? "");
        if (!match) break;
        quoted.push(match[1] ?? "");
        index += 1;
      }
      blocks.push({ type: "quote", text: quoted.join("\n") });
      continue;
    }

    const paragraph: string[] = [];
    while (index < lines.length) {
      const current = lines[index] ?? "";
      if (current.trim() === "" || FENCE.test(current) || HEADING.test(current) || UNORDERED.test(current) || ORDERED.test(current) || QUOTE.test(current)) break;
      paragraph.push(current);
      index += 1;
    }
    if (paragraph.length === 0) {
      paragraph.push(line);
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join("\n") });
  }

  return blocks;
}

const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

/** Allows http, https, mailto and relative links. Everything else (javascript:, data:, ...) is refused. */
export function isSafeHref(href: string): boolean {
  const trimmed = href.trim();
  if (!trimmed) return false;
  try {
    const parsed = new URL(trimmed, "https://relative.invalid/");
    return SAFE_PROTOCOLS.has(parsed.protocol);
  } catch {
    return false;
  }
}

// Order matters: code spans first so their contents are not formatted.
const INLINE =
  /(`+)([^`]+?)\1|\[([^\]\n]+)\]\(([^)\s]+)\)|\*\*([^*\n]+?)\*\*|__([^_\n]+?)__|(?<![\w*])\*([^*\n]+?)\*(?![\w*])|(?<![\w])_([^_\n]+?)_(?![\w])|(https?:\/\/[^\s<>()]+[^\s<>()*_.,;:!?])/g;

/** Renders inline Markdown into React nodes. Plain text is always escaped by React. */
export function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let counter = 0;
  const key = (): string => `${keyPrefix}-${counter++}`;

  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(<Fragment key={key()}>{text.slice(cursor, start)}</Fragment>);
    cursor = start + match[0].length;

    if (match[2] !== undefined) {
      nodes.push(
        <code key={key()} className="rounded bg-raised px-1 py-0.5 font-mono text-[0.9em]">
          {match[2]}
        </code>,
      );
    } else if (match[3] !== undefined && match[4] !== undefined) {
      const label = match[3];
      const href = match[4];
      if (isSafeHref(href)) {
        nodes.push(
          <a key={key()} href={href.trim()} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2 break-words">
            {renderInline(label, `${keyPrefix}-${counter}`)}
          </a>,
        );
      } else {
        nodes.push(<Fragment key={key()}>{label}</Fragment>);
      }
    } else if (match[5] !== undefined || match[6] !== undefined) {
      const inner = match[5] ?? match[6] ?? "";
      nodes.push(<strong key={key()}>{renderInline(inner, `${keyPrefix}-${counter}`)}</strong>);
    } else if (match[7] !== undefined || match[8] !== undefined) {
      const inner = match[7] ?? match[8] ?? "";
      nodes.push(<em key={key()}>{renderInline(inner, `${keyPrefix}-${counter}`)}</em>);
    } else if (match[9] !== undefined) {
      const href = match[9];
      nodes.push(
        <a key={key()} href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2 break-all">
          {href}
        </a>,
      );
    } else {
      nodes.push(<Fragment key={key()}>{match[0]}</Fragment>);
    }
  }

  if (cursor < text.length) nodes.push(<Fragment key={key()}>{text.slice(cursor)}</Fragment>);
  return nodes;
}

/** Splits paragraph text on newlines, keeping the line breaks as <br />. */
function lines(text: string, prefix: string): ReactNode[] {
  return text.split("\n").flatMap((line, index) => {
    const content = <Fragment key={`${prefix}-${index}`}>{renderInline(line, `${prefix}-${index}`)}</Fragment>;
    return index === 0 ? [content] : [<br key={`${prefix}-br-${index}`} />, content];
  });
}

export function CodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
    window.setTimeout(() => setCopied("idle"), 2000);
  };

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-canvas">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-1.5 text-xs text-muted">
        <span className="font-mono">{language || "text"}</span>
        <button type="button" onClick={() => void copy()} className="rounded px-2 py-0.5 text-muted hover:text-ink" aria-label="Copy code">
          {copied === "copied" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-[0.85rem] leading-relaxed">
        <code className="font-mono whitespace-pre">{code}</code>
      </pre>
    </div>
  );
}

function renderBlock(block: MarkdownBlock, index: number): ReactNode {
  switch (block.type) {
    case "heading": {
      const className = cn("font-semibold text-ink", block.level === 1 ? "text-lg" : block.level === 2 ? "text-base" : "text-sm uppercase tracking-wide text-muted");
      if (block.level === 1) return <h3 key={index} className={className}>{renderInline(block.text, `h${index}`)}</h3>;
      if (block.level === 2) return <h4 key={index} className={className}>{renderInline(block.text, `h${index}`)}</h4>;
      return <h5 key={index} className={className}>{renderInline(block.text, `h${index}`)}</h5>;
    }
    case "paragraph":
      return <p key={index}>{lines(block.text, `p${index}`)}</p>;
    case "quote":
      return <blockquote key={index} className="border-l-2 border-accent pl-3 text-muted">{lines(block.text, `q${index}`)}</blockquote>;
    case "list": {
      const ListTag = block.ordered ? "ol" : "ul";
      return (
        <ListTag key={index} className={cn("flex flex-col gap-1 pl-5", block.ordered ? "list-decimal" : "list-disc")}>
          {block.items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item, `li${index}-${itemIndex}`)}</li>
          ))}
        </ListTag>
      );
    }
    case "code":
      return <CodeBlock key={index} code={block.code} language={block.language} />;
    default:
      return null;
  }
}

export interface MarkdownProps {
  source: string;
  className?: string;
}

export function Markdown({ source, className }: MarkdownProps) {
  const blocks = useMemo(() => parseMarkdown(source), [source]);
  return <div className={cn("flex flex-col gap-3 break-words text-sm leading-relaxed", className)}>{blocks.map(renderBlock)}</div>;
}
