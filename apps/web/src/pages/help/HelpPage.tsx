import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge } from "../../components/ui/Badge";
import { Card, CardTitle } from "../../components/ui/Card";
import { detectVoiceCapabilities } from "../../services/speech";
import { API_ERROR_ROWS, DOCS_PATHS, IOS_SHORTCUT_STEPS, KEYBOARD_SHORTCUTS, PROMPT_LINK_EXAMPLE, PROMPT_LINK_LIMIT, TASK_ERROR_ROWS, type TroubleshootingRow } from "./helpContent";

const STEPS = [
  { title: "Sign in and check the overview", body: "The Overview shows whether the assistant, the database and the AI provider are ready." },
  { title: "Configure an AI provider", body: "Add a key on AI Models, test the connection, then choose a model. Keys are stored by the server and are never shown again." },
  { title: "Pair a desktop agent (optional)", body: "The Windows agent lets JARVIS open approved apps, folders and links on your PC." },
  { title: "Ask for something", body: "Use AI Chat or Voice. Actions that need approval wait on the Tasks page until you decide." },
];

function TroubleshootingTable({ rows, caption }: { rows: TroubleshootingRow[]; caption: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[32rem] border-collapse text-left text-sm">
        <caption className="mb-2 text-left text-sm font-medium text-ink">{caption}</caption>
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th scope="col" className="py-2 pr-3 font-medium">
              Code
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              What it means
            </th>
            <th scope="col" className="py-2 font-medium">
              What to do
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.code} className="border-b border-line align-top">
              <td className="py-2 pr-3">
                <code className="text-xs text-ink">{row.code}</code>
              </td>
              <td className="py-2 pr-3 text-muted">{row.meaning}</td>
              <td className="py-2 text-ink">{row.fix}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Getting started, setup, voice requirements, troubleshooting and documentation paths. */
export default function HelpPage() {
  const [voice] = useState(() => detectVoiceCapabilities());
  const voiceReady = voice.recognition && voice.synthesis;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Help" description="How to get started, how to connect the parts, and what to do when something fails." />

      <Card className="flex flex-col gap-4">
        <CardTitle>Getting started</CardTitle>
        <ol className="flex flex-col gap-3">
          {STEPS.map((step, index) => (
            <li key={step.title} className="flex gap-3">
              <span aria-hidden="true" className="flex size-6 shrink-0 items-center justify-center rounded-full border border-line text-xs text-muted">
                {index + 1}
              </span>
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-medium text-ink">{step.title}</span>
                <span className="text-sm text-muted">{step.body}</span>
              </div>
            </li>
          ))}
        </ol>
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>Pairing a desktop agent</CardTitle>
        <ol className="list-decimal flex flex-col gap-2 pl-5 text-sm text-ink">
          <li>
            Open <Link to="/devices?pair=1" className="text-accent underline underline-offset-4">Devices</Link> and create a pairing code.
          </li>
          <li>The code is shown once and expires. Enter it in the Windows agent.</li>
          <li>When the agent connects, it appears as online. Set its permissions for each tool on the Devices page.</li>
        </ol>
        <p className="text-xs text-muted">Codes are single use. Too many wrong attempts lock the code, so create a new one.</p>
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>Configuring an AI provider</CardTitle>
        <ol className="list-decimal flex flex-col gap-2 pl-5 text-sm text-ink">
          <li>
            Open <Link to="/models" className="text-accent underline underline-offset-4">AI Models</Link> and enter the provider key.
          </li>
          <li>Run the connection test. It uses a free call and never generates a reply.</li>
          <li>Choose a model from the live list, then set the active provider in the routing panel.</li>
        </ol>
        <p className="text-xs text-muted">The key field is write-only. Only the last few characters are shown afterwards.</p>
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>Voice requirements</CardTitle>
        <p className="text-sm text-muted">What this browser can do. JARVIS reports only what the browser actually provides.</p>
        <ul className="flex flex-col gap-2 text-sm">
          <li className="flex flex-wrap items-center gap-2">
            <Badge tone={voice.recognition ? "success" : "warning"}>{voice.recognition ? "Available" : "Not available"}</Badge>
            Speech recognition (listening to you)
          </li>
          <li className="flex flex-wrap items-center gap-2">
            <Badge tone={voice.synthesis ? "success" : "warning"}>{voice.synthesis ? "Available" : "Not available"}</Badge>
            Speech output (JARVIS speaking)
          </li>
          <li className="flex flex-wrap items-center gap-2">
            <Badge tone={voice.voices ? "success" : "neutral"}>{voice.voices ? "Available" : "Not listed"}</Badge>
            Choosing a voice
          </li>
          <li className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">Not available</Badge>
            Wake word. No engine is included.
          </li>
        </ul>
        <p className="text-xs text-muted">
          {voiceReady
            ? "Use a current Chromium-based browser and allow microphone access when asked."
            : "Speech recognition needs a Chromium-based browser such as Chrome or Edge. Speech output needs a browser with speech synthesis."}{" "}
          Microphone access also needs a secure address, such as https or localhost.
        </p>
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>Keyboard shortcuts</CardTitle>
        <dl className="flex flex-col gap-3 text-sm">
          {KEYBOARD_SHORTCUTS.map((shortcut) => (
            <div key={shortcut.keys} className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
              <dt className="min-w-44 font-medium text-ink">{shortcut.keys}</dt>
              <dd className="text-muted">{shortcut.does}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>Open the chat with a prompt</CardTitle>
        <p className="text-sm text-muted">
          A link can put a prompt in the chat box. The link never sends it. Open the chat, read the text, then press Enter.
        </p>
        <ul className="flex list-disc flex-col gap-2 pl-5 text-sm text-ink">
          <li>
            Format: your JARVIS address, then <code className="text-xs">/chat?prompt=</code> and the prompt with its characters URL-encoded.
          </li>
          <li>{`Prompts longer than ${PROMPT_LINK_LIMIT} characters are cut, and the chat says so.`}</li>
        </ul>
        <pre className="overflow-x-auto rounded-lg border border-line bg-canvas p-3 text-xs text-ink">
          <code>{PROMPT_LINK_EXAMPLE}</code>
        </pre>
        <h3 className="text-sm font-semibold text-ink">iPhone Shortcut</h3>
        <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm text-ink">
          {IOS_SHORTCUT_STEPS.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>Troubleshooting</CardTitle>
        <TroubleshootingTable rows={TASK_ERROR_ROWS} caption="Task error codes" />
        <TroubleshootingTable rows={API_ERROR_ROWS} caption="Request error codes" />
      </Card>

      <Card className="flex flex-col gap-4">
        <CardTitle>Documentation</CardTitle>
        <ul className="flex flex-col gap-2 text-sm">
          {DOCS_PATHS.map((doc) => (
            <li key={doc.path} className="flex flex-col gap-0.5">
              <code className="text-ink">{doc.path}</code>
              <span className="text-muted">{doc.for}</span>
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted">These are files in the project repository, so they are shown as paths rather than links.</p>
      </Card>
    </div>
  );
}
