import { Outlet } from "react-router-dom";
import { Icon } from "../components/ui/Icon";

/** Centred layout for the first-run setup and the sign-in screen. */
export function AuthLayout() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-on-accent"
      >
        Skip to content
      </a>
      <div className="mb-8 flex items-center gap-3">
        <span aria-hidden="true" className="flex size-10 items-center justify-center rounded-full border border-accent/60 bg-accent/15 text-accent">
          <Icon name="voice" className="size-5" />
        </span>
        <span className="font-display text-xl font-semibold tracking-tight text-ink">JARVIS AI</span>
      </div>
      <main id="main-content" tabIndex={-1} className="w-full max-w-md outline-none">
        <Outlet />
      </main>
    </div>
  );
}
